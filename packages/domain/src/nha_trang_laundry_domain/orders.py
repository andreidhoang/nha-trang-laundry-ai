"""Deterministic order state machines and cross-state invariants."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
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
    if (
        state.commercial is CommercialOrderStatus.CANCELLATION_REVIEW
        and target is CommercialOrderStatus.CANCELLED
        and not (cancellation_approved and custody_and_financial_resolution_recorded)
    ):
        raise OrderTransitionError("HUMAN_APPROVAL_REQUIRED: cancellation resolution is incomplete")
    return replace(state, commercial=target)


def transition_intake(
    state: OrderState,
    target: IntakeStatus,
    *,
    readiness: IntakeReadiness | None = None,
    production_accepted_at: datetime | None = None,
) -> OrderState:
    """Advance intake; waiting states may be skipped only when final readiness is proven."""
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
    current = state.production
    if target is ProductionStatus.NOT_STARTED or target is current:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: invalid production target")
    if state.intake is not IntakeStatus.ACCEPTED:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: intake is not accepted")
    if current is ProductionStatus.ON_HOLD:
        if target is not state.production_resume_status:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: invalid hold resume target")
        return replace(state, production=target, production_resume_status=None)
    if current in {ProductionStatus.RELEASED, ProductionStatus.EXCEPTION}:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: production is terminal")
    if target is ProductionStatus.ON_HOLD:
        if current is ProductionStatus.NOT_STARTED:
            raise OrderTransitionError("INVALID_STATE_TRANSITION: unstarted work cannot be held")
        return replace(state, production=target, production_resume_status=current)
    if target is ProductionStatus.EXCEPTION:
        return replace(state, production=target, production_resume_status=None)
    current_index = PRODUCTION_SEQUENCE.index(current)
    if target not in PRODUCTION_SEQUENCE or PRODUCTION_SEQUENCE.index(target) != current_index + 1:
        raise OrderTransitionError("INVALID_STATE_TRANSITION: illegal production transition")
    return replace(state, production=target, production_resume_status=None)
