"""`API-INTEGRITY-004`: a genuine deadlock at the HTTP boundary, against PostgreSQL.

`API-INTEGRITY-003` answered a timed-out statement with 503 `DATABASE_BUSY` and left a deadlock
(SQLSTATE 40P01) answering 500 -- which the console renders as "Máy chủ gặp lỗi. Đừng thử lại", the
advice for a write whose outcome is unknown. A deadlock's outcome is known: PostgreSQL aborts the
transaction it picks, whole, before anything commits. This module produces a real one on the
decision route and measures what that means rather than asserting it -- 503, `Retry-After`, not one
row of the loser's decision, event, audit, outbox or idempotency claim left behind, and the same
request under the same `Idempotency-Key` then recording the decision exactly once.

**How the deadlock is made, and why it is deterministic.** PostgreSQL checks a lock wait for a cycle
once, `deadlock_timeout` after the wait begins, and the backend whose check finds the cycle is the
one aborted. So the victim is chosen by arranging who waits when, not by racing:

1. ``blocker`` holds `outbox_events` in SHARE mode -- the decision's last write, as in
   `test_api_integrity_003_postgres.py` -- and ``holder`` holds the quote row FOR UPDATE.
2. The decision request locks its approval row (`_lock_approval`), then waits on ``holder`` at the
   decision-time re-resolution of the quote (`_current_quote_revision`, `FOR SHARE OF q`).
3. ``blocker`` asks for the approval row FOR UPDATE and waits on the request. That is not yet a
   cycle -- the request waits on ``holder``, who waits on nobody -- and the test waits out
   ``blocker``'s one check so that it finds none and ``blocker`` can no longer be picked.
4. ``holder`` lets go. The request writes the state change, decision row, event and audit row, and
   then waits on ``blocker`` for the outbox: now a cycle, and the only wait still to be checked is
   the request's own. The request is the one PostgreSQL aborts, every time.

The request's own bounds are raised far above that so that nothing but the deadlock detector can end
its wait; the aborted backend's counter in `pg_stat_database.deadlocks` is read back as well, so the
503 is proven to be the deadlock and not a timeout that happened to answer the same way.
"""

# ruff: noqa: F811  (pytest fixtures re-exported from test_api_integrity_003_postgres)

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_api.main import DATABASE_BUSY_RETRY_AFTER_SECONDS
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteRevisionCommand

# The 003 module puts `packages/db/tests` on the path, so it is imported before the quote fixtures.
from test_api_integrity_003_postgres import (  # noqa: F401  (fixtures re-exported)
    RENDERED,
    _as,
    _database_url,
    _decision,
    _decision_footprint,
    _headers,
    _queued,
    _raise,
    _Shop,
    connection,
    service,
)

from quote_test_data import PRICED_AT, make_quote_snapshot  # isort: skip

#: Long enough that the request cannot time out while the deadlock is being arranged: only the
#: deadlock detector may end its wait. Neither is anywhere near what the assertions below allow.
REQUEST_LOCK_TIMEOUT_MS = "60000"
REQUEST_STATEMENT_TIMEOUT_MS = "120000"
#: A bound on every wait in the test itself, so a mistake fails rather than hangs.
WAIT_SECONDS = 30.0


def _poll(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.01)


def _blocked_by(monitor: Any, holder_pid: int) -> list[int]:
    """The backends of this database waiting on `holder_pid` right now."""
    with monitor.cursor() as cursor:
        cursor.execute(
            """
            SELECT pid FROM pg_stat_activity
            WHERE datname = current_database() AND %s = ANY(pg_blocking_pids(pid))
            """,
            (holder_pid,),
        )
        return [int(row[0]) for row in cursor.fetchall()]


def _deadlocks(monitor: Any) -> int:
    with monitor.cursor() as cursor:
        # Cumulative statistics are cached per transaction; drop the cache before each read.
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _deadlock_timeout_seconds(monitor: Any) -> float:
    with monitor.cursor() as cursor:
        cursor.execute("SELECT setting::int, unit FROM pg_settings WHERE name = 'deadlock_timeout'")
        row = cursor.fetchone()
    assert row is not None and row[1] == "ms", row
    return int(row[0]) / 1000


def _quote_envelope(connection: Any, service: OperationsService, shop: _Shop) -> tuple[UUID, UUID]:
    """A PRESENT_QUOTE envelope over revision 1 of a real quote: its decision locks that quote."""
    quote_id = uuid4()
    snapshot = make_quote_snapshot(quote_id, 1, 100_000)
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(
            shop.store_id,
            uuid4(),
            snapshot,
            0,
            0,
            shop.operator.staff_user_id,
            uuid4(),
            PRICED_AT,
        ),
    )
    approval_id = _raise(
        service,
        shop,
        {
            "action": "PRESENT_QUOTE",
            "resource_type": "QUOTE_REVISION",
            "resource_id": str(quote_id),
            "resource_version": 1,
            "snapshot_hash": snapshot.document.snapshot_hash,
            "rendered_hash": RENDERED,
            "policy_version": "quote-presentation-v1",
        },
    )
    return approval_id, quote_id


def test_a_decision_that_loses_a_deadlock_answers_503_leaves_nothing_and_retries_once(
    connection: Any, service: OperationsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    shop = _Shop(connection)
    approval_id, quote_id = _quote_envelope(connection, service, shop)
    item = _queued(service, shop, approval_id)
    key = f"decide-{uuid4().hex}"
    url = f"/internal/v1/approvals/{approval_id}/decisions"
    monkeypatch.setenv("DATABASE_LOCK_TIMEOUT_MS", REQUEST_LOCK_TIMEOUT_MS)
    monkeypatch.setenv("DATABASE_STATEMENT_TIMEOUT_MS", REQUEST_STATEMENT_TIMEOUT_MS)
    check_after = _deadlock_timeout_seconds(connection)
    deadlocks_before = _deadlocks(connection)

    with (
        psycopg.connect(_database_url()) as blocker,
        psycopg.connect(_database_url()) as holder,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        blocker_pid, holder_pid = blocker.info.backend_pid, holder.info.backend_pid
        # 1. The decision's last write, and the quote its re-resolution reads, are both held.
        blocker.execute("LOCK TABLE outbox_events IN SHARE MODE")
        holder.execute("SELECT 1 FROM quotes WHERE id = %s FOR UPDATE", (quote_id,))
        blocker_wait: Future[Any] | None = None
        try:
            with _as(service, shop.approver) as client:
                started = time.monotonic()
                request = pool.submit(client.post, url, json=_decision(item), headers=_headers(key))
                # 2. The request holds its approval row and waits on the quote.
                _poll(lambda: len(_blocked_by(connection, holder_pid)) == 1, "the request")
                (request_pid,) = _blocked_by(connection, holder_pid)
                # 3. The blocker waits on the request's approval row; no cycle yet. Its one
                # deadlock check runs `deadlock_timeout` from now, finds none, and is spent.
                blocker_wait = pool.submit(
                    blocker.execute,
                    "SELECT 1 FROM approval_request_states "
                    "WHERE approval_request_id = %s FOR UPDATE",
                    (approval_id,),
                )
                _poll(
                    lambda: blocker_pid in _blocked_by(connection, request_pid),
                    "the blocker to wait on the request",
                )
                time.sleep(check_after + 0.5)
                assert blocker_pid in _blocked_by(connection, request_pid)
                # 4. Release the quote: the request writes, reaches the outbox, closes the cycle.
                holder.rollback()
                deadlocked = request.result(timeout=WAIT_SECONDS)
                elapsed = time.monotonic() - started
            # The survivor got the approval row only because the request's transaction was gone.
            blocker_wait.result(timeout=WAIT_SECONDS)
        finally:
            holder.rollback()
            if blocker_wait is not None:
                blocker_wait.result(timeout=WAIT_SECONDS)
            blocker.rollback()

    assert deadlocked.status_code == 503, deadlocked.text
    assert deadlocked.json() == {"detail": {"reason_code": "DATABASE_BUSY"}}
    assert deadlocked.headers["Retry-After"] == str(DATABASE_BUSY_RETRY_AFTER_SECONDS)
    assert "deadlock" not in deadlocked.text
    # It was the deadlock detector, not a bound: the request's lock wait allowed a minute.
    assert elapsed < int(REQUEST_LOCK_TIMEOUT_MS) / 1000 / 2
    _poll(lambda: _deadlocks(connection) > deadlocks_before, "the deadlock to be counted")
    # Invariant 5: the loser rolled back whole. It had written the state change, the decision row,
    # the domain event and the audit row before it waited on the outbox; none survived, and neither
    # did its idempotency claim -- so the retry below runs, not replays.
    assert _decision_footprint(connection, approval_id, shop.approver, key) == {
        "status": "REQUESTED",
        "decisions": 0,
        "events": 0,
        "audit": 0,
        "outbox": 0,
        "idempotency": 0,
    }

    with _as(service, shop.approver) as client:
        retried = client.post(url, json=_decision(item), headers=_headers(key))
        replayed = client.post(url, json=_decision(item), headers=_headers(key))
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "APPROVED"
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["replayed"] is True
    assert _decision_footprint(connection, approval_id, shop.approver, key) == {
        "status": "APPROVED",
        "decisions": 1,
        "events": 1,
        "audit": 1,
        "outbox": 1,
        "idempotency": 1,
    }
