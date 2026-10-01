"""`OPS-HARDENING-002`: five small operational defects, each pinned against the rendered topology.

Every assertion here reads what `docker compose config` actually produces for a combination of
files somebody runs, not what one file says in isolation. That distinction is the fourth defect in
this list: `ports: []` in an overlay *appends* to the list below it, so the production overlay that
looked like it published nothing published the development superuser on every interface.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[3]

#: Every combination a runbook or contract test brings up, with the profiles that change it.
COMBINATIONS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("compose.yaml",), ()),
    (("compose.yaml", "compose.production.yaml"), ()),
    (("compose.yaml", "compose.production.yaml"), ("local-database",)),
    (("compose.yaml", "compose.production.yaml", "compose.demo.yaml"), ()),
    (("compose.yaml", "compose.production.yaml", "compose.demo.yaml"), ("local-database",)),
    (("compose.yaml", "compose.production.yaml", "deploy/staging/compose.smoke.yaml"), ()),
    (
        ("compose.yaml", "compose.production.yaml", "deploy/staging/compose.smoke.yaml"),
        ("local-database",),
    ),
    (("compose.r1.yaml",), ()),
    (("compose.r1.yaml",), ("self-managed-database",)),
    (("compose.r1.yaml", "compose.shop-local.yaml"), ("self-managed-database",)),
    (
        ("compose.r1.yaml", "compose.shop-local.yaml", "compose.shop-till.yaml"),
        ("self-managed-database",),
    ),
)


def _render(files: tuple[str, ...], profiles: tuple[str, ...], tmp: Path) -> dict[str, Any]:
    command = ["docker", "compose"]
    for name in files:
        command.extend(("-f", name))
    for profile in profiles:
        command.extend(("--profile", profile))
    command.extend(("config", "--format", "json"))
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("R1_", "STAGING_", "COMPOSE_"))
    }
    environment["R1_LOCAL_ARCHIVE_PATH"] = str(tmp)
    environment["STAGING_SMOKE_SECRET_DIRECTORY"] = str(tmp)
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=environment)
    if result.returncode != 0:
        if "docker" in result.stderr and "not found" in result.stderr:
            pytest.skip("docker compose is unavailable")
        raise AssertionError(f"{' + '.join(files)} does not render: {result.stderr[-400:]}")
    return cast(dict[str, Any], json.loads(result.stdout))


def _label(files: tuple[str, ...], profiles: tuple[str, ...]) -> str:
    return " + ".join(files) + "".join(f" --profile {p}" for p in profiles)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    try:
        subprocess.run(["docker", "compose", "version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("docker compose is unavailable")
    tmp = tmp_path_factory.mktemp("compose")
    return {
        _label(files, profiles): _render(files, profiles, tmp) for files, profiles in COMBINATIONS
    }


# --- 4. Nothing published beyond loopback -------------------------------------------------------


def test_no_combination_publishes_anything_beyond_loopback(
    rendered: dict[str, dict[str, Any]],
) -> None:
    """`compose.yaml` published `app`/`app`, a superuser, on 0.0.0.0:5432.

    `compose.production.yaml` said `ports: []` for it, which reads as "publish nothing" and, being
    an overlay, appended nothing to the list below -- so `--profile local-database` exposed the
    development superuser on every interface of the staging host.
    """

    exposed: list[str] = []
    for label, document in rendered.items():
        for name, service in document["services"].items():
            for port in service.get("ports", []):
                if port.get("host_ip") != "127.0.0.1":
                    exposed.append(f"{label}: {name} publishes {port}")
    assert not exposed, "\n".join(exposed)


def test_no_compose_file_says_publish_nothing_in_a_way_that_appends() -> None:
    """The trap itself, so the next overlay cannot fall into it: an empty list is not a reset."""

    offenders: list[str] = []
    for path in [*ROOT.glob("compose*.yaml"), *ROOT.glob("deploy/**/compose*.yaml")]:
        for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if re.match(r"^\s+ports:\s*\[\]\s*(#.*)?$", line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, "use `ports: !reset []`:\n" + "\n".join(offenders)


# --- 3. Every container's log is bounded ---------------------------------------------------------

#: A shop Mac has one disk, and the database is on it. Unbounded json-file logs are how a chatty
#: container fills the disk the WAL needs.
MAX_LOG_BYTES_PER_SERVICE = 100 * 1024 * 1024


def _bytes(size: str) -> int:
    match = re.fullmatch(r"(\d+)([kmg]?)", size.strip().lower())
    assert match, f"unparseable max-size {size!r}"
    unit = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match.group(2)]
    return int(match.group(1)) * unit


def test_every_service_in_every_combination_rotates_its_log(
    rendered: dict[str, dict[str, Any]],
) -> None:
    unbounded: list[str] = []
    for label, document in rendered.items():
        for name, service in document["services"].items():
            logging = service.get("logging") or {}
            options = logging.get("options") or {}
            if logging.get("driver") != "json-file" or not {"max-size", "max-file"} <= set(options):
                unbounded.append(f"{label}: {name} logging={logging}")
                continue
            total = _bytes(options["max-size"]) * int(options["max-file"])
            if total > MAX_LOG_BYTES_PER_SERVICE:
                unbounded.append(f"{label}: {name} may keep {total} bytes of log")
    assert not unbounded, "\n".join(unbounded)


#: `OPS-OBSERVABILITY-009` (review P2). The API's log is what `check_shop_operations.py --check app`
#: reads and the only record of what the application did; it rotated away in one to three days.
#:
#: Round 9 (verifier): the first model counted only the console's requests at a hand-measured
#: 553 bytes. It left out the image's own Docker `HEALTHCHECK` -- `/healthz` every 30 s, all day,
#: 2,880 lines -- and the conformance run then wrote a 584-byte line. So the model is now built
#: from the things that decide it rather than from a sample: the longest line any route the API
#: serves can produce, computed from the route table through the API's own logger; the healthcheck
#: interval read from `apps/api/Dockerfile` (or the rendered compose override); the console check's
#: interval read from `deploy/shop-till/install.sh`. Only the traffic itself is an assumption:
#:
#: - 100 orders (above the owner's 300-400 kg/day ceiling at ~4 kg an order), each at the daily
#:   walk's own ratio of API lines to orders. 2026-10-01, port 8108: 506 API lines (501
#:   `http.request.completed`, 5 `auth.*`) for 7 order submissions -> 73 an order. The walk opens a
#:   fresh browser every time, so the console's static files are in that figure too.
#: - three consoles open fourteen hours, each polling once a minute (`ui/promise.js`).
#:
#: Every one of those lines is costed at the longest line any route can write; only the two probes,
#: whose route is fixed, are costed at their own length.
ORDERS_PER_BUSY_DAY = 100
API_LINES_PER_ORDER = 73
CONSOLE_POLL_LINES_PER_DAY = 3 * 14 * 60
API_LOG_RETENTION_DAYS = 14
#: The longest stored line the conformance run wrote (round-9 verifier, `/quotes/{id}/range-prices/
#: {id}`). The computed bound must never be below what was actually seen.
LONGEST_LINE_SEEN = 584
SECONDS_PER_DAY = 24 * 60 * 60
#: RFC3339Nano at full length; Docker trims trailing zeros, so no stored time is longer.
_LONGEST_DOCKER_TIME = "2026-10-01T12:34:56.123456789Z"


def _stored_bytes(line: str) -> int:
    """What json-file writes for one line of stdout: `{"log":…,"stream":…,"time":…}` and a newline.

    Docker escapes the line as a JSON string, HTML-safe (`<`, `>`, `&` as `\\u00XX`).
    """

    escaped = json.dumps(line + "\n", ensure_ascii=False)
    for character in "<>&":
        escaped = escaped.replace(character, f"\\u{ord(character):04x}")
    record = f'{{"log":{escaped},"stream":"stdout","time":"{_LONGEST_DOCKER_TIME}"}}\n'
    return len(record.encode("utf-8"))


def _request_line(method: str, path: str) -> str:
    """The `http.request.completed` line the API writes for this request, at its longest."""

    from types import SimpleNamespace

    from nha_trang_laundry_api import main as api_main
    from nha_trang_laundry_observability import CorrelationContext, SafeStructuredLogger

    written: list[str] = []
    original = api_main._LOGGER
    api_main._LOGGER = SafeStructuredLogger(sink=written.append)
    try:
        request = SimpleNamespace(method=method, url=SimpleNamespace(path=path))
        api_main._record_http_completed(request, 503, CorrelationContext.new())  # type: ignore[arg-type]
    finally:
        api_main._LOGGER = original
    (line,) = written
    # `isoformat` drops the microseconds when they are zero; cost every line at the long form.
    return re.sub(
        r'"occurred_at":"[^"]+"', '"occurred_at":"2026-10-01T12:34:56.123456+00:00"', line
    )


#: A UUID with no run of digits, so the logger's phone-number redaction leaves it alone ...
_IDENTIFIER = "ffffffff-ffff-4fff-bfff-ffffffffffff"
#: ... and what that redaction could add to a real one. `[REDACTED]` is ten bytes and the shortest
#: thing it replaces is nine digits, so a 36-character UUID (32 hex digits) grows by at most 3.
_REDACTION_GROWTH_PER_IDENTIFIER = 3


def _longest_request_line(stored: Callable[[str], int] = _stored_bytes) -> tuple[int, str]:
    from fastapi.routing import APIRoute
    from nha_trang_laundry_api import main as api_main

    candidates: list[tuple[str, str, int]] = []
    for route in api_main.app.routes:
        if isinstance(route, APIRoute):
            # Every path parameter is a UUID except the statement month (`2026-09`) and a role
            # name, which is shorter than a UUID; a UUID is the upper bound for both.
            path = route.path.replace("{month}", "2026-09")
            identifiers = len(re.findall(r"\{[^}]+\}", path))
            path = re.sub(r"\{[^}]+\}", _IDENTIFIER, path)
            candidates.extend((method, path, identifiers) for method in route.methods or ())
    web = Path(api_main.WEB_DIRECTORY)
    candidates.extend(
        ("GET", "/staff/" + file.relative_to(web).as_posix(), 0)
        for file in web.rglob("*")
        if file.is_file()
    )
    return max(
        (
            stored(_request_line(method, path)) + identifiers * _REDACTION_GROWTH_PER_IDENTIFIER,
            f"{method} {path}",
        )
        for method, path, identifiers in candidates
    )


def _seconds(duration: str) -> int:
    match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", duration.strip())
    assert match and any(match.groups()), f"unparseable interval {duration!r}"
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


def _healthcheck_interval(api: dict[str, Any]) -> int:
    """Seconds between Docker's own `/healthz` probes: the compose override, else the image's."""

    override = (api.get("healthcheck") or {}).get("interval")
    if override:
        return _seconds(str(override))
    dockerfile = (ROOT / "apps/api/Dockerfile").read_text("utf-8")
    match = re.search(r"^HEALTHCHECK\b.*?--interval=(\S+)", dockerfile, re.MULTILINE)
    assert match, "apps/api/Dockerfile has no HEALTHCHECK interval; the model needs one"
    assert "/healthz" in dockerfile[match.start() : match.start() + 400]
    return _seconds(match.group(1))


def _console_check_interval() -> int:
    install = (ROOT / "deploy/shop-till/install.sh").read_text("utf-8")
    match = re.search(r"every_five_minutes='<key>StartInterval</key><integer>(\d+)<", install)
    assert match, "the host checks' StartInterval moved; the model reads it from install.sh"
    return int(match.group(1))


def _file_bytes(line: str) -> int:
    """What the API's own log file holds for one line: the line and its newline, nothing else."""

    return len((line + "\n").encode("utf-8"))


def _busy_day_bytes(
    api: dict[str, Any], stored: Callable[[str], int] = _stored_bytes
) -> tuple[int, dict[str, int]]:
    longest, _ = _longest_request_line(stored)
    healthz = stored(_request_line("GET", "/healthz"))
    readyz = stored(_request_line("GET", "/readyz"))
    lines = {
        "orders": ORDERS_PER_BUSY_DAY * API_LINES_PER_ORDER,
        "polls": CONSOLE_POLL_LINES_PER_DAY,
        "readyz": SECONDS_PER_DAY // _console_check_interval(),
        "healthcheck": SECONDS_PER_DAY // _healthcheck_interval(api),
    }
    total = (
        (lines["orders"] + lines["polls"]) * longest
        + lines["readyz"] * readyz
        + lines["healthcheck"] * healthz
    )
    return total, lines


def test_the_retention_model_is_built_from_what_writes_the_log(
    rendered: dict[str, dict[str, Any]],
) -> None:
    """The pieces the first model got wrong, pinned: the healthcheck is in it, and no line the API
    can write is longer than the figure the arithmetic uses."""

    longest, where = _longest_request_line()
    assert longest >= LONGEST_LINE_SEEN, where
    assert _stored_bytes(_request_line("GET", "/healthz")) == 441  # measured on the json-file log
    for label, document in rendered.items():
        api = document["services"].get("api")
        if api is None:
            continue
        _, lines = _busy_day_bytes(api)
        assert lines["healthcheck"] == SECONDS_PER_DAY // 30, label
        assert lines["readyz"] == SECONDS_PER_DAY // 300, label


def _api_log_mount(api: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """The log file the API writes, and the mount its directory lives on (if any)."""

    path = str((api.get("environment") or {}).get("STRUCTURED_LOG_FILE") or "")
    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    for volume in api.get("volumes") or []:
        if isinstance(volume, dict) and volume.get("target") == directory:
            return path, volume
    return path, None


def test_the_api_log_outlives_its_container(rendered: dict[str, dict[str, Any]]) -> None:
    """`PLATFORM-RESIDUAL-009B` L4. Docker's json-file log is removed with the container, and every
    image update recreates it: the record has to be on the host. Each api service writes its
    structured log to a file whose directory is a bind mount of a host path -- not a tmpfs, not an
    anonymous volume, not the container's own (read-only) filesystem."""

    wrong: list[str] = []
    checked = 0
    for label, document in rendered.items():
        api = document["services"].get("api")
        if api is None:
            continue
        checked += 1
        path, mount = _api_log_mount(api)
        if not path:
            wrong.append(f"{label}: api sets no STRUCTURED_LOG_FILE")
        elif mount is None:
            wrong.append(f"{label}: {path} is not on a mounted directory")
        elif mount.get("type") != "bind" or not str(mount.get("source", "")).startswith("/"):
            wrong.append(f"{label}: {path} is on {mount}, not a host directory")
        elif mount.get("read_only"):
            wrong.append(f"{label}: the API cannot write {path}")
    assert checked, "no rendered combination has an api service"
    assert not wrong, "\n".join(wrong)


def test_the_api_log_keeps_fourteen_busy_days(rendered: dict[str, dict[str, Any]]) -> None:
    """The file rotates inside the process (`RotatingFileHandler`): it rolls over before the line
    that would pass `LOG_FILE_MAX_BYTES`, so each of the `LOG_FILE_BACKUP_COUNT` rotated files holds
    at least the maximum less one line -- that is the history guaranteed.

    Round 9 held Docker's json-file log to this; it is deleted with the container, so the model now
    holds the file that survives (L4)."""

    from nha_trang_laundry_observability import logging_setup

    longest, _ = _longest_request_line(_file_bytes)
    kept = logging_setup.LOG_FILE_BACKUP_COUNT * (logging_setup.LOG_FILE_MAX_BYTES - longest)
    short: list[str] = []
    checked = 0
    for label, document in rendered.items():
        api = document["services"].get("api")
        if api is None:
            continue
        checked += 1
        per_day, _ = _busy_day_bytes(api, _file_bytes)
        needed = per_day * API_LOG_RETENTION_DAYS
        if kept < needed:
            short.append(
                f"{label}: api keeps {kept} bytes for certain = {kept / per_day:.1f} busy days; "
                f"{API_LOG_RETENTION_DAYS} need {needed}"
            )
    assert checked, "no rendered combination has an api service"
    assert not short, "\n".join(short)
    # And the figures the compose comment states are the ones computed here.
    assert (longest, _file_bytes(_request_line("GET", "/healthz"))) == (481, 329)
    assert kept == 102_736_879
    for compose in ("compose.r1.yaml", "compose.production.yaml"):
        text = (ROOT / compose).read_text("utf-8")
        assert "5,765,404 bytes a day" in text and "102,736,879" in text, compose


def test_the_api_log_on_disk_is_bounded() -> None:
    from nha_trang_laundry_observability import logging_setup

    total = (logging_setup.LOG_FILE_BACKUP_COUNT + 1) * logging_setup.LOG_FILE_MAX_BYTES
    assert total <= MAX_LOG_BYTES_PER_SERVICE


# --- 5. The WAL staging area fits a segment ------------------------------------------------------

#: `initdb`'s default, and nothing in this repository changes it: no `--wal-segsize` in any
#: `POSTGRES_INITDB_ARGS`. Asserted below so a change to it breaks this arithmetic loudly.
WAL_SEGMENT_BYTES = 16 * 1024 * 1024


def _worst_case_gzip(size: int) -> int:
    """zlib's `deflateBound` plus the gzip wrapper: incompressible input grows, it never shrinks.

    `sourceLen + (sourceLen >> 12) + (sourceLen >> 14) + (sourceLen >> 25) + 13 - 6`, plus an
    18-byte header and trailer and the stored file name. Busybox and GNU gzip are both inside this.
    """

    return size + (size >> 12) + (size >> 14) + (size >> 25) + 7 + 18 + 256


def _worst_case_age(size: int, recipients: int = 8) -> int:
    """age v1: one header stanza per recipient, then 64 KiB chunks each with a 16-byte tag."""

    header = 64 + 16 + recipients * 128
    chunks = size // (64 * 1024) + 1
    return header + size + chunks * 16


def _tmpfs_bytes(entries: list[str], mount: str) -> int:
    for entry in entries:
        target, _, options = entry.partition(":")
        if target == mount:
            match = re.search(r"size=(\d+)([kmg]?)", options)
            assert match, entry
            return _bytes(match.group(1) + match.group(2))
    raise AssertionError(f"no tmpfs at {mount}")


def test_the_postgres_tmp_holds_a_worst_case_segment_twice_over(
    rendered: dict[str, dict[str, Any]],
) -> None:
    """`archive-wal.sh` stages `<segment>.gz` and `<segment>.gz.age` in /tmp at the same time.

    A full, incompressible 16 MiB segment is therefore ~32 MiB on a 16 MiB tmpfs: `gzip` hits
    ENOSPC, `archive_command` fails, the segment is retried forever, WAL is pinned, and the disk
    that holds the shop's orders fills. Idle segments compress to kilobytes, which is why nobody saw
    it -- the failure waits for the first busy afternoon.
    """

    archive = (ROOT / "deploy/production/backup/archive-wal.sh").read_text("utf-8")
    assert 'compressed="/tmp/' in archive and 'encrypted="/tmp/' in archive
    for path in [*ROOT.glob("compose*.yaml")]:
        assert "wal-segsize" not in path.read_text("utf-8"), path

    needed = _worst_case_gzip(WAL_SEGMENT_BYTES) + _worst_case_age(
        _worst_case_gzip(WAL_SEGMENT_BYTES)
    )
    for label in (
        "compose.r1.yaml --profile self-managed-database",
        "compose.r1.yaml + compose.shop-local.yaml + compose.shop-till.yaml"
        " --profile self-managed-database",
    ):
        postgres = rendered[label]["services"]["postgres"]
        size = _tmpfs_bytes(postgres["tmpfs"], "/tmp")
        assert size >= needed, f"{label}: /tmp is {size} bytes, one segment needs {needed}"
        # Headroom for a second pair left behind by an archiver killed mid-segment.
        assert size >= 2 * needed, f"{label}: /tmp is {size} bytes, want {2 * needed}"


# --- 1. Readiness is used where it tells the truth, and nowhere it would loop --------------------


def test_the_console_check_asks_for_readiness_and_the_container_does_not(
    rendered: dict[str, dict[str, Any]],
) -> None:
    """`/healthz` answers from the process alone, so a console whose database is gone read healthy.

    `/readyz` touches the database. It is what the *console check* asks, because that check stands
    in for a person at the counter. It is deliberately **not** the container healthcheck: `tls`
    waits on `api` being healthy, so a readiness healthcheck would make the TLS listener depend on
    the database, and a database blip would take the console's front door down with it.
    """

    install = (ROOT / "deploy/shop-till/install.sh").read_text("utf-8")
    assert "R1_CONSOLE_HEALTH_URL=https://console.giatlasachcong.lan:8443/readyz" in install
    for name in ("shop-pilot.md", "shop-till-mac.md", "production-deploy-day.md"):
        runbook = (ROOT / "docs/runbooks" / name).read_text("utf-8")
        for line in runbook.splitlines():
            if "R1_CONSOLE_HEALTH_URL=" in line:
                assert "/readyz" in line, f"{name}: {line.strip()}"

    dockerfile = (ROOT / "apps/api/Dockerfile").read_text("utf-8")
    healthcheck = dockerfile[dockerfile.index("HEALTHCHECK") :].split("\n\n")[0]
    assert "/healthz" in healthcheck and "/readyz" not in healthcheck

    for label, document in rendered.items():
        api = document["services"].get("api")
        if api is None:
            continue
        test = json.dumps(api.get("healthcheck", {}))
        assert "/readyz" not in test, f"{label}: the api healthcheck must stay process-only"
        tls = document["services"].get("tls", {})
        depends = tls.get("depends_on", {})
        assert "postgres" not in depends, f"{label}: tls must not wait for the database"


def test_the_console_check_reports_a_503_as_not_ready_rather_than_unreachable() -> None:
    """A console that answers 503 is up and cannot serve.

    "Did not answer" would send the owner looking at the network instead of the database.
    """

    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    sys.path.insert(0, str(ROOT / "scripts"))
    import check_shop_operations as checks  # type: ignore[import-not-found]

    class _NotReady(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b'{"status": "unavailable", "dependency": "database"}'
            self.send_response(503)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), _NotReady)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = checks.check_console_reachable(
            f"http://127.0.0.1:{server.server_address[1]}/readyz"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.passed is False
    assert "answered 503" in result.detail and "database" in result.detail
    assert "did not answer" not in result.detail
    assert result.fields["status"] == 503


# --- 7. apk pins: assessed, deliberately unchanged here -----------------------------------------
#
# `deploy/production/Postgres.Dockerfile` pins `age=1.2.1-r10`, `gzip=1.14-r2`, `rclone=1.69.3-r5`
# onto `postgres:16.10-alpine`, a tag rather than a digest, and those three are downloaded. Each
# Alpine branch keeps only the newest `-rN`, so an uncached rebuild stops resolving when any is
# rebuilt (rclone is rebuilt with every Go update). The fix is `~<version>`, but
# `test_backup_restore_contract.py` asserts the literal `rclone=` and changing that assertion is not
# this item's call, and no uncached build can be run here to prove either form. Recorded as a
# residual in `docs/runbooks/shop-till-mac.md` §8 with the recovery step, not fixed.


# --- 2. Every application connection is bounded ---------------------------------------------------


def test_every_application_connection_goes_through_the_bounded_factory() -> None:
    """`OPS-HARDENING-002` item 2. The defect was the default: `psycopg.connect`, seven times."""

    from nha_trang_laundry_agent_tools.backend import DomainAgentToolBackend, build_domain_backend
    from nha_trang_laundry_api.assistant import AssistantService
    from nha_trang_laundry_api.auth import StaffIdentityService
    from nha_trang_laundry_api.operations import OperationsService
    from nha_trang_laundry_api.ops_board import OpsBoardService
    from nha_trang_laundry_db.connection import application_connect
    from nha_trang_laundry_worker.host import WorkerSupervisor

    for owner in (
        StaffIdentityService,
        OperationsService,
        OpsBoardService,
        AssistantService,
        WorkerSupervisor,
        DomainAgentToolBackend,
        build_domain_backend,
    ):
        default = inspect.signature(owner).parameters["connection_factory"].default
        assert default is application_connect, f"{owner.__qualname__} connects unbounded"
