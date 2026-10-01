"""GOODS-AND-DRAWER-009 (review M2): the goods leave by any door only when their money allows it.

The review's failure: an unpaid delivery order was released to the courier (`RELEASE` checked
`goods_may_leave` only for an order the customer collects) and its return leg recorded as delivered
(the leg asked nothing), so a later payment was recorded as "prepaid delivery" for goods already
gone. These pure tests pin the matrix the fix promises -- every fulfilment mode x every money state
x every door -- at the domain: what `plan_step(RELEASE)` answers, what `next_steps` offers, and what
`delivery_refusal` (the question the leg route asks) says. The database and HTTP tests prove the
same answers are what gets written and refused.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nha_trang_laundry_domain.catalog import (
    MODES_EXPECTING_RETURN,
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.order_steps import (
    GoodsMayNotLeave,
    OrderStep,
    QuoteReadinessFacts,
    StepFacts,
    next_steps,
    plan_step,
)
from nha_trang_laundry_domain.orders import OrderState, OrderTransitionError
from nha_trang_laundry_domain.settlement import (
    DELIVERY_REQUIRES_PAYMENT,
    GOODS_NOT_READY_FOR_HANDOVER,
    RELEASE_REQUIRES_PAYMENT,
    QuotedTotal,
    SettlementShape,
    delivery_refusal,
)

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
B = OrderBalanceStatus
P = ProductionStatus

#: The four modes the review's matrix names: counter, pickup, delivery, pickup + delivery.
MODES = (
    FulfillmentMode.SELF_DROP_SELF_COLLECT,
    FulfillmentMode.PICKUP_ONLY,
    FulfillmentMode.RETURN_ONLY,
    FulfillmentMode.PICKUP_AND_RETURN,
)
#: unpaid, a deposit, paid in full, and charged to the customer's account (PAYMENT-002).
BALANCES = (B.UNPAID, B.PARTIALLY_PAID, B.PAID, B.ON_ACCOUNT)
LEAVES = {B.PAID, B.ON_ACCOUNT}


def _facts(
    mode: FulfillmentMode,
    balance: OrderBalanceStatus,
    *,
    production: ProductionStatus = P.READY_AT_STORE,
) -> StepFacts:
    """A running order in `mode` with `balance`, finished and on the shelf unless told otherwise.

    The settlement shape and the collection flag are the ones each money state really carries:
    a prepaid order (`PAID`) has its prepaid shape and has not been collected; an account order
    (`ON_ACCOUNT`) that the customer collects was handed over by the account charge itself.
    """

    returns = mode in MODES_EXPECTING_RETURN
    shape = None
    if balance is B.PAID:
        shape = (
            SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY
            if returns
            else SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION
        )
    return StepFacts(
        state=OrderState(
            commercial=CommercialOrderStatus.ACTIVE,
            intake=IntakeStatus.ACCEPTED,
            production=production,
            fulfillment_mode=mode,
            balance=balance,
            required_delivery_legs_succeeded=False,
            self_collection_recorded=balance is B.ON_ACCOUNT and not returns,
            production_accepted_at=NOW,
            production_resume_status=None,
        ),
        quote_readiness=QuoteReadinessFacts(True, True, True, True),
        quoted_total=QuotedTotal(110_000, 110_000),
        settlement_shape=shape,
        pickup_leg_succeeded=True,
    )


def _release(facts: StepFacts) -> object:
    return plan_step(
        OrderStep.RELEASE, facts, slot_approved=False, custody_resolution=None, accepted_at=NOW
    )


MATRIX = [
    pytest.param(mode, balance, id=f"{mode.value}-{balance.value}")
    for mode in MODES
    for balance in BALANCES
]


@pytest.mark.parametrize(("mode", "balance"), MATRIX)
def test_release_obeys_the_money_rule_for_every_mode(
    mode: FulfillmentMode, balance: OrderBalanceStatus
) -> None:
    facts = _facts(mode, balance)
    if balance in LEAVES:
        plan = _release(facts)
        assert [item.production_target for item in plan] == [P.RELEASED]  # type: ignore[attr-defined]
        return
    with pytest.raises(GoodsMayNotLeave) as caught:
        _release(facts)
    assert caught.value.reason_code == RELEASE_REQUIRES_PAYMENT
    # Still the domain's transition refusal for every older reader of `plan_step`.
    assert isinstance(caught.value, OrderTransitionError)
    assert str(caught.value).startswith("INVALID_STATE_TRANSITION: RELEASE_REQUIRES_PAYMENT")


@pytest.mark.parametrize(("mode", "balance"), MATRIX)
def test_the_step_list_offers_a_way_out_only_when_the_goods_may_leave(
    mode: FulfillmentMode, balance: OrderBalanceStatus
) -> None:
    """`next_steps` is `plan_step` and the leg rule asked in advance: never a door that refuses."""

    listed = {item.step: item for item in next_steps(_facts(mode, balance))}
    leaving = {OrderStep.RELEASE, OrderStep.HAND_OVER, OrderStep.DELIVERY_RETURN} & set(listed)
    returns = mode in MODES_EXPECTING_RETURN
    if balance in LEAVES:
        assert leaving, listed
        assert (OrderStep.DELIVERY_RETURN in listed) is returns
        assert OrderStep.TAKE_PAYMENT not in listed
    else:
        assert not leaving, listed
        # The way forward is the money: taking it is the page's big button.
        assert listed[OrderStep.TAKE_PAYMENT].primary


@pytest.mark.parametrize(("mode", "balance"), MATRIX)
def test_a_return_trip_of_either_outcome_asks_the_same_two_questions(
    mode: FulfillmentMode, balance: OrderBalanceStatus
) -> None:
    """`delivery_refusal` is what the leg route asks, for a succeeded and a failed trip alike."""

    expected = None if balance in LEAVES else DELIVERY_REQUIRES_PAYMENT
    assert delivery_refusal(balance, P.READY_AT_STORE) == expected
    assert delivery_refusal(balance, P.RELEASED) == expected
    for unfinished in (P.NOT_STARTED, P.QUEUED, P.IN_PROCESS, P.QUALITY_CHECK, P.ON_HOLD):
        # The laundry's own state is asked first: unfinished is refused for that, paid or not.
        assert delivery_refusal(balance, unfinished) == GOODS_NOT_READY_FOR_HANDOVER


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("production", [P.IN_PROCESS, P.QUALITY_CHECK, P.ON_HOLD])
def test_unfinished_laundry_is_refused_for_what_it_is_not_for_money(
    mode: FulfillmentMode, production: ProductionStatus
) -> None:
    facts = _facts(mode, B.UNPAID, production=production)
    with pytest.raises(OrderTransitionError) as caught:
        _release(facts)
    assert not isinstance(caught.value, GoodsMayNotLeave)
    listed = {item.step for item in next_steps(facts)}
    assert OrderStep.DELIVERY_RETURN not in listed
    assert OrderStep.RELEASE not in listed


def test_the_review_case_an_unpaid_delivery_order_is_neither_released_nor_offered_a_trip() -> None:
    """The exact combination the review named: unpaid delivery, finished, on the shelf."""

    facts = _facts(FulfillmentMode.PICKUP_AND_RETURN, B.UNPAID)
    with pytest.raises(GoodsMayNotLeave):
        _release(facts)
    steps = next_steps(facts)
    assert [item.step for item in steps if item.primary] == [OrderStep.TAKE_PAYMENT]
    assert not {OrderStep.RELEASE, OrderStep.DELIVERY_RETURN} & {item.step for item in steps}
