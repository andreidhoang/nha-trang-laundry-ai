"""Forward-only SQL migration discovery and application."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

MIGRATION_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")
MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[2] / "migrations"


@dataclass(frozen=True)
class Migration:
    """A versioned SQL migration with a content checksum."""

    version: str
    name: str
    path: Path
    checksum: str


def discover_migrations(directory: Path = MIGRATIONS_DIRECTORY) -> tuple[Migration, ...]:
    """Return SQL migrations in unique, forward-only version order."""
    migrations: list[Migration] = []
    versions: set[str] = set()
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            raise ValueError(f"invalid migration filename: {path.name}")
        version = match.group("version")
        if version in versions:
            raise ValueError(f"duplicate migration version: {version}")
        versions.add(version)
        migrations.append(
            Migration(
                version=version,
                name=match.group("name"),
                path=path,
                checksum=sha256(path.read_bytes()).hexdigest(),
            )
        )
    return tuple(migrations)


def apply_migrations(connection: Any, directory: Path = MIGRATIONS_DIRECTORY) -> tuple[str, ...]:
    """Apply unapplied migrations and reject changed deployed migration content.

    The caller must use a dedicated migration identity. Application runtime identities must not have
    DDL rights. Each migration is committed as its own PostgreSQL transaction.
    """
    applied_versions: list[str] = []
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                checksum_sha256 TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    for migration in discover_migrations(directory):
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT checksum_sha256 FROM schema_migrations WHERE version = %s",
                (migration.version,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing[0]) != migration.checksum:
                    raise RuntimeError(
                        f"migration {migration.version} checksum changed after application"
                    )
                continue
            cursor.execute(migration.path.read_text(encoding="utf-8"))
            cursor.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum_sha256)
                VALUES (%s, %s, %s)
                """,
                (migration.version, migration.name, migration.checksum),
            )
            applied_versions.append(migration.version)
    return tuple(applied_versions)
