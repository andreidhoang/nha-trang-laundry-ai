from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    AgentToolOperation,
    ReleaseCapability,
)
from nha_trang_laundry_db.agent_runs import (
    AgentRunAuthorizationError,
    AgentRunEnqueueCommand,
    AgentRunRepository,
    AgentRunStateError,
    AgentToolCallLedgerEntry,
    request_fingerprint,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.catalog import ActorRole

NOW = datetime(2026, 8, 1, 3, tzinfo=UTC)
SHA_A = f"sha256:{'a' * 64}"
SHA_B = f"sha256:{'b' * 64}"
SHA_C = f"sha256:{'c' * 64}"


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def command() -> AgentRunEnqueueCommand:
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
        runtime_registry_hash=SHA_A,
        prompt_bundle_version="1.0.0-eval",
        prompt_bundle_hash=SHA_B,
        tool_contract_hash=SHA_C,
        correlation_id=uuid4(),
        created_at=NOW,
    )


def test_agent_run_queue_tool_ledger_and_draft_completion_are_durable(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = AgentRunRepository()
    queued = command()
    repository.enqueue(postgres_connection, queued)

    with pytest.raises(AgentRunAuthorizationError, match="AGENT_RUNNER"):
        repository.claim_next(postgres_connection, worker_role=ActorRole.OUTBOX_WORKER, now=NOW)
    claimed = repository.claim_next(
        postgres_connection, worker_role=ActorRole.AGENT_RUNNER, now=NOW
    )
    assert claimed is not None
    assert claimed.agent_run_id == queued.agent_run_id
    assert claimed.attempt_count == 1
    assert claimed.lease_expires_at == NOW + timedelta(seconds=20)

    repository.record_tool_call(
        postgres_connection,
        AgentToolCallLedgerEntry(
            agent_run_id=claimed.agent_run_id,
            claim_token=claimed.claim_token,
            sequence_number=1,
            operation=AgentToolOperation.CATALOG_RESOLVE,
            request_fingerprint=request_fingerprint({"locale": "vi-VN", "query_length": 9}),
            result_status_code=200,
            result_code="REQUIRE_HUMAN",
            trace_id="tr_12345678",
            safe_summary={"candidate_count": 1, "decision": "REQUIRE_HUMAN"},
            started_at=NOW,
            completed_at=NOW + timedelta(milliseconds=40),
            correlation_id=queued.correlation_id,
        ),
    )
    repository.complete_draft(
        postgres_connection,
        agent_run_id=claimed.agent_run_id,
        claim_token=claimed.claim_token,
        safe_summary={"disposition": "REQUIRE_HUMAN", "draft_chars": 42},
        correlation_id=queued.correlation_id,
        completed_at=NOW + timedelta(seconds=1),
    )

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, automatic_send_authorized, result_safe_summary, completed_at
            FROM agent_runs WHERE id = %s
            """,
            (queued.agent_run_id,),
        )
        run_row = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM agent_tool_calls WHERE agent_run_id = %s",
            (queued.agent_run_id,),
        )
        tool_count = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM audit_events "
            "WHERE aggregate_type = 'AGENT_RUN' AND aggregate_id = %s",
            (queued.agent_run_id,),
        )
        audit_count = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM outbox_events "
            "WHERE aggregate_type = 'AGENT_RUN' AND aggregate_id = %s",
            (queued.agent_run_id,),
        )
        outbox_count = cursor.fetchone()

    assert run_row == (
        "DRAFT_REQUIRES_HUMAN",
        False,
        {"disposition": "REQUIRE_HUMAN", "draft_chars": 42},
        NOW + timedelta(seconds=1),
    )
    assert tool_count == (1,)
    assert audit_count == (3,)
    assert outbox_count == (3,)


def test_agent_run_rejects_non_shadow_raw_reasoning_and_stale_claim(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = AgentRunRepository()
    with pytest.raises(AgentRunStateError, match="only shadow"):
        repository.enqueue(
            postgres_connection,
            replace(command(), deployment_stage=AgentDeploymentStage.ASSISTED),
        )

    queued = command()
    repository.enqueue(postgres_connection, queued)
    claimed = repository.claim_next(
        postgres_connection, worker_role=ActorRole.AGENT_RUNNER, now=NOW
    )
    assert claimed is not None
    with pytest.raises(AgentRunStateError, match="prohibited internal content"):
        repository.complete_draft(
            postgres_connection,
            agent_run_id=claimed.agent_run_id,
            claim_token=claimed.claim_token,
            safe_summary={"chain_of_thought": "never persist this"},
            correlation_id=queued.correlation_id,
            completed_at=NOW,
        )
    with pytest.raises(AgentRunStateError, match="stale or unavailable"):
        repository.complete_draft(
            postgres_connection,
            agent_run_id=claimed.agent_run_id,
            claim_token=uuid4(),
            safe_summary={"disposition": "REQUIRE_HUMAN"},
            correlation_id=queued.correlation_id,
            completed_at=NOW,
        )
