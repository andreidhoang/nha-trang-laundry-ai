"""`0056` applied to a database that already holds paid, prepaid, refunded and unpaid orders.

`test_migration_populated.py` explains why this shape of test exists. `0056` adds the payment ledger
and backfills it: every settlement written before it becomes the one payment it was, `TIEN_MAT`,
marked `legacy`, dated by its attestation and attributed to its staff member. The questions that
matter are about what was written before the ledger existed:

* every order's sum is preserved -- its payments sum to exactly what it settled;
* every day's takings are preserved -- `collected-today-v3` over the ledger reads, for every day,
  exactly what `collected-today-v2` read over the settlements (the v2 statement is kept below,
  verbatim, as the reference);
* the orders keep working under the new code: an unpaid order takes a deposit and the rest, a
  prepaid order is picked up and closed, and a refunded order's refund still equals its ledger.

The settlements are written the way the pre-`0056` repository wrote them -- the settlement row and
the order's balance, and nothing else -- because the current repository also writes the ledger,
which does not exist yet at that point.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_db.migrations import MIGRATIONS_DIRECTORY, apply_migrations
from nha_trang_laundry_db.orders import OrderRepository
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository
from nha_trang_laundry_db.settlement import (
    BUSINESS_TIMEZONE,
    CollectionCommand,
    SettlementRepository,
)
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    CustodyResolution,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import PaymentMethod
from nha_trang_laundry_domain.settlement import SettlementShape
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_reports import DAY, NEXT, QUOTED_TOTAL, _Order, _person, _store, local

MIGRATION_UNDER_TEST = "0056"

#: `collected-today-v2`, verbatim as `settlement.py` published it before `PAYMENT-001`: the figure
#: every past day's card showed. v3 must read the same for every day that existed before `0056`.
_COLLECTED_TODAY_V2_SQL = """
    WITH settled AS (
        SELECT coalesce(sum(paid_amount_vnd), 0) AS amount, count(*) AS entries
        FROM order_settlements
        WHERE store_id = %(store)s
          AND (attested_at AT TIME ZONE %(zone)s)::date = %(business_date)s
    ), refunded AS (
        SELECT coalesce(sum(refunded_amount_vnd), 0) AS amount, count(*) AS entries
        FROM order_refunds
        WHERE store_id = %(store)s
          AND direction = 'TO_CUSTOMER'
          AND (refunded_at AT TIME ZONE %(zone)s)::date = %(business_date)s
    )
    SELECT settled.amount, settled.entries, refunded.amount, refunded.entries,
           abs(settled.amount - refunded.amount),
           CASE WHEN settled.amount >= refunded.amount THEN 'IN' ELSE 'OUT' END
    FROM settled, refunded
"""

_COLLECTED_BY = {
    SettlementShape.EXACT_PAYMENT_SELF_COLLECTION: "CUSTOMER",
    SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY: "PENDING_DELIVERY",
    SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION: "PENDING_COLLECTION",
}


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


def _settle_as_before(connection: Any, order: _Order, shape: SettlementShape, at: datetime) -> UUID:
    """What `SettlementRepository.record` wrote before `0056`: the row and the balance only."""

    settlement_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT o.store_id, o.current_quote_id, o.current_quote_revision,
                   o.current_quote_snapshot_hash, o.row_version
            FROM orders o WHERE o.id = %s FOR UPDATE
            """,
            (order.order_id,),
        )
        store_id, quote_id, revision, snapshot, version = cursor.fetchone()
        cursor.execute(
            """
            INSERT INTO order_settlements (
                id, order_id, store_id, settled_quote_id, settled_quote_revision,
                settled_quote_snapshot_hash, expected_total_vnd, paid_amount_vnd,
                settlement_shape, collected_by, attested_by_staff_id, attested_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                settlement_id,
                order.order_id,
                store_id,
                quote_id,
                revision,
                snapshot,
                QUOTED_TOTAL,
                QUOTED_TOTAL,
                shape.value,
                _COLLECTED_BY[shape],
                order.staff.staff_user_id,
                at,
                at,
            ),
        )
        cursor.execute(
            """
            UPDATE orders SET balance_status = 'PAID', self_collection_recorded = %s,
                              row_version = row_version + 1
            WHERE id = %s AND row_version = %s
            """,
            (shape is SettlementShape.EXACT_PAYMENT_SELF_COLLECTION, order.order_id, version),
        )
    order.version = int(version) + 1
    return settlement_id


def _v2_takings(connection: Any, store_id: UUID, day: date) -> tuple[Any, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            _COLLECTED_TODAY_V2_SQL,
            {"store": store_id, "zone": BUSINESS_TIMEZONE, "business_date": day},
        )
        return tuple(cursor.fetchone())


def _rows(connection: Any, statement: str, *params: object) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        return [tuple(row) for row in cursor.fetchall()]


def test_every_settlement_before_0056_becomes_its_one_payment_and_no_day_moves(
    scratch_database: str, migrations_before: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    P = ProductionStatus
    with psycopg.connect(scratch_database) as connection:
        apply_migrations(connection, migrations_before)
        # The production moves are written the way the pre-`0056` repository wrote them, too: the
        # move and nothing else. Today's `transition` also opens and closes a wash cycle
        # (`SHOP-CAPTURE-001`), in a table `0058` creates -- later than the database this test
        # populates -- so, as `_settle_as_before` does for the ledger, that one hook is left out
        # while the old data is written, and restored before the migration is applied.
        monkeypatch.setattr(
            "nha_trang_laundry_db.orders.apply_cycle_effect", lambda *args, **kwargs: None
        )
        store_id = _store(connection)
        staff = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))
        owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))

        def active(at: datetime) -> _Order:
            return _Order(connection, store_id, staff, created=at).activate(at)

        # A walk-in paid and collected at pickup on DAY.
        collected = active(local(DAY, 8)).produce(
            (P.QUEUED, local(DAY, 9)),
            (P.IN_PROCESS, local(DAY, 9)),
            (P.QUALITY_CHECK, local(DAY, 10)),
            (P.READY_AT_STORE, local(DAY, 11)),
        )
        collected_settlement = _settle_as_before(
            connection, collected, SettlementShape.EXACT_PAYMENT_SELF_COLLECTION, local(DAY, 17)
        )
        # A walk-in prepaid at drop-off on DAY, laundry finished, not yet picked up.
        prepaid = active(local(DAY, 9))
        _settle_as_before(
            connection,
            prepaid,
            SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
            local(DAY, 9, 5),
        )
        prepaid.produce(
            (P.QUEUED, local(DAY, 10)),
            (P.IN_PROCESS, local(DAY, 10)),
            (P.QUALITY_CHECK, local(DAY, 12)),
            (P.READY_AT_STORE, local(DAY, 13)),
        )
        # Prepaid on DAY, cancelled unwashed and refunded on NEXT (DEC-024).
        refunded = active(local(DAY, 15))
        _settle_as_before(
            connection,
            refunded,
            SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
            local(DAY, 15, 5),
        )
        refunded.move(local(NEXT, 10), commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW)
        refunded.move(
            local(NEXT, 10, 5),
            commercial_target=CommercialOrderStatus.CANCELLED,
            custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        )
        # Unpaid and running.
        unpaid = active(local(NEXT, 8))
        connection.commit()

        before = {day: _v2_takings(connection, store_id, day) for day in (DAY, NEXT)}
        settled = dict(
            _rows(
                connection,
                "SELECT order_id, paid_amount_vnd FROM order_settlements WHERE store_id = %s",
                store_id,
            )
        )
        assert len(settled) == 3

        monkeypatch.undo()
        applied = apply_migrations(connection)
        assert MIGRATION_UNDER_TEST in applied
        connection.commit()

        # Every settlement is its one payment: same order, amount, moment and staff member.
        backfilled = _rows(
            connection,
            """
            SELECT p.order_id, p.amount_vnd, p.method, p.legacy, p.bank_ref_last,
                   p.settlement_id = s.id, p.recorded_at = s.attested_at,
                   p.recorded_by_staff_id = s.attested_by_staff_id
            FROM order_payments p JOIN order_settlements s ON s.order_id = p.order_id
            WHERE p.store_id = %s
            """,
            store_id,
        )
        assert sorted((row[0], row[1]) for row in backfilled) == sorted(settled.items())
        assert all(row[2:] == ("TIEN_MAT", True, None, True, True, True) for row in backfilled)
        # The identifiers are derived, so the backfill is the same on every database.
        assert _rows(
            connection,
            "SELECT id = md5('order-payment-legacy:' || %s::text)::uuid FROM order_payments "
            "WHERE settlement_id = %s",
            str(collected_settlement),
            collected_settlement,
        ) == [(True,)]

        # Every order's sum is preserved, and each read says so.
        for order, paid, remaining in (
            (collected, QUOTED_TOTAL, 0),
            (prepaid, QUOTED_TOTAL, 0),
            (refunded, QUOTED_TOTAL, 0),
            (unpaid, 0, QUOTED_TOTAL),
        ):
            with connection.cursor() as cursor:
                view = OrderRepository.read_for_principal(
                    cursor, order_id=order.order_id, principal=staff
                )
            assert (view.paid_vnd, view.remaining_vnd) == (paid, remaining), order.order_id
            assert len(view.payments) == (1 if paid else 0)

        # No day's takings moved: v3 over the ledger reads what v2 read over the settlements.
        for day, (amount, entries, refund, refunds, net, direction) in before.items():
            with connection.cursor() as cursor:
                after = SettlementRepository.collected_today(
                    cursor, store_id=store_id, principal=staff, as_of=local(day, 12)
                )
            assert (after.collected_vnd, after.settlement_count) == (amount, entries)
            assert (after.refunded_vnd, after.refund_count) == (refund, refunds)
            assert (after.net_vnd, after.net_direction) == (net, direction)
            assert (after.cash_vnd, after.transfer_vnd) == (amount, 0)
            assert after.payment_count == entries

        # And the orders keep working under the new code.
        with connection.cursor() as cursor:
            prepaid_view = OrderRepository.read_for_principal(
                cursor, order_id=prepaid.order_id, principal=staff
            )
        assert next(s.step for s in prepaid_view.next_steps if s.primary) is OrderStep.COLLECT
        SettlementRepository().record_collection(
            connection,
            CollectionCommand(prepaid.order_id, prepaid_view.row_version, staff, uuid4()),
        )
        connection.commit()

        with connection.cursor() as cursor:
            version = OrderRepository.read_for_principal(
                cursor, order_id=unpaid.order_id, principal=staff
            ).row_version
        deposit = PaymentRepository().record(
            connection,
            PaymentCommand(
                unpaid.order_id,
                version,
                50_000,
                PaymentMethod.CHUYEN_KHOAN,
                True,
                None,
                False,
                staff,
                uuid4(),
            ),
        )
        rest = PaymentRepository().record(
            connection,
            PaymentCommand(
                unpaid.order_id,
                deposit.row_version,
                QUOTED_TOTAL - 50_000,
                PaymentMethod.TIEN_MAT,
                False,
                None,
                False,
                staff,
                uuid4(),
            ),
        )
        connection.commit()
        assert (deposit.balance_status, rest.balance_status) == ("PARTIALLY_PAID", "PAID")

        balances = dict(
            _rows(connection, "SELECT id, balance_status FROM orders WHERE store_id = %s", store_id)
        )
        assert balances[refunded.order_id] == OrderBalanceStatus.REFUNDED.value
        assert owner  # the store's owner exists so membership reads resolve as in production
