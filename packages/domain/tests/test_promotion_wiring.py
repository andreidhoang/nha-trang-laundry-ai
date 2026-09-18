"""A published promotion reaches the price, and may not move between the quote and the handshake.

`PROMO-WIRING-001`, `DEC-002`, `DEC-021`. The engine in `promotion.py` was complete and correct and
had no production call site; these tests are about the wiring, not the arithmetic. What they hold
down is the four things the wiring can get wrong:

* the programme must come from a published document, so the owner can run one without a deploy;
* a zero must say *why* it is zero, because today's honest answer is zero and the only confirmed
  programme expired on 31/08/2026;
* the number frozen at quote time must be the number charged, or the acceptance is refused;
* a band is not a price, so a promotion waits for the amount a person chooses inside it.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from nha_trang_laundry_domain.catalog import (
    ErrorCode,
    FulfillmentMode,
    PromotionEligibilityEvent,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
    Unit,
)
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv, runtime_price_rules
from nha_trang_laundry_domain.pricing import PriceRule
from nha_trang_laundry_domain.promotion import (
    RATE_DENOMINATOR,
    PromotionReason,
    allocate_largest_remainder,
)
from nha_trang_laundry_domain.promotion_policy import (
    PROMOTION_COMPONENT,
    PROMOTION_SNAPSHOT_CONFIG_TYPE,
    PromotionPolicyError,
    PublishedPromotionProgram,
    parse_promotion_policy,
)
from nha_trang_laundry_domain.quote_composition import (
    PROMOTION_CHANGED_SINCE_QUOTE,
    PROMOTION_NOT_PUBLISHED,
    PROMOTION_PENDING_BAND_CLOSE,
    ComposedQuote,
    PricebookProvenance,
    RequestedLine,
    UnresolvedQuote,
    accept_quote_revision,
    close_range_prices,
    compose_quote_revision,
    frozen_promotion,
    stored_price_bands,
)
from nha_trang_laundry_domain.quotes import (
    ExactLineAmounts,
    QuoteAdjustmentKind,
    RangeLineAmounts,
    verify_quote_snapshot,
)
from nha_trang_laundry_domain.range_prices import (
    PriceBand,
    RangePriceAttestation,
    RangePriceChoice,
)

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "templates/promotion-policy-dec-002.json"
PRICEBOOK_ID = UUID("00000000-0000-0000-0000-0000000004a1")
PROMOTION_ID = UUID("00000000-0000-0000-0000-0000000005b1")
OTHER_PROMOTION_ID = UUID("00000000-0000-0000-0000-0000000005b2")
APPROVAL_ID = UUID("00000000-0000-0000-0000-0000000004b1")
HASH = f"JCS-SHA256-V1:{'a' * 64}"

#: Today, as the packet states it. The shipped programme ran 17/07 to 31/08/2026 inclusive, so this
#: moment is deliberately outside it: an expired programme is the *normal* case in this repository
#: right now, and a test suite that only ever ran inside a live window would not notice.
NOW = datetime(2026, 9, 18, 3, 0, tzinfo=UTC)

STANDARD = "STANDARD_WASH_DRY"
AO_DAI = "DC_AO_DAI_TRADITIONAL"
#: Confirmed at 30%, but `applicability` is `UNCLEAR` in the owner's own source, so the programme
#: records it as `HUMAN_CONFIRM`: the shop has not decided whether upholstery is covered.
SOFA = "OTHER_SOFA"
#: `OUT_OF_SCOPE` in the source -- the storefront sign does not mention ironing.
IRONING = "IRON_SUIT"


def rules() -> dict[str, PriceRule]:
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    return runtime_price_rules(pricebook)


def provenance() -> PricebookProvenance:
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    return PricebookProvenance(PRICEBOOK_ID, 1, pricebook.manifest.canonical_snapshot_hash)


def document() -> dict[str, Any]:
    """The shipped programme, as the owner would publish it."""

    payload: dict[str, Any] = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    return payload


def program(
    *,
    start_at: str | None = None,
    end_at_exclusive: str | None = None,
    version_id: UUID = PROMOTION_ID,
    version: int = 1,
    **overrides: Any,
) -> PublishedPromotionProgram:
    """One published programme version, parsed from a document with the window moved if asked.

    The window is the only thing most of these tests vary, because it is the only thing that decides
    whether the shop's one confirmed programme applies. Moving it here rather than hand-building a
    `PromotionPolicy` keeps every test running through the same parse the publication path runs.
    """

    payload = document()
    if start_at is not None:
        payload["start_at"] = start_at
    if end_at_exclusive is not None:
        payload["end_at_exclusive"] = end_at_exclusive
    payload.update(overrides)
    return PublishedPromotionProgram(
        program=parse_promotion_policy(payload),
        version_id=version_id,
        version=version,
        snapshot_hash=HASH,
    )


def live(
    *,
    start_at: str = "2026-09-01T00:00:00+07:00",
    end_at_exclusive: str = "2026-10-01T00:00:00+07:00",
    **overrides: Any,
) -> PublishedPromotionProgram:
    """A programme whose window contains `NOW`, and a week either side of it.

    Both bounds are named parameters with those defaults rather than positional arguments passed
    through `**overrides`, so that a test which wants a live programme ending *tomorrow* -- the
    expires-between-quote-and-acceptance case -- can say so without colliding with the default.
    """

    return program(start_at=start_at, end_at_exclusive=end_at_exclusive, **overrides)


def expired(**overrides: Any) -> PublishedPromotionProgram:
    """The shipped programme, unchanged: 17/07/2026 to 31/08/2026 inclusive, and over."""

    return program(**overrides)


def compose(
    *lines: RequestedLine,
    quote_id: UUID | None = None,
    revision: int = 1,
    promotion: PublishedPromotionProgram | None = None,
    present_range_as_band: bool = False,
    priced_at: datetime = NOW,
) -> ComposedQuote | UnresolvedQuote:
    """Compose against the walk-in case, so the delivery fee resolves to zero and never masks a
    promotion refusal with a delivery one."""

    return compose_quote_revision(
        quote_id=quote_id or uuid4(),
        revision=revision,
        rules=rules(),
        requested=lines,
        pricebook=provenance(),
        priced_at=priced_at,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        present_range_as_band=present_range_as_band,
        promotion=promotion,
    )


def line(service_code: str, quantity: str, unit: Unit = Unit.KG) -> RequestedLine:
    return RequestedLine(service_code, quantity, unit, QuantityBasis.STAFF_MEASUREMENT)


def standard(quantity: str = "6") -> RequestedLine:
    return line(STANDARD, quantity)


# --- a live programme reaches the price ---------------------------------------------------------


def test_a_quote_inside_a_live_programme_carries_a_provisional_discount_a_rate_and_a_code() -> None:
    """The item's first required outcome, and the one the engine could never previously produce.

    6 kg of standard wash is 120.000 d at 20.000/kg (`STD_WASH_DRY_GE6`, the far side of the cliff),
    and the programme discounts it 30%: 36.000 d off, 84.000 d to pay. Provisional rather than final
    because eligibility is keyed to `accepted_at` and nobody has accepted anything yet.
    """

    result = compose(standard("6"), promotion=live())
    assert isinstance(result, ComposedQuote)
    data = result.snapshot.data
    totals = data.totals

    assert totals.list_service_subtotal_max_vnd == 120_000
    assert totals.discount_amount_min_vnd == totals.discount_amount_max_vnd == 36_000
    assert totals.net_service_subtotal_max_vnd == 84_000
    assert totals.display_total_max_vnd == 84_000

    promotion = frozen_promotion(result.snapshot)
    assert promotion is not None
    assert promotion.policy_code == "PROMO_WET30_DRY40_20260717_20260831"
    assert promotion.rate_bps == (3_000,)
    assert promotion.status == "PROVISIONAL"
    assert promotion.discount_amount_vnd == 36_000
    assert PromotionReason.PROMOTION_APPLIED.value in data.reason_codes
    assert ErrorCode.PROMOTION_ELIGIBILITY_UNRESOLVED.value in data.reason_codes
    assert verify_quote_snapshot(result.snapshot)


def test_the_discount_is_a_credit_adjustment_naming_the_programme_that_produced_it() -> None:
    """A discount on an immutable revision that cannot be traced to a programme is a number
    somebody typed. `_validate_adjustments` also requires the credit rows to reconcile to the
    line-level discounts, so this assertion is checked twice from two directions."""

    result = compose(standard("6"), promotion=live())
    assert isinstance(result, ComposedQuote)
    adjustments = [
        item
        for item in result.snapshot.data.adjustments
        if item.kind is QuoteAdjustmentKind.PROMOTION
    ]
    assert len(adjustments) == 1
    assert adjustments[0].amount_min_vnd == adjustments[0].amount_max_vnd == 36_000
    assert adjustments[0].reason_code == "PROMO_WET30_DRY40_20260717_20260831"
    assert adjustments[0].source_version_id == PROMOTION_ID
    # No envelope: publishing the programme was the approval for an automatic in-interval discount.
    assert adjustments[0].approval_id is None
    reference = next(
        item
        for item in result.snapshot.data.configuration_snapshots
        if item.config_type == PROMOTION_SNAPSHOT_CONFIG_TYPE
    )
    assert (reference.version_id, reference.version) == (PROMOTION_ID, 1)


def test_the_six_kilogram_cliff_is_discounted_where_it_fell_not_smoothed() -> None:
    """The promotion applies to the list amount the pricing engine produced, on whichever side of
    the cliff it landed. 5.999 kg is 149.975 d at 25.000/kg and 6 kg is 120.000 d at 20.000/kg, so
    the discounted totals stay discontinuous -- a promotion is not a licence to interpolate."""

    below = compose(standard("5.999"), promotion=live())
    at = compose(standard("6"), promotion=live())
    assert isinstance(below, ComposedQuote) and isinstance(at, ComposedQuote)
    # 149.975 x 3000 / 10000 = 44992.5, rounded half up to 44.993.
    assert below.snapshot.data.totals.discount_amount_max_vnd == 44_993
    assert below.snapshot.data.totals.net_service_subtotal_max_vnd == 104_982
    assert at.snapshot.data.totals.net_service_subtotal_max_vnd == 84_000
    assert at.snapshot.data.totals.net_service_subtotal_max_vnd < (
        below.snapshot.data.totals.net_service_subtotal_max_vnd
    )


# --- the two zeroes are not the same zero -------------------------------------------------------


def test_with_no_programme_published_a_quote_composes_at_list_price_and_says_so() -> None:
    """Invariant 11, and the fail-closed direction for a discount is to charge list price.

    There is no constant to fall back on, which is the whole difference from `CURRENT_PROMOTION`.
    """

    result = compose(standard("6"), promotion=None)
    assert isinstance(result, ComposedQuote)
    totals = result.snapshot.data.totals
    assert totals.discount_amount_max_vnd == 0
    assert totals.net_service_subtotal_max_vnd == 120_000
    assert PROMOTION_NOT_PUBLISHED in result.snapshot.data.reason_codes
    assert frozen_promotion(result.snapshot) is None
    assert not any(
        item.config_type == PROMOTION_SNAPSHOT_CONFIG_TYPE
        for item in result.snapshot.data.configuration_snapshots
    )


def test_a_published_but_expired_programme_says_the_interval_ended_and_carries_its_end_date() -> (
    None
):
    """The item's headline case. Today is 18/09/2026 and the shop's one confirmed programme ended on
    31/08/2026, so the correct discount is zero -- but not the same zero as the line above.

    The engine on its own would answer `PROMOTION_ELIGIBILITY_UNRESOLVED` here, because acceptance
    has not happened and that reason shadows the interval one. `_promotion_reasons` replaces it once
    the programme has *ended*, which is a fact of arithmetic rather than a judgement: acceptance is
    always at or after pricing, so no acceptance derived from this quote can fall inside a window
    that closed before it was priced.

    The end date reaches the console through the frozen trace, because "0 d" with no sentence beside
    it is the same failure as rendering a null total as `0`.
    """

    result = compose(standard("6"), promotion=expired())
    assert isinstance(result, ComposedQuote)
    data = result.snapshot.data
    assert data.totals.discount_amount_max_vnd == 0
    assert data.totals.net_service_subtotal_max_vnd == 120_000
    assert PromotionReason.PROMOTION_OUTSIDE_INTERVAL.value in data.reason_codes
    assert ErrorCode.PROMOTION_ELIGIBILITY_UNRESOLVED.value not in data.reason_codes
    assert PROMOTION_NOT_PUBLISHED not in data.reason_codes

    promotion = frozen_promotion(result.snapshot)
    assert promotion is not None
    assert promotion.candidate_inside_interval is False
    # 1 Sep 2026 00:00 +07:00 is 31 Aug 2026 17:00 UTC. The console renders the last covered day,
    # which is the day before the exclusive bound in the programme's own timezone: 31/08/2026.
    assert promotion.interval_end_at_exclusive == "2026-08-31T17:00:00.000000Z"


def test_a_programme_that_has_not_started_yet_stays_eligibility_unresolved() -> None:
    """The mirror of the case above, and the reason the substitution is conditioned on *ended*
    rather than on *outside*. A programme starting next week may well cover the acceptance this
    quote leads to, so eligibility genuinely is unresolved and claiming otherwise would be a guess
    about the future."""

    future = program(
        start_at="2026-10-01T00:00:00+07:00", end_at_exclusive="2026-11-01T00:00:00+07:00"
    )
    result = compose(standard("6"), promotion=future)
    assert isinstance(result, ComposedQuote)
    assert ErrorCode.PROMOTION_ELIGIBILITY_UNRESOLVED.value in result.snapshot.data.reason_codes
    assert result.snapshot.data.totals.discount_amount_max_vnd == 0


def test_a_service_the_programme_does_not_cover_is_not_targeted() -> None:
    """Silence is not a discount. `OTHER_HARD_CARPET` is in the programme and this one is not,
    because the published document is a list of services rather than a blanket rate."""

    payload = document()
    payload["start_at"] = "2026-09-01T00:00:00+07:00"
    payload["end_at_exclusive"] = "2026-10-01T00:00:00+07:00"
    payload["services"] = [item for item in payload["services"] if item["service_code"] != STANDARD]
    narrowed = PublishedPromotionProgram(
        program=parse_promotion_policy(payload),
        version_id=PROMOTION_ID,
        version=1,
        snapshot_hash=HASH,
    )
    result = compose(standard("6"), promotion=narrowed)
    assert isinstance(result, ComposedQuote)
    assert result.snapshot.data.totals.discount_amount_max_vnd == 0
    assert PromotionReason.PROMOTION_NOT_TARGETED.value in result.snapshot.data.reason_codes


@pytest.mark.parametrize("service_code", [SOFA, IRONING])
def test_a_service_the_owner_left_unconfirmed_needs_the_owner_before_it_is_discounted(
    service_code: str,
) -> None:
    """`HUMAN_CONFIRM` is the owner's own record that the sign did not say. The line is priced at
    list, the revision says a person must decide, and `ApplyPromotion` is required -- which is the
    one case the packet keeps the envelope for. Nothing here is a discount applied on a guess."""

    quantity = "1"
    unit = Unit.SET if service_code == SOFA else Unit.ITEM
    requested = line(service_code, quantity, unit)
    result = compose(requested, promotion=live(), present_range_as_band=service_code == SOFA)
    assert isinstance(result, ComposedQuote)
    if service_code == SOFA:
        # A band, so the promotion has not been evaluated at all yet; the human-confirm question
        # arrives when the band is closed.
        assert PROMOTION_PENDING_BAND_CLOSE in result.snapshot.data.reason_codes
        return
    data = result.snapshot.data
    assert data.totals.discount_amount_max_vnd == 0
    assert PromotionReason.PROMOTION_TARGET_REQUIRES_HUMAN.value in data.reason_codes
    assert data.required_approvals == ("APPLY_PROMOTION",)


# --- freeze at quote, re-verify at acceptance ---------------------------------------------------


def priced_under(promotion: PublishedPromotionProgram, *lines: RequestedLine) -> ComposedQuote:
    result = compose(*(lines or (standard("6"),)), promotion=promotion)
    assert isinstance(result, ComposedQuote)
    return result


def test_accepting_inside_the_interval_finalises_the_same_number_to_the_dong() -> None:
    """`DEC-021`: the customer agreed to a price that was read aloud, and this is the path where
    that price survives. The re-evaluation uses the real `accepted_at`, so eligibility resolves and
    the revision records the event and the moment it happened."""

    programme = live()
    priced = priced_under(programme)
    accepted = accept_quote_revision(
        priced=priced.snapshot,
        revision=2,
        promotion=programme,
        accepted_at=NOW + timedelta(minutes=3),
    )
    assert isinstance(accepted, ComposedQuote)
    data = accepted.snapshot.data
    assert data.finality is QuoteFinality.APPROVED_EXACT
    assert data.status is QuoteRevisionStatus.ACCEPTED_FINAL
    assert data.totals.discount_amount_max_vnd == 36_000
    assert data.totals.display_total_max_vnd == 84_000
    assert data.promotion_eligibility_event is PromotionEligibilityEvent.STORE_COMMERCIAL_ACCEPTED
    assert data.promotion_eligibility_at == NOW + timedelta(minutes=3)
    assert verify_quote_snapshot(accepted.snapshot)


def test_accepting_after_the_interval_ends_refuses_rather_than_repricing() -> None:
    """The failure this whole design exists to prevent: the programme expires between the price
    being read aloud and the customer saying yes, and the till quietly charges 120.000 d for a bag
    quoted at 84.000 d. The acceptance is refused instead, and a person prices it again."""

    programme = live(end_at_exclusive="2026-09-19T00:00:00+07:00")
    priced = priced_under(programme)
    assert priced.snapshot.data.totals.discount_amount_max_vnd == 36_000

    refused = accept_quote_revision(
        priced=priced.snapshot,
        revision=2,
        promotion=programme,
        # One second after the programme's exclusive end, in the programme's own timezone.
        accepted_at=datetime(2026, 9, 18, 17, 0, 1, tzinfo=UTC),
    )
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == (PROMOTION_CHANGED_SINCE_QUOTE,)


def test_a_programme_republished_between_quote_and_acceptance_refuses() -> None:
    """Re-verifying against a different document is not re-verification. The numbers agreeing would
    be a coincidence, and invariant 8's reading of "the content that was bound" covers the programme
    as much as the lines."""

    priced = priced_under(live())
    refused = accept_quote_revision(
        priced=priced.snapshot,
        revision=2,
        promotion=live(version_id=OTHER_PROMOTION_ID, version=2),
        accepted_at=NOW + timedelta(minutes=3),
    )
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == (PROMOTION_CHANGED_SINCE_QUOTE,)


def test_a_programme_published_after_the_quote_refuses_even_though_it_is_cheaper() -> None:
    """A discount that appeared is as much a price the customer did not agree to as one that
    vanished. Refusing sends them back to the counter to hear the lower number, which is the only
    way they can agree to it."""

    priced = priced_under_no_programme()
    refused = accept_quote_revision(
        priced=priced.snapshot,
        revision=2,
        promotion=live(),
        accepted_at=NOW + timedelta(minutes=3),
    )
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == (PROMOTION_CHANGED_SINCE_QUOTE,)


def priced_under_no_programme() -> ComposedQuote:
    result = compose(standard("6"), promotion=None)
    assert isinstance(result, ComposedQuote)
    return result


def test_an_expired_programme_still_accepts_because_the_number_did_not_move() -> None:
    """Today's case end to end. The quote showed 120.000 d because the programme had ended; the
    acceptance re-checks and finds the same zero, so nothing moved and the bag is taken. The
    revision still records that eligibility was assessed, and when."""

    programme = expired()
    priced = priced_under(programme)
    accepted = accept_quote_revision(
        priced=priced.snapshot,
        revision=2,
        promotion=programme,
        accepted_at=NOW + timedelta(minutes=3),
    )
    assert isinstance(accepted, ComposedQuote)
    assert accepted.snapshot.data.totals.display_total_max_vnd == 120_000
    assert (
        accepted.snapshot.data.promotion_eligibility_event
        is PromotionEligibilityEvent.STORE_COMMERCIAL_ACCEPTED
    )
    assert PromotionReason.PROMOTION_OUTSIDE_INTERVAL.value in accepted.snapshot.data.reason_codes


def test_acceptance_without_the_moment_it_happened_refuses() -> None:
    """A promotion keyed to `accepted_at` cannot be re-verified without `accepted_at`, and the
    alternative to refusing is choosing a moment nobody chose."""

    priced = priced_under(live())
    refused = accept_quote_revision(priced=priced.snapshot, revision=2, promotion=live())
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == (ErrorCode.MISSING_REQUIRED_FACT.value,)


def test_a_programme_keyed_to_another_event_cannot_produce_an_exact_price() -> None:
    """Acceptance resolves `STORE_COMMERCIAL_ACCEPTED` and nothing else. A programme the owner keyed
    to production intake is not satisfied by a customer saying yes, so its eligibility stays
    unresolved and the refusal names that rather than leaving the snapshot validator to answer
    `VALIDATION_ERROR`."""

    programme = live(eligibility_event="PRODUCTION_ACCEPTED")
    priced = priced_under(programme)
    refused = accept_quote_revision(
        priced=priced.snapshot,
        revision=2,
        promotion=programme,
        accepted_at=NOW + timedelta(minutes=3),
    )
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == (ErrorCode.PROMOTION_ELIGIBILITY_UNRESOLVED.value,)


# --- the frozen trace has to reproduce the discount ---------------------------------------------


def test_the_frozen_trace_reproduces_the_discount_exactly_on_recomputation() -> None:
    """Three lines at one rate, so the allocation actually has a remainder to award and the trace
    has something to explain. Every intermediate the engine used is on the revision, and running
    `allocate_largest_remainder` over those intermediates reproduces the per-line dong exactly.

    This is what makes the stored discount auditable years later. A revision that carried only the
    answer could not tell a customer why one item took one more dong than another.
    """

    result = compose(
        line(AO_DAI, "3", Unit.SET),
        line("DC_SHIRT", "1", Unit.ITEM),
        line("DC_TROUSERS", "1", Unit.ITEM),
        promotion=live(),
        present_range_as_band=False,
        # A band would defer the promotion; the ao dai line is closed below instead.
    )
    # `DC_AO_DAI_TRADITIONAL` is a band, so the whole revision is refused rather than half-priced.
    assert isinstance(result, UnresolvedQuote)

    exact = compose(
        line("DC_SHIRT", "3", Unit.ITEM),
        line("DC_TROUSERS", "1", Unit.ITEM),
        line("DC_WOOL_COAT_SHORT", "1", Unit.ITEM),
        promotion=live(),
    )
    assert isinstance(exact, ComposedQuote)
    trace = next(
        item
        for item in exact.snapshot.data.calculation_traces
        if item.component == PROMOTION_COMPONENT
    )
    payload = json.loads(trace.trace.canonical_json)
    group = payload["allocation_groups"][0]

    recomputed = allocate_largest_remainder(
        weights=tuple(
            (str(item["line_id"]), int(item["list_amount_vnd"]))
            for item in payload["lines"]
            if item["rate_bps"] == group["rate_bps"] and item["resolution"] == "AUTO_IF_TARGETED"
        ),
        multiplier=int(group["rate_bps"]),
        denominator=RATE_DENOMINATOR,
        total_vnd=int(group["rounded_discount_vnd"]),
    )
    assert list(recomputed.final_allocations_vnd) == group["final_allocations_vnd"]
    assert sum(recomputed.final_allocations_vnd) == payload["discount_amount_vnd"]
    assert payload["discount_amount_vnd"] == exact.snapshot.data.totals.discount_amount_max_vnd
    # And the stored per-line discounts are the same allocation, not a second one.
    by_line = {
        item.line_id: item.amounts.discount_amount_vnd
        for item in exact.snapshot.data.lines
        if isinstance(item.amounts, ExactLineAmounts)
    }
    assert [by_line[line_id] for line_id in recomputed.ordered_ids] == list(
        recomputed.final_allocations_vnd
    )


# --- the wall RANGE-PRICE-001 left --------------------------------------------------------------


def band(quote_id: UUID, promotion: PublishedPromotionProgram | None) -> ComposedQuote:
    result = compose(
        line(AO_DAI, "1", Unit.SET),
        quote_id=quote_id,
        promotion=promotion,
        present_range_as_band=True,
    )
    assert isinstance(result, ComposedQuote)
    return result


def attestation(quote_id: UUID, amount_vnd: int) -> RangePriceAttestation:
    return RangePriceAttestation(
        quote_id=quote_id,
        revision=1,
        pricebook_version_id=PRICEBOOK_ID,
        pricebook_version=1,
        choices=(RangePriceChoice(AO_DAI, amount_vnd),),
        approval_id=APPROVAL_ID,
    )


def test_a_band_carries_no_discount_and_says_the_promotion_is_waiting_for_the_amount() -> None:
    """The rule that replaced the wall. A band is the owner's authorisation to charge somewhere in
    an interval, not a price, and "80.000-240.000 less 40%" is a discount off a number nobody will
    pay. So the banded line stays at its bounds and the revision says why.

    `stored_price_bands` refuses a banded line carrying a discount, and that check is what makes
    this rule checkable from the other side. It stays; it is now unreachable by rule rather than by
    the absence of promotions.
    """

    quote_id = uuid4()
    banded = band(quote_id, live())
    data = banded.snapshot.data
    amounts = data.lines[0].amounts
    assert isinstance(amounts, RangeLineAmounts)
    assert amounts.discount_min_vnd == amounts.discount_max_vnd == 0
    assert (amounts.net_amount_min_vnd, amounts.net_amount_max_vnd) == (80_000, 240_000)
    assert data.totals.discount_amount_max_vnd == 0
    assert PROMOTION_PENDING_BAND_CLOSE in data.reason_codes
    assert ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value in data.reason_codes
    assert frozen_promotion(banded.snapshot) is None
    # The wall still stands, and the band is still readable.
    assert stored_price_bands(banded.snapshot) == {AO_DAI: PriceBand(80_000, 240_000)}


def test_closing_a_band_discounts_the_amount_the_staff_member_chose() -> None:
    """The decision, stated as a number. The owner's own note on `BED_PILLOW` says it outright --
    "nhan vien chon gia niem yet trong khoang truoc khi giam" -- and `DEC-002` keys eligibility to
    `accepted_at`, which is necessarily after the band was closed.

    Dry cleaning is the 40% arm of the programme, so 200.000 d chosen inside the published
    80.000-240.000 band becomes 80.000 d off and 120.000 d to pay.
    """

    quote_id = uuid4()
    programme = live()
    banded = band(quote_id, programme)
    closed = close_range_prices(
        priced=banded.snapshot,
        revision=2,
        attestation=attestation(quote_id, 200_000),
        promotion=programme,
        closed_at=NOW,
    )
    assert isinstance(closed, ComposedQuote)
    data = closed.snapshot.data
    assert data.finality is QuoteFinality.APPROVED_EXACT
    assert data.totals.list_service_subtotal_max_vnd == 200_000
    assert data.totals.discount_amount_max_vnd == 80_000
    assert data.totals.net_service_subtotal_max_vnd == 120_000
    assert data.totals.display_total_max_vnd == 120_000
    assert PROMOTION_PENDING_BAND_CLOSE not in data.reason_codes
    assert ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value not in data.reason_codes
    assert PromotionReason.PROMOTION_APPLIED.value in data.reason_codes
    frozen = frozen_promotion(closed.snapshot)
    assert frozen is not None and frozen.discount_amount_vnd == 80_000
    assert verify_quote_snapshot(closed.snapshot)


def test_closing_a_band_under_a_different_programme_version_refuses() -> None:
    """The band was shown under one programme. Closing it against another would apply a discount the
    customer was never offered."""

    quote_id = uuid4()
    banded = band(quote_id, live())
    refused = close_range_prices(
        priced=banded.snapshot,
        revision=2,
        attestation=attestation(quote_id, 200_000),
        promotion=live(version_id=OTHER_PROMOTION_ID, version=2),
        closed_at=NOW,
    )
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == (PROMOTION_CHANGED_SINCE_QUOTE,)


def test_a_closed_band_under_an_expired_programme_is_charged_in_full() -> None:
    """The band case of today's honest answer: the amount the staff member chose, undiscounted,
    with the revision saying the programme had ended rather than showing a bare zero."""

    quote_id = uuid4()
    programme = expired()
    banded = band(quote_id, programme)
    closed = close_range_prices(
        priced=banded.snapshot,
        revision=2,
        attestation=attestation(quote_id, 200_000),
        promotion=programme,
        closed_at=NOW,
    )
    assert isinstance(closed, ComposedQuote)
    assert closed.snapshot.data.totals.display_total_max_vnd == 200_000
    assert PromotionReason.PROMOTION_OUTSIDE_INTERVAL.value in closed.snapshot.data.reason_codes


def test_a_band_closed_with_no_programme_at_all_is_unchanged_from_before_this_item() -> None:
    """Every existing caller and every existing test passes no programme, and must keep working."""

    quote_id = uuid4()
    banded = band(quote_id, None)
    closed = close_range_prices(
        priced=banded.snapshot, revision=2, attestation=attestation(quote_id, 200_000)
    )
    assert isinstance(closed, ComposedQuote)
    assert closed.snapshot.data.totals.display_total_max_vnd == 200_000
    assert closed.snapshot.data.totals.discount_amount_max_vnd == 0


# --- the document itself ------------------------------------------------------------------------


def test_the_shipped_document_parses_and_matches_the_owners_confirmed_programme() -> None:
    """The template is a transcription of `templates/promotions.csv` and
    `templates/promotion-service-rules.csv`, and it has to stay one."""

    parsed = parse_promotion_policy(document())
    assert parsed.policy.code == "PROMO_WET30_DRY40_20260717_20260831"
    assert parsed.policy.stacking_allowed is False
    assert parsed.policy.eligibility_event is PromotionEligibilityEvent.STORE_COMMERCIAL_ACCEPTED
    assert parsed.rule_for(STANDARD).rate_bps == 3_000
    assert parsed.rule_for("DC_SHIRT").rate_bps == 4_000
    assert parsed.rule_for(SOFA).resolution.value == "HUMAN_CONFIRM"
    # A service the document does not name gets nothing, rather than a neighbour's rate.
    assert parsed.rule_for("NOT_A_SERVICE").rate_bps is None


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"eligibility_event": None}, "no eligibility event"),
        ({"eligibility_event": "NOT_AN_EVENT"}, "an invented eligibility event"),
        ({"timezone": "UTC"}, "a timezone the engine will not evaluate"),
        ({"code": "promo lowercase"}, "a code that cannot go on a money line"),
        ({"stacking_allowed": "false"}, "stacking stated as a string"),
        ({"start_at": "2026-07-17T00:00:00"}, "a boundary with no UTC offset"),
        ({"start_at": "2026-10-01T00:00:00+07:00"}, "a window that ends before it starts"),
        ({"decision": "DEC-004"}, "the wrong decision"),
        ({"schema": "remedy-policy-v1"}, "another type's schema tag"),
        ({"services": []}, "no targeted services"),
    ],
)
def test_a_document_that_could_not_price_is_refused_at_publication(
    mutation: dict[str, Any], reason: str
) -> None:
    """Refused while somebody can still fix it, rather than at the counter with a customer waiting.
    Each mutation is a document that would otherwise reach the till and fail there."""

    payload = document()
    payload.update(mutation)
    with pytest.raises(PromotionPolicyError):
        parse_promotion_policy(payload)


@pytest.mark.parametrize(
    "rule",
    [
        {"service_code": "DC_SHIRT", "resolution": "AUTO_IF_TARGETED", "rate_bps": 0},
        {"service_code": "DC_SHIRT", "resolution": "AUTO_IF_TARGETED", "rate_bps": 10_001},
        {"service_code": "DC_SHIRT", "resolution": "AUTO_IF_TARGETED", "rate_bps": True},
        {"service_code": "DC_SHIRT", "resolution": "AUTO_IF_TARGETED", "rate_bps": None},
        {"service_code": "DC_SHIRT", "resolution": "NOT_ELIGIBLE", "rate_bps": 3_000},
        {"service_code": "dc_shirt", "resolution": "AUTO_IF_TARGETED", "rate_bps": 3_000},
        {"service_code": "DC_SHIRT", "resolution": "MAYBE", "rate_bps": 3_000},
    ],
)
def test_a_service_rule_that_is_not_a_rate_is_refused(rule: dict[str, Any]) -> None:
    """`rate_bps: true` is the one worth naming: `True` is an `int` in Python, so without the bool
    check it would publish a one-basis-point discount nobody decided."""

    payload = document()
    payload["services"] = [rule]
    with pytest.raises(PromotionPolicyError):
        parse_promotion_policy(payload)


def test_one_service_may_not_be_targeted_twice() -> None:
    """Two rules for one service would give one line two rates, and choosing between them would be
    this code deciding what the owner meant."""

    payload = document()
    duplicate = deepcopy(payload["services"][0])
    payload["services"] = [*payload["services"], duplicate]
    with pytest.raises(PromotionPolicyError):
        parse_promotion_policy(payload)
