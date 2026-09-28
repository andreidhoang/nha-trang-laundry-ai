"""Database-authoritative staff identity, roles, and revocable opaque sessions."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation

from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change


class StaffRole(StrEnum):
    OWNER_ADMIN = "OWNER_ADMIN"
    OPS_APPROVER = "OPS_APPROVER"
    OPERATOR = "OPERATOR"
    DRIVER = "DRIVER"
    ACCOUNTANT = "ACCOUNTANT"
    AUDITOR = "AUDITOR"


#: Roles whose sessions must carry MFA proof: all of them (`AUTHZ-LIFECYCLE-001`).
#:
#: This used to omit `OPERATOR` and `DRIVER`, which left six read routes (the order board, order
#: detail, promise, Shadow reviews/drafts/audit, SLA board) open to an operator session with no
#: second factor -- customer names and phone numbers among them. `SECURITY_RELIABILITY_SPEC_V1`
#: §5.1 wants MFA for every role that sees PII before any public channel, and the realm already
#: demands a second factor at every sign-in (`acr_values=mfa`), so no real session lacked it: this
#: makes the server say what the deployment already does instead of depending on it. The rule is
#: applied to the session itself, not per role, so a user with no role gets no exemption either.
SENSITIVE_MFA_ROLES = frozenset(StaffRole)


class StaffSubjectTakenError(ValueError):
    """The OIDC subject already belongs to a staff user.

    Its own type rather than a message, because the route has to answer it differently: every other
    `IdentityStateError` from this module means "that staff user is missing or not in a state that
    allows this", and this one means "that person is already here". Until 2026-09-09 the difference
    was invisible -- nothing caught the `UniqueViolation`, so it escaped `create_staff` as an HTTP
    500. Found by adding the same staff member twice in the console, which is the first mistake an
    owner makes: a double tap, or re-adding somebody who left. A 500 tells the client to retry, so
    the owner retries forever and is never told the account already exists.
    """


class IdentityStateError(ValueError):
    """Raised when a database identity or session is inactive, stale, or invalid."""


class IdentityTokenReplayedError(IdentityStateError):
    """This provider sign-in was already exchanged for a session (`AUTHZ-LIFECYCLE-001`).

    A subclass, so the exchange route answers it exactly like every other refused identity -- the
    same opaque 401 -- while the structured log and tests can still tell a replay apart.
    """


class IdentityPermissionError(IdentityStateError):
    """The actor lacks the owner role an identity action needs, re-read from the database.

    A subclass, so every caller that already maps `IdentityStateError` keeps its answer; a route
    that must say "not yours" rather than "not there" (`revoke_session`) catches this first.
    """


@dataclass(frozen=True)
class StaffPrincipal:
    staff_user_id: UUID
    oidc_subject: str
    roles: frozenset[StaffRole]
    mfa_verified: bool
    session_id: UUID | None = None


@dataclass(frozen=True)
class SessionToken:
    value: str
    session_id: UUID
    expires_at: datetime


#: The most sessions one read returns. A person signed in on more devices than this is not a shop
#: pattern; the read says `truncated` rather than pretending the page is the whole list.
MAX_SESSION_LIST = 200


@dataclass(frozen=True)
class LiveSession:
    """One session that would authenticate right now (`SESSION-LIST-001`).

    Timestamps only. The row's `secret_hash` never leaves this module: it is half of what a cookie
    is checked against, and a read that returned it would hand out a way to test guesses offline.
    """

    session_id: UUID
    issued_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True)
class LiveSessionList:
    staff_user_id: UUID
    sessions: tuple[LiveSession, ...]
    truncated: bool


class IdentityRepository:
    """Persist and resolve named staff identities without trusting OIDC role claims."""

    def bootstrap_owner(
        self,
        connection: Any,
        *,
        oidc_subject: str,
        display_name: str,
        email: str | None,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> UUID:
        subject = _required_text(oidc_subject, "OIDC subject", 255)
        name = _required_text(display_name, "display name", 200)
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("LOCK TABLE staff_users IN SHARE ROW EXCLUSIVE MODE")
            cursor.execute(
                """
                SELECT u.id, u.oidc_subject
                FROM staff_users u
                JOIN staff_role_assignments r ON r.staff_user_id = u.id
                WHERE r.role = %s AND r.revoked_at IS NULL
                LIMIT 1
                """,
                (StaffRole.OWNER_ADMIN,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing[1]) == subject:
                    return _uuid(existing[0])
                raise IdentityStateError("an owner is already bootstrapped")

            staff_id = uuid4()
            timestamp = occurred_at or datetime.now(UTC)
            commit_material_change(
                connection,
                _staff_change(
                    staff_id,
                    1,
                    "STAFF_OWNER_BOOTSTRAPPED",
                    {"role": StaffRole.OWNER_ADMIN},
                    "STAFF_OWNER_BOOTSTRAP",
                    None,
                    correlation_id,
                    timestamp,
                ),
                lambda change_cursor: _insert_staff_owner(
                    change_cursor, staff_id, subject, name, email, timestamp
                ),
            )
        return staff_id

    def create_staff(
        self,
        connection: Any,
        *,
        oidc_subject: str,
        display_name: str,
        email: str | None,
        actor_id: UUID,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> UUID:
        subject = _required_text(oidc_subject, "OIDC subject", 255)
        name = _required_text(display_name, "display name", 200)
        staff_id = uuid4()
        timestamp = occurred_at or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            _require_owner(cursor, actor_id)
            commit_material_change(
                connection,
                _staff_change(
                    staff_id,
                    1,
                    "STAFF_CREATED",
                    {},
                    "STAFF_CREATE",
                    actor_id,
                    correlation_id,
                    timestamp,
                ),
                lambda change_cursor: _insert_staff(
                    change_cursor, staff_id, subject, name, email, timestamp
                ),
            )
        return staff_id

    def assign_role(
        self,
        connection: Any,
        *,
        staff_user_id: UUID,
        role: StaffRole,
        actor_id: UUID,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> None:
        timestamp = occurred_at or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            _serialize_owner_changes(cursor)
            _require_owner(cursor, actor_id)
            aggregate_version = _lock_staff_version(cursor, staff_user_id) + 1
            commit_material_change(
                connection,
                _staff_change(
                    staff_user_id,
                    aggregate_version,
                    "STAFF_ROLE_ASSIGNED",
                    {"role": role},
                    "STAFF_ROLE_ASSIGN",
                    actor_id,
                    correlation_id,
                    timestamp,
                ),
                lambda change_cursor: _assign_role(
                    change_cursor, staff_user_id, role, actor_id, timestamp
                ),
            )

    def revoke_role(
        self,
        connection: Any,
        *,
        staff_user_id: UUID,
        role: StaffRole,
        actor_id: UUID,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> None:
        """Take one role away without disabling the person (`AUTHZ-LIFECYCLE-001`).

        Before this the only way to reduce someone's authority was `disable_staff`, and a disabled
        row keeps its OIDC subject, which is unique -- so the same person could never be re-added
        with less. A demotion was impossible; least privilege could only be restored by giving the
        person a new identity at the provider.

        The authorization version moves in the same transaction, so every session the person holds
        is refused at its next request (`authenticate_session` compares versions) and they sign in
        again with exactly what remains. Revoking the last active `OWNER_ADMIN` is refused for the
        reason `disable_staff` refuses it: nobody would be left who can grant it back.
        """

        timestamp = occurred_at or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            _serialize_owner_changes(cursor)
            _require_owner(cursor, actor_id)
            aggregate_version = _lock_staff_version(cursor, staff_user_id) + 1
            if role == StaffRole.OWNER_ADMIN:
                _require_owner_survives_disable(cursor, staff_user_id)
            commit_material_change(
                connection,
                _staff_change(
                    staff_user_id,
                    aggregate_version,
                    "STAFF_ROLE_REVOKED",
                    {"role": role},
                    "STAFF_ROLE_REVOKE",
                    actor_id,
                    correlation_id,
                    timestamp,
                ),
                lambda change_cursor: _revoke_role(
                    change_cursor, staff_user_id, role, actor_id, timestamp
                ),
            )

    def disable_staff(
        self,
        connection: Any,
        *,
        staff_user_id: UUID,
        actor_id: UUID,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> None:
        timestamp = occurred_at or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            _serialize_owner_changes(cursor)
            _require_owner(cursor, actor_id)
            aggregate_version = _lock_staff_version(cursor, staff_user_id) + 1
            _require_owner_survives_disable(cursor, staff_user_id)
            commit_material_change(
                connection,
                _staff_change(
                    staff_user_id,
                    aggregate_version,
                    "STAFF_DISABLED",
                    {},
                    "STAFF_DISABLE",
                    actor_id,
                    correlation_id,
                    timestamp,
                ),
                lambda change_cursor: _disable_staff(change_cursor, staff_user_id, timestamp),
            )

    def create_session(
        self,
        connection: Any,
        *,
        oidc_subject: str,
        mfa_verified: bool,
        correlation_id: UUID,
        now: datetime | None = None,
        idle_ttl: timedelta = timedelta(hours=8),
        absolute_ttl: timedelta = timedelta(hours=24),
        identity_token_digest: str | None = None,
    ) -> SessionToken:
        """Mint one opaque session for an active staff subject.

        `identity_token_digest` binds the session to the provider sign-in it came from; the database
        refuses a second session for the same digest, so one ID token is one session. The HTTP
        exchange always passes it. `None` exists for sessions minted without a provider token
        (repository tests and operator tooling), never for the exchange.
        """
        if identity_token_digest is not None and not _is_sha256_hex(identity_token_digest):
            raise IdentityStateError("invalid identity token digest")
        if idle_ttl <= timedelta() or absolute_ttl <= timedelta() or idle_ttl > absolute_ttl:
            raise IdentityStateError("invalid session lifetime")
        idle_timeout_seconds = int(idle_ttl.total_seconds())
        if idle_ttl != timedelta(seconds=idle_timeout_seconds):
            raise IdentityStateError("session idle lifetime must use whole seconds")
        timestamp = now or datetime.now(UTC)
        subject = _required_text(oidc_subject, "OIDC subject", 255)
        with connection.cursor() as cursor:
            principal = self._principal_for_subject(cursor, subject, mfa_verified)
        if not mfa_verified:
            raise IdentityStateError("MFA proof is required for every staff session")

        session_id = uuid4()
        secret = secrets.token_urlsafe(32)
        secret_hash = _secret_hash(secret)
        expires_at = timestamp + absolute_ttl

        def insert_session(cursor: Any) -> None:
            try:
                cursor.execute(
                    """
                    INSERT INTO staff_sessions (
                        id, staff_user_id, secret_hash, authorization_version, mfa_verified,
                        issued_at, last_seen_at, idle_expires_at, absolute_expires_at,
                        idle_timeout_seconds, identity_token_digest
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        session_id,
                        principal.staff_user_id,
                        secret_hash,
                        _authorization_version(cursor, principal.staff_user_id),
                        mfa_verified,
                        timestamp,
                        timestamp,
                        timestamp + idle_ttl,
                        expires_at,
                        idle_timeout_seconds,
                        identity_token_digest,
                    ),
                )
            except UniqueViolation as error:
                # The only unique columns are the fresh session id, the fresh secret's hash and the
                # token digest; the first two are 128 and 256 random bits. The whole transaction
                # unwinds, so the replay leaves no event, audit or outbox row behind.
                raise IdentityTokenReplayedError("identity token was already exchanged") from error

        commit_material_change(
            connection,
            _session_change(
                session_id,
                principal.staff_user_id,
                1,
                "STAFF_SESSION_ISSUED",
                {"session_id": str(session_id), "mfa_verified": mfa_verified},
                "STAFF_SESSION_ISSUE",
                principal.staff_user_id,
                correlation_id,
                timestamp,
            ),
            insert_session,
        )
        return SessionToken(f"{session_id}.{secret}", session_id, expires_at)

    def authenticate_session(
        self, connection: Any, token: str, *, now: datetime | None = None
    ) -> StaffPrincipal:
        session_id, secret = _parse_session_token(token)
        timestamp = now or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.oidc_subject, u.status, u.authorization_version,
                       s.authorization_version,
                       s.mfa_verified, s.idle_expires_at, s.absolute_expires_at, s.revoked_at,
                       s.idle_timeout_seconds
                FROM staff_sessions s JOIN staff_users u ON u.id = s.staff_user_id
                WHERE s.id = %s AND s.secret_hash = %s
                FOR UPDATE OF s
                """,
                (session_id, _secret_hash(secret)),
            )
            row = cursor.fetchone()
            if row is None:
                raise IdentityStateError("invalid session")
            (
                user_id,
                subject,
                status,
                user_version,
                session_version,
                mfa,
                idle,
                absolute,
                revoked,
                idle_timeout_seconds,
            ) = row
            if (
                status != "ACTIVE"
                or revoked is not None
                or int(user_version) != int(session_version)
                or timestamp >= idle
                or timestamp >= absolute
            ):
                raise IdentityStateError("session is inactive, expired, or stale")
            roles = _active_roles(cursor, _uuid(user_id))
            if not bool(mfa):
                raise IdentityStateError("MFA proof is required for every staff session")
            next_idle_expiry = min(
                timestamp + timedelta(seconds=int(idle_timeout_seconds)), absolute
            )
            cursor.execute(
                """
                UPDATE staff_sessions
                SET last_seen_at = %s, idle_expires_at = %s
                WHERE id = %s AND revoked_at IS NULL
                """,
                (timestamp, next_idle_expiry, session_id),
            )
        return StaffPrincipal(_uuid(user_id), str(subject), roles, bool(mfa), session_id)

    def revoke_session(
        self,
        connection: Any,
        *,
        session_id: UUID,
        actor_id: UUID,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> None:
        timestamp = occurred_at or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT staff_user_id, revoked_at FROM staff_sessions WHERE id = %s FOR UPDATE",
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None or row[1] is not None:
                raise IdentityStateError("session does not exist or is already revoked")
            staff_user_id = _uuid(row[0])
            if staff_user_id != actor_id:
                _require_owner(cursor, actor_id)

            def revoke(change_cursor: Any) -> None:
                change_cursor.execute(
                    """
                    UPDATE staff_sessions SET revoked_at = %s
                    WHERE id = %s AND revoked_at IS NULL
                    """,
                    (timestamp, session_id),
                )
                if change_cursor.rowcount != 1:
                    raise IdentityStateError("session does not exist or is already revoked")

            commit_material_change(
                connection,
                _session_change(
                    session_id,
                    staff_user_id,
                    2,
                    "STAFF_SESSION_REVOKED",
                    {"session_id": str(session_id)},
                    "STAFF_SESSION_REVOKE",
                    actor_id,
                    correlation_id,
                    timestamp,
                ),
                revoke,
            )

    def list_live_sessions(
        self,
        connection: Any,
        *,
        staff_user_id: UUID,
        actor_id: UUID,
        now: datetime,
        limit: int = 50,
    ) -> LiveSessionList:
        """The sessions of one staff user that would authenticate at `now` (`SESSION-LIST-001`).

        "Live" is exactly `authenticate_session`'s test, read without touching a row: not revoked,
        inside both lifetimes, minted at the user's current authorization version, the account
        active, and MFA-proven (every session must be). A session that would be
        refused at its next request is not listed, because offering to sign out a device that is
        already signed out is a control that does nothing.

        Anyone may read their own. Anyone else's needs an active `OWNER_ADMIN`, re-read from the
        database here for the reason `revoke_session` gives: a session minted while its holder was
        an owner keeps claiming so after the role is gone. An unknown staff id is refused rather
        than answered with an empty list, which would read as "signed in nowhere".

        `now` is passed in, never read, so the boundary (`idle_expires_at > now`, the complement of
        the authenticator's `now >= idle_expires_at`) is testable at the exact instant.
        """
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if not 1 <= limit <= MAX_SESSION_LIST:
            raise ValueError(f"limit must be between 1 and {MAX_SESSION_LIST}")
        with connection.transaction(), connection.cursor() as cursor:
            if staff_user_id != actor_id:
                _require_owner(cursor, actor_id)
            cursor.execute("SELECT 1 FROM staff_users WHERE id = %s", (staff_user_id,))
            if cursor.fetchone() is None:
                raise IdentityStateError("staff user is missing")
            cursor.execute(
                """
                SELECT s.id, s.issued_at, s.last_seen_at, s.idle_expires_at, s.absolute_expires_at
                FROM staff_sessions s
                JOIN staff_users u ON u.id = s.staff_user_id
                WHERE s.staff_user_id = %s
                  AND s.revoked_at IS NULL
                  AND s.idle_expires_at > %s
                  AND s.absolute_expires_at > %s
                  AND u.status = 'ACTIVE'
                  AND s.authorization_version = u.authorization_version
                  AND s.mfa_verified
                ORDER BY s.last_seen_at DESC, s.id
                LIMIT %s
                """,
                (
                    staff_user_id,
                    now,
                    now,
                    limit + 1,
                ),
            )
            rows = cursor.fetchall()
        return LiveSessionList(
            staff_user_id=staff_user_id,
            sessions=tuple(
                LiveSession(
                    session_id=_uuid(row[0]),
                    issued_at=row[1],
                    last_seen_at=row[2],
                    idle_expires_at=row[3],
                    absolute_expires_at=row[4],
                )
                for row in rows[:limit]
            ),
            truncated=len(rows) > limit,
        )

    @staticmethod
    def _principal_for_subject(cursor: Any, subject: str, mfa_verified: bool) -> StaffPrincipal:
        cursor.execute("SELECT id, status FROM staff_users WHERE oidc_subject = %s", (subject,))
        row = cursor.fetchone()
        if row is None or row[1] != "ACTIVE":
            raise IdentityStateError("OIDC subject is not an active staff user")
        staff_id = _uuid(row[0])
        return StaffPrincipal(staff_id, subject, _active_roles(cursor, staff_id), mfa_verified)


def _staff_change(
    staff_id: UUID,
    version: int,
    event_type: str,
    payload: dict[str, object],
    action: str,
    actor_id: UUID | None,
    correlation_id: UUID,
    occurred_at: datetime,
) -> MaterialChange:
    return _change(
        "STAFF_USER",
        staff_id,
        staff_id,
        version,
        event_type,
        payload,
        action,
        actor_id,
        correlation_id,
        occurred_at,
    )


def _session_change(
    session_id: UUID,
    staff_id: UUID,
    version: int,
    event_type: str,
    payload: dict[str, object],
    action: str,
    actor_id: UUID | None,
    correlation_id: UUID,
    occurred_at: datetime,
) -> MaterialChange:
    return _change(
        "STAFF_SESSION",
        session_id,
        staff_id,
        version,
        event_type,
        payload,
        action,
        actor_id,
        correlation_id,
        occurred_at,
    )


def _change(
    aggregate_type: str,
    aggregate_id: UUID,
    staff_id: UUID,
    version: int,
    event_type: str,
    payload: dict[str, object],
    action: str,
    actor_id: UUID | None,
    correlation_id: UUID,
    occurred_at: datetime,
) -> MaterialChange:
    return MaterialChange(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=version,
        event_type=event_type,
        event_payload=payload,
        audit_action=action,
        actor_type="STAFF" if actor_id is not None else "BOOTSTRAP",
        actor_id=actor_id,
        correlation_id=correlation_id,
        outbox_events=(
            OutboxEvent(
                "staff.identity_changed.v1",
                {"staff_user_id": str(staff_id), "event_type": event_type},
                f"{aggregate_type.lower()}:{aggregate_id}:{event_type}:{correlation_id}",
            ),
        ),
        occurred_at=occurred_at,
    )


def _insert_staff_owner(
    cursor: Any, staff_id: UUID, subject: str, name: str, email: str | None, timestamp: datetime
) -> None:
    try:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, email, status, created_at)
            VALUES (%s, %s, %s, %s, 'ACTIVE', %s)
            """,
            (staff_id, subject, name, email, timestamp),
        )
    except UniqueViolation as error:
        # No savepoint: unlike `delivery_legs`, nothing here continues after the clash. The whole
        # transaction must unwind, and it does -- this only changes what the caller is told.
        raise StaffSubjectTakenError("this OIDC subject already belongs to a staff user") from error
    cursor.execute(
        """
        INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
        VALUES (%s, %s, %s, %s)
        """,
        (uuid4(), staff_id, StaffRole.OWNER_ADMIN, timestamp),
    )


def _insert_staff(
    cursor: Any, staff_id: UUID, subject: str, name: str, email: str | None, timestamp: datetime
) -> None:
    try:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, email, status, created_at)
            VALUES (%s, %s, %s, %s, 'ACTIVE', %s)
            """,
            (staff_id, subject, name, email, timestamp),
        )
    except UniqueViolation as error:
        # No savepoint: unlike `delivery_legs`, nothing here continues after the clash. The whole
        # transaction must unwind, and it does -- this only changes what the caller is told.
        raise StaffSubjectTakenError("this OIDC subject already belongs to a staff user") from error


def _assign_role(
    cursor: Any, staff_id: UUID, role: StaffRole, actor_id: UUID, timestamp: datetime
) -> None:
    cursor.execute(
        """
        UPDATE staff_users SET authorization_version = authorization_version + 1
        WHERE id = %s AND status = 'ACTIVE'
        """,
        (staff_id,),
    )
    if cursor.rowcount != 1:
        raise IdentityStateError("staff user is missing or disabled")
    cursor.execute(
        """
        INSERT INTO staff_role_assignments (
            id, staff_user_id, role, assigned_at, assigned_by, revoked_at, revoked_by
        )
        VALUES (%s, %s, %s, %s, %s, NULL, NULL)
        ON CONFLICT (staff_user_id, role) DO UPDATE
        SET assigned_at = EXCLUDED.assigned_at, assigned_by = EXCLUDED.assigned_by,
            revoked_at = NULL, revoked_by = NULL
        """,
        (uuid4(), staff_id, role, timestamp, actor_id),
    )


def _revoke_role(
    cursor: Any, staff_id: UUID, role: StaffRole, actor_id: UUID, timestamp: datetime
) -> None:
    cursor.execute(
        """
        UPDATE staff_role_assignments SET revoked_at = %s, revoked_by = %s
        WHERE staff_user_id = %s AND role = %s AND revoked_at IS NULL
        """,
        (timestamp, actor_id, staff_id, role),
    )
    if cursor.rowcount != 1:
        raise IdentityStateError("role is not currently assigned")
    cursor.execute(
        """
        UPDATE staff_users SET authorization_version = authorization_version + 1
        WHERE id = %s AND status = 'ACTIVE'
        """,
        (staff_id,),
    )
    if cursor.rowcount != 1:
        raise IdentityStateError("staff user is missing or disabled")


def _disable_staff(cursor: Any, staff_id: UUID, timestamp: datetime) -> None:
    cursor.execute(
        """
        UPDATE staff_users
        SET status = 'DISABLED', disabled_at = %s, authorization_version = authorization_version + 1
        WHERE id = %s AND status = 'ACTIVE'
        """,
        (timestamp, staff_id),
    )
    if cursor.rowcount != 1:
        raise IdentityStateError("staff user is missing or already disabled")
    cursor.execute(
        "UPDATE staff_sessions SET revoked_at = %s WHERE staff_user_id = %s AND revoked_at IS NULL",
        (timestamp, staff_id),
    )


def _active_roles(cursor: Any, staff_id: UUID) -> frozenset[StaffRole]:
    cursor.execute(
        "SELECT role FROM staff_role_assignments WHERE staff_user_id = %s AND revoked_at IS NULL",
        (staff_id,),
    )
    return frozenset(StaffRole(str(row[0])) for row in cursor.fetchall())


def _authorization_version(cursor: Any, staff_id: UUID) -> int:
    cursor.execute("SELECT authorization_version FROM staff_users WHERE id = %s", (staff_id,))
    row = cursor.fetchone()
    if row is None:
        raise IdentityStateError("staff user is missing")
    return int(row[0])


def _lock_staff_version(cursor: Any, staff_id: UUID) -> int:
    cursor.execute(
        "SELECT authorization_version, status FROM staff_users WHERE id = %s FOR UPDATE",
        (staff_id,),
    )
    row = cursor.fetchone()
    if row is None or row[1] != "ACTIVE":
        raise IdentityStateError("staff user is missing or disabled")
    return int(row[0])


def _require_owner(cursor: Any, actor_id: UUID) -> None:
    cursor.execute(
        """
        SELECT 1
        FROM staff_users u
        JOIN staff_role_assignments r ON r.staff_user_id = u.id
        WHERE u.id = %s AND u.status = 'ACTIVE'
          AND r.role = %s AND r.revoked_at IS NULL
        """,
        (actor_id, StaffRole.OWNER_ADMIN),
    )
    if cursor.fetchone() is None:
        raise IdentityPermissionError("owner authorization is required")


#: `pg_advisory_xact_lock` key for "the set of active owners may shrink in this transaction".
OWNER_SET_LOCK_KEY = 0x4E54_4C41_4F57_4E52  # "NTLAOWNR"


def _serialize_owner_changes(cursor: Any) -> None:
    """One authority change at a time (`AUTHZ-LIFECYCLE-001`).

    The last-owner guard reads *other* owners' rows but locks only the target's. Measured on
    PostgreSQL 16 with two owners acting on each other at once, both past the guard before either
    wrote: two disables both committed and left **no active owner** (a defect `disable_staff` had
    before this item), as did a revoke racing a disable; two revokes deadlocked on the `revoked_by`
    foreign key and one was aborted. With this lock every pairing leaves one owner and the loser is
    refused as no longer an owner. Held until commit; each later statement is a fresh READ
    COMMITTED snapshot, so the second transaction re-checks its actor against what the first wrote.
    """

    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (OWNER_SET_LOCK_KEY,))


def _require_owner_survives_disable(cursor: Any, staff_id: UUID) -> None:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM staff_role_assignments
            WHERE staff_user_id = %s AND role = %s AND revoked_at IS NULL
        )
        """,
        (staff_id, StaffRole.OWNER_ADMIN),
    )
    row = cursor.fetchone()
    target_is_owner = bool(row and row[0])
    if not target_is_owner:
        return
    cursor.execute(
        """
        SELECT 1
        FROM staff_users u
        JOIN staff_role_assignments r ON r.staff_user_id = u.id
        WHERE u.id <> %s AND u.status = 'ACTIVE'
          AND r.role = %s AND r.revoked_at IS NULL
        LIMIT 1
        """,
        (staff_id, StaffRole.OWNER_ADMIN),
    )
    if cursor.fetchone() is None:
        raise IdentityStateError("cannot disable the last active owner")


def _required_text(value: str, label: str, maximum_length: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum_length:
        raise IdentityStateError(f"invalid {label}")
    return normalized


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _parse_session_token(value: str) -> tuple[UUID, str]:
    try:
        session_text, secret = value.split(".", 1)
        if not secret:
            raise ValueError
        return UUID(session_text), secret
    except (ValueError, AttributeError) as error:
        raise IdentityStateError("invalid session") from error


def _uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
