"""Fail-closed HTTP controls for the same-origin staff browser boundary."""

from __future__ import annotations

import hmac
from http.cookies import SimpleCookie

from nha_trang_laundry_observability import SafeStructuredLogger
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nha_trang_laundry_api.auth import AuthSettings

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSP = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
    "form-action 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; manifest-src 'self'; worker-src 'self'"
)
_LOGGER = SafeStructuredLogger()


class RequestSizeLimitMiddleware:
    """Reject declared or streamed request bodies beyond the configured byte ceiling."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("request size limit must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        try:
            declared = None if content_length is None else int(content_length)
        except ValueError:
            await _reject(scope, send, 400, "invalid Content-Length")
            return
        if declared is not None and (declared < 0 or declared > self.max_bytes):
            await _reject(scope, send, 413, "request body is too large")
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await _reject(scope, send, 413, "request body is too large")


class BrowserSecurityMiddleware:
    """Apply security headers and double-submit CSRF/origin checks for cookie authority."""

    def __init__(self, app: ASGIApp, *, settings: AuthSettings) -> None:
        self.app = app
        self.allowed_origins = frozenset(settings.allowed_origins())
        self.session_cookie_name = settings.staff_session_cookie_name
        self.csrf_cookie_name = settings.staff_csrf_cookie_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        method = str(scope["method"]).upper()
        path = str(scope["path"])
        cookies = _cookies(headers.get("cookie"))
        has_session = bool(cookies.get(self.session_cookie_name))
        identity_exchange = path == "/internal/v1/auth/session" and method == "POST"

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["Content-Security-Policy"] = CSP
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["X-Frame-Options"] = "DENY"
                response_headers["Referrer-Policy"] = "no-referrer"
                response_headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
                )
                response_headers["Cross-Origin-Opener-Policy"] = "same-origin"
                response_headers["Cross-Origin-Resource-Policy"] = "same-origin"
                if path.startswith("/internal/"):
                    response_headers["Cache-Control"] = "no-store"
                if scope.get("scheme") == "https":
                    response_headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        if identity_exchange or (
            path.startswith("/internal/") and method in UNSAFE_METHODS and has_session
        ):
            origin = headers.get("origin")
            if origin not in self.allowed_origins:
                _record_rejection("ORIGIN_REJECTED")
                await _reject(scope, secure_send, 403, "untrusted browser origin")
                return
        if (
            path.startswith("/internal/")
            and method in UNSAFE_METHODS
            and has_session
            and not identity_exchange
        ):
            cookie_token = cookies.get(self.csrf_cookie_name, "")
            header_token = headers.get("x-csrf-token", "")
            if (
                len(cookie_token) < 32
                or len(cookie_token) > 200
                or not hmac.compare_digest(cookie_token, header_token)
            ):
                _record_rejection("CSRF_REJECTED")
                await _reject(scope, secure_send, 403, "CSRF validation failed")
                return

        await self.app(scope, receive, secure_send)


class RequestBodyTooLarge(Exception):
    pass


def _cookies(value: str | None) -> dict[str, str]:
    parsed = SimpleCookie()
    if value:
        parsed.load(value)
    return {key: morsel.value for key, morsel in parsed.items()}


async def _reject(scope: Scope, send: Send, status_code: int, detail: str) -> None:
    response = JSONResponse({"detail": detail}, status_code=status_code)
    await response(scope, _empty_receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def _record_rejection(reason_code: str) -> None:
    _LOGGER.record(
        component="api",
        name="auth.browser_boundary",
        outcome="rejected",
        fields={"reason_code": reason_code},
    )
