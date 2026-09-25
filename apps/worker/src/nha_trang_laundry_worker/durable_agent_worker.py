"""Database-backed, draft-only execution of one claimed Agent Runner job."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from nha_trang_laundry_contracts import AgentToolOperation
from nha_trang_laundry_db.agent_runs import (
    AgentRunRepository,
    AgentRunStateError,
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

from .agent_run_policy import AgentRunPolicyGate
from .agent_runner import (
    AgentRunJob,
    AgentRunner,
    AgentRunnerError,
    AgentRunRejected,
    AgentRunResult,
    AgentToolBridgeResult,
    AgentToolCallObserver,
    AgentToolTransport,
    ConstrainedAgentRuntime,
    ExecutionPins,
)

#: Persists the agent's proposal for human review; see `ShadowConsoleRepository.record_draft`.
DraftRecorder = Callable[[Any, ClaimedAgentRun, AgentRunResult, UUID, datetime], None]

#: The runner's hard ceiling for one turn (ADR-0003: twenty seconds wall clock).
RUN_HARD_DEADLINE = timedelta(seconds=20)

#: Time reserved between the job's deadline and the claim's lease expiry, so a run that times out
#: can still record *how* it ended under a live claim. Before AGENT-SHADOW-DEFECTS-001 the
#: deadline was `min(started + 20 s, lease)` with a 20 s lease -- the same instant -- so `fail()`
#: always found a stale claim, raised from inside the `except`, and the real code (MODEL_TIMEOUT)
#: was lost.
RECORDING_MARGIN = timedelta(seconds=5)

#: `run_once` outcome when the run's claim was lost before its outcome could be written. Nothing is
#: written under a lost claim; lease-expiry recovery hands the run to a human instead.
CLAIM_LOST_STATUS = "CLAIM_LOST"


@dataclass(frozen=True, slots=True)
class DurableAgentRunWorkerResult:
    agent_run_id: str | None
    status: str


class _DatabaseToolCallObserver(AgentToolCallObserver):
    """Write the tool ledger under the run's claim, and never after the worker has let go.

    The runtime executes on its own thread and this observer writes through the worker's
    connection. Once the worker starts recording the run's outcome it closes the observer; the lock
    makes that wait for any write already in progress, so a late runtime thread can neither
    interleave a write with the outcome's transaction nor write after it.
    """

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
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

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
        with self._lock:
            if self._closed:
                raise AgentRunStateError("agent run is no longer live; late tool call not recorded")
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
    """Claims one durable job, runs only a bounded draft path, then persists a safe outcome.

    Every exit is named: IDLE; FAILED, with its code, written under a live claim; CLAIM_LOST, with
    nothing written; or the runtime's own disposition, written with the draft in one transaction.
    """

    def __init__(
        self,
        runner: AgentRunner,
        repository: AgentRunRepository | None = None,
        logger: SafeStructuredLogger | None = None,
        terminal_evidence: Callable[[UUID], Mapping[str, Any] | None] | None = None,
        draft_recorder: DraftRecorder | None = None,
        policy_gate: AgentRunPolicyGate | None = None,
    ) -> None:
        self._runner = runner
        # Always present: the default reads PostgreSQL and fails closed on anything it cannot read.
        self._policy_gate = policy_gate or AgentRunPolicyGate()
        self._repository = repository or AgentRunRepository()
        self._logger = logger or SafeStructuredLogger()
        # Supplied by the assembled pipeline so the run's redacted runtime evidence lands in the
        # same summary as its disposition. Keyed by run: a late record is never handed to another.
        self._terminal_evidence = terminal_evidence
        # Supplied by the assembled pipeline. Without it the agent's proposal is never persisted and
        # the Shadow review queue has nothing to show, which is how SHADOW-CONSOLE-001 found this.
        self._draft_recorder = draft_recorder

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
            # Before the runner, before any model call: suppression, the derived classification, the
            # kill switches and the release manifest (AGENT-SHADOW-DEFECTS-001 F5). A handoff is a
            # refusal here too -- with no model call there is nothing for a human to review.
            decision = self._policy_gate.decide(
                connection, claimed, provider_backed=runtime.provider_backed, now=timestamp
            )
            if not decision.allowed:
                return self._record_failure(
                    connection,
                    claimed,
                    decision.reason_codes[0].value,
                    correlation_id,
                    timestamp,
                    context,
                )
            observer = _DatabaseToolCallObserver(
                connection, self._repository, claimed, correlation_id
            )
            try:
                result = self._runner.execute(
                    job=_job_from_claim(claimed, timestamp, correlation_id),
                    runtime=runtime,
                    transport=transport,
                    observer=observer,
                    now=timestamp,
                )
            except Exception as error:
                observer.close()
                # Drain the sink even on failure, so a terminal record from this run can never be
                # attributed to the next one.
                self._drain_terminal_evidence(claimed.agent_run_id)
                return self._record_failure(
                    connection, claimed, _failure_code(error), correlation_id, timestamp, context
                )
            observer.close()
            safe_summary: dict[str, Any] = {
                "disposition": "REQUIRE_HUMAN",
                # DRAFT only for a validated model draft; a deterministic handoff says REQUIRE_HUMAN
                # and carries the runtime's own code (AGENT-SHADOW-DEFECTS-001 F1).
                "terminal_outcome": (
                    "DRAFT" if result.status == "DRAFT_REQUIRES_HUMAN" else "REQUIRE_HUMAN"
                ),
                "terminal_code": result.terminal_code,
                "draft_character_count": len(result.draft_text),
                "tool_call_count": result.tool_call_count,
                # Which policy admitted the run to a model, and why.
                "policy_version": decision.policy_version,
                "policy_reason_codes": [reason.value for reason in decision.reason_codes],
            }
            runtime_evidence = self._drain_terminal_evidence(claimed.agent_run_id)
            if runtime_evidence is not None:
                safe_summary["runtime_evidence"] = dict(runtime_evidence)
            try:
                # One transaction, claim first. `complete_draft` locks the run row and proves the
                # claim is still this worker's; only then is the proposal filed for review. The
                # draft used to be committed on its own before the claim was checked, so a run
                # finishing at its deadline left a reviewable draft behind a failed completion.
                with connection.transaction():
                    self._repository.complete_draft(
                        connection,
                        agent_run_id=claimed.agent_run_id,
                        claim_token=claimed.claim_token,
                        safe_summary=safe_summary,
                        correlation_id=correlation_id,
                        completed_at=timestamp,
                    )
                    if self._draft_recorder is not None and result.draft_text:
                        self._draft_recorder(connection, claimed, result, correlation_id, timestamp)
            except AgentRunStateError:
                return self._claim_lost(claimed, "COMPLETE_DRAFT", context)
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

    def _record_failure(
        self,
        connection: Any,
        claimed: ClaimedAgentRun,
        failure_code: str,
        correlation_id: UUID,
        timestamp: datetime,
        context: CorrelationContext,
    ) -> DurableAgentRunWorkerResult:
        try:
            self._repository.fail(
                connection,
                agent_run_id=claimed.agent_run_id,
                claim_token=claimed.claim_token,
                failure_code=failure_code,
                correlation_id=correlation_id,
                completed_at=timestamp,
            )
        except AgentRunStateError:
            # Raising here used to escape `run_once` from inside its own `except`, losing the
            # failure code. The run is not this worker's to write any more; say so and stop.
            return self._claim_lost(claimed, failure_code, context)
        self._logger.record(
            component="agent-worker",
            name="agent.run.completed",
            outcome="failed",
            severity=EventSeverity.ERROR,
            correlation=context,
            fields={"agent_run_id": claimed.agent_run_id, "failure_code": failure_code},
        )
        return DurableAgentRunWorkerResult(str(claimed.agent_run_id), "FAILED")

    def _claim_lost(
        self, claimed: ClaimedAgentRun, unrecorded: str, context: CorrelationContext
    ) -> DurableAgentRunWorkerResult:
        self._logger.record(
            component="agent-worker",
            name="agent.run.completed",
            outcome="claim_lost",
            severity=EventSeverity.ERROR,
            correlation=context,
            fields={"agent_run_id": claimed.agent_run_id, "unrecorded_outcome": unrecorded},
        )
        return DurableAgentRunWorkerResult(str(claimed.agent_run_id), CLAIM_LOST_STATUS)

    def _drain_terminal_evidence(self, run_id: UUID) -> Mapping[str, Any] | None:
        if self._terminal_evidence is None:
            return None
        return self._terminal_evidence(run_id)


def _job_from_claim(
    claimed: ClaimedAgentRun, started_at: datetime, correlation_id: UUID
) -> AgentRunJob:
    deadline_at = min(started_at + RUN_HARD_DEADLINE, claimed.lease_expires_at - RECORDING_MARGIN)
    if deadline_at - started_at < timedelta(seconds=1):
        raise AgentRunRejected(
            "LEASE_MARGIN_EXHAUSTED: the claim cannot cover a run and its record"
        )
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
        deadline_at=deadline_at,
        correlation_id=correlation_id,
        order_request_id=claimed.order_request_id,
        public_code=claimed.public_code,
        row_version=claimed.bound_row_version,
        # What the run was queued for. Dropped here before AGENT-SHADOW-DEFECTS-001, so nothing
        # could compare it with what the runtime executes; the runner now refuses a mismatch.
        pins=ExecutionPins(
            runtime_registry_version=claimed.runtime_registry_version,
            runtime_registry_hash=claimed.runtime_registry_hash,
            prompt_bundle_version=claimed.prompt_bundle_version,
            prompt_bundle_hash=claimed.prompt_bundle_hash,
            tool_contract_hash=claimed.tool_contract_hash,
        ),
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


__all__ = [
    "CLAIM_LOST_STATUS",
    "RECORDING_MARGIN",
    "RUN_HARD_DEADLINE",
    "DraftRecorder",
    "DurableAgentRunWorker",
    "DurableAgentRunWorkerResult",
]
