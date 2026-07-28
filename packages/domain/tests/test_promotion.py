from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import (
    PolicyOutcome,
    PromotionEligibilityEvent,
    PromotionResolution,
    PromotionStatus,
)
from nha_trang_laundry_domain.promotion import (
    CURRENT_PROMOTION,
    PromotionError,
    PromotionLine,
    PromotionReason,
    evaluate_promotion,
)

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
APPROVED_POLICY = replace(
    CURRENT_PROMOTION,
    eligibility_event=PromotionEligibilityEvent.PRODUCTION_ACCEPTED,
)


def wet_line(line_id: str = "wet-1", amount_vnd: int = 100_000) -> PromotionLine:
    return PromotionLine(
        line_id,
        amount_vnd,
        PromotionResolution.AUTO_IF_TARGETED,
        rate_bps=3_000,
    )


@pytest.mark.parametrize(
    ("eligibility_at", "expected_status", "expected_discount"),
    [
        (
            datetime(2026, 7, 17, tzinfo=TZ) - timedelta(microseconds=1),
            PromotionStatus.INELIGIBLE,
            0,
        ),
        (datetime(2026, 7, 17, tzinfo=TZ), PromotionStatus.ELIGIBLE, 30_000),
        (
            datetime(2026, 8, 31, 23, 59, 59, 999_000, tzinfo=TZ),
            PromotionStatus.ELIGIBLE,
            30_000,
        ),
        (datetime(2026, 9, 1, tzinfo=TZ), PromotionStatus.INELIGIBLE, 0),
    ],
)
def test_approved_promotion_uses_half_open_interval(
    eligibility_at: datetime,
    expected_status: PromotionStatus,
    expected_discount: int,
) -> None:
    result = evaluate_promotion(
        APPROVED_POLICY,
        (wet_line(),),
        evaluation_at=eligibility_at,
        eligibility_event=PromotionEligibilityEvent.PRODUCTION_ACCEPTED,
        eligibility_at=eligibility_at,
    )

    assert result.status is expected_status
    assert result.discount_amount_vnd == expected_discount


def test_current_unresolved_event_is_provisional_but_candidate_math_is_visible() -> None:
    evaluation_at = datetime(2026, 8, 1, tzinfo=TZ)
    result = evaluate_promotion(
        CURRENT_PROMOTION,
        (wet_line(),),
        evaluation_at=evaluation_at,
        delivery_fee_vnd=10_000,
    )

    assert result.status is PromotionStatus.PROVISIONAL
    assert result.outcome is PolicyOutcome.REQUIRE_HUMAN
    assert result.discount_amount_vnd == 30_000
    assert result.net_service_subtotal_vnd == 70_000
    assert result.display_total_vnd == 80_000
    assert result.eligibility_event is None
    assert result.eligibility_at is None
    assert PromotionReason.PROMOTION_ELIGIBILITY_UNRESOLVED in result.reason_codes
    assert result.trace.delivery_discount_vnd == 0


def test_delivery_fee_is_structurally_excluded_from_discount() -> None:
    now = datetime(2026, 8, 1, tzinfo=TZ)
    result = evaluate_promotion(
        APPROVED_POLICY,
        (wet_line(amount_vnd=100_000),),
        evaluation_at=now,
        eligibility_event=PromotionEligibilityEvent.PRODUCTION_ACCEPTED,
        eligibility_at=now,
        delivery_fee_vnd=10_000,
    )

    assert result.discount_amount_vnd == 30_000
    assert result.delivery_fee_vnd == 10_000
    assert result.display_total_vnd == 80_000


def test_wet_and_dry_rates_are_grouped_independently() -> None:
    now = datetime(2026, 8, 1, tzinfo=TZ)
    dry = PromotionLine("dry-1", 100_000, PromotionResolution.AUTO_IF_TARGETED, rate_bps=4_000)
    result = evaluate_promotion(
        APPROVED_POLICY,
        (wet_line(), dry),
        evaluation_at=now,
        eligibility_event=PromotionEligibilityEvent.PRODUCTION_ACCEPTED,
        eligibility_at=now,
    )

    assert result.eligible_service_subtotal_vnd == 200_000
    assert result.discount_amount_vnd == 70_000
    assert tuple(group.rate_bps for group in result.trace.allocation_groups) == (3_000, 4_000)


def test_human_target_and_stacking_never_auto_authorize() -> None:
    now = datetime(2026, 8, 1, tzinfo=TZ)
    ambiguous = PromotionLine(
        "curtain-install", 50_000, PromotionResolution.HUMAN_CONFIRM, rate_bps=3_000
    )
    result = evaluate_promotion(
        APPROVED_POLICY,
        (wet_line(), ambiguous),
        evaluation_at=now,
        eligibility_event=PromotionEligibilityEvent.PRODUCTION_ACCEPTED,
        eligibility_at=now,
        other_promotion_present=True,
    )

    assert result.status is PromotionStatus.REQUIRES_HUMAN
    assert result.outcome is PolicyOutcome.REQUIRE_HUMAN
    assert result.discount_amount_vnd == 30_000
    assert (
        next(item for item in result.adjustments if item.line_id == "curtain-install").discount_vnd
        == 0
    )
    assert PromotionReason.PROMOTION_TARGET_REQUIRES_HUMAN in result.reason_codes
    assert PromotionReason.PROMOTION_STACKING_REQUIRES_HUMAN in result.reason_codes


def test_largest_remainder_tie_breaks_by_stable_line_id() -> None:
    now = datetime(2026, 8, 1, tzinfo=TZ)
    lines = tuple(
        PromotionLine(line_id, 1, PromotionResolution.AUTO_IF_TARGETED, rate_bps=5_000)
        for line_id in ("c", "a", "b")
    )
    result = evaluate_promotion(
        APPROVED_POLICY,
        lines,
        evaluation_at=now,
        eligibility_event=PromotionEligibilityEvent.PRODUCTION_ACCEPTED,
        eligibility_at=now,
    )

    allocations = {item.line_id: item.discount_vnd for item in result.adjustments}
    assert result.discount_amount_vnd == 2
    assert allocations == {"a": 1, "b": 1, "c": 0}
    assert result.trace.allocation_groups[0].remainder_award_order == ("a", "b")


def test_not_targeted_service_does_not_need_unresolved_event() -> None:
    result = evaluate_promotion(
        CURRENT_PROMOTION,
        (PromotionLine("delivery", 10_000, PromotionResolution.NOT_ELIGIBLE),),
        evaluation_at=datetime(2026, 8, 1, tzinfo=TZ),
    )

    assert result.status is PromotionStatus.NOT_APPLICABLE
    assert result.outcome is PolicyOutcome.ALLOW
    assert result.discount_amount_vnd == 0


@pytest.mark.parametrize(
    "lines",
    [
        (),
        (wet_line("duplicate"), wet_line("duplicate")),
        (
            PromotionLine(
                "float-money",
                cast(int, 100.5),
                PromotionResolution.AUTO_IF_TARGETED,
                3_000,
            ),
        ),
        (PromotionLine("invalid-rate", 100, PromotionResolution.AUTO_IF_TARGETED, 0),),
        (PromotionLine("ineligible-rate", 100, PromotionResolution.NOT_ELIGIBLE, 3_000),),
    ],
)
def test_invalid_promotion_inputs_fail_closed(lines: tuple[PromotionLine, ...]) -> None:
    with pytest.raises(PromotionError):
        evaluate_promotion(
            APPROVED_POLICY,
            lines,
            evaluation_at=datetime(2026, 8, 1, tzinfo=TZ),
        )


def test_naive_timestamp_and_wrong_event_never_resolve_eligibility() -> None:
    with pytest.raises(PromotionError, match="VALIDATION_ERROR"):
        evaluate_promotion(
            APPROVED_POLICY,
            (wet_line(),),
            evaluation_at=datetime(2026, 8, 1),
        )

    now = datetime(2026, 8, 1, tzinfo=TZ)
    result = evaluate_promotion(
        APPROVED_POLICY,
        (wet_line(),),
        evaluation_at=now,
        eligibility_event=PromotionEligibilityEvent.QUOTE_PRESENTED,
        eligibility_at=now,
    )
    assert result.status is PromotionStatus.PROVISIONAL
    assert result.eligibility_event is None

    with pytest.raises(PromotionError, match="VALIDATION_ERROR"):
        evaluate_promotion(
            APPROVED_POLICY,
            (wet_line(),),
            evaluation_at=now,
            eligibility_event=cast(PromotionEligibilityEvent, "accepted_at"),
            eligibility_at=now,
        )


@given(
    st.lists(st.integers(min_value=0, max_value=1_000_000), min_size=1, max_size=25),
    st.integers(min_value=1, max_value=10_000),
)
def test_discount_allocation_preserves_rounded_group_total(
    amounts: list[int], rate_bps: int
) -> None:
    now = datetime(2026, 8, 1, tzinfo=TZ)
    lines = tuple(
        PromotionLine(
            f"line-{index:02d}",
            amount,
            PromotionResolution.AUTO_IF_TARGETED,
            rate_bps,
        )
        for index, amount in enumerate(amounts)
    )
    result = evaluate_promotion(
        APPROVED_POLICY,
        lines,
        evaluation_at=now,
        eligibility_event=PromotionEligibilityEvent.PRODUCTION_ACCEPTED,
        eligibility_at=now,
    )

    expected = (sum(amounts) * rate_bps + 5_000) // 10_000
    assert result.discount_amount_vnd == expected
    assert sum(item.discount_vnd for item in result.adjustments) == expected
    assert all(0 <= item.discount_vnd <= item.list_amount_vnd for item in result.adjustments)
    assert result.net_service_subtotal_vnd == sum(amounts) - expected
