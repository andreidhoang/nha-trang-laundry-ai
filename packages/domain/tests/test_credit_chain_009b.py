"""Credit chains: every order of cancellation ends the customer at the same total. J3.

`MONEY-RESIDUAL-009B` J3. Order X issues credit c1, c1 is spent on Y, Y issues c2, c2 is spent on Z.
The customer's total is what they paid in cash, less what came back, less the face value of the
credits they still hold (a live credit is money the shop owes them). `DEC-045` then `DEC-046`,
applied by `cancellation_money_plan` one cancellation at a time, must end that total at the same
figure whichever order is cancelled first -- or refuse the later cancellation by name
(`CREDIT_CHAIN_NOT_NETTED`), never end it somewhere else silently.

The ledger here is the smallest model of what the repository keeps: each order's cash, each
credit's state and chain, each cancellation's refund and netting. The one fact the repository
derives from its rows -- a spent credit whose issuing order was cancelled without its refund taking
the credit's whole value off -- is derived the same way here (`issuer_uncovered`); the HTTP test
(`apps/api/tests/test_money_residual_009b_http.py`) walks the real chains through the API.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.cancellation_money import (
    CancellationMoneyFigures,
    CancellationMoneyRefusal,
    CancellationMoneyRefused,
    CreditState,
    RemedyCreditFact,
    cancellation_money_plan,
)
from nha_trang_laundry_domain.catalog import CustodyResolution
from nha_trang_laundry_domain.remedies import RemedyKind


@dataclass
class _Credit:
    id: UUID
    issued_from: str
    amount: int
    spent_on: str | None
    state: CreditState
    replaced_by: UUID | None = None


@dataclass
class _Ledger:
    cash: dict[str, int]
    credits: list[_Credit]
    refunded: dict[str, int] = field(default_factory=dict)
    #: Each cancelled order: whether its refund took the whole value of what it netted.
    covered: dict[str, bool] = field(default_factory=dict)

    def fact(self, credit: _Credit, *, spent_view: bool) -> RemedyCreditFact:
        issuer = credit.issued_from
        uncovered = spent_view and issuer in self.covered and not self.covered[issuer]
        return RemedyCreditFact(
            credit.id, RemedyKind.DAMAGE_COMPENSATION, credit.amount, credit.state, uncovered
        )

    def cancel(self, order: str) -> None:
        issued = [c for c in self.credits if c.issued_from == order and c.replaced_by is None]
        spent = [c for c in self.credits if c.spent_on == order]
        plan = cancellation_money_plan(
            resolution=CustodyResolution.SHOP_FAULT_NO_CHARGE,
            refundable_vnd=self.cash[order],
            issued_from_order=[self.fact(c, spent_view=False) for c in issued],
            spent_on_order=[self.fact(c, spent_view=True) for c in spent],
        )
        # What the sheet showed is what the press carries: the plan refuses only on its own rule.
        plan.require_allowed(plan.figures() if plan.moves_remedy_money else None)
        for credit in issued:
            if credit.state is CreditState.UNSPENT:
                credit.state = CreditState.VOIDED
        for credit in spent:
            if credit.state is CreditState.SPENT:
                again = _Credit(
                    uuid4(), credit.issued_from, credit.amount, None, CreditState.UNSPENT
                )
                credit.replaced_by = again.id
                self.credits.append(again)
        self.refunded[order] = plan.refund_vnd
        self.covered[order] = plan.netted_vnd == sum(c.amount_vnd for c in plan.netted)

    def total(self) -> int:
        """What the customer is out of pocket, less what the shop still owes them in credits."""

        paid = sum(self.cash.values()) - sum(self.refunded.values())
        live = sum(c.amount for c in self.credits if c.state is CreditState.UNSPENT)
        return paid - live


def _chain(cash: dict[str, int], links: list[tuple[str, int, str]]) -> _Ledger:
    """`links` are (issued from, face value, spent on), in chain order."""

    return _Ledger(
        cash=dict(cash),
        credits=[_Credit(uuid4(), x, amount, y, CreditState.SPENT) for x, amount, y in links],
    )


def _outcomes(
    cash: dict[str, int], links: list[tuple[str, int, str]]
) -> dict[tuple[str, ...], int | str]:
    """Every order of cancelling every order: the customer's final total, or the refused order."""

    found: dict[tuple[str, ...], int | str] = {}
    for order in itertools.permutations(cash):
        ledger = _chain(cash, links)
        try:
            for name in order:
                ledger.cancel(name)
        except CancellationMoneyRefused as refused:
            assert refused.code is CancellationMoneyRefusal.CREDIT_CHAIN_NOT_NETTED
            found[order] = f"refused {name}"
            continue
        found[order] = ledger.total()
    return found


# --- the chains of the brief ----------------------------------------------------------------


def test_a_two_order_chain_ends_at_the_same_total_either_way() -> None:
    # X paid 110.000 and issued c1 (80.000), spent on Y (100.000 bill, 20.000 in cash).
    outcomes = _outcomes({"X": 110_000, "Y": 20_000}, [("X", 80_000, "Y")])
    assert outcomes == {("X", "Y"): 0, ("Y", "X"): 0}


def test_a_two_order_chain_whose_first_netting_was_capped_refuses_the_later_cancellation() -> None:
    # c1 (150.000) is worth more than X's ledger (110.000): cancelling X first nets only 110.000,
    # so c1's last 40.000 stayed with the customer; reissuing c1 on Y would give it again.
    outcomes = _outcomes({"X": 110_000, "Y": 10_000}, [("X", 150_000, "Y")])
    assert outcomes == {("X", "Y"): "refused Y", ("Y", "X"): 0}


def test_a_three_order_chain_ends_at_the_same_total_in_every_order() -> None:
    # X (110.000) issues c1 (11.000) spent on Y (89.000 cash); Y issues c2 (30.000) spent on Z.
    outcomes = _outcomes(
        {"X": 110_000, "Y": 89_000, "Z": 70_000}, [("X", 11_000, "Y"), ("Y", 30_000, "Z")]
    )
    assert set(outcomes.values()) == {0}
    assert len(outcomes) == 6


def test_a_three_order_chain_refuses_exactly_the_orders_that_would_pay_twice() -> None:
    # c2 (30.000) is worth more than Y's cash (20.000): wherever Y is cancelled before Z while c2
    # is spent, Z's cancellation would reissue c2 whole -- refused; every other order ends at 0.
    outcomes = _outcomes(
        {"X": 110_000, "Y": 20_000, "Z": 70_000}, [("X", 80_000, "Y"), ("Y", 30_000, "Z")]
    )
    assert outcomes == {
        ("X", "Y", "Z"): "refused Z",
        ("X", "Z", "Y"): 0,
        ("Y", "X", "Z"): "refused Z",
        ("Y", "Z", "X"): "refused Z",
        ("Z", "X", "Y"): 0,
        ("Z", "Y", "X"): 0,
    }


@given(
    cash=st.lists(st.integers(min_value=0, max_value=500_000), min_size=3, max_size=3),
    faces=st.lists(st.integers(min_value=1, max_value=500_000), min_size=2, max_size=2),
    length=st.sampled_from([2, 3]),
)
def test_property_no_order_of_cancellation_ends_anywhere_else(
    cash: list[int], faces: list[int], length: int
) -> None:
    names = ["X", "Y", "Z"][:length]
    links = [(names[i], faces[i], names[i + 1]) for i in range(length - 1)]
    outcomes = _outcomes(dict(zip(names, cash, strict=False)), links)
    totals = {value for value in outcomes.values() if isinstance(value, int)}
    # Every order of cancellation that goes through ends at the same total: paid nothing, got
    # nothing extra.
    assert totals <= {0}
    # Cancelling from the end of the chain back to its start is never refused.
    assert outcomes[tuple(reversed(names))] == 0
    # A refusal happens only where an earlier netting was capped.
    capped = any(face > amount for (_x, face, _y), amount in zip(links, cash, strict=False))
    if not capped:
        assert totals == {0} and len(totals) == 1
        assert all(isinstance(value, int) for value in outcomes.values())


# --- the press carries the preview (J4) ------------------------------------------------------


def _plan(**overrides: object) -> object:
    spent = RemedyCreditFact(uuid4(), RemedyKind.LATE_DELIVERY_CREDIT, 11_000, CreditState.SPENT)
    return cancellation_money_plan(
        resolution=CustodyResolution.SHOP_FAULT_NO_CHARGE,
        refundable_vnd=110_000,
        issued_from_order=[spent],
        spent_on_order=[],
        **overrides,  # type: ignore[arg-type]
    )


def test_the_press_must_carry_exactly_the_figures_it_would_execute() -> None:
    plan = _plan()
    shown = plan.figures()  # type: ignore[attr-defined]
    assert shown == CancellationMoneyFigures(99_000, 11_000, 0, 0)
    plan.require_allowed(shown)  # type: ignore[attr-defined]
    for moved in (
        CancellationMoneyFigures(110_000, 0, 11_000, 0),  # the credit was unspent when shown
        CancellationMoneyFigures(99_000, 11_000, 0, 11_000),
        None,  # remedy money moves and the press said nothing about it
    ):
        with pytest.raises(CancellationMoneyRefused) as refused:
            plan.require_allowed(moved)  # type: ignore[attr-defined]
        assert refused.value.code is CancellationMoneyRefusal.CANCELLATION_MONEY_CHANGED
        assert "Xem lại số mới" in refused.value.message_vi


def test_a_cancellation_that_moves_no_remedy_money_needs_no_figures() -> None:
    plan = cancellation_money_plan(
        resolution=None, refundable_vnd=50_000, issued_from_order=[], spent_on_order=[]
    )
    plan.require_allowed(None)
    plan.require_allowed(CancellationMoneyFigures(50_000, 0, 0, 0))
    with pytest.raises(CancellationMoneyRefused):
        plan.require_allowed(CancellationMoneyFigures(40_000, 0, 0, 0))


def test_figures_are_whole_non_negative_dong() -> None:
    for bad in (-1, 1.5, True):
        with pytest.raises(ValueError):
            CancellationMoneyFigures(bad, 0, 0, 0)  # type: ignore[arg-type]
