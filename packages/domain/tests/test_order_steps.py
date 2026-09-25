"""`ORDER-STEPS-001`: next steps and composite steps, decided by the domain state machines.

Pure tests over `order_steps`. They pin the vocabulary a console codes against: which steps are
legal at every lifecycle stage of every fulfilment mode, prepaid and paid at pickup, which one is
primary, and what each composite step is made of. The database tests prove the same plans are what
gets written; these prove what the plans are.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    IntakeRejectionReason,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
    RewashReason,
)
from nha_trang_laundry_domain.order_steps import (
    COMPOSITE_STEPS,
    NEVER_PRIMARY_STEPS,
    NextStep,
    OrderStep,
    PlannedTransition,
    QuoteReadinessFacts,
    StepFacts,
    StepRequiresHuman,
    derive_intake_readiness,
    next_steps,
    plan_step,
    readiness_blockers,
)
from nha_trang_laundry_domain.orders import (
    OrderState,
    OrderTransitionError,
    transition_commercial,
    transition_intake,
    transition_production,
)
from nha_trang_laundry_domain.settlement import QuotedTotal, SettlementShape

NOW = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
READY_QUOTE = QuoteReadinessFacts(True, True, True, True)
SELF = FulfillmentMode.SELF_DROP_SELF_COLLECT
P_AND_R = FulfillmentMode.PICKUP_AND_RETURN
P_ONLY = FulfillmentMode.PICKUP_ONLY
R_ONLY = FulfillmentMode.RETURN_ONLY
C = CommercialOrderStatus
I = IntakeStatus  # noqa: E741
P = ProductionStatus
S = OrderStep


def _facts(
    mode: FulfillmentMode = SELF,
    *,
    commercial: CommercialOrderStatus = C.REQUESTED,
    intake: IntakeStatus = I.AWAITING_HANDOFF,
    production: ProductionStatus = P.NOT_STARTED,
    balance: OrderBalanceStatus = OrderBalanceStatus.UNPAID,
    legs_done: bool = False,
    collected: bool = False,
    resume: ProductionStatus | None = None,
    shape: SettlementShape | None = None,
    pickup_done: bool = False,
    quote: QuoteReadinessFacts = READY_QUOTE,
    total: int | None = 110_000,
) -> StepFacts:
    return StepFacts(
        state=OrderState(
            commercial=commercial,
            intake=intake,
            production=production,
            fulfillment_mode=mode,
            balance=balance,
            required_delivery_legs_succeeded=legs_done,
            self_collection_recorded=collected,
            production_accepted_at=NOW if intake is I.ACCEPTED else None,
            production_resume_status=resume,
        ),
        quote_readiness=quote,
        quoted_total=QuotedTotal(total, total),
        settlement_shape=shape,
        pickup_leg_succeeded=pickup_done,
    )


def _active(mode: FulfillmentMode = SELF, **kwargs: object) -> StepFacts:
    return _facts(mode, commercial=C.ACTIVE, intake=I.ACCEPTED, **kwargs)  # type: ignore[arg-type]


def _listed(facts: StepFacts) -> tuple[list[OrderStep], OrderStep | None]:
    steps = next_steps(facts)
    primaries = [item.step for item in steps if item.primary]
    assert len(primaries) <= 1
    if steps:
        assert len(primaries) == 1, steps
    return [item.step for item in steps], (primaries[0] if primaries else None)


def _plan(step: OrderStep, facts: StepFacts, **kwargs: object) -> tuple[PlannedTransition, ...]:
    options: dict[str, object] = {"slot_approved": True, "custody_resolution": None}
    options.update(kwargs)
    return plan_step(step, facts, accepted_at=NOW, **options)  # type: ignore[arg-type]


def _targets(plan: tuple[PlannedTransition, ...]) -> list[str]:
    return [
        f"{'commercial' if p.commercial_target else 'intake' if p.intake_target else 'production'}"
        f":{(p.commercial_target or p.intake_target or p.production_target)}"
        for p in plan
    ]


# --- the lifecycle table -------------------------------------------------------------------------

#: (stage, facts) -> (listed steps in order, primary). Every mode, every stage, both payment paths.
LIFECYCLE: list[tuple[str, StepFacts, list[OrderStep], OrderStep]] = [
    # --- a new order, any mode: receive, or cancel for free
    *[
        (f"new {mode}", _facts(mode), [S.RECEIVE, S.CANCEL], S.RECEIVE)
        for mode in (SELF, P_AND_R, P_ONLY, R_ONLY)
    ],
    # --- received and active, not started
    *[
        (
            f"accepted {mode}",
            _active(mode),
            [S.START_WASH, S.CANCEL, S.PREPAY, *extra],
            S.START_WASH,
        )
        for mode, extra in (
            (SELF, []),
            (P_AND_R, [S.DELIVERY_PICKUP]),
            (P_ONLY, [S.DELIVERY_PICKUP]),
            (R_ONLY, []),
        )
    ],
    (
        "washing",
        _active(production=P.IN_PROCESS),
        [S.QUALITY_CHECK, S.HOLD, S.CANCEL, S.PREPAY],
        S.QUALITY_CHECK,
    ),
    (
        "checking",
        _active(production=P.QUALITY_CHECK),
        [S.MARK_READY, S.HOLD, S.REWASH, S.CANCEL, S.PREPAY],
        S.MARK_READY,
    ),
    (
        "held",
        _active(production=P.ON_HOLD, resume=P.IN_PROCESS),
        [S.RESUME, S.CANCEL, S.PREPAY],
        S.RESUME,
    ),
    (
        "exception",
        _active(production=P.EXCEPTION, resume=P.QUALITY_CHECK),
        [S.RESUME, S.CANCEL, S.PREPAY],
        S.RESUME,
    ),
    # --- self-collect, pay at pickup
    (
        "self ready unpaid",
        _active(SELF, production=P.READY_AT_STORE),
        # Founder ruling 2026-09-25: no RELEASE while unpaid for a customer who collects.
        [S.HOLD, S.REWASH, S.CANCEL, S.SETTLE, S.PREPAY],
        S.SETTLE,
    ),
    (
        "self ready paid+collected",
        _active(
            SELF,
            production=P.READY_AT_STORE,
            balance=OrderBalanceStatus.PAID,
            collected=True,
            shape=SettlementShape.EXACT_PAYMENT_SELF_COLLECTION,
        ),
        [S.HOLD, S.HAND_OVER, S.CANCEL],
        S.HAND_OVER,
    ),
    # --- self-collect, prepaid at drop-off (DEC-032)
    (
        "self washing prepaid",
        _active(
            SELF,
            production=P.IN_PROCESS,
            balance=OrderBalanceStatus.PAID,
            shape=SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
        ),
        [S.QUALITY_CHECK, S.HOLD, S.CANCEL],
        S.QUALITY_CHECK,
    ),
    (
        "self ready prepaid",
        _active(
            SELF,
            production=P.READY_AT_STORE,
            balance=OrderBalanceStatus.PAID,
            shape=SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
        ),
        [S.HOLD, S.REWASH, S.RELEASE, S.CANCEL, S.COLLECT],
        S.COLLECT,
    ),
    (
        "self ready prepaid collected",
        _active(
            SELF,
            production=P.READY_AT_STORE,
            balance=OrderBalanceStatus.PAID,
            collected=True,
            shape=SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
        ),
        [S.HOLD, S.HAND_OVER, S.CANCEL],
        S.HAND_OVER,
    ),
    # --- pickup only: courier fetched it, customer collects at the counter
    (
        "pickup-only ready unpaid",
        _active(P_ONLY, production=P.READY_AT_STORE, pickup_done=True),
        [S.HOLD, S.REWASH, S.CANCEL, S.SETTLE, S.PREPAY],
        S.SETTLE,
    ),
    (
        "pickup-only ready prepaid",
        _active(
            P_ONLY,
            production=P.READY_AT_STORE,
            balance=OrderBalanceStatus.PAID,
            shape=SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
            pickup_done=True,
        ),
        [S.HOLD, S.REWASH, S.RELEASE, S.CANCEL, S.COLLECT],
        S.COLLECT,
    ),
    # --- delivery back to the customer (DEC-023: paid at the counter before it leaves)
    *[
        (
            f"{mode} ready unpaid",
            _active(mode, production=P.READY_AT_STORE, pickup_done=True),
            [S.HOLD, S.REWASH, S.RELEASE, S.CANCEL, S.PREPAY, S.DELIVERY_RETURN],
            S.PREPAY,
        )
        for mode in (P_AND_R, R_ONLY)
    ],
    *[
        (
            f"{mode} ready prepaid",
            _active(
                mode,
                production=P.READY_AT_STORE,
                balance=OrderBalanceStatus.PAID,
                shape=SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY,
                pickup_done=True,
            ),
            [S.HOLD, S.REWASH, S.RELEASE, S.CANCEL, S.DELIVERY_RETURN],
            S.RELEASE,
        )
        for mode in (P_AND_R, R_ONLY)
    ],
    *[
        (
            f"{mode} out for delivery",
            _active(
                mode,
                production=P.RELEASED,
                balance=OrderBalanceStatus.PAID,
                shape=SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY,
                pickup_done=True,
            ),
            [S.CANCEL, S.DELIVERY_RETURN],
            S.DELIVERY_RETURN,
        )
        for mode in (P_AND_R, R_ONLY)
    ],
    *[
        (
            f"{mode} delivered",
            _active(
                mode,
                production=P.RELEASED,
                balance=OrderBalanceStatus.PAID,
                shape=SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY,
                legs_done=True,
                pickup_done=True,
            ),
            [S.COMPLETE, S.CANCEL],
            S.COMPLETE,
        )
        for mode in (P_AND_R, R_ONLY)
    ],
    # --- partly advanced by the per-axis routes: RECEIVE still finishes the job
    (
        "old UI stopped at CONFIRMED",
        _facts(commercial=C.CONFIRMED, intake=I.RECEIVED_PENDING_INSPECTION),
        [S.RECEIVE, S.REJECT_INTAKE],
        S.RECEIVE,
    ),
    (
        "old UI accepted intake, commercial CONFIRMED",
        _facts(commercial=C.CONFIRMED, intake=I.ACCEPTED),
        # `transition_production` alone would allow washing before commercial ACTIVE; the founder
        # ruling of 2026-09-25 makes START_WASH an active-order step, so RECEIVE finishes first.
        [S.RECEIVE],
        S.RECEIVE,
    ),
    (
        "cancellation review",
        _facts(commercial=C.CANCELLATION_REVIEW, intake=I.ACCEPTED),
        # No washing while a cancellation is being decided (founder ruling 2026-09-25).
        [S.CANCEL, S.REOPEN],
        S.CANCEL,
    ),
    # --- closed
    (
        "completed",
        _facts(commercial=C.COMPLETED, intake=I.ACCEPTED, production=P.RELEASED),
        [],
        S.RECEIVE,
    ),
    ("cancelled", _facts(commercial=C.CANCELLED), [], S.RECEIVE),
]


@pytest.mark.parametrize(
    ("facts", "expected", "primary"),
    [
        pytest.param(facts, expected, primary, id=stage)
        for stage, facts, expected, primary in LIFECYCLE
    ],
)
def test_next_steps_at_every_stage(
    facts: StepFacts, expected: list[OrderStep], primary: OrderStep
) -> None:
    listed, chosen = _listed(facts)
    assert listed == expected
    if expected:
        assert chosen is primary
    else:
        assert chosen is None


def test_receive_says_it_needs_the_slot_attestation() -> None:
    receive = next(item for item in next_steps(_facts()) if item.step is S.RECEIVE)
    assert receive == NextStep(S.RECEIVE, True, ("slot_approved",), ())


def test_a_cancellation_through_review_lists_exactly_the_resolutions_the_domain_accepts() -> None:
    unwashed = next(item for item in next_steps(_active()) if item.step is S.CANCEL)
    assert unwashed.requires == ("custody_resolution",)
    # Received, so never "not received"; not washed, so "returned unwashed" is true.
    assert unwashed.custody_resolutions == (
        CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        CustodyResolution.SHOP_FAULT_NO_CHARGE,
    )
    washed = next(
        item for item in next_steps(_active(production=P.IN_PROCESS)) if item.step is S.CANCEL
    )
    assert washed.custody_resolutions == (CustodyResolution.SHOP_FAULT_NO_CHARGE,)
    direct = next(item for item in next_steps(_facts()) if item.step is S.CANCEL)
    assert direct.requires == () and direct.custody_resolutions == ()


def test_a_quote_with_no_single_total_offers_no_money_step() -> None:
    listed, _ = _listed(_active(SELF, production=P.READY_AT_STORE, total=None))
    assert S.SETTLE not in listed and S.PREPAY not in listed


# --- composite plans -----------------------------------------------------------------------------


def test_receive_from_a_new_order_is_the_five_audited_transitions_in_order() -> None:
    plan = _plan(S.RECEIVE, _facts())
    assert _targets(plan) == [
        "intake:RECEIVED_PENDING_INSPECTION",
        "commercial:STORE_CONFIRMATION_PENDING",
        "commercial:CONFIRMED",
        "intake:ACCEPTED",
        "commercial:ACTIVE",
    ]
    accept = plan[3]
    assert accept.intake_readiness is not None and accept.intake_readiness.ready


@pytest.mark.parametrize(
    ("commercial", "intake", "expected"),
    [
        (
            C.STORE_CONFIRMATION_PENDING,
            I.RECEIVED_PENDING_INSPECTION,
            ["commercial:CONFIRMED", "intake:ACCEPTED", "commercial:ACTIVE"],
        ),
        (C.CONFIRMED, I.WAITING_SLOT_APPROVAL, ["intake:ACCEPTED", "commercial:ACTIVE"]),
        (C.CONFIRMED, I.ACCEPTED, ["commercial:ACTIVE"]),
        (
            C.CONFIRMED,
            I.AWAITING_HANDOFF,
            ["intake:RECEIVED_PENDING_INSPECTION", "intake:ACCEPTED", "commercial:ACTIVE"],
        ),
    ],
)
def test_receive_finishes_what_the_per_axis_routes_started(
    commercial: CommercialOrderStatus, intake: IntakeStatus, expected: list[str]
) -> None:
    assert _targets(_plan(S.RECEIVE, _facts(commercial=commercial, intake=intake))) == expected


def test_receive_without_the_slot_attestation_is_refused_whole() -> None:
    with pytest.raises(StepRequiresHuman) as refused:
        _plan(S.RECEIVE, _facts(), slot_approved=False)
    assert refused.value.reason_codes == ("SLOT_APPROVAL_REQUIRED",)
    assert str(refused.value).startswith("HUMAN_APPROVAL_REQUIRED: intake blockers remain")


def test_receive_names_every_missing_readiness_fact() -> None:
    quote = QuoteReadinessFacts(False, True, False, False)
    with pytest.raises(StepRequiresHuman) as refused:
        _plan(S.RECEIVE, _facts(quote=quote), slot_approved=False)
    assert refused.value.reason_codes == (
        "QUANTITY_NOT_MEASURED",
        "EXACT_PRICE_NOT_APPROVED",
        "CUSTOMER_AGREEMENT_MISSING",
        "SLOT_APPROVAL_REQUIRED",
    )
    assert S.RECEIVE not in _listed(_facts(quote=quote))[0]


def test_start_wash_queues_first_when_nothing_has_started() -> None:
    assert _targets(_plan(S.START_WASH, _active())) == [
        "production:QUEUED",
        "production:IN_PROCESS",
    ]
    assert _targets(_plan(S.START_WASH, _active(production=P.QUEUED))) == ["production:IN_PROCESS"]


@pytest.mark.parametrize(
    ("production", "resume"),
    [(P.ON_HOLD, P.IN_PROCESS), (P.EXCEPTION, P.READY_AT_STORE), (P.IN_PROCESS, None)],
)
def test_start_wash_is_not_a_resume_or_a_rewash(
    production: ProductionStatus, resume: ProductionStatus | None
) -> None:
    with pytest.raises(OrderTransitionError):
        _plan(S.START_WASH, _active(production=production, resume=resume))


def test_hand_over_is_release_then_complete_and_refused_while_unpaid() -> None:
    paid = _active(
        production=P.READY_AT_STORE,
        balance=OrderBalanceStatus.PAID,
        collected=True,
        shape=SettlementShape.EXACT_PAYMENT_SELF_COLLECTION,
    )
    assert _targets(_plan(S.HAND_OVER, paid)) == ["production:RELEASED", "commercial:COMPLETED"]
    released = replace(paid, state=replace(paid.state, production=P.RELEASED))
    assert _targets(_plan(S.HAND_OVER, released)) == ["commercial:COMPLETED"]
    with pytest.raises(OrderTransitionError, match="balance is not settled"):
        _plan(S.HAND_OVER, _active(production=P.READY_AT_STORE, collected=True))


def test_cancel_plans() -> None:
    assert _targets(_plan(S.CANCEL, _facts())) == ["commercial:CANCELLED"]
    active = _plan(
        S.CANCEL, _active(), custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED
    )
    assert _targets(active) == ["commercial:CANCELLATION_REVIEW", "commercial:CANCELLED"]
    assert active[0].custody_resolution is None
    assert active[1].custody_resolution is CustodyResolution.RETURNED_UNWASHED_REFUNDED
    with pytest.raises(OrderTransitionError, match="cancellation resolution is incomplete"):
        _plan(S.CANCEL, _active())
    with pytest.raises(OrderTransitionError, match="custody of the goods"):
        _plan(S.CANCEL, _active(), custody_resolution=CustodyResolution.NOT_RECEIVED)


def test_money_and_delivery_steps_are_not_composite() -> None:
    for step in (S.SETTLE, S.PREPAY, S.COLLECT, S.DELIVERY_PICKUP, S.DELIVERY_RETURN):
        assert step not in COMPOSITE_STEPS
        with pytest.raises(OrderTransitionError, match="its own route"):
            _plan(step, _active())


def test_every_listed_composite_step_plans_successfully_and_every_other_is_refused() -> None:
    """`next_steps` is `plan_step` asked in advance: the two can never disagree."""

    for _stage, facts, _expected, _primary in LIFECYCLE:
        listed = {item.step: item for item in next_steps(facts)}
        for step in COMPOSITE_STEPS:
            if step in listed:
                entry = listed[step]
                assert _plan(
                    step,
                    facts,
                    custody_resolution=next(iter(entry.custody_resolutions), None),
                    rewash_reason=next(iter(entry.rewash_reasons), None),
                    rejection_reason=next(iter(entry.rejection_reasons), None),
                )
            elif step in {S.RELEASE, S.HAND_OVER}:
                continue  # one of the pair is suppressed in favour of the other on purpose
            else:
                with pytest.raises(OrderTransitionError):
                    _plan(step, facts)


def test_readiness_derivation_is_the_one_both_routes_share() -> None:
    readiness = derive_intake_readiness(I.AWAITING_HANDOFF, READY_QUOTE, slot_approved=False)
    assert readiness_blockers(readiness) == ("CUSTODY_NOT_RECORDED", "SLOT_APPROVAL_REQUIRED")
    received = derive_intake_readiness(
        I.RECEIVED_PENDING_INSPECTION, READY_QUOTE, slot_approved=True
    )
    assert received.ready and readiness_blockers(received) == ()


def test_start_wash_is_refused_on_an_order_that_is_not_active() -> None:
    """Founder ruling 2026-09-25: the step executor refuses, not only the button list."""

    for commercial in (C.CONFIRMED, C.CANCELLATION_REVIEW):
        facts = _facts(commercial=commercial, intake=I.ACCEPTED)
        with pytest.raises(OrderTransitionError, match="active order"):
            plan_step(
                S.START_WASH,
                facts,
                slot_approved=False,
                custody_resolution=None,
                accepted_at=datetime(2026, 9, 25, tzinfo=UTC),
            )


def test_an_unpaid_self_collect_order_is_not_released_on_its_own() -> None:
    """Founder ruling 2026-09-25: the goods leave a counter order with its payment."""

    for facts in (
        _active(SELF, production=P.READY_AT_STORE),
        _active(P_ONLY, production=P.READY_AT_STORE, pickup_done=True),
    ):
        with pytest.raises(OrderTransitionError, match="taking payment"):
            plan_step(
                S.RELEASE,
                facts,
                slot_approved=False,
                custody_resolution=None,
                accepted_at=datetime(2026, 9, 25, tzinfo=UTC),
            )


# --- ORDER-STEPS-002: rewash and refuse-at-intake ------------------------------------------------

_RESUME_FOR: dict[ProductionStatus, ProductionStatus] = {
    P.ON_HOLD: P.IN_PROCESS,
    P.EXCEPTION: P.QUALITY_CHECK,
}


def _apply(facts: StepFacts, plan: tuple[PlannedTransition, ...]) -> StepFacts:
    """Run a plan through the real transition functions, as the repository writes it."""

    state = facts.state
    for planned in plan:
        if planned.production_target is not None:
            state = transition_production(state, planned.production_target)
        elif planned.intake_target is not None:
            state = transition_intake(
                state,
                planned.intake_target,
                readiness=planned.intake_readiness,
                production_accepted_at=NOW,
            )
        else:
            assert planned.commercial_target is not None
            resolved = planned.custody_resolution is not None
            state = transition_commercial(
                state,
                planned.commercial_target,
                cancellation_approved=resolved,
                custody_and_financial_resolution_recorded=resolved,
                custody_resolution=planned.custody_resolution,
            )
    return replace(facts, state=state)


@pytest.mark.parametrize("production", list(ProductionStatus))
@pytest.mark.parametrize(
    "commercial", [C.ACTIVE, C.CONFIRMED, C.CANCELLATION_REVIEW, C.COMPLETED, C.CANCELLED]
)
def test_rewash_is_legal_exactly_at_quality_check_or_on_the_shelf_of_an_active_order(
    commercial: CommercialOrderStatus, production: ProductionStatus
) -> None:
    facts = _facts(
        commercial=commercial,
        intake=I.ACCEPTED,
        production=production,
        resume=_RESUME_FOR.get(production),
    )
    legal = commercial is C.ACTIVE and production in {P.QUALITY_CHECK, P.READY_AT_STORE}
    listed = {item.step: item for item in next_steps(facts)}
    assert (S.REWASH in listed) is legal
    if legal:
        plan = _plan(S.REWASH, facts, rewash_reason=RewashReason.NOT_CLEAN)
        assert _targets(plan) == ["production:EXCEPTION", "production:IN_PROCESS"]
        assert listed[S.REWASH] == NextStep(
            S.REWASH, False, ("rewash_reason",), rewash_reasons=tuple(RewashReason)
        )
    else:
        with pytest.raises(OrderTransitionError, match="INVALID_STATE_TRANSITION"):
            _plan(S.REWASH, facts, rewash_reason=RewashReason.NOT_CLEAN)


_BEFORE_ACTIVE = (C.DRAFT, C.REQUESTED, C.STORE_CONFIRMATION_PENDING, C.CONFIRMED)
_RECEIVED_WAITING = (
    I.RECEIVED_PENDING_INSPECTION,
    I.WAITING_PRICE_APPROVAL,
    I.WAITING_CUSTOMER_RECONFIRMATION,
    I.WAITING_SLOT_APPROVAL,
)


@pytest.mark.parametrize("intake", list(IntakeStatus))
@pytest.mark.parametrize("commercial", list(CommercialOrderStatus))
def test_goods_are_refused_only_on_the_counter_before_the_order_is_live(
    commercial: CommercialOrderStatus, intake: IntakeStatus
) -> None:
    facts = _facts(commercial=commercial, intake=intake)
    legal = commercial in _BEFORE_ACTIVE and intake in _RECEIVED_WAITING
    listed = {item.step: item for item in next_steps(facts)}
    assert (S.REJECT_INTAKE in listed) is legal
    if legal:
        plan = _plan(S.REJECT_INTAKE, facts, rejection_reason=IntakeRejectionReason.NOT_SERVICEABLE)
        assert _targets(plan) == ["intake:REJECTED", "commercial:CANCELLED"]
        # A direct cancellation: no custody answer is asked for or carried.
        assert all(item.custody_resolution is None for item in plan)
        assert listed[S.REJECT_INTAKE] == NextStep(
            S.REJECT_INTAKE,
            False,
            ("rejection_reason",),
            rejection_reasons=tuple(IntakeRejectionReason),
        )
    else:
        with pytest.raises(OrderTransitionError):
            _plan(S.REJECT_INTAKE, facts, rejection_reason=IntakeRejectionReason.OTHER)


def test_the_reason_rides_on_the_first_transition_of_the_step_only() -> None:
    rewash = _plan(
        S.REWASH, _active(production=P.READY_AT_STORE), rewash_reason=RewashReason.MACHINE_FAULT
    )
    assert [item.rewash_reason for item in rewash] == [RewashReason.MACHINE_FAULT, None]
    assert all(item.rejection_reason is None for item in rewash)
    refused = _plan(
        S.REJECT_INTAKE,
        _facts(intake=I.WAITING_PRICE_APPROVAL),
        rejection_reason=IntakeRejectionReason.DAMAGED_ON_ARRIVAL,
    )
    assert [item.rejection_reason for item in refused] == [
        IntakeRejectionReason.DAMAGED_ON_ARRIVAL,
        None,
    ]
    assert all(item.rewash_reason is None for item in refused)


def test_each_step_needs_its_reason_and_takes_no_other() -> None:
    checking = _active(production=P.QUALITY_CHECK)
    received = _facts(intake=I.RECEIVED_PENDING_INSPECTION)
    with pytest.raises(OrderTransitionError, match="VALIDATION_ERROR: REWASH requires"):
        _plan(S.REWASH, checking)
    with pytest.raises(OrderTransitionError, match="VALIDATION_ERROR: REJECT_INTAKE requires"):
        _plan(S.REJECT_INTAKE, received)
    with pytest.raises(OrderTransitionError, match="rewash_reason is taken only by REWASH"):
        _plan(S.MARK_READY, checking, rewash_reason=RewashReason.NOT_CLEAN)
    with pytest.raises(OrderTransitionError, match="rewash_reason is taken only by REWASH"):
        _plan(
            S.REJECT_INTAKE,
            received,
            rewash_reason=RewashReason.NOT_CLEAN,
            rejection_reason=IntakeRejectionReason.OTHER,
        )
    with pytest.raises(OrderTransitionError, match="rejection_reason is taken only by"):
        _plan(S.RECEIVE, received, rejection_reason=IntakeRejectionReason.OTHER)
    with pytest.raises(OrderTransitionError, match="custody_resolution is not taken by REWASH"):
        _plan(
            S.REWASH,
            checking,
            rewash_reason=RewashReason.OTHER,
            custody_resolution=CustodyResolution.SHOP_FAULT_NO_CHARGE,
        )
    with pytest.raises(OrderTransitionError, match="not taken by REJECT_INTAKE"):
        _plan(
            S.REJECT_INTAKE,
            received,
            rejection_reason=IntakeRejectionReason.OTHER,
            custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        )


def test_no_rewash_once_the_customer_is_recorded_as_having_taken_the_goods() -> None:
    """R1: a garment brought back after collection is a complaint (DEC-004), not a rewash."""

    collected = _active(
        production=P.READY_AT_STORE,
        balance=OrderBalanceStatus.PAID,
        collected=True,
        shape=SettlementShape.EXACT_PAYMENT_SELF_COLLECTION,
    )
    assert S.REWASH not in _listed(collected)[0]
    with pytest.raises(OrderTransitionError, match="complaint"):
        _plan(S.REWASH, collected, rewash_reason=RewashReason.NOT_CLEAN)


def test_a_prepaid_order_is_rewashed_and_its_balance_is_untouched() -> None:
    """R1: a rewash costs the customer nothing; the plan never touches money."""

    prepaid = _active(
        production=P.READY_AT_STORE,
        balance=OrderBalanceStatus.PAID,
        shape=SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION,
    )
    after = _apply(prepaid, _plan(S.REWASH, prepaid, rewash_reason=RewashReason.NOT_CLEAN))
    assert after.state.balance is OrderBalanceStatus.PAID
    assert after.quoted_total == prepaid.quoted_total


@pytest.mark.parametrize("start", [P.QUALITY_CHECK, P.READY_AT_STORE])
def test_after_a_rewash_the_order_walks_forward_again(start: ProductionStatus) -> None:
    facts = _active(production=start)
    after = _apply(facts, _plan(S.REWASH, facts, rewash_reason=RewashReason.NOT_CLEAN))
    assert after.state.production is P.IN_PROCESS
    assert after.state.production_resume_status is None
    assert _listed(after)[1] is S.QUALITY_CHECK
    checked = _apply(after, _plan(S.QUALITY_CHECK, after))
    ready = _apply(checked, _plan(S.MARK_READY, checked))
    assert ready.state.production is P.READY_AT_STORE
    assert _listed(ready)[1] is S.SETTLE


def test_after_a_refusal_the_order_is_cancelled_and_offers_nothing() -> None:
    facts = _facts(commercial=C.CONFIRMED, intake=I.WAITING_SLOT_APPROVAL)
    after = _apply(
        facts,
        _plan(S.REJECT_INTAKE, facts, rejection_reason=IntakeRejectionReason.NOT_SERVICEABLE),
    )
    assert (after.state.commercial, after.state.intake) == (C.CANCELLED, I.REJECTED)
    assert after.state.balance is OrderBalanceStatus.UNPAID
    assert next_steps(after) == ()
    with pytest.raises(OrderTransitionError, match="order is closed"):
        _plan(S.REJECT_INTAKE, after, rejection_reason=IntakeRejectionReason.OTHER)


def test_neither_step_is_ever_primary() -> None:
    assert frozenset({S.REWASH, S.REJECT_INTAKE}) == NEVER_PRIMARY_STEPS
    for stage, facts, _expected, _primary in LIFECYCLE:
        for item in next_steps(facts):
            assert not (item.primary and item.step in NEVER_PRIMARY_STEPS), stage


def test_a_refusal_alone_is_listed_without_any_primary() -> None:
    """Goods on the counter whose quote is not final: RECEIVE is blocked, refusing is not.

    The one position where a never-primary step is the only legal one. The list names it and marks
    nothing primary, rather than promoting "Không nhận đồ" to the big button.
    """

    blocked = _facts(
        intake=I.RECEIVED_PENDING_INSPECTION, quote=QuoteReadinessFacts(True, True, False, False)
    )
    steps = next_steps(blocked)
    assert [item.step for item in steps] == [S.REJECT_INTAKE]
    assert not any(item.primary for item in steps)


def test_rewash_is_not_the_resume_of_an_exception_or_a_hold() -> None:
    """An order already interrupted resumes; it is not rewashed a second time from there."""

    for production in (P.ON_HOLD, P.EXCEPTION):
        facts = _active(production=production, resume=P.QUALITY_CHECK)
        with pytest.raises(OrderTransitionError, match="quality check or from the shelf"):
            _plan(S.REWASH, facts, rewash_reason=RewashReason.NOT_CLEAN)
