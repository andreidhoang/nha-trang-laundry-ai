from datetime import UTC, datetime

import pytest
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    FulfillmentMode,
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

ACCEPTED_AT = datetime(2026, 8, 1, 1, 2, tzinfo=UTC)
READY = IntakeReadiness(True, True, True, True, True, True)


def state(
    *,
    commercial: CommercialOrderStatus = CommercialOrderStatus.REQUESTED,
    intake: IntakeStatus = IntakeStatus.AWAITING_HANDOFF,
    production: ProductionStatus = ProductionStatus.NOT_STARTED,
    balance: OrderBalanceStatus = OrderBalanceStatus.UNPAID,
    delivery_succeeded: bool = False,
    collected: bool = False,
) -> OrderState:
    return OrderState(
        commercial,
        intake,
        production,
        FulfillmentMode.PICKUP_AND_RETURN,
        balance,
        delivery_succeeded,
        collected,
        ACCEPTED_AT if intake is IntakeStatus.ACCEPTED else None,
    )


def test_active_requires_accepted_intake_and_direct_active_cancellation_is_forbidden() -> None:
    confirmed = state(commercial=CommercialOrderStatus.CONFIRMED)

    with pytest.raises(OrderTransitionError, match="intake is not accepted"):
        transition_commercial(confirmed, CommercialOrderStatus.ACTIVE)

    active = transition_commercial(
        state(commercial=CommercialOrderStatus.CONFIRMED, intake=IntakeStatus.ACCEPTED),
        CommercialOrderStatus.ACTIVE,
    )
    with pytest.raises(OrderTransitionError, match="illegal commercial transition"):
        transition_commercial(active, CommercialOrderStatus.CANCELLED)


def test_active_cancellation_requires_review_approval_and_resolution() -> None:
    active = state(commercial=CommercialOrderStatus.ACTIVE, intake=IntakeStatus.ACCEPTED)
    review = transition_commercial(active, CommercialOrderStatus.CANCELLATION_REVIEW)

    with pytest.raises(OrderTransitionError, match="HUMAN_APPROVAL_REQUIRED"):
        transition_commercial(review, CommercialOrderStatus.CANCELLED)

    cancelled = transition_commercial(
        review,
        CommercialOrderStatus.CANCELLED,
        cancellation_approved=True,
        custody_and_financial_resolution_recorded=True,
    )
    assert cancelled.commercial is CommercialOrderStatus.CANCELLED


def test_intake_acceptance_requires_every_blocker_and_sets_timestamp_once() -> None:
    received = transition_intake(state(), IntakeStatus.RECEIVED_PENDING_INSPECTION)
    not_ready = IntakeReadiness(True, True, True, True, True, False)

    with pytest.raises(OrderTransitionError, match="intake blockers remain"):
        transition_intake(
            received,
            IntakeStatus.ACCEPTED,
            readiness=not_ready,
            production_accepted_at=ACCEPTED_AT,
        )

    accepted = transition_intake(
        received,
        IntakeStatus.ACCEPTED,
        readiness=READY,
        production_accepted_at=ACCEPTED_AT,
    )
    assert accepted.intake is IntakeStatus.ACCEPTED
    assert accepted.production_accepted_at == ACCEPTED_AT
    with pytest.raises(OrderTransitionError, match="intake is terminal"):
        transition_intake(
            accepted,
            IntakeStatus.ACCEPTED,
            readiness=READY,
            production_accepted_at=ACCEPTED_AT,
        )


def test_production_is_sequential_and_hold_resumes_only_prior_state() -> None:
    accepted = state(intake=IntakeStatus.ACCEPTED)
    with pytest.raises(OrderTransitionError, match="illegal production transition"):
        transition_production(accepted, ProductionStatus.IN_PROCESS)

    queued = transition_production(accepted, ProductionStatus.QUEUED)
    in_process = transition_production(queued, ProductionStatus.IN_PROCESS)
    held = transition_production(in_process, ProductionStatus.ON_HOLD)
    with pytest.raises(OrderTransitionError, match="invalid hold resume target"):
        transition_production(held, ProductionStatus.QUALITY_CHECK)
    assert transition_production(held, ProductionStatus.IN_PROCESS).production_resume_status is None


def test_completion_requires_released_fulfillment_and_settled_balance() -> None:
    incomplete = state(
        commercial=CommercialOrderStatus.ACTIVE,
        intake=IntakeStatus.ACCEPTED,
        production=ProductionStatus.RELEASED,
        balance=OrderBalanceStatus.PAID,
    )
    with pytest.raises(OrderTransitionError, match="fulfillment is incomplete"):
        transition_commercial(incomplete, CommercialOrderStatus.COMPLETED)

    complete = transition_commercial(
        state(
            commercial=CommercialOrderStatus.ACTIVE,
            intake=IntakeStatus.ACCEPTED,
            production=ProductionStatus.RELEASED,
            balance=OrderBalanceStatus.ON_ACCOUNT,
            delivery_succeeded=True,
        ),
        CommercialOrderStatus.COMPLETED,
    )
    assert complete.commercial is CommercialOrderStatus.COMPLETED
