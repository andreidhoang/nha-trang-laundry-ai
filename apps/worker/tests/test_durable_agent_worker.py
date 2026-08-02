from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    AgentToolOperation,
    ReleaseCapability,
)
from nha_trang_laundry_db.agent_runs import AgentRunEnqueueCommand, AgentRunRepository
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_worker.agent_runner import (
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentRuntimeTimeout,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
    DisabledOpenClawProviderRuntime,
    ScriptedToolCall,
    SyntheticScriptedRuntime,
)
from nha_trang_laundry_worker.durable_agent_worker import DurableAgentRunWorker

NOW = datetime.now(UTC)


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


class CatalogTransport:
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
                    "reason_codes": ["SHADOW_MODE_ALL_SENDS"],
                    "obligations": [],
                    "policy_version": "policy-eval-only-v1",
                    "snapshot_hash": f"sha256:{'a' * 64}",
                },
                "data": {"candidates": []},
            },
        )


def runner() -> AgentRunner:
    private = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    return AgentRunner(
        AgentRunnerTokenIssuer(
            issuer="https://control-plane.test",
            audience="agent-tool-facade",
            private_key=private,
        )
    )


def enqueue_command(*, created_at: datetime | None = None) -> AgentRunEnqueueCommand:
    return AgentRunEnqueueCommand(
        agent_run_id=uuid4(),
        source_webhook_event_id=None,
        organization_id=uuid4(),
        store_id=uuid4(),
        channel="INTERNAL_TEST",
        conversation_binding_id=uuid4(),
        contact_binding_id=uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        deployment_stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        runtime_registry_version="1.0.0-eval",
        runtime_registry_hash=f"sha256:{'a' * 64}",
        prompt_bundle_version="1.0.0-eval",
        prompt_bundle_hash=f"sha256:{'b' * 64}",
        tool_contract_hash=f"sha256:{'c' * 64}",
        correlation_id=uuid4(),
        created_at=created_at or NOW,
    )


def test_durable_worker_claims_runs_persists_safe_tool_ledger_and_requires_human(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = AgentRunRepository()
    command = enqueue_command(created_at=_before_pending_agent_run(postgres_connection))
    repository.enqueue(postgres_connection, command)
    transport = CatalogTransport()
    worker = DurableAgentRunWorker(runner(), repository)
    draft_text = "Đã ghi nhận, nhân viên sẽ xác nhận."

    result = worker.run_once(
        postgres_connection,
        runtime=SyntheticScriptedRuntime(
            draft_text=draft_text,
            tool_calls=(
                ScriptedToolCall(
                    operation=AgentToolOperation.CATALOG_RESOLVE,
                    arguments={"query": "giặt chăn", "locale": "vi-VN"},
                ),
            ),
        ),
        transport=transport,
        correlation_id=command.correlation_id,
        now=NOW,
    )

    assert result.agent_run_id == str(command.agent_run_id)
    assert result.status == "DRAFT_REQUIRES_HUMAN"
    assert len(transport.requests) == 1
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, automatic_send_authorized, result_safe_summary
            FROM agent_runs WHERE id = %s
            """,
            (command.agent_run_id,),
        )
        run_row = cursor.fetchone()
        cursor.execute(
            "SELECT operation_id, request_fingerprint, safe_summary FROM agent_tool_calls "
            "WHERE agent_run_id = %s",
            (command.agent_run_id,),
        )
        tool_row = cursor.fetchone()

    assert run_row == (
        "DRAFT_REQUIRES_HUMAN",
        False,
        {
            "disposition": "REQUIRE_HUMAN",
            "draft_character_count": len(draft_text),
            "tool_call_count": 1,
        },
    )
    assert tool_row is not None
    assert tool_row[0] == "catalogResolve"
    assert str(tool_row[1]).startswith("sha256:")
    assert tool_row[2]["result_code"] == "REQUIRE_HUMAN"
    assert "query" not in tool_row[2]


def test_durable_worker_records_fail_closed_provider_rejection(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = AgentRunRepository()
    command = enqueue_command(created_at=_before_pending_agent_run(postgres_connection))
    repository.enqueue(postgres_connection, command)
    worker = DurableAgentRunWorker(runner(), repository)

    result = worker.run_once(
        postgres_connection,
        runtime=DisabledOpenClawProviderRuntime(),
        transport=CatalogTransport(),
        correlation_id=command.correlation_id,
        now=NOW,
    )

    assert result.status == "FAILED"
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, automatic_send_authorized, failure_code FROM agent_runs WHERE id = %s",
            (command.agent_run_id,),
        )
        row = cursor.fetchone()
    assert row == ("FAILED", False, "POLICY_DENIED")


def test_durable_worker_preserves_timeout_run_for_human_recovery(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    class TimedOutRuntime:
        provider_backed = False

        def invoke(self, invocation: Any, bridge: Any) -> Any:
            del invocation, bridge
            raise AgentRuntimeTimeout("MODEL_TIMEOUT: synthetic hard deadline reached")

    repository = AgentRunRepository()
    command = enqueue_command(created_at=_before_pending_agent_run(postgres_connection))
    repository.enqueue(postgres_connection, command)
    worker = DurableAgentRunWorker(runner(), repository)

    result = worker.run_once(
        postgres_connection,
        runtime=TimedOutRuntime(),
        transport=CatalogTransport(),
        correlation_id=command.correlation_id,
        now=NOW,
    )

    assert result == type(result)(str(command.agent_run_id), "FAILED")
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, failure_code, automatic_send_authorized, completed_at
            FROM agent_runs WHERE id = %s
            """,
            (command.agent_run_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0:3] == ("FAILED", "MODEL_TIMEOUT", False)
    assert row[3] == NOW


def _before_pending_agent_run(connection: psycopg.Connection[Any]) -> datetime:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SELECT min(created_at) FROM agent_runs WHERE status = 'PENDING'")
        row = cursor.fetchone()
    earliest = None if row is None else row[0]
    if not isinstance(earliest, datetime):
        return datetime(1970, 1, 1, tzinfo=UTC)
    return earliest - timedelta(days=1)
