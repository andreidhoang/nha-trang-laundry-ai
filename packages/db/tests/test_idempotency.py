from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Literal

import pytest
from nha_trang_laundry_db.idempotency import (
    IdempotencyConflictError,
    IdempotencyRepository,
    IdempotentCommand,
)


class MemoryCursor:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], tuple[str, dict[str, object] | None]] = {}
        self._result: tuple[object, ...] | None = None

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        normalized = " ".join(query.split())
        if normalized.startswith("INSERT INTO command_idempotency_records"):
            scope, key, request_hash = str(params[0]), str(params[1]), str(params[2])
            logical_key = (scope, key)
            if logical_key in self.records:
                self._result = None
            else:
                self.records[logical_key] = (request_hash, None)
                self._result = (scope,)
        elif normalized.startswith("SELECT request_hash, response"):
            record = self.records.get((str(params[0]), str(params[1])))
            self._result = record
        elif normalized.startswith("UPDATE command_idempotency_records"):
            scope, key = str(params[2]), str(params[3])
            request_hash, old_response = self.records[(scope, key)]
            assert old_response is None
            import json

            response = json.loads(str(params[0]))
            assert isinstance(response, dict)
            self.records[(scope, key)] = (request_hash, response)
            self._result = (scope,)
        else:
            raise AssertionError(normalized)

    def fetchone(self) -> tuple[object, ...] | None:
        return self._result

    def __enter__(self) -> MemoryCursor:
        return self

    def __exit__(self, *args: object) -> Literal[False]:
        return False


class MemoryConnection:
    def __init__(self) -> None:
        self.cursor_value = MemoryCursor()

    @contextmanager
    def transaction(self) -> Any:
        yield

    def cursor(self) -> MemoryCursor:
        return self.cursor_value


def test_same_key_and_canonical_input_returns_original_result_once() -> None:
    connection = MemoryConnection()
    calls = 0

    def execute() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"order_id": "original"}

    repository = IdempotencyRepository()
    command = IdempotentCommand("store:1:order:create", "opaque-key", {"b": 2, "a": 1})
    first = repository.execute(connection, command, execute)
    replay = repository.execute(
        connection,
        IdempotentCommand("store:1:order:create", "opaque-key", {"a": 1, "b": 2}),
        execute,
    )

    assert calls == 1
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.response == first.response == {"order_id": "original"}


def test_same_key_with_different_input_hash_conflicts() -> None:
    connection = MemoryConnection()
    repository = IdempotencyRepository()
    repository.execute(
        connection,
        IdempotentCommand("scope", "key", {"quantity": "1"}),
        lambda: {"ok": True},
    )

    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        repository.execute(
            connection,
            IdempotentCommand("scope", "key", {"quantity": "2"}),
            lambda: {"ok": True},
        )
