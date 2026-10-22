"""Deployment smoke must refuse a live process whose database cannot answer."""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from scripts import staging_smoke


def _run(
    monkeypatch: pytest.MonkeyPatch,
    ready: staging_smoke.SmokeResponse,
) -> list[str]:
    headers = dict.fromkeys(
        (
            "strict-transport-security",
            "content-security-policy",
            "x-content-type-options",
            "x-frame-options",
            "referrer-policy",
        ),
        "present",
    )
    responses = {
        "/healthz": staging_smoke.SmokeResponse(200, headers, b'{"status":"ok"}'),
        "/readyz": ready,
        "/staff/": staging_smoke.SmokeResponse(
            200,
            headers,
            '<title>Bảng vận hành</title><meta name="console-signin-path">'.encode(),
        ),
        "/internal/v1/session": staging_smoke.SmokeResponse(
            401, {**headers, "cache-control": "no-store"}, b"{}"
        ),
    }
    observed: list[str] = []

    def request(
        target: str,
        path: str,
        context: ssl.SSLContext,
        *,
        allow_http_error: bool = False,
    ) -> staging_smoke.SmokeResponse:
        assert target == "https://synthetic.invalid:8443"
        assert isinstance(context, ssl.SSLContext)
        if path == "/readyz":
            assert allow_http_error
        observed.append(path)
        return responses.get(path, staging_smoke.SmokeResponse(404, {}, b"{}"))

    monkeypatch.setattr(
        staging_smoke, "validate_target", lambda *_: "https://synthetic.invalid:8443"
    )
    monkeypatch.setattr(
        ssl,
        "create_default_context",
        lambda **_: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )
    monkeypatch.setattr(staging_smoke, "_request", request)
    staging_smoke.run_smoke("https://synthetic.invalid:8443", Path("synthetic-ca"))
    return observed


def _ready_headers() -> dict[str, str]:
    return {
        "strict-transport-security": "present",
        "content-security-policy": "present",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "no-referrer",
        "cache-control": "no-store",
    }


def test_live_process_with_unavailable_database_is_not_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="database readiness"):
        _run(
            monkeypatch,
            staging_smoke.SmokeResponse(
                503, _ready_headers(), b'{"status":"unavailable","dependency":"database"}'
            ),
        )


@pytest.mark.parametrize("body", [b'{"status":"ok"}', b'{"status":"ready","extra":true}'])
def test_success_status_without_exact_readiness_contract_is_refused(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    with pytest.raises(RuntimeError, match="database readiness"):
        _run(monkeypatch, staging_smoke.SmokeResponse(200, _ready_headers(), body))


def test_cacheable_readiness_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = _ready_headers()
    del headers["cache-control"]
    with pytest.raises(RuntimeError, match="cacheable"):
        _run(monkeypatch, staging_smoke.SmokeResponse(200, headers, b'{"status":"ready"}'))


def test_healthy_database_continues_through_all_private_boundary_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = _run(
        monkeypatch,
        staging_smoke.SmokeResponse(200, _ready_headers(), b'{"status":"ready"}'),
    )
    assert observed == [
        "/healthz",
        "/readyz",
        "/staff/",
        "/internal/v1/session",
        "/openclaw",
        "/webhook",
        "/public/v1/messages",
    ]
