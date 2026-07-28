from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import pytest
from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    ConfigurationStateError,
    ConfigurationValidationError,
    snapshot_hash,
)


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...] | None] | None = None) -> None:
        self.executed: list[str] = []
        self.rows = rows or []

    def execute(self, query: str, params: object | None = None) -> None:
        del params
        self.executed.append(query)

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows.pop(0) if self.rows else None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> Literal[False]:
        return False


class FakeConnection:
    def __init__(self, rows: list[tuple[object, ...] | None] | None = None) -> None:
        self.cursor_instance = FakeCursor(rows)
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def transaction(self) -> Any:
        try:
            yield
        except Exception:
            self.rolled_back = True
            raise
        else:
            self.committed = True

    def cursor(self) -> FakeCursor:
        return self.cursor_instance


CONFIG_ID = UUID("00000000-0000-0000-0000-000000000010")
STAFF_ID = UUID("00000000-0000-0000-0000-000000000011")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000012")
OCCURRED_AT = datetime(2026, 7, 28, tzinfo=UTC)


def validate_pricebook(payload: Mapping[str, object]) -> None:
    if set(payload) != {"services"} or not isinstance(payload["services"], list):
        raise ConfigurationValidationError("PRICEBOOK requires a services list")


def repository() -> ConfigurationRepository:
    return ConfigurationRepository({"PRICEBOOK": validate_pricebook})


def test_snapshot_hash_uses_canonical_payload_and_ignores_volatile_fields() -> None:
    first = {"services": [], "trace_id": "first", "nested": {"server_generated_at": "now"}}
    second = {"nested": {}, "services": [], "request_received_at": "later"}

    assert snapshot_hash(first) == snapshot_hash(second)


def test_create_draft_writes_atomic_ledger_records() -> None:
    connection = FakeConnection()

    config_id = repository().create_draft(
        connection,
        ConfigurationDraft(
            config_type="PRICEBOOK",
            version=1,
            payload={"services": []},
            created_by=STAFF_ID,
            config_id=CONFIG_ID,
            occurred_at=OCCURRED_AT,
        ),
        correlation_id=CORRELATION_ID,
    )

    assert config_id == CONFIG_ID
    statements = "\n".join(connection.cursor_instance.executed)
    assert "INSERT INTO configuration_versions" in statements
    assert "INSERT INTO domain_events" in statements
    assert "INSERT INTO audit_events" in statements
    assert "INSERT INTO outbox_events" in statements


def test_create_draft_rejects_unknown_or_invalid_config_type() -> None:
    with pytest.raises(ConfigurationValidationError, match="registered typed validator"):
        repository().create_draft(
            FakeConnection(),
            ConfigurationDraft("SLA", 1, {"services": []}, STAFF_ID),
            correlation_id=CORRELATION_ID,
        )


def test_publish_is_atomic_and_rejects_stale_draft() -> None:
    content_hash = snapshot_hash({"services": []})
    connection = FakeConnection(rows=[("PRICEBOOK",)])

    repository().publish(
        connection,
        config_id=CONFIG_ID,
        version=1,
        snapshot_hash_value=content_hash,
        published_by=STAFF_ID,
        correlation_id=CORRELATION_ID,
        occurred_at=OCCURRED_AT,
    )

    assert connection.committed is True
    statements = "\n".join(connection.cursor_instance.executed)
    assert "UPDATE configuration_versions" in statements
    assert "AND lifecycle = 'DRAFT'" in statements
    assert "INSERT INTO domain_events" in statements
    assert "INSERT INTO audit_events" in statements
    assert "INSERT INTO outbox_events" in statements

    with pytest.raises(ConfigurationStateError, match="missing, stale, or already published"):
        repository().publish(
            FakeConnection(rows=[None]),
            config_id=CONFIG_ID,
            version=1,
            snapshot_hash_value=content_hash,
            published_by=STAFF_ID,
            correlation_id=CORRELATION_ID,
            occurred_at=OCCURRED_AT,
        )


def test_get_published_hides_drafts() -> None:
    cursor = FakeCursor(rows=[None])

    assert ConfigurationRepository.get_published(cursor, CONFIG_ID) is None
    assert "lifecycle = 'PUBLISHED'" in cursor.executed[0]
