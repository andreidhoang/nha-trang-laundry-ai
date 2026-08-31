"""Give a synthetic principal a real shop to act in.

Every synthetic generator minted principals with `uuid4()` and no `staff_store_assignments` row,
which was invisible while the approval path checked only roles. Once an approval names its store
and the repository requires membership (`0034`, 2026-08-30), a principal with no assignment is
refused -- correctly, by the same rule that stops one store approving another's action.

So the fixtures seed what production requires instead of the check being relaxed to fit them. This
is the same correction the order fixtures needed when the order guard started reading the chain from
quote to customer: a fixture that could not exist in production tests a path no customer can reach.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.identity import StaffPrincipal


def seed_store_membership(
    connection: Any,
    *,
    principals: tuple[StaffPrincipal | UUID, ...],
    occurred_at: datetime,
    store_id: UUID | None = None,
) -> UUID:
    """Create the staff rows and assignments these principals need, and return their store.

    Idempotent on staff id, because generators reuse a principal across several commands within one
    fixture and a second insert must not be an error.
    """
    store = store_id or uuid4()
    assigner = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên tổng hợp', 'ACTIVE', %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (assigner, f"synthetic-assigner-{assigner}", occurred_at),
        )
        for member in principals:
            # Generators hold either a full principal or a bare actor id, so both are accepted.
            staff_id = member if isinstance(member, UUID) else member.staff_user_id
            subject = (
                f"synthetic-actor-{staff_id}" if isinstance(member, UUID) else member.oidc_subject
            )
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên tổng hợp', 'ACTIVE', %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (staff_id, subject, occurred_at),
            )
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                ON CONFLICT DO NOTHING
                """,
                (staff_id, store, assigner, occurred_at),
            )
    return store


__all__ = ["seed_store_membership"]
