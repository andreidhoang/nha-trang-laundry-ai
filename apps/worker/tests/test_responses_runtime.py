from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    AgentToolOperation,
    ReleaseCapability,
)
from nha_trang_laundry_worker.agent_runner import (
    AgentRunJob,
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentRunRejected,
    AgentRuntimeInvocation,
    AgentToolBridgeRejected,
    AgentToolBridgeSession,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
)
from nha_trang_laundry_worker.responses_runtime import (
    CURRENT_TOOL_CONTRACT_HASH,
    DETERMINISTIC_HANDOFF_TEXT,
    BoundedResponsesRuntime,
    BoundResponsesContextLoader,
    RecordingResponsesEvidenceSink,
    ResponsesConnectionFailure,
    ResponsesOutcomeAmbiguous,
    ResponsesPriceTable,
    ResponsesProviderResponse,
    ResponsesRequestCancelled,
    ResponsesRuntimeConfig,
    ResponsesRuntimeContext,
    ResponsesTransportTimeout,
    ScriptedResponsesTransport,
)

HASH = "sha256:" + ("1" * 64)
START = datetime(2026, 8, 11, 1, 0, tzinfo=UTC)
INSTRUCTIONS = "Chỉ tạo bản nháp và dùng các công cụ đã đăng ký."
INSTRUCTIONS_HASH = "sha256:" + sha256(INSTRUCTIONS.encode()).hexdigest()


class Clock:
    def __init__(self, value: datetime = START) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class FacadeTransport:
    def __init__(
        self,
        responses: list[AgentToolForwardResponse] | None = None,
        on_send: Any | None = None,
    ) -> None:
        self.responses = responses or [catalog_response()]
        self.requests: list[AgentToolForwardRequest] = []
        self._on_send = on_send

    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        self.requests.append(request)
        if self._on_send is not None:
            self._on_send()
        return self.responses.pop(0) if self.responses else catalog_response()


def catalog_response(*, malformed: bool = False) -> AgentToolForwardResponse:
    body: dict[str, Any]
    if malformed:
        body = {"unregistered": True}
    else:
        body = {
            "ok": True,
            "trace_id": "tr_12345678",
            "decision": {
                "outcome": "REQUIRE_HUMAN",
                "reason_codes": ["SHADOW_MODE_ALL_SENDS"],
                "obligations": [],
                "policy_version": "policy-eval-only-v1",
                "snapshot_hash": "sha256:" + ("a" * 64),
            },
            "data": {"candidates": []},
        }
    return AgentToolForwardResponse(status_code=200, headers={}, body=body)


def private_key() -> str:
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def config(**overrides: Any) -> ResponsesRuntimeConfig:
    values: dict[str, Any] = {
        "runtime_id": "responses-test",
        "model_id": "gpt-test",
        "immutable_model_release": "gpt-test-2026-08-01",
        "reasoning_effort": "low",
        "runtime_registry_hash": HASH,
        "prompt_bundle_version": "prompt-v1",
        "prompt_bundle_hash": HASH,
        "prompt_instructions_hash": INSTRUCTIONS_HASH,
        "tool_contract_hash": CURRENT_TOOL_CONTRACT_HASH,
        "max_model_calls": 3,
        "max_input_tokens": 8000,
        "max_output_tokens": 1200,
        "max_turn_cost_usd": "0.02",
    }
    values.update(overrides)
    if "price_table" not in overrides:
        values["price_table"] = ResponsesPriceTable.assemble(
            price_table_version="price-v1",
            model_id=values["model_id"],
            immutable_model_release=values["immutable_model_release"],
            effective_at=START - timedelta(days=1),
            input_cost_per_million_usd="1",
            cached_input_cost_per_million_usd="0.25",
            output_cost_per_million_usd="2",
        )
    return ResponsesRuntimeConfig(**values)


def final_response(
    *,
    draft: str = "Bản nháp đã được kiểm tra.",
    input_tokens: int = 100,
    cached_input_tokens: int = 0,
    output_tokens: int = 20,
) -> dict[str, Any]:
    envelope = {
        "disposition": "DRAFT_REQUIRES_HUMAN",
        "draft_text": draft,
        "reason_code": None,
    }
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
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
        },
    }


def handoff_response(reason: str = "MISSING_REQUIRED_FACT") -> dict[str, Any]:
    envelope = {
        "disposition": "REQUIRE_HUMAN",
        "draft_text": None,
        "reason_code": reason,
    }
    response = final_response()
    response["output"][0]["content"][0]["text"] = json.dumps(envelope)
    return response


def tool_response(
    *,
    name: str = "catalogResolve",
    call_id: str = "call_001",
    arguments: str = '{"query":"giặt chăn","locale":"vi-VN","known_attributes":null}',
    reasoning: bool = False,
) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    if reasoning:
        output.append({"type": "reasoning", "id": "rs_001", "encrypted_content": "opaque"})
    output.append(
        {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": arguments,
        }
    )
    return {
        "status": "completed",
        "parallel_tool_calls": False,
        "output": output,
        "usage": {"input_tokens": 50, "output_tokens": 10},
    }


def harness(
    script: list[Any],
    *,
    capability: ReleaseCapability = ReleaseCapability.INTERNAL_SHADOW,
    current_config: ResponsesRuntimeConfig | None = None,
    clock: Clock | None = None,
    facade: FacadeTransport | None = None,
) -> tuple[
    BoundedResponsesRuntime,
    AgentRuntimeInvocation,
    AgentToolBridgeSession,
    ScriptedResponsesTransport,
    RecordingResponsesEvidenceSink,
    FacadeTransport,
    ResponsesRuntimeContext,
]:
    selected_clock = clock or Clock()
    selected_config = current_config or config()
    current_job = AgentRunJob(
        run_id=uuid4(),
        organization_id=uuid4(),
        store_id=uuid4(),
        channel="INTERNAL_TEST",
        conversation_binding_id=uuid4(),
        contact_binding_id=uuid4(),
        capability=capability,
        stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        started_at=selected_clock(),
        deadline_at=selected_clock() + timedelta(seconds=20),
    )
    token = "b" * 32
    facade_transport = facade or FacadeTransport()
    bridge = AgentToolBridgeSession(
        job=current_job,
        bridge_token=token,
        issuer=AgentRunnerTokenIssuer(
            issuer="https://control-plane.test",
            audience="agent-tool-facade",
            private_key=private_key(),
        ),
        transport=facade_transport,
    )
    invocation = AgentRuntimeInvocation(
        run_id=current_job.run_id,
        capability=capability,
        session_key=bridge.session_key,
        bridge_token=token,
    )
    context = ResponsesRuntimeContext.assemble(
        run_id=current_job.run_id,
        capability=capability,
        session_key=bridge.session_key,
        config=selected_config,
        deadline_at=current_job.deadline_at,
        instructions=INSTRUCTIONS,
        input_text="Khách hỏi về dịch vụ giặt chăn.",
    )
    provider = ScriptedResponsesTransport(script)
    evidence = RecordingResponsesEvidenceSink()
    runtime = BoundedResponsesRuntime(
        config=selected_config,
        context_loader=BoundResponsesContextLoader((context,)),
        transport=provider,
        evidence_sink=evidence,
        now=selected_clock,
    )
    return runtime, invocation, bridge, provider, evidence, facade_transport, context


def invoke(script: list[Any], **kwargs: Any) -> tuple[Any, ...]:
    parts = harness(script, **kwargs)
    output = parts[0].invoke(parts[1], parts[2])
    return (output, *parts[3:])


def assert_strict_objects(value: Any) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if value.get("type") == "object" or isinstance(properties, dict):
            assert value["additionalProperties"] is False
            assert set(value["required"]) == set(properties or {})
        for child in value.values():
            assert_strict_objects(child)
    elif isinstance(value, list):
        for child in value:
            assert_strict_objects(child)


def test_zero_tool_draft_uses_exact_stateless_strict_request_and_redacted_evidence() -> None:
    output, provider, evidence, facade, context = invoke([final_response()])

    assert output.draft_text == "Bản nháp đã được kiểm tra."
    assert output.model_calls == 1
    assert facade.requests == []
    request = provider.requests[0]
    assert request.model == "gpt-test"
    assert request.store is False
    assert request.parallel_tool_calls is False
    assert request.include == ("reasoning.encrypted_content",)
    assert request.tool_choice == "auto"
    assert {tool.type for tool in request.tools} == {"function"}
    assert {tool.name for tool in request.tools} == {
        operation.value for operation in AgentToolOperation
    }
    assert all(tool.strict is True for tool in request.tools)
    for tool in request.tools:
        assert_strict_objects(tool.parameters)
    serialized_request = request.model_dump(mode="json", by_alias=True)
    assert "previous_response_id" not in serialized_request
    assert serialized_request["text"]["format"]["schema"] == request.text.format.schema_

    assert len(evidence.records) == 1
    record = evidence.records[0]
    assert record.terminal_outcome == "DRAFT"
    assert record.terminal_code == "VALIDATED_DRAFT"
    assert record.provider_backed is False
    assert record.context_packet_hash == context.context_packet_hash
    assert record.chain_of_thought_persisted is False
    encoded = record.model_dump_json()
    assert "Khách hỏi" not in encoded
    assert "Bản nháp" not in encoded
    assert "arguments" not in encoded
    assert "raw_provider_response" not in encoded


def test_serial_tool_round_trip_carries_ephemeral_reasoning_and_uses_only_bridge() -> None:
    output, provider, evidence, facade, _ = invoke(
        [tool_response(reasoning=True), final_response()]
    )

    assert output.model_calls == 2
    assert len(facade.requests) == 1
    assert facade.requests[0].path == "/agent/v1/catalog:resolve"
    assert len(provider.requests) == 2
    continued_types = [item.type for item in provider.requests[1].input]
    assert continued_types == ["message", "reasoning", "function_call", "function_call_output"]
    function_output = provider.requests[1].input[-1]
    assert function_output.type == "function_call_output"
    assert "candidates" in function_output.output
    assert evidence.records[0].tool_call_count == 1
    assert "opaque" not in evidence.records[0].model_dump_json()


@pytest.mark.parametrize(
    ("provider_output", "terminal_code"),
    [
        (tool_response(name="genericDispatch"), "UNKNOWN_TOOL"),
        (tool_response(arguments="{"), "MALFORMED_TOOL_ARGUMENTS_JSON"),
        (
            tool_response(
                arguments=('{"query":"giặt chăn","locale":"vi-VN","unknown_field":true}')
            ),
            "VALIDATION_ERROR",
        ),
        (
            tool_response(
                arguments=(f'{{"query":"giặt chăn","locale":"vi-VN","customer_id":"{uuid4()}"}}')
            ),
            "VALIDATION_ERROR",
        ),
        (
            tool_response(
                arguments=(
                    '{"query":"giặt chăn","locale":"vi-VN",'
                    '"known_attributes":null,"customer_id":null}'
                )
            ),
            "VALIDATION_ERROR",
        ),
    ],
)
def test_unknown_malformed_or_identity_substituting_tool_call_fails_closed(
    provider_output: dict[str, Any], terminal_code: str
) -> None:
    output, provider, evidence, facade, _ = invoke([provider_output])

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert len(provider.requests) == 1
    assert facade.requests == []
    assert evidence.records[0].terminal_outcome == "REQUIRE_HUMAN"
    assert evidence.records[0].terminal_code == terminal_code


def test_malformed_facade_result_is_not_returned_to_model() -> None:
    facade = FacadeTransport([catalog_response(malformed=True)])
    output, provider, evidence, facade, _ = invoke([tool_response()], facade=facade)

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert len(provider.requests) == 1
    assert len(facade.requests) == 1
    assert evidence.records[0].terminal_code == "TOOL_UNAVAILABLE"


@pytest.mark.parametrize(
    ("response", "terminal_code"),
    [
        (
            {
                **tool_response(),
                "output": [
                    tool_response(call_id="call_1")["output"][0],
                    tool_response(call_id="call_2")["output"][0],
                ],
            },
            "MULTIPLE_OR_MIXED_OUTPUT_ITEMS",
        ),
        ({**tool_response(), "parallel_tool_calls": True}, "MALFORMED_PROVIDER_RESPONSE"),
        (
            {
                **tool_response(),
                "output": [{"type": "computer_call", "id": "unsafe"}],
            },
            "MALFORMED_PROVIDER_RESPONSE",
        ),
        ({"status": "completed", "output": []}, "MALFORMED_PROVIDER_RESPONSE"),
    ],
)
def test_multiple_parallel_unsupported_or_malformed_provider_output_is_rejected(
    response: dict[str, Any], terminal_code: str
) -> None:
    output, provider, evidence, facade, _ = invoke([response])

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert len(provider.requests) == 1
    assert facade.requests == []
    assert evidence.records[0].terminal_code == terminal_code


def test_duplicate_call_id_is_rejected_before_second_bridge_call() -> None:
    output, provider, evidence, facade, _ = invoke(
        [tool_response(call_id="same"), tool_response(call_id="same")]
    )

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert len(provider.requests) == 2
    assert len(facade.requests) == 1
    assert evidence.records[0].terminal_code == "DUPLICATE_TOOL_CALL_ID"


def test_per_intent_model_budget_does_not_reset_after_tool_call() -> None:
    output, provider, evidence, facade, _ = invoke(
        [tool_response()], capability=ReleaseCapability.PUBLIC_FAQ
    )

    assert output.model_calls == 1
    assert len(provider.requests) == 1
    assert len(facade.requests) == 1
    assert evidence.records[0].terminal_code == "MODEL_CALL_BUDGET_EXHAUSTED"


def test_global_model_budget_does_not_reset_across_distinct_tool_calls() -> None:
    limited = config(max_model_calls=2)
    output, provider, evidence, facade, _ = invoke(
        [tool_response(call_id="one"), tool_response(call_id="two")],
        current_config=limited,
    )

    assert output.model_calls == 2
    assert len(provider.requests) == 2
    assert len(facade.requests) == 2
    assert evidence.records[0].terminal_code == "MODEL_CALL_BUDGET_EXHAUSTED"


def test_deadline_exhausted_before_model_reserves_nothing() -> None:
    clock = Clock()
    parts = harness([final_response()], clock=clock)
    clock.advance(20)
    output = parts[0].invoke(parts[1], parts[2])

    assert output.model_calls == 0
    assert parts[3].requests == []
    record = parts[4].records[0]
    assert record.terminal_code == "DEADLINE_EXHAUSTED"
    assert record.reservation_count == 0
    assert record.settled_cost_usd == "0"


def test_deadline_after_provider_prevents_tool_authority() -> None:
    class AdvancingProvider(ScriptedResponsesTransport):
        def create(self, request: Any, *, timeout_seconds: float) -> Any:
            result = super().create(request, timeout_seconds=timeout_seconds)
            clock.advance(20)
            return result

    clock = Clock()
    runtime, invocation, bridge, _, evidence, facade, context = harness(
        [tool_response()], clock=clock
    )
    provider = AdvancingProvider([tool_response()])
    runtime = BoundedResponsesRuntime(
        config=config(),
        context_loader=BoundResponsesContextLoader((context,)),
        transport=provider,
        evidence_sink=evidence,
        now=clock,
    )

    output = runtime.invoke(invocation, bridge)

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert facade.requests == []
    assert evidence.records[0].terminal_code == "DEADLINE_EXHAUSTED"


def test_deadline_crossed_during_tool_returns_handoff_and_revokes_bridge() -> None:
    clock = Clock()
    facade = FacadeTransport(on_send=lambda: clock.advance(20))
    runtime, invocation, bridge, provider, evidence, _, _ = harness(
        [tool_response()], clock=clock, facade=facade
    )

    output = runtime.invoke(invocation, bridge)

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert len(provider.requests) == 1
    assert len(facade.requests) == 1
    assert evidence.records[0].terminal_code == "DEADLINE_EXHAUSTED"
    with pytest.raises(AgentToolBridgeRejected, match="bridge is closed"):
        bridge.invoke(
            AgentToolOperation.CATALOG_RESOLVE,
            {"query": "late", "locale": "vi-VN"},
            bridge_token=invocation.bridge_token,
            binding_id=bridge.binding_id,
            route_path_parameters={},
            now=START,
        )


@pytest.mark.parametrize(
    ("failure", "code", "settled"),
    [
        (ResponsesConnectionFailure("not sent"), "PROVIDER_CONNECTION_FAILURE", False),
        (ResponsesOutcomeAmbiguous("unknown"), "PROVIDER_OUTCOME_AMBIGUOUS", True),
        (ResponsesRequestCancelled("cancelled"), "PROVIDER_REQUEST_CANCELLED", True),
        (ResponsesTransportTimeout("timeout"), "PROVIDER_TIMEOUT", True),
    ],
)
def test_provider_failure_has_no_hidden_retry_and_accounts_reservation(
    failure: Exception, code: str, settled: bool
) -> None:
    output, provider, evidence, facade, _ = invoke([failure])

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert len(provider.requests) == 1
    assert facade.requests == []
    record = evidence.records[0]
    assert record.terminal_code == code
    assert record.retry_count == 0
    if settled:
        assert record.settled_cost_usd == record.reserved_cost_usd
        assert record.released_cost_usd == "0"
    else:
        assert record.settled_cost_usd == "0"
        assert record.released_cost_usd == record.reserved_cost_usd


@pytest.mark.parametrize(
    "text",
    [
        "send this directly",
        '{"action":"send","automatic_send_authorized":true}',
        json.dumps(
            {
                "disposition": "DRAFT_REQUIRES_HUMAN",
                "draft_text": "draft",
                "reason_code": None,
                "send": True,
            }
        ),
    ],
)
def test_invalid_final_or_action_send_encoding_becomes_deterministic_handoff(text: str) -> None:
    response = final_response()
    response["output"][0]["content"][0]["text"] = text

    output, provider, evidence, facade, _ = invoke([response])

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert text not in output.draft_text
    assert len(provider.requests) == 1
    assert facade.requests == []
    assert evidence.records[0].terminal_code in {"MALFORMED_FINAL_JSON", "INVALID_FINAL_OUTPUT"}


def test_model_requested_handoff_is_validated_but_never_authorizes_send() -> None:
    output, _, evidence, facade, _ = invoke([handoff_response()])

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert facade.requests == []
    assert evidence.records[0].terminal_outcome == "REQUIRE_HUMAN"
    assert evidence.records[0].terminal_code == "MISSING_REQUIRED_FACT"


def test_success_settles_actual_cost_and_releases_unused_reservation() -> None:
    output, _, evidence, _, _ = invoke([final_response(input_tokens=100, output_tokens=20)])

    assert output.input_tokens == 100
    assert output.output_tokens == 20
    record = evidence.records[0]
    assert record.reserved_cost_usd == "0.0104"
    assert record.settled_cost_usd == "0.00014"
    assert record.released_cost_usd == "0.01026"


def test_cached_input_uses_the_registered_versioned_price_table() -> None:
    output, _, evidence, _, _ = invoke(
        [final_response(input_tokens=100, cached_input_tokens=40, output_tokens=20)]
    )

    assert output.input_tokens == 100
    record = evidence.records[0]
    assert record.cached_input_tokens == 40
    assert record.price_table_version == "price-v1"
    assert record.price_table_hash.startswith("sha256:")
    assert record.settled_cost_usd == "0.00011"


def test_unreservable_turn_cost_fails_before_provider_use() -> None:
    too_small = config(max_turn_cost_usd="0.001")
    output, provider, evidence, facade, _ = invoke([final_response()], current_config=too_small)

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert output.model_calls == 0
    assert provider.requests == []
    assert facade.requests == []
    assert evidence.records[0].terminal_code == "COST_BUDGET_EXHAUSTED"


def test_context_pin_substitution_fails_before_model_or_tool() -> None:
    runtime, invocation, bridge, provider, evidence, facade, context = harness([final_response()])
    mismatched_config = config(model_id="different-model")
    runtime = BoundedResponsesRuntime(
        config=mismatched_config,
        context_loader=BoundResponsesContextLoader((context,)),
        transport=provider,
        evidence_sink=evidence,
        now=Clock(),
    )

    output = runtime.invoke(invocation, bridge)

    assert output.draft_text == DETERMINISTIC_HANDOFF_TEXT
    assert provider.requests == []
    assert facade.requests == []
    assert evidence.records[0].terminal_code == "CONTEXT_MODEL_MISMATCH"


def test_scripted_transport_returns_typed_response_without_network() -> None:
    typed = ResponsesProviderResponse.model_validate(final_response())
    output, provider, evidence, _, _ = invoke([typed])

    assert output.model_calls == 1
    assert len(provider.requests) == 1
    assert evidence.records[0].terminal_code == "VALIDATED_DRAFT"


def test_provider_backed_transport_remains_behind_existing_release_gates() -> None:
    class ProviderBackedScript(ScriptedResponsesTransport):
        provider_backed = True

    current_config = config()
    current_job = AgentRunJob(
        run_id=uuid4(),
        organization_id=uuid4(),
        store_id=uuid4(),
        channel="INTERNAL_TEST",
        conversation_binding_id=uuid4(),
        contact_binding_id=uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        started_at=START,
        deadline_at=START + timedelta(seconds=20),
    )
    context = ResponsesRuntimeContext.assemble(
        run_id=current_job.run_id,
        capability=current_job.capability,
        session_key=current_job.session_key(),
        config=current_config,
        deadline_at=current_job.deadline_at,
        instructions=INSTRUCTIONS,
        input_text="Synthetic only.",
    )
    provider = ProviderBackedScript([final_response()])
    evidence = RecordingResponsesEvidenceSink()
    runtime = BoundedResponsesRuntime(
        config=current_config,
        context_loader=BoundResponsesContextLoader((context,)),
        transport=provider,
        evidence_sink=evidence,
        now=Clock(),
    )
    token_issuer = AgentRunnerTokenIssuer(
        issuer="https://control-plane.test",
        audience="agent-tool-facade",
        private_key=private_key(),
    )

    with pytest.raises(AgentRunRejected, match="provider runtime release gates are incomplete"):
        AgentRunner(token_issuer).execute(
            job=current_job,
            runtime=runtime,
            transport=FacadeTransport(),
            bridge_token="z" * 32,
            now=START,
        )

    assert provider.requests == []
    assert evidence.records == []
