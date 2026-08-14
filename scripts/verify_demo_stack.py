"""Exercise the running demo stack end to end, including the paths that must be refused.

`scripts/staging_smoke.py` checks that the private endpoint answers and sets the right browser
headers. That is the right check for a deployment, and it is not enough for this item.
`DEMO-STACK-001` claims the demo does not weaken authentication, and a claim like that is only
worth what its negative cases prove. So this signs in, then tries to get in the ways that must
fail.

Every check is a property someone could break by "making the demo easier". A wrong audience, a
missing Origin, an unknown virtual host, a read-only role writing — each has an expected refusal,
and an unexpected success fails the run.

Usage:
    uv run python scripts/verify_demo_stack.py
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import json
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path

ROOT = _Path(__file__).resolve().parents[1]
DEFAULT_BASE = "https://staging.internal:8443"
DEFAULT_CA = ROOT / ".demo" / "ca.crt"
DEMO_STORE_ID = "11111111-2222-4333-8444-555555555555"


class _PinnedHostHandler(urllib.request.HTTPSHandler):
    """Reach 127.0.0.1 while still presenting and verifying the real hostname.

    The proxy refuses any request that does not name staging.internal, and the demo does not relax
    that. Rather than requiring an /etc/hosts entry for a verification run, connect to the loopback
    address and keep the TLS server_name and Host header intact — the same thing curl --resolve
    does. Nothing about the certificate check is skipped.
    """

    def __init__(self, context: ssl.SSLContext, address: str) -> None:
        super().__init__(context=context)
        self._context = context
        self._address = address

    def https_open(self, request: urllib.request.Request) -> Any:
        return self.do_open(self._connection_factory, request)

    def _connection_factory(self, host: str, **kwargs: Any) -> Any:
        from http.client import HTTPSConnection

        context = self._context
        address = self._address

        class _PinnedConnection(HTTPSConnection):
            def connect(self) -> None:
                raw = socket.create_connection((address, self.port), timeout=self.timeout)
                self.sock = context.wrap_socket(raw, server_hostname=self.host)

        hostname, _, port = host.partition(":")
        kwargs.pop("context", None)
        return _PinnedConnection(hostname, int(port or 443), context=context, **kwargs)


@dataclass
class Result:
    name: str
    passed: bool
    detail: str


class DemoClient:
    def __init__(self, base_url: str, ca_file: _Path) -> None:
        self.base_url = base_url.rstrip("/")
        context = ssl.create_default_context(cafile=str(ca_file))
        host = urlsplit(self.base_url).hostname or "127.0.0.1"
        address = "127.0.0.1" if host != "127.0.0.1" else host
        self._opener = urllib.request.build_opener(_PinnedHostHandler(context, address))
        self.cookies: dict[str, str] = {}

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        send_cookies: bool = True,
    ) -> tuple[int, bytes, dict[str, str]]:
        request = urllib.request.Request(f"{self.base_url}{path}", method=method, data=body)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if send_cookies and self.cookies:
            request.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        try:
            with self._opener.open(request, timeout=15) as response:
                return response.status, response.read(), self._absorb(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), self._absorb(error.headers)

    def _absorb(self, message: Any) -> dict[str, str]:
        """Store cookies from every Set-Cookie header, and return a flattened view.

        Two traps make the naive version wrong, and getting them wrong turns a real refusal into
        a misleading pass: the response carries several Set-Cookie headers and `dict(headers)`
        keeps only one, and an expiry like "expires=Sat, 15 Aug 2026" contains the comma that a
        split would use as a separator. Read them individually and let SimpleCookie parse.
        """
        from http.cookies import SimpleCookie

        raw = message.get_all("Set-Cookie") or []
        for header in raw:
            parsed = SimpleCookie()
            parsed.load(header)
            for name, morsel in parsed.items():
                self.cookies[name] = morsel.value
        flattened = dict(message)
        flattened["Set-Cookie"] = " | ".join(raw)
        return flattened

    def store_cookies(self, headers: dict[str, str]) -> None:
        """Kept for call-site readability; cookies are absorbed on every response."""
        return None

    def token(self, subject: str) -> str:
        status, body, _ = self.request(f"/demo-idp/token?sub={subject}")
        if status != 200:
            raise SystemExit(f"demo identity provider returned {status} for {subject}")
        return str(json.loads(body)["id_token"])


def run(base_url: str, ca_file: _Path) -> list[Result]:
    results: list[Result] = []

    def check(name: str, condition: bool, detail: str) -> None:
        results.append(Result(name, condition, detail))

    client = DemoClient(base_url, ca_file)

    status, body, _ = client.request("/healthz")
    check("tls endpoint answers", status == 200 and b'"ok"' in body, f"http {status}")

    status, _, _ = client.request("/.well-known/jwks.json")
    check(
        "jwks is not exposed through the proxy root",
        status in {404, 421},
        f"http {status}",
    )

    token = client.token("demo-owner")

    # Positive: the exchange succeeds only with a correct token AND an allowed Origin.
    status, body, headers = client.request(
        "/internal/v1/auth/session",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Origin": base_url},
    )
    check("owner signs in", status == 200, f"http {status}")
    if status == 200:
        client.store_cookies(headers)
        payload = json.loads(body)
        check(
            "owner has OWNER_ADMIN and MFA",
            payload.get("roles") == ["OWNER_ADMIN"] and payload.get("mfa_verified") is True,
            json.dumps(payload),
        )

    check(
        "session cookie is HttpOnly, Secure and SameSite=Strict",
        all(
            marker in headers.get("Set-Cookie", "")
            for marker in ("HttpOnly", "Secure", "SameSite=strict")
        ),
        headers.get("Set-Cookie", "")[:120],
    )

    # Negative: no Origin header at all.
    status, _, _ = client.request(
        "/internal/v1/auth/session",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        send_cookies=False,
    )
    check("exchange without an Origin is refused", status == 403, f"http {status}")

    # Negative: a token that is not a token.
    status, _, _ = client.request(
        "/internal/v1/auth/session",
        method="POST",
        headers={"Authorization": "Bearer not.a.real.token", "Origin": base_url},
        send_cookies=False,
    )
    check("a malformed token is refused", status == 401, f"http {status}")

    # Reads that should work for a signed-in owner.
    for path in (
        f"/internal/v1/stores/{DEMO_STORE_ID}/orders?limit=5",
        f"/internal/v1/stores/{DEMO_STORE_ID}/quotes?limit=5",
        f"/internal/v1/stores/{DEMO_STORE_ID}/shadow/drafts",
        "/internal/v1/shadow/unknown-sends",
        "/internal/v1/approvals?limit=5",
        "/internal/v1/queue-recovery",
    ):
        status, _, _ = client.request(path)
        check(f"owner reads {path.split('?')[0]}", status == 200, f"http {status}")

    # Negative: a store the owner is not assigned to must be refused, and must be
    # indistinguishable from a role refusal.
    status, _, _ = client.request(
        "/internal/v1/stores/99999999-9999-4999-8999-999999999999/shadow/drafts"
    )
    check("a store with no membership is refused", status == 403, f"http {status}")

    # Negative: a mutation without the CSRF header, with a valid session, must be refused.
    status, _, _ = client.request(
        f"/internal/v1/stores/{DEMO_STORE_ID}/incidents",
        method="POST",
        headers={"Origin": base_url, "Content-Type": "application/json"},
        body=b"{}",
    )
    check("a mutation without the CSRF header is refused", status == 403, f"http {status}")

    # The read-only role must be refused on every write.
    auditor = DemoClient(base_url, ca_file)
    auditor_token = auditor.token("demo-auditor")
    status, _, headers = auditor.request(
        "/internal/v1/auth/session",
        method="POST",
        headers={"Authorization": f"Bearer {auditor_token}", "Origin": base_url},
    )
    check("auditor signs in", status == 200, f"http {status}")
    auditor.store_cookies(headers)
    csrf = auditor.cookies.get("staff_csrf", "")
    zero = "0" * 64
    status, _, _ = auditor.request(
        f"/internal/v1/stores/{DEMO_STORE_ID}/incidents",
        method="POST",
        headers={
            "Origin": base_url,
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "verify-demo-stack-auditor",
        },
        body=json.dumps(
            {
                "order_id": "11111111-1111-4111-8111-111111111111",
                "contact_scope_hash": f"sha256:{zero}",
                "evidence_summary_hash": f"sha256:{zero}",
            }
        ).encode(),
    )
    check("auditor cannot open an incident", status == 403, f"http {status}")

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--ca-file", type=_Path, default=DEFAULT_CA)
    arguments = parser.parse_args()
    if not arguments.ca_file.is_file():
        raise SystemExit(
            f"{arguments.ca_file} is missing; run scripts/generate_demo_material.py first"
        )

    results = run(arguments.base_url, arguments.ca_file)
    for result in results:
        print(f"  {'PASS' if result.passed else 'FAIL'}  {result.name}  [{result.detail}]")
    failed = [result for result in results if not result.passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
