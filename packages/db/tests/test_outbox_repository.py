from __future__ import annotations

import os
import threading
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.outbox import (
    OutboxAuthorizationError,
    OutboxRepository,
    OutboxStateError,
)
from nha_trang_laundry_domain.catalog import ActorRole

NOW = datetime.now(UTC)


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def test_only_outbox_worker_claims_allowlisted_internal_events_not_provider_send(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = OutboxRepository()
    message_id = _insert_outbox(
        postgres_connection,
        event_type="message.send_requested.v1",
        available_at=datetime(1999, 1, 1, tzinfo=UTC),
        purpose="MARKETING",
    )
    internal_id = _insert_outbox(
        postgres_connection,
        event_type="order.state_transitioned.v1",
        available_at=_before_pending_outbox(postgres_connection),
    )

    with pytest.raises(OutboxAuthorizationError, match="OUTBOX_WORKER"):
        repository.claim_next_internal(
            postgres_connection, worker_role=ActorRole.AGENT_RUNNER, now=NOW
        )
    claimed = repository.claim_next_internal(
        postgres_connection, worker_role=ActorRole.OUTBOX_WORKER, now=NOW
    )
    assert claimed is not None and claimed.event_id == internal_id
    repository.complete_internal(
        postgres_connection,
        event_id=internal_id,
        claim_token=claimed.claim_token,
        worker_role=ActorRole.OUTBOX_WORKER,
        completed_at=NOW,
    )
    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT status FROM outbox_events WHERE id = %s", (internal_id,))
        internal_status = cursor.fetchone()
        cursor.execute("SELECT status FROM outbox_events WHERE id = %s", (message_id,))
        message_status = cursor.fetchone()
    assert internal_status == ("SENT",)
    assert message_status == ("PENDING",)


def test_internal_outbox_retry_schedule_and_dead_letter_are_durable(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = OutboxRepository()
    retry_id = _insert_outbox(
        postgres_connection,
        event_type="order.state_transitioned.v1",
        available_at=_before_pending_outbox(postgres_connection),
    )
    claimed = repository.claim_next_internal(
        postgres_connection, worker_role=ActorRole.OUTBOX_WORKER, now=NOW
    )
    assert claimed is not None and claimed.event_id == retry_id
    assert (
        repository.retry_or_dead_letter_internal(
            postgres_connection,
            event_id=retry_id,
            claim_token=claimed.claim_token,
            worker_role=ActorRole.OUTBOX_WORKER,
            error_class="DEPENDENCY_TIMEOUT",
            retryable=True,
            now=NOW,
        )
        == "PENDING"
    )
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, available_at, attempt_count FROM outbox_events WHERE id = %s",
            (retry_id,),
        )
        assert cursor.fetchone() == ("PENDING", NOW + timedelta(seconds=30), 1)

    dead_id = _insert_outbox(
        postgres_connection,
        event_type="order.state_transitioned.v1",
        available_at=_before_pending_outbox(postgres_connection),
    )
    claimed_dead = repository.claim_next_internal(
        postgres_connection, worker_role=ActorRole.OUTBOX_WORKER, now=NOW
    )
    assert claimed_dead is not None and claimed_dead.event_id == dead_id
    assert (
        repository.retry_or_dead_letter_internal(
            postgres_connection,
            event_id=dead_id,
            claim_token=claimed_dead.claim_token,
            worker_role=ActorRole.OUTBOX_WORKER,
            error_class="INVALID_PAYLOAD",
            retryable=False,
            now=NOW,
        )
        == "DEAD"
    )
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT o.status, d.last_error_class, d.replay_eligible
            FROM outbox_events o
            JOIN dead_letter_events d ON d.outbox_event_id = o.id
            WHERE o.id = %s
            """,
            (dead_id,),
        )
        assert cursor.fetchone() == ("DEAD", "INVALID_PAYLOAD", False)


def test_internal_outbox_claim_is_fenced_and_expiry_requires_dlq_recovery(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = OutboxRepository()
    event_id = _insert_outbox(
        postgres_connection,
        event_type="order.state_transitioned.v1",
        available_at=_before_pending_outbox(postgres_connection),
    )
    claimed = repository.claim_next_internal(
        postgres_connection, worker_role=ActorRole.OUTBOX_WORKER, now=NOW
    )
    assert claimed is not None and claimed.event_id == event_id
    assert claimed.lease_expires_at == NOW + timedelta(seconds=30)
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, claim_token FROM outbox_events WHERE id = %s",
            (event_id,),
        )
        assert cursor.fetchone() == ("PROCESSING", claimed.claim_token)
    with pytest.raises(OutboxStateError, match="stale"):
        repository.complete_internal(
            postgres_connection,
            event_id=event_id,
            claim_token=uuid4(),
            worker_role=ActorRole.OUTBOX_WORKER,
            completed_at=NOW,
        )

    recovered_ids: list[UUID] = []
    for _ in range(100):
        recovered = repository.recover_expired_internal(
            postgres_connection,
            worker_role=ActorRole.OUTBOX_WORKER,
            now=NOW + timedelta(seconds=31),
        )
        if recovered is None:
            break
        recovered_ids.append(recovered)
        if recovered == event_id:
            break
    assert event_id in recovered_ids
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT event.status, event.claim_token, dead.last_error_class,
                   dead.replay_eligible
            FROM outbox_events AS event
            JOIN dead_letter_events AS dead ON dead.outbox_event_id = event.id
            WHERE event.id = %s
            """,
            (event_id,),
        )
        assert cursor.fetchone() == (
            "DEAD",
            None,
            "UNKNOWN_INTERNAL_HANDLER_OUTCOME",
            False,
        )
        cursor.execute(
            """
            SELECT count(*) FROM audit_events
            WHERE aggregate_type = 'OUTBOX_EVENT' AND aggregate_id = %s
              AND action = 'OUTBOX_EXPIRED_TO_DLQ'
            """,
            (event_id,),
        )
        assert cursor.fetchone() == (1,)


def test_internal_outbox_claim_carries_only_constrained_w3c_trace_context(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    traceparent = "00-00000000000000000000000000000901-0000000000000902-01"
    event_id = _insert_outbox(
        postgres_connection,
        event_type="order.state_transitioned.v1",
        available_at=_before_pending_outbox(postgres_connection),
        traceparent=traceparent,
        tracestate="synthetic=vendor-neutral",
    )

    claimed = OutboxRepository().claim_next_internal(
        postgres_connection, worker_role=ActorRole.OUTBOX_WORKER, now=NOW
    )

    assert claimed is not None and claimed.event_id == event_id
    assert claimed.traceparent == traceparent
    assert claimed.tracestate == "synthetic=vendor-neutral"
    assert "traceparent" not in claimed.payload

    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_outbox(
            postgres_connection,
            event_type="order.state_transitioned.v1",
            available_at=_before_pending_outbox(postgres_connection),
            traceparent="customer-controlled-value",
        )


def test_two_workers_cannot_claim_the_same_internal_event(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = OutboxRepository()
    claim_time = datetime.now(UTC)
    event_id = _insert_outbox(
        postgres_connection,
        event_type="order.state_transitioned.v1",
        available_at=_before_pending_outbox(postgres_connection),
    )
    barrier = threading.Barrier(2)
    claimed: list[tuple[UUID, UUID] | None] = []
    errors: list[BaseException] = []

    def claim() -> None:
        try:
            with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
                barrier.wait(timeout=2)
                result = repository.claim_next_internal(
                    connection,
                    worker_role=ActorRole.OUTBOX_WORKER,
                    now=claim_time,
                )
                claimed.append(None if result is None else (result.event_id, result.claim_token))
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3)

    assert errors == []
    matching_claims = [item for item in claimed if item is not None and item[0] == event_id]
    assert len(matching_claims) == 1
    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT status, attempt_count FROM outbox_events WHERE id = %s", (event_id,))
        assert cursor.fetchone() == ("PROCESSING", 1)
    repository.complete_internal(
        postgres_connection,
        event_id=event_id,
        claim_token=matching_claims[0][1],
        worker_role=ActorRole.OUTBOX_WORKER,
        completed_at=claim_time,
    )


def test_retry_exhaustion_moves_internal_event_to_dlq(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = OutboxRepository()
    claim_time = datetime.now(UTC)
    event_id = _insert_outbox(
        postgres_connection,
        event_type="order.state_transitioned.v1",
        available_at=_before_pending_outbox(postgres_connection),
        attempt_count=5,
    )
    claimed = repository.claim_next_internal(
        postgres_connection, worker_role=ActorRole.OUTBOX_WORKER, now=claim_time
    )
    assert claimed is not None and claimed.event_id == event_id

    result = repository.retry_or_dead_letter_internal(
        postgres_connection,
        event_id=event_id,
        claim_token=claimed.claim_token,
        worker_role=ActorRole.OUTBOX_WORKER,
        error_class="DEPENDENCY_TIMEOUT",
        retryable=True,
        now=claim_time,
    )

    assert result == "DEAD"
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT event.status, event.attempt_count, dead.last_error_class,
                   dead.replay_eligible
            FROM outbox_events AS event
            JOIN dead_letter_events AS dead ON dead.outbox_event_id = event.id
            WHERE event.id = %s
            """,
            (event_id,),
        )
        assert cursor.fetchone() == ("DEAD", 6, "DEPENDENCY_TIMEOUT", True)


def _insert_outbox(
    connection: psycopg.Connection[Any],
    *,
    event_type: str,
    available_at: datetime,
    purpose: str | None = None,
    attempt_count: int = 0,
    traceparent: str | None = None,
    tracestate: str | None = None,
) -> UUID:
    event_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                correlation_id, occurred_at, available_at, purpose, attempt_count,
                traceparent, tracestate
            ) VALUES (%s, 'TEST', %s, %s, '{}'::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_id,
                uuid4(),
                event_type,
                f"test:{event_id}",
                uuid4(),
                available_at,
                available_at,
                purpose,
                attempt_count,
                traceparent,
                tracestate,
            ),
        )
    return event_id


def _before_pending_outbox(connection: psycopg.Connection[Any]) -> datetime:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SELECT min(available_at) FROM outbox_events WHERE status = 'PENDING'")
        row = cursor.fetchone()
    earliest = None if row is None else row[0]
    if not isinstance(earliest, datetime):
        return datetime(1970, 1, 1, tzinfo=UTC)
    return earliest - timedelta(days=1)
