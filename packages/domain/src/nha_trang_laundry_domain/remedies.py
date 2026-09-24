"""Decide what the shop owes a customer when something went wrong. `DEC-004`, `REMEDY-001`.

`DEC-004` was resolved on 2026-08-18 with six figures the owner supplied, and until this module
nothing read any of them: `ApprovalAction.APPROVE_REMEDY`, the `REMEDY_PROPOSAL` resource type and
`AdjustmentDirection.CREDIT` all existed with no implementation behind them. A complaint could be
recorded and could not lead to any outcome, so the shop settled it verbally and the 5x ceiling and
the 100.000 d escalation the owner decided were enforced by nothing.

**The figures are configuration, never constants.** Every number below is read from a published
`REMEDY_POLICY` document (`packages/db/.../remedies.py`), for the reason invariant 11 exists and the
reason `CURRENT_PROMOTION` is a cautionary tale: a hardcoded `100_000` would make the shop's
liability ceiling a code deploy. With nothing published, `REMEDY_POLICY_UNPUBLISHED` refuses every
request; there is no fallback, no default and no minimum.

**Loss refuses, and this is the most likely way the item goes wrong.** The decision packet says
loss is *not* covered by the damage figures and must be confirmed with the owner before any of them
is applied to it by analogy. So `RemedyKind.LOST_ITEM` is accepted as a **record** -- the complaint
is written down, which is the whole point of the item -- and answers `REQUIRE_HUMAN` with
`LOSS_POLICY_UNRESOLVED`. It inherits no ceiling, no window and no staff authority, and nothing
below computes a number for it. "Unknown means stop", applied to the one sub-case nobody answered.

**Money direction, under invariant 2.** A remedy is money owed *to* the customer and is never a
negative settlement. A proposal carries a non-negative `amount_vnd` and an explicit
`AdjustmentDirection.CREDIT`; the direction carries the sign, so the amount needs no special case.

**Staff never type a ceiling.** The server computes 5x from the order line's own priced amount and
10% from the order's settled total, both already stored. A proposal above its computed ceiling is
refused **with the ceiling named**, never truncated to it -- truncating would silently rewrite what
a person asked for into a number they did not choose.

**Windows run from a recorded fact, not from staff input, and the fact is the customer's.**
`CUSTOMER_SERVICE_POLICY_DRAFT.md` s5 states the report window as "trong 24 gio sau khi *nhan* do"
with *khach* as the subject: the clock starts when the **customer receives their laundry back**, not
when the shop received it for washing. The 7-day rewash window runs from pickup, which is the same
event seen from the other side. Both therefore anchor on one timestamp, supplied by the caller as
`RemedyOrderFacts.goods_returned_at` and derived there from `orders.production_released_at` for a
counter collection or from the succeeded `RETURN` delivery leg for a delivery. Absent, the request
is refused with `REMEDY_WINDOW_EVIDENCE_MISSING` rather than measured against `now`.

The refusal registry follows `settlement.REFUSAL_DECISIONS` and `range_prices`: every refusal names
the invariant or decision that owns it, so a caller is told what would have to change rather than
only that something did not pass. `test_remedies.py` pins the mapping's completeness against the
enum.

Nothing here is added to `ErrorCode` or to `canonical-enums-v1.json`, exactly as `RANGE-PRICE-001`
decided: this is an internal staff capability and the public agent contract does not change because
the counter gained a form.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from nha_trang_laundry_domain.canonical import CanonicalDocument, canonical_document
from nha_trang_laundry_domain.catalog import AdjustmentDirection, PolicyOutcome
from nha_trang_laundry_domain.promotion import (
    LARGEST_REMAINDER_RULE,
    RATE_DENOMINATOR,
    LargestRemainderAllocation,
    allocate_largest_remainder,
)

#: The same ceiling `quotes._money` enforces. An amount above it cannot survive canonicalization.
MAX_JCS_INTEGER: Final = 9_007_199_254_740_991

#: `ConfigurationRepository` config type for the published document. Matches
#: `^[A-Z][A-Z0-9_]{1,62}$`, and `(config_type, version)` is unique since migration `0002`.
REMEDY_POLICY_CONFIG_TYPE: Final = "REMEDY_POLICY"

#: The schema tag inside the published document, so a payload cannot be mistaken for another type's.
REMEDY_POLICY_SCHEMA: Final = "remedy-policy-v1"

#: The rule version an approval envelope names, so the envelope records which reading of `DEC-004`
#: was in force when it was signed. It moves when what counts as an authorised remedy changes -- not
#: when the owner republishes a figure, which is what the configuration version records.
REMEDY_POLICY_VERSION: Final = "remedy-dec-004-v1"

#: The canonical document type a proposal's `rendered_hash` is taken over. Named inside the document
#: so a digest can never be mistaken for the digest of some other kind of content.
REMEDY_PROPOSAL_DOCUMENT_SCHEMA: Final = "remedy-proposal-attestation-v1"

#: `reason_code` on the quote adjustment a redeemed credit becomes. `quotes.CODE_PATTERN` applies.
REMEDY_CREDIT_REASON_CODE: Final = "REMEDY_CREDIT_DEC_004"

#: Stamped on the revision a credit lands on, so a reader of the immutable snapshot can see that
#: its discount is a remedy rather than a promotion nobody can find.
REMEDY_CREDIT_APPLIED: Final = "REMEDY_CREDIT_APPLIED"

#: Stamped on a re-priced revision that could not carry a credit its parent reserved -- the bill got
#: smaller than the credit, became a band, or the credit was spent by another order meanwhile. The
#: credit is not shrunk to fit and not lost: it stays owed in full, and a calculation trace named
#: `REMEDY_CREDIT_RELEASED_<credit id>` on the same revision says which credit and why.
REMEDY_CREDIT_RELEASED: Final = "REMEDY_CREDIT_RELEASED"


class RemedyKind(StrEnum):
    """The four outcomes an incident can reach. There is deliberately no fifth."""

    #: The laundry is redone at no charge, within 7 days of pickup, on staff-attested store fault.
    #: No money moves, so there is no ceiling and no approval threshold to cross.
    FREE_REWASH = "FREE_REWASH"
    #: Compensation for an item the shop damaged, capped at a multiple of what the shop charged to
    #: clean that item -- not its retail value and not its replacement value.
    DAMAGE_COMPENSATION = "DAMAGE_COMPENSATION"
    #: The 10% credit on the next bill the owner confirmed for a delivery more than two hours late
    #: by store fault.
    LATE_DELIVERY_CREDIT = "LATE_DELIVERY_CREDIT"
    #: Recorded, never priced. `DEC-004` explicitly carries loss forward as unresolved.
    LOST_ITEM = "LOST_ITEM"


class RemedyStatus(StrEnum):
    """Where one proposal is in its life. Persisted verbatim in `remedy_proposals.status`."""

    #: Inside the staff approval ceiling. A named staff member may execute it themselves.
    STAFF_AUTHORIZED = "STAFF_AUTHORIZED"
    #: Above the staff ceiling, so `DEC-004` requires the owner. An `APPROVE_REMEDY` envelope is
    #: raised at proposal time, which is what lets the console tell staff *before* they fill the
    #: form in rather than after.
    OWNER_APPROVAL_REQUIRED = "OWNER_APPROVAL_REQUIRED"
    #: The remedy happened: a credit was issued, or a rewash was commanded.
    EXECUTED = "EXECUTED"
    #: Recorded and stopped. Only `LOST_ITEM` reaches this, and nothing moves it out.
    POLICY_UNRESOLVED = "POLICY_UNRESOLVED"


class RemedyRefusal(StrEnum):
    """Why this system will not turn a request into money owed."""

    #: Invariant 11. No `REMEDY_POLICY` version is published, so there are no figures to apply and
    #: none may be assumed. Every kind fails closed on this, including the ones that move no money:
    #: the 7-day rewash window is itself a published figure.
    REMEDY_POLICY_UNPUBLISHED = "REMEDY_POLICY_UNPUBLISHED"
    #: `DEC-004` carries loss forward as unresolved. The record is kept; no figure is applied.
    LOSS_POLICY_UNRESOLVED = "LOSS_POLICY_UNRESOLVED"
    #: The amount asked for is above the ceiling the server computed from the shop's own stored
    #: numbers. Refused with the ceiling named, never truncated to it.
    REMEDY_CEILING_EXCEEDED = "REMEDY_CEILING_EXCEEDED"
    #: The published window measured from the recorded fact has closed. The refusal carries the
    #: moment it closed, so staff can tell the customer *why* rather than only *that*.
    REMEDY_WINDOW_CLOSED = "REMEDY_WINDOW_CLOSED"
    #: Nothing recorded says when the customer got their laundry back, so no window can be
    #: measured. Refusing is the only honest answer; `now` is not a substitute for the fact.
    REMEDY_WINDOW_EVIDENCE_MISSING = "REMEDY_WINDOW_EVIDENCE_MISSING"
    #: Every remedy in `DEC-004` rests on staff determining the store was at fault. Nobody did.
    REMEDY_STORE_FAULT_NOT_ATTESTED = "REMEDY_STORE_FAULT_NOT_ATTESTED"
    #: An amount was supplied for a kind whose amount the server computes or that moves no money, or
    #: none was supplied for the one kind that needs one.
    REMEDY_AMOUNT_NOT_APPLICABLE = "REMEDY_AMOUNT_NOT_APPLICABLE"
    #: Damage compensation names a line the order's own priced revision does not contain, so there
    #: is no cleaning fee to take a multiple of.
    REMEDY_LINE_NOT_PRICED = "REMEDY_LINE_NOT_PRICED"
    #: A 10% credit on a total nobody has settled has nothing to be 10% of.
    REMEDY_ORDER_NOT_SETTLED = "REMEDY_ORDER_NOT_SETTLED"
    #: The order records no succeeded return leg, so no delivery happened that could be late. The
    #: same shape as `orders._reject_resolution_contradicting_the_record`: only contradictions the
    #: system can check are refused.
    REMEDY_DELIVERY_NOT_RECORDED = "REMEDY_DELIVERY_NOT_RECORDED"
    #: The attested lateness does not reach the published threshold, which is named in the refusal.
    REMEDY_LATENESS_BELOW_THRESHOLD = "REMEDY_LATENESS_BELOW_THRESHOLD"
    #: The credit is larger than what is left to discount on the revision it would land on. Refused
    #: and carried, never capped: capping would quietly cancel part of a debt the shop owes.
    REMEDY_CREDIT_UNALLOCATABLE = "REMEDY_CREDIT_UNALLOCATABLE"
    #: The revision a credit would land on is a band or has already been agreed. A credit changes
    #: what a total *is* before the customer is told it; it may not change one already told.
    REMEDY_CREDIT_REVISION_NOT_OPEN = "REMEDY_CREDIT_REVISION_NOT_OPEN"
    #: A credit is a bearer instrument redeemable exactly once, and this one is spent.
    REMEDY_CREDIT_ALREADY_REDEEMED = "REMEDY_CREDIT_ALREADY_REDEEMED"
    #: The revision this credit would land on already carries a discount from a programme whose own
    #: document forbids compounding. Two instruments, one bill, and only one of them may be taken:
    #: `DEC-004` does not rank a debt the shop owes this customer against an offer the shop chose to
    #: make, and this code will not rank them on the owner's behalf. Refused before the credit is
    #: touched, so it stays unredeemed and a person can spend it on a bill with no programme on it.
    REMEDY_CREDIT_PROMOTION_NOT_STACKABLE = "REMEDY_CREDIT_PROMOTION_NOT_STACKABLE"
    #: A late-delivery credit has already been proposed or paid for this order. `DEC-004` gives one
    #: 10% credit for one late delivery; a second incident about the same delivery, or a second
    #: proposal on the same incident, is the same lateness counted twice.
    REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED = "REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED"
    #: The revision this credit would be reserved on already carries it. A credit lands on a bill
    #: once; presenting it again against the same quote would discount the same bill twice.
    REMEDY_CREDIT_ALREADY_ON_QUOTE = "REMEDY_CREDIT_ALREADY_ON_QUOTE"


#: Which invariant or decision owns each refusal, in the shape `settlement.REFUSAL_DECISIONS` and
#: `range_prices.RANGE_PRICE_REFUSAL_AUTHORITIES` use. A caller is told what would have to change.
REMEDY_REFUSAL_AUTHORITIES: Final = {
    # Invariant 11: missing or unpublished policy fails closed.
    RemedyRefusal.REMEDY_POLICY_UNPUBLISHED: "INVARIANT-11",
    # DEC-004 itself: loss is carried forward as not yet decided, and must not inherit by analogy.
    RemedyRefusal.LOSS_POLICY_UNRESOLVED: "DEC-004",
    # DEC-004 set the ceiling; invariant 3 makes deterministic code, not a person, apply it.
    RemedyRefusal.REMEDY_CEILING_EXCEEDED: "DEC-004",
    RemedyRefusal.REMEDY_WINDOW_CLOSED: "DEC-004",
    RemedyRefusal.REMEDY_STORE_FAULT_NOT_ATTESTED: "DEC-004",
    RemedyRefusal.REMEDY_LATENESS_BELOW_THRESHOLD: "DEC-004",
    # Invariant 3: the server decides money from recorded facts. A fact it does not have is a stop.
    RemedyRefusal.REMEDY_WINDOW_EVIDENCE_MISSING: "INVARIANT-3",
    RemedyRefusal.REMEDY_AMOUNT_NOT_APPLICABLE: "INVARIANT-3",
    RemedyRefusal.REMEDY_LINE_NOT_PRICED: "INVARIANT-3",
    RemedyRefusal.REMEDY_ORDER_NOT_SETTLED: "INVARIANT-3",
    RemedyRefusal.REMEDY_DELIVERY_NOT_RECORDED: "INVARIANT-3",
    # Invariant 2: money is non-negative integer VND, so a discount may not exceed what is owed.
    RemedyRefusal.REMEDY_CREDIT_UNALLOCATABLE: "INVARIANT-2",
    # Invariant 4: an agreed revision is an immutable historical snapshot. DEC-010 keeps the
    # settlement path accepting only the exact quoted total, so a credit lands before agreement.
    RemedyRefusal.REMEDY_CREDIT_REVISION_NOT_OPEN: "INVARIANT-4",
    # DEC-015: there is no customer ledger, so the credit is a bearer instrument spent once.
    RemedyRefusal.REMEDY_CREDIT_ALREADY_REDEEMED: "DEC-015",
    # DEC-004 owns what a credit is worth and when it may be given. It does not say what happens
    # when the shop's own promotion forbids compounding with it, and the promotion engine's answer
    # to that question is REQUIRE_HUMAN. Naming DEC-004 as the authority is the caller being told
    # exactly what would have to change: the owner deciding which instrument wins, not this code.
    RemedyRefusal.REMEDY_CREDIT_PROMOTION_NOT_STACKABLE: "DEC-004",
    # DEC-004 gives one 10% credit per late delivery, not one per complaint about it.
    RemedyRefusal.REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED: "DEC-004",
    # DEC-015: a bearer credit is spent once, and it cannot discount one bill twice either.
    RemedyRefusal.REMEDY_CREDIT_ALREADY_ON_QUOTE: "DEC-015",
}


class RemedyPolicyError(ValueError):
    """Raised when a payload is not a publishable statement of `DEC-004`."""


@dataclass(frozen=True, slots=True)
class RemedyPolicy:
    """The owner's ratified figures, as read from one published configuration version.

    Every field is required and none has a default. A policy missing a figure is not a policy with
    a gap to fill in later -- it is a document that must not be published, which is why
    `parse_remedy_policy` raises rather than substituting anything.
    """

    #: How long after receiving their laundry the customer may report a visible defect.
    defect_report_window_hours: int
    #: How long the store has to propose an initial resolution after a report. Published because it
    #: is one of the six ratified figures; no code path enforces it yet, and none pretends to.
    initial_response_window_hours: int
    #: How long after pickup a free rewash may be requested.
    free_rewash_window_days: int
    #: How late a delivery must be before the credit applies. ">2 hours" in the decision.
    late_delivery_threshold_minutes: int
    #: The credit, in basis points of the settled total. 10% is 1000.
    late_delivery_credit_rate_bps: int
    #: Damage compensation cap, as a multiple of what the shop charged to clean that item.
    damage_compensation_multiple: int
    #: What a staff member may authorise without the owner.
    staff_approval_ceiling_vnd: int


def parse_remedy_policy(payload: Mapping[str, Any]) -> RemedyPolicy:
    """Read a published payload into typed figures, or refuse it.

    Run at publication time as the configuration type's registered validator *and* again at read
    time, so a document that somehow reached the table without passing is still refused at the
    counter rather than producing a number nobody ratified.
    """

    if not isinstance(payload, Mapping) or payload.get("schema") != REMEDY_POLICY_SCHEMA:
        raise RemedyPolicyError("remedy policy payload is not a remedy-policy-v1 document")
    if payload.get("decision") != "DEC-004":
        raise RemedyPolicyError("a remedy policy must name the decision it expresses")
    figures = {
        field: _policy_integer(payload, field)
        for field in (
            "defect_report_window_hours",
            "initial_response_window_hours",
            "free_rewash_window_days",
            "late_delivery_threshold_minutes",
            "late_delivery_credit_rate_bps",
            "damage_compensation_multiple",
            "staff_approval_ceiling_vnd",
        )
    }
    if not 0 < figures["late_delivery_credit_rate_bps"] <= RATE_DENOMINATOR:
        # A rate above 100% would make the shop owe more than the customer ever paid, which is not
        # a figure the owner could have meant and is not one this code will infer past.
        raise RemedyPolicyError("the late-delivery credit rate must be a rate")
    if figures["damage_compensation_multiple"] < 1:
        raise RemedyPolicyError("the damage compensation multiple must be at least one")
    for window in ("defect_report_window_hours", "initial_response_window_hours"):
        if figures[window] < 1:
            raise RemedyPolicyError("a report window must be at least one hour")
    if figures["free_rewash_window_days"] < 1:
        raise RemedyPolicyError("the rewash window must be at least one day")
    return RemedyPolicy(**figures)


def _policy_integer(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    # `bool` is excluded because `True` is an `int` in Python: without this, `"..._vnd": true`
    # would be read as 1 d and published as a liability ceiling.
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_JCS_INTEGER:
        raise RemedyPolicyError(f"remedy policy field {field} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class RemedyOrderFacts:
    """What the order already records. Every field is read from stored state, never from a request.

    Invariant 9 is the reason this is a separate type: a staff member may attest that the shop was
    at fault and how late a driver was, and may not tell the server what an order's line was priced
    at, when the laundry was collected, or what was settled.
    """

    #: When the customer received their laundry back: `orders.production_released_at` for a counter
    #: collection, or the succeeded `RETURN` leg's `recorded_at` for a delivery. `None` when the
    #: order records neither, which stops every window rather than defaulting one.
    goods_returned_at: datetime | None
    #: `order_settlements.paid_amount_vnd`, which the schema constrains to equal the quoted total.
    settled_total_vnd: int | None
    #: Each priced line of the order's current revision, by `line_id`, at its net amount -- what the
    #: shop actually charged for that item after any discount, which is what `DEC-004` caps against.
    line_amounts_vnd: Mapping[str, int]
    #: Whether the order's fulfilment mode expects the shop's courier to hand the laundry over.
    expects_return_leg: bool
    #: Whether a `RETURN` leg has actually succeeded.
    return_leg_succeeded: bool


@dataclass(frozen=True, slots=True)
class RemedyCommitments:
    """What earlier proposals have already committed against the thing this request is about.

    `DEC-004`'s two damage figures -- 5x the item's cleaning fee, and 100.000 d before the owner
    must decide -- are limits on what the shop pays **for one item**, not on what one form asks
    for. Checked per request, a claim split into three 80.000 d proposals passed the staff limit
    three times and paid 240.000 d with no owner; the lead reproduced exactly that. So every figure
    is compared against what was already committed *plus* what is asked for now.

    Server-derived and never a staff input (invariant 9), for the same reason `RemedyOrderFacts`
    is: the repository sums stored proposals under a row lock and passes the total in. This module
    decides from it and never computes it, so the answer is reproducible from the two numbers
    alone.

    "Committed" means every earlier proposal that can still pay or already paid. `RemedyStatus` has
    no terminal non-paying member; a proposal is dead only when the owner's `APPROVE_REMEDY`
    envelope it waits on has reached `REJECTED`, `EXPIRED` or `CANCELLED`, which the repository
    reads and this type does not need to know about.
    """

    #: Sum of `amount_vnd` over live-or-paid `DAMAGE_COMPENSATION` proposals on the same order line.
    line_committed_vnd: int
    #: How many live-or-paid `LATE_DELIVERY_CREDIT` proposals the order already has.
    late_delivery_credits: int

    def __post_init__(self) -> None:
        for value in (self.line_committed_vnd, self.late_delivery_credits):
            # Invariant 2, and `bool` excluded because `True` is an `int`. A negative "committed"
            # total would hand a request headroom nobody granted.
            if not _valid_amount(value):
                raise RemedyPolicyError("prior remedy commitments must be non-negative integers")


@dataclass(frozen=True, slots=True)
class RemedyRequest:
    """What a named staff member asked for. Only attested facts and a choice of kind."""

    kind: RemedyKind
    #: A staff member determined the store was at fault. `DEC-004` rests every remedy on this.
    store_fault_attested: bool
    #: Which priced line was damaged. Required for `DAMAGE_COMPENSATION` and forbidden otherwise.
    order_line_id: str | None = None
    #: The compensation asked for. Required for `DAMAGE_COMPENSATION` and forbidden otherwise -- the
    #: late-delivery credit is computed by the server, and the other two move no money.
    amount_vnd: int | None = None
    #: How late the delivery was, attested by the staff member who handled it. The shop records no
    #: promised arrival time, so this cannot be derived; what *is* checkable -- that a return leg
    #: happened at all -- is checked against the record below.
    attested_late_by_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class RemedyAuthorized:
    """This remedy may be recorded, with the ceiling and window the server computed for it."""

    kind: RemedyKind
    amount_vnd: int | None
    direction: AdjustmentDirection | None
    ceiling_vnd: int | None
    window_opened_at: datetime | None
    window_closes_at: datetime | None
    requires_owner_approval: bool
    outcome: PolicyOutcome = PolicyOutcome.ALLOW


@dataclass(frozen=True, slots=True)
class RemedyUnresolved:
    """Accepted as a record and stopped, because the owner has not decided this case.

    Only `LOST_ITEM` reaches here. It carries no amount, no ceiling and no window, and there is
    deliberately nothing on this type that a caller could read a figure out of.
    """

    kind: RemedyKind
    refusal: RemedyRefusal
    outcome: PolicyOutcome = PolicyOutcome.REQUIRE_HUMAN

    @property
    def reason_code(self) -> str:
        return self.refusal.value

    @property
    def authority(self) -> str:
        return REMEDY_REFUSAL_AUTHORITIES[self.refusal]


@dataclass(frozen=True, slots=True)
class RemedyRefused:
    """Refused, with the number that would have made it allowable where one exists."""

    refusal: RemedyRefusal
    ceiling_vnd: int | None = None
    window_closes_at: datetime | None = None
    threshold_minutes: int | None = None
    #: What earlier proposals had already committed against the same item, when that is why the
    #: ceiling was reached. Staff need both numbers to tell a customer what is still possible.
    committed_vnd: int | None = None
    outcome: PolicyOutcome = PolicyOutcome.DENY

    @property
    def reason_code(self) -> str:
        return self.refusal.value

    @property
    def authority(self) -> str:
        return REMEDY_REFUSAL_AUTHORITIES[self.refusal]


RemedyOutcome = RemedyAuthorized | RemedyUnresolved | RemedyRefused


def evaluate_remedy(
    *,
    policy: RemedyPolicy,
    facts: RemedyOrderFacts,
    request: RemedyRequest,
    requested_at: datetime,
    committed: RemedyCommitments,
) -> RemedyOutcome:
    """Decide one remedy request against the published figures and the order's recorded facts.

    Loss is answered first and alone, before fault, window, amount or ceiling are looked at. That
    ordering is the point: every later branch reads a published figure, and the figures do not apply
    to loss. A loss case that reached them would be answered by analogy, which is exactly what the
    decision packet forbids without asking the owner first.

    `committed` is required and has no default, deliberately: a caller that has not summed what the
    item already carries has not asked the question `DEC-004` asks. See `RemedyCommitments`.
    """

    _require_aware(requested_at)
    if request.kind is RemedyKind.LOST_ITEM:
        return RemedyUnresolved(RemedyKind.LOST_ITEM, RemedyRefusal.LOSS_POLICY_UNRESOLVED)

    shape = _refuse_wrong_shape(request)
    if shape is not None:
        return shape
    if not request.store_fault_attested:
        return RemedyRefused(RemedyRefusal.REMEDY_STORE_FAULT_NOT_ATTESTED)

    if request.kind is RemedyKind.LATE_DELIVERY_CREDIT:
        return _evaluate_late_delivery(policy, facts, request, committed)

    window = (
        timedelta(days=policy.free_rewash_window_days)
        if request.kind is RemedyKind.FREE_REWASH
        else timedelta(hours=policy.defect_report_window_hours)
    )
    opened_at = facts.goods_returned_at
    if opened_at is None:
        return RemedyRefused(RemedyRefusal.REMEDY_WINDOW_EVIDENCE_MISSING)
    closes_at = opened_at + window
    if requested_at > closes_at:
        # Inclusive at the far end: a request exactly seven days after pickup is inside the window
        # the owner published, and a shop that turns somebody away one second early is not applying
        # the policy it wrote down.
        return RemedyRefused(RemedyRefusal.REMEDY_WINDOW_CLOSED, window_closes_at=closes_at)

    if request.kind is RemedyKind.FREE_REWASH:
        # No money moves, so there is no ceiling to compute and no threshold to cross. The record is
        # the authority, the fault finding and the window -- which is what was missing.
        return RemedyAuthorized(
            kind=request.kind,
            amount_vnd=None,
            direction=None,
            ceiling_vnd=None,
            window_opened_at=opened_at,
            window_closes_at=closes_at,
            requires_owner_approval=False,
        )

    assert request.kind is RemedyKind.DAMAGE_COMPENSATION
    assert request.order_line_id is not None and request.amount_vnd is not None
    line_amount = facts.line_amounts_vnd.get(request.order_line_id)
    if line_amount is None:
        return RemedyRefused(RemedyRefusal.REMEDY_LINE_NOT_PRICED)
    # `DEC-004`: capped at 5x *that item's* cleaning fee -- what the store charged, not retail or
    # replacement value. The multiple is published; the fee is the order's own stored line amount.
    ceiling = line_amount * policy.damage_compensation_multiple
    # Both figures are about the item, so both are compared against the item's running total: what
    # earlier proposals on this line already committed, plus this one. Owner approval answers the
    # staff limit; it is not a way past the item's own ceiling, so the ceiling is checked first and
    # regardless of who would approve.
    total = committed.line_committed_vnd + request.amount_vnd
    if total > ceiling:
        return RemedyRefused(
            RemedyRefusal.REMEDY_CEILING_EXCEEDED,
            ceiling_vnd=ceiling,
            committed_vnd=committed.line_committed_vnd,
        )
    return RemedyAuthorized(
        kind=request.kind,
        amount_vnd=request.amount_vnd,
        direction=AdjustmentDirection.CREDIT,
        ceiling_vnd=ceiling,
        window_opened_at=opened_at,
        window_closes_at=closes_at,
        # Inclusive, as it always was: "staff may approve up to 100.000 d" -- now of the item's
        # total, so a claim split into pieces reaches the owner exactly when the whole would have.
        requires_owner_approval=total > policy.staff_approval_ceiling_vnd,
    )


def _evaluate_late_delivery(
    policy: RemedyPolicy,
    facts: RemedyOrderFacts,
    request: RemedyRequest,
    committed: RemedyCommitments,
) -> RemedyOutcome:
    """The 10% credit, computed by the server from the settled total.

    No elapsed-time window applies: `DEC-004` gates this one on the lateness and the fault rather
    than on how long ago it happened. What is gated is that a delivery this system recorded actually
    took place -- an order the customer collected at the counter cannot have been delivered late,
    and refusing that contradiction is the only part of the lateness the shop can check.
    """

    assert request.attested_late_by_minutes is not None
    if committed.late_delivery_credits:
        # One late delivery, one credit. Checked first because it is the fact that decides: every
        # later check would pass again for the same delivery and compute the same 10% again.
        return RemedyRefused(RemedyRefusal.REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED)
    if not (facts.expects_return_leg and facts.return_leg_succeeded):
        return RemedyRefused(RemedyRefusal.REMEDY_DELIVERY_NOT_RECORDED)
    if request.attested_late_by_minutes <= policy.late_delivery_threshold_minutes:
        return RemedyRefused(
            RemedyRefusal.REMEDY_LATENESS_BELOW_THRESHOLD,
            threshold_minutes=policy.late_delivery_threshold_minutes,
        )
    if facts.settled_total_vnd is None:
        return RemedyRefused(RemedyRefusal.REMEDY_ORDER_NOT_SETTLED)
    credit = _round_half_up(facts.settled_total_vnd * policy.late_delivery_credit_rate_bps)
    return RemedyAuthorized(
        kind=request.kind,
        amount_vnd=credit,
        direction=AdjustmentDirection.CREDIT,
        # The computed 10% is simultaneously the amount and its own ceiling: staff supplied no
        # number, so there is nothing for a bound to refuse. It is reported so the console can show
        # what the figure was derived from before anyone commits to it.
        ceiling_vnd=credit,
        window_opened_at=None,
        window_closes_at=None,
        requires_owner_approval=credit > policy.staff_approval_ceiling_vnd,
    )


def _refuse_wrong_shape(request: RemedyRequest) -> RemedyRefused | None:
    """Refuse a request whose fields do not belong to the kind it names.

    Each kind owns exactly one shape, and a field that does not belong is refused rather than
    ignored. Ignoring it would let a staff member type an amount for a rewash, watch it be accepted,
    and reasonably believe the shop had agreed to pay it.
    """

    kind = request.kind
    if kind is RemedyKind.DAMAGE_COMPENSATION:
        if (
            request.order_line_id is None
            or not _valid_amount(request.amount_vnd)
            or request.attested_late_by_minutes is not None
        ):
            return RemedyRefused(RemedyRefusal.REMEDY_AMOUNT_NOT_APPLICABLE)
        return None
    if kind is RemedyKind.LATE_DELIVERY_CREDIT:
        if (
            request.amount_vnd is not None
            or request.order_line_id is not None
            or not isinstance(request.attested_late_by_minutes, int)
            or isinstance(request.attested_late_by_minutes, bool)
            or request.attested_late_by_minutes < 0
        ):
            return RemedyRefused(RemedyRefusal.REMEDY_AMOUNT_NOT_APPLICABLE)
        return None
    if request.amount_vnd is not None or request.order_line_id is not None:
        return RemedyRefused(RemedyRefusal.REMEDY_AMOUNT_NOT_APPLICABLE)
    if request.attested_late_by_minutes is not None:
        return RemedyRefused(RemedyRefusal.REMEDY_AMOUNT_NOT_APPLICABLE)
    return None


def remedy_proposal_document(
    *,
    proposal_id: UUID,
    incident_id: UUID,
    order_id: UUID,
    authorized: RemedyAuthorized,
    policy_version_id: UUID,
    policy_version: int,
    order_line_id: str | None,
    proposed_at: datetime,
) -> CanonicalDocument:
    """The exact content an `APPROVE_REMEDY` envelope binds. Invariant 8.

    The proposal's own identity, the incident it answers, the order it is against, the amount, the
    ceiling it was checked against and the policy version that ceiling came from are all inside the
    hashed document. An owner approving 150.000 d of damage on one incident therefore cannot have
    that approval reused for another incident, another amount, or the same amount computed against a
    policy version the owner never saw -- every one of those changes the digest.
    """

    return canonical_document(
        {
            "schema": REMEDY_PROPOSAL_DOCUMENT_SCHEMA,
            "proposal_id": str(proposal_id),
            "incident_id": str(incident_id),
            "order_id": str(order_id),
            "kind": authorized.kind.value,
            "amount_vnd": authorized.amount_vnd,
            "direction": None if authorized.direction is None else authorized.direction.value,
            "ceiling_vnd": authorized.ceiling_vnd,
            "order_line_id": order_line_id,
            "policy_version_id": str(policy_version_id),
            "policy_version": policy_version,
            "policy_rule_version": REMEDY_POLICY_VERSION,
            "proposed_at": proposed_at.isoformat(),
        }
    )


@dataclass(frozen=True, slots=True)
class RemedyCredit:
    """One issued credit, as the quote composition needs to see it.

    Deliberately not the persistence row. What lands on a quote is an amount, the policy version the
    amount was computed under, the envelope that authorised it where one was required, and an
    identifier to spend exactly once -- and nothing about who the customer is, because `DEC-015`
    refuses to build a customer record and a credit is therefore a bearer instrument, redeemed by
    presenting the ticket the original order carried.
    """

    credit_id: UUID
    amount_vnd: int
    policy_version_id: UUID
    approval_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CreditAllocation:
    """One credit spread across the lines it lands on, and the arithmetic that spread it."""

    #: `line_id` -> the whole-dong share of the credit that line absorbs.
    per_line_vnd: Mapping[str, int]
    total_vnd: int
    allocation: LargestRemainderAllocation

    @property
    def rounding(self) -> str:
        return LARGEST_REMAINDER_RULE


def allocate_remedy_credit(
    *, line_weights_vnd: Mapping[str, int], credit_vnd: int
) -> CreditAllocation | RemedyRefused:
    """Spread an order-level credit across the lines of the quote it lands on.

    A remedy credit is a fact about an order; `quote_revisions` records discounts per line and a
    database CHECK ties `net_service_subtotal` to `list_service_subtotal - discount`, so an
    order-level credit has to become line-level before it can be stored at all. That is exactly the
    problem `promotion._allocate_group` already solved, and `allocate_largest_remainder` is that
    same allocator extracted so both callers give the same answer to "who gets the spare dong".
    Writing a second one would put two answers in one system.

    A credit larger than what is left to discount is refused and carried, never capped: capping it
    would cancel part of a debt the shop owes without anybody deciding to.
    """

    if not _valid_amount(credit_vnd) or credit_vnd == 0:
        return RemedyRefused(RemedyRefusal.REMEDY_CREDIT_UNALLOCATABLE)
    weights = tuple(sorted(line_weights_vnd.items()))
    available = sum(weight for _, weight in weights)
    if not weights or available <= 0 or credit_vnd > available:
        return RemedyRefused(
            RemedyRefusal.REMEDY_CREDIT_UNALLOCATABLE, ceiling_vnd=max(available, 0)
        )
    allocation = allocate_largest_remainder(
        weights=weights,
        multiplier=credit_vnd,
        denominator=available,
        total_vnd=credit_vnd,
    )
    return CreditAllocation(
        per_line_vnd=dict(
            zip(allocation.ordered_ids, allocation.final_allocations_vnd, strict=True)
        ),
        total_vnd=credit_vnd,
        allocation=allocation,
    )


def _round_half_up(numerator: int) -> int:
    """Basis points to whole dong, half up -- the same rule `promotion._allocate_group` applies."""

    return (numerator + RATE_DENOMINATOR // 2) // RATE_DENOMINATOR


def _valid_amount(value: object) -> bool:
    """Non-negative integer VND. Invariant 2, with `bool` excluded because `True` is an `int`."""

    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_JCS_INTEGER


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RemedyPolicyError("a remedy must be evaluated against an aware timestamp")


#: Nothing committed yet: the first proposal against an item or an order. Defined after
#: `_valid_amount`, which `RemedyCommitments` validates with.
NO_PRIOR_COMMITMENTS: Final = RemedyCommitments(line_committed_vnd=0, late_delivery_credits=0)


__all__ = [
    "MAX_JCS_INTEGER",
    "NO_PRIOR_COMMITMENTS",
    "REMEDY_CREDIT_APPLIED",
    "REMEDY_CREDIT_REASON_CODE",
    "REMEDY_POLICY_CONFIG_TYPE",
    "REMEDY_POLICY_SCHEMA",
    "REMEDY_POLICY_VERSION",
    "REMEDY_PROPOSAL_DOCUMENT_SCHEMA",
    "REMEDY_REFUSAL_AUTHORITIES",
    "CreditAllocation",
    "RemedyCommitments",
    "RemedyCredit",
    "RemedyKind",
    "RemedyOrderFacts",
    "RemedyOutcome",
    "RemedyPolicy",
    "RemedyPolicyError",
    "RemedyRefusal",
    "RemedyRefused",
    "RemedyRequest",
    "RemedyStatus",
    "RemedyUnresolved",
    "allocate_remedy_credit",
    "evaluate_remedy",
    "parse_remedy_policy",
    "remedy_proposal_document",
]
