"""Part payments with a method, as a Vietnamese counter takes them. `PAYMENT-001`, `DEC-035`.

`DEC-010` deferred everything but "the exact total, in full, in one settlement". `DEC-035`
(2026-09-25) supersedes that deferral for the counter:

* **Methods.** `TIEN_MAT` (cash) and `CHUYEN_KHOAN` (bank transfer to the shop's account). A
  transfer is recorded when the staff member has seen it arrive in the shop's bank app; the
  optional last characters of the bank reference are kept, and nothing is verified automatically,
  because no bank feed exists.
* **Deposits and part payments (đặt cọc).** Any amount from 1 đồng up to what is still owed, as many
  times as needed, each with its method.
* **No overpayment.** The counter gives change. A payment above what is still owed is refused
  (`OVERPAYMENT_REFUSED`); no store credit is created from change.
* **Goods leave only when paid.** `settlement.goods_may_leave` (re-exported here) is the one
  statement of that rule, and the seam where `PAYMENT-002` (an account customer within its limit)
  will be added.

What is owed is a **list of charges**, not one number. Today the list has exactly one entry, the
bound quote revision's presentable total. `UNCLAIMED-001` adds a storage-fee charge to the same
list (`DEC-036`), and nothing here has to be redesigned for it: every rule below reads `owed_vnd`,
the sum of the charges.

Pure: no clock, no database, no environment. Integer đồng only; every amount is validated as an
`int` that is not a `bool`, and no division or rounding happens anywhere in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nha_trang_laundry_domain.catalog import CommercialOrderStatus, OrderBalanceStatus
from nha_trang_laundry_domain.settlement import (
    GOODS_MAY_LEAVE_BALANCES,
    MAX_SETTLEMENT_VND,
    QuotedTotal,
    goods_may_leave,
)


class PaymentMethod(StrEnum):
    """How the money reached the shop (`DEC-035`). There is no third way at this counter."""

    #: Cash across the counter.
    TIEN_MAT = "TIEN_MAT"
    #: A bank transfer to the shop's account, seen arriving in the shop's bank app by the staff
    #: member who records it.
    CHUYEN_KHOAN = "CHUYEN_KHOAN"


class ChargeKind(StrEnum):
    """What one line of the amount owed is for.

    One member today. `UNCLAIMED-001` adds the storage fee (`DEC-036`) as a second kind; the list of
    charges is the place it goes, so the payment rules below do not change when it does.
    """

    #: The bound quote revision's presentable total: what the customer agreed to pay for the work.
    QUOTED_TOTAL = "QUOTED_TOTAL"


@dataclass(frozen=True, slots=True)
class OrderCharge:
    """One amount the customer owes on an order, in whole đồng."""

    kind: ChargeKind
    amount_vnd: int


class PaymentRefusal(StrEnum):
    """Why a payment is not recorded. Each is a state or a typing slip, never an open decision."""

    #: The order's quote presents no single total (an unresolved delivery fee, `DEC-003`, or a
    #: range, `DEC-001`), so there is nothing a payment can be measured against.
    NO_PRESENTABLE_TOTAL = "NO_PRESENTABLE_TOTAL"
    #: Only a running order takes money at the counter.
    ORDER_NOT_ACTIVE = "ORDER_NOT_ACTIVE"
    #: Nothing is owed any more: the order is paid, refunded, or on an account.
    NOTHING_OWED = "NOTHING_OWED"
    #: Zero, negative, or not a whole number of đồng.
    PAYMENT_AMOUNT_INVALID = "PAYMENT_AMOUNT_INVALID"
    #: More than is still owed. The counter gives change ("trả lại tiền thừa cho khách"); `DEC-035`
    #: creates no store credit from change.
    OVERPAYMENT_REFUSED = "OVERPAYMENT_REFUSED"
    #: A transfer is recorded only once the staff member has seen it arrive (`DEC-035`).
    TRANSFER_NOT_SEEN = "TRANSFER_NOT_SEEN"
    #: The bank reference is 2 to 12 letters or digits, and only a transfer has one.
    BANK_REF_INVALID = "BANK_REF_INVALID"
    #: "The customer takes the goods now" with money still owed. Goods leave only when paid.
    HANDOVER_REQUIRES_FULL_PAYMENT = "HANDOVER_REQUIRES_FULL_PAYMENT"


#: The sentence the counter reads with `OVERPAYMENT_REFUSED`, stated once in the domain so the API
#: and the console cannot word the rule differently.
OVERPAYMENT_NOTE_VI: Final = "Số tiền lớn hơn số còn lại — trả lại tiền thừa cho khách."

#: The bank reference tail as stored: 2 to 12 upper-case letters or digits. The schema's CHECK in
#: `0056` states the same pattern.
BANK_REF_PATTERN: Final = re.compile(r"^[A-Z0-9]{2,12}$")

#: The balances a payment may be taken against: money is still owed on them.
PAYABLE_BALANCES: Final = frozenset({OrderBalanceStatus.UNPAID, OrderBalanceStatus.PARTIALLY_PAID})


def owed_charges(quoted: QuotedTotal) -> tuple[OrderCharge, ...] | None:
    """What the customer owes, as charges, or `None` when the quote presents no single total.

    `None` is not zero: an unresolved delivery fee or a range is "we do not know what this costs",
    and a payment measured against zero would be refused as an overpayment for the wrong reason.
    """

    if quoted.minimum_vnd is None or quoted.maximum_vnd is None:
        return None
    if quoted.minimum_vnd != quoted.maximum_vnd:
        return None
    if not _whole_vnd(quoted.minimum_vnd):
        return None
    return (OrderCharge(ChargeKind.QUOTED_TOTAL, quoted.minimum_vnd),)


def owed_total(charges: tuple[OrderCharge, ...]) -> int:
    """The sum of the charges, in whole đồng. The one place the domain adds charges up."""

    total = 0
    for charge in charges:
        if not _whole_vnd(charge.amount_vnd):
            raise ValueError("a charge must be a non-negative whole number of đồng")
        total += charge.amount_vnd
    return total


@dataclass(frozen=True, slots=True)
class PaymentPosition:
    """Tổng · Đã trả · Còn lại, for one order. `owed_vnd` and `remaining_vnd` are `None` together,
    exactly when the quote presents no single total."""

    charges: tuple[OrderCharge, ...]
    owed_vnd: int | None
    paid_vnd: int
    remaining_vnd: int | None


def payment_position(charges: tuple[OrderCharge, ...] | None, paid_vnd: int) -> PaymentPosition:
    """What is owed, what is paid (summed by SQL and passed in), and what remains.

    `remaining_vnd` is never negative: the ledger refuses overpayment and `0056` refuses a paid
    order whose payments do not sum to its settlement. A ledger that nonetheless reads more paid
    than owed is a contradiction, and this raises rather than showing a minus sign or a clamped 0.
    """

    if not _whole_vnd(paid_vnd):
        raise ValueError("the paid amount must be a non-negative whole number of đồng")
    if charges is None:
        return PaymentPosition((), None, paid_vnd, None)
    owed = owed_total(charges)
    if paid_vnd > owed:
        raise ValueError("the order's payments exceed what it owes")
    return PaymentPosition(charges, owed, paid_vnd, owed - paid_vnd)


@dataclass(frozen=True, slots=True)
class PaymentAccepted:
    """A payment the domain accepts, and what the order's balance becomes with it."""

    amount_vnd: int
    owed_vnd: int
    paid_after_vnd: int
    remaining_after_vnd: int
    balance_after: OrderBalanceStatus
    #: The staff member's word that the customer takes the goods now; only ever true on the
    #: payment that settles the order.
    collected_by_customer: bool = False

    @property
    def completes(self) -> bool:
        """Whether this payment settles the order in full."""

        return self.balance_after is OrderBalanceStatus.PAID


@dataclass(frozen=True, slots=True)
class PaymentRefused:
    refusal: PaymentRefusal

    @property
    def reason_code(self) -> str:
        return self.refusal.value


PaymentOutcome = PaymentAccepted | PaymentRefused


def evaluate_payment(
    *,
    commercial: CommercialOrderStatus,
    balance: OrderBalanceStatus,
    charges: tuple[OrderCharge, ...] | None,
    paid_vnd: int,
    amount_vnd: int,
    method: PaymentMethod,
    transfer_seen: bool,
    bank_ref_last: str | None,
    collected_by_customer: bool,
) -> PaymentOutcome:
    """Decide whether this payment may be recorded, and what the balance becomes (`DEC-035`).

    Accepts 1 <= `amount_vnd` <= owed - paid. `paid_vnd` is the sum of the order's payment ledger,
    computed by the database under the order's row lock. `bank_ref_last` is the already-normalised
    value (`normalise_bank_ref`). `collected_by_customer` is the staff member's word that the
    customer takes the goods now; it is accepted only with the payment that settles the order, and
    whether the goods are finished is the caller's `handover_refusal`, asked after this.
    """

    if commercial is not CommercialOrderStatus.ACTIVE:
        return PaymentRefused(PaymentRefusal.ORDER_NOT_ACTIVE)
    if balance not in PAYABLE_BALANCES:
        return PaymentRefused(PaymentRefusal.NOTHING_OWED)
    if charges is None:
        return PaymentRefused(PaymentRefusal.NO_PRESENTABLE_TOTAL)
    if not _whole_vnd(amount_vnd):
        return PaymentRefused(PaymentRefusal.PAYMENT_AMOUNT_INVALID)
    if method is PaymentMethod.CHUYEN_KHOAN and not transfer_seen:
        return PaymentRefused(PaymentRefusal.TRANSFER_NOT_SEEN)
    if bank_ref_last is not None and (
        method is not PaymentMethod.CHUYEN_KHOAN or not BANK_REF_PATTERN.fullmatch(bank_ref_last)
    ):
        return PaymentRefused(PaymentRefusal.BANK_REF_INVALID)
    position = payment_position(charges, paid_vnd)
    assert position.owed_vnd is not None and position.remaining_vnd is not None
    if position.owed_vnd == 0 and paid_vnd == 0 and amount_vnd == 0:
        # A bill a credit covered in full. The exact-total settlement accepted 0 for it, and this
        # route must not strand such an order unpaid forever: 0 đồng settles it, and no ledger row
        # is written (a payment row is money that moved, and none did).
        return PaymentAccepted(0, 0, 0, 0, OrderBalanceStatus.PAID, collected_by_customer)
    if amount_vnd < 1:
        return PaymentRefused(PaymentRefusal.PAYMENT_AMOUNT_INVALID)
    if position.remaining_vnd == 0:
        # A ledger that already covers what is owed on a balance still reading unpaid. The balance
        # and the ledger move together (`0056`), so this is not reachable by a command; refusing it
        # as "nothing owed" is the truthful answer if it ever is.
        return PaymentRefused(PaymentRefusal.NOTHING_OWED)
    if amount_vnd > position.remaining_vnd:
        return PaymentRefused(PaymentRefusal.OVERPAYMENT_REFUSED)
    paid_after = paid_vnd + amount_vnd
    remaining_after = position.owed_vnd - paid_after
    balance_after = (
        OrderBalanceStatus.PAID if remaining_after == 0 else OrderBalanceStatus.PARTIALLY_PAID
    )
    if collected_by_customer and balance_after is not OrderBalanceStatus.PAID:
        return PaymentRefused(PaymentRefusal.HANDOVER_REQUIRES_FULL_PAYMENT)
    return PaymentAccepted(
        amount_vnd=amount_vnd,
        owed_vnd=position.owed_vnd,
        paid_after_vnd=paid_after,
        remaining_after_vnd=remaining_after,
        balance_after=balance_after,
        collected_by_customer=collected_by_customer,
    )


def normalise_bank_ref(text: str | None) -> str | None:
    """The bank reference tail as stored: trimmed, inner spaces removed, upper-cased; `None` when
    blank. Whether the result is acceptable is `evaluate_payment`'s question, not this one's."""

    if text is None:
        return None
    cleaned = "".join(text.split()).upper()
    return cleaned or None


def _whole_vnd(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SETTLEMENT_VND
    )


__all__ = [
    "BANK_REF_PATTERN",
    "GOODS_MAY_LEAVE_BALANCES",
    "OVERPAYMENT_NOTE_VI",
    "PAYABLE_BALANCES",
    "ChargeKind",
    "OrderCharge",
    "PaymentAccepted",
    "PaymentMethod",
    "PaymentOutcome",
    "PaymentPosition",
    "PaymentRefusal",
    "PaymentRefused",
    "evaluate_payment",
    "goods_may_leave",
    "normalise_bank_ref",
    "owed_charges",
    "owed_total",
    "payment_position",
]
