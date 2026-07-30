"""P0 in-flight kill-switch preflight with no provider execution path."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from nha_trang_laundry_db.automation import AutomationExecutionRepository

from .fixtures import SyntheticFixtureBundle


@dataclass(frozen=True, slots=True)
class SyntheticKillSwitchPreflight:
    automated_envelope_held: bool
    human_operation_available: bool
    disabled_capability_not_overridden: bool
    trace_id: str


@dataclass(frozen=True, slots=True)
class SyntheticStaleFlagStorePreflight:
    automation_defaults_off: bool
    provider_attempted: bool
    trace_id: str


def execute_kill_switch_inflight_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticKillSwitchPreflight:
    payload = fixture.payload
    seed = _mapping(payload, "database_seed")
    envelope = _mapping(seed, "automated_envelope")
    fault = _mapping(payload, "fault_injection")
    capability = f"{_text(envelope, 'capability')}_{uuid4().hex}"
    if envelope.get("status") != "PENDING" or envelope.get("hold_policy") != "HOLD":
        raise ValueError("kill-switch fixture envelope is invalid")
    if (
        fault.get("global_automation_enabled") is not True
        or fault.get("capability_enabled") is not False
    ):
        raise ValueError("kill-switch fixture must prove disabled capability precedence")
    timestamp = _clock(payload)
    envelope_id = uuid4()
    outbox_event_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                correlation_id, occurred_at
            ) VALUES (%s, 'MESSAGE', %s, 'message.send_requested.v1', '{}'::jsonb, %s, %s, %s)
            """,
            (outbox_event_id, envelope_id, f"synthetic-kill:{envelope_id}", uuid4(), timestamp),
        )
        cursor.execute(
            """
            INSERT INTO automated_execution_envelopes (
                id, capability, outbox_event_id, status, hold_policy, created_at
            ) VALUES (%s, %s, %s, 'PENDING', 'HOLD', %s)
            """,
            (envelope_id, capability, outbox_event_id, timestamp),
        )
        cursor.execute(
            """
            INSERT INTO automation_execution_gates (
                capability, global_automation_enabled, agent_processing_enabled,
                agent_outbound_enabled, channel_ingress_enabled, capability_enabled,
                stage_policy_allows, pdp_allows, version, expires_at, updated_at
            ) VALUES (%s, TRUE, TRUE, FALSE, TRUE, FALSE, TRUE, TRUE, 1, %s, %s)
            """,
            (capability, timestamp + timedelta(minutes=1), timestamp),
        )
    status = AutomationExecutionRepository().hold_if_disabled(
        connection,
        envelope_id=envelope_id,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        now=timestamp,
    )
    if status != "HELD":
        raise ValueError("disabled automated envelope was not held")
    return SyntheticKillSwitchPreflight(True, True, True, "synthetic-kill-switch-inflight-001")


def execute_stale_flag_store_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticStaleFlagStorePreflight:
    payload = fixture.payload
    seed = _mapping(payload, "database_seed")
    envelope = _mapping(seed, "automated_envelope")
    fault = _mapping(payload, "fault_injection")
    if fault.get("feature_flag_store_available") is not False:
        raise ValueError("stale-flag fixture must make the flag store unavailable")
    timestamp = _clock(payload)
    envelope_id = uuid4()
    outbox_event_id = uuid4()
    capability = f"{_text(envelope, 'capability')}_{uuid4().hex}"
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                correlation_id, occurred_at
            ) VALUES (%s, 'MESSAGE', %s, 'message.send_requested.v1', '{}'::jsonb, %s, %s, %s)
            """,
            (outbox_event_id, envelope_id, f"synthetic-stale:{envelope_id}", uuid4(), timestamp),
        )
        cursor.execute(
            """
            INSERT INTO automated_execution_envelopes (
                id, capability, outbox_event_id, status, hold_policy, created_at
            ) VALUES (%s, %s, %s, 'PENDING', 'HOLD', %s)
            """,
            (envelope_id, capability, outbox_event_id, timestamp),
        )
        # No gate row is inserted: unavailable state must be indistinguishable from disabled state.
    status = AutomationExecutionRepository().hold_if_disabled(
        connection,
        envelope_id=envelope_id,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        now=timestamp,
    )
    return SyntheticStaleFlagStorePreflight(
        automation_defaults_off=status == "HELD",
        provider_attempted=False,
        trace_id="synthetic-stale-flag-store-001",
    )


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    candidate = value.get(key)
    if not isinstance(candidate, Mapping):
        raise ValueError(f"kill-switch fixture {key} is invalid")
    return candidate


def _text(value: Mapping[str, Any], key: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate:
        raise ValueError(f"kill-switch fixture {key} is invalid")
    return candidate


def _clock(payload: Mapping[str, Any]) -> datetime:
    value = _text(payload, "clock")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("kill-switch fixture clock is invalid")
    return timestamp.astimezone(UTC)
