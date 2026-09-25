"""The staff console may not call a route that does not exist, and may not compute money.

The application is constructed with ``openapi_url=None``, so there is no served schema, no generated
client and no type checker standing between the browser and the API. Every field name and every path
in ``apps/web`` was written by hand against a reading of ``main.py``. The failure this guards is the
cheap one that costs the most: a path that was correct when it was written and silently wrong after
a route moved, discovered by an operator at a counter rather than by CI.

Reading ``app.routes`` rather than ``app.openapi()`` is deliberate. Four routes carry
``include_in_schema=False`` — ``/healthz`` and the entire session lifecycle — and the console cannot
function without three of them, so a schema-based check would miss exactly the routes whose absence
would be fatal.

The remaining tests are source-text assertions over authored JavaScript. That is only possible
because the console has no build step; it is one of the reasons it does not have one.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from nha_trang_laundry_api.main import app

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"

#: Any string literal in the client that looks like a call into the API.
#:
#: Whitespace terminates the match. Screen copy discusses routes in prose — a sentence beginning
#: "/internal/v1/session không trả session_id, nên …" — and a greedy match to the closing quote
#: would report the whole sentence as an unknown path. A real path literal never contains a space.
API_PATH_LITERAL = re.compile(r"""["'`](/(?:internal|healthz)[^"'`\s]*)["'`]?""")

#: A JavaScript template placeholder, which stands where a path parameter goes.
TEMPLATE_HOLE = re.compile(r"\$\{[^}]*\}")

#: A FastAPI path parameter.
ROUTE_HOLE = re.compile(r"\{[^}]*\}")


def javascript_sources() -> list[Path]:
    return sorted(path for path in WEB.rglob("*.js") if path.is_file())


def normalize_client_path(raw: str) -> str:
    """Reduce a client path literal to a comparable template."""

    without_query = raw.split("?", 1)[0]
    return TEMPLATE_HOLE.sub("{}", without_query)


def normalize_route_path(raw: str) -> str:
    return ROUTE_HOLE.sub("{}", raw)


def served_paths() -> set[str]:
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str) and (path.startswith("/internal/") or path == "/healthz"):
            paths.add(normalize_route_path(path))
    return paths


def client_paths() -> dict[str, list[str]]:
    """Every API path the console references, mapped to the files that reference it."""

    found: dict[str, list[str]] = {}
    for source in javascript_sources():
        text = source.read_text(encoding="utf-8")
        for match in API_PATH_LITERAL.findall(text):
            normalized = normalize_client_path(match)
            found.setdefault(normalized, []).append(str(source.relative_to(ROOT)))
    return found


def test_every_path_the_console_calls_is_a_route_the_api_serves() -> None:
    served = served_paths()
    assert served, "no internal routes were discovered; the import or the filter is wrong"

    unknown = {path: files for path, files in client_paths().items() if path not in served}

    assert not unknown, (
        "the staff console references API paths that this application does not serve:\n"
        + "\n".join(
            f"  {path}  <- {', '.join(sorted(set(files)))}" for path, files in unknown.items()
        )
    )


def test_the_console_reaches_the_routes_its_screens_depend_on() -> None:
    """A guard against the opposite failure: a screen quietly losing its data source."""

    referenced = set(client_paths())
    required = {
        "/internal/v1/session",
        "/internal/v1/stores",
        "/internal/v1/stores/{}/orders",
        "/internal/v1/stores/{}/quotes",
        "/internal/v1/pricebook/services",
        "/internal/v1/stores/{}/order-requests",
        "/internal/v1/stores/{}/incidents",
        "/internal/v1/approvals",
        "/internal/v1/queue-recovery",
        "/internal/v1/stores/{}/shadow/drafts",
        # API-INTEGRITY-002: the unknown-send queue is the selected store's.
        "/internal/v1/stores/{}/shadow/unknown-sends",
        "/internal/v1/stores/{}/assistant/turns",
        # The counter's day, end to end. These three are one requirement, not three: an order
        # cannot reach ACTIVE while intake is not ACCEPTED, and every later step hangs off ACTIVE.
        # Both transition routes existed on the server from the beginning and no screen called
        # either, so a console-only operator could create, quote and confirm an order and then
        # dead-end on `409 INVALID_STATE_TRANSITION: intake is not accepted` -- while the gap
        # register told them the console covered "việc nhận đồ". The walk that proves the shop can
        # run on this software is the walk that needs all three.
        "/internal/v1/orders/{}/transition",
        "/internal/v1/orders/{}/intake-transition",
        "/internal/v1/orders/{}/production-transition",
        "/internal/v1/orders/{}/settlement",
        # PREPAID-DROPOFF-001 (`DEC-032`). Without it a walk-in who paid at drop-off can never be
        # recorded as having taken the goods, and so their order can never complete.
        "/internal/v1/orders/{}/collection",
        "/internal/v1/orders/{}/delivery-legs",
        # ORDER-LOOKUP-001. The only read that reaches an order older than the board's newest page,
        # and the only one that hands back its row version for `If-Match`. Order detail and the
        # board's "?order=" hand-off both depend on it; losing it puts every order older than about
        # three days of trade back out of reach at pickup.
        "/internal/v1/orders/{}",
        # RANGE-PRICE-001. Twenty of the forty-four published services are priced by inspection,
        # and these three are the whole of the console's half of closing one: read the revision to
        # learn the band the customer was shown, propose an amount inside it, and -- after a second
        # person approves the envelope through `/internal/v1/approvals/{}/decisions`, which is
        # already required above -- apply it. A screen that stops calling any one of the three
        # leaves those services quotable and unsellable, which is the state this item ended.
        "/internal/v1/stores/{}/quotes/{}",
        "/internal/v1/stores/{}/quotes/{}/range-prices",
        "/internal/v1/stores/{}/quotes/{}/range-prices/{}",
        # REMEDY-001. These four are the whole of an incident's path to an outcome, and losing any
        # one of them puts the shop back where the item found it -- a complaint recorded, resolved
        # verbally, with the owner's 5x cap and 100.000d ceiling enforced by nothing. `remedy-
        # options` is the load-bearing one and the easiest to drop as "just a read": it is what
        # puts the computed ceiling, the window and the owner requirement on screen *before* a
        # staff member types an amount, and a form without it would let somebody discover that the
        # owner is required after telling a customer what they were getting.
        "/internal/v1/stores/{}/incidents/{}/remedy-options",
        "/internal/v1/stores/{}/incidents/{}/remedy-proposals",
        "/internal/v1/remedy-proposals/{}/execution",
        "/internal/v1/stores/{}/quotes/{}/remedy-credits",
        # REMEDY-OWNER-DECIDE-001. The owner's approvals card reads what an `APPROVE_REMEDY`
        # envelope binds through this; without it every loss claim reaches the queue undecidable.
        "/internal/v1/stores/{}/remedy-proposals/{}/approval-binding",
        # OPS-BOARD-001. The board query and the day counts both existed with no route and no
        # screen, which is how a surface that answers "which order needs a person right now" stayed
        # reachable only as two numbers inside an assistant sentence. A screen that stops calling
        # the board puts the shop back there, and one that stops calling the export leaves
        # `EXPORT_SANITIZED_DATA` as vocabulary with nothing behind it again.
        "/internal/v1/stores/{}/sla-board",
        "/internal/v1/stores/{}/day-summary",
        "/internal/v1/stores/{}/exports",
        "/internal/v1/stores/{}/exports/{}/execution",
        # CONSENT-TRANSACTIONAL-001 (DEC-033). The manual-send panel reads a contact's service-
        # messaging state and releases a STOP through these. Without the read, a refusal names a
        # customer the console can say nothing more about; without the release, a STOP can only be
        # lifted by hand in the database, which is the one way it must never be lifted.
        "/internal/v1/stores/{}/contacts/{}/service-messaging",
        "/internal/v1/stores/{}/contacts/{}/service-messaging/release",
    }
    missing = sorted(required - referenced)
    assert not missing, f"no screen calls these routes any more: {missing}"


def test_the_client_never_reaches_for_a_raw_html_escape() -> None:
    """The console renders agent-authored drafts and customer text; it has no HTML sink."""

    forbidden = [
        "inner" + "HTML",
        "outer" + "HTML",
        "insertAdjacent" + "HTML",
        "document." + "write",
        "eval" + "(",
        "new " + "Function(",
    ]
    offenders: list[str] = []
    for source in javascript_sources():
        # `dom.js` names these constructs in order to search other files for them.
        if source.name == "dom.js":
            continue
        text = source.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{source.relative_to(ROOT)}: {needle}")

    assert not offenders, "raw HTML escapes found:\n" + "\n".join(offenders)


def test_the_client_performs_no_arithmetic_on_money() -> None:
    """Money is decided by ``packages/domain`` and only ever formatted here.

    Every amount crosses the wire as an integer of VND that the deterministic engine produced. A
    browser that adds two of them has become a second, unreviewed opinion about what a customer
    owes, which is the failure ``ENGINEERING_SPEC_V1.md:530`` is written against and which
    ``AGENT_SYSTEM_AND_EVAL_SPEC_V1.md:1320`` names the operator UI as a possible cause of.
    """

    arithmetic = re.compile(
        r"(?:_vnd\s*[+\-*/%]|[+\-*/%]\s*[A-Za-z_.\[\]\"']*_vnd)"
        r"|(?:_vnd[^\n]*\.toFixed)"
        r"|(?:parseFloat\([^)]*_vnd)"
        r"|(?:Math\.(?:round|floor|ceil|abs)\([^)]*_vnd)"
        r"|(?:_vnd[^\n]*\.reduce\()"
    )

    offenders: list[str] = []
    for source in javascript_sources():
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            if arithmetic.search(line):
                offenders.append(f"{source.relative_to(ROOT)}:{number}: {line.strip()}")

    assert not offenders, "arithmetic on a monetary field:\n" + "\n".join(offenders)


def test_the_client_persists_nothing_but_a_store_identifier() -> None:
    """A counter phone is shared and often unlocked. No customer data may rest on it."""

    banned = ["indexedDB", "sync.register", "sessionStorage", "openDatabase"]
    offenders: list[str] = []
    for source in javascript_sources():
        text = source.read_text(encoding="utf-8")
        for needle in banned:
            if needle in text:
                offenders.append(f"{source.relative_to(ROOT)}: {needle}")
        if "localStorage" in text and source.name != "session.js":
            offenders.append(f"{source.relative_to(ROOT)}: localStorage outside core/session.js")

    assert not offenders, "device persistence found:\n" + "\n".join(offenders)


def test_the_console_declares_only_capabilities_that_exist() -> None:
    """A screen guarded by a typo is a screen guarded by nothing."""

    rbac = (WEB / "src" / "core" / "rbac.js").read_text(encoding="utf-8")
    declared = set(re.findall(r"^  ([A-Z][A-Z_]+):\s*\{$", rbac, re.MULTILINE))
    assert declared, "no capabilities were parsed out of rbac.js"

    used: set[str] = set()
    for source in javascript_sources():
        text = source.read_text(encoding="utf-8")
        used.update(re.findall(r"""capability:\s*["']([A-Z_]+)["']""", text))
        used.update(re.findall(r"""can\([^,]+,\s*["']([A-Z_]+)["']\)""", text))

    unknown = sorted(used - declared)
    assert not unknown, f"screens reference capabilities that rbac.js does not define: {unknown}"


def test_every_screen_declares_a_unique_path() -> None:
    paths: dict[str, str] = {}
    duplicates: list[str] = []
    for source in sorted((WEB / "src" / "screens").glob("*.js")):
        if source.name == "index.js":
            continue
        text = source.read_text(encoding="utf-8")
        for path in re.findall(r"""^\s*path:\s*["']([^"']+)["']""", text, re.M):
            if path in paths:
                duplicates.append(f"{path}: {paths[path]} and {source.name}")
            paths[path] = source.name

    assert not duplicates, "duplicate route paths:\n" + "\n".join(duplicates)
    assert "/" in paths, "there is no landing screen"


#: ``today.js`` calls store-scoped routes and deliberately does not declare ``needsStore``.
#:
#: It is the landing screen. Declaring the flag would make the shell guard replace the whole screen
#: with "choose a store" for any account without one — and having no store is the default state of
#: every staff user this API can create, because assigning one has no HTTP route. So it gates per
#: tile instead: a tile that needs a store is skipped rather than requested. The exemption is named
#: here rather than inferred, and the test below still proves the guard exists.
#: Screens that call a store-scoped route for one part of themselves and must still render without a
#: store. Each has a test below proving the part that needs a store is guarded; without that guard
#: the exemption is unsafe and the screen should declare `needsStore` instead. `approvals.js` is
#: here because its queue spans every store the owner is assigned to, and only the DEC-029 review
#: panel is per store -- declaring `needsStore` would blank the whole queue for a multi-store owner.
#: Its `MESSAGE_DRAFT` card is store-scoped too, but by the envelope's own store rather than the
#: selected one (`MESSAGE-DRAFT-BINDING-001`), and has its own guard test below. `manualSend.js`
#: is not a screen at all: it is the panel `exceptions.js` mounts, and that screen declares
#: `needsStore`. The test below proves both halves of that, and that the panel still refuses
#: without a store.
#: `staff.js` is here because an owner with no store yet must still create people and assign stores
#: (to themselves, first); only READ-PATHS-001's directory panel is per store.
STORE_GATE_EXEMPT = {"today.js", "approvals.js", "manualSend.js", "staff.js"}

SCREEN_EXPORT = re.compile(r"export const screen = \{(.*?)\n\};", re.S)


def test_a_store_scoped_screen_declares_that_it_needs_a_store() -> None:
    """Otherwise the screen renders, calls ``/stores/undefined/...`` and shows a parse error.

    The flag is read out of the ``export const screen`` block specifically. Searching the whole file
    for the substring passed once already on a screen that never declared it, because the words
    appeared in unrelated per-tile state.
    """

    offenders: list[str] = []
    for source in sorted((WEB / "src" / "screens").glob("*.js")):
        if source.name == "index.js" or source.name in STORE_GATE_EXEMPT:
            continue
        text = source.read_text(encoding="utf-8")
        if "/internal/v1/stores/${" not in text:
            continue
        block = SCREEN_EXPORT.search(text)
        if block is None or "needsStore: true" not in block.group(1):
            offenders.append(source.name)

    assert not offenders, f"store-scoped screens missing needsStore: {offenders}"


def test_the_exempt_landing_screen_still_refuses_to_build_a_store_scoped_url() -> None:
    """The exemption above is only safe while the per-tile guard is actually there."""

    text = (WEB / "src" / "screens" / "today.js").read_text(encoding="utf-8")
    assert "needsStore" in text, "today.js no longer marks which tiles need a store"
    assert re.search(r"needsStore\s*&&\s*!\s*store", text), (
        "today.js must skip a store-scoped tile when no store is selected; without that guard the "
        "exemption in STORE_GATE_EXEMPT is unsafe and the screen should declare needsStore instead"
    )


def test_the_service_worker_shell_manifest_is_current() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_staff_console_manifest.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed on this host")
def test_every_client_module_parses(tmp_path: Path) -> None:
    """There is no bundler and therefore no parse step; this is it.

    Each file is copied to a ``.mjs`` name before checking. ``node --check`` decides CommonJS or
    ESM from the extension, and a ``.js`` file containing ``import`` fails as CommonJS on some
    versions and passes by detection on others — a check whose result depends on the Node minor is
    not a check.
    """

    failures: list[str] = []
    for source in javascript_sources():
        target = tmp_path / f"{source.relative_to(WEB).as_posix().replace('/', '__')}.mjs"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        result = subprocess.run(
            ["node", "--check", str(target)], capture_output=True, text=True, cwd=ROOT
        )
        if result.returncode != 0:
            failures.append(f"{source.relative_to(ROOT)}\n{result.stderr.strip()}")

    assert not failures, "modules failed to parse:\n" + "\n\n".join(failures)


def test_every_mutating_console_call_carries_an_idempotency_key() -> None:
    """A mutating call without a key throws in the browser and never reaches the server.

    `core/api.js` raises `"<METHOD> <path> was issued without an idempotency key"` before it opens
    a request, so a screen that omits one is not a slow path or a 4xx -- it is a button that does
    nothing and reports a client error. Two shipped that way and were found by adversarial review
    rather than by CI: "Phát phiếu", which made `DEC-013`'s walk-in unreachable from the console,
    and "Ghi nhận chuyến giao", which made a delivery order impossible to close.

    Asserted over source text because the console has no build step and no type checker between it
    and the API -- the same reason the route-existence test above reads authored JavaScript.
    """

    offenders: list[str] = []
    for source in sorted((WEB / "src").rglob("*.js")):
        text = source.read_text(encoding="utf-8")
        for match in re.finditer(r"\brequest\(", text):
            depth, index = 0, match.end() - 1
            while index < len(text):
                if text[index] == "(":
                    depth += 1
                elif text[index] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                index += 1
            call = text[match.end() : index]
            method = re.search(r"method:\s*([^,}]+)", call)
            if method is None:
                continue
            expression = method.group(1).strip()
            literal = re.fullmatch(r"""["'`](GET|POST|PATCH|PUT|DELETE|HEAD)["'`]""", expression)
            # COUNTER-DEFECTS-001. This used to require the method to be a double-quoted literal
            # naming a mutating verb, so `method: intent === "grant" ? "POST" : "DELETE"` in
            # `staff.js` was invisible and the call was silently classified as a read. That call is
            # the only writer of `staff_store_assignments`, which every membership check in the
            # system depends on -- so the one route the test most needed to cover was the one it
            # could not see, and it passed by not looking. An expression this test cannot resolve
            # is now treated as mutating: proving a call is a read is the caller's job, not the
            # reader's guess.
            verb = literal.group(1) if literal else expression
            if literal and verb in {"GET", "HEAD"}:
                continue
            if "idempotencyKey" not in call:
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{source.relative_to(ROOT)}:{line} issues {verb} with no key")

    assert not offenders, (
        "these console calls would throw in the browser before reaching the server; "
        f"give each one an idempotencyKey: {offenders}"
    )


def test_only_a_pinned_read_may_be_marked_as_one() -> None:
    """`data-intent="read"` exempts a button from the "auditor sees no live write control" checks.

    Both real-API browser scripts count a live `data-requires-network` or submit button on the order
    board as a write an auditor must not be offered. ORDER-LOOKUP-001 put the first *read* form on
    that screen -- "Tìm theo số phiếu", a GET an auditor is entitled to -- and both checks failed on
    it while all three writes were correctly disabled. The marker is what lets the checks tell the
    two apart. Marking a write as a read would switch the check off for it, so each use is pinned
    here and a new one is a reviewed change, not a one-word edit.
    """

    pinned = {("screens/orders.js", '"Tìm"')}
    found = set()
    for path in (WEB / "src").rglob("*.js"):
        text = path.read_text("utf-8")
        for match in re.finditer(r'dataIntent:\s*"read"', text):
            label = re.search(r'"[^"]+"', text[match.end() : match.end() + 60])
            found.add((path.relative_to(WEB / "src").as_posix(), label.group(0) if label else "?"))
    assert found == pinned


def test_the_exempt_approvals_screen_builds_its_store_url_only_with_a_store() -> None:
    """The DEC-029 review panel is the one store-scoped part of #/approvals; it must be guarded."""

    text = (WEB / "src" / "screens" / "approvals.js").read_text(encoding="utf-8")
    assert "range-price-reviews" in text
    assert re.search(r"if \(store\) void reviews\.reload\(\)", text), (
        "approvals.js must not load the range-price review without a selected store; without that "
        "guard the exemption in STORE_GATE_EXEMPT is unsafe"
    )
    assert re.search(r"store\s*\n?\s*\?\s*h\(", text), (
        "approvals.js must render a no-store notice instead of the review list when no store is set"
    )


def test_a_formatted_range_is_rendered_by_its_text_not_as_an_object() -> None:
    """`moneyRange` returns `{text, isRange, isKnown}`, not a string.

    The owner's `DEC-029` review card passed the object itself to `h()` as a child, and the
    screen read "160.000 ₫ trong khoảng [object Object]" -- the one number the review exists to
    show, the band the price was checked against, replaced by a JavaScript default. The API tests,
    the contract tests and the stubbed browser suite were all green; only the real-API browser run
    caught it. Every call must either read `.text` off the result or keep it in a named variable
    whose fields are read later.
    """

    call = re.compile(r"moneyRange\(")
    offenders: list[str] = []
    for source in javascript_sources():
        if source.name == "format.js":
            continue
        text = source.read_text(encoding="utf-8")
        for match in call.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            before = text[line_start : match.start()]
            if before.lstrip().startswith(("*", "//")):
                continue
            depth, index = 0, match.end() - 1
            while index < len(text):
                if text[index] == "(":
                    depth += 1
                elif text[index] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                index += 1
            used_as_value = text[index + 1 : index + 2] == "."
            assigned = re.search(r"(?:const|let|var)\s+\w+\s*=\s*$", before) is not None
            if not (used_as_value or assigned):
                number = text.count("\n", 0, match.start()) + 1
                where = f"{source.relative_to(ROOT)}:{number}"
                offenders.append(f"{where}: {before.strip()}moneyRange(")

    assert not offenders, "moneyRange result used as a value:\n" + "\n".join(offenders)


def test_the_message_draft_card_reads_through_the_envelopes_own_store() -> None:
    """`MESSAGE-DRAFT-BINDING-001`. The queue spans stores; the selected one would be wrong.

    The card must build its URL from the queue row's `store_id` and refuse a row without one --
    never fall back to `storeId()`, which names whichever shop the top bar shows.
    """

    text = (WEB / "src" / "screens" / "approvals.js").read_text(encoding="utf-8")
    start = text.index("async function loadMessageDraft(")
    body = text[start : text.index("\n}\n", start)]
    assert "message-drafts/${draftPart}/binding" in body
    assert re.search(r"const storePart = encodeURIComponent\(envelopeStore\)", body)
    assert re.search(r"const envelopeStore = typeof item\.store_id === \"string\"", body)
    assert re.search(r"if \(!envelopeStore\) \{", body), (
        "a queue row without a store must be refused, not guessed"
    )
    assert "storeId()" not in body


def test_the_remedy_card_reads_through_the_envelopes_own_store_and_compares_its_binding() -> None:
    """`REMEDY-OWNER-DECIDE-001`. The same guard as the `MESSAGE_DRAFT` card, for money.

    The URL is built from the queue row's `store_id` and a row without one is refused; the read's
    proposal, approval, version and both digests are compared with the envelope's, together with
    the server's own `envelope_matches`, before anything is printed or "Duyệt" is enabled; and a
    mismatch leaves only "Từ chối" (`refuseOnly`).
    """

    text = (WEB / "src" / "screens" / "approvals.js").read_text(encoding="utf-8")
    assert re.search(r"REMEDY_PROPOSAL: \(\) => null,", text), (
        "REMEDY_PROPOSAL must be a type this screen can show, or its envelopes are undecidable"
    )
    start = text.index("async function loadRemedyProposal(")
    body = text[start : text.index("\n}\n", start)]
    assert "remedy-proposals/${proposalPart}/approval-binding" in body
    assert re.search(r"const storePart = encodeURIComponent\(envelopeStore\)", body)
    assert re.search(r"const envelopeStore = typeof item\.store_id === \"string\"", body)
    assert re.search(r"if \(!envelopeStore\) \{", body), (
        "a queue row without a store must be refused, not guessed"
    )
    assert "storeId()" not in body
    for comparison in (
        "read.resource_id !== item.resource_id",
        "read.approval_id !== item.approval_request_id",
        "read.resource_version !== item.resource_version",
        "read.snapshot_hash !== item.snapshot_hash",
        "read.rendered_hash !== item.rendered_hash",
        "read.envelope_matches !== true",
    ):
        assert comparison in body, f"the remedy card no longer checks {comparison}"
    # The stale branch renders before the success branch and leaves only a refusal.
    stale = body.index("read.envelope_matches !== true")
    shown = body.index("render(contentHost, remedyContents(read))")
    assert stale < shown
    assert "true,\n      );\n      return;" in body[stale:shown]


def test_the_recorded_proposals_execute_only_what_the_server_says_may_be_executed() -> None:
    """The list's execute press is offered from the server's `next_step`, never from a status or
    a clock this file reads, and calls the existing execute route with a per-proposal key."""

    text = (WEB / "src" / "screens" / "remedies.js").read_text(encoding="utf-8")
    start = text.index("function recordedNextStep(")
    body = text[start : text.index("\n}\n", start)]
    assert 'if (step === "EXECUTE") {' in body
    assert body.count('"Thực hiện bồi hoàn"') == 1
    assert "Date" not in body and "approval_status ===" not in body.split("AWAIT_OWNER")[0]
    panel = text[text.index("function recordedProposalsPanel(") :]
    panel = panel[: panel.index("\n}\n")]
    assert "/internal/v1/remedy-proposals/${encodeURIComponent(id)}/execution" in panel
    assert "idempotencyKey: submission.key()" in panel
    assert "new Submission(`remedy-execute-${id}`)" in panel


def test_the_manual_send_panel_is_mounted_only_by_a_screen_that_needs_a_store() -> None:
    """The exemption of `manualSend.js` above is only safe while all three of these hold."""

    screens = WEB / "src" / "screens"
    panel = (screens / "manualSend.js").read_text(encoding="utf-8")
    assert SCREEN_EXPORT.search(panel) is None, "manualSend.js became a screen; declare needsStore"
    importers = sorted(
        path.name
        for path in screens.glob("*.js")
        if path.name != "manualSend.js" and "./manualSend.js" in path.read_text(encoding="utf-8")
    )
    assert importers == ["exceptions.js"], importers
    block = SCREEN_EXPORT.search((screens / "exceptions.js").read_text(encoding="utf-8"))
    assert block is not None and "needsStore: true" in block.group(1)
    assert re.search(r"if \(!store\) \{", panel), (
        "the manual-send panel must refuse to build a store-scoped URL without a store"
    )


def test_the_exempt_staff_screen_builds_its_store_url_only_with_a_store() -> None:
    """READ-PATHS-001's staff directory is the one store-scoped part of #/staff.

    The forms must work for an owner with no store yet -- assigning a store, including to
    themselves, is what those forms are for -- so the screen cannot declare `needsStore`. The
    directory must therefore refuse to request `/stores/${store}/staff` without one, and say so.
    """

    text = (WEB / "src" / "screens" / "staff.js").read_text(encoding="utf-8")
    assert "/internal/v1/stores/${encodeURIComponent(store)}/staff" in text
    assert re.search(r"if \(!store \|\| !spec\.verdict\.allowed\) return;", text), (
        "staff.js must not request the staff directory without a selected store; without that "
        "guard the exemption in STORE_GATE_EXEMPT is unsafe"
    )
    assert re.search(r"\} else if \(store\) \{\s*void reload\(\);\s*\} else \{", text), (
        "staff.js must render a no-store notice instead of the directory when no store is set"
    )
    assert "Chưa chọn cửa hàng" in text
