"""Database-authoritative staff identity, roles, and revocable opaque sessions."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change


class StaffRole(StrEnum):
    OWNER_ADMIN = "OWNER_ADMIN"
    OPS_APPROVER = "OPS_APPROVER"
    OPERATOR = "OPERATOR"
    DRIVER = "DRIVER"
    ACCOUNTANT = "ACCOUNTANT"
    AUDITOR = "AUDITOR"


SENSITIVE_MFA_ROLES = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.ACCOUNTANT, StaffRole.AUDITOR}
)


class IdentityStateError(ValueError):
    """Raised when a database identity or session is inactive, stale, or invalid."""


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
    ) -> SessionToken:
        if idle_ttl <= timedelta() or absolute_ttl <= timedelta() or idle_ttl > absolute_ttl:
            raise IdentityStateError("invalid session lifetime")
        idle_timeout_seconds = int(idle_ttl.total_seconds())
        if idle_ttl != timedelta(seconds=idle_timeout_seconds):
            raise IdentityStateError("session idle lifetime must use whole seconds")
        timestamp = now or datetime.now(UTC)
        subject = _required_text(oidc_subject, "OIDC subject", 255)
        with connection.cursor() as cursor:
            principal = self._principal_for_subject(cursor, subject, mfa_verified)
        if SENSITIVE_MFA_ROLES & principal.roles and not mfa_verified:
            raise IdentityStateError("MFA proof is required for this staff role")

        session_id = uuid4()
        secret = secrets.token_urlsafe(32)
        secret_hash = _secret_hash(secret)
        expires_at = timestamp + absolute_ttl
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
            lambda cursor: cursor.execute(
                """
                INSERT INTO staff_sessions (
                    id, staff_user_id, secret_hash, authorization_version, mfa_verified, issued_at,
                    last_seen_at, idle_expires_at, absolute_expires_at, idle_timeout_seconds
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                ),
            ),
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
            if SENSITIVE_MFA_ROLES & roles and not bool(mfa):
                raise IdentityStateError("MFA proof is required for this staff role")
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
    cursor.execute(
        """
        INSERT INTO staff_users (id, oidc_subject, display_name, email, status, created_at)
        VALUES (%s, %s, %s, %s, 'ACTIVE', %s)
        """,
        (staff_id, subject, name, email, timestamp),
    )
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
    cursor.execute(
        """
        INSERT INTO staff_users (id, oidc_subject, display_name, email, status, created_at)
        VALUES (%s, %s, %s, %s, 'ACTIVE', %s)
        """,
        (staff_id, subject, name, email, timestamp),
    )


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
        raise IdentityStateError("owner authorization is required")


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
