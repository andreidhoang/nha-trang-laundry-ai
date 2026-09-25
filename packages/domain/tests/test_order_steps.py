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
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.order_steps import (
    COMPOSITE_STEPS,
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
from nha_trang_laundry_domain.orders import OrderState, OrderTransitionError
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
        [S.MARK_READY, S.HOLD, S.CANCEL, S.PREPAY],
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
        [S.HOLD, S.RELEASE, S.CANCEL, S.SETTLE, S.PREPAY],
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
        [S.HOLD, S.RELEASE, S.CANCEL, S.COLLECT],
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
        [S.HOLD, S.RELEASE, S.CANCEL, S.SETTLE, S.PREPAY],
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
        [S.HOLD, S.RELEASE, S.CANCEL, S.COLLECT],
        S.COLLECT,
    ),
    # --- delivery back to the customer (DEC-023: paid at the counter before it leaves)
    *[
        (
            f"{mode} ready unpaid",
            _active(mode, production=P.READY_AT_STORE, pickup_done=True),
            [S.HOLD, S.RELEASE, S.CANCEL, S.PREPAY, S.DELIVERY_RETURN],
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
            [S.HOLD, S.RELEASE, S.CANCEL, S.DELIVERY_RETURN],
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
        [S.RECEIVE],
        S.RECEIVE,
    ),
    (
        "old UI accepted intake, commercial CONFIRMED",
        _facts(commercial=C.CONFIRMED, intake=I.ACCEPTED),
        # `transition_production` asks only that intake is accepted and the order is open, so the
        # domain allows washing before commercial ACTIVE. Listed because it is legal; not primary.
        [S.RECEIVE, S.START_WASH],
        S.RECEIVE,
    ),
    (
        "cancellation review",
        _facts(commercial=C.CANCELLATION_REVIEW, intake=I.ACCEPTED),
        # The same domain fact: production does not consult the commercial axis short of closure.
        [S.START_WASH, S.CANCEL, S.REOPEN],
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
                resolution = (
                    listed[step].custody_resolutions[0]
                    if listed[step].custody_resolutions
                    else None
                )
                assert _plan(step, facts, custody_resolution=resolution)
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
