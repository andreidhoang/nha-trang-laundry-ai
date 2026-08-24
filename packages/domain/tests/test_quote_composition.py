"""The composition may not drift from the engine, and may not resolve what policy has not."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import (
    FulfillmentMode,
    PriceRuleType,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
    Unit,
)
from nha_trang_laundry_domain.pricebook_import import (
    canonical_pricebook_payload,
    import_pricebook_csv,
    published_price_rules,
    runtime_price_rules,
)
from nha_trang_laundry_domain.pricing import PriceLine, PriceRule, price_lines
from nha_trang_laundry_domain.quote_composition import (
    ComposedQuote,
    PricebookProvenance,
    RequestedLine,
    UnresolvedQuote,
    compose_quote_revision,
)
from nha_trang_laundry_domain.quotes import (
    ExactLineAmounts,
    QuoteAdjustmentKind,
    verify_quote_snapshot,
)

ROOT = Path(__file__).resolve().parents[3]
PRICEBOOK_ID = UUID("00000000-0000-0000-0000-0000000004a1")
NOW = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)
STANDARD = "STANDARD_WASH_DRY"


def rules() -> dict[str, PriceRule]:
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    return runtime_price_rules(pricebook)


def provenance() -> PricebookProvenance:
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    return PricebookProvenance(PRICEBOOK_ID, 1, pricebook.manifest.canonical_snapshot_hash)


def compose(
    *lines: RequestedLine,
    revision: int = 1,
    quote_id: UUID | None = None,
    fulfillment_mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
    verified_distance_m: int | None = None,
) -> ComposedQuote | UnresolvedQuote:
    """Compose one revision. Delivery defaults to the walk-in case because most tests here are
    about pricing, and the walk-in case is the one that resolves without any delivery fact. Tests
    that care about delivery pass the mode explicitly."""
    return compose_quote_revision(
        quote_id=quote_id or uuid4(),
        revision=revision,
        rules=rules(),
        requested=lines,
        pricebook=provenance(),
        priced_at=NOW,
        fulfillment_mode=fulfillment_mode,
        verified_distance_m=verified_distance_m,
    )


def standard(
    quantity: str, basis: QuantityBasis = QuantityBasis.STAFF_MEASUREMENT
) -> RequestedLine:
    return RequestedLine(STANDARD, quantity, Unit.KG, basis)


@pytest.mark.parametrize("quantity", ["1", "5.999", "6", "6.001", "20", "0.5"])
def test_composed_amount_equals_a_direct_engine_call(quantity: str) -> None:
    """The composition must never become a second opinion about price.

    This is the test that makes the whole design defensible: whatever route, service or console
    sits above it, the number persisted is the number `price_lines` returned for the same input.
    """
    engine = price_lines(
        rules(),
        (PriceLine(STANDARD, quantity, Unit.KG, QuantityBasis.STAFF_MEASUREMENT),),
    )[STANDARD]
    result = compose(standard(quantity))
    assert isinstance(result, ComposedQuote)
    line = result.snapshot.data.lines[0]
    assert isinstance(line.amounts, ExactLineAmounts)
    assert line.amounts.list_amount_vnd == engine.list_amount_vnd
    assert line.amounts.net_amount_vnd == engine.list_amount_vnd
    assert line.amounts.unit_price_vnd == engine.trace.unit_price_vnd
    assert result.snapshot.data.totals.net_service_subtotal_max_vnd == engine.list_amount_vnd


def test_the_six_kilogram_cliff_is_preserved_not_smoothed() -> None:
    """Crossing 6 kg lowers the unit price, so 6 kg costs less than 5.999 kg. That is the rule.

    `PRICEBOOK_V1.md` confirms it and `AGENTS.md` names it as a rule rather than a defect. A future
    change that "fixes" the discontinuity by interpolating would pass a naive monotonicity test and
    break the business, so the discontinuity itself is asserted here.
    """
    below = compose(standard("5.999"))
    at = compose(standard("6"))
    assert isinstance(below, ComposedQuote) and isinstance(at, ComposedQuote)
    below_total = below.snapshot.data.totals.net_service_subtotal_max_vnd
    at_total = at.snapshot.data.totals.net_service_subtotal_max_vnd
    assert below_total == 149_975
    assert at_total == 120_000
    assert at_total < below_total


def test_billable_minimum_applies_below_one_kilogram() -> None:
    """A half kilogram is billed as one, and the trace says so rather than the quantity lying."""
    result = compose(standard("0.5"))
    assert isinstance(result, ComposedQuote)
    line = result.snapshot.data.lines[0]
    assert isinstance(line.amounts, ExactLineAmounts)
    assert line.quantity == "0.5"
    assert line.amounts.net_amount_vnd == 25_000
    # Selected by component rather than by index: the snapshot sorts its traces, and a revision
    # now carries a DELIVERY trace alongside the pricing one.
    trace = next(
        item
        for item in result.snapshot.data.calculation_traces
        if item.component.startswith("PRICING_")
    )
    assert b'"billable_quantity":"1"' in trace.trace.canonical_json


def test_a_range_priced_service_is_refused_rather_than_averaged() -> None:
    ranged = [
        code for code, rule in rules().items() if rule.rule_type is PriceRuleType.RANGE_PER_UNIT
    ]
    assert ranged, "the pricebook must contain range-priced services for this test to mean anything"
    result = compose(RequestedLine(ranged[0], "1", Unit.ITEM, QuantityBasis.STAFF_MEASUREMENT))
    assert isinstance(result, UnresolvedQuote)
    assert result.reason_codes == ("RANGE_PRICE_REQUIRES_HUMAN",)


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        ("", "MISSING_REQUIRED_FACT"),
        ("abc", "MISSING_REQUIRED_FACT"),
        ("0", "MISSING_REQUIRED_FACT"),
        ("-4", "MISSING_REQUIRED_FACT"),
        ("4.12345", "MISSING_REQUIRED_FACT"),
    ],
)
def test_an_unusable_quantity_produces_no_price(quantity: str, expected: str) -> None:
    """A quantity the engine will not accept must not become a quote by any route.

    The NVIDIA perception probe run on 2026-08-14 produced `quantity_kg: "1"` from a message that
    said "about one big sack". Nothing in this repository lets a model write directly to this
    function, and this test is why that stays true even if something upstream fabricates a number:
    the engine decides what a quantity is, and an unusable one yields a reason code, not a price.
    """
    result = compose(standard(quantity))
    assert isinstance(result, UnresolvedQuote)
    assert result.reason_codes == (expected,)


def test_an_unknown_service_is_not_guessed() -> None:
    result = compose(
        RequestedLine("NO_SUCH_SERVICE", "1", Unit.KG, QuantityBasis.STAFF_MEASUREMENT)
    )
    assert isinstance(result, UnresolvedQuote)
    assert result.reason_codes == ("PRICE_RULE_UNRESOLVED",)


def test_two_measurement_bases_for_one_service_are_refused() -> None:
    """Nobody can say whether the customer or the scale produced the total, so nobody guesses."""
    result = compose(
        standard("3", QuantityBasis.STAFF_MEASUREMENT),
        standard("4", QuantityBasis.CUSTOMER_ESTIMATE),
    )
    assert isinstance(result, UnresolvedQuote)
    assert result.reason_codes == ("MEASUREMENT_POLICY_UNRESOLVED",)


def test_same_service_lines_aggregate_before_the_tier_is_chosen() -> None:
    """Two 3 kg lines are 6 kg, and 6 kg selects the cheaper tier. Splitting must not cost more."""
    split = compose(standard("3"), standard("3"))
    single = compose(standard("6"))
    assert isinstance(split, ComposedQuote) and isinstance(single, ComposedQuote)
    assert (
        split.snapshot.data.totals.net_service_subtotal_max_vnd
        == single.snapshot.data.totals.net_service_subtotal_max_vnd
    )
    assert split.snapshot.data.lines[0].quantity == "6"


def test_no_display_total_is_presented_while_delivery_is_unresolved() -> None:
    """A number a customer would read as "what I pay" must not exist until delivery is decided.

    Still the invariant; it is now conditional on the facts rather than universal. A pickup-and-
    return job with no verified distance is the case where the fee genuinely needs a human, and the
    engine says so -- so no total is presented, exactly as before.
    """
    result = compose(standard("8"), fulfillment_mode=FulfillmentMode.PICKUP_AND_RETURN)
    assert isinstance(result, ComposedQuote)
    totals = result.snapshot.data.totals
    assert totals.delivery_fee_vnd is None
    assert totals.display_total_min_vnd is None
    assert totals.display_total_max_vnd is None
    assert "DELIVERY_FEE_UNRESOLVED" in result.snapshot.data.reason_codes
    assert "PROMOTION_NOT_EVALUATED" in result.snapshot.data.reason_codes


def test_a_walk_in_quote_carries_the_total_the_customer_actually_pays() -> None:
    """The commonest transaction in the shop, and until now it produced no total at all.

    `evaluate_delivery` returns fee 0 and ALLOW for SELF_DROP_SELF_COLLECT -- there is no delivery
    job, so there is nothing to price. The composer never called it, stamped DELIVERY_FEE_UNRESOLVED
    on every revision, and the snapshot validator then correctly refused to present a total. The
    customer could be quoted a subtotal and never a price.
    """
    result = compose(standard("8"), fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT)
    assert isinstance(result, ComposedQuote)
    totals = result.snapshot.data.totals
    assert totals.delivery_fee_vnd == 0
    assert totals.display_total_min_vnd == totals.net_service_subtotal_min_vnd
    assert totals.display_total_max_vnd == totals.net_service_subtotal_max_vnd
    assert "DELIVERY_FEE_UNRESOLVED" not in result.snapshot.data.reason_codes
    # A zero fee is not a line item: nothing is being charged for, so nothing is shown.
    assert result.snapshot.data.adjustments == ()


def test_the_owner_confirmed_zone_schedule_reaches_the_quote() -> None:
    """0đ under 2km and 10,000đ from 2 to 6km, owner-confirmed and previously unreachable.

    The amounts are the engine's; this asserts they arrive in the revision the customer is shown,
    as an exact debit adjustment that reconciles with the total.
    """
    near = compose(
        standard("8"), fulfillment_mode=FulfillmentMode.PICKUP_AND_RETURN, verified_distance_m=1_500
    )
    mid = compose(
        standard("8"), fulfillment_mode=FulfillmentMode.PICKUP_AND_RETURN, verified_distance_m=4_000
    )
    assert isinstance(near, ComposedQuote)
    assert isinstance(mid, ComposedQuote)
    assert near.snapshot.data.totals.delivery_fee_vnd == 0
    assert near.snapshot.data.adjustments == ()
    assert mid.snapshot.data.totals.delivery_fee_vnd == 10_000
    assert mid.snapshot.data.totals.display_total_min_vnd == (
        mid.snapshot.data.totals.net_service_subtotal_min_vnd + 10_000
    )
    adjustment = mid.snapshot.data.adjustments[0]
    assert adjustment.kind is QuoteAdjustmentKind.DELIVERY
    assert adjustment.amount_min_vnd == adjustment.amount_max_vnd == 10_000
    # Who set the number, not which zone produced it -- the zone is in the calculation trace.
    assert adjustment.reason_code == "AUTO_FIXED"


def test_a_negotiated_fee_over_six_kilometres_needs_the_customer_to_have_agreed() -> None:
    """DEC-003 ratified staff negotiation over 6km, and it requires the customer to accept.

    Recording a fee without the acknowledgement leaves the quote unresolved, which is the decision's
    own condition rather than an extra one invented here.
    """
    unacknowledged = compose_quote_revision(
        quote_id=uuid4(),
        revision=1,
        rules=rules(),
        requested=(standard("8"),),
        pricebook=provenance(),
        priced_at=NOW,
        fulfillment_mode=FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=9_000,
        approved_manual_fee_vnd=45_000,
        customer_acknowledged_manual_fee=False,
    )
    agreed = compose_quote_revision(
        quote_id=uuid4(),
        revision=1,
        rules=rules(),
        requested=(standard("8"),),
        pricebook=provenance(),
        priced_at=NOW,
        fulfillment_mode=FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=9_000,
        approved_manual_fee_vnd=45_000,
        customer_acknowledged_manual_fee=True,
    )
    assert isinstance(unacknowledged, ComposedQuote)
    assert unacknowledged.snapshot.data.totals.display_total_min_vnd is None
    assert "DELIVERY_FEE_UNRESOLVED" in unacknowledged.snapshot.data.reason_codes
    assert isinstance(agreed, ComposedQuote)
    assert agreed.snapshot.data.totals.delivery_fee_vnd == 45_000
    assert agreed.snapshot.data.adjustments[0].reason_code == "HUMAN_APPROVED"


def test_a_composed_revision_is_an_estimate_that_cannot_be_final() -> None:
    result = compose(standard("7"))
    assert isinstance(result, ComposedQuote)
    data = result.snapshot.data
    assert data.finality is QuoteFinality.ESTIMATE
    assert data.status is QuoteRevisionStatus.REVIEW_REQUIRED
    assert data.approval_id is None
    assert data.tax_treatment == "UNVERIFIED" and data.tax_vnd is None
    assert "TAX_TREATMENT_UNVERIFIED" in data.required_approvals


def test_the_snapshot_verifies_and_is_reproducible_from_its_own_data() -> None:
    result = compose(standard("6"), quote_id=PRICEBOOK_ID, revision=1)
    assert isinstance(result, ComposedQuote)
    assert verify_quote_snapshot(result.snapshot)
    again = compose(standard("6"), quote_id=PRICEBOOK_ID, revision=1)
    assert isinstance(again, ComposedQuote)
    # Same inputs, same clock, same pricebook: the hash is a function of the facts, not of the run.
    assert again.snapshot.document.snapshot_hash == result.snapshot.document.snapshot_hash


def test_a_service_version_identity_is_bound_to_the_pricebook_it_was_priced_against() -> None:
    first = compose(standard("6"))
    other_book = compose_quote_revision(
        quote_id=uuid4(),
        revision=1,
        rules=rules(),
        requested=(standard("6"),),
        pricebook=PricebookProvenance(uuid4(), 2, provenance().snapshot_hash),
        priced_at=NOW,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
    )
    assert isinstance(first, ComposedQuote) and isinstance(other_book, ComposedQuote)
    assert (
        first.snapshot.data.lines[0].service_version_id
        != other_book.snapshot.data.lines[0].service_version_id
    )


def test_published_rules_match_the_imported_rules_exactly() -> None:
    """Publishing must not be a lossy step, or the runtime prices against a different pricebook."""
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    payload = canonical_pricebook_payload(pricebook)
    assert published_price_rules(payload) == runtime_price_rules(pricebook)
    assert canonical_document(payload).snapshot_hash == pricebook.manifest.canonical_snapshot_hash


def test_an_edited_price_changes_the_digest_the_runtime_checks() -> None:
    """Two defences, and this asserts the one that catches an edited price.

    The round-trip check below proves the *parser* loses nothing; it cannot prove the payload is
    the one that was approved, because a changed price re-serializes to exactly the changed bytes.
    Authenticity is the digest's job: `configuration_versions.snapshot_hash` was computed when a
    human published the pricebook, and `OperationsService._published_pricebook` refuses when the
    stored payload no longer hashes to it. So the property that matters here is that an edit is
    visible in the digest at all.
    """
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    payload = canonical_pricebook_payload(pricebook)
    tampered = dict(payload)
    tampered["tiers"] = [
        {**tier, "unit_price_vnd": 1} if tier["service_code"] == STANDARD else tier
        for tier in payload["tiers"]
    ]
    assert canonical_document(tampered).snapshot_hash != canonical_document(payload).snapshot_hash
    # It still parses, which is exactly why the digest check upstream is not optional.
    assert published_price_rules(tampered) != published_price_rules(payload)


def test_an_unrecognized_field_does_not_survive_a_round_trip() -> None:
    """A payload carrying something this parser ignores must not be priced against.

    A field the parser silently drops is the dangerous shape: the rules in memory would be missing
    something the published document said. Re-serializing and comparing bytes turns that from a
    silent omission into a refusal.
    """
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    payload = canonical_pricebook_payload(pricebook)
    extended = dict(payload)
    extended["surcharges"] = [{"service_code": STANDARD, "amount_vnd": 5_000}]
    with pytest.raises(ValueError, match="lossless round trip"):
        published_price_rules(extended)


def test_dropping_a_service_from_a_published_pricebook_is_refused() -> None:
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    payload = canonical_pricebook_payload(pricebook)
    short = dict(payload)
    short["services"] = payload["services"][:-1]
    with pytest.raises(ValueError, match="counts do not match"):
        published_price_rules(short)


def test_the_engine_rejects_more_precision_than_policy_allows() -> None:
    """DEC-001 is open, so three decimal places is the engine's limit and nothing rounds past it."""
    assert Decimal("5.999") == Decimal("5.999")
    result = compose(standard("5.9994"))
    assert isinstance(result, UnresolvedQuote)
