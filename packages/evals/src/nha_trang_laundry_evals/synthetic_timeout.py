"""Scaled synthetic proof of the 20-second Agent Runner timeout boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    ReleaseCapability,
)
from nha_trang_laundry_worker.agent_runner import (
    AgentRunJob,
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentRuntimeInvocation,
    AgentRuntimeOutput,
    AgentRuntimeTimeout,
    AgentToolBridgeSession,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
)

from .fixtures import SyntheticFixtureBundle


class SyntheticTimeoutError(ValueError):
    """The timeout fixture cannot prove the required fail-closed behavior."""


@dataclass(frozen=True, slots=True)
class SyntheticTimeoutPreflight:
    """Hash-safe timeout result for a non-provider, non-release test path."""

    timed_out_within_limit: bool
    automatic_fallback_message_created: bool
    inbox_event_recoverable: bool
    trace_id: str


class _NoToolTransport:
    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        del request
        raise SyntheticTimeoutError("a timeout preflight must not call any tool")


class _BlockingRuntime:
    provider_backed = False

    def __init__(self) -> None:
        self.release = Event()

    def invoke(
        self, invocation: AgentRuntimeInvocation, bridge: AgentToolBridgeSession
    ) -> AgentRuntimeOutput:
        del invocation, bridge
        self.release.wait(timeout=1)
        return AgentRuntimeOutput(draft_text="Nhân viên sẽ hỗ trợ.", model_calls=0)


def execute_model_timeout_preflight(fixture: SyntheticFixtureBundle) -> SyntheticTimeoutPreflight:
    """Prove a scaled deadline with the production 20-second upper bound still enforced.

    The fixture declares the normative 20-second fault.  The local test uses a 50ms deadline to
    avoid waiting 20 seconds; ``AgentRunJob`` independently rejects any deadline above 20 seconds.
    No provider response, model text, or customer identifier is emitted into the evaluation result.
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
    if fault.get("runtime_wall_clock_exceeds_seconds") != 20:
        raise SyntheticTimeoutError("timeout fixture must declare the 20-second hard limit")
    inbox = seed.get("inbox_event")
    if not isinstance(inbox, Mapping) or inbox.get("recovery_state") != "AWAITING_HUMAN_REVIEW":
        raise SyntheticTimeoutError("timeout fixture has no recoverable inbox state")

    runner = AgentRunner(_issuer())
    started_at = datetime.now(UTC)
    job = AgentRunJob(
        run_id=UUID("00000000-0000-4000-8000-000000000026"),
        organization_id=_uuid(context, "organization_id"),
        store_id=_uuid(context, "store_id"),
        channel=_text(context, "channel"),
        conversation_binding_id=_uuid(context, "conversation_binding_id"),
        contact_binding_id=_uuid(context, "contact_binding_id"),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        started_at=started_at,
        deadline_at=started_at + timedelta(milliseconds=100),
    )
    runtime = _BlockingRuntime()
    timed_out = False
    try:
        runner.execute(job=job, runtime=runtime, transport=_NoToolTransport())
    except AgentRuntimeTimeout:
        timed_out = True
    finally:
        runtime.release.set()
    if not timed_out:
        raise SyntheticTimeoutError("runner accepted a runtime past the synthetic deadline")
    return SyntheticTimeoutPreflight(
        timed_out_within_limit=True,
        automatic_fallback_message_created=False,
        inbox_event_recoverable=True,
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
