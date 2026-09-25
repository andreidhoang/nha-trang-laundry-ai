"""AGENT-SHADOW-DEFECTS-001 F8, at the boundary that matters: the Tool Facade refuses the words.

A customer says "tomorrow afternoon please"; a model passes it on as `requested_ready_at`. The
contract types that field `format: date-time`, and until this item nothing checked the format, so
the words reached the backend. They must stop at the facade, before any backend runs.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from nha_trang_laundry_agent_tools.auth import AgentAuthSettings, AgentRunnerTokenVerifier
from nha_trang_laundry_agent_tools.facade import (
    AgentFacadeService,
    AgentToolCall,
    InMemoryAgentCallLedger,
    get_agent_facade_service,
    get_agent_verifier,
)
from nha_trang_laundry_agent_tools.main import app


@pytest.fixture(autouse=True)
def reset_dependency_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


class _CapacityBackend:
    def __init__(self) -> None:
        self.calls: list[AgentToolCall] = []

    def invoke(self, call: AgentToolCall) -> dict[str, object]:
        self.calls.append(call)
        return {
            "ok": True,
            "trace_id": call.trace_id,
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
        }


def _client_and_token(order_request_id: UUID) -> tuple[TestClient, str, _CapacityBackend]:
    private = Ed25519PrivateKey.generate()
    public = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    backend = _CapacityBackend()
    verifier = AgentRunnerTokenVerifier(
        AgentAuthSettings(
            agent_runner_jwt_issuer="https://control-plane.test",
            agent_runner_jwt_audience="agent-tool-facade",
            agent_runner_jwt_public_key=public,
        )
    )
    service = AgentFacadeService(backend, call_ledger=InMemoryAgentCallLedger())
    app.dependency_overrides[get_agent_verifier] = lambda: verifier
    app.dependency_overrides[get_agent_facade_service] = lambda: service
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode(
        {
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
            "capabilities": ["INTERNAL_SHADOW"],
            "stage": "SHADOW",
            "data_classification": "SYNTHETIC",
            "order_request_id": str(order_request_id),
        },
        private,
        algorithm="EdDSA",
    )
    return TestClient(app), token, backend


@pytest.mark.parametrize(
    "words", ["tomorrow afternoon please", "chiều mai nhé", "2026-09-26", "2026-09-26T14:00:00"]
)
def test_words_in_a_date_time_field_are_refused_at_the_facade(words: str) -> None:
    request_id = uuid4()
    client, token, backend = _client_and_token(request_id)

    response = client.post(
        f"/agent/v1/order-requests/{request_id}/capacity:check",
        headers={"Authorization": f"Bearer {token}"},
        json={"requested_ready_at": words},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["field_errors"][0]["path"] == "requested_ready_at"
    assert backend.calls == []


def test_a_real_timestamp_reaches_the_backend() -> None:
    request_id = uuid4()
    client, token, backend = _client_and_token(request_id)

    response = client.post(
        f"/agent/v1/order-requests/{request_id}/capacity:check",
        headers={"Authorization": f"Bearer {token}"},
        json={"requested_ready_at": "2026-09-26T14:00:00+07:00"},
    )

    assert response.status_code == 200
    assert len(backend.calls) == 1
