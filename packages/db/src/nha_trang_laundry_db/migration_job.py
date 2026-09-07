"""One-shot, secret-file-only staging migration entry point."""

from __future__ import annotations

import time
from pathlib import Path

import psycopg

from .migrations import apply_migrations

DATABASE_URL_SECRET = Path("/run/secrets/database_url")
MAX_SECRET_BYTES = 4096

#: How long to wait for the database to start accepting TCP connections, and how often to look.
#: Bounded and loud: a database that is not coming back must fail the bring-up rather than hold it
#: open, and every attempt says what it is waiting for so the logs distinguish "not up yet" from
#: "wrong address".
STARTUP_TIMEOUT_SECONDS = 90.0
STARTUP_RETRY_SECONDS = 1.0


def _connect_when_ready(database_url: str) -> psycopg.Connection[object]:
    """Wait for the database to accept a connection, then return it.

    **`depends_on` cannot do this job here and it was tried.** On the self-managed branch
    `postgres` is a profiled service in the same project, so `migrate` must wait for it; on the
    provider-managed branch there is no `postgres` service at all and a hard dependency makes the
    file invalid. `required: false` satisfies both -- and drops the wait: measured, `migrate`
    started 14ms *before* `postgres` and the database reported healthy ten seconds later.

    Waiting here also covers the case no compose file can express, which is a provider-managed
    endpoint that is briefly unreachable while the stack comes up.
    """

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    attempt = 0
    while True:
        attempt += 1
        try:
            return psycopg.connect(database_url)
        except psycopg.OperationalError as error:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"the database did not accept a connection within "
                    f"{STARTUP_TIMEOUT_SECONDS:.0f}s ({attempt} attempts). Migrations were not "
                    f"applied and nothing was changed. Last error: {error}"
                ) from error
            print(f"waiting for the database (attempt {attempt}): {str(error).strip()[:120]}")
            time.sleep(STARTUP_RETRY_SECONDS)


def main() -> None:
    """Apply forward-only migrations without exposing the migration connection string."""

    if not DATABASE_URL_SECRET.is_file() or DATABASE_URL_SECRET.is_symlink():
        raise RuntimeError("migration database secret is unavailable")
    if DATABASE_URL_SECRET.stat().st_size > MAX_SECRET_BYTES:
        raise RuntimeError("migration database secret is oversized")
    database_url = DATABASE_URL_SECRET.read_text(encoding="utf-8").strip()
    if not database_url or "\n" in database_url or "\r" in database_url:
        raise RuntimeError("migration database secret is invalid")
    with _connect_when_ready(database_url) as connection:
        applied = apply_migrations(connection)
    print(f"Applied forward-only migrations: {len(applied)}")


if __name__ == "__main__":
    main()
