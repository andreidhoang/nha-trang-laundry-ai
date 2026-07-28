"""Deterministic delivery fee and vehicle decision boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from nha_trang_laundry_domain.catalog import ErrorCode, FulfillmentMode, PolicyOutcome

WEIGHT_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
MAX_NUMERIC_WEIGHT_KG: Final = Decimal("999999999.999")
MAX_BIGINT: Final = 9_223_372_036_854_775_807


class DeliveryVehicle(StrEnum):
    MOTORCYCLE = "MOTORCYCLE"
    CAR = "CAR"


class DeliveryFeeResolution(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    AUTO_FIXED = "AUTO_FIXED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_INPUT_REQUIRED = "HUMAN_INPUT_REQUIRED"


class DeliveryReason(StrEnum):
    SELF_SERVICE_NO_DELIVERY_JOB = "SELF_SERVICE_NO_DELIVERY_JOB"
    NEAR_ZONE_FREE = "NEAR_ZONE_FREE"
    MID_ZONE_FIXED_FEE = "MID_ZONE_FIXED_FEE"
    FAR_ZONE_MANUAL_FEE = "FAR_ZONE_MANUAL_FEE"
    ONE_LEG_PRICE_UNRESOLVED = "ONE_LEG_PRICE_UNRESOLVED"
    MANUAL_FEE_RECORDED_AND_ACKNOWLEDGED = "MANUAL_FEE_RECORDED_AND_ACKNOWLEDGED"
    DELIVERY_DISTANCE_UNVERIFIED = ErrorCode.DELIVERY_DISTANCE_UNVERIFIED
    DELIVERY_FEE_REQUIRES_HUMAN = ErrorCode.DELIVERY_FEE_REQUIRES_HUMAN
    TRANSPORT_WEIGHT_REQUIRES_HUMAN = "TRANSPORT_WEIGHT_REQUIRES_HUMAN"
    VEHICLE_RECOMMENDATION_ONLY = "VEHICLE_RECOMMENDATION_ONLY"
    SLOT_APPROVAL_REQUIRED = ErrorCode.SLOT_APPROVAL_REQUIRED


class DeliveryError(ValueError):
    """Raised when delivery facts are malformed or conflict with confirmed policy."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class DeliveryCalculationTrace:
    fulfillment_mode: FulfillmentMode
    verified_distance_m: int | None
    distance_zone: str | None
    planned_transport_weight_kg: str | None
    vehicle_threshold_kg: str
    fee_rule: str
    manual_fee_candidate_vnd: int | None
    customer_acknowledged_manual_fee: bool
    delivery_job_required: bool
    dispatch_authorized: bool


@dataclass(frozen=True)
class DeliveryResult:
    delivery_fee_vnd: int | None
    fee_resolution: DeliveryFeeResolution
    fee_outcome: PolicyOutcome
    vehicle_recommendation: DeliveryVehicle | None
    vehicle_outcome: PolicyOutcome
    slot_outcome: PolicyOutcome
    overall_outcome: PolicyOutcome
    delivery_job_required: bool
    reason_codes: tuple[DeliveryReason, ...]
    trace: DeliveryCalculationTrace


def evaluate_delivery(
    mode: FulfillmentMode,
    *,
    verified_distance_m: int | None,
    planned_transport_weight_kg: str | None,
    approved_manual_fee_vnd: int | None = None,
    customer_acknowledged_manual_fee: bool = False,
) -> DeliveryResult:
    """Resolve fee and advisory vehicle without authorizing a delivery slot or dispatch."""
    if not isinstance(mode, FulfillmentMode) or not isinstance(
        customer_acknowledged_manual_fee, bool
    ):
        raise DeliveryError(ErrorCode.VALIDATION_ERROR)
    _validate_distance(verified_distance_m)
    planned_weight = (
        _weight(planned_transport_weight_kg) if planned_transport_weight_kg is not None else None
    )
    if approved_manual_fee_vnd is not None:
        _validate_vnd(approved_manual_fee_vnd)

    if mode is FulfillmentMode.SELF_DROP_SELF_COLLECT:
        if approved_manual_fee_vnd is not None or customer_acknowledged_manual_fee:
            raise DeliveryError(ErrorCode.VALIDATION_ERROR)
        return DeliveryResult(
            delivery_fee_vnd=0,
            fee_resolution=DeliveryFeeResolution.NOT_APPLICABLE,
            fee_outcome=PolicyOutcome.ALLOW,
            vehicle_recommendation=None,
            vehicle_outcome=PolicyOutcome.ALLOW,
            slot_outcome=PolicyOutcome.ALLOW,
            overall_outcome=PolicyOutcome.ALLOW,
            delivery_job_required=False,
            reason_codes=(DeliveryReason.SELF_SERVICE_NO_DELIVERY_JOB,),
            trace=DeliveryCalculationTrace(
                fulfillment_mode=mode,
                verified_distance_m=verified_distance_m,
                distance_zone=_distance_zone(verified_distance_m),
                planned_transport_weight_kg=planned_transport_weight_kg,
                vehicle_threshold_kg="20",
                fee_rule="NO_DELIVERY_JOB",
                manual_fee_candidate_vnd=None,
                customer_acknowledged_manual_fee=False,
                delivery_job_required=False,
                dispatch_authorized=False,
            ),
        )

    reasons: list[DeliveryReason] = []
    vehicle, vehicle_outcome = _resolve_vehicle(planned_weight)
    if vehicle is None:
        reasons.append(DeliveryReason.TRANSPORT_WEIGHT_REQUIRES_HUMAN)
    else:
        reasons.append(DeliveryReason.VEHICLE_RECOMMENDATION_ONLY)

    zone = _distance_zone(verified_distance_m)
    fee: int | None
    fee_resolution: DeliveryFeeResolution
    fee_outcome: PolicyOutcome
    fee_rule: str

    if mode in {FulfillmentMode.PICKUP_ONLY, FulfillmentMode.RETURN_ONLY}:
        reasons.append(DeliveryReason.ONE_LEG_PRICE_UNRESOLVED)
        fee, fee_resolution, fee_outcome = _resolve_manual_fee(
            approved_manual_fee_vnd, customer_acknowledged_manual_fee, reasons
        )
        fee_rule = "ONE_LEG_HUMAN_PRICE"
    elif verified_distance_m is None:
        if approved_manual_fee_vnd is not None or customer_acknowledged_manual_fee:
            raise DeliveryError(ErrorCode.VALIDATION_ERROR)
        fee = None
        fee_resolution = DeliveryFeeResolution.HUMAN_INPUT_REQUIRED
        fee_outcome = PolicyOutcome.REQUIRE_HUMAN
        fee_rule = "VERIFIED_DISTANCE_REQUIRED"
        reasons.extend(
            (
                DeliveryReason.DELIVERY_DISTANCE_UNVERIFIED,
                DeliveryReason.DELIVERY_FEE_REQUIRES_HUMAN,
            )
        )
    elif verified_distance_m <= 2_000:
        _reject_manual_override(approved_manual_fee_vnd, customer_acknowledged_manual_fee)
        fee = 0
        fee_resolution = DeliveryFeeResolution.AUTO_FIXED
        fee_outcome = PolicyOutcome.ALLOW
        fee_rule = "DISTANCE_M_LE_2000"
        reasons.append(DeliveryReason.NEAR_ZONE_FREE)
    elif verified_distance_m <= 6_000:
        _reject_manual_override(approved_manual_fee_vnd, customer_acknowledged_manual_fee)
        fee = 10_000
        fee_resolution = DeliveryFeeResolution.AUTO_FIXED
        fee_outcome = PolicyOutcome.ALLOW
        fee_rule = "2000_LT_DISTANCE_M_LE_6000"
        reasons.append(DeliveryReason.MID_ZONE_FIXED_FEE)
    else:
        reasons.append(DeliveryReason.FAR_ZONE_MANUAL_FEE)
        fee, fee_resolution, fee_outcome = _resolve_manual_fee(
            approved_manual_fee_vnd, customer_acknowledged_manual_fee, reasons
        )
        fee_rule = "DISTANCE_M_GT_6000_HUMAN_PRICE"

    # Stage 0/Shadow has 0kg/day auto-confirmable capacity. Fee resolution never grants a slot.
    slot_outcome = PolicyOutcome.REQUIRE_HUMAN
    reasons.append(DeliveryReason.SLOT_APPROVAL_REQUIRED)
    overall = (
        PolicyOutcome.REQUIRE_HUMAN
        if PolicyOutcome.REQUIRE_HUMAN in {fee_outcome, vehicle_outcome, slot_outcome}
        else PolicyOutcome.ALLOW
    )
    return DeliveryResult(
        delivery_fee_vnd=fee,
        fee_resolution=fee_resolution,
        fee_outcome=fee_outcome,
        vehicle_recommendation=vehicle,
        vehicle_outcome=vehicle_outcome,
        slot_outcome=slot_outcome,
        overall_outcome=overall,
        delivery_job_required=True,
        reason_codes=tuple(reasons),
        trace=DeliveryCalculationTrace(
            fulfillment_mode=mode,
            verified_distance_m=verified_distance_m,
            distance_zone=zone,
            planned_transport_weight_kg=planned_transport_weight_kg,
            vehicle_threshold_kg="20",
            fee_rule=fee_rule,
            manual_fee_candidate_vnd=approved_manual_fee_vnd,
            customer_acknowledged_manual_fee=customer_acknowledged_manual_fee,
            delivery_job_required=True,
            dispatch_authorized=False,
        ),
    )


def _resolve_vehicle(weight: Decimal | None) -> tuple[DeliveryVehicle | None, PolicyOutcome]:
    if weight is None:
        return None, PolicyOutcome.REQUIRE_HUMAN
    vehicle = DeliveryVehicle.MOTORCYCLE if weight < Decimal("20") else DeliveryVehicle.CAR
    return vehicle, PolicyOutcome.ALLOW


def _resolve_manual_fee(
    approved_fee: int | None,
    acknowledged: bool,
    reasons: list[DeliveryReason],
) -> tuple[int | None, DeliveryFeeResolution, PolicyOutcome]:
    if approved_fee is not None and acknowledged:
        reasons.append(DeliveryReason.MANUAL_FEE_RECORDED_AND_ACKNOWLEDGED)
        return approved_fee, DeliveryFeeResolution.HUMAN_APPROVED, PolicyOutcome.ALLOW
    reasons.append(DeliveryReason.DELIVERY_FEE_REQUIRES_HUMAN)
    return None, DeliveryFeeResolution.HUMAN_INPUT_REQUIRED, PolicyOutcome.REQUIRE_HUMAN


def _reject_manual_override(approved_fee: int | None, acknowledged: bool) -> None:
    if approved_fee is not None or acknowledged:
        raise DeliveryError(ErrorCode.VALIDATION_ERROR)


def _distance_zone(distance_m: int | None) -> str | None:
    if distance_m is None:
        return None
    if distance_m <= 2_000:
        return "LE_2_KM"
    if distance_m <= 6_000:
        return "GT_2_LE_6_KM"
    return "GT_6_KM"


def _validate_distance(distance_m: int | None) -> None:
    if distance_m is None:
        return
    if (
        isinstance(distance_m, bool)
        or not isinstance(distance_m, int)
        or not 0 <= distance_m <= MAX_BIGINT
    ):
        raise DeliveryError(ErrorCode.VALIDATION_ERROR)


def _validate_vnd(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_BIGINT:
        raise DeliveryError(ErrorCode.VALIDATION_ERROR)


def _weight(value: str) -> Decimal:
    if not isinstance(value, str) or not WEIGHT_PATTERN.fullmatch(value):
        raise DeliveryError(ErrorCode.MISSING_REQUIRED_FACT)
    try:
        weight = Decimal(value)
    except InvalidOperation as error:
        raise DeliveryError(ErrorCode.MISSING_REQUIRED_FACT) from error
    exponent = weight.as_tuple().exponent
    if not isinstance(exponent, int):
        raise DeliveryError(ErrorCode.MISSING_REQUIRED_FACT)
    if (
        not weight.is_finite()
        or weight <= 0
        or weight > MAX_NUMERIC_WEIGHT_KG
        or max(0, -exponent) > 3
    ):
        raise DeliveryError(ErrorCode.MISSING_REQUIRED_FACT)
    return weight
