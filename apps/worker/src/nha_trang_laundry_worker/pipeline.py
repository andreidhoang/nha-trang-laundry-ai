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

import hmac
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

import yaml
from nha_trang_laundry_contracts import load_public_runtime_registry
from nha_trang_laundry_db.agent_runs import AgentRunRepository
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_observability import EventSeverity, SafeStructuredLogger

from .agent_run_policy import AgentRunPolicyGate
from .agent_runner import (
    ROOT as REPOSITORY_ROOT,
)
from .agent_runner import (
    AgentRunner,
    AgentRuntimeInvocation,
    AgentToolTransport,
    ExecutionPins,
    registry_execution_pins,
)
from .durable_agent_worker import (
    DraftRecorder,
    DurableAgentRunWorker,
    DurableAgentRunWorkerResult,
)
from .responses_runtime import (
    CURRENT_TOOL_CONTRACT_HASH,
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

# How long before the job's hard deadline the runtime must stop on its own. The runtime then returns
# its own handoff (PROVIDER_TIMEOUT, DEADLINE_EXHAUSTED) with the bridge revoked and the reservation
# settled, instead of being cut off by the runner's guard with nothing accounted.
RUNTIME_DEADLINE_GRACE = timedelta(milliseconds=500)


class PipelineConfigurationError(RuntimeError):
    """Raised when the pipeline cannot be assembled safely from settings."""


class CapturingEvidenceSink:
    """Hold the terminal redacted evidence of the current run so the worker can persist it.

    `ResponsesRuntimeEvidence` is already typed `extra="forbid"` with no prompt text, no draft, no
    tool arguments, no reasoning and no provider response id, so what is captured here is safe by
    construction rather than by filtering.

    Keyed by run. A runtime thread the runner abandoned at its deadline still persists its
    evidence when it finally returns; with a single slot that late record was taken by whichever
    run the worker processed next, which is misattribution. Now a record is only ever handed to the
    run that produced it, and a late one is simply never collected.
    """

    def __init__(self) -> None:
        self._terminal: dict[UUID, ResponsesRuntimeEvidence] = {}
        self._lock = Lock()

    #: A worker processes one run at a time, so anything beyond a handful is a late record nobody
    #: will collect. Bounded so abandoned runs cannot accumulate for the life of the process.
    _MAX_UNCOLLECTED = 8

    def persist(self, evidence: ResponsesRuntimeEvidence) -> None:
        with self._lock:
            self._terminal[evidence.run_id] = evidence
            while len(self._terminal) > self._MAX_UNCOLLECTED:
                self._terminal.pop(next(iter(self._terminal)))

    def take(self, run_id: UUID) -> ResponsesRuntimeEvidence | None:
        with self._lock:
            return self._terminal.pop(run_id, None)


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
        #
        # The deadline is the job's, less a grace for the runtime to conclude itself. It used to be
        # `now + 20 s` whatever the job had left, so a provider could be granted time the run's
        # lease did not have (AGENT-SHADOW-DEFECTS-001 F2).
        deadline_at = min(
            self._now() + timedelta(seconds=self._turn_deadline_seconds),
            invocation.deadline_at - RUNTIME_DEADLINE_GRACE,
        )
        return ResponsesRuntimeContext.assemble(
            run_id=invocation.run_id,
            capability=invocation.capability,
            session_key=invocation.session_key,
            config=self._config,
            deadline_at=deadline_at,
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
    draft_recorder: DraftRecorder | None = None,
    now: Callable[[], datetime] | None = None,
    policy_gate: AgentRunPolicyGate | None = None,
) -> AgentPipeline:
    """Compose existing parts into a runnable pipeline. This function creates no new authority."""

    if provider_transport.provider_backed:
        raise PipelineConfigurationError(
            "AGENT-PIPELINE-001 assembles the deterministic path only; a provider-backed transport "
            "requires PROVIDER-TRANSPORT-001 and its release authorization"
        )
    _require_pinned_release(config, instructions, load_pinned_prompt())
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
        terminal_evidence=lambda run_id: _safe_evidence(sink.take(run_id)),
        draft_recorder=draft_recorder if draft_recorder is not None else _record_draft_for_review,
        policy_gate=policy_gate,
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


@dataclass(frozen=True, slots=True)
class PinnedPrompt:
    """The release the repository's runtime registry pins, read and hash-verified from disk."""

    pins: ExecutionPins
    instructions: str
    instructions_hash: str


def load_pinned_prompt(
    registry_path: Path = REPOSITORY_ROOT / "runtime/model-registry-v1.yaml",
) -> PinnedPrompt:
    """Follow the registry's pin chain to the exact instruction text, verifying every link.

    registry -> prompt bundle manifest (bundle_sha256) -> prompt file (prompt.sha256), and the
    bundle's tool contract pin against the registry's and against the contract actually loaded.
    Any broken link refuses the pipeline: a configuration whose hashes are labels checked against
    nothing is how a run queued for one prompt executed another (AGENT-SHADOW-DEFECTS-001 F3).
    """

    pins = registry_execution_pins(registry_path)
    registry = load_public_runtime_registry(registry_path)
    manifest_bytes = _repository_file(registry.prompt.bundle_path).read_bytes()
    if not _digest_equal(_sha256_bytes(manifest_bytes), registry.prompt.bundle_sha256):
        raise PipelineConfigurationError("PROMPT_BUNDLE_HASH_MISMATCH")
    manifest = yaml.safe_load(manifest_bytes)
    prompt = manifest.get("prompt") if isinstance(manifest, dict) else None
    tools = manifest.get("tool_contract") if isinstance(manifest, dict) else None
    if (
        not isinstance(prompt, dict)
        or not isinstance(tools, dict)
        or manifest.get("bundle_version") != registry.prompt.bundle_version
        or not isinstance(prompt.get("path"), str)
        or not isinstance(prompt.get("sha256"), str)
    ):
        raise PipelineConfigurationError("PROMPT_BUNDLE_MANIFEST_INVALID")
    prompt_bytes = _repository_file(prompt["path"]).read_bytes()
    instructions_hash = _sha256_bytes(prompt_bytes)
    if not _digest_equal(instructions_hash, prompt["sha256"]):
        raise PipelineConfigurationError("PROMPT_FILE_HASH_MISMATCH")
    if not _digest_equal(str(tools.get("sha256")), registry.tool_contract_sha256) or not (
        _digest_equal(registry.tool_contract_sha256, CURRENT_TOOL_CONTRACT_HASH)
    ):
        raise PipelineConfigurationError("TOOL_CONTRACT_PIN_MISMATCH")
    return PinnedPrompt(
        pins=pins, instructions=prompt_bytes.decode("utf-8"), instructions_hash=instructions_hash
    )


def _require_pinned_release(
    config: ResponsesRuntimeConfig, instructions: str, pinned: PinnedPrompt
) -> None:
    configured = (
        ("runtime_registry_version", config.runtime_registry_version),
        ("runtime_registry_hash", config.runtime_registry_hash),
        ("prompt_bundle_version", config.prompt_bundle_version),
        ("prompt_bundle_hash", config.prompt_bundle_hash),
        ("tool_contract_hash", config.tool_contract_hash),
    )
    mismatched = [
        name for name, value in configured if not _digest_equal(value, getattr(pinned.pins, name))
    ]
    if not _digest_equal(config.prompt_instructions_hash, pinned.instructions_hash):
        mismatched.append("prompt_instructions_hash")
    if not _digest_equal(_sha256_bytes(instructions.encode("utf-8")), pinned.instructions_hash):
        mismatched.append("instructions")
    if mismatched:
        raise PipelineConfigurationError(
            "runtime configuration does not match the registry-pinned release: "
            + ",".join(mismatched)
        )


def _repository_file(relative: str) -> Path:
    path = (REPOSITORY_ROOT / relative).resolve()
    if REPOSITORY_ROOT not in path.parents:
        raise PipelineConfigurationError("PINNED_ARTIFACT_PATH_ESCAPES_REPOSITORY")
    return path


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def _digest_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def _safe_evidence(evidence: ResponsesRuntimeEvidence | None) -> Mapping[str, Any] | None:
    """Project terminal evidence onto the small set of fields safe to store in a run summary."""

    if evidence is None:
        return None
    document = evidence.model_dump(mode="json")
    allowed: Sequence[str] = (
        # The run the record belongs to; without it a summary cannot be checked against its row.
        "run_id",
        "terminal_outcome",
        "terminal_code",
        "runtime_id",
        "model_id",
        "immutable_model_release",
        # What actually ran, not only what was queued: the exact registry, prompt bundle,
        # instructions and price table (AGENT-SHADOW-DEFECTS-001 F3). All are digests or versions.
        "runtime_registry_version",
        "runtime_registry_hash",
        "prompt_bundle_version",
        "prompt_bundle_hash",
        "prompt_instructions_hash",
        "price_table_version",
        "price_table_hash",
        "context_packet_hash",
        "tool_contract_hash",
        "model_attempt_count",
        "tool_call_count",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reservation_count",
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


def _record_draft_for_review(
    connection: Any,
    claimed: Any,
    result: Any,
    correlation_id: UUID,
    timestamp: datetime,
) -> None:
    """Persist the proposal so the Shadow console has something for a human to approve.

    A run that is retried after a failure keeps its original proposal: the first draft stands and a
    second recording is skipped. Re-recording would either crash the worker on the primary key or
    silently replace what a human may already have reviewed.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM agent_drafts WHERE agent_run_id = %s", (claimed.agent_run_id,)
        )
        if cursor.fetchone() is not None:
            return
    ShadowConsoleRepository.record_draft(
        connection,
        agent_run_id=claimed.agent_run_id,
        store_id=claimed.store_id,
        conversation_binding_id=claimed.conversation_binding_id,
        contact_binding_id=claimed.contact_binding_id,
        draft_text=result.draft_text,
        # Read from the run, not asserted. `AgentRunResult.status` is
        # `Literal["DRAFT_REQUIRES_HUMAN", "REQUIRE_HUMAN"]` and carries the runtime's terminal
        # disposition; hardcoding "DRAFT" here threw it away, so a run that never reached the
        # provider -- where the bounded runtime falls back to DETERMINISTIC_HANDOFF_TEXT and
        # concludes REQUIRE_HUMAN -- was filed and shown to staff as an ordinary AI draft awaiting
        # approval. That is wrong twice: a reviewer approves text believing a model wrote it, and
        # the Shadow evidence the capability ladder is measured on records a deterministic fallback
        # as model output. `agent_drafts.terminal_outcome` has admitted both values since 0021.
        #
        # That mapping could still never produce REQUIRE_HUMAN, because the runner reported every
        # returned output as DRAFT_REQUIRES_HUMAN and the bounded runtime returns, rather than
        # raises, on a handoff. `result.status` now carries the runtime's own disposition and
        # `result.terminal_code` its own reason (PROVIDER_TIMEOUT, COST_BUDGET_EXHAUSTED, a
        # model-requested reason code, ...), so the reviewer sees why no model draft exists.
        terminal_outcome=("DRAFT" if result.status == "DRAFT_REQUIRES_HUMAN" else "REQUIRE_HUMAN"),
        terminal_code=result.terminal_code,
        tool_call_count=result.tool_call_count,
        correlation_id=correlation_id,
        now=timestamp,
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "DISABLED_STATUS",
    "RUNTIME_DEADLINE_GRACE",
    "STOPPED_STATUS",
    "TURN_DEADLINE_SECONDS",
    "AgentPipeline",
    "CapturingEvidenceSink",
    "PinnedPrompt",
    "PipelineConfigurationError",
    "RunScopedContextLoader",
    "build_agent_cycle",
    "build_agent_pipeline",
    "load_pinned_prompt",
    "utc_now",
]
