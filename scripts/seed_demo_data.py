"""Seed the local demo database with synthetic staff and one store. Development only.

Every record this creates is invented. No real person, phone number or address appears here, and
the script refuses to run against anything that does not look like a local database, because the
cost of being wrong about that is a synthetic staff member with OWNER_ADMIN in a real system.

The store is a bare UUID because there is no `stores` table: `store_id` appears in eight columns
across six migrations with no foreign key anywhere. That is a real gap recorded in
docs/PRODUCTION_READINESS_ASSESSMENT.md, not something this script papers over — it prints the
UUID it chose so a human can paste it into the console, which is exactly what the console requires
today.

Usage:
    DATABASE_URL=postgresql://... uv run python scripts/seed_demo_data.py
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import os
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_db.identity import (
    IdentityRepository,
    StaffPrincipal,
    StaffRole,
)
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.store_access import is_store_member

# Stable so that re-seeding, and the runbook, always name the same store.
DEMO_STORE_ID = UUID("11111111-2222-4333-8444-555555555555")

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres", "db", "host.docker.internal"})

# (oidc subject, display name, roles). Subjects must match scripts/demo_identity_provider.py.
DEMO_STAFF: tuple[tuple[str, str, tuple[StaffRole, ...]], ...] = (
    ("demo-operations", "Demo Nhân viên vận hành", (StaffRole.OPERATOR,)),
    ("demo-approver", "Demo Người duyệt", (StaffRole.OPS_APPROVER,)),
    ("demo-auditor", "Demo Kiểm toán", (StaffRole.AUDITOR,)),
)

OWNER_SUBJECT = "demo-owner"
OWNER_NAME = "Demo Chủ cửa hàng"

# The one thing this script publishes that is not invented. Mounted read-only into the seed job;
# the API image does not ship it, because the running service prices from the published
# configuration rather than from a file.
PRICEBOOK_SOURCE = "templates/services-pricebook.csv"


class UnsafeTarget(SystemExit):
    """Raised when the target database does not look local."""


def require_local_database(database_url: str) -> None:
    """Refuse anything that is not obviously a developer's own database.

    A seeding script that creates an OWNER_ADMIN is the most dangerous small script in a
    repository. The check is deliberately strict and deliberately not overridable by a flag.
    """
    host = urlsplit(database_url).hostname
    if host is None or host.lower() not in LOCAL_HOSTS:
        raise UnsafeTarget(
            f"refusing to seed synthetic staff into a non-local database (host: {host!r}). "
            f"Allowed hosts: {', '.join(sorted(LOCAL_HOSTS))}."
        )


def seed(connection: object) -> tuple[UUID, dict[str, UUID]]:
    identity = IdentityRepository()
    owner_id = identity.bootstrap_owner(
        connection,
        oidc_subject=OWNER_SUBJECT,
        display_name=OWNER_NAME,
        email=None,
        correlation_id=uuid4(),
    )
    # assign_store demands a real OWNER_ADMIN principal rather than a flag, so build one from
    # what the database says the owner's roles actually are.
    owner = StaffPrincipal(
        staff_user_id=owner_id,
        oidc_subject=OWNER_SUBJECT,
        roles=_active_roles_for(connection, owner_id),
        mfa_verified=True,
    )

    created: dict[str, UUID] = {OWNER_SUBJECT: owner_id}
    for subject, display_name, roles in DEMO_STAFF:
        # Re-running has to be safe: the stack is brought up more than once during a demo, and
        # `up --wait` reruns every one-shot job. Ask before inserting rather than catching a
        # UniqueViolation, because that error aborts the surrounding transaction as well.
        staff_id = _staff_id_for_subject(connection, subject)
        if staff_id is None:
            staff_id = identity.create_staff(
                connection,
                oidc_subject=subject,
                display_name=display_name,
                email=None,
                actor_id=owner_id,
                correlation_id=uuid4(),
            )
            for role in roles:
                identity.assign_role(
                    connection,
                    staff_user_id=staff_id,
                    role=role,
                    actor_id=owner_id,
                    correlation_id=uuid4(),
                )
        created[subject] = staff_id

    for staff_id in created.values():
        # assign_store writes a domain event at aggregate_version 1, and domain_events is unique
        # on (aggregate_type, aggregate_id, aggregate_version, event_type). Calling it twice
        # therefore fails even though the assignment row itself is ON CONFLICT DO NOTHING. That
        # is the repository's property and not this script's to change, so ask first.
        with connection.cursor() as cursor:  # type: ignore[attr-defined]
            if is_store_member(cursor, staff_user_id=staff_id, store_id=DEMO_STORE_ID):
                continue
        ShadowConsoleRepository.assign_store(
            connection,
            staff_user_id=staff_id,
            store_id=DEMO_STORE_ID,
            principal=owner,
            correlation_id=uuid4(),
        )
    return DEMO_STORE_ID, created


def _active_roles_for(connection: object, staff_id: UUID) -> frozenset[StaffRole]:
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(
            "SELECT role FROM staff_role_assignments "
            "WHERE staff_user_id = %s AND revoked_at IS NULL",
            (staff_id,),
        )
        return frozenset(StaffRole(str(row[0])) for row in cursor.fetchall())


def _staff_id_for_subject(connection: object, subject: str) -> UUID | None:
    with connection.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute("SELECT id FROM staff_users WHERE oidc_subject = %s", (subject,))
        row = cursor.fetchone()
    return None if row is None else UUID(str(row[0]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    mounted_secret = _Path("/run/secrets/database_url")
    parser.add_argument(
        "--database-url-file",
        default=str(mounted_secret) if mounted_secret.is_file() else None,
        help="read the DSN from a file, so it never appears in an environment or a process list",
    )
    arguments = parser.parse_args()
    database_url = arguments.database_url
    if arguments.database_url_file:
        database_url = _Path(arguments.database_url_file).read_text(encoding="utf-8").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL or --database-url-file is required")
    require_local_database(database_url)

    with psycopg.connect(database_url) as connection:
        store_id, staff = seed(connection)
        # QUOTE-COMMAND-001 made the runtime price against a published pricebook rather than a
        # file, so a demo without one cannot quote at all — it answers 503 and looks broken. The
        # pricebook itself is the real owner-confirmed PRICEBOOK_V1, not synthetic data; it is the
        # staff and the store around it that are invented. Publishing is attributed to the demo
        # owner, and re-running does not create a second version.
        digest, published = publish_pricebook(
            connection,
            actor_id=staff[OWNER_SUBJECT],
            source=(_Path(__file__).resolve().parents[1] / PRICEBOOK_SOURCE).read_bytes(),
        )

    print("Seeded synthetic demo data. Nothing here describes a real person.")
    print(f"  Store UUID (paste into the console): {store_id}")
    for subject, staff_id in sorted(staff.items()):
        print(f"  {subject:<18} {staff_id}")
    state = "published" if published else "already published"
    print(f"  Pricebook {state}: JCS-SHA256-V1:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
