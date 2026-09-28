"""`SUMMARY-ATTENTION-001` (`DEC-044`): the *Cần chú ý* block's reads, against PostgreSQL.

Each read is another module's figure, counted or compared here and printed by the template:

* the day against the same weekday of the previous four weeks, by the report's own one-day
  figures, only after closing time on the day itself and only with three weeks of trade;
* last month's core cost categories with no Sổ thu chi line, after the 10th, for a shop that
  traded last month -- the categories the month margin refuses on;
* laundry whose free-storage days run out within three days, by the waiting list's own count under
  the published storage policy.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from nha_trang_laundry_db.daily_summary import (
    COMPARE_FROM_LOCAL,
    day_comparison,
    fee_soon_figures,
    missing_costs,
)
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_db.reports import ReportRepository
from nha_trang_laundry_db.shop_capture import ExpenseRepository, RecordExpenseCommand
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_domain.daily_summary import (
    DayComparison,
    Direction,
    FeeSoon,
    MissingCosts,
    OmissionReason,
    Unavailable,
)
from nha_trang_laundry_domain.shop_capture import ExpenseCategory
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from nha_trang_laundry_domain.unclaimed import withdrawal_document
from test_reports import _Order, _person, _store
from test_reports import connection as connection
from test_unclaimed_laundry import Shop, _age, _publish, _ready

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")

#: A closed day far enough back that its four previous weeks are inside the report's window rule.
TODAY_UTC = datetime.now(UTC).replace(microsecond=0)


def _local(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour, 0), ZONE)


def _compare(
    connection: Any, store_id: Any, owner: Any, day: date, *, live: bool, as_of: datetime
) -> DayComparison | Unavailable:
    connection.commit()
    with connection.cursor() as cursor:
        report = ReportRepository.store_report(
            cursor,
            store_id=store_id,
            principal=owner,
            policy=STANDARD_WASH_SLA,
            from_date=day,
            to_date=day,
            as_of=as_of,
        )
        return day_comparison(
            cursor,
            store_id=store_id,
            principal=owner,
            policy=STANDARD_WASH_SLA,
            day=day,
            live=live,
            as_of=as_of,
            today=report,
        )


def _orders_on(connection: Any, store_id: Any, staff: Any, day: date, count: int) -> None:
    for index in range(count):
        _Order(connection, store_id, staff, created=_local(day, 9 + index))


def test_the_day_is_compared_with_the_weeks_that_traded_and_a_quiet_one_is_not_a_zero(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = _store(connection)
    owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
    staff = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))
    day = (TODAY_UTC.astimezone(ZONE) - timedelta(days=2)).date()
    # Three previous same weekdays traded (4, 3 and 2 orders); the fourth the shop was closed.
    for weeks, count in ((1, 4), (2, 3), (3, 2)):
        _orders_on(connection, store_id, staff, day - timedelta(days=7 * weeks), count)
    _orders_on(connection, store_id, staff, day, 1)

    compared = _compare(connection, store_id, owner, day, live=False, as_of=TODAY_UTC)

    assert isinstance(compared, DayComparison)
    assert compared.weeks_with_data == 3
    # (4 + 3 + 2) / 3 = 3; one order is below 70% of it. No money moved on any of the days.
    assert (compared.orders.today, compared.orders.usual) == (1, 3)
    assert compared.orders.direction is Direction.LOW
    assert (compared.collected.today, compared.collected.usual) == (0, 0)
    assert compared.collected.direction is Direction.USUAL


def test_two_weeks_of_trade_are_too_little_to_call_anything_usual(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = _store(connection)
    owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
    staff = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))
    day = (TODAY_UTC.astimezone(ZONE) - timedelta(days=2)).date()
    for weeks in (1, 2):
        _orders_on(connection, store_id, staff, day - timedelta(days=7 * weeks), 5)

    compared = _compare(connection, store_id, owner, day, live=False, as_of=TODAY_UTC)

    assert compared == Unavailable(OmissionReason.TOO_LITTLE_HISTORY, "REPORT")


def test_today_is_compared_only_from_closing_time(connection: psycopg.Connection[Any]) -> None:
    store_id = _store(connection)
    owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
    today = (TODAY_UTC.astimezone(ZONE) - timedelta(days=1)).date()
    before_close = datetime.combine(today, COMPARE_FROM_LOCAL, ZONE) - timedelta(minutes=1)

    compared = _compare(connection, store_id, owner, today, live=True, as_of=before_close)

    assert compared == Unavailable(OmissionReason.DAY_NOT_OVER, "REPORT")


def _expense(connection: Any, store_id: Any, owner: Any, category: ExpenseCategory) -> None:
    ExpenseRepository().record(
        connection,
        RecordExpenseCommand(
            store_id=store_id,
            spent_on=date(2026, 8, 28),
            category=category,
            amount_vnd=100_000,
            note=None,
            principal=owner,
            idempotency_key=f"expense-{uuid4().hex}",
            correlation_id=uuid4(),
            today=date(2026, 8, 31),
        ),
    )


def test_last_months_missing_costs_are_asked_for_after_the_tenth_of_a_trading_shop(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = _store(connection)
    owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
    staff = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))

    with connection.cursor() as cursor:
        # The shop took no order in August: it has no last month to complete.
        assert missing_costs(cursor, store_id=store_id, day=date(2026, 9, 15)) is None

    _Order(connection, store_id, staff, created=_local(date(2026, 8, 20), 10))
    _expense(connection, store_id, owner, ExpenseCategory.DIEN)
    _expense(connection, store_id, owner, ExpenseCategory.NUOC)
    _expense(connection, store_id, owner, ExpenseCategory.TUI_NHAN)
    connection.commit()

    with connection.cursor() as cursor:
        # Up to the 10th the month's bills may still be arriving.
        assert missing_costs(cursor, store_id=store_id, day=date(2026, 9, 10)) is None
        found = missing_costs(cursor, store_id=store_id, day=date(2026, 9, 11))
    assert found == MissingCosts(
        month=date(2026, 8, 1), categories=("HOA_CHAT", "LUONG", "MAT_BANG")
    )

    for category in (ExpenseCategory.HOA_CHAT, ExpenseCategory.LUONG, ExpenseCategory.MAT_BANG):
        _expense(connection, store_id, owner, category)
    connection.commit()
    with connection.cursor() as cursor:
        assert missing_costs(cursor, store_id=store_id, day=date(2026, 9, 11)) is None


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Generator[Shop, None, None]:
    """As `test_unclaimed_laundry`'s: no storage policy in force before or after the test."""

    made = Shop(connection)
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )
    yield made
    connection.rollback()
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )


def _fee_soon(connection: Any, shop: Shop, *, live: bool = True) -> FeeSoon | Unavailable:
    connection.commit()
    with connection.cursor() as cursor:
        return fee_soon_figures(
            cursor,
            store_id=shop.store_id,
            principal=shop.owner,
            live=live,
            as_of=datetime.now(UTC),
        )


def test_laundry_whose_free_days_run_out_within_three_days_is_counted(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    ages = (16, 17, 18, 20, 21)
    for days in ages:
        _age(connection, _ready(connection, shop), days)

    assert _fee_soon(connection, shop) == Unavailable(
        OmissionReason.STORAGE_POLICY_UNPUBLISHED, "UNCLAIMED-001"
    )

    _publish(connection, shop)
    soon = _fee_soon(connection, shop)
    # Free through day 20: days 18, 19 and 20 are the last three free days; 17 is not yet, and
    # 21 is already charged.
    assert isinstance(soon, FeeSoon)
    assert soon.free_days == 20
    assert soon.count == sum(1 for days in ages if 17 < days <= 20)

    assert _fee_soon(connection, shop, live=False) == Unavailable(
        OmissionReason.LIVE_ONLY_TODAY, "UNCLAIMED-001"
    )
