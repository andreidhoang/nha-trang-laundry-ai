"""`REMEDY-GARMENT-001`: the staff limit and the 5x ceiling are cumulative per garment.

The DEC-031 addendum (2026-09-25, delegated): `DEC-004` lets staff approve up to 100.000 d *per
item* and caps each at 5x *the item's* cleaning fee. On a line priced per piece a claim names its
garment by its 1-based position within the line's quantity, and both limits are cumulative per
(line, garment). A line priced by weight, or whose per-piece fee was never recorded, has no garment
identity and keeps the per-line rule. A proposal recorded before garments could be named counts
against every garment on its line. Loss and refunded-order compensation still always go to the
owner, and the line's total can never pass 5x unit x quantity.

Every test below fails on the code before this item: `RemedyRequest` had no `garment_index`, and the
staff limit was compared against the line's running total.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import Unit
from nha_trang_laundry_domain.remedies import (
    REMEDY_REFUSAL_AUTHORITIES,
    OwnerReason,
    RemedyAuthorized,
    RemedyCommitments,
    RemedyKind,
    RemedyLineFacts,
    RemedyOrderFacts,
    RemedyPolicyError,
    RemedyRefusal,
    RemedyRefused,
    RemedyRequest,
    committed_against_any_garment,
    committed_against_garment,
    evaluate_remedy,
    parse_remedy_policy,
    remedy_proposal_document,
)

NOW = datetime(2026, 9, 25, 3, tzinfo=UTC)
POLICY = parse_remedy_policy(
    {
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
)
#: Three shirts at 50.000 d: 250.000 d a shirt, 750.000 d the line.
SHIRTS = RemedyLineFacts("DC_SHIRT", Unit.ITEM, "3", 50_000, 150_000)
#: One evening dress: a line of one garment.
DRESS = RemedyLineFacts("DC_EVENING_DRESS", Unit.ITEM, "1", 120_000, 120_000)
#: 5,2 kg under the cliff at 25.000 d/kg: a bag, no garment has a fee of its own.
BAG = RemedyLineFacts("STD_WASH_DRY_LT6", Unit.KG, "5.2", 25_000, 130_000)
#: Two pillows closed inside a band: no recorded per-piece fee.
PILLOWS = RemedyLineFacts("BED_PILLOW", Unit.ITEM, "2", None, 140_000)


def facts(*, refunded: bool = False) -> RemedyOrderFacts:
    return RemedyOrderFacts(
        goods_returned_at=NOW - timedelta(hours=1),
        settled_total_vnd=None if refunded else 400_000,
        lines={"shirts": SHIRTS, "dress": DRESS, "bag": BAG, "pillows": PILLOWS},
        expects_return_leg=True,
        return_leg_succeeded=True,
        refunded=refunded,
    )


def claim(
    line_id: str,
    amount: int,
    garment: object = None,
    *,
    kind: RemedyKind = RemedyKind.DAMAGE_COMPENSATION,
    line: int = 0,
    on_garment: int | None = None,
    refunded: bool = False,
) -> RemedyAuthorized | RemedyRefused:
    return evaluate_remedy(
        policy=POLICY,
        facts=facts(refunded=refunded),
        request=RemedyRequest(
            kind=kind,
            store_fault_attested=True,
            order_line_id=line_id,
            amount_vnd=amount,
            garment_index=garment,  # type: ignore[arg-type]
        ),
        requested_at=NOW,
        committed=RemedyCommitments(
            line_committed_vnd=line, late_delivery_credits=0, garment_committed_vnd=on_garment
        ),
    )


# --- the staff limit and the ceiling, per garment -----------------------------------------------


def test_shirt_two_has_its_own_staff_limit_independent_of_shirt_one() -> None:
    """Shirt #1 already carries 100.000 d; 100.000 d more on shirt #2 is staff's to approve."""

    outcome = claim("shirts", 100_000, 2, line=100_000, on_garment=0)
    assert isinstance(outcome, RemedyAuthorized)
    assert outcome.requires_owner_approval is False
    assert outcome.garment_index == 2


def test_a_second_claim_on_the_same_garment_is_cumulative_with_the_first() -> None:
    """60.000 d on shirt #2: 40.000 d more is staff's (inclusive), 40.001 d is the owner's."""

    at = claim("shirts", 40_000, 2, line=60_000, on_garment=60_000)
    over = claim("shirts", 40_001, 2, line=60_000, on_garment=60_000)
    assert isinstance(at, RemedyAuthorized) and not at.requires_owner_approval
    assert isinstance(over, RemedyAuthorized)
    assert over.owner_reasons == (OwnerReason.ABOVE_STAFF_LIMIT,)


def test_each_garment_has_its_own_ceiling_and_it_is_cumulative() -> None:
    """Shirt #2 at 200.000 d: 50.000 d more reaches its 250.000 d exactly; 50.001 d is refused
    with its ceiling and what it already carries -- whatever shirts #1 and #3 hold."""

    at = claim("shirts", 50_000, 2, line=200_000, on_garment=200_000)
    assert isinstance(at, RemedyAuthorized) and at.ceiling_vnd == 250_000
    over = claim("shirts", 50_001, 2, line=200_000, on_garment=200_000)
    assert isinstance(over, RemedyRefused)
    assert over.refusal is RemedyRefusal.REMEDY_CEILING_EXCEEDED
    assert (over.ceiling_vnd, over.committed_vnd) == (250_000, 200_000)
    # Shirt #3 with nothing on it takes a full 250.000 d while shirt #2 is at 200.000 d.
    other = claim("shirts", 250_000, 3, line=200_000, on_garment=0)
    assert isinstance(other, RemedyAuthorized)


def test_the_line_total_still_binds_every_garment_together() -> None:
    """Never more than 5 x unit x quantity on the line, whichever garment asks."""

    full = claim("shirts", 1, 3, line=750_000, on_garment=0)
    assert isinstance(full, RemedyRefused)
    assert full.refusal is RemedyRefusal.REMEDY_CEILING_EXCEEDED
    assert (full.ceiling_vnd, full.committed_vnd) == (750_000, 750_000)


def test_a_line_level_proposal_counts_against_every_garment() -> None:
    """Migration 0053's existing rows name no garment. 60.000 d recorded line-level on the shirts
    is counted against shirt #1, #2 and #3 alike, so 50.000 d on any of them is the owner's."""

    by_garment: dict[int | None, int] = {None: 60_000}
    for garment in (1, 2, 3):
        prior = committed_against_garment(by_garment, garment)
        assert prior == 60_000
        outcome = claim("shirts", 50_000, garment, line=60_000, on_garment=prior)
        assert isinstance(outcome, RemedyAuthorized)
        assert outcome.owner_reasons == (OwnerReason.ABOVE_STAFF_LIMIT,), garment
    # And against each shirt's ceiling: 200.000 d line-level leaves 50.000 d on every shirt.
    refused = claim("shirts", 50_001, 1, line=200_000, on_garment=200_000)
    assert isinstance(refused, RemedyRefused) and refused.committed_vnd == 200_000


def test_committed_against_a_garment_is_its_own_claims_plus_every_line_level_claim() -> None:
    by_garment: dict[int | None, int] = {None: 10_000, 1: 30_000, 2: 5_000}
    assert committed_against_garment(by_garment, 1) == 40_000
    assert committed_against_garment(by_garment, 2) == 15_000
    assert committed_against_garment(by_garment, 3) == 10_000
    assert committed_against_any_garment(by_garment) == 40_000
    assert committed_against_any_garment({}) == 0
    assert committed_against_any_garment({None: 7}) == 7


def test_an_unattributed_commitment_counts_the_whole_line_against_the_garment() -> None:
    """A caller that did not sum per garment gets the pre-addendum rule, never more headroom."""

    outcome = claim("shirts", 50_000, 2, line=60_000, on_garment=None)
    assert isinstance(outcome, RemedyAuthorized)
    assert outcome.owner_reasons == (OwnerReason.ABOVE_STAFF_LIMIT,)


@pytest.mark.parametrize("bad", [-1, 60_001, True])
def test_a_garment_share_larger_than_its_line_or_not_an_amount_is_refused(bad: object) -> None:
    with pytest.raises(RemedyPolicyError):
        RemedyCommitments(
            line_committed_vnd=60_000,
            late_delivery_credits=0,
            garment_committed_vnd=bad,  # type: ignore[arg-type]
        )


# --- which garment -------------------------------------------------------------------------------


def test_a_line_of_several_garments_requires_one_to_be_named() -> None:
    outcome = claim("shirts", 10_000, None)
    assert isinstance(outcome, RemedyRefused)
    assert outcome.refusal is RemedyRefusal.REMEDY_GARMENT_REQUIRED
    assert outcome.garments == 3
    assert outcome.authority == "DEC-031"


@pytest.mark.parametrize("garment", [0, 4, -1, True, "2"])
def test_a_garment_outside_the_line_is_refused_with_the_count(garment: object) -> None:
    outcome = claim("shirts", 10_000, garment)
    assert isinstance(outcome, RemedyRefused)
    assert outcome.refusal is RemedyRefusal.REMEDY_GARMENT_OUT_OF_RANGE
    assert outcome.garments == 3


def test_both_ends_of_the_line_are_garments() -> None:
    for garment in (1, 3):
        outcome = claim("shirts", 10_000, garment)
        assert isinstance(outcome, RemedyAuthorized) and outcome.garment_index == garment


def test_a_line_of_one_garment_is_garment_one_named_or_not() -> None:
    unnamed = claim("dress", 10_000, None)
    named = claim("dress", 10_000, 1)
    assert isinstance(unnamed, RemedyAuthorized) and unnamed.garment_index == 1
    assert isinstance(named, RemedyAuthorized) and named.garment_index == 1
    two = claim("dress", 10_000, 2)
    assert isinstance(two, RemedyRefused)
    assert (two.refusal, two.garments) == (RemedyRefusal.REMEDY_GARMENT_OUT_OF_RANGE, 1)


@pytest.mark.parametrize("line_id", ["bag", "pillows"])
def test_a_line_with_no_garment_identity_refuses_a_garment(line_id: str) -> None:
    """A bag by weight, or a per-piece line whose fee was never recorded: one claimable whole."""

    refused = claim(line_id, 10_000, 1)
    assert isinstance(refused, RemedyRefused)
    assert refused.refusal is RemedyRefusal.REMEDY_GARMENT_NOT_APPLICABLE
    whole = claim(line_id, 10_000, None)
    assert isinstance(whole, RemedyAuthorized) and whole.garment_index is None


def test_a_bag_keeps_the_per_line_staff_limit() -> None:
    """Weight has no garments, so the line's running total is the item's, exactly as before."""

    outcome = claim("bag", 50_000, None, line=60_000)
    assert isinstance(outcome, RemedyAuthorized)
    assert outcome.owner_reasons == (OwnerReason.ABOVE_STAFF_LIMIT,)


@pytest.mark.parametrize(
    "request_",
    [
        RemedyRequest(kind=RemedyKind.FREE_REWASH, store_fault_attested=True, garment_index=1),
        RemedyRequest(
            kind=RemedyKind.LATE_DELIVERY_CREDIT,
            store_fault_attested=True,
            attested_late_by_minutes=180,
            garment_index=1,
        ),
    ],
)
def test_a_kind_that_is_not_about_one_item_refuses_a_garment(request_: RemedyRequest) -> None:
    outcome = evaluate_remedy(
        policy=POLICY,
        facts=facts(),
        request=request_,
        requested_at=NOW,
        committed=RemedyCommitments(line_committed_vnd=0, late_delivery_credits=0),
    )
    assert isinstance(outcome, RemedyRefused)
    assert outcome.refusal is RemedyRefusal.REMEDY_GARMENT_NOT_APPLICABLE


def test_every_garment_refusal_names_its_authority() -> None:
    for refusal in (
        RemedyRefusal.REMEDY_GARMENT_REQUIRED,
        RemedyRefusal.REMEDY_GARMENT_NOT_APPLICABLE,
        RemedyRefusal.REMEDY_GARMENT_OUT_OF_RANGE,
    ):
        assert REMEDY_REFUSAL_AUTHORITIES[refusal] == "DEC-031"


# --- the owner is still the owner ----------------------------------------------------------------


def test_a_loss_on_a_garment_with_headroom_still_needs_the_owner() -> None:
    """Per-garment headroom loosens nothing that DEC-031 rules 2 and 3 send to the owner."""

    loss = claim("shirts", 1_000, 2, kind=RemedyKind.LOST_ITEM, on_garment=0)
    assert isinstance(loss, RemedyAuthorized)
    assert loss.owner_reasons == (OwnerReason.LOSS_CLAIM,)
    refunded = claim("shirts", 1_000, 2, on_garment=0, refunded=True)
    assert isinstance(refunded, RemedyAuthorized)
    assert refunded.owner_reasons == (OwnerReason.ORDER_REFUNDED,)


def test_the_owner_approves_a_figure_for_one_named_garment() -> None:
    """Invariant 8: the garment is inside the hashed document, so an envelope for shirt #1 can
    never be spent on shirt #2."""

    first = claim("shirts", 150_000, 1)
    second = claim("shirts", 150_000, 2)
    assert isinstance(first, RemedyAuthorized) and isinstance(second, RemedyAuthorized)
    documents = [
        remedy_proposal_document(
            proposal_id=UUID(int=1),
            incident_id=UUID(int=2),
            order_id=UUID(int=3),
            authorized=outcome,
            policy_version_id=UUID(int=4),
            policy_version=1,
            order_line_id="shirts",
            proposed_at=NOW,
        )
        for outcome in (first, second)
    ]
    assert documents[0].snapshot_hash != documents[1].snapshot_hash
    assert b'"garment_index":1' in documents[0].canonical_json


@given(
    amounts=st.lists(
        st.tuples(st.integers(min_value=1, max_value=3), st.integers(0, 300_000)),
        max_size=12,
    ),
    line_level=st.integers(min_value=0, max_value=300_000),
)
@settings(max_examples=300, deadline=None)
def test_accepting_claims_one_by_one_never_passes_any_limit(
    amounts: list[tuple[int, int]], line_level: int
) -> None:
    """Fed claims in any order, the rule never lets one shirt pass 250.000 d, the line pass
    750.000 d, or staff alone pay more than 100.000 d on one shirt -- line-level money included."""

    by_garment: dict[int | None, int] = {None: 0}
    staff_by_garment: dict[int | None, int] = {None: 0}
    if line_level <= 250_000:
        by_garment[None] = line_level
        staff_by_garment[None] = line_level if line_level <= 100_000 else 0
    for garment, amount in amounts:
        outcome = claim(
            "shirts",
            amount,
            garment,
            line=sum(by_garment.values()),
            on_garment=committed_against_garment(by_garment, garment),
        )
        if isinstance(outcome, RemedyRefused):
            continue
        by_garment[garment] = by_garment.get(garment, 0) + amount
        if not outcome.requires_owner_approval:
            staff_by_garment[garment] = staff_by_garment.get(garment, 0) + amount
    for garment in (1, 2, 3):
        assert committed_against_garment(by_garment, garment) <= 250_000
        assert committed_against_garment(staff_by_garment, garment) <= 100_000
    assert sum(by_garment.values()) <= 750_000
