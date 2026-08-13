"""Assemble the bounded Responses runtime into a running agent pipeline.

Before this module, `responses_runtime.py` was reachable from nothing. `AgentCycle` in `host.py` is
a `Callable` alias defaulting to `None`, and `WorkerSupervisor` accepted the hole where the
pipeline should be. This module fills it by composition only. It creates no authority, adds no
tool, changes no budget, and touches neither the finite state machine nor the bridge nor the ledger.

```text
DurableAgentRunWorker claims a job under lease
  -> BoundResponsesContextLoader loads the pinned context packet
  -> AgentToolBridgeSession opens, scoped to the run's binding
  -> BoundedResponsesRuntime executes the FSM
  -> terminal outcome persisted as structured redacted evidence
  -> bridge revoked, budget settled, lease released
```

Wiring a pipeline is not enabling it. `worker_agent_queue_enabled` and
`feature_agent_runtime_enabled` remain `false` by default, and the transport here is deterministic:
this module makes no provider call and needs no credential. The real transport arrives in
`PROVIDER-TRANSPORT-001`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from nha_trang_laundry_db.agent_runs import AgentRunRepository
from nha_trang_laundry_observability import EventSeverity, SafeStructuredLogger

from .agent_runner import AgentRunner, AgentRuntimeInvocation, AgentToolTransport
from .durable_agent_worker import DurableAgentRunWorker, DurableAgentRunWorkerResult
from .responses_runtime import (
    BoundedResponsesRuntime,
    ResponsesContextLoader,
    ResponsesProviderTransport,
    ResponsesRuntimeConfig,
    ResponsesRuntimeContext,
    ResponsesRuntimeEvidence,
)

AuthorityCheck = Callable[[], bool]

# The deterministic handoff the supervisor reports when it is not allowed to claim anything.
DISABLED_STATUS = "DISABLED"
STOPPED_STATUS = "STOPPED"

# Mirrors the runner's hard run deadline; the context deadline may be shorter but never longer.
TURN_DEADLINE_SECONDS = 20.0


class PipelineConfigurationError(RuntimeError):
    """Raised when the pipeline cannot be assembled safely from settings."""


class CapturingEvidenceSink:
    """Hold the terminal redacted evidence of the current run so the worker can persist it.

    `ResponsesRuntimeEvidence` is already typed `extra="forbid"` with no prompt text, no draft, no
    tool arguments, no reasoning and no provider response id, so what is captured here is safe by
    construction rather than by filtering.
    """

    def __init__(self) -> None:
        self._terminal: ResponsesRuntimeEvidence | None = None

    def persist(self, evidence: ResponsesRuntimeEvidence) -> None:
        self._terminal = evidence

    def take(self) -> ResponsesRuntimeEvidence | None:
        terminal = self._terminal
        self._terminal = None
        return terminal


class RunScopedContextLoader:
    """Bind a context packet to the run being executed, and refuse any other run.

    `BoundResponsesContextLoader` matches a packet from a fixed sequence. This loader assembles the
    packet for exactly the claimed job, so a packet can never be served to a run it was not built
    for — the failure mode that would let one customer's context reach another customer's draft.
    """

    def __init__(
        self,
        *,
        config: ResponsesRuntimeConfig,
        instructions: str,
        input_text_for: Callable[[AgentRuntimeInvocation], str],
        turn_deadline_seconds: float = TURN_DEADLINE_SECONDS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0 < turn_deadline_seconds <= TURN_DEADLINE_SECONDS:
            raise PipelineConfigurationError(
                "the turn deadline must be positive and no longer than the run hard deadline"
            )
        self._config = config
        self._instructions = instructions
        self._input_text_for = input_text_for
        self._turn_deadline_seconds = turn_deadline_seconds
        self._now = now or utc_now

    def load(self, invocation: AgentRuntimeInvocation) -> ResponsesRuntimeContext:
        # The session key is the bridge's own, not a derived one: the runtime re-hashes
        # `invocation.session_key` and rejects a context whose hash disagrees, which is what stops a
        # packet built for one session being served to another.
        return ResponsesRuntimeContext.assemble(
            run_id=invocation.run_id,
            capability=invocation.capability,
            session_key=invocation.session_key,
            config=self._config,
            deadline_at=self._now() + timedelta(seconds=self._turn_deadline_seconds),
            instructions=self._instructions,
            input_text=self._input_text_for(invocation),
        )


@dataclass(frozen=True, slots=True)
class AgentPipeline:
    """The constructed pipeline plus the pieces a caller needs for observability and tests."""

    runtime: BoundedResponsesRuntime
    worker: DurableAgentRunWorker
    transport: AgentToolTransport
    evidence_sink: CapturingEvidenceSink

    def run_cycle(
        self, connection: Any, authority_valid: AuthorityCheck
    ) -> DurableAgentRunWorkerResult:
        """Execute at most one claimed job, returning which run was processed and its status."""

        if not authority_valid():
            return DurableAgentRunWorkerResult(None, STOPPED_STATUS)
        return self.worker.run_once(
            connection,
            runtime=self.runtime,
            transport=self.transport,
            correlation_id=uuid4(),
        )


def build_agent_pipeline(
    *,
    config: ResponsesRuntimeConfig,
    provider_transport: ResponsesProviderTransport,
    tool_transport: AgentToolTransport,
    runner: AgentRunner,
    instructions: str,
    input_text_for: Callable[[AgentRuntimeInvocation], str],
    repository: AgentRunRepository | None = None,
    logger: SafeStructuredLogger | None = None,
    context_loader: ResponsesContextLoader | None = None,
    evidence_sink: CapturingEvidenceSink | None = None,
    now: Callable[[], datetime] | None = None,
) -> AgentPipeline:
    """Compose existing parts into a runnable pipeline. This function creates no new authority."""

    if provider_transport.provider_backed:
        raise PipelineConfigurationError(
            "AGENT-PIPELINE-001 assembles the deterministic path only; a provider-backed transport "
            "requires PROVIDER-TRANSPORT-001 and its release authorization"
        )
    sink = evidence_sink or CapturingEvidenceSink()
    loader = context_loader or RunScopedContextLoader(
        config=config,
        instructions=instructions,
        input_text_for=input_text_for,
        now=now,
    )
    runtime = BoundedResponsesRuntime(
        config=config,
        context_loader=loader,
        transport=provider_transport,
        evidence_sink=sink,
        now=now,
    )
    worker = DurableAgentRunWorker(
        runner,
        repository=repository,
        logger=logger,
        terminal_evidence=lambda: _safe_evidence(sink.take()),
    )
    return AgentPipeline(
        runtime=runtime, worker=worker, transport=tool_transport, evidence_sink=sink
    )


def build_agent_cycle(
    pipeline: AgentPipeline,
    *,
    enabled: bool,
    logger: SafeStructuredLogger | None = None,
) -> Callable[[Any, AuthorityCheck], str]:
    """Return the `AgentCycle` the supervisor injects, closed by default.

    `enabled` mirrors the supervisor's own queue flag rather than replacing it. A pipeline that is
    constructed but not enabled claims nothing, which is what makes assembling it safe.
    """

    safe_logger = logger or SafeStructuredLogger()

    def cycle(connection: Any, authority_valid: AuthorityCheck) -> str:
        if not enabled:
            return DISABLED_STATUS
        try:
            return pipeline.run_cycle(connection, authority_valid).status
        except Exception:
            safe_logger.record(
                component="agent-pipeline",
                name="agent.cycle.completed",
                outcome="failed",
                severity=EventSeverity.ERROR,
                fields={"error_code": "AGENT_CYCLE_FAILED"},
            )
            raise

    return cycle


def _safe_evidence(evidence: ResponsesRuntimeEvidence | None) -> Mapping[str, Any] | None:
    """Project terminal evidence onto the small set of fields safe to store in a run summary."""

    if evidence is None:
        return None
    document = evidence.model_dump(mode="json")
    allowed: Sequence[str] = (
        "terminal_outcome",
        "terminal_code",
        "runtime_id",
        "immutable_model_release",
        "context_packet_hash",
        "tool_contract_hash",
        "model_attempt_count",
        "tool_call_count",
        "input_tokens",
        "output_tokens",
        "reserved_cost_usd",
        "settled_cost_usd",
        "released_cost_usd",
        "provider_backed",
        "retry_count",
        "bridge_revoked",
        "chain_of_thought_persisted",
        "provider_response_id_persisted",
    )
    return {key: document[key] for key in allowed if key in document}


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DISABLED_STATUS",
    "STOPPED_STATUS",
    "TURN_DEADLINE_SECONDS",
    "AgentPipeline",
    "CapturingEvidenceSink",
    "PipelineConfigurationError",
    "RunScopedContextLoader",
    "build_agent_cycle",
    "build_agent_pipeline",
    "utc_now",
]
