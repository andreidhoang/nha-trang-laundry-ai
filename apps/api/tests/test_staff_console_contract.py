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
        "/internal/v1/shadow/unknown-sends",
        "/internal/v1/stores/{}/assistant/turns",
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
STORE_GATE_EXEMPT = {"today.js"}

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
