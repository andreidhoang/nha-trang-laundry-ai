from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.automation import AutomationExecutionRepository
from nha_trang_laundry_db.migrations import apply_migrations

NOW = datetime(2026, 8, 1, 3, tzinfo=UTC)


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def test_disabled_capability_holds_previously_created_automated_envelope(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    envelope_id = _seed_pending_envelope(postgres_connection, capability_enabled=False)

    status = AutomationExecutionRepository().hold_if_disabled(
        postgres_connection,
        envelope_id=envelope_id,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        now=NOW,
    )

    assert status == "HELD"
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, hold_reason FROM automated_execution_envelopes WHERE id = %s",
            (envelope_id,),
        )
        assert cursor.fetchone() == ("HELD", "AUTOMATION_DISABLED")
        cursor.execute("SELECT count(*) FROM audit_events WHERE aggregate_id = %s", (envelope_id,))
        assert cursor.fetchone() == (1,)


def test_missing_or_expired_gate_fails_closed_before_automated_execution(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    for expires_at in (None, NOW - timedelta(seconds=1)):
        envelope_id = _seed_pending_envelope(
            postgres_connection,
            capability_enabled=True,
            expires_at=expires_at,
        )
        status = AutomationExecutionRepository().hold_if_disabled(
            postgres_connection,
            envelope_id=envelope_id,
            actor_id=uuid4(),
            correlation_id=uuid4(),
            now=NOW,
        )
        assert status == "HELD"


def _seed_pending_envelope(
    connection: Any,
    *,
    capability_enabled: bool,
    expires_at: datetime | None = NOW + timedelta(minutes=1),
) -> UUID:
    envelope_id = uuid4()
    outbox_event_id = uuid4()
    capability = f"LIST_PRICE_INFO_{envelope_id.hex}"
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                correlation_id, occurred_at
            ) VALUES (%s, 'MESSAGE', %s, 'message.send_requested.v1', '{}'::jsonb, %s, %s, %s)
            """,
            (outbox_event_id, envelope_id, f"test:{envelope_id}", uuid4(), NOW),
        )
        cursor.execute(
            """
            INSERT INTO automated_execution_envelopes (
                id, capability, outbox_event_id, status, hold_policy, created_at
            ) VALUES (%s, %s, %s, 'PENDING', 'HOLD', %s)
            """,
            (envelope_id, capability, outbox_event_id, NOW),
        )
        if expires_at is not None:
            cursor.execute(
                """
            INSERT INTO automation_execution_gates (
                capability, global_automation_enabled, agent_processing_enabled,
                agent_outbound_enabled, channel_ingress_enabled, capability_enabled,
                stage_policy_allows, pdp_allows, version, expires_at, updated_at
            ) VALUES (%s, TRUE, TRUE, TRUE, TRUE, %s, TRUE, TRUE, 1, %s, %s)
            """,
                (capability, capability_enabled, expires_at, NOW),
            )
    return envelope_id
