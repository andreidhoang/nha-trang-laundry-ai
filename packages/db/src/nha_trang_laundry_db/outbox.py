"""Constrained internal outbox claiming, retry, and dead-letter primitives."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import ActorRole

INTERNAL_EVENT_TYPES: Final = frozenset(
    {
        "approval.requested.v1",
        "agent.run_completed.v1",
        "agent.run_requested.v1",
        "agent.tool_called.v1",
        "approval.decided.v1",
        "approval.execution_claimed.v1",
        "approval.expired.v1",
        "configuration.draft_created.v1",
        "configuration.published.v1",
        "consent.changed.v1",
        "inbox.dispatch_requested.v1",
        "message.manual_send_attested.v1",
        "message.manual_send_prepared.v1",
        "order.requested.v1",
        "order.state_transitioned.v1",
        "quote.revision_created.v1",
        "security.inbox_replay_conflict.v1",
        "staff.identity_changed.v1",
    }
)
RETRY_DELAYS: Final = MappingProxyType(
    {
        1: timedelta(seconds=30),
        2: timedelta(minutes=2),
        3: timedelta(minutes=10),
        4: timedelta(hours=1),
        5: timedelta(hours=6),
    }
)


class OutboxAuthorizationError(PermissionError):
    """Raised when a non-worker identity attempts queue execution."""


class OutboxStateError(ValueError):
    """Raised for stale claims, invalid retry classes, or corrupt payloads."""


@dataclass(frozen=True)
class ClaimedOutboxEvent:
    event_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, object]
    idempotency_key: str
    correlation_id: UUID
    attempt_count: int


class OutboxRepository:
    """Claim only allowlisted internal events; provider outbound is a separate gated worker."""

    def claim_next_internal(
        self,
        connection: Any,
        *,
        worker_role: ActorRole,
        now: datetime | None = None,
    ) -> ClaimedOutboxEvent | None:
        _require_worker(worker_role)
        claimed_at = now or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT id
                    FROM outbox_events
                    WHERE status = 'PENDING' AND available_at <= %s
                        AND event_type = ANY(%s)
                    ORDER BY available_at, occurred_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE outbox_events AS event
                SET status = 'PROCESSING', attempt_count = attempt_count + 1,
                    held_reason = NULL
                FROM candidate
                WHERE event.id = candidate.id
                RETURNING event.id, event.aggregate_type, event.aggregate_id,
                          event.event_type, event.payload, event.idempotency_key,
                          event.correlation_id, event.attempt_count
                """,
                (claimed_at, list(sorted(INTERNAL_EVENT_TYPES))),
            )
            row = cursor.fetchone()
        return None if row is None else _claimed_event(tuple(row))

    def complete_internal(
        self,
        connection: Any,
        *,
        event_id: UUID,
        worker_role: ActorRole,
        completed_at: datetime | None = None,
    ) -> None:
        _require_worker(worker_role)
        timestamp = completed_at or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE outbox_events
                SET status = 'SENT', sent_at = %s
                WHERE id = %s AND status = 'PROCESSING'
                RETURNING id
                """,
                (timestamp, event_id),
            )
            if cursor.fetchone() is None:
                raise OutboxStateError("outbox completion claim is stale")

    def retry_or_dead_letter_internal(
        self,
        connection: Any,
        *,
        event_id: UUID,
        worker_role: ActorRole,
        error_class: str,
        retryable: bool,
        now: datetime | None = None,
    ) -> str:
        _require_worker(worker_role)
        if not error_class.strip() or len(error_class) > 100:
            raise OutboxStateError("outbox error class is invalid")
        timestamp = now or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload, attempt_count, occurred_at
                FROM outbox_events
                WHERE id = %s AND status = 'PROCESSING'
                FOR UPDATE
                """,
                (event_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise OutboxStateError("outbox retry claim is stale")
            attempt_count = int(row[1])
            delay = RETRY_DELAYS.get(attempt_count)
            if retryable and delay is not None:
                cursor.execute(
                    """
                    UPDATE outbox_events
                    SET status = 'PENDING', available_at = %s, held_reason = %s
                    WHERE id = %s AND status = 'PROCESSING'
                    """,
                    (timestamp + delay, error_class, event_id),
                )
                return "PENDING"

            payload = row[0]
            if not isinstance(payload, dict):
                raise OutboxStateError("outbox payload is not a JSON object")
            payload_hash = canonical_document(payload).snapshot_hash
            occurred_at = _datetime(row[2])
            cursor.execute(
                """
                UPDATE outbox_events
                SET status = 'DEAD', held_reason = %s
                WHERE id = %s AND status = 'PROCESSING'
                """,
                (error_class, event_id),
            )
            cursor.execute(
                """
                INSERT INTO dead_letter_events (
                    id, outbox_event_id, normalized_payload_hash, last_error_class,
                    attempts, first_attempted_at, last_attempted_at, replay_eligible,
                    operator_decision
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
                """,
                (
                    uuid4(),
                    event_id,
                    payload_hash,
                    error_class,
                    attempt_count,
                    occurred_at,
                    timestamp,
                    retryable,
                ),
            )
            return "DEAD"


def _require_worker(role: ActorRole) -> None:
    if role is not ActorRole.OUTBOX_WORKER:
        raise OutboxAuthorizationError("only OUTBOX_WORKER may claim outbox events")


def _claimed_event(row: tuple[object, ...]) -> ClaimedOutboxEvent:
    payload = row[4]
    if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
        raise OutboxStateError("outbox payload is not a string-keyed JSON object")
    normalized = json.loads(json.dumps(payload))
    if not isinstance(normalized, dict):
        raise OutboxStateError("outbox payload is not a JSON object")
    return ClaimedOutboxEvent(
        _uuid(row[0]),
        str(row[1]),
        _uuid(row[2]),
        str(row[3]),
        {str(key): value for key, value in normalized.items()},
        str(row[5]),
        _uuid(row[6]),
        int(str(row[7])),
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OutboxStateError("stored outbox timestamp is invalid")
    return value
