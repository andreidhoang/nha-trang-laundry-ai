"""Fail-closed staff authentication boundary for Identity Platform tokens and sessions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import psycopg
from nha_trang_laundry_db.identity import (
    IdentityRepository,
    IdentityStateError,
    StaffPrincipal,
    StaffRole,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthenticationError(ValueError):
    """Raised when an external identity token is malformed or not acceptable."""


class AuthenticationUnavailable(RuntimeError):
    """Raised when the environment lacks required identity configuration."""


class AuthSettings(BaseSettings):
    """Environment-only auth settings; secrets never enter source control or logs."""

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_mfa_claim: str | None = None
    oidc_mfa_value: str | None = None
    staff_session_cookie_name: str = "staff_session"
    staff_session_idle_hours: int = 8
    staff_session_absolute_hours: int = 24

    def require_identity_configuration(self) -> None:
        values = (
            self.database_url,
            self.oidc_issuer,
            self.oidc_audience,
            self.oidc_jwks_url,
            self.oidc_mfa_claim,
            self.oidc_mfa_value,
        )
        if not all(values):
            raise AuthenticationUnavailable("staff identity is not configured")
        if not 0 < self.staff_session_idle_hours <= self.staff_session_absolute_hours:
            raise AuthenticationUnavailable("invalid staff session lifetime")


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    mfa_verified: bool


class IdentityPlatformVerifier:
    """Verify signed ID tokens and extract only identity plus configured MFA proof."""

    def __init__(self, settings: AuthSettings) -> None:
        settings.require_identity_configuration()
        self._issuer = str(settings.oidc_issuer)
        self._audience = str(settings.oidc_audience)
        self._mfa_claim = str(settings.oidc_mfa_claim)
        self._mfa_value = str(settings.oidc_mfa_value)
        self._jwks_client = jwt.PyJWKClient(str(settings.oidc_jwks_url))

    def verify(self, token: str) -> VerifiedIdentity:
        try:
            key = self._jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as error:
            raise AuthenticationError("invalid staff identity token") from error
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise AuthenticationError("identity token lacks a subject")
        return VerifiedIdentity(
            subject.strip(), _claim_value(claims, self._mfa_claim) == self._mfa_value
        )


class StaffIdentityService:
    """Exchange verified provider tokens for database-backed opaque staff sessions."""

    def __init__(
        self,
        settings: AuthSettings,
        verifier: IdentityPlatformVerifier | None = None,
        connection_factory: Callable[[str], Any] = psycopg.connect,
    ) -> None:
        settings.require_identity_configuration()
        self._settings = settings
        self._verifier = verifier or IdentityPlatformVerifier(settings)
        self._connection_factory = connection_factory
        self._repository = IdentityRepository()

    def exchange(self, id_token: str) -> tuple[str, datetime]:
        identity = self._verifier.verify(id_token)
        with self._connection_factory(str(self._settings.database_url)) as connection:
            session = self._repository.create_session(
                connection,
                oidc_subject=identity.subject,
                mfa_verified=identity.mfa_verified,
                correlation_id=uuid4(),
                idle_ttl=timedelta(hours=self._settings.staff_session_idle_hours),
                absolute_ttl=timedelta(hours=self._settings.staff_session_absolute_hours),
            )
        return session.value, session.expires_at

    def current_principal(self, session_token: str) -> StaffPrincipal:
        with self._connection_factory(str(self._settings.database_url)) as connection:
            return self._repository.authenticate_session(
                connection, session_token, now=datetime.now(UTC)
            )

    def logout(self, session_token: str, principal: StaffPrincipal) -> None:
        if principal.session_id is None:
            raise IdentityStateError("session identity is required")
        with self._connection_factory(str(self._settings.database_url)) as connection:
            self._repository.revoke_session(
                connection,
                session_id=principal.session_id,
                actor_id=principal.staff_user_id,
                correlation_id=uuid4(),
            )

    def assign_role(self, staff_user_id: UUID, role: StaffRole, actor_id: UUID) -> None:
        with self._connection_factory(str(self._settings.database_url)) as connection:
            self._repository.assign_role(
                connection,
                staff_user_id=staff_user_id,
                role=role,
                actor_id=actor_id,
                correlation_id=uuid4(),
            )

    def create_staff(
        self, *, oidc_subject: str, display_name: str, email: str | None, actor_id: UUID
    ) -> UUID:
        with self._connection_factory(str(self._settings.database_url)) as connection:
            return self._repository.create_staff(
                connection,
                oidc_subject=oidc_subject,
                display_name=display_name,
                email=email,
                actor_id=actor_id,
                correlation_id=uuid4(),
            )

    def disable_staff(self, staff_user_id: UUID, actor_id: UUID) -> None:
        with self._connection_factory(str(self._settings.database_url)) as connection:
            self._repository.disable_staff(
                connection,
                staff_user_id=staff_user_id,
                actor_id=actor_id,
                correlation_id=uuid4(),
            )

    def revoke_session(self, session_id: UUID, actor_id: UUID) -> None:
        with self._connection_factory(str(self._settings.database_url)) as connection:
            self._repository.revoke_session(
                connection,
                session_id=session_id,
                actor_id=actor_id,
                correlation_id=uuid4(),
            )


def _claim_value(claims: Mapping[str, Any], claim_path: str) -> str | None:
    current: Any = claims
    for part in claim_path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current if isinstance(current, str) else None
