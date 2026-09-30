"""`OPS-OBSERVABILITY-009` (review P6): "pending" is work somebody will do, not every row ever
written.

Every material mutation writes an outbox row, and only the event types in
`nha_trang_laundry_db.outbox.INTERNAL_EVENT_TYPES` are ever claimed -- the rest (payments, remedies,
invoices, reminders, ...) are the record of what happened, with no consumer. `GET
/internal/v1/queue-recovery` answered `pending_internal` with `count(*) WHERE status = 'PENDING'`,
so an idle, healthy shop showed a "pending" figure that grew by one with every order and never
went down. These tests commit rows through real PostgreSQL and read the figure through the route.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import app, current_principal, get_operations_service
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.outbox import INTERNAL_EVENT_TYPES

OWNER_ID = UUID("00000000-0000-0000-0000-000000000101")
#: Never claimable by `claim_next_internal` (not in the allowlist) -- a real type the payment path
#: writes on every settlement.
RECORD_ONLY_TYPE = "order.payment_recorded.v1"
#: A row that must not be claimed by any other test sharing this database while it sits here.
FAR_FUTURE = datetime(2126, 1, 1, tzinfo=UTC)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = AuthSettings(database_url=_database_url())
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        OWNER_ID, "owner-test-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _outbox_row(connection: Any, event_type: str, *, available_at: datetime) -> None:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                correlation_id, occurred_at, available_at
            ) VALUES (%s, 'ORDER', %s, %s, %s::jsonb, %s, %s, %s, %s)
            """,
            (
                uuid4(),
                uuid4(),
                event_type,
                json.dumps({"probe": "OPS-OBSERVABILITY-009"}),
                f"ops-observability-009:{uuid4()}",
                uuid4(),
                datetime.now(UTC),
                available_at,
            ),
        )


def _pending(client: TestClient) -> int:
    response = client.get("/internal/v1/queue-recovery")
    assert response.status_code == 200, response.text
    return int(response.json()["pending_internal"])


def test_the_record_only_type_really_is_never_claimed() -> None:
    assert RECORD_ONLY_TYPE not in INTERNAL_EVENT_TYPES


def test_a_record_only_outbox_row_is_not_pending_work(connection: Any, client: TestClient) -> None:
    before = _pending(client)
    _outbox_row(connection, RECORD_ONLY_TYPE, available_at=datetime.now(UTC))
    _outbox_row(connection, RECORD_ONLY_TYPE, available_at=FAR_FUTURE)
    assert _pending(client) == before


@pytest.mark.parametrize("event_type", sorted(INTERNAL_EVENT_TYPES)[:3])
def test_a_claimable_internal_row_is_still_pending_work(
    connection: Any, client: TestClient, event_type: str
) -> None:
    """Held back by `available_at` (a retry delay) is still pending: the worker will take it."""

    before = _pending(client)
    _outbox_row(connection, event_type, available_at=FAR_FUTURE)
    assert _pending(client) == before + 1


def test_the_other_counters_are_unchanged_by_either_kind(
    connection: Any, client: TestClient
) -> None:
    def _others() -> dict[str, int]:
        body = client.get("/internal/v1/queue-recovery").json()
        return {k: v for k, v in body.items() if k not in {"pending_internal"}}

    before = _others()
    _outbox_row(connection, RECORD_ONLY_TYPE, available_at=datetime.now(UTC))
    _outbox_row(connection, sorted(INTERNAL_EVENT_TYPES)[0], available_at=FAR_FUTURE)
    assert _others() == before


def test_the_backlog_a_real_day_leaves_is_not_reported_as_pending(
    connection: Any, client: TestClient
) -> None:
    """What the review measured: ~56 record-only types accumulating. A day of them adds nothing."""

    before = _pending(client)
    for offset in range(25):
        _outbox_row(
            connection,
            RECORD_ONLY_TYPE,
            available_at=datetime.now(UTC) - timedelta(minutes=offset),
        )
    assert _pending(client) == before
