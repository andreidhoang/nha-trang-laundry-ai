"""`MONEY-LIFECYCLE-009` (review M1): money that moved is never un-owed. Pure tables and
properties over the domain; the cancellation half (M3, M7) is `test_cancellation_money_rules.py`.

M1. A part payment can cover part of the storage fee (100.000 ₫ quoted, 25.000 ₫ accrued, 103.000 ₫
paid). The fee is recomputed at every read, and a later event -- a waiver, a hold, a rewash that
restarts the free days, a withdrawn policy, a cancellation -- can lower it. Before this item the
order then "owed" less than its ledger held and `payment_position` raised on every read; and a
waiver that left what is owed equal to what was paid stranded the order partly paid with nothing to
take. The rule now (`fee_already_paid`): the fee part already paid stays owed-for; the waiver takes
off only the unpaid part and says when it settles; 0 đồng settles a ledger that covers everything.

`DEC-047` (2026-09-30): a hold is not one of those events any more. A hold of finished laundry
pauses the fee where it stood (status `PAUSED`), the days on hold do not count, and `RESUME`
continues the count -- so "hold, take the quoted total, settle" no longer gets round the
approver-only waiver.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.payments import (
    PaymentAccepted,
    PaymentMethod,
    PaymentRefusal,
    PaymentRefused,
    evaluate_payment,
    owed_charges,
    payment_position,
)
from nha_trang_laundry_domain.settlement import QuotedTotal
from nha_trang_laundry_domain.unclaimed import (
    OrderStorageFee,
    StorageClock,
    StorageFeeStatus,
    StoragePause,
    StoragePolicy,
    counted_days,
    fee_already_paid,
    order_storage_fee,
    storage_pause_after_move,
    waiver_effect,
)

POLICY = StoragePolicy(
    free_days=20,
    fee_per_started_day_vnd=5_000,
    fee_cap_percent=50,
    disposal_from_day=60,
    disposal_min_attempts=3,
    disposal_min_attempt_days=2,
)
VN = timedelta(hours=7)
READY = (datetime(2026, 9, 1, 10, 0) - VN).replace(tzinfo=UTC)
QUOTED = 100_000
#: Day 25: five started days past the free twenty, 5.000 ₫ each.
FEE_DAY_25 = 25_000
NO_PAUSE = StoragePause(paused_at=None, paused_days=0)


def day(n: int) -> datetime:
    return (datetime(2026, 9, 1, 12, 0) + timedelta(days=n) - VN).replace(tzinfo=UTC)


def fee(**overrides: object) -> OrderStorageFee:
    arguments: dict[str, object] = {
        "clock": StorageClock.RUNNING,
        "pause": NO_PAUSE,
        "ready_at": READY,
        "as_of": day(25),
        "quoted_total_vnd": QUOTED,
        "waived": False,
        "settled": False,
        "fixed_vnd": None,
        "paid_vnd": 0,
    }
    arguments.update(overrides)
    policy = arguments.pop("policy", POLICY)
    return order_storage_fee(policy, **arguments)  # type: ignore[arg-type]


def owed(storage: OrderStorageFee) -> int:
    charges = owed_charges(QuotedTotal(QUOTED, QUOTED), storage_fee_vnd=storage.amount_vnd)
    assert charges is not None
    total = payment_position(charges, 0).owed_vnd
    assert total is not None
    return total


# --- M1: the part of the fee already paid ---------------------------------------------------------


@pytest.mark.parametrize(
    ("paid", "expected"),
    [
        (0, 0),
        (50_000, 0),  # (a) a deposit below the quoted total pays none of the fee
        (QUOTED, 0),  # exactly the quoted total: the fee is still wholly unpaid
        (QUOTED + 1, 1),
        (QUOTED + 3_000, 3_000),  # (b) part of the fee
        (QUOTED + FEE_DAY_25, FEE_DAY_25),
    ],
)
def test_the_fee_part_already_paid_is_what_the_ledger_holds_past_the_quoted_total(
    paid: int, expected: int
) -> None:
    assert fee_already_paid(paid_vnd=paid, quoted_total_vnd=QUOTED) == expected


def test_no_single_total_means_nothing_of_a_fee_was_paid_and_amounts_are_validated() -> None:
    assert fee_already_paid(paid_vnd=50_000, quoted_total_vnd=None) == 0
    for bad in (-1, 1.5, True):
        with pytest.raises(ValueError):
            fee_already_paid(paid_vnd=bad, quoted_total_vnd=QUOTED)  # type: ignore[arg-type]


#: Every event the review names that lowers the recomputed fee, as the facts it leaves behind.
LOWERING_EVENTS: dict[str, dict[str, object]] = {
    "waiver": {"waived": True},
    # `DEC-047`: a hold is no longer here -- it pauses the fee (`test_a_hold_pauses_...` below).
    "cancellation review / collected / not waiting": {"clock": StorageClock.STOPPED},
    "rewash: ready again, free days restarted": {"ready_at": day(24), "as_of": day(25)},
    "policy withdrawn": {"policy": None},
    "rewash in progress (no ready time)": {"ready_at": None},
}


@pytest.mark.parametrize("event", sorted(LOWERING_EVENTS))
@pytest.mark.parametrize("paid", [0, 50_000, QUOTED, QUOTED + 3_000, QUOTED + 24_999])
def test_a_later_event_never_lowers_what_is_owed_below_what_was_paid(event: str, paid: int) -> None:
    """The matrix M1 names: fee accrued, then (a) a deposit below the total or (b) part of the fee
    paid, then each event that lowers the recomputed fee."""

    before = fee(paid_vnd=paid)
    assert before.status is StorageFeeStatus.ACCRUING and before.amount_vnd == FEE_DAY_25
    assert owed(before) >= paid

    after = fee(paid_vnd=paid, **LOWERING_EVENTS[event])
    kept = max(0, paid - QUOTED)
    assert after.amount_vnd == kept
    assert owed(after) >= paid
    # The read states it; `payment_position` would raise on a ledger above what is owed.
    charges = owed_charges(QuotedTotal(QUOTED, QUOTED), storage_fee_vnd=after.amount_vnd)
    position = payment_position(charges, paid)
    assert position.remaining_vnd == QUOTED + kept - paid
    if kept:
        assert after.status is StorageFeeStatus.ALREADY_PAID
        assert after.already_paid_vnd == kept
        assert position.remaining_vnd == 0
    else:
        assert after.status is not StorageFeeStatus.ALREADY_PAID


def test_resume_continues_the_accrual_and_the_paid_part_never_exceeds_it() -> None:
    # DEC-047 changed this test. It used to pin a hold as `ALREADY_PAID` 3.000 ₫ -- the 22.000 ₫
    # still unpaid erased by the hold -- which is exactly the bypass of the approver-only waiver
    # the decision closes. Held on day 25, read on day 40: the fee stands where the hold found it.
    held = fee(
        paid_vnd=QUOTED + 3_000,
        clock=StorageClock.PAUSED,
        pause=StoragePause(paused_at=day(25), paused_days=0),
        as_of=day(40),
    )
    assert (held.status, held.amount_vnd, held.already_paid_vnd) == (
        StorageFeeStatus.PAUSED,
        FEE_DAY_25,
        3_000,
    )
    # Resumed on day 40 (15 days on hold), read on day 41: one day more than at the hold.
    resumed = fee(
        paid_vnd=QUOTED + 3_000, pause=StoragePause(paused_at=None, paused_days=15), as_of=day(41)
    )
    assert (resumed.status, resumed.amount_vnd) == (StorageFeeStatus.ACCRUING, 30_000)
    assert resumed.already_paid_vnd == 3_000


def test_a_fixed_fee_is_what_was_fixed_whatever_the_ledger_says() -> None:
    assert fee(settled=True, fixed_vnd=3_000, paid_vnd=QUOTED + 3_000).amount_vnd == 3_000
    fixed = fee(settled=True, paid_vnd=QUOTED)
    assert (fixed.status, fixed.amount_vnd, fixed.already_paid_vnd) == (
        StorageFeeStatus.FIXED,
        0,
        0,
    )


@given(
    paid=st.integers(min_value=0, max_value=200_000),
    n=st.integers(min_value=0, max_value=90),
    clock=st.sampled_from(StorageClock),
    held_on=st.none() | st.integers(min_value=0, max_value=90),
    waived=st.booleans(),
    published=st.booleans(),
)
def test_property_owed_is_never_below_paid_before_the_fee_is_fixed(
    paid: int, n: int, clock: StorageClock, held_on: int | None, waived: bool, published: bool
) -> None:
    storage = fee(
        paid_vnd=paid,
        as_of=day(n),
        clock=clock,
        pause=StoragePause(paused_at=None if held_on is None else day(held_on), paused_days=0),
        waived=waived,
        policy=POLICY if published else None,
    )
    assert owed(storage) >= paid
    assert storage.amount_vnd >= storage.already_paid_vnd == max(0, paid - QUOTED)


# --- M1 / A3: what the waiver does ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("paid", "waived", "kept", "remaining_after", "settles"),
    [
        (0, FEE_DAY_25, 0, QUOTED, False),
        (50_000, FEE_DAY_25, 0, QUOTED - 50_000, False),  # (a) below the quoted total
        (QUOTED, FEE_DAY_25, 0, 0, True),  # the review's stranded order
        (QUOTED + 3_000, FEE_DAY_25 - 3_000, 3_000, 0, True),  # (b) part of the fee paid
        (QUOTED + 24_999, 1, 24_999, 0, True),
    ],
)
def test_the_waiver_takes_off_only_the_unpaid_part_and_says_when_it_settles(
    paid: int, waived: int, kept: int, remaining_after: int, settles: bool
) -> None:
    effect = waiver_effect(fee(paid_vnd=paid), quoted_total_vnd=QUOTED, paid_vnd=paid)
    assert effect is not None
    assert (effect.waived_vnd, effect.kept_vnd, effect.remaining_after_vnd) == (
        waived,
        kept,
        remaining_after,
    )
    assert effect.owed_after_vnd == QUOTED + kept >= paid
    assert effect.settles is settles
    assert effect.quoted_total_vnd == QUOTED


def test_nothing_unpaid_is_nothing_to_waive() -> None:
    for storage, paid in (
        (fee(as_of=day(20)), 0),  # free days
        (fee(policy=None), 0),
        (fee(waived=True, paid_vnd=QUOTED + 3_000), QUOTED + 3_000),
        (fee(clock=StorageClock.STOPPED, paid_vnd=QUOTED + 3_000), QUOTED + 3_000),  # ALREADY_PAID
    ):
        assert waiver_effect(storage, quoted_total_vnd=QUOTED, paid_vnd=paid) is None
    # A fee computed against another ledger is a contradiction, not a figure to act on.
    with pytest.raises(ValueError):
        waiver_effect(fee(paid_vnd=0), quoted_total_vnd=QUOTED, paid_vnd=QUOTED + 3_000)


# --- M1 / A2: 0 đồng settles a ledger that covers everything --------------------------------------


def _pay(
    *,
    balance: OrderBalanceStatus,
    storage_fee_vnd: int,
    paid: int,
    amount: int,
    collected: bool = False,
) -> PaymentAccepted | PaymentRefused:
    return evaluate_payment(
        commercial=CommercialOrderStatus.ACTIVE,
        balance=balance,
        charges=owed_charges(QuotedTotal(QUOTED, QUOTED), storage_fee_vnd=storage_fee_vnd),
        paid_vnd=paid,
        amount_vnd=amount,
        method=PaymentMethod.TIEN_MAT,
        transfer_seen=False,
        bank_ref_last=None,
        collected_by_customer=collected,
    )


@pytest.mark.parametrize("collected", [False, True])
@pytest.mark.parametrize(("kept", "paid"), [(0, QUOTED), (3_000, QUOTED + 3_000)])
def test_zero_dong_settles_a_partly_paid_order_whose_ledger_covers_what_it_owes(
    kept: int, paid: int, collected: bool
) -> None:
    outcome = _pay(
        balance=OrderBalanceStatus.PARTIALLY_PAID,
        storage_fee_vnd=kept,
        paid=paid,
        amount=0,
        collected=collected,
    )
    assert isinstance(outcome, PaymentAccepted)
    assert (outcome.amount_vnd, outcome.owed_vnd, outcome.paid_after_vnd) == (0, paid, paid)
    assert outcome.balance_after is OrderBalanceStatus.PAID and outcome.completes
    assert outcome.collected_by_customer is collected


def test_zero_dong_is_still_refused_while_money_is_owed_and_money_is_refused_when_none_is() -> None:
    owing = _pay(
        balance=OrderBalanceStatus.PARTIALLY_PAID, storage_fee_vnd=FEE_DAY_25, paid=QUOTED, amount=0
    )
    assert isinstance(owing, PaymentRefused)
    assert owing.refusal is PaymentRefusal.PAYMENT_AMOUNT_INVALID
    more = _pay(
        balance=OrderBalanceStatus.PARTIALLY_PAID,
        storage_fee_vnd=3_000,
        paid=QUOTED + 3_000,
        amount=1,
    )
    assert isinstance(more, PaymentRefused) and more.refusal is PaymentRefusal.NOTHING_OWED
    # A bill a credit covered in full still settles at 0, as before.
    free = evaluate_payment(
        commercial=CommercialOrderStatus.ACTIVE,
        balance=OrderBalanceStatus.UNPAID,
        charges=owed_charges(QuotedTotal(0, 0)),
        paid_vnd=0,
        amount_vnd=0,
        method=PaymentMethod.TIEN_MAT,
        transfer_seen=False,
        bank_ref_last=None,
        collected_by_customer=False,
    )
    assert isinstance(free, PaymentAccepted) and free.completes
    # A paid order takes nothing, 0 included.
    paid = _pay(balance=OrderBalanceStatus.PAID, storage_fee_vnd=0, paid=QUOTED, amount=0)
    assert isinstance(paid, PaymentRefused) and paid.refusal is PaymentRefusal.NOTHING_OWED


# --- DEC-047: a hold pauses the fee; it never erases it -------------------------------------------


def _held(held_on: int, as_of: int, *, paid: int = 0, lifted_days: int = 0) -> OrderStorageFee:
    return fee(
        clock=StorageClock.PAUSED,
        pause=StoragePause(paused_at=day(held_on), paused_days=lifted_days),
        as_of=day(as_of),
        paid_vnd=paid,
    )


@pytest.mark.parametrize("paid", [0, 50_000, QUOTED, QUOTED + 3_000])
@pytest.mark.parametrize("as_of", [25, 26, 40, 90])
def test_a_hold_pauses_the_fee_where_it_stood(paid: int, as_of: int) -> None:
    """The verifier's case: 25 days on the shelf, 25.000 ₫ accrued, then HOLD."""

    held = _held(25, as_of, paid=paid)
    assert (held.status, held.amount_vnd) == (StorageFeeStatus.PAUSED, FEE_DAY_25)
    assert held.fee is not None and held.fee.days_waiting == 25
    assert owed(held) == QUOTED + FEE_DAY_25


def test_holding_then_paying_the_quoted_total_does_not_settle_the_fee() -> None:
    """The bypass DEC-047 closes: an operator holds the order and takes only the quoted total."""

    held = _held(25, 26, paid=QUOTED)
    charges = owed_charges(QuotedTotal(QUOTED, QUOTED), storage_fee_vnd=held.amount_vnd)
    assert payment_position(charges, QUOTED).remaining_vnd == FEE_DAY_25
    taking_the_quoted_total = _pay(
        balance=OrderBalanceStatus.UNPAID, storage_fee_vnd=held.amount_vnd, paid=0, amount=QUOTED
    )
    assert isinstance(taking_the_quoted_total, PaymentAccepted)
    assert taking_the_quoted_total.balance_after is OrderBalanceStatus.PARTIALLY_PAID
    assert not taking_the_quoted_total.completes
    # Only the approver's waiver takes it off, and it says so before the press.
    effect = waiver_effect(held, quoted_total_vnd=QUOTED, paid_vnd=QUOTED)
    assert effect is not None and (effect.waived_vnd, effect.settles) == (FEE_DAY_25, True)


@pytest.mark.parametrize(
    ("held_on", "resumed_on", "as_of", "status", "amount"),
    [
        (
            25,
            40,
            40,
            StorageFeeStatus.ACCRUING,
            FEE_DAY_25,
        ),  # the resume day counts as the hold day
        (25, 40, 41, StorageFeeStatus.ACCRUING, 30_000),  # then the count continues
        (10, 30, 30, StorageFeeStatus.FREE_PERIOD, 0),  # held inside the free days
        (10, 30, 40, StorageFeeStatus.FREE_PERIOD, 0),  # 20 days counted: still free
        (10, 30, 41, StorageFeeStatus.ACCRUING, 5_000),  # day 21 of the count
        (20, 21, 21, StorageFeeStatus.FREE_PERIOD, 0),  # held on day 20, one day on hold
        (20, 21, 22, StorageFeeStatus.ACCRUING, 5_000),
        (25, 25, 26, StorageFeeStatus.ACCRUING, 30_000),  # held and lifted the same day
    ],
)
def test_resume_continues_the_count_where_the_hold_stopped_it(
    held_on: int, resumed_on: int, as_of: int, status: StorageFeeStatus, amount: int
) -> None:
    lifted = storage_pause_after_move(
        StoragePause(paused_at=day(held_on), paused_days=0),
        before=ProductionStatus.ON_HOLD,
        after=ProductionStatus.READY_AT_STORE,
        resume_to=None,
        ready_restamped=False,
        ready_kept=True,
        moment=day(resumed_on),
    )
    assert lifted == StoragePause(paused_at=None, paused_days=resumed_on - held_on)
    running = fee(pause=lifted, as_of=day(as_of))
    assert (running.status, running.amount_vnd) == (status, amount)


def test_the_pause_bookkeeping_follows_each_production_move() -> None:
    start = StoragePause(paused_at=None, paused_days=3)
    move = {
        "before": ProductionStatus.READY_AT_STORE,
        "after": ProductionStatus.ON_HOLD,
        "resume_to": ProductionStatus.READY_AT_STORE,
        "ready_restamped": False,
        "ready_kept": True,
        "moment": day(25),
    }
    assert storage_pause_after_move(start, **move) == StoragePause(day(25), 3)  # type: ignore[arg-type]
    # A hold of laundry still being washed is not a pause of a fee (nothing accrues then).
    washing = {
        **move,
        "before": ProductionStatus.IN_PROCESS,
        "resume_to": ProductionStatus.IN_PROCESS,
    }
    assert storage_pause_after_move(start, **washing) == start  # type: ignore[arg-type]
    # A rewash restarts the free days, and the hold count with them.
    rewashed = {**move, "after": ProductionStatus.READY_AT_STORE, "ready_restamped": True}
    assert storage_pause_after_move(StoragePause(day(25), 3), **rewashed) == NO_PAUSE  # type: ignore[arg-type]
    # Rework clears the ready stamp: nothing accrues, nothing is paused.
    rework = {**move, "after": ProductionStatus.EXCEPTION, "ready_kept": False}
    assert storage_pause_after_move(StoragePause(day(25), 3), **rework) == NO_PAUSE  # type: ignore[arg-type]
    # Lifting a hold with no recorded start (legacy) changes nothing.
    lifted = {**move, "before": ProductionStatus.ON_HOLD, "after": ProductionStatus.READY_AT_STORE}
    assert storage_pause_after_move(start, **lifted) == start  # type: ignore[arg-type]


def test_a_rewash_restarts_the_free_days_but_a_paid_part_stays_owed_for() -> None:
    rewashed = fee(ready_at=day(40), as_of=day(41), paid_vnd=QUOTED + 3_000)
    assert (rewashed.status, rewashed.amount_vnd) == (StorageFeeStatus.ALREADY_PAID, 3_000)


def test_a_pause_is_ignored_unless_the_order_is_paused() -> None:
    # A stale start on an order that is not on hold any more does not freeze anything.
    running = fee(pause=StoragePause(paused_at=day(22), paused_days=0), as_of=day(25))
    assert running.amount_vnd == FEE_DAY_25
    # A paused order with no recorded start reads as before DEC-047: the customer's side.
    unknown = fee(clock=StorageClock.PAUSED, pause=NO_PAUSE)
    assert unknown.status is StorageFeeStatus.NOT_WAITING


@given(
    ready=st.integers(min_value=0, max_value=30),
    held=st.integers(min_value=0, max_value=60),
    later=st.integers(min_value=0, max_value=60),
    lifted=st.integers(min_value=0, max_value=30),
)
def test_property_a_paused_count_never_moves_and_never_exceeds_the_days_on_the_shelf(
    ready: int, held: int, later: int, lifted: int
) -> None:
    ready_at, held_at = day(ready), day(ready + held)
    frozen = counted_days(ready_at, held_at, paused_at=held_at, paused_days=lifted)
    assert frozen == counted_days(
        ready_at, day(ready + held + later), paused_at=held_at, paused_days=lifted
    )
    assert 0 <= frozen <= held
    running = counted_days(ready_at, day(ready + held + later), paused_at=None, paused_days=lifted)
    assert running <= held + later


def test_the_clock_pauses_only_for_finished_laundry_held_for_the_customer() -> None:
    from nha_trang_laundry_domain.catalog import FulfillmentMode
    from nha_trang_laundry_domain.unclaimed import storage_clock

    base = {
        "commercial": CommercialOrderStatus.ACTIVE,
        "production": ProductionStatus.ON_HOLD,
        "resume_to": ProductionStatus.READY_AT_STORE,
        "fulfillment_mode": FulfillmentMode.SELF_DROP_SELF_COLLECT,
        "self_collection_recorded": False,
    }
    assert storage_clock(**base) is StorageClock.PAUSED  # type: ignore[arg-type]
    shelf = {**base, "production": ProductionStatus.READY_AT_STORE, "resume_to": None}
    assert storage_clock(**shelf) is StorageClock.RUNNING  # type: ignore[arg-type]
    for change in (
        {"resume_to": ProductionStatus.IN_PROCESS},  # held mid-wash: nothing had accrued
        {"commercial": CommercialOrderStatus.CANCELLATION_REVIEW},
        {"fulfillment_mode": FulfillmentMode.PICKUP_AND_RETURN},  # the courier's to return
        {"self_collection_recorded": True},
    ):
        assert storage_clock(**{**base, **change}) is StorageClock.STOPPED  # type: ignore[arg-type]
