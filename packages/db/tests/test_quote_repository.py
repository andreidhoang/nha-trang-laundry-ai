from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import pytest
from nha_trang_laundry_db.quotes import (
    QuoteIntegrityError,
    QuoteRepository,
    QuoteRevisionCommand,
    QuoteStateError,
)
from quote_test_data import make_quote_snapshot

QUOTE_ID = UUID("00000000-0000-0000-0000-000000000210")
STORE_ID = UUID("00000000-0000-0000-0000-000000000211")
REQUEST_ID = UUID("00000000-0000-0000-0000-000000000212")
ACTOR_ID = UUID("00000000-0000-0000-0000-000000000213")
CORRELATION_ID = UUID("00000000-0000-0000-0000-000000000214")


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...] | None] | None = None) -> None:
        self.executed: list[str] = []
        self.rows = rows or []

    def execute(self, query: str, params: object | None = None) -> None:
        del params
        self.executed.append(query)

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        rows = [row for row in self.rows if row is not None]
        self.rows.clear()
        return rows

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


def command(revision: int = 1, expected: int = 0) -> QuoteRevisionCommand:
    return QuoteRevisionCommand(
        STORE_ID,
        REQUEST_ID,
        make_quote_snapshot(QUOTE_ID, revision),
        expected,
        expected,
        ACTOR_ID,
        CORRELATION_ID,
    )


def test_first_quote_revision_commits_snapshot_event_audit_and_outbox() -> None:
    connection = FakeConnection()

    QuoteRepository().create_revision(connection, command())

    statements = "\n".join(connection.cursor_instance.executed)
    assert connection.committed is True
    assert "INSERT INTO quotes" in statements
    assert "INSERT INTO quote_revisions" in statements
    assert "INSERT INTO domain_events" in statements
    assert "INSERT INTO audit_events" in statements
    assert "INSERT INTO outbox_events" in statements


def test_nonsequential_and_stale_revision_fail_closed() -> None:
    with pytest.raises(QuoteStateError, match="sequential"):
        QuoteRepository().create_revision(FakeConnection(), command(revision=2, expected=0))

    stale = FakeConnection(rows=[None])
    with pytest.raises(QuoteStateError, match="missing, closed, or stale"):
        QuoteRepository().create_revision(stale, command(revision=2, expected=1))
    assert stale.rolled_back is True


def test_read_recomputes_stored_snapshot_hash() -> None:
    snapshot = make_quote_snapshot(QUOTE_ID, 1)
    payload = json.loads(snapshot.document.canonical_json)
    cursor = FakeCursor(
        rows=[
            (
                "ESTIMATE",
                "REVIEW_REQUIRED",
                payload,
                snapshot.document.snapshot_hash,
            )
        ]
    )

    stored = QuoteRepository.get_revision(cursor, QUOTE_ID, 1)
    assert stored is not None
    assert stored.document == snapshot.document

    bad_cursor = FakeCursor(rows=[("ESTIMATE", "DRAFT", payload, "JCS-SHA256-V1:" + "f" * 64)])
    with pytest.raises(QuoteIntegrityError, match="hash mismatch"):
        QuoteRepository.get_revision(bad_cursor, QUOTE_ID, 1)


def test_quote_board_returns_only_server_scoped_current_revisions() -> None:
    valid_until = datetime(2026, 8, 2, tzinfo=UTC)
    cursor = FakeCursor(
        rows=[
            (
                QUOTE_ID,
                2,
                3,
                "APPROVED_EXACT",
                "ACCEPTED_FINAL",
                "JCS-SHA256-V1:" + "a" * 64,
                100_000,
                100_000,
                valid_until,
            )
        ]
    )

    listed = QuoteRepository.list_for_store(cursor, store_id=STORE_ID, limit=100)

    assert len(listed) == 1
    assert listed[0].quote_id == QUOTE_ID
    assert listed[0].row_version == 3
    assert listed[0].valid_until == valid_until
    assert "WHERE quote.store_id = %s" in cursor.executed[0]
