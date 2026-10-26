from __future__ import annotations

import asyncio
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
    AuthenticationUnavailable,
    AuthSettings,
    IdentityPlatformVerifier,
    SigningKey,
    StaffIdentityService,
    identity_token_digest,
)
from nha_trang_laundry_api.main import app, current_principal, get_identity_service
from nha_trang_laundry_api.security import RequestSizeLimitMiddleware
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from starlette.types import Message, Receive, Scope, Send

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


@pytest.mark.parametrize("declared_length", [None, "1"])
@pytest.mark.parametrize("body_size", [262_144, 262_145])
def test_streamed_json_enforces_actual_body_limit_before_the_staff_write(
    declared_length: str | None, body_size: int
) -> None:
    class RecordingStaffCreationService(StaffCreationService):
        calls = 0

        def create_staff(
            self, *, oidc_subject: str, display_name: str, email: str | None, actor_id: UUID
        ) -> UUID:
            self.calls += 1
            return super().create_staff(
                oidc_subject=oidc_subject,
                display_name=display_name,
                email=email,
                actor_id=actor_id,
            )

    service = RecordingStaffCreationService()
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        OWNER_ID, "owner-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    app.dependency_overrides[get_identity_service] = lambda: service
    client = TestClient(app, base_url=ORIGIN)
    client.cookies.set("staff_session", "opaque-session")
    client.cookies.set("staff_csrf", "csrf-token-with-at-least-thirty-two-bytes")
    headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": "csrf-token-with-at-least-thirty-two-bytes",
        "Content-Type": "application/json",
    }
    if declared_length is not None:
        headers["Content-Length"] = declared_length
    body = json.dumps({"oidc_subject": "new-staff", "display_name": "New Staff"}).encode()
    body += b" " * (body_size - len(body))
    try:
        response = client.post(
            "/internal/v1/staff",
            headers=headers,
            content=(body[index : index + 4096] for index in range(0, len(body), 4096)),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.request.headers.get("Content-Length") == declared_length
    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert response.headers["Cache-Control"] == "no-store"
    if body_size > 262_144:
        assert response.status_code == 413
        assert response.json() == {"detail": "request body is too large"}
        assert service.calls == 0
    else:
        assert response.status_code == 201
        assert service.calls == 1


@pytest.mark.parametrize("last_chunk", [b"cd", b"cde"])
def test_raw_asgi_stream_counts_chunks_without_a_fastapi_exception_handler(
    last_chunk: bytes,
) -> None:
    handled: list[bool] = []
    sent: list[Message] = []
    incoming = iter(
        [
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": last_chunk, "more_body": False},
        ]
    )

    async def receive() -> Message:
        return next(incoming)

    async def send(message: Message) -> None:
        sent.append(message)

    async def consume(scope: Scope, receive: Receive, send: Send) -> None:
        del scope
        while (await receive()).get("more_body", False):
            pass
        handled.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    asyncio.run(
        RequestSizeLimitMiddleware(consume, max_bytes=4)(
            {"type": "http", "method": "POST", "path": "/", "headers": []}, receive, send
        )
    )
    assert sent[0]["status"] == (413 if last_chunk == b"cde" else 200)
    assert handled == ([] if last_chunk == b"cde" else [True])


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


@pytest.mark.parametrize(
    "neighbour",
    [
        'consent={"a":1}',  # a JSON-valued cookie another app on the same site set
        "pref=a b",  # an unquoted space
        'tracking="unterminated',  # a stray quote
    ],
)
def test_a_cookie_the_strict_parser_rejects_does_not_switch_csrf_off(neighbour: str) -> None:
    """The middleware and the session lookup must agree on whether a session cookie is present.

    `BrowserSecurityMiddleware` decided "is there a session?" with `http.cookies.SimpleCookie`,
    which discards the WHOLE header when any cookie in it is malformed, while authentication reads
    the same header with Starlette's lenient parser and finds the session. So one odd neighbouring
    cookie made the middleware skip both the Origin and the double-submit checks for a request the
    API then authenticated -- fail open, against invariant 11. Reproduced by the API reviewer with
    the real middleware: a cross-origin POST went from 403 to 200.
    """

    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        OWNER_ID, "owner-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    app.dependency_overrides[get_identity_service] = StaffCreationService
    client = TestClient(app, base_url=ORIGIN)
    cookie = f"{neighbour}; staff_session=opaque-session"
    body = {"oidc_subject": "new-staff", "display_name": "New Staff"}
    try:
        cross_origin = client.post(
            "/internal/v1/staff",
            headers={"Cookie": cookie, "Origin": "https://evil.example"},
            json=body,
        )
        no_token = client.post(
            "/internal/v1/staff", headers={"Cookie": cookie, "Origin": ORIGIN}, json=body
        )
    finally:
        app.dependency_overrides.clear()

    assert cross_origin.status_code == 403
    assert no_token.status_code == 403


@pytest.mark.parametrize(
    ("cookie_token", "header_token"),
    [
        (b"a" * 32, b"a" * 31 + b"\xe9"),
        (b"a" * 31 + b"\xe9", b"a" * 32),
        (b"a" * 31 + b"\xe9", b"a" * 31 + b"\xe9"),
    ],
)
def test_non_ascii_csrf_tokens_are_rejected_before_authentication(
    cookie_token: bytes, header_token: bytes
) -> None:
    """Raw HTTP headers may contain non-ASCII bytes; token comparison must still deny safely."""

    def authentication_must_not_run() -> StaffPrincipal:
        raise AssertionError("malformed CSRF token reached authentication")

    app.dependency_overrides[current_principal] = authentication_must_not_run
    client = TestClient(app, base_url=ORIGIN, raise_server_exceptions=False)
    try:
        response = client.post(
            "/internal/v1/staff",
            headers=[
                (b"cookie", b"staff_session=opaque-session; staff_csrf=" + cookie_token),
                (b"origin", ORIGIN.encode("ascii")),
                (b"x-csrf-token", header_token),
            ],
            json={"oidc_subject": "new-staff", "display_name": "New Staff"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert response.headers["Cache-Control"] == "no-store"


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


def test_an_id_token_is_a_sign_in_that_just_happened_not_a_credential_to_keep() -> None:
    """`AUTHZ-LIFECYCLE-001`: our own age bound on `iat`, whatever `exp` the issuer chose."""

    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    now = datetime.now(UTC)
    verifier = IdentityPlatformVerifier(
        _settings(), StaticJwksClient(private_key.public_key()), clock=lambda: now
    )

    def signed(**overrides: object) -> str:
        return jwt.encode(
            _claims(**overrides), private_key, algorithm="RS256", headers={"kid": "staff-key"}
        )

    fresh = signed(iat=now - timedelta(seconds=299))
    identity = verifier.verify(fresh)
    assert identity.token_digest == identity_token_digest(fresh)
    assert len(identity.token_digest) == 64 and fresh not in identity.token_digest

    # Issued six minutes ago but told by the issuer it lives an hour: still refused here.
    stale = signed(iat=now - timedelta(seconds=361), exp=now + timedelta(hours=1))
    # Claims to be issued in the future (refused by `jwt.decode` itself).
    future = signed(iat=datetime.now(UTC) + timedelta(minutes=2))
    for token in (stale, future):
        with pytest.raises(AuthenticationError, match="invalid staff identity token"):
            verifier.verify(token)


@pytest.mark.parametrize("issued_at", [-(10**100), -1e100, -62_135_596_801])
def test_signed_out_of_range_issue_time_is_refused_before_session_creation(
    issued_at: int | float,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    verifier = IdentityPlatformVerifier(_settings(), StaticJwksClient(private_key.public_key()))
    token = jwt.encode(
        _claims(iat=issued_at), private_key, algorithm="RS256", headers={"kid": "staff-key"}
    )

    def no_database(_: str) -> Any:
        raise AssertionError("an invalid issue time must be refused before session creation")

    service = StaffIdentityService(_settings(), verifier=verifier, connection_factory=no_database)
    app.dependency_overrides[get_identity_service] = lambda: service
    original_limiter = api_main._AUTH_LIMITER
    api_main._AUTH_LIMITER = AuthenticationAttemptLimiter(limit=2, window_seconds=60)
    try:
        response = TestClient(app, base_url=ORIGIN).post(
            "/internal/v1/auth/session",
            headers={"Origin": ORIGIN, "Authorization": f"Bearer {token}"},
        )
    finally:
        api_main._AUTH_LIMITER = original_limiter
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json() == {"detail": "identity exchange rejected"}
    assert "set-cookie" not in response.headers


def test_every_spelling_of_one_signed_token_is_the_same_sign_in() -> None:
    """Noncanonical signatures are refused; signing-input aliases still bind to one digest."""

    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    verifier = IdentityPlatformVerifier(_settings(), StaticJwksClient(private_key.public_key()))
    token = jwt.encode(_claims(), private_key, algorithm="RS256", headers={"kid": "staff-key"})
    head, payload, signature = token.split(".")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last = alphabet.index(signature[-1])
    # 2048 signature bits over 342 characters: the last character's low 4 bits are spare.
    spellings = {
        f"{head}.{payload}.{signature[:-1]}{alphabet[(last & 0b110000) | spare]}"
        for spare in range(16)
    }
    assert len(spellings) == 16
    spellings.add(f"{token}==")

    assert verifier.verify(token).token_digest == identity_token_digest(token)

    def no_database(_: str) -> Any:
        raise AssertionError("a noncanonical token must be refused before session creation")

    service = StaffIdentityService(_settings(), verifier=verifier, connection_factory=no_database)
    # PyJWT 2.14+ rejects noncanonical compact JWS (GHSA-hxm8-2xgr-2p9m). The previous
    # assertion required those spellings to authenticate, undoing that security expectation.
    for spelling in spellings - {token}:
        with pytest.raises(AuthenticationError, match="invalid staff identity token"):
            service.exchange(spelling)

    # Independently retain the one-sign-in binding even for spellings the verifier rejects.
    digests = {identity_token_digest(spelling) for spelling in spellings}

    assert digests == {identity_token_digest(token)}


@pytest.mark.parametrize("seconds", [0, -1, 3601])
def test_an_unbounded_token_age_is_a_configuration_error(seconds: int) -> None:
    settings = _settings()
    settings.oidc_max_token_age_seconds = seconds
    with pytest.raises(AuthenticationUnavailable, match="token age"):
        settings.require_identity_configuration()


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
