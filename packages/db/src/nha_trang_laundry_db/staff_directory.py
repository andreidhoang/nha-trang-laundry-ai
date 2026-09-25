"""Who works in a store, read back for its owner (`READ-PATHS-001`).

The staff screen had four identity commands and no read. An owner could create a person, grant a
role, assign a store and disable an account, and could not see any of it afterwards: every form took
a pasted UUID, and the only moment a staff identifier was ever shown was the reply to the create
command. A shop owner who lost that reply had no way to address the person again short of the
database.

This is the read. It is deliberately narrow:

* **Owner only, with MFA, and re-checked against the database.** The same authority the staff write
  routes use. A session keeps claiming `OWNER_ADMIN` after the role is revoked, so the role is read
  from `staff_role_assignments` here rather than trusted from the principal -- the check
  `ShadowConsoleRepository.assign_store` makes before it grants anything.
* **Store-scoped, by membership of the store named.** An owner is not implicitly a member of every
  store (`store_access`), and a directory of a shop the caller does not work in is the cross-store
  read `STORE-SCOPING-001` exists to prevent. A non-member, an unknown store and a wrong role are
  one indistinguishable refusal.
* **No email, no OIDC subject.** The create command answers with the identifier alone, so the owner
  has never been handed either through the API; a read that returned them would widen what the
  console discloses about a person, not merely restore what it already knew.
* **Roles are the person's, not the store's.** `staff_role_assignments` has no store column, so the
  roles listed are the ones the person holds everywhere. The console says so.

Revoked assignments are included for a bounded window, flagged by `assignment_revoked_at`, so the
owner who just removed somebody can see that it took effect. The window is measured from `now`,
which the caller passes in: nothing in this module reads a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership

#: How long a revoked assignment stays on the directory, flagged as revoked. Long enough to confirm
#: a removal and to notice one somebody else made; short enough that the list is the shop's current
#: staff rather than its history, which the audit trail already holds.
RECENT_REVOCATION_WINDOW: Final = timedelta(days=30)

#: A safety bound, not a page size. A laundry shop has a handful of people; a store that reaches
#: this many assignments is told it was cut rather than shown a list that looks complete.
STAFF_DIRECTORY_LIMIT: Final = 500

#: The console lists roles in the order the database `CHECK` constraint and `StaffRole` declare
#: them, not alphabetically, so the owner's own role always reads first.
_ROLE_ORDER: Final = {role.value: index for index, role in enumerate(StaffRole)}


@dataclass(frozen=True, slots=True)
class StaffDirectoryEntry:
    """One person assigned to the store, with what the owner needs to act on them."""

    staff_user_id: UUID
    display_name: str
    #: `staff_users.status`: `ACTIVE` or `DISABLED`. A disabled account keeps its store assignment
    #: (disabling revokes sessions, not memberships), so it is listed and says so.
    status: str
    #: Active roles, everywhere -- the role table carries no store. Empty is a real answer: a
    #: person who was created and never given a role.
    roles: tuple[str, ...]
    assigned_at: datetime
    #: `None` for a live assignment; the revocation instant for one ended inside the window.
    assignment_revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class StaffDirectory:
    store_id: UUID
    entries: tuple[StaffDirectoryEntry, ...]
    #: True when the store had more assignments than `STAFF_DIRECTORY_LIMIT`.
    truncated: bool
    recent_revocation_days: int


class StaffDirectoryRepository:
    """The owner's read of one store's staff. Reads only; writes nothing."""

    @staticmethod
    def list_for_store(
        cursor: Any, *, store_id: UUID, principal: StaffPrincipal, now: datetime
    ) -> StaffDirectory:
        _require_active_owner(cursor, principal)
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        cursor.execute(
            """
            SELECT u.id, u.display_name, u.status, a.assigned_at, a.revoked_at,
                   ARRAY(
                       SELECT r.role FROM staff_role_assignments r
                       WHERE r.staff_user_id = u.id AND r.revoked_at IS NULL
                   ) AS roles
            FROM staff_store_assignments a
            JOIN staff_users u ON u.id = a.staff_user_id
            WHERE a.store_id = %s
              AND (a.revoked_at IS NULL OR a.revoked_at >= %s)
            ORDER BY (a.revoked_at IS NOT NULL), u.display_name, u.id
            LIMIT %s
            """,
            (store_id, now - RECENT_REVOCATION_WINDOW, STAFF_DIRECTORY_LIMIT + 1),
        )
        rows = cursor.fetchall()
        entries = tuple(
            StaffDirectoryEntry(
                staff_user_id=_uuid(row[0]),
                display_name=str(row[1]),
                status=str(row[2]),
                roles=tuple(
                    sorted(
                        (str(role) for role in (row[5] or ())),
                        key=lambda role: (_ROLE_ORDER.get(role, len(_ROLE_ORDER)), role),
                    )
                ),
                assigned_at=row[3],
                assignment_revoked_at=row[4],
            )
            for row in rows[:STAFF_DIRECTORY_LIMIT]
        )
        return StaffDirectory(
            store_id=store_id,
            entries=entries,
            truncated=len(rows) > STAFF_DIRECTORY_LIMIT,
            recent_revocation_days=RECENT_REVOCATION_WINDOW.days,
        )


def _require_active_owner(cursor: Any, principal: StaffPrincipal) -> None:
    """Refuse unless the caller is, right now, an active `OWNER_ADMIN` holding an MFA session.

    `StoreAccessError` rather than a type of its own, so a wrong role and a store the caller is not
    in produce the same 403 through the application's single handler for it.
    """

    if StaffRole.OWNER_ADMIN not in principal.roles or not principal.mfa_verified:
        raise StoreAccessError("staff directory requires OWNER_ADMIN with MFA")
    cursor.execute(
        """
        SELECT 1
        FROM staff_users u
        JOIN staff_role_assignments r ON r.staff_user_id = u.id
        WHERE u.id = %s AND u.status = 'ACTIVE'
          AND r.role = 'OWNER_ADMIN' AND r.revoked_at IS NULL
        """,
        (principal.staff_user_id,),
    )
    if cursor.fetchone() is None:
        raise StoreAccessError("staff directory requires an active OWNER_ADMIN")


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "RECENT_REVOCATION_WINDOW",
    "STAFF_DIRECTORY_LIMIT",
    "StaffDirectory",
    "StaffDirectoryEntry",
    "StaffDirectoryRepository",
]
