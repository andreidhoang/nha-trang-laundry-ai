"""`AUTHZ-LIFECYCLE-001`: taking authority away, and one sign-in per session, on real PostgreSQL.

Two holes in the staff identity lifecycle, both proved closed here:

* **Mover.** There was no way to remove one role. Reducing someone's authority meant disabling the
  account, and a disabled row keeps its unique OIDC subject, so the same person could never be
  re-added with less. `revoke_role` removes one role, and moving the authorization version makes
  every session the person holds stale at its next request.
* **Replay.** One ID token could be exchanged for any number of sessions until it expired. A
  session is now bound to the digest of the token it came from, and the database refuses a second.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import (
    OWNER_SET_LOCK_KEY,
    IdentityPermissionError,
    IdentityRepository,
    IdentityStateError,
    IdentityTokenReplayedError,
    StaffRole,
)
from nha_trang_laundry_db.migrations import apply_migrations

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _person(connection: Any, *roles: StaffRole) -> tuple[UUID, str]:
    staff_id = uuid4()
    subject = f"lifecycle-{staff_id}"
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


def _sign_in(connection: Any, subject: str, digest: str | None = None) -> str:
    return (
        IdentityRepository()
        .create_session(
            connection,
            oidc_subject=subject,
            mfa_verified=True,
            correlation_id=uuid4(),
            now=NOW,
            identity_token_digest=digest,
        )
        .value
    )


def _revoke(connection: Any, staff_id: UUID, role: StaffRole, actor: UUID) -> None:
    IdentityRepository().revoke_role(
        connection,
        staff_user_id=staff_id,
        role=role,
        actor_id=actor,
        correlation_id=uuid4(),
        occurred_at=NOW,
    )


def _count(connection: Any, sql: str, *params: object) -> int:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    return int(row[0])


# --- mover ----------------------------------------------------------------------------------------


def test_an_owner_demotes_an_approver_and_their_sessions_stop_at_once(
    connection: psycopg.Connection[Any],
) -> None:
    owner, _ = _person(connection, StaffRole.OWNER_ADMIN)
    approver, subject = _person(connection, StaffRole.OPS_APPROVER, StaffRole.OPERATOR)
    live = _sign_in(connection, subject)
    repository = IdentityRepository()
    assert (
        StaffRole.OPS_APPROVER in repository.authenticate_session(connection, live, now=NOW).roles
    )

    _revoke(connection, approver, StaffRole.OPS_APPROVER, owner)

    # The session minted with the approver role is refused at its very next request...
    with pytest.raises(IdentityStateError, match="stale"):
        repository.authenticate_session(connection, live, now=NOW)
    # ...and a fresh sign-in carries exactly what remains.
    principal = repository.authenticate_session(connection, _sign_in(connection, subject), now=NOW)
    assert principal.roles == frozenset({StaffRole.OPERATOR})
    assert (
        _count(
            connection,
            "SELECT count(*) FROM staff_role_assignments "
            "WHERE staff_user_id = %s AND role = 'OPS_APPROVER' AND revoked_by = %s",
            approver,
            owner,
        )
        == 1
    )
    # The revocation is a material change: event and audit written with the mutation.
    assert (
        _count(
            connection,
            "SELECT count(*) FROM domain_events e JOIN audit_events a "
            "ON a.aggregate_id = e.aggregate_id AND a.correlation_id = e.correlation_id "
            "WHERE e.aggregate_id = %s AND e.event_type = %s AND a.action = %s",
            approver,
            "STAFF_ROLE_REVOKED",
            "STAFF_ROLE_REVOKE",
        )
        == 1
    )


def test_a_revoked_role_can_be_granted_again(connection: psycopg.Connection[Any]) -> None:
    owner, _ = _person(connection, StaffRole.OWNER_ADMIN)
    person, subject = _person(connection, StaffRole.ACCOUNTANT)
    _revoke(connection, person, StaffRole.ACCOUNTANT, owner)
    IdentityRepository().assign_role(
        connection,
        staff_user_id=person,
        role=StaffRole.ACCOUNTANT,
        actor_id=owner,
        correlation_id=uuid4(),
        occurred_at=NOW,
    )
    principal = IdentityRepository().authenticate_session(
        connection, _sign_in(connection, subject), now=NOW
    )
    assert principal.roles == frozenset({StaffRole.ACCOUNTANT})


def test_the_last_owner_cannot_be_demoted_but_one_of_two_can(
    connection: psycopg.Connection[Any],
) -> None:
    first, _ = _person(connection, StaffRole.OWNER_ADMIN)
    with pytest.raises(IdentityStateError, match="last active owner"):
        _revoke(connection, first, StaffRole.OWNER_ADMIN, first)

    second, _ = _person(connection, StaffRole.OWNER_ADMIN)
    _revoke(connection, first, StaffRole.OWNER_ADMIN, second)
    with pytest.raises(IdentityStateError, match="last active owner"):
        _revoke(connection, second, StaffRole.OWNER_ADMIN, second)


def test_only_an_owner_revokes_and_nothing_is_written_otherwise(
    connection: psycopg.Connection[Any],
) -> None:
    approver, _ = _person(connection, StaffRole.OPS_APPROVER)
    operator, _ = _person(connection, StaffRole.OPERATOR)
    before = _count(connection, "SELECT count(*) FROM domain_events")

    with pytest.raises(IdentityPermissionError):
        _revoke(connection, operator, StaffRole.OPERATOR, approver)

    assert _count(connection, "SELECT count(*) FROM domain_events") == before
    assert (
        _count(
            connection,
            "SELECT count(*) FROM staff_role_assignments "
            "WHERE staff_user_id = %s AND revoked_at IS NULL",
            operator,
        )
        == 1
    )


def test_a_role_that_is_not_held_cannot_be_revoked(connection: psycopg.Connection[Any]) -> None:
    owner, _ = _person(connection, StaffRole.OWNER_ADMIN)
    operator, _ = _person(connection, StaffRole.OPERATOR)
    version = _count(
        connection, "SELECT authorization_version FROM staff_users WHERE id = %s", operator
    )

    with pytest.raises(IdentityStateError, match="not currently assigned"):
        _revoke(connection, operator, StaffRole.AUDITOR, owner)
    with pytest.raises(IdentityStateError, match="missing or disabled"):
        _revoke(connection, uuid4(), StaffRole.OPERATOR, owner)

    # A refused revocation does not sign the person out.
    assert (
        _count(connection, "SELECT authorization_version FROM staff_users WHERE id = %s", operator)
        == version
    )


# --- replay ---------------------------------------------------------------------------------------


def test_one_identity_token_mints_one_session(connection: psycopg.Connection[Any]) -> None:
    _, subject = _person(connection, StaffRole.OPERATOR)
    digest = hashlib.sha256(b"one-provider-sign-in").hexdigest()
    issued = _count(connection, "SELECT count(*) FROM staff_sessions")
    events = _count(connection, "SELECT count(*) FROM domain_events")

    _sign_in(connection, subject, digest)
    with pytest.raises(IdentityTokenReplayedError):
        _sign_in(connection, subject, digest)

    # Exactly one session and one issue event: the replay unwound whole.
    assert _count(connection, "SELECT count(*) FROM staff_sessions") == issued + 1
    assert _count(connection, "SELECT count(*) FROM domain_events") == events + 1
    # A different sign-in by the same person is a different token and is fine.
    _sign_in(connection, subject, hashlib.sha256(b"a-second-sign-in").hexdigest())


@pytest.mark.parametrize("digest", ["", "ABC", "z" * 64, "a" * 63])
def test_a_malformed_token_digest_is_refused_before_the_database(digest: str) -> None:
    class Unused:
        def cursor(self) -> None:
            raise AssertionError("a malformed digest must fail before database access")

    with pytest.raises(IdentityStateError, match="digest"):
        IdentityRepository().create_session(
            Unused(),
            oidc_subject="subject",
            mfa_verified=True,
            correlation_id=uuid4(),
            identity_token_digest=digest,
        )


# --- concurrency ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [("disable", "disable"), ("revoke", "disable"), ("revoke", "revoke")],
)
def test_two_owners_acting_on_each_other_at_once_leave_one_owner(
    connection: psycopg.Connection[Any], first: str, second: str
) -> None:
    """Measured before the fix: two disables, or a revoke and a disable, both committed and left no
    active owner. Here a third connection holds the owner-set lock while both writers queue on it,
    so the test fails outright -- not flakily -- if either path stops taking the lock."""

    first_owner, _ = _person(connection, StaffRole.OWNER_ADMIN)
    second_owner, _ = _person(connection, StaffRole.OWNER_ADMIN)
    database_url = os.environ["DATABASE_URL"]
    outcomes: list[str] = []

    def act(kind: str, target: UUID, actor: UUID) -> None:
        repository = IdentityRepository()
        try:
            with psycopg.connect(database_url) as own:
                if kind == "revoke":
                    repository.revoke_role(
                        own,
                        staff_user_id=target,
                        role=StaffRole.OWNER_ADMIN,
                        actor_id=actor,
                        correlation_id=uuid4(),
                    )
                else:
                    repository.disable_staff(
                        own, staff_user_id=target, actor_id=actor, correlation_id=uuid4()
                    )
            outcomes.append("done")
        except IdentityStateError:
            outcomes.append("refused")

    with psycopg.connect(database_url, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(%s)", (OWNER_SET_LOCK_KEY,))
        workers = [
            threading.Thread(target=act, args=(first, second_owner, first_owner)),
            threading.Thread(target=act, args=(second, first_owner, second_owner)),
        ]
        for worker in workers:
            worker.start()
        deadline = time.monotonic() + 10
        while _count(holder, _LOCK_WAITERS, OWNER_SET_LOCK_KEY) < 2:
            assert time.monotonic() < deadline, "both writers must queue on the owner-set lock"
            time.sleep(0.02)
        holder.execute("SELECT pg_advisory_unlock(%s)", (OWNER_SET_LOCK_KEY,))
        for worker in workers:
            worker.join(timeout=10)

    assert sorted(outcomes) == ["done", "refused"]
    assert _count(connection, _ACTIVE_OWNERS) == 1


_LOCK_WAITERS = """
    SELECT count(*) FROM pg_locks
    WHERE locktype = 'advisory' AND NOT granted
      AND ((classid::bigint << 32) | objid::bigint) = %s
"""
_ACTIVE_OWNERS = """
    SELECT count(*) FROM staff_role_assignments r JOIN staff_users u ON u.id = r.staff_user_id
    WHERE r.role = 'OWNER_ADMIN' AND r.revoked_at IS NULL AND u.status = 'ACTIVE'
"""


# --- MFA on every session -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "roles",
    [(StaffRole.OPERATOR,), (StaffRole.DRIVER,), ()],
    ids=["operator", "driver", "no-role"],
)
def test_no_staff_session_is_minted_without_mfa(
    connection: psycopg.Connection[Any], roles: tuple[StaffRole, ...]
) -> None:
    """`OPERATOR` and `DRIVER` used to be exempt, and a role-less user was exempt too."""

    _, subject = _person(connection, *roles)
    sessions = _count(connection, "SELECT count(*) FROM staff_sessions")

    with pytest.raises(IdentityStateError, match="MFA proof is required"):
        IdentityRepository().create_session(
            connection, oidc_subject=subject, mfa_verified=False, correlation_id=uuid4(), now=NOW
        )

    assert _count(connection, "SELECT count(*) FROM staff_sessions") == sessions


def test_a_session_minted_without_mfa_before_the_rule_is_refused_and_not_listed(
    connection: psycopg.Connection[Any],
) -> None:
    """An operator's pre-existing non-MFA session stops at its next request, with no migration."""

    operator, subject = _person(connection, StaffRole.OPERATOR)
    token = _sign_in(connection, subject)
    session_id = UUID(token.split(".", 1)[0])
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE staff_sessions SET mfa_verified = false WHERE id = %s", (session_id,)
        )
    repository = IdentityRepository()

    with pytest.raises(IdentityStateError, match="MFA proof is required"):
        repository.authenticate_session(connection, token, now=NOW)
    listed = repository.list_live_sessions(
        connection, staff_user_id=operator, actor_id=operator, now=NOW
    )
    assert listed.sessions == ()
