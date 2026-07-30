"""Deterministic pricing/promotion preflight using the approved pricebook source."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import rfc8785
from nha_trang_laundry_domain.catalog import PromotionResolution, QuantityBasis, Unit
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv, runtime_price_rules
from nha_trang_laundry_domain.pricing import PriceLine, price_lines
from nha_trang_laundry_domain.promotion import (
    CURRENT_PROMOTION,
    PromotionLine,
    PromotionReason,
    evaluate_promotion,
)

from .fixtures import SyntheticFixtureBundle

ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True, slots=True)
class SyntheticPricingPreflight:
    tool_trace: tuple[Mapping[str, object], ...]
    side_effects: tuple[str, ...]
    assertion_results: Mapping[str, bool]
    trace_id: str


PricingScenario = Literal["STANDARD", "PROMO_EXPIRED", "PROMO_UNRESOLVED"]

EXPECTED_ASSERTIONS: Mapping[str, Mapping[str, tuple[str, object]]] = {
    "STANDARD:5.9": {
        "ASSERT_103C92043CC1F076": ("list_service_subtotal_vnd", 147_500),
        "ASSERT_B73187E62D43BB8B": ("discount_amount_vnd", 44_250),
        "ASSERT_B3BF81C65A524038": ("display_total_vnd", 103_250),
        "ASSERT_103C6F33CB68166F": ("finality", "ESTIMATE"),
        "ASSERT_550C92EA0CD57A6C": ("customer_final_quote_accepted_at", None),
    },
    "STANDARD:6": {
        "ASSERT_7DB13EDB190AE97D": ("list_service_subtotal_vnd", 120_000),
        "ASSERT_1364154901F8CB6D": ("discount_amount_vnd", 36_000),
        "ASSERT_B5A9A7B225726F62": ("display_total_vnd", 84_000),
    },
    "STANDARD:0.6": {
        "ASSERT_AEE3096B0F757ED1": ("pricing_quantity_kg", "0.6"),
        "ASSERT_8C820C34A5FDA599": ("billable_quantity_kg", "1"),
        "ASSERT_1754ECA0D3447628": ("list_service_subtotal_vnd", 25_000),
        "ASSERT_0EC6B1E02B292E44": ("display_total_vnd", 17_500),
    },
    "PROMO_EXPIRED:6": {
        "ASSERT_7DB13EDB190AE97D": ("list_service_subtotal_vnd", 120_000),
        "ASSERT_76FA56700F61D1E9": ("discount_amount_vnd", 0),
        "ASSERT_B029035C2082A2A8": ("display_total_vnd", 120_000),
    },
    "PROMO_UNRESOLVED:6": {
        "ASSERT_D61B2DC6692A427B": ("promotion_status", "PROVISIONAL"),
        "ASSERT_46739EB70578435E": ("promotion_eligibility_event", None),
        "ASSERT_5DAD914F801836EB": ("promotion_eligibility_at", None),
        "ASSERT_DE0F80E7D02DA98D": ("promotion_eligibility_approval_required", True),
    },
}


def execute_pricing_preflight(
    fixture: SyntheticFixtureBundle,
    *,
    scenario: PricingScenario = "STANDARD",
    evaluation_at: datetime | None = None,
) -> SyntheticPricingPreflight:
    seed = fixture.payload.get("database_seed")
    if not isinstance(seed, Mapping):
        raise ValueError("pricing fixture database seed is invalid")
    service_code = _text(seed, "service_code")
    quantity = _text(seed, "quantity")
    if seed.get("quantity_basis") != "CUSTOMER_ESTIMATE" or seed.get("unit") != "KG":
        raise ValueError("pricing fixture must contain a customer KG estimate")
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    rules = runtime_price_rules(pricebook)
    priced = price_lines(
        rules,
        (PriceLine(service_code, quantity, Unit.KG, QuantityBasis.CUSTOMER_ESTIMATE),),
    )[service_code]
    if priced.list_amount_vnd is None:
        raise ValueError("pricing boundary did not produce an exact estimate amount")
    promotion = evaluate_promotion(
        CURRENT_PROMOTION,
        (
            PromotionLine(
                "synthetic-standard-wash",
                priced.list_amount_vnd,
                PromotionResolution.AUTO_IF_TARGETED,
                rate_bps=3_000,
            ),
        ),
        evaluation_at=evaluation_at or _clock(fixture.payload),
    )
    facts: dict[str, object] = {
        "pricing_quantity_kg": priced.trace.aggregate_quantity,
        "billable_quantity_kg": priced.trace.billable_quantity,
        "list_service_subtotal_vnd": promotion.list_service_subtotal_vnd,
        "discount_amount_vnd": promotion.discount_amount_vnd,
        "display_total_vnd": promotion.display_total_vnd,
        "finality": priced.finality.value,
        "customer_final_quote_accepted_at": None,
        "promotion_status": promotion.status.value,
        "promotion_eligibility_event": (
            promotion.eligibility_event.value if promotion.eligibility_event is not None else None
        ),
        "promotion_eligibility_at": promotion.eligibility_at,
        "promotion_eligibility_approval_required": (
            PromotionReason.PROMOTION_ELIGIBILITY_UNRESOLVED in promotion.reason_codes
        ),
    }
    expected = EXPECTED_ASSERTIONS.get(f"{scenario}:{quantity}")
    if expected is None:
        raise ValueError("pricing fixture quantity has no normative assertions")
    arguments: dict[str, Any] = {
        "lines": [
            {
                "service_code": service_code,
                "quantity_basis": "CUSTOMER_ESTIMATE",
                "quantity": quantity,
                "unit": "KG",
            }
        ],
        "fulfillment": {"mode": "SELF_DROP_SELF_COLLECT"},
    }
    return SyntheticPricingPreflight(
        tool_trace=(
            {
                "sequence": 1,
                "operation_id": "quoteEstimate",
                "argument_field_names": ["fulfillment", "lines"],
                "arguments_sha256": f"sha256:{sha256(rfc8785.dumps(arguments)).hexdigest()}",
                "status_code": 200,
                "trace_id": _event_id(fixture.payload),
            },
        ),
        side_effects=("QUOTE_ESTIMATE_REVISION_CREATED",),
        assertion_results={
            assertion_id: facts.get(field) == value
            for assertion_id, (field, value) in expected.items()
        },
        trace_id=_event_id(fixture.payload),
    )


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"pricing fixture {key} is invalid")
    return result


def _clock(payload: Mapping[str, Any]) -> datetime:
    value = _text(payload, "clock")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("pricing fixture clock must be timezone-aware")
    return result


def _event_id(payload: Mapping[str, Any]) -> str:
    events = payload.get("provider_events")
    if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], Mapping):
        raise ValueError("pricing fixture must contain one synthetic event")
    return _text(events[0], "event_id")


__all__ = ["PricingScenario", "SyntheticPricingPreflight", "execute_pricing_preflight"]
