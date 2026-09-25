"""`0053` applied to a database that already holds consent decisions, not an empty one.

`test_migration_populated.py` explains why this shape of test exists. `0053` backfills, and the
question that matters is what happens to the withdrawals recorded before a TRANSACTIONAL purpose
existed. `DEC-033` takes the conservative reading:

* every SUPPRESSED or PENDING_REVIEW_BLOCKED MARKETING row gets a TRANSACTIONAL twin in the same
  state, citing the same source consent event -- a STOP recorded before the purposes were separated
  stops service messages too;
* a CLEAR or UNKNOWN_BLOCKED MARKETING row gets no twin: nobody wrote STOP there, and absence of a
  TRANSACTIONAL row is not a block;
* the `0008` guard still holds after the migration: a MARKETING withdrawal is never undone, and a
  TRANSACTIONAL block cannot be cleared by an ordinary UPDATE.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.migrations import MIGRATIONS_DIRECTORY, apply_migrations
from psycopg import sql
from psycopg.conninfo import make_conninfo

MIGRATION_UNDER_TEST = "0053"
NOW = datetime(2026, 9, 20, 3, tzinfo=UTC)
CHANNEL = "INTERNAL_TEST"


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def scratch_database() -> Generator[str, None, None]:
    configured = _database_url()
    maintenance = make_conninfo(configured, dbname="postgres")
    name = f"ntl_migration_{uuid4().hex[:12]}"
    with psycopg.connect(maintenance, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(configured, dbname=name)
    finally:
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def migrations_before(tmp_path: Path) -> Path:
    directory = tmp_path / "migrations"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIRECTORY.glob("*.sql")):
        if path.name[:4] < MIGRATION_UNDER_TEST:
            shutil.copy2(path, directory / path.name)
    return directory


def _legacy_decision(connection: Any, state: str) -> tuple[UUID, UUID]:
    """A MARKETING consent decision in the pre-`0053` shape; returns (contact, consent event)."""
    contact, webhook, event = uuid4(), uuid4(), uuid4()
    event_type = {"SUPPRESSED": "WITHDRAW", "PENDING_REVIEW_BLOCKED": "PENDING_REVIEW_BLOCK"}.get(
        state, "WITHDRAW"
    )
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO webhook_events (
                id, provider, channel_account_id, provider_event_id, payload_hash, event_type,
                contact_binding_id, channel, opt_out_disposition, processing_status, received_at
            ) VALUES (%s, 'LEGACY', %s, %s, %s, 'MESSAGE', %s, %s, 'NONE', 'PROCESSED', %s)
            """,
            (
                webhook,
                f"account-{uuid4().hex}",
                f"event-{uuid4().hex}",
                f"RAW-SHA256-V1:{'a' * 64}",
                contact,
                CHANNEL,
                NOW,
            ),
        )
        cursor.execute(
            """
            INSERT INTO consent_events (
                id, contact_binding_id, purpose, channel, event_type, registry_version,
                evidence_webhook_id, occurred_at
            ) VALUES (%s, %s, 'MARKETING', %s, %s, 'opt-out-v1', %s, %s)
            """,
            (event, contact, CHANNEL, event_type, webhook, NOW),
        )
        cursor.execute(
            """
            INSERT INTO suppression_entries (
                contact_binding_id, purpose, channel, state, source_consent_event_id,
                row_version, updated_at
            ) VALUES (%s, 'MARKETING', %s, %s, %s, 3, %s)
            """,
            (contact, CHANNEL, state, event, NOW),
        )
    return contact, event


def _rows(connection: Any, contact: UUID) -> dict[str, tuple[str, UUID, int]]:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT purpose, state, source_consent_event_id, row_version
            FROM suppression_entries WHERE contact_binding_id = %s
            """,
            (contact,),
        )
        return {str(r[0]): (str(r[1]), r[2], int(r[3])) for r in cursor.fetchall()}


def test_existing_blocks_gain_a_transactional_twin_and_nothing_else_does(
    scratch_database: str, migrations_before: Path
) -> None:
    with psycopg.connect(scratch_database) as connection:
        apply_migrations(connection, migrations_before)
        suppressed = _legacy_decision(connection, "SUPPRESSED")
        pending = _legacy_decision(connection, "PENDING_REVIEW_BLOCKED")
        clear = _legacy_decision(connection, "CLEAR")
        unknown = _legacy_decision(connection, "UNKNOWN_BLOCKED")

        applied = apply_migrations(connection)

        assert MIGRATION_UNDER_TEST in applied
        for (contact, event), state in (
            (suppressed, "SUPPRESSED"),
            (pending, "PENDING_REVIEW_BLOCKED"),
        ):
            assert _rows(connection, contact) == {
                "MARKETING": (state, event, 3),
                "TRANSACTIONAL": (state, event, 1),
            }
        for contact, _ in (clear, unknown):
            rows = _rows(connection, contact)
            assert set(rows) == {"MARKETING"}, rows


def test_the_guard_still_holds_after_the_migration(
    scratch_database: str, migrations_before: Path
) -> None:
    with psycopg.connect(scratch_database) as connection:
        apply_migrations(connection, migrations_before)
        contact, _ = _legacy_decision(connection, "SUPPRESSED")
        apply_migrations(connection)

        for purpose in ("MARKETING", "TRANSACTIONAL"):
            with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
                connection.execute(
                    """
                    UPDATE suppression_entries SET state = 'CLEAR', row_version = row_version + 1
                    WHERE contact_binding_id = %s AND purpose = %s
                    """,
                    (contact, purpose),
                )
        with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
            connection.execute(
                """
                UPDATE suppression_entries SET row_version = row_version + 5
                WHERE contact_binding_id = %s AND purpose = 'TRANSACTIONAL'
                """,
                (contact,),
            )
        assert {state for state, _, _ in _rows(connection, contact).values()} == {"SUPPRESSED"}
