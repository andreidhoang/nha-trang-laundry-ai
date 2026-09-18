"""The server owns the bound; the human owns the number. `RANGE-PRICE-001`.

Twenty of the forty-four published services are priced by inspection, and until this item none of
them could be quoted at all. What these tests guard is not that they can be now, but that making
them quotable did not move the authority: the interval still comes from the published pricebook,
the amount still comes from a person, and nothing anywhere invents one when a person did not.

The property test at the bottom is the load-bearing one. It walks every range service in
`templates/services-pricebook.csv` and asserts both ends of every published band are accepted and
both numbers one step outside are refused, so a future change that widens a bound by one đồng --
or reads the band off the wrong column -- fails on twenty services rather than on the one somebody
remembered to write a test for.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from nha_trang_laundry_domain.catalog import (
    ErrorCode,
    FulfillmentMode,
    PriceRuleType,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
    Unit,
)
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv, runtime_price_rules
from nha_trang_laundry_domain.pricing import PriceRule
from nha_trang_laundry_domain.quote_composition import (
    ComposedQuote,
    PricebookProvenance,
    RequestedLine,
    UnresolvedQuote,
    accept_quote_revision,
    close_range_prices,
    compose_quote_revision,
)
from nha_trang_laundry_domain.quotes import (
    ExactLineAmounts,
    RangeLineAmounts,
    verify_quote_snapshot,
)
from nha_trang_laundry_domain.range_prices import (
    RANGE_PRICE_REFUSAL_AUTHORITIES,
    PriceBand,
    RangePriceAttestation,
    RangePriceChoice,
    RangePriceRefusal,
    RangePriceRefused,
    ResolvedRangePrices,
    range_price_rendered_document,
    resolve_range_prices,
)

ROOT = Path(__file__).resolve().parents[3]
PRICEBOOK_ID = UUID("00000000-0000-0000-0000-0000000004a1")
OTHER_PRICEBOOK_ID = UUID("00000000-0000-0000-0000-0000000004a2")
NOW = datetime(2026, 9, 18, 3, 0, tzinfo=UTC)
AO_DAI = "DC_AO_DAI_TRADITIONAL"
STANDARD = "STANDARD_WASH_DRY"
APPROVAL_ID = UUID("00000000-0000-0000-0000-0000000004b1")


def rules() -> dict[str, PriceRule]:
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    return runtime_price_rules(pricebook)


def provenance(version_id: UUID = PRICEBOOK_ID, version: int = 1) -> PricebookProvenance:
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    return PricebookProvenance(version_id, version, pricebook.manifest.canonical_snapshot_hash)


def range_services() -> dict[str, PriceRule]:
    """Every service the published pricebook prices as a band, keyed by code."""

    return {
        code: rule
        for code, rule in rules().items()
        if rule.rule_type is PriceRuleType.RANGE_PER_UNIT
    }


def attestation(
    *choices: RangePriceChoice,
    quote_id: UUID,
    revision: int = 1,
    version_id: UUID = PRICEBOOK_ID,
    version: int = 1,
) -> RangePriceAttestation:
    return RangePriceAttestation(
        quote_id=quote_id,
        revision=revision,
        pricebook_version_id=version_id,
        pricebook_version=version,
        choices=choices,
        approval_id=APPROVAL_ID,
    )


def compose(
    *lines: RequestedLine,
    quote_id: UUID,
    revision: int = 1,
    range_prices: RangePriceAttestation | None = None,
    present_range_as_band: bool = False,
) -> ComposedQuote | UnresolvedQuote:
    """Compose against the walk-in case, so the delivery fee resolves to zero and never masks a
    range refusal with a delivery one."""

    return compose_quote_revision(
        quote_id=quote_id,
        revision=revision,
        rules=rules(),
        requested=lines,
        pricebook=provenance(),
        priced_at=NOW,
        fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
        range_prices=range_prices,
        present_range_as_band=present_range_as_band,
    )


def ao_dai(quantity: str = "1") -> RequestedLine:
    return RequestedLine(AO_DAI, quantity, Unit.SET, QuantityBasis.STAFF_MEASUREMENT)


# --- the refusal registry ----------------------------------------------------------------------


def test_every_range_price_refusal_names_the_authority_that_causes_it() -> None:
    """`settlement.REFUSAL_DECISIONS` sets the precedent and `test_settlement_policy.py:103` pins
    it the same way: a refusal a caller cannot trace to an invariant or a decision tells them only
    that something failed, which is not enough to act on at a counter."""

    assert set(RANGE_PRICE_REFUSAL_AUTHORITIES) == set(RangePriceRefusal)
    assert all(value.strip() for value in RANGE_PRICE_REFUSAL_AUTHORITIES.values())
    assert RangePriceRefused(RangePriceRefusal.RANGE_PRICE_OUT_OF_BAND).authority == "INVARIANT-3"


# --- band validation ---------------------------------------------------------------------------


@pytest.mark.parametrize("amount", [80_000, 150_000, 240_000])
def test_an_amount_inside_the_published_band_becomes_the_price(amount: int) -> None:
    """Áo dài truyền thống is published 80.000-240.000 ₫: every one of these is a price the shop
    charges for one."""

    quote_id = uuid4()
    result = compose(
        ao_dai(),
        quote_id=quote_id,
        range_prices=attestation(RangePriceChoice(AO_DAI, amount), quote_id=quote_id),
    )
    assert isinstance(result, ComposedQuote)
    line = result.snapshot.data.lines[0]
    assert isinstance(line.amounts, ExactLineAmounts)
    assert line.amounts.net_amount_vnd == amount
    # No unit price: the number is a judgement about one garment, not a rate times a quantity.
    assert line.amounts.unit_price_vnd is None
    totals = result.snapshot.data.totals
    assert totals.display_total_min_vnd == totals.display_total_max_vnd == amount
    assert result.snapshot.data.finality is QuoteFinality.APPROVED_EXACT
    assert result.snapshot.data.approval_id == APPROVAL_ID
    assert verify_quote_snapshot(result.snapshot)


@pytest.mark.parametrize("amount", [79_999, 240_001, 250_000, 0])
def test_an_amount_outside_the_published_band_is_refused_and_nothing_is_composed(
    amount: int,
) -> None:
    """The owner authorised the interval, not the number. Outside it there is no authority."""

    quote_id = uuid4()
    result = compose(
        ao_dai(),
        quote_id=quote_id,
        range_prices=attestation(RangePriceChoice(AO_DAI, amount), quote_id=quote_id),
    )
    assert isinstance(result, UnresolvedQuote)
    assert result.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",)


def test_an_amount_with_no_approval_envelope_is_not_a_price() -> None:
    """`RangePriceAttestation.approval_id` is nullable only so a proposal can be hashed before the
    envelope that binds the hash exists. Composing through that gap is refused."""

    quote_id = uuid4()
    unapproved = replace(
        attestation(RangePriceChoice(AO_DAI, 150_000), quote_id=quote_id), approval_id=None
    )
    result = compose(ao_dai(), quote_id=quote_id, range_prices=unapproved)
    assert isinstance(result, UnresolvedQuote)
    assert result.reason_codes == (ErrorCode.HUMAN_APPROVAL_REQUIRED.value,)

    banded = banded_revision(quote_id)
    closed = close_range_prices(priced=banded.snapshot, revision=2, attestation=unapproved)
    assert isinstance(closed, UnresolvedQuote)
    assert closed.reason_codes == (ErrorCode.HUMAN_APPROVAL_REQUIRED.value,)


def test_a_range_line_with_no_amount_still_requires_a_human() -> None:
    """The refusal this item narrowed did not disappear. It is still the truth about an open
    band, and it is what lets a console ask for a number instead of showing nothing."""

    result = compose(ao_dai(), quote_id=uuid4())
    assert isinstance(result, UnresolvedQuote)
    assert result.reason_codes == (ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value,)


def test_an_amount_for_an_exactly_priced_service_is_refused() -> None:
    """`STD_WASH_DRY_LT6` is 25.000 ₫/kg, published. There is no band for anyone to choose inside,
    and accepting a number for it would let a staff member overwrite a published price."""

    quote_id = uuid4()
    for code in (STANDARD, "STD_WASH_DRY_LT6"):
        result = compose(
            RequestedLine(STANDARD, "6", Unit.KG, QuantityBasis.STAFF_MEASUREMENT),
            quote_id=quote_id,
            range_prices=attestation(RangePriceChoice(code, 100_000), quote_id=quote_id),
        )
        assert isinstance(result, UnresolvedQuote), code
        assert result.reason_codes == ("RANGE_PRICE_NOT_APPLICABLE",), code


def test_an_amount_validated_against_another_pricebook_version_is_refused() -> None:
    """The band that authorises an amount is the one the revision was priced against. A quote
    priced on version 1 whose amount was checked against some other version was checked against an
    interval the customer never saw."""

    quote_id = uuid4()
    for version_id, version in ((OTHER_PRICEBOOK_ID, 1), (PRICEBOOK_ID, 2)):
        result = compose(
            ao_dai(),
            quote_id=quote_id,
            range_prices=attestation(
                RangePriceChoice(AO_DAI, 150_000),
                quote_id=quote_id,
                version_id=version_id,
                version=version,
            ),
        )
        assert isinstance(result, UnresolvedQuote)
        assert result.reason_codes == ("RANGE_PRICE_PRICEBOOK_MISMATCH",)


def test_the_band_scales_with_the_quantity_the_engine_priced() -> None:
    """Two áo dài is one line whose band is 160.000-480.000 ₫, because the staff member closes the
    line. 150.000 ₫ is inside the per-garment band and outside this one, and is refused."""

    quote_id = uuid4()
    inside = compose(
        ao_dai("2"),
        quote_id=quote_id,
        range_prices=attestation(RangePriceChoice(AO_DAI, 300_000), quote_id=quote_id),
    )
    outside = compose(
        ao_dai("2"),
        quote_id=quote_id,
        range_prices=attestation(RangePriceChoice(AO_DAI, 150_000), quote_id=quote_id),
    )
    assert isinstance(inside, ComposedQuote)
    assert inside.snapshot.data.totals.display_total_min_vnd == 300_000
    assert isinstance(outside, UnresolvedQuote)
    assert outside.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",)


def test_a_closed_band_cannot_rest_on_a_quantity_nobody_measured() -> None:
    """An exact price on a customer's own estimate is the rule `accept_quote_revision` already
    enforces; closing a band does not create an exception to it."""

    quote_id = uuid4()
    result = compose(
        RequestedLine(AO_DAI, "1", Unit.SET, QuantityBasis.CUSTOMER_ESTIMATE),
        quote_id=quote_id,
        range_prices=attestation(RangePriceChoice(AO_DAI, 150_000), quote_id=quote_id),
    )
    assert isinstance(result, UnresolvedQuote)
    assert result.reason_codes == ("QUOTE_QUANTITY_NOT_MEASURED",)


def test_one_open_band_keeps_the_whole_revision_unresolved() -> None:
    """Half-closing a quote would present a total that is partly a decision and partly a guess."""

    quote_id = uuid4()
    result = compose(
        ao_dai(),
        RequestedLine("DC_SUIT", "1", Unit.ITEM, QuantityBasis.STAFF_MEASUREMENT),
        quote_id=quote_id,
        range_prices=attestation(RangePriceChoice(AO_DAI, 150_000), quote_id=quote_id),
    )
    assert isinstance(result, UnresolvedQuote)
    assert result.reason_codes == (ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value,)


# --- the band as a revision --------------------------------------------------------------------


def test_a_band_can_be_presented_to_a_customer_and_stored() -> None:
    """A price read to a customer before anyone has looked at the garment is a legitimate thing to
    show, and until this item it could not be stored at all. It carries both bounds, no single
    total, and the reason code that says a person still has to close it."""

    result = compose(ao_dai(), quote_id=uuid4(), present_range_as_band=True)
    assert isinstance(result, ComposedQuote)
    data = result.snapshot.data
    assert data.finality is QuoteFinality.RANGE
    assert data.status is QuoteRevisionStatus.REVIEW_REQUIRED
    assert data.approval_id is None
    line = data.lines[0]
    assert isinstance(line.amounts, RangeLineAmounts)
    assert (line.amounts.net_amount_min_vnd, line.amounts.net_amount_max_vnd) == (80_000, 240_000)
    assert (data.totals.display_total_min_vnd, data.totals.display_total_max_vnd) == (
        80_000,
        240_000,
    )
    assert ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value in data.reason_codes
    assert verify_quote_snapshot(result.snapshot)


def test_a_band_revision_cannot_be_accepted_as_a_price() -> None:
    """A band is not one price for a customer to agree to, so there is nothing to attest."""

    banded = compose(ao_dai(), quote_id=uuid4(), present_range_as_band=True)
    assert isinstance(banded, ComposedQuote)
    refused = accept_quote_revision(priced=banded.snapshot, revision=2)
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == (ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value,)


# --- closing a stored band ---------------------------------------------------------------------


def banded_revision(quote_id: UUID) -> ComposedQuote:
    result = compose(ao_dai(), quote_id=quote_id, present_range_as_band=True)
    assert isinstance(result, ComposedQuote)
    return result


def test_closing_a_stored_band_derives_an_exact_revision_without_repricing() -> None:
    """The band an amount is checked against is the one on the revision the customer was shown, so
    a pricebook republished in between cannot move it. Everything except the money, the finality
    and the status is carried through untouched."""

    quote_id = uuid4()
    banded = banded_revision(quote_id)
    closed = close_range_prices(
        priced=banded.snapshot,
        revision=2,
        attestation=attestation(RangePriceChoice(AO_DAI, 150_000), quote_id=quote_id),
    )
    assert isinstance(closed, ComposedQuote)
    data = closed.snapshot.data
    assert data.finality is QuoteFinality.APPROVED_EXACT
    assert data.status is QuoteRevisionStatus.APPROVED
    assert data.approval_id == APPROVAL_ID
    assert data.revision == 2
    assert data.totals.display_total_min_vnd == data.totals.display_total_max_vnd == 150_000
    assert ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value not in data.reason_codes
    # Derived, not re-priced: same lines, same traces, same pricebook reference.
    assert data.calculation_traces == banded.snapshot.data.calculation_traces
    assert data.configuration_snapshots == banded.snapshot.data.configuration_snapshots
    assert verify_quote_snapshot(closed.snapshot)


def test_closing_a_stored_band_out_of_band_is_refused() -> None:
    quote_id = uuid4()
    banded = banded_revision(quote_id)
    refused = close_range_prices(
        priced=banded.snapshot,
        revision=2,
        attestation=attestation(RangePriceChoice(AO_DAI, 240_001), quote_id=quote_id),
    )
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",)


def test_amounts_chosen_for_one_revision_cannot_be_carried_to_another() -> None:
    """Invariant 8. The rendered document the approval binds names the quote and the revision, so
    an amount approved for one garment cannot be replayed onto the next customer's."""

    quote_id = uuid4()
    banded = banded_revision(quote_id)
    refused = close_range_prices(
        priced=banded.snapshot,
        revision=2,
        attestation=attestation(RangePriceChoice(AO_DAI, 150_000), quote_id=quote_id, revision=7),
    )
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == (ErrorCode.VALIDATION_ERROR.value,)


def test_closing_a_revision_that_has_no_band_is_refused() -> None:
    result = compose(
        RequestedLine(STANDARD, "6", Unit.KG, QuantityBasis.STAFF_MEASUREMENT), quote_id=uuid4()
    )
    assert isinstance(result, ComposedQuote)
    refused = close_range_prices(
        priced=result.snapshot,
        revision=2,
        attestation=attestation(
            RangePriceChoice(STANDARD, 100_000), quote_id=result.snapshot.data.quote_id
        ),
    )
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == ("RANGE_PRICE_NOT_APPLICABLE",)


def test_a_closed_revision_is_acceptable_and_reaches_accepted_final() -> None:
    """The point of the whole item: a closed band is sellable. `accept_quote_revision` used to
    refuse anything that was not `ESTIMATE`, which would have stopped the twenty range services one
    step short of an order."""

    quote_id = uuid4()
    closed = close_range_prices(
        priced=banded_revision(quote_id).snapshot,
        revision=2,
        attestation=attestation(RangePriceChoice(AO_DAI, 150_000), quote_id=quote_id),
    )
    assert isinstance(closed, ComposedQuote)
    accepted = accept_quote_revision(priced=closed.snapshot, revision=3)
    assert isinstance(accepted, ComposedQuote)
    assert accepted.snapshot.data.status is QuoteRevisionStatus.ACCEPTED_FINAL
    assert accepted.snapshot.data.finality is QuoteFinality.APPROVED_EXACT
    # The approval that authorised the amount survives beside the acceptance that agreed to it.
    assert accepted.snapshot.data.approval_id == APPROVAL_ID
    assert accepted.snapshot.data.totals.display_total_min_vnd == 150_000


def test_an_already_accepted_revision_is_still_refused() -> None:
    """Narrowing the acceptance guard must not have opened it: a revision already carried to
    `ACCEPTED_FINAL` cannot be accepted a second time."""

    quote_id = uuid4()
    closed = close_range_prices(
        priced=banded_revision(quote_id).snapshot,
        revision=2,
        attestation=attestation(RangePriceChoice(AO_DAI, 150_000), quote_id=quote_id),
    )
    assert isinstance(closed, ComposedQuote)
    accepted = accept_quote_revision(priced=closed.snapshot, revision=3)
    assert isinstance(accepted, ComposedQuote)
    refused = accept_quote_revision(priced=accepted.snapshot, revision=4)
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == ("QUOTE_ALREADY_FINAL",)


# --- what an approval binds ---------------------------------------------------------------------


def test_editing_a_line_changes_the_content_an_approval_binds() -> None:
    """Invariant 8, at the level this module owns: the digest is over the amounts *and* the revision
    they were chosen against, so changing either produces a different one. The database-side half --
    that the approval also stops matching the stored revision -- is pinned in the API tests."""

    quote_id = uuid4()
    base = attestation(RangePriceChoice(AO_DAI, 150_000), quote_id=quote_id)
    digest = range_price_rendered_document(base).snapshot_hash
    edited_amount = replace(base, choices=(RangePriceChoice(AO_DAI, 160_000),))
    edited_revision = replace(base, revision=2)
    added_line = replace(base, choices=(*base.choices, RangePriceChoice("DC_SUIT", 120_000)))
    assert digest != range_price_rendered_document(edited_amount).snapshot_hash
    assert digest != range_price_rendered_document(edited_revision).snapshot_hash
    assert digest != range_price_rendered_document(added_line).snapshot_hash


def test_the_rendered_digest_does_not_depend_on_submission_order() -> None:
    """An approval must be about what was priced, not about the order a form serialised it in."""

    quote_id = uuid4()
    first = attestation(
        RangePriceChoice(AO_DAI, 150_000), RangePriceChoice("DC_SUIT", 120_000), quote_id=quote_id
    )
    second = attestation(
        RangePriceChoice("DC_SUIT", 120_000), RangePriceChoice(AO_DAI, 150_000), quote_id=quote_id
    )
    assert (
        range_price_rendered_document(first).snapshot_hash
        == range_price_rendered_document(second).snapshot_hash
    )


def test_a_boolean_is_not_an_amount() -> None:
    """`True` is an `int` in Python, so without an explicit guard `amount_vnd=True` would be read
    as 1 ₫ and compared as a price."""

    outcome = resolve_range_prices(
        bands={AO_DAI: PriceBand(0, 240_000)},
        pricebook_version_id=PRICEBOOK_ID,
        pricebook_version=1,
        attestation=attestation(
            RangePriceChoice(AO_DAI, True),
            quote_id=uuid4(),
        ),
    )
    assert isinstance(outcome, RangePriceRefused)
    assert outcome.refusal is RangePriceRefusal.RANGE_PRICE_OUT_OF_BAND


def test_two_amounts_for_one_line_are_refused() -> None:
    """Two numbers for one interval leave no answer to which one a person meant."""

    quote_id = uuid4()
    outcome = resolve_range_prices(
        bands={AO_DAI: PriceBand(80_000, 240_000)},
        pricebook_version_id=PRICEBOOK_ID,
        pricebook_version=1,
        attestation=attestation(
            RangePriceChoice(AO_DAI, 100_000),
            RangePriceChoice(AO_DAI, 200_000),
            quote_id=quote_id,
        ),
    )
    assert isinstance(outcome, RangePriceRefused)
    assert outcome.refusal is RangePriceRefusal.RANGE_PRICE_NOT_APPLICABLE


# --- the property that holds across the whole published catalogue -------------------------------


def test_the_pricebook_still_publishes_twenty_bands() -> None:
    """If this number moves, the property test below silently covers a different catalogue."""

    assert len(range_services()) == 20


@pytest.mark.parametrize("service_code", sorted(range_services()))
def test_both_ends_of_every_published_band_are_accepted_and_neither_step_outside_is(
    service_code: str,
) -> None:
    """For all twenty: `min` and `max` are prices the shop charges, `min - 1` and `max + 1` are not.

    One quantity unit each, so the line's band is exactly the published band and the assertion is
    about the pricebook's own numbers rather than about the engine's multiplication.
    """

    rule = range_services()[service_code]
    assert rule.range_min_vnd is not None and rule.range_max_vnd is not None
    minimum, maximum = rule.range_min_vnd, rule.range_max_vnd
    assert minimum < maximum, "a band with one number in it would make this test vacuous"
    line = RequestedLine(service_code, "1", rule.unit, QuantityBasis.STAFF_MEASUREMENT)

    for amount in (minimum, maximum):
        quote_id = uuid4()
        accepted = compose(
            line,
            quote_id=quote_id,
            range_prices=attestation(RangePriceChoice(service_code, amount), quote_id=quote_id),
        )
        assert isinstance(accepted, ComposedQuote), (service_code, amount)
        assert accepted.snapshot.data.totals.display_total_min_vnd == amount

    for amount in (minimum - 1, maximum + 1):
        quote_id = uuid4()
        refused = compose(
            line,
            quote_id=quote_id,
            range_prices=attestation(RangePriceChoice(service_code, amount), quote_id=quote_id),
        )
        assert isinstance(refused, UnresolvedQuote), (service_code, amount)
        assert refused.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",), (service_code, amount)


@pytest.mark.parametrize("service_code", sorted(range_services()))
def test_every_published_band_round_trips_through_a_stored_band_revision(
    service_code: str,
) -> None:
    """The same twenty, through the path the API actually uses: present the band, then close it."""

    rule = range_services()[service_code]
    assert rule.range_min_vnd is not None and rule.range_max_vnd is not None
    quote_id = uuid4()
    banded = compose(
        RequestedLine(service_code, "1", rule.unit, QuantityBasis.STAFF_MEASUREMENT),
        quote_id=quote_id,
        present_range_as_band=True,
    )
    assert isinstance(banded, ComposedQuote)
    assert banded.snapshot.data.totals.display_total_min_vnd == rule.range_min_vnd
    assert banded.snapshot.data.totals.display_total_max_vnd == rule.range_max_vnd

    closed = close_range_prices(
        priced=banded.snapshot,
        revision=2,
        attestation=attestation(
            RangePriceChoice(service_code, rule.range_max_vnd), quote_id=quote_id
        ),
    )
    assert isinstance(closed, ComposedQuote)
    assert closed.snapshot.data.totals.display_total_min_vnd == rule.range_max_vnd

    refused = close_range_prices(
        priced=banded.snapshot,
        revision=2,
        attestation=attestation(
            RangePriceChoice(service_code, rule.range_min_vnd - 1), quote_id=quote_id
        ),
    )
    assert isinstance(refused, UnresolvedQuote)
    assert refused.reason_codes == ("RANGE_PRICE_OUT_OF_BAND",)


def test_a_resolved_set_carries_exactly_what_was_chosen() -> None:
    """No defaulting: the resolved mapping holds the submitted amounts and nothing else."""

    quote_id = uuid4()
    outcome = resolve_range_prices(
        bands={AO_DAI: PriceBand(80_000, 240_000), "DC_SUIT": PriceBand(100_000, 150_000)},
        pricebook_version_id=PRICEBOOK_ID,
        pricebook_version=1,
        attestation=attestation(RangePriceChoice(AO_DAI, 90_000), quote_id=quote_id),
    )
    assert isinstance(outcome, ResolvedRangePrices)
    assert dict(outcome.amounts) == {AO_DAI: 90_000}
