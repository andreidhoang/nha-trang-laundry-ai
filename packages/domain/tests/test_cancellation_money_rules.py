"""`MONEY-LIFECYCLE-009` (M3, M7): what a cancellation without charge does to remedy money.

`DEC-045`: an unspent credit issued from the order is voided; one spent elsewhere is netted from the
refund, never below 0. `DEC-046`: a credit the order's bill spent is reissued at its face value.
`DEC-045` first, then `DEC-046`. The owner's disposal (`UNCLAIMED_DISPOSED`) keeps everything.
Pure tables and one property: the customer ends at "paid nothing, got nothing extra" -- except where
the decision's own "never below 0" leaves them a credit's value above what they paid.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.cancellation_money import (
    CreditState,
    RemedyCreditFact,
    cancellation_money_plan,
)
from nha_trang_laundry_domain.catalog import CustodyResolution
from nha_trang_laundry_domain.remedies import RemedyKind

LATE = RemedyKind.LATE_DELIVERY_CREDIT
DAMAGE = RemedyKind.DAMAGE_COMPENSATION
SHOP_FAULT = CustodyResolution.SHOP_FAULT_NO_CHARGE


def credit(kind: RemedyKind, amount: int, state: CreditState) -> RemedyCreditFact:
    return RemedyCreditFact(credit_id=uuid4(), kind=kind, amount_vnd=amount, state=state)


def ids(credits: tuple[RemedyCreditFact, ...]) -> list[UUID]:
    return [c.credit_id for c in credits]


@pytest.mark.parametrize(
    "resolution",
    [
        None,
        CustodyResolution.NOT_RECEIVED,
        CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        CustodyResolution.SHOP_FAULT_NO_CHARGE,
    ],
)
@pytest.mark.parametrize("refundable", [0, 50_000, 110_000])
def test_an_unspent_credit_from_the_order_is_voided_and_nothing_is_netted(
    resolution: CustodyResolution | None, refundable: int
) -> None:
    unspent = credit(DAMAGE, 40_000, CreditState.UNSPENT)
    plan = cancellation_money_plan(
        resolution=resolution,
        refundable_vnd=refundable,
        issued_from_order=[unspent],
        spent_on_order=[],
    )
    assert ids(plan.voided) == [unspent.credit_id]
    assert (plan.netted, plan.netted_vnd, plan.refund_vnd, plan.reissued) == (
        (),
        0,
        refundable,
        (),
    )
    assert plan.lines_vi == (
        "Khoản Bồi thường món bị hỏng 40.000 ₫ khách chưa dùng được huỷ cùng đơn.",
    )


@pytest.mark.parametrize(
    ("refundable", "netted", "refund", "tail"),
    [
        (110_000, 40_000, 70_000, "."),
        (40_000, 40_000, 0, "."),  # exactly the face value: nothing goes back
        (30_000, 30_000, 0, " (không trừ quá số khách đã trả)."),  # never below 0
    ],
)
def test_a_spent_credit_from_the_order_is_netted_from_the_refund_never_below_zero(
    refundable: int, netted: int, refund: int, tail: str
) -> None:
    spent = credit(DAMAGE, 40_000, CreditState.SPENT)
    plan = cancellation_money_plan(
        resolution=SHOP_FAULT,
        refundable_vnd=refundable,
        issued_from_order=[spent],
        spent_on_order=[],
    )
    assert ids(plan.netted) == [spent.credit_id]
    assert (plan.netted_vnd, plan.refund_vnd, plan.voided, plan.reissued) == (
        netted,
        refund,
        (),
        (),
    )
    assert plan.lines_vi == (
        f"Trừ khoản Bồi thường món bị hỏng 40.000 ₫ khách đã dùng: hoàn "
        f"{refund:,} ₫ thay vì {refundable:,} ₫".replace(",", ".")
        + tail,
    )


def test_a_spent_credit_with_nothing_refunded_nets_nothing_and_says_so() -> None:
    spent = credit(LATE, 11_000, CreditState.SPENT)
    plan = cancellation_money_plan(
        resolution=SHOP_FAULT, refundable_vnd=0, issued_from_order=[spent], spent_on_order=[]
    )
    assert (plan.netted_vnd, plan.refund_vnd) == (0, 0)
    assert "không có gì để trừ" in plan.lines_vi[0]


@pytest.mark.parametrize("refundable", [0, 50_000, 89_000])
def test_a_credit_the_bill_spent_is_reissued_at_its_face_value(refundable: int) -> None:
    spent = credit(LATE, 11_000, CreditState.SPENT)
    plan = cancellation_money_plan(
        resolution=SHOP_FAULT,
        refundable_vnd=refundable,
        issued_from_order=[],
        spent_on_order=[spent],
    )
    assert ids(plan.reissued) == [spent.credit_id]
    assert (plan.refund_vnd, plan.netted_vnd, plan.voided) == (refundable, 0, ())
    assert plan.lines_vi == (
        "Cấp lại cho khách khoản Giảm trừ do giao trễ 11.000 ₫ đã dùng cho đơn này.",
    )


def test_dec_045_first_then_dec_046_when_one_order_spent_and_generated_a_credit() -> None:
    spent_here = credit(LATE, 20_000, CreditState.SPENT)
    unspent_from_here = credit(LATE, 15_000, CreditState.UNSPENT)
    spent_from_here = credit(DAMAGE, 40_000, CreditState.SPENT)
    plan = cancellation_money_plan(
        resolution=SHOP_FAULT,
        refundable_vnd=80_000,
        issued_from_order=[unspent_from_here, spent_from_here],
        spent_on_order=[spent_here],
    )
    assert ids(plan.voided) == [unspent_from_here.credit_id]
    assert ids(plan.netted) == [spent_from_here.credit_id]
    assert ids(plan.reissued) == [spent_here.credit_id]
    assert (plan.netted_vnd, plan.refund_vnd) == (40_000, 40_000)
    assert [line.split(" ")[0] for line in plan.lines_vi] == ["Khoản", "Trừ", "Cấp"]


def test_disposal_keeps_every_credit_and_the_money() -> None:
    plan = cancellation_money_plan(
        resolution=CustodyResolution.UNCLAIMED_DISPOSED,
        refundable_vnd=0,
        issued_from_order=[credit(LATE, 11_000, CreditState.UNSPENT)],
        spent_on_order=[credit(DAMAGE, 40_000, CreditState.SPENT)],
    )
    assert not plan.moves_remedy_money and plan.lines_vi == ()


def test_a_voided_credit_is_neither_voided_again_netted_nor_reissued() -> None:
    voided = credit(LATE, 11_000, CreditState.VOIDED)
    plan = cancellation_money_plan(
        resolution=SHOP_FAULT, refundable_vnd=50_000, issued_from_order=[voided], spent_on_order=[]
    )
    assert not plan.moves_remedy_money and plan.refund_vnd == 50_000


def test_amounts_are_validated() -> None:
    for bad in (-1, 1.5, True):
        with pytest.raises(ValueError):
            cancellation_money_plan(
                resolution=SHOP_FAULT,
                refundable_vnd=bad,  # type: ignore[arg-type]
                issued_from_order=[],
                spent_on_order=[],
            )
    with pytest.raises(ValueError):
        cancellation_money_plan(
            resolution=SHOP_FAULT,
            refundable_vnd=0,
            issued_from_order=[credit(LATE, 0, CreditState.SPENT)],
            spent_on_order=[],
        )


@given(
    paid=st.integers(min_value=0, max_value=300_000),
    issued=st.lists(
        st.tuples(st.integers(min_value=1, max_value=150_000), st.sampled_from(CreditState)),
        max_size=4,
    ),
    spent_here=st.lists(st.integers(min_value=1, max_value=150_000), max_size=3),
)
def test_property_the_customer_ends_with_nothing_extra_beyond_the_decisions_floor(
    paid: int, issued: list[tuple[int, CreditState]], spent_here: list[int]
) -> None:
    """What the customer holds after = the refund + the credits still usable (not voided), measured
    against what they paid and what the order generated. DEC-045 makes the order generate nothing;
    DEC-046 gives back exactly what the bill spent; the only surplus left is the netting the
    "never below 0" floor could not take."""

    from_order = [credit(DAMAGE, amount, state) for amount, state in issued]
    on_bill = [credit(LATE, amount, CreditState.SPENT) for amount in spent_here]
    plan = cancellation_money_plan(
        resolution=SHOP_FAULT,
        refundable_vnd=paid,
        issued_from_order=from_order,
        spent_on_order=on_bill,
    )
    assert 0 <= plan.refund_vnd <= paid
    assert plan.netted_vnd + plan.refund_vnd == paid
    face_spent_elsewhere = sum(c.amount_vnd for c in from_order if c.state is CreditState.SPENT)
    assert plan.netted_vnd == min(paid, face_spent_elsewhere)
    assert sum(c.amount_vnd for c in plan.reissued) == sum(spent_here)
    assert {c.credit_id for c in plan.voided} == {
        c.credit_id for c in from_order if c.state is CreditState.UNSPENT
    }
