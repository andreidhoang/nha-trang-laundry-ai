"""`PLATFORM-SECURITY-009` P5: the API's repository write paths, executed as `laundry_api`.

Every other database test runs as the schema owner (in CI, the container superuser), so a
repository that needed DELETE, DDL or a table the grant forgot would pass the whole suite and fail
in the shop with `permission denied`. This walks one counter day -- a customer whose phone is sealed
with the deployment key, a priced and accepted quote, the order, its steps, a payment that settles
it, the hand-over, and an export request -- through the real repositories on a connection that *is*
`laundry_api`, after `scripts/apply_demo_grants.py` has run exactly as the runbooks run it.

With `LAUNDRY_API_DATABASE_URL` set (CI creates the role with a password) the connection logs in as
the role; otherwise it is `DATABASE_URL` + `SET ROLE laundry_api`. `current_user` is asserted either
way. The worker's half is `apps/worker/tests/test_worker_least_privilege.py`.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from nha_trang_laundry_db.exports import (
    ExportDataset,
    ExportRequestCommand,
    SanitizedExportRepository,
)
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.catalog import CommercialOrderStatus
from nha_trang_laundry_domain.order_steps import OrderStep
from psycopg import sql
from test_customer_records import _create, _mobile, _order_for, _published, _staff, _store
from test_order_payments import _pay, _to_ready
from test_order_step_repository import _primary, _read, _step

ROOT = Path(__file__).resolve().parents[3]
API = "laundry_api"


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return value


def _apply_grants(connection: Any) -> None:
    path = ROOT / "scripts" / "apply_demo_grants.py"
    specification = importlib.util.spec_from_file_location("_ps009_apply_grants", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    with connection.cursor() as cursor:
        cursor.execute("SELECT tableowner FROM pg_tables WHERE tablename = 'schema_migrations'")
        row = cursor.fetchone()
    assert row is not None
    module.apply_grants(connection, owner=str(row[0]))


@pytest.fixture
def api() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(_database_url(), autocommit=True) as owner:
        apply_migrations(owner)
        with owner.cursor() as cursor:
            for role in (API, "laundry_worker"):
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                if cursor.fetchone() is None:
                    cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        _apply_grants(owner)
    login = os.environ.get("LAUNDRY_API_DATABASE_URL")
    connection = psycopg.connect(login or _database_url(), autocommit=True)
    try:
        if not login:
            connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(API)))
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user"
            )
            assert cursor.fetchone() == (API, False), "the smoke must run as the role itself"
        yield connection
    finally:
        connection.close()


def test_a_counter_day_runs_end_to_end_as_laundry_api(api: psycopg.Connection[Any]) -> None:
    _published(api)
    store_id = _store(api)
    staff = _staff(api, store_id)
    owner = _staff(api, store_id, StaffRole.OWNER_ADMIN)
    national, _ = _mobile()
    customer_id = _create(api, store_id, staff, national)
    order_id = _order_for(api, store_id, staff, customer_id)

    view = _read(api, order_id, staff)
    view = _step(api, order_id, staff, view.row_version, OrderStep.RECEIVE, slot_approved=True).view
    view = _to_ready(api, order_id, staff)
    _pay(api, order_id, staff, view.remaining_vnd, collected=True)
    view = _read(api, order_id, staff)
    assert _primary(view) is OrderStep.HAND_OVER
    done = _step(api, order_id, staff, view.row_version, OrderStep.HAND_OVER).view
    assert done.commercial is CommercialOrderStatus.COMPLETED
    assert done.remaining_vnd == 0

    requested = SanitizedExportRepository().request(
        api,
        ExportRequestCommand(
            store_id=store_id,
            dataset=ExportDataset.STORE_DAY_ORDERS_V1,
            business_date=datetime.now(UTC).date(),
            principal=owner,
            correlation_id=uuid4(),
            idempotency_key=f"export-{uuid4().hex}",
        ),
    )
    assert requested.replayed is False

    with api.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM audit_events WHERE aggregate_id = ANY(%s)",
            ([order_id, customer_id, requested.export_request_id],),
        )
        row = cursor.fetchone()
    assert row is not None and row[0] > 0


@pytest.mark.parametrize(
    "statement",
    (
        "DELETE FROM orders",
        "DELETE FROM customers",
        "DELETE FROM webhook_event_payloads",  # DEC-020: retention_purge alone
        "TRUNCATE outbox_events",
        "CREATE TABLE ps009_ddl_probe (id int)",
    ),
)
def test_laundry_api_holds_nothing_destructive(
    api: psycopg.Connection[Any], statement: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute(statement)
