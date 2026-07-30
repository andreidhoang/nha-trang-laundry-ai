"""Fail-closed, server-owned execution gates for future automated outbox actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from nha_trang_laundry_domain.catalog import ActorRole

from .transactions import MaterialChange, OutboxEvent, commit_material_change


class AutomationGateError(ValueError):
    """A future automated execution cannot safely proceed."""


@dataclass(frozen=True, slots=True)
class AutomationGate:
    capability: str
    global_automation_enabled: bool
    agent_processing_enabled: bool
    agent_outbound_enabled: bool
    channel_ingress_enabled: bool
    capability_enabled: bool
    stage_policy_allows: bool
    pdp_allows: bool
    version: int
    expires_at: datetime

    def allows(self, now: datetime) -> bool:
        return (
            self.expires_at >= now
            and self.global_automation_enabled
            and self.agent_processing_enabled
            and self.agent_outbound_enabled
            and self.channel_ingress_enabled
            and self.capability_enabled
            and self.stage_policy_allows
            and self.pdp_allows
        )


class AutomationExecutionRepository:
    """Re-evaluate gates immediately before execution and atomically hold disabled work."""

    def hold_if_disabled(
        self,
        connection: Any,
        *,
        envelope_id: UUID,
        actor_id: UUID,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> str:
        timestamp = now or datetime.now(UTC)
        result: list[str] = []

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                SELECT envelope.status, envelope.hold_policy, envelope.capability,
                       gate.global_automation_enabled, gate.agent_processing_enabled,
                       gate.agent_outbound_enabled, gate.channel_ingress_enabled,
                       gate.capability_enabled, gate.stage_policy_allows, gate.pdp_allows,
                       gate.expires_at
                FROM automated_execution_envelopes AS envelope
                LEFT JOIN automation_execution_gates AS gate
                    ON gate.capability = envelope.capability
                WHERE envelope.id = %s
                FOR UPDATE OF envelope
                """,
                (envelope_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise AutomationGateError("automated execution envelope is unavailable")
            if str(row[0]) != "PENDING":
                result.append(str(row[0]))
                return
            allowed = _row_allows(row[3:], timestamp)
            if allowed:
                result.append("PENDING")
                return
            target = "HELD" if str(row[1]) == "HOLD" else "CANCELLED"
            cursor.execute(
                """
                UPDATE automated_execution_envelopes
                SET status = %s, held_at = %s, hold_reason = 'AUTOMATION_DISABLED',
                    row_version = row_version + 1
                WHERE id = %s AND status = 'PENDING'
                """,
                (target, timestamp, envelope_id),
            )
            result.append(target)

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="AUTOMATED_EXECUTION_ENVELOPE",
                aggregate_id=envelope_id,
                aggregate_version=2,
                event_type="AUTOMATED_EXECUTION_HELD",
                event_payload={"reason": "AUTOMATION_DISABLED"},
                audit_action="AUTOMATED_EXECUTION_HOLD",
                actor_type=ActorRole.OUTBOX_WORKER.value,
                actor_id=actor_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "automation.execution_held.v1",
                        {"envelope_id": str(envelope_id)},
                        f"automated-execution:{envelope_id}:held",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )
        if not result:
            raise AutomationGateError("automated execution gate produced no result")
        return result[0]


def _row_allows(values: tuple[object, ...], now: datetime) -> bool:
    if len(values) != 8 or not isinstance(values[-1], datetime) or values[-1].tzinfo is None:
        return False
    return values[-1] >= now and all(value is True for value in values[:-1])


__all__ = ["AutomationExecutionRepository", "AutomationGate", "AutomationGateError"]
