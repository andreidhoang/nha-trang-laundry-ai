from datetime import UTC, datetime

import pytest
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    CustodyResolution,
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


def test_a_terminal_commercial_status_freezes_intake_and_production() -> None:
    # COUNTER-DEFECTS-001. `transition_intake` and `transition_production` read `state.intake` and
    # `state.production` and never `state.commercial`, so the domain happily returned a next state
    # for a cancelled order. The `0008` projection trigger then refused the UPDATE -- correctly --
    # and `psycopg.errors.RaiseException` escaped the route as HTTP 500. The rule the trigger
    # enforces belongs here, where every other lifecycle rule already lives, so the refusal is a
    # typed 4xx naming the cause instead of a crash naming nothing.
    for terminal in (CommercialOrderStatus.CANCELLED, CommercialOrderStatus.COMPLETED):
        cancelled = state(commercial=terminal, intake=IntakeStatus.RECEIVED_PENDING_INSPECTION)
        with pytest.raises(OrderTransitionError, match="order is closed"):
            transition_intake(cancelled, IntakeStatus.WAITING_PRICE_APPROVAL)

        released = state(
            commercial=terminal,
            intake=IntakeStatus.ACCEPTED,
            production=ProductionStatus.QUEUED,
        )
        with pytest.raises(OrderTransitionError, match="order is closed"):
            transition_production(released, ProductionStatus.IN_PROCESS)


def test_a_production_exception_is_an_interruption_not_an_outcome() -> None:
    """`DEC-024`: `EXCEPTION` had zero outgoing edges, so a stain orphaned a paid order forever.

    It also discarded the state it interrupted. `DEC-004` gives a free rewash within 7 days on
    store fault, so the shop's own remedy policy assumes the laundry gets finished -- a state
    machine in which it cannot is the policy contradicting itself.
    """

    working = state(
        commercial=CommercialOrderStatus.ACTIVE,
        intake=IntakeStatus.ACCEPTED,
        production=ProductionStatus.QUALITY_CHECK,
    )
    excepted = transition_production(working, ProductionStatus.EXCEPTION)
    assert excepted.production is ProductionStatus.EXCEPTION
    # The interrupted state is remembered rather than thrown away, exactly as a hold does.
    assert excepted.production_resume_status is ProductionStatus.QUALITY_CHECK

    resumed = transition_production(excepted, ProductionStatus.QUALITY_CHECK)
    assert resumed.production is ProductionStatus.QUALITY_CHECK
    assert resumed.production_resume_status is None

    # And onward to release, so the order can actually reach COMPLETED.
    released = transition_production(
        transition_production(resumed, ProductionStatus.READY_AT_STORE),
        ProductionStatus.RELEASED,
    )
    assert released.production is ProductionStatus.RELEASED


def test_an_exception_may_resume_backwards_for_a_rewash_but_not_forwards() -> None:
    """The half of the rule that matters: a stain found at quality check needs the load rewashed.

    That is backward movement, which the forward-only sequence rule refuses. Requiring an
    attributed exception first is the control -- production cannot walk backwards quietly.
    """

    excepted = transition_production(
        state(
            commercial=CommercialOrderStatus.ACTIVE,
            intake=IntakeStatus.ACCEPTED,
            production=ProductionStatus.QUALITY_CHECK,
        ),
        ProductionStatus.EXCEPTION,
    )

    rewashing = transition_production(excepted, ProductionStatus.IN_PROCESS)
    assert rewashing.production is ProductionStatus.IN_PROCESS

    # But an exception is not a shortcut: it cannot resume past where it interrupted.
    with pytest.raises(OrderTransitionError, match="cannot resume past"):
        transition_production(excepted, ProductionStatus.READY_AT_STORE)


def test_unstarted_work_has_no_production_exception() -> None:
    """Same reason a hold is refused there, plus the `0007` CHECK does not admit NOT_STARTED."""

    with pytest.raises(OrderTransitionError, match="unstarted work has no production exception"):
        transition_production(
            state(commercial=CommercialOrderStatus.ACTIVE, intake=IntakeStatus.ACCEPTED),
            ProductionStatus.EXCEPTION,
        )


def test_a_cancellation_is_free_before_work_starts_and_reviewed_after() -> None:
    """`DEC-024`: the guard was inverted in practice.

    `CANCELLATION_REVIEW -> CANCELLED` was the only guarded edge and could never succeed, because
    its two flags default false and no caller passed them. Every other edge into `CANCELLED` had no
    guard at all -- so the reviewed path always refused and the unreviewed path always succeeded.
    """

    # Nothing taken in, nothing paid: the customer changed their mind at the counter.
    untouched = state(commercial=CommercialOrderStatus.CONFIRMED)
    assert (
        transition_commercial(untouched, CommercialOrderStatus.CANCELLED).commercial
        is CommercialOrderStatus.CANCELLED
    )

    # Laundry received and a machine running: this is the case that used to succeed silently.
    for started in (
        state(commercial=CommercialOrderStatus.CONFIRMED, intake=IntakeStatus.ACCEPTED),
        state(
            commercial=CommercialOrderStatus.CONFIRMED,
            intake=IntakeStatus.ACCEPTED,
            production=ProductionStatus.IN_PROCESS,
        ),
        state(commercial=CommercialOrderStatus.CONFIRMED, balance=OrderBalanceStatus.PAID),
    ):
        with pytest.raises(OrderTransitionError, match="work has begun"):
            transition_commercial(started, CommercialOrderStatus.CANCELLED)


def test_the_reviewed_cancellation_now_has_a_way_to_succeed() -> None:
    review = transition_commercial(
        state(commercial=CommercialOrderStatus.ACTIVE, intake=IntakeStatus.ACCEPTED),
        CommercialOrderStatus.CANCELLATION_REVIEW,
    )

    with pytest.raises(OrderTransitionError, match="HUMAN_APPROVAL_REQUIRED"):
        transition_commercial(review, CommercialOrderStatus.CANCELLED)

    cancelled = transition_commercial(
        review,
        CommercialOrderStatus.CANCELLED,
        cancellation_approved=True,
        custody_and_financial_resolution_recorded=True,
    )
    assert cancelled.commercial is CommercialOrderStatus.CANCELLED


def test_custody_not_acceptance_is_what_a_cancellation_must_resolve() -> None:
    """The guard read `intake is ACCEPTED` and so caught only the last of six intake states.

    The shop takes physical custody at `RECEIVED_PENDING_INSPECTION` -- four stages earlier. An
    order sitting in any of those states has a customer's laundry on the counter, and used to
    cancel outright with nothing recorded about where the goods went.
    """

    holding_the_goods = (
        IntakeStatus.RECEIVED_PENDING_INSPECTION,
        IntakeStatus.WAITING_PRICE_APPROVAL,
        IntakeStatus.WAITING_CUSTOMER_RECONFIRMATION,
        IntakeStatus.WAITING_SLOT_APPROVAL,
        IntakeStatus.ACCEPTED,
    )
    for intake in holding_the_goods:
        with pytest.raises(OrderTransitionError, match="work has begun"):
            transition_commercial(
                state(commercial=CommercialOrderStatus.CONFIRMED, intake=intake),
                CommercialOrderStatus.CANCELLED,
            )

    # The two states where the shop holds nothing stay free: before handover, and after a
    # rejection that returned the goods. `transition_intake` refuses `-> REJECTED` from
    # `AWAITING_HANDOFF` because custody was never received, which is the same boundary.
    for intake in (IntakeStatus.AWAITING_HANDOFF, IntakeStatus.REJECTED):
        assert (
            transition_commercial(
                state(commercial=CommercialOrderStatus.CONFIRMED, intake=intake),
                CommercialOrderStatus.CANCELLED,
            ).commercial
            is CommercialOrderStatus.CANCELLED
        )


def test_a_resolution_the_order_record_contradicts_is_refused() -> None:
    """Making a resolution the approval did not make it true.

    Only contradictions are refused, never judgements: the shop records custody at handover and
    records when production starts, so both of these claims are checkable against what the system
    already wrote down.
    """

    def review(intake: IntakeStatus, production: ProductionStatus) -> OrderState:
        return state(
            commercial=CommercialOrderStatus.CANCELLATION_REVIEW,
            intake=intake,
            production=production,
        )

    def cancel(order: OrderState, resolution: CustodyResolution) -> OrderState:
        return transition_commercial(
            order,
            CommercialOrderStatus.CANCELLED,
            cancellation_approved=True,
            custody_and_financial_resolution_recorded=True,
            custody_resolution=resolution,
        )

    with pytest.raises(OrderTransitionError, match="never received"):
        cancel(
            review(IntakeStatus.ACCEPTED, ProductionStatus.NOT_STARTED),
            CustodyResolution.NOT_RECEIVED,
        )
    with pytest.raises(OrderTransitionError, match="returned unwashed"):
        cancel(
            review(IntakeStatus.ACCEPTED, ProductionStatus.QUALITY_CHECK),
            CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        )

    # The same two resolutions, on orders whose records agree with them.
    assert (
        cancel(
            review(IntakeStatus.AWAITING_HANDOFF, ProductionStatus.NOT_STARTED),
            CustodyResolution.NOT_RECEIVED,
        ).commercial
        is CommercialOrderStatus.CANCELLED
    )
    assert (
        cancel(
            review(IntakeStatus.ACCEPTED, ProductionStatus.NOT_STARTED),
            CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        ).commercial
        is CommercialOrderStatus.CANCELLED
    )
