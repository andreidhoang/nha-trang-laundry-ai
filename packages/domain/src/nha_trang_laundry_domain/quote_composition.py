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

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID, uuid5

from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import (
    ErrorCode,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
    Unit,
)
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
    QuoteLineSnapshot,
    QuoteRevisionData,
    QuoteSnapshotError,
    QuoteTotalsSnapshot,
    build_quote_snapshot,
    capture_calculation_trace,
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
#   DELIVERY_FEE_UNRESOLVED    DEC-003 (delivery beyond 6 km) is open. A quote composed here has no
#                              delivery fee, and therefore no display total — see below.
#   PROMOTION_NOT_EVALUATED    DEC-002 (promotion eligibility event) is open. No promotion is
#                              applied, so the discount is zero because none was evaluated, not
#                              because none applies.
BASE_REASON_CODES: Final = (
    "TAX_TREATMENT_UNVERIFIED",
    "DELIVERY_FEE_UNRESOLVED",
    "PROMOTION_NOT_EVALUATED",
)
BASE_REQUIRED_APPROVALS: Final = ("TAX_TREATMENT_UNVERIFIED",)

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
) -> QuoteComposition:
    """Price the requested lines and assemble one immutable revision, or refuse with reasons."""
    if not requested:
        return UnresolvedQuote((ErrorCode.MISSING_REQUIRED_FACT.value,))
    try:
        results = price_lines(rules, tuple(_engine_line(line) for line in requested))
    except PricingError as error:
        # The engine's own code, verbatim. Translating it here would lose the only signal the
        # caller has about *why* no price exists.
        return UnresolvedQuote((error.code.value,))

    unresolved = _unresolved_reasons(results)
    if unresolved:
        return UnresolvedQuote(unresolved)

    lines: list[QuoteLineSnapshot] = []
    traces: list[CalculationTraceSnapshot] = []
    net_amounts: list[int] = []
    for index, service_code in enumerate(sorted(results), start=1):
        result = results[service_code]
        basis = _single_basis(result)
        if basis is None:
            # One service priced from two different measurement bases has no single answer to
            # "who said this weight". Refusing is the only honest option while DEC-001 is open.
            return UnresolvedQuote((ErrorCode.MEASUREMENT_POLICY_UNRESOLVED.value,))
        amount = result.list_amount_vnd
        if amount is None:
            return UnresolvedQuote((ErrorCode.PRICE_RULE_UNRESOLVED.value,))
        trace = capture_calculation_trace(
            f"PRICING_{service_code}", PRICING_COMPONENT_VERSION, result.trace
        )
        traces.append(trace)
        net_amounts.append(amount)
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
                amounts=ExactLineAmounts(
                    kind="EXACT",
                    unit_price_vnd=result.trace.unit_price_vnd,
                    list_amount_vnd=amount,
                    # No promotion is evaluated here (DEC-002), so there is no discount to apply.
                    # Zero because nothing was assessed — PROMOTION_NOT_EVALUATED says so.
                    discount_amount_vnd=0,
                    net_amount_vnd=amount,
                ),
                price_trace_hash=trace.trace.snapshot_hash,
            )
        )

    # The one place a subtotal is computed. `build_quote_snapshot` recomputes the same sum from the
    # line snapshots and refuses the revision if the two disagree, so this arithmetic is checked by
    # an independent implementation before anything is persisted.
    subtotal = sum(net_amounts)
    totals = QuoteTotalsSnapshot(
        list_service_subtotal_min_vnd=subtotal,
        list_service_subtotal_max_vnd=subtotal,
        discount_amount_min_vnd=0,
        discount_amount_max_vnd=0,
        net_service_subtotal_min_vnd=subtotal,
        net_service_subtotal_max_vnd=subtotal,
        # No delivery fee, and therefore no display total. The snapshot validator enforces that
        # pairing, which is the behaviour we want: a quote with an unresolved delivery fee must not
        # present a total that looks like the amount a customer will pay.
        delivery_fee_vnd=None,
        approved_surcharge_vnd=0,
        display_total_min_vnd=None,
        display_total_max_vnd=None,
    )
    try:
        snapshot = build_quote_snapshot(
            QuoteRevisionData(
                schema_version=1,
                quote_id=quote_id,
                revision=revision,
                finality=QuoteFinality.ESTIMATE,
                status=QuoteRevisionStatus.REVIEW_REQUIRED,
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
                adjustments=(),
                totals=totals,
                calculation_traces=tuple(traces),
                calculation_engine_version=QUOTE_ENGINE_VERSION,
                calculation_engine_hash=QUOTE_ENGINE_HASH,
                promotion_eligibility_event=None,
                promotion_eligibility_at=None,
                reason_codes=BASE_REASON_CODES,
                required_approvals=BASE_REQUIRED_APPROVALS,
                approval_id=None,
            )
        )
    except QuoteSnapshotError:
        # The validator refused the assembled revision. That is a defect in this composition, not a
        # customer-visible policy outcome, so it must not be reported as a priced quote — but it
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


def _unresolved_reasons(results: dict[str, PriceResult]) -> tuple[str, ...]:
    """Collect every reason the set of results cannot become an exact revision.

    A range price is the important case. `RANGE_PER_UNIT` services are priced between a minimum and
    a maximum precisely because a human has to look at the garment, and the pricebook marks them
    `RANGE_ONLY_HUMAN_FINAL`. Persisting a range revision is a real capability, and it is not this
    item's: composing one would mean deciding how a range interacts with totals, orders and
    settlement. Refusing keeps the boundary visible instead of guessing at it.
    """
    reasons: set[str] = set()
    for result in results.values():
        if result.requires_human or result.finality is not QuoteFinality.ESTIMATE:
            reasons.add(ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value)
        elif result.list_amount_vnd is None:
            reasons.add(ErrorCode.PRICE_RULE_UNRESOLVED.value)
    return tuple(sorted(reasons))


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
    "compose_quote_revision",
]
