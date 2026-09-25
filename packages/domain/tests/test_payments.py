"""`PAYMENT-001` (`DEC-035`): deposits and part payments with a method, decided by the domain.

The table the spec asks for -- a deposit, two part payments, the exact rest, overpayment refused,
pickup refused while partly paid -- and the edges around it: the 1 đồng floor, the remaining amount
as the ceiling to the đồng, a transfer that was not seen, the bank reference shape, a quote with no
single total, and a bill a credit covered in full. Every figure is an integer of đồng.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.payments import (
    ChargeKind,
    OrderCharge,
    PaymentAccepted,
    PaymentMethod,
    PaymentRefusal,
    PaymentRefused,
    evaluate_payment,
    goods_may_leave,
    normalise_bank_ref,
    owed_charges,
    owed_total,
    payment_position,
)
from nha_trang_laundry_domain.settlement import (
    CollectionRefusal,
    QuotedTotal,
    SettlementShape,
    evaluate_collection,
)

TOTAL = 120_000
CASH = PaymentMethod.TIEN_MAT
TRANSFER = PaymentMethod.CHUYEN_KHOAN
UNPAID = OrderBalanceStatus.UNPAID
PARTLY = OrderBalanceStatus.PARTIALLY_PAID
PAID = OrderBalanceStatus.PAID


def pay(
    amount: int,
    *,
    paid: int = 0,
    balance: OrderBalanceStatus | None = None,
    method: PaymentMethod = CASH,
    seen: bool = False,
    ref: str | None = None,
    collected: bool = False,
    total: int | None = TOTAL,
    commercial: CommercialOrderStatus = CommercialOrderStatus.ACTIVE,
) -> PaymentAccepted | PaymentRefused:
    return evaluate_payment(
        commercial=commercial,
        balance=balance or (UNPAID if paid == 0 else PARTLY),
        charges=owed_charges(QuotedTotal(total, total)),
        paid_vnd=paid,
        amount_vnd=amount,
        method=method,
        transfer_seen=seen,
        bank_ref_last=ref,
        collected_by_customer=collected,
    )


def accepted(outcome: PaymentAccepted | PaymentRefused) -> PaymentAccepted:
    assert isinstance(outcome, PaymentAccepted), outcome
    return outcome


def refused(outcome: PaymentAccepted | PaymentRefused) -> PaymentRefusal:
    assert isinstance(outcome, PaymentRefused), outcome
    return outcome.refusal


# --- the spec's table ----------------------------------------------------------------------------


def test_a_deposit_by_transfer_leaves_the_order_partly_paid() -> None:
    deposit = accepted(pay(50_000, method=TRANSFER, seen=True, ref="FT26"))
    assert (deposit.amount_vnd, deposit.paid_after_vnd, deposit.remaining_after_vnd) == (
        50_000,
        50_000,
        70_000,
    )
    assert deposit.balance_after is PARTLY and not deposit.completes


def test_two_part_payments_then_the_exact_rest_settle_the_order() -> None:
    first = accepted(pay(30_000))
    second = accepted(pay(40_000, paid=first.paid_after_vnd))
    assert second.balance_after is PARTLY and second.remaining_after_vnd == 50_000
    rest = accepted(pay(second.remaining_after_vnd, paid=second.paid_after_vnd))
    assert rest.balance_after is PAID and rest.completes
    assert (rest.paid_after_vnd, rest.remaining_after_vnd) == (TOTAL, 0)


def test_the_whole_total_in_one_payment_settles_it() -> None:
    whole = accepted(pay(TOTAL))
    assert whole.completes and whole.paid_after_vnd == TOTAL


@pytest.mark.parametrize(("paid", "amount"), [(0, TOTAL + 1), (50_000, 70_001), (119_999, 2)])
def test_overpayment_is_refused_to_the_dong(paid: int, amount: int) -> None:
    assert refused(pay(amount, paid=paid)) is PaymentRefusal.OVERPAYMENT_REFUSED
    # One đồng less is exactly the rest.
    assert accepted(pay(amount - 1, paid=paid)).completes


def test_pickup_is_refused_while_partly_paid() -> None:
    # The payment that does not settle cannot carry "the customer takes the goods".
    assert refused(pay(50_000, collected=True)) is PaymentRefusal.HANDOVER_REQUIRES_FULL_PAYMENT
    # And the pickup record for an order paid in advance is refused on a deposit alone.
    assert (
        evaluate_collection(
            commercial=CommercialOrderStatus.ACTIVE,
            production=ProductionStatus.READY_AT_STORE,
            balance=PARTLY,
            settlement_shape=None,
            self_collection_recorded=False,
        )
        is CollectionRefusal.COLLECTION_REQUIRES_PAYMENT
    )
    assert not goods_may_leave(PARTLY) and not goods_may_leave(UNPAID) and goods_may_leave(PAID)
    # Once the rest is taken the pickup is recorded like any prepaid order's.
    assert (
        evaluate_collection(
            commercial=CommercialOrderStatus.ACTIVE,
            production=ProductionStatus.READY_AT_STORE,
            balance=PAID,
            settlement_shape=SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
            self_collection_recorded=False,
        )
        is None
    )


def test_the_rest_taken_at_pickup_may_record_the_handover_with_it() -> None:
    rest = accepted(pay(70_000, paid=50_000, collected=True))
    assert rest.completes and rest.collected_by_customer


# --- edges ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("amount", [0, -1, True, False])
def test_an_amount_below_one_dong_or_not_an_integer_is_refused(amount: int) -> None:
    assert refused(pay(amount)) is PaymentRefusal.PAYMENT_AMOUNT_INVALID


def test_one_dong_is_a_payment() -> None:
    assert accepted(pay(1)).remaining_after_vnd == TOTAL - 1


def test_a_transfer_is_recorded_only_once_it_was_seen() -> None:
    assert refused(pay(50_000, method=TRANSFER)) is PaymentRefusal.TRANSFER_NOT_SEEN
    assert accepted(pay(50_000, method=TRANSFER, seen=True)).balance_after is PARTLY


@pytest.mark.parametrize(
    ("method", "ref", "ok"),
    [
        (TRANSFER, "AB", True),
        (TRANSFER, "123456789012", True),
        (TRANSFER, "A", False),
        (TRANSFER, "1234567890123", False),
        (TRANSFER, "ab12", False),  # normalised before the domain sees it; lower case is not
        (TRANSFER, "FT-26", False),
        (CASH, "FT26", False),  # a cash payment has no bank reference
    ],
)
def test_the_bank_reference_is_two_to_twelve_letters_or_digits_on_a_transfer(
    method: PaymentMethod, ref: str, ok: bool
) -> None:
    outcome = pay(10_000, method=method, seen=True, ref=ref)
    if ok:
        accepted(outcome)
    else:
        assert refused(outcome) is PaymentRefusal.BANK_REF_INVALID


def test_the_bank_reference_is_normalised_before_it_is_judged() -> None:
    assert normalise_bank_ref("  ft 26 ab ") == "FT26AB"
    assert normalise_bank_ref("   ") is None
    assert normalise_bank_ref(None) is None


@pytest.mark.parametrize(
    "commercial",
    [
        CommercialOrderStatus.CONFIRMED,
        CommercialOrderStatus.CANCELLATION_REVIEW,
        CommercialOrderStatus.CANCELLED,
        CommercialOrderStatus.COMPLETED,
    ],
)
def test_only_a_running_order_takes_money(commercial: CommercialOrderStatus) -> None:
    assert refused(pay(10_000, commercial=commercial)) is PaymentRefusal.ORDER_NOT_ACTIVE


@pytest.mark.parametrize(
    "balance",
    [PAID, OrderBalanceStatus.REFUNDED, OrderBalanceStatus.ON_ACCOUNT, OrderBalanceStatus.OVERPAID],
)
def test_nothing_is_taken_where_nothing_is_owed(balance: OrderBalanceStatus) -> None:
    assert refused(pay(10_000, balance=balance)) is PaymentRefusal.NOTHING_OWED


def test_a_quote_with_no_single_total_takes_no_payment() -> None:
    assert refused(pay(10_000, total=None)) is PaymentRefusal.NO_PRESENTABLE_TOTAL
    assert owed_charges(QuotedTotal(100_000, 120_000)) is None
    assert owed_charges(QuotedTotal(None, None)) is None


def test_a_bill_a_credit_covered_in_full_is_settled_with_zero_and_nothing_else() -> None:
    zero = accepted(pay(0, total=0))
    assert zero.completes and zero.amount_vnd == 0
    assert refused(pay(1, total=0)) is PaymentRefusal.NOTHING_OWED


# --- the charges list and the position -----------------------------------------------------------


def test_what_is_owed_is_a_list_of_charges_whose_sum_is_the_quoted_total_today() -> None:
    charges = owed_charges(QuotedTotal(TOTAL, TOTAL))
    assert charges == (OrderCharge(ChargeKind.QUOTED_TOTAL, TOTAL),)
    assert owed_total(charges) == TOTAL


def test_a_second_charge_is_owed_without_any_rule_changing() -> None:
    """The seam `UNCLAIMED-001` uses: a later charge joins the list and the rules read the sum."""

    fee = OrderCharge(ChargeKind.QUOTED_TOTAL, 5_000)  # the kind is illustrative; the sum is not
    charges = (OrderCharge(ChargeKind.QUOTED_TOTAL, TOTAL), fee)
    outcome = evaluate_payment(
        commercial=CommercialOrderStatus.ACTIVE,
        balance=PARTLY,
        charges=charges,
        paid_vnd=TOTAL,
        amount_vnd=5_000,
        method=CASH,
        transfer_seen=False,
        bank_ref_last=None,
        collected_by_customer=False,
    )
    assert accepted(outcome).completes and accepted(outcome).owed_vnd == TOTAL + 5_000


def test_the_position_reads_total_paid_and_remaining() -> None:
    position = payment_position(owed_charges(QuotedTotal(TOTAL, TOTAL)), 50_000)
    assert (position.owed_vnd, position.paid_vnd, position.remaining_vnd) == (
        TOTAL,
        50_000,
        70_000,
    )
    unknown = payment_position(None, 0)
    assert (unknown.owed_vnd, unknown.remaining_vnd, unknown.charges) == (None, None, ())


def test_a_ledger_above_what_is_owed_is_a_contradiction_not_a_negative_remainder() -> None:
    with pytest.raises(ValueError, match="exceed"):
        payment_position(owed_charges(QuotedTotal(TOTAL, TOTAL)), TOTAL + 1)


@given(
    total=st.integers(min_value=1, max_value=50_000_000),
    parts=st.lists(st.integers(min_value=1, max_value=50_000_000), min_size=1, max_size=8),
)
def test_any_sequence_of_payments_never_exceeds_the_total_and_settles_exactly_once(
    total: int, parts: list[int]
) -> None:
    paid = 0
    settled = 0
    for part in parts:
        outcome = pay(part, paid=paid, total=total, balance=UNPAID if paid == 0 else PARTLY)
        if paid == total:
            assert refused(outcome) is PaymentRefusal.NOTHING_OWED
            continue
        if part > total - paid:
            assert refused(outcome) is PaymentRefusal.OVERPAYMENT_REFUSED
            continue
        taken = accepted(outcome)
        paid = taken.paid_after_vnd
        assert taken.remaining_after_vnd == total - paid >= 0
        assert taken.completes is (paid == total)
        settled += int(taken.completes)
    assert paid <= total and settled <= 1
