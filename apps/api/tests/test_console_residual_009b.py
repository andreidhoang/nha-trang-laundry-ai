"""CONSOLE-RESIDUAL-009B (round 9b, slice K): the shell's own rules, run under Node.

- **K2, one "Thoát" for every tab.** The tabs of the console in one browser share one session
  cookie, so a sign-out the server answered in one tab has ended the session in all of them -- and
  each other tab kept its last screen (a customer's name, a number) on show. `core/session.js` now
  tells the other tabs on a `BroadcastChannel`, and each clears itself as the pressing tab does.
  A sign-out the server did not answer tells nobody.
- **K2, "Nhận đồ" without two-step verification.** `core/nav.js` listed only what a person can
  open (C6), so a counter role whose session lacks two-step verification lost "Nhận đồ" without a
  word. It is now shown, shut, with "Cần xác thực hai bước"; a role that never takes laundry in
  still does not see it (C6), and `#/more` still lists it under "Cần quyền khác".

The rendered halves (the tab bar and sidebar, Hôm nay, the other tab's page, the address after a
walk-in, step 1 while a write is out, the consent ticks, the receipt's year) are section 29 of
`scripts/verify_console_interaction.py`, because they need a real browser; the real-API halves
are `console_residual` in `scripts/verify_workflow_conformance.py`.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

#: What a browser gives the session module at load: a cookie jar with the CSRF cookie, a network
#: flag, `location`, and a `fetch` the test answers.
_BROWSER = """
globalThis.document = {cookie: "staff_csrf=" + "c".repeat(40)};
globalThis.location = {assign() {}, href: "http://localhost/staff/"};
if (!globalThis.navigator || globalThis.navigator.onLine === undefined) {
  Object.defineProperty(globalThis, "navigator", {value: {onLine: true}, configurable: true});
}
globalThis.localStorage = {getItem: () => null, setItem() {}, removeItem() {}};
globalThis.__answers = [];
globalThis.fetch = async (url, init = {}) => {
  const [status, body] = globalThis.__answers.shift() || [200, {}];
  return new Response(JSON.stringify(body), {
    status, headers: {"Content-Type": "application/json"},
  });
};
"""


def _run(script: str) -> Any:
    """Execute an ES module against a copy of the client tree; return what it prints as JSON."""
    with tempfile.TemporaryDirectory() as directory:
        target = pathlib.Path(directory)
        shutil.copytree(WEB / "src", target / "src")
        (target / "package.json").write_text(json.dumps({"type": "module"}))
        entry = target / "check.mjs"
        entry.write_text(_BROWSER + script + "\nprocess.exit(0);\n")
        result = subprocess.run(
            ["node", str(entry)],
            capture_output=True,
            text=True,
            cwd=target,
            timeout=60,
            env={**os.environ, "TZ": "UTC"},
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])


# --- K2: "Nhận đồ" for a session without two-step verification ---------------------------------


def _plan(roles: list[str], mfa: bool) -> dict[str, Any]:
    return dict(
        _run(
            f"""
const nav = await import("./src/core/nav.js");
const plan = nav.navPlan({{staffUserId: "u", roles: {json.dumps(roles)},
                          mfaVerified: {json.dumps(mfa)}, sessionId: null}});
const entry = plan.shown.find((e) => e.item.path === "/new");
console.log(JSON.stringify({{
  shown: Boolean(entry),
  tab: entry ? entry.tab : null,
  shut: entry ? Boolean(entry.denied) : null,
  short: entry && entry.denied ? entry.denied.short : null,
  reason: entry && entry.denied ? entry.denied.reason : null,
  deniedListed: plan.denied.some((e) => e.item.path === "/new"),
  othersShut: plan.shown.filter((e) => e.denied && e.item.path !== "/new").length,
}}));
"""
        )
    )


@pytest.mark.parametrize(
    ("roles", "mfa", "expected"),
    [
        # The case: the counter's job, shut, with what is missing in three words.
        (["OPERATOR"], False, {"shown": True, "shut": True, "short": "Cần xác thực hai bước"}),
        (["OWNER_ADMIN"], False, {"shown": True, "shut": True, "short": "Cần xác thực hai bước"}),
        # Neighbours: with the proof it is simply open; a role that never takes laundry in does
        # not see it at all (C6), with or without the proof.
        (["OPERATOR"], True, {"shown": True, "shut": False, "short": None}),
        (["AUDITOR"], False, {"shown": False, "shut": None, "short": None}),
        (["AUDITOR"], True, {"shown": False, "shut": None, "short": None}),
    ],
)
def test_new_order_is_shown_shut_only_for_a_missing_second_step(
    roles: list[str], mfa: bool, expected: dict[str, Any]
) -> None:
    got = _plan(roles, mfa)
    assert {key: got[key] for key in expected} == expected, got


def test_the_shut_entry_keeps_its_tab_its_full_reason_and_its_place_on_more() -> None:
    got = _plan(["OPERATOR"], False)
    # The raised middle tab on a phone, as for everyone who takes laundry in.
    assert got["tab"] == 3
    # The full sentence for the title and the guard screen a press opens.
    assert "xác thực hai bước" in str(got["reason"])
    # Still under "Cần quyền khác" on `#/more`, never silently absent there.
    assert got["deniedListed"] is True
    # Only "Nhận đồ": every other shut destination stays off the navigation (C6).
    assert got["othersShut"] == 0


# --- K2: one "Thoát", every tab ---------------------------------------------------------------

_OTHER_TAB = """
const session = await import("./src/core/session.js");
let wiped = 0;
session.onSignOut(() => { wiped += 1; });
const before = session.snapshot();
const elsewhere = new BroadcastChannel("staff-console-session");
"""


def test_a_sign_out_in_another_tab_clears_this_one() -> None:
    got = _run(
        _OTHER_TAB
        + """
elsewhere.postMessage({type: "signed-out"});
await new Promise((resolve) => setTimeout(resolve, 50));
const after = session.snapshot();
console.log(JSON.stringify({wiped, status: after.status, signedOut: after.signedOut,
  elsewhere: after.signedOutElsewhere, principal: after.principal, store: after.storeId}));
"""
    )
    assert got == {
        "wiped": 1,
        "status": "ended",
        "signedOut": True,
        "elsewhere": True,
        "principal": None,
        "store": None,
    }


def test_any_other_message_on_the_channel_changes_nothing() -> None:
    got = _run(
        _OTHER_TAB
        + """
elsewhere.postMessage({type: "hello"});
elsewhere.postMessage("signed-out");
await new Promise((resolve) => setTimeout(resolve, 50));
const after = session.snapshot();
console.log(JSON.stringify({wiped, same: after.status === before.status,
  signedOut: after.signedOut}));
"""
    )
    assert got == {"wiped": 0, "same": True, "signedOut": False}


@pytest.mark.parametrize(
    ("answer", "told"),
    [
        # The server ended the session (or there was none to end): every tab is told.
        ([200, {"end_session_url": None}], True),
        ([401, {"detail": "invalid staff session"}], True),
        # The server did not answer the sign-out: the session may be alive, nobody is told.
        ([503, {"detail": "operations unavailable"}], False),
    ],
)
def test_thoat_here_tells_the_other_tabs_only_when_the_server_ended_it(
    answer: list[Any], told: bool
) -> None:
    got = _run(
        f"""
const session = await import("./src/core/session.js");
const elsewhere = new BroadcastChannel("staff-console-session");
const heard = [];
elsewhere.onmessage = (event) => heard.push(event.data);
globalThis.__answers.push({json.dumps(answer)});
const outcome = await session.signOut();
await new Promise((resolve) => setTimeout(resolve, 50));
console.log(JSON.stringify({{signedOut: outcome.signedOut, heard,
  elsewhereHere: session.snapshot().signedOutElsewhere}}));
"""
    )
    assert got["signedOut"] is told
    assert got["heard"] == ([{"type": "signed-out"}] if told else [])
    # The tab that pressed is not "signed out elsewhere".
    assert got["elsewhereHere"] is False


# --- K2 (round-9b verification 2): a success after the session went out ---------------------
#
# The hold (`app.js` `holdScreen`) rests on "the session is out, every destination needs the
# server". A write sent after signing in again elsewhere succeeded -- and its own move to the order
# it created was held, because only a re-read of the session cleared the local status. The server's
# answer now says so at once (`answered`), before the caller acts on it, and the session is read
# again. A request sent before the session went out says nothing about now.

_ANSWERED = """
const api = await import("./src/core/api.js");
const session = await import("./src/core/session.js");
const asked = [];
const pending = [];
globalThis.fetch = (url, init = {}) => {
  asked.push(String(url));
  const answer = globalThis.__answers.shift();
  if (answer === "hold") {
    return new Promise((resolve) => pending.push(() => resolve(new Response("{}", {
      status: 200, headers: {"Content-Type": "application/json"},
    }))));
  }
  const [status, body] = answer || [200, {}];
  return Promise.resolve(new Response(JSON.stringify(body), {
    status, headers: {"Content-Type": "application/json"},
  }));
};
const SESSION = {staff_user_id: "u1", roles: ["OPERATOR"], mfa_verified: true, session_id: "s1"};
const settle = () => new Promise((resolve) => setTimeout(resolve, 30));
"""


def _session_answers(script: str) -> Any:
    return _run(_ANSWERED + script)


def test_a_success_sent_after_an_expiry_says_so_before_the_caller_sees_it() -> None:
    got = _session_answers(
        """
globalThis.__answers.push([200, SESSION], [200, []]);
await session.refresh();
globalThis.__answers.push([401, {detail: "invalid staff session"}]);
await api.request("/internal/v1/x").catch(() => null);
const out = session.snapshot().status;
asked.length = 0;
// Signed in again elsewhere; the write is answered. What the caller sees, at once:
globalThis.__answers.push([201, {order_id: "o1"}], [200, SESSION], [200, []]);
const created = await api.request("/internal/v1/orders", {method: "POST", body: {},
  idempotencyKey: "k1"});
const atOnce = session.snapshot();
await settle();
const later = session.snapshot();
console.log(JSON.stringify({out, created: created.order_id, answered: atOnce.answered,
  statusAtOnce: atOnce.status, reread: asked.includes("/internal/v1/session"),
  statusLater: later.status, answeredLater: later.answered}));
"""
    )
    assert got == {
        "out": "ended",
        "created": "o1",
        "answered": True,
        "statusAtOnce": "ended",
        "reread": True,
        "statusLater": "active",
        "answeredLater": False,
    }


def test_a_success_sent_before_the_expiry_says_nothing_about_now() -> None:
    got = _session_answers(
        """
globalThis.__answers.push([200, SESSION], [200, []]);
await session.refresh();
globalThis.__answers.push("hold");
const early = api.request("/internal/v1/slow");
globalThis.__answers.push([401, {detail: "invalid staff session"}]);
await api.request("/internal/v1/x").catch(() => null);
asked.length = 0;
pending.shift()();
await early;
await settle();
const now = session.snapshot();
console.log(JSON.stringify({status: now.status, answered: now.answered,
  reread: asked.includes("/internal/v1/session")}));
"""
    )
    assert got == {"status": "ended", "answered": False, "reread": False}


def test_a_success_after_the_server_was_unreachable_reads_the_session_again() -> None:
    got = _session_answers(
        """
globalThis.__answers.push([200, SESSION], [200, []]);
await session.refresh();
globalThis.__answers.push([503, {detail: "operations unavailable"}]);
await session.refresh();
const out = session.snapshot().status;
globalThis.__answers.push([200, []], [200, SESSION], [200, []]);
await api.request("/internal/v1/y");
const atOnce = session.snapshot().answered;
await settle();
console.log(JSON.stringify({out, atOnce, status: session.snapshot().status}));
"""
    )
    assert got == {"out": "unreachable", "atOnce": True, "status": "active"}


def test_the_re_read_finding_no_session_puts_the_hold_back() -> None:
    got = _session_answers(
        """
globalThis.__answers.push([200, SESSION], [200, []]);
await session.refresh();
globalThis.__answers.push([401, {detail: "invalid staff session"}]);
await api.request("/internal/v1/x").catch(() => null);
// A public answer (no session needed) -- the re-read then finds no session after all.
globalThis.__answers.push([200, {}], [401, {detail: "invalid staff session"}]);
await api.request("/internal/v1/public");
await settle();
const now = session.snapshot();
console.log(JSON.stringify({status: now.status, answered: now.answered}));
"""
    )
    assert got == {"status": "ended", "answered": False}


def test_after_thoat_a_success_changes_nothing() -> None:
    got = _session_answers(
        """
globalThis.__answers.push([200, SESSION], [200, []]);
await session.refresh();
globalThis.__answers.push([200, {end_session_url: null}]);
await session.signOut();
asked.length = 0;
globalThis.__answers.push([200, {}]);
await api.request("/internal/v1/z");
await settle();
const now = session.snapshot();
console.log(JSON.stringify({status: now.status, signedOut: now.signedOut,
  answered: now.answered, reread: asked.includes("/internal/v1/session")}));
"""
    )
    assert got == {"status": "ended", "signedOut": True, "answered": False, "reread": False}


# --- Round-9b verification 3: what the guide promises, and who writes the address ---------------

GUIDE = ROOT / "docs" / "HUONG_DAN_CA_LAM_VIEC_VI.md"


def _guide_row(cell: str) -> str:
    rows = [line for line in GUIDE.read_text().splitlines() if line.startswith(f"| {cell}")]
    assert len(rows) == 1, cell
    return rows[0]


def test_the_guide_does_not_promise_a_reminder_from_a_dimmed_row() -> None:
    # While a ticket or intake write is out, step 1's rows are disabled: a press on one reaches
    # nothing, so nothing can remind anyone. The guide said it would.
    row = _guide_row("Các dòng ở **Nhận đồ** mờ đi vài giây")
    assert "Bấm vào dòng mờ thì máy nhắc" not in row
    assert "chưa bấm được" in row


def test_the_guide_says_a_press_after_signing_in_again_goes_through() -> None:
    row = _guide_row("**Phiên đăng nhập đã kết thúc**")
    assert "Đăng nhập lại xong thì bấm lại nút đó" in row
    assert "kiểm tra lại phiên rồi mở" in row


def test_the_hand_off_asks_the_router_and_writes_no_address_itself() -> None:
    # A hash written past `navigate` while a screen is held made a history entry of its own.
    source = (WEB / "src" / "ui" / "handoff.js").read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("*", "//", "/*"))
    )
    assert "location.hash" not in code
    assert code.count("navigate(") == 2


def test_the_guide_says_typing_on_after_a_held_press_keeps_the_screen() -> None:
    # Round-9b verification 4: a held press is forgotten once the person carries on with the held
    # screen (router.js `forgetHeld`); a session read answering late no longer takes them away.
    row = _guide_row("**Phiên đăng nhập đã kết thúc**")
    assert "nhập tiếp ở màn hình đang giữ thì máy ở lại màn hình đó" in row
    source = (WEB / "src" / "core" / "router.js").read_text()
    assert "function forgetHeld()" in source
