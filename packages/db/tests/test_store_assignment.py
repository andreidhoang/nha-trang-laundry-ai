"""`STORE-ASSIGNMENT-001`: store membership gets a governed write path, and a revocation.

`staff_store_assignments` gates almost every read and every write in the system, and nothing could
create a row in it. `ShadowConsoleRepository.assign_store` existed with no route and no caller, so
provisioning a working operator meant an owner connecting to PostgreSQL and inserting into an
authorization table by hand — the one authorization change in the system with no actor, no audit
entry and no event.

Two questions the packet required be answered rather than left to fall out of the code are asserted
here, so that changing either is a test failure rather than a silent drift.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import IdentityRepository, StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.shadow_console import ShadowAuthorizationError, ShadowConsoleRepository
from nha_trang_laundry_db.store_access import (
    StoreAccessError,
    is_store_member,
    member_store_ids,
    require_store_membership,
)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _owner(connection: Any) -> StaffPrincipal:
    identity = IdentityRepository()
    owner_id = identity.bootstrap_owner(
        connection,
        oidc_subject=f"owner-{uuid4().hex}",
        display_name="Chủ cửa hàng",
        email=None,
        correlation_id=uuid4(),
    )
    return StaffPrincipal(
        owner_id, f"owner-{owner_id}", frozenset({StaffRole.OWNER_ADMIN}), True, uuid4()
    )


def _operator(connection: Any, owner: StaffPrincipal) -> StaffPrincipal:
    identity = IdentityRepository()
    staff_id = identity.create_staff(
        connection,
        oidc_subject=f"operator-{uuid4().hex}",
        display_name="Nhân viên",
        email=None,
        actor_id=owner.staff_user_id,
        correlation_id=uuid4(),
    )
    identity.assign_role(
        connection,
        staff_user_id=staff_id,
        role=StaffRole.OPERATOR,
        actor_id=owner.staff_user_id,
        correlation_id=uuid4(),
    )
    return StaffPrincipal(
        staff_id, f"operator-{staff_id}", frozenset({StaffRole.OPERATOR}), True, uuid4()
    )


def _assign(connection: Any, owner: StaffPrincipal, staff: StaffPrincipal, store_id: UUID) -> None:
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=staff.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
        now=NOW,
    )


def _revoke(connection: Any, owner: StaffPrincipal, staff: StaffPrincipal, store_id: UUID) -> None:
    ShadowConsoleRepository.revoke_store(
        connection,
        staff_user_id=staff.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
        now=NOW,
    )


def _member(connection: Any, staff: StaffPrincipal, store_id: UUID) -> bool:
    with connection.cursor() as cursor:
        return is_store_member(cursor, staff_user_id=staff.staff_user_id, store_id=store_id)


def _events(connection: Any, staff: StaffPrincipal) -> list[tuple[str, int]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT event_type, aggregate_version FROM domain_events
            WHERE aggregate_type = 'STAFF_STORE_ASSIGNMENT' AND aggregate_id = %s
            ORDER BY aggregate_version
            """,
            (staff.staff_user_id,),
        )
        return [(str(row[0]), int(row[1])) for row in cursor.fetchall()]


def test_an_assigned_staff_member_passes_the_checks_that_refused_them(
    connection: psycopg.Connection[Any],
) -> None:
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()

    with connection.cursor() as cursor, pytest.raises(StoreAccessError):
        require_store_membership(cursor, staff_user_id=operator.staff_user_id, store_id=store_id)

    _assign(connection, owner, operator, store_id)

    with connection.cursor() as cursor:
        require_store_membership(cursor, staff_user_id=operator.staff_user_id, store_id=store_id)
        assert member_store_ids(cursor, staff_user_id=operator.staff_user_id) == frozenset(
            {store_id}
        )


def test_a_staff_member_can_be_assigned_to_more_than_one_store(
    connection: psycopg.Connection[Any],
) -> None:
    """The bug that made a governed grant path impossible.

    `assign_store` wrote its domain event at a hardcoded aggregate_version of 1, and `domain_events`
    is unique on (aggregate_type, aggregate_id, aggregate_version, event_type). The second store
    therefore raised a unique violation from inside the transaction. The demo seed worked around it
    by checking membership before calling; the defect was in the repository.
    """
    owner = _owner(connection)
    operator = _operator(connection, owner)
    first, second = uuid4(), uuid4()

    _assign(connection, owner, operator, first)
    _assign(connection, owner, operator, second)

    with connection.cursor() as cursor:
        assert member_store_ids(cursor, staff_user_id=operator.staff_user_id) == frozenset(
            {first, second}
        )
    assert _events(connection, operator) == [
        ("STAFF_STORE_ASSIGNED", 1),
        ("STAFF_STORE_ASSIGNED", 2),
    ]


def test_revoking_removes_access_and_keeps_both_events(
    connection: psycopg.Connection[Any],
) -> None:
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()

    _assign(connection, owner, operator, store_id)
    assert _member(connection, operator, store_id)

    _revoke(connection, owner, operator, store_id)
    assert not _member(connection, operator, store_id)

    assert _events(connection, operator) == [
        ("STAFF_STORE_ASSIGNED", 1),
        ("STAFF_STORE_REVOKED", 2),
    ]
    # The row survives, carrying who granted and who ended it.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT assigned_by_staff_id, revoked_by_staff_id, revoked_at
            FROM staff_store_assignments WHERE staff_user_id = %s AND store_id = %s
            """,
            (operator.staff_user_id, store_id),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == owner.staff_user_id and row[1] == owner.staff_user_id and row[2] is not None


def test_access_can_be_granted_again_after_a_revocation(
    connection: psycopg.Connection[Any],
) -> None:
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()

    _assign(connection, owner, operator, store_id)
    _revoke(connection, owner, operator, store_id)
    _assign(connection, owner, operator, store_id)

    assert _member(connection, operator, store_id)
    assert [event for event, _ in _events(connection, operator)] == [
        "STAFF_STORE_ASSIGNED",
        "STAFF_STORE_REVOKED",
        "STAFF_STORE_ASSIGNED",
    ]


def test_a_non_owner_cannot_grant_or_revoke(connection: psycopg.Connection[Any]) -> None:
    owner = _owner(connection)
    operator = _operator(connection, owner)
    victim = _operator(connection, owner)
    store_id = uuid4()

    with pytest.raises(ShadowAuthorizationError):
        _assign(connection, operator, victim, store_id)
    with pytest.raises(ShadowAuthorizationError):
        _revoke(connection, operator, victim, store_id)
    assert not _member(connection, victim, store_id)


def test_a_stale_session_claiming_owner_is_refused_by_the_database(
    connection: psycopg.Connection[Any],
) -> None:
    """The principal says OWNER_ADMIN; the database says the account is disabled.

    A `StaffPrincipal` carries the roles a session was minted with. This is the route that hands out
    access to a store's customers, so the repository asks the database whether the actor is still an
    active owner rather than trusting what the session remembers.
    """
    owner = _owner(connection)
    # `bootstrap_owner` refuses a second owner deliberately, so the successor is promoted the
    # ordinary way. Disabling an owner needs a second one, because `disable_staff` refuses
    # to remove the last.
    second_owner = _operator(connection, owner)
    IdentityRepository().assign_role(
        connection,
        staff_user_id=second_owner.staff_user_id,
        role=StaffRole.OWNER_ADMIN,
        actor_id=owner.staff_user_id,
        correlation_id=uuid4(),
    )
    operator = _operator(connection, owner)
    store_id = uuid4()

    IdentityRepository().disable_staff(
        connection,
        staff_user_id=owner.staff_user_id,
        actor_id=second_owner.staff_user_id,
        correlation_id=uuid4(),
    )

    with pytest.raises(ShadowAuthorizationError):
        _assign(connection, owner, operator, store_id)
    assert not _member(connection, operator, store_id)


def test_an_owner_may_assign_into_a_store_they_are_not_a_member_of(
    connection: psycopg.Connection[Any],
) -> None:
    """The packet's open question, answered: yes.

    Both readings are defensible — an owner administers the business rather than a shop, versus an
    owner cannot grant access to data they cannot see. The deciding argument is that the restrictive
    reading makes the *first* assignment in a deployment impossible: a fresh owner belongs to no
    store, so they could never assign themselves or anyone else, and the only way in would be the
    hand-written INSERT this item exists to remove.

    It is also consistent with the rest of staff administration here, which is already business-wide
    rather than store-scoped: `assign_role` and `disable_staff` carry no store dimension at all.

    Granting is not reading. `OWNER_ADMIN` is still not implicitly a member of any store, which the
    next test asserts.
    """
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()

    assert not _member(connection, owner, store_id)
    _assign(connection, owner, operator, store_id)
    assert _member(connection, operator, store_id)
    assert not _member(connection, owner, store_id)


def test_owner_is_not_implicitly_a_member_of_every_store(
    connection: psycopg.Connection[Any],
) -> None:
    """Granting access is not having it. An owner who wants to see a store assigns themselves."""
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()
    _assign(connection, owner, operator, store_id)

    with connection.cursor() as cursor, pytest.raises(StoreAccessError):
        require_store_membership(cursor, staff_user_id=owner.staff_user_id, store_id=store_id)

    _assign(connection, owner, owner, store_id)
    assert _member(connection, owner, store_id)


def test_revoking_the_last_assignment_on_a_store_is_allowed(
    connection: psycopg.Connection[Any],
) -> None:
    """The packet's second question, answered: allowed, and deliberately.

    `disable_staff` protects the last active owner because losing it locks everyone out of the
    system permanently, with no way back. This is not that: an owner can assign anyone to any store
    at any time, so a store left with no members is recoverable in one command. Refusing would add a
    rule that blocks a legitimate act — winding a store down — to prevent a state that is not a
    trap.
    """
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()
    _assign(connection, owner, operator, store_id)

    _revoke(connection, owner, operator, store_id)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM staff_store_assignments
            WHERE store_id = %s AND revoked_at IS NULL
            """,
            (store_id,),
        )
        remaining = cursor.fetchone()
    assert remaining is not None and int(remaining[0]) == 0


def test_revoking_something_never_granted_is_not_an_error(
    connection: psycopg.Connection[Any],
) -> None:
    """The caller asked for no access, and afterwards there is none. Reporting failure invites a
    retry that would change nothing, and the audit entry is still worth writing."""
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()

    _revoke(connection, owner, operator, store_id)

    assert not _member(connection, operator, store_id)
    assert _events(connection, operator) == [("STAFF_STORE_REVOKED", 1)]


def test_an_audit_write_failure_rolls_the_assignment_back(
    connection: psycopg.Connection[Any],
) -> None:
    """An unaudited grant is the defect this item exists to close, so it must not be possible.

    The assignment, its domain event, its audit entry and its outbox event go through
    `commit_material_change` in one transaction. Injecting a failure on the audit insert must take
    the membership row with it — otherwise the failure mode is an authorization nobody can account
    for, which is exactly what an owner's hand-written INSERT produced.
    """
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION test_reject_assignment_audit() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'injected audit failure';
            END;
            $$
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER test_assignment_audit_failure
            BEFORE INSERT ON audit_events
            FOR EACH ROW EXECUTE FUNCTION test_reject_assignment_audit()
            """
        )
    try:
        with pytest.raises(psycopg.Error):
            _assign(connection, owner, operator, store_id)
    finally:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER test_assignment_audit_failure ON audit_events")
            cursor.execute("DROP FUNCTION test_reject_assignment_audit()")

    assert not _member(connection, operator, store_id)
    assert _events(connection, operator) == []
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM outbox_events WHERE aggregate_type = 'STAFF_STORE_ASSIGNMENT'"
        )
        emitted = cursor.fetchone()
    assert emitted is not None and int(emitted[0]) == 0


def test_the_grant_writes_its_event_audit_and_outbox_together(
    connection: psycopg.Connection[Any],
) -> None:
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()
    _assign(connection, owner, operator, store_id)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT action, actor_type, actor_id FROM audit_events
            WHERE aggregate_type = 'STAFF_STORE_ASSIGNMENT' AND aggregate_id = %s
            """,
            (operator.staff_user_id,),
        )
        assert cursor.fetchall() == [("STAFF_STORE_ASSIGN", "STAFF", owner.staff_user_id)]
        cursor.execute(
            """
            SELECT event_type FROM outbox_events
            WHERE aggregate_type = 'STAFF_STORE_ASSIGNMENT' AND aggregate_id = %s
            """,
            (operator.staff_user_id,),
        )
        assert cursor.fetchall() == [("staff.store_assigned.v1",)]


def test_a_revoked_assignment_disappears_from_every_read(
    connection: psycopg.Connection[Any],
) -> None:
    """Soft revocation is only correct if every reader filters it, so each reader is exercised."""
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()
    _assign(connection, owner, operator, store_id)
    _revoke(connection, owner, operator, store_id)

    with connection.cursor() as cursor:
        assert member_store_ids(cursor, staff_user_id=operator.staff_user_id) == frozenset()
        assert not is_store_member(cursor, staff_user_id=operator.staff_user_id, store_id=store_id)
        with pytest.raises(StoreAccessError):
            require_store_membership(
                cursor, staff_user_id=operator.staff_user_id, store_id=store_id
            )
