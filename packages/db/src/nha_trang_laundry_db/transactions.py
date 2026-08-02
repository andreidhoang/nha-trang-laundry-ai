"""Atomic material-change envelope: mutation, domain event, audit, and outbox."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from opentelemetry import propagate

JsonObject = Mapping[str, Any]
Mutation = Callable[[Any], None]


@dataclass(frozen=True)
class OutboxEvent:
    """A required downstream event with a stable, caller-owned idempotency key."""

    event_type: str
    payload: JsonObject
    idempotency_key: str


@dataclass(frozen=True)
class MaterialChange:
    """Metadata that must accompany every material domain mutation."""

    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    event_type: str
    event_payload: JsonObject
    audit_action: str
    actor_type: str
    actor_id: UUID | None
    correlation_id: UUID
    outbox_events: Sequence[OutboxEvent]
    occurred_at: datetime | None = None
    audit_details: JsonObject | None = None


def commit_material_change(connection: Any, change: MaterialChange, mutate: Mutation) -> None:
    """Commit a mutation and all required ledger records or roll back everything.

    `mutate` receives the same database cursor used for event, audit, and outbox writes. It must not
    commit independently. An empty outbox set is rejected because callers using this primitive have
    declared a material command that requires downstream processing.
    """
    if change.aggregate_version < 1:
        raise ValueError("aggregate_version must be at least 1")
    if not change.outbox_events:
        raise ValueError("a material change requires at least one outbox event")

    occurred_at = change.occurred_at or datetime.now(UTC)
    trace_carrier: dict[str, str] = {}
    propagate.inject(trace_carrier)
    traceparent = trace_carrier.get("traceparent")
    tracestate = trace_carrier.get("tracestate")
    with connection.transaction(), connection.cursor() as cursor:
        mutate(cursor)
        cursor.execute(
            """
                INSERT INTO domain_events (
                    id, aggregate_type, aggregate_id, aggregate_version, event_type, payload,
                    correlation_id, occurred_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
            (
                uuid4(),
                change.aggregate_type,
                change.aggregate_id,
                change.aggregate_version,
                change.event_type,
                json.dumps(change.event_payload, sort_keys=True),
                change.correlation_id,
                occurred_at,
            ),
        )
        cursor.execute(
            """
                INSERT INTO audit_events (
                    id, aggregate_type, aggregate_id, action, actor_type, actor_id, correlation_id,
                    details, occurred_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
            (
                uuid4(),
                change.aggregate_type,
                change.aggregate_id,
                change.audit_action,
                change.actor_type,
                change.actor_id,
                change.correlation_id,
                json.dumps(
                    {"event_type": change.event_type, **(change.audit_details or {})},
                    sort_keys=True,
                ),
                occurred_at,
            ),
        )
        for outbox_event in change.outbox_events:
            cursor.execute(
                """
                    INSERT INTO outbox_events (
                        id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                        correlation_id, occurred_at, traceparent, tracestate
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                    """,
                (
                    uuid4(),
                    change.aggregate_type,
                    change.aggregate_id,
                    outbox_event.event_type,
                    json.dumps(outbox_event.payload, sort_keys=True),
                    outbox_event.idempotency_key,
                    change.correlation_id,
                    occurred_at,
                    traceparent,
                    tracestate,
                ),
            )
