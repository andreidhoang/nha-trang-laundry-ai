"""Compose one immutable quote revision from deterministic engine output.

This module exists so that no other layer has a reason to do arithmetic on money.

`pricing.price_lines` is the authority on what a service costs, and `quotes.build_quote_snapshot` is
the authority on whether a revision is internally consistent. Between them sits a gap that somebody
has to fill: turning per-service results into lines, summing them into totals, and deciding which
reason codes the revision carries. Until now that gap was filled inside the synthetic eval harness,
which is fine for a harness and unacceptable for a command path — `TASK-quote-command-001` says the
item is not done if a reviewer can find arithmetic in the route layer, and moving it into the API
service would only move the problem one file further from the tests that guard it.

So the composition lives here, next to the engine whose output it is composing, and the route is
left as a translator: it validates its input, calls this, and persists what comes back.

Two rules shape everything below.

**Unresolved policy propagates.** Where the engine cannot resolve a price, or resolves it only to a
range a human must close, this returns `UnresolvedQuote` carrying the engine's own reason codes. It
never substitutes a default, a midpoint, or a zero. A caller that wants a number from an unresolved
quote does not get one.

**Nothing outside the engine invents a quantity.** The quantity written into a line is the
engine's own aggregate text from its calculation trace, not a value this module re-derives from the
inputs. `DEC-001` (weight precision and rounding) is open, so the safest thing this code can do with
a quantity is carry it verbatim and refuse when it cannot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID, uuid5

from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import (
    AdjustmentDirection,
    ErrorCode,
    FulfillmentMode,
    PolicyOutcome,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
    Unit,
)
from nha_trang_laundry_domain.delivery import DeliveryError, DeliveryResult, evaluate_delivery
from nha_trang_laundry_domain.pricing import (
    PriceLine,
    PriceResult,
    PriceRule,
    PricingError,
    price_lines,
)
from nha_trang_laundry_domain.quotes import (
    CalculationTraceSnapshot,
    ConfigurationSnapshotReference,
    ExactLineAmounts,
    ImmutableQuoteSnapshot,
    QuoteAdjustmentKind,
    QuoteAdjustmentSnapshot,
    QuoteLineSnapshot,
    QuoteRevisionData,
    QuoteSnapshotError,
    QuoteTotalsSnapshot,
    RangeLineAmounts,
    build_quote_snapshot,
    capture_calculation_trace,
)
from nha_trang_laundry_domain.range_prices import (
    PriceBand,
    RangePriceAttestation,
    RangePriceRefusal,
    RangePriceRefused,
    resolve_range_prices,
)

QUOTE_ENGINE_VERSION: Final = "quote-engine-v1"
QUOTE_ENGINE_HASH: Final = canonical_document({"engine": QUOTE_ENGINE_VERSION}).snapshot_hash
PRICING_COMPONENT_VERSION: Final = "pricing-v1"
QUOTE_VALIDITY: Final = timedelta(days=1)

# Every revision this module composes carries these, and each names an open decision rather than a
# transient gap. Writing them into the immutable snapshot is the point: a reader six months from now
# can see exactly which questions were unanswered when the price was computed.
#
#   TAX_TREATMENT_UNVERIFIED   the snapshot validator refuses any other tax treatment; tax policy
#                              is not published, so no revision may claim one.
#   DELIVERY_FEE_UNRESOLVED    Emitted only when `evaluate_delivery` actually returns
#                              REQUIRE_HUMAN for the fee -- an unverified distance, a one-leg job,
#                              or a >6km route whose negotiated fee is not yet recorded and
#                              acknowledged. It used to be stamped on every quote, which was false
#                              for the commonest case in the shop and is why no quote ever carried
#                              a total. `DEC-003` resolved 2026-08-18 and the <=6km schedule is
#                              owner-confirmed, so the fee is decided; only the wiring was missing.
#   PROMOTION_NOT_EVALUATED    No promotion is evaluated here, so the discount is zero because
#                              nothing was assessed rather than because nothing applies. `DEC-002`
#                              resolved 2026-08-18; evaluating promotions is unbuilt work, not an
#                              open decision, and this code says so until that work lands.
BASE_REASON_CODES: Final = (
    "TAX_TREATMENT_UNVERIFIED",
    "PROMOTION_NOT_EVALUATED",
)
DELIVERY_FEE_UNRESOLVED: Final = "DELIVERY_FEE_UNRESOLVED"
DELIVERY_COMPONENT_VERSION: Final = "delivery-v1"
# Empty since `DEC-022` (2026-08-25). The owner decided that tax treatment is settled by the
# accountant after an order completes rather than being a condition of accepting one, so
# TAX_TREATMENT_UNVERIFIED leaves this gate and stays in `BASE_REASON_CODES`. The distinction is the
# decision's whole content: a reason code is a fact recorded on the immutable revision for whoever
# reads it later; `required_approvals` is a gate `quotes.py:500` enforces against APPROVED_EXACT.
# The flag is still true and still stamped on every revision. It is no longer a precondition of
# taking the customer's laundry.
BASE_REQUIRED_APPROVALS: Final[tuple[str, ...]] = ()

# A service version identity has to be stable: the same pricebook version and the same service must
# produce the same UUID on every host and every replay, or two revisions of one quote would appear
# to reference different services. Deriving it from the pricebook version this way also makes the
# reference falsifiable — it cannot silently point at a pricebook the quote was not priced against.
SERVICE_VERSION_NAMESPACE: Final = UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


@dataclass(frozen=True, slots=True)
class RequestedLine:
    """One line as the caller asked for it. `quantity` is carried verbatim, never normalized."""

    service_code: str
    quantity: str
    unit: Unit
    quantity_basis: QuantityBasis


@dataclass(frozen=True, slots=True)
class PricebookProvenance:
    """Identity of the published pricebook a quote was priced against.

    This is not decoration. `build_quote_snapshot` requires exactly one PRICEBOOK reference, and it
    is what makes a stored revision reproducible: without it, a recomputation years later has no way
    to know which price list produced the number.
    """

    version_id: UUID
    version: int
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
class ComposedQuote:
    """A revision that priced cleanly and is ready to persist."""

    snapshot: ImmutableQuoteSnapshot


@dataclass(frozen=True, slots=True)
class UnresolvedQuote:
    """A revision that must not be persisted, and the engine's reasons for it.

    `reason_codes` are canonical `ErrorCode` values, passed through untouched. The caller's job is
    to show them, not to interpret them into a price.
    """

    reason_codes: tuple[str, ...]

    @property
    def requires_human(self) -> bool:
        return True


QuoteComposition = ComposedQuote | UnresolvedQuote


def compose_quote_revision(
    *,
    quote_id: UUID,
    revision: int,
    rules: dict[str, PriceRule],
    requested: tuple[RequestedLine, ...],
    pricebook: PricebookProvenance,
    priced_at: datetime,
    fulfillment_mode: FulfillmentMode,
    verified_distance_m: int | None = None,
    planned_transport_weight_kg: str | None = None,
    approved_manual_fee_vnd: int | None = None,
    customer_acknowledged_manual_fee: bool = False,
    range_prices: RangePriceAttestation | None = None,
    present_range_as_band: bool = False,
) -> QuoteComposition:
    """Price the requested lines and assemble one immutable revision, or refuse with reasons.

    `fulfillment_mode` has no default on purpose. Whether the shop is carrying this laundry decides
    whether a delivery fee exists at all, and guessing it would be this code deciding a fact about
    the customer's order. A caller that does not know must ask rather than assume.

    A range-priced line has three possible fates and the caller chooses between them by what it
    passes, never by what this function guesses (`RANGE-PRICE-001`):

    * nothing -- the default, and what every caller did before this item existed. The revision
      cannot be exact, so it is refused with `RANGE_PRICE_REQUIRES_HUMAN` and nothing is composed.
    * `present_range_as_band=True` -- the band itself becomes the revision. Finality is
      `QuoteFinality.RANGE`, the line carries `RangeLineAmounts`, the totals carry a minimum and a
      maximum that differ, and the revision still carries `RANGE_PRICE_REQUIRES_HUMAN` because that
      is the truth about it. This is the price a customer is read before anyone has looked at the
      garment, and until this item it could not be stored at all.
    * `range_prices=<attestation>` -- a named staff member chose an exact amount inside the
      published band and an owner approved it. Every range line must carry one; a revision with
      some closed and some open is refused rather than half-priced.

    `present_range_as_band` is ignored when `range_prices` is given, for that last reason.
    """
    if not requested:
        return UnresolvedQuote((ErrorCode.MISSING_REQUIRED_FACT.value,))
    try:
        results = price_lines(rules, tuple(_engine_line(line) for line in requested))
    except PricingError as error:
        # The engine's own code, verbatim. Translating it here would lose the only signal the
        # caller has about *why* no price exists.
        return UnresolvedQuote((error.code.value,))

    chosen: Mapping[str, int] = {}
    if range_prices is not None:
        # The bands come from the engine's own results for *this* pricebook, so the interval an
        # amount is checked against is the one the published version drew. Nothing a caller sends
        # reaches this check: a client-supplied band would let the submitter authorise their own
        # number, which is the whole thing the published band exists to prevent.
        outcome = resolve_range_prices(
            bands={
                service_code: band
                for service_code, result in results.items()
                if (band := _published_band(result)) is not None
            },
            pricebook_version_id=pricebook.version_id,
            pricebook_version=pricebook.version,
            attestation=range_prices,
        )
        if isinstance(outcome, RangePriceRefused):
            return UnresolvedQuote((outcome.reason_code,))
        if outcome.amounts and range_prices.approval_id is None:
            # An in-band amount is a fact a person supplied; the envelope is what makes it this
            # shop's price. `RangePriceAttestation.approval_id` is nullable only so a proposal can
            # be hashed before the approval that binds the hash exists, and this is the wall that
            # keeps the gap from being walked through.
            return UnresolvedQuote((ErrorCode.HUMAN_APPROVAL_REQUIRED.value,))
        chosen = outcome.amounts

    unresolved = _unresolved_reasons(
        results, chosen, present_as_band=present_range_as_band and range_prices is None
    )
    if unresolved:
        return UnresolvedQuote(unresolved)

    lines: list[QuoteLineSnapshot] = []
    traces: list[CalculationTraceSnapshot] = []
    net_minimums: list[int] = []
    net_maximums: list[int] = []
    banded = False
    for index, service_code in enumerate(sorted(results), start=1):
        result = results[service_code]
        basis = _single_basis(result)
        if basis is None:
            # One service priced from two different measurement bases has no single answer to
            # "who said this weight". Refusing is the only honest option while DEC-001 is open.
            return UnresolvedQuote((ErrorCode.MEASUREMENT_POLICY_UNRESOLVED.value,))
        band = _published_band(result)
        if band is not None and service_code not in chosen:
            unit_minimum = result.trace.range_min_unit_vnd
            unit_maximum = result.trace.range_max_unit_vnd
            if unit_minimum is None or unit_maximum is None:
                # A range rule with no per-unit band is a pricebook this engine cannot
                # explain, and an unexplainable band must not become a stored price.
                return UnresolvedQuote((ErrorCode.PRICE_RULE_UNRESOLVED.value,))
            banded = True
            amounts: ExactLineAmounts | RangeLineAmounts = RangeLineAmounts(
                kind="RANGE",
                unit_price_min_vnd=unit_minimum,
                unit_price_max_vnd=unit_maximum,
                list_amount_min_vnd=band.minimum_vnd,
                list_amount_max_vnd=band.maximum_vnd,
                # Zero for the same reason the exact branch below is zero: no promotion was
                # evaluated (DEC-002), so there is nothing to discount either end of the band by.
                discount_min_vnd=0,
                discount_max_vnd=0,
                net_amount_min_vnd=band.minimum_vnd,
                net_amount_max_vnd=band.maximum_vnd,
            )
            minimum, maximum = band.minimum_vnd, band.maximum_vnd
        else:
            # `chosen` first: for a range line the engine returns no `list_amount_vnd` at all, and
            # the staff member's in-band amount is the only number there is.
            amount = chosen.get(service_code, result.list_amount_vnd)
            if amount is None:
                return UnresolvedQuote((ErrorCode.PRICE_RULE_UNRESOLVED.value,))
            amounts = ExactLineAmounts(
                kind="EXACT",
                # `None` for a closed range line, because there is no unit price: the amount is a
                # judgement about one garment, not a rate multiplied by a quantity. The band the
                # judgement was made inside stays visible in the calculation trace below.
                unit_price_vnd=result.trace.unit_price_vnd,
                list_amount_vnd=amount,
                # No promotion is evaluated here (DEC-002), so there is no discount to apply.
                # Zero because nothing was assessed -- PROMOTION_NOT_EVALUATED says so.
                discount_amount_vnd=0,
                net_amount_vnd=amount,
            )
            minimum = maximum = amount
        trace = capture_calculation_trace(
            f"PRICING_{service_code}", PRICING_COMPONENT_VERSION, result.trace
        )
        traces.append(trace)
        net_minimums.append(minimum)
        net_maximums.append(maximum)
        lines.append(
            QuoteLineSnapshot(
                line_id=f"line-{index}",
                service_code=service_code,
                service_version_id=_service_version_id(pricebook, service_code),
                quantity_basis=basis,
                # The engine's aggregated quantity, not a re-derivation. If two requested lines
                # named the same service, this is the total the engine actually priced.
                quantity=result.trace.aggregate_quantity,
                unit=result.trace.unit,
                amounts=amounts,
                price_trace_hash=trace.trace.snapshot_hash,
            )
        )

    # The one place a subtotal is computed. `build_quote_snapshot` recomputes the same sum from the
    # line snapshots and refuses the revision if the two disagree, so this arithmetic is checked by
    # an independent implementation before anything is persisted.
    try:
        delivery = evaluate_delivery(
            fulfillment_mode,
            verified_distance_m=verified_distance_m,
            planned_transport_weight_kg=planned_transport_weight_kg,
            approved_manual_fee_vnd=approved_manual_fee_vnd,
            customer_acknowledged_manual_fee=customer_acknowledged_manual_fee,
        )
    except DeliveryError as error:
        # The engine's own code, verbatim, exactly as the pricing branch above does.
        return UnresolvedQuote((error.code.value,))
    fee, delivery_adjustments, delivery_reasons = _delivery_outcome(delivery)
    traces.append(
        capture_calculation_trace("DELIVERY", DELIVERY_COMPONENT_VERSION, _delivery_trace(delivery))
    )

    if chosen:
        # An approved in-band amount makes this revision APPROVED_EXACT, and `_validate_finality`
        # asks two further things of that finality. Checking them here rather than letting the
        # snapshot validator refuse the assembly means the caller is told which fact is missing --
        # the person doing this has a customer standing in front of them, the same reason
        # `accept_quote_revision` names its refusals individually.
        refusal = _exactness_refusal(lines, fee)
        if refusal is not None:
            return UnresolvedQuote((refusal,))

    minimum_subtotal = sum(net_minimums)
    maximum_subtotal = sum(net_maximums)
    totals = QuoteTotalsSnapshot(
        list_service_subtotal_min_vnd=minimum_subtotal,
        list_service_subtotal_max_vnd=maximum_subtotal,
        discount_amount_min_vnd=0,
        discount_amount_max_vnd=0,
        net_service_subtotal_min_vnd=minimum_subtotal,
        net_service_subtotal_max_vnd=maximum_subtotal,
        # An unresolved fee still means no display total: the snapshot validator enforces the
        # pairing, and a quote whose fee needs a human must not present a number that looks like
        # the amount a customer will pay. A resolved fee -- including the zero the engine returns
        # for self-drop/self-collect -- produces the total the customer is actually quoted.
        delivery_fee_vnd=fee,
        approved_surcharge_vnd=0,
        display_total_min_vnd=None if fee is None else minimum_subtotal + fee,
        display_total_max_vnd=None if fee is None else maximum_subtotal + fee,
    )
    finality, status = _finality(banded=banded, closed=bool(chosen))
    try:
        snapshot = build_quote_snapshot(
            QuoteRevisionData(
                schema_version=1,
                quote_id=quote_id,
                revision=revision,
                finality=finality,
                status=status,
                priced_at=priced_at,
                valid_until=priced_at + QUOTE_VALIDITY,
                currency="VND",
                configuration_snapshots=(
                    ConfigurationSnapshotReference(
                        config_type="PRICEBOOK",
                        version_id=pricebook.version_id,
                        version=pricebook.version,
                        snapshot_hash=pricebook.snapshot_hash,
                    ),
                ),
                lines=tuple(lines),
                adjustments=delivery_adjustments,
                totals=totals,
                calculation_traces=tuple(traces),
                calculation_engine_version=QUOTE_ENGINE_VERSION,
                calculation_engine_hash=QUOTE_ENGINE_HASH,
                promotion_eligibility_event=None,
                promotion_eligibility_at=None,
                reason_codes=BASE_REASON_CODES
                + delivery_reasons
                # Carried on the revision, not only returned beside it: a band nobody has closed is
                # a fact about the stored revision, and it is what lets a console say "can nhan
                # vien chot gia trong khoang" instead of showing a total that does not exist.
                + ((ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value,) if banded else ()),
                required_approvals=BASE_REQUIRED_APPROVALS,
                approval_id=None
                if range_prices is None or not chosen
                else range_prices.approval_id,
            )
        )
    except QuoteSnapshotError:
        # The validator refused the assembled revision. That is a defect in this composition, not a
        # customer-visible policy outcome, so it must not be reported as a priced quote -- but it
        # also must not become a 500 that hides which input triggered it.
        return UnresolvedQuote((ErrorCode.VALIDATION_ERROR.value,))
    return ComposedQuote(snapshot)


def _engine_line(line: RequestedLine) -> PriceLine:
    return PriceLine(
        service_code=line.service_code,
        quantity=line.quantity,
        unit=line.unit,
        quantity_basis=line.quantity_basis,
    )


ACCEPTANCE_REFUSALS: Final = {
    "QUOTE_QUANTITY_NOT_MEASURED": (
        "an exact price may not rest on a quantity the customer estimated"
    ),
    "QUOTE_DELIVERY_FEE_UNRESOLVED": "the delivery fee is not resolved, so there is no total",
    "QUOTE_PROMOTION_NOT_EVALUATED": "a promotion was priced against but never evaluated",
    "QUOTE_ALREADY_FINAL": "this quote has already been accepted",
}

#: Statuses from which no acceptance can be derived, because the revision's life is already over.
#: `SUPERSEDED`, `EXPIRED` and `REJECTED` are here although no path produces them today: a revision
#: in any of them is one the shop has finished with, and an attestation against it would record a
#: customer agreeing to a price that had been withdrawn.
_UNACCEPTABLE_STATUSES: Final = frozenset(
    {
        QuoteRevisionStatus.ACCEPTED_FINAL,
        QuoteRevisionStatus.SUPERSEDED,
        QuoteRevisionStatus.EXPIRED,
        QuoteRevisionStatus.REJECTED,
    }
)


def accept_quote_revision(
    *,
    priced: ImmutableQuoteSnapshot,
    revision: int,
) -> ComposedQuote | UnresolvedQuote:
    """Derive the accepted revision from the priced one. `DEC-021`, resolved 2026-08-25.

    **This never re-prices.** It takes the stored revision the customer was actually read -- the
    same lines, totals, traces and pricebook reference -- and changes only what acceptance
    changes:
    finality, status, and the approval envelope behind it. Re-running the pricing engine here would
    mean a pricebook republished between reading the price aloud and the customer saying yes could
    silently bind them to a different number than they heard, and they would have agreed to a price
    this system then did not honour.

    A revision cannot be promoted in place -- `quote_revisions` is immutable by trigger -- so
    acceptance is a new revision born accepted, which is also why the record shows both what was
    quoted and what was agreed rather than overwriting one with the other.

    The attestation is the authority, not this function and not the snapshot. It is written to
    `quote_acceptances` in the same transaction, naming the staff member, the revision and its
    digest, and `OrderRepository.create` reads it. `approval_id` stays `None` for a revision that
    had none: an approval envelope is two parties, and what the owner ratified is one. A revision
    that *does* carry one -- a closed price band, `RANGE-PRICE-001` -- keeps it, because that
    envelope records a different fact (the owner authorised this amount) than the attestation does
    (the customer agreed to it), and both belong on the record.
    """

    data = priced.data
    # Narrowed by `RANGE-PRICE-001`. This read `finality is not ESTIMATE`, using finality as a
    # proxy for "already accepted", which was exact while `compose_quote_revision` could only
    # produce `ESTIMATE`. It can now produce `APPROVED_EXACT`/`APPROVED` for a band an owner closed,
    # and that revision has not been accepted by anyone -- refusing it would make the twenty
    # range-priced services sellable right up to the point of selling them. The condition is now
    # the fact it always meant: a revision already carried to a terminal status cannot be accepted,
    # and neither can a band, because a band is not one price to agree to.
    if data.status in _UNACCEPTABLE_STATUSES:
        return UnresolvedQuote(("QUOTE_ALREADY_FINAL",))
    if data.finality is QuoteFinality.RANGE:
        return UnresolvedQuote((ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value,))
    if any(line.quantity_basis is QuantityBasis.CUSTOMER_ESTIMATE for line in data.lines):
        # `quotes.py` refuses this too. Checked here so the operator is told which fact is missing
        # rather than being handed one message for every possible refusal, because the person
        # reading it has a customer standing in front of them.
        return UnresolvedQuote(("QUOTE_QUANTITY_NOT_MEASURED",))
    if data.totals.delivery_fee_vnd is None:
        return UnresolvedQuote(("QUOTE_DELIVERY_FEE_UNRESOLVED",))
    if (
        any(item.config_type == "PROMOTION" for item in data.configuration_snapshots)
        and data.promotion_eligibility_event is None
    ):
        return UnresolvedQuote(("QUOTE_PROMOTION_NOT_EVALUATED",))
    try:
        snapshot = build_quote_snapshot(
            replace(
                data,
                revision=revision,
                finality=QuoteFinality.APPROVED_EXACT,
                status=QuoteRevisionStatus.ACCEPTED_FINAL,
                required_approvals=(),
            )
        )
    except QuoteSnapshotError:
        # The validator refused an assembly the checks above did not anticipate. Refusing with a
        # generic code is correct: presenting it as an accepted price would be worse than refusing.
        return UnresolvedQuote((ErrorCode.VALIDATION_ERROR.value,))
    return ComposedQuote(snapshot)


def stored_price_bands(priced: ImmutableQuoteSnapshot) -> dict[str, PriceBand] | None:
    """The published band each open line of a stored revision carries.

    `None` when the stored revision is not a shape this can read; see the refusals below.

    This is the read a console needs in order to draw the bound it is asking a staff member to
    choose inside, and the read `close_range_prices` checks an amount against. Both take it from the
    stored revision rather than from the live pricebook: the band that authorises an amount is the
    one the customer was shown, and a republication between the two must not move it.

    `None` rather than an exception or an empty dict, because the two unreadable shapes below are
    not producible by any path in this system and a caller that met one must refuse rather than
    proceed with a partial answer.
    """

    bands: dict[str, PriceBand] = {}
    for line in priced.data.lines:
        if not isinstance(line.amounts, RangeLineAmounts):
            continue
        if line.service_code in bands or line.amounts.discount_min_vnd:
            # Two banded lines for one service would give one amount two intervals to satisfy, and
            # a discounted band would make "the amount" ambiguous between list and net. Neither is
            # producible today -- the composer emits one line per service and no promotion is
            # evaluated (DEC-002) -- so meeting one means the stored revision is not what this
            # function can safely read, and guessing which number was meant is how money moves by
            # accident.
            return None
        if line.amounts.discount_max_vnd:
            return None
        bands[line.service_code] = PriceBand(
            line.amounts.net_amount_min_vnd, line.amounts.net_amount_max_vnd
        )
    return bands


def close_range_prices(
    *,
    priced: ImmutableQuoteSnapshot,
    revision: int,
    attestation: RangePriceAttestation,
) -> ComposedQuote | UnresolvedQuote:
    """Derive an exact revision from a stored band revision. `RANGE-PRICE-001`.

    **This never re-prices, for the same reason `accept_quote_revision` never re-prices.** The band
    an amount is checked against is the one stored on the revision the customer was shown, which was
    drawn by the pricebook version that revision names. Re-running the engine here would check the
    amount against whatever pricebook is published now, so a republication between showing a band
    and closing it could accept an amount the customer's own band never contained -- or refuse one
    it did.

    A revision cannot be promoted in place (`quote_revisions` is immutable by trigger), so closing a
    band produces a new revision. The band revision survives beside it, which is what makes the
    record show both what the customer was offered and what was chosen inside it.

    The approval is not re-checked here. Whether `attestation.approval_id` names an envelope that is
    approved, unexpired, and bound to this revision's digest is a question about stored state, and
    this module has no database: `OperationsService.apply_range_prices` answers it before calling,
    and `quote_revisions.approval_id` is a foreign key (migration `0029`) so an invented one cannot
    be persisted either.
    """

    data = priced.data
    if data.finality is not QuoteFinality.RANGE:
        # Nothing here is a band, so there is nothing for an amount to be inside. Same refusal as
        # an amount for an exactly-priced service, because it is the same mistake one level up.
        return UnresolvedQuote((RangePriceRefusal.RANGE_PRICE_NOT_APPLICABLE.value,))
    if (data.quote_id, data.revision) != (attestation.quote_id, attestation.revision):
        # Invariant 8. The amounts were chosen against one revision of one quote and may not be
        # carried to another; the rendered document the approval binds says which.
        return UnresolvedQuote((ErrorCode.VALIDATION_ERROR.value,))
    pricebook = next(
        (item for item in data.configuration_snapshots if item.config_type == "PRICEBOOK"), None
    )
    if pricebook is None:
        return UnresolvedQuote((ErrorCode.VALIDATION_ERROR.value,))

    if attestation.approval_id is None:
        return UnresolvedQuote((ErrorCode.HUMAN_APPROVAL_REQUIRED.value,))
    bands = stored_price_bands(priced)
    if bands is None:
        return UnresolvedQuote((ErrorCode.VALIDATION_ERROR.value,))

    outcome = resolve_range_prices(
        bands=bands,
        pricebook_version_id=pricebook.version_id,
        pricebook_version=pricebook.version,
        attestation=attestation,
    )
    if isinstance(outcome, RangePriceRefused):
        return UnresolvedQuote((outcome.reason_code,))
    if set(outcome.amounts) != set(bands):
        # A band left open keeps the revision a band. Half-closing it would present a total that
        # is partly a decision and partly a guess.
        return UnresolvedQuote((ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value,))

    closed_lines = tuple(
        line
        if not isinstance(line.amounts, RangeLineAmounts)
        else replace(
            line,
            amounts=ExactLineAmounts(
                kind="EXACT",
                # No unit price: the amount is a judgement about the garment in front of the staff
                # member, not a rate. The band it was chosen inside is still on the band revision.
                unit_price_vnd=None,
                list_amount_vnd=outcome.amounts[line.service_code],
                discount_amount_vnd=0,
                net_amount_vnd=outcome.amounts[line.service_code],
            ),
        )
        for line in data.lines
    )
    refusal = _exactness_refusal(list(closed_lines), data.totals.delivery_fee_vnd)
    if refusal is not None:
        return UnresolvedQuote((refusal,))

    subtotal = sum(_line_net(line) for line in closed_lines)
    fee = data.totals.delivery_fee_vnd
    assert fee is not None  # `_exactness_refusal` returned above when it was not resolved.
    totals = replace(
        data.totals,
        list_service_subtotal_min_vnd=subtotal,
        list_service_subtotal_max_vnd=subtotal,
        net_service_subtotal_min_vnd=subtotal,
        net_service_subtotal_max_vnd=subtotal,
        display_total_min_vnd=subtotal + fee + data.totals.approved_surcharge_vnd,
        display_total_max_vnd=subtotal + fee + data.totals.approved_surcharge_vnd,
    )
    try:
        snapshot = build_quote_snapshot(
            replace(
                data,
                revision=revision,
                finality=QuoteFinality.APPROVED_EXACT,
                status=QuoteRevisionStatus.APPROVED,
                lines=closed_lines,
                totals=totals,
                # The band is closed, so the reason that said it was not no longer applies. Every
                # other reason the revision carried is untouched: they are facts about how it was
                # priced and closing a band did not change any of them.
                reason_codes=tuple(
                    code
                    for code in data.reason_codes
                    if code != ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value
                ),
                required_approvals=(),
                approval_id=attestation.approval_id,
            )
        )
    except QuoteSnapshotError:
        return UnresolvedQuote((ErrorCode.VALIDATION_ERROR.value,))
    return ComposedQuote(snapshot)


def _line_net(line: QuoteLineSnapshot) -> int:
    """The one number a closed line contributes. Every line is exact by the time this is called."""

    if isinstance(line.amounts, ExactLineAmounts):
        return line.amounts.net_amount_vnd
    raise QuoteSnapshotError("a closed revision cannot contain a band")


def _delivery_outcome(
    delivery: DeliveryResult,
) -> tuple[int | None, tuple[QuoteAdjustmentSnapshot, ...], tuple[str, ...]]:
    """Project the engine's delivery result onto the three things a revision needs.

    The fee is taken only when the engine allows it. `REQUIRE_HUMAN` keeps the fee `None`, which
    keeps the display total `None`, which is the whole reason the pairing exists: a quote nobody has
    priced the transport for must not show a number that reads like a final amount.

    A zero fee produces no adjustment row. `_validate_adjustments` compares the summed debits with
    `totals.delivery_fee_vnd or 0`, so zero and absent agree, and a zero-amount adjustment would be
    a line item for something the customer is not being charged for.
    """

    reasons = tuple(str(code) for code in delivery.reason_codes)
    if delivery.fee_outcome is not PolicyOutcome.ALLOW or delivery.delivery_fee_vnd is None:
        return None, (), (DELIVERY_FEE_UNRESOLVED, *reasons)
    fee = delivery.delivery_fee_vnd
    if fee == 0:
        return fee, (), reasons
    return (
        fee,
        (
            QuoteAdjustmentSnapshot(
                adjustment_id="delivery",
                kind=QuoteAdjustmentKind.DELIVERY,
                direction=AdjustmentDirection.DEBIT,
                amount_min_vnd=fee,
                amount_max_vnd=fee,
                # `fee_resolution`, not `fee_rule`: the rule name can begin with a digit
                # (`2000_LT_DISTANCE_M_LE_6000`) and `CODE_PATTERN` requires a leading letter, and
                # the more useful thing on a money line is who set the number -- `AUTO_FIXED` for
                # the zone table, `HUMAN_APPROVED` for a fee a person negotiated. The rule itself
                # is in the calculation trace, where it can carry any shape.
                reason_code=str(delivery.fee_resolution),
            ),
        ),
        reasons,
    )


def _delivery_trace(delivery: DeliveryResult) -> dict[str, object]:
    """The engine's own trace, canonicalised, so a reader can see which rule produced the fee.

    Recorded for the same reason the pricing trace is: six months from now the question is not what
    the fee was but which zone and rule decided it, and an immutable revision that carries the
    amount without the rule cannot answer that.
    """

    trace = delivery.trace
    return {
        "fulfillment_mode": str(trace.fulfillment_mode),
        "verified_distance_m": trace.verified_distance_m,
        "distance_zone": trace.distance_zone,
        "fee_rule": trace.fee_rule,
        "fee_resolution": str(delivery.fee_resolution),
        "fee_outcome": str(delivery.fee_outcome),
        "delivery_fee_vnd": delivery.delivery_fee_vnd,
        "manual_fee_candidate_vnd": trace.manual_fee_candidate_vnd,
        "customer_acknowledged_manual_fee": trace.customer_acknowledged_manual_fee,
        "delivery_job_required": trace.delivery_job_required,
        "dispatch_authorized": trace.dispatch_authorized,
    }


def _published_band(result: PriceResult) -> PriceBand | None:
    """The interval the published pricebook drew for this line, or `None` if it drew a price.

    Derived from the engine's own result rather than read from the rule, so it is the band *after*
    the engine applied the quantity: two ao dai at 80.000-240.000 each is one line whose band is
    160.000-480.000. The staff member closes the line, so the bound has to be the line's.
    """

    if (
        result.finality is not QuoteFinality.RANGE
        or result.list_amount_vnd is not None
        or result.range_min_vnd is None
        or result.range_max_vnd is None
    ):
        return None
    return PriceBand(result.range_min_vnd, result.range_max_vnd)


def _unresolved_reasons(
    results: dict[str, PriceResult], chosen: Mapping[str, int], *, present_as_band: bool
) -> tuple[str, ...]:
    """Collect every reason the set of results cannot become the revision the caller asked for.

    A range price is the important case. `RANGE_PER_UNIT` services are priced between a minimum and
    a maximum precisely because a human has to look at the garment, and the pricebook marks them
    `RANGE_ONLY_HUMAN_FINAL`.

    Until `RANGE-PRICE-001` this function refused all of them unconditionally, and the docstring
    said why: composing one "would mean deciding how a range interacts with totals, orders and
    settlement," which was not that item's scope. It is this item's, so the refusal narrowed rather
    than disappeared. A range line is now unresolved only when nobody has closed it *and* the caller
    did not ask for the band itself -- which is still the commonest case and still the truth about
    it. What has not changed at all is that this function never resolves one: it has no way to
    produce a number and is not given one.
    """
    reasons: set[str] = set()
    for service_code, result in results.items():
        if _published_band(result) is not None:
            if service_code not in chosen and not present_as_band:
                reasons.add(ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value)
            continue
        if result.requires_human or result.finality is not QuoteFinality.ESTIMATE:
            reasons.add(ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value)
        elif result.list_amount_vnd is None:
            reasons.add(ErrorCode.PRICE_RULE_UNRESOLVED.value)
    return tuple(sorted(reasons))


def _finality(*, banded: bool, closed: bool) -> tuple[QuoteFinality, QuoteRevisionStatus]:
    """What a revision with these lines is, and what state that leaves it in.

    Three cases, and the snapshot validator enforces the first two from the other direction:
    `_validate_finality` requires `RANGE` exactly when a line carries `RangeLineAmounts`, and
    refuses `APPROVED_EXACT` in any status a person could still be editing.

    `APPROVED` rather than `ACCEPTED_FINAL` for a closed band: the owner approved the amount, which
    is not the same event as the customer agreeing to it. That second event is `DEC-021`'s
    attestation and it is recorded by `accept_quote_revision`, which derives a further revision.
    """

    if banded:
        return QuoteFinality.RANGE, QuoteRevisionStatus.REVIEW_REQUIRED
    if closed:
        return QuoteFinality.APPROVED_EXACT, QuoteRevisionStatus.APPROVED
    return QuoteFinality.ESTIMATE, QuoteRevisionStatus.REVIEW_REQUIRED


def _exactness_refusal(lines: list[QuoteLineSnapshot], fee: int | None) -> str | None:
    """The two facts `_validate_finality` demands of `APPROVED_EXACT`, named individually."""

    if any(line.quantity_basis is QuantityBasis.CUSTOMER_ESTIMATE for line in lines):
        # An exact price may not rest on a quantity the customer estimated. Same rule and same code
        # as the acceptance path, because it is the same rule.
        return "QUOTE_QUANTITY_NOT_MEASURED"
    if fee is None:
        return "QUOTE_DELIVERY_FEE_UNRESOLVED"
    return None


def _single_basis(result: PriceResult) -> QuantityBasis | None:
    bases = set(result.trace.quantity_bases)
    if len(bases) != 1:
        return None
    return bases.pop()


def _service_version_id(pricebook: PricebookProvenance, service_code: str) -> UUID:
    return uuid5(uuid5(SERVICE_VERSION_NAMESPACE, str(pricebook.version_id)), service_code)


__all__ = [
    "BASE_REASON_CODES",
    "BASE_REQUIRED_APPROVALS",
    "QUOTE_ENGINE_HASH",
    "QUOTE_ENGINE_VERSION",
    "ComposedQuote",
    "PricebookProvenance",
    "QuoteComposition",
    "RequestedLine",
    "UnresolvedQuote",
    "accept_quote_revision",
    "close_range_prices",
    "compose_quote_revision",
    "stored_price_bands",
]
