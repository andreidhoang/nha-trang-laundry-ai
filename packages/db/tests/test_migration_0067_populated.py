"""`0067` applied to a database that already holds a refund written before refunds had a method.

`test_migration_populated.py` explains why this shape of test exists. `0067` adds
`order_refunds.refund_method` and makes every NEW refund carry it; the rows already written never
recorded how the money went back, and the migration must not guess. So:

* the pre-`0067` refund is untouched and reads as method unknown (NULL);
* the drawer figure over that day excludes it and says so -- counted and summed as unknown, never
  netted as cash -- while money in and the all-method net are exactly what they were;
* a refunding cancellation after the migration records its method, and a refund row without one is
  refused by the database.

The legacy refund is written the way the pre-`0067` repository wrote it -- the refund row and the
order's move to CANCELLED/REFUNDED, and nothing else -- because the current repository names the
new column, which does not exist yet at that point.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_db.migrations import MIGRATIONS_DIRECTORY, apply_migrations
from nha_trang_laundry_db.settlement import SettlementRepository
from nha_trang_laundry_domain.catalog import CommercialOrderStatus, CustodyResolution
from nha_trang_laundry_domain.payments import PaymentMethod
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_reports import DAY, _Order, _person, _store, local

MIGRATION_UNDER_TEST = "0067"

#: The totals of `collected-today-v3`, verbatim as `settlement.py` published them before `0067`:
#: what every past day's card showed. v4 must read the same four figures for such a day.
_COLLECTED_TODAY_V3_TOTALS_SQL = """
    WITH taken AS (
        SELECT coalesce(sum(amount_vnd), 0) AS amount
        FROM order_payments
        WHERE store_id = %(store)s
          AND (recorded_at AT TIME ZONE %(zone)s)::date = %(business_date)s
    ), refunded AS (
        SELECT coalesce(sum(refunded_amount_vnd), 0) AS amount
        FROM order_refunds
        WHERE store_id = %(store)s
          AND direction = 'TO_CUSTOMER'
          AND (refunded_at AT TIME ZONE %(zone)s)::date = %(business_date)s
    )
    SELECT taken.amount, refunded.amount, abs(taken.amount - refunded.amount),
           CASE WHEN taken.amount >= refunded.amount THEN 'IN' ELSE 'OUT' END
    FROM taken, refunded
"""


@pytest.fixture
def scratch_database() -> Generator[str, None, None]:
    configured = os.environ.get("DATABASE_URL")
    if configured is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
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


def _refund_as_before(connection: Any, order: _Order, at: Any) -> None:
    """What `OrderRepository` wrote for a refunding cancellation before `0067`: the refund row
    (no method -- the column did not exist) and the order's move, in one transaction."""

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO order_refunds (
                id, order_id, store_id, settlement_id, refunded_amount_vnd, direction,
                custody_resolution, attested_by_staff_id, refunded_at, created_at
            )
            SELECT %s, o.id, o.store_id, s.id, s.paid_amount_vnd, 'TO_CUSTOMER',
                   'RETURNED_UNWASHED_REFUNDED', %s, %s, %s
            FROM orders o JOIN order_settlements s ON s.order_id = o.id
            WHERE o.id = %s
            """,
            (uuid4(), order.staff.staff_user_id, at, at, order.order_id),
        )
        cursor.execute(
            """
            UPDATE orders
            SET commercial_status = 'CANCELLED', balance_status = 'REFUNDED',
                row_version = row_version + 1
            WHERE id = %s
            """,
            (order.order_id,),
        )


def test_a_refund_written_before_0067_is_unknown_and_the_drawer_says_it_excludes_it(
    scratch_database: str, migrations_before: Path
) -> None:
    with psycopg.connect(scratch_database, autocommit=True) as connection:
        assert apply_migrations(connection, migrations_before)[-1] < MIGRATION_UNDER_TEST
        store_id = _store(connection)
        staff = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))
        kept = _Order(connection, store_id, staff, created=local(DAY, 9)).activate(local(DAY, 9))
        kept.settle(local(DAY, 10), collected=False)
        refunded = _Order(connection, store_id, staff, created=local(DAY, 9)).activate(
            local(DAY, 9)
        )
        refunded.settle(local(DAY, 10, 30), collected=False)
        refunded.move(local(DAY, 11), commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW)
        _refund_as_before(connection, refunded, local(DAY, 11, 5))

        with connection.cursor() as cursor:
            cursor.execute(
                _COLLECTED_TODAY_V3_TOTALS_SQL,
                {"store": store_id, "zone": "Asia/Ho_Chi_Minh", "business_date": DAY},
            )
            before = cursor.fetchone()
        assert before is not None

        applied = apply_migrations(connection)
        assert applied == (MIGRATION_UNDER_TEST,)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT refund_method FROM order_refunds WHERE order_id = %s",
                (refunded.order_id,),
            )
            assert cursor.fetchall() == [(None,)]
            after = SettlementRepository.collected_today(
                cursor, store_id=store_id, principal=staff, as_of=local(DAY, 20)
            )
        # What v3 published for the day does not move: money in, money back, the all-method net.
        assert (
            after.collected_vnd,
            after.refunded_vnd,
            after.net_vnd,
            after.net_direction,
        ) == tuple(before)
        # The legacy refund is unknown -- not cash -- and the drawer excludes it, saying so.
        assert (after.refunded_unknown_count, after.refunded_unknown_vnd) == (1, after.refunded_vnd)
        assert (after.refunded_cash_count, after.refunded_transfer_count) == (0, 0)
        assert (after.drawer_vnd, after.drawer_direction) == (after.cash_vnd, "IN")

        # After the migration a refunding cancellation records its method; the old writer's row
        # shape is refused by the database.
        kept.move(local(DAY, 12), commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW)
        with pytest.raises(psycopg.errors.RaiseException, match="how the money went back"):
            _refund_as_before(connection, kept, local(DAY, 12, 5))
        kept.move(
            local(DAY, 12, 10),
            commercial_target=CommercialOrderStatus.CANCELLED,
            custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
            refund_method=PaymentMethod.TIEN_MAT,
        )
        with connection.cursor() as cursor:
            later = SettlementRepository.collected_today(
                cursor, store_id=store_id, principal=staff, as_of=local(DAY, 20)
            )
        assert (later.refunded_cash_count, later.refunded_unknown_count) == (1, 1)
        # Cash in 220.000 (both settlements) minus the one cash refund of 110.000; the unknown
        # refund is still excluded, not netted.
        assert (later.drawer_vnd, later.drawer_direction) == (110_000, "IN")
