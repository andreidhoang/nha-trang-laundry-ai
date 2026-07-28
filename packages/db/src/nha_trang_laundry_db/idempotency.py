"""Lifetime logical idempotency records for material commands."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nha_trang_laundry_domain.canonical import canonical_document


class IdempotencyConflictError(ValueError):
    """Raised when one logical key is reused with different canonical input."""


class IdempotencyStateError(RuntimeError):
    """Raised when an idempotency record is incomplete or corrupt."""


@dataclass(frozen=True)
class IdempotentCommand:
    scope: str
    key: str
    payload: Mapping[str, object]
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class IdempotentResult:
    response: dict[str, object]
    replayed: bool
    request_hash: str


CommandExecutor = Callable[[], Mapping[str, object]]


class IdempotencyRepository:
    """Execute a command once and durably return its original JSON result on replay."""

    def execute(
        self,
        connection: Any,
        command: IdempotentCommand,
        executor: CommandExecutor,
    ) -> IdempotentResult:
        scope = _required_text(command.scope, "idempotency scope")
        key = _required_text(command.key, "idempotency key")
        request = canonical_document(command.payload)
        occurred_at = command.occurred_at or datetime.now(UTC)

        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO command_idempotency_records (
                    scope, idempotency_key, request_hash, response, created_at, completed_at
                ) VALUES (%s, %s, %s, NULL, %s, NULL)
                ON CONFLICT (scope, idempotency_key) DO NOTHING
                RETURNING scope
                """,
                (scope, key, request.snapshot_hash, occurred_at),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    """
                    SELECT request_hash, response
                    FROM command_idempotency_records
                    WHERE scope = %s AND idempotency_key = %s
                    """,
                    (scope, key),
                )
                row = cursor.fetchone()
                if row is None or not hmac.compare_digest(str(row[0]), request.snapshot_hash):
                    raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")
                response = row[1]
                if not isinstance(response, dict):
                    raise IdempotencyStateError("idempotency result is incomplete or invalid")
                return IdempotentResult(_string_keyed_object(response), True, request.snapshot_hash)

            response_mapping = executor()
            response_document = canonical_document(response_mapping)
            response_value = json.loads(response_document.canonical_json)
            if not isinstance(response_value, dict):
                raise IdempotencyStateError("command result must be a JSON object")
            cursor.execute(
                """
                UPDATE command_idempotency_records
                SET response = %s::jsonb, completed_at = %s
                WHERE scope = %s AND idempotency_key = %s AND response IS NULL
                RETURNING scope
                """,
                (
                    response_document.canonical_json.decode("utf-8"),
                    occurred_at,
                    scope,
                    key,
                ),
            )
            if cursor.fetchone() is None:
                raise IdempotencyStateError("idempotency claim was lost")
            return IdempotentResult(
                _string_keyed_object(response_value), False, request.snapshot_hash
            )


def _required_text(value: str, field: str) -> str:
    stripped = value.strip()
    if not stripped or len(stripped) > 300:
        raise ValueError(f"{field} must contain 1 to 300 characters")
    return stripped


def _string_keyed_object(value: dict[object, object]) -> dict[str, object]:
    if not all(isinstance(key, str) for key in value):
        raise IdempotencyStateError("idempotency result keys must be strings")
    return {str(key): item for key, item in value.items()}
