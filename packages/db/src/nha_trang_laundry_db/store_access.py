"""Staff-to-store membership, enforced in the repository rather than in the route.

`SHADOW-CONSOLE-001` introduced `staff_store_assignments` because its IDOR test could not otherwise
pass: the console authorized by role alone, so an operator could read any store by changing an
identifier in a URL. That fix covered the Shadow surfaces only, and fixing one surface while five
others stay open is worse than not knowing, because it reads as solved.

This module is the single membership check every store-scoped repository calls. It lives next to the
data rather than in a route, because a route is a place a check can be forgotten.

The failure for an unauthorized role and the failure for an unassigned store are deliberately
indistinguishable to the caller, so probing identifiers teaches nobody which stores exist.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


class StoreAccessError(PermissionError):
    """Raised when a principal is not a member of the store they addressed."""


def member_store_ids(cursor: Any, *, staff_user_id: UUID) -> frozenset[UUID]:
    """Return every store this staff member belongs to. Empty means no access anywhere."""

    cursor.execute(
        """
        SELECT store_id FROM staff_store_assignments
        WHERE staff_user_id = %s AND revoked_at IS NULL
        """,
        (staff_user_id,),
    )
    return frozenset(
        value if isinstance(value := row[0], UUID) else UUID(str(row[0]))
        for row in cursor.fetchall()
    )


def is_store_member(cursor: Any, *, staff_user_id: UUID, store_id: UUID) -> bool:
    cursor.execute(
        """
        SELECT 1 FROM staff_store_assignments
        WHERE staff_user_id = %s AND store_id = %s AND revoked_at IS NULL
        """,
        (staff_user_id, store_id),
    )
    return cursor.fetchone() is not None


def require_store_membership(
    cursor: Any,
    *,
    staff_user_id: UUID,
    store_id: UUID,
    error: type[Exception] = StoreAccessError,
) -> None:
    """Refuse unless the principal is an assigned member of this exact store.

    `error` lets each repository raise the type its callers already handle, so adding this check
    changes no error mapping at the API boundary. `OWNER_ADMIN` is not implicitly a member of every
    store: seeing all stores is an explicit assignment, never an inference from a role.
    """

    if not is_store_member(cursor, staff_user_id=staff_user_id, store_id=store_id):
        raise error("store access is not authorized")


__all__ = [
    "StoreAccessError",
    "is_store_member",
    "member_store_ids",
    "require_store_membership",
]
