"""What a cancellation does to money a remedy already moved. `MONEY-LIFECYCLE-009` (M3, M7).

A cancellation in this system never charges the customer: `NOT_RECEIVED` takes nothing, and the two
`DEC-024` resolutions that end a paid order (`RETURNED_UNWASHED_REFUNDED`, `SHOP_FAULT_NO_CHARGE`)
refund what the ledger holds. The one exception is the owner's disposal of unclaimed laundry
(`UNCLAIMED_DISPOSED`, `DEC-036`), where what was paid is kept -- and so is every credit.

Two kinds of remedy money meet that rule. The owner decided both on 2026-09-30 (delegated,
`docs/DECISION_RECORD_ROUND9_2026-09-30.md`); the principle is that a customer ends each order
at exactly what the shop's own records say -- *paid nothing, got nothing extra*, never less, never
more:

* **`DEC-045` (M3) -- a money remedy was executed for this order.** `DEC-004` issued the customer a
  credit from it (a late-delivery 10%, damage or loss compensation). Every executed money remedy in
  this system is such a credit (`remedy_credits`); none pays cash out. On a cancellation without
  charge:

  - a credit from this order that is still **unspent** is **voided** in the same transaction;
  - a credit from this order already **spent** elsewhere is **netted**: the refund is what was paid
    less its face value, never below 0.

* **`DEC-046` (M7) -- the order's bill spent a remedy credit.** The customer's cash comes back and
  so does the credit: a **new credit of the same face value** is issued, linked to the original
  (`remedy_credits.reissue_of`, migration `0066`) and to the refund by audit. `DEC-045` applies
  first, then `DEC-046`, when one order both spent a credit and generated one.

A credit reissued under `DEC-046` stands in for the one it replaces: the caller passes each chain of
credits issued from the order by its latest link, so a credit spent, reissued and then unspent is
voided once, and never netted as well.

Pure: no clock, no database, no environment. Integer đồng only; the one comparison that bounds the
netting (`min`) is the "never below 0" of the decision, and nothing divides.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from nha_trang_laundry_domain.catalog import CustodyResolution
from nha_trang_laundry_domain.remedies import RemedyKind
from nha_trang_laundry_domain.settlement import MAX_SETTLEMENT_VND

DEC_NO_DOUBLE_COMPENSATION: Final = "DEC-045"
DEC_SPENT_CREDIT_REISSUED: Final = "DEC-046"


class CreditState(StrEnum):
    """Where one remedy credit stands (`0042`, `0066`)."""

    UNSPENT = "UNSPENT"
    SPENT = "SPENT"
    VOIDED = "VOIDED"


@dataclass(frozen=True, slots=True)
class RemedyCreditFact:
    """One remedy credit as the cancellation sees it: which, what for, how much, and its state."""

    credit_id: UUID
    kind: RemedyKind
    amount_vnd: int
    state: CreditState


@dataclass(frozen=True, slots=True)
class CancellationMoneyPlan:
    """What a cancellation does to remedy money, decided before anything is written."""

    #: `DEC-045`: unspent credits issued from this order, voided with the cancellation.
    voided: tuple[RemedyCreditFact, ...]
    #: `DEC-045`: credits issued from this order and spent elsewhere, netted from the refund.
    netted: tuple[RemedyCreditFact, ...]
    #: What the refund would return before netting (the ledger's sum), 0 when nothing is refunded.
    refundable_vnd: int
    #: What is actually taken off the refund: the netted credits' face value, never more than
    #: `refundable_vnd` (the refund never goes below 0).
    netted_vnd: int
    #: `DEC-046`: credits this order's bill spent, each reissued at its face value.
    reissued: tuple[RemedyCreditFact, ...]
    #: The lines the refund sheet, the order page and the receipt print, in order.
    lines_vi: tuple[str, ...]

    @property
    def refund_vnd(self) -> int:
        """What goes back to the customer: the refundable amount less the netting."""

        return self.refundable_vnd - self.netted_vnd

    @property
    def moves_remedy_money(self) -> bool:
        return bool(self.voided or self.netted or self.reissued)


#: `RemedyKind` in the counter's words -- the same words the order page's credit rows use.
REMEDY_KIND_VI: Final = {
    RemedyKind.DAMAGE_COMPENSATION: "Bồi thường món bị hỏng",
    RemedyKind.LATE_DELIVERY_CREDIT: "Giảm trừ do giao trễ",
    RemedyKind.LOST_ITEM: "Mất đồ",
    RemedyKind.FREE_REWASH: "Giặt lại miễn phí",
}


def cancellation_money_plan(
    *,
    resolution: CustodyResolution | None,
    refundable_vnd: int,
    issued_from_order: Sequence[RemedyCreditFact],
    spent_on_order: Sequence[RemedyCreditFact],
) -> CancellationMoneyPlan:
    """`DEC-045` then `DEC-046` for one cancellation.

    `refundable_vnd` is what the cancellation would refund before netting -- the order's ledger sum
    when it refunds, 0 when it refunds nothing (unpaid, or not a refunding resolution).
    `issued_from_order` is each credit chain issued from this order by its latest link;
    `spent_on_order` the credits this order's bill spent. `resolution` is the custody resolution
    the cancellation carries, `None` for a direct cancellation before work began.
    """

    if (
        not isinstance(refundable_vnd, int)
        or isinstance(refundable_vnd, bool)
        or not 0 <= refundable_vnd <= MAX_SETTLEMENT_VND
    ):
        raise ValueError("the refundable amount is a non-negative whole number of đồng")
    for credit in (*issued_from_order, *spent_on_order):
        amount = credit.amount_vnd
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError("a remedy credit is a positive whole number of đồng")
    if resolution is CustodyResolution.UNCLAIMED_DISPOSED:
        # `DEC-036`: what the customer paid is kept, and a credit spent on the bill was paid.
        return CancellationMoneyPlan((), (), refundable_vnd, 0, (), ())
    voided = tuple(c for c in issued_from_order if c.state is CreditState.UNSPENT)
    netted = tuple(c for c in issued_from_order if c.state is CreditState.SPENT)
    face = sum(c.amount_vnd for c in netted)
    netted_vnd = min(refundable_vnd, face)
    # A credit the bill spent comes back once; one voided since is not the customer's to get back.
    reissued = tuple(c for c in spent_on_order if c.state is CreditState.SPENT)
    lines: list[str] = []
    for credit in voided:
        lines.append(f"Khoản {_credit_vi(credit)} khách chưa dùng được huỷ cùng đơn.")
    if netted:
        spent = ", ".join(_credit_vi(c) for c in netted)
        if refundable_vnd == 0:
            lines.append(
                f"Khoản {spent} khách đã dùng ở đơn khác; đơn này không hoàn tiền nên không có "
                "gì để trừ."
            )
        else:
            lines.append(
                f"Trừ khoản {spent} khách đã dùng: hoàn {_vnd_text(refundable_vnd - netted_vnd)} "
                f"thay vì {_vnd_text(refundable_vnd)}"
                + (" (không trừ quá số khách đã trả)." if netted_vnd < face else ".")
            )
    for credit in reissued:
        lines.append(f"Cấp lại cho khách khoản {_credit_vi(credit)} đã dùng cho đơn này.")
    return CancellationMoneyPlan(
        voided=voided,
        netted=netted,
        refundable_vnd=refundable_vnd,
        netted_vnd=netted_vnd,
        reissued=reissued,
        lines_vi=tuple(lines),
    )


def _credit_vi(credit: RemedyCreditFact) -> str:
    """ "Giảm trừ do giao trễ 11.000 ₫" -- a credit by what it was for and its amount."""

    return f"{REMEDY_KIND_VI.get(credit.kind, credit.kind.value)} {_vnd_text(credit.amount_vnd)}"


def _vnd_text(amount: int) -> str:
    """`11000` -> `11.000 ₫`: the Vietnamese thousands separator, as `unclaimed` writes it."""

    return f"{amount:,}".replace(",", ".") + " ₫"


__all__ = [
    "DEC_NO_DOUBLE_COMPENSATION",
    "DEC_SPENT_CREDIT_REISSUED",
    "REMEDY_KIND_VI",
    "CancellationMoneyPlan",
    "CreditState",
    "RemedyCreditFact",
    "cancellation_money_plan",
]
