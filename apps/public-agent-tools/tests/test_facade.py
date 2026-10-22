from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from nha_trang_laundry_agent_tools.auth import (
    AgentAuthSettings,
    AgentRunnerTokenVerifier,
)
from nha_trang_laundry_agent_tools.backend import build_domain_facade_service
from nha_trang_laundry_agent_tools.facade import (
    AgentFacadeService,
    AgentToolCall,
    AgentToolUnavailable,
    PostgresAgentCallLedger,
    get_agent_facade_service,
    get_agent_verifier,
)
from nha_trang_laundry_agent_tools.main import app
from nha_trang_laundry_contracts import AgentToolOperation
from nha_trang_laundry_db.connection import DatabaseUnavailableError


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
    public_code: str | None = None,
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
    if public_code is not None:
        claims["public_code"] = public_code
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

    correlation_id = "00000000-0000-0000-0000-000000000902"
    response = client.post(
        "/agent/v1/catalog:resolve",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Correlation-ID": correlation_id,
        },
        json={"query": "giặt chăn", "locale": "vi-VN"},
    )

    assert response.status_code == 200
    assert response.json()["decision"]["outcome"] == "REQUIRE_HUMAN"
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert response.json()["trace_id"] == "tr_00000000000000000000000000000902"
    assert len(backend.calls) == 1

    injected = client.post(
        "/agent/v1/catalog:resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": "giặt chăn",
            "locale": "vi-VN",
            "customer_id": str(uuid4()),
            "policy_outcome": "ALLOW",
        },
    )
    assert injected.status_code == 422
    assert injected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert len(backend.calls) == 1


def test_no_generic_invoke_or_direct_send_route_exists() -> None:
    client = TestClient(app)

    assert client.post("/agent/v1/tools:invoke", json={}).status_code == 404
    assert client.post("/agent/v1/messages:send", json={}).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"",
        b'\x7b"query":"\xff"\x7d',
        pytest.param(b'{"query":' + b"9" * 4500 + b"}", id="integer-digit-limit"),
        pytest.param(b'{"query":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}", id="nesting-limit"),
    ],
)
def test_malformed_json_is_rejected_without_spending_bearer(body: bytes) -> None:
    private, public = key_pair()
    backend = CatalogMockBackend()
    configured_client(private, public, backend)
    client = TestClient(app, raise_server_exceptions=False)
    headers = {
        "Authorization": f"Bearer {runner_token(private)}",
        "Content-Type": "application/json",
    }

    rejected = client.post("/agent/v1/catalog:resolve", headers=headers, content=body)

    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"
    assert backend.calls == []
    accepted = client.post(
        "/agent/v1/catalog:resolve",
        headers=headers,
        json={"query": "giặt chăn", "locale": "vi-VN"},
    )
    assert accepted.status_code == 200
    assert len(backend.calls) == 1


@pytest.mark.parametrize("authorization", [None, "Bearer invalid"])
def test_authentication_precedes_malformed_json(authorization: str | None) -> None:
    private, public = key_pair()
    backend = CatalogMockBackend()
    client = configured_client(private, public, backend)
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.post("/agent/v1/catalog:resolve", headers=headers, content=b"{")

    assert response.status_code == 401
    assert backend.calls == []


@pytest.mark.parametrize(
    ("path_code", "claim_code"),
    [("é" * 20, "a" * 20), ("a" * 20, "é" * 20), ("é" * 20, "é" * 20)],
)
def test_non_ascii_public_locator_is_denied_before_backend(path_code: str, claim_code: str) -> None:
    private, public = key_pair()
    backend = CatalogMockBackend()
    configured_client(private, public, backend)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        f"/agent/v1/orders/{path_code}/status",
        headers={"Authorization": f"Bearer {runner_token(private, public_code=claim_code)}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "POLICY_DENIED"
    assert backend.calls == []


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


@pytest.mark.parametrize(
    "header",
    [b'"\xb2"', b'"' + b"1" * 20 + b'"', b'"' + b"1" * 4500 + b'"'],
    ids=["non_ascii_digit", "twenty_digit_version", "integer_conversion_limit"],
)
def test_invalid_if_match_is_rejected_before_backend(header: bytes) -> None:
    private, public = key_pair()
    backend = CatalogMockBackend()
    configured_client(private, public, backend)
    client = TestClient(app, raise_server_exceptions=False)
    request_id = uuid4()
    token = runner_token(private, order_request_id=str(request_id))

    response = client.post(
        f"/agent/v1/order-requests/{request_id}/quotes:estimate",
        headers=[
            (b"authorization", f"Bearer {token}".encode("ascii")),
            (b"idempotency-key", b"agent-test-key-0001"),
            # Superscript digits and oversized decimal strings must not reach int conversion.
            (b"if-match", header),
        ],
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

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
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


@pytest.mark.parametrize("entry_point", ["ledger", "service_admission", "service_backend"])
@pytest.mark.parametrize("injected", [False, True])
def test_facade_database_connections_are_bounded_and_keep_injection(
    monkeypatch: pytest.MonkeyPatch, entry_point: str, injected: bool
) -> None:
    for name in (
        "DATABASE_CONNECT_TIMEOUT_SECONDS",
        "DATABASE_STATEMENT_TIMEOUT_MS",
        "DATABASE_LOCK_TIMEOUT_MS",
        "DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)
    captured: list[tuple[str, dict[str, Any]]] = []

    def unavailable(conninfo: str, **kwargs: Any) -> Any:
        captured.append((conninfo, kwargs))
        raise psycopg.OperationalError("synthetic connection refusal")

    monkeypatch.setattr(psycopg, "connect", unavailable)
    private, public = key_pair()
    verifier = AgentRunnerTokenVerifier(
        AgentAuthSettings(
            agent_runner_jwt_issuer="https://control-plane.test",
            agent_runner_jwt_audience="agent-tool-facade",
            agent_runner_jwt_public_key=public,
        )
    )
    claims = verifier.verify(f"Bearer {runner_token(private)}")
    # A syntactically valid DSN with an invalid port also prevents a real connection if a
    # regression bypasses the patched driver function through an old, captured default.
    database_url = "dbname=synthetic port=invalid"
    service = (
        build_domain_facade_service(database_url=database_url, connection_factory=unavailable)
        if injected
        else build_domain_facade_service(database_url=database_url)
    )
    call = AgentToolCall(
        AgentToolOperation.CATALOG_RESOLVE,
        {"query": "giặt chăn", "locale": "vi-VN"},
        {},
        None,
        None,
        claims,
        "tr_synthetic",
    )

    expected_error = (
        AgentToolUnavailable
        if entry_point != "service_backend"
        else psycopg.OperationalError
        if injected
        else DatabaseUnavailableError
    )
    with pytest.raises(expected_error):
        if entry_point == "ledger":
            ledger = (
                PostgresAgentCallLedger(database_url, connection_factory=unavailable)
                if injected
                else PostgresAgentCallLedger(database_url)
            )
            ledger.admit(
                claims=claims,
                operation=call.operation,
                max_calls_per_run=1,
                now=datetime.now(UTC),
            )
        elif entry_point == "service_admission":
            service.invoke(
                operation=call.operation,
                arguments=call.arguments,
                path_parameters=call.path_parameters,
                idempotency_key=None,
                if_match=None,
                claims=claims,
                trace_id=call.trace_id,
            )
        else:
            # Exercise the assembled backend too: the factory must not overwrite its bounds.
            service._backend.invoke(call)

    expected = (
        {}
        if injected
        else {
            "connect_timeout": 5,
            "options": (
                "-c statement_timeout=15000 -c lock_timeout=5000 "
                "-c idle_in_transaction_session_timeout=60000"
            ),
        }
    )
    assert captured == [(database_url, expected)]
