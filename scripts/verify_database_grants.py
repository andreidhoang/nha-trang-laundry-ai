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
role have what it needs and nothing it must not have?* It decides no policy.

`DEC-020` resolved on 2026-09-17 and changed what "nothing it must not have" means. DELETE is no
longer revoked from everyone: a dedicated `retention_purge` role holds it on exactly the disposable
payload side tables, so that the identity serving customers is not the identity that can erase their
records. The application roles are unchanged and still hold no DELETE anywhere.

The purge role is therefore audited here too, and its permitted table set is read from
`DISPOSABLE_PAYLOAD_STORES` rather than restated. `DEC-020` calls out the failure mode by name -- a
purgeable table whose permission lives somewhere else drifts silently in the direction that matters,
leaving a schedule the database cannot honour. Deriving the list means a class entering the registry
without its grant fails this check instead of failing a purge in production.

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
from nha_trang_laundry_db.retention import DISPOSABLE_PAYLOAD_STORES

#: What an application role must be able to do on every table it serves.
REQUIRED: tuple[str, ...] = ("SELECT", "INSERT", "UPDATE")

#: What it must never hold. DELETE and TRUNCATE are the purge surface `DEC-020` governs; REFERENCES
#: and TRIGGER are DDL-adjacent and have no place in an application identity.
FORBIDDEN: tuple[str, ...] = ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

#: `DEC-020`: the one identity permitted to delete, and only from the separable stores.
PURGE_ROLE = "retention_purge"

#: Derived from the retention registry, never restated. A class gaining a store without gaining its
#: grant fails here rather than in production.
PURGE_DELETE_TABLES: frozenset[str] = frozenset(
    store.payload_table for store in DISPOSABLE_PAYLOAD_STORES.values()
)

#: The purge identity deletes rows. It has no business rewriting them, truncating a table, or
#: holding anything DDL-adjacent.
PURGE_FORBIDDEN: tuple[str, ...] = ("TRUNCATE", "REFERENCES", "TRIGGER")

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


def audit_purge_role(connection: object, role: str = PURGE_ROLE) -> tuple[list[str], list[str]]:
    """Return (missing, excess) for the purge identity: DELETE on the side tables and nowhere else.

    `has_table_privilege` rather than a scan of `information_schema.role_table_grants`, because the
    former accounts for privileges held through role membership and the latter does not -- and
    membership is exactly how an operator attaches a login identity to this group role.
    """

    missing: list[str] = []
    excess: list[str] = []
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(QUERY, (role, ["DELETE"]))
        for table, privilege, held in cursor.fetchall():
            permitted = table in PURGE_DELETE_TABLES
            if permitted and not held:
                missing.append(f"{table}.{privilege}")
            elif held and not permitted:
                excess.append(f"{table}.{privilege}")
        cursor.execute(QUERY, (role, list(PURGE_FORBIDDEN)))
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
                    "       DELETE and TRUNCATE are the purge surface DEC-020 governs, and it "
                    "grants them to retention_purge alone. No application identity holds them."
                )
            if not missing and not excess:
                print(
                    f"OK   {role}: {len(REQUIRED)} required and 0 forbidden "
                    f"across {table_count} tables"
                )
        # DEC-020: the purge identity, audited against the registry it exists to serve.
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (PURGE_ROLE,))
            purge_role_exists = cursor.fetchone() is not None
        if not purge_role_exists:
            failures += 1
            print(
                f"FAIL {PURGE_ROLE}: the role does not exist. Migration 0038 creates it beside "
                "the table it governs; a database without it reports a retention schedule it "
                "cannot honour."
            )
        else:
            missing, excess = audit_purge_role(connection)
            if missing:
                failures += 1
                print(f"FAIL {PURGE_ROLE}: cannot delete from {len(missing)} separable store(s)")
                for item in missing:
                    print(f"       missing  {item}")
            if excess:
                failures += 1
                print(f"FAIL {PURGE_ROLE}: holds {len(excess)} privilege(s) DEC-020 withholds")
                for item in excess[:20]:
                    print(f"       excess   {item}")
            if not missing and not excess:
                print(
                    f"OK   {PURGE_ROLE}: DELETE on {len(PURGE_DELETE_TABLES)} separable store(s) "
                    f"and nothing else across {table_count} tables"
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
