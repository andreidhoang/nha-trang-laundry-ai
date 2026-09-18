"""Apply the payload separation to a database that already holds rows, and prove nothing is lost.

`0034` recorded the trap this file exists to close, in its own header: its first version reasoned
correctly that `ADD COLUMN` is DDL and fires no row trigger, then issued two `UPDATE` statements,
which are DML and do. It applied cleanly to an empty database -- which is every database the test
suite has, because the fixtures migrate a fresh one -- and failed on any database holding a single
approval request, which is every deployment. A re-verification caught it; the suite structurally
could not.

`RETENTION-STORE-001` moves the ciphertext of every inbound webhook out of an append-only table, so
it is exactly the shape of change that trap catches. The evidence the work item requires is
`payload_migration_loses_nothing`, "proven by count and hash before and after" -- which is only
provable against data that existed before.

So this test builds a scratch database, applies `0001`-`0037` through the real runner, seeds rows
with real payload bytes, and only then applies `0038`. It is slower than every other test here and
that is the cost of the only test in the repository that can see this class of defect.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.migrations import MIGRATIONS_DIRECTORY, apply_migrations
from psycopg import sql
from psycopg.conninfo import make_conninfo

SEPARATION_MIGRATION = "0038"
#: Everything from the separation onwards is withheld from the "before" directory. Excluding only
#: `0038` by name left `0039` in it, which then applied against a database that had no
#: `reject_payload_rewrite` yet -- the fixture silently changed meaning the moment a later migration
#: landed, which is the class of breakage this whole file exists to catch.

#: Payload bytes chosen so a comparison cannot pass by accident: different lengths, a repeated
#: value across two rows, non-ASCII, and one row whose bytes are a prefix of another's.
PAYLOADS = (
    b"xin chao",
    b"xin chao",
    b"xin chao mua he",
    "DỪNG".encode(),
    bytes(range(256)),
)


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def scratch_database() -> Generator[str, None, None]:
    """A database of this test's own, because the shared one is already fully migrated."""

    configured = _database_url()
    maintenance = make_conninfo(configured, dbname="postgres")
    name = f"ntl_migration_{uuid4().hex[:12]}"
    # `CREATE DATABASE` cannot run inside a transaction block, hence `autocommit`.
    with psycopg.connect(maintenance, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(configured, dbname=name)
    finally:
        # `FORCE` so a connection this test failed to close cannot leave a database behind for the
        # next run to trip over.
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def migrations_before_separation(tmp_path: Path) -> Path:
    """Every migration up to but excluding the one under test, in a directory of their own."""

    directory = tmp_path / "migrations"
    directory.mkdir()
    copied = 0
    for path in sorted(MIGRATIONS_DIRECTORY.glob("*.sql")):
        if path.name[:4] >= SEPARATION_MIGRATION:
            continue
        shutil.copy2(path, directory / path.name)
        copied += 1
    assert copied > 0
    return directory


def _seed(connection: psycopg.Connection[Any]) -> dict[UUID, bytes]:
    seeded: dict[UUID, bytes] = {}
    with connection.transaction(), connection.cursor() as cursor:
        for index, payload in enumerate(PAYLOADS):
            event_id = uuid4()
            cursor.execute(
                """
                INSERT INTO webhook_events (
                    id, provider, channel_account_id, provider_event_id, payload_hash,
                    encrypted_payload, event_type, channel, opt_out_disposition,
                    processing_status, received_at
                ) VALUES (%s, 'TEST_PROVIDER', %s, %s, %s, %s, 'MESSAGE', 'TEST_CHANNEL',
                          'NONE', 'DISPATCH_PENDING', now())
                """,
                (
                    event_id,
                    f"account-{index}",
                    f"event-{index}",
                    f"RAW-SHA256-V1:{sha256(payload).hexdigest()}",
                    payload,
                ),
            )
            seeded[event_id] = payload
    return seeded


def test_separating_the_payload_from_a_populated_ledger_loses_nothing(
    scratch_database: str, migrations_before_separation: Path
) -> None:
    with psycopg.connect(scratch_database) as connection:
        applied = apply_migrations(connection, migrations_before_separation)
        assert SEPARATION_MIGRATION not in applied

        before = _seed(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM webhook_events")
            row = cursor.fetchone()
            assert row is not None and row[0] == len(PAYLOADS)

        # The migration under test, applied to rows that already existed.
        applied = apply_migrations(connection)
        assert SEPARATION_MIGRATION in applied

        with connection.cursor() as cursor:
            cursor.execute("SELECT webhook_event_id, encrypted_payload FROM webhook_event_payloads")
            after = {UUID(str(row[0])): bytes(row[1]) for row in cursor.fetchall()}

            # The column really left the ledger rather than merely being copied out of it.
            cursor.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'webhook_events' AND column_name = 'encrypted_payload'"
            )
            row = cursor.fetchone()
            assert row is not None and row[0] == 0

            # Every ledger row survives, and its hash still describes the bytes now held beside it.
            cursor.execute(
                """
                SELECT count(*)
                  FROM webhook_events w
                  JOIN webhook_event_payloads p ON p.webhook_event_id = w.id
                 WHERE w.payload_hash =
                       'RAW-SHA256-V1:' || encode(sha256(p.encrypted_payload), 'hex')
                """
            )
            row = cursor.fetchone()
            assert row is not None and row[0] == len(PAYLOADS)

    assert after == before, "count and bytes must match on both sides of the migration"


def test_the_ledger_still_refuses_mutation_after_the_payload_is_separated(
    scratch_database: str, migrations_before_separation: Path
) -> None:
    """The constraint the packet puts first: no existing trigger is dropped, relaxed or bypassed.

    `protect_webhook_event` is replaced by `0038` because one column it named stops existing. That
    is the kind of edit that silently loosens a guard, so the guard is re-proved here on a populated
    database rather than trusted to have survived a `CREATE OR REPLACE`.
    """
    with psycopg.connect(scratch_database) as connection:
        apply_migrations(connection, migrations_before_separation)
        seeded = _seed(connection)
        apply_migrations(connection)
        event_id = next(iter(seeded))

        with (
            pytest.raises(psycopg.errors.RaiseException),
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM webhook_events WHERE id = %s", (event_id,))

        with (
            pytest.raises(psycopg.errors.RaiseException),
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE webhook_events SET payload_hash = %s WHERE id = %s",
                (f"RAW-SHA256-V1:{'0' * 64}", event_id),
            )

        # And the side table is deletable, which is the entire purpose of moving the payload there.
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM webhook_event_payloads WHERE webhook_event_id = %s", (event_id,)
            )
            assert cursor.rowcount == 1
            cursor.execute("SELECT count(*) FROM webhook_events WHERE id = %s", (event_id,))
            row = cursor.fetchone()
            assert row is not None and row[0] == 1
