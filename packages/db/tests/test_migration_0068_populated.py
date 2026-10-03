"""`0068` applied to a database that already holds issued, open and cancelled invoice requests.

`test_migration_populated.py` explains why this shape of test exists. `0068` fixes the figure an
invoice request was issued for (`INVOICE-TRUTH-009`, review M6), and gives every request that was
already ISSUED its snapshot from the value the pre-`0068` read computed at migration time:

* an order: its quote's single total, plus the storage fee the ledger had fixed for it;
* an account month: one line per order charged to the month, with what went on the account --
  which is what the pre-`0068` read and download showed -- so a month issued without a deposit is
  then flagged `AMOUNT_CHANGED_AFTER_ISSUE` with the figure the order really cost (review M5);
* an order already cancelled and refunded when it was issued records that, and is not flagged;
  one cancelled or refunded only *after* its issue records that it was not, and is flagged;
* an open request gets no snapshot, and is issued afterwards by the new code with one.

The requests are issued the way the pre-`0068` repository did -- the row's state only -- because
the current repository also writes the snapshot, whose table does not exist yet at that point.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.migrations import MIGRATIONS_DIRECTORY, apply_migrations
from nha_trang_laundry_db.orders import OrderStateError
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository
from nha_trang_laundry_db.privacy_notice import read_published_privacy_notice
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_domain.accounts import statement_month
from nha_trang_laundry_domain.catalog import CustodyResolution
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import PaymentMethod
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_customer_accounts import Shop
from test_invoice_requests import _issue, _read, _walk_in_order
from test_order_step_repository import TOTAL_VND, _step
from test_order_step_repository import _read as _read_order
from test_unclaimed_laundry import policy_payload

MIGRATION_UNDER_TEST = "0068"
DEPOSIT = 40_000


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


def _pay(shop: Shop, order_id: UUID, amount: int, *, collected: bool = False) -> None:
    PaymentRepository().record(
        shop.connection,
        PaymentCommand(
            order_id=order_id,
            expected_row_version=_read_order(shop.connection, order_id, shop.counter).row_version,
            amount_vnd=amount,
            method=PaymentMethod.TIEN_MAT,
            transfer_seen=False,
            bank_ref_last=None,
            collected_by_customer=collected,
            principal=shop.counter,
            correlation_id=uuid4(),
        ),
    )


def _create_as_before(
    shop: Shop,
    *,
    order_id: UUID | None = None,
    account_id: UUID | None = None,
    month: date | None = None,
) -> UUID:
    """What `create` wrote before `0068`: the row (its cross-kind check read no snapshot)."""

    with shop.connection.cursor() as cursor:
        notice = read_published_privacy_notice(cursor)
        assert notice is not None
        cursor.execute(
            """
            INSERT INTO invoice_requests (
                id, store_id, request_number, subject_kind, order_id, account_id, period_month,
                buyer_unit_name, privacy_notice_version_id, status, requested_by, requested_at,
                row_version
            )
            SELECT %(id)s, %(store)s,
                   (SELECT coalesce(max(request_number), 0) + 1 FROM invoice_requests
                    WHERE store_id = %(store)s),
                   %(kind)s, %(order)s, %(account)s, %(month)s, 'Công ty TNHH Biển Xanh',
                   %(notice)s, 'REQUESTED', %(staff)s, now(), 1
            """,
            {
                "id": (request_id := uuid4()),
                "store": shop.store_id,
                "kind": "ORDER" if order_id is not None else "ACCOUNT_MONTH",
                "order": order_id,
                "account": account_id,
                "month": month,
                "notice": notice.version_id,
                "staff": shop.counter.staff_user_id,
            },
        )
    return request_id


def _issue_as_before(connection: Any, request_id: UUID, number: str, closer: UUID) -> None:
    """What `record_issued` wrote before `0068`: the row's state, and nothing else."""

    connection.execute(
        """
        UPDATE invoice_requests
        SET status = 'ISSUED', invoice_symbol = '1C26TYY', invoice_number = %s,
            invoice_date = current_date - 1, closed_by = %s, closed_at = now(),
            row_version = row_version + 1
        WHERE id = %s
        """,
        (number, closer, request_id),
    )


def _snapshot(connection: Any, request_id: UUID) -> Any:
    return connection.execute(
        "SELECT origin, total_vnd, storage_fee_vnd, order_count FROM invoice_request_snapshots "
        "WHERE request_id = %s",
        (request_id,),
    ).fetchone()


def _lines(connection: Any, request_id: UUID) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT order_id, amount_vnd, cancelled_at_issue, refunded_at_issue "
            "FROM invoice_request_snapshot_orders WHERE request_id = %s ORDER BY position",
            (request_id,),
        ).fetchall()
    ]


def test_0068_fixes_what_each_issued_request_was_issued_for(
    scratch_database: str, migrations_before: Path
) -> None:
    with psycopg.connect(scratch_database, autocommit=True) as connection:
        apply_migrations(connection, migrations_before)
        shop = Shop(connection)
        closer = shop.owner.staff_user_id

        # An order, issued.
        plain = _walk_in_order(shop)
        plain_request = _create_as_before(shop, order_id=plain)
        _issue_as_before(connection, plain_request, "1", closer)

        # An order whose storage fee the ledger fixed when it was paid, issued.
        publish_storage_policy(connection, actor_id=closer, payload=policy_payload())
        homestay = shop.customer()
        shop.open(homestay, 5_000_000)
        fee_order = shop.ready_order(homestay)
        connection.execute(
            "UPDATE orders SET production_ready_at = production_ready_at - interval '25 days', "
            "production_accepted_at = production_accepted_at - interval '25 days', "
            "row_version = row_version + 1 WHERE id = %s",
            (fee_order,),
        )
        owed = _read_order(connection, fee_order, shop.counter).remaining_vnd
        assert owed is not None and owed > TOTAL_VND
        _pay(shop, fee_order, owed, collected=True)
        fee_request = _create_as_before(shop, order_id=fee_order)
        _issue_as_before(connection, fee_request, "2", closer)

        # An order already cancelled and refunded when its invoice was issued.
        refunded = _walk_in_order(shop)
        refunded_request = _create_as_before(shop, order_id=refunded)
        _pay(shop, refunded, TOTAL_VND)
        _step(
            connection,
            refunded,
            shop.counter,
            _read_order(connection, refunded, shop.counter).row_version,
            OrderStep.CANCEL,
            custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
            # GOODS-AND-DRAWER-009: the paid order's money goes back, so the cancel says how.
            refund_method=PaymentMethod.TIEN_MAT,
        )
        _issue_as_before(connection, refunded_request, "3", closer)

        # An account month with a deposit, issued as the old read showed it.
        with_deposit = shop.ready_order(homestay)
        _pay(shop, with_deposit, DEPOSIT)
        shop.charge(with_deposit)
        on_account = shop.ready_order(homestay)
        shop.charge(on_account)
        account_row = connection.execute(
            "SELECT id FROM customer_accounts WHERE customer_id = %s", (homestay,)
        ).fetchone()
        assert account_row is not None
        account_id = account_row[0]
        month_request = _create_as_before(
            shop, account_id=account_id, month=statement_month(datetime.now(UTC))
        )
        _issue_as_before(connection, month_request, "4", closer)

        # An open request.
        open_order = _walk_in_order(shop)
        open_request = _create_as_before(shop, order_id=open_order)

        applied = apply_migrations(connection)
        assert applied == (MIGRATION_UNDER_TEST,) or applied[0] == MIGRATION_UNDER_TEST

        assert _snapshot(connection, plain_request) == (
            "MIGRATION_0068",
            TOTAL_VND,
            None,
            1,
        )
        assert _lines(connection, plain_request) == [(plain, TOTAL_VND, False, False)]
        assert _snapshot(connection, fee_request) == (
            "MIGRATION_0068",
            owed,
            owed - TOTAL_VND,
            1,
        )
        assert _lines(connection, refunded_request) == [(refunded, TOTAL_VND, True, True)]
        assert _snapshot(connection, month_request) == (
            "MIGRATION_0068",
            2 * TOTAL_VND - DEPOSIT,
            None,
            2,
        )
        assert _lines(connection, month_request) == [
            (with_deposit, TOTAL_VND - DEPOSIT, False, False),
            (on_account, TOTAL_VND, False, False),
        ]
        assert _snapshot(connection, open_request) is None

        # The new read: the fixed figures, and the flags that tell the bookkeeper what differs.
        assert _read(shop, plain_request).flags == ()
        fee_view = _read(shop, fee_request)
        assert (fee_view.amount.total_vnd, fee_view.amount.storage_fee_vnd) == (
            owed,
            owed - TOTAL_VND,
        )
        assert fee_view.flags == ()
        assert _read(shop, refunded_request).flags == ()
        month_view = _read(shop, month_request)
        assert month_view.amount.total_vnd == 2 * TOTAL_VND - DEPOSIT
        assert month_view.flags == ("AMOUNT_CHANGED_AFTER_ISSUE",)
        assert month_view.live_total_vnd == 2 * TOTAL_VND

        # The open one is issued by the new code, which writes its snapshot in the same breath.
        _issue(shop, open_request, 1, number="5")
        assert _snapshot(connection, open_request) == ("AT_ISSUE", TOTAL_VND, None, 1)
        assert (datetime.now(UTC) - timedelta(minutes=5)) < _read(
            shop, open_request
        ).amount.fixed_at.astimezone(UTC)


def _cancel(
    shop: Shop,
    order_id: UUID,
    *,
    paid: bool,
    resolution: CustodyResolution = CustodyResolution.RETURNED_UNWASHED_REFUNDED,
) -> None:
    _step(
        shop.connection,
        order_id,
        shop.counter,
        _read_order(shop.connection, order_id, shop.counter).row_version,
        OrderStep.CANCEL,
        custody_resolution=resolution,
        refund_method=PaymentMethod.TIEN_MAT if paid else None,
    )


def test_0068_does_not_record_a_cancellation_or_refund_after_the_issue_as_at_the_issue(
    scratch_database: str, migrations_before: Path
) -> None:
    """Pre-production review 9: the backfill read "cancelled/refunded at issue" from the state at
    migration time, so an order cancelled or refunded *after* its invoice was issued -- the case
    review M6 exists to surface -- was recorded as already so at the issue, and
    `CANCELLED_AFTER_ISSUE` / `REFUNDED_AFTER_ISSUE` could never be raised for it. The backfill now
    compares the refund's and the cancellation's own times with the request's `closed_at`.

    Matrix for an order request: {paid, unpaid} x {cancelled before the issue, after it}. An
    account month's order cannot be cancelled today, which the test pins.
    """

    with psycopg.connect(scratch_database, autocommit=True) as connection:
        apply_migrations(connection, migrations_before)
        shop = Shop(connection)
        closer = shop.owner.staff_user_id

        # Order requests.
        before = _walk_in_order(shop)
        before_request = _create_as_before(shop, order_id=before)
        _pay(shop, before, TOTAL_VND)
        _cancel(shop, before, paid=True)
        _issue_as_before(connection, before_request, "1", closer)

        refunded_after = _walk_in_order(shop)
        refunded_after_request = _create_as_before(shop, order_id=refunded_after)
        _pay(shop, refunded_after, TOTAL_VND)
        _issue_as_before(connection, refunded_after_request, "2", closer)
        _cancel(shop, refunded_after, paid=True)

        cancelled_before = _walk_in_order(shop)
        cancelled_before_request = _create_as_before(shop, order_id=cancelled_before)
        _cancel(shop, cancelled_before, paid=False)
        _issue_as_before(connection, cancelled_before_request, "4", closer)

        cancelled_after = _walk_in_order(shop)
        cancelled_after_request = _create_as_before(shop, order_id=cancelled_after)
        _issue_as_before(connection, cancelled_after_request, "3", closer)
        _cancel(shop, cancelled_after, paid=False)

        # Account months: an order on the account cannot be cancelled at all today (`ON_ACCOUNT` is
        # not a cancellable balance), so neither month cell can arise. The backfill compares times
        # there too; this pins why the month half of the matrix has no fixture.
        customer = shop.customer()
        shop.open(customer, 5_000_000)
        on_account = shop.ready_order(customer)
        _pay(shop, on_account, DEPOSIT)
        shop.charge(on_account)
        with pytest.raises(OrderStateError, match=r"^NOT_SUPPORTED"):
            _cancel(shop, on_account, paid=True, resolution=CustodyResolution.SHOP_FAULT_NO_CHARGE)

        apply_migrations(connection)

        assert _lines(connection, before_request) == [(before, TOTAL_VND, True, True)]
        assert _read(shop, before_request).flags == ()
        assert _lines(connection, refunded_after_request) == [
            (refunded_after, TOTAL_VND, False, False)
        ]
        assert _read(shop, refunded_after_request).flags == ("REFUNDED_AFTER_ISSUE",)
        assert _lines(connection, cancelled_after_request) == [
            (cancelled_after, TOTAL_VND, False, False)
        ]
        assert _read(shop, cancelled_after_request).flags == ("CANCELLED_AFTER_ISSUE",)
        assert _lines(connection, cancelled_before_request) == [
            (cancelled_before, TOTAL_VND, True, False)
        ]
        assert _read(shop, cancelled_before_request).flags == ()
