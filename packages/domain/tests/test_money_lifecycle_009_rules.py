"""`MONEY-LIFECYCLE-009` (review M1): money that moved is never un-owed. Pure tables and
properties over the domain; the cancellation half (M3, M7) is `test_cancellation_money_rules.py`.

M1. A part payment can cover part of the storage fee (100.000 ₫ quoted, 25.000 ₫ accrued, 103.000 ₫
paid). The fee is recomputed at every read, and a later event -- a waiver, a hold, a rewash that
restarts the free days, a withdrawn policy, a cancellation -- can lower it. Before this item the
order then "owed" less than its ledger held and `payment_position` raised on every read; and a
waiver that left what is owed equal to what was paid stranded the order partly paid with nothing to
take. The rule now (`fee_already_paid`): the fee part already paid stays owed-for; the waiver takes
off only the unpaid part and says when it settles; 0 đồng settles a ledger that covers everything.

"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import CommercialOrderStatus, OrderBalanceStatus
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
    StorageFeeStatus,
    StoragePolicy,
    fee_already_paid,
    order_storage_fee,
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


def day(n: int) -> datetime:
    return (datetime(2026, 9, 1, 12, 0) + timedelta(days=n) - VN).replace(tzinfo=UTC)


def fee(**overrides: object) -> OrderStorageFee:
    arguments: dict[str, object] = {
        "awaiting": True,
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
    "on hold / cancellation review / not waiting": {"awaiting": False},
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


def test_resume_brings_the_accrual_back_and_the_paid_part_never_exceeds_it() -> None:
    held = fee(paid_vnd=QUOTED + 3_000, awaiting=False)
    assert (held.status, held.amount_vnd) == (StorageFeeStatus.ALREADY_PAID, 3_000)
    resumed = fee(paid_vnd=QUOTED + 3_000, as_of=day(26))
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
    awaiting=st.booleans(),
    waived=st.booleans(),
    published=st.booleans(),
)
def test_property_owed_is_never_below_paid_before_the_fee_is_fixed(
    paid: int, n: int, awaiting: bool, waived: bool, published: bool
) -> None:
    storage = fee(
        paid_vnd=paid,
        as_of=day(n),
        awaiting=awaiting,
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
        (fee(awaiting=False, paid_vnd=QUOTED + 3_000), QUOTED + 3_000),  # ALREADY_PAID
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
