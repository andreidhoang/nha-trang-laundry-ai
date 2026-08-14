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

MAX_SETTLEMENT_VND: Final = 9_007_199_254_740_991


class SettlementShape(StrEnum):
    """The one supported shape. An enum of one, so adding a second is a visible decision."""

    EXACT_PAYMENT_SELF_COLLECTION = "EXACT_PAYMENT_SELF_COLLECTION"


class SettlementRefusal(StrEnum):
    """Why a settlement is not the supported shape. Each names what a human must resolve."""

    #: The order's quote presents no total to pay, because its delivery fee is unresolved (DEC-003).
    NO_PRESENTABLE_TOTAL = "NO_PRESENTABLE_TOTAL"
    #: The quote is a range, so there is no single amount the customer owes (DEC-001).
    TOTAL_IS_A_RANGE = "TOTAL_IS_A_RANGE"
    #: Anything other than the exact total in one settlement: part payment, deposit, overpayment,
    #: instalments, or credit terms. All of it is DEC-010.
    AMOUNT_IS_NOT_THE_EXACT_TOTAL = "AMOUNT_IS_NOT_THE_EXACT_TOTAL"
    #: The goods did not go back to the customer at the counter, so a delivery leg decided it
    #: (DEC-003), and delivery is not built.
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
) -> SettlementOutcome:
    """Decide whether this is the one supported settlement shape.

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
    if not collected_by_customer:
        return _refuse(SettlementRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER)
    return SettlementAccepted(SettlementShape.EXACT_PAYMENT_SELF_COLLECTION, quoted.minimum_vnd)


def _refuse(refusal: SettlementRefusal) -> SettlementNotSupported:
    return SettlementNotSupported(refusal, REFUSAL_DECISIONS[refusal])


def _valid_amount(value: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= (MAX_SETTLEMENT_VND)
    )


__all__ = [
    "REFUSAL_DECISIONS",
    "QuotedTotal",
    "SettlementAccepted",
    "SettlementNotSupported",
    "SettlementOutcome",
    "SettlementRefusal",
    "SettlementShape",
    "evaluate_settlement",
]
