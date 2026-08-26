"""One settlement shape is supported. Everything else names the decision that owns it."""

from __future__ import annotations

import pytest
from nha_trang_laundry_domain.catalog import FulfillmentMode
from nha_trang_laundry_domain.settlement import (
    QuotedTotal,
    SettlementAccepted,
    SettlementNotSupported,
    SettlementRefusal,
    SettlementShape,
    evaluate_settlement,
)

TOTAL = 110_000


def evaluate(
    *,
    minimum: int | None = TOTAL,
    maximum: int | None = TOTAL,
    paid: int = TOTAL,
    collected: bool = True,
    mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
) -> SettlementAccepted | SettlementNotSupported:
    return evaluate_settlement(
        quoted=QuotedTotal(minimum, maximum),
        tendered_vnd=paid,
        collected_by_customer=collected,
        fulfillment_mode=mode,
    )


def test_the_exact_total_collected_by_the_customer_is_accepted() -> None:
    outcome = evaluate()
    assert isinstance(outcome, SettlementAccepted)
    assert outcome.shape is SettlementShape.EXACT_PAYMENT_SELF_COLLECTION
    assert outcome.expected_total_vnd == TOTAL


@pytest.mark.parametrize("paid", [TOTAL - 1, TOTAL + 1, 0, 1, TOTAL // 2, TOTAL * 2])
def test_any_other_amount_is_refused_and_named_dec_010(paid: int) -> None:
    """Part payment, deposit, overpayment and instalment are one refusal, not four behaviours.

    Distinguishing them would mean deciding what each *is*, and that is exactly the decision
    DEC-010 holds open. One refusal that names the decision tells a staff member the truth: the
    business has not said what to do with this yet.
    """
    outcome = evaluate(paid=paid)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.AMOUNT_IS_NOT_THE_EXACT_TOTAL
    assert outcome.decision == "DEC-010"


def test_there_is_no_tolerance_in_either_direction() -> None:
    """One đồng under is not paid, and one đồng over is not paid either."""
    assert isinstance(evaluate(paid=TOTAL - 1), SettlementNotSupported)
    assert isinstance(evaluate(paid=TOTAL + 1), SettlementNotSupported)
    assert isinstance(evaluate(paid=TOTAL), SettlementAccepted)


def test_a_quote_with_no_presentable_total_cannot_be_settled() -> None:
    """A quote whose delivery fee is unresolved presents no total, so there is nothing to pay.

    This is the state every quote from `QUOTE-COMMAND-001` is in, and it is why settlement cannot
    quietly charge a service subtotal: the customer was never quoted that number.
    """
    outcome = evaluate(minimum=None, maximum=None)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.NO_PRESENTABLE_TOTAL
    assert outcome.decision == "DEC-003"


def test_a_range_quote_cannot_be_settled() -> None:
    outcome = evaluate(minimum=100_000, maximum=140_000, paid=100_000)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.TOTAL_IS_A_RANGE
    assert outcome.decision == "DEC-001"


def test_goods_not_collected_by_the_customer_are_refused() -> None:
    outcome = evaluate(collected=False)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER
    assert outcome.decision == "DEC-003"


def test_a_boolean_is_not_an_amount() -> None:
    """`True` is an `int` in Python, and this is the money path."""
    outcome = evaluate(paid=True)
    assert isinstance(outcome, SettlementNotSupported)


def test_a_negative_amount_is_refused() -> None:
    assert isinstance(evaluate(paid=-TOTAL), SettlementNotSupported)


def test_every_refusal_names_an_open_decision() -> None:
    """A refusal with no decision behind it is a dead end a staff member cannot escalate."""
    from nha_trang_laundry_domain.settlement import REFUSAL_DECISIONS

    assert set(REFUSAL_DECISIONS) == set(SettlementRefusal)
    assert all(value.startswith("DEC-") for value in REFUSAL_DECISIONS.values())


def test_every_shape_traces_to_a_signed_decision() -> None:
    """Adding a shape must be a visible decision, not a new string somewhere.

    This asserted a list of one until 2026-08-26 and failed the day a second was added, which is
    what it is for. `DEC-023` is that visible decision: the owner chose that a delivery customer
    pays the exact total at the counter before the laundry leaves. Both shapes are the same money
    -- the full total in one payment -- which is why `DEC-010`, deferring partial payment, deposits,
    instalments and credit, is untouched by either.
    """

    assert [shape.value for shape in SettlementShape] == [
        "EXACT_PAYMENT_SELF_COLLECTION",  # the original, DEC-010
        "EXACT_PAYMENT_PREPAID_DELIVERY",  # DEC-023, 2026-08-26
    ]


def test_a_delivery_order_paid_in_full_at_the_counter_is_a_supported_shape() -> None:
    """`DEC-023`: the customer pays before the laundry leaves, and a leg attests arrival later.

    Same money as the self-collection shape -- the exact total, in full, in one payment -- so
    `DEC-010`, which deferred partial payment, deposits, instalments and credit, is untouched.
    """

    outcome = evaluate(collected=False, mode=FulfillmentMode.PICKUP_AND_RETURN)
    assert isinstance(outcome, SettlementAccepted)
    assert outcome.shape is SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY


def test_a_walk_in_that_nobody_collected_is_still_refused() -> None:
    """Nothing travelled and nobody took it, so no settlement can attest anything."""

    outcome = evaluate(collected=False, mode=FulfillmentMode.SELF_DROP_SELF_COLLECT)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER


def test_a_delivery_order_collected_at_the_counter_is_refused_as_contradictory() -> None:
    """The order says the laundry travels, the settlement says it was handed over. One is wrong."""

    outcome = evaluate(collected=True, mode=FulfillmentMode.PICKUP_AND_RETURN)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER


def test_a_delivery_order_still_cannot_be_part_paid() -> None:
    """DEC-010 is untouched: the amount rule is the same for both shapes."""

    outcome = evaluate(paid=TOTAL - 1, collected=False, mode=FulfillmentMode.PICKUP_AND_RETURN)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.AMOUNT_IS_NOT_THE_EXACT_TOTAL
