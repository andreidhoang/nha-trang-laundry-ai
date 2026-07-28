"""Production SLA risk calculations separated from promise and remedy authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from nha_trang_laundry_domain.catalog import (
    CommitmentAuthority,
    ErrorCode,
    PolicyOutcome,
    SlaLifecycle,
    SlaOutcome,
)


class SlaPolicyType(StrEnum):
    COMMITMENT = "COMMITMENT"
    GUIDANCE_RANGE = "GUIDANCE_RANGE"
    RESPONSE_TARGET = "RESPONSE_TARGET"
    HUMAN_ETA_REQUIRED = "HUMAN_ETA_REQUIRED"


class SlaReason(StrEnum):
    PRODUCTION_NOT_ACCEPTED = "PRODUCTION_NOT_ACCEPTED"
    PRODUCTION_SLA_EXCLUDES_DELIVERY = "PRODUCTION_SLA_EXCLUDES_DELIVERY"
    ELAPSED_EIGHT_HOUR_INTERNAL_RISK = "ELAPSED_EIGHT_HOUR_INTERNAL_RISK"
    SLA_PENDING = "SLA_PENDING"
    SLA_MET = "SLA_MET"
    SLA_BREACHED = "SLA_BREACHED"
    BREACH_REMEDY_REQUIRES_HUMAN = "BREACH_REMEDY_REQUIRES_HUMAN"
    EXACT_CLOSURE_CUTOFF_UNPUBLISHED = "EXACT_CLOSURE_CUTOFF_UNPUBLISHED"
    HUMAN_PROMISE_REQUIRED = "HUMAN_PROMISE_REQUIRED"
    HUMAN_PROMISE_RECORDED = "HUMAN_PROMISE_RECORDED"
    GUIDANCE_RANGE_24_TO_48_HOURS = "GUIDANCE_RANGE_24_TO_48_HOURS"
    GUIDANCE_DOES_NOT_CREATE_BREACH = "GUIDANCE_DOES_NOT_CREATE_BREACH"
    HUMAN_ETA_REQUIRED = "HUMAN_ETA_REQUIRED"


class SlaError(ValueError):
    """Raised when SLA facts or policies cannot be evaluated safely."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class ProductionSlaPolicy:
    policy_id: str
    policy_type: SlaPolicyType
    target_min_hours: int | None
    target_max_hours: int | None
    commitment_authority: CommitmentAuthority


@dataclass(frozen=True)
class SlaCalculationTrace:
    policy_id: str
    policy_type: SlaPolicyType
    clock_start_event: str
    clock_end_event: str
    delivery_included: bool
    target_min_hours: int | None
    target_max_hours: int | None
    actual_elapsed_microseconds: int | None
    internal_risk_due_at: datetime | None
    breach_microseconds: int
    automatic_promise_authorized: bool
    automatic_breach_remedy_authorized: bool


@dataclass(frozen=True)
class ProductionSlaResult:
    lifecycle: SlaLifecycle
    outcome: SlaOutcome
    promise_outcome: PolicyOutcome
    review_outcome: PolicyOutcome
    overall_outcome: PolicyOutcome
    guidance_window_start_at: datetime | None
    guidance_window_end_at: datetime | None
    internal_risk_due_at: datetime | None
    human_confirmed_promised_ready_at_store: datetime | None
    reason_codes: tuple[SlaReason, ...]
    trace: SlaCalculationTrace


STANDARD_WASH_SLA = ProductionSlaPolicy(
    policy_id="SLA_STANDARD_CLOTHES",
    policy_type=SlaPolicyType.COMMITMENT,
    target_min_hours=None,
    target_max_hours=8,
    commitment_authority=CommitmentAuthority.HUMAN_CONFIRM,
)

SPECIAL_ITEM_GUIDANCE_SLA = ProductionSlaPolicy(
    policy_id="SLA_SPECIAL_GUIDANCE_24_48",
    policy_type=SlaPolicyType.GUIDANCE_RANGE,
    target_min_hours=24,
    target_max_hours=48,
    commitment_authority=CommitmentAuthority.HUMAN_CONFIRM,
)

OTHER_SPECIAL_ITEM_SLA = ProductionSlaPolicy(
    policy_id="SLA_OTHER_SPECIAL",
    policy_type=SlaPolicyType.HUMAN_ETA_REQUIRED,
    target_min_hours=None,
    target_max_hours=None,
    commitment_authority=CommitmentAuthority.HUMAN_CONFIRM,
)


def evaluate_production_sla(
    policy: ProductionSlaPolicy,
    *,
    evaluated_at: datetime,
    production_accepted_at: datetime | None = None,
    ready_at_store: datetime | None = None,
    human_confirmed_promised_ready_at_store: datetime | None = None,
) -> ProductionSlaResult:
    """Calculate production risk; never convert it into dispatch, promise, or remedy authority."""
    _validate_policy(policy)
    _require_aware(evaluated_at)
    for value in (
        production_accepted_at,
        ready_at_store,
        human_confirmed_promised_ready_at_store,
    ):
        if value is not None:
            _require_aware(value)
    if ready_at_store is not None and production_accepted_at is None:
        raise SlaError(ErrorCode.INVALID_STATE_TRANSITION)
    if production_accepted_at is not None:
        if evaluated_at < production_accepted_at:
            raise SlaError(ErrorCode.VALIDATION_ERROR)
        if ready_at_store is not None and ready_at_store < production_accepted_at:
            raise SlaError(ErrorCode.INVALID_STATE_TRANSITION)
        if (
            human_confirmed_promised_ready_at_store is not None
            and human_confirmed_promised_ready_at_store < production_accepted_at
        ):
            raise SlaError(ErrorCode.VALIDATION_ERROR)

    lifecycle = _lifecycle(
        production_accepted_at, ready_at_store, human_confirmed_promised_ready_at_store
    )
    promise_outcome = (
        PolicyOutcome.ALLOW
        if human_confirmed_promised_ready_at_store is not None
        else PolicyOutcome.REQUIRE_HUMAN
    )
    reasons = [SlaReason.PRODUCTION_SLA_EXCLUDES_DELIVERY]
    reasons.append(
        SlaReason.HUMAN_PROMISE_RECORDED
        if promise_outcome is PolicyOutcome.ALLOW
        else SlaReason.HUMAN_PROMISE_REQUIRED
    )

    guidance_start: datetime | None = None
    guidance_end: datetime | None = None
    internal_due: datetime | None = None
    elapsed_microseconds: int | None = None
    breach_microseconds = 0
    review_outcome = PolicyOutcome.ALLOW

    if production_accepted_at is None:
        outcome = SlaOutcome.PENDING
        reasons.append(SlaReason.PRODUCTION_NOT_ACCEPTED)
    elif policy.policy_type is SlaPolicyType.COMMITMENT:
        assert policy.target_max_hours is not None
        internal_due = production_accepted_at + timedelta(hours=policy.target_max_hours)
        comparison_at = ready_at_store or evaluated_at
        elapsed_microseconds = _timedelta_microseconds(comparison_at - production_accepted_at)
        breach_microseconds = max(0, _timedelta_microseconds(comparison_at - internal_due))
        reasons.extend(
            (
                SlaReason.ELAPSED_EIGHT_HOUR_INTERNAL_RISK,
                SlaReason.EXACT_CLOSURE_CUTOFF_UNPUBLISHED,
            )
        )
        if comparison_at <= internal_due:
            outcome = SlaOutcome.MET if ready_at_store is not None else SlaOutcome.PENDING
            reasons.append(
                SlaReason.SLA_MET if ready_at_store is not None else SlaReason.SLA_PENDING
            )
        else:
            outcome = SlaOutcome.BREACHED
            review_outcome = PolicyOutcome.REQUIRE_HUMAN
            reasons.extend((SlaReason.SLA_BREACHED, SlaReason.BREACH_REMEDY_REQUIRES_HUMAN))
    elif policy.policy_type is SlaPolicyType.GUIDANCE_RANGE:
        assert policy.target_min_hours is not None and policy.target_max_hours is not None
        guidance_start = production_accepted_at + timedelta(hours=policy.target_min_hours)
        guidance_end = production_accepted_at + timedelta(hours=policy.target_max_hours)
        comparison_at = ready_at_store or evaluated_at
        elapsed_microseconds = _timedelta_microseconds(comparison_at - production_accepted_at)
        outcome = SlaOutcome.PENDING
        reasons.extend(
            (
                SlaReason.GUIDANCE_RANGE_24_TO_48_HOURS,
                SlaReason.GUIDANCE_DOES_NOT_CREATE_BREACH,
            )
        )
    else:
        outcome = SlaOutcome.PENDING
        reasons.append(SlaReason.HUMAN_ETA_REQUIRED)

    if policy.policy_type is SlaPolicyType.HUMAN_ETA_REQUIRED:
        promise_outcome = PolicyOutcome.REQUIRE_HUMAN
    overall = (
        PolicyOutcome.REQUIRE_HUMAN
        if PolicyOutcome.REQUIRE_HUMAN in {promise_outcome, review_outcome}
        else PolicyOutcome.ALLOW
    )
    trace = SlaCalculationTrace(
        policy_id=policy.policy_id,
        policy_type=policy.policy_type,
        clock_start_event="production_accepted_at",
        clock_end_event="ready_at_store",
        delivery_included=False,
        target_min_hours=policy.target_min_hours,
        target_max_hours=policy.target_max_hours,
        actual_elapsed_microseconds=elapsed_microseconds,
        internal_risk_due_at=internal_due,
        breach_microseconds=breach_microseconds,
        automatic_promise_authorized=False,
        automatic_breach_remedy_authorized=False,
    )
    return ProductionSlaResult(
        lifecycle=lifecycle,
        outcome=outcome,
        promise_outcome=promise_outcome,
        review_outcome=review_outcome,
        overall_outcome=overall,
        guidance_window_start_at=guidance_start,
        guidance_window_end_at=guidance_end,
        internal_risk_due_at=internal_due,
        human_confirmed_promised_ready_at_store=human_confirmed_promised_ready_at_store,
        reason_codes=tuple(reasons),
        trace=trace,
    )


def _lifecycle(
    start: datetime | None, end: datetime | None, promised: datetime | None
) -> SlaLifecycle:
    if end is not None:
        return SlaLifecycle.COMPLETED
    if start is not None:
        return SlaLifecycle.RUNNING
    if promised is not None:
        return SlaLifecycle.SCHEDULED
    return SlaLifecycle.DRAFT


def _validate_policy(policy: ProductionSlaPolicy) -> None:
    if not policy.policy_id or not isinstance(policy.policy_type, SlaPolicyType):
        raise SlaError(ErrorCode.VALIDATION_ERROR)
    if not isinstance(policy.commitment_authority, CommitmentAuthority):
        raise SlaError(ErrorCode.VALIDATION_ERROR)
    if policy.commitment_authority is not CommitmentAuthority.HUMAN_CONFIRM:
        raise SlaError(ErrorCode.VALIDATION_ERROR)
    if policy.policy_type is SlaPolicyType.RESPONSE_TARGET:
        raise SlaError(ErrorCode.VALIDATION_ERROR)
    minimum = policy.target_min_hours
    maximum = policy.target_max_hours
    if minimum is not None and (isinstance(minimum, bool) or minimum <= 0):
        raise SlaError(ErrorCode.VALIDATION_ERROR)
    if maximum is not None and (isinstance(maximum, bool) or maximum <= 0):
        raise SlaError(ErrorCode.VALIDATION_ERROR)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise SlaError(ErrorCode.VALIDATION_ERROR)
    if policy.policy_type is SlaPolicyType.COMMITMENT and (minimum is not None or maximum is None):
        raise SlaError(ErrorCode.VALIDATION_ERROR)
    if policy.policy_type is SlaPolicyType.GUIDANCE_RANGE and (minimum is None or maximum is None):
        raise SlaError(ErrorCode.VALIDATION_ERROR)
    if policy.policy_type is SlaPolicyType.HUMAN_ETA_REQUIRED and (
        minimum is not None or maximum is not None
    ):
        raise SlaError(ErrorCode.VALIDATION_ERROR)


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SlaError(ErrorCode.VALIDATION_ERROR)


def _timedelta_microseconds(value: timedelta) -> int:
    return value.days * 24 * 60 * 60 * 1_000_000 + value.seconds * 1_000_000 + value.microseconds
