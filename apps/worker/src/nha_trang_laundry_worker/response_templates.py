"""Deterministic fail-closed response shapes; these are drafts, never sends."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class GenericUnavailableDraft(TypedDict):
    response_shape: Literal["GENERIC_UNAVAILABLE"]
    message_kind: Literal["ORDER_STATUS"]
    disposition: Literal["REQUIRE_HUMAN"]


class ListPriceDraft(TypedDict):
    response_shape: Literal["PUBLISHED_LIST_PRICE"]
    message_kind: Literal["LIST_PRICE_INFO"]
    service_code: str
    unit: str
    published_price_rules: tuple[dict[str, int | str | None], ...]
    estimate_disclosure: Literal["LIST_PRICE_ONLY_NOT_PERSONALIZED_TOTAL"]
    personalized_subtotal_vnd: NotRequired[int]
    personalized_total_vnd: NotRequired[int]


def render_generic_order_status_unavailable() -> GenericUnavailableDraft:
    """Return an ownership-neutral shape for missing, forbidden, or unavailable status data."""

    return {
        "response_shape": "GENERIC_UNAVAILABLE",
        "message_kind": "ORDER_STATUS",
        "disposition": "REQUIRE_HUMAN",
    }


def render_published_list_price(
    *, service_code: str, unit: str, published_price_rules: tuple[dict[str, int | str | None], ...]
) -> ListPriceDraft:
    """Render only published rule facts, never a customer-specific calculation."""

    if not service_code or not unit or not published_price_rules:
        raise ValueError("published list-price facts are incomplete")
    return {
        "response_shape": "PUBLISHED_LIST_PRICE",
        "message_kind": "LIST_PRICE_INFO",
        "service_code": service_code,
        "unit": unit,
        "published_price_rules": published_price_rules,
        "estimate_disclosure": "LIST_PRICE_ONLY_NOT_PERSONALIZED_TOTAL",
    }


__all__ = [
    "GenericUnavailableDraft",
    "ListPriceDraft",
    "render_generic_order_status_unavailable",
    "render_published_list_price",
]
