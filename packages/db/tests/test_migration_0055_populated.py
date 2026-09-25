"""`0055` applied to a database that already holds orders and intakes (`CUSTOMER-001`).

`test_migration_populated.py` explains why this shape of test exists. `0055` adds two tables, a
nullable `customer_id` on `orders` and `order_requests`, and triggers on `orders`. The question is
what happens to what was written before a customer record could exist: a walk-in's order and an
intake still waiting to be priced, when the new code is deployed.

The answer this pins: nothing about them moves. Both keep a NULL customer, the order still reads
through the new read model (with no customer), the waiting intake still becomes an order -- which
inherits no customer -- and the first customer recorded afterwards gets orders of their own.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.counter_tickets import CounterTicketRepository
from nha_trang_laundry_db.customers import CustomerRepository
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.intake import CreateOrderRequestCommand, OrderRequestRepository
from nha_trang_laundry_db.migrations import MIGRATIONS_DIRECTORY, apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository
from nha_trang_laundry_db.privacy_notice import publish_privacy_notice
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode
from nha_trang_laundry_domain.customers import CustomerKind
from psycopg import sql
from psycopg.conninfo import make_conninfo
from quote_test_data import accepted_quote
from test_customer_notice import notice_payload

MIGRATION_UNDER_TEST = "0055"
NOW = datetime.now(UTC).replace(microsecond=0)


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def scratch_database() -> Generator[str, None, None]:
    configured = _database_url()
    maintenance = make_conninfo(configured, dbname="postgres")
    name = f"ntl_migration_{uuid4().hex[:12]}"
    with psycopg.connect(maintenance, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(configured, dbname=name)
    finally:
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def migrations_before(tmp_path: Path) -> Path:
    directory = tmp_path / "migrations"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIRECTORY.glob("*.sql")):
        if path.name[:4] < MIGRATION_UNDER_TEST:
            shutil.copy2(path, directory / path.name)
    return directory


def _staff(connection: psycopg.Connection, store_id: UUID, role: StaffRole) -> StaffPrincipal:
    staff_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
            """,
            (staff_id, f"oidc-{staff_id}", NOW),
        )
        cursor.execute(
            """
            INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
            VALUES (%s, %s, %s, %s)
            """,
            (uuid4(), staff_id, role.value, NOW),
        )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (staff_id, store_id, staff_id, NOW),
        )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), True, uuid4())


def _order(connection: psycopg.Connection, store_id: UUID, staff: StaffPrincipal, **extra):  # type: ignore[no-untyped-def]
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, **extra
    )
    return (
        OrderRepository()
        .create(
            connection,
            CreateOrderCommand(
                store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                NOW,
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )


def test_orders_and_intakes_from_before_0055_keep_no_customer_and_still_work(
    scratch_database: str, migrations_before: Path
) -> None:
    with psycopg.connect(scratch_database, autocommit=True) as connection:
        apply_migrations(connection, migrations_before)
        store_id = uuid4()
        # The shop exists before anyone joins it, as the deploy-day runbook requires.
        staff = _staff_after_store(connection, store_id)
        old_order = _order(connection, store_id, staff)
        ticket = CounterTicketRepository().issue(
            connection, store_id=store_id, principal=staff, correlation_id=uuid4()
        )
        waiting = OrderRequestRepository().create(
            connection,
            CreateOrderRequestCommand(
                store_id=store_id,
                contact_binding_id=ticket.ticket_id,
                conversation_binding_id=uuid4(),
                actor_id=staff.staff_user_id,
                correlation_id=uuid4(),
                created_at=NOW,
                actor_type="STAFF",
            ),
        )

        apply_migrations(connection)

        with connection.cursor() as cursor:
            cursor.execute("SELECT customer_id FROM orders WHERE id = %s", (old_order,))
            assert cursor.fetchone() == (None,)
            cursor.execute(
                "SELECT customer_id FROM order_requests WHERE id = %s", (waiting.order_request_id,)
            )
            assert cursor.fetchone() == (None,)
            view = OrderRepository.read_for_principal(cursor, order_id=old_order, principal=staff)
            assert (view.customer_id, view.customer_name, view.customer_has_phone) == (
                None,
                None,
                False,
            )
            summary = OrderRequestRepository.get_for_store(
                cursor,
                order_request_id=waiting.order_request_id,
                store_id=store_id,
                principal=staff,
            )
            assert summary is not None and summary.customer_id is None

        # A walk-in order taken after the migration still inherits nothing.
        assert _customer_of(connection, _order(connection, store_id, staff)) is None

        # And the first customer recorded afterwards gets orders of their own.
        owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
        publish_privacy_notice(connection, actor_id=owner.staff_user_id, payload=notice_payload())
        customer_id = CustomerRepository().create(
            connection,
            store_id=store_id,
            principal=staff,
            phone="0905 000 555",
            display_name="chị Lan",
            delivery_address=None,
            note=None,
            kind=CustomerKind.RETAIL,
            service_consent=True,
            marketing_consent=False,
            at=NOW,
            correlation_id=uuid4(),
        )
        assert (
            _customer_of(connection, _order(connection, store_id, staff, customer_id=customer_id))
            == customer_id
        )


def _staff_after_store(connection: psycopg.Connection, store_id: UUID) -> StaffPrincipal:
    from quote_test_data import ensure_store

    ensure_store(connection, store_id)
    return _staff(connection, store_id, StaffRole.OPERATOR)


def _customer_of(connection: psycopg.Connection, order_id: object) -> object:
    with connection.cursor() as cursor:
        cursor.execute("SELECT customer_id FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
    assert row is not None
    return row[0]
