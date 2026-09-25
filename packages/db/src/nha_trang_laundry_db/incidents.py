"""Atomic customer-incident intake and automated-message correction containment.

Two callers, deliberately separate commands. The agent path supplies its own `contact_scope_hash`
and `evidence_summary_hash`, because it resolves facts against a contact scope and has producers for
both. The counter path has neither, which is why `#/incidents` could not be completed by anybody
until `DEC-028`: a customer said their shirt came back stained and the staff member had two required
`sha256:` fields and nowhere to get them.

`DEC-028` derives the contact scope on the server and stores the summary. The derivation is not a
convenience: `DEC-013` already settled that a walk-in's counter ticket *is* the customer, so the
order's binding is the scope, and invariant 9 keeps a staff member from naming a scope for a
customer of their choosing. The summary is stored rather than hashed-and-discarded because a hash of
text nobody can read back proves only that a discarded string did not change, and `DEC-004`'s remedy
decision has to be made from something.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import ActorRole

from .identity import StaffPrincipal
from .store_access import StoreAccessError, require_store_membership
from .transactions import MaterialChange, OutboxEvent, commit_material_change


@dataclass(frozen=True, slots=True)
class IncidentOpenCommand:
    store_id: UUID
    order_id: UUID | None
    affected_message_id: UUID | None
    affected_policy_version: str | None
    contact_scope_hash: str
    category: str
    evidence_summary_hash: str
    actor_id: UUID
    correlation_id: UUID
    opened_at: datetime
    actor_type: str = ActorRole.AGENT_RUNNER.value


@dataclass(frozen=True, slots=True)
class CorrectionOpenCommand:
    store_id: UUID
    affected_message_id: UUID
    affected_policy_version: str
    contact_scope_hash: str
    evidence_summary_hash: str
    rendered_hash: str
    affected_capability: str
    actor_id: UUID
    correlation_id: UUID
    opened_at: datetime


@dataclass(frozen=True, slots=True)
class StaffIncidentOpenCommand:
    """A complaint taken at the counter, against an order the shop already has.

    No hash fields: both are derived by the server inside the opening transaction. The agent path
    keeps `IncidentOpenCommand` unchanged, because it has real producers for both and its own
    contract governs them.
    """

    store_id: UUID
    order_id: UUID
    evidence_summary: str
    actor_id: UUID
    correlation_id: UUID
    opened_at: datetime
    category: str = "SERVICE_QUALITY"


@dataclass(frozen=True, slots=True)
class StoredIncident:
    incident_id: UUID
    status: str
    fault_decided: bool
    remedy_decided: bool


@dataclass(frozen=True, slots=True)
class IncidentSummary:
    incident_id: UUID
    store_id: UUID
    order_id: UUID | None
    category: str
    status: str
    fault_decided: bool
    remedy_decided: bool
    opened_at: datetime
    #: What the staff member wrote. `None` for an incident opened by the agent path, which stores no
    #: summary, and for one whose evidence has been purged under `INCIDENT_EVIDENCE` at 365 days.
    #: The two are distinguishable from `retention_disposal_records`, not from this field.
    evidence_summary: str | None = None
    #: `READ-ENRICH-001`. The walk-in ticket of the incident's order ("Phiếu 17"), read through
    #: the order's customer reference within the order's own store. Null when the incident names no
    #: order or the order's customer is a channel binding.
    ticket_number: int | None = None
    ticket_issued_on: date | None = None


#: The incident read model, shared by the store list, the per-order list and the read by id so the
#: three cannot disagree about a field. LEFT JOINs throughout: an agent-opened incident has no
#: summary and a purged one no longer has its summary, and neither may disappear from a list -- an
#: incident whose text is gone still has to be visible, which is the whole difference between a
#: disposal and a deletion. The order and its ticket are joined within the incident's own store.
_INCIDENT_SUMMARY_SELECT = """
    SELECT i.id, i.store_id, i.order_id, i.category, i.status, i.fault_decided,
           i.remedy_decided, i.opened_at, e.summary, t.ticket_number, t.issued_on
    FROM customer_incidents i
    LEFT JOIN customer_incident_evidence e ON e.incident_id = i.id
    LEFT JOIN orders o ON o.id = i.order_id AND o.store_id = i.store_id
    LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
"""


def contact_scope_digest(bound_contact_id: UUID) -> str:
    """The scope a counter incident is recorded against: the order's bound contact, hashed.

    Domain-separated by a version prefix so this digest can never collide with another `sha256:`
    commitment in the system, and so a second generation is telling rather than silent.

    The preimage is a UUID the system minted rather than customer content, so unlike
    `payload_hash` this digest carries no reversibility residual and `HASH-KEYING-001` need not
    reach it.
    """

    return f"sha256:{sha256(f'CONTACT-SCOPE-V1:{bound_contact_id}'.encode()).hexdigest()}"


def evidence_summary_digest(summary: str) -> str:
    """Commit to the summary text exactly as it will be stored.

    NFC first: Vietnamese arrives from a browser in either normalization depending on the input
    method, and two spellings of the same sentence must not produce two hashes. Normalizing here and
    storing the normalized form is what keeps the hash a statement about the stored bytes.
    """

    return f"sha256:{sha256(unicodedata.normalize('NFC', summary).encode()).hexdigest()}"


class IncidentRepository:
    def open(
        self,
        connection: Any,
        command: IncidentOpenCommand,
        *,
        principal: StaffPrincipal | None = None,
    ) -> StoredIncident:
        # Authorization precedes validation on purpose: a caller with no access to this store
        # should not learn what is wrong with their payload.
        if principal is not None:
            with connection.cursor() as membership_cursor:
                require_store_membership(
                    membership_cursor,
                    staff_user_id=principal.staff_user_id,
                    store_id=command.store_id,
                    error=StoreAccessError,
                )
        _validate_incident(command)
        incident_id = uuid4()

        def mutation(cursor: Any) -> None:
            _insert_incident(cursor, incident_id, command)

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CUSTOMER_INCIDENT",
                aggregate_id=incident_id,
                aggregate_version=1,
                event_type="INCIDENT_OPENED",
                event_payload={"category": command.category, "order_id": str(command.order_id)},
                audit_action="INCIDENT_OPEN",
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "incident.opened.v1",
                        {"incident_id": str(incident_id)},
                        f"incident:{incident_id}:opened",
                    ),
                ),
                occurred_at=command.opened_at,
            ),
            mutation,
        )
        return StoredIncident(incident_id, "OPEN", False, False)

    def open_from_counter(
        self,
        connection: Any,
        command: StaffIncidentOpenCommand,
        *,
        principal: StaffPrincipal,
    ) -> StoredIncident:
        """Open an incident taken in person, deriving both digests on the server."""

        with connection.cursor() as membership_cursor:
            require_store_membership(
                membership_cursor,
                staff_user_id=principal.staff_user_id,
                store_id=command.store_id,
                error=StoreAccessError,
            )
        summary = unicodedata.normalize("NFC", command.evidence_summary).strip()
        if not 1 <= len(summary) <= 2000:
            raise ValueError("an incident summary must be between 1 and 2000 characters")
        if command.opened_at.tzinfo is None:
            raise ValueError("incident binding is invalid")
        incident_id = uuid4()

        def mutation(cursor: Any) -> None:
            # Read inside the transaction, so the binding that is hashed is the binding that exists
            # when the row is written. `orders.bound_contact_id` cannot change under us --
            # `enforce_order_projection_update` names it immutable -- but the order's existence and
            # its store are what this check is really for, and those belong in the same transaction.
            cursor.execute(
                "SELECT bound_contact_id FROM orders WHERE id = %s AND store_id = %s",
                (command.order_id, command.store_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("incident order binding is unavailable")
            cursor.execute(
                """
                INSERT INTO customer_incidents (
                    id, store_id, order_id, affected_message_id, affected_policy_version,
                    contact_scope_hash, category, status, fault_decided, remedy_decided,
                    evidence_summary_hash, opened_at
                ) VALUES (%s, %s, %s, NULL, NULL, %s, %s, 'OPEN', FALSE, FALSE, %s, %s)
                """,
                (
                    incident_id,
                    command.store_id,
                    command.order_id,
                    contact_scope_digest(_uuid(row[0])),
                    command.category,
                    evidence_summary_digest(summary),
                    command.opened_at,
                ),
            )
            cursor.execute(
                "INSERT INTO customer_incident_evidence (incident_id, summary) VALUES (%s, %s)",
                (incident_id, summary),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CUSTOMER_INCIDENT",
                aggregate_id=incident_id,
                # The summary is deliberately absent from the event, the audit row and the outbox
                # row. It is personal data on a 365-day schedule, and a copy in an append-only
                # ledger would outlive the purge and make it a false statement -- the DEC-018 shape,
                # avoided here rather than discovered later.
                aggregate_version=1,
                event_type="INCIDENT_OPENED",
                event_payload={
                    "category": command.category,
                    "order_id": str(command.order_id),
                    "opened_by": "STAFF",
                },
                audit_action="INCIDENT_OPEN",
                actor_type="STAFF",
                actor_id=command.actor_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "incident.opened.v1",
                        {"incident_id": str(incident_id)},
                        f"incident:{incident_id}:opened",
                    ),
                ),
                occurred_at=command.opened_at,
            ),
            mutation,
        )
        return StoredIncident(incident_id, "OPEN", False, False)

    @staticmethod
    def list_for_store(
        cursor: Any, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[IncidentSummary, ...]:
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        if not 1 <= limit <= 200:
            raise ValueError("incident list limit must be between 1 and 200")
        cursor.execute(
            _INCIDENT_SUMMARY_SELECT
            + """
            WHERE i.store_id = %s
            ORDER BY i.opened_at DESC, i.id DESC
            LIMIT %s
            """,
            (store_id, limit),
        )
        return tuple(_incident_summary(row) for row in cursor.fetchall())

    @staticmethod
    def list_for_order(
        cursor: Any, *, store_id: UUID, order_id: UUID, principal: StaffPrincipal, limit: int = 100
    ) -> tuple[IncidentSummary, ...]:
        """One order's incidents, newest first. `READ-ENRICH-001`.

        Membership of the named store first, then both the store and the order in the predicate:
        another store's order id reads as an empty list, exactly what an id that does not exist
        reads as, so the route teaches nobody which orders exist elsewhere.
        """

        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        if not 1 <= limit <= 200:
            raise ValueError("incident list limit must be between 1 and 200")
        cursor.execute(
            _INCIDENT_SUMMARY_SELECT
            + """
            WHERE i.store_id = %s AND i.order_id = %s
            ORDER BY i.opened_at DESC, i.id DESC
            LIMIT %s
            """,
            (store_id, order_id, limit),
        )
        return tuple(_incident_summary(row) for row in cursor.fetchall())

    @staticmethod
    def read_for_store(
        cursor: Any, *, store_id: UUID, incident_id: UUID, principal: StaffPrincipal
    ) -> IncidentSummary | None:
        """One incident of this store, or `None` -- another store's and a missing one alike."""

        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        cursor.execute(
            _INCIDENT_SUMMARY_SELECT + " WHERE i.id = %s AND i.store_id = %s",
            (incident_id, store_id),
        )
        row = cursor.fetchone()
        return None if row is None else _incident_summary(row)

    def open_correction(self, connection: Any, command: CorrectionOpenCommand) -> StoredIncident:
        if not command.affected_policy_version.strip() or not command.affected_capability.strip():
            raise ValueError("correction policy and capability are required")
        incident = IncidentOpenCommand(
            command.store_id,
            None,
            command.affected_message_id,
            command.affected_policy_version,
            command.contact_scope_hash,
            "AUTOMATED_MESSAGE_ERROR",
            command.evidence_summary_hash,
            command.actor_id,
            command.correlation_id,
            command.opened_at,
        )
        _validate_incident(incident)
        incident_id, draft_id = uuid4(), uuid4()

        def mutation(cursor: Any) -> None:
            _insert_incident(cursor, incident_id, incident)
            cursor.execute(
                """
                INSERT INTO customer_correction_drafts (
                    id, incident_id, affected_message_id, affected_policy_version,
                    rendered_hash, approval_required, status, created_at
                ) VALUES (%s, %s, %s, %s, %s, TRUE, 'PENDING_APPROVAL', %s)
                """,
                (
                    draft_id,
                    incident_id,
                    command.affected_message_id,
                    command.affected_policy_version,
                    command.rendered_hash,
                    command.opened_at,
                ),
            )
            cursor.execute(
                """
                INSERT INTO automation_execution_gates (
                    capability, version, expires_at, updated_at
                ) VALUES (%s, 1, %s, %s)
                ON CONFLICT (capability) DO UPDATE SET
                    global_automation_enabled = FALSE,
                    agent_processing_enabled = FALSE,
                    agent_outbound_enabled = FALSE,
                    channel_ingress_enabled = FALSE,
                    capability_enabled = FALSE,
                    stage_policy_allows = FALSE,
                    pdp_allows = FALSE,
                    version = automation_execution_gates.version + 1,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    command.affected_capability,
                    command.opened_at + timedelta(days=1),
                    command.opened_at,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CUSTOMER_INCIDENT",
                aggregate_id=incident_id,
                aggregate_version=1,
                event_type="CUSTOMER_CORRECTION_REVIEW_OPENED",
                event_payload={
                    "affected_message_id": str(command.affected_message_id),
                    "affected_capability": command.affected_capability,
                },
                audit_action="CUSTOMER_CORRECTION_OPEN",
                actor_type=ActorRole.AGENT_RUNNER.value,
                actor_id=command.actor_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "customer.correction_review_opened.v1",
                        {"incident_id": str(incident_id), "draft_id": str(draft_id)},
                        f"incident:{incident_id}:correction-review",
                    ),
                ),
                occurred_at=command.opened_at,
            ),
            mutation,
        )
        return StoredIncident(incident_id, "OPEN", False, False)


def _validate_incident(command: IncidentOpenCommand) -> None:
    if (
        command.opened_at.tzinfo is None
        or (command.order_id is None and command.affected_message_id is None)
        or command.category not in {"SERVICE_QUALITY", "AUTOMATED_MESSAGE_ERROR"}
        or command.actor_type not in {"STAFF", ActorRole.AGENT_RUNNER.value}
    ):
        raise ValueError("incident binding is invalid")


def _insert_incident(cursor: Any, incident_id: UUID, command: IncidentOpenCommand) -> None:
    if command.order_id is not None:
        cursor.execute(
            "SELECT id FROM orders WHERE id = %s AND store_id = %s",
            (command.order_id, command.store_id),
        )
        if cursor.fetchone() is None:
            raise ValueError("incident order binding is unavailable")
    cursor.execute(
        """
        INSERT INTO customer_incidents (
            id, store_id, order_id, affected_message_id, affected_policy_version,
            contact_scope_hash, category, status, fault_decided, remedy_decided,
            evidence_summary_hash, opened_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN', FALSE, FALSE, %s, %s)
        """,
        (
            incident_id,
            command.store_id,
            command.order_id,
            command.affected_message_id,
            command.affected_policy_version,
            command.contact_scope_hash,
            command.category,
            command.evidence_summary_hash,
            command.opened_at,
        ),
    )


__all__ = [
    "CorrectionOpenCommand",
    "IncidentOpenCommand",
    "IncidentRepository",
    "IncidentSummary",
    "StaffIncidentOpenCommand",
    "StoredIncident",
    "contact_scope_digest",
    "evidence_summary_digest",
]


def _incident_summary(row: Any) -> IncidentSummary:
    return IncidentSummary(
        _uuid(row[0]),
        _uuid(row[1]),
        None if row[2] is None else _uuid(row[2]),
        str(row[3]),
        str(row[4]),
        bool(row[5]),
        bool(row[6]),
        _datetime(row[7]),
        None if row[8] is None else str(row[8]),
        ticket_number=None if row[9] is None else int(row[9]),
        ticket_issued_on=row[10] if isinstance(row[10], date) else None,
    )


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("stored incident timestamp is invalid")
    return value


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
