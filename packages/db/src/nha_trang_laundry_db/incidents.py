"""Atomic customer-incident intake and automated-message correction containment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import ActorRole

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
class StoredIncident:
    incident_id: UUID
    status: str
    fault_decided: bool
    remedy_decided: bool


class IncidentRepository:
    def open(self, connection: Any, command: IncidentOpenCommand) -> StoredIncident:
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
                actor_type=ActorRole.AGENT_RUNNER.value,
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
    ):
        raise ValueError("incident binding is invalid")


def _insert_incident(cursor: Any, incident_id: UUID, command: IncidentOpenCommand) -> None:
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
    "StoredIncident",
]
