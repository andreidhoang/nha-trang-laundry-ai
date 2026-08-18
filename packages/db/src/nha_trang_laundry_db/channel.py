"""Server-owned contact binding and per-attempt send receipts for the canonical channel envelope.

Two responsibilities, both deliberately narrow:

* resolve a provider identity to a contact binding the server owns, creating an `UNVERIFIED` one for
  an identity nobody has vouched for. `UNVERIFIED` carries no access to order status, quotes or any
  customer-specific fact; the caller enforces that, and the verification state is the signal;
* record exactly one receipt per send attempt, so that a send whose outcome nobody observed is
  visible as `UNKNOWN` rather than invisible.

This module contains no business logic, no policy evaluation, no pricing and no model call, and it
never sends. The provider-specific sender worker sends, driven by the outbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_contracts.channel_envelope import (
    AMBIGUOUS_OUTCOMES,
    RESOLVED_STATES,
    ChannelOutboundReceipt,
    ChannelProvider,
    ContactBindingRef,
    ContactVerificationState,
    ReconciliationState,
    SendAttemptOutcome,
)

from .transactions import MaterialChange, OutboxEvent, commit_material_change

_HUMAN_RECONCILIATION_STATES = frozenset(
    {ReconciliationState.UNKNOWN, ReconciliationState.UNKNOWN_REQUIRES_HUMAN}
)


class ChannelBindingError(ValueError):
    """Raised when a contact binding cannot be resolved safely."""


class ChannelReceiptError(ValueError):
    """Raised when a send receipt would record an unsafe or unreconcilable outcome."""


@dataclass(frozen=True, slots=True)
class ResolvedContactBinding:
    binding: ContactBindingRef
    created: bool


class ContactChannelBindingRepository:
    """Resolve provider identity to a server-owned contact binding, never the reverse."""

    @staticmethod
    def binding_exists(cursor: Any, *, contact_binding_id: UUID) -> bool:
        """Whether a binding id names a row the server itself recorded.

        The staff intake path receives a contact binding id from a person, not from a verified
        channel envelope, so the id must be proven against this table before an order request may
        name it. Existence is all this answers — an `UNVERIFIED` binding exists, and creating a
        draft that names it grants the contact nothing.
        """
        cursor.execute(
            """
            SELECT 1 FROM contact_channel_bindings WHERE contact_binding_id = %s
            """,
            (contact_binding_id,),
        )
        return cursor.fetchone() is not None

    def resolve_or_create(
        self,
        connection: Any,
        *,
        provider: ChannelProvider,
        provider_user_ref: str,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> ResolvedContactBinding:
        reference = provider_user_ref.strip()
        if not reference or len(reference) > 200:
            raise ChannelBindingError("provider user reference is outside the accepted length")
        timestamp = now or datetime.now(UTC)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT contact_binding_id, verification_state
                FROM contact_channel_bindings
                WHERE provider = %s AND provider_user_ref = %s
                """,
                (provider.value, reference),
            )
            row = cursor.fetchone()
        if row is not None:
            return ResolvedContactBinding(
                ContactBindingRef(
                    provider_user_ref=reference,
                    contact_id=_uuid(row[0]),
                    verification_state=ContactVerificationState(str(row[1])),
                ),
                created=False,
            )

        contact_binding_id = uuid4()

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO contact_channel_bindings (
                    provider, provider_user_ref, contact_binding_id, verification_state,
                    row_version, created_at, updated_at
                ) VALUES (%s, %s, %s, 'UNVERIFIED', 1, %s, %s)
                ON CONFLICT (provider, provider_user_ref) DO NOTHING
                """,
                (provider.value, reference, contact_binding_id, timestamp, timestamp),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CONTACT_CHANNEL_BINDING",
                aggregate_id=contact_binding_id,
                aggregate_version=1,
                event_type="CONTACT_CHANNEL_BINDING_CREATED",
                event_payload={
                    "provider": provider.value,
                    "verification_state": ContactVerificationState.UNVERIFIED.value,
                },
                audit_action="CONTACT_CHANNEL_BINDING_CREATE",
                actor_type="PROVIDER",
                actor_id=None,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "contact.binding_created.v1",
                        {
                            "contact_binding_id": str(contact_binding_id),
                            "provider": provider.value,
                            "verification_state": ContactVerificationState.UNVERIFIED.value,
                        },
                        f"contact-binding:{provider.value}:{contact_binding_id}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )

        # A concurrent request may have won the insert; the server-owned row is authoritative.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT contact_binding_id, verification_state
                FROM contact_channel_bindings
                WHERE provider = %s AND provider_user_ref = %s
                """,
                (provider.value, reference),
            )
            stored = cursor.fetchone()
        if stored is None:
            raise ChannelBindingError("contact binding disappeared after insert")
        resolved_id = _uuid(stored[0])
        return ResolvedContactBinding(
            ContactBindingRef(
                provider_user_ref=reference,
                contact_id=resolved_id,
                verification_state=ContactVerificationState(str(stored[1])),
            ),
            created=resolved_id == contact_binding_id,
        )


class ChannelSendReceiptRepository:
    """One row per send attempt. An attempt with no receipt did not happen for reconciliation."""

    def record_attempt(
        self,
        connection: Any,
        receipt: ChannelOutboundReceipt,
        *,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> UUID:
        _reject_unreconcilable(receipt)
        timestamp = now or datetime.now(UTC)
        authorization = receipt.authorization
        attempt = receipt.attempt
        resolution = receipt.resolution

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO channel_send_receipts (
                    receipt_id, outbox_id, idempotency_key, provider, message_kind,
                    authorization_source, approval_ref, capability, egress_suppression_check,
                    messaging_window, attempt_number, attempt_started_at, attempt_completed_at,
                    attempt_outcome, provider_message_ref, provider_error_code, rate_limited,
                    delivery_status, reconciliation_state, resolved_by, resolved_at,
                    resolution_actor_id, resolution_note, recorded_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                (
                    receipt.receipt_id,
                    receipt.outbox_id,
                    receipt.idempotency_key,
                    receipt.provider.value,
                    receipt.message_kind.value,
                    authorization.source.value,
                    authorization.approval_ref,
                    authorization.capability.value if authorization.capability else None,
                    authorization.egress_suppression_check,
                    authorization.messaging_window.value
                    if authorization.messaging_window
                    else None,
                    attempt.attempt_number,
                    attempt.started_at,
                    attempt.completed_at,
                    attempt.outcome.value,
                    attempt.provider_message_ref,
                    attempt.provider_error_code,
                    attempt.rate_limited,
                    receipt.delivery_status.value,
                    receipt.reconciliation_state.value,
                    resolution.resolved_by if resolution else None,
                    resolution.resolved_at if resolution else None,
                    resolution.actor_id if resolution else None,
                    resolution.note if resolution else None,
                    timestamp,
                ),
            )

        # The aggregate is the outbox row and the version is the attempt number, so
        # `domain_events`' UNIQUE (aggregate_type, aggregate_id, aggregate_version, event_type) and
        # the outbox idempotency key both reject a second recording of the same attempt. Two workers
        # racing one attempt lose at the constraint rather than at an application check.
        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CHANNEL_SEND",
                aggregate_id=receipt.outbox_id,
                aggregate_version=attempt.attempt_number,
                event_type="CHANNEL_SEND_ATTEMPT_RECORDED",
                event_payload={
                    "receipt_id": str(receipt.receipt_id),
                    "provider": receipt.provider.value,
                    "message_kind": receipt.message_kind.value,
                    "attempt_number": attempt.attempt_number,
                    "outcome": attempt.outcome.value,
                    "delivery_status": receipt.delivery_status.value,
                    "reconciliation_state": receipt.reconciliation_state.value,
                },
                audit_action="CHANNEL_SEND_ATTEMPT_RECORD",
                actor_type="WORKER",
                actor_id=None,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "channel.send_attempt_recorded.v1",
                        {
                            "receipt_id": str(receipt.receipt_id),
                            "outbox_id": str(receipt.outbox_id),
                            "attempt_number": attempt.attempt_number,
                            "reconciliation_state": receipt.reconciliation_state.value,
                            "requires_human": receipt.reconciliation_state
                            in _HUMAN_RECONCILIATION_STATES,
                        },
                        f"channel-receipt:{receipt.outbox_id}:{attempt.attempt_number}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )
        return receipt.receipt_id

    @staticmethod
    def unresolved_unknown_outcomes(connection: Any, *, limit: int = 100) -> tuple[UUID, ...]:
        """Return receipts awaiting a provider confirmation or a human decision."""

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT receipt_id
                FROM channel_send_receipts
                WHERE reconciliation_state IN ('UNKNOWN', 'UNKNOWN_REQUIRES_HUMAN')
                ORDER BY recorded_at
                LIMIT %s
                """,
                (limit,),
            )
            return tuple(_uuid(row[0]) for row in cursor.fetchall())


def _reject_unreconcilable(receipt: ChannelOutboundReceipt) -> None:
    """Refuse a receipt that would let an unobserved send look settled."""

    if (
        receipt.attempt.outcome in AMBIGUOUS_OUTCOMES
        and receipt.reconciliation_state is ReconciliationState.NOT_REQUIRED
    ):
        raise ChannelReceiptError("an ambiguous provider outcome cannot be NOT_REQUIRED")
    if receipt.reconciliation_state in RESOLVED_STATES and receipt.resolution is None:
        raise ChannelReceiptError("a resolved reconciliation state requires its resolution record")
    if (
        receipt.attempt.outcome is SendAttemptOutcome.ACCEPTED
        and receipt.reconciliation_state is ReconciliationState.UNKNOWN_REQUIRES_HUMAN
    ):
        raise ChannelReceiptError("an accepted send is not an unknown outcome")


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "ChannelBindingError",
    "ChannelReceiptError",
    "ChannelSendReceiptRepository",
    "ContactChannelBindingRepository",
    "ResolvedContactBinding",
]
