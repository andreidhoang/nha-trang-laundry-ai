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
