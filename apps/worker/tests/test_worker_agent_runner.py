from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
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
    AgentToolBridgeRejected,
    AgentToolBridgeSession,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
    DisabledOpenClawProviderRuntime,
    ScriptedToolCall,
    SyntheticScriptedRuntime,
)
from nha_trang_laundry_worker.bridge_api import AgentBridgeSessionStore, create_agent_bridge_app


class RecordingTransport:
    def __init__(self, responses: list[AgentToolForwardResponse] | None = None) -> None:
        self.requests: list[AgentToolForwardRequest] = []
        self.responses = responses or []

    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        return catalog_response()


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def job(**overrides: Any) -> AgentRunJob:
    started_at = now()
    values: dict[str, Any] = {
        "run_id": uuid4(),
        "organization_id": uuid4(),
        "store_id": uuid4(),
        "channel": "INTERNAL_TEST",
        "conversation_binding_id": uuid4(),
        "contact_binding_id": uuid4(),
        "capability": ReleaseCapability.INTERNAL_SHADOW,
        "stage": AgentDeploymentStage.SHADOW,
        "data_classification": AgentDataClassification.SYNTHETIC,
        "started_at": started_at,
        "deadline_at": started_at + timedelta(seconds=20),
    }
    values.update(overrides)
    return AgentRunJob(**values)


def key_pair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_pem, public_pem


def issuer() -> tuple[AgentRunnerTokenIssuer, str]:
    private, public = key_pair()
    return (
        AgentRunnerTokenIssuer(
            issuer="https://control-plane.test",
            audience="agent-tool-facade",
            private_key=private,
        ),
        public,
    )


def catalog_response() -> AgentToolForwardResponse:
    return AgentToolForwardResponse(
        status_code=200,
        headers={},
        body={
            "ok": True,
            "trace_id": "tr_12345678",
            "decision": {
                "outcome": "REQUIRE_HUMAN",
                "reason_codes": ["SHADOW_MODE_ALL_SENDS"],
                "obligations": [],
                "policy_version": "policy-eval-only-v1",
                "snapshot_hash": f"sha256:{'a' * 64}",
            },
            "data": {"candidates": []},
        },
    )


def tool_unavailable_response() -> AgentToolForwardResponse:
    return AgentToolForwardResponse(
        status_code=503,
        headers={},
        body={
            "ok": False,
            "trace_id": "tr_12345678",
            "error": {
                "code": "TOOL_UNAVAILABLE",
                "message": "Deterministic tool adapter is unavailable.",
                "reason_codes": ["TOOL_UNAVAILABLE"],
                "field_errors": [],
            },
        },
    )


def create_response(request_id: UUID) -> AgentToolForwardResponse:
    return AgentToolForwardResponse(
        status_code=201,
        headers={"ETag": '"2"'},
        body={
            "ok": True,
            "trace_id": "tr_12345678",
            "decision": {
                "outcome": "REQUIRE_HUMAN",
                "reason_codes": ["SHADOW_MODE_ALL_SENDS"],
                "obligations": [],
                "policy_version": "policy-eval-only-v1",
                "snapshot_hash": f"sha256:{'a' * 64}",
            },
            "data": {"order_request_id": str(request_id), "row_version": 2, "status": "DRAFT"},
        },
    )


def record_facts_response(request_id: UUID) -> AgentToolForwardResponse:
    return AgentToolForwardResponse(
        status_code=200,
        headers={"ETag": '"2"'},
        body={
            "ok": True,
            "trace_id": "tr_12345678",
            "decision": {
                "outcome": "REQUIRE_HUMAN",
                "reason_codes": ["SHADOW_MODE_ALL_SENDS"],
                "obligations": [],
                "policy_version": "policy-eval-only-v1",
                "snapshot_hash": f"sha256:{'a' * 64}",
            },
            "data": {
                "order_request_id": str(request_id),
                "row_version": 2,
                "accepted_fact_count": 1,
                "unresolved_fact_types": [],
            },
        },
    )


def session(
    current_job: AgentRunJob | None = None,
) -> tuple[AgentToolBridgeSession, RecordingTransport, str, str]:
    token_issuer, public = issuer()
    transport = RecordingTransport()
    bridge_token = "a" * 32
    return (
        AgentToolBridgeSession(
            job=current_job or job(),
            bridge_token=bridge_token,
            issuer=token_issuer,
            transport=transport,
        ),
        transport,
        bridge_token,
        public,
    )


def invoke_catalog(
    bridge: AgentToolBridgeSession,
    current_job: AgentRunJob,
    bridge_token: str,
    **overrides: Any,
) -> None:
    bridge.invoke(
        AgentToolOperation.CATALOG_RESOLVE,
        overrides.pop("arguments", {"query": "giặt chăn", "locale": "vi-VN"}),
        bridge_token=overrides.pop("token", bridge_token),
        binding_id=overrides.pop("binding_id", current_job.binding_id),
        route_path_parameters=overrides.pop("path", {}),
        **overrides,
    )


def test_bridge_mints_short_lived_contact_bound_jwt_and_uses_fixed_facade_path() -> None:
    current_job = job()
    bridge, transport, bridge_token, public = session(current_job)

    invoke_catalog(bridge, current_job, bridge_token)

    request = transport.requests[0]
    claims = jwt.decode(
        request.headers["Authorization"].removeprefix("Bearer "),
        public,
        algorithms=["EdDSA"],
        audience="agent-tool-facade",
        issuer="https://control-plane.test",
    )
    assert request.path == "/agent/v1/catalog:resolve"
    assert claims["contact_binding_id"] == str(current_job.contact_binding_id)
    assert claims["capabilities"] == ["INTERNAL_SHADOW"]
    assert claims["exp"] - claims["iat"] <= 20


def test_tool_injection_is_rejected_before_transport() -> None:
    current_job = job()
    bridge, transport, bridge_token, _ = session(current_job)

    with pytest.raises(AgentToolBridgeRejected, match="VALIDATION_ERROR"):
        invoke_catalog(
            bridge,
            current_job,
            bridge_token,
            arguments={
                "query": "giặt chăn",
                "locale": "vi-VN",
                "customer_id": str(uuid4()),
            },
        )

    assert transport.requests == []


def test_runtime_deadline_revokes_bridge_before_a_late_tool_call() -> None:
    class BlockingRuntime:
        provider_backed = False

        def __init__(self) -> None:
            self.release = Event()
            self.late_tool_rejected = Event()

        def invoke(self, invocation: Any, bridge: AgentToolBridgeSession) -> Any:
            self.release.wait(timeout=5)
            try:
                bridge.invoke(
                    AgentToolOperation.CATALOG_RESOLVE,
                    {"query": "giặt chăn", "locale": "vi-VN"},
                    bridge_token=invocation.bridge_token,
                    binding_id=UUID(invocation.session_key.split(":")[1]),
                    route_path_parameters={},
                )
            except AgentToolBridgeRejected:
                self.late_tool_rejected.set()
            return {"draft_text": "handoff", "model_calls": 0}

    current_job = job(deadline_at=now() + timedelta(seconds=1))
    runtime = BlockingRuntime()
    token_issuer, _ = issuer()
    runner = AgentRunner(token_issuer)

    with pytest.raises(AgentRunRejected, match="runtime deadline"):
        runner.execute(job=current_job, runtime=runtime, transport=RecordingTransport())

    runtime.release.set()
    assert runtime.late_tool_rejected.wait(timeout=1)


def test_cross_bound_order_path_is_rejected_before_transport() -> None:
    current_job = job(order_request_id=uuid4(), row_version=1)
    bridge, transport, bridge_token, _ = session(current_job)

    with pytest.raises(AgentToolBridgeRejected, match="cross-bound path"):
        bridge.invoke(
            AgentToolOperation.QUOTE_ESTIMATE,
            {
                "lines": [
                    {
                        "service_code": "STANDARD_WASH_DRY",
                        "quantity_basis": "CUSTOMER_ESTIMATE",
                        "quantity": "3.0",
                        "unit": "KG",
                    }
                ],
                "fulfillment": {"mode": "SELF_DROP_SELF_COLLECT"},
            },
            bridge_token=bridge_token,
            binding_id=current_job.binding_id,
            route_path_parameters={"order_request_id": str(uuid4())},
            idempotency_key="tool-call-identity-0001",
        )

    assert transport.requests == []


def test_idempotent_replay_does_not_consume_or_repeat_tool_call() -> None:
    current_job = job(order_request_id=uuid4(), row_version=1)
    assert current_job.order_request_id is not None
    token_issuer, _ = issuer()
    transport = RecordingTransport([record_facts_response(current_job.order_request_id)])
    bridge_token = "a" * 32
    bridge = AgentToolBridgeSession(
        job=current_job,
        bridge_token=bridge_token,
        issuer=token_issuer,
        transport=transport,
    )
    arguments = {
        "facts": [
            {
                "fact_type": "SERVICE_TEXT",
                "service_text": "giặt chăn",
                "source_provider_message_id": "source-message-001",
            }
        ]
    }
    path = {"order_request_id": str(current_job.order_request_id)}
    key = "tool-call-identity-0001"
    bridge.invoke(
        AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS,
        arguments,
        bridge_token=bridge_token,
        binding_id=current_job.binding_id,
        route_path_parameters=path,
        idempotency_key=key,
    )
    bridge.invoke(
        AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS,
        arguments,
        bridge_token=bridge_token,
        binding_id=current_job.binding_id,
        route_path_parameters=path,
        idempotency_key=key,
    )

    assert len(transport.requests) == 1
    assert bridge.tool_call_count == 1
    assert transport.requests[0].headers["If-Match"] == '"1"'


def test_global_six_tool_ceiling_stops_seventh_call_before_transport() -> None:
    current_job = job(order_request_id=uuid4(), row_version=1)
    token_issuer, _ = issuer()
    transport = RecordingTransport([tool_unavailable_response() for _ in range(6)])
    bridge_token = "d" * 32
    bridge = AgentToolBridgeSession(
        job=current_job,
        bridge_token=bridge_token,
        issuer=token_issuer,
        transport=transport,
    )

    for _ in range(3):
        invoke_catalog(bridge, current_job, bridge_token)
    for _ in range(2):
        bridge.invoke(
            AgentToolOperation.DELIVERY_EVALUATE,
            {"fulfillment_mode": "SELF_DROP_SELF_COLLECT"},
            bridge_token=bridge_token,
            binding_id=current_job.binding_id,
            route_path_parameters={"order_request_id": str(current_job.order_request_id)},
        )
    bridge.invoke(
        AgentToolOperation.CAPACITY_CHECK,
        {},
        bridge_token=bridge_token,
        binding_id=current_job.binding_id,
        route_path_parameters={"order_request_id": str(current_job.order_request_id)},
    )
    with pytest.raises(AgentToolBridgeRejected, match="tool-call budget"):
        invoke_catalog(bridge, current_job, bridge_token)

    assert len(transport.requests) == 6
    assert bridge.tool_call_count == 6


def test_runner_creates_shadow_draft_only_and_never_send_authorization() -> None:
    current_job = job()
    token_issuer, _ = issuer()
    runner = AgentRunner(token_issuer)
    transport = RecordingTransport()

    result = runner.execute(
        job=current_job,
        runtime=SyntheticScriptedRuntime(
            draft_text="Em đã ghi nhận yêu cầu và sẽ nhờ nhân viên xác nhận.",
            tool_calls=(
                ScriptedToolCall(
                    operation=AgentToolOperation.CATALOG_RESOLVE,
                    arguments={"query": "giặt chăn", "locale": "vi-VN"},
                ),
            ),
        ),
        transport=transport,
        bridge_token="b" * 32,
    )

    assert result.status == "DRAFT_REQUIRES_HUMAN"
    assert result.automatic_send_authorized is False
    assert result.tool_call_count == 1


def test_real_customer_and_provider_paths_are_fail_closed() -> None:
    token_issuer, _ = issuer()
    runner = AgentRunner(token_issuer)
    transport = RecordingTransport()

    with pytest.raises(AgentRunRejected, match="real-customer processing is disabled"):
        runner.execute(
            job=job(data_classification=AgentDataClassification.REAL_CUSTOMER),
            runtime=SyntheticScriptedRuntime(draft_text="Bản nháp an toàn."),
            transport=transport,
        )
    with pytest.raises(AgentRunRejected, match="provider runtime release gates are incomplete"):
        runner.execute(job=job(), runtime=DisabledOpenClawProviderRuntime(), transport=transport)


def test_create_binds_new_order_request_for_subsequent_fixed_paths() -> None:
    current_job = job(capability=ReleaseCapability.INTAKE_FACT_CAPTURE)
    new_request = uuid4()
    token_issuer, _ = issuer()
    transport = RecordingTransport([create_response(new_request), catalog_response()])
    bridge = AgentToolBridgeSession(
        job=current_job,
        bridge_token="c" * 32,
        issuer=token_issuer,
        transport=transport,
    )
    bridge.invoke(
        AgentToolOperation.ORDER_REQUEST_CREATE,
        {
            "customer_intent": "Tôi muốn giặt chăn",
            "locale": "vi-VN",
            "source_provider_message_ids": ["source-message-001"],
        },
        bridge_token="c" * 32,
        binding_id=current_job.binding_id,
        route_path_parameters={},
        idempotency_key="tool-call-identity-0001",
    )

    assert bridge.expected_path_parameters(AgentToolOperation.QUOTE_ESTIMATE) == {
        "order_request_id": str(new_request)
    }


def test_loopback_bridge_exposes_only_fixed_routes_and_forwards_etag() -> None:
    current_job = job()
    token_issuer, _ = issuer()
    transport = RecordingTransport([catalog_response()])
    bridge_token = "e" * 32
    session = AgentToolBridgeSession(
        job=current_job,
        bridge_token=bridge_token,
        issuer=token_issuer,
        transport=transport,
    )
    store = AgentBridgeSessionStore()
    store.install(session)
    client = TestClient(create_agent_bridge_app(store))
    headers = {
        "Authorization": f"Bearer {bridge_token}",
        "X-Agent-Run-Binding": str(current_job.binding_id),
    }

    response = client.post(
        "/agent/v1/catalog:resolve",
        headers=headers,
        json={"query": "giặt chăn", "locale": "vi-VN"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert client.post("/agent/v1/tools:invoke", headers=headers, json={}).status_code == 404
    assert client.post("/agent/v1/messages:send", headers=headers, json={}).status_code == 404


def test_loopback_bridge_rejects_cross_bound_path_before_transport() -> None:
    current_job = job(order_request_id=uuid4(), row_version=1)
    token_issuer, _ = issuer()
    transport = RecordingTransport()
    bridge_token = "f" * 32
    store = AgentBridgeSessionStore()
    store.install(
        AgentToolBridgeSession(
            job=current_job,
            bridge_token=bridge_token,
            issuer=token_issuer,
            transport=transport,
        )
    )
    client = TestClient(create_agent_bridge_app(store))

    response = client.post(
        f"/agent/v1/order-requests/{uuid4()}/quotes:estimate",
        headers={
            "Authorization": f"Bearer {bridge_token}",
            "X-Agent-Run-Binding": str(current_job.binding_id),
            "Idempotency-Key": "tool-call-identity-0001",
            "If-Match": '"1"',
        },
        json={
            "lines": [
                {
                    "service_code": "STANDARD_WASH_DRY",
                    "quantity_basis": "CUSTOMER_ESTIMATE",
                    "quantity": "3.0",
                    "unit": "KG",
                }
            ],
            "fulfillment": {"mode": "SELF_DROP_SELF_COLLECT"},
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "POLICY_DENIED"
    assert transport.requests == []


def test_bridge_rejects_malformed_facade_output_as_unavailable() -> None:
    current_job = job()
    token_issuer, _ = issuer()
    transport = RecordingTransport(
        [AgentToolForwardResponse(status_code=200, headers={}, body={"ok": True})]
    )
    bridge = AgentToolBridgeSession(
        job=current_job,
        bridge_token="g" * 32,
        issuer=token_issuer,
        transport=transport,
    )

    with pytest.raises(AgentToolBridgeRejected, match="invalid facade response"):
        invoke_catalog(bridge, current_job, "g" * 32)
