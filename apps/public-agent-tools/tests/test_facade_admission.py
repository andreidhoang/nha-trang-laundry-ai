"""AGENT-SHADOW-DEFECTS-001 F7: the Tool Facade admits each runner bearer once, within limits.

The facade verified a runner bearer's signature and time window and remembered nothing: a captured
bearer could be replayed for its whole lifetime (up to 60 s), and `x-agent-tool.max_calls_per_run`
was enforced only by the runner's in-process bridge -- which the facade exists precisely not to
trust. Both are now enforced server-side, before the backend is reached.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from nha_trang_laundry_agent_tools.auth import AgentAuthSettings, AgentRunnerTokenVerifier
from nha_trang_laundry_agent_tools.facade import (
    AgentFacadeService,
    AgentToolCall,
    InMemoryAgentCallLedger,
    PostgresAgentCallLedger,
    get_agent_facade_service,
    get_agent_verifier,
)
from nha_trang_laundry_agent_tools.main import app
from nha_trang_laundry_contracts import AgentDeploymentStage, ReleaseCapability
from nha_trang_laundry_db.agent_runs import AgentRunEnqueueCommand, AgentRunRepository
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import ActorRole

CATALOG = "/agent/v1/catalog:resolve"
BODY = {"query": "giặt chăn", "locale": "vi-VN"}
SHA = "sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def reset_dependency_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


class _Backend:
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
                "snapshot_hash": SHA,
            },
            "data": {"candidates": []},
        }


def _keys() -> tuple[Ed25519PrivateKey, str]:
    private = Ed25519PrivateKey.generate()
    public = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private, public


def _token(private: Ed25519PrivateKey, *, run_id: UUID) -> str:
    now = int(datetime.now(UTC).timestamp())
    return jwt.encode(
        {
            "iss": "https://control-plane.test",
            "aud": "agent-tool-facade",
            "sub": "AGENT_RUNNER",
            "iat": now,
            "exp": now + 30,
            "jti": str(uuid4()),
            "run_id": str(run_id),
            "organization_id": str(uuid4()),
            "store_id": str(uuid4()),
            "channel": "INTERNAL_TEST",
            "conversation_binding_id": str(uuid4()),
            "contact_binding_id": str(uuid4()),
            "capabilities": ["INTERNAL_SHADOW"],
            "stage": "SHADOW",
            "data_classification": "SYNTHETIC",
        },
        private,
        algorithm="EdDSA",
    )


def _client(public: str, service: AgentFacadeService) -> TestClient:
    verifier = AgentRunnerTokenVerifier(
        AgentAuthSettings(
            agent_runner_jwt_issuer="https://control-plane.test",
            agent_runner_jwt_audience="agent-tool-facade",
            agent_runner_jwt_public_key=public,
        )
    )
    app.dependency_overrides[get_agent_verifier] = lambda: verifier
    # One service for the whole client, as a deployment holds one; the ledger outlives requests.
    app.dependency_overrides[get_agent_facade_service] = lambda: service
    return TestClient(app)


def _call(client: TestClient, token: str) -> Any:
    return client.post(CATALOG, headers={"Authorization": f"Bearer {token}"}, json=BODY)


def test_a_bearer_is_admitted_once_and_a_replay_never_reaches_the_backend() -> None:
    private, public = _keys()
    backend = _Backend()
    client = _client(public, AgentFacadeService(backend, call_ledger=InMemoryAgentCallLedger()))
    token = _token(private, run_id=uuid4())

    first = _call(client, token)
    replay = _call(client, token)

    assert first.status_code == 200
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "POLICY_DENIED"
    assert len(backend.calls) == 1


def test_a_replay_is_refused_even_by_a_fresh_service_instance_in_the_same_process() -> None:
    """The production dependency builds a service per request; the default ledger is per process."""
    private, public = _keys()
    backend = _Backend()
    verifier = AgentRunnerTokenVerifier(
        AgentAuthSettings(
            agent_runner_jwt_issuer="https://control-plane.test",
            agent_runner_jwt_audience="agent-tool-facade",
            agent_runner_jwt_public_key=public,
        )
    )
    app.dependency_overrides[get_agent_verifier] = lambda: verifier
    app.dependency_overrides[get_agent_facade_service] = lambda: AgentFacadeService(backend)
    client = TestClient(app)
    token = _token(private, run_id=uuid4())

    assert _call(client, token).status_code == 200
    assert _call(client, token).status_code == 401
    assert len(backend.calls) == 1


def test_an_operation_is_refused_past_its_registered_per_run_limit() -> None:
    private, public = _keys()
    backend = _Backend()
    client = _client(public, AgentFacadeService(backend, call_ledger=InMemoryAgentCallLedger()))
    run_id = uuid4()

    statuses = [_call(client, _token(private, run_id=run_id)).status_code for _ in range(4)]

    # catalogResolve declares `max_calls_per_run: 3`; every bearer here is fresh and valid.
    assert statuses == [200, 200, 200, 429]
    assert len(backend.calls) == 3
    # The limit is per run: another run is unaffected.
    assert _call(client, _token(private, run_id=uuid4())).status_code == 200


def test_the_rate_limit_answer_is_the_contract_error_envelope() -> None:
    private, public = _keys()
    client = _client(public, AgentFacadeService(_Backend(), call_ledger=InMemoryAgentCallLedger()))
    run_id = uuid4()
    for _ in range(3):
        _call(client, _token(private, run_id=run_id))

    refused = _call(client, _token(private, run_id=run_id))

    assert refused.status_code == 429
    body = refused.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "RATE_LIMITED"


def test_an_invalid_request_consumes_no_admission() -> None:
    """Validation precedes admission, so a malformed body cannot burn a run's budget."""
    private, public = _keys()
    backend = _Backend()
    client = _client(public, AgentFacadeService(backend, call_ledger=InMemoryAgentCallLedger()))
    token = _token(private, run_id=uuid4())

    rejected = client.post(
        CATALOG,
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "x", "locale": "vi-VN", "customer_id": str(uuid4())},
    )

    assert rejected.status_code == 422
    assert _call(client, token).status_code == 200
    assert len(backend.calls) == 1


# --- PostgreSQL: the ledger a multi-process facade shares ---------------------------------------


@pytest.fixture
def database_url() -> Generator[str, None, None]:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(url) as connection:
        apply_migrations(connection)
    yield url


def _live_run(database_url: str) -> UUID:
    """A queued run claimed by the runner: the only state in which its bearers may be admitted."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        store_id = uuid4()
        StoreRepository.create(
            connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
        )
        command = AgentRunEnqueueCommand(
            agent_run_id=uuid4(),
            source_webhook_event_id=None,
            organization_id=uuid4(),
            store_id=store_id,
            channel="INTERNAL_TEST",
            conversation_binding_id=uuid4(),
            contact_binding_id=uuid4(),
            capability=ReleaseCapability.INTERNAL_SHADOW,
            deployment_stage=AgentDeploymentStage.SHADOW,
            runtime_registry_version="1.0.0-eval",
            runtime_registry_hash=SHA,
            prompt_bundle_version="1.0.0-eval",
            prompt_bundle_hash=SHA,
            tool_contract_hash=SHA,
            correlation_id=uuid4(),
            created_at=datetime(1970, 1, 1, tzinfo=UTC),
        )
        AgentRunRepository().enqueue(connection, command)
        claimed = AgentRunRepository().claim_next(
            connection, worker_role=ActorRole.AGENT_RUNNER, now=datetime.now(UTC)
        )
        assert claimed is not None and claimed.agent_run_id == command.agent_run_id
        return command.agent_run_id


def test_the_shared_ledger_refuses_a_replay_across_service_instances(database_url: str) -> None:
    private, public = _keys()
    backend = _Backend()
    run_id = _live_run(database_url)
    token = _token(private, run_id=run_id)

    def fresh_service() -> AgentFacadeService:
        return AgentFacadeService(backend, call_ledger=PostgresAgentCallLedger(database_url))

    first = _call(_client(public, fresh_service()), token)
    replay = _call(_client(public, fresh_service()), token)

    assert (first.status_code, replay.status_code) == (200, 401)
    assert len(backend.calls) == 1
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            SELECT count(*), min(operation_id) FROM agent_facade_invocations
            WHERE agent_run_id = %s
            """,
            (run_id,),
        ).fetchone()
    assert row == (1, "catalogResolve")


def test_the_shared_ledger_enforces_the_per_run_limit(database_url: str) -> None:
    private, public = _keys()
    backend = _Backend()
    run_id = _live_run(database_url)
    client = _client(
        public, AgentFacadeService(backend, call_ledger=PostgresAgentCallLedger(database_url))
    )

    statuses = [_call(client, _token(private, run_id=run_id)).status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 429]
    assert len(backend.calls) == 3


def test_a_bearer_for_a_run_that_is_no_longer_live_is_refused(database_url: str) -> None:
    """A late call after the run ended -- the facade-side half of bridge revocation."""
    private, public = _keys()
    backend = _Backend()
    run_id = _live_run(database_url)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            UPDATE agent_runs SET status = 'FAILED', claim_token = NULL, claimed_at = NULL,
                lease_expires_at = NULL, failure_code = 'MODEL_TIMEOUT', completed_at = now()
            WHERE id = %s
            """,
            (run_id,),
        )
    client = _client(
        public, AgentFacadeService(backend, call_ledger=PostgresAgentCallLedger(database_url))
    )

    refused = _call(client, _token(private, run_id=run_id))
    unknown = _call(client, _token(private, run_id=uuid4()))

    assert (refused.status_code, unknown.status_code) == (403, 403)
    assert refused.json()["error"]["code"] == "POLICY_DENIED"
    assert backend.calls == []


def test_an_unreachable_ledger_fails_closed() -> None:
    private, public = _keys()
    backend = _Backend()

    def unreachable(_: str) -> Any:
        raise psycopg.OperationalError("connection refused")

    ledger = PostgresAgentCallLedger("postgresql://unused", connection_factory=unreachable)
    client = _client(public, AgentFacadeService(backend, call_ledger=ledger))

    response = _call(client, _token(private, run_id=uuid4()))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TOOL_UNAVAILABLE"
    assert backend.calls == []


def test_expired_bearers_do_not_accumulate_in_the_process_ledger() -> None:
    from nha_trang_laundry_contracts import AgentRunnerClaims, AgentToolOperation

    ledger = InMemoryAgentCallLedger()
    past = int((datetime.now(UTC) - timedelta(minutes=10)).timestamp())

    def claims(issued: int) -> AgentRunnerClaims:
        return AgentRunnerClaims.model_validate(
            {
                "iss": "i",
                "aud": "a",
                "sub": "AGENT_RUNNER",
                "iat": issued,
                "exp": issued + 30,
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
            }
        )

    for _ in range(50):
        ledger.admit(
            claims=claims(past),
            operation=AgentToolOperation.CATALOG_RESOLVE,
            max_calls_per_run=3,
            now=datetime.fromtimestamp(past, UTC),
        )
    assert ledger.admitted_count() == 50

    now = datetime.now(UTC)
    ledger.admit(
        claims=claims(int(now.timestamp())),
        operation=AgentToolOperation.CATALOG_RESOLVE,
        max_calls_per_run=3,
        now=now,
    )

    assert ledger.admitted_count() == 1
