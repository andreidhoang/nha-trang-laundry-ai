"""Deterministic order state machines and cross-state invariants."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
)


class OrderTransitionError(ValueError):
    """Raised when an order command is stale, illegal, or missing required facts."""


@dataclass(frozen=True)
class IntakeReadiness:
    custody_recorded: bool
    quantity_basis_approved: bool
    service_classified: bool
    exact_price_approved: bool
    customer_reconfirmation_satisfied: bool
    slot_approved: bool

    @property
    def ready(self) -> bool:
        return all(
            (
                self.custody_recorded,
                self.quantity_basis_approved,
                self.service_classified,
                self.exact_price_approved,
                self.customer_reconfirmation_satisfied,
                self.slot_approved,
            )
        )


@dataclass(frozen=True)
class OrderState:
    commercial: CommercialOrderStatus
    intake: IntakeStatus
    production: ProductionStatus
    fulfillment_mode: FulfillmentMode
    balance: OrderBalanceStatus
    required_delivery_legs_succeeded: bool = False
    self_collection_recorded: bool = False
    production_accepted_at: datetime | None = None
    production_resume_status: ProductionStatus | None = None


COMMERCIAL_TRANSITIONS: Final = {
    CommercialOrderStatus.DRAFT: frozenset(
        {CommercialOrderStatus.REQUESTED, CommercialOrderStatus.CANCELLED}
    ),
    CommercialOrderStatus.REQUESTED: frozenset(
        {CommercialOrderStatus.STORE_CONFIRMATION_PENDING, CommercialOrderStatus.CANCELLED}
    ),
    CommercialOrderStatus.STORE_CONFIRMATION_PENDING: frozenset(
        {CommercialOrderStatus.CONFIRMED, CommercialOrderStatus.CANCELLED}
    ),
    CommercialOrderStatus.CONFIRMED: frozenset(
        {CommercialOrderStatus.ACTIVE, CommercialOrderStatus.CANCELLED}
    ),
    CommercialOrderStatus.ACTIVE: frozenset({CommercialOrderStatus.CANCELLATION_REVIEW}),
    CommercialOrderStatus.CANCELLATION_REVIEW: frozenset(
        {CommercialOrderStatus.CANCELLED, CommercialOrderStatus.ACTIVE}
    ),
    CommercialOrderStatus.CANCELLED: frozenset(),
    CommercialOrderStatus.COMPLETED: frozenset(),
}

INTAKE_SEQUENCE: Final = (
    IntakeStatus.AWAITING_HANDOFF,
    IntakeStatus.RECEIVED_PENDING_INSPECTION,
    IntakeStatus.WAITING_PRICE_APPROVAL,
    IntakeStatus.WAITING_CUSTOMER_RECONFIRMATION,
    IntakeStatus.WAITING_SLOT_APPROVAL,
    IntakeStatus.ACCEPTED,
)

# Custody, not acceptance, is what a cancellation has to account for. This tested
# `intake is ACCEPTED` and so caught only the last of six intake states: an order at
# `RECEIVED_PENDING_INSPECTION` -- the customer's laundry physically on the counter, four stages
# before acceptance -- cancelled outright with nothing recorded about where the goods went. The
# same file already knew better, refusing `-> REJECTED` from `AWAITING_HANDOFF` because "custody
# was never received"; that is precisely the boundary, and it belongs on both sides.
#
# These two are the only states where the shop holds nothing: before handover, and after a
# rejection that returned the goods. Every other state means somebody has to say what happened to
# them.
CUSTODY_NOT_HELD_INTAKE_STATUSES: Final = frozenset(
    {IntakeStatus.AWAITING_HANDOFF, IntakeStatus.REJECTED}
)

# A closed order has no further intake or production. `0008`'s projection trigger already refuses
# any UPDATE of a row in one of these states, so before COUNTER-DEFECTS-001 the two dimensions that
# never consulted `commercial` produced a valid next state and met the trigger as an unhandled
# `RaiseException` -- HTTP 500 on a rule the system had actually decided correctly. The rule lives
# here now, with the others, so the caller is told which one it broke.
TERMINAL_COMMERCIAL_STATUSES: Final = frozenset(
    {CommercialOrderStatus.CANCELLED, CommercialOrderStatus.COMPLETED}
)

PRODUCTION_SEQUENCE: Final = (
    ProductionStatus.NOT_STARTED,
    ProductionStatus.QUEUED,
    ProductionStatus.IN_PROCESS,
    ProductionStatus.QUALITY_CHECK,
    ProductionStatus.READY_AT_STORE,
    ProductionStatus.RELEASED,
)


def transition_commercial(
    state: OrderState,
    target: CommercialOrderStatus,
    *,
    cancellation_approved: bool = False,
    custody_and_financial_resolution_recorded: bool = False,
    custody_resolution: CustodyResolution | None = None,
) -> OrderState:
    """Apply a commercial transition after checking all orthogonal state requirements."""
    if target is CommercialOrderStatus.COMPLETED:
        if state.commercial is not CommercialOrderStatus.ACTIVE:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: order is not active")
        if state.production is not ProductionStatus.RELEASED:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: production is not released")
        if not (state.required_delivery_legs_succeeded or state.self_collection_recorded):
            raise OrderTransitionError("INVALID_STATE_TRANSITION: fulfillment is incomplete")
        if state.balance not in {OrderBalanceStatus.PAID, OrderBalanceStatus.ON_ACCOUNT}:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: balance is not settled")
        return replace(state, commercial=target)

    if target not in COMMERCIAL_TRANSITIONS[state.commercial]:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: illegal commercial transition")
    if target is CommercialOrderStatus.ACTIVE and state.intake is not IntakeStatus.ACCEPTED:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: intake is not accepted")
    if target is CommercialOrderStatus.CANCELLED:
        if state.commercial is CommercialOrderStatus.CANCELLATION_REVIEW:
            if not (cancellation_approved and custody_and_financial_resolution_recorded):
                raise OrderTransitionError(
                    "HUMAN_APPROVAL_REQUIRED: cancellation resolution is incomplete"
                )
            _reject_resolution_contradicting_the_record(state, custody_resolution)
        elif _work_has_begun(state):
            # DEC-024. Every other edge into CANCELLED had no guard at all, so an order whose
            # laundry was already in a machine could be cancelled outright and the money never
            # recorded -- while the one path that *is* guarded could never succeed, because its two
            # flags default false and no caller passed them. The guarded path was the impossible
            # one.
            #
            # Free before work starts, reviewed after. A customer changing their mind at the counter
            # is the common case and staff would work around a review step for it; an order with
            # laundry in the shop belongs in `CANCELLATION_REVIEW`, reached by advancing to ACTIVE
            # first, which is where an order holding a customer's goods should be anyway.
            raise OrderTransitionError(
                "HUMAN_APPROVAL_REQUIRED: work has begun; cancel through cancellation review"
            )
    return replace(state, commercial=target)


def _reject_resolution_contradicting_the_record(
    state: OrderState, resolution: CustodyResolution | None
) -> None:
    """Refuse a resolution the order's own recorded facts say is untrue.

    ORDER-EXIT-001 made a resolution the approval, which stopped an order from disappearing
    without one -- but nothing then compared what staff said against what the system had already
    written down. A named staff member can be mistaken as easily as they can be dishonest, and both
    produce the same ledger entry.

    Only contradictions are refused here, never judgements. "We never received it" is checkable:
    the shop records custody at handover. "Returned unwashed" is checkable: the shop records when
    production started. What a paid, collected order should be cancelled *as* is not checkable and
    is not decided here -- `CustodyResolution` has three members precisely because the fourth case
    is an open question, and inventing an answer in domain code is how a policy gap becomes a
    silent default.
    """

    received = state.intake is not IntakeStatus.AWAITING_HANDOFF
    if resolution is CustodyResolution.NOT_RECEIVED and received:
        raise OrderTransitionError(
            "INVALID_STATE_TRANSITION: the order records custody of the goods, "
            "so they cannot be resolved as never received"
        )
    washed = state.production is not ProductionStatus.NOT_STARTED
    if resolution is CustodyResolution.RETURNED_UNWASHED_REFUNDED and washed:
        raise OrderTransitionError(
            "INVALID_STATE_TRANSITION: production has begun on the goods, "
            "so they cannot be resolved as returned unwashed"
        )


def _work_has_begun(state: OrderState) -> bool:
    """Whether anything has happened that a cancellation would have to resolve.

    Any one of these means the shop is holding something of the customer's, has done work, or has
    taken money -- and each is a thing a named staff member has to account for before the order can
    disappear.
    """

    return (
        state.production is not ProductionStatus.NOT_STARTED
        or state.intake not in CUSTODY_NOT_HELD_INTAKE_STATUSES
        or state.balance is not OrderBalanceStatus.UNPAID
    )


def transition_intake(
    state: OrderState,
    target: IntakeStatus,
    *,
    readiness: IntakeReadiness | None = None,
    production_accepted_at: datetime | None = None,
) -> OrderState:
    """Advance intake; waiting states may be skipped only when final readiness is proven."""
    if state.commercial in TERMINAL_COMMERCIAL_STATUSES:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: order is closed")
    if state.intake in {IntakeStatus.ACCEPTED, IntakeStatus.REJECTED}:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: intake is terminal")
    if target is IntakeStatus.REJECTED:
        if state.intake is IntakeStatus.AWAITING_HANDOFF:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: custody was never received")
        return replace(state, intake=target)
    if target not in INTAKE_SEQUENCE:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: invalid intake target")
    current_index = INTAKE_SEQUENCE.index(state.intake)
    target_index = INTAKE_SEQUENCE.index(target)
    if target_index <= current_index:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: intake cannot move backward")
    if (
        state.intake is IntakeStatus.AWAITING_HANDOFF
        and target is not IntakeStatus.RECEIVED_PENDING_INSPECTION
    ):
        raise OrderTransitionError("INVALID_STATE_TRANSITION: handoff must be recorded first")
    if target is IntakeStatus.ACCEPTED:
        if readiness is None or not readiness.ready:
            raise OrderTransitionError("HUMAN_APPROVAL_REQUIRED: intake blockers remain")
        if production_accepted_at is None or production_accepted_at.tzinfo is None:
            raise OrderTransitionError("VALIDATION_ERROR: production acceptance time is required")
        if state.production_accepted_at is not None:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: production was already accepted")
        return replace(
            state,
            intake=target,
            production_accepted_at=production_accepted_at,
        )
    return replace(state, intake=target)


def transition_production(state: OrderState, target: ProductionStatus) -> OrderState:
    """Advance production without allowing work before intake acceptance."""
    if state.commercial in TERMINAL_COMMERCIAL_STATUSES:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: order is closed")
    current = state.production
    if target is ProductionStatus.NOT_STARTED or target is current:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: invalid production target")
    if state.intake is not IntakeStatus.ACCEPTED:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: intake is not accepted")
    if current is ProductionStatus.ON_HOLD:
        if target is not state.production_resume_status:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: invalid hold resume target")
        return replace(state, production=target, production_resume_status=None)
    if current is ProductionStatus.EXCEPTION:
        # DEC-024. `EXCEPTION` used to be terminal alongside `RELEASED`, and it also discarded the
        # state it interrupted -- so a paid order in which staff recorded a stain or a machine fault
        # could never reach `COMPLETED` or `CANCELLED`. It sat ACTIVE forever with the goods in the
        # shop, which contradicts `DEC-004`: the remedy policy gives a free rewash within 7 days on
        # store fault, and therefore assumes the laundry gets finished.
        #
        # An exception resumes to the state it interrupted, **or to any earlier point in the
        # sequence**. That second half is the case that matters: a stain found at quality check
        # needs a rewash, which is backward movement, and the forward-only rule below would refuse
        # it. Requiring an attributed exception first is the control on going backwards -- it cannot
        # happen quietly.
        resume = state.production_resume_status
        if resume is None or target not in PRODUCTION_SEQUENCE:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: invalid exception resume target")
        if PRODUCTION_SEQUENCE.index(target) > PRODUCTION_SEQUENCE.index(resume):
            raise OrderTransitionError(
                "INVALID_STATE_TRANSITION: an exception cannot resume past where it interrupted"
            )
        return replace(state, production=target, production_resume_status=None)
    if current is ProductionStatus.RELEASED:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: production is terminal")
    if target is ProductionStatus.ON_HOLD:
        if current is ProductionStatus.NOT_STARTED:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: unstarted work cannot be held")
        return replace(state, production=target, production_resume_status=current)
    if target is ProductionStatus.EXCEPTION:
        # Refused before work starts, for the same reason a hold is: nothing has happened to the
        # laundry yet, so there is no production exception to record and the case is a commercial
        # cancellation. It also keeps `production_resume_status` inside the `0007` CHECK
        # constraint, which does not admit `NOT_STARTED`.
        if current is ProductionStatus.NOT_STARTED:
            raise OrderTransitionError(
                "INVALID_STATE_TRANSITION: unstarted work has no production exception"
            )
        return replace(state, production=target, production_resume_status=current)
    current_index = PRODUCTION_SEQUENCE.index(current)
    if target not in PRODUCTION_SEQUENCE or PRODUCTION_SEQUENCE.index(target) != current_index + 1:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: illegal production transition")
    return replace(state, production=target, production_resume_status=None)
