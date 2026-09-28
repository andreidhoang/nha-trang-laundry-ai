"""`PICKUP-REMIND-001` (`DEC-043`): the reminder schedule, the newest-due rule and the fixed text.

Golden texts are pinned whole: a change to any sentence is a new template version
(`pickup-reminder-v1` -> v2), never an edit that slips through.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.pickup_reminders import (
    PICKUP_REMINDER_TEMPLATE,
    EgressVerdict,
    Reachability,
    ReminderFacts,
    ReminderFee,
    ReminderStep,
    combine_egress,
    current_reminder,
    fee_starts_on,
    reachability,
    reminder_steps,
    reminder_text,
    schedule,
    step_day,
    zalo_url,
)
from nha_trang_laundry_domain.unclaimed import StoragePolicy, days_waiting, shop_date

READY_ON = date(2026, 9, 1)
POLICY = StoragePolicy(
    free_days=20,
    fee_per_started_day_vnd=5000,
    fee_cap_percent=50,
    disposal_from_day=60,
    disposal_min_attempts=3,
    disposal_min_attempt_days=2,
)


def _policy(free_days: int) -> StoragePolicy:
    return StoragePolicy(free_days, 5000, 50, free_days + 40, 3, 2)


def _day(n: int) -> date:
    return READY_ON + timedelta(days=n)


# --- the schedule ---------------------------------------------------------------------------------


def test_the_schedule_is_days_0_3_7_14_and_the_last_free_day() -> None:
    assert schedule(None) == (
        (ReminderStep.READY, 0),
        (ReminderStep.DAY_3, 3),
        (ReminderStep.DAY_7, 7),
        (ReminderStep.DAY_14, 14),
    )
    assert schedule(POLICY)[-1] == (ReminderStep.BEFORE_FEE, 20)
    assert fee_starts_on(READY_ON, POLICY) == _day(21)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (0, ReminderStep.READY),
        (2, ReminderStep.READY),
        (3, ReminderStep.DAY_3),
        (6, ReminderStep.DAY_3),
        (7, ReminderStep.DAY_7),
        (13, ReminderStep.DAY_7),
        (14, ReminderStep.DAY_14),
        (19, ReminderStep.DAY_14),
        (20, ReminderStep.BEFORE_FEE),
        (21, None),
        (65, None),
    ],
)
def test_the_newest_due_step_with_the_published_policy(
    day: int, expected: ReminderStep | None
) -> None:
    assert current_reminder(READY_ON, _day(day), POLICY, ()) is expected


def test_without_a_policy_there_is_no_before_fee_and_day_14_stays_due() -> None:
    assert current_reminder(READY_ON, _day(20), None, ()) is ReminderStep.DAY_14
    assert current_reminder(READY_ON, _day(90), None, ()) is ReminderStep.DAY_14
    assert ReminderStep.BEFORE_FEE not in reminder_steps(READY_ON, _day(90), None)


def test_a_done_step_hides_and_an_undone_earlier_step_is_superseded() -> None:
    assert current_reminder(READY_ON, _day(4), POLICY, [ReminderStep.DAY_3]) is None
    # READY never done, but day 3 has come: only DAY_3 shows.
    assert current_reminder(READY_ON, _day(3), POLICY, [ReminderStep.READY]) is ReminderStep.DAY_3
    assert current_reminder(READY_ON, _day(9), POLICY, []) is ReminderStep.DAY_7
    # Doing an older step does not satisfy the newest one.
    assert current_reminder(READY_ON, _day(9), POLICY, [ReminderStep.DAY_3]) is ReminderStep.DAY_7


def test_the_clock_before_the_ready_day_is_the_ready_day() -> None:
    assert reminder_steps(READY_ON, READY_ON - timedelta(days=2), POLICY) == (ReminderStep.READY,)


@pytest.mark.parametrize("free_days", [3, 7, 14])
def test_before_fee_supersedes_a_fixed_step_on_the_same_day(free_days: int) -> None:
    assert current_reminder(READY_ON, _day(free_days), _policy(free_days), ()) is (
        ReminderStep.BEFORE_FEE
    )


def test_fixed_steps_after_the_fee_starts_are_not_scheduled() -> None:
    short = _policy(10)
    assert [step for step, _ in schedule(short)] == [
        ReminderStep.READY,
        ReminderStep.DAY_3,
        ReminderStep.DAY_7,
        ReminderStep.BEFORE_FEE,
    ]
    assert step_day(ReminderStep.DAY_14, short) is None
    assert current_reminder(READY_ON, _day(14), short, ()) is None
    assert current_reminder(READY_ON, _day(1), _policy(1), ()) is ReminderStep.BEFORE_FEE


def test_days_are_shop_calendar_days_from_the_same_ready_fact_as_unclaimed() -> None:
    # Ready 19:55 shop time on 1 Sep; 00:05 shop time on 4 Sep is day 3.
    ready_at = datetime(2026, 9, 1, 12, 55, tzinfo=UTC)
    as_of = datetime(2026, 9, 3, 17, 5, tzinfo=UTC)
    assert days_waiting(ready_at, as_of) == 3
    assert current_reminder(shop_date(ready_at), shop_date(as_of), POLICY, ()) is (
        ReminderStep.DAY_3
    )
    # 23:59 shop time on 3 Sep is still day 2.
    assert (
        current_reminder(shop_date(ready_at), shop_date(as_of - timedelta(minutes=10)), POLICY, ())
        is ReminderStep.READY
    )


@given(
    free_days=st.integers(min_value=1, max_value=365),
    waited=st.integers(min_value=0, max_value=800),
    done=st.sets(st.sampled_from(list(ReminderStep))),
)
def test_at_most_one_step_shows_and_it_is_the_newest_due(
    free_days: int, waited: int, done: set[ReminderStep]
) -> None:
    policy = _policy(free_days)
    due = reminder_steps(READY_ON, _day(waited), policy)
    shown = current_reminder(READY_ON, _day(waited), policy, done)
    if waited > free_days:
        assert due == () and shown is None
        return
    assert due and due[0] is ReminderStep.READY
    assert shown is (None if due[-1] in done else due[-1])
    # Days never go backwards along the schedule.
    days = [step_day(step, policy) for step in due]
    assert days == sorted(days)  # type: ignore[type-var]


# --- who can receive it ---------------------------------------------------------------------------


def test_reachability_and_the_zalo_link() -> None:
    assert reachability(has_phone=True, has_chat=True) is Reachability.PHONE
    assert reachability(has_phone=False, has_chat=True) is Reachability.CHAT
    assert reachability(has_phone=False, has_chat=False) is Reachability.NONE
    assert zalo_url("0905123456") == "https://zalo.me/0905123456"
    assert zalo_url("02583812345") == "https://zalo.me/02583812345"
    for bad in (None, "", "+84905123456", "905123456", "0905 123 456", "09051234567890"):
        assert zalo_url(bad) is None


def test_the_egress_answer_over_every_channel() -> None:
    allow = EgressVerdict(True, None)
    stop = EgressVerdict(False, "SUPPRESSED")
    review = EgressVerdict(False, "PENDING_REVIEW")
    unpublished = EgressVerdict(False, "MESSAGING_POLICY_UNPUBLISHED")
    no_basis = EgressVerdict(False, "NO_SERVICE_BASIS")
    assert combine_egress([allow]) is None
    assert combine_egress([allow, no_basis]) is None
    # A STOP anywhere refuses, whatever else allows; the STOP is the reason named.
    assert combine_egress([allow, review, stop]) == "SUPPRESSED"
    assert combine_egress([allow, review]) == "PENDING_REVIEW"
    assert combine_egress([no_basis, unpublished]) == "MESSAGING_POLICY_UNPUBLISHED"
    assert combine_egress([no_basis]) == "NO_SERVICE_BASIS"
    assert combine_egress([]) == "NO_SERVICE_BASIS"


# --- the text (golden) ----------------------------------------------------------------------------


def _facts(**overrides: object) -> ReminderFacts:
    values: dict[str, object] = {
        "shop_name": "Giặt sấy Hoa Sen",
        "ticket_number": 17,
        "ticket_issued_on": date(2026, 8, 30),
        "received_on": date(2026, 8, 30),
        "ready_on": READY_ON,
        "days_waiting": 0,
        "remaining_vnd": 45_000,
        "opening_hours": (time(8, 0), time(20, 0)),
        "fee": None,
    }
    values.update(overrides)
    return ReminderFacts(**values)  # type: ignore[arg-type]


def test_golden_ready() -> None:
    assert PICKUP_REMINDER_TEMPLATE == "pickup-reminder-v1"
    assert reminder_text(ReminderStep.READY, _facts()) == (
        "Giặt sấy Hoa Sen xin báo: đồ giặt phiếu số 17 (nhận ngày 30/08/2026) đã xong ngày "
        "01/09/2026.\n"
        "Mời anh/chị qua tiệm lấy đồ.\n"
        "Số tiền còn lại: 45.000 ₫.\n"
        "Giờ mở cửa: từ 08:00 đến 20:00.\n"
        "Cảm ơn anh/chị!"
    )


@pytest.mark.parametrize(
    ("step", "days"), [(ReminderStep.DAY_3, 3), (ReminderStep.DAY_7, 8), (ReminderStep.DAY_14, 14)]
)
def test_golden_day_steps(step: ReminderStep, days: int) -> None:
    assert reminder_text(step, _facts(days_waiting=days)) == (
        "Giặt sấy Hoa Sen xin nhắc: đồ giặt phiếu số 17 (nhận ngày 30/08/2026) đã xong từ ngày "
        f"01/09/2026, đến nay đã {days} ngày.\n"
        "Mời anh/chị sắp xếp qua tiệm lấy đồ.\n"
        "Số tiền còn lại: 45.000 ₫.\n"
        "Giờ mở cửa: từ 08:00 đến 20:00.\n"
        "Cảm ơn anh/chị!"
    )


def test_golden_before_fee_quotes_the_published_figures() -> None:
    fee = ReminderFee(
        fee_per_started_day_vnd=POLICY.fee_per_started_day_vnd,
        fee_cap_percent=POLICY.fee_cap_percent,
        starts_on=fee_starts_on(READY_ON, POLICY),
    )
    assert reminder_text(ReminderStep.BEFORE_FEE, _facts(days_waiting=20, fee=fee)) == (
        "Giặt sấy Hoa Sen xin nhắc: đồ giặt phiếu số 17 (nhận ngày 30/08/2026) đã xong từ ngày "
        "01/09/2026 và đang chờ anh/chị qua lấy.\n"
        "Từ ngày 22/09/2026 tiệm tính phí lưu kho 5.000 ₫/ngày (tối đa 50% tiền giặt).\n"
        "Mời anh/chị qua lấy trước ngày đó.\n"
        "Số tiền còn lại: 45.000 ₫.\n"
        "Giờ mở cửa: từ 08:00 đến 20:00.\n"
        "Cảm ơn anh/chị!"
    )
    with pytest.raises(ValueError):
        reminder_text(ReminderStep.BEFORE_FEE, _facts(days_waiting=20))


def test_golden_chat_order_paid_unnamed_shop_no_hours() -> None:
    assert reminder_text(
        ReminderStep.READY,
        _facts(
            shop_name=None,
            ticket_number=None,
            ticket_issued_on=None,
            remaining_vnd=0,
            opening_hours=None,
        ),
    ) == (
        "Tiệm giặt xin báo: đồ giặt đơn nhận ngày 30/08/2026 đã xong ngày 01/09/2026.\n"
        "Mời anh/chị qua tiệm lấy đồ.\n"
        "Đơn đã thanh toán đủ.\n"
        "Cảm ơn anh/chị!"
    )
    assert "Tiệm sẽ báo số tiền khi anh/chị qua lấy." in reminder_text(
        ReminderStep.DAY_3, _facts(days_waiting=3, remaining_vnd=None)
    )


def test_the_text_has_no_field_for_a_name_or_a_phone() -> None:
    assert not {"customer_name", "display_name", "phone"} & set(ReminderFacts.__dataclass_fields__)
    with pytest.raises(ValueError):
        reminder_text(ReminderStep.READY, _facts(remaining_vnd=-1))
