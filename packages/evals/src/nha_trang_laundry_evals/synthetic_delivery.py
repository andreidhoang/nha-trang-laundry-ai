"""Deterministic delivery boundary preflight with server-owned distance and weight facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import rfc8785
from nha_trang_laundry_domain.catalog import FulfillmentMode
from nha_trang_laundry_domain.delivery import DeliveryReason, evaluate_delivery

from .fixtures import SyntheticFixtureBundle


@dataclass(frozen=True, slots=True)
class SyntheticDeliveryPreflight:
    tool_trace: tuple[Mapping[str, object], ...]
    assertion_results: Mapping[str, bool]
    trace_id: str


def execute_delivery_preflight(fixture: SyntheticFixtureBundle) -> SyntheticDeliveryPreflight:
    seed = fixture.payload.get("database_seed")
    if not isinstance(seed, Mapping):
        raise ValueError("delivery fixture seed is invalid")
    distance = seed.get("verified_distance_m")
    weight = seed.get("planned_transport_weight_kg")
    if not isinstance(distance, int) or isinstance(distance, bool) or not isinstance(weight, str):
        raise ValueError("delivery fixture facts are invalid")
    result = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=distance,
        planned_transport_weight_kg=weight,
    )
    facts: dict[str, object] = {
        "delivery_fee_vnd": result.delivery_fee_vnd,
        "vehicle_recommendation": (
            result.vehicle_recommendation.value
            if result.vehicle_recommendation is not None
            else None
        ),
        "fee_status": (
            "REQUIRES_HUMAN" if result.delivery_fee_vnd is None else result.fee_resolution.value
        ),
        "fee_requires_human_reason": (
            DeliveryReason.DELIVERY_FEE_REQUIRES_HUMAN in result.reason_codes
        ),
    }
    expected: dict[int, Mapping[str, tuple[str, object]]] = {
        2000: {
            "ASSERT_EEE2CF71E34CCA59": ("delivery_fee_vnd", 0),
            "ASSERT_D8ACE300E9205231": ("vehicle_recommendation", "MOTORCYCLE"),
        },
        5000: {
            "ASSERT_D95CE7BB838C09FA": ("vehicle_recommendation", "CAR"),
            "ASSERT_FC5122E81AB8BC47": ("delivery_fee_vnd", 10_000),
        },
        6001: {
            "ASSERT_E75BB6E15EBBF7CE": ("delivery_fee_vnd", None),
            "ASSERT_AC241907F7EC56A2": ("fee_status", "REQUIRES_HUMAN"),
            "ASSERT_3796949284505FE1": ("fee_requires_human_reason", True),
        },
    }
    assertions = expected.get(distance)
    if assertions is None:
        raise ValueError("delivery fixture has no normative boundary assertions")
    arguments: dict[str, Any] = {"fulfillment_mode": "PICKUP_AND_RETURN"}
    trace_id = _event_id(fixture)
    return SyntheticDeliveryPreflight(
        tool_trace=(
            {
                "sequence": 1,
                "operation_id": "deliveryEvaluate",
                "argument_field_names": ["fulfillment_mode"],
                "arguments_sha256": f"sha256:{sha256(rfc8785.dumps(arguments)).hexdigest()}",
                "status_code": 200,
                "trace_id": trace_id,
            },
        ),
        assertion_results={
            key: facts.get(field) == value for key, (field, value) in assertions.items()
        },
        trace_id=trace_id,
    )


def _event_id(fixture: SyntheticFixtureBundle) -> str:
    events = fixture.payload.get("provider_events")
    if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], Mapping):
        raise ValueError("delivery fixture must contain one event")
    value = events[0].get("event_id")
    if not isinstance(value, str) or not value:
        raise ValueError("delivery fixture event id is invalid")
    return value


__all__ = ["SyntheticDeliveryPreflight", "execute_delivery_preflight"]
