"""GOODS-AND-DRAWER-009 (review M4) against real PostgreSQL: "tiền trong két" is the drawer.

The review: `collected-today-v3` netted every refund against money in by every method, and the
Today screen called the result "Tiền trong két" -- 500.000 d cash + 2.000.000 d transfer - 100.000 d
refund read "tăng 2.400.000 d" while the drawer had moved about 400.000 d. The drawer is cash taken
minus cash handed back, which needs each refund's method (`0067`); a refund written before `0067`
has none, and is counted apart -- the drawer figure says it excludes them, it never guesses.

One shop-local day, driven through the real payment, step and export commands:

    a  110.000 cash, paid in full                 -> cash in
    b  110.000 transfer, paid in full             -> transfer in
    c   60.000 cash deposit, cancelled, refunded in CASH
    d  110.000 transfer, cancelled, refunded by TRANSFER
    e   40.000 cash deposit, cancelled, refunded before 0067 recorded how (method unknown)

    money in 430.000 (cash 210.000, transfer 220.000); money back 210.000 (cash 60.000, transfer
    110.000, unknown 40.000); every method in minus every refund: +220.000 (what v3 called the
    drawer); the drawer: 210.000 - 60.000 = +150.000, excluding 1 refund of 40.000.

The Today figure, the report's day, the evening summary and the export must all say exactly that.
Then the rule that makes it possible: a refunding cancellation says how the money went back, or is
refused by name and writes nothing -- on the step route and on the per-axis route -- and the
database refuses a refund row without it.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.daily_summary import DailySummaryRepository
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    OrderRepository,
    OrderStateError,
    OrderStepCommand,
    OrderStepRequiresHuman,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.reports import ReportKey, ReportRepository
from nha_trang_laundry_db.settlement import COLLECTED_TODAY_QUERY, SettlementRepository
from nha_trang_laundry_domain.catalog import CommercialOrderStatus, CustodyResolution
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import PaymentMethod
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from test_export_payments import CASH, TRANSFER, _body, _pay, _received, _release
from test_export_range import _request
from test_order_step_repository import _read
from test_order_step_repository import _staff as _operator
from test_sanitized_export import _approve, _local_date, _Shop

RESOLUTION = CustodyResolution.RETURNED_UNWASHED_REFUNDED


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url, autocommit=True) as established:
        apply_migrations(established)
        yield established


def _cancel(
    connection: Any,
    order_id: UUID,
    staff: Any,
    method: PaymentMethod | None,
    *,
    key: str | None = None,
    version: int | None = None,
) -> Any:
    """`CANCEL` as the counter sends it; `version` is the If-Match (default: the order as read)."""

    return OrderRepository().execute_step(
        connection,
        OrderStepCommand(
            order_id=order_id,
            expected_row_version=version or _read(connection, order_id, staff).row_version,
            principal=staff,
            idempotency_key=key or f"cancel-{uuid4().hex}",
            correlation_id=uuid4(),
            step=OrderStep.CANCEL,
            custody_resolution=RESOLUTION,
            refund_method=method,
        ),
    )


def _forget_method(connection: Any, order_id: UUID) -> None:
    """A refund as every refund before `0067` was written: no method.

    `order_refunds` is append-only and `0067` refuses a new row without a method, so the only way to
    hold such a row in a test is the one `test_reports.py` uses for `orders.created_at`: inside one
    transaction that re-arms the guard before it commits.
    """

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("ALTER TABLE order_refunds DISABLE TRIGGER order_refunds_append_only")
        cursor.execute(
            "UPDATE order_refunds SET refund_method = NULL WHERE order_id = %s", (order_id,)
        )
        cursor.execute("ALTER TABLE order_refunds ENABLE TRIGGER order_refunds_append_only")


def _the_day(connection: Any) -> tuple[_Shop, Any, dict[str, UUID]]:
    shop = _Shop(connection, datetime.now(UTC))
    staff = _operator(connection, shop.store_id)
    orders = {name: _received(connection, shop, staff) for name in "abcde"}
    _pay(connection, orders["a"], staff, 110_000, CASH)
    _pay(connection, orders["b"], staff, 110_000, TRANSFER)
    _pay(connection, orders["c"], staff, 60_000, CASH)
    _cancel(connection, orders["c"], staff, PaymentMethod.TIEN_MAT)
    _pay(connection, orders["d"], staff, 110_000, TRANSFER)
    _cancel(connection, orders["d"], staff, PaymentMethod.CHUYEN_KHOAN)
    _pay(connection, orders["e"], staff, 40_000, CASH)
    _cancel(connection, orders["e"], staff, PaymentMethod.TIEN_MAT)
    _forget_method(connection, orders["e"])
    return shop, staff, orders


def test_the_drawer_is_cash_in_minus_cash_back_and_every_reader_agrees(connection: Any) -> None:
    shop, staff, orders = _the_day(connection)
    now = datetime.now(UTC)

    # --- Today (`collected-today-v4`) -------------------------------------------------------
    with connection.cursor() as cursor:
        today = SettlementRepository.collected_today(
            cursor, store_id=shop.store_id, principal=staff, as_of=now
        )
    assert COLLECTED_TODAY_QUERY.label.startswith("collected-today-v4:")
    assert (today.collected_vnd, today.cash_vnd, today.transfer_vnd) == (
        430_000,
        210_000,
        220_000,
    )
    assert (today.refunded_vnd, today.refund_count) == (210_000, 3)
    assert (today.refunded_cash_vnd, today.refunded_cash_count) == (60_000, 1)
    assert (today.refunded_transfer_vnd, today.refunded_transfer_count) == (110_000, 1)
    assert (today.refunded_unknown_vnd, today.refunded_unknown_count) == (40_000, 1)
    # Every method in minus every refund: what v3 put under "Tiền trong két".
    assert (today.net_vnd, today.net_direction) == (220_000, "IN")
    # The drawer: cash in minus cash back; the unknown refund is excluded, not netted.
    assert (today.drawer_vnd, today.drawer_direction) == (150_000, "IN")

    # --- the report's day (`report-v5`) -----------------------------------------------------
    day = _local_date(now)
    with connection.cursor() as cursor:
        report = ReportRepository.store_report(
            cursor,
            store_id=shop.store_id,
            principal=shop.owner,
            policy=STANDARD_WASH_SLA,
            from_date=day,
            to_date=day,
            as_of=now,
        )
    assert report.query_version.startswith("report-v5:")
    figures = {figure.key: figure for figure in report.summary.figures}
    drawer = figures[ReportKey.MONEY_DRAWER]
    assert (drawer.numerator, drawer.direction) == (today.drawer_vnd, today.drawer_direction)
    assert drawer.by_kind == (
        ("CASH_IN", 3, 210_000),
        ("CASH_REFUNDED", 1, 60_000),
        ("EXCLUDED_UNKNOWN_REFUNDS", 1, 40_000),
    )
    assert figures[ReportKey.MONEY_REFUNDED].by_kind == (
        ("TIEN_MAT", 1, 60_000),
        ("CHUYEN_KHOAN", 1, 110_000),
        ("UNKNOWN", 1, 40_000),
    )
    net = figures[ReportKey.MONEY_NET]
    assert (net.numerator, net.direction) == (today.net_vnd, today.net_direction)
    (only_day,) = report.days
    day_figures = {figure.key: figure for figure in only_day.figures}
    assert day_figures[ReportKey.MONEY_DRAWER] == drawer

    # --- the evening summary (`daily-summary-v4`) -------------------------------------------
    summary = DailySummaryRepository.read(
        connection,
        store_id=shop.store_id,
        principal=shop.owner,
        policy=STANDARD_WASH_SLA,
        day=day,
        as_of=now,
    )
    money = next(line for line in summary.rendered.lines if line.key.value == "MONEY")
    values = dict(money.figures)
    assert (values["drawer_vnd"], values["drawer_direction"]) == (150_000, "IN")
    assert (values["refunded_unknown_vnd"], values["refunded_unknown_entries"]) == (40_000, 1)
    assert "Thu trừ hoàn còn 220.000đ." in money.text
    assert "Tiền mặt trong két tăng 150.000đ." in money.text
    assert "chưa tính 1 khoản hoàn chưa rõ cách hoàn (40.000đ)" in money.text
    assert "Hoàn tiền mặt 60.000đ." in money.text and "Hoàn chuyển khoản 110.000đ." in money.text

    # --- the export: each refund's method on its row, the unknown one empty -----------------
    created = _request(connection, shop, day, None)
    produced = _release(connection, shop, created, _approve(connection, shop, created))
    rows = _body(produced.content_csv)
    assert {name: rows[str(order)]["refund_method"] for name, order in orders.items()} == {
        "a": "",
        "b": "",
        "c": "TIEN_MAT",
        "d": "CHUYEN_KHOAN",
        "e": "",
    }
    cash_back = [row for row in rows.values() if row["refund_method"] == "TIEN_MAT"]
    unknown_back = [
        row for row in rows.values() if row["refunded_amount_vnd"] and not row["refund_method"]
    ]
    assert [int(row["refunded_amount_vnd"]) for row in cash_back] == [today.refunded_cash_vnd]
    assert [int(row["refunded_amount_vnd"]) for row in unknown_back] == [today.refunded_unknown_vnd]


def test_a_transfer_paid_back_in_cash_empties_the_drawer_while_the_net_says_no_change(
    connection: Any,
) -> None:
    """The divergence in its smallest form: nothing moved overall, the drawer lost 110.000."""

    shop = _Shop(connection, datetime.now(UTC))
    staff = _operator(connection, shop.store_id)
    order = _received(connection, shop, staff)
    _pay(connection, order, staff, 110_000, TRANSFER)
    _cancel(connection, order, staff, PaymentMethod.TIEN_MAT)
    with connection.cursor() as cursor:
        today = SettlementRepository.collected_today(
            cursor, store_id=shop.store_id, principal=staff, as_of=datetime.now(UTC)
        )
    assert (today.net_vnd, today.net_direction) == (0, "IN")
    assert (today.drawer_vnd, today.drawer_direction) == (110_000, "OUT")
    assert today.refunded_unknown_count == 0


def test_a_refunding_cancellation_must_say_how_the_money_went_back(connection: Any) -> None:
    shop = _Shop(connection, datetime.now(UTC))
    staff = _operator(connection, shop.store_id)
    order = _received(connection, shop, staff)
    _pay(connection, order, staff, 50_000, CASH)
    view = _read(connection, order, staff)
    cancel = next(item for item in view.next_steps if item.step is OrderStep.CANCEL)
    assert cancel.requires == ("custody_resolution", "refund_method")

    with pytest.raises(OrderStepRequiresHuman) as refused:
        _cancel(connection, order, staff, None)
    assert refused.value.reason_codes == ("REFUND_METHOD_REQUIRED",)
    after = _read(connection, order, staff)
    assert (after.row_version, after.commercial, after.balance) == (
        view.row_version,
        "ACTIVE",
        "PARTIALLY_PAID",
    )

    key = f"cancel-{uuid4().hex}"
    first = view.row_version
    done = _cancel(connection, order, staff, PaymentMethod.CHUYEN_KHOAN, key=key, version=first)
    assert (done.view.commercial, done.view.balance) == ("CANCELLED", "REFUNDED")
    # Same key, same words: the first answer. Same key, the other method: a conflict.
    again = _cancel(connection, order, staff, PaymentMethod.CHUYEN_KHOAN, key=key, version=first)
    assert again.replayed and again.view == done.view
    with pytest.raises(IdempotencyConflictError):
        _cancel(connection, order, staff, PaymentMethod.TIEN_MAT, key=key, version=first)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.refund_method, r.refunded_amount_vnd,
                   (SELECT e.payload->'refund'->>'refund_method' FROM domain_events e
                    WHERE e.aggregate_id = r.order_id AND e.payload ? 'refund'),
                   (SELECT a.details->'refund'->>'refund_method' FROM audit_events a
                    WHERE a.aggregate_id = r.order_id AND a.details ? 'refund'),
                   (SELECT x.payload->>'refund_method' FROM outbox_events x
                    WHERE x.event_type = 'order.refund_recorded.v1'
                      AND x.payload->>'order_id' = r.order_id::text)
            FROM order_refunds r WHERE r.order_id = %s
            """,
            (order,),
        )
        assert cursor.fetchone() == (
            "CHUYEN_KHOAN",
            50_000,
            "CHUYEN_KHOAN",
            "CHUYEN_KHOAN",
            "CHUYEN_KHOAN",
        )


def test_a_method_on_a_cancellation_that_hands_nothing_back_is_refused(connection: Any) -> None:
    shop = _Shop(connection, datetime.now(UTC))
    staff = _operator(connection, shop.store_id)
    order = _received(connection, shop, staff)
    view = _read(connection, order, staff)
    cancel = next(item for item in view.next_steps if item.step is OrderStep.CANCEL)
    assert "refund_method" not in cancel.requires
    with pytest.raises(OrderStateError, match="refund_method is taken only by"):
        _cancel(connection, order, staff, PaymentMethod.TIEN_MAT)
    assert _read(connection, order, staff).row_version == view.row_version


def test_the_per_axis_cancellation_asks_the_same_question(connection: Any) -> None:
    shop = _Shop(connection, datetime.now(UTC))
    staff = _operator(connection, shop.store_id)
    order = _received(connection, shop, staff)
    _pay(connection, order, staff, 110_000, CASH)
    version = _read(connection, order, staff).row_version
    version = (
        OrderRepository()
        .transition(
            connection,
            OrderTransitionCommand(
                order,
                version,
                staff,
                f"review-{uuid4().hex}",
                uuid4(),
                commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW,
            ),
        )
        .row_version
    )

    def cancel(method: PaymentMethod | None) -> Any:
        return OrderRepository().transition(
            connection,
            OrderTransitionCommand(
                order,
                version,
                staff,
                f"cancel-{uuid4().hex}",
                uuid4(),
                commercial_target=CommercialOrderStatus.CANCELLED,
                custody_resolution=RESOLUTION,
                refund_method=method,
            ),
        )

    with pytest.raises(OrderStepRequiresHuman) as refused:
        cancel(None)
    assert refused.value.reason_codes == ("REFUND_METHOD_REQUIRED",)
    assert _read(connection, order, staff).row_version == version
    assert cancel(PaymentMethod.TIEN_MAT).balance.value == "REFUNDED"


def test_the_database_refuses_a_refund_that_does_not_say_how(connection: Any) -> None:
    """`0067`: whatever path writes a refund, it records the method or it is not written."""

    shop = _Shop(connection, datetime.now(UTC))
    staff = _operator(connection, shop.store_id)
    order = _received(connection, shop, staff)
    _pay(connection, order, staff, 20_000, CASH)
    with (
        pytest.raises(psycopg.errors.RaiseException, match="how the money went back"),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO order_refunds (
                id, order_id, store_id, settlement_id, refunded_amount_vnd, direction,
                custody_resolution, attested_by_staff_id, refunded_at, created_at
            ) VALUES (%s, %s, %s, NULL, 20000, 'TO_CUSTOMER', 'RETURNED_UNWASHED_REFUNDED',
                      %s, now(), now())
            """,
            (uuid4(), order, shop.store_id, staff.staff_user_id),
        )
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO order_refunds (
                id, order_id, store_id, settlement_id, refunded_amount_vnd, direction,
                custody_resolution, attested_by_staff_id, refunded_at, created_at, refund_method
            ) VALUES (%s, %s, %s, NULL, 20000, 'TO_CUSTOMER', 'RETURNED_UNWASHED_REFUNDED',
                      %s, now(), now(), 'THE_BANK_OF_MUM')
            """,
            (uuid4(), order, shop.store_id, staff.staff_user_id),
        )
