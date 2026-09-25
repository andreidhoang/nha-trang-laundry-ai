"""Fail-closed staff authentication boundary for Identity Platform tokens and sessions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from re import fullmatch
from threading import Lock
from time import monotonic
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import jwt
from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.identity import (
    IdentityRepository,
    IdentityStateError,
    LiveSessionList,
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

    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        secrets_dir="/run/secrets" if Path("/run/secrets").is_dir() else None,
    )

    database_url: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_mfa_claim: str | None = None
    oidc_mfa_value: str | None = None
    staff_session_cookie_name: str = "staff_session"
    staff_csrf_cookie_name: str = "staff_csrf"
    staff_session_idle_hours: int = 8
    staff_session_absolute_hours: int = 24
    staff_allowed_origins: str = (
        "https://localhost,http://localhost,http://localhost:8000,http://127.0.0.1:8000,"
        "https://testserver,http://testserver"
    )
    api_trusted_hosts: str = "localhost,127.0.0.1,testserver"
    api_max_request_bytes: int = 262_144
    #: How many *failed* identity exchanges one network source may make in a window. Successes do
    #: not accumulate -- `exchange_identity_token` calls `AuthenticationAttemptLimiter.reset` on a
    #: verified token -- so this bounds a run of failures and nothing else. Measured 2026-09-09:
    #: eight legitimate sign-ins in a row all answer 200; five failures then answer 429, and so
    #: does a ninth sign-in carrying a perfectly good token.
    #:
    #: That last sentence is why 5 was wrong here. Every tablet reaches the API through Caddy, so
    #: `request.client.host` is the proxy for all of them and they share one bucket. Five failures
    #: from anywhere in the shop -- one expired token, or Keycloak hiccupping while three tablets
    #: retry -- locks out everybody for up to five minutes, including staff whose credentials are
    #: fine, with customers at the counter. The bucket cannot be made finer safely: the only
    #: candidates are `X-Forwarded-For`, which a client controls, and the `sub` of an unverified
    #: token, which an attacker varies freely. Either would let the throttle be evaded outright.
    #:
    #: So the number moves instead, and what it costs is proportionate to what it buys. Thirty
    #: signature verifications in five minutes is negligible work, and R1 has no public ingress at
    #: all (ADR-0007: Zone P does not exist yet), so the credential-stuffing this bounds cannot
    #: reach it from outside the shop's own network. When public ingress arrives this number should
    #: be revisited together with the bucket key, and the reasoning above is the record of why it
    #: is what it is.
    auth_attempt_limit: int = 30
    auth_attempt_window_seconds: int = 300

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

    def allowed_origins(self) -> tuple[str, ...]:
        origins = _comma_separated(self.staff_allowed_origins)
        if not origins or any(not _valid_origin(value) for value in origins):
            raise AuthenticationUnavailable("staff allowed origins are invalid")
        return origins

    def trusted_hosts(self) -> tuple[str, ...]:
        hosts = _comma_separated(self.api_trusted_hosts)
        if not hosts or any(fullmatch(r"[A-Za-z0-9.-]+", host) is None for host in hosts):
            raise AuthenticationUnavailable("API trusted hosts are invalid")
        return hosts


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    mfa_verified: bool


class SigningKey(Protocol):
    @property
    def key(self) -> Any: ...


class SigningKeyProvider(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


@dataclass(frozen=True, slots=True)
class AuthenticationLimit:
    allowed: bool
    retry_after_seconds: int


class AuthenticationAttemptLimiter:
    """Bound authentication work per direct network source without retaining raw tokens."""

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("authentication limit and window must be positive")
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock
        self._attempts: dict[str, tuple[float, int]] = {}
        self._lock = Lock()

    def acquire(self, source_key: str) -> AuthenticationLimit:
        now = self._clock()
        with self._lock:
            started_at, attempts = self._attempts.get(source_key, (now, 0))
            if now - started_at >= self._window_seconds:
                started_at, attempts = now, 0
            if attempts >= self._limit:
                retry_after = max(1, int(self._window_seconds - (now - started_at)))
                return AuthenticationLimit(False, retry_after)
            self._attempts[source_key] = (started_at, attempts + 1)
        return AuthenticationLimit(True, 0)

    def reset(self, source_key: str) -> None:
        with self._lock:
            self._attempts.pop(source_key, None)


class IdentityPlatformVerifier:
    """Verify signed ID tokens and extract only identity plus configured MFA proof."""

    def __init__(
        self,
        settings: AuthSettings,
        jwks_client: SigningKeyProvider | None = None,
    ) -> None:
        settings.require_identity_configuration()
        self._issuer = str(settings.oidc_issuer)
        self._audience = str(settings.oidc_audience)
        self._mfa_claim = str(settings.oidc_mfa_claim)
        self._mfa_value = str(settings.oidc_mfa_value)
        self._jwks_client = jwks_client or jwt.PyJWKClient(str(settings.oidc_jwks_url))

    def verify(self, token: str) -> VerifiedIdentity:
        if not token or len(token) > 16_384:
            raise AuthenticationError("invalid staff identity token")
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
        connection_factory: Callable[[str], Any] = application_connect,
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

    def list_sessions(self, staff_user_id: UUID, actor_id: UUID, limit: int) -> LiveSessionList:
        """`SESSION-LIST-001`: one person's live sessions; the repository decides who may read."""
        with self._connection_factory(str(self._settings.database_url)) as connection:
            return self._repository.list_live_sessions(
                connection,
                staff_user_id=staff_user_id,
                actor_id=actor_id,
                now=datetime.now(UTC),
                limit=limit,
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


def _comma_separated(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


def _valid_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return False
    if parsed.scheme == "https":
        return bool(parsed.hostname)
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "testserver"}
