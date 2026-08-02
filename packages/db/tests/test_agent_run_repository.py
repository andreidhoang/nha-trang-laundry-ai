from __future__ import annotations

import os
import threading
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

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

NOW = datetime.now(UTC)
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


def command(*, created_at: datetime | None = None) -> AgentRunEnqueueCommand:
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
        created_at=created_at or NOW,
    )


def test_agent_run_queue_tool_ledger_and_draft_completion_are_durable(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = AgentRunRepository()
    queued = command(created_at=_before_pending_agent_run(postgres_connection))
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


def test_agent_tool_audit_failure_rolls_back_tool_event_and_outbox(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = AgentRunRepository()
    queued = command(created_at=_before_pending_agent_run(postgres_connection))
    repository.enqueue(postgres_connection, queued)
    claimed = repository.claim_next(
        postgres_connection, worker_role=ActorRole.AGENT_RUNNER, now=NOW
    )
    assert claimed is not None
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE FUNCTION reject_agent_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'injected agent audit failure'; END;
            $$
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER agent_audit_failure BEFORE INSERT ON audit_events
            FOR EACH ROW EXECUTE FUNCTION reject_agent_audit()
            """
        )
    try:
        with pytest.raises(psycopg.Error, match="injected agent audit failure"):
            repository.record_tool_call(
                postgres_connection,
                AgentToolCallLedgerEntry(
                    claimed.agent_run_id,
                    claimed.claim_token,
                    1,
                    AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS,
                    request_fingerprint({"fact_count": 1}),
                    200,
                    "REQUIRE_HUMAN",
                    "synthetic-audit-failure",
                    {"result_code": "REQUIRE_HUMAN"},
                    NOW,
                    NOW,
                    queued.correlation_id,
                ),
            )
        with postgres_connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM agent_tool_calls WHERE agent_run_id = %s",
                (queued.agent_run_id,),
            )
            assert cursor.fetchone() == (0,)
            cursor.execute(
                """
                SELECT count(*) FROM domain_events
                WHERE aggregate_id = %s AND event_type = 'AGENT_TOOL_CALL_RECORDED'
                """,
                (queued.agent_run_id,),
            )
            assert cursor.fetchone() == (0,)
            cursor.execute(
                """
                SELECT count(*) FROM outbox_events
                WHERE aggregate_id = %s AND event_type = 'agent.tool_called.v1'
                """,
                (queued.agent_run_id,),
            )
            assert cursor.fetchone() == (0,)
    finally:
        with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER IF EXISTS agent_audit_failure ON audit_events")
            cursor.execute("DROP FUNCTION IF EXISTS reject_agent_audit()")


def test_agent_run_rejects_non_shadow_raw_reasoning_and_stale_claim(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = AgentRunRepository()
    with pytest.raises(AgentRunStateError, match="only shadow"):
        repository.enqueue(
            postgres_connection,
            replace(command(), deployment_stage=AgentDeploymentStage.ASSISTED),
        )

    queued = command(created_at=_before_pending_agent_run(postgres_connection))
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


def test_expired_agent_run_fails_to_human_recovery_without_replay(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = AgentRunRepository()
    claim_time = datetime(1970, 1, 2, tzinfo=UTC)
    queued = command(created_at=_before_pending_agent_run(postgres_connection))
    repository.enqueue(postgres_connection, queued)
    claimed = repository.claim_next(
        postgres_connection, worker_role=ActorRole.AGENT_RUNNER, now=claim_time
    )
    assert claimed is not None and claimed.agent_run_id == queued.agent_run_id

    recovered = repository.recover_expired(
        postgres_connection,
        worker_role=ActorRole.AGENT_RUNNER,
        now=claim_time + timedelta(seconds=21),
    )
    assert recovered == queued.agent_run_id
    assert (
        repository.recover_expired(
            postgres_connection,
            worker_role=ActorRole.AGENT_RUNNER,
            now=claim_time + timedelta(seconds=21),
        )
        is None
    )
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT status, failure_code, automatic_send_authorized, claim_token
            FROM agent_runs WHERE id = %s
            """,
            (queued.agent_run_id,),
        )
        assert cursor.fetchone() == ("FAILED", "LEASE_EXPIRED", False, None)
        cursor.execute(
            """
            SELECT count(*) FROM audit_events
            WHERE aggregate_type = 'AGENT_RUN' AND aggregate_id = %s
              AND action = 'AGENT_RUN_LEASE_EXPIRED'
            """,
            (queued.agent_run_id,),
        )
        assert cursor.fetchone() == (1,)
        cursor.execute(
            """
            SELECT payload, idempotency_key FROM outbox_events
            WHERE aggregate_type = 'AGENT_RUN' AND aggregate_id = %s
              AND event_type = 'agent.run_completed.v1'
            """,
            (queued.agent_run_id,),
        )
        assert cursor.fetchone() == (
            {
                "agent_run_id": str(queued.agent_run_id),
                "disposition": "REQUIRE_HUMAN",
            },
            f"agent-run:{queued.agent_run_id}:lease-expired",
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


def test_two_agent_workers_cannot_claim_the_same_run(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = AgentRunRepository()
    queued = replace(command(), created_at=_before_pending_agent_run(postgres_connection))
    repository.enqueue(postgres_connection, queued)
    claim_time = datetime.now(UTC)
    barrier = threading.Barrier(2)
    claimed: list[tuple[UUID, UUID] | None] = []
    errors: list[BaseException] = []

    def claim() -> None:
        try:
            with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
                barrier.wait(timeout=2)
                result = repository.claim_next(
                    connection,
                    worker_role=ActorRole.AGENT_RUNNER,
                    now=claim_time,
                )
                claimed.append(
                    None if result is None else (result.agent_run_id, result.claim_token)
                )
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3)

    assert errors == []
    matching_claims = [
        item for item in claimed if item is not None and item[0] == queued.agent_run_id
    ]
    assert len(matching_claims) == 1
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, attempt_count FROM agent_runs WHERE id = %s",
            (queued.agent_run_id,),
        )
        assert cursor.fetchone() == ("PROCESSING", 1)
    repository.fail(
        postgres_connection,
        agent_run_id=queued.agent_run_id,
        claim_token=matching_claims[0][1],
        failure_code="SYNTHETIC_TEST_CLEANUP",
        correlation_id=queued.correlation_id,
        completed_at=claim_time,
    )


def _before_pending_agent_run(connection: psycopg.Connection[Any]) -> datetime:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SELECT min(created_at) FROM agent_runs WHERE status = 'PENDING'")
        row = cursor.fetchone()
    earliest = None if row is None else row[0]
    if not isinstance(earliest, datetime):
        return datetime(1970, 1, 1, tzinfo=UTC)
    return earliest - timedelta(days=1)
