from __future__ import annotations

import os
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
)
from nha_trang_laundry_domain.catalog import ActorRole

NOW = datetime(2026, 8, 1, tzinfo=UTC)


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
        available_at=datetime(2000, 1, 1, tzinfo=UTC),
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
        available_at=datetime(2001, 1, 1, tzinfo=UTC),
    )
    claimed = repository.claim_next_internal(
        postgres_connection, worker_role=ActorRole.OUTBOX_WORKER, now=NOW
    )
    assert claimed is not None and claimed.event_id == retry_id
    assert (
        repository.retry_or_dead_letter_internal(
            postgres_connection,
            event_id=retry_id,
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
        available_at=datetime(2002, 1, 1, tzinfo=UTC),
    )
    claimed_dead = repository.claim_next_internal(
        postgres_connection, worker_role=ActorRole.OUTBOX_WORKER, now=NOW
    )
    assert claimed_dead is not None and claimed_dead.event_id == dead_id
    assert (
        repository.retry_or_dead_letter_internal(
            postgres_connection,
            event_id=dead_id,
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


def _insert_outbox(
    connection: psycopg.Connection[Any],
    *,
    event_type: str,
    available_at: datetime,
    purpose: str | None = None,
) -> UUID:
    event_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                correlation_id, occurred_at, available_at, purpose
            ) VALUES (%s, 'TEST', %s, %s, '{}'::jsonb, %s, %s, %s, %s, %s)
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
            ),
        )
    return event_id
