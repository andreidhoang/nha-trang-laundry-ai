from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from nha_trang_laundry_agent_tools.auth import (
    AgentAuthSettings,
    AgentRunnerTokenVerifier,
)
from nha_trang_laundry_agent_tools.facade import (
    AgentFacadeService,
    AgentToolCall,
    get_agent_facade_service,
    get_agent_verifier,
)
from nha_trang_laundry_agent_tools.main import app


@pytest.fixture(autouse=True)
def reset_dependency_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


class CatalogMockBackend:
    def __init__(self) -> None:
        self.calls: list[AgentToolCall] = []

    def invoke(self, call: AgentToolCall) -> dict[str, object]:
        self.calls.append(call)
        return {
            "ok": True,
            "trace_id": call.trace_id,
            "decision": {
                "outcome": "REQUIRE_HUMAN",
                "reason_codes": ["SHADOW_MODE_ALL_SENDS"],
                "obligations": [],
                "policy_version": "policy-eval-only-v1",
                "snapshot_hash": f"sha256:{'a' * 64}",
            },
            "data": {"candidates": []},
        }


class InvalidBackend:
    def invoke(self, call: AgentToolCall) -> dict[str, object]:
        del call
        return {"ok": True}


def key_pair() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public_pem.decode("ascii")


def runner_token(
    private_key: Ed25519PrivateKey,
    *,
    capabilities: list[str] | None = None,
    order_request_id: str | None = None,
    data_classification: str = "SYNTHETIC",
) -> str:
    now = int(datetime.now(UTC).timestamp())
    claims: dict[str, object] = {
        "iss": "https://control-plane.test",
        "aud": "agent-tool-facade",
        "sub": "AGENT_RUNNER",
        "iat": now,
        "exp": now + 30,
        "jti": str(uuid4()),
        "run_id": str(uuid4()),
        "organization_id": str(uuid4()),
        "store_id": str(uuid4()),
        "channel": "INTERNAL_TEST",
        "conversation_binding_id": str(uuid4()),
        "contact_binding_id": str(uuid4()),
        "capabilities": capabilities or ["INTERNAL_SHADOW"],
        "stage": "SHADOW",
        "data_classification": data_classification,
    }
    if order_request_id is not None:
        claims["order_request_id"] = order_request_id
    return jwt.encode(claims, private_key, algorithm="EdDSA")


def configured_client(
    private_key: Ed25519PrivateKey, public_key: str, backend: CatalogMockBackend
) -> TestClient:
    verifier = AgentRunnerTokenVerifier(
        AgentAuthSettings(
            agent_runner_jwt_issuer="https://control-plane.test",
            agent_runner_jwt_audience="agent-tool-facade",
            agent_runner_jwt_public_key=public_key,
        )
    )
    app.dependency_overrides[get_agent_verifier] = lambda: verifier
    app.dependency_overrides[get_agent_facade_service] = lambda: AgentFacadeService(backend)
    return TestClient(app)


def test_mock_facade_path_accepts_only_registered_strict_body() -> None:
    private, public = key_pair()
    backend = CatalogMockBackend()
    client = configured_client(private, public, backend)
    token = runner_token(private)

    response = client.post(
        "/agent/v1/catalog:resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "giặt chăn", "locale": "vi-VN"},
    )

    assert response.status_code == 200
    assert response.json()["decision"]["outcome"] == "REQUIRE_HUMAN"
    assert len(backend.calls) == 1

    injected = client.post(
        "/agent/v1/catalog:resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "giặt chăn", "locale": "vi-VN", "customer_id": str(uuid4())},
    )
    assert injected.status_code == 422
    assert injected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(backend.calls) == 1


def test_no_generic_invoke_or_direct_send_route_exists() -> None:
    client = TestClient(app)

    assert client.post("/agent/v1/tools:invoke", json={}).status_code == 404
    assert client.post("/agent/v1/messages:send", json={}).status_code == 404


def test_cross_request_path_is_denied_before_backend() -> None:
    private, public = key_pair()
    backend = CatalogMockBackend()
    client = configured_client(private, public, backend)
    bound = uuid4()
    other = uuid4()
    token = runner_token(private, order_request_id=str(bound))

    response = client.post(
        f"/agent/v1/order-requests/{other}/quotes:estimate",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "agent-test-key-0001",
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
    assert backend.calls == []


def test_capability_cannot_expand_tool_access() -> None:
    private, public = key_pair()
    backend = CatalogMockBackend()
    client = configured_client(private, public, backend)
    request_id = uuid4()
    token = runner_token(private, capabilities=["PUBLIC_FAQ"], order_request_id=str(request_id))

    response = client.post(
        f"/agent/v1/order-requests/{request_id}/delivery:evaluate",
        headers={"Authorization": f"Bearer {token}"},
        json={"fulfillment_mode": "SELF_DROP_SELF_COLLECT"},
    )

    assert response.status_code == 403
    assert backend.calls == []


def test_real_customer_token_is_denied_by_runtime_registry() -> None:
    private, public = key_pair()
    verifier = AgentRunnerTokenVerifier(
        AgentAuthSettings(
            agent_runner_jwt_issuer="https://control-plane.test",
            agent_runner_jwt_audience="agent-tool-facade",
            agent_runner_jwt_public_key=public,
        )
    )
    token = runner_token(private, data_classification="REAL_CUSTOMER")

    try:
        verifier.verify(f"Bearer {token}")
    except ValueError as error:
        assert "real-customer agent processing is disabled" in str(error)
    else:
        raise AssertionError("Real-customer agent token bypassed the release registry")


def test_invalid_backend_output_is_safe_tool_unavailable() -> None:
    private, public = key_pair()
    verifier = AgentRunnerTokenVerifier(
        AgentAuthSettings(
            agent_runner_jwt_issuer="https://control-plane.test",
            agent_runner_jwt_audience="agent-tool-facade",
            agent_runner_jwt_public_key=public,
        )
    )
    app.dependency_overrides[get_agent_verifier] = lambda: verifier
    app.dependency_overrides[get_agent_facade_service] = lambda: AgentFacadeService(
        InvalidBackend()
    )
    client = TestClient(app)

    response = client.post(
        "/agent/v1/catalog:resolve",
        headers={"Authorization": f"Bearer {runner_token(private)}"},
        json={"query": "giặt chăn", "locale": "vi-VN"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TOOL_UNAVAILABLE"
