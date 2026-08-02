"""Durable Agent Runner queue and safe tool-call ledger; no provider or send client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    AgentToolOperation,
    ReleaseCapability,
)
from nha_trang_laundry_domain.catalog import ActorRole

from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change


class AgentRunStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DRAFT_REQUIRES_HUMAN = "DRAFT_REQUIRES_HUMAN"
    FAILED = "FAILED"


class AgentRunStateError(ValueError):
    """Agent-run queue record is invalid, stale, or inconsistent with its safe ledger."""


class AgentRunAuthorizationError(PermissionError):
    """A non-AGENT_RUNNER identity attempted a run queue mutation."""


@dataclass(frozen=True)
class AgentRunEnqueueCommand:
    agent_run_id: UUID
    source_webhook_event_id: UUID | None
    organization_id: UUID
    store_id: UUID
    channel: str
    conversation_binding_id: UUID
    contact_binding_id: UUID
    capability: ReleaseCapability
    deployment_stage: AgentDeploymentStage
    data_classification: AgentDataClassification
    runtime_registry_version: str
    runtime_registry_hash: str
    prompt_bundle_version: str
    prompt_bundle_hash: str
    tool_contract_hash: str
    correlation_id: UUID
    order_request_id: UUID | None = None
    public_code: str | None = None
    bound_row_version: int = 0
    created_at: datetime | None = None


@dataclass(frozen=True)
class ClaimedAgentRun:
    agent_run_id: UUID
    claim_token: UUID
    organization_id: UUID
    store_id: UUID
    channel: str
    conversation_binding_id: UUID
    contact_binding_id: UUID
    capability: ReleaseCapability
    deployment_stage: AgentDeploymentStage
    data_classification: AgentDataClassification
    runtime_registry_version: str
    runtime_registry_hash: str
    prompt_bundle_version: str
    prompt_bundle_hash: str
    tool_contract_hash: str
    order_request_id: UUID | None
    public_code: str | None
    bound_row_version: int
    attempt_count: int
    lease_expires_at: datetime


@dataclass(frozen=True)
class AgentToolCallLedgerEntry:
    agent_run_id: UUID
    claim_token: UUID
    sequence_number: int
    operation: AgentToolOperation
    request_fingerprint: str
    result_status_code: int
    result_code: str
    trace_id: str | None
    safe_summary: dict[str, object]
    started_at: datetime
    completed_at: datetime
    correlation_id: UUID


class AgentRunRepository:
    """Queue claims and completion are server-owned and atomically event/audit/outbox backed."""

    def enqueue(self, connection: Any, command: AgentRunEnqueueCommand) -> None:
        _validate_enqueue(command)
        occurred_at = command.created_at or datetime.now(UTC)

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO agent_runs (
                    id, source_webhook_event_id, organization_id, store_id, channel,
                    conversation_binding_id, contact_binding_id, capability, deployment_stage,
                    data_classification, runtime_registry_version, runtime_registry_hash,
                    prompt_bundle_version, prompt_bundle_hash, tool_contract_hash, status,
                    order_request_id, public_code, bound_row_version, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING',
                    %s, %s, %s, %s
                )
                """,
                (
                    command.agent_run_id,
                    command.source_webhook_event_id,
                    command.organization_id,
                    command.store_id,
                    command.channel,
                    command.conversation_binding_id,
                    command.contact_binding_id,
                    command.capability.value,
                    command.deployment_stage.value,
                    command.data_classification.value,
                    command.runtime_registry_version,
                    command.runtime_registry_hash,
                    command.prompt_bundle_version,
                    command.prompt_bundle_hash,
                    command.tool_contract_hash,
                    command.order_request_id,
                    command.public_code,
                    command.bound_row_version,
                    occurred_at,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="AGENT_RUN",
                aggregate_id=command.agent_run_id,
                aggregate_version=1,
                event_type="AGENT_RUN_ENQUEUED",
                event_payload={
                    "capability": command.capability.value,
                    "stage": command.deployment_stage.value,
                    "data_classification": command.data_classification.value,
                },
                audit_action="AGENT_RUN_ENQUEUE",
                actor_type="AGENT_RUNNER",
                actor_id=None,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "agent.run_requested.v1",
                        {"agent_run_id": str(command.agent_run_id)},
                        f"agent-run:{command.agent_run_id}:requested",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            mutation,
        )

    def claim_next(
        self,
        connection: Any,
        *,
        worker_role: ActorRole,
        now: datetime | None = None,
    ) -> ClaimedAgentRun | None:
        _require_runner(worker_role)
        claimed_at = now or datetime.now(UTC)
        claim_token = uuid4()
        lease_expires_at = claimed_at + timedelta(seconds=20)
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT id FROM agent_runs
                    WHERE status = 'PENDING'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE agent_runs AS run
                SET status = 'PROCESSING', attempt_count = attempt_count + 1,
                    claim_token = %s, claimed_at = %s, lease_expires_at = %s
                FROM candidate
                WHERE run.id = candidate.id
                RETURNING run.id, run.organization_id, run.store_id, run.channel,
                    run.conversation_binding_id, run.contact_binding_id, run.capability,
                    run.deployment_stage, run.data_classification, run.runtime_registry_version,
                    run.runtime_registry_hash, run.prompt_bundle_version, run.prompt_bundle_hash,
                    run.tool_contract_hash, run.order_request_id, run.public_code,
                    run.bound_row_version, run.attempt_count, run.lease_expires_at
                """,
                (claim_token, claimed_at, lease_expires_at),
            )
            row = cursor.fetchone()
        return None if row is None else _claimed(row, claim_token)

    def record_tool_call(self, connection: Any, entry: AgentToolCallLedgerEntry) -> None:
        _require_safe_summary(entry.safe_summary)
        if not 1 <= entry.sequence_number <= 6:
            raise AgentRunStateError("tool call sequence must be between 1 and 6")
        if not 100 <= entry.result_status_code <= 599:
            raise AgentRunStateError("tool result status is invalid")
        if entry.completed_at < entry.started_at:
            raise AgentRunStateError("tool completion precedes its start")
        occurred_at = entry.completed_at

        def mutation(cursor: Any) -> None:
            _assert_claim(cursor, entry.agent_run_id, entry.claim_token)
            cursor.execute(
                """
                INSERT INTO agent_tool_calls (
                    id, agent_run_id, sequence_number, operation_id, request_fingerprint,
                    result_status_code, result_code, trace_id, safe_summary, started_at,
                    completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    uuid4(),
                    entry.agent_run_id,
                    entry.sequence_number,
                    entry.operation.value,
                    entry.request_fingerprint,
                    entry.result_status_code,
                    entry.result_code,
                    entry.trace_id,
                    json.dumps(entry.safe_summary, sort_keys=True),
                    entry.started_at,
                    entry.completed_at,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="AGENT_RUN",
                aggregate_id=entry.agent_run_id,
                aggregate_version=entry.sequence_number + 1,
                event_type="AGENT_TOOL_CALL_RECORDED",
                event_payload={
                    "operation_id": entry.operation.value,
                    "result_code": entry.result_code,
                    "result_status_code": entry.result_status_code,
                },
                audit_action="AGENT_TOOL_CALL",
                actor_type="AGENT_RUNNER",
                actor_id=None,
                correlation_id=entry.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "agent.tool_called.v1",
                        {
                            "agent_run_id": str(entry.agent_run_id),
                            "sequence": entry.sequence_number,
                        },
                        f"agent-run:{entry.agent_run_id}:tool:{entry.sequence_number}",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            mutation,
        )

    def complete_draft(
        self,
        connection: Any,
        *,
        agent_run_id: UUID,
        claim_token: UUID,
        safe_summary: dict[str, object],
        correlation_id: UUID,
        completed_at: datetime | None = None,
    ) -> None:
        _require_safe_summary(safe_summary)
        occurred_at = completed_at or datetime.now(UTC)

        def mutation(cursor: Any) -> None:
            _assert_claim(cursor, agent_run_id, claim_token)
            cursor.execute(
                """
                UPDATE agent_runs
                SET status = 'DRAFT_REQUIRES_HUMAN', claim_token = NULL, claimed_at = NULL,
                    lease_expires_at = NULL, result_safe_summary = %s::jsonb, completed_at = %s
                WHERE id = %s AND status = 'PROCESSING'
                RETURNING id
                """,
                (json.dumps(safe_summary, sort_keys=True), occurred_at, agent_run_id),
            )
            if cursor.fetchone() is None:
                raise AgentRunStateError("agent run completion claim is stale")

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="AGENT_RUN",
                aggregate_id=agent_run_id,
                aggregate_version=8,
                event_type="AGENT_RUN_DRAFT_RECORDED",
                event_payload={"disposition": "REQUIRE_HUMAN"},
                audit_action="AGENT_RUN_COMPLETE_DRAFT",
                actor_type="AGENT_RUNNER",
                actor_id=None,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "agent.run_completed.v1",
                        {"agent_run_id": str(agent_run_id), "disposition": "REQUIRE_HUMAN"},
                        f"agent-run:{agent_run_id}:completed",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            mutation,
        )

    def fail(
        self,
        connection: Any,
        *,
        agent_run_id: UUID,
        claim_token: UUID,
        failure_code: str,
        correlation_id: UUID,
        completed_at: datetime | None = None,
    ) -> None:
        if not 1 <= len(failure_code) <= 100 or not failure_code.replace("_", "").isalnum():
            raise AgentRunStateError("agent run failure code is invalid")
        occurred_at = completed_at or datetime.now(UTC)

        def mutation(cursor: Any) -> None:
            _assert_claim(cursor, agent_run_id, claim_token)
            cursor.execute(
                """
                UPDATE agent_runs
                SET status = 'FAILED', claim_token = NULL, claimed_at = NULL,
                    lease_expires_at = NULL, failure_code = %s, completed_at = %s
                WHERE id = %s AND status = 'PROCESSING'
                RETURNING id
                """,
                (failure_code, occurred_at, agent_run_id),
            )
            if cursor.fetchone() is None:
                raise AgentRunStateError("agent run failure claim is stale")

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="AGENT_RUN",
                aggregate_id=agent_run_id,
                aggregate_version=8,
                event_type="AGENT_RUN_FAILED",
                event_payload={"failure_code": failure_code},
                audit_action="AGENT_RUN_FAIL",
                actor_type="AGENT_RUNNER",
                actor_id=None,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "agent.run_completed.v1",
                        {"agent_run_id": str(agent_run_id), "disposition": "REQUIRE_HUMAN"},
                        f"agent-run:{agent_run_id}:failed",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            mutation,
        )

    def recover_expired(
        self,
        connection: Any,
        *,
        worker_role: ActorRole,
        now: datetime | None = None,
    ) -> UUID | None:
        """Fail one expired run to human recovery; never rerun an unknown outcome."""

        _require_runner(worker_role)
        timestamp = now or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT run.id, source.correlation_id
                FROM agent_runs AS run
                JOIN LATERAL (
                    SELECT correlation_id
                    FROM domain_events
                    WHERE aggregate_type = 'AGENT_RUN' AND aggregate_id = run.id
                      AND event_type = 'AGENT_RUN_ENQUEUED'
                    ORDER BY occurred_at, id
                    LIMIT 1
                ) AS source ON TRUE
                WHERE run.status = 'PROCESSING' AND run.lease_expires_at < %s
                ORDER BY run.lease_expires_at, run.id
                FOR UPDATE OF run SKIP LOCKED
                LIMIT 1
                """,
                (timestamp,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            agent_run_id = _uuid(row[0])
            correlation_id = _uuid(row[1])
            cursor.execute(
                """
                UPDATE agent_runs
                SET status = 'FAILED', claim_token = NULL, claimed_at = NULL,
                    lease_expires_at = NULL, failure_code = 'LEASE_EXPIRED',
                    completed_at = %s
                WHERE id = %s AND status = 'PROCESSING' AND lease_expires_at < %s
                RETURNING id
                """,
                (timestamp, agent_run_id, timestamp),
            )
            if cursor.fetchone() is None:
                raise AgentRunStateError("expired agent run changed during recovery")
            cursor.execute(
                """
                INSERT INTO domain_events (
                    id, aggregate_type, aggregate_id, aggregate_version, event_type,
                    payload, correlation_id, occurred_at
                ) VALUES (
                    %s, 'AGENT_RUN', %s, 8, 'AGENT_RUN_FAILED',
                    '{"failure_code":"LEASE_EXPIRED"}'::jsonb, %s, %s
                )
                """,
                (uuid4(), agent_run_id, correlation_id, timestamp),
            )
            cursor.execute(
                """
                INSERT INTO audit_events (
                    id, aggregate_type, aggregate_id, action, actor_type, actor_id,
                    correlation_id, details, occurred_at
                ) VALUES (
                    %s, 'AGENT_RUN', %s, 'AGENT_RUN_LEASE_EXPIRED',
                    'AGENT_RUNNER', NULL, %s,
                    '{"event_type":"AGENT_RUN_FAILED","failure_code":"LEASE_EXPIRED"}'::jsonb,
                    %s
                )
                """,
                (uuid4(), agent_run_id, correlation_id, timestamp),
            )
            cursor.execute(
                """
                INSERT INTO outbox_events (
                    id, aggregate_type, aggregate_id, event_type, payload,
                    idempotency_key, correlation_id, occurred_at
                ) VALUES (
                    %s, 'AGENT_RUN', %s, 'agent.run_completed.v1',
                    jsonb_build_object('agent_run_id', %s::text, 'disposition', 'REQUIRE_HUMAN'),
                    %s, %s, %s
                )
                """,
                (
                    uuid4(),
                    agent_run_id,
                    agent_run_id,
                    f"agent-run:{agent_run_id}:lease-expired",
                    correlation_id,
                    timestamp,
                ),
            )
        return agent_run_id


def _validate_enqueue(command: AgentRunEnqueueCommand) -> None:
    if command.deployment_stage is not AgentDeploymentStage.SHADOW:
        raise AgentRunStateError("only shadow-stage runs may be enqueued")
    if command.channel != command.channel.upper() or not command.channel.replace("_", "").isalnum():
        raise AgentRunStateError("channel must be an uppercase identifier")
    for value, label in (
        (command.runtime_registry_version, "runtime registry version"),
        (command.prompt_bundle_version, "prompt bundle version"),
    ):
        if not 1 <= len(value) <= 120:
            raise AgentRunStateError(f"{label} is invalid")
    for value, label in (
        (command.runtime_registry_hash, "runtime registry hash"),
        (command.prompt_bundle_hash, "prompt bundle hash"),
        (command.tool_contract_hash, "tool contract hash"),
    ):
        if not _is_sha256(value):
            raise AgentRunStateError(f"{label} is invalid")
    if command.bound_row_version < 0:
        raise AgentRunStateError("bound row version is invalid")
    if command.public_code is not None and not 16 <= len(command.public_code) <= 64:
        raise AgentRunStateError("public code is invalid")


def _require_runner(role: ActorRole) -> None:
    if role is not ActorRole.AGENT_RUNNER:
        raise AgentRunAuthorizationError("only AGENT_RUNNER may claim agent runs")


def _assert_claim(cursor: Any, agent_run_id: UUID, claim_token: UUID) -> None:
    cursor.execute(
        """
        SELECT id FROM agent_runs
        WHERE id = %s AND status = 'PROCESSING' AND claim_token = %s
            AND lease_expires_at >= CURRENT_TIMESTAMP
        FOR UPDATE
        """,
        (agent_run_id, claim_token),
    )
    if cursor.fetchone() is None:
        raise AgentRunStateError("agent run claim is stale or unavailable")


def _claimed(row: tuple[object, ...], claim_token: UUID) -> ClaimedAgentRun:
    lease_expires_at = row[18]
    if not isinstance(lease_expires_at, datetime) or lease_expires_at.tzinfo is None:
        raise AgentRunStateError("claimed agent run lease is invalid")
    return ClaimedAgentRun(
        agent_run_id=_uuid(row[0]),
        claim_token=claim_token,
        organization_id=_uuid(row[1]),
        store_id=_uuid(row[2]),
        channel=str(row[3]),
        conversation_binding_id=_uuid(row[4]),
        contact_binding_id=_uuid(row[5]),
        capability=ReleaseCapability(str(row[6])),
        deployment_stage=AgentDeploymentStage(str(row[7])),
        data_classification=AgentDataClassification(str(row[8])),
        runtime_registry_version=str(row[9]),
        runtime_registry_hash=str(row[10]),
        prompt_bundle_version=str(row[11]),
        prompt_bundle_hash=str(row[12]),
        tool_contract_hash=str(row[13]),
        order_request_id=None if row[14] is None else _uuid(row[14]),
        public_code=None if row[15] is None else str(row[15]),
        bound_row_version=int(str(row[16])),
        attempt_count=int(str(row[17])),
        lease_expires_at=lease_expires_at,
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _is_sha256(value: str) -> bool:
    return bool(value.startswith("sha256:") and len(value) == 71 and _hex(value[7:]))


def _hex(value: str) -> bool:
    return all(character in "0123456789abcdef" for character in value)


def _require_safe_summary(summary: dict[str, object]) -> None:
    if not summary or len(summary) > 20:
        raise AgentRunStateError("safe summary must be a non-empty bounded object")
    prohibited = {"chain_of_thought", "cot", "raw_prompt", "raw_provider_response", "secret"}
    if prohibited.intersection(summary):
        raise AgentRunStateError("safe summary contains prohibited internal content")


def request_fingerprint(arguments: dict[str, object]) -> str:
    """Hash tool arguments for audit without persisting model-visible raw customer content."""

    encoded = json.dumps(
        arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


__all__ = [
    "AgentRunAuthorizationError",
    "AgentRunEnqueueCommand",
    "AgentRunRepository",
    "AgentRunStateError",
    "AgentRunStatus",
    "AgentToolCallLedgerEntry",
    "ClaimedAgentRun",
    "request_fingerprint",
]
