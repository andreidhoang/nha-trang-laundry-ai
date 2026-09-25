"""Scaled synthetic proof of the 20-second Agent Runner timeout boundary.

AGENT-SHADOW-DEFECTS-001 F9. This preflight used to report "the inbox event remains recoverable"
by reading the fixture's own `recovery_state`, and "no automatic fallback message" as a constant.
Both are now observations of the system: the claimed run goes through the real
`DurableAgentRunWorker`, `AgentRunner` and policy gate, and the answers are read off what the
worker actually recorded against a run ledger that enforces the same claim predicate as
`agent_runs._assert_claim`. If the failure could not be recorded under a live claim, the
observation says so.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    ReleaseCapability,
)
from nha_trang_laundry_db.agent_runs import (
    AgentRunPolicyFacts,
    AgentRunStateError,
    ClaimedAgentRun,
)
from nha_trang_laundry_worker.agent_run_policy import AgentRunPolicyGate
from nha_trang_laundry_worker.agent_runner import (
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentRuntimeInvocation,
    AgentRuntimeOutput,
    AgentToolBridgeSession,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
    ConstrainedAgentRuntime,
    ExecutionPins,
)
from nha_trang_laundry_worker.durable_agent_worker import RECORDING_MARGIN, DurableAgentRunWorker

from .fixtures import SyntheticFixtureBundle

#: The production hard limit the fixture declares; the run below is a scaled stand-in for it.
PRODUCTION_LIMIT_SECONDS = 20
#: The scaled deadline. The worker refuses a claim that cannot cover at least one second of run.
SCALED_DEADLINE = timedelta(milliseconds=1200)


class SyntheticTimeoutError(ValueError):
    """The timeout fixture cannot prove the required fail-closed behavior."""


@dataclass(frozen=True, slots=True)
class SyntheticTimeoutPreflight:
    """Hash-safe timeout result for a non-provider, non-release test path."""

    timed_out_within_limit: bool
    automatic_fallback_message_created: bool
    inbox_event_recoverable: bool
    #: What the worker wrote to the run ledger, or `None` when nothing could be written.
    recorded_failure_code: str | None
    trace_id: str


#: A synthetic release stated by both the claim and the runtime double. The runner compares them
#: before invoking (AGENT-SHADOW-DEFECTS-001 F3); this preflight exercises the deadline, not pins.
_SYNTHETIC_PINS = ExecutionPins(
    runtime_registry_version="synthetic-timeout-v1",
    runtime_registry_hash="sha256:" + "0" * 64,
    prompt_bundle_version="synthetic-timeout-v1",
    prompt_bundle_hash="sha256:" + "0" * 64,
    tool_contract_hash="sha256:" + "0" * 64,
)


class LeaseEnforcingRunLedger:
    """In-memory run ledger with `_assert_claim`'s predicate, checked against the wall clock.

    It records only what the worker writes; the preflight's observations are read from it.
    """

    def __init__(self) -> None:
        self._pending: ClaimedAgentRun | None = None
        self._live: ClaimedAgentRun | None = None
        self.failures: list[str] = []
        self.completions = 0
        self.tool_calls = 0

    def load(self, claim: ClaimedAgentRun) -> None:
        self._pending = claim

    def claim_next(
        self, connection: Any, *, worker_role: Any, now: datetime
    ) -> ClaimedAgentRun | None:
        del connection, worker_role, now
        self._live, self._pending = self._pending, None
        return self._live

    def record_tool_call(self, connection: Any, entry: Any) -> None:
        del connection, entry
        self._assert_claim()
        self.tool_calls += 1

    def complete_draft(self, connection: Any, **values: Any) -> None:
        del connection, values
        self._assert_claim()
        self.completions += 1

    def fail(self, connection: Any, **values: Any) -> None:
        del connection
        self._assert_claim()
        self.failures.append(str(values["failure_code"]))

    def _assert_claim(self) -> None:
        if self._live is None or self._live.lease_expires_at < datetime.now(UTC):
            raise AgentRunStateError("agent run claim is stale or unavailable")


class _NoToolTransport:
    def __init__(self) -> None:
        self.requests: list[AgentToolForwardRequest] = []

    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        self.requests.append(request)
        raise SyntheticTimeoutError("a timeout preflight must not call any tool")


class _BlockingRuntime:
    provider_backed = False
    execution_pins = _SYNTHETIC_PINS

    def __init__(self) -> None:
        self.release = Event()

    def invoke(
        self, invocation: AgentRuntimeInvocation, bridge: AgentToolBridgeSession
    ) -> AgentRuntimeOutput:
        del invocation, bridge
        self.release.wait(timeout=5)
        # Only reached after the deadline, when the runner has already discarded it.
        return AgentRuntimeOutput(
            disposition="REQUIRE_HUMAN",
            terminal_code="SYNTHETIC_LATE_OUTPUT",
            draft_text="Nhân viên sẽ hỗ trợ.",
            model_calls=0,
        )


class _NoTransaction:
    def transaction(self) -> Any:
        return self

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


def execute_model_timeout_preflight(
    fixture: SyntheticFixtureBundle,
    *,
    ledger: LeaseEnforcingRunLedger | None = None,
    runtime: ConstrainedAgentRuntime | None = None,
) -> SyntheticTimeoutPreflight:
    """Prove a scaled deadline with the production 20-second upper bound still enforced.

    The fixture declares the normative 20-second fault. The run here is scaled to a
    1.2-second deadline; ``AgentRunJob`` independently rejects any deadline above 20 seconds.
    No provider response, model text, or customer identifier is emitted into the result.
    """

    context = fixture.payload.get("authenticated_context")
    seed = fixture.payload.get("database_seed")
    fault = fixture.payload.get("fault_injection")
    if (
        not isinstance(context, Mapping)
        or not isinstance(seed, Mapping)
        or not isinstance(fault, Mapping)
    ):
        raise SyntheticTimeoutError("timeout fixture sections are invalid")
    if fault.get("runtime_wall_clock_exceeds_seconds") != PRODUCTION_LIMIT_SECONDS:
        raise SyntheticTimeoutError("timeout fixture must declare the 20-second hard limit")
    if not isinstance(seed.get("inbox_event"), Mapping):
        # The fixture must describe an inbound event to be recovered. What state it ends in is
        # observed below, never read from here.
        raise SyntheticTimeoutError("timeout fixture has no inbox event")

    started_at = datetime.now(UTC)
    run_ledger = ledger or LeaseEnforcingRunLedger()
    run_ledger.load(
        ClaimedAgentRun(
            agent_run_id=UUID("00000000-0000-4000-8000-000000000026"),
            claim_token=uuid4(),
            organization_id=_uuid(context, "organization_id"),
            store_id=_uuid(context, "store_id"),
            channel=_text(context, "channel"),
            conversation_binding_id=_uuid(context, "conversation_binding_id"),
            contact_binding_id=_uuid(context, "contact_binding_id"),
            capability=ReleaseCapability.INTERNAL_SHADOW,
            deployment_stage=AgentDeploymentStage.SHADOW,
            data_classification=AgentDataClassification.SYNTHETIC,
            runtime_registry_version=_SYNTHETIC_PINS.runtime_registry_version,
            runtime_registry_hash=_SYNTHETIC_PINS.runtime_registry_hash,
            prompt_bundle_version=_SYNTHETIC_PINS.prompt_bundle_version,
            prompt_bundle_hash=_SYNTHETIC_PINS.prompt_bundle_hash,
            tool_contract_hash=_SYNTHETIC_PINS.tool_contract_hash,
            order_request_id=None,
            public_code=None,
            bound_row_version=0,
            attempt_count=1,
            lease_expires_at=started_at + RECORDING_MARGIN + SCALED_DEADLINE,
        )
    )
    drafts: list[object] = []
    worker = DurableAgentRunWorker(
        AgentRunner(_issuer()),
        repository=run_ledger,  # type: ignore[arg-type]
        draft_recorder=lambda *record: drafts.append(record),
        policy_gate=AgentRunPolicyGate(
            facts_reader=lambda _connection, claimed: AgentRunPolicyFacts(
                data_classification=claimed.data_classification,
                suppression_states=(),
                has_source_event=False,
                source_contact_binding_id=None,
                gate=None,
            )
        ),
    )
    blocking = _BlockingRuntime()
    transport = _NoToolTransport()
    clock = time.monotonic()
    try:
        outcome = worker.run_once(
            _NoTransaction(),
            # Injectable so a test can show the observations follow the run, not the fixture.
            runtime=runtime or blocking,
            transport=transport,
            correlation_id=uuid4(),
            now=started_at,
        )
    finally:
        blocking.release.set()
    elapsed = time.monotonic() - clock
    recorded = run_ledger.failures[0] if run_ledger.failures else None
    return SyntheticTimeoutPreflight(
        timed_out_within_limit=(recorded == "MODEL_TIMEOUT" and elapsed < PRODUCTION_LIMIT_SECONDS),
        automatic_fallback_message_created=bool(
            drafts or run_ledger.completions or transport.requests
        ),
        # Recoverable means a human can find the run and why it stopped: its failure is on the
        # durable record, written under a claim that was still this worker's.
        inbox_event_recoverable=outcome.status == "FAILED" and recorded is not None,
        recorded_failure_code=recorded,
        trace_id="synthetic-model-timeout-001",
    )


def _issuer() -> AgentRunnerTokenIssuer:
    private_key = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    return AgentRunnerTokenIssuer(
        issuer="https://synthetic-eval.invalid",
        audience="agent-tool-facade",
        private_key=private_key,
    )


def _uuid(context: Mapping[str, Any], key: str) -> UUID:
    try:
        return UUID(str(context[key]))
    except (KeyError, ValueError) as error:
        raise SyntheticTimeoutError(f"timeout fixture {key} is invalid") from error


def _text(context: Mapping[str, Any], key: str) -> str:
    value = context.get(key)
    if not isinstance(value, str):
        raise SyntheticTimeoutError(f"timeout fixture {key} is invalid")
    return value
