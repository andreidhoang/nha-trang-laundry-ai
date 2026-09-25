"""Release a TRANSACTIONAL suppression on verified evidence, and read a contact's service state.

`CONSENT-TRANSACTIONAL-001`, `DEC-033` ruling 2: a release is a human act with server-verified
evidence. After a STOP, nothing the shop initiates on that channel may be sent until an
`OWNER_ADMIN` or `OPS_APPROVER` with MFA, a member of the shop, cites an inbound message that the
server itself verifies is

* from this contact binding,
* on this channel,
* a customer message (provider event type `MESSAGE`) that was not itself an opt-out, and
* received after the consent event the block rests on, and not after the release itself.

The release appends a `RELEASE` consent event and moves the TRANSACTIONAL entry to CLEAR, with its
domain event, audit row and outbox row in one transaction (invariant 5), under the advisory lock the
ingress STOP writer and the egress guard take -- so a STOP that commits while a release is being
written is either seen by it or suppresses again straight after it.

MARKETING is never touched here. A marketing grant needs a real consent request with exact wording,
purpose, channel and expiry (SECURITY spec §8.4); a customer asking about their laundry is not one.
Migration `0053`'s guard enforces the same shape in the database.

The evidence is picked from a list the server serves (`read_service_messaging_state`), never typed:
the console offers only the inbound messages that would pass the checks above.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.consent import SuppressionState

from nha_trang_laundry_db.consent_egress import (
    MARKETING,
    TRANSACTIONAL,
    EgressCheck,
    evaluate_transactional_egress,
    suppression_lock,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.service_messaging import INBOUND_MESSAGE_EVENT_TYPE
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Who may lift a TRANSACTIONAL block. The two roles that may decide an approval; an operator may
#: see the state and ask, never lift it.
RELEASE_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER})
#: Who may read a contact's service-messaging state: the roles that raise, decide or spend a
#: `SEND_MESSAGE` envelope, exactly `MESSAGE_DRAFT_BINDING_READ_ROLES`.
READ_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR})
#: The blocked states a release lifts. `CLEAR` and no row at all have nothing to lift.
RELEASABLE_STATES = frozenset(
    {
        SuppressionState.SUPPRESSED,
        SuppressionState.PENDING_REVIEW_BLOCKED,
        SuppressionState.UNKNOWN_BLOCKED,
    }
)
#: At most this many candidate messages are offered as evidence, newest first.
EVIDENCE_LIMIT = 20


class TransactionalConsentAuthorizationError(PermissionError):
    """Role, MFA or store membership refused. One opaque answer for all three."""


class TransactionalConsentNotFoundError(LookupError):
    """The contact is unknown to this shop. The same answer as a contact that does not exist."""


class TransactionalConsentStateError(ValueError):
    """A release the facts do not support. `reason_code` says which fact."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class TransactionalReleaseCommand:
    store_id: UUID
    contact_binding_id: UUID
    channel: str
    evidence_webhook_event_id: UUID
    principal: StaffPrincipal
    correlation_id: UUID
    released_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredTransactionalRelease:
    release_consent_event_id: UUID
    contact_binding_id: UUID
    channel: str
    purpose: str
    previous_state: str
    state: str
    evidence_webhook_event_id: UUID
    released_at: datetime


@dataclass(frozen=True, slots=True)
class ReleaseEvidenceCandidate:
    """An inbound message a release may cite. Its identifier and time only; never its words."""

    webhook_event_id: UUID
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ServiceMessagingState:
    store_id: UUID
    contact_binding_id: UUID
    channel: str
    #: `NONE` when no TRANSACTIONAL row exists: nobody wrote STOP on this channel.
    transactional_state: str
    #: When the event the block rests on happened; `None` when nothing blocks.
    blocked_since: datetime | None
    releasable: bool
    #: Shown so staff see a release does not touch it. `NONE` when no MARKETING row exists.
    marketing_state: str
    #: What the guard would answer now. Advice only; the send re-checks under the lock.
    egress: EgressCheck
    evidence: tuple[ReleaseEvidenceCandidate, ...]


def release_transactional_suppression(
    connection: Any, command: TransactionalReleaseCommand
) -> StoredTransactionalRelease:
    """Lift one TRANSACTIONAL block on verified evidence. Refuses rather than guesses."""

    released_at = command.released_at or datetime.now(UTC)
    if released_at.tzinfo is None or released_at.utcoffset() is None:
        raise TransactionalConsentStateError("RELEASE_TIME_INVALID", "release time is invalid")
    channel = _channel(command.channel)
    principal = command.principal
    if not principal.mfa_verified or not principal.roles.intersection(RELEASE_ROLES):
        raise TransactionalConsentAuthorizationError(
            "an owner or approver with MFA is required to release a suppression"
        )
    with connection.transaction(), connection.cursor() as cursor:
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=command.store_id,
            error=TransactionalConsentAuthorizationError,
        )
        _require_contact_in_store(cursor, command.store_id, command.contact_binding_id)
        # The same key the STOP writer and the egress guard take.
        suppression_lock(cursor, contact_binding_id=command.contact_binding_id, channel=channel)
        cursor.execute(
            """
            SELECT s.state, s.source_consent_event_id, s.row_version, s.updated_at, c.occurred_at
            FROM suppression_entries s
            JOIN consent_events c ON c.id = s.source_consent_event_id
            WHERE s.contact_binding_id = %s AND s.purpose = 'TRANSACTIONAL' AND s.channel = %s
            FOR UPDATE OF s
            """,
            (command.contact_binding_id, channel),
        )
        row = cursor.fetchone()
        if row is None or SuppressionState(str(row[0])) not in RELEASABLE_STATES:
            raise TransactionalConsentStateError(
                "NOTHING_TO_RELEASE", "no transactional suppression blocks this contact"
            )
        previous_state = str(row[0])
        blocked_by = _uuid(row[1])
        row_version = int(str(row[2]))
        stored_updated_at = _aware(row[3])
        blocked_at = _aware(row[4])
        _require_evidence(
            cursor,
            evidence_id=command.evidence_webhook_event_id,
            contact_binding_id=command.contact_binding_id,
            channel=channel,
            blocked_at=blocked_at,
            released_at=released_at,
        )
        release_id = uuid4()
        # The projection guard refuses a clock that runs backwards; the release is dated when it
        # happened, and the row keeps whichever of the two is later.
        row_updated_at = max(released_at, stored_updated_at)

        def mutation(change_cursor: Any) -> None:
            change_cursor.execute(
                """
                INSERT INTO consent_events (
                    id, contact_binding_id, purpose, channel, event_type, registry_version,
                    evidence_webhook_id, occurred_at, released_by_staff_id, released_for_store_id
                ) VALUES (%s, %s, 'TRANSACTIONAL', %s, 'RELEASE', NULL, %s, %s, %s, %s)
                """,
                (
                    release_id,
                    command.contact_binding_id,
                    channel,
                    command.evidence_webhook_event_id,
                    released_at,
                    principal.staff_user_id,
                    command.store_id,
                ),
            )
            change_cursor.execute(
                """
                UPDATE suppression_entries
                SET state = 'CLEAR', source_consent_event_id = %s,
                    row_version = row_version + 1, updated_at = %s
                WHERE contact_binding_id = %s AND purpose = 'TRANSACTIONAL' AND channel = %s
                  AND row_version = %s
                RETURNING row_version
                """,
                (
                    release_id,
                    row_updated_at,
                    command.contact_binding_id,
                    channel,
                    row_version,
                ),
            )
            if change_cursor.fetchone() is None:
                raise TransactionalConsentStateError(
                    "SUPPRESSION_STALE", "the suppression changed while it was being released"
                )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CONSENT_SUPPRESSION",
                aggregate_id=release_id,
                aggregate_version=1,
                event_type="TRANSACTIONAL_SUPPRESSION_RELEASED",
                event_payload={
                    "contact_binding_id": str(command.contact_binding_id),
                    "channel": channel,
                    "purpose": TRANSACTIONAL,
                    "store_id": str(command.store_id),
                    "previous_state": previous_state,
                    "blocked_by_consent_event_id": str(blocked_by),
                    "evidence_webhook_event_id": str(command.evidence_webhook_event_id),
                    "decision": "DEC-033",
                },
                audit_action="CONSENT_TRANSACTIONAL_RELEASE",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "consent.changed.v1",
                        {
                            "consent_event_id": str(release_id),
                            "purpose": TRANSACTIONAL,
                            "state": SuppressionState.CLEAR.value,
                        },
                        f"consent-release:{release_id}",
                    ),
                ),
                occurred_at=released_at,
            ),
            mutation,
        )
    return StoredTransactionalRelease(
        release_consent_event_id=release_id,
        contact_binding_id=command.contact_binding_id,
        channel=channel,
        purpose=TRANSACTIONAL,
        previous_state=previous_state,
        state=SuppressionState.CLEAR.value,
        evidence_webhook_event_id=command.evidence_webhook_event_id,
        released_at=released_at,
    )


def read_service_messaging_state(
    connection: Any,
    *,
    store_id: UUID,
    contact_binding_id: UUID,
    channel: str,
    principal: StaffPrincipal,
    at: datetime,
) -> ServiceMessagingState:
    """A contact's TRANSACTIONAL state on one channel, what the guard would answer, and the
    messages a release may cite. Role and MFA, then membership, then the contact's presence in the
    shop -- in that order, so a non-member learns nothing about which contacts exist."""

    normalized = _channel(channel)
    if not principal.mfa_verified or not principal.roles.intersection(READ_ROLES):
        raise TransactionalConsentAuthorizationError("an operations role with MFA is required")
    with connection.transaction(), connection.cursor() as cursor:
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=TransactionalConsentAuthorizationError,
        )
        _require_contact_in_store(cursor, store_id, contact_binding_id)
        cursor.execute(
            """
            SELECT s.purpose, s.state, c.occurred_at
            FROM suppression_entries s
            JOIN consent_events c ON c.id = s.source_consent_event_id
            WHERE s.contact_binding_id = %s AND s.channel = %s
            """,
            (contact_binding_id, normalized),
        )
        rows = {str(row[0]): (str(row[1]), _aware(row[2])) for row in cursor.fetchall()}
        transactional = rows.get(TRANSACTIONAL)
        marketing = rows.get(MARKETING)
        releasable = transactional is not None and (
            SuppressionState(transactional[0]) in RELEASABLE_STATES
        )
        blocked_since = transactional[1] if releasable and transactional is not None else None
        evidence: tuple[ReleaseEvidenceCandidate, ...] = ()
        if blocked_since is not None:
            cursor.execute(
                """
                SELECT id, received_at
                FROM webhook_events
                WHERE contact_binding_id = %s AND channel = %s AND event_type = %s
                  AND opt_out_disposition = 'NONE' AND received_at > %s AND received_at <= %s
                ORDER BY received_at DESC, id
                LIMIT %s
                """,
                (
                    contact_binding_id,
                    normalized,
                    INBOUND_MESSAGE_EVENT_TYPE,
                    blocked_since,
                    at,
                    EVIDENCE_LIMIT,
                ),
            )
            evidence = tuple(
                ReleaseEvidenceCandidate(_uuid(row[0]), _aware(row[1])) for row in cursor.fetchall()
            )
        egress = evaluate_transactional_egress(
            cursor, contact_binding_id=contact_binding_id, channel=normalized, at=at
        )
    return ServiceMessagingState(
        store_id=store_id,
        contact_binding_id=contact_binding_id,
        channel=normalized,
        transactional_state="NONE" if transactional is None else transactional[0],
        blocked_since=blocked_since,
        releasable=releasable,
        marketing_state="NONE" if marketing is None else marketing[0],
        egress=egress,
        evidence=evidence,
    )


def _require_contact_in_store(cursor: Any, store_id: UUID, contact_binding_id: UUID) -> None:
    """The contact has a footprint in this shop: a draft, an intake request or an order.

    Contact bindings are not owned by a store, so this is how a store-scoped route decides that a
    contact is "this shop's". A contact the shop has never dealt with is answered exactly as one
    that does not exist.
    """

    cursor.execute(
        """
        SELECT EXISTS (
                   SELECT 1 FROM agent_drafts WHERE store_id = %(store)s
                   AND contact_binding_id = %(contact)s
               )
            OR EXISTS (
                   SELECT 1 FROM order_requests WHERE store_id = %(store)s
                   AND contact_binding_id = %(contact)s
               )
            OR EXISTS (
                   SELECT 1 FROM orders WHERE store_id = %(store)s
                   AND bound_contact_id = %(contact)s
               )
        """,
        {"store": store_id, "contact": contact_binding_id},
    )
    row = cursor.fetchone()
    if row is None or not row[0]:
        raise TransactionalConsentNotFoundError("contact not found")


def _require_evidence(
    cursor: Any,
    *,
    evidence_id: UUID,
    contact_binding_id: UUID,
    channel: str,
    blocked_at: datetime,
    released_at: datetime,
) -> None:
    cursor.execute(
        """
        SELECT contact_binding_id, channel, event_type, opt_out_disposition, received_at
        FROM webhook_events WHERE id = %s
        """,
        (evidence_id,),
    )
    row = cursor.fetchone()
    # One refusal for every way the evidence can fail, so the answer does not tell a caller which
    # inbound message ids exist for other contacts.
    if (
        row is None
        or row[0] is None
        or _uuid(row[0]) != contact_binding_id
        or str(row[1]) != channel
        or str(row[2]) != INBOUND_MESSAGE_EVENT_TYPE
        or str(row[3]) != "NONE"
        or not blocked_at < _aware(row[4]) <= released_at
    ):
        raise TransactionalConsentStateError(
            "RELEASE_EVIDENCE_INVALID",
            "the cited message is not a message from this contact on this channel after the stop",
        )


def _channel(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 50:
        raise TransactionalConsentStateError("CHANNEL_INVALID", "channel is invalid")
    return normalized


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TransactionalConsentStateError("STORED_TIME_INVALID", "stored timestamp is invalid")
    return value


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "EVIDENCE_LIMIT",
    "READ_ROLES",
    "RELEASABLE_STATES",
    "RELEASE_ROLES",
    "ReleaseEvidenceCandidate",
    "ServiceMessagingState",
    "StoredTransactionalRelease",
    "TransactionalConsentAuthorizationError",
    "TransactionalConsentNotFoundError",
    "TransactionalConsentStateError",
    "TransactionalReleaseCommand",
    "read_service_messaging_state",
    "release_transactional_suppression",
]
