"""TLS-verifying smoke check for the private staging operator boundary."""

from __future__ import annotations

import argparse
import json
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

EXPECTED_HOST: Final = "staging.internal"
EXPECTED_PORT: Final = 8443
MAX_RESPONSE_BYTES: Final = 1_048_576


@dataclass(frozen=True, slots=True)
class SmokeResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def validate_target(base_url: str, ca_file: Path) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != EXPECTED_HOST
        or parsed.port != EXPECTED_PORT
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("staging URL must be exactly https://staging.internal:8443")
    if not ca_file.is_file() or ca_file.is_symlink():
        raise ValueError("a regular private staging CA file is required")
    return f"https://{EXPECTED_HOST}:{EXPECTED_PORT}"


def run_smoke(base_url: str, ca_file: Path) -> None:
    target = validate_target(base_url, ca_file)
    context = ssl.create_default_context(cafile=str(ca_file))

    health = _request(target, "/healthz", context)
    if health.status != 200 or json.loads(health.body) != {"status": "ok"}:
        raise RuntimeError("private API health smoke failed")
    _require_browser_headers(health.headers, require_no_store=False)

    staff = _request(target, "/staff/", context)
    if staff.status != 200 or b"Staff Operations" not in staff.body:
        raise RuntimeError("private staff shell smoke failed")
    _require_browser_headers(staff.headers, require_no_store=False)

    unauthenticated_session = _request(
        target, "/internal/v1/session", context, allow_http_error=True
    )
    if unauthenticated_session.status != 401:
        raise RuntimeError("private staff session boundary did not fail closed")
    _require_browser_headers(unauthenticated_session.headers, require_no_store=True)

    for prohibited_path in ("/openclaw", "/webhook", "/public/v1/messages"):
        response = _request(target, prohibited_path, context, allow_http_error=True)
        if response.status != 404:
            raise RuntimeError("a prohibited public route is reachable")


def _request(
    base_url: str,
    path: str,
    context: ssl.SSLContext,
    *,
    allow_http_error: bool = False,
) -> SmokeResponse:
    request = Request(f"{base_url}{path}", headers={"User-Agent": "private-staging-smoke/1"})
    try:
        response = urlopen(request, context=context, timeout=5)
    except HTTPError as error:
        if not allow_http_error:
            raise
        response = error
    with response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RuntimeError("staging smoke response exceeded its bound")
        return SmokeResponse(
            status=int(response.status),
            headers={name.casefold(): value for name, value in response.headers.items()},
            body=body,
        )


def _require_browser_headers(headers: dict[str, str], *, require_no_store: bool) -> None:
    required = {
        "strict-transport-security",
        "content-security-policy",
        "x-content-type-options",
        "x-frame-options",
        "referrer-policy",
    }
    if not required <= headers.keys():
        raise RuntimeError("private browser security headers are incomplete")
    if require_no_store and "no-store" not in headers.get("cache-control", "").casefold():
        raise RuntimeError("private staff content is cacheable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ca-file", required=True, type=Path)
    arguments = parser.parse_args()
    run_smoke(arguments.base_url, arguments.ca_file)
    print("Private TLS staging smoke passed; no release authority was granted.")


if __name__ == "__main__":
    main()
