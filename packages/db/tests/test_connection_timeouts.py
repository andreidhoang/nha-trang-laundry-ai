"""`OPS-HARDENING-002` item 2: no connection waits forever.

There was no `connect_timeout`, `statement_timeout` or `lock_timeout` anywhere, and every request
opened its own `psycopg.connect`. One request holding a row lock -- or a migration queued for an
`ACCESS EXCLUSIVE` lock behind it -- and every request after it waited without bound, each one
holding a connection, until PostgreSQL ran out of them and the counter stopped.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from nha_trang_laundry_db.connection import (
    APPLICATION_TIMEOUTS_DEFAULT,
    MIGRATION_LOCK_TIMEOUT_MS_DEFAULT,
    ConnectionTimeouts,
    application_connect,
    application_timeouts,
    migration_lock_timeout_ms,
)
from nha_trang_laundry_db.migrations import apply_migrations

POSTGRES_SKIP_REASON = "DATABASE_URL is required for PostgreSQL integration tests"

#: One advisory-lock key for this file, far from anything the application takes.
LOCK_KEY = 0x0FF_4A2D_0002


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip(POSTGRES_SKIP_REASON)
    return url


# --- Defaults and configuration ------------------------------------------------------------------


def test_the_defaults_are_bounded() -> None:
    assert (
        ConnectionTimeouts(
            connect_timeout_seconds=5,
            statement_timeout_ms=15_000,
            lock_timeout_ms=5_000,
            idle_in_transaction_session_timeout_ms=60_000,
        )
        == APPLICATION_TIMEOUTS_DEFAULT
    )
    # A data migration may run for minutes; what it may not do is *queue* for a lock, because every
    # request that arrives after it queues behind it.
    assert 0 < MIGRATION_LOCK_TIMEOUT_MS_DEFAULT <= 10_000


def test_every_value_is_configurable_from_the_environment() -> None:
    timeouts = application_timeouts(
        {
            "DATABASE_CONNECT_TIMEOUT_SECONDS": "3",
            "DATABASE_STATEMENT_TIMEOUT_MS": "30000",
            "DATABASE_LOCK_TIMEOUT_MS": "2000",
            "DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_MS": "120000",
        }
    )
    assert timeouts == ConnectionTimeouts(3, 30_000, 2_000, 120_000)
    assert application_timeouts({}) == APPLICATION_TIMEOUTS_DEFAULT
    assert migration_lock_timeout_ms({"MIGRATION_LOCK_TIMEOUT_MS": "1500"}) == 1_500
    assert migration_lock_timeout_ms({}) == MIGRATION_LOCK_TIMEOUT_MS_DEFAULT


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DATABASE_STATEMENT_TIMEOUT_MS", "0"),  # "no limit" is the defect, not a setting
        ("DATABASE_LOCK_TIMEOUT_MS", "0"),
        ("DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_MS", "0"),
        ("DATABASE_LOCK_TIMEOUT_MS", "-1"),
        ("DATABASE_CONNECT_TIMEOUT_SECONDS", "0"),
        ("DATABASE_CONNECT_TIMEOUT_SECONDS", "61"),
        ("DATABASE_CONNECT_TIMEOUT_SECONDS", "5s"),
        ("DATABASE_STATEMENT_TIMEOUT_MS", "1.5"),
        ("DATABASE_STATEMENT_TIMEOUT_MS", "86400001"),
        ("DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_MS", "true"),
    ],
)
def test_a_bad_value_refuses_rather_than_falling_back(name: str, value: str) -> None:
    """A typo must not quietly become the default, and never "unbounded"."""

    with pytest.raises(ValueError, match=name):
        application_timeouts({name: value})


@pytest.mark.parametrize("value", ["0", "-5", "soon"])
def test_a_bad_migration_lock_bound_refuses(value: str) -> None:
    with pytest.raises(ValueError, match="MIGRATION_LOCK_TIMEOUT_MS"):
        migration_lock_timeout_ms({"MIGRATION_LOCK_TIMEOUT_MS": value})


def test_the_session_options_carry_every_bound() -> None:
    options = APPLICATION_TIMEOUTS_DEFAULT.session_options()
    assert options == (
        "-c statement_timeout=15000 -c lock_timeout=5000 "
        "-c idle_in_transaction_session_timeout=60000"
    )


def test_the_factory_passes_the_bounds_and_keeps_the_dsns_own_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    def _connect(conninfo: str, **kwargs: Any) -> str:
        captured.append((conninfo, kwargs))
        return "connection"

    monkeypatch.setattr(psycopg, "connect", _connect)
    for name in (
        "DATABASE_CONNECT_TIMEOUT_SECONDS",
        "DATABASE_STATEMENT_TIMEOUT_MS",
        "DATABASE_LOCK_TIMEOUT_MS",
        "DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)

    returned: object = application_connect("postgresql://u@h/db")
    assert returned == "connection"
    conninfo, kwargs = captured[-1]
    assert conninfo == "postgresql://u@h/db"
    assert kwargs["connect_timeout"] == 5
    assert kwargs["options"] == APPLICATION_TIMEOUTS_DEFAULT.session_options()

    # An operator's own `options` in the DSN come *after* ours, so on a repeated `-c` the DSN wins:
    # an explicit, reviewed override stays possible; an absent one is bounded.
    application_connect("postgresql://u@h/db?options=-c%20search_path%3Dx&connect_timeout=9")
    _, kwargs = captured[-1]
    assert kwargs["options"].startswith(APPLICATION_TIMEOUTS_DEFAULT.session_options())
    assert kwargs["options"].endswith("-c search_path=x")
    assert kwargs["connect_timeout"] == 9

    monkeypatch.setenv("DATABASE_LOCK_TIMEOUT_MS", "750")
    application_connect("postgresql://u@h/db")
    assert "-c lock_timeout=750 " in captured[-1][1]["options"]


def test_a_zero_migration_lock_bound_is_refused_before_anything_runs() -> None:
    with pytest.raises(ValueError, match="positive"):
        apply_migrations(object(), lock_timeout_ms=0)


# --- Against PostgreSQL ---------------------------------------------------------------------------


@pytest.fixture
def lock_holder(database_url: str) -> Iterator[int]:
    """Hold one advisory lock on a separate connection, as a long request would hold a row lock."""

    with psycopg.connect(database_url, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            yield LOCK_KEY
        finally:
            holder.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def _show(connection: psycopg.Connection[Any], name: str) -> str:
    row = connection.execute(f"SHOW {name}").fetchone()
    assert row is not None
    return str(row[0])


def test_postgres_applies_the_application_bounds(database_url: str) -> None:
    with application_connect(database_url) as connection:
        settings = {
            name: _show(connection, name)
            for name in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")
        }
    assert settings == {
        "statement_timeout": "15s",
        "lock_timeout": "5s",
        "idle_in_transaction_session_timeout": "1min",
    }


def test_a_request_behind_a_held_lock_fails_fast_instead_of_queueing(
    database_url: str, lock_holder: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_LOCK_TIMEOUT_MS", "300")
    started = time.monotonic()
    with application_connect(database_url) as connection:
        # Without the bound this waits for ever -- which is the defect, and a suite that never
        # finishes is no way to report it. The watchdog cancels, and `QueryCanceled` is not
        # `LockNotAvailable`, so a regression fails here with a message.
        watchdog = threading.Timer(10, connection.cancel)
        watchdog.start()
        try:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                connection.execute("SELECT pg_advisory_lock(%s)", (lock_holder,))
        finally:
            watchdog.cancel()
    assert time.monotonic() - started < 5


def test_a_runaway_statement_is_cancelled(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_STATEMENT_TIMEOUT_MS", "300")
    started = time.monotonic()
    with (
        application_connect(database_url) as connection,
        pytest.raises(psycopg.errors.QueryCanceled),
    ):
        connection.execute("SELECT pg_sleep(5)")
    assert time.monotonic() - started < 4


def _probe_directory(tmp_path: Path, sql: str) -> Path:
    """A one-file migration directory, version 9999 so it can never collide with a real one.

    The failing probes are never recorded in `schema_migrations`; the one that succeeds deletes its
    own row before it returns.
    """

    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "9999_timeout_probe.sql").write_text(sql, encoding="utf-8")
    return directory


def test_a_migration_gives_up_on_a_lock_rather_than_stalling_every_request(
    database_url: str, lock_holder: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The migration is the connection most likely to queue an `ACCESS EXCLUSIVE` lock."""

    monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT_MS", "300")
    directory = _probe_directory(tmp_path, f"SELECT pg_advisory_xact_lock({lock_holder});")
    with psycopg.connect(database_url) as connection:
        # Without the bound this waits for ever. The watchdog turns a regression into a failure
        # (`QueryCanceled` is not `LockNotAvailable`) instead of a suite that never finishes.
        watchdog = threading.Timer(10, connection.cancel)
        watchdog.start()
        started = time.monotonic()
        try:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                apply_migrations(connection, directory)
        finally:
            watchdog.cancel()
        assert time.monotonic() - started < 5
        connection.rollback()
        # Scoped to the migration's transaction: the caller's session is exactly as it was.
        assert _show(connection, "lock_timeout") == "0"
        recorded = connection.execute(
            "SELECT count(*) FROM schema_migrations WHERE version = '9999'"
        ).fetchone()
        assert recorded == (0,)


def test_a_migration_that_succeeds_leaves_the_callers_session_as_it_was(
    database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`SET LOCAL`, not `SET`: after a *committed* migration a plain `SET` would stay in force.

    The rollback path cannot show this -- a rolled-back transaction undoes a plain `SET` too -- so
    this one applies a harmless probe for real and removes its own ledger row afterwards.
    """

    monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT_MS", "300")
    directory = _probe_directory(tmp_path, "SELECT 1;")
    with psycopg.connect(database_url) as connection:
        try:
            assert apply_migrations(connection, directory) == ("9999",)
            assert _show(connection, "lock_timeout") == "0"
            assert _show(connection, "statement_timeout") == "0"
        finally:
            connection.rollback()
            connection.execute("DELETE FROM schema_migrations WHERE version = '9999'")
            connection.commit()


def test_inside_a_migration_the_lock_is_bounded_and_the_statement_is_not(
    database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT_MS", "4321")
    directory = _probe_directory(
        tmp_path,
        "DO $$ BEGIN RAISE EXCEPTION 'probe lock=% statement=%', "
        "current_setting('lock_timeout'), current_setting('statement_timeout'); END $$;",
    )
    with (
        psycopg.connect(database_url, options="-c statement_timeout=1000") as connection,
        pytest.raises(psycopg.errors.RaiseException, match="probe lock=4321ms statement=0"),
    ):
        apply_migrations(connection, directory)


def test_an_unreachable_database_is_bounded_by_the_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A black-holed address, unlike a refused port: nothing answers, so only a timeout ends it.

    TEST-NET-1 (RFC 5737) is never routed. The attempt runs in a thread so a regression is a failure
    with a message rather than a suite that hangs.
    """

    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "2")
    outcome: list[BaseException | None] = []

    def attempt() -> None:
        try:
            application_connect("postgresql://nobody@192.0.2.1:5432/none")
        except BaseException as error:
            outcome.append(error)
        else:
            outcome.append(None)

    worker = threading.Thread(target=attempt, daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(timeout=15)
    assert not worker.is_alive(), "connect did not return within 15s: connect_timeout is not set"
    assert isinstance(outcome[0], psycopg.OperationalError)
    assert time.monotonic() - started < 15
