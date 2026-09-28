"""`late_delivery_clock` (`LATE-CREDIT-002`, `DEC-042`): the deadline, the arrival, the minutes.

Every instant is fixed; nothing reads a clock. The property tests pin the two edges a person would
argue about at the counter: the threshold is *more than* 120 minutes, and a second short of a
minute is not a minute.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.late_delivery import (
    DeadlineBasis,
    LegAttempt,
    PromiseMove,
    late_delivery_clock,
)
from nha_trang_laundry_domain.promise import PromiseChangeReason

VN = ZoneInfo("Asia/Ho_Chi_Minh")
PROMISE = datetime(2026, 9, 28, 17, 0, tzinfo=VN)


def _returned(at: datetime) -> LegAttempt:
    return LegAttempt("RETURN", "SUCCEEDED", at)


def _failed(at: datetime) -> LegAttempt:
    return LegAttempt("RETURN", "FAILED", at)


def test_the_deadline_is_the_first_promise_when_nothing_moved_it() -> None:
    clock = late_delivery_clock(PROMISE, (), (_returned(PROMISE + timedelta(hours=3, minutes=10)),))
    assert clock.deadline == PROMISE
    assert clock.deadline_basis is DeadlineBasis.FIRST_PROMISE
    assert clock.late_by_minutes == 190
    assert clock.is_late_beyond(120)


@pytest.mark.parametrize(
    "reason",
    [
        reason
        for reason in PromiseChangeReason
        if reason is not PromiseChangeReason.CUSTOMER_REQUEST
    ],
)
def test_a_shop_side_hen_lai_never_moves_the_deadline(reason: PromiseChangeReason) -> None:
    """Re-promising for a machine, the workload or the weather must not cancel the credit."""

    later = PROMISE + timedelta(hours=5)
    clock = late_delivery_clock(
        PROMISE,
        (PromiseMove(later, reason, PROMISE - timedelta(hours=2)),),
        (_returned(PROMISE + timedelta(hours=4)),),
    )
    assert clock.deadline == PROMISE
    assert clock.deadline_basis is DeadlineBasis.FIRST_PROMISE
    assert clock.late_by_minutes == 240


def test_a_customer_request_moves_the_deadline_to_its_new_time() -> None:
    asked = PROMISE + timedelta(hours=5)
    clock = late_delivery_clock(
        PROMISE,
        (PromiseMove(asked, PromiseChangeReason.CUSTOMER_REQUEST, PROMISE - timedelta(hours=3)),),
        (_returned(PROMISE + timedelta(hours=4)),),
    )
    assert clock.deadline == asked
    assert clock.deadline_basis is DeadlineBasis.CUSTOMER_REQUEST
    assert clock.late_by_minutes == 0
    assert not clock.is_late_beyond(120)


def test_the_newest_customer_request_wins_and_shop_moves_after_it_do_not_count() -> None:
    first_ask = PROMISE + timedelta(hours=1)
    second_ask = PROMISE + timedelta(hours=2)
    changes = (
        PromiseMove(first_ask, PromiseChangeReason.CUSTOMER_REQUEST, PROMISE - timedelta(hours=5)),
        PromiseMove(second_ask, PromiseChangeReason.CUSTOMER_REQUEST, PROMISE - timedelta(hours=4)),
        PromiseMove(
            PROMISE + timedelta(hours=9), PromiseChangeReason.WORKLOAD, PROMISE - timedelta(hours=1)
        ),
    )
    clock = late_delivery_clock(PROMISE, changes, (_returned(second_ask + timedelta(minutes=121)),))
    assert clock.deadline == second_ask
    assert clock.late_by_minutes == 121


def test_a_customer_request_recorded_after_the_arrival_is_ignored() -> None:
    arrived = PROMISE + timedelta(hours=3)
    clock = late_delivery_clock(
        PROMISE,
        (
            PromiseMove(
                PROMISE + timedelta(hours=6),
                PromiseChangeReason.CUSTOMER_REQUEST,
                arrived + timedelta(minutes=1),
            ),
        ),
        (_returned(arrived),),
    )
    assert clock.deadline == PROMISE
    assert clock.late_by_minutes == 180


def test_a_customer_request_may_also_bring_the_deadline_earlier() -> None:
    """The customer asked for it sooner; the shop agreed; that is the time the customer was owed."""

    sooner = PROMISE - timedelta(hours=3)
    clock = late_delivery_clock(
        PROMISE,
        (PromiseMove(sooner, PromiseChangeReason.CUSTOMER_REQUEST, sooner - timedelta(hours=5)),),
        (_returned(PROMISE),),
    )
    assert clock.deadline == sooner and clock.late_by_minutes == 180


def test_minutes_are_floored_and_never_negative() -> None:
    almost = late_delivery_clock(
        PROMISE, (), (_returned(PROMISE + timedelta(minutes=120, seconds=59)),)
    )
    assert almost.late_by_minutes == 120 and not almost.is_late_beyond(120)
    one_more = late_delivery_clock(PROMISE, (), (_returned(PROMISE + timedelta(minutes=121)),))
    assert one_more.late_by_minutes == 121 and one_more.is_late_beyond(120)
    early = late_delivery_clock(PROMISE, (), (_returned(PROMISE - timedelta(hours=2)),))
    assert early.late_by_minutes == 0


def test_not_delivered_has_no_measurement() -> None:
    clock = late_delivery_clock(PROMISE, (), (_failed(PROMISE - timedelta(hours=1)),))
    assert clock.delivered_at is None and clock.late_by_minutes is None
    assert not clock.is_late_beyond(0)
    assert clock.failed_attempts_before_deadline == (PROMISE - timedelta(hours=1),)


def test_arrival_is_the_earliest_succeeded_return_and_a_pickup_is_never_an_arrival() -> None:
    clock = late_delivery_clock(
        PROMISE,
        (),
        (
            LegAttempt("PICKUP", "SUCCEEDED", PROMISE - timedelta(days=1)),
            _returned(PROMISE + timedelta(hours=4)),
            _returned(PROMISE + timedelta(hours=3)),
        ),
    )
    assert clock.delivered_at == PROMISE + timedelta(hours=3)


def test_failed_attempts_at_or_before_the_deadline_are_listed_oldest_first() -> None:
    clock = late_delivery_clock(
        PROMISE,
        (),
        (
            _failed(PROMISE),
            _failed(PROMISE - timedelta(hours=2)),
            _failed(PROMISE + timedelta(minutes=1)),
            LegAttempt("PICKUP", "FAILED", PROMISE - timedelta(hours=3)),
            _returned(PROMISE + timedelta(hours=3)),
        ),
    )
    assert clock.failed_attempts_before_deadline == (PROMISE - timedelta(hours=2), PROMISE)


def test_naive_instants_are_refused() -> None:
    with pytest.raises(ValueError):
        late_delivery_clock(datetime(2026, 9, 28, 17), (), ())
    with pytest.raises(ValueError):
        late_delivery_clock(
            PROMISE, (), (LegAttempt("RETURN", "SUCCEEDED", datetime(2026, 9, 28)),)
        )


def test_the_same_instant_in_two_zones_measures_the_same() -> None:
    arrived = PROMISE + timedelta(hours=2, minutes=1)
    local = late_delivery_clock(PROMISE, (), (_returned(arrived),))
    utc = late_delivery_clock(PROMISE.astimezone(UTC), (), (_returned(arrived.astimezone(UTC)),))
    assert local.late_by_minutes == utc.late_by_minutes == 121


@given(seconds=st.integers(min_value=-86_400 * 3, max_value=86_400 * 3))
def test_minutes_late_is_the_floor_of_whole_minutes_and_never_negative(seconds: int) -> None:
    clock = late_delivery_clock(PROMISE, (), (_returned(PROMISE + timedelta(seconds=seconds)),))
    assert clock.late_by_minutes == max(0, seconds // 60)
    assert clock.is_late_beyond(120) is (seconds >= 121 * 60)


@given(
    shop_moves=st.lists(
        st.tuples(
            st.sampled_from(
                [r for r in PromiseChangeReason if r is not PromiseChangeReason.CUSTOMER_REQUEST]
            ),
            st.integers(min_value=-600, max_value=600),
        ),
        max_size=5,
    ),
    late_minutes=st.integers(min_value=0, max_value=600),
)
def test_no_shop_side_move_can_change_the_measurement(
    shop_moves: list[tuple[PromiseChangeReason, int]], late_minutes: int
) -> None:
    arrived = PROMISE + timedelta(minutes=late_minutes)
    moves = tuple(
        PromiseMove(
            PROMISE + timedelta(minutes=offset), reason, PROMISE - timedelta(minutes=index + 1)
        )
        for index, (reason, offset) in enumerate(shop_moves)
    )
    clock = late_delivery_clock(PROMISE, moves, (_returned(arrived),))
    assert clock.late_by_minutes == late_minutes
