"""AGENT-SHADOW-DEFECTS-001 F8 in the runtime: a malformed date-time never leaves the process.

The bounded runtime validates a provider's tool arguments against the strict schema before the
bridge. That validator used the same format checker that silently skipped `date-time`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    ReleaseCapability,
)
from nha_trang_laundry_worker.agent_runner import (
    AgentRunJob,
    AgentRunnerTokenIssuer,
    AgentRuntimeInvocation,
    AgentToolBridgeSession,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
)
from nha_trang_laundry_worker.pipeline import (
    CapturingEvidenceSink,
    RunScopedContextLoader,
    load_pinned_prompt,
)
from nha_trang_laundry_worker.responses_runtime import (
    BoundedResponsesRuntime,
    ResponsesPriceTable,
    ResponsesRuntimeConfig,
    ScriptedResponsesTransport,
)

PINNED = load_pinned_prompt()


class _Facade:
    def __init__(self) -> None:
        self.requests: list[AgentToolForwardRequest] = []

    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        self.requests.append(request)
        return AgentToolForwardResponse(
            status_code=200,
            headers={},
            body={
                "ok": True,
                "trace_id": "tr_12345678",
                "decision": {
                    "outcome": "REQUIRE_HUMAN",
                    "reason_codes": [],
                    "obligations": ["SLOT_CONFIRMATION"],
                    "policy_version": "synthetic-internal-v1",
                    "snapshot_hash": "sha256:" + "a" * 64,
                },
                "data": {
                    "advisory": "UNKNOWN",
                    "advisory_ready_window_start": None,
                    "advisory_ready_window_end": None,
                    "slot_confirmed": False,
                    "required_approval": "SLOT_CONFIRMATION",
                },
            },
        )


def _config() -> ResponsesRuntimeConfig:
    now = datetime.now(UTC)
    return ResponsesRuntimeConfig(
        runtime_id="responses-datetime-test",
        model_id="gpt-test",
        immutable_model_release="gpt-test-2026-08-01",
        reasoning_effort="low",
        runtime_registry_version=PINNED.pins.runtime_registry_version,
        runtime_registry_hash=PINNED.pins.runtime_registry_hash,
        prompt_bundle_version=PINNED.pins.prompt_bundle_version,
        prompt_bundle_hash=PINNED.pins.prompt_bundle_hash,
        prompt_instructions_hash=PINNED.instructions_hash,
        tool_contract_hash=PINNED.pins.tool_contract_hash,
        max_model_calls=3,
        max_input_tokens=8000,
        max_output_tokens=1200,
        max_turn_cost_usd="0.02",
        price_table=ResponsesPriceTable.assemble(
            price_table_version="price-v1",
            model_id="gpt-test",
            immutable_model_release="gpt-test-2026-08-01",
            effective_at=now - timedelta(days=1),
            input_cost_per_million_usd="1",
            cached_input_cost_per_million_usd="0.25",
            output_cost_per_million_usd="2",
        ),
    )


def _capacity_call(requested: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "parallel_tool_calls": False,
        "output": [
            {
                "type": "function_call",
                "call_id": "call-capacity",
                "name": "capacityCheck",
                "arguments": json.dumps({"requested_ready_at": requested}),
            }
        ],
        "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5},
    }


def _final() -> dict[str, Any]:
    envelope = {"disposition": "DRAFT_REQUIRES_HUMAN", "draft_text": "Dạ.", "reason_code": None}
    return {
        "status": "completed",
        "parallel_tool_calls": False,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(envelope)}],
            }
        ],
        "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5},
    }


def _run(requested: str) -> tuple[Any, _Facade]:
    key = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    started = datetime.now(UTC)
    job = AgentRunJob(
        run_id=uuid4(),
        organization_id=uuid4(),
        store_id=uuid4(),
        channel="INTERNAL_TEST",
        conversation_binding_id=uuid4(),
        contact_binding_id=uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        started_at=started,
        deadline_at=started + timedelta(seconds=15),
        order_request_id=uuid4(),
        row_version=1,
    )
    facade = _Facade()
    bridge = AgentToolBridgeSession(
        job=job,
        bridge_token="b" * 32,
        issuer=AgentRunnerTokenIssuer(
            issuer="https://control-plane.test", audience="agent-tool-facade", private_key=key
        ),
        transport=facade,
    )
    config = _config()
    runtime = BoundedResponsesRuntime(
        config=config,
        context_loader=RunScopedContextLoader(
            config=config, instructions=PINNED.instructions, input_text_for=lambda _: "chiều mai"
        ),
        transport=ScriptedResponsesTransport([_capacity_call(requested), _final()]),
        evidence_sink=CapturingEvidenceSink(),
    )
    output = runtime.invoke(
        AgentRuntimeInvocation(
            run_id=job.run_id,
            capability=job.capability,
            session_key=bridge.session_key,
            bridge_token="b" * 32,
            deadline_at=job.deadline_at,
        ),
        bridge,
    )
    return output, facade


@pytest.mark.parametrize("words", ["tomorrow afternoon please", "2026-09-26"])
def test_a_provider_tool_call_with_words_for_a_timestamp_never_reaches_the_facade(
    words: str,
) -> None:
    output, facade = _run(words)

    assert facade.requests == []
    assert output.disposition == "REQUIRE_HUMAN"
    assert output.terminal_code == "VALIDATION_ERROR"


def test_a_provider_tool_call_with_a_real_timestamp_is_forwarded() -> None:
    output, facade = _run("2026-09-26T14:00:00+07:00")

    assert len(facade.requests) == 1
    assert output.disposition == "DRAFT_REQUIRES_HUMAN"
