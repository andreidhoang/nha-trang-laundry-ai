"""Deterministic capacity advisory preflight for the zero-authority R1 policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

import rfc8785
from nha_trang_laundry_domain.capacity import evaluate_r1_capacity

from .fixtures import SyntheticFixtureBundle


@dataclass(frozen=True, slots=True)
class SyntheticCapacityPreflight:
    tool_trace: tuple[Mapping[str, object], ...]
    assertion_results: Mapping[str, bool]
    trace_id: str


def execute_capacity_preflight(fixture: SyntheticFixtureBundle) -> SyntheticCapacityPreflight:
    seed = fixture.payload.get("database_seed")
    if not isinstance(seed, Mapping):
        raise ValueError("capacity fixture seed is invalid")
    requested = seed.get("requested_ready_at")
    if not isinstance(requested, str):
        raise ValueError("capacity requested ready time is invalid")
    requested_at = datetime.fromisoformat(requested.replace("Z", "+00:00"))
    result = evaluate_r1_capacity(requested_ready_at=requested_at)
    arguments: dict[str, Any] = {"requested_ready_at": requested}
    trace_id = _event_id(fixture)
    return SyntheticCapacityPreflight(
        tool_trace=(
            {
                "sequence": 1,
                "operation_id": "capacityCheck",
                "argument_field_names": ["requested_ready_at"],
                "arguments_sha256": f"sha256:{sha256(rfc8785.dumps(arguments)).hexdigest()}",
                "status_code": 200,
                "trace_id": trace_id,
            },
        ),
        assertion_results={
            "ASSERT_2C4A08E08C610B39": result.slot_confirmed is False,
            "ASSERT_764C64CB95D1452D": result.required_approval == "SLOT_CONFIRMATION",
            "ASSERT_08EB2F12ED5A9D6E": result.promised_ready_at_store is None,
        },
        trace_id=trace_id,
    )


def _event_id(fixture: SyntheticFixtureBundle) -> str:
    events = fixture.payload.get("provider_events")
    if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], Mapping):
        raise ValueError("capacity fixture must contain one event")
    value = events[0].get("event_id")
    if not isinstance(value, str) or not value:
        raise ValueError("capacity fixture event id is invalid")
    return value


__all__ = ["SyntheticCapacityPreflight", "execute_capacity_preflight"]
