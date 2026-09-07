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

#: The private staging host, kept because three COMPLETE items' evidence names this script and the
#: commands they recorded still have to mean what they meant. It is no longer a constraint.
EXPECTED_HOST: Final = "staging.internal"
EXPECTED_PORT: Final = 8443
MAX_RESPONSE_BYTES: Final = 1_048_576


@dataclass(frozen=True, slots=True)
class SmokeResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def validate_target(base_url: str, ca_file: Path) -> str:
    """Refuse a URL that could point this check somewhere it should not go, and nothing more.

    **The hostname used to be pinned to `staging.internal` and that made the check single-use.**
    `--base-url` was accepted and then compared to one literal, so deploy day's first verification
    step -- "prove it before letting staff in" -- could not be run against the console it exists to
    verify. It refused with `staging URL must be exactly https://staging.internal:8443`, on a page
    whose whole subject is a different host.

    Every structural check stays, because each one refuses a real way of pointing this somewhere
    else: plain `http`, credentials smuggled in the authority, a query or fragment, a path prefix
    that would make every request below relative to something. What the hostname pin was doing is
    done properly by TLS: the certificate must be valid for the name, signed by the CA the caller
    supplies, so reaching a host you have no CA for fails at the handshake. An explicit port is
    required because these consoles are never on 443 and a missing one is a typo rather than a
    default.
    """

    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError(
            "the target must be https://<host>:<port> with an explicit port and nothing else -- "
            f"no path, query, fragment or credentials. Got: {base_url}"
        )
    if not ca_file.is_file() or ca_file.is_symlink():
        raise ValueError(
            "a regular CA file is required. The console's name is internal, so no public CA can "
            "issue for it and there is nothing to fall back to."
        )
    return f"https://{parsed.hostname}:{parsed.port}"


def run_smoke(base_url: str, ca_file: Path) -> None:
    target = validate_target(base_url, ca_file)
    context = ssl.create_default_context(cafile=str(ca_file))

    health = _request(target, "/healthz", context)
    if health.status != 200 or json.loads(health.body) != {"status": "ok"}:
        raise RuntimeError("private API health smoke failed")
    _require_browser_headers(health.headers, require_no_store=False)

    # `b"Staff Operations"` was the marker here and that string exists nowhere in `apps/web/`: the
    # console was localised to Vietnamese and this was never updated. So this check failed against
    # every deployment, including the private staging host it was pinned to -- and because both
    # deploy runbooks invoked the script with arguments it does not take, nobody ever saw it fail.
    #
    # These two markers are chosen to stay true and to mean something. The title is what a tablet
    # shows; the sign-in path is what turns the signed-out screen's button on, and a shell served
    # without it explains the flow instead of offering a way in, which looks like a working console
    # and is not one.
    staff = _request(target, "/staff/", context)
    missing = [
        marker
        for marker in (
            b"<title>B\xe1\xba\xa3ng v\xe1\xba\xadn h\xc3\xa0nh</title>",
            b'name="console-signin-path"',
        )
        if marker not in staff.body
    ]
    if staff.status != 200 or missing:
        raise RuntimeError(
            f"staff shell smoke failed: http {staff.status}, missing markers {missing}"
        )
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
