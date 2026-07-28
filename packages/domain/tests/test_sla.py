from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import (
    CommitmentAuthority,
    PolicyOutcome,
    SlaLifecycle,
    SlaOutcome,
)
from nha_trang_laundry_domain.sla import (
    OTHER_SPECIAL_ITEM_SLA,
    SPECIAL_ITEM_GUIDANCE_SLA,
    STANDARD_WASH_SLA,
    SlaError,
    SlaPolicyType,
    SlaReason,
    evaluate_production_sla,
)

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
START = datetime(2026, 8, 1, 9, tzinfo=TZ)


def test_quote_or_intake_without_production_acceptance_does_not_start_sla() -> None:
    result = evaluate_production_sla(
        STANDARD_WASH_SLA,
        evaluated_at=START + timedelta(hours=20),
    )

    assert result.lifecycle is SlaLifecycle.DRAFT
    assert result.outcome is SlaOutcome.PENDING
    assert result.internal_risk_due_at is None
    assert SlaReason.PRODUCTION_NOT_ACCEPTED in result.reason_codes


def test_ready_exactly_at_eight_hours_is_met() -> None:
    ready = START + timedelta(hours=8)
    result = evaluate_production_sla(
        STANDARD_WASH_SLA,
        evaluated_at=ready,
        production_accepted_at=START,
        ready_at_store=ready,
    )

    assert result.lifecycle is SlaLifecycle.COMPLETED
    assert result.outcome is SlaOutcome.MET
    assert result.internal_risk_due_at == ready
    assert result.trace.actual_elapsed_microseconds == 8 * 60 * 60 * 1_000_000
    assert result.trace.breach_microseconds == 0


def test_overdue_unfinished_order_is_running_and_breached() -> None:
    result = evaluate_production_sla(
        STANDARD_WASH_SLA,
        evaluated_at=START + timedelta(hours=8, microseconds=1),
        production_accepted_at=START,
    )

    assert result.lifecycle is SlaLifecycle.RUNNING
    assert result.outcome is SlaOutcome.BREACHED
    assert result.review_outcome is PolicyOutcome.REQUIRE_HUMAN
    assert result.trace.breach_microseconds == 1
    assert result.trace.automatic_breach_remedy_authorized is False
    assert SlaReason.BREACH_REMEDY_REQUIRES_HUMAN in result.reason_codes


def test_late_completion_preserves_completed_plus_breached_dimensions() -> None:
    ready = START + timedelta(hours=9)
    result = evaluate_production_sla(
        STANDARD_WASH_SLA,
        evaluated_at=ready,
        production_accepted_at=START,
        ready_at_store=ready,
    )

    assert result.lifecycle is SlaLifecycle.COMPLETED
    assert result.outcome is SlaOutcome.BREACHED
    assert result.trace.breach_microseconds == 60 * 60 * 1_000_000


def test_staff_promise_is_recorded_but_never_inferred_from_elapsed_math() -> None:
    promised = START + timedelta(hours=8)
    result = evaluate_production_sla(
        STANDARD_WASH_SLA,
        evaluated_at=START,
        production_accepted_at=START,
        human_confirmed_promised_ready_at_store=promised,
    )

    assert result.promise_outcome is PolicyOutcome.ALLOW
    assert result.overall_outcome is PolicyOutcome.ALLOW
    assert result.human_confirmed_promised_ready_at_store == promised
    assert result.trace.automatic_promise_authorized is False
    assert SlaReason.EXACT_CLOSURE_CUTOFF_UNPUBLISHED in result.reason_codes


def test_special_item_guidance_builds_range_but_never_breach() -> None:
    result = evaluate_production_sla(
        SPECIAL_ITEM_GUIDANCE_SLA,
        evaluated_at=START + timedelta(hours=72),
        production_accepted_at=START,
    )

    assert result.lifecycle is SlaLifecycle.RUNNING
    assert result.outcome is SlaOutcome.PENDING
    assert result.guidance_window_start_at == START + timedelta(hours=24)
    assert result.guidance_window_end_at == START + timedelta(hours=48)
    assert result.review_outcome is PolicyOutcome.ALLOW
    assert SlaReason.GUIDANCE_DOES_NOT_CREATE_BREACH in result.reason_codes


def test_other_special_item_has_no_invented_eta() -> None:
    result = evaluate_production_sla(
        OTHER_SPECIAL_ITEM_SLA,
        evaluated_at=START,
        production_accepted_at=START,
    )

    assert result.guidance_window_start_at is None
    assert result.guidance_window_end_at is None
    assert result.promise_outcome is PolicyOutcome.REQUIRE_HUMAN
    assert result.overall_outcome is PolicyOutcome.REQUIRE_HUMAN
    assert SlaReason.HUMAN_ETA_REQUIRED in result.reason_codes


def test_production_trace_explicitly_excludes_delivery_clock() -> None:
    result = evaluate_production_sla(
        STANDARD_WASH_SLA,
        evaluated_at=START,
        production_accepted_at=START,
    )

    assert result.trace.clock_start_event == "production_accepted_at"
    assert result.trace.clock_end_event == "ready_at_store"
    assert result.trace.delivery_included is False


def test_invalid_timestamps_and_ready_without_acceptance_fail_closed() -> None:
    with pytest.raises(SlaError, match="VALIDATION_ERROR"):
        evaluate_production_sla(
            STANDARD_WASH_SLA,
            evaluated_at=datetime(2026, 8, 1, 9),
        )

    with pytest.raises(SlaError, match="INVALID_STATE_TRANSITION"):
        evaluate_production_sla(
            STANDARD_WASH_SLA,
            evaluated_at=START,
            ready_at_store=START,
        )

    with pytest.raises(SlaError, match="INVALID_STATE_TRANSITION"):
        evaluate_production_sla(
            STANDARD_WASH_SLA,
            evaluated_at=START,
            production_accepted_at=START,
            ready_at_store=START - timedelta(seconds=1),
        )

    for unsupported in (
        replace(STANDARD_WASH_SLA, policy_type=SlaPolicyType.RESPONSE_TARGET),
        replace(
            STANDARD_WASH_SLA,
            commitment_authority=CommitmentAuthority.AUTO_WITHIN_ENVELOPE,
        ),
    ):
        with pytest.raises(SlaError, match="VALIDATION_ERROR"):
            evaluate_production_sla(unsupported, evaluated_at=START)


@given(st.integers(min_value=0, max_value=24 * 60 * 60))
def test_standard_sla_completion_boundary_property(elapsed_seconds: int) -> None:
    ready = START + timedelta(seconds=elapsed_seconds)
    result = evaluate_production_sla(
        STANDARD_WASH_SLA,
        evaluated_at=ready,
        production_accepted_at=START,
        ready_at_store=ready,
    )

    expected = SlaOutcome.MET if elapsed_seconds <= 8 * 60 * 60 else SlaOutcome.BREACHED
    assert result.lifecycle is SlaLifecycle.COMPLETED
    assert result.outcome is expected
    assert result.trace.breach_microseconds == max(0, elapsed_seconds - 8 * 60 * 60) * 1_000_000
