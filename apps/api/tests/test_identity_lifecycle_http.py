"""`AUTHZ-LIFECYCLE-001` over HTTP: real cookies, real signed ID tokens, real PostgreSQL.

Nothing overrides `current_principal`. The exchange test signs a genuine RS256 ID token and posts
it to `POST /internal/v1/auth/session` twice, so what is proved is the property an attacker holding
a leaked token meets -- the second exchange is refused -- not a statement about a repository call.
"""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from nha_trang_laundry_api import main as api_main
from nha_trang_laundry_api.auth import (
    AuthenticationAttemptLimiter,
    AuthSettings,
    IdentityPlatformVerifier,
    StaffIdentityService,
)
from nha_trang_laundry_api.main import app, get_identity_service
from nha_trang_laundry_db.identity import IdentityRepository, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations

ORIGIN = "http://testserver"
CSRF = "csrf-token-with-at-least-thirty-two-bytes-of-entropy"
ISSUER = "https://idp.invalid/realms/test"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65_537, key_size=2048)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@dataclass(frozen=True)
class _Key:
    key: Any


class _StaticJwks:
    def get_signing_key_from_jwt(self, token: str) -> _Key:
        del token
        return _Key(PRIVATE_KEY.public_key())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = AuthSettings(
        database_url=_database_url(),
        oidc_issuer=ISSUER,
        oidc_audience="staff-console",
        oidc_jwks_url=f"{ISSUER}/jwks",
        oidc_mfa_claim="acr",
        oidc_mfa_value="mfa",
    )
    verifier = IdentityPlatformVerifier(settings, _StaticJwks())
    app.dependency_overrides[get_identity_service] = lambda: StaffIdentityService(
        settings, verifier=verifier
    )
    # A fresh limiter, so failures other tests recorded against "testclient" cannot throttle this.
    monkeypatch.setattr(
        api_main, "_AUTH_LIMITER", AuthenticationAttemptLimiter(limit=30, window_seconds=300)
    )
    try:
        yield TestClient(app, base_url=ORIGIN)
    finally:
        app.dependency_overrides.clear()


def _person(connection: Any, *roles: StaffRole) -> tuple[UUID, str]:
    staff_id = uuid4()
    subject = f"lifecycle-http-{staff_id}"
    now = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, email, status, created_at)
            VALUES (%s, %s, 'Người thử', NULL, 'ACTIVE', %s)
            """,
            (staff_id, subject, now),
        )
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_id, role.value, now),
            )
    return staff_id, subject


def _headers(connection: Any, subject: str) -> dict[str, str]:
    device = IdentityRepository().create_session(
        connection, oidc_subject=subject, mfa_verified=True, correlation_id=uuid4()
    )
    return {
        "Cookie": f"staff_session={device.value}; staff_csrf={CSRF}",
        "Origin": ORIGIN,
        "X-CSRF-Token": CSRF,
    }


def _id_token(subject: str) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "aud": "staff-console",
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "acr": "mfa",
        # Keycloak gives every issued token a random `jti` (and `sid`); without one, two sign-ins
        # in the same second would be byte-identical tokens and correctly count as one.
        "jti": str(uuid4()),
    }
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": "k"})


def test_a_leaked_id_token_cannot_be_exchanged_a_second_time(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    _, subject = _person(connection, StaffRole.OPERATOR)
    token = _id_token(subject)
    exchange = {"Origin": ORIGIN, "Authorization": f"Bearer {token}"}

    first = client.post("/internal/v1/auth/session", headers=exchange)
    client.cookies.clear()
    replay = client.post("/internal/v1/auth/session", headers=exchange)

    assert first.status_code == 200, first.text
    assert replay.status_code == 401
    # The same opaque refusal as any other rejected identity: nothing tells the holder why.
    assert replay.json() == {"detail": "identity exchange rejected"}
    assert "staff_session" not in replay.cookies
    # A new sign-in by the same person -- a new token -- still works.
    fresh = client.post(
        "/internal/v1/auth/session",
        headers={"Origin": ORIGIN, "Authorization": f"Bearer {_id_token(subject)}"},
    )
    assert fresh.status_code == 200, fresh.text


def test_the_owner_revokes_a_role_over_http_and_the_holder_is_signed_out(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    _, owner_subject = _person(connection, StaffRole.OWNER_ADMIN)
    approver, approver_subject = _person(connection, StaffRole.OPS_APPROVER, StaffRole.OPERATOR)
    owner = _headers(connection, owner_subject)
    approver_device = _headers(connection, approver_subject)
    assert client.get("/internal/v1/session", headers=approver_device).status_code == 200

    revoked = client.delete(f"/internal/v1/staff/{approver}/roles/OPS_APPROVER", headers=owner)
    again = client.delete(f"/internal/v1/staff/{approver}/roles/OPS_APPROVER", headers=owner)

    assert revoked.status_code == 204, revoked.text
    assert again.status_code == 409
    assert client.get("/internal/v1/session", headers=approver_device).status_code == 401
    session = client.get("/internal/v1/session", headers=_headers(connection, approver_subject))
    assert session.json()["roles"] == ["OPERATOR"]


def test_revoking_a_role_needs_the_owner_and_never_removes_the_last_one(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    owner_id, owner_subject = _person(connection, StaffRole.OWNER_ADMIN)
    _, approver_subject = _person(connection, StaffRole.OPS_APPROVER)

    by_approver = client.delete(
        f"/internal/v1/staff/{owner_id}/roles/OWNER_ADMIN",
        headers=_headers(connection, approver_subject),
    )
    last_owner = client.delete(
        f"/internal/v1/staff/{owner_id}/roles/OWNER_ADMIN",
        headers=_headers(connection, owner_subject),
    )
    unknown_role = client.delete(
        f"/internal/v1/staff/{owner_id}/roles/SUPERUSER",
        headers=_headers(connection, owner_subject),
    )

    assert by_approver.status_code == 403
    assert by_approver.json() == {"detail": "operation denied"}
    assert last_owner.status_code == 409
    assert unknown_role.status_code == 422
