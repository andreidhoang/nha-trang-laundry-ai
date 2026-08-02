"""Database-backed, draft-only execution of one claimed Agent Runner job."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from nha_trang_laundry_contracts import AgentToolOperation
from nha_trang_laundry_db.agent_runs import (
    AgentRunRepository,
    AgentToolCallLedgerEntry,
    ClaimedAgentRun,
    request_fingerprint,
)
from nha_trang_laundry_domain.catalog import ActorRole
from nha_trang_laundry_observability import (
    CorrelationContext,
    EventSeverity,
    SafeStructuredLogger,
    correlation_scope,
)

from .agent_runner import (
    AgentRunJob,
    AgentRunner,
    AgentRunnerError,
    AgentToolBridgeResult,
    AgentToolCallObserver,
    AgentToolTransport,
    ConstrainedAgentRuntime,
)


@dataclass(frozen=True, slots=True)
class DurableAgentRunWorkerResult:
    agent_run_id: str | None
    status: str


class _DatabaseToolCallObserver(AgentToolCallObserver):
    def __init__(
        self,
        connection: Any,
        repository: AgentRunRepository,
        claimed: ClaimedAgentRun,
        correlation_id: UUID,
    ) -> None:
        self._connection = connection
        self._repository = repository
        self._claimed = claimed
        self._correlation_id = correlation_id

    def record(
        self,
        *,
        sequence_number: int,
        operation: AgentToolOperation,
        arguments: Mapping[str, Any],
        result: AgentToolBridgeResult,
        started_at: datetime,
        completed_at: datetime,
    ) -> None:
        self._repository.record_tool_call(
            self._connection,
            AgentToolCallLedgerEntry(
                agent_run_id=self._claimed.agent_run_id,
                claim_token=self._claimed.claim_token,
                sequence_number=sequence_number,
                operation=operation,
                request_fingerprint=request_fingerprint(
                    {str(key): value for key, value in arguments.items()}
                ),
                result_status_code=result.status_code,
                result_code=_result_code(result),
                trace_id=result.trace_id,
                safe_summary={
                    "result_code": _result_code(result),
                    "status_code": result.status_code,
                    "top_level_keys": sorted(str(key) for key in result.body),
                },
                started_at=started_at,
                completed_at=completed_at,
                correlation_id=self._correlation_id,
            ),
        )


class DurableAgentRunWorker:
    """Claims one durable job, runs only a bounded draft path, then persists a safe outcome."""

    def __init__(
        self,
        runner: AgentRunner,
        repository: AgentRunRepository | None = None,
        logger: SafeStructuredLogger | None = None,
    ) -> None:
        self._runner = runner
        self._repository = repository or AgentRunRepository()
        self._logger = logger or SafeStructuredLogger()

    def run_once(
        self,
        connection: Any,
        *,
        runtime: ConstrainedAgentRuntime,
        transport: AgentToolTransport,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> DurableAgentRunWorkerResult:
        timestamp = now or datetime.now(UTC)
        context = CorrelationContext.from_uuid(correlation_id)
        claimed = self._repository.claim_next(
            connection, worker_role=ActorRole.AGENT_RUNNER, now=timestamp
        )
        if claimed is None:
            return DurableAgentRunWorkerResult(None, "IDLE")
        with correlation_scope(context):
            try:
                result = self._runner.execute(
                    job=_job_from_claim(claimed, timestamp, correlation_id),
                    runtime=runtime,
                    transport=transport,
                    observer=_DatabaseToolCallObserver(
                        connection, self._repository, claimed, correlation_id
                    ),
                    now=timestamp,
                )
            except Exception as error:
                failure_code = _failure_code(error)
                self._repository.fail(
                    connection,
                    agent_run_id=claimed.agent_run_id,
                    claim_token=claimed.claim_token,
                    failure_code=failure_code,
                    correlation_id=correlation_id,
                    completed_at=timestamp,
                )
                self._logger.record(
                    component="agent-worker",
                    name="agent.run.completed",
                    outcome="failed",
                    severity=EventSeverity.ERROR,
                    correlation=context,
                    fields={"agent_run_id": claimed.agent_run_id, "failure_code": failure_code},
                )
                return DurableAgentRunWorkerResult(str(claimed.agent_run_id), "FAILED")
            self._repository.complete_draft(
                connection,
                agent_run_id=claimed.agent_run_id,
                claim_token=claimed.claim_token,
                safe_summary={
                    "disposition": "REQUIRE_HUMAN",
                    "draft_character_count": len(result.draft_text),
                    "tool_call_count": result.tool_call_count,
                },
                correlation_id=correlation_id,
                completed_at=timestamp,
            )
            self._logger.record(
                component="agent-worker",
                name="agent.run.completed",
                outcome="requires_human",
                correlation=context,
                fields={
                    "agent_run_id": claimed.agent_run_id,
                    "tool_call_count": result.tool_call_count,
                },
            )
            return DurableAgentRunWorkerResult(str(claimed.agent_run_id), result.status)


def _job_from_claim(
    claimed: ClaimedAgentRun, started_at: datetime, correlation_id: UUID
) -> AgentRunJob:
    return AgentRunJob(
        run_id=claimed.agent_run_id,
        organization_id=claimed.organization_id,
        store_id=claimed.store_id,
        channel=claimed.channel,
        conversation_binding_id=claimed.conversation_binding_id,
        contact_binding_id=claimed.contact_binding_id,
        capability=claimed.capability,
        stage=claimed.deployment_stage,
        data_classification=claimed.data_classification,
        started_at=started_at,
        deadline_at=min(started_at + timedelta(seconds=20), claimed.lease_expires_at),
        correlation_id=correlation_id,
        order_request_id=claimed.order_request_id,
        public_code=claimed.public_code,
        row_version=claimed.bound_row_version,
    )


def _result_code(result: AgentToolBridgeResult) -> str:
    error = result.body.get("error")
    error_code = error.get("code") if isinstance(error, Mapping) else None
    if isinstance(error_code, str):
        return error_code
    decision = result.body.get("decision")
    outcome = decision.get("outcome") if isinstance(decision, Mapping) else None
    if isinstance(outcome, str):
        return outcome
    return "TOOL_UNAVAILABLE"


def _failure_code(error: Exception) -> str:
    if isinstance(error, AgentRunnerError):
        candidate = str(error).split(":", 1)[0]
        if candidate.replace("_", "").isalnum() and 1 <= len(candidate) <= 100:
            return candidate
    return "UNEXPECTED_RUNTIME_FAILURE"


__all__ = ["DurableAgentRunWorker", "DurableAgentRunWorkerResult"]
