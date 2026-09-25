"""Business steps over the order state machines: what may happen next, and what a step is made of.

`ORDER-STEPS-001`. A walk-in order used to need about sixteen manual state commands on three axes
before the customer walked out with their laundry, and the console chose among them from dropdowns.
The staff task is not "move intake to RECEIVED_PENDING_INSPECTION"; it is *nhận đồ*, *bắt đầu giặt*,
*báo sẵn sàng*, *thu tiền*, *giao đồ*. This module names those tasks and says, for one order, which
of them are legal now and which one is the natural next real-world event.

It holds **no second transition table**. Every answer is produced by running the real domain
functions -- `transition_commercial`, `transition_intake`, `transition_production`,
`evaluate_settlement`, `evaluate_collection`, `handover_refusal` -- against the order's stored
facts. A composite step is a *plan*: the ordered list of single-axis transitions that
`OrderRepository` then writes one at a time, each with its own event, audit and outbox rows, inside
one database transaction. `plan_step` builds that list by applying each transition to a simulated
state as it goes, so the list it returns is exactly the list the domain has already accepted, and
`next_steps` is nothing but `plan_step` (and the money rules) asked in advance.

Money is never computed here. `TAKE_PAYMENT` (`PAYMENT-001`, `DEC-035`) is legal while money is
still owed on a running order whose quote presents a single total -- the balance reads `UNPAID` or
`PARTIALLY_PAID`, which `0056` keeps equal to "owed > paid" -- and it is executed on the payments
route, where the amount, the method and the customer's word are recorded.

Pure: no clock, no database, no environment. The one instant a plan needs (when production accepted
the goods) is a parameter.

The vocabulary
--------------

Composite steps, executed by ``POST /internal/v1/orders/{id}/steps``:

* ``RECEIVE`` -- goods received and accepted for work. Intake -> RECEIVED_PENDING_INSPECTION (if
  still AWAITING_HANDOFF); commercial -> STORE_CONFIRMATION_PENDING -> CONFIRMED (whatever
  remains); intake -> ACCEPTED with the caller's explicit ``slot_approved`` and the five
  server-derived readiness facts; commercial -> ACTIVE. Tolerates an order the per-axis routes
  already partly advanced.
* ``START_WASH`` -- production -> QUEUED (if NOT_STARTED) -> IN_PROCESS.
* ``QUALITY_CHECK`` -- production IN_PROCESS -> QUALITY_CHECK.
* ``MARK_READY`` -- production QUALITY_CHECK -> READY_AT_STORE.
* ``HOLD`` -- production -> ON_HOLD, from any started, unreleased state.
* ``RESUME`` -- production ON_HOLD / EXCEPTION -> the state it interrupted.
* ``RELEASE`` -- production READY_AT_STORE -> RELEASED: the goods leave the shop.
* ``HAND_OVER`` -- production READY_AT_STORE -> RELEASED, then commercial -> COMPLETED; legal only
  when the balance is settled and the fulfilment is recorded.
* ``COMPLETE`` -- commercial ACTIVE -> COMPLETED, production already RELEASED.
* ``CANCEL`` -- before ACTIVE, commercial -> CANCELLED directly (the domain refuses it once work
  has begun); on an ACTIVE order, commercial -> CANCELLATION_REVIEW -> CANCELLED with
  ``custody_resolution``; from CANCELLATION_REVIEW, -> CANCELLED with ``custody_resolution``.
* ``REOPEN`` -- commercial CANCELLATION_REVIEW -> ACTIVE: the cancellation is withdrawn.
* ``REWASH`` -- ``ORDER-STEPS-002``, founder ruling R1. Laundry found wanting at quality check or on
  the shelf, before it leaves, is washed again inside the same order: production -> EXCEPTION
  (interrupting where it was), then EXCEPTION -> IN_PROCESS -- backwards, which only an exception
  permits (`transition_production`'s DEC-024 branch). Legal only on an ACTIVE order at
  QUALITY_CHECK or READY_AT_STORE whose customer has not been recorded as taking the goods; it
  requires ``rewash_reason``. The price is untouched: a rewash costs the customer nothing.
* ``REJECT_INTAKE`` -- ``ORDER-STEPS-002``, founder ruling R2. The shop refuses laundry that is on
  the counter but not yet accepted for work: intake -> REJECTED, then commercial -> CANCELLED
  directly (REJECTED is in `CUSTODY_NOT_HELD_INTAKE_STATUSES`, so the domain's "work has begun"
  guard admits it). Legal only while intake is RECEIVED_PENDING_INSPECTION, WAITING_PRICE_APPROVAL,
  WAITING_CUSTOMER_RECONFIRMATION or WAITING_SLOT_APPROVAL and commercial is before ACTIVE; it
  requires ``rejection_reason``. No money moves: none can have, prepayment needs an active order.

Neither ``REWASH`` nor ``REJECT_INTAKE`` is ever the primary step. They are exceptions to the day's
flow, offered beside it, and the reason each records is on the first transition's event and audit.

Steps served by their own existing routes, listed so the console has one source of the next action:

* ``TAKE_PAYMENT`` -- ``POST /orders/{id}/payments`` (``PAYMENT-001``, ``DEC-035``): a deposit, a
  part payment or the rest, with its method. It replaced ``SETTLE`` and ``PREPAY``: both were the
  exact total and differed only in whether the customer took the goods at once, which the payment
  that settles the order still records (``collected_by_customer``). The exact-total settlement route
  is unchanged for older clients; the step list no longer offers it by name.
* ``COLLECT`` -- ``POST /orders/{id}/collection``: a customer who has paid in full takes the goods.
* ``DELIVERY_PICKUP`` -- ``POST /orders/{id}/delivery-legs`` with ``leg_kind=PICKUP``.
* ``DELIVERY_RETURN`` -- ``POST /orders/{id}/delivery-legs`` with ``leg_kind=RETURN``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from nha_trang_laundry_domain.catalog import (
    MODES_EXPECTING_PICKUP,
    MODES_EXPECTING_RETURN,
    CommercialOrderStatus,
    CustodyResolution,
    IntakeRejectionReason,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
    RewashReason,
)
from nha_trang_laundry_domain.orders import (
    TERMINAL_COMMERCIAL_STATUSES,
    IntakeReadiness,
    OrderState,
    OrderTransitionError,
    transition_commercial,
    transition_intake,
    transition_production,
)
from nha_trang_laundry_domain.settlement import (
    QuotedTotal,
    SettlementAccepted,
    SettlementShape,
    evaluate_collection,
    evaluate_settlement,
    goods_may_leave,
    handover_refusal,
)


class OrderStep(StrEnum):
    """A named staff task on one order. See the module docstring for what each one does."""

    RECEIVE = "RECEIVE"
    START_WASH = "START_WASH"
    QUALITY_CHECK = "QUALITY_CHECK"
    MARK_READY = "MARK_READY"
    HOLD = "HOLD"
    RESUME = "RESUME"
    REWASH = "REWASH"
    RELEASE = "RELEASE"
    HAND_OVER = "HAND_OVER"
    COMPLETE = "COMPLETE"
    CANCEL = "CANCEL"
    REJECT_INTAKE = "REJECT_INTAKE"
    REOPEN = "REOPEN"
    TAKE_PAYMENT = "TAKE_PAYMENT"
    COLLECT = "COLLECT"
    DELIVERY_PICKUP = "DELIVERY_PICKUP"
    DELIVERY_RETURN = "DELIVERY_RETURN"


#: The steps `POST /orders/{id}/steps` executes: sequences of order state transitions.
COMPOSITE_STEPS: Final = frozenset(
    {
        OrderStep.RECEIVE,
        OrderStep.START_WASH,
        OrderStep.QUALITY_CHECK,
        OrderStep.MARK_READY,
        OrderStep.HOLD,
        OrderStep.RESUME,
        OrderStep.REWASH,
        OrderStep.RELEASE,
        OrderStep.HAND_OVER,
        OrderStep.COMPLETE,
        OrderStep.CANCEL,
        OrderStep.REJECT_INTAKE,
        OrderStep.REOPEN,
    }
)

#: `ORDER-STEPS-002`: steps that are never the primary step, whatever else is legal. A rewash and a
#: refusal are exceptions a person decides on, not the natural next event; a big button inviting
#: either would be pressed by a thumb looking for the ordinary one.
NEVER_PRIMARY_STEPS: Final = frozenset({OrderStep.REWASH, OrderStep.REJECT_INTAKE})

#: Where a rewash may start (founder ruling R1): the laundry has been washed and is being checked,
#: or is finished and waiting on the shelf. Earlier, it is still being washed (HOLD is the
#: interruption); later, it has left the shop and a bring-back is a complaint (`DEC-004`).
REWASH_FROM_PRODUCTION: Final = frozenset(
    {ProductionStatus.QUALITY_CHECK, ProductionStatus.READY_AT_STORE}
)

#: Where goods may be refused (founder ruling R2): received and on the counter, not yet accepted.
#: Never AWAITING_HANDOFF (nothing was received: that is a plain cancellation) and never ACCEPTED
#: (the shop took the work on: that is a cancellation through review).
REJECT_INTAKE_FROM: Final = frozenset(
    {
        IntakeStatus.RECEIVED_PENDING_INSPECTION,
        IntakeStatus.WAITING_PRICE_APPROVAL,
        IntakeStatus.WAITING_CUSTOMER_RECONFIRMATION,
        IntakeStatus.WAITING_SLOT_APPROVAL,
    }
)

#: The commercial states before the order is live. `REJECT_INTAKE` is legal only in these.
_COMMERCIAL_BEFORE_ACTIVE: Final = frozenset(
    {
        CommercialOrderStatus.DRAFT,
        CommercialOrderStatus.REQUESTED,
        CommercialOrderStatus.STORE_CONFIRMATION_PENDING,
        CommercialOrderStatus.CONFIRMED,
    }
)

#: The order in which steps are listed. Also the tie-break order for the primary step's fallback.
STEP_ORDER: Final = tuple(OrderStep)


@dataclass(frozen=True, slots=True)
class QuoteReadinessFacts:
    """The four intake readiness facts the bound quote revision holds (`IntakeReadiness`).

    Read by the server from the order's bound revision, never received from a client: a caller
    asserting "an exact price was approved" would be a caller asserting server state.
    """

    quantity_basis_approved: bool
    service_classified: bool
    exact_price_approved: bool
    customer_reconfirmation_satisfied: bool


def derive_intake_readiness(
    intake: IntakeStatus, quote: QuoteReadinessFacts, *, slot_approved: bool
) -> IntakeReadiness:
    """The six readiness facts: custody from the intake state, four from the quote, one attested.

    The one derivation both the per-axis intake route and the `RECEIVE` step use. Custody means the
    laundry is physically here, which is what leaving `AWAITING_HANDOFF` means. `slot_approved` is
    the operator's word, because capacity is a human judgement this system never makes.
    """

    return IntakeReadiness(
        custody_recorded=intake is not IntakeStatus.AWAITING_HANDOFF,
        quantity_basis_approved=quote.quantity_basis_approved,
        service_classified=quote.service_classified,
        exact_price_approved=quote.exact_price_approved,
        customer_reconfirmation_satisfied=quote.customer_reconfirmation_satisfied,
        slot_approved=slot_approved,
    )


#: Why intake cannot be accepted, one code per missing readiness fact, in `IntakeReadiness` order.
READINESS_BLOCKER_CODES: Final = (
    ("custody_recorded", "CUSTODY_NOT_RECORDED"),
    ("quantity_basis_approved", "QUANTITY_NOT_MEASURED"),
    ("service_classified", "SERVICE_NOT_CLASSIFIED"),
    ("exact_price_approved", "EXACT_PRICE_NOT_APPROVED"),
    ("customer_reconfirmation_satisfied", "CUSTOMER_AGREEMENT_MISSING"),
    ("slot_approved", "SLOT_APPROVAL_REQUIRED"),
)


def readiness_blockers(readiness: IntakeReadiness) -> tuple[str, ...]:
    """The reason codes for every readiness fact that is not true; empty when intake may accept."""

    return tuple(code for field, code in READINESS_BLOCKER_CODES if not getattr(readiness, field))


@dataclass(frozen=True, slots=True)
class StepFacts:
    """Everything a step decision reads about one order. Every field is a stored fact.

    `quoted_total` is the bound quote revision's display bounds, the same two columns
    `SettlementRepository.record` checks a payment against. `settlement_shape` is the order's
    settlement row, or `None` when there is none. `pickup_leg_succeeded` is whether a succeeded
    `PICKUP` delivery leg exists; the `RETURN` side is `state.required_delivery_legs_succeeded`.
    """

    state: OrderState
    quote_readiness: QuoteReadinessFacts
    quoted_total: QuotedTotal
    settlement_shape: SettlementShape | None
    pickup_leg_succeeded: bool


@dataclass(frozen=True, slots=True)
class PlannedTransition:
    """One single-axis transition of a composite step, already accepted by the domain."""

    commercial_target: CommercialOrderStatus | None = None
    intake_target: IntakeStatus | None = None
    production_target: ProductionStatus | None = None
    intake_readiness: IntakeReadiness | None = None
    custody_resolution: CustodyResolution | None = None
    #: `ORDER-STEPS-002`: the reason a `REWASH` / `REJECT_INTAKE` records. Set on the step's first
    #: transition only, so the event that starts the step says why, once.
    rewash_reason: RewashReason | None = None
    rejection_reason: IntakeRejectionReason | None = None


@dataclass(frozen=True, slots=True)
class NextStep:
    """One legal step for the order as it stands.

    `requires` names the request fields the caller must supply for the step to be accepted:
    `slot_approved` for `RECEIVE` (the operator attests capacity), `custody_resolution` for a
    cancellation that goes through review, `rewash_reason` for `REWASH`, `rejection_reason` for
    `REJECT_INTAKE`. `custody_resolutions`, `rewash_reasons` and `rejection_reasons` list exactly
    the answers the domain would accept for this order -- each one dry-run -- so the console offers
    only answers the server will take.
    """

    step: OrderStep
    primary: bool
    requires: tuple[str, ...] = ()
    custody_resolutions: tuple[CustodyResolution, ...] = ()
    rewash_reasons: tuple[RewashReason, ...] = ()
    rejection_reasons: tuple[IntakeRejectionReason, ...] = ()


class StepRequiresHuman(OrderTransitionError):
    """A step a person must unblock first, with one reason code per missing fact.

    Raised for `RECEIVE` when intake readiness is incomplete -- most often because the operator did
    not attest `slot_approved`. The whole step is refused; nothing is partially applied.
    """

    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        super().__init__(
            "HUMAN_APPROVAL_REQUIRED: intake blockers remain: " + ", ".join(reason_codes)
        )
        self.reason_codes = reason_codes


#: The instant `next_steps` hands `plan_step` for a dry run. `transition_intake` requires only that
#: an acceptance time is present and timezone-aware; the value is never stored or compared.
_DRY_RUN_INSTANT: Final = datetime(2000, 1, 1, tzinfo=UTC)

_COMMERCIAL_TO_CONFIRMED: Final = (
    CommercialOrderStatus.REQUESTED,
    CommercialOrderStatus.STORE_CONFIRMATION_PENDING,
    CommercialOrderStatus.CONFIRMED,
)

_NOTHING_TO_DO: Final = "INVALID_STATE_TRANSITION: this step has nothing to do for the order now"


class _Simulation:
    """Apply domain transitions to a copy of the state, recording each one as it is accepted."""

    def __init__(self, state: OrderState) -> None:
        self.state = state
        self.planned: list[PlannedTransition] = []

    def commercial(
        self, target: CommercialOrderStatus, resolution: CustodyResolution | None = None
    ) -> None:
        resolved = resolution is not None
        self.state = transition_commercial(
            self.state,
            target,
            cancellation_approved=resolved,
            custody_and_financial_resolution_recorded=resolved,
            custody_resolution=resolution,
        )
        self.planned.append(
            PlannedTransition(commercial_target=target, custody_resolution=resolution)
        )

    def intake(
        self,
        target: IntakeStatus,
        *,
        readiness: IntakeReadiness | None = None,
        accepted_at: datetime | None = None,
    ) -> None:
        self.state = transition_intake(
            self.state, target, readiness=readiness, production_accepted_at=accepted_at
        )
        self.planned.append(PlannedTransition(intake_target=target, intake_readiness=readiness))

    def production(self, target: ProductionStatus) -> None:
        self.state = transition_production(self.state, target)
        self.planned.append(PlannedTransition(production_target=target))


def plan_step(
    step: OrderStep,
    facts: StepFacts,
    *,
    slot_approved: bool,
    custody_resolution: CustodyResolution | None,
    accepted_at: datetime,
    rewash_reason: RewashReason | None = None,
    rejection_reason: IntakeRejectionReason | None = None,
) -> tuple[PlannedTransition, ...]:
    """The single-axis transitions `step` is made of for this order, or a refusal.

    Every transition is applied to a simulated state by the real domain function before the next is
    planned, so a refusal anywhere refuses the whole step and the caller writes nothing. Raises
    `OrderTransitionError` (its message carries the domain's own `INVALID_STATE_TRANSITION:` /
    `HUMAN_APPROVAL_REQUIRED:` / `VALIDATION_ERROR:` prefix) or its subclass `StepRequiresHuman`.

    A reason is taken only by the step it belongs to, and that step refuses to run without it: a
    reason attached to a different step would be recorded nowhere, and silently dropping what a
    person said is how a ledger stops being believed.
    """

    if step not in COMPOSITE_STEPS:
        raise OrderTransitionError(
            f"INVALID_STATE_TRANSITION: {step.value} is recorded on its own route, not as a step"
        )
    if rewash_reason is not None and step is not OrderStep.REWASH:
        raise OrderTransitionError("VALIDATION_ERROR: rewash_reason is taken only by REWASH")
    if rejection_reason is not None and step is not OrderStep.REJECT_INTAKE:
        raise OrderTransitionError(
            "VALIDATION_ERROR: rejection_reason is taken only by REJECT_INTAKE"
        )
    if custody_resolution is not None and step in NEVER_PRIMARY_STEPS:
        raise OrderTransitionError(
            f"VALIDATION_ERROR: custody_resolution is not taken by {step.value}"
        )
    simulation = _Simulation(facts.state)
    state = facts.state
    if step is OrderStep.RECEIVE:
        _plan_receive(simulation, facts, slot_approved=slot_approved, accepted_at=accepted_at)
    elif step is OrderStep.START_WASH:
        # Founder ruling 2026-09-25 (redesign spec §6.1): washing starts on a live order only.
        # `transition_production` alone admits it at CONFIRMED-with-intake-ACCEPTED and during
        # CANCELLATION_REVIEW, which is exactly when nobody should be putting laundry in a machine.
        if state.commercial is not CommercialOrderStatus.ACTIVE:
            raise OrderTransitionError(
                "INVALID_STATE_TRANSITION: washing starts only on an active order"
            )
        if state.production not in {ProductionStatus.NOT_STARTED, ProductionStatus.QUEUED}:
            # Pinned, because the domain would also accept `ON_HOLD -> IN_PROCESS` (that is
            # RESUME) and `EXCEPTION -> IN_PROCESS` (a rewash, which is not this step's meaning).
            _require_production(state, ProductionStatus.QUEUED)
        if state.production is ProductionStatus.NOT_STARTED:
            simulation.production(ProductionStatus.QUEUED)
        simulation.production(ProductionStatus.IN_PROCESS)
    elif step is OrderStep.QUALITY_CHECK:
        _require_production(state, ProductionStatus.IN_PROCESS)
        simulation.production(ProductionStatus.QUALITY_CHECK)
    elif step is OrderStep.MARK_READY:
        _require_production(state, ProductionStatus.QUALITY_CHECK)
        simulation.production(ProductionStatus.READY_AT_STORE)
    elif step is OrderStep.HOLD:
        simulation.production(ProductionStatus.ON_HOLD)
    elif step is OrderStep.RESUME:
        if state.production not in {ProductionStatus.ON_HOLD, ProductionStatus.EXCEPTION}:
            raise OrderTransitionError(_NOTHING_TO_DO)
        if state.production_resume_status is None:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: invalid hold resume target")
        simulation.production(state.production_resume_status)
    elif step is OrderStep.RELEASE:
        # Founder ruling 2026-09-25: goods a customer collects in person do not leave an unpaid
        # order by this step. The counter's way out is TAKE_PAYMENT then HAND_OVER, which takes
        # the money in the same visit; a delivery order is released to the courier after its
        # DEC-023 prepayment, and a courier never takes money. `DEC-035` widened "unpaid" to "not
        # paid in full": a deposit does not let the goods leave (`goods_may_leave`, the seam where
        # PAYMENT-002's account customers will be admitted).
        if state.fulfillment_mode not in MODES_EXPECTING_RETURN and not goods_may_leave(
            state.balance
        ):
            raise OrderTransitionError(
                "INVALID_STATE_TRANSITION: an order the customer collects is released only once "
                "it is paid in full"
            )
        _require_production(state, ProductionStatus.READY_AT_STORE)
        simulation.production(ProductionStatus.RELEASED)
    elif step is OrderStep.HAND_OVER:
        if state.production is not ProductionStatus.RELEASED:
            _require_production(state, ProductionStatus.READY_AT_STORE)
            simulation.production(ProductionStatus.RELEASED)
        simulation.commercial(CommercialOrderStatus.COMPLETED)
    elif step is OrderStep.COMPLETE:
        simulation.commercial(CommercialOrderStatus.COMPLETED)
    elif step is OrderStep.CANCEL:
        _plan_cancel(simulation, custody_resolution)
    elif step is OrderStep.REOPEN:
        if state.commercial is not CommercialOrderStatus.CANCELLATION_REVIEW:
            raise OrderTransitionError(_NOTHING_TO_DO)
        simulation.commercial(CommercialOrderStatus.ACTIVE)
    elif step is OrderStep.REWASH:
        _plan_rewash(simulation, rewash_reason)
    elif step is OrderStep.REJECT_INTAKE:
        _plan_reject_intake(simulation, rejection_reason)
    if not simulation.planned:  # pragma: no cover - every branch above plans or raises
        raise OrderTransitionError(_NOTHING_TO_DO)
    return tuple(simulation.planned)


def _require_production(state: OrderState, expected: ProductionStatus) -> None:
    """The step's own starting point, so a step never silently becomes a different one.

    `transition_production` would accept, say, `IN_PROCESS -> ON_HOLD` for a `MARK_READY` if this
    did not pin where `MARK_READY` starts; the domain allows the move, the step would not mean it.
    """

    if state.commercial in {CommercialOrderStatus.CANCELLED, CommercialOrderStatus.COMPLETED}:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: order is closed")
    if state.production is not expected:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: illegal production transition")


def _plan_receive(
    simulation: _Simulation, facts: StepFacts, *, slot_approved: bool, accepted_at: datetime
) -> None:
    state = facts.state
    if state.commercial in {
        CommercialOrderStatus.ACTIVE,
        CommercialOrderStatus.CANCELLATION_REVIEW,
    }:
        raise OrderTransitionError(_NOTHING_TO_DO)
    if state.intake is IntakeStatus.AWAITING_HANDOFF:
        simulation.intake(IntakeStatus.RECEIVED_PENDING_INSPECTION)
    if simulation.state.commercial is CommercialOrderStatus.DRAFT:
        simulation.commercial(CommercialOrderStatus.REQUESTED)
    for target in _COMMERCIAL_TO_CONFIRMED[1:]:
        current = simulation.state.commercial
        if current in _COMMERCIAL_TO_CONFIRMED and _COMMERCIAL_TO_CONFIRMED.index(
            current
        ) < _COMMERCIAL_TO_CONFIRMED.index(target):
            simulation.commercial(target)
    if simulation.state.intake is not IntakeStatus.ACCEPTED:
        readiness = derive_intake_readiness(
            simulation.state.intake, facts.quote_readiness, slot_approved=slot_approved
        )
        blockers = readiness_blockers(readiness)
        # A rejected intake is refused by the domain for what it is (terminal), not as a blocker.
        if blockers and simulation.state.intake is not IntakeStatus.REJECTED:
            raise StepRequiresHuman(blockers)
        simulation.intake(IntakeStatus.ACCEPTED, readiness=readiness, accepted_at=accepted_at)
    simulation.commercial(CommercialOrderStatus.ACTIVE)


def _plan_cancel(simulation: _Simulation, resolution: CustodyResolution | None) -> None:
    commercial = simulation.state.commercial
    if commercial is CommercialOrderStatus.ACTIVE:
        simulation.commercial(CommercialOrderStatus.CANCELLATION_REVIEW)
        simulation.commercial(CommercialOrderStatus.CANCELLED, resolution)
        return
    if commercial is CommercialOrderStatus.CANCELLATION_REVIEW:
        simulation.commercial(CommercialOrderStatus.CANCELLED, resolution)
        return
    # Before ACTIVE: the direct edge. The domain refuses it once work has begun, with its own
    # `HUMAN_APPROVAL_REQUIRED: work has begun` -- the caller is not steered around that rule.
    # A resolution supplied here is carried onto the event exactly as the per-axis route does.
    simulation.commercial(CommercialOrderStatus.CANCELLED, resolution)


def _plan_rewash(simulation: _Simulation, reason: RewashReason | None) -> None:
    """`REWASH`: interrupt production with an exception, then send it back through the wash.

    The pins are the founder ruling's, stated before the domain is asked: the domain alone would
    also accept an exception from `QUEUED` or `IN_PROCESS`, and an exception during cancellation
    review -- neither of which is "wash it again".
    """

    state = simulation.state
    if state.commercial in TERMINAL_COMMERCIAL_STATUSES:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: order is closed")
    if state.commercial is not CommercialOrderStatus.ACTIVE:
        raise OrderTransitionError(
            "INVALID_STATE_TRANSITION: laundry is washed again only on an active order"
        )
    if state.production not in REWASH_FROM_PRODUCTION:
        raise OrderTransitionError(
            "INVALID_STATE_TRANSITION: a rewash starts at quality check or from the shelf, "
            "before the goods leave the shop"
        )
    if state.self_collection_recorded:
        # R1: once the record says the customer took the laundry, a garment brought back is a
        # complaint (`DEC-004`, the remedy flow), not a rewash inside this order.
        raise OrderTransitionError(
            "INVALID_STATE_TRANSITION: the customer is recorded as having taken the goods; "
            "a garment brought back is a complaint"
        )
    if reason is None:
        raise OrderTransitionError("VALIDATION_ERROR: REWASH requires rewash_reason")
    simulation.production(ProductionStatus.EXCEPTION)
    simulation.production(ProductionStatus.IN_PROCESS)
    simulation.planned[0] = replace(simulation.planned[0], rewash_reason=reason)


def _plan_reject_intake(simulation: _Simulation, reason: IntakeRejectionReason | None) -> None:
    """`REJECT_INTAKE`: refuse the goods on the counter, and close the order they came with."""

    state = simulation.state
    if state.commercial in TERMINAL_COMMERCIAL_STATUSES:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: order is closed")
    if state.commercial not in _COMMERCIAL_BEFORE_ACTIVE:
        raise OrderTransitionError(
            "INVALID_STATE_TRANSITION: goods are refused only before the order is accepted for work"
        )
    if state.intake is IntakeStatus.AWAITING_HANDOFF:
        raise OrderTransitionError(
            "INVALID_STATE_TRANSITION: nothing was received, so there is nothing to refuse"
        )
    if state.intake not in REJECT_INTAKE_FROM:
        raise OrderTransitionError(
            "INVALID_STATE_TRANSITION: goods are refused only while they wait on the counter"
        )
    if reason is None:
        raise OrderTransitionError("VALIDATION_ERROR: REJECT_INTAKE requires rejection_reason")
    simulation.intake(IntakeStatus.REJECTED)
    # The direct edge. After REJECTED the shop holds nothing (`CUSTODY_NOT_HELD_INTAKE_STATUSES`),
    # nothing was washed and nothing paid, so the domain's "work has begun" guard admits it; if any
    # of that were untrue it would refuse, and the whole step with it.
    simulation.commercial(CommercialOrderStatus.CANCELLED)
    simulation.planned[0] = replace(simulation.planned[0], rejection_reason=reason)


def _legal(
    step: OrderStep,
    facts: StepFacts,
    *,
    custody_resolution: CustodyResolution | None = None,
    rewash_reason: RewashReason | None = None,
    rejection_reason: IntakeRejectionReason | None = None,
) -> bool:
    """Dry-run `step`. `slot_approved` is taken as attested: the listing says what the caller may
    do once they attest it, and `requires` tells them that they must."""

    try:
        plan_step(
            step,
            facts,
            slot_approved=True,
            custody_resolution=custody_resolution,
            accepted_at=_DRY_RUN_INSTANT,
            rewash_reason=rewash_reason,
            rejection_reason=rejection_reason,
        )
    except OrderTransitionError:
        return False
    return True


def _payment_legal(facts: StepFacts) -> bool:
    """Is money still owed on a running order whose quote presents a single total? `DEC-035`.

    The balance says whether money is owed: `0056` keeps `UNPAID` / `PARTIALLY_PAID` equal to
    "owed > paid" at every commit. Whether the quote presents a total is asked of
    `evaluate_settlement` with the order's own total, as before -- the payment that settles the
    order writes the settlement row whose shape it decides, so a quote it would refuse is one no
    payment may be measured against.
    """

    state = facts.state
    if state.commercial is not CommercialOrderStatus.ACTIVE:
        return False
    if state.balance not in {OrderBalanceStatus.UNPAID, OrderBalanceStatus.PARTIALLY_PAID}:
        return False
    total = facts.quoted_total.minimum_vnd
    if total is None:
        return False
    outcome = evaluate_settlement(
        quoted=facts.quoted_total,
        tendered_vnd=total,
        collected_by_customer=False,
        fulfillment_mode=state.fulfillment_mode,
    )
    return isinstance(outcome, SettlementAccepted)


def payment_may_hand_over(facts: StepFacts) -> bool:
    """Whether the payment that settles this order may also record that the customer takes the
    goods now (`collected_by_customer`), as the old `SETTLE` did.

    True for a running self-collect order, not yet recorded as collected, whose laundry is finished
    and on which money is owed. The payments route asks the same questions under the row lock and
    refuses anything else; this only tells the console whether to offer the tick.
    """

    state = facts.state
    return (
        _payment_legal(facts)
        and state.fulfillment_mode not in MODES_EXPECTING_RETURN
        and not state.self_collection_recorded
        and handover_refusal(state.production) is None
    )


def _legal_steps(facts: StepFacts) -> dict[OrderStep, NextStep]:
    state = facts.state
    found: dict[OrderStep, NextStep] = {}

    for step in COMPOSITE_STEPS - {OrderStep.CANCEL, OrderStep.RECEIVE} - NEVER_PRIMARY_STEPS:
        if _legal(step, facts):
            found[step] = NextStep(step, False)
    if _legal(OrderStep.RECEIVE, facts):
        found[OrderStep.RECEIVE] = NextStep(OrderStep.RECEIVE, False, ("slot_approved",))
    if _legal(OrderStep.CANCEL, facts):
        found[OrderStep.CANCEL] = NextStep(OrderStep.CANCEL, False)
    else:
        accepted = tuple(
            resolution
            for resolution in CustodyResolution
            if _legal(OrderStep.CANCEL, facts, custody_resolution=resolution)
        )
        if accepted:
            found[OrderStep.CANCEL] = NextStep(
                OrderStep.CANCEL, False, ("custody_resolution",), accepted
            )
    # ORDER-STEPS-002: each reason dry-run, as the custody answers are, so `rewash_reasons` and
    # `rejection_reasons` can only ever name answers the step would take.
    rewash = tuple(
        reason for reason in RewashReason if _legal(OrderStep.REWASH, facts, rewash_reason=reason)
    )
    if rewash:
        found[OrderStep.REWASH] = NextStep(
            OrderStep.REWASH, False, ("rewash_reason",), rewash_reasons=rewash
        )
    rejection = tuple(
        reason
        for reason in IntakeRejectionReason
        if _legal(OrderStep.REJECT_INTAKE, facts, rejection_reason=reason)
    )
    if rejection:
        found[OrderStep.REJECT_INTAKE] = NextStep(
            OrderStep.REJECT_INTAKE, False, ("rejection_reason",), rejection_reasons=rejection
        )
    # HAND_OVER is RELEASE followed by COMPLETE; while it is legal, RELEASE alone is the same
    # button with the order left open, so only the complete one is offered. And at RELEASED,
    # HAND_OVER is exactly COMPLETE, so it is offered under that name alone.
    if OrderStep.HAND_OVER in found:
        if state.production is ProductionStatus.RELEASED:
            del found[OrderStep.HAND_OVER]
        else:
            found.pop(OrderStep.RELEASE, None)

    if _payment_legal(facts):
        # One step whatever the amount: a deposit, a part payment or the rest (`DEC-035`).
        found[OrderStep.TAKE_PAYMENT] = NextStep(
            OrderStep.TAKE_PAYMENT, False, ("amount_vnd", "method")
        )
    if (
        evaluate_collection(
            commercial=state.commercial,
            production=state.production,
            balance=state.balance,
            settlement_shape=facts.settlement_shape,
            self_collection_recorded=state.self_collection_recorded,
        )
        is None
    ):
        found[OrderStep.COLLECT] = NextStep(OrderStep.COLLECT, False)
    active = state.commercial is CommercialOrderStatus.ACTIVE
    if (
        active
        and state.fulfillment_mode in MODES_EXPECTING_PICKUP
        and not facts.pickup_leg_succeeded
    ):
        found[OrderStep.DELIVERY_PICKUP] = NextStep(OrderStep.DELIVERY_PICKUP, False)
    # A return leg is offered once the laundry is finished -- the same `HANDOVER_READY_PRODUCTION`
    # rule the settlement applies to "the customer took the goods". The route itself accepts a leg
    # on any ACTIVE order and is unchanged; this only declines to suggest recording an arrival for
    # laundry that is still in the machine.
    if (
        active
        and state.fulfillment_mode in MODES_EXPECTING_RETURN
        and not state.required_delivery_legs_succeeded
        and handover_refusal(state.production) is None
    ):
        found[OrderStep.DELIVERY_RETURN] = NextStep(OrderStep.DELIVERY_RETURN, False)
    return found


def _primary_order(facts: StepFacts) -> tuple[OrderStep, ...]:
    """Which legal step is the natural next real-world event, in preference order."""

    if facts.state.commercial is CommercialOrderStatus.CANCELLATION_REVIEW:
        return (OrderStep.CANCEL, OrderStep.REOPEN)
    finish: tuple[OrderStep, ...]
    if facts.state.fulfillment_mode in MODES_EXPECTING_RETURN:
        # `DEC-023`: paid in full at the counter before the laundry leaves, then the courier
        # takes it out, then the leg records arrival, then the order closes.
        finish = (
            OrderStep.TAKE_PAYMENT,
            OrderStep.RELEASE,
            OrderStep.DELIVERY_RETURN,
            OrderStep.HAND_OVER,
            OrderStep.COMPLETE,
        )
    else:
        finish = (
            OrderStep.TAKE_PAYMENT,
            OrderStep.COLLECT,
            OrderStep.HAND_OVER,
            OrderStep.COMPLETE,
        )
    return (
        OrderStep.RECEIVE,
        OrderStep.RESUME,
        OrderStep.START_WASH,
        OrderStep.QUALITY_CHECK,
        OrderStep.MARK_READY,
        *finish,
    )


def next_steps(facts: StepFacts) -> tuple[NextStep, ...]:
    """Every legal step for this order, in `STEP_ORDER`, with at most one marked primary.

    Empty for a closed order. The primary is the first legal step of `_primary_order`; when none of
    those is legal, the first legal step that is neither `CANCEL` nor `HOLD`; and only when nothing
    else is legal, the first legal step at all. A step in `NEVER_PRIMARY_STEPS` is never chosen
    (`ORDER-STEPS-002`), so when a rewash or a refusal is the only legal step the list has no
    primary at all: exactly one entry is primary whenever any ordinary step is legal.
    """

    found = _legal_steps(facts)
    if not found:
        return ()
    candidates = [step for step in STEP_ORDER if step in found and step not in NEVER_PRIMARY_STEPS]
    primary = next((step for step in _primary_order(facts) if step in found), None)
    if primary is None:
        primary = next(
            (step for step in candidates if step not in {OrderStep.CANCEL, OrderStep.HOLD}),
            None,
        )
    if primary is None:
        primary = next(iter(candidates), None)
    return tuple(
        NextStep(
            item.step,
            item.step is primary,
            item.requires,
            item.custody_resolutions,
            item.rewash_reasons,
            item.rejection_reasons,
        )
        for step in STEP_ORDER
        if (item := found.get(step)) is not None
    )


__all__ = [
    "COMPOSITE_STEPS",
    "NEVER_PRIMARY_STEPS",
    "READINESS_BLOCKER_CODES",
    "REJECT_INTAKE_FROM",
    "REWASH_FROM_PRODUCTION",
    "STEP_ORDER",
    "NextStep",
    "OrderStep",
    "PlannedTransition",
    "QuoteReadinessFacts",
    "StepFacts",
    "StepRequiresHuman",
    "derive_intake_readiness",
    "next_steps",
    "payment_may_hand_over",
    "plan_step",
    "readiness_blockers",
]
