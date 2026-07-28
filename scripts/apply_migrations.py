"""Apply checksum-locked SQL migrations using the configured PostgreSQL URL."""

from __future__ import annotations

import os

import psycopg
from nha_trang_laundry_db.migrations import apply_migrations


def main() -> int:
    """Apply pending migrations without ever printing the connection URL."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required to apply migrations")

    with psycopg.connect(database_url) as connection:
        applied_versions = apply_migrations(connection)

    if applied_versions:
        print(f"Applied migrations: {', '.join(applied_versions)}")
    else:
        print("No pending migrations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
