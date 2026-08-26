"""Complete the demo's database role separation after migrations have run. Development only.

`scripts/generate_demo_material.py` creates three login roles and gives only the migration identity
CREATE on the schema. Table-level privileges cannot be granted at that point because the tables do
not exist yet, so this runs after the migration job and grants the application roles exactly what
they need: read and write on the migrated tables, usage on their sequences, and nothing else.

The separation is the point. If a repository ever needs DDL at runtime, or the worker reaches for a
table it should not, the demo fails here rather than in production.

Usage:
    SUPERUSER_DATABASE_URL=postgresql://... uv run python scripts/apply_demo_grants.py
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import os

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path

APPLICATION_ROLES = ("laundry_api", "laundry_worker")
#: The identity that owns the schema and therefore creates every future table. Default
#: privileges are granted *for* this role, because they apply to what it creates.
MIGRATION_ROLE = "laundry_migrate"

# Written as one statement per role rather than a loop over information_schema, so the grant set is
# readable in a review and in the demo evidence.
# `GRANT ... ON ALL TABLES` is a one-shot snapshot: it covers the tables that exist when it runs and
# nothing a later migration creates. That is a silent trap -- the first symptom is `permission
# denied` on a write path in production, long after the deploy that caused it. Migrations 0031 and
# 0032 added two tables and reproduced it exactly on a role-separated database, which is how it was
# found. `ALTER DEFAULT PRIVILEGES` closes the class: tables the migration identity creates from now
# on carry these privileges without anyone remembering to re-run this script.
#
# The snapshot statements stay, because default privileges are not retroactive -- they do nothing
# for the tables that already exist. Both halves are needed, and `verify_database_grants.py`
# proves the result rather than trusting it.
GRANTS = """
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};
REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM {role};
ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO {role};
ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO {role};
"""


def _statements(script: str) -> list[str]:
    """Split on semicolons, because a default-privileges statement spans two lines."""

    return [part.strip() for part in script.split(";") if part.strip()]


def apply_grants(connection: psycopg.Connection, owner: str = MIGRATION_ROLE) -> list[str]:
    applied: list[str] = []
    with connection.cursor() as cursor:
        for role in APPLICATION_ROLES:
            for statement in _statements(GRANTS.format(role=role, owner=owner)):
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
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("SUPERUSER_DATABASE_URL is required")

    with psycopg.connect(arguments.database_url) as connection:
        applied = apply_grants(connection)

    print(f"Applied {len(applied)} grant statements across {len(APPLICATION_ROLES)} roles.")
    print("Neither application role has DDL, DELETE or TRUNCATE on any table.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
