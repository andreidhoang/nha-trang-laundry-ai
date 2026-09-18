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

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID, uuid5

from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import (
    AdjustmentDirection,
    ApprovalAction,
    ErrorCode,
    FulfillmentMode,
    PolicyOutcome,
    PromotionEligibilityEvent,
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
from nha_trang_laundry_domain.promotion import (
    PromotionError,
    PromotionLine,
    PromotionReason,
    PromotionResult,
    evaluate_promotion,
)
from nha_trang_laundry_domain.promotion_policy import (
    PROMOTION_COMPONENT,
    PROMOTION_COMPONENT_VERSION,
    PROMOTION_POLICY_VERSION,
    PROMOTION_SNAPSHOT_CONFIG_TYPE,
    PromotionServiceRule,
    PublishedPromotionProgram,
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
from nha_trang_laundry_domain.remedies import (
    REMEDY_CREDIT_APPLIED,
    REMEDY_CREDIT_REASON_CODE,
    RemedyCredit,
    RemedyRefusal,
    RemedyRefused,
    allocate_remedy_credit,
)

QUOTE_ENGINE_VERSION: Final = "quote-engine-v1"
#: Version of the allocation this module records when it spends a remedy credit. Separate from the
#: quote engine's own version because the credit is applied to an already-priced revision and did
#: not re-run the engine.
REMEDY_CREDIT_COMPONENT_VERSION: Final = "remedy-credit-v1"
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
#
# `PROMOTION_NOT_EVALUATED` used to be the third entry, and `PROMO-WIRING-001` deleted it. It said
# the discount was zero because nothing had been assessed, which was true while the promotion engine
# had no production call site. It is now false on every revision this module composes: a promotion
# is always assessed, and the revision says which of the several possible zeroes it got --
# `PROMOTION_NOT_PUBLISHED` (invariant 11, no programme is running), `PROMOTION_OUTSIDE_INTERVAL`
# (a programme ran and has ended), `PROMOTION_NOT_TARGETED` (it is running and does not cover these
# services), or `PROMOTION_PENDING_BAND_CLOSE` (it cannot be assessed until a band is closed). A
# reason code that is no longer true does not become harmless by being about zero: it is stamped on
# an immutable revision, so it is permanent misinformation.
BASE_REASON_CODES: Final = ("TAX_TREATMENT_UNVERIFIED",)
DELIVERY_FEE_UNRESOLVED: Final = "DELIVERY_FEE_UNRESOLVED"
DELIVERY_COMPONENT_VERSION: Final = "delivery-v1"

#: Invariant 11. No `PROMOTION_POLICY` version is published, so no programme is running, none may be
#: assumed, and the quote composes at list price. Distinct from every other zero below, because
#: "nobody has published a programme" and "the programme does not cover this" are different answers
#: to the customer standing at the counter.
PROMOTION_NOT_PUBLISHED: Final = "PROMOTION_NOT_PUBLISHED"

#: A promotion is never evaluated against a band. `PROMO-WIRING-001` decided this explicitly, and it
#: is the rule that replaced the wall `RANGE-PRICE-001` left in `stored_price_bands`; see
#: `close_range_prices` for the reasoning in full.
PROMOTION_PENDING_BAND_CLOSE: Final = "PROMOTION_PENDING_BAND_CLOSE"

#: `DEC-021`. The discount re-computed at acceptance is not the one frozen when the price was read
#: to the customer, so the acceptance is refused and the bag must be priced again. Never a silent
#: re-price: the whole point of the refusal is that a person sees the number move.
PROMOTION_CHANGED_SINCE_QUOTE: Final = "PROMOTION_CHANGED_SINCE_QUOTE"

#: The real-world event `accept_quote_revision` observes: the shop commercially accepted the order,
#: which is `DEC-002`'s `accepted_at` in the enum's own vocabulary. It is passed to the engine as
#: the *observed* event and compared there against the programme's *configured* one -- a programme
#: keyed to some other event therefore stays unresolved rather than being satisfied by acceptance,
#: which is what keeps this from being circular.
ACCEPTANCE_ELIGIBILITY_EVENT: Final = PromotionEligibilityEvent.STORE_COMMERCIAL_ACCEPTED

#: One promotion adjustment per revision. The engine allocates one discount across the lines, so
#: there is one credit row for it, and its `reason_code` is the programme's own published code.
PROMOTION_ADJUSTMENT_ID: Final = "promotion"

#: The two reasons a promotion needs `ApplyPromotion` before the revision can be exact. Neither is
#: "the programme is running" -- publishing the programme *was* that approval, and demanding a
#: per-quote envelope for an automatic in-interval discount would make the owner sign every bag.
#: These are the cases where the engine says a person must decide: a service whose inclusion the
#: owner recorded as unconfirmed, and a second discount the programme does not allow to stack.
_PROMOTION_APPROVAL_REASONS: Final = (
    PromotionReason.PROMOTION_TARGET_REQUIRES_HUMAN,
    PromotionReason.PROMOTION_STACKING_REQUIRES_HUMAN,
)
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


@dataclass(frozen=True, slots=True)
class FrozenPromotion:
    """The promotion result a stored revision carries, read back from its calculation trace.

    This is what "freeze at quote, re-verify at acceptance" needs in order to mean anything. The
    revision's `totals.discount_amount_*` is the sum of *every* credit on it -- a remedy credit
    lands there too -- so comparing that number at acceptance would refuse a perfectly unchanged
    promotion on any quote that had also spent a `DEC-004` credit. `discount_amount_vnd` below is
    the promotion's own figure, and it is the only one the re-verification compares.

    Everything here is read, not recomputed. The trace is canonical JSON inside an immutable
    revision; a reader that recomputed a field would no longer be reading what the customer was
    told."""

    policy_code: str
    configuration_version_id: UUID
    configuration_version: int
    status: str
    discount_amount_vnd: int
    list_service_subtotal_vnd: int
    eligible_service_subtotal_vnd: int
    rate_bps: tuple[int, ...]
    interval_start_at: str
    interval_end_at_exclusive: str
    candidate_inside_interval: bool
    eligibility_resolved: bool
    reason_codes: tuple[str, ...]


def frozen_promotion(priced: ImmutableQuoteSnapshot) -> FrozenPromotion | None:
    """The promotion frozen onto a stored revision, or `None` if none was evaluated against it.

    `None` is the honest answer for a revision composed with no programme published and for a band
    revision, both of which carry their own reason code saying so. It is deliberately not an empty
    result with a zero in it: "no promotion was evaluated here" and "a promotion was evaluated and
    came to nothing" are the two answers this whole item exists to keep apart.
    """

    trace = next(
        (item for item in priced.data.calculation_traces if item.component == PROMOTION_COMPONENT),
        None,
    )
    if trace is None:
        return None
    payload = json.loads(trace.trace.canonical_json)
    if not isinstance(payload, Mapping):
        # Unreachable: `capture_calculation_trace` canonicalises a mapping. Refused rather than
        # asserted, because a stored revision this cannot read must not be half-understood.
        return None
    groups = payload["allocation_groups"]
    return FrozenPromotion(
        policy_code=str(payload["policy_code"]),
        configuration_version_id=UUID(str(payload["configuration_version_id"])),
        configuration_version=int(payload["configuration_version"]),
        status=str(payload["status"]),
        discount_amount_vnd=int(payload["discount_amount_vnd"]),
        list_service_subtotal_vnd=int(payload["list_service_subtotal_vnd"]),
        eligible_service_subtotal_vnd=int(payload["eligible_service_subtotal_vnd"]),
        rate_bps=tuple(int(group["rate_bps"]) for group in groups),
        interval_start_at=str(payload["interval_start_at"]),
        interval_end_at_exclusive=str(payload["interval_end_at_exclusive"]),
        candidate_inside_interval=bool(payload["candidate_inside_interval"]),
        eligibility_resolved=bool(payload["eligibility_resolved"]),
        reason_codes=tuple(str(code) for code in payload["reason_codes"]),
    )


@dataclass(frozen=True, slots=True)
class _PromotionOutcome:
    """Everything one promotion evaluation contributes to a revision, or the refusal instead."""

    lines: tuple[QuoteLineSnapshot, ...]
    discount_vnd: int
    adjustments: tuple[QuoteAdjustmentSnapshot, ...]
    traces: tuple[CalculationTraceSnapshot, ...]
    configuration_snapshots: tuple[ConfigurationSnapshotReference, ...]
    reason_codes: tuple[str, ...]
    required_approvals: tuple[str, ...]
    eligibility_event: PromotionEligibilityEvent | None
    eligibility_at: datetime | None
    refusal: str | None = None


def _promotion_outcome(
    *,
    published: PublishedPromotionProgram | None,
    lines: tuple[QuoteLineSnapshot, ...],
    banded: bool,
    evaluation_at: datetime,
    eligibility_at: datetime | None,
) -> _PromotionOutcome:
    """Evaluate the published programme against these lines and project it onto the revision.

    Three shapes come out of here and only one of them does arithmetic.

    **No programme published** (invariant 11): nothing is evaluated, nothing is referenced, and the
    revision carries `PROMOTION_NOT_PUBLISHED`. The quote composes at list price. There is no
    fallback to a constant, which is the mistake `CURRENT_PROMOTION` records.

    **A band**: the programme is referenced but not evaluated, and the revision carries
    `PROMOTION_PENDING_BAND_CLOSE`. `close_range_prices` evaluates it against the amount a staff
    member chose. See that function for why the closed amount is the right base.

    **Otherwise**: the engine decides, this projects. `eligibility_at` is `None` at quote time
    because acceptance has not happened -- the engine answers PROVISIONAL and says so -- and is the
    real `accepted_at` when `accept_quote_revision` re-verifies.
    """

    if published is None:
        return _PromotionOutcome(lines, 0, (), (), (), (PROMOTION_NOT_PUBLISHED,), (), None, None)
    reference = ConfigurationSnapshotReference(
        config_type=PROMOTION_SNAPSHOT_CONFIG_TYPE,
        version_id=published.version_id,
        version=published.version,
        snapshot_hash=published.snapshot_hash,
    )
    if banded:
        return _PromotionOutcome(
            lines, 0, (), (), (reference,), (PROMOTION_PENDING_BAND_CLOSE,), (), None, None
        )

    policy = published.program.policy
    promotion_lines: list[PromotionLine] = []
    for line in lines:
        if not isinstance(line.amounts, ExactLineAmounts):
            # Unreachable while `banded` is false -- `_validate_finality` ties the two together --
            # and refused rather than asserted, for the reason `redeem_remedy_credit` gives: a
            # stored revision this cannot read must not be half-discounted.
            return _failed_promotion(lines, ErrorCode.VALIDATION_ERROR.value)
        promotion_lines.append(_promotion_line(line, published.program.rule_for(line.service_code)))

    try:
        result = evaluate_promotion(
            policy,
            tuple(promotion_lines),
            evaluation_at=evaluation_at,
            eligibility_event=None if eligibility_at is None else ACCEPTANCE_ELIGIBILITY_EVENT,
            eligibility_at=eligibility_at,
        )
    except PromotionError as error:
        # The engine's own code, verbatim, exactly as the pricing and delivery branches do.
        return _failed_promotion(lines, error.code.value)

    allocated = {item.line_id: item.discount_vnd for item in result.adjustments}
    discounted = tuple(
        replace(
            line,
            amounts=replace(
                line.amounts,
                discount_amount_vnd=allocated[line.line_id],
                net_amount_vnd=line.amounts.list_amount_vnd - allocated[line.line_id],
            ),
        )
        if isinstance(line.amounts, ExactLineAmounts) and allocated.get(line.line_id)
        else line
        for line in lines
    )
    adjustments = (
        (
            QuoteAdjustmentSnapshot(
                adjustment_id=PROMOTION_ADJUSTMENT_ID,
                kind=QuoteAdjustmentKind.PROMOTION,
                direction=AdjustmentDirection.CREDIT,
                amount_min_vnd=result.discount_amount_vnd,
                amount_max_vnd=result.discount_amount_vnd,
                # The programme's own published code, which is why `parse_promotion_policy` holds it
                # to `CODE_PATTERN`: the money line names the programme that produced it.
                reason_code=policy.code,
                source_version_id=published.version_id,
                # No envelope. An automatic in-interval discount on a targeted service was approved
                # when the owner published the programme; see `_PROMOTION_APPROVAL_REASONS` for the
                # two cases that are not that.
                approval_id=None,
            ),
        )
        if result.discount_amount_vnd
        else ()
    )
    reasons = _promotion_reasons(
        result, evaluation_at=evaluation_at, policy_end=policy.end_at_exclusive
    )
    return _PromotionOutcome(
        lines=discounted,
        discount_vnd=result.discount_amount_vnd,
        adjustments=adjustments,
        traces=(
            capture_calculation_trace(
                PROMOTION_COMPONENT,
                PROMOTION_COMPONENT_VERSION,
                _promotion_trace(result, published=published),
            ),
        ),
        configuration_snapshots=(reference,),
        reason_codes=reasons,
        required_approvals=(
            (ApprovalAction.APPLY_PROMOTION.value,)
            if any(str(reason) in reasons for reason in _PROMOTION_APPROVAL_REASONS)
            else ()
        ),
        eligibility_event=result.eligibility_event,
        eligibility_at=result.eligibility_at,
    )


def _failed_promotion(lines: tuple[QuoteLineSnapshot, ...], refusal: str) -> _PromotionOutcome:
    return _PromotionOutcome(lines, 0, (), (), (), (), (), None, None, refusal=refusal)


def _promotion_line(line: QuoteLineSnapshot, rule: PromotionServiceRule) -> PromotionLine:
    """One priced line as the engine takes it: its **list** amount and the programme's rule.

    List, not net. The 6 kg cliff and every other pricing decision have already happened by the time
    this runs, and a promotion applies to the amount the pricing engine produced whichever side of
    the cliff it fell on. Netting first would discount an already-discounted number.
    """

    assert isinstance(
        line.amounts, ExactLineAmounts
    )  # checked by the caller before this is reached
    return PromotionLine(
        line_id=line.line_id,
        list_amount_vnd=line.amounts.list_amount_vnd,
        resolution=rule.resolution,
        rate_bps=rule.rate_bps,
    )


def _promotion_reasons(
    result: PromotionResult, *, evaluation_at: datetime, policy_end: datetime
) -> tuple[str, ...]:
    """The engine's reasons, plus the one fact its single-status answer cannot express.

    The engine reports the first reason that stops a discount, and at quote time that is always
    unresolved eligibility: acceptance has not happened yet, so `PROMOTION_ELIGIBILITY_UNRESOLVED`
    shadows everything behind it -- including an interval that closed weeks ago. On 18/09/2026, with
    the shop's one confirmed programme having ended on 31/08/2026, that would make every quote say
    "eligibility is not yet resolved" about a programme that can never become eligible again.

    So when the programme has *ended* -- `evaluation_at` is at or past `end_at_exclusive` -- this
    replaces the eligibility reason with `PROMOTION_OUTSIDE_INTERVAL`. That substitution is a fact
    of arithmetic and not a judgement: acceptance is always at or after pricing, the interval is
    half-open, and so no acceptance derived from this quote can fall inside it. The answer cannot
    change, so there is nothing left to resolve.

    A programme that has not *started* is left exactly as the engine reported it. Acceptance may
    well fall inside that interval, so eligibility genuinely is unresolved and saying otherwise
    would be a guess about the future.
    """

    reasons = tuple(str(code) for code in result.reason_codes)
    if evaluation_at < policy_end:
        return reasons
    outside = PromotionReason.PROMOTION_OUTSIDE_INTERVAL.value
    ended = tuple(
        code for code in reasons if code != PromotionReason.PROMOTION_ELIGIBILITY_UNRESOLVED.value
    )
    # The engine reports `PROMOTION_OUTSIDE_INTERVAL` itself once eligibility *is* resolved, which
    # is what the acceptance re-evaluation passes, so appending it unconditionally would put the
    # same code on a revision twice. A reason code is a statement, and a statement made twice is
    # not more true.
    if outside in ended:
        return ended
    return (*ended, outside)


def _promotion_trace(
    result: PromotionResult, *, published: PublishedPromotionProgram
) -> dict[str, object]:
    """The engine's own working, frozen onto the revision so the discount can be reproduced.

    Every intermediate the largest-remainder allocation used is here -- the eligible subtotal, the
    exact numerator and denominator, the rounded group total, the floors, the remainders and the
    order the spare dong were awarded in. A revision that carried only the answer could not be
    audited: "why did this shirt get 11.112 d and that one 11.111 d" is a question only the working
    answers, and it is a question customers actually ask.
    """

    policy = published.program.policy
    return {
        "policy_code": policy.code,
        "policy_rule_version": PROMOTION_POLICY_VERSION,
        "configuration_version_id": str(published.version_id),
        "configuration_version": published.version,
        "timezone": policy.timezone,
        "interval_start_at": _moment_text(policy.start_at),
        "interval_end_at_exclusive": _moment_text(policy.end_at_exclusive),
        "stacking_allowed": policy.stacking_allowed,
        "status": result.status.value,
        "outcome": result.outcome.value,
        "configured_eligibility_event": str(result.trace.configured_eligibility_event),
        "observed_eligibility_event": (
            None
            if result.trace.observed_eligibility_event is None
            else str(result.trace.observed_eligibility_event)
        ),
        "observed_eligibility_at": (
            None
            if result.trace.observed_eligibility_at is None
            else _moment_text(result.trace.observed_eligibility_at)
        ),
        "candidate_reference_at": _moment_text(result.trace.candidate_reference_at),
        "candidate_inside_interval": result.trace.candidate_inside_interval,
        "eligibility_resolved": result.trace.eligibility_resolved,
        "list_service_subtotal_vnd": result.list_service_subtotal_vnd,
        "eligible_service_subtotal_vnd": result.eligible_service_subtotal_vnd,
        "discount_amount_vnd": result.discount_amount_vnd,
        "net_service_subtotal_vnd": result.net_service_subtotal_vnd,
        "rounding": result.trace.rounding,
        "delivery_discount_vnd": result.trace.delivery_discount_vnd,
        "allocation_groups": [
            {
                "rate_bps": group.rate_bps,
                "eligible_line_ids": list(group.eligible_line_ids),
                "eligible_subtotal_vnd": group.eligible_subtotal_vnd,
                "exact_discount_numerator": group.exact_discount_numerator,
                "exact_discount_denominator": group.exact_discount_denominator,
                "rounded_discount_vnd": group.rounded_discount_vnd,
                "floor_allocations_vnd": list(group.floor_allocations_vnd),
                "remainders": list(group.remainders),
                "remainder_award_order": list(group.remainder_award_order),
                "final_allocations_vnd": list(group.final_allocations_vnd),
            }
            for group in result.trace.allocation_groups
        ],
        "lines": [
            {
                "line_id": item.line_id,
                "list_amount_vnd": item.list_amount_vnd,
                "rate_bps": item.rate_bps,
                "discount_vnd": item.discount_vnd,
                "net_amount_vnd": item.net_amount_vnd,
                "resolution": str(item.resolution),
            }
            for item in result.adjustments
        ],
        "reason_codes": [str(code) for code in result.reason_codes],
    }


def _moment_text(value: datetime) -> str:
    """The same UTC spelling `canonical_document` gives a datetime, so a read-back is
    byte-stable."""
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


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
    promotion: PublishedPromotionProgram | None = None,
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

    `promotion` is the published `PROMOTION_POLICY` version in force, or `None` when the owner has
    published none. `None` is a legitimate and expected state, not a missing argument: the quote
    composes at list price and carries `PROMOTION_NOT_PUBLISHED` (invariant 11). There is
    deliberately no default programme to fall back on.
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
                # A band never carries a discount. `PROMO-WIRING-001` decided that a promotion
                # applies to the amount a staff member closes the band at, not to its bounds, so
                # there is nothing to discount either end by; `close_range_prices` evaluates the
                # programme when the amount exists. The revision says so with
                # `PROMOTION_PENDING_BAND_CLOSE` rather than showing an unexplained zero.
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
                # Zero here, and filled in below by `_promotion_outcome` once every line exists:
                # the engine allocates one group discount across the whole set of eligible lines by
                # largest remainder, so it cannot be computed one line at a time.
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

    promoted = _promotion_outcome(
        published=promotion,
        lines=tuple(lines),
        banded=banded,
        # The moment the price is computed. A promotion keyed to `accepted_at` cannot be resolved
        # here because acceptance has not happened; the engine answers PROVISIONAL and says so, and
        # `accept_quote_revision` re-verifies against the real acceptance moment.
        evaluation_at=priced_at,
        eligibility_at=None,
    )
    if promoted.refusal is not None:
        return UnresolvedQuote((promoted.refusal,))
    if chosen and promoted.required_approvals:
        # A closed band is `APPROVED_EXACT`, and `_validate_finality` refuses that finality while an
        # approval is still outstanding. Naming the promotion reason rather than letting the
        # validator answer VALIDATION_ERROR tells the person at the counter which decision is
        # missing: the owner has to rule on this service before its price can be exact.
        return UnresolvedQuote(promoted.reason_codes)

    minimum_subtotal = sum(net_minimums)
    maximum_subtotal = sum(net_maximums)
    discount = promoted.discount_vnd
    totals = QuoteTotalsSnapshot(
        list_service_subtotal_min_vnd=minimum_subtotal,
        list_service_subtotal_max_vnd=maximum_subtotal,
        discount_amount_min_vnd=discount,
        discount_amount_max_vnd=discount,
        net_service_subtotal_min_vnd=minimum_subtotal - discount,
        net_service_subtotal_max_vnd=maximum_subtotal - discount,
        # An unresolved fee still means no display total: the snapshot validator enforces the
        # pairing, and a quote whose fee needs a human must not present a number that looks like
        # the amount a customer will pay. A resolved fee -- including the zero the engine returns
        # for self-drop/self-collect -- produces the total the customer is actually quoted.
        delivery_fee_vnd=fee,
        approved_surcharge_vnd=0,
        display_total_min_vnd=None if fee is None else minimum_subtotal - discount + fee,
        display_total_max_vnd=None if fee is None else maximum_subtotal - discount + fee,
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
                    *promoted.configuration_snapshots,
                ),
                lines=promoted.lines,
                adjustments=delivery_adjustments + promoted.adjustments,
                totals=totals,
                calculation_traces=tuple(traces) + promoted.traces,
                calculation_engine_version=QUOTE_ENGINE_VERSION,
                calculation_engine_hash=QUOTE_ENGINE_HASH,
                promotion_eligibility_event=promoted.eligibility_event,
                promotion_eligibility_at=promoted.eligibility_at,
                reason_codes=BASE_REASON_CODES
                + delivery_reasons
                + promoted.reason_codes
                # Carried on the revision, not only returned beside it: a band nobody has closed is
                # a fact about the stored revision, and it is what lets a console say "can nhan
                # vien chot gia trong khoang" instead of showing a total that does not exist.
                + ((ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value,) if banded else ()),
                required_approvals=BASE_REQUIRED_APPROVALS + promoted.required_approvals,
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
    # `PROMO-WIRING-001` replaced `QUOTE_PROMOTION_NOT_EVALUATED` with these two. The old code said
    # a promotion had been "priced against but never evaluated", which stopped being a state this
    # system can be in: every revision is either evaluated against a published programme or says
    # that none was published. What can still go wrong is that the answer *moved* between the price
    # being read aloud and the customer saying yes, or that it cannot be resolved at all.
    PROMOTION_CHANGED_SINCE_QUOTE: (
        "the promotion is not what it was when this price was read to the customer; price again"
    ),
    ErrorCode.PROMOTION_ELIGIBILITY_UNRESOLVED.value: (
        "the published programme is keyed to an event this acceptance is not"
    ),
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
    promotion: PublishedPromotionProgram | None = None,
    accepted_at: datetime | None = None,
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

    **The one thing it does re-compute is the promotion, and only ever to refuse.**
    `PROMO-WIRING-001`, `DEC-002`, `DEC-021`. A promotion is keyed to `accepted_at`, which does not
    exist when the price is quoted, so the quote freezes a provisional discount and this
    re-evaluates the same published programme against the real acceptance moment. If the two
    disagree by so much as one dong the acceptance is refused with `PROMOTION_CHANGED_SINCE_QUOTE`
    and the bag is priced again. Nothing is silently re-priced: the refusal exists so that a
    programme expiring between reading a price and taking the laundry is something a person sees
    rather than something the till absorbs.
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
    verified = _reverified_eligibility(priced=priced, promotion=promotion, accepted_at=accepted_at)
    if isinstance(verified, str):
        return UnresolvedQuote((verified,))
    event, event_at = verified
    try:
        snapshot = build_quote_snapshot(
            replace(
                data,
                revision=revision,
                finality=QuoteFinality.APPROVED_EXACT,
                status=QuoteRevisionStatus.ACCEPTED_FINAL,
                promotion_eligibility_event=event,
                promotion_eligibility_at=event_at,
                required_approvals=(),
            )
        )
    except QuoteSnapshotError:
        # The validator refused an assembly the checks above did not anticipate. Refusing with a
        # generic code is correct: presenting it as an accepted price would be worse than refusing.
        return UnresolvedQuote((ErrorCode.VALIDATION_ERROR.value,))
    return ComposedQuote(snapshot)


def _promotion_reference(
    priced: ImmutableQuoteSnapshot,
) -> ConfigurationSnapshotReference | None:
    """The published programme a stored revision was priced against, if it names one."""

    return next(
        (
            item
            for item in priced.data.configuration_snapshots
            if item.config_type == PROMOTION_SNAPSHOT_CONFIG_TYPE
        ),
        None,
    )


def _reverified_eligibility(
    *,
    priced: ImmutableQuoteSnapshot,
    promotion: PublishedPromotionProgram | None,
    accepted_at: datetime | None,
) -> tuple[PromotionEligibilityEvent | None, datetime | None] | str:
    """Re-run the frozen programme against the real acceptance moment. `DEC-021`, `DEC-002`.

    Returns the eligibility event and moment the accepted revision records, or a single refusal
    code. The refusal is the product; the tuple is what is left when nothing has to be refused.

    **What is compared is the promotion's own discount, not the revision's.**
    `totals.discount_amount_*` is the sum of every credit on the revision, and a `DEC-004` remedy
    credit lands in the same place. Comparing that number would refuse an unchanged promotion on any
    quote that had also spent a credit, which is a refusal with no fact behind it.

    **A programme version that is no longer the published one refuses too**, before any arithmetic.
    Re-verifying against a different document is not re-verification: the number agreeing would be a
    coincidence, and invariant 8's reading of "the content the approval bound" applies to the
    programme as much as to the lines.
    """

    reference = _promotion_reference(priced)
    if reference is None and promotion is None:
        # No programme was published when this was priced and none is published now. There is
        # nothing to re-verify and nothing to record.
        return None, None
    if accepted_at is None:
        # A promotion keyed to `accepted_at` cannot be re-verified without `accepted_at`. Refusing
        # is the only option: the alternative is choosing a moment nobody chose.
        return ErrorCode.MISSING_REQUIRED_FACT.value
    if reference is not None and (
        promotion is None or promotion.version_id != reference.version_id
    ):
        return PROMOTION_CHANGED_SINCE_QUOTE

    frozen = frozen_promotion(priced)
    if reference is not None and frozen is None:
        # A revision citing a programme with no frozen trace is one this cannot read. `RANGE`
        # revisions are the only composer output shaped that way and acceptance already refused
        # them above, so meeting this means the stored revision is not what it claims.
        return ErrorCode.VALIDATION_ERROR.value

    recomputed = _promotion_outcome(
        published=promotion,
        lines=priced.data.lines,
        banded=False,
        evaluation_at=accepted_at,
        eligibility_at=accepted_at,
    )
    if recomputed.refusal is not None:
        return recomputed.refusal
    if recomputed.discount_vnd != (0 if frozen is None else frozen.discount_amount_vnd):
        # The number moved. `DEC-021` says the customer agreed to a price that was read aloud, so
        # the shop may not charge this one -- in either direction. A discount that appeared is as
        # much a price the customer did not agree to as one that vanished.
        return PROMOTION_CHANGED_SINCE_QUOTE
    if reference is None:
        # Nothing was quoted against a programme and the recomputation confirms nothing changed, so
        # the accepted revision records no eligibility event. It has none: no programme priced it.
        return None, None
    if recomputed.eligibility_event is None:
        # The programme is keyed to an event that commercial acceptance is not, so acceptance does
        # not resolve its eligibility and no exact price can rest on it. Named rather than left to
        # the snapshot validator, which would answer VALIDATION_ERROR.
        return ErrorCode.PROMOTION_ELIGIBILITY_UNRESOLVED.value
    return recomputed.eligibility_event, recomputed.eligibility_at


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
            # producible, and the second one is now unproducible *by rule* rather than by the
            # absence of promotions: `PROMO-WIRING-001` decided that a promotion applies to the
            # closed amount and never to a band, so `_promotion_outcome` leaves every banded
            # revision at zero. The check stays because it is what makes that rule checkable from
            # this side; meeting it means the stored revision is not what this function can safely
            # read, and guessing which number was meant is how money moves by accident.
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
    promotion: PublishedPromotionProgram | None = None,
    closed_at: datetime | None = None,
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

    **A promotion applies to the closed amount, never to the band.** `RANGE-PRICE-001` left
    `stored_price_bands` refusing a banded line that carried a discount, because "the amount" would
    then be ambiguous between list and net, and it left the question of which number a promotion
    discounts for this item to answer. It is answered here, and the answer is the closed one.

    Three things decide it. The band is the owner's published *authorisation* to charge somewhere in
    an interval; the closed amount is a staff attestation under `DEC-021` of what this garment
    costs, and it is the only one of the two that is a price. `DEC-002` keys eligibility to
    `accepted_at`, which is necessarily after the band was closed, so a discount computed against
    the band would be computed against a number that was already superseded when eligibility
    arrived. And a customer reading "160.000-480.000, less 30%" is being read a discount off a price
    nobody will pay.

    So the band revision carries `PROMOTION_PENDING_BAND_CLOSE` and a zero, and the promotion is
    evaluated here, once, over the whole closed line set. Once, because the engine allocates a group
    discount across every eligible line at the same rate by largest remainder: discounting the exact
    lines at band time and the closed ones now would produce two allocations whose dong do not add
    up to the one the rate implies.

    `promotion` must be the same published version the band revision cites, and `closed_at` the
    moment the band is closed. A different version refuses with `PROMOTION_CHANGED_SINCE_QUOTE`
    rather than pricing against a programme the customer was never shown.
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

    reference = _promotion_reference(priced)
    if reference is not None and (
        promotion is None or promotion.version_id != reference.version_id
    ):
        # The band was shown under one programme and a different one is published now. Closing the
        # band against the new one would apply a discount the customer was never offered.
        return UnresolvedQuote((PROMOTION_CHANGED_SINCE_QUOTE,))
    if promotion is not None and closed_at is None:
        # A programme is in force and nobody said when the band was closed. The interval test needs
        # a moment and this module will not pick one.
        return UnresolvedQuote((ErrorCode.MISSING_REQUIRED_FACT.value,))
    promoted = _promotion_outcome(
        published=promotion,
        lines=closed_lines,
        banded=False,
        evaluation_at=closed_at or data.priced_at,
        # Acceptance has not happened: closing a band is the shop choosing the price, not the
        # customer agreeing to it. The discount is provisional here exactly as it is on any other
        # quote, and `accept_quote_revision` re-verifies it against the real `accepted_at`.
        eligibility_at=None,
    )
    if promoted.refusal is not None:
        return UnresolvedQuote((promoted.refusal,))
    if promoted.required_approvals:
        # This revision is `APPROVED_EXACT`, which `_validate_finality` refuses while an approval is
        # outstanding. The owner has to rule on the promotion for this service first.
        return UnresolvedQuote(promoted.reason_codes)
    closed_lines = promoted.lines

    subtotal = sum(_line_list(line) for line in closed_lines)
    discount = promoted.discount_vnd
    fee = data.totals.delivery_fee_vnd
    assert fee is not None  # `_exactness_refusal` returned above when it was not resolved.
    net = subtotal - discount
    totals = replace(
        data.totals,
        list_service_subtotal_min_vnd=subtotal,
        list_service_subtotal_max_vnd=subtotal,
        discount_amount_min_vnd=discount,
        discount_amount_max_vnd=discount,
        net_service_subtotal_min_vnd=net,
        net_service_subtotal_max_vnd=net,
        display_total_min_vnd=net + fee + data.totals.approved_surcharge_vnd,
        display_total_max_vnd=net + fee + data.totals.approved_surcharge_vnd,
    )
    try:
        snapshot = build_quote_snapshot(
            replace(
                data,
                revision=revision,
                finality=QuoteFinality.APPROVED_EXACT,
                status=QuoteRevisionStatus.APPROVED,
                lines=closed_lines,
                adjustments=(*data.adjustments, *promoted.adjustments),
                totals=totals,
                calculation_traces=(*data.calculation_traces, *promoted.traces),
                # The band is closed, so the two reasons that said it was not no longer apply:
                # `RANGE_PRICE_REQUIRES_HUMAN` because a person has now chosen, and
                # `PROMOTION_PENDING_BAND_CLOSE` because the promotion has now been evaluated
                # against the amount they chose. Both are replaced by what the evaluation found.
                # Every other reason the revision carried is untouched: they are facts about how it
                # was priced and closing a band did not change any of them.
                reason_codes=tuple(
                    code
                    for code in data.reason_codes
                    if code
                    not in {
                        ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value,
                        PROMOTION_PENDING_BAND_CLOSE,
                    }
                )
                + promoted.reason_codes,
                required_approvals=(),
                approval_id=attestation.approval_id,
            )
        )
    except QuoteSnapshotError:
        return UnresolvedQuote((ErrorCode.VALIDATION_ERROR.value,))
    return ComposedQuote(snapshot)


#: Statuses a credit may not land on, because the total has already been agreed or withdrawn. It is
#: `_UNACCEPTABLE_STATUSES` plus `APPROVED`: a credit changes what a total *is* before the customer
#: is told it, and `APPROVED` means an owner has already authorised the number on the page.
_UNCREDITABLE_STATUSES: Final = _UNACCEPTABLE_STATUSES | {QuoteRevisionStatus.APPROVED}


def redeem_remedy_credit(
    *,
    priced: ImmutableQuoteSnapshot,
    revision: int,
    credit: RemedyCredit,
) -> ComposedQuote | UnresolvedQuote:
    """Spend one remedy credit against the next bill. `REMEDY-001`, `DEC-004`.

    "10% credit on the next bill" needs a next bill, and this is where one gets it. The credit
    becomes a `REMEDY_CREDIT` adjustment on a **new revision derived from the stored one**, never an
    adjustment to a settlement: the settlement ledger is append-only, `reject_ledger_mutation()`
    would refuse a rewrite anyway, and `DEC-010` keeps the settlement path accepting only the exact
    quoted total in full. Nothing about what may be paid changes; what changes is what the total is,
    before anybody is told it.

    **The credit is allocated across lines before it can be stored at all.** `_validate_adjustments`
    requires the summed credit adjustments to equal the revision's line-level discount totals, and
    `net_service_subtotal = list_service_subtotal - discount` is a CHECK on `quote_revisions`
    (migration `0005`) rather than merely a Python assertion. So an order-level credit has to become
    line-level, using `promotion`'s own allocator through `allocate_remedy_credit` so that "who gets
    the spare dong" has one answer in this system rather than two.

    **A band is refused rather than credited**, and that refusal is load-bearing.
    `stored_price_bands` returns `None` for a banded line carrying a non-zero discount, so
    `close_range_prices` would answer `VALIDATION_ERROR` for a band this function had discounted --
    a wall `RANGE-PRICE-001` put there deliberately. A remedy credit must not be the thing that
    walks into it, so it never lands on a `RANGE` revision at all.

    Allocation weights are each line's **net** amount, not its list amount. A line already carrying
    a promotion discount can absorb only what is left of it, and weighting by list could push a net
    below zero on a heavily discounted line while the revision as a whole still balanced.
    """

    data = priced.data
    if data.finality is QuoteFinality.RANGE or data.status in _UNCREDITABLE_STATUSES:
        return UnresolvedQuote((RemedyRefusal.REMEDY_CREDIT_REVISION_NOT_OPEN.value,))
    weights: dict[str, int] = {}
    for line in data.lines:
        if not isinstance(line.amounts, ExactLineAmounts):
            # Unreachable while finality is not RANGE -- `_validate_finality` ties the two together
            # -- and refused rather than asserted, because a stored revision this cannot read must
            # not be half-credited on the strength of an invariant holding somewhere else.
            return UnresolvedQuote((RemedyRefusal.REMEDY_CREDIT_REVISION_NOT_OPEN.value,))
        weights[line.line_id] = line.amounts.net_amount_vnd

    allocated = allocate_remedy_credit(line_weights_vnd=weights, credit_vnd=credit.amount_vnd)
    if isinstance(allocated, RemedyRefused):
        # Carried, not capped. A credit bigger than the bill it is presented against stays owed in
        # full; silently shrinking it to fit would cancel part of a debt nobody decided to cancel.
        return UnresolvedQuote((allocated.reason_code,))

    credited_lines = tuple(
        replace(
            line,
            amounts=replace(
                line.amounts,
                discount_amount_vnd=line.amounts.discount_amount_vnd
                + allocated.per_line_vnd[line.line_id],
                net_amount_vnd=line.amounts.net_amount_vnd - allocated.per_line_vnd[line.line_id],
            ),
        )
        if isinstance(line.amounts, ExactLineAmounts)
        else line
        for line in data.lines
    )
    discount = data.totals.discount_amount_min_vnd + allocated.total_vnd
    subtotal = data.totals.list_service_subtotal_min_vnd - discount
    fee = data.totals.delivery_fee_vnd
    totals = replace(
        data.totals,
        discount_amount_min_vnd=discount,
        discount_amount_max_vnd=data.totals.discount_amount_max_vnd + allocated.total_vnd,
        net_service_subtotal_min_vnd=subtotal,
        net_service_subtotal_max_vnd=subtotal,
        # The pairing the validator enforces: an unresolved fee still presents no total. Crediting a
        # quote does not resolve its transport.
        display_total_min_vnd=(
            None if fee is None else subtotal + fee + data.totals.approved_surcharge_vnd
        ),
        display_total_max_vnd=(
            None if fee is None else subtotal + fee + data.totals.approved_surcharge_vnd
        ),
    )
    adjustment = QuoteAdjustmentSnapshot(
        # Scoped by the credit's own identifier, so two credits on one revision cannot collide and
        # the adjustment on the stored snapshot names the instrument that was spent.
        adjustment_id=f"remedy-credit-{credit.credit_id}",
        kind=QuoteAdjustmentKind.REMEDY_CREDIT,
        direction=AdjustmentDirection.CREDIT,
        amount_min_vnd=allocated.total_vnd,
        amount_max_vnd=allocated.total_vnd,
        reason_code=REMEDY_CREDIT_REASON_CODE,
        # The published `REMEDY_POLICY` version the figure came from, exactly as the specification's
        # field table assigns it. It is the provenance a reader needs years later.
        source_version_id=credit.policy_version_id,
        approval_id=credit.approval_id,
    )
    try:
        snapshot = build_quote_snapshot(
            replace(
                data,
                revision=revision,
                lines=credited_lines,
                adjustments=(*data.adjustments, adjustment),
                totals=totals,
                reason_codes=(*data.reason_codes, REMEDY_CREDIT_APPLIED),
                calculation_traces=(
                    *data.calculation_traces,
                    capture_calculation_trace(
                        # Scoped by the credit's own id so a second credit on a later revision of
                        # the same quote does not collide with this one: `_validate_traces` requires
                        # component names to be unique within a revision, and a revision derived
                        # from a credited one carries the earlier trace forward.
                        f"REMEDY_CREDIT_{credit.credit_id.hex.upper()}",
                        REMEDY_CREDIT_COMPONENT_VERSION,
                        {
                            "credit_id": str(credit.credit_id),
                            "credit_vnd": allocated.total_vnd,
                            "policy_version_id": str(credit.policy_version_id),
                            "weights_vnd": [
                                {"line_id": line_id, "net_amount_vnd": weights[line_id]}
                                for line_id in allocated.allocation.ordered_ids
                            ],
                            "floor_allocations_vnd": list(
                                allocated.allocation.floor_allocations_vnd
                            ),
                            "remainders": list(allocated.allocation.remainders),
                            "remainder_award_order": list(
                                allocated.allocation.remainder_award_order
                            ),
                            "final_allocations_vnd": list(
                                allocated.allocation.final_allocations_vnd
                            ),
                            "rounding": allocated.rounding,
                        },
                    ),
                ),
            )
        )
    except QuoteSnapshotError:
        # The validator refused an assembly the checks above did not anticipate. Reporting it as a
        # priced quote would be worse than refusing, exactly as the other two derivations decide.
        return UnresolvedQuote((ErrorCode.VALIDATION_ERROR.value,))
    return ComposedQuote(snapshot)


def _line_list(line: QuoteLineSnapshot) -> int:
    """The list amount a closed line contributes. Every line is exact by the time this is called.

    List rather than net since `PROMO-WIRING-001`: a closed line may now carry a promotion discount,
    and `quote_revisions` has `net_service_subtotal = list_service_subtotal - discount` as a CHECK
    (migration `0005`). Summing net amounts into the list subtotal would have subtracted the
    discount twice.
    """

    if isinstance(line.amounts, ExactLineAmounts):
        return line.amounts.list_amount_vnd
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
    "ACCEPTANCE_ELIGIBILITY_EVENT",
    "BASE_REASON_CODES",
    "BASE_REQUIRED_APPROVALS",
    "PROMOTION_CHANGED_SINCE_QUOTE",
    "PROMOTION_NOT_PUBLISHED",
    "PROMOTION_PENDING_BAND_CLOSE",
    "QUOTE_ENGINE_HASH",
    "QUOTE_ENGINE_VERSION",
    "REMEDY_CREDIT_COMPONENT_VERSION",
    "ComposedQuote",
    "FrozenPromotion",
    "PricebookProvenance",
    "QuoteComposition",
    "RequestedLine",
    "UnresolvedQuote",
    "accept_quote_revision",
    "close_range_prices",
    "compose_quote_revision",
    "frozen_promotion",
    "redeem_remedy_credit",
    "stored_price_bands",
]
