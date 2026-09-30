"""CONSOLE-SHELL-009: the console's shell -- sign-out, navigation, and what a deploy does to it.

Findings of the 2026-09-29 review that live in the shell rather than in a screen, each invisible
to the tests that existed:

- **C1.** "Thoát" swallowed a failed sign-out and ended the session locally anyway, so a counter PC
  with the wifi down showed "Chưa đăng nhập" over a server session that was still alive -- and the
  next person's "Kiểm tra lại phiên" signed them in as the one who had left. A sign-out that did
  succeed left the last customer's order on the screen. `session.signOut()` is run here under Node
  against a stubbed `fetch`, for every answer the server can give, and the idle-expiry path -- which
  must keep the screen -- is run beside it.
- **C7.** `/staff/*` had no `Cache-Control`, and the service worker fetched the shell in the default
  cache mode and took over running pages by itself: after a deploy one tablet could run modules
  from two builds. The header is asserted on what the API serves, and the worker on its source.

The browser halves are in `scripts/verify_console_interaction.py` (its CONSOLE-SHELL-009
sections), because they need a real browser.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Any

import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.main import app

ROOT = pathlib.Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _run(script: str) -> dict[str, Any]:
    """Execute an ES module against a copy of the client tree and return what it prints as JSON."""
    with tempfile.TemporaryDirectory() as directory:
        target = pathlib.Path(directory)
        shutil.copytree(WEB / "src", target / "src")
        (target / "package.json").write_text(json.dumps({"type": "module"}))
        entry = target / "check.mjs"
        entry.write_text(script)
        result = subprocess.run(
            ["node", str(entry)], capture_output=True, text=True, cwd=target, timeout=60
        )
        assert result.returncode == 0, result.stderr
        answer = json.loads(result.stdout)
        assert isinstance(answer, dict), answer
        return answer


# --- C1: what "Thoát" does, for every answer ------------------------------------------------------

#: A browser for `core/session.js`: a CSRF cookie, a connectivity flag, a fetch that answers from a
#: table and records every call, and a `location` that records a navigation instead of making one.
_SESSION_HARNESS = """
const calls = [];
const assigned = [];
let answers = {};
globalThis.document = { cookie: "staff_csrf=" + "c".repeat(40) };
Object.defineProperty(globalThis, "navigator", {
  value: { onLine: true }, configurable: true, writable: true,
});
const stored = { staff_store_id: "11111111-2222-4333-8444-555555555555" };
globalThis.localStorage = {
  getItem: (key) => (key in stored ? stored[key] : null),
  setItem: (key, value) => { stored[key] = String(value); },
  removeItem: (key) => { delete stored[key]; },
};
globalThis.location = { assign: (url) => assigned.push(url) };
globalThis.fetch = async (path, init) => {
  calls.push({ path, method: init.method, key: init.headers["Idempotency-Key"] || null });
  const answer = answers[path.split("?")[0]];
  if (answer instanceof Error) throw answer;
  const [status, body] = answer || [404, { detail: "no stub" }];
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
};
const SESSION = {
  staff_user_id: "u-1", roles: ["OPERATOR"], mfa_verified: true, session_id: "s-1",
};
const STORES = { store_ids: ["11111111-2222-4333-8444-555555555555"], stores: [] };
const session = await import("./src/core/session.js");
const forgotten = [];
const unregister = typeof session.onSignOut === "function"
  ? session.onSignOut(() => forgotten.push("hand-off"))
  : null;
let notified = 0;
session.subscribe(() => { notified += 1; });

async function signIn() {
  answers["/internal/v1/session"] = [200, SESSION];
  answers["/internal/v1/stores"] = [200, STORES];
  await session.refresh();
}

function view(outcome) {
  const state = session.snapshot();
  return {
    signedOut: outcome ? outcome.signedOut === true : null,
    failed: outcome ? outcome.signedOut === false : null,
    errorKind: outcome && outcome.error ? outcome.error.kind : null,
    principal: state.principal ? state.principal.staffUserId : null,
    status: state.status,
    stateSignedOut: state.signedOut === true,
    storeId: state.storeId,
    stored: stored.staff_store_id || null,
    logoutCalls: calls.filter((call) => call.path === "/internal/v1/auth/logout").length,
    logoutKeyed: calls
      .filter((call) => call.path === "/internal/v1/auth/logout")
      .every((call) => Boolean(call.key)),
    forgotten: forgotten.length,
    notified,
    assigned: [...assigned],
  };
}
"""


#: A same-origin issuer's end-session URL, as `_end_session_url()` builds it on R1.
_ISSUER_LOGOUT = "/idp/realms/nhatrang/logout?x=1"


def _sign_out_with(setup: str) -> dict[str, Any]:
    """Sign in, apply `setup` (answers, connectivity), press "Thoát" once, and report."""
    return _run(
        _SESSION_HARNESS
        + f"""
await signIn();
{setup}
const outcome = await session.signOut();
console.log(JSON.stringify(view(outcome)));
"""
    )


@needs_node
@pytest.mark.parametrize(
    ("answer", "end_session_url"),
    [
        ("[200, { end_session_url: null }]", None),
        (f'[200, {{ end_session_url: "{_ISSUER_LOGOUT}" }}]', _ISSUER_LOGOUT),
    ],
)
def test_a_sign_out_the_server_answered_forgets_the_person_here(
    answer: str, end_session_url: str | None
) -> None:
    result = _sign_out_with(f'answers["/internal/v1/auth/logout"] = {answer};')

    assert result["signedOut"] is True
    assert result["principal"] is None and result["status"] == "ended"
    assert result["stateSignedOut"] is True, "the shell must know it was a sign-out, not an expiry"
    # The store scope goes with the person; the device's one key stays for the next session read.
    assert result["storeId"] is None
    assert result["stored"] == "11111111-2222-4333-8444-555555555555"
    assert result["forgotten"] == 1, "every registered hand-off is dropped, once"
    assert result["logoutCalls"] == 1 and result["logoutKeyed"]
    assert result["assigned"] == ([end_session_url] if end_session_url else [])


@needs_node
def test_a_sign_out_answered_401_is_already_the_goal_state() -> None:
    """A second press, or an expiry that got there first: the server holds no session."""

    result = _sign_out_with(
        'answers["/internal/v1/auth/logout"] = [401, { detail: "invalid staff session" }];'
    )

    assert result["signedOut"] is True
    assert result["principal"] is None and result["stateSignedOut"] is True
    assert result["forgotten"] == 1


@needs_node
@pytest.mark.parametrize(
    ("setup", "kind", "requests"),
    [
        # The C1 case: the counter PC's wifi is down. Nothing is sent, nothing is ended.
        ("navigator.onLine = false;", "OFFLINE", 0),
        # The request left and never came back.
        ('answers["/internal/v1/auth/logout"] = new TypeError("Failed to fetch");', "NETWORK", 1),
        ('answers["/internal/v1/auth/logout"] = [500, { detail: "boom" }];', "FAULT", 1),
        ('answers["/internal/v1/auth/logout"] = [503, { detail: "down" }];', "UNAVAILABLE", 1),
        (
            'answers["/internal/v1/auth/logout"] = [403, { detail: "CSRF validation failed" }];',
            "DENIED",
            1,
        ),
    ],
)
def test_a_sign_out_that_did_not_reach_the_server_keeps_the_session(
    setup: str, kind: str, requests: int
) -> None:
    result = _sign_out_with(setup)

    assert result["failed"] is True, "the caller must be told the sign-out did not happen"
    assert result["errorKind"] == kind
    # Still signed in, here as on the server: the operator is not shown "Chưa đăng nhập" over a
    # session the next person's "Kiểm tra lại phiên" would pick up.
    assert result["principal"] == "u-1" and result["status"] == "active"
    assert result["stateSignedOut"] is False
    assert result["storeId"] == "11111111-2222-4333-8444-555555555555"
    assert result["forgotten"] == 0, "nothing is wiped for a sign-out that did not happen"
    assert result["logoutCalls"] == requests, "and nothing is retried"
    assert result["assigned"] == []


@needs_node
def test_an_idle_expiry_is_not_a_sign_out_and_keeps_everything() -> None:
    """The deliberate other path: a 401 mid-shift ends the session and drops nothing."""

    result = _run(
        _SESSION_HARNESS
        + """
await signIn();
answers["/internal/v1/orders/o-1"] = [401, { detail: "invalid staff session" }];
const { request } = await import("./src/core/api.js");
try { await request("/internal/v1/orders/o-1"); } catch (error) { /* the screen shows it */ }
console.log(JSON.stringify(view(null)));
"""
    )

    assert result["principal"] is None and result["status"] == "ended"
    assert result["stateSignedOut"] is False, "an expiry must not be taken for a sign-out"
    assert result["forgotten"] == 0, "an expiry throws nothing away"
    assert result["storeId"] == "11111111-2222-4333-8444-555555555555"


@needs_node
def test_signing_in_again_after_a_sign_out_is_an_ordinary_session() -> None:
    result = _run(
        _SESSION_HARNESS
        + """
await signIn();
answers["/internal/v1/auth/logout"] = [200, { end_session_url: null }];
await session.signOut();
await signIn();
console.log(JSON.stringify(view(null)));
"""
    )

    assert result["principal"] == "u-1" and result["status"] == "active"
    assert result["stateSignedOut"] is False
    assert result["storeId"] == "11111111-2222-4333-8444-555555555555"


# --- C7: no mixed builds after a deploy -----------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/staff/",
        "/staff/app.js",
        "/staff/sw.js",
        "/staff/src/core/nav.js",
        "/staff/src/screens/orderDetail.js",
        "/staff/styles/kit.css",
        "/staff/manifest.webmanifest",
        "/staff/icon.svg",
    ],
)
def test_the_console_shell_is_served_for_revalidation(path: str) -> None:
    response = TestClient(app).get(path)

    assert response.status_code == 200, path
    assert response.headers.get("cache-control") == "no-cache", path


def test_a_revalidated_shell_file_answers_304_and_keeps_the_policy() -> None:
    client = TestClient(app)
    first = client.get("/staff/app.js")
    etag = first.headers.get("etag")
    assert etag, "StaticFiles sends an ETag; revalidation depends on it"

    again = client.get("/staff/app.js", headers={"If-None-Match": etag})

    assert again.status_code == 304
    assert again.headers.get("cache-control") == "no-cache"


def test_api_answers_are_still_never_stored() -> None:
    response = TestClient(app).get("/internal/v1/session")

    assert response.headers.get("cache-control") == "no-store"


def _worker() -> str:
    return (WEB / "sw.js").read_text(encoding="utf-8")


def _listener(source: str, event: str) -> str:
    match = re.search(
        r'self\.addEventListener\("' + event + r'", \(event\) => \{(.*?)\n\}\);', source, re.S
    )
    assert match, f"the worker has no {event} listener"
    return match.group(1)


def test_the_worker_revalidates_every_shell_file_it_serves() -> None:
    fetch = _listener(_worker(), "fetch")
    code = "\n".join(line for line in fetch.splitlines() if not line.strip().startswith("//"))

    assert 'fetch(event.request, { cache: "no-cache" })' in code
    assert "fetch(event.request)" not in code


def test_a_new_worker_waits_for_the_person_and_never_takes_over_by_itself() -> None:
    source = _worker()
    install = _listener(source, "install")
    install_code = "\n".join(
        line for line in install.splitlines() if not line.strip().startswith("//")
    )

    assert "skipWaiting" not in install_code, "a worker that activates itself mixes two builds"
    message = _listener(source, "message")
    assert 'event.data === "SKIP_WAITING"' in message and "self.skipWaiting()" in message
    code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("//"))
    assert code.count("skipWaiting()") == 1


def test_the_console_offers_the_new_build_and_reloads_only_when_pressed() -> None:
    shell = (WEB / "app.js").read_text(encoding="utf-8")

    assert "Có bản mới" in shell and "Tải lại" in shell
    assert 'postMessage("SKIP_WAITING")' in shell
    # Every reload in the shell is behind the person's press.
    reloads = [line.strip() for line in shell.splitlines() if "location.reload()" in line]
    assert reloads, "the press must reload into the new build"
    assert all("reloadRequested" in line or "setTimeout" in line for line in reloads), reloads
