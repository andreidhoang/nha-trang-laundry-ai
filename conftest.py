"""Repository-level pytest controls for non-skippable CI integration coverage."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from pluggy import Result


def _bootstrap_workspace_imports() -> None:
    """Put the workspace source roots on `sys.path` and `PYTHONPATH` before collection.

    Editable workspace packages reach `sys.path` only through `.pth` files, and CPython skips any
    `.pth` carrying the macOS `UF_HIDDEN` flag. Tests in `packages/evals/tests` also execute
    `scripts/` entry points through `subprocess`, so the roots must be exported, not merely
    inserted. See `scripts/workspace_env.py` and `context/tasks/TASK-env-integrity-001.md`.
    """
    module_path = Path(__file__).resolve().parent / "scripts" / "workspace_env.py"
    if not module_path.is_file():
        return
    specification = importlib.util.spec_from_file_location("workspace_env", module_path)
    if specification is None or specification.loader is None:
        return
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)


_bootstrap_workspace_imports()

POSTGRES_SKIP_REASON = "DATABASE_URL is required for PostgreSQL integration tests"
POSTGRES_SKIP_FAILURE = (
    "PostgreSQL integration coverage is required; a database-backed test attempted to skip."
)

# `TEST-ISOLATION-001`. The integration tests share one database, so without a reset each test
# inherits whatever its predecessors left behind and an order-dependent failure looks like a defect.
#
# `TRUNCATE` rather than `DELETE`: most tables carry `reject_ledger_mutation` as a
# `BEFORE DELETE ... FOR EACH ROW` trigger, and `TRUNCATE` does not fire row-level triggers. The
# ledger guarantee stays intact for application code, which is the only code that must obey it.
#
# `schema_migrations` is preserved because the fixtures apply migrations through it; truncating it
# would make every test re-run the whole migration ledger.
PRESERVED_TABLES = ("schema_migrations",)

# A reset that hangs is worse than a reset that fails: the suite stops with no output and no cause.
# `TRUNCATE` needs `ACCESS EXCLUSIVE` on every table it names, so a connection left open by a
# concurrency test blocks it indefinitely. These bounds turn that into a message.
RESET_LOCK_TIMEOUT_MS = 2_000
RESET_STATEMENT_TIMEOUT_MS = 30_000

RESET_BLOCKED_MESSAGE = (
    "Could not reset the test database within {timeout_ms} ms because another connection still "
    "holds locks on it. A previous test opened a connection it did not close - most likely a "
    "concurrency test whose thread was joined with a timeout and kept running, or a subprocess "
    "still exiting. Make that teardown deterministic; do not raise this timeout. "
    "See context/tasks/TASK-test-isolation-001.md.\nStill connected:\n{holders}"
)


def _connection_holders(cursor: Any) -> str:
    """Describe every other backend on this database, so a blocker is named rather than guessed."""
    try:
        cursor.execute(
            """
            SELECT pid, state, application_name, left(coalesce(query, ''), 120)
            FROM pg_stat_activity
            WHERE datname = current_database() AND pid <> pg_backend_pid()
            ORDER BY pid
            """
        )
        return (
            "\n".join(
                f"  pid={row[0]} state={row[1]} app={row[2]} query={row[3]!r}"
                for row in cursor.fetchall()
            )
            or "  (none)"
        )
    except Exception as error:  # pragma: no cover - diagnosis must never mask the original failure
        return f"  (could not read pg_stat_activity: {error})"


def _database_reset_connection(database_url: str) -> Any:
    """Open the dedicated reset connection with its failure bounds already set."""
    import psycopg

    connection = psycopg.connect(database_url, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SET lock_timeout = {RESET_LOCK_TIMEOUT_MS}")
            cursor.execute(f"SET statement_timeout = {RESET_STATEMENT_TIMEOUT_MS}")
    except BaseException:
        connection.close()
        raise
    return connection


def _truncate_when_dirty(connection: Any) -> None:
    """Clear every table a test may have written, and do nothing at all when none was written.

    The emptiness probe is one query over empty tables, which costs microseconds. Most tests in this
    repository never touch the database, so paying that instead of an unconditional `TRUNCATE` keeps
    a per-test reset affordable across the whole suite.

    The table list is read fresh each time rather than cached: the first database test applies the
    migrations, so a list captured at session start would be empty for the rest of the run.
    """
    import psycopg

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public' AND tablename <> ALL(%s)
            ORDER BY tablename
            """,
            (list(PRESERVED_TABLES),),
        )
        tables = tuple(f'"{row[0]}"' for row in cursor.fetchall())
        if not tables:
            return
        cursor.execute("SELECT " + " OR ".join(f"EXISTS (SELECT 1 FROM {name})" for name in tables))
        probe = cursor.fetchone()
        if probe is None or not probe[0]:
            return
        try:
            cursor.execute(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE")
        except (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled) as error:
            raise RuntimeError(
                RESET_BLOCKED_MESSAGE.format(
                    timeout_ms=RESET_LOCK_TIMEOUT_MS, holders=_connection_holders(cursor)
                )
            ) from error


@pytest.fixture(scope="session")
def _database_reset_session() -> Generator[Any | None, None, None]:
    """One long-lived autocommit connection used only to reset state between tests.

    It is session-scoped so the reset costs no connection handshake, and autocommit so it never
    itself holds a lock that would block the next `TRUNCATE`.
    """
    import psycopg

    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        yield None
        return
    try:
        connection = _database_reset_connection(database_url)
    except psycopg.OperationalError:
        # An unreachable database is not this fixture's failure to report. The tests that need it
        # fail on their own connection with their own message, and `pytest_runtest_makereport`
        # below turns the repository's skip into the guarded failure. Reporting it here instead
        # would replace that specific diagnosis with a generic one.
        yield None
        return
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def isolated_database_state(_database_reset_session: Any | None) -> Generator[None, None, None]:
    """Give every test a database containing only what that test puts there.

    The reset runs before the test rather than after, for two reasons: a test that fails leaves its
    rows in place to be inspected, and a test cannot truncate under its own feet by opening a second
    connection.
    """
    if _database_reset_session is not None:
        _truncate_when_dirty(_database_reset_session)
    yield


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the explicit CI-only PostgreSQL integration guard."""
    group = parser.getgroup("nha-trang-laundry-ci")
    group.addoption(
        "--require-postgres-integration",
        action="store_true",
        default=False,
        help="Fail when DATABASE_URL is absent or a PostgreSQL integration test attempts to skip.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Reject a guarded run before collection when its synthetic database URL is missing."""
    if config.getoption("require_postgres_integration") and not os.environ.get("DATABASE_URL"):
        raise pytest.UsageError(
            "--require-postgres-integration requires DATABASE_URL; "
            "CI must provide a reachable synthetic PostgreSQL service."
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
    call: pytest.CallInfo[Any],
) -> Generator[None, Result[pytest.TestReport], None]:
    """Convert the repository's missing-PostgreSQL skip into a deterministic test failure."""
    outcome = yield
    report = outcome.get_result()
    guard_enabled = item.config.getoption("require_postgres_integration")
    if guard_enabled and report.skipped and POSTGRES_SKIP_REASON in str(report.longrepr):
        report.outcome = "failed"
        report.longrepr = POSTGRES_SKIP_FAILURE
