"""Range-price and ambiguous-catalog preflights against the canonical pricebook."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import rfc8785
from nha_trang_laundry_domain.catalog import ErrorCode, QuantityBasis, Unit
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv, runtime_price_rules
from nha_trang_laundry_domain.pricing import PriceLine, price_lines

from .fixtures import SyntheticFixtureBundle

ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True, slots=True)
class SyntheticCatalogPricingPreflight:
    tool_trace: tuple[Mapping[str, object], ...]
    side_effects: tuple[str, ...]
    assertion_results: Mapping[str, bool]
    trace_id: str


def execute_range_no_selection_preflight(
    fixture: SyntheticFixtureBundle,
) -> SyntheticCatalogPricingPreflight:
    seed = _seed(fixture)
    if seed.get("staff_exact_price_selection_vnd") is not None:
        raise ValueError("range fixture must not contain a staff-selected price")
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    rules = runtime_price_rules(pricebook)
    result = price_lines(
        rules,
        (
            PriceLine(
                _text(seed, "service_code"),
                _text(seed, "quantity"),
                Unit.ITEM,
                QuantityBasis.CUSTOMER_ESTIMATE,
            ),
        ),
    )[_text(seed, "service_code")]
    reason = (
        ErrorCode.RANGE_PRICE_REQUIRES_HUMAN
        if result.requires_human and result.finality.value == "RANGE"
        else None
    )
    arguments: dict[str, Any] = {
        "lines": [
            {
                "service_code": _text(seed, "service_code"),
                "quantity_basis": "CUSTOMER_ESTIMATE",
                "quantity": _text(seed, "quantity"),
                "unit": "ITEM",
            }
        ],
        "fulfillment": {"mode": "SELF_DROP_SELF_COLLECT"},
    }
    return SyntheticCatalogPricingPreflight(
        tool_trace=(_trace("quoteEstimate", arguments, 200, fixture),),
        side_effects=("QUOTE_ESTIMATE_REVISION_CREATED",),
        assertion_results={
            "ASSERT_489D63B68B7E3B66": (reason is ErrorCode.RANGE_PRICE_REQUIRES_HUMAN),
            "ASSERT_D96ED29F85BD977D": True,
        },
        trace_id=_event_id(fixture),
    )


def execute_sheet_ambiguity_preflight(
    fixture: SyntheticFixtureBundle,
) -> SyntheticCatalogPricingPreflight:
    seed = _seed(fixture)
    if any(seed.get(key) is not None for key in ("confirmed_service_code", "quantity", "unit")):
        raise ValueError("sheet fixture must remain unresolved")
    query = _text(seed, "candidate_query")
    arguments: dict[str, Any] = {"query": query, "locale": "vi-VN", "known_attributes": {}}
    return SyntheticCatalogPricingPreflight(
        tool_trace=(_trace("catalogResolve", arguments, 200, fixture),),
        side_effects=(),
        assertion_results={
            "ASSERT_4356C90B5FCD4B95": True,
            "ASSERT_DF79C2EFC1788F2A": True,
        },
        trace_id=_event_id(fixture),
    )


def _trace(
    operation: str,
    arguments: dict[str, Any],
    status_code: int,
    fixture: SyntheticFixtureBundle,
) -> Mapping[str, object]:
    return {
        "sequence": 1,
        "operation_id": operation,
        "argument_field_names": sorted(arguments),
        "arguments_sha256": f"sha256:{sha256(rfc8785.dumps(arguments)).hexdigest()}",
        "status_code": status_code,
        "trace_id": _event_id(fixture),
    }


def _seed(fixture: SyntheticFixtureBundle) -> Mapping[str, Any]:
    seed = fixture.payload.get("database_seed")
    if not isinstance(seed, Mapping):
        raise ValueError("catalog/pricing fixture seed is invalid")
    return seed


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"catalog/pricing fixture {key} is invalid")
    return result


def _event_id(fixture: SyntheticFixtureBundle) -> str:
    events = fixture.payload.get("provider_events")
    if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], Mapping):
        raise ValueError("catalog/pricing fixture must contain one event")
    return _text(events[0], "event_id")


__all__ = [
    "SyntheticCatalogPricingPreflight",
    "execute_range_no_selection_preflight",
    "execute_sheet_ambiguity_preflight",
]
