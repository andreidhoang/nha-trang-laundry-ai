"""One settlement shape is supported. Everything else names the decision that owns it."""

from __future__ import annotations

import pytest
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    FulfillmentMode,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.settlement import (
    CollectionRefusal,
    QuotedTotal,
    SettlementAccepted,
    SettlementNotSupported,
    SettlementRefusal,
    SettlementShape,
    evaluate_collection,
    evaluate_settlement,
    handover_refusal,
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
    # Changed 2026-09-25 under `DEC-032`: this used the default mode, a walk-in, where "nobody
    # collected" is now a customer paying at drop-off. It then measured `PICKUP_ONLY`, until the
    # `DEC-032` addendum the same day let that customer pay at the counter in advance too. What is
    # left of the refusal is its other half, and it is measured there: a counter handover recorded
    # on an order whose laundry travels back by courier. The code and its decision are unchanged.
    outcome = evaluate(collected=True, mode=FulfillmentMode.RETURN_ONLY)
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
        "EXACT_PAYMENT_PREPAID_SELF_COLLECTION",  # DEC-032, 2026-09-25
    ]


def test_a_delivery_order_paid_in_full_at_the_counter_is_a_supported_shape() -> None:
    """`DEC-023`: the customer pays before the laundry leaves, and a leg attests arrival later.

    Same money as the self-collection shape -- the exact total, in full, in one payment -- so
    `DEC-010`, which deferred partial payment, deposits, instalments and credit, is untouched.
    """

    outcome = evaluate(collected=False, mode=FulfillmentMode.PICKUP_AND_RETURN)
    assert isinstance(outcome, SettlementAccepted)
    assert outcome.shape is SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY


def test_a_walk_in_that_has_not_collected_yet_is_a_prepayment_at_drop_off() -> None:
    """Changed 2026-09-25 under `DEC-032`, from "a walk-in that nobody collected is refused".

    The old reasoning was that nothing travelled and nobody took it, so a settlement had nothing to
    attest. `DEC-032` answers it: the settlement attests the money, which did change hands, and the
    handover gets its own record later at pickup. So this is accepted as its own shape -- the same
    exact total as every other -- and it is not the self-collection shape, because nobody took
    anything.
    """

    outcome = evaluate(collected=False, mode=FulfillmentMode.SELF_DROP_SELF_COLLECT)
    assert isinstance(outcome, SettlementAccepted)
    assert outcome.shape is SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION
    assert outcome.expected_total_vnd == TOTAL


@pytest.mark.parametrize("paid", [TOTAL - 1, TOTAL + 1, 0, TOTAL // 2, TOTAL * 2])
def test_a_deposit_at_drop_off_is_still_refused_and_named_dec_010(paid: int) -> None:
    """`DEC-032` moved *when* the exact total may be paid, not *what* may be paid."""

    outcome = evaluate(paid=paid, collected=False, mode=FulfillmentMode.SELF_DROP_SELF_COLLECT)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.AMOUNT_IS_NOT_THE_EXACT_TOTAL
    assert outcome.decision == "DEC-010"


def test_a_delivery_order_collected_at_the_counter_is_refused_as_contradictory() -> None:
    """The order says the laundry travels, the settlement says it was handed over. One is wrong."""

    outcome = evaluate(collected=True, mode=FulfillmentMode.PICKUP_AND_RETURN)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER


#: The whole of the mode/collection decision, stated once. Four modes times "did the customer take
#: it at the counter", and the shape each pair produces or the refusal it earns.
#:
#: `PICKUP_ONLY` is the row this table was written for. Until 2026-08-29 it was refused when the
#: customer collected -- the only way such an order can ever end -- and *accepted* as a prepaid
#: delivery when they did not, which took the money into an order no leg this system permits could
#: close. Both halves were wrong and both are fixed here; the rest of the table is unchanged
#: behaviour, pinned so the next mode added has to argue with a failing test.
MODE_TRUTH_TABLE = (
    (FulfillmentMode.SELF_DROP_SELF_COLLECT, True, SettlementShape.EXACT_PAYMENT_SELF_COLLECTION),
    # `DEC-032` (2026-09-25): was COLLECTION_WAS_NOT_BY_THE_CUSTOMER. A walk-in paying at drop-off.
    (
        FulfillmentMode.SELF_DROP_SELF_COLLECT,
        False,
        SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
    ),
    (FulfillmentMode.PICKUP_ONLY, True, SettlementShape.EXACT_PAYMENT_SELF_COLLECTION),
    # The `DEC-032` addendum (2026-09-25): was COLLECTION_WAS_NOT_BY_THE_CUSTOMER. Paid at the
    # counter before the laundry is finished, handed over later -- the walk-in's shape, never the
    # delivery's, because no leg of this mode brings anything back.
    (
        FulfillmentMode.PICKUP_ONLY,
        False,
        SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
    ),
    (FulfillmentMode.PICKUP_AND_RETURN, True, SettlementRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER),
    (FulfillmentMode.PICKUP_AND_RETURN, False, SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY),
    (FulfillmentMode.RETURN_ONLY, True, SettlementRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER),
    (FulfillmentMode.RETURN_ONLY, False, SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY),
)


@pytest.mark.parametrize(("mode", "collected", "expected"), MODE_TRUTH_TABLE)
def test_every_mode_and_collection_pair_has_one_settled_answer(
    mode: FulfillmentMode, collected: bool, expected: object
) -> None:
    """Eight cases, no gaps.

    Until `DEC-032` every mode was accepted for exactly one side of the counter. Both self-collect
    modes are now accepted on both, as two different shapes: paid and taken at pickup, or paid in
    advance with the handover still to be recorded. No mode is accepted twice as the same shape.
    """

    outcome = evaluate(collected=collected, mode=mode)
    if isinstance(expected, SettlementShape):
        assert isinstance(outcome, SettlementAccepted)
        assert outcome.shape is expected
        assert outcome.expected_total_vnd == TOTAL
    else:
        assert isinstance(outcome, SettlementNotSupported)
        assert outcome.refusal is expected


def test_a_pickup_only_order_is_closed_by_the_customer_at_the_counter() -> None:
    """The case that stranded money, stated on its own because it is why the table exists.

    The shop fetched the laundry and the customer comes in for it. Migration `0033` says so in as
    many words -- `PICKUP_ONLY` "has no return leg at all and is completed by self-collection at the
    counter, exactly as a walk-in is" -- and `delivery_legs` enforces that half by refusing such an
    order a `RETURN` leg. Settlement compared against `SELF_DROP_SELF_COLLECT` alone and so refused
    the handover, while accepting the prepayment that had no way to be closed afterwards.

    No new shape and no new refusal: this is the same money as a walk-in, paid at the counter, with
    the goods handed over there. `DEC-010` and `DEC-023` are both untouched.
    """

    outcome = evaluate(collected=True, mode=FulfillmentMode.PICKUP_ONLY)

    assert isinstance(outcome, SettlementAccepted)
    assert outcome.shape is SettlementShape.EXACT_PAYMENT_SELF_COLLECTION


def test_a_pickup_only_order_cannot_be_prepaid_for_a_delivery_that_never_comes() -> None:
    """The other half, and the one that was actually taking money.

    Accepting this as a delivery was worse than refusing the handover: it set
    `balance_status='PAID'` on an order whose completion needs `required_delivery_legs_succeeded`,
    which needs a succeeded `RETURN` leg, which `delivery_legs` refuses for this mode. Paid in full,
    permanently `ACTIVE`.

    Changed 2026-09-25 by the `DEC-032` addendum, from "refused". The customer may now pay at the
    counter in advance -- but as the walk-in's prepayment, whose handover the pickup command
    records, and never as a delivery. The shape is what decides whether the order can be closed,
    so the shape is what this pins.
    """

    outcome = evaluate(collected=False, mode=FulfillmentMode.PICKUP_ONLY)

    assert isinstance(outcome, SettlementAccepted)
    assert outcome.shape is not SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY
    assert outcome.shape is SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION
    assert outcome.expected_total_vnd == TOTAL


@pytest.mark.parametrize("paid", [TOTAL - 1, TOTAL + 1, 0, TOTAL // 2, TOTAL * 2])
def test_a_pickup_only_prepayment_is_the_exact_total_or_nothing(paid: int) -> None:
    """The `DEC-032` addendum moves *when* a `PICKUP_ONLY` customer may pay, not *what*.

    A deposit handed over at the counter while the laundry is still being washed is the shape
    `DEC-010` deferred, and it is refused with that decision's name exactly as a walk-in's is.
    """

    outcome = evaluate(paid=paid, collected=False, mode=FulfillmentMode.PICKUP_ONLY)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.AMOUNT_IS_NOT_THE_EXACT_TOTAL
    assert outcome.decision == "DEC-010"


@pytest.mark.parametrize(
    ("minimum", "maximum", "refusal"),
    [
        (None, None, SettlementRefusal.NO_PRESENTABLE_TOTAL),
        (TOTAL, TOTAL + 20_000, SettlementRefusal.TOTAL_IS_A_RANGE),
    ],
)
def test_a_pickup_only_prepayment_still_needs_a_single_quoted_total(
    minimum: int | None, maximum: int | None, refusal: SettlementRefusal
) -> None:
    """A `PICKUP_ONLY` quote's courier fee is always negotiated (`DEC-003`); until it is, there is
    no total, and a customer paying in advance cannot be taken for a number nobody quoted."""

    outcome = evaluate(
        minimum=minimum, maximum=maximum, collected=False, mode=FulfillmentMode.PICKUP_ONLY
    )
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is refusal


def test_a_delivery_order_still_cannot_be_part_paid() -> None:
    """DEC-010 is untouched: the amount rule is the same for both shapes."""

    outcome = evaluate(paid=TOTAL - 1, collected=False, mode=FulfillmentMode.PICKUP_AND_RETURN)
    assert isinstance(outcome, SettlementNotSupported)
    assert outcome.refusal is SettlementRefusal.AMOUNT_IS_NOT_THE_EXACT_TOTAL


# --- DEC-032: who may be handed laundry, and when -------------------------------------------------


@pytest.mark.parametrize("stage", list(ProductionStatus))
def test_only_finished_laundry_can_be_handed_over(stage: ProductionStatus) -> None:
    """The staging review's finding: "collected" was accepted for shirts still in the machine.

    Finished means washed, checked and waiting, or released. Every other state -- including a hold
    or an exception somebody raised on purpose -- is laundry the shop still has work to do on.
    """

    finished = stage in {ProductionStatus.READY_AT_STORE, ProductionStatus.RELEASED}
    assert (handover_refusal(stage) is None) is finished
    if not finished:
        assert handover_refusal(stage) == "GOODS_NOT_READY_FOR_HANDOVER"


def _collection(**overrides: object) -> CollectionRefusal | None:
    facts: dict[str, object] = {
        "commercial": CommercialOrderStatus.ACTIVE,
        "production": ProductionStatus.RELEASED,
        "balance": OrderBalanceStatus.PAID,
        "settlement_shape": SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
        "self_collection_recorded": False,
    }
    facts.update(overrides)
    return evaluate_collection(**facts)  # type: ignore[arg-type]


def test_a_paid_finished_walk_in_order_may_be_collected() -> None:
    assert _collection() is None
    assert _collection(production=ProductionStatus.READY_AT_STORE) is None


@pytest.mark.parametrize(
    ("overrides", "refusal"),
    [
        ({"commercial": CommercialOrderStatus.CANCELLED}, CollectionRefusal.ORDER_NOT_ACTIVE),
        ({"commercial": CommercialOrderStatus.COMPLETED}, CollectionRefusal.ORDER_NOT_ACTIVE),
        ({"self_collection_recorded": True}, CollectionRefusal.ALREADY_COLLECTED),
        (
            {"balance": OrderBalanceStatus.UNPAID, "settlement_shape": None},
            CollectionRefusal.COLLECTION_REQUIRES_PAYMENT,
        ),
        (
            {"settlement_shape": SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY},
            CollectionRefusal.NOT_A_PREPAID_SELF_COLLECTION,
        ),
        (
            {"production": ProductionStatus.QUALITY_CHECK},
            CollectionRefusal.GOODS_NOT_READY_FOR_HANDOVER,
        ),
        (
            {"production": ProductionStatus.NOT_STARTED},
            CollectionRefusal.GOODS_NOT_READY_FOR_HANDOVER,
        ),
    ],
)
def test_a_pickup_is_refused_until_it_is_true(
    overrides: dict[str, object], refusal: CollectionRefusal
) -> None:
    assert _collection(**overrides) is refusal
