"""`DEC-004` as deterministic code: the figures apply, and the one they do not cover refuses.

The persistence tests walk the whole thread; these pin the decisions themselves, where a wrong
answer is cheapest to see. Three of them are the ones that matter:

* the refusal registry is complete against its enum, so a refusal added later cannot ship without an
  invariant or decision owning it;
* loss carries no figure anywhere on it, because `DEC-004` declined to give one;
* the allocator that spreads a credit across lines is the promotion allocator, so "who gets the
  spare dong" has one answer in this system rather than two.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import AdjustmentDirection, PolicyOutcome, PromotionResolution
from nha_trang_laundry_domain.promotion import CURRENT_PROMOTION as _PROMOTION
from nha_trang_laundry_domain.promotion import (
    LARGEST_REMAINDER_RULE,
    PromotionLine,
    allocate_largest_remainder,
    evaluate_promotion,
)
from nha_trang_laundry_domain.remedies import (
    NO_PRIOR_COMMITMENTS,
    REMEDY_REFUSAL_AUTHORITIES,
    RemedyAuthorized,
    RemedyCommitments,
    RemedyKind,
    RemedyOrderFacts,
    RemedyPolicy,
    RemedyPolicyError,
    RemedyRefusal,
    RemedyRefused,
    RemedyRequest,
    RemedyUnresolved,
    allocate_remedy_credit,
    evaluate_remedy,
    parse_remedy_policy,
)

NOW = datetime(2026, 9, 18, 3, tzinfo=UTC)
RETURNED = NOW - timedelta(hours=1)

POLICY_PAYLOAD = {
    "schema": "remedy-policy-v1",
    "decision": "DEC-004",
    "defect_report_window_hours": 24,
    "initial_response_window_hours": 24,
    "free_rewash_window_days": 7,
    "late_delivery_threshold_minutes": 120,
    "late_delivery_credit_rate_bps": 1000,
    "damage_compensation_multiple": 5,
    "staff_approval_ceiling_vnd": 100_000,
}
POLICY = parse_remedy_policy(POLICY_PAYLOAD)


def facts(**overrides: object) -> RemedyOrderFacts:
    base: dict[str, object] = {
        "goods_returned_at": RETURNED,
        "settled_total_vnd": 110_000,
        "line_amounts_vnd": {"line-1": 100_000, "line-2": 40_000},
        "expects_return_leg": False,
        "return_leg_succeeded": False,
    }
    base.update(overrides)
    return RemedyOrderFacts(**base)  # type: ignore[arg-type]


def decide(
    request: RemedyRequest,
    *,
    at: datetime = NOW,
    committed: RemedyCommitments = NO_PRIOR_COMMITMENTS,
    **fact_overrides: object,
) -> object:
    # `committed` became a required input of `evaluate_remedy` when the figures became cumulative.
    # Every test written before that is about the first proposal against an item, which is exactly
    # what `NO_PRIOR_COMMITMENTS` states; the cumulative tests at the end pass their own.
    return evaluate_remedy(
        policy=POLICY,
        facts=facts(**fact_overrides),
        request=request,
        requested_at=at,
        committed=committed,
    )


# --- the registry ---------------------------------------------------------------------------------


def test_every_refusal_names_the_invariant_or_decision_that_owns_it() -> None:
    """Completeness, both ways, as `test_settlement_policy.py:103` pins settlement's.

    A refusal with no authority tells a caller that something failed without telling them what would
    have to change, which is the failure mode this registry exists to prevent.
    """

    assert set(REMEDY_REFUSAL_AUTHORITIES) == set(RemedyRefusal)
    assert all(value.strip() for value in REMEDY_REFUSAL_AUTHORITIES.values())


def test_a_policy_missing_a_figure_is_not_a_policy() -> None:
    """No field has a default: a gap is a document that must not be published, not one to fill."""

    for field in POLICY_PAYLOAD:
        if field in {"schema", "decision"}:
            continue
        with pytest.raises(RemedyPolicyError):
            parse_remedy_policy({k: v for k, v in POLICY_PAYLOAD.items() if k != field})


@pytest.mark.parametrize(
    "override",
    [
        # `True` is an `int` in Python, so without the explicit exclusion this would be read as 1 d
        # and published as the shop's liability ceiling.
        {"staff_approval_ceiling_vnd": True},
        {"staff_approval_ceiling_vnd": -1},
        {"staff_approval_ceiling_vnd": 100_000.0},
        # A rate above 100% would make the shop owe more than the customer ever paid.
        {"late_delivery_credit_rate_bps": 10_001},
        {"late_delivery_credit_rate_bps": 0},
        {"damage_compensation_multiple": 0},
        {"free_rewash_window_days": 0},
        {"decision": "DEC-010"},
        {"schema": "remedy-policy-v2"},
    ],
)
def test_a_policy_that_is_not_dec_004_is_refused(override: dict[str, object]) -> None:
    with pytest.raises(RemedyPolicyError):
        parse_remedy_policy({**POLICY_PAYLOAD, **override})


# --- loss -----------------------------------------------------------------------------------------


def test_loss_is_recorded_and_carries_no_figure_of_any_kind() -> None:
    """`DEC-004`'s own words: loss is not covered by the damage figures and must not inherit them.

    Deliberately asserts the *absence* of every number. There is no ceiling to assert, and a test
    that asserted one would ratify a figure the owner declined to give.
    """

    outcome = decide(RemedyRequest(kind=RemedyKind.LOST_ITEM, store_fault_attested=True))
    assert isinstance(outcome, RemedyUnresolved)
    assert outcome.outcome is PolicyOutcome.REQUIRE_HUMAN
    assert outcome.reason_code == RemedyRefusal.LOSS_POLICY_UNRESOLVED.value
    assert outcome.authority == "DEC-004"
    assert not hasattr(outcome, "ceiling_vnd")
    assert not hasattr(outcome, "amount_vnd")


def test_loss_refuses_before_any_window_or_fault_check() -> None:
    """The ordering is the point: every later branch reads a figure that does not apply to loss."""

    for at, fault in ((NOW + timedelta(days=400), False), (NOW, False), (NOW, True)):
        outcome = decide(
            RemedyRequest(kind=RemedyKind.LOST_ITEM, store_fault_attested=fault),
            at=at,
            goods_returned_at=None,
        )
        assert isinstance(outcome, RemedyUnresolved)
        assert outcome.reason_code == RemedyRefusal.LOSS_POLICY_UNRESOLVED.value


# --- ceilings and escalation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "needs_owner"),
    [(1, False), (99_999, False), (100_000, False), (100_001, True), (500_000, True)],
)
def test_the_staff_ceiling_is_inclusive(amount: int, needs_owner: bool) -> None:
    outcome = decide(
        RemedyRequest(
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-1",
            amount_vnd=amount,
        )
    )
    assert isinstance(outcome, RemedyAuthorized)
    assert outcome.requires_owner_approval is needs_owner
    assert outcome.direction is AdjustmentDirection.CREDIT
    assert outcome.amount_vnd == amount


def test_the_damage_ceiling_is_five_times_that_line_and_refuses_above_it() -> None:
    """Five times *that item's* fee, not the order's total. `line-2` was charged 40.000 d."""

    inside = decide(
        RemedyRequest(
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-2",
            amount_vnd=200_000,
        )
    )
    assert isinstance(inside, RemedyAuthorized) and inside.ceiling_vnd == 200_000
    outside = decide(
        RemedyRequest(
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-2",
            amount_vnd=200_001,
        )
    )
    assert isinstance(outside, RemedyRefused)
    assert outside.refusal is RemedyRefusal.REMEDY_CEILING_EXCEEDED
    # Named, not applied: the amount is refused rather than truncated to the bound.
    assert outside.ceiling_vnd == 200_000


def test_the_late_delivery_credit_is_ten_percent_of_the_settled_total() -> None:
    outcome = decide(
        RemedyRequest(
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=True,
            attested_late_by_minutes=121,
        ),
        expects_return_leg=True,
        return_leg_succeeded=True,
    )
    assert isinstance(outcome, RemedyAuthorized)
    # 110.000 x 1000 / 10000 = 11.000 exactly; no rounding is exercised by this figure.
    assert outcome.amount_vnd == 11_000
    assert outcome.window_closes_at is None


def test_the_late_delivery_credit_rounds_half_up_to_whole_dong() -> None:
    """VND has no minor unit. 55.555 x 10% is 5.555,5 and the customer gets 5.556."""

    outcome = decide(
        RemedyRequest(
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=True,
            attested_late_by_minutes=121,
        ),
        settled_total_vnd=55_555,
        expects_return_leg=True,
        return_leg_succeeded=True,
    )
    assert isinstance(outcome, RemedyAuthorized) and outcome.amount_vnd == 5_556


# --- windows --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "extra", "window"),
    [
        (RemedyKind.FREE_REWASH, {}, timedelta(days=7)),
        (
            RemedyKind.DAMAGE_COMPENSATION,
            {"order_line_id": "line-1", "amount_vnd": 1_000},
            timedelta(hours=24),
        ),
    ],
)
def test_a_window_is_inclusive_at_its_far_end_and_closed_one_microsecond_later(
    kind: RemedyKind, extra: dict[str, object], window: timedelta
) -> None:
    request = RemedyRequest(kind=kind, store_fault_attested=True, **extra)  # type: ignore[arg-type]
    assert isinstance(decide(request, at=RETURNED + window), RemedyAuthorized)
    refused = decide(request, at=RETURNED + window + timedelta(microseconds=1))
    assert isinstance(refused, RemedyRefused)
    assert refused.refusal is RemedyRefusal.REMEDY_WINDOW_CLOSED
    # The moment it closed, so staff can tell the customer why rather than only that.
    assert refused.window_closes_at == RETURNED + window


def test_no_recorded_handover_stops_rather_than_measuring_from_now() -> None:
    refused = decide(
        RemedyRequest(kind=RemedyKind.FREE_REWASH, store_fault_attested=True),
        goods_returned_at=None,
    )
    assert isinstance(refused, RemedyRefused)
    assert refused.refusal is RemedyRefusal.REMEDY_WINDOW_EVIDENCE_MISSING


# --- shapes ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_",
    [
        # An amount for a remedy that moves no money.
        RemedyRequest(kind=RemedyKind.FREE_REWASH, store_fault_attested=True, amount_vnd=50_000),
        # An amount for the credit the server computes.
        RemedyRequest(
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=True,
            attested_late_by_minutes=200,
            amount_vnd=50_000,
        ),
        # Damage with no amount, and damage with no line.
        RemedyRequest(
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-1",
        ),
        RemedyRequest(
            kind=RemedyKind.DAMAGE_COMPENSATION, store_fault_attested=True, amount_vnd=1_000
        ),
        # A negative amount, and `True`, which is an `int`.
        RemedyRequest(
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-1",
            amount_vnd=-1,
        ),
        RemedyRequest(
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-1",
            amount_vnd=True,
        ),
        # A lateness on a kind that has nothing to do with delivery.
        RemedyRequest(
            kind=RemedyKind.FREE_REWASH,
            store_fault_attested=True,
            attested_late_by_minutes=200,
        ),
    ],
)
def test_a_field_that_does_not_belong_to_the_kind_is_refused_rather_than_ignored(
    request_: RemedyRequest,
) -> None:
    """Ignoring it would let staff type an amount, see it accepted, and believe the shop agreed."""

    outcome = decide(request_)
    assert isinstance(outcome, RemedyRefused)
    assert outcome.refusal is RemedyRefusal.REMEDY_AMOUNT_NOT_APPLICABLE


def test_every_money_kind_needs_a_store_fault_finding() -> None:
    for request_ in (
        RemedyRequest(kind=RemedyKind.FREE_REWASH, store_fault_attested=False),
        RemedyRequest(
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=False,
            order_line_id="line-1",
            amount_vnd=1_000,
        ),
        RemedyRequest(
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=False,
            attested_late_by_minutes=200,
        ),
    ):
        outcome = decide(request_, expects_return_leg=True, return_leg_succeeded=True)
        assert isinstance(outcome, RemedyRefused)
        assert outcome.refusal is RemedyRefusal.REMEDY_STORE_FAULT_NOT_ATTESTED


def test_a_credit_needs_a_settled_total_and_a_delivery_that_happened() -> None:
    late = RemedyRequest(
        kind=RemedyKind.LATE_DELIVERY_CREDIT,
        store_fault_attested=True,
        attested_late_by_minutes=200,
    )
    nothing_delivered = decide(late)
    assert isinstance(nothing_delivered, RemedyRefused)
    assert nothing_delivered.refusal is RemedyRefusal.REMEDY_DELIVERY_NOT_RECORDED

    unsettled = decide(
        late, expects_return_leg=True, return_leg_succeeded=True, settled_total_vnd=None
    )
    assert isinstance(unsettled, RemedyRefused)
    assert unsettled.refusal is RemedyRefusal.REMEDY_ORDER_NOT_SETTLED


def test_a_lateness_at_the_threshold_is_not_above_it() -> None:
    """The owner said "more than two hours". Two hours exactly is not more than two hours."""

    outcome = decide(
        RemedyRequest(
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=True,
            attested_late_by_minutes=120,
        ),
        expects_return_leg=True,
        return_leg_succeeded=True,
    )
    assert isinstance(outcome, RemedyRefused)
    assert outcome.refusal is RemedyRefusal.REMEDY_LATENESS_BELOW_THRESHOLD
    assert outcome.threshold_minutes == 120


# --- allocation -----------------------------------------------------------------------------------


def test_a_credit_is_spread_by_largest_remainder_and_sums_to_itself_exactly() -> None:
    """Three equal lines and a credit that does not divide: the spare dong goes to `line-1`."""

    allocated = allocate_remedy_credit(
        line_weights_vnd={"line-3": 10_000, "line-1": 10_000, "line-2": 10_000},
        credit_vnd=1_000,
    )
    assert not isinstance(allocated, RemedyRefused)
    assert sum(allocated.per_line_vnd.values()) == 1_000
    assert allocated.per_line_vnd == {"line-1": 334, "line-2": 333, "line-3": 333}
    assert allocated.rounding == LARGEST_REMAINDER_RULE
    # Ordered by line id, not by the order the caller happened to submit them in, so the same lines
    # in a different order produce the same allocation.
    assert allocated.allocation.ordered_ids == ("line-1", "line-2", "line-3")


def test_a_credit_larger_than_the_lines_is_refused_with_what_was_available() -> None:
    refused = allocate_remedy_credit(line_weights_vnd={"line-1": 10_000}, credit_vnd=10_001)
    assert isinstance(refused, RemedyRefused)
    assert refused.refusal is RemedyRefusal.REMEDY_CREDIT_UNALLOCATABLE
    assert refused.ceiling_vnd == 10_000
    # Exactly the whole subtotal still allocates: it is a bill paid entirely by the credit.
    exact = allocate_remedy_credit(line_weights_vnd={"line-1": 10_000}, credit_vnd=10_000)
    assert not isinstance(exact, RemedyRefused) and exact.per_line_vnd == {"line-1": 10_000}


@pytest.mark.parametrize("weights", [{}, {"line-1": 0}, {"line-1": 0, "line-2": 0}])
def test_a_credit_cannot_be_allocated_across_lines_worth_nothing(
    weights: dict[str, int],
) -> None:
    """Dividing by a zero subtotal is the shape that produces a plausible wrong answer."""

    refused = allocate_remedy_credit(line_weights_vnd=weights, credit_vnd=1)
    assert isinstance(refused, RemedyRefused)
    assert refused.refusal is RemedyRefusal.REMEDY_CREDIT_UNALLOCATABLE


def test_the_extracted_allocator_still_gives_promotion_the_answer_it_gave_before() -> None:
    """`_allocate_group` now calls `allocate_largest_remainder`; the answer must not have moved.

    The arithmetic, written out because it is the whole assertion. Three lines at 1.000 d and a
    rate of 3333 basis points: the group discount is 3.000 x 3333 / 10.000 = 999,9 d, which rounds
    half up to 1.000 d. Each line's floor is 1.000 x 3333 / 10.000 = 333 d, so the floors sum to 999
    and exactly one dong is left to award. All three remainders are equal, so the tie breaks on
    `line_id` ascending and `line-1` takes it -- the property the shared allocator exists to keep
    identical between a promotion and a remedy credit.

    Checked against the promotion engine itself rather than a remembered constant, so this fails if
    the extraction changed the answer rather than if somebody mis-copied it.
    """

    result = evaluate_promotion(
        _PROMOTION,
        tuple(
            PromotionLine(f"line-{index}", 1_000, PromotionResolution.AUTO_IF_TARGETED, 3_333)
            for index in (3, 1, 2)
        ),
        evaluation_at=datetime(2026, 8, 1, 3, tzinfo=UTC),
    )
    assert result.discount_amount_vnd == 1_000
    assert {item.line_id: item.discount_vnd for item in result.adjustments} == {
        "line-1": 334,
        "line-2": 333,
        "line-3": 333,
    }
    assert result.trace.rounding == LARGEST_REMAINDER_RULE


def test_the_allocator_never_awards_more_dong_than_there_are_lines() -> None:
    """The shortfall is the sum of the fractional parts, so the award slice cannot overrun."""

    allocation = allocate_largest_remainder(
        weights=(("a", 1), ("b", 1), ("c", 1)), multiplier=2, denominator=3, total_vnd=2
    )
    assert sum(allocation.final_allocations_vnd) == 2
    assert len(allocation.remainder_award_order) <= 3


def test_a_policy_is_only_a_policy_when_every_figure_is_an_integer() -> None:
    """The typed record is what the rest of the system reads; nothing downstream re-validates it."""

    assert isinstance(POLICY, RemedyPolicy)
    assert POLICY.staff_approval_ceiling_vnd == 100_000
    assert POLICY.damage_compensation_multiple == 5
    assert POLICY.late_delivery_credit_rate_bps == 1_000


# --- the figures are per item, not per form -------------------------------------------------------
#
# The defect: a claim split into three 80.000 d proposals passed a per-request 100.000 d staff limit
# three times. Both damage figures are now compared against what the line already carries plus what
# is asked for, and these pin the arithmetic at its edges.


def _damage(amount: int, line_committed: int) -> object:
    return decide(
        RemedyRequest(
            kind=RemedyKind.DAMAGE_COMPENSATION,
            store_fault_attested=True,
            order_line_id="line-1",
            amount_vnd=amount,
        ),
        committed=RemedyCommitments(line_committed_vnd=line_committed, late_delivery_credits=0),
    )


@pytest.mark.parametrize(
    ("committed", "amount", "owner"),
    [
        (0, 100_000, False),  # the limit itself, alone: staff
        (20_000, 80_000, False),  # 100.000 in total, inclusive: staff
        (20_000, 80_001, True),  # one dong over, in total: owner
        (80_000, 80_000, True),  # the reproduction's second proposal
        (100_000, 1, True),  # anything at all once the limit is used up
    ],
)
def test_the_staff_limit_applies_to_the_item_total(
    committed: int, amount: int, owner: bool
) -> None:
    outcome = _damage(amount, committed)
    assert isinstance(outcome, RemedyAuthorized)
    assert outcome.requires_owner_approval is owner
    # The amount recorded is what this proposal asks for, never the running total.
    assert outcome.amount_vnd == amount


def test_the_item_ceiling_is_cumulative_and_inclusive() -> None:
    """500.000 d on a 100.000 d line: reachable exactly, never exceeded, whoever approves."""

    at_ceiling = _damage(200_000, 300_000)
    assert isinstance(at_ceiling, RemedyAuthorized) and at_ceiling.requires_owner_approval
    over = _damage(200_001, 300_000)
    assert isinstance(over, RemedyRefused)
    assert over.refusal is RemedyRefusal.REMEDY_CEILING_EXCEEDED
    # Refused naming the ceiling and what is already on the item, never truncated to the remainder.
    assert (over.ceiling_vnd, over.committed_vnd) == (500_000, 300_000)


def test_a_second_late_delivery_credit_is_refused_before_anything_is_computed() -> None:
    outcome = decide(
        RemedyRequest(
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=True,
            attested_late_by_minutes=150,
        ),
        committed=RemedyCommitments(line_committed_vnd=0, late_delivery_credits=1),
        expects_return_leg=True,
        return_leg_succeeded=True,
    )
    assert isinstance(outcome, RemedyRefused)
    assert outcome.refusal is RemedyRefusal.REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED
    assert REMEDY_REFUSAL_AUTHORITIES[outcome.refusal] == "DEC-004"


@pytest.mark.parametrize("bad", [-1, True, 1.0, "0"])
def test_a_committed_total_that_is_not_money_is_refused(bad: object) -> None:
    with pytest.raises(RemedyPolicyError):
        RemedyCommitments(line_committed_vnd=bad, late_delivery_credits=0)  # type: ignore[arg-type]


@given(
    amounts=st.lists(st.integers(min_value=1, max_value=600_000), min_size=1, max_size=8),
    line_amount=st.integers(min_value=1, max_value=200_000),
)
@settings(max_examples=300, deadline=None)
def test_no_sequence_of_proposals_pays_more_than_the_figures_allow(
    amounts: list[int], line_amount: int
) -> None:
    """Whatever split a counter tries, the item never carries more than 5x its fee, and never more
    than the staff limit without the owner. The repository feeds each accepted amount back in as
    the next proposal's committed total, exactly as this loop does."""

    committed = staff_only = 0
    ceiling = line_amount * POLICY.damage_compensation_multiple
    for amount in amounts:
        outcome = evaluate_remedy(
            policy=POLICY,
            facts=facts(line_amounts_vnd={"line-1": line_amount}),
            request=RemedyRequest(
                kind=RemedyKind.DAMAGE_COMPENSATION,
                store_fault_attested=True,
                order_line_id="line-1",
                amount_vnd=amount,
            ),
            requested_at=NOW,
            committed=RemedyCommitments(line_committed_vnd=committed, late_delivery_credits=0),
        )
        if isinstance(outcome, RemedyRefused):
            assert outcome.refusal is RemedyRefusal.REMEDY_CEILING_EXCEEDED
            assert committed + amount > ceiling
            continue
        assert isinstance(outcome, RemedyAuthorized)
        committed += amount
        if not outcome.requires_owner_approval:
            staff_only += amount
        assert committed <= ceiling
        assert staff_only <= POLICY.staff_approval_ceiling_vnd
