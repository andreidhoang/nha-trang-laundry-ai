"""Complete the database role separation after migrations have run. Demo stack and CI.

`scripts/generate_demo_material.py` creates three login roles and gives only the migration identity
CREATE on the schema. Table-level privileges cannot be granted at that point because the tables do
not exist yet, so this runs after the migration job and grants the application roles exactly what
they need: the API reads and writes the migrated tables; the worker holds only the tables and
columns its own code executes (`nha_trang_laundry_db.role_grants`). Neither holds anything
destructive. The self-managed shop host runs the same statements through
`scripts/emit_shop_database_setup.py`.

The separation is the point. If a repository ever needs DDL at runtime, or the worker reaches for a
table it should not, the demo and CI fail here rather than in production.

Usage:
    SUPERUSER_DATABASE_URL=postgresql://... uv run python scripts/apply_demo_grants.py
    uv run python scripts/apply_demo_grants.py --owner ci_app   # CI, whose superuser migrates
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import os

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_db.role_grants import (
    API_GRANTS,
    APPLICATION_ROLES,
    MIGRATION_ROLE,
    WORKER_GRANTS,
    grant_statements,
)

# The grant set is data in `nha_trang_laundry_db.role_grants`, read by this script, by
# `emit_shop_database_setup.py` (the self-managed host's psql path) and by
# `verify_database_grants.py`, so the three can never describe different privilege models.
#
# `laundry_api` gets `API_GRANTS`: SELECT, INSERT, UPDATE on every table, now (the `ON ALL TABLES`
# snapshot) and later (`ALTER DEFAULT PRIVILEGES` -- a grant on ALL TABLES alone is a one-shot
# snapshot, which migrations 0031 and 0032 once turned into `permission denied` on a write path).
#
# `laundry_worker` gets `WORKER_GRANTS` and nothing else (`PLATFORM-SECURITY-009` P1). It used to
# receive the API's grant, which let the process that will host the model runtime read every
# customer's phone ciphertext. The worker statements revoke first, so running this against a
# database provisioned under the old model narrows it rather than adding to it.
GRANTS = API_GRANTS

__all__ = ["APPLICATION_ROLES", "GRANTS", "MIGRATION_ROLE", "WORKER_GRANTS", "apply_grants"]


def apply_grants(connection: psycopg.Connection, owner: str = MIGRATION_ROLE) -> list[str]:
    applied: list[str] = []
    with connection.cursor() as cursor:
        for statement in grant_statements(owner):
            cursor.execute(statement)
            applied.append(statement)
    connection.commit()
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("SUPERUSER_DATABASE_URL") or os.environ.get("DATABASE_URL"),
    )
    parser.add_argument(
        "--owner",
        default=MIGRATION_ROLE,
        help=(
            "the role that owns the schema and creates future tables (default privileges are "
            f"declared for it); {MIGRATION_ROLE} everywhere except CI, whose superuser migrates"
        ),
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("SUPERUSER_DATABASE_URL is required")

    with psycopg.connect(arguments.database_url) as connection:
        applied = apply_grants(connection, owner=arguments.owner)

    print(f"Applied {len(applied)} grant statements across {len(APPLICATION_ROLES)} roles.")
    print("Neither application role has DDL, DELETE or TRUNCATE on any table.")
    print(
        f"laundry_worker holds exactly {len(WORKER_GRANTS)} grants on "
        f"{len({grant.table for grant in WORKER_GRANTS})} tables and nothing by default."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
