"""A reserved remedy credit survives a reprice, whole, or is released with a reason -- never lost.

The domain half of the credit-lifecycle fix. The reviewer's reproduction was four lines: price
4 kg (100.000 d), redeem a 30.000 d credit (70.000 d), re-price at 5 kg -- 125.000 d, no credit on
it, and the credit already burnt. `compose_quote_revision` had no way to be told a credit existed.

Now it is told, by the caller, with `reserved_remedy_credits(<the revision being replaced>)`, and
these pin what it does with them: land each whole through the same allocator a first reservation
uses, or release it -- stated on the revision -- when the new bill cannot carry it. Never a partial
credit, never a negative bill.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from hypothesis import given, settings
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import FulfillmentMode, QuantityBasis, Unit
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv, runtime_price_rules
from nha_trang_laundry_domain.promotion import PromotionReason
from nha_trang_laundry_domain.quote_composition import (
    ComposedQuote,
    PricebookProvenance,
    RequestedLine,
    UnresolvedQuote,
    accept_quote_revision,
    compose_quote_revision,
    redeem_remedy_credit,
    released_remedy_credit_ids,
    reserved_remedy_credits,
)
from nha_trang_laundry_domain.quotes import QuoteAdjustmentKind, verify_quote_snapshot
from nha_trang_laundry_domain.remedies import (
    REMEDY_CREDIT_APPLIED,
    REMEDY_CREDIT_RELEASED,
    RemedyCredit,
    RemedyRefusal,
)
from test_promotion_wiring import live

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 18, 3, 0, tzinfo=UTC)
POLICY_ID = UUID("00000000-0000-0000-0000-0000000007a1")
PRICEBOOK = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
RULES = runtime_price_rules(PRICEBOOK)
PROVENANCE = PricebookProvenance(
    UUID("00000000-0000-0000-0000-0000000007a2"), 1, PRICEBOOK.manifest.canonical_snapshot_hash
)


def _kg(quantity: str) -> RequestedLine:
    return RequestedLine("STANDARD_WASH_DRY", quantity, Unit.KG, QuantityBasis.STAFF_MEASUREMENT)


def _price(
    quote_id: UUID,
    revision: int,
    quantity: str,
    *,
    carried: tuple[RemedyCredit, ...] = (),
    spent: frozenset[UUID] = frozenset(),
    promotion: object = None,
) -> ComposedQuote:
    composed = compose_quote_revision(
        quote_id=quote_id,
        revision=revision,
        rules=RULES,
        requested=(_kg(quantity),),
        pricebook=PROVENANCE,
        priced_at=NOW,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        promotion=promotion,  # type: ignore[arg-type]
        remedy_credits=carried,
        spent_remedy_credit_ids=spent,
    )
    assert isinstance(composed, ComposedQuote), composed
    assert verify_quote_snapshot(composed.snapshot)
    return composed


def _credit(amount: int) -> RemedyCredit:
    return RemedyCredit(credit_id=uuid4(), amount_vnd=amount, policy_version_id=POLICY_ID)


def _reserve(priced: ComposedQuote, credit: RemedyCredit) -> ComposedQuote:
    landed = redeem_remedy_credit(
        priced=priced.snapshot, revision=priced.snapshot.data.revision + 1, credit=credit
    )
    assert isinstance(landed, ComposedQuote), landed
    return landed


def test_the_reviewers_reproduction_now_keeps_the_credit() -> None:
    """4 kg = 100.000; less 30.000 = 70.000; re-priced at 5 kg = 125.000 less 30.000 = 95.000."""

    quote_id = uuid4()
    credit = _credit(30_000)
    first = _price(quote_id, 1, "4")
    assert first.snapshot.data.totals.display_total_min_vnd == 100_000
    credited = _reserve(first, credit)
    assert credited.snapshot.data.totals.display_total_min_vnd == 70_000
    assert reserved_remedy_credits(credited.snapshot) == (credit,)

    repriced = _price(quote_id, 3, "5", carried=reserved_remedy_credits(credited.snapshot))
    data = repriced.snapshot.data
    assert data.totals.display_total_min_vnd == 95_000
    assert [a.kind for a in data.adjustments] == [QuoteAdjustmentKind.REMEDY_CREDIT]
    assert REMEDY_CREDIT_APPLIED in data.reason_codes
    assert reserved_remedy_credits(repriced.snapshot) == (credit,)

    accepted = accept_quote_revision(priced=repriced.snapshot, revision=4, accepted_at=NOW)
    assert isinstance(accepted, ComposedQuote)
    assert accepted.snapshot.data.totals.display_total_min_vnd == 95_000
    assert reserved_remedy_credits(accepted.snapshot) == (credit,)


def test_the_six_kilogram_cliff_is_repriced_honestly_under_a_carried_credit() -> None:
    """5,9 kg is 147.500 d and 6,0 kg is 120.000 d. The credit rides both, unchanged."""

    quote_id = uuid4()
    credit = _credit(20_000)
    credited = _reserve(_price(quote_id, 1, "5.9"), credit)
    assert credited.snapshot.data.totals.display_total_min_vnd == 147_500 - 20_000
    at_cliff = _price(quote_id, 3, "6", carried=reserved_remedy_credits(credited.snapshot))
    assert at_cliff.snapshot.data.totals.display_total_min_vnd == 120_000 - 20_000


def test_a_credit_larger_than_the_new_bill_is_released_whole_and_says_so() -> None:
    quote_id = uuid4()
    credit = _credit(30_000)
    credited = _reserve(_price(quote_id, 1, "4"), credit)
    # 1 kg is the 25.000 d minimum: less than the credit.
    smaller = _price(quote_id, 3, "1", carried=reserved_remedy_credits(credited.snapshot))
    data = smaller.snapshot.data
    assert data.totals.display_total_min_vnd == 25_000
    assert data.totals.discount_amount_min_vnd == 0
    assert data.adjustments == ()
    assert REMEDY_CREDIT_RELEASED in data.reason_codes
    assert REMEDY_CREDIT_APPLIED not in data.reason_codes
    assert reserved_remedy_credits(smaller.snapshot) == ()
    assert released_remedy_credit_ids(smaller.snapshot) == {credit.credit_id}
    trace = next(t for t in data.calculation_traces if t.component.startswith("REMEDY_CREDIT_REL"))
    assert b'"reason_code":"REMEDY_CREDIT_UNALLOCATABLE"' in trace.trace.canonical_json
    assert b'"credit_vnd":30000' in trace.trace.canonical_json


def test_a_credit_another_order_already_spent_is_released_rather_than_carried() -> None:
    quote_id = uuid4()
    credit = _credit(11_000)
    credited = _reserve(_price(quote_id, 1, "4"), credit)
    repriced = _price(
        quote_id,
        3,
        "4",
        carried=reserved_remedy_credits(credited.snapshot),
        spent=frozenset({credit.credit_id}),
    )
    assert repriced.snapshot.data.totals.display_total_min_vnd == 100_000
    assert released_remedy_credit_ids(repriced.snapshot) == {credit.credit_id}
    trace = next(
        t
        for t in repriced.snapshot.data.calculation_traces
        if t.component.startswith("REMEDY_CREDIT_REL")
    )
    assert b'"reason_code":"REMEDY_CREDIT_ALREADY_REDEEMED"' in trace.trace.canonical_json


def test_two_carried_credits_land_independently() -> None:
    """One fits and one does not: the one that fits is carried whole, the other released whole."""

    quote_id = uuid4()
    small, large = _credit(10_000), _credit(60_000)
    first = _reserve(_reserve(_price(quote_id, 1, "4"), small), large)
    assert first.snapshot.data.totals.display_total_min_vnd == 100_000 - 70_000
    # 2 kg is 50.000 d: 10.000 fits, and 60.000 does not fit on top of it.
    repriced = _price(quote_id, 4, "2", carried=reserved_remedy_credits(first.snapshot))
    assert repriced.snapshot.data.totals.display_total_min_vnd == 50_000 - 10_000
    assert [c.credit_id for c in reserved_remedy_credits(repriced.snapshot)] == [small.credit_id]
    assert released_remedy_credit_ids(repriced.snapshot) == {large.credit_id}


def test_the_same_credit_cannot_be_reserved_twice_on_one_bill() -> None:
    credit = _credit(11_000)
    credited = _reserve(_price(uuid4(), 1, "4"), credit)
    again = redeem_remedy_credit(priced=credited.snapshot, revision=3, credit=credit)
    assert isinstance(again, UnresolvedQuote)
    assert again.reason_codes == (RemedyRefusal.REMEDY_CREDIT_ALREADY_ON_QUOTE.value,)


def test_a_carried_credit_withholds_a_non_stacking_programme_until_it_is_released() -> None:
    """The credit was on the bag first, so the programme that may not stack is withheld -- and if
    the credit turns out not to fit and is released, the programme applies after all."""

    programme = live()
    quote_id = uuid4()
    credit = _credit(30_000)
    credited = _reserve(_price(quote_id, 1, "4"), credit)

    # 6 kg under a live 30% programme would be 84.000 d. With the credit carried, the programme is
    # withheld and the credit applies to the list price: 120.000 - 30.000.
    carried = _price(
        quote_id, 3, "6", carried=reserved_remedy_credits(credited.snapshot), promotion=programme
    )
    data = carried.snapshot.data
    assert data.totals.display_total_min_vnd == 90_000
    assert PromotionReason.PROMOTION_STACKING_REQUIRES_HUMAN.value in data.reason_codes

    # 1 kg: the credit cannot fit and is released, so nothing is stacking and the programme
    # applies -- 25.000 less 30% is 17.500.
    released = _price(
        quote_id, 3, "1", carried=reserved_remedy_credits(credited.snapshot), promotion=programme
    )
    data = released.snapshot.data
    assert REMEDY_CREDIT_RELEASED in data.reason_codes
    assert PromotionReason.PROMOTION_STACKING_REQUIRES_HUMAN.value not in data.reason_codes
    assert data.totals.display_total_min_vnd == 17_500


@given(
    first_kg=st.integers(min_value=1, max_value=200).map(lambda tenths: Decimal(tenths) / 10),
    second_kg=st.integers(min_value=1, max_value=200).map(lambda tenths: Decimal(tenths) / 10),
    amounts=st.lists(st.integers(min_value=1, max_value=400_000), min_size=1, max_size=3),
)
@settings(max_examples=200, deadline=timedelta(seconds=5))
def test_a_carried_credit_never_exceeds_the_bill_and_never_lands_partly(
    first_kg: Decimal, second_kg: Decimal, amounts: list[int]
) -> None:
    """Whatever the bag weighs before and after, and whatever the credits are worth:

    * the credit total on the re-priced revision never exceeds its service subtotal, so the bill is
      never negative (invariant 2);
    * each carried credit is either on the new revision at exactly its face value, or named as
      released -- never both, never neither, never a smaller amount.
    """

    quote_id = uuid4()
    priced = _price(quote_id, 1, str(first_kg))
    reserved: list[RemedyCredit] = []
    for amount in amounts:
        credit = _credit(amount)
        landed = redeem_remedy_credit(
            priced=priced.snapshot, revision=priced.snapshot.data.revision + 1, credit=credit
        )
        if isinstance(landed, ComposedQuote):
            priced = landed
            reserved.append(credit)
    carried = reserved_remedy_credits(priced.snapshot)
    assert {c.credit_id for c in carried} == {c.credit_id for c in reserved}

    repriced = _price(quote_id, priced.snapshot.data.revision + 1, str(second_kg), carried=carried)
    totals = repriced.snapshot.data.totals
    assert 0 <= totals.discount_amount_min_vnd <= totals.list_service_subtotal_min_vnd
    assert totals.net_service_subtotal_min_vnd >= 0
    assert totals.display_total_min_vnd is not None and totals.display_total_min_vnd >= 0

    on_bill = {c.credit_id: c.amount_vnd for c in reserved_remedy_credits(repriced.snapshot)}
    released = released_remedy_credit_ids(repriced.snapshot)
    for credit in reserved:
        assert (credit.credit_id in on_bill) != (credit.credit_id in released)
        if credit.credit_id in on_bill:
            assert on_bill[credit.credit_id] == credit.amount_vnd
    assert totals.discount_amount_min_vnd == sum(on_bill.values())


def test_a_quote_with_no_credit_is_composed_exactly_as_before() -> None:
    """No credit, no change: the same revision, byte for byte, as a caller that passes nothing.

    Every stored revision's digest depends on this. Carrying credits must not perturb a quote that
    has none, or a revision recomputed from its inputs would stop matching its own snapshot.
    """

    quote_id = uuid4()
    plain = compose_quote_revision(
        quote_id=quote_id,
        revision=1,
        rules=RULES,
        requested=(_kg("4"),),
        pricebook=PROVENANCE,
        priced_at=NOW,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
    )
    explicit = _price(quote_id, 1, "4", carried=(), spent=frozenset({uuid4()}))
    assert isinstance(plain, ComposedQuote)
    assert plain.snapshot.document.snapshot_hash == explicit.snapshot.document.snapshot_hash
