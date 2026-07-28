from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import FulfillmentMode, PolicyOutcome
from nha_trang_laundry_domain.delivery import (
    DeliveryError,
    DeliveryFeeResolution,
    DeliveryReason,
    DeliveryVehicle,
    evaluate_delivery,
)


@pytest.mark.parametrize(
    ("distance_m", "expected_fee", "expected_zone"),
    [
        (2_000, 0, "LE_2_KM"),
        (2_001, 10_000, "GT_2_LE_6_KM"),
        (6_000, 10_000, "GT_2_LE_6_KM"),
    ],
)
def test_confirmed_delivery_distance_boundaries(
    distance_m: int, expected_fee: int, expected_zone: str
) -> None:
    result = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=distance_m,
        planned_transport_weight_kg="5",
    )

    assert result.delivery_fee_vnd == expected_fee
    assert result.fee_resolution is DeliveryFeeResolution.AUTO_FIXED
    assert result.fee_outcome is PolicyOutcome.ALLOW
    assert result.trace.distance_zone == expected_zone
    assert result.trace.dispatch_authorized is False
    assert result.slot_outcome is PolicyOutcome.REQUIRE_HUMAN


def test_six_km_plus_one_meter_requires_human_fee_and_acknowledgement() -> None:
    unresolved = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=6_001,
        planned_transport_weight_kg="5",
    )
    not_acknowledged = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=6_001,
        planned_transport_weight_kg="5",
        approved_manual_fee_vnd=25_000,
    )
    approved = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=6_001,
        planned_transport_weight_kg="5",
        approved_manual_fee_vnd=25_000,
        customer_acknowledged_manual_fee=True,
    )

    assert unresolved.delivery_fee_vnd is None
    assert unresolved.fee_outcome is PolicyOutcome.REQUIRE_HUMAN
    assert not_acknowledged.delivery_fee_vnd is None
    assert approved.delivery_fee_vnd == 25_000
    assert approved.fee_resolution is DeliveryFeeResolution.HUMAN_APPROVED
    assert approved.fee_outcome is PolicyOutcome.ALLOW
    assert approved.overall_outcome is PolicyOutcome.REQUIRE_HUMAN


def test_self_drop_self_collect_has_no_delivery_job_or_slot_gate() -> None:
    result = evaluate_delivery(
        FulfillmentMode.SELF_DROP_SELF_COLLECT,
        verified_distance_m=None,
        planned_transport_weight_kg=None,
    )

    assert result.delivery_fee_vnd == 0
    assert result.delivery_job_required is False
    assert result.vehicle_recommendation is None
    assert result.overall_outcome is PolicyOutcome.ALLOW
    assert result.reason_codes == (DeliveryReason.SELF_SERVICE_NO_DELIVERY_JOB,)


def test_one_leg_pricing_stays_human_until_approved_fee_and_ack_are_supplied() -> None:
    unresolved = evaluate_delivery(
        FulfillmentMode.PICKUP_ONLY,
        verified_distance_m=1_000,
        planned_transport_weight_kg="3",
    )
    approved = evaluate_delivery(
        FulfillmentMode.PICKUP_ONLY,
        verified_distance_m=1_000,
        planned_transport_weight_kg="3",
        approved_manual_fee_vnd=7_000,
        customer_acknowledged_manual_fee=True,
    )

    assert unresolved.delivery_fee_vnd is None
    assert unresolved.fee_outcome is PolicyOutcome.REQUIRE_HUMAN
    assert DeliveryReason.ONE_LEG_PRICE_UNRESOLVED in unresolved.reason_codes
    assert approved.delivery_fee_vnd == 7_000
    assert approved.fee_outcome is PolicyOutcome.ALLOW


def test_unverified_distance_never_uses_a_zone_fee() -> None:
    result = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=None,
        planned_transport_weight_kg="3",
    )

    assert result.delivery_fee_vnd is None
    assert result.trace.distance_zone is None
    assert DeliveryReason.DELIVERY_DISTANCE_UNVERIFIED in result.reason_codes
    assert result.fee_outcome is PolicyOutcome.REQUIRE_HUMAN


@pytest.mark.parametrize(
    ("weight", "expected"),
    [("19.999", DeliveryVehicle.MOTORCYCLE), ("20.000", DeliveryVehicle.CAR)],
)
def test_vehicle_recommendation_uses_exact_twenty_kg_boundary(
    weight: str, expected: DeliveryVehicle
) -> None:
    result = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=2_000,
        planned_transport_weight_kg=weight,
    )

    assert result.vehicle_recommendation is expected
    assert result.vehicle_outcome is PolicyOutcome.ALLOW
    assert result.trace.vehicle_threshold_kg == "20"


def test_missing_weight_does_not_block_known_fee_but_requires_vehicle_handoff() -> None:
    result = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=2_001,
        planned_transport_weight_kg=None,
    )

    assert result.delivery_fee_vnd == 10_000
    assert result.fee_outcome is PolicyOutcome.ALLOW
    assert result.vehicle_recommendation is None
    assert result.vehicle_outcome is PolicyOutcome.REQUIRE_HUMAN


@pytest.mark.parametrize("weight", ["0", "-1", "20.0000", "2e1", " 20", "unknown"])
def test_invalid_or_noncanonical_weights_fail_closed(weight: str) -> None:
    with pytest.raises(DeliveryError, match="MISSING_REQUIRED_FACT"):
        evaluate_delivery(
            FulfillmentMode.PICKUP_AND_RETURN,
            verified_distance_m=2_000,
            planned_transport_weight_kg=weight,
        )


def test_invalid_distance_and_fixed_zone_manual_override_are_rejected() -> None:
    for distance in (-1, cast(int, 2_000.5), cast(int, True)):
        with pytest.raises(DeliveryError, match="VALIDATION_ERROR"):
            evaluate_delivery(
                FulfillmentMode.PICKUP_AND_RETURN,
                verified_distance_m=distance,
                planned_transport_weight_kg="5",
            )

    with pytest.raises(DeliveryError, match="VALIDATION_ERROR"):
        evaluate_delivery(
            FulfillmentMode.PICKUP_AND_RETURN,
            verified_distance_m=2_000,
            planned_transport_weight_kg="5",
            approved_manual_fee_vnd=1,
            customer_acknowledged_manual_fee=True,
        )

    with pytest.raises(DeliveryError, match="MISSING_REQUIRED_FACT"):
        evaluate_delivery(
            FulfillmentMode.SELF_DROP_SELF_COLLECT,
            verified_distance_m=None,
            planned_transport_weight_kg="not-a-weight",
        )


@given(st.integers(min_value=0, max_value=50_000))
def test_distance_property_matches_confirmed_zone_function(distance_m: int) -> None:
    result = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=distance_m,
        planned_transport_weight_kg="1",
    )

    if distance_m <= 2_000:
        assert result.delivery_fee_vnd == 0
        assert result.fee_outcome is PolicyOutcome.ALLOW
    elif distance_m <= 6_000:
        assert result.delivery_fee_vnd == 10_000
        assert result.fee_outcome is PolicyOutcome.ALLOW
    else:
        assert result.delivery_fee_vnd is None
        assert result.fee_outcome is PolicyOutcome.REQUIRE_HUMAN


@given(st.integers(min_value=1, max_value=100_000))
def test_vehicle_property_matches_twenty_kg_threshold(weight_thousandths: int) -> None:
    weight = f"{weight_thousandths // 1000}.{weight_thousandths % 1000:03d}"
    result = evaluate_delivery(
        FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=1_000,
        planned_transport_weight_kg=weight,
    )

    expected = DeliveryVehicle.MOTORCYCLE if weight_thousandths < 20_000 else DeliveryVehicle.CAR
    assert result.vehicle_recommendation is expected
