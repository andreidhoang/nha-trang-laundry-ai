"""Final fail-closed suppression check for claimed marketing outbox work."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from nha_trang_laundry_domain.catalog import ActorRole

from .transactions import MaterialChange, OutboxEvent, commit_material_change


class MarketingDeliveryAuthorizationError(PermissionError):
    """Only the outbox worker may perform the final marketing authorization check."""


class MarketingDeliveryStateError(ValueError):
    """The claimed outbox event cannot be evaluated without guessing policy or identity."""


class MarketingDeliveryRepository:
    """Hold claimed marketing work when suppression is present or cannot be proven clear."""

    def hold_if_not_authorized(
        self,
        connection: Any,
        *,
        outbox_event_id: UUID,
        claim_token: UUID,
        worker_role: ActorRole,
        actor_id: UUID,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> str:
        if worker_role is not ActorRole.OUTBOX_WORKER:
            raise MarketingDeliveryAuthorizationError("only OUTBOX_WORKER may authorize delivery")
        timestamp = now or datetime.now(UTC)
        result: list[str] = []

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                SELECT event.status, event.purpose, event.recipient_binding_id,
                       event.payload->>'channel', suppression.state
                FROM outbox_events AS event
                LEFT JOIN suppression_entries AS suppression
                  ON suppression.contact_binding_id = event.recipient_binding_id
                 AND suppression.purpose = 'MARKETING'
                 AND suppression.channel = event.payload->>'channel'
                WHERE event.id = %s AND event.claim_token = %s
                  AND event.lease_expires_at >= %s
                FOR UPDATE OF event
                """,
                (outbox_event_id, claim_token, timestamp),
            )
            row = cursor.fetchone()
            if row is None:
                raise MarketingDeliveryStateError("marketing outbox event is unavailable")
            if row[0] != "PROCESSING" or row[1] != "MARKETING":
                raise MarketingDeliveryStateError("marketing outbox event is not a claimed send")
            if row[2] is None or not isinstance(row[3], str) or not row[3]:
                reason = "RECIPIENT_OR_CHANNEL_UNAVAILABLE"
            elif row[4] == "SUPPRESSED":
                reason = "SUPPRESSED_CONTACT"
            else:
                # R1 has no positive consent projection or authorized marketing send path.
                reason = "MARKETING_AUTHORIZATION_UNAVAILABLE"
            cursor.execute(
                """
                UPDATE outbox_events
                SET status = 'HELD', held_reason = %s, claim_token = NULL,
                    claimed_at = NULL, lease_expires_at = NULL
                WHERE id = %s AND status = 'PROCESSING' AND claim_token = %s
                RETURNING id
                """,
                (reason, outbox_event_id, claim_token),
            )
            if cursor.fetchone() is None:
                raise MarketingDeliveryStateError("marketing outbox claim is stale")
            result.append(reason)

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="MARKETING_DELIVERY",
                aggregate_id=outbox_event_id,
                aggregate_version=1,
                event_type="MARKETING_DELIVERY_HELD",
                event_payload={"reason": "FINAL_AUTHORIZATION_DENIED"},
                audit_action="MARKETING_DELIVERY_HOLD",
                actor_type=ActorRole.OUTBOX_WORKER.value,
                actor_id=actor_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "marketing.delivery_held.v1",
                        {"outbox_event_id": str(outbox_event_id)},
                        f"marketing-delivery:{outbox_event_id}:held",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )
        if not result:
            raise MarketingDeliveryStateError("marketing authorization produced no result")
        return result[0]


__all__ = [
    "MarketingDeliveryAuthorizationError",
    "MarketingDeliveryRepository",
    "MarketingDeliveryStateError",
]
