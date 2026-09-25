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
