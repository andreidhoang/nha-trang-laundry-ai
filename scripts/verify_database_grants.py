"""Prove that a deployed database actually enforces the role separation it is supposed to.

`compose.production.yaml` declares three database identities -- migration, api, worker -- and the
schema has never created them. `scripts/apply_demo_grants.py` grants the right privileges when it
is run, but `GRANT ... ON ALL TABLES` is a **one-shot snapshot**: it covers the tables that exist at
that moment and nothing created afterwards. Nothing has ever checked the result.

That combination has a specific, silent failure. Every migration that adds a table leaves the
application roles with *no privileges on it* until somebody re-runs the grants. The first symptom is
`permission denied` on a write path in production, long after the deploy that caused it. Migrations
`0031` and `0032` added `quote_acceptances` and `counter_tickets` on 2026-08-25 and 2026-08-26, so
any stack provisioned before those dates has exactly this waiting.

This script answers the question the deploy needs answered: *for every table, does each application
role have what it needs and nothing it must not have?* It decides no policy. DELETE stays revoked
from everyone, which is the state `DEC-020` exists to decide and is deliberately left alone here.

Usage:
    DATABASE_URL=postgresql://... uv run python scripts/verify_database_grants.py \
        --role laundry_api --role laundry_worker
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import os

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path

#: What an application role must be able to do on every table it serves.
REQUIRED: tuple[str, ...] = ("SELECT", "INSERT", "UPDATE")

#: What it must never hold. DELETE and TRUNCATE are the purge surface `DEC-020` governs; REFERENCES
#: and TRIGGER are DDL-adjacent and have no place in an application identity.
FORBIDDEN: tuple[str, ...] = ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

QUERY = """
SELECT c.relname, p.privilege, has_table_privilege(%s, c.oid, p.privilege) AS held
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
CROSS JOIN unnest(%s::text[]) AS p(privilege)
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname, p.privilege
"""


def audit(connection: object, role: str) -> tuple[list[str], list[str]]:
    """Return (missing, excess) as human-readable `table.PRIVILEGE` strings for one role."""

    missing: list[str] = []
    excess: list[str] = []
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(QUERY, (role, list(REQUIRED)))
        for table, privilege, held in cursor.fetchall():
            if not held:
                missing.append(f"{table}.{privilege}")
        cursor.execute(QUERY, (role, list(FORBIDDEN)))
        for table, privilege, held in cursor.fetchall():
            if held:
                excess.append(f"{table}.{privilege}")
    return missing, excess


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--role",
        action="append",
        default=None,
        help="an application role to audit; repeatable",
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        raise SystemExit("DATABASE_URL is required")
    roles = arguments.role or ["laundry_api", "laundry_worker"]

    failures = 0
    with psycopg.connect(arguments.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r'"
            )
            table_count = int(cursor.fetchone()[0])
        for role in roles:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                if cursor.fetchone() is None:
                    print(f"FAIL {role}: the role does not exist in this database")
                    failures += 1
                    continue
            missing, excess = audit(connection, role)
            if missing:
                failures += 1
                print(f"FAIL {role}: missing {len(missing)} required privilege(s)")
                for item in missing[:20]:
                    print(f"       missing  {item}")
                if len(missing) > 20:
                    print(f"       ... and {len(missing) - 20} more")
                print(
                    "       Re-run scripts/apply_demo_grants.py. A table added by a "
                    "migration after the last grant run has no privileges until it is."
                )
            if excess:
                failures += 1
                print(f"FAIL {role}: holds {len(excess)} privilege(s) it must not have")
                for item in excess[:20]:
                    print(f"       excess   {item}")
                print(
                    "       DELETE and TRUNCATE are the purge surface DEC-020 governs. No "
                    "application identity may hold them while that decision is open."
                )
            if not missing and not excess:
                print(
                    f"OK   {role}: {len(REQUIRED)} required and 0 forbidden "
                    f"across {table_count} tables"
                )
    if failures:
        print(
            f"\n{failures} role check(s) failed. "
            "This database does not enforce its role separation."
        )
        return 1
    print(
        f"\nRole separation verified across {table_count} tables "
        f"and {len(roles)} application roles."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
