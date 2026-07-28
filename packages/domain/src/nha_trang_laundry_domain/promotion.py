"""Deterministic promotion decisions and integer-VND discount allocation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from nha_trang_laundry_domain.catalog import (
    ErrorCode,
    PolicyOutcome,
    PromotionEligibilityEvent,
    PromotionResolution,
    PromotionStatus,
)

MAX_BIGINT_VND: Final = 9_223_372_036_854_775_807
RATE_DENOMINATOR: Final = 10_000
PROMOTION_TIMEZONE: Final = ZoneInfo("Asia/Ho_Chi_Minh")


class PromotionReason(StrEnum):
    PROMOTION_APPLIED = "PROMOTION_APPLIED"
    PROMOTION_OUTSIDE_INTERVAL = "PROMOTION_OUTSIDE_INTERVAL"
    PROMOTION_NOT_TARGETED = "PROMOTION_NOT_TARGETED"
    PROMOTION_TARGET_REQUIRES_HUMAN = "PROMOTION_TARGET_REQUIRES_HUMAN"
    PROMOTION_STACKING_REQUIRES_HUMAN = "PROMOTION_STACKING_REQUIRES_HUMAN"
    PROMOTION_ELIGIBILITY_UNRESOLVED = ErrorCode.PROMOTION_ELIGIBILITY_UNRESOLVED


class PromotionError(ValueError):
    """Raised when promotion inputs cannot be evaluated safely."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class PromotionPolicy:
    code: str
    start_at: datetime
    end_at_exclusive: datetime
    timezone: str
    eligibility_event: PromotionEligibilityEvent | None
    stacking_allowed: bool = False


@dataclass(frozen=True)
class PromotionLine:
    line_id: str
    list_amount_vnd: int
    resolution: PromotionResolution
    rate_bps: int | None = None


@dataclass(frozen=True)
class PromotionLineAdjustment:
    line_id: str
    list_amount_vnd: int
    rate_bps: int | None
    discount_vnd: int
    net_amount_vnd: int
    resolution: PromotionResolution


@dataclass(frozen=True)
class PromotionAllocationTrace:
    rate_bps: int
    eligible_line_ids: tuple[str, ...]
    eligible_subtotal_vnd: int
    exact_discount_numerator: int
    exact_discount_denominator: int
    rounded_discount_vnd: int
    floor_allocations_vnd: tuple[int, ...]
    remainders: tuple[int, ...]
    remainder_award_order: tuple[str, ...]
    final_allocations_vnd: tuple[int, ...]


@dataclass(frozen=True)
class PromotionCalculationTrace:
    policy_code: str
    interval_start_at: datetime
    interval_end_at_exclusive: datetime
    configured_eligibility_event: PromotionEligibilityEvent | None
    observed_eligibility_event: PromotionEligibilityEvent | None
    observed_eligibility_at: datetime | None
    candidate_reference_at: datetime
    candidate_inside_interval: bool
    eligibility_resolved: bool
    allocation_groups: tuple[PromotionAllocationTrace, ...]
    rounding: str
    delivery_discount_vnd: int


@dataclass(frozen=True)
class PromotionResult:
    status: PromotionStatus
    outcome: PolicyOutcome
    list_service_subtotal_vnd: int
    eligible_service_subtotal_vnd: int
    discount_amount_vnd: int
    net_service_subtotal_vnd: int
    delivery_fee_vnd: int
    display_total_vnd: int
    eligibility_event: PromotionEligibilityEvent | None
    eligibility_at: datetime | None
    adjustments: tuple[PromotionLineAdjustment, ...]
    reason_codes: tuple[PromotionReason, ...]
    trace: PromotionCalculationTrace


CURRENT_PROMOTION = PromotionPolicy(
    code="PROMO_WET30_DRY40_20260717_20260831",
    start_at=datetime(2026, 7, 17, tzinfo=PROMOTION_TIMEZONE),
    end_at_exclusive=datetime(2026, 9, 1, tzinfo=PROMOTION_TIMEZONE),
    timezone="Asia/Ho_Chi_Minh",
    # DEC-002 remains open. A generic accepted_at is prohibited.
    eligibility_event=None,
)


def evaluate_promotion(
    policy: PromotionPolicy,
    lines: tuple[PromotionLine, ...],
    *,
    evaluation_at: datetime,
    eligibility_event: PromotionEligibilityEvent | None = None,
    eligibility_at: datetime | None = None,
    delivery_fee_vnd: int = 0,
    other_promotion_present: bool = False,
) -> PromotionResult:
    """Evaluate a published target snapshot without inventing eligibility semantics."""
    _validate_policy(policy)
    _require_aware(evaluation_at)
    if eligibility_event is not None and not isinstance(
        eligibility_event, PromotionEligibilityEvent
    ):
        raise PromotionError(ErrorCode.VALIDATION_ERROR)
    if eligibility_at is not None:
        _require_aware(eligibility_at)
    _require_vnd(delivery_fee_vnd)
    if not lines:
        raise PromotionError(ErrorCode.MISSING_REQUIRED_FACT)
    if not isinstance(other_promotion_present, bool):
        raise PromotionError(ErrorCode.VALIDATION_ERROR)

    seen_line_ids: set[str] = set()
    list_subtotal = 0
    has_auto_target = False
    has_human_target = False
    for line in lines:
        _validate_line(line, seen_line_ids)
        seen_line_ids.add(line.line_id)
        list_subtotal = _checked_add(list_subtotal, line.list_amount_vnd)
        has_auto_target = has_auto_target or line.resolution is PromotionResolution.AUTO_IF_TARGETED
        has_human_target = has_human_target or line.resolution is PromotionResolution.HUMAN_CONFIRM

    reference_at = eligibility_at or evaluation_at
    inside_interval = policy.start_at <= reference_at < policy.end_at_exclusive
    eligibility_resolved = (
        policy.eligibility_event is not None
        and eligibility_event is policy.eligibility_event
        and eligibility_at is not None
    )
    relevant = has_auto_target or has_human_target
    apply_candidate = relevant and inside_interval

    allocations: dict[str, int] = {}
    group_traces: list[PromotionAllocationTrace] = []
    eligible_subtotal = 0
    if apply_candidate:
        grouped: dict[int, list[PromotionLine]] = {}
        for line in lines:
            if line.resolution is PromotionResolution.AUTO_IF_TARGETED:
                assert line.rate_bps is not None
                grouped.setdefault(line.rate_bps, []).append(line)
                eligible_subtotal = _checked_add(eligible_subtotal, line.list_amount_vnd)
        for rate_bps in sorted(grouped):
            group_allocations, group_trace = _allocate_group(tuple(grouped[rate_bps]), rate_bps)
            allocations.update(group_allocations)
            group_traces.append(group_trace)

    discount = sum(allocations.values())
    if discount > list_subtotal:
        raise PromotionError(ErrorCode.VALIDATION_ERROR)
    net_subtotal = list_subtotal - discount
    display_total = _checked_add(net_subtotal, delivery_fee_vnd)
    adjustments = tuple(
        PromotionLineAdjustment(
            line_id=line.line_id,
            list_amount_vnd=line.list_amount_vnd,
            rate_bps=line.rate_bps,
            discount_vnd=allocations.get(line.line_id, 0),
            net_amount_vnd=line.list_amount_vnd - allocations.get(line.line_id, 0),
            resolution=line.resolution,
        )
        for line in lines
    )

    reasons: list[PromotionReason] = []
    if not relevant:
        status = PromotionStatus.NOT_APPLICABLE
        outcome = PolicyOutcome.ALLOW
        reasons.append(PromotionReason.PROMOTION_NOT_TARGETED)
    elif not eligibility_resolved:
        status = PromotionStatus.PROVISIONAL
        outcome = PolicyOutcome.REQUIRE_HUMAN
        reasons.append(PromotionReason.PROMOTION_ELIGIBILITY_UNRESOLVED)
    elif not inside_interval:
        status = PromotionStatus.INELIGIBLE
        outcome = PolicyOutcome.ALLOW
        reasons.append(PromotionReason.PROMOTION_OUTSIDE_INTERVAL)
    elif has_human_target or (other_promotion_present and not policy.stacking_allowed):
        status = PromotionStatus.REQUIRES_HUMAN
        outcome = PolicyOutcome.REQUIRE_HUMAN
    else:
        status = PromotionStatus.ELIGIBLE
        outcome = PolicyOutcome.ALLOW

    if inside_interval and has_human_target:
        reasons.append(PromotionReason.PROMOTION_TARGET_REQUIRES_HUMAN)
    if inside_interval and other_promotion_present and not policy.stacking_allowed:
        reasons.append(PromotionReason.PROMOTION_STACKING_REQUIRES_HUMAN)
        outcome = PolicyOutcome.REQUIRE_HUMAN
        if status not in {PromotionStatus.PROVISIONAL, PromotionStatus.NOT_APPLICABLE}:
            status = PromotionStatus.REQUIRES_HUMAN
    if discount:
        reasons.append(PromotionReason.PROMOTION_APPLIED)

    public_event = eligibility_event if eligibility_resolved else None
    public_event_at = eligibility_at if eligibility_resolved else None
    trace = PromotionCalculationTrace(
        policy_code=policy.code,
        interval_start_at=policy.start_at,
        interval_end_at_exclusive=policy.end_at_exclusive,
        configured_eligibility_event=policy.eligibility_event,
        observed_eligibility_event=eligibility_event,
        observed_eligibility_at=eligibility_at,
        candidate_reference_at=reference_at,
        candidate_inside_interval=inside_interval,
        eligibility_resolved=eligibility_resolved,
        allocation_groups=tuple(group_traces),
        rounding="ROUND_HALF_UP_1_VND_THEN_LARGEST_REMAINDER_LINE_ID_ASC",
        delivery_discount_vnd=0,
    )
    return PromotionResult(
        status=status,
        outcome=outcome,
        list_service_subtotal_vnd=list_subtotal,
        eligible_service_subtotal_vnd=eligible_subtotal,
        discount_amount_vnd=discount,
        net_service_subtotal_vnd=net_subtotal,
        delivery_fee_vnd=delivery_fee_vnd,
        display_total_vnd=display_total,
        eligibility_event=public_event,
        eligibility_at=public_event_at,
        adjustments=adjustments,
        reason_codes=tuple(reasons),
        trace=trace,
    )


def _allocate_group(
    lines: tuple[PromotionLine, ...], rate_bps: int
) -> tuple[dict[str, int], PromotionAllocationTrace]:
    ordered = tuple(sorted(lines, key=lambda line: line.line_id))
    subtotal = sum(line.list_amount_vnd for line in ordered)
    numerator = subtotal * rate_bps
    rounded_group_discount = (numerator + RATE_DENOMINATOR // 2) // RATE_DENOMINATOR
    floor_allocations: list[int] = []
    remainders: list[int] = []
    for line in ordered:
        floor_amount, remainder = divmod(line.list_amount_vnd * rate_bps, RATE_DENOMINATOR)
        floor_allocations.append(floor_amount)
        remainders.append(remainder)
    awards_needed = rounded_group_discount - sum(floor_allocations)
    award_indexes = sorted(
        range(len(ordered)), key=lambda index: (-remainders[index], ordered[index].line_id)
    )[:awards_needed]
    final_allocations = list(floor_allocations)
    for index in award_indexes:
        final_allocations[index] += 1
    allocations = {line.line_id: final_allocations[index] for index, line in enumerate(ordered)}
    trace = PromotionAllocationTrace(
        rate_bps=rate_bps,
        eligible_line_ids=tuple(line.line_id for line in ordered),
        eligible_subtotal_vnd=subtotal,
        exact_discount_numerator=numerator,
        exact_discount_denominator=RATE_DENOMINATOR,
        rounded_discount_vnd=rounded_group_discount,
        floor_allocations_vnd=tuple(floor_allocations),
        remainders=tuple(remainders),
        remainder_award_order=tuple(ordered[index].line_id for index in award_indexes),
        final_allocations_vnd=tuple(final_allocations),
    )
    return allocations, trace


def _validate_policy(policy: PromotionPolicy) -> None:
    if not policy.code or policy.timezone != "Asia/Ho_Chi_Minh":
        raise PromotionError(ErrorCode.VALIDATION_ERROR)
    _require_aware(policy.start_at)
    _require_aware(policy.end_at_exclusive)
    if policy.start_at >= policy.end_at_exclusive or not isinstance(policy.stacking_allowed, bool):
        raise PromotionError(ErrorCode.VALIDATION_ERROR)
    if policy.eligibility_event is not None and not isinstance(
        policy.eligibility_event, PromotionEligibilityEvent
    ):
        raise PromotionError(ErrorCode.VALIDATION_ERROR)


def _validate_line(line: PromotionLine, seen_line_ids: set[str]) -> None:
    if not line.line_id or line.line_id in seen_line_ids:
        raise PromotionError(ErrorCode.VALIDATION_ERROR)
    _require_vnd(line.list_amount_vnd)
    if not isinstance(line.resolution, PromotionResolution):
        raise PromotionError(ErrorCode.VALIDATION_ERROR)
    if line.resolution is PromotionResolution.NOT_ELIGIBLE:
        if line.rate_bps is not None:
            raise PromotionError(ErrorCode.VALIDATION_ERROR)
    elif (
        not isinstance(line.rate_bps, int)
        or isinstance(line.rate_bps, bool)
        or not 0 < line.rate_bps <= RATE_DENOMINATOR
    ):
        raise PromotionError(ErrorCode.VALIDATION_ERROR)


def _require_vnd(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_BIGINT_VND:
        raise PromotionError(ErrorCode.VALIDATION_ERROR)


def _checked_add(first: int, second: int) -> int:
    value = first + second
    if value > MAX_BIGINT_VND:
        raise PromotionError(ErrorCode.VALIDATION_ERROR)
    return value


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PromotionError(ErrorCode.VALIDATION_ERROR)
