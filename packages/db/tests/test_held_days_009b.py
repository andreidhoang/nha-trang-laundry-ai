"""`DEC-050` against real PostgreSQL: held days are not waiting days. `MONEY-RESIDUAL-009B` J1.

`DEC-047` made a hold pause the storage fee. The round-9 verifier found the other measures of the
customer's lateness still counted the days the shop held the laundry: *days waiting* on the list and
on the order, the evening summary's "chờ quá 20 ngày", disposal eligibility (`thanh lý` from day
60) and the pickup reminders' day steps. One clock now counts all of them
(`unclaimed.waiting_clock`); this file reads each of them through the repositories, for the matrix
the brief names:

* a hold before the free days end, and one after;
* a hold spanning a reminder step (the step falls on the counted day, not the calendar day);
* several holds;
* a hold, then a rewash (the hold before the new ready time no longer counts);
* an order still on hold (the count is frozen).

Harness steps (documented, as in `test_unclaimed_laundry.py` and `test_money_lifecycle_009_
postgres.py`): the ready time is moved back with one SQL statement (`_age`); a hold is made by the
counter's own HOLD and RESUME steps and then moved into the past with the append-only guard set
aside for that one statement (`_backdate_hold`) -- nothing else can make a hold ten days long inside
a test.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest
from message_draft_test_data import publish_test_messaging_policy
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pickup_reminders import PickupReminderRepository
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_db.unclaimed import UnclaimedRefused, UnclaimedRepository
from nha_trang_laundry_domain.catalog import RewashReason
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.pickup_reminders import ReminderStep
from nha_trang_laundry_domain.unclaimed import StorageFeeStatus, withdrawal_document
from test_order_step_repository import _read, _step
from test_pickup_reminders_repository import _customer
from test_unclaimed_laundry import (
    VN,
    Shop,
    _age,
    _attempt,
    _dispose,
    _list,
    _publish,
    _ready,
    _rows,
    _shop_day,
    _storage,
)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Generator[Shop, None, None]:
    """A store with one of each role, the storage policy withdrawn before and after the test."""

    made = Shop(connection)
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )
    connection.commit()
    yield made
    connection.rollback()
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )
    connection.commit()


def _do(connection: Any, order_id: UUID, shop: Shop, step: OrderStep, **extra: Any) -> None:
    version = _read(connection, order_id, shop.operator).row_version
    _step(connection, order_id, shop.operator, version, step, **extra)
    connection.commit()


def _backdate_hold(
    connection: Any, order_id: UUID, held_days_ago: int, resumed_days_ago: int | None
) -> None:
    """Harness step: the order's newest hold began `held_days_ago` days ago and was lifted
    `resumed_days_ago` days ago (still open when `None`), the same time of day as now."""

    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE order_storage_holds DISABLE TRIGGER order_storage_holds_protected"
        )
        cursor.execute(
            """
            UPDATE order_storage_holds
            SET held_at = held_at - make_interval(days => %s),
                resumed_at = CASE WHEN resumed_at IS NULL THEN NULL
                                  ELSE resumed_at - make_interval(days => %s) END
            WHERE id = (
                SELECT id FROM order_storage_holds WHERE order_id = %s
                ORDER BY held_at DESC LIMIT 1
            )
            """,
            (held_days_ago, resumed_days_ago or 0, order_id),
        )
        cursor.execute(
            "ALTER TABLE order_storage_holds ENABLE TRIGGER order_storage_holds_protected"
        )
    connection.commit()


def _hold(
    connection: Any, order_id: UUID, shop: Shop, held_days_ago: int, resumed_days_ago: int | None
) -> None:
    """A hold of the finished laundry from `held_days_ago` to `resumed_days_ago` (open: None)."""

    _do(connection, order_id, shop, OrderStep.HOLD)
    if resumed_days_ago is not None:
        _do(connection, order_id, shop, OrderStep.RESUME)
    _backdate_hold(connection, order_id, held_days_ago, resumed_days_ago)


def _today() -> date:
    return (datetime.now(UTC) + VN).date()


def _row(connection: Any, shop: Shop, order_id: UUID) -> Any:
    listed = _list(connection, shop)
    connection.rollback()
    return next(item for item in listed.orders if item.order_id == order_id)


def _count(connection: Any, shop: Shop) -> dict[int, int]:
    with connection.cursor() as cursor:
        counted = UnclaimedRepository.count_waiting(
            cursor,
            store_id=shop.store_id,
            principal=shop.operator,
            as_of=datetime.now(UTC),
            thresholds=(19, 20, 29, 30),
        )
    connection.rollback()
    return dict(counted.over)


def _due(connection: Any, shop: Shop) -> dict[UUID, Any]:
    with connection.cursor() as cursor:
        found = PickupReminderRepository.list_due(
            cursor, store_id=shop.store_id, principal=shop.operator, as_of=datetime.now(UTC)
        )
    connection.rollback()
    return {row.order_id: row for row in found.orders}


# --- the matrix ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "ready_days_ago", "holds", "waited", "held"),
    [
        # Ready 35 days ago, held from day 10 to day 25 (inside the free days): 20 days count.
        ("hold before the free days end", 35, ((25, 10),), 20, 15),
        # Ready 40 days ago, held from day 25 to day 35 (after them): 30 days count.
        ("hold after the free days end", 40, ((15, 5),), 30, 10),
        # Two holds, 5 and 10 days: 50 calendar days on the shelf, 35 count.
        ("several holds", 50, ((45, 40), (30, 20)), 35, 15),
    ],
)
def test_days_waiting_the_count_and_the_fee_skip_every_lifted_hold(
    connection: psycopg.Connection[Any],
    shop: Shop,
    label: str,
    ready_days_ago: int,
    holds: tuple[tuple[int, int], ...],
    waited: int,
    held: int,
) -> None:
    _publish(connection, shop)
    connection.commit()
    order_id = _ready(connection, shop)
    _age(connection, order_id, ready_days_ago)
    for held_ago, resumed_ago in holds:
        _hold(connection, order_id, shop, held_ago, resumed_ago)
    row = _row(connection, shop, order_id)
    storage = _storage(connection, order_id, shop.operator)
    connection.rollback()
    # The list, the order and the fee read the same days, and the order says how many were held.
    assert row.days_waiting == waited, label
    # So does Đồ chờ lấy's row (verification round 1, P2: the list printed "Chờ N ngày" alone).
    assert row.held_days == held, label
    assert (storage.days_waiting, storage.held_days) == (waited, held), label
    assert storage.fee.fee is not None and storage.fee.fee.days_waiting == waited, label
    expected_fee = min(max(0, waited - 20) * 5_000, 55_000)  # capped at half of 110.000 ₫
    assert storage.fee.amount_vnd == expected_fee, label
    # The evening summary's "chờ quá N ngày" counts the same days.
    over = _count(connection, shop)
    for threshold in (19, 20, 29, 30):
        assert over[threshold] == (1 if waited > threshold else 0), (label, threshold)
    # Disposal: day 60 is the counted day 60, so it falls `held` calendar days later.
    expected_on = _today() - timedelta(days=ready_days_ago) + timedelta(days=60 + held)
    assert storage.disposal_verdict.days_waiting == waited, label
    assert storage.disposal_verdict.eligible_on == expected_on, label
    assert row.disposal.eligible_on == expected_on, label


def test_the_longest_waiting_is_first_by_the_counted_days(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """Đồ chờ lấy lists the longest-waiting first: an order ready longer ago but held most of the
    time waits less than one ready later and never held.

    An order with no recorded ready time (legacy data migration 0037 keeps as NULL) has an unknown
    wait, possibly the longest: it stays at the top, where the page's SQL (`NULLS FIRST`) put it
    -- never ranked as if it had waited one day (verification round 2, P2)."""

    held_long = _ready(connection, shop)
    _age(connection, held_long, 30)
    _hold(connection, held_long, shop, 28, 3)  # 25 days held: 5 count
    plain = _ready(connection, shop)
    _age(connection, plain, 12)
    today = _ready(connection, shop)
    legacy = _ready(connection, shop)
    with connection.cursor() as cursor:
        # Harness step: the legacy shape -- finished before 0037 recorded the ready time.
        cursor.execute(
            "UPDATE orders SET production_ready_at = NULL, row_version = row_version + 1"
            " WHERE id = %s",
            (legacy,),
        )
    connection.commit()
    listed = [item.order_id for item in _list(connection, shop).orders]
    connection.rollback()
    assert listed.index(plain) < listed.index(held_long) < listed.index(today)
    assert listed[0] == legacy
    assert _row(connection, shop, legacy).days_waiting is None
    assert _row(connection, shop, today).days_waiting == 0
    assert _row(connection, shop, held_long).days_waiting == 5
    assert _row(connection, shop, plain).days_waiting == 12


def test_an_open_hold_freezes_the_days_waiting_and_puts_disposal_off(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    connection.commit()
    order_id = _ready(connection, shop)
    _age(connection, order_id, 70)
    _hold(connection, order_id, shop, 50, None)  # on hold since day 20, still
    storage = _storage(connection, order_id, shop.operator)
    connection.rollback()
    assert storage.days_waiting == 20
    assert (storage.fee.status, storage.fee.amount_vnd) == (StorageFeeStatus.FREE_PERIOD, 0)
    # Nothing that measures lateness moves while the shop holds the laundry.
    assert storage.disposal_verdict.days_waiting == 20
    assert storage.disposal_verdict.eligible_on is None
    assert "NOT_AWAITING_PICKUP" in [r.value for r in storage.disposal_verdict.refusals]
    assert "DISPOSAL_TOO_EARLY" in [r.value for r in storage.disposal_verdict.refusals]


def test_disposal_is_refused_until_the_counted_day_sixty_not_the_calendar_one(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """Ready 65 days ago, held ten of them: 55 count. Three attempts on two days are recorded, so
    before DEC-050 the owner could dispose of laundry the shop itself held for ten days."""

    _publish(connection, shop)
    connection.commit()
    order_id = _ready(connection, shop)
    _age(connection, order_id, 65)
    _hold(connection, order_id, shop, 40, 30)
    for days_ago, hour in ((3, 9), (3, 15), (2, 10)):
        _attempt(connection, order_id, shop.operator, at=_shop_day(days_ago, hour))
    with pytest.raises(UnclaimedRefused) as refused:
        _dispose(connection, order_id, shop.owner)
    connection.rollback()
    assert refused.value.reason_codes == ("DISPOSAL_TOO_EARLY",)
    assert _rows(
        connection, "SELECT count(*) FROM order_disposals WHERE order_id = %s", order_id
    ) == [(0,)]
    # Five days later (the counted day 60) the same order may go: the harness moves it on.
    _age(connection, order_id, 5)
    _backdate_hold(connection, order_id, 5, 5)
    disposed = _dispose(connection, order_id, shop.owner)
    connection.commit()
    assert disposed.view.commercial.value == "CANCELLED"
    assert _rows(
        connection, "SELECT days_waiting FROM order_disposals WHERE order_id = %s", order_id
    ) == [(60,)]


@pytest.mark.parametrize(
    ("ready_days_ago", "held_ago", "resumed_ago", "step", "waited"),
    [
        # Held on day 2, lifted on day 9 (across day 3 and day 7): today is the counted day 2,
        # so the step due is still READY -- not DAY_7.
        (9, 7, 0, ReminderStep.READY, 2),
        # One day later the count reaches 3: DAY_3, not the calendar's DAY_7.
        (10, 8, 1, ReminderStep.DAY_3, 3),
        # Held on day 5 for ten days, 22 calendar days ago: day 12 counts -- DAY_7, not BEFORE_FEE.
        (22, 17, 7, ReminderStep.DAY_7, 12),
    ],
)
def test_a_hold_spanning_a_reminder_step_moves_the_step_to_the_counted_day(
    connection: psycopg.Connection[Any],
    shop: Shop,
    ready_days_ago: int,
    held_ago: int,
    resumed_ago: int,
    step: ReminderStep,
    waited: int,
) -> None:
    _publish(connection, shop)
    connection.commit()
    customer_id, _ = _customer(connection, shop)
    order_id = _ready(connection, shop, customer_id=customer_id)
    _age(connection, order_id, ready_days_ago)
    _hold(connection, order_id, shop, held_ago, resumed_ago)
    due = _due(connection, shop)[order_id]
    assert (due.step, due.days_waiting) == (step, waited)
    # The reminders row names the held days beside its count (verification round 1, P2).
    assert due.held_days == held_ago - resumed_ago
    # The attempt check reads the same clock: the calendar's step is refused, the counted one
    # is the one due.
    publish_test_messaging_policy(connection)
    connection.commit()
    message = PickupReminderRepository.read_message(
        connection, order_id=order_id, step=step, principal=shop.operator, as_of=datetime.now(UTC)
    )
    connection.rollback()
    if step is not ReminderStep.READY:
        # Verification round 1 (P2): the text pairs the count with the calendar ready day, so it
        # names the held days it leaves out -- the ready day, plus the count, plus the held days,
        # is today. v1 printed "đến nay đã 12 ngày" beside a ready day 22 calendar days back.
        ready = (_ready_at(connection, order_id) + VN).date()
        held = (_today() - ready).days - waited
        assert held > 0
        assert f"xong từ ngày {ready.day:02d}/{ready.month:02d}/{ready.year}" in message.text
        assert f"đến nay đã {waited} ngày (không tính {held} ngày tiệm giữ đơn)." in message.text
        assert message.template == "pickup-reminder-v2"


def _ready_at(connection: Any, order_id: UUID) -> datetime:
    [(ready_at,)] = _rows(
        connection, "SELECT production_ready_at FROM orders WHERE id = %s", order_id
    )
    connection.rollback()
    return ready_at  # type: ignore[no-any-return]


def test_before_fee_names_the_fee_day_past_the_held_days(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """Ready 26 days ago, held six of them: today is the counted day 20 (BEFORE_FEE), and the fee
    starts tomorrow -- not on the calendar day 21, five days ago."""

    _publish(connection, shop)
    customer_id, _ = _customer(connection, shop)
    order_id = _ready(connection, shop, customer_id=customer_id)
    _age(connection, order_id, 26)
    _hold(connection, order_id, shop, 16, 10)
    assert _due(connection, shop)[order_id].step is ReminderStep.BEFORE_FEE
    publish_test_messaging_policy(connection)
    connection.commit()
    message = PickupReminderRepository.read_message(
        connection,
        order_id=order_id,
        step=ReminderStep.BEFORE_FEE,
        principal=shop.operator,
        as_of=datetime.now(UTC),
    )
    connection.rollback()
    starts = _today() + timedelta(days=1)
    assert f"Từ ngày {starts.day:02d}/{starts.month:02d}/{starts.year} tiệm tính phí" in (
        message.text
    )
    # And it says why the fee day is later than the ready day suggests (verification P2).
    assert "Không tính 6 ngày tiệm giữ đơn vào thời gian chờ." in message.text


def test_a_hold_then_a_rewash_counts_from_the_new_ready_time_only(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """The hold before the rewash belongs to laundry that was washed again: it neither counts nor
    is subtracted. The new ready day is day 0 for every measure."""

    _publish(connection, shop)
    connection.commit()
    order_id = _ready(connection, shop)
    _age(connection, order_id, 30)
    _hold(connection, order_id, shop, 25, 20)
    _do(connection, order_id, shop, OrderStep.REWASH, rewash_reason=RewashReason.NOT_CLEAN)
    _do(connection, order_id, shop, OrderStep.QUALITY_CHECK)
    _do(connection, order_id, shop, OrderStep.MARK_READY)
    _age(connection, order_id, 4)  # ready again four days ago; the old hold is before that
    storage = _storage(connection, order_id, shop.operator)
    row = _row(connection, shop, order_id)
    connection.rollback()
    assert (storage.days_waiting, storage.held_days, row.days_waiting) == (4, 0, 4)
    assert row.held_days == 0
    assert storage.disposal_verdict.eligible_on == _today() + timedelta(days=56)
    assert _due(connection, shop)[order_id].step is ReminderStep.DAY_3
    assert _due(connection, shop)[order_id].held_days == 0
