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

Money is never computed here. `SETTLE` and `PREPAY` are legal when `evaluate_settlement` would
accept the order's own quoted total -- a figure read off the bound quote revision, not derived --
and they are executed on the settlement route, where the staff member types the amount they took.

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

Steps served by their own existing routes, listed so the console has one source of the next action:

* ``SETTLE`` -- ``POST /orders/{id}/settlement``, ``collected_by_customer=true``: the customer
  pays the exact total and takes the goods now.
* ``PREPAY`` -- ``POST /orders/{id}/settlement``, ``collected_by_customer=false``: paid before
  collection or delivery (``DEC-023`` / ``DEC-032``).
* ``COLLECT`` -- ``POST /orders/{id}/collection``: a prepaid customer takes the goods.
* ``DELIVERY_PICKUP`` -- ``POST /orders/{id}/delivery-legs`` with ``leg_kind=PICKUP``.
* ``DELIVERY_RETURN`` -- ``POST /orders/{id}/delivery-legs`` with ``leg_kind=RETURN``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from nha_trang_laundry_domain.catalog import (
    MODES_EXPECTING_PICKUP,
    MODES_EXPECTING_RETURN,
    CommercialOrderStatus,
    CustodyResolution,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.orders import (
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
    RELEASE = "RELEASE"
    HAND_OVER = "HAND_OVER"
    COMPLETE = "COMPLETE"
    CANCEL = "CANCEL"
    REOPEN = "REOPEN"
    SETTLE = "SETTLE"
    PREPAY = "PREPAY"
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
        OrderStep.RELEASE,
        OrderStep.HAND_OVER,
        OrderStep.COMPLETE,
        OrderStep.CANCEL,
        OrderStep.REOPEN,
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


@dataclass(frozen=True, slots=True)
class NextStep:
    """One legal step for the order as it stands.

    `requires` names the request fields the caller must supply for the step to be accepted:
    `slot_approved` for `RECEIVE` (the operator attests capacity), `custody_resolution` for a
    cancellation that goes through review. `custody_resolutions` lists, for such a cancellation,
    exactly the resolutions the domain would accept for this order -- each one dry-run -- so the
    console offers only answers the server will take.
    """

    step: OrderStep
    primary: bool
    requires: tuple[str, ...] = ()
    custody_resolutions: tuple[CustodyResolution, ...] = ()


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
) -> tuple[PlannedTransition, ...]:
    """The single-axis transitions `step` is made of for this order, or a refusal.

    Every transition is applied to a simulated state by the real domain function before the next is
    planned, so a refusal anywhere refuses the whole step and the caller writes nothing. Raises
    `OrderTransitionError` (its message carries the domain's own `INVALID_STATE_TRANSITION:` /
    `HUMAN_APPROVAL_REQUIRED:` prefix) or its subclass `StepRequiresHuman`.
    """

    if step not in COMPOSITE_STEPS:
        raise OrderTransitionError(
            f"INVALID_STATE_TRANSITION: {step.value} is recorded on its own route, not as a step"
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
        # order by this step. The counter's way out is SETTLE (collected) then HAND_OVER, which
        # takes the money in the same visit; a delivery order is released to the courier after
        # its DEC-023 prepayment, and a courier never takes money.
        if (
            state.fulfillment_mode not in MODES_EXPECTING_RETURN
            and state.balance is OrderBalanceStatus.UNPAID
        ):
            raise OrderTransitionError(
                "INVALID_STATE_TRANSITION: an unpaid order the customer collects is released "
                "by taking payment, not on its own"
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


def _legal(
    step: OrderStep, facts: StepFacts, *, custody_resolution: CustodyResolution | None = None
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
        )
    except OrderTransitionError:
        return False
    return True


def _settlement_legal(facts: StepFacts, *, collected_by_customer: bool) -> bool:
    """Would the settlement route accept the order's own total for this shape now?"""

    state = facts.state
    if state.commercial is not CommercialOrderStatus.ACTIVE:
        return False
    if state.balance is not OrderBalanceStatus.UNPAID:
        return False
    total = facts.quoted_total.minimum_vnd
    if total is None:
        return False
    outcome = evaluate_settlement(
        quoted=facts.quoted_total,
        tendered_vnd=total,
        collected_by_customer=collected_by_customer,
        fulfillment_mode=state.fulfillment_mode,
    )
    if not isinstance(outcome, SettlementAccepted):
        return False
    return not (collected_by_customer and handover_refusal(state.production) is not None)


def _legal_steps(facts: StepFacts) -> dict[OrderStep, NextStep]:
    state = facts.state
    found: dict[OrderStep, NextStep] = {}

    for step in COMPOSITE_STEPS - {OrderStep.CANCEL, OrderStep.RECEIVE}:
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
    # HAND_OVER is RELEASE followed by COMPLETE; while it is legal, RELEASE alone is the same
    # button with the order left open, so only the complete one is offered. And at RELEASED,
    # HAND_OVER is exactly COMPLETE, so it is offered under that name alone.
    if OrderStep.HAND_OVER in found:
        if state.production is ProductionStatus.RELEASED:
            del found[OrderStep.HAND_OVER]
        else:
            found.pop(OrderStep.RELEASE, None)

    if _settlement_legal(facts, collected_by_customer=True):
        found[OrderStep.SETTLE] = NextStep(OrderStep.SETTLE, False)
    if _settlement_legal(facts, collected_by_customer=False):
        found[OrderStep.PREPAY] = NextStep(OrderStep.PREPAY, False)
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
            OrderStep.PREPAY,
            OrderStep.RELEASE,
            OrderStep.DELIVERY_RETURN,
            OrderStep.HAND_OVER,
            OrderStep.COMPLETE,
        )
    else:
        finish = (OrderStep.SETTLE, OrderStep.COLLECT, OrderStep.HAND_OVER, OrderStep.COMPLETE)
    return (
        OrderStep.RECEIVE,
        OrderStep.RESUME,
        OrderStep.START_WASH,
        OrderStep.QUALITY_CHECK,
        OrderStep.MARK_READY,
        *finish,
    )


def next_steps(facts: StepFacts) -> tuple[NextStep, ...]:
    """Every legal step for this order, in `STEP_ORDER`, with exactly one marked primary.

    Empty for a closed order. The primary is the first legal step of `_primary_order`; when none of
    those is legal, the first legal step that is neither `CANCEL` nor `HOLD`; and only when nothing
    else is legal, the first legal step at all.
    """

    found = _legal_steps(facts)
    if not found:
        return ()
    primary = next((step for step in _primary_order(facts) if step in found), None)
    if primary is None:
        primary = next(
            (
                step
                for step in STEP_ORDER
                if step in found and step not in {OrderStep.CANCEL, OrderStep.HOLD}
            ),
            None,
        )
    if primary is None:
        primary = next(step for step in STEP_ORDER if step in found)
    return tuple(
        NextStep(
            item.step,
            item.step is primary,
            item.requires,
            item.custody_resolutions,
        )
        for step in STEP_ORDER
        if (item := found.get(step)) is not None
    )


__all__ = [
    "COMPOSITE_STEPS",
    "READINESS_BLOCKER_CODES",
    "STEP_ORDER",
    "NextStep",
    "OrderStep",
    "PlannedTransition",
    "QuoteReadinessFacts",
    "StepFacts",
    "StepRequiresHuman",
    "derive_intake_readiness",
    "next_steps",
    "plan_step",
    "readiness_blockers",
]
