"""Decide whether a settlement is the one shape this system supports, or none of them.

`transition_commercial` refuses `COMPLETED` unless the balance is settled and the goods have gone
back to the customer. Both conditions were unreachable: `OrderRepository.create` hardcodes
`balance_status = 'UNPAID'`, the only `UPDATE orders` statement touches neither fulfilment flag, and
`CreateOrderCommand` exposes none of them. Every order was born unpaid and uncollected with no way
to change either, so **no order this system creates could ever reach `COMPLETED`**.

The guard is right. What was missing is the command that supplies the evidence it asks for.

This module is the whole of the decision, and it is deliberately small. Settlement is where scope
creep would hurt most, because every interesting variation — a deposit, a part payment, an
overpayment, a B2B account, a refund — is a policy question the business owner has not answered.
`DEC-010` holds those open, so exactly one shape is supported here:

    the customer paid the quoted total, in full, in one settlement, at handover, and collected the
    goods themselves.

Nothing in that sentence is a judgement. The amount is compared against the immutable quote snapshot
the order already references; the staff member attests to an event they witnessed at the counter.
Everything else returns `NOT_SUPPORTED` naming the decision that owns it — not rounded, not accepted
partially, and not recorded as something it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nha_trang_laundry_domain.catalog import (
    MODES_EXPECTING_RETURN,
    CommercialOrderStatus,
    FulfillmentMode,
    OrderBalanceStatus,
    ProductionStatus,
)

MAX_SETTLEMENT_VND: Final = 9_007_199_254_740_991


class SettlementShape(StrEnum):
    """The supported shapes. Adding one is a visible decision: the second was `DEC-023`, the third
    `DEC-032`.

    All three are the same money: the exact quoted total, in full, in one payment. They differ only
    in when it is paid and where the laundry goes afterwards, which is why `DEC-010` -- partial
    payment, deposits, instalments, credit -- is untouched by the second and the third.
    """

    #: Paid at the counter and carried home by the customer. The original and the common case.
    EXACT_PAYMENT_SELF_COLLECTION = "EXACT_PAYMENT_SELF_COLLECTION"
    #: Paid at the counter when the laundry was dropped off, to be delivered afterwards. `DEC-023`
    #: (2026-08-26): the shop is never owed money by somebody holding its laundry, and no driver
    #: carries cash. Arrival is attested by a delivery leg, not by this settlement.
    EXACT_PAYMENT_PREPAID_DELIVERY = "EXACT_PAYMENT_PREPAID_DELIVERY"
    #: Paid at the counter before the laundry is finished, to be collected at the counter later.
    #: `DEC-032` (2026-09-25, delegated): a walk-in paying at drop-off, as `DEC-023` allows for
    #: delivery, and for the same reason -- the alternative was ticking "collected" for shirts still
    #: in the machine. Its addendum the same day extends it to `PICKUP_ONLY`: the courier fetched
    #: the laundry, and the customer comes by the counter and pays before it is ready. Pickup is
    #: attested separately, by the staff member who hands the goods over (`evaluate_collection`).
    EXACT_PAYMENT_PREPAID_SELF_COLLECTION = "EXACT_PAYMENT_PREPAID_SELF_COLLECTION"


class SettlementRefusal(StrEnum):
    """Why a settlement is not the supported shape. Each names what a human must resolve."""

    #: The order's quote presents no total to pay, because its delivery fee is unresolved (DEC-003).
    NO_PRESENTABLE_TOTAL = "NO_PRESENTABLE_TOTAL"
    #: The quote is a range, so there is no single amount the customer owes (DEC-001).
    TOTAL_IS_A_RANGE = "TOTAL_IS_A_RANGE"
    #: Anything other than the exact total in one settlement: part payment, deposit, overpayment,
    #: instalments, or credit terms. All of it is DEC-010.
    AMOUNT_IS_NOT_THE_EXACT_TOTAL = "AMOUNT_IS_NOT_THE_EXACT_TOTAL"
    #: "Collected at the counter" on an order whose mode says the laundry travels back to the
    #: customer. Since `DEC-023` a delivery reaches its customer by a leg (DEC-003), so the tick and
    #: the mode disagree and one of them is wrong. Every self-collect mode, `PICKUP_ONLY` included
    #: since the `DEC-032` addendum, may now pay either with the tick or before it.
    COLLECTION_WAS_NOT_BY_THE_CUSTOMER = "COLLECTION_WAS_NOT_BY_THE_CUSTOMER"


#: Which open decision owns each refusal, so a caller can be told what is actually blocking them
#: rather than only that something is.
REFUSAL_DECISIONS: Final = {
    SettlementRefusal.NO_PRESENTABLE_TOTAL: "DEC-003",
    SettlementRefusal.TOTAL_IS_A_RANGE: "DEC-001",
    SettlementRefusal.AMOUNT_IS_NOT_THE_EXACT_TOTAL: "DEC-010",
    SettlementRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER: "DEC-003",
}


@dataclass(frozen=True, slots=True)
class QuotedTotal:
    """What the order's bound quote revision says the customer owes.

    Both bounds are carried rather than one number, because a range quote has no single total and
    the difference has to survive as far as the decision. Collapsing them earlier would turn "we do
    not know what this costs" into a number somebody could take money against.
    """

    minimum_vnd: int | None
    maximum_vnd: int | None


@dataclass(frozen=True, slots=True)
class SettlementAccepted:
    """The supported shape, with the amount the domain agrees was owed."""

    shape: SettlementShape
    expected_total_vnd: int


@dataclass(frozen=True, slots=True)
class SettlementNotSupported:
    """Refused, with the reason and the decision that would have to be made to allow it."""

    refusal: SettlementRefusal
    decision: str

    @property
    def reason_code(self) -> str:
        return self.refusal.value


SettlementOutcome = SettlementAccepted | SettlementNotSupported


def evaluate_settlement(
    *,
    quoted: QuotedTotal,
    tendered_vnd: int,
    collected_by_customer: bool,
    fulfillment_mode: FulfillmentMode,
) -> SettlementOutcome:
    """Decide whether this is a supported settlement shape.

    `fulfillment_mode` is the order's own field rather than a second boolean saying the same thing,
    because two ways to state one fact can disagree and then something has to decide which is
    right.

    There is no tolerance and no rounding. `tendered_vnd` must equal the quoted total exactly: VND
    has no minor unit, the quote's total is an integer the engine already rounded once, and
    rounding again here would give this module an opinion about money the pricing engine did not.
    """
    if not _valid_amount(tendered_vnd):
        return _refuse(SettlementRefusal.AMOUNT_IS_NOT_THE_EXACT_TOTAL)
    if quoted.minimum_vnd is None or quoted.maximum_vnd is None:
        # No delivery fee resolved means the quote presents no total at all, and taking money
        # against a service subtotal would charge the customer for a number nobody quoted them.
        return _refuse(SettlementRefusal.NO_PRESENTABLE_TOTAL)
    if quoted.minimum_vnd != quoted.maximum_vnd:
        return _refuse(SettlementRefusal.TOTAL_IS_A_RANGE)
    if not _valid_amount(quoted.minimum_vnd):
        return _refuse(SettlementRefusal.NO_PRESENTABLE_TOTAL)
    if tendered_vnd != quoted.minimum_vnd:
        # Under, over, or a deposit: all DEC-010, and all refused rather than partially recorded.
        return _refuse(SettlementRefusal.AMOUNT_IS_NOT_THE_EXACT_TOTAL)
    if collected_by_customer:
        if fulfillment_mode in MODES_EXPECTING_RETURN:
            # The goods were handed back at the counter on an order that says they travel. One of
            # the two is wrong and this module will not guess which.
            return _refuse(SettlementRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER)
        return SettlementAccepted(SettlementShape.EXACT_PAYMENT_SELF_COLLECTION, quoted.minimum_vnd)
    if fulfillment_mode not in MODES_EXPECTING_RETURN:
        # The customer collects at the counter, and has paid before doing so. `DEC-032`: a walk-in
        # paying at drop-off. Its addendum (2026-09-25): a `PICKUP_ONLY` customer -- the shop's
        # courier fetched the laundry -- paying at the counter before it is finished. Same money,
        # same moment of handover still to come, so the same shape. The courier took nothing
        # (`DEC-023`): this is money handed over the counter, never on the doorstep.
        #
        # The handover is recorded later, at pickup, by the staff member who makes it, and
        # `transition_commercial` refuses to complete the order until it is --
        # `self_collection_recorded` does not move for this shape. That record is what once did
        # not exist: before `DEC-032` a `PICKUP_ONLY` order paid here had no permitted action
        # left that could close it, which is why this branch was refused until now.
        return SettlementAccepted(
            SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION, quoted.minimum_vnd
        )
    # `DEC-023`: paid in full at the counter, laundry still to travel. Arrival is a delivery leg's
    # fact, not this one's, and `transition_commercial` still refuses to complete the order until a
    # leg says the customer has their laundry.
    return SettlementAccepted(SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY, quoted.minimum_vnd)


#: Production states in which the laundry is finished and may be put in the customer's hands.
#:
#: `READY_AT_STORE` is washed, checked and waiting; `RELEASED` is let go from production. Every
#: earlier state is laundry still being worked on, and `ON_HOLD` and `EXCEPTION` are laundry
#: somebody stopped on purpose. The staging review found `collected_by_customer = true` accepted at
#: any of them -- a handover recorded for shirts still in the machine -- and `DEC-032` makes the
#: same question the precondition of the pickup command. One rule for both, stated once.
HANDOVER_READY_PRODUCTION: Final = frozenset(
    {ProductionStatus.READY_AT_STORE, ProductionStatus.RELEASED}
)

#: The refusal for handing over laundry that is not finished. A state, not an open decision, so it
#: is not a `SettlementRefusal` and names no `DEC-`: the next step is to finish the washing.
GOODS_NOT_READY_FOR_HANDOVER: Final = "GOODS_NOT_READY_FOR_HANDOVER"


def handover_refusal(production: ProductionStatus) -> str | None:
    """`GOODS_NOT_READY_FOR_HANDOVER` unless the laundry is finished; otherwise `None`."""

    return None if production in HANDOVER_READY_PRODUCTION else GOODS_NOT_READY_FOR_HANDOVER


class CollectionRefusal(StrEnum):
    """Why the counter may not yet record that a customer who paid in advance took their laundry.

    States, not open decisions: each says what has to happen first, and none is a policy question
    somebody has yet to answer -- which is why, unlike `SettlementRefusal`, none names a `DEC-`.
    """

    #: Only a running order hands anything over. A cancelled one returns goods under `DEC-024`.
    ORDER_NOT_ACTIVE = "ORDER_NOT_ACTIVE"
    #: Already recorded -- by an earlier pickup, or by a customer who paid at pickup, whose
    #: settlement records the handover in the same step.
    ALREADY_COLLECTED = "ALREADY_COLLECTED"
    #: Not paid. A customer who pays at pickup is recorded by the settlement, in one step.
    COLLECTION_REQUIRES_PAYMENT = "COLLECTION_REQUIRES_PAYMENT"
    #: Paid, but not in advance for collection at the counter: a delivery reaches its customer by a
    #: leg (`DEC-023`), and a customer who paid at pickup was recorded by that settlement.
    NOT_A_PREPAID_SELF_COLLECTION = "NOT_A_PREPAID_SELF_COLLECTION"
    #: The laundry is not finished, so there is nothing to hand over yet.
    GOODS_NOT_READY_FOR_HANDOVER = GOODS_NOT_READY_FOR_HANDOVER


def evaluate_collection(
    *,
    commercial: CommercialOrderStatus,
    production: ProductionStatus,
    balance: OrderBalanceStatus,
    settlement_shape: SettlementShape | None,
    self_collection_recorded: bool,
) -> CollectionRefusal | None:
    """Decide whether a pickup may be recorded now. `None` means it may. `DEC-032`.

    Every input is a stored fact about the order, read under its row lock by the caller. The person
    at the counter supplies nothing but the fact that they are the one handing the goods over.
    """

    if commercial is not CommercialOrderStatus.ACTIVE:
        return CollectionRefusal.ORDER_NOT_ACTIVE
    if self_collection_recorded:
        return CollectionRefusal.ALREADY_COLLECTED
    if balance is not OrderBalanceStatus.PAID:
        return CollectionRefusal.COLLECTION_REQUIRES_PAYMENT
    if settlement_shape is not SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION:
        return CollectionRefusal.NOT_A_PREPAID_SELF_COLLECTION
    if handover_refusal(production) is not None:
        return CollectionRefusal.GOODS_NOT_READY_FOR_HANDOVER
    return None


def _refuse(refusal: SettlementRefusal) -> SettlementNotSupported:
    return SettlementNotSupported(refusal, REFUSAL_DECISIONS[refusal])


def _valid_amount(value: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= (MAX_SETTLEMENT_VND)
    )


__all__ = [
    "GOODS_NOT_READY_FOR_HANDOVER",
    "HANDOVER_READY_PRODUCTION",
    "REFUSAL_DECISIONS",
    "CollectionRefusal",
    "QuotedTotal",
    "SettlementAccepted",
    "SettlementNotSupported",
    "SettlementOutcome",
    "SettlementRefusal",
    "SettlementShape",
    "evaluate_collection",
    "evaluate_settlement",
    "handover_refusal",
]
