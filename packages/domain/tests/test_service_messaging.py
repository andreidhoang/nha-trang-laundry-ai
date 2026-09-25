"""`DEC-033`: the pure rule a service send rests on, its order, and its edges to the microsecond."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.consent import SuppressionState
from nha_trang_laundry_domain.service_messaging import (
    MessagingPolicyError,
    ServiceBasis,
    ServiceBasisFacts,
    TransactionalEgressDecision,
    TransactionalMessagingPolicy,
    TransactionalRefusal,
    decide_transactional_egress,
    parse_messaging_policy,
)

TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "templates"
    / "transactional-messaging-policy-dec-033.json"
)
AT = datetime(2026, 9, 25, 3, tzinfo=UTC)
TICK = timedelta(microseconds=1)
POLICY = TransactionalMessagingPolicy(
    service_window_hours=48,
    closed_order_grace_hours=72,
    bases=(ServiceBasis.CUSTOMER_INITIATED, ServiceBasis.OPEN_ORDER),
)
NOTHING = ServiceBasisFacts(None, False, None)


def _document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    return document


def _decide(
    *,
    suppression: SuppressionState | None = None,
    policy: TransactionalMessagingPolicy | None = POLICY,
    facts: ServiceBasisFacts = NOTHING,
    at: datetime = AT,
) -> Any:
    return decide_transactional_egress(suppression=suppression, policy=policy, facts=facts, at=at)


def test_the_shipped_template_carries_the_recommended_figures_and_both_statements() -> None:
    document = _document()
    policy = parse_messaging_policy(document)
    assert policy == POLICY
    assert "chủ tiệm xác nhận" in document["owner_confirmation_vi"]
    assert "owner confirms" in document["owner_confirmation_en"]


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "remedy-policy-v1"},
        {"decision": "DEC-004"},
        {"service_window_hours": 0},
        {"service_window_hours": True},
        {"service_window_hours": 48.0},
        {"closed_order_grace_hours": -1},
        {"bases": []},
        {"bases": ["CUSTOMER_INITIATED", "CUSTOMER_INITIATED"]},
        {"bases": ["ANY_TIME"]},
        {"owner_confirmation_vi": "  "},
        {"owner_confirmation_en": None},
    ],
)
def test_a_malformed_policy_is_refused(change: dict[str, Any]) -> None:
    with pytest.raises(MessagingPolicyError):
        parse_messaging_policy({**_document(), **change})


def test_bases_are_read_in_canonical_order_whatever_order_the_document_lists() -> None:
    policy = parse_messaging_policy({**_document(), "bases": ["OPEN_ORDER", "CUSTOMER_INITIATED"]})
    assert policy.bases == (ServiceBasis.CUSTOMER_INITIATED, ServiceBasis.OPEN_ORDER)


# --- order: suppression, then policy, then basis ---------------------------------------------

EVERY_BASIS = ServiceBasisFacts(AT, True, AT)


@pytest.mark.parametrize(
    ("suppression", "decision", "refusal"),
    [
        (
            SuppressionState.SUPPRESSED,
            TransactionalEgressDecision.SUPPRESSED,
            TransactionalRefusal.SUPPRESSED,
        ),
        (
            SuppressionState.PENDING_REVIEW_BLOCKED,
            TransactionalEgressDecision.REQUIRE_HUMAN,
            TransactionalRefusal.PENDING_REVIEW,
        ),
        (
            SuppressionState.UNKNOWN_BLOCKED,
            TransactionalEgressDecision.REQUIRE_HUMAN,
            TransactionalRefusal.SUPPRESSION_UNKNOWN,
        ),
    ],
)
def test_a_block_wins_over_every_basis_and_over_a_missing_policy(
    suppression: SuppressionState,
    decision: TransactionalEgressDecision,
    refusal: TransactionalRefusal,
) -> None:
    for policy in (POLICY, None):
        outcome = _decide(suppression=suppression, policy=policy, facts=EVERY_BASIS)
        assert (outcome.decision, outcome.refusal, outcome.basis) == (decision, refusal, None)


def test_no_policy_refuses_every_basis() -> None:
    outcome = _decide(policy=None, facts=EVERY_BASIS)
    assert outcome.decision is TransactionalEgressDecision.REQUIRE_HUMAN
    assert outcome.refusal is TransactionalRefusal.MESSAGING_POLICY_UNPUBLISHED


@pytest.mark.parametrize("suppression", [None, SuppressionState.CLEAR])
def test_no_row_or_clear_passes_the_suppression_half_and_still_needs_a_basis(
    suppression: SuppressionState | None,
) -> None:
    assert _decide(suppression=suppression).refusal is TransactionalRefusal.NO_SERVICE_BASIS
    allowed = _decide(suppression=suppression, facts=ServiceBasisFacts(AT, False, None))
    assert allowed.allowed and allowed.basis is ServiceBasis.CUSTOMER_INITIATED


def test_the_first_published_basis_that_holds_is_the_one_recorded() -> None:
    assert _decide(facts=EVERY_BASIS).basis is ServiceBasis.CUSTOMER_INITIATED
    assert _decide(facts=ServiceBasisFacts(None, True, None)).basis is ServiceBasis.OPEN_ORDER


def test_a_naive_instant_is_refused() -> None:
    with pytest.raises(ValueError):
        _decide(at=datetime(2026, 9, 25, 3))


# --- edges, as properties ----------------------------------------------------------------------

hours = st.integers(min_value=1, max_value=24 * 30)
offsets = st.integers(min_value=-(10**12), max_value=10**12)  # microseconds, about +-11.5 days


@given(window=hours, offset=offsets)
def test_the_service_window_is_inclusive_at_exactly_its_hours_and_never_counts_the_future(
    window: int, offset: int
) -> None:
    policy = TransactionalMessagingPolicy(window, 0, (ServiceBasis.CUSTOMER_INITIATED,))
    edge = AT - timedelta(hours=window)
    message = edge + timedelta(microseconds=offset)
    outcome = _decide(policy=policy, facts=ServiceBasisFacts(message, False, None))
    assert outcome.allowed is (edge <= message <= AT)
    assert _decide(policy=policy, facts=ServiceBasisFacts(edge, False, None)).allowed
    assert not _decide(policy=policy, facts=ServiceBasisFacts(edge - TICK, False, None)).allowed
    assert not _decide(policy=policy, facts=ServiceBasisFacts(AT + TICK, False, None)).allowed


@given(grace=st.integers(min_value=0, max_value=24 * 30), offset=offsets)
def test_the_closed_order_grace_is_inclusive_at_exactly_its_hours(grace: int, offset: int) -> None:
    policy = TransactionalMessagingPolicy(1, grace, (ServiceBasis.OPEN_ORDER,))
    edge = AT - timedelta(hours=grace)
    closed = edge + timedelta(microseconds=offset)
    outcome = _decide(policy=policy, facts=ServiceBasisFacts(None, False, closed))
    assert outcome.allowed is (edge <= closed <= AT)
    assert _decide(policy=policy, facts=ServiceBasisFacts(None, False, edge)).allowed
    assert not _decide(policy=policy, facts=ServiceBasisFacts(None, False, edge - TICK)).allowed


@given(
    last=st.one_of(st.none(), st.integers(min_value=-(10**12), max_value=0)),
    open_order=st.booleans(),
)
def test_a_basis_the_policy_does_not_publish_never_allows(
    last: int | None, open_order: bool
) -> None:
    only_orders = TransactionalMessagingPolicy(48, 72, (ServiceBasis.OPEN_ORDER,))
    message = None if last is None else AT + timedelta(microseconds=last)
    outcome = _decide(policy=only_orders, facts=ServiceBasisFacts(message, open_order, None))
    assert outcome.allowed is open_order
