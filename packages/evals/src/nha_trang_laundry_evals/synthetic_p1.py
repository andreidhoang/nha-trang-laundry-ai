"""Deterministic P1 list-price and server-bound intake preflights."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import rfc8785
from nha_trang_laundry_db.intake import CreateOrderRequestCommand, OrderRequestRepository
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv, runtime_price_rules
from nha_trang_laundry_worker.response_templates import render_published_list_price

from .fixtures import SyntheticFixtureBundle

ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True, slots=True)
class SyntheticP1Preflight:
    assertion_results: Mapping[str, bool]
    trace_id: str
    tool_trace: tuple[Mapping[str, object], ...]
    side_effects: tuple[str, ...]


def execute_list_price_preflight(fixture: SyntheticFixtureBundle) -> SyntheticP1Preflight:
    seed = _seed(fixture)
    service_code = _text(seed, "service_code")
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    rule = runtime_price_rules(pricebook)[service_code]
    published_rules = tuple(
        {
            "minimum_quantity": str(tier.minimum_quantity),
            "maximum_quantity_exclusive": (
                str(tier.maximum_quantity_exclusive)
                if tier.maximum_quantity_exclusive is not None
                else None
            ),
            "unit_price_vnd": tier.unit_price_vnd,
        }
        for tier in rule.tiers
    )
    draft = render_published_list_price(
        service_code=service_code,
        unit=rule.unit.value,
        published_price_rules=published_rules,
    )
    trace_id = _event_id(fixture)
    return SyntheticP1Preflight(
        {
            "ASSERT_B6C08D0DEF76A2CB": draft["response_shape"] == "PUBLISHED_LIST_PRICE",
            "ASSERT_07F3240B5647192E": (
                draft["estimate_disclosure"] == "LIST_PRICE_ONLY_NOT_PERSONALIZED_TOTAL"
            ),
            "ASSERT_8FC969DA64FB1757": (
                "personalized_subtotal_vnd" not in draft and "personalized_total_vnd" not in draft
            ),
        },
        trace_id,
        (
            _tool_trace(1, "catalogResolve", {"service_text": "standard wash"}, trace_id),
            _tool_trace(
                2,
                "messageDraftCreate",
                {"kind": "LIST_PRICE_INFO", "fact_refs": ["PUBLISHED_PRICEBOOK"]},
                trace_id,
            ),
        ),
        ("MESSAGE_DRAFT_CREATED",),
    )


def execute_bound_intake_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticP1Preflight:
    context = fixture.payload.get("authenticated_context")
    if not isinstance(context, Mapping):
        raise ValueError("intake authenticated context is invalid")
    store_id = UUID(_text(context, "store_id"))
    contact_id = UUID(_text(context, "contact_binding_id"))
    conversation_id = UUID(_text(context, "conversation_binding_id"))
    timestamp = _clock(fixture.payload)
    stored = OrderRequestRepository().create(
        connection,
        CreateOrderRequestCommand(
            store_id, contact_id, conversation_id, uuid4(), uuid4(), timestamp
        ),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT store_id, contact_binding_id, conversation_binding_id
            FROM order_requests WHERE id = %s
            """,
            (stored.order_request_id,),
        )
        row = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM orders WHERE store_id = %s", (store_id,))
        commercial_orders = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT
                (SELECT count(*) FROM domain_events
                 WHERE aggregate_type = 'ORDER_REQUEST' AND aggregate_id = %s),
                (SELECT count(*) FROM audit_events
                 WHERE aggregate_type = 'ORDER_REQUEST' AND aggregate_id = %s)
            """,
            (stored.order_request_id, stored.order_request_id),
        )
        evidence = cursor.fetchone()
    trace_id = _event_id(fixture)
    arguments = {"initial_facts": [{"fact_type": "SERVICE_TEXT"}]}
    return SyntheticP1Preflight(
        {
            "ASSERT_635D3AF4C8D41C73": row == (store_id, contact_id, conversation_id),
            "ASSERT_1B5BC58C471C270B": commercial_orders == 0,
            "ASSERT_CC263977DEB8FC77": evidence == (1, 1),
        },
        trace_id,
        (_tool_trace(1, "orderRequestCreate", arguments, trace_id),),
        ("ORDER_REQUEST_DRAFT_CREATED",),
    )


def _tool_trace(
    sequence: int, operation_id: str, arguments: dict[str, Any], trace_id: str
) -> Mapping[str, object]:
    return {
        "sequence": sequence,
        "operation_id": operation_id,
        "argument_field_names": sorted(arguments),
        "arguments_sha256": f"sha256:{sha256(rfc8785.dumps(arguments)).hexdigest()}",
        "status_code": 200,
        "trace_id": trace_id,
    }


def _seed(fixture: SyntheticFixtureBundle) -> Mapping[str, Any]:
    value = fixture.payload.get("database_seed")
    if not isinstance(value, Mapping):
        raise ValueError("P1 fixture seed is invalid")
    return value


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"P1 fixture {key} is invalid")
    return result


def _clock(payload: Mapping[str, Any]) -> datetime:
    result = datetime.fromisoformat(_text(payload, "clock").replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("P1 fixture clock is invalid")
    return result


def _event_id(fixture: SyntheticFixtureBundle) -> str:
    events = fixture.payload.get("provider_events")
    if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], Mapping):
        raise ValueError("P1 fixture must contain one event")
    return _text(events[0], "event_id")


__all__ = ["SyntheticP1Preflight", "execute_bound_intake_preflight", "execute_list_price_preflight"]
