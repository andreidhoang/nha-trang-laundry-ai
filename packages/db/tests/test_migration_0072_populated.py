"""`0072` applied to a database that already holds Sổ thu chi lines (`CASH-COUNT-009`, `DEC-049`).

`test_migration_populated.py` explains why this shape of test exists. `0072` adds the yes/no
"Trả từ két" to a Sổ thu chi line and the `cash_counts` ledger. The lines already written never
said whether the money came out of the drawer, and the migration must not guess that it did:

* every pre-`0072` line reads "no" (`paid_from_drawer = FALSE`), so the cash count expects nothing
  less on account of it;
* the line is still voided once and otherwise immutable -- including the new column -- and the
  older writer's INSERT, which does not name the column, still works and writes "no".
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from nha_trang_laundry_db.cash_counts import drawer_movements
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_db.migrations import MIGRATIONS_DIRECTORY, apply_migrations
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_reports import DAY, _person, _store

MIGRATION_UNDER_TEST = "0072"


@pytest.fixture
def scratch_database() -> Generator[str, None, None]:
    configured = os.environ.get("DATABASE_URL")
    if configured is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
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


def _expense_as_before(connection: Any, store_id: Any, staff_id: Any, amount: int) -> Any:
    """What `ExpenseRepository.record` wrote before `0072`: no "Trả từ két" column to name."""
    expense_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO expenses (
                id, store_id, spent_on, category, amount_vnd, note, recorded_by, recorded_at
            ) VALUES (%s, %s, %s, 'HOA_CHAT', %s, NULL, %s, %s)
            """,
            (expense_id, store_id, DAY, amount, staff_id, datetime.now(UTC)),
        )
    return expense_id


def test_lines_written_before_0072_are_not_from_the_drawer_and_stay_immutable(
    scratch_database: str, migrations_before: Path
) -> None:
    with psycopg.connect(scratch_database, autocommit=True) as connection:
        assert apply_migrations(connection, migrations_before)[-1] < MIGRATION_UNDER_TEST
        store_id = _store(connection)
        owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
        old = _expense_as_before(connection, store_id, owner.staff_user_id, 50_000)
        voided = _expense_as_before(connection, store_id, owner.staff_user_id, 20_000)

        shutil.copy2(
            next(MIGRATIONS_DIRECTORY.glob(f"{MIGRATION_UNDER_TEST}_*.sql")), migrations_before
        )
        assert apply_migrations(connection, migrations_before) == (MIGRATION_UNDER_TEST,)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, paid_from_drawer FROM expenses WHERE store_id = %s ORDER BY amount_vnd",
                (store_id,),
            )
            assert cursor.fetchall() == [(voided, False), (old, False)]
            day = drawer_movements(cursor, store_id=store_id, from_date=DAY, to_date=DAY)[DAY]
        assert (day.drawer_expenses_vnd, day.drawer_expenses_entries) == (0, 0)

        # The older writer still works after the migration, and still writes "no".
        later = _expense_as_before(connection, store_id, owner.staff_user_id, 10_000)
        with connection.cursor() as cursor:
            cursor.execute("SELECT paid_from_drawer FROM expenses WHERE id = %s", (later,))
            assert cursor.fetchone() == (False,)

        # Voided once, as before; "Trả từ két" can no more be rewritten than the amount.
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE expenses SET voided_at = now(), voided_by = recorded_by, row_version = 2 "
                "WHERE id = %s",
                (voided,),
            )
        for change in ("paid_from_drawer = TRUE", "amount_vnd = 1"):
            with (
                pytest.raises(psycopg.errors.RaiseException, match="voided once"),
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    f"UPDATE expenses SET {change}, voided_at = now(), voided_by = recorded_by, "
                    "row_version = 2 WHERE id = %s",
                    (old,),
                )
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM cash_counts")
            assert cursor.fetchone() == (0,)
