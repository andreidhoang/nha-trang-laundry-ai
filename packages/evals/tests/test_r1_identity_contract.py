"""`SHOP-IDENTITY-001`: the identity provider `DEC-011` chose, and the trap it nearly walked into.

The realm is a committed artifact rather than click-ops, so these are assertions about a file. The
behavioural claims -- that a second factor is required and that the token says so in a form the
API's verifier can read -- were measured against a real Keycloak with a real browser; what is
pinned here is the configuration that produced those measurements, so a later edit that would
change them fails loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REALM = json.loads((ROOT / "deploy/production/keycloak/realm-nhatrang.json").read_text("utf-8"))
CADDYFILE = (ROOT / "deploy/production/Caddyfile").read_text("utf-8")


def test_the_realm_carries_no_user_and_no_secret() -> None:
    """What makes the file committable, and what makes gitleaks over it meaningful."""

    assert REALM["users"] == []
    serialized = json.dumps(REALM)
    assert "secret" not in serialized.casefold() or all(
        "secret" not in str(key).casefold() for client in REALM["clients"] for key in client
    )
    for client in REALM["clients"]:
        assert client["publicClient"] is True
        assert "secret" not in client


def test_the_second_factor_is_required_rather_than_conditional() -> None:
    """A conditional second factor is skipped for an account that has not configured one.

    That is exactly the account an attacker would choose, so the OTP execution is REQUIRED inside a
    subflow whose only condition is the level of authentication being asked for -- and the client
    asks for it on every request.
    """

    flows = {flow["alias"]: flow for flow in REALM["authenticationFlows"]}
    assert REALM["browserFlow"] == "browser-mfa-required"

    second_factor = flows["browser-mfa-required-second-factor"]
    executions = {
        execution["authenticator"]: execution
        for execution in second_factor["authenticationExecutions"]
        if "authenticator" in execution
    }
    assert executions["auth-otp-form"]["requirement"] == "REQUIRED"
    assert executions["conditional-level-of-authentication"]["requirement"] == "REQUIRED"

    config = {entry["alias"]: entry["config"] for entry in REALM["authenticatorConfig"]}
    assert config["loa-two"]["loa-condition-level"] == "2"

    client = REALM["clients"][0]
    # Server-enforced: the client cannot decline the level it is configured to require.
    assert client["attributes"]["minimum.acr.value"] == "mfa"


def test_the_mfa_claim_is_one_the_verifier_can_actually_read() -> None:
    """The sharpest trap in this item, and the first attempt fell into it.

    `_claim_value` in `apps/api/.../auth.py` walks a dotted path and returns the value only
    `if isinstance(current, str)`. Measured against a real Keycloak with a real browser login:

        amr = None          -- absent entirely; binding to it yields None on every sign-in
        acr = '1'           -- with no level-of-authentication condition configured
        acr = 'mfa'         -- with the condition and the loa map below

    A binding that yields None makes `mfa_verified` false, and every `OWNER_ADMIN`, `OPS_APPROVER`,
    `ACCOUNTANT` and `AUDITOR` sign-in then dies in `identity.py` with a message the browser sees as
    a generic 401 -- indistinguishable from a bad token. Safe, and unbearable to debug.
    """

    loa_map = json.loads(REALM["attributes"]["acr.loa.map"])
    assert loa_map == {"mfa": 2}
    assert json.loads(REALM["clients"][0]["attributes"]["acr.loa.map"]) == {"mfa": 2}


def test_the_client_is_public_with_pkce_and_no_password_grant() -> None:
    client = REALM["clients"][0]
    assert client["clientId"] == "staff-console"
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert client["standardFlowEnabled"] is True
    # A browser holding a password would defeat the second factor entirely.
    assert client["directAccessGrantsEnabled"] is False
    assert client["implicitFlowEnabled"] is False
    assert client["serviceAccountsEnabled"] is False
    # Absolute, and pinned against the compose default so the two cannot drift.
    #
    # Two measurements produced this. A *relative* redirect URI with no root URL makes the authorize
    # endpoint answer "Invalid parameter: redirect_uri" and render no login form at all. And
    # `${env.VAR}` is not substituted during realm import, so a root URL written that way makes
    # Keycloak refuse to start -- "Invalid client staff-console: Root URL is not a valid URL" --
    # after the container has already reported itself Up.
    origin = "https://console.giatlasachcong.lan:8443"
    assert client["rootUrl"] == origin
    assert client["redirectUris"] == [f"{origin}/signin/callback.html"]
    assert client["webOrigins"] == [origin]
    compose = (ROOT / "compose.r1.yaml").read_text("utf-8")
    assert "R1_CONSOLE_HOST:-console.giatlasachcong.lan" in compose


def test_the_realm_refuses_plain_http_and_self_registration() -> None:
    assert REALM["sslRequired"] == "all"
    assert REALM["registrationAllowed"] is False
    assert REALM["resetPasswordAllowed"] is False
    assert REALM["bruteForceProtected"] is True
    # Every account configures a second factor on first sign-in.
    totp = next(
        action for action in REALM["requiredActions"] if action["providerId"] == "CONFIGURE_TOTP"
    )
    assert totp["enabled"] is True and totp["defaultAction"] is True


def test_only_the_realms_browser_endpoints_are_reachable_from_the_shop() -> None:
    """The admin console and the master realm are not on the shop network at all."""

    assert "handle /idp/realms/nhatrang/*" in CADDYFILE
    assert "handle /idp/resources/*" in CADDYFILE
    # Everything else under /idp is a 404 rather than a proxy.
    assert "handle /idp* {" in CADDYFILE
    assert "respond 404" in CADDYFILE


def test_the_sign_in_pages_carry_their_own_policy() -> None:
    """They are served by the proxy, so they never pass through `BrowserSecurityMiddleware`.

    The policy the API would have given them is therefore stated explicitly here, and must be at
    least as strict. `connect-src 'self'` is what permits the token exchange -- Keycloak is
    same-origin -- and forbids anything else.
    """

    assert "root * /srv/signin" in CADDYFILE
    for directive in (
        "default-src 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self'",
        "connect-src 'self'",
    ):
        assert directive in CADDYFILE, directive
    assert "X-Content-Type-Options nosniff" in CADDYFILE
    assert 'Cache-Control "no-store"' in CADDYFILE


def test_the_console_points_at_the_sign_in_surface() -> None:
    """`app.js` refuses a cross-origin sign-in path, so this must stay same-origin and absolute."""

    index = (ROOT / "apps/web/index.html").read_text("utf-8")
    assert '<meta name="console-signin-path" content="/signin/">' in index
