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

**Credit chains** (round 9b, `MONEY-RESIDUAL-009B` J3). Order X issues credit c1, c1 is spent on Y,
Y issues c2, c2 is spent on Z... Each customer must end at the same total whichever of those orders
is cancelled first -- the total being what they paid less what came back, less the face value of
the credits they still hold. `DEC-045` then `DEC-046` already give that for every order of
cancellation but one: X is cancelled while c1 is spent, and X's refund does *not* take c1's whole
face value off (it was capped at what X's ledger held -- "never below 0" -- or X refunded nothing).
c1's value then stayed with the customer at X's cancellation, and cancelling Y afterwards would
give it back a second time as a reissued credit. Making that order equal would need a rule nobody
has decided (reissue part of a credit, or take it back from Y's refund), so the later cancellation
is refused instead, by name (`CREDIT_CHAIN_NOT_NETTED`); the owner decides
(`RemedyCreditFact.issuer_uncovered`, read by the repository from X's refund row).

**The preview is the press** (round 9b, J4). What the refund sheet showed before the press is
carried by the press (`CancellationMoneyFigures`): the refund, what is netted, voided and reissued.
A credit can move under the sheet without the order changing -- spent on another order, say -- so
the cancellation is refused (`CANCELLATION_MONEY_CHANGED`) when the figures it would execute are not
the ones the person saw, or when it moves remedy money and the press carried no figures at all.

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


class CancellationMoneyRefusal(StrEnum):
    """Why a cancellation is refused over remedy money, by name."""

    #: J3: the bill spent a credit from an order already cancelled whose refund did not take that
    #: credit's whole face value off; reissuing it would compensate the customer twice.
    CREDIT_CHAIN_NOT_NETTED = "CREDIT_CHAIN_NOT_NETTED"
    #: J4: what the cancellation would do to the money is not what the press was shown.
    CANCELLATION_MONEY_CHANGED = "CANCELLATION_MONEY_CHANGED"


#: What the counter reads for each refusal, ≤ 25 words, beside the press.
CANCELLATION_REFUSAL_VI: Final = {
    CancellationMoneyRefusal.CREDIT_CHAIN_NOT_NETTED: (
        "Đơn này dùng khoản giảm trừ của một đơn đã huỷ mà lúc huỷ chưa trừ lại đủ. Huỷ không "
        "tính tiền sẽ trả khách hai lần — báo chủ tiệm."
    ),
    CancellationMoneyRefusal.CANCELLATION_MONEY_CHANGED: (
        "Số tiền hoàn hoặc khoản giảm trừ của đơn vừa thay đổi. Xem lại số mới rồi bấm Huỷ đơn "
        "lần nữa."
    ),
}


class CancellationMoneyRefused(ValueError):
    """A cancellation refused over remedy money. `str()` is the code."""

    def __init__(self, code: CancellationMoneyRefusal) -> None:
        super().__init__(code.value)
        self.code = code
        self.message_vi = CANCELLATION_REFUSAL_VI[code]


@dataclass(frozen=True, slots=True)
class RemedyCreditFact:
    """One remedy credit as the cancellation sees it: which, what for, how much, and its state."""

    credit_id: UUID
    kind: RemedyKind
    amount_vnd: int
    state: CreditState
    #: J3, for a credit the bill spent: the order it was issued from is already cancelled, and that
    #: cancellation's refund did not take this credit's whole face value off (capped, or nothing
    #: refunded). Not a disposal: `DEC-036` keeps every credit.
    issuer_uncovered: bool = False


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
    #: J3: the cancellation may not go through over remedy money (and why), or `None`.
    refusal: CancellationMoneyRefusal | None = None

    @property
    def refund_vnd(self) -> int:
        """What goes back to the customer: the refundable amount less the netting."""

        return self.refundable_vnd - self.netted_vnd

    @property
    def moves_remedy_money(self) -> bool:
        return bool(self.voided or self.netted or self.reissued)

    def figures(self) -> CancellationMoneyFigures:
        """What the refund sheet shows and the press carries back (J4)."""

        return CancellationMoneyFigures(
            refund_vnd=self.refund_vnd,
            netted_vnd=self.netted_vnd,
            voided_vnd=sum(credit.amount_vnd for credit in self.voided),
            reissued_vnd=sum(credit.amount_vnd for credit in self.reissued),
        )

    def require_allowed(self, expected: CancellationMoneyFigures | None) -> None:
        """Refuse the cancellation this plan is for, before anything is written, when it may not
        go through (`refusal`), or when what it would execute is not what the press was shown:
        the figures differ, or remedy money moves and the press carried no figures."""

        if self.refusal is not None:
            raise CancellationMoneyRefused(self.refusal)
        if expected is None:
            if self.moves_remedy_money:
                raise CancellationMoneyRefused(CancellationMoneyRefusal.CANCELLATION_MONEY_CHANGED)
            return
        if expected != self.figures():
            raise CancellationMoneyRefused(CancellationMoneyRefusal.CANCELLATION_MONEY_CHANGED)


@dataclass(frozen=True, slots=True)
class CancellationMoneyFigures:
    """The four figures of a cancellation without charge, as the sheet showed them (J4).

    `refund_vnd` is what goes back to the customer (0 when nothing does); the other three are the
    face values netted from it, voided with it and reissued by it. Whole đồng, each validated.
    """

    refund_vnd: int
    netted_vnd: int
    voided_vnd: int
    reissued_vnd: int

    def __post_init__(self) -> None:
        for value in (self.refund_vnd, self.netted_vnd, self.voided_vnd, self.reissued_vnd):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= MAX_SETTLEMENT_VND
            ):
                raise ValueError("a cancellation figure is a non-negative whole number of đồng")


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
    # J3: a credit whose issuing order was cancelled without taking its whole value back would be
    # given back twice; no rule decides how much of it to reissue, so the cancellation is refused.
    refusal = (
        CancellationMoneyRefusal.CREDIT_CHAIN_NOT_NETTED
        if any(c.issuer_uncovered for c in reissued)
        else None
    )
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
    if refusal is not None:
        lines = [CANCELLATION_REFUSAL_VI[refusal]]
    return CancellationMoneyPlan(
        voided=voided,
        netted=netted,
        refundable_vnd=refundable_vnd,
        netted_vnd=netted_vnd,
        reissued=reissued,
        lines_vi=tuple(lines),
        refusal=refusal,
    )


def _credit_vi(credit: RemedyCreditFact) -> str:
    """ "Giảm trừ do giao trễ 11.000 ₫" -- a credit by what it was for and its amount."""

    return f"{REMEDY_KIND_VI.get(credit.kind, credit.kind.value)} {_vnd_text(credit.amount_vnd)}"


def _vnd_text(amount: int) -> str:
    """`11000` -> `11.000 ₫`: the Vietnamese thousands separator, as `unclaimed` writes it."""

    return f"{amount:,}".replace(",", ".") + " ₫"


__all__ = [
    "CANCELLATION_REFUSAL_VI",
    "DEC_NO_DOUBLE_COMPENSATION",
    "DEC_SPENT_CREDIT_REISSUED",
    "REMEDY_KIND_VI",
    "CancellationMoneyFigures",
    "CancellationMoneyPlan",
    "CancellationMoneyRefusal",
    "CancellationMoneyRefused",
    "CreditState",
    "RemedyCreditFact",
    "cancellation_money_plan",
]
