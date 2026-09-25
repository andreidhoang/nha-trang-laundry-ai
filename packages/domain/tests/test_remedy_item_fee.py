"""`DEC-031`: which fee "5x the item's cleaning fee" multiplies, and when only the owner may pay.

`DEC-004` caps compensation at five times "the item's cleaning fee" and the code applied that to the
whole line, so three shirts at 50.000 d -- one 150.000 d line -- capped *each* shirt at 750.000 d.
`DEC-031` read the words where they stop:

* a line priced per piece, pair, set, plush animal or case: the item fee is the **unit price**,
  read from the immutable quote snapshot -- never the line divided by its quantity and rounded;
* a line priced by weight: no item has a fee of its own, so it is the fee of the **bag**;
* a per-piece line whose snapshot recorded no unit price (a closed band over several pieces), or a
  measure the ruling does not address (m2): no ratified figure exists, so every amount goes to the
  owner, bounded by the line's own fee -- the only number on record;
* every loss claim, and every compensation on a refunded order, needs the owner whatever the amount.

Each rule has a test that fails if the rule is removed; the mutation notes in the report name them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import QuantityBasis, Unit
from nha_trang_laundry_domain.quotes import ExactLineAmounts, QuoteLineSnapshot, RangeLineAmounts
from nha_trang_laundry_domain.remedies import (
    NO_PRIOR_COMMITMENTS,
    ItemFeeBasis,
    OwnerReason,
    RemedyAuthorized,
    RemedyCommitments,
    RemedyKind,
    RemedyLineFacts,
    RemedyOrderFacts,
    RemedyRefusal,
    RemedyRefused,
    RemedyRequest,
    evaluate_remedy,
    item_compensation_terms,
    parse_remedy_policy,
    remedy_line_facts,
)

NOW = datetime(2026, 9, 25, 3, tzinfo=UTC)
RETURNED = NOW - timedelta(hours=1)
POLICY = parse_remedy_policy(
    {
        "schema": "remedy-policy-v1",
        "decision": "DEC-004",
        "defect_report_window_hours": 24,
        "initial_response_window_hours": 24,
        "free_rewash_window_days": 7,
        "late_delivery_threshold_minutes": 120,
        "late_delivery_credit_rate_bps": 1000,
        "damage_compensation_multiple": 5,
        "staff_approval_ceiling_vnd": 100_000,
    }
)

#: Three shirts at the published 50.000 d (`DC_SHIRT`): one 150.000 d line, three items.
SHIRTS = RemedyLineFacts(
    service_code="DC_SHIRT",
    unit=Unit.ITEM,
    quantity="3",
    unit_price_vnd=50_000,
    net_amount_vnd=150_000,
)
#: 5,2 kg of standard wash under the 6 kg cliff at 25.000 d/kg: one 130.000 d bag.
BAG = RemedyLineFacts(
    service_code="STD_WASH_DRY_LT6",
    unit=Unit.KG,
    quantity="5.2",
    unit_price_vnd=25_000,
    net_amount_vnd=130_000,
)
#: Two pillows closed inside the published 30.000-90.000 d band at 140.000 d for the line. A closed
#: band records no unit price (`quote_composition`: "the amount is a judgement about one garment,
#: not a rate"), so no number on record is the fee of one pillow.
PILLOWS = RemedyLineFacts(
    service_code="BED_PILLOW",
    unit=Unit.ITEM,
    quantity="2",
    unit_price_vnd=None,
    net_amount_vnd=140_000,
)
#: One leather handbag closed inside its band at 120.000 d. One piece: the line's fee *is* the
#: item's fee, and nothing was divided to find it.
HANDBAG = RemedyLineFacts(
    service_code="LEATHER_HANDBAG",
    unit=Unit.ITEM,
    quantity="1",
    unit_price_vnd=None,
    net_amount_vnd=120_000,
)
#: A 6 m2 hard carpet at 100.000 d/m2. DEC-031 speaks of pieces and of weight; area is neither.
CARPET = RemedyLineFacts(
    service_code="OTHER_HARD_CARPET",
    unit=Unit.M2,
    quantity="6",
    unit_price_vnd=100_000,
    net_amount_vnd=600_000,
)


def order(
    lines: dict[str, RemedyLineFacts] | None = None, *, refunded: bool = False
) -> RemedyOrderFacts:
    return RemedyOrderFacts(
        goods_returned_at=RETURNED,
        settled_total_vnd=None if refunded else 280_000,
        lines=lines or {"shirts": SHIRTS, "bag": BAG},
        expects_return_leg=False,
        return_leg_succeeded=False,
        refunded=refunded,
    )


def claim(
    kind: RemedyKind,
    line_id: str,
    amount: int,
    facts: RemedyOrderFacts | None = None,
    committed: int = 0,
    *,
    garment: int | None = None,
    garment_committed: int | None = None,
) -> object:
    """One claim. `garment` names the shirt (`REMEDY-GARMENT-001`); `garment_committed` is what
    already counts against it, and `None` leaves the line's total counting against it."""

    return evaluate_remedy(
        policy=POLICY,
        facts=facts or order(),
        request=RemedyRequest(
            kind=kind,
            store_fault_attested=True,
            order_line_id=line_id,
            amount_vnd=amount,
            garment_index=garment,
        ),
        requested_at=NOW,
        committed=RemedyCommitments(
            line_committed_vnd=committed,
            late_delivery_credits=0,
            garment_committed_vnd=garment_committed,
        )
        if committed or garment_committed is not None
        else NO_PRIOR_COMMITMENTS,
    )


# --- 1. the item fee ----------------------------------------------------------------------------


def test_a_per_piece_line_caps_each_item_at_five_times_its_unit_price() -> None:
    """Three shirts at 50.000 d cap each shirt at 250.000 d -- not the 750.000 d the line gave."""

    terms = item_compensation_terms(POLICY, order(), "shirts")
    assert terms is not None
    assert (terms.basis, terms.item_fee_vnd, terms.ceiling_vnd) == (
        ItemFeeBasis.UNIT,
        50_000,
        250_000,
    )
    at = claim(RemedyKind.DAMAGE_COMPENSATION, "shirts", 250_000, garment=1)
    assert isinstance(at, RemedyAuthorized) and at.ceiling_vnd == 250_000
    assert at.item_fee_basis is ItemFeeBasis.UNIT and at.item_fee_vnd == 50_000
    over = claim(RemedyKind.DAMAGE_COMPENSATION, "shirts", 250_001, garment=1)
    assert isinstance(over, RemedyRefused)
    assert over.refusal is RemedyRefusal.REMEDY_CEILING_EXCEEDED
    # Refused with the per-piece ceiling named, never the line's 750.000 d.
    assert over.ceiling_vnd == 250_000


def test_a_weight_priced_line_caps_against_the_fee_of_the_bag() -> None:
    """No garment in a washed bag has its own fee; the bag's is the only number that exists."""

    terms = item_compensation_terms(POLICY, order(), "bag")
    assert terms is not None
    assert (terms.basis, terms.item_fee_vnd, terms.ceiling_vnd) == (
        ItemFeeBasis.BAG,
        130_000,
        650_000,
    )
    assert terms.owner_always == ()


def test_positive_control_an_ordinary_damage_on_a_bag_is_still_staff_authorised() -> None:
    """80.000 d on a kg bag: inside the ceiling and inside the staff limit, so staff may approve."""

    outcome = claim(RemedyKind.DAMAGE_COMPENSATION, "bag", 80_000)
    assert isinstance(outcome, RemedyAuthorized)
    assert outcome.requires_owner_approval is False
    assert outcome.owner_reasons == ()
    assert outcome.ceiling_vnd == 650_000


def test_the_unit_price_is_read_from_the_snapshot_and_never_divided_out_of_the_line() -> None:
    """A line whose total is not a multiple of its quantity still has an exact recorded unit price.

    7 pieces at 15.000 d with a 1 d rounding artefact on the line would divide to a fraction; the
    recorded unit price is the answer and no division happens anywhere.
    """

    odd = RemedyLineFacts(
        service_code="IRON_KNIT",
        unit=Unit.ITEM,
        quantity="7",
        unit_price_vnd=15_000,
        net_amount_vnd=105_001,
    )
    terms = item_compensation_terms(POLICY, order({"odd": odd}), "odd")
    assert terms is not None
    assert (terms.item_fee_vnd, terms.ceiling_vnd) == (15_000, 75_000)


def test_a_discounted_single_piece_is_capped_at_what_the_shop_actually_charged() -> None:
    """The unit price, and never more than the line was charged after its discount.

    `REMEDY-001` capped against the net, "what the store charged"; `DEC-031` says "the unit price"
    and that it *lowers* exposure. On a single discounted piece the two readings differ, and taking
    the lower keeps both statements true -- a remedy credit or a promotion on the line does not
    raise what the shop may owe for it.
    """

    discounted = RemedyLineFacts(
        service_code="DC_SHIRT",
        unit=Unit.ITEM,
        quantity="1",
        unit_price_vnd=50_000,
        net_amount_vnd=40_000,
    )
    terms = item_compensation_terms(POLICY, order({"one": discounted}), "one")
    assert terms is not None
    assert (terms.basis, terms.item_fee_vnd, terms.ceiling_vnd) == (
        ItemFeeBasis.UNIT,
        40_000,
        200_000,
    )


def test_one_piece_with_no_recorded_unit_price_is_its_line() -> None:
    """A closed band on a single handbag: the line's fee is the item's fee, nothing is divided."""

    terms = item_compensation_terms(POLICY, order({"handbag": HANDBAG}), "handbag")
    assert terms is not None
    assert (terms.basis, terms.item_fee_vnd, terms.ceiling_vnd) == (
        ItemFeeBasis.UNIT,
        120_000,
        600_000,
    )
    staff = claim(RemedyKind.DAMAGE_COMPENSATION, "handbag", 50_000, order({"handbag": HANDBAG}))
    assert isinstance(staff, RemedyAuthorized) and staff.requires_owner_approval is False


@pytest.mark.parametrize(("line_id", "line"), [("pillows", PILLOWS), ("carpet", CARPET)])
def test_an_item_fee_nobody_recorded_sends_every_amount_to_the_owner(
    line_id: str, line: RemedyLineFacts
) -> None:
    """Unknown means stop, in the shape DEC-031 chose for it: the owner decides, not a formula.

    The bound is the line's own fee -- no single item on it can have cost more -- and a proposal
    inside it is recorded but never staff-authorised, however small.
    """

    facts = order({line_id: line})
    terms = item_compensation_terms(POLICY, facts, line_id)
    assert terms is not None
    assert terms.basis is ItemFeeBasis.NOT_RECORDED
    assert terms.item_fee_vnd == line.net_amount_vnd
    assert terms.owner_always == (OwnerReason.ITEM_FEE_NOT_RECORDED,)
    small = claim(RemedyKind.DAMAGE_COMPENSATION, line_id, 1_000, facts)
    assert isinstance(small, RemedyAuthorized)
    assert small.requires_owner_approval is True
    assert small.owner_reasons == (OwnerReason.ITEM_FEE_NOT_RECORDED,)


def test_a_banded_line_has_no_item_fee_at_all() -> None:
    """A revision still carrying a band prices nothing exactly, so no ceiling can be taken of it."""

    lines = remedy_line_facts(
        (
            QuoteLineSnapshot(
                line_id="line-0",
                service_code="BED_PILLOW",
                service_version_id=uuid4(),
                quantity_basis=QuantityBasis.STAFF_MEASUREMENT,
                quantity="2",
                unit=Unit.ITEM,
                amounts=RangeLineAmounts(
                    "RANGE", 30_000, 90_000, 60_000, 180_000, 0, 0, 60_000, 180_000
                ),
                price_trace_hash="JCS-SHA256-V1:" + "a" * 64,
            ),
            QuoteLineSnapshot(
                line_id="line-1",
                service_code="DC_SHIRT",
                service_version_id=uuid4(),
                quantity_basis=QuantityBasis.STAFF_MEASUREMENT,
                quantity="3",
                unit=Unit.ITEM,
                amounts=ExactLineAmounts("EXACT", 50_000, 150_000, 15_000, 135_000),
                price_trace_hash="JCS-SHA256-V1:" + "b" * 64,
            ),
        )
    )
    assert set(lines) == {"line-1"}
    assert lines["line-1"] == RemedyLineFacts(
        service_code="DC_SHIRT",
        unit=Unit.ITEM,
        quantity="3",
        unit_price_vnd=50_000,
        net_amount_vnd=135_000,
    )


# --- 1b. several pieces on one line --------------------------------------------------------------
#
# The founder's clarification of DEC-031 rule 1 (2026-09-25, relayed by the lead): the ceiling is
# per ITEM. A line of N pieces carries 5 x unit fee x N in total and one proposal stays capped at
# one item's 5 x unit fee. The DEC-031 addendum (`REMEDY-GARMENT-001`) then made the 100.000 d staff
# limit and the ceiling cumulative per GARMENT rather than per line: each claim names its shirt,
# and a second claim on the same shirt adds to the first. `test_remedy_garment.py` pins the
# garment rules themselves; the tests here were updated to name the shirt they claim for.


def test_a_line_of_several_pieces_carries_one_ceiling_per_piece() -> None:
    """Three shirts at 50.000 d: 250.000 d per shirt, 750.000 d for the line."""

    terms = item_compensation_terms(POLICY, order(), "shirts")
    assert terms is not None
    assert (terms.pieces, terms.ceiling_vnd, terms.line_ceiling_vnd) == (3, 250_000, 750_000)
    assert terms.garments == 3

    first = claim(RemedyKind.DAMAGE_COMPENSATION, "shirts", 250_000, garment=1)
    assert isinstance(first, RemedyAuthorized)
    assert first.owner_reasons == (OwnerReason.ABOVE_STAFF_LIMIT,)
    assert (first.ceiling_vnd, first.line_ceiling_vnd) == (250_000, 750_000)
    # The second shirt's full claim: to the owner, not refused -- it has its own 250.000 d.
    second = claim(
        RemedyKind.DAMAGE_COMPENSATION,
        "shirts",
        250_000,
        committed=250_000,
        garment=2,
        garment_committed=0,
    )
    assert isinstance(second, RemedyAuthorized) and second.requires_owner_approval
    # The third reaches the line's 750.000 d exactly, inclusive.
    third = claim(
        RemedyKind.LOST_ITEM,
        "shirts",
        250_000,
        committed=500_000,
        garment=3,
        garment_committed=0,
    )
    assert isinstance(third, RemedyAuthorized) and third.requires_owner_approval
    # One dong more on a full shirt is refused with that shirt's ceiling and what it carries.
    past = claim(
        RemedyKind.DAMAGE_COMPENSATION,
        "shirts",
        1,
        committed=750_000,
        garment=3,
        garment_committed=250_000,
    )
    assert isinstance(past, RemedyRefused)
    assert past.refusal is RemedyRefusal.REMEDY_CEILING_EXCEEDED
    assert (past.ceiling_vnd, past.committed_vnd) == (250_000, 250_000)


def test_one_claim_is_still_capped_at_one_item() -> None:
    """300.000 d in one claim is more than one shirt can be owed, whatever the line holds."""

    single = claim(RemedyKind.DAMAGE_COMPENSATION, "shirts", 300_000, garment=2)
    assert isinstance(single, RemedyRefused)
    assert single.refusal is RemedyRefusal.REMEDY_CEILING_EXCEEDED
    assert single.ceiling_vnd == 250_000


def test_the_staff_limit_is_per_garment_not_per_line() -> None:
    """60.000 d on shirt #1, then 50.000 d on shirt #2: two items, so both are staff's.

    Was `test_the_staff_limit_stays_per_line_across_pieces`, asserting the second went to the
    owner. The DEC-031 addendum reversed that rule; the half that survives -- a second claim on the
    *same* shirt is cumulative with the first -- is asserted here and in `test_remedy_garment.py`.
    """

    staff = claim(RemedyKind.DAMAGE_COMPENSATION, "shirts", 60_000, garment=1)
    assert isinstance(staff, RemedyAuthorized) and not staff.requires_owner_approval
    other = claim(
        RemedyKind.DAMAGE_COMPENSATION,
        "shirts",
        50_000,
        committed=60_000,
        garment=2,
        garment_committed=0,
    )
    assert isinstance(other, RemedyAuthorized) and not other.requires_owner_approval
    assert other.garment_index == 2
    same = claim(
        RemedyKind.DAMAGE_COMPENSATION,
        "shirts",
        50_000,
        committed=110_000,
        garment=1,
        garment_committed=60_000,
    )
    assert isinstance(same, RemedyAuthorized)
    assert same.owner_reasons == (OwnerReason.ABOVE_STAFF_LIMIT,)


def test_a_bag_and_an_unrecorded_fee_keep_a_single_ceiling() -> None:
    """Weight has no pieces, and a line with no recorded unit price stays bounded by the line."""

    for line_id, line in (("bag", BAG), ("pillows", PILLOWS), ("carpet", CARPET)):
        terms = item_compensation_terms(POLICY, order({line_id: line}), line_id)
        assert terms is not None
        assert terms.pieces == 1, line_id
        assert terms.line_ceiling_vnd == terms.ceiling_vnd, line_id


def test_a_discounted_line_of_pieces_is_never_capped_above_what_the_line_was_charged() -> None:
    """Three 50.000 d shirts sold for 120.000 d: 250.000 d a claim, 600.000 d for the line.

    5 x 50.000 x 3 would be 750.000 d -- more than 5x what the shop charged for the line. The line
    total takes the lower, as the single-piece fee does (accepted deviation 2).
    """

    discounted = RemedyLineFacts(
        service_code="DC_SHIRT",
        unit=Unit.ITEM,
        quantity="3",
        unit_price_vnd=50_000,
        net_amount_vnd=120_000,
    )
    terms = item_compensation_terms(POLICY, order({"shirts": discounted}), "shirts")
    assert terms is not None
    assert (terms.ceiling_vnd, terms.line_ceiling_vnd) == (250_000, 600_000)
    # So the line binds before the shirts do: two shirts full and 100.000 d on the third fill
    # the 600.000 d line, and one dong more on the third -- well inside its own 250.000 d -- is
    # refused with the line's ceiling and what the line already carries.
    past = claim(
        RemedyKind.DAMAGE_COMPENSATION,
        "shirts",
        1,
        order({"shirts": discounted}),
        committed=600_000,
        garment=3,
        garment_committed=100_000,
    )
    assert isinstance(past, RemedyRefused)
    assert past.refusal is RemedyRefusal.REMEDY_CEILING_EXCEEDED
    assert (past.ceiling_vnd, past.committed_vnd) == (600_000, 600_000)


# --- 2. loss -------------------------------------------------------------------------------------


def test_every_loss_claim_needs_the_owner() -> None:
    """Loss uses the same item fee as damage, and is never staff-authorised at any amount."""

    for line_id, amount, ceiling, garment in (
        ("shirts", 1, 250_000, 1),
        ("bag", 80_000, 650_000, None),
    ):
        outcome = claim(RemedyKind.LOST_ITEM, line_id, amount, garment=garment)
        assert isinstance(outcome, RemedyAuthorized)
        assert outcome.requires_owner_approval is True
        assert outcome.owner_reasons[0] is OwnerReason.LOSS_CLAIM
        assert outcome.ceiling_vnd == ceiling
    over = claim(RemedyKind.LOST_ITEM, "shirts", 250_001, garment=1)
    assert isinstance(over, RemedyRefused) and over.ceiling_vnd == 250_000


# --- 3. a refunded order -------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [RemedyKind.DAMAGE_COMPENSATION, RemedyKind.LOST_ITEM])
def test_compensation_on_a_refunded_order_needs_the_owner(kind: RemedyKind) -> None:
    """Refund and compensation on one order is where money can be paid twice: the owner decides.

    Still capped against the fee the shop *quoted* -- the refund does not make the item's fee zero,
    because the ceiling is about the garment and the refund is about the service.
    """

    outcome = claim(kind, "bag", 10_000, order(refunded=True))
    assert isinstance(outcome, RemedyAuthorized)
    assert outcome.requires_owner_approval is True
    assert OwnerReason.ORDER_REFUNDED in outcome.owner_reasons
    assert outcome.ceiling_vnd == 650_000


def test_the_late_delivery_credit_stays_refused_on_a_refunded_bill() -> None:
    """10% of nothing: a refunded settlement reads as unsettled, as `CANCEL-REFUND-001` built."""

    outcome = evaluate_remedy(
        policy=POLICY,
        facts=RemedyOrderFacts(
            goods_returned_at=RETURNED,
            settled_total_vnd=None,
            lines={"bag": BAG},
            expects_return_leg=True,
            return_leg_succeeded=True,
            refunded=True,
        ),
        request=RemedyRequest(
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=True,
            attested_late_by_minutes=150,
        ),
        requested_at=NOW,
        committed=NO_PRIOR_COMMITMENTS,
    )
    assert isinstance(outcome, RemedyRefused)
    assert outcome.refusal is RemedyRefusal.REMEDY_ORDER_NOT_SETTLED


# --- properties ----------------------------------------------------------------------------------

_COUNT_UNITS = (Unit.ITEM, Unit.PAIR, Unit.SET, Unit.ANIMAL_PLUSH_ITEM, Unit.CASE)


@st.composite
def _lines(draw: st.DrawFn) -> RemedyLineFacts:
    unit = draw(st.sampled_from((*_COUNT_UNITS, Unit.KG, Unit.M2)))
    if unit in _COUNT_UNITS:
        quantity = str(draw(st.integers(min_value=1, max_value=40)))
    else:
        quantity = draw(st.sampled_from(("0.5", "1", "5.2", "6", "12.75")))
    unit_price = draw(st.one_of(st.none(), st.integers(min_value=1, max_value=500_000)))
    net = draw(st.integers(min_value=0, max_value=5_000_000))
    return RemedyLineFacts(
        service_code="ANY",
        unit=unit,
        quantity=quantity,
        unit_price_vnd=unit_price,
        net_amount_vnd=net,
    )


@given(
    line=_lines(),
    kind=st.sampled_from((RemedyKind.DAMAGE_COMPENSATION, RemedyKind.LOST_ITEM)),
    amount=st.integers(min_value=0, max_value=30_000_000),
    committed=st.integers(min_value=0, max_value=3_000_000),
    refunded=st.booleans(),
    garment_share=st.integers(min_value=0, max_value=3_000_000),
    garment_pick=st.integers(min_value=1, max_value=40),
)
@settings(max_examples=500, deadline=None)
def test_no_item_is_ever_capped_above_the_old_line_rule_or_paid_without_the_owner_it_needs(
    line: RemedyLineFacts,
    kind: RemedyKind,
    amount: int,
    committed: int,
    refunded: bool,
    garment_share: int,
    garment_pick: int,
) -> None:
    """Over every line shape: DEC-031 only ever lowers the cap, and never lets staff pay what it
    sends to the owner. Since `REMEDY-GARMENT-001` a line of several garments with a fee each is
    claimed per garment -- a valid position and what already counts against it, never more than
    the line holds -- and the item whose total is compared is that garment."""

    facts = order({"it": line}, refunded=refunded)
    terms = item_compensation_terms(POLICY, facts, "it")
    assert terms is not None
    garment = None if terms.garments is None else 1 + (garment_pick - 1) % terms.garments
    garment_committed = min(garment_share, committed) if garment is not None else None
    outcome = claim(
        kind,
        "it",
        amount,
        facts,
        committed,
        garment=garment,
        garment_committed=garment_committed,
    )
    ceiling_by_line = line.net_amount_vnd * POLICY.damage_compensation_multiple
    per_garment = terms.garments is not None and terms.garments > 1
    item_prior = garment_committed if per_garment and garment_committed is not None else committed
    # Per piece and per line, since the founder's clarification of DEC-031 rule 1: one proposal is
    # capped at one item's ceiling, and everything on the line at the sum of its items' ceilings --
    # which is never above the old line-wide cap (5x what the line was charged).
    assert terms.ceiling_vnd <= terms.line_ceiling_vnd <= ceiling_by_line
    if isinstance(outcome, RemedyRefused):
        assert outcome.refusal is RemedyRefusal.REMEDY_CEILING_EXCEEDED
        assert outcome.ceiling_vnd in {terms.ceiling_vnd, terms.line_ceiling_vnd}
        assert (
            amount > terms.ceiling_vnd
            or item_prior + amount > terms.ceiling_vnd
            or committed + amount > terms.line_ceiling_vnd
        )
        return
    assert isinstance(outcome, RemedyAuthorized)
    assert outcome.ceiling_vnd is not None and outcome.line_ceiling_vnd is not None
    assert isinstance(outcome.ceiling_vnd, int) and outcome.ceiling_vnd >= 0
    assert amount <= outcome.ceiling_vnd
    assert item_prior + amount <= outcome.ceiling_vnd
    assert committed + amount <= outcome.line_ceiling_vnd <= ceiling_by_line
    assert outcome.garment_index == garment
    if line.unit_price_vnd is not None and line.unit in _COUNT_UNITS:
        assert outcome.ceiling_vnd <= line.unit_price_vnd * POLICY.damage_compensation_multiple
    must_ask_owner = (
        kind is RemedyKind.LOST_ITEM
        or refunded
        or outcome.item_fee_basis is ItemFeeBasis.NOT_RECORDED
        or item_prior + amount > POLICY.staff_approval_ceiling_vnd
    )
    assert outcome.requires_owner_approval is must_ask_owner
    assert outcome.requires_owner_approval is bool(outcome.owner_reasons)
