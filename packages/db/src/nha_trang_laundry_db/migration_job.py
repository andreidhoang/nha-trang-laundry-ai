"""One-shot, secret-file-only staging migration entry point."""

from __future__ import annotations

from pathlib import Path

import psycopg

from .migrations import apply_migrations

DATABASE_URL_SECRET = Path("/run/secrets/database_url")
MAX_SECRET_BYTES = 4096


def main() -> None:
    """Apply forward-only migrations without exposing the migration connection string."""

    if not DATABASE_URL_SECRET.is_file() or DATABASE_URL_SECRET.is_symlink():
        raise RuntimeError("migration database secret is unavailable")
    if DATABASE_URL_SECRET.stat().st_size > MAX_SECRET_BYTES:
        raise RuntimeError("migration database secret is oversized")
    database_url = DATABASE_URL_SECRET.read_text(encoding="utf-8").strip()
    if not database_url or "\n" in database_url or "\r" in database_url:
        raise RuntimeError("migration database secret is invalid")
    with psycopg.connect(database_url) as connection:
        applied = apply_migrations(connection)
    print(f"Applied forward-only migrations: {len(applied)}")


if __name__ == "__main__":
    main()
