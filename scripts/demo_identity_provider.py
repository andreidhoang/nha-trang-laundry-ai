"""A real OIDC-shaped issuer for the local demo. Development only.

The staff API verifies identity tokens with `jwt.decode(..., algorithms=["RS256"], audience=...,
issuer=..., options={"require": ["exp", "iat", "sub"]})` against a JWKS it fetches over the network,
and requires a configured MFA claim before any mutation. That verifier is not modified, subclassed
or bypassed by this file, and it must not be: an authentication check that is switched off for the
demo stops being evidence that it works.

So this serves an actual JWKS document and mints actual RS256 tokens. A token with the wrong
audience, the wrong issuer or no MFA claim is rejected by the API exactly as a malformed token from
a real identity platform would be, and the tests for this item assert that.

It is deliberately impossible to select from the production overlay: the service exists only in
compose.demo.yaml, and its name says what it is.

Usage (normally started by compose):
    python demo_identity_provider.py --port 9000
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt

TOKEN_LIFETIME = timedelta(minutes=30)

# The demo's synthetic staff. Subjects must match what scripts/seed_demo_data.py creates; they are
# arbitrary strings standing in for an identity platform's stable subject identifier.
DEMO_SUBJECTS: tuple[tuple[str, str, str], ...] = (
    ("demo-owner", "Chủ cửa hàng · OWNER_ADMIN", "toàn quyền, gồm cấp quyền cửa hàng"),
    ("demo-operations", "Nhân viên vận hành · OPERATOR", "tạo đơn hàng, sự cố, duyệt bản nháp"),
    ("demo-approver", "Người duyệt · OPS_APPROVER", "hàng chờ duyệt và khôi phục hàng đợi"),
    ("demo-auditor", "Kiểm toán · AUDITOR", "chỉ đọc; mọi thao tác ghi phải bị từ chối"),
)

SIGN_IN_PAGE = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Đăng nhập demo</title>
<link rel="stylesheet" href="./style.css">
</head>
<body>
<main>
<p class="eyebrow">DEMO IDENTITY PROVIDER &middot; KHÔNG DÙNG CHO SẢN XUẤT</p>
<h1>Chọn tài khoản nhân viên</h1>
<p class="note">Máy chủ này phát hành token RS256 thật cho các chủ thể tổng hợp. API xác minh
chúng bằng đúng bộ kiểm tra dùng ở sản xuất — không có đường tắt nào được mở.</p>
<ul id="accounts"></ul>
<output id="result" aria-live="polite"></output>
</main>
<script src="./app.js"></script>
</body>
</html>
"""

SIGN_IN_STYLE = """:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; margin: 0; padding: 2rem; line-height: 1.5; }
main { max-width: 34rem; margin: 0 auto; }
.eyebrow { font-size: .75rem; letter-spacing: .08em; font-weight: 700; color: #b45309; margin: 0; }
h1 { font-size: 1.5rem; margin: .25rem 0 .75rem; }
.note { color: #57534e; font-size: .9rem; }
ul { list-style: none; padding: 0; display: grid; gap: .5rem; }
button { width: 100%; text-align: left; padding: .75rem 1rem; border: 1px solid #d6d3d1;
  border-radius: .5rem; background: transparent; font: inherit; cursor: pointer; color: inherit; }
button:hover { border-color: #b45309; }
button strong { display: block; }
button span { color: #78716c; font-size: .85rem; }
output { display: block; margin-top: 1rem; font-size: .9rem; }
output.error { color: #b91c1c; }
"""

# Inline script is blocked by the API's content security policy, and the demo page keeps the same
# discipline rather than making itself an exception.
SIGN_IN_SCRIPT = """const accounts = document.querySelector("#accounts");
const result = document.querySelector("#result");

const signIn = async (subject) => {
  result.className = "";
  result.textContent = "Đang lấy token…";
  try {
    const tokenResponse = await fetch(`./token?sub=${encodeURIComponent(subject)}`);
    if (!tokenResponse.ok) throw new Error(`IdP lỗi ${tokenResponse.status}`);
    const { id_token: idToken } = await tokenResponse.json();
    result.textContent = "Đang đổi lấy phiên staff…";
    const exchange = await fetch("/internal/v1/auth/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { Authorization: `Bearer ${idToken}`, Accept: "application/json" },
    });
    if (!exchange.ok) {
      const body = await exchange.json().catch(() => ({}));
      throw new Error(body.detail || `Đổi phiên thất bại (${exchange.status})`);
    }
    result.textContent = "Đã đăng nhập. Đang chuyển tới bảng vận hành…";
    window.location.assign("/staff/");
  } catch (error) {
    result.className = "error";
    result.textContent = error.message;
  }
};

fetch("./accounts")
  .then((response) => response.json())
  .then((items) => {
    accounts.replaceChildren();
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button";
      const name = document.createElement("strong");
      name.textContent = item.label;
      const description = document.createElement("span");
      description.textContent = item.description;
      button.append(name, description);
      button.addEventListener("click", () => signIn(item.subject));
      const entry = document.createElement("li");
      entry.append(button);
      accounts.append(entry);
    }
  })
  .catch(() => {
    result.className = "error";
    result.textContent = "Không tải được danh sách tài khoản.";
  });
"""


class DemoIdentityProvider:
    """Mints RS256 tokens the production verifier accepts, for synthetic subjects only."""

    def __init__(
        self,
        *,
        private_key_pem: str,
        jwks: dict[str, Any],
        issuer: str,
        audience: str,
        mfa_claim: str,
        mfa_value: str,
    ) -> None:
        self._private_key = private_key_pem
        self._jwks = jwks
        self._issuer = issuer
        self._audience = audience
        self._mfa_claim = mfa_claim
        self._mfa_value = mfa_value
        self._key_id = str(jwks["keys"][0]["kid"])

    @property
    def jwks(self) -> dict[str, Any]:
        return self._jwks

    def mint(self, subject: str, *, with_mfa: bool = True) -> str:
        if not subject or len(subject) > 200:
            raise ValueError("subject is required")
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": subject,
            "iat": int(now.timestamp()),
            "exp": int((now + TOKEN_LIFETIME).timestamp()),
        }
        if with_mfa:
            claims[self._mfa_claim] = self._mfa_value
        return jwt.encode(
            claims, self._private_key, algorithm="RS256", headers={"kid": self._key_id}
        )


def _read(path: str | None, name: str) -> str:
    if not path:
        raise SystemExit(f"{name} is required")
    return Path(path).read_text(encoding="utf-8").strip()


def build_handler(provider: DemoIdentityProvider) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "demo-idp"

        def log_message(self, fmt: str, *args: Any) -> None:
            # Requests carry a synthetic subject in the query string; there is nothing worth
            # logging and a quiet demo is easier to watch.
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            route = parsed.path.rstrip("/") or "/"
            if route == "/.well-known/jwks.json":
                self._send(200, json.dumps(provider.jwks).encode(), "application/jwk-set+json")
                return
            if route in {"/", "/index.html"}:
                self._send(200, SIGN_IN_PAGE.encode(), "text/html; charset=utf-8")
                return
            if route == "/style.css":
                self._send(200, SIGN_IN_STYLE.encode(), "text/css; charset=utf-8")
                return
            if route == "/app.js":
                self._send(200, SIGN_IN_SCRIPT.encode(), "text/javascript; charset=utf-8")
                return
            if route == "/accounts":
                payload = [
                    {"subject": subject, "label": label, "description": description}
                    for subject, label, description in DEMO_SUBJECTS
                ]
                self._send(200, json.dumps(payload).encode(), "application/json")
                return
            if route == "/token":
                subjects = parse_qs(parsed.query).get("sub", [])
                known = {subject for subject, _, _ in DEMO_SUBJECTS}
                if not subjects or subjects[0] not in known:
                    self._send(
                        404,
                        json.dumps({"detail": "unknown demo subject"}).encode(),
                        "application/json",
                    )
                    return
                token = provider.mint(subjects[0])
                self._send(200, json.dumps({"id_token": token}).encode(), "application/json")
                return
            self._send(404, b'{"detail":"not found"}', "application/json")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--private-key", default=os.environ.get("DEMO_IDP_PRIVATE_KEY"))
    parser.add_argument("--jwks", default=os.environ.get("DEMO_IDP_JWKS"))
    parser.add_argument("--issuer-file", default=os.environ.get("DEMO_IDP_ISSUER_FILE"))
    parser.add_argument("--audience-file", default=os.environ.get("DEMO_IDP_AUDIENCE_FILE"))
    parser.add_argument("--mfa-claim-file", default=os.environ.get("DEMO_IDP_MFA_CLAIM_FILE"))
    parser.add_argument("--mfa-value-file", default=os.environ.get("DEMO_IDP_MFA_VALUE_FILE"))
    arguments = parser.parse_args()

    provider = DemoIdentityProvider(
        private_key_pem=_read(arguments.private_key, "--private-key"),
        jwks=json.loads(_read(arguments.jwks, "--jwks")),
        issuer=_read(arguments.issuer_file, "--issuer-file"),
        audience=_read(arguments.audience_file, "--audience-file"),
        mfa_claim=_read(arguments.mfa_claim_file, "--mfa-claim-file"),
        mfa_value=_read(arguments.mfa_value_file, "--mfa-value-file"),
    )
    server = ThreadingHTTPServer(("0.0.0.0", arguments.port), build_handler(provider))
    print(f"demo identity provider listening on :{arguments.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
