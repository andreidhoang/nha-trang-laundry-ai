from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from nha_trang_laundry_api import main as api_main
from nha_trang_laundry_api.auth import (
    AuthenticationAttemptLimiter,
    AuthenticationError,
    AuthSettings,
    IdentityPlatformVerifier,
    SigningKey,
)
from nha_trang_laundry_api.main import app, current_principal, get_identity_service
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole

OWNER_ID = UUID("00000000-0000-0000-0000-000000000701")
STAFF_ID = UUID("00000000-0000-0000-0000-000000000702")
ORIGIN = "https://testserver"


class BrowserIdentityService:
    def exchange(self, token: str) -> tuple[str, datetime]:
        assert token == "signed-provider-token"
        return "server-generated-session", datetime.now(UTC) + timedelta(hours=1)

    def current_principal(self, token: str) -> StaffPrincipal:
        assert token == "server-generated-session"
        return StaffPrincipal(
            OWNER_ID,
            "signed-provider-subject",
            frozenset({StaffRole.OWNER_ADMIN}),
            True,
        )


class RejectingIdentityService:
    def exchange(self, token: str) -> tuple[str, datetime]:
        del token
        raise AuthenticationError("invalid staff identity token")


class StaffCreationService:
    def create_staff(
        self, *, oidc_subject: str, display_name: str, email: str | None, actor_id: UUID
    ) -> UUID:
        del oidc_subject, display_name, email, actor_id
        return STAFF_ID


@dataclass(frozen=True)
class StaticSigningKey:
    key: Any


class StaticJwksClient:
    def __init__(self, key: Any) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> SigningKey:
        assert token
        return StaticSigningKey(self._key)


class JwksHandler(BaseHTTPRequestHandler):
    payload = b"{}"

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def _settings() -> AuthSettings:
    return AuthSettings(
        database_url="postgresql://unused",
        oidc_issuer="https://identity.example.test",
        oidc_audience="staff-console",
        oidc_jwks_url="https://identity.example.test/.well-known/jwks.json",
        oidc_mfa_claim="amr.level",
        oidc_mfa_value="mfa",
    )


def _claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "iss": "https://identity.example.test",
        "aud": "staff-console",
        "sub": "named-staff-subject",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "amr": {"level": "mfa"},
    }
    values.update(overrides)
    return values


def test_security_headers_trusted_host_and_request_size_are_enforced() -> None:
    client = TestClient(app, base_url=ORIGIN)

    healthy = client.get("/healthz")
    untrusted_host = client.get("/healthz", headers={"Host": "evil.example"})
    oversized = client.post(
        "/internal/v1/auth/session",
        headers={"Origin": ORIGIN, "Authorization": "Bearer ignored"},
        content=b"x" * 262_145,
    )

    assert healthy.status_code == 200
    assert healthy.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert healthy.headers["X-Content-Type-Options"] == "nosniff"
    assert healthy.headers["X-Frame-Options"] == "DENY"
    assert healthy.headers["Strict-Transport-Security"].startswith("max-age=31536000")
    assert untrusted_host.status_code == 400
    assert untrusted_host.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert oversized.status_code == 413


def test_browser_mutation_requires_exact_origin_and_double_submit_csrf() -> None:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        OWNER_ID, "owner-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    app.dependency_overrides[get_identity_service] = StaffCreationService
    client = TestClient(app, base_url=ORIGIN)
    client.cookies.set("staff_session", "opaque-session")
    client.cookies.set("staff_csrf", "csrf-token-with-at-least-thirty-two-bytes")
    body = {"oidc_subject": "new-staff", "display_name": "New Staff"}
    try:
        missing_origin = client.post("/internal/v1/staff", json=body)
        cross_origin = client.post(
            "/internal/v1/staff",
            headers={
                "Origin": "https://evil.example",
                "X-CSRF-Token": "csrf-token-with-at-least-thirty-two-bytes",
            },
            json=body,
        )
        missing_token = client.post("/internal/v1/staff", headers={"Origin": ORIGIN}, json=body)
        accepted = client.post(
            "/internal/v1/staff",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": "csrf-token-with-at-least-thirty-two-bytes",
            },
            json=body,
        )
    finally:
        app.dependency_overrides.clear()

    assert missing_origin.status_code == 403
    assert cross_origin.status_code == 403
    assert missing_token.status_code == 403
    assert accepted.status_code == 201
    assert accepted.headers["Cache-Control"] == "no-store"


def test_identity_exchange_replaces_session_and_issues_strict_csrf_cookie() -> None:
    app.dependency_overrides[get_identity_service] = BrowserIdentityService
    client = TestClient(app, base_url=ORIGIN)
    client.cookies.set(
        "staff_session", "attacker-fixed-session", domain="testserver.local", path="/"
    )
    try:
        response = client.post(
            "/internal/v1/auth/session",
            headers={"Origin": ORIGIN, "Authorization": "Bearer signed-provider-token"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert (
        client.cookies.get("staff_session", domain="testserver.local", path="/")
        == "server-generated-session"
    )
    csrf_token = response.headers["X-CSRF-Token"]
    assert len(csrf_token) >= 32
    assert client.cookies.get("staff_csrf", domain="testserver.local", path="/") == csrf_token
    cookies = response.headers.get_list("set-cookie")
    assert all("Secure" in value and "SameSite=strict" in value for value in cookies)


def test_identity_exchange_origin_and_abuse_limit_fail_closed() -> None:
    original_limiter = api_main._AUTH_LIMITER
    api_main._AUTH_LIMITER = AuthenticationAttemptLimiter(limit=2, window_seconds=60)
    app.dependency_overrides[get_identity_service] = RejectingIdentityService
    client = TestClient(app, base_url=ORIGIN)
    try:
        no_origin = client.post(
            "/internal/v1/auth/session", headers={"Authorization": "Bearer invalid"}
        )
        first = client.post(
            "/internal/v1/auth/session",
            headers={"Origin": ORIGIN, "Authorization": "Bearer invalid"},
        )
        second = client.post(
            "/internal/v1/auth/session",
            headers={"Origin": ORIGIN, "Authorization": "Bearer invalid"},
        )
        throttled = client.post(
            "/internal/v1/auth/session",
            headers={"Origin": ORIGIN, "Authorization": "Bearer invalid"},
        )
    finally:
        api_main._AUTH_LIMITER = original_limiter
        app.dependency_overrides.clear()

    assert no_origin.status_code == 403
    assert first.status_code == 401
    assert second.status_code == 401
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) > 0


def test_real_jwt_decoder_accepts_only_bound_rs256_oidc_claims() -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    verifier = IdentityPlatformVerifier(_settings(), StaticJwksClient(private_key.public_key()))

    token = jwt.encode(_claims(), private_key, algorithm="RS256", headers={"kid": "staff-key"})
    identity = verifier.verify(token)

    assert identity.subject == "named-staff-subject"
    assert identity.mfa_verified is True

    invalid_claims = [
        _claims(iss="https://wrong-issuer.example"),
        _claims(aud="wrong-audience"),
        _claims(exp=datetime.now(UTC) - timedelta(seconds=1)),
        {key: value for key, value in _claims().items() if key != "exp"},
        {key: value for key, value in _claims().items() if key != "sub"},
    ]
    for claims in invalid_claims:
        invalid = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "staff-key"})
        with pytest.raises(AuthenticationError, match="invalid staff identity token"):
            verifier.verify(invalid)

    other_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    bad_signature = jwt.encode(
        _claims(), other_key, algorithm="RS256", headers={"kid": "staff-key"}
    )
    wrong_algorithm = jwt.encode(
        _claims(), "symmetric-test-key-at-least-32-bytes", algorithm="HS256"
    )
    with pytest.raises(AuthenticationError, match="invalid staff identity token"):
        verifier.verify(bad_signature)
    with pytest.raises(AuthenticationError, match="invalid staff identity token"):
        verifier.verify(wrong_algorithm)

    no_mfa = jwt.encode(
        _claims(amr={"level": "password"}),
        private_key,
        algorithm="RS256",
        headers={"kid": "staff-key"},
    )
    assert verifier.verify(no_mfa).mfa_verified is False


def test_oidc_boundary_fetches_jwks_and_rejects_unknown_signing_key() -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    assert isinstance(jwk, dict)
    jwk.update({"kid": "live-jwks-key", "use": "sig", "alg": "RS256"})
    JwksHandler.payload = json.dumps({"keys": [jwk]}).encode("utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), JwksHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = _settings().model_copy(
        update={"oidc_jwks_url": f"http://127.0.0.1:{server.server_port}/jwks.json"}
    )
    verifier = IdentityPlatformVerifier(settings)
    try:
        valid = jwt.encode(
            _claims(), private_key, algorithm="RS256", headers={"kid": "live-jwks-key"}
        )
        unknown = jwt.encode(
            _claims(), private_key, algorithm="RS256", headers={"kid": "unknown-key"}
        )
        assert verifier.verify(valid).subject == "named-staff-subject"
        with pytest.raises(AuthenticationError, match="invalid staff identity token"):
            verifier.verify(unknown)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
