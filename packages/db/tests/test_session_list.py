"""`SESSION-LIST-001`: which sessions of a person are live, read against real PostgreSQL.

The revoke route existed and nothing could name a session to it, so a lost phone had one remedy:
the owner disabling the whole account. `IdentityRepository.list_live_sessions` is the read that
names them. What is proved here is that "live" means exactly what the authenticator means -- a
session the list offers to sign out would be accepted at its next request, and a session the
authenticator would refuse is not listed -- that the list is the person's own unless an owner the
database still recognises asks, and that it is bounded and says so.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import (
    MAX_SESSION_LIST,
    IdentityRepository,
    IdentityStateError,
    LiveSession,
    StaffRole,
)
from nha_trang_laundry_db.migrations import apply_migrations

NOW = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _person(connection: Any, *roles: StaffRole) -> tuple[UUID, str]:
    """A staff user with roles, inserted directly so a test may hold two owners."""

    staff_id = uuid4()
    subject = f"session-list-{staff_id}"
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, email, status, created_at)
            VALUES (%s, %s, 'Người thử', NULL, 'ACTIVE', %s)
            """,
            (staff_id, subject, NOW - timedelta(days=1)),
        )
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_id, role.value, NOW - timedelta(days=1)),
            )
    return staff_id, subject


def _sign_in(
    connection: Any,
    subject: str,
    *,
    at: datetime = NOW,
    idle: timedelta = timedelta(hours=8),
    absolute: timedelta = timedelta(hours=24),
    mfa: bool = True,
) -> Any:
    return IdentityRepository().create_session(
        connection,
        oidc_subject=subject,
        mfa_verified=mfa,
        correlation_id=uuid4(),
        now=at,
        idle_ttl=idle,
        absolute_ttl=absolute,
    )


def _listed(
    connection: Any, staff_id: UUID, *, actor: UUID | None = None, at: datetime = NOW
) -> list[UUID]:
    listed = IdentityRepository().list_live_sessions(
        connection, staff_user_id=staff_id, actor_id=actor or staff_id, now=at
    )
    return [entry.session_id for entry in listed.sessions]


def test_a_person_reads_their_own_live_sessions_newest_activity_first(connection: Any) -> None:
    lan, subject = _person(connection, StaffRole.OPERATOR)
    phone = _sign_in(connection, subject, at=NOW - timedelta(hours=3))
    tablet = _sign_in(connection, subject, at=NOW - timedelta(hours=2))
    # The phone is used after the tablet was signed in, so it is the most recent activity.
    IdentityRepository().authenticate_session(
        connection, phone.value, now=NOW - timedelta(minutes=5)
    )
    someone_else, other_subject = _person(connection, StaffRole.OPERATOR)
    _sign_in(connection, other_subject)

    listed = IdentityRepository().list_live_sessions(
        connection, staff_user_id=lan, actor_id=lan, now=NOW
    )

    assert [entry.session_id for entry in listed.sessions] == [phone.session_id, tablet.session_id]
    assert listed.staff_user_id == lan
    assert listed.truncated is False
    first = listed.sessions[0]
    assert first.issued_at == NOW - timedelta(hours=3)
    assert first.last_seen_at == NOW - timedelta(minutes=5)
    assert first.idle_expires_at == NOW - timedelta(minutes=5) + timedelta(hours=8)
    assert first.absolute_expires_at == NOW - timedelta(hours=3) + timedelta(hours=24)
    assert someone_else not in {entry.session_id for entry in listed.sessions}


def test_a_listed_session_carries_no_secret_and_no_hash() -> None:
    """The dataclass is the whole of what leaves the repository; it has no field to leak into."""

    assert {field.name for field in fields(LiveSession)} == {
        "session_id",
        "issued_at",
        "last_seen_at",
        "idle_expires_at",
        "absolute_expires_at",
    }


def test_live_means_what_the_authenticator_means(connection: Any) -> None:
    """Every session the authenticator would refuse is left out; the rest are listed."""

    lan, subject = _person(connection, StaffRole.OPERATOR)
    live = _sign_in(connection, subject)
    revoked = _sign_in(connection, subject)
    IdentityRepository().revoke_session(
        connection, session_id=revoked.session_id, actor_id=lan, correlation_id=uuid4()
    )
    idled_out = _sign_in(connection, subject, at=NOW - timedelta(hours=2), idle=timedelta(hours=1))
    aged_out = _sign_in(
        connection,
        subject,
        at=NOW - timedelta(hours=5),
        idle=timedelta(hours=4),
        absolute=timedelta(hours=4),
    )

    assert _listed(connection, lan) == [live.session_id]
    for refused in (revoked, idled_out, aged_out):
        with pytest.raises(IdentityStateError):
            IdentityRepository().authenticate_session(connection, refused.value, now=NOW)


def test_the_idle_boundary_is_the_authenticators_boundary_to_the_microsecond(
    connection: Any,
) -> None:
    """The authenticator refuses at `now >= idle_expires_at`; the list keeps `idle > now`."""

    lan, subject = _person(connection, StaffRole.OPERATOR)
    session = _sign_in(connection, subject, idle=timedelta(hours=1), absolute=timedelta(hours=2))
    expiry = NOW + timedelta(hours=1)

    assert _listed(connection, lan, at=expiry - timedelta(microseconds=1)) == [session.session_id]
    assert _listed(connection, lan, at=expiry) == []


def test_a_stale_authorization_version_is_not_live(connection: Any) -> None:
    """A role grant bumps the user's version; a session minted before it no longer authenticates."""

    owner, _ = _person(connection, StaffRole.OWNER_ADMIN)
    lan, subject = _person(connection, StaffRole.OPERATOR)
    before = _sign_in(connection, subject, mfa=False)
    IdentityRepository().assign_role(
        connection,
        staff_user_id=lan,
        role=StaffRole.DRIVER,
        actor_id=owner,
        correlation_id=uuid4(),
        occurred_at=NOW,
    )
    after = _sign_in(connection, subject, mfa=False)

    assert _listed(connection, lan) == [after.session_id]
    with pytest.raises(IdentityStateError):
        IdentityRepository().authenticate_session(connection, before.value, now=NOW)


def test_a_disabled_person_has_no_live_session(connection: Any) -> None:
    owner, _ = _person(connection, StaffRole.OWNER_ADMIN)
    lan, subject = _person(connection, StaffRole.OPERATOR)
    _sign_in(connection, subject)
    IdentityRepository().disable_staff(
        connection, staff_user_id=lan, actor_id=owner, correlation_id=uuid4(), occurred_at=NOW
    )

    assert _listed(connection, lan, actor=owner) == []


def test_revoking_one_session_leaves_the_others_live(connection: Any) -> None:
    lan, subject = _person(connection, StaffRole.OPERATOR)
    phone = _sign_in(connection, subject, at=NOW - timedelta(minutes=3))
    tablet = _sign_in(connection, subject, at=NOW - timedelta(minutes=2))
    laptop = _sign_in(connection, subject, at=NOW - timedelta(minutes=1))

    IdentityRepository().revoke_session(
        connection, session_id=phone.session_id, actor_id=lan, correlation_id=uuid4()
    )

    assert _listed(connection, lan) == [laptop.session_id, tablet.session_id]
    for survivor in (tablet, laptop):
        principal = IdentityRepository().authenticate_session(connection, survivor.value, now=NOW)
        assert principal.session_id == survivor.session_id


def test_a_person_cannot_read_anyone_elses_sessions(connection: Any) -> None:
    lan, _ = _person(connection, StaffRole.OPERATOR)
    approver, _ = _person(connection, StaffRole.OPS_APPROVER)
    owner, owner_subject = _person(connection, StaffRole.OWNER_ADMIN)
    _sign_in(connection, owner_subject)

    for reader in (lan, approver):
        with pytest.raises(IdentityStateError, match="owner authorization"):
            _listed(connection, owner, actor=reader)


def test_an_owner_reads_anyones_sessions(connection: Any) -> None:
    owner, _ = _person(connection, StaffRole.OWNER_ADMIN)
    lan, subject = _person(connection, StaffRole.OPERATOR)
    session = _sign_in(connection, subject)

    assert _listed(connection, lan, actor=owner) == [session.session_id]


def test_an_owner_whose_role_was_revoked_in_the_database_reads_nobody_elses(
    connection: Any,
) -> None:
    """The route's `require_owner` trusts the session; this re-read is what does not."""

    former_owner, _ = _person(connection, StaffRole.OWNER_ADMIN)
    lan, subject = _person(connection, StaffRole.OPERATOR)
    _sign_in(connection, subject)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE staff_role_assignments SET revoked_at = %s WHERE staff_user_id = %s",
            (NOW, former_owner),
        )

    with pytest.raises(IdentityStateError, match="owner authorization"):
        _listed(connection, lan, actor=former_owner)


def test_an_unknown_person_is_refused_rather_than_answered_with_nothing(connection: Any) -> None:
    owner, _ = _person(connection, StaffRole.OWNER_ADMIN)

    with pytest.raises(IdentityStateError, match="missing"):
        _listed(connection, uuid4(), actor=owner)


def test_the_list_is_bounded_and_says_when_it_was_cut(connection: Any) -> None:
    lan, subject = _person(connection, StaffRole.OPERATOR)
    sessions = [_sign_in(connection, subject, at=NOW - timedelta(minutes=n)) for n in range(3)]

    cut = IdentityRepository().list_live_sessions(
        connection, staff_user_id=lan, actor_id=lan, now=NOW, limit=2
    )
    whole = IdentityRepository().list_live_sessions(
        connection, staff_user_id=lan, actor_id=lan, now=NOW, limit=3
    )

    assert [entry.session_id for entry in cut.sessions] == [s.session_id for s in sessions[:2]]
    assert cut.truncated is True
    assert len(whole.sessions) == 3
    assert whole.truncated is False


@pytest.mark.parametrize("limit", [0, MAX_SESSION_LIST + 1, True, 2.0])
def test_an_out_of_range_limit_is_refused(connection: Any, limit: Any) -> None:
    lan, _ = _person(connection, StaffRole.OPERATOR)

    with pytest.raises(ValueError, match="limit"):
        IdentityRepository().list_live_sessions(
            connection, staff_user_id=lan, actor_id=lan, now=NOW, limit=limit
        )
