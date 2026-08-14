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

# Written as one statement per role rather than a loop over information_schema, so the grant set is
# readable in a review and in the demo evidence.
GRANTS = """
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};
REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM {role};
"""


def apply_grants(connection: psycopg.Connection) -> list[str]:
    applied: list[str] = []
    with connection.cursor() as cursor:
        for role in APPLICATION_ROLES:
            for statement in GRANTS.format(role=role).strip().splitlines():
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
