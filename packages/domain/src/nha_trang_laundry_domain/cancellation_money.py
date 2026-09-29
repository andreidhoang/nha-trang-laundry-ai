"""What a cancellation may not do to money a remedy already moved. `MONEY-LIFECYCLE-009` (M3, M7).

A cancellation in this system never charges the customer: `NOT_RECEIVED` takes nothing, and the two
`DEC-024` resolutions that end a paid order (`RETURNED_UNWASHED_REFUNDED`, `SHOP_FAULT_NO_CHARGE`)
refund everything the ledger holds. The one exception is the owner's disposal of unclaimed laundry
(`UNCLAIMED_DISPOSED`, `DEC-036`), where what was paid is kept.

Two kinds of remedy money meet that rule, and neither has a decided answer:

* **M3 -- a money remedy was executed for this order.** `DEC-004` issued the customer a credit from
  it (a late-delivery 10%, damage or loss compensation), spent since or not. Cancelling the order
  without charging then pays the customer twice: the whole bill back, and the credit for the same
  bill. `DEC-031` rule 3 says "refunding and compensating on one order is exactly where money can be
  paid twice" and that the late-delivery credit is refused on a refunded bill -- about a remedy
  proposed *after* a refund. The reverse order has no netting rule, and no decision lets an issued
  credit be voided (`0042`: "the only update to a remedy credit is its redemption").
* **M7 -- the order's bill spent a remedy credit.** The credit was a debt the shop owed the customer
  (`DEC-030`), paid off against this bill. Cancelling without charging would lose it: `0042` forbids
  handing a spent credit back, and no decision reissues one.

Unknown means stop. Both are refused, by name, with a Vietnamese sentence that names the credit and
says what to do instead: keep the order running and finish it as usual (the customer pays and
collects, the credit stands), and take the case to the owner. The rule that would net them is
listed as an open decision in the slice report.

Pure: no clock, no database, no environment. Integer đồng only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from nha_trang_laundry_domain.catalog import CustodyResolution
from nha_trang_laundry_domain.remedies import RemedyKind


class CancellationMoneyRefusal(StrEnum):
    """Why a cancellation is refused for the remedy money on the order. Every one that applies."""

    #: M3: a money remedy was executed for this order (a credit issued from it).
    CANCEL_AFTER_MONEY_REMEDY = "CANCEL_AFTER_MONEY_REMEDY"
    #: M7: the order's bill spent a remedy credit, which the cancellation would lose.
    CANCEL_WOULD_LOSE_SPENT_CREDIT = "CANCEL_WOULD_LOSE_SPENT_CREDIT"


@dataclass(frozen=True, slots=True)
class RemedyCreditFact:
    """One remedy credit as the cancellation sees it: what it was for, and how much."""

    kind: RemedyKind
    amount_vnd: int


@dataclass(frozen=True, slots=True)
class CancellationMoneyRefused:
    refusals: tuple[CancellationMoneyRefusal, ...]
    #: The sentence the counter reads at the control: which credit, why, and what to do.
    reason_vi: str

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(refusal.value for refusal in self.refusals)


#: `RemedyKind` in the counter's words -- the same words the order page's credit rows use.
REMEDY_KIND_VI: Final = {
    RemedyKind.DAMAGE_COMPENSATION: "Bồi thường món bị hỏng",
    RemedyKind.LATE_DELIVERY_CREDIT: "Giảm trừ do giao trễ",
    RemedyKind.LOST_ITEM: "Mất đồ",
    RemedyKind.FREE_REWASH: "Giặt lại miễn phí",
}

#: What to do instead, said once for both refusals.
WAY_FORWARD_VI: Final = "Giữ đơn, làm tiếp và báo chủ tiệm."


def cancellation_money_refusal(
    *,
    resolution: CustodyResolution | None,
    issued_from_order: Sequence[RemedyCreditFact],
    spent_on_order: Sequence[RemedyCreditFact],
) -> CancellationMoneyRefused | None:
    """Refuse a cancellation that would pay a remedy twice or lose a spent credit, else `None`.

    `issued_from_order` are the credits executed remedies issued from this order (spent or not);
    `spent_on_order` the credits this order's bill spent. `resolution` is the custody resolution the
    cancellation carries, `None` for a direct cancellation before work began.
    """

    for credit in (*issued_from_order, *spent_on_order):
        amount = credit.amount_vnd
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError("a remedy credit is a positive whole number of đồng")
    if resolution is CustodyResolution.UNCLAIMED_DISPOSED:
        # `DEC-036`: what the customer paid is kept, and a credit spent on the bill was paid.
        return None
    refusals: list[CancellationMoneyRefusal] = []
    sentences: list[str] = []
    if issued_from_order:
        refusals.append(CancellationMoneyRefusal.CANCEL_AFTER_MONEY_REMEDY)
        sentences.append(
            f"Không huỷ được: khách đã nhận {_credits_vi(issued_from_order)} từ đơn này — huỷ "
            "không thu tiền là trả hai lần."
        )
    if spent_on_order:
        refusals.append(CancellationMoneyRefusal.CANCEL_WOULD_LOSE_SPENT_CREDIT)
        spent = _credits_vi(spent_on_order)
        sentences.append(
            f"Đơn cũng đã dùng {spent} của khách — huỷ thì khoản đó mất."
            if issued_from_order
            else f"Không huỷ được: đơn đã dùng {spent} của khách — huỷ thì khoản đó mất."
        )
    if not refusals:
        return None
    return CancellationMoneyRefused(
        refusals=tuple(refusals), reason_vi=" ".join((*sentences, WAY_FORWARD_VI))
    )


def _credits_vi(credits: Sequence[RemedyCreditFact]) -> str:
    """ "khoản Giảm trừ do giao trễ 11.000 ₫" -- each credit by what it was for and its amount."""

    named = ", ".join(
        f"{REMEDY_KIND_VI.get(credit.kind, credit.kind.value)} {_vnd_text(credit.amount_vnd)}"
        for credit in credits
    )
    return f"khoản {named}"


def _vnd_text(amount: int) -> str:
    """`11000` -> `11.000 ₫`: the Vietnamese thousands separator, as `unclaimed` writes it."""

    return f"{amount:,}".replace(",", ".") + " ₫"


__all__ = [
    "REMEDY_KIND_VI",
    "WAY_FORWARD_VI",
    "CancellationMoneyRefusal",
    "CancellationMoneyRefused",
    "RemedyCreditFact",
    "cancellation_money_refusal",
]
