"""`SHOP-ALERT-DELIVERY-001`: a backup or disk alert has to reach a person.

The data checks run in a container attached only to `database-private`, which is `internal: true`
(ADR-0007 §1), so the container has no route to `api.telegram.org`. The check used to try anyway,
catch the `URLError`, and return -- and `install.sh` never passed it a credential in the first
place. So the two checks `DEC-025` names as the ones nobody goes looking for, the WAL archive gap
and the filling volume, could not tell anyone, and nothing said so.

The fix keeps the network internal and moves delivery to the host: the check *emits* its alert as
one structured line and needs no egress; `scripts/relay_shop_alert.py` runs the check, reads that
line, and posts it from the host, which has internet. A delivery that fails is a non-zero exit and
a log line, never a silent return.

**What these tests prove and what they do not.** Every test below talks to a local HTTP stub that
stands in for the Telegram Bot API, over a real socket, through the real relay and the real check
script run as subprocesses. None of them reaches Telegram, and none of them proves a message
arrives on the owner's phone. That is the owner's first-install step in
`docs/runbooks/shop-till-mac.md` §5, and until it has been done the path is built, not watched.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import os
import plistlib
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[3]
RELAY = ROOT / "scripts/relay_shop_alert.py"
CHECK = ROOT / "scripts/check_shop_operations.py"
INSTALL = ROOT / "deploy/shop-till/install.sh"

#: Shaped like a real bot token so the relay's format check accepts it, and obviously not one.
TOKEN = "123456789:local-stub-token-never-a-real-bot"
CHAT_ID = "987654321"


@dataclass
class _Stub:
    """A local stand-in for `https://api.telegram.org`, recording every request it receives."""

    status: int = 200
    body: bytes = b'{"ok": true, "result": {"message_id": 1}}'
    requests: list[dict[str, Any]] = field(default_factory=list)
    base: str = ""


@pytest.fixture
def telegram_stub() -> Iterator[_Stub]:
    stub = _Stub()

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            stub.requests.append(
                {
                    "path": self.path,
                    "content_type": self.headers.get("Content-Type"),
                    "form": {key: values[0] for key, values in urllib.parse.parse_qs(raw).items()},
                }
            )
            self.send_response(stub.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(stub.body)))
            self.end_headers()
            self.wfile.write(stub.body)

        def log_message(self, *_: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    stub.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def credentials(tmp_path: Path) -> dict[str, str]:
    """The host-side credential files, 0600, outside the repository -- as the runbook makes them."""

    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    token = secrets / "alert_telegram_token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    token.chmod(0o600)
    chat = secrets / "alert_telegram_chat_id"
    chat.write_text(CHAT_ID + "\n", encoding="utf-8")
    chat.chmod(0o600)
    return {
        "R1_ALERT_TELEGRAM_TOKEN_FILE": str(token),
        "R1_ALERT_TELEGRAM_CHAT_ID_FILE": str(chat),
        "R1_ALERT_LOG_FILE": str(tmp_path / "alert-delivery.log"),
    }


def _environment(extra: dict[str, str]) -> dict[str, str]:
    inherited = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("R1_ALERT_", "R1_CONSOLE_", "DATABASE_URL"))
    }
    return {**inherited, **extra}


def _check(*arguments: str) -> list[str]:
    return [sys.executable, str(CHECK), *arguments, "--emit-alert"]


def _relay(
    command: list[str], environment: dict[str, str], *, label: str = "checks-data"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RELAY), "--label", label, "--", *command],
        cwd=ROOT,
        env=_environment(environment),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# --- The end-to-end path: real check, real relay, local stub ------------------------------------


def test_a_forced_failure_reaches_the_stub_exactly_once_in_the_checks_own_words(
    telegram_stub: _Stub, credentials: dict[str, str], tmp_path: Path
) -> None:
    """The case the whole item exists for: a backup check fails and somebody is told.

    `base_backup_age` alerts at any hour under `DEC-025`, so this cannot pass or fail by the clock.
    """

    missing = tmp_path / "staging" / "last-success"
    result = _relay(
        _check("--check", "base", "--base-backup-marker", str(missing)),
        {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base},
    )

    assert result.returncode == 1, result.stderr  # the check's failure, delivered
    assert len(telegram_stub.requests) == 1, telegram_stub.requests
    request = telegram_stub.requests[0]
    assert request["path"] == f"/bot{TOKEN}/sendMessage"
    assert request["content_type"] == "application/x-www-form-urlencoded"
    assert request["form"]["chat_id"] == CHAT_ID
    assert request["form"]["text"] == (
        "Bảng vận hành — cần xem ngay:\n"
        f"• base_backup_age: no base backup has ever completed (no marker at {missing})"
    )

    log = Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")
    assert "ALERT DELIVERED" in log and "base_backup_age" in log
    assert TOKEN not in log + result.stderr + result.stdout


def test_a_passing_check_sends_nothing(
    telegram_stub: _Stub, credentials: dict[str, str], tmp_path: Path
) -> None:
    marker = tmp_path / "last-success"
    marker.write_text("base-20260925T023000Z 3001743", encoding="utf-8")

    result = _relay(
        _check("--check", "base", "--base-backup-marker", str(marker)),
        {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base},
    )

    assert result.returncode == 0, result.stderr
    assert telegram_stub.requests == []


@pytest.mark.parametrize(
    ("status", "body", "reported"),
    [
        (500, b'{"ok": false, "description": "Internal Server Error"}', "HTTP 500"),
        # Telegram answers 200 with `ok: false` for some refusals. A 2xx is not a delivery.
        (200, b'{"ok": false, "description": "Bad Request: chat not found"}', "ok=false"),
        (200, b"<html>captive portal</html>", "not JSON"),
    ],
)
def test_a_refused_delivery_is_a_non_zero_exit_and_a_log_line(
    telegram_stub: _Stub,
    credentials: dict[str, str],
    tmp_path: Path,
    status: int,
    body: bytes,
    reported: str,
) -> None:
    telegram_stub.status = status
    telegram_stub.body = body

    result = _relay(
        _check("--check", "base", "--base-backup-marker", str(tmp_path / "missing")),
        {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base},
    )

    assert result.returncode == 3, (result.returncode, result.stderr)
    assert len(telegram_stub.requests) == 1
    assert "ALERT NOT DELIVERED" in result.stderr and reported in result.stderr
    log = Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")
    assert "ALERT NOT DELIVERED" in log and reported in log
    assert TOKEN not in log + result.stderr + result.stdout, "the token is in the request path"


def test_an_unreachable_telegram_is_a_non_zero_exit_and_a_log_line(
    credentials: dict[str, str], tmp_path: Path
) -> None:
    result = _relay(
        _check("--check", "base", "--base-backup-marker", str(tmp_path / "missing")),
        {**credentials, "R1_ALERT_TELEGRAM_API_BASE": f"http://127.0.0.1:{_closed_port()}"},
    )

    assert result.returncode == 3, (result.returncode, result.stderr)
    assert "ALERT NOT DELIVERED" in result.stderr
    assert "ALERT NOT DELIVERED" in Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")
    assert TOKEN not in result.stderr


def test_a_missing_credential_with_something_to_say_is_a_visible_failure(
    telegram_stub: _Stub, credentials: dict[str, str], tmp_path: Path
) -> None:
    """Unconfigured used to be silent. With an alert to deliver, silence is the defect."""

    Path(credentials["R1_ALERT_TELEGRAM_TOKEN_FILE"]).unlink()
    result = _relay(
        _check("--check", "base", "--base-backup-marker", str(tmp_path / "missing")),
        {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base},
    )

    assert result.returncode == 3
    assert telegram_stub.requests == []
    assert "ALERT NOT DELIVERED" in result.stderr and "alert_telegram_token" in result.stderr


def test_a_check_that_could_not_run_is_itself_the_alert(
    telegram_stub: _Stub, credentials: dict[str, str]
) -> None:
    """Docker not running, the image missing, the network gone: the container never answers.

    That is the moment the backup checks are blind, so it is reported rather than logged away.
    """

    cannot_run = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('Cannot connect to the Docker daemon\\n'); sys.exit(125)",
    ]
    result = _relay(cannot_run, {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base})

    assert result.returncode == 1, result.stderr
    assert len(telegram_stub.requests) == 1
    text = telegram_stub.requests[0]["form"]["text"]
    assert text.startswith("Bảng vận hành — cần xem ngay:")
    assert "checks-data" in text and "125" in text and "Cannot connect to the Docker daemon" in text


def test_the_check_does_not_deliver_when_it_emits(
    telegram_stub: _Stub, credentials: dict[str, str], tmp_path: Path
) -> None:
    """One alert, one sender. With `--emit-alert` the check posts nothing even if it could.

    The relay also strips the credential variables from the child's environment, so a check run on
    the host (the capability flags, the console) cannot double-send either.
    """

    token_file = Path(credentials["R1_ALERT_TELEGRAM_TOKEN_FILE"])
    result = subprocess.run(
        _check("--check", "base", "--base-backup-marker", str(tmp_path / "missing")),
        cwd=ROOT,
        env=_environment(
            {
                "R1_ALERT_TELEGRAM_TOKEN_FILE": str(token_file),
                "R1_ALERT_TELEGRAM_CHAT_ID": CHAT_ID,
                "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base,
            }
        ),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 1
    assert telegram_stub.requests == []
    documents = [
        json.loads(line)
        for line in result.stdout.splitlines()
        if line.startswith("{") and "nha-trang-laundry.shop-alert.v1" in line
    ]
    assert len(documents) == 1
    document = documents[0]
    assert document["passed"] is False
    assert document["alert"]["checks"] == ["base_backup_age"]
    assert document["alert"]["text"].startswith("Bảng vận hành — cần xem ngay:")
    # The human lines move to stderr so stdout stays parseable.
    assert "!!! base_backup_age" in result.stderr


def test_quiet_hours_are_decided_where_the_failure_is_seen() -> None:
    """`DEC-025`: the console may wait for opening; a guarantee going away may not."""

    sys.path.insert(0, str(ROOT / "scripts"))
    import check_shop_operations as checks  # type: ignore[import-not-found]

    night = datetime(2026, 9, 2, 20, tzinfo=UTC)  # 03:00 in Nha Trang
    console = checks.CheckResult("console_reachable", passed=False, detail="down", fields={})
    wal = checks.CheckResult("wal_archive_gap", passed=False, detail="stale", fields={})

    quiet = checks.alert_document([console], now=night)
    assert quiet["passed"] is False and quiet["alert"] is None
    assert quiet["suppressed"] == ["console_reachable"]

    loud = checks.alert_document([console, wal], now=night)
    assert loud["alert"]["checks"] == ["wal_archive_gap"]

    opening = checks.alert_document([console], now=night + timedelta(hours=4, minutes=45))
    assert opening["alert"]["checks"] == ["console_reachable"]


def test_the_test_only_api_override_cannot_send_a_token_anywhere_but_loopback(
    telegram_stub: _Stub, credentials: dict[str, str], tmp_path: Path
) -> None:
    """The base URL is overridable so these tests can run. It must not be a way to leak a token."""

    for refused in (
        "https://api.telegram.org.evil.example",
        "http://api.telegram.org",
        "http://10.0.0.5:8080",
        f"http://user@127.0.0.1:{_closed_port()}",
        telegram_stub.base + "/prefix",
    ):
        result = _relay(
            _check("--check", "base", "--base-backup-marker", str(tmp_path / "missing")),
            {**credentials, "R1_ALERT_TELEGRAM_API_BASE": refused},
        )
        assert result.returncode == 2, (refused, result.stderr)
        assert "R1_ALERT_TELEGRAM_API_BASE" in result.stderr
    assert telegram_stub.requests == []

    sys.path.insert(0, str(ROOT / "scripts"))
    import relay_shop_alert as relay  # type: ignore[import-not-found]

    assert relay.TELEGRAM_API_BASE == "https://api.telegram.org"
    assert relay.resolve_api_base(None) == "https://api.telegram.org"


def test_the_relay_never_touches_the_send_machinery() -> None:
    """`DEC-025`: a monitoring notification, not an automated send -- no outbox, no channel."""

    source = RELAY.read_text("utf-8")
    imports = "\n".join(
        line for line in source.splitlines() if line.lstrip().startswith(("import ", "from "))
    )
    for forbidden in ("nha_trang_laundry", "outbox", "channel", "consent", "psycopg"):
        assert forbidden not in imports, forbidden
    # No inbound handler: nothing here listens, polls or reads updates.
    for forbidden in ("getUpdates", "setWebhook", "HTTPServer", "socketserver"):
        assert forbidden not in source, forbidden


# --- The launch agents the till actually runs ----------------------------------------------------


@pytest.fixture
def installed_agents(tmp_path: Path) -> dict[str, dict[str, Any]]:
    """Run `install.sh` for real against a scratch HOME, with `launchctl` stubbed."""

    home = tmp_path / "home"
    home.mkdir()
    archive = tmp_path / "archive"
    archive.mkdir()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    launchctl = stubs / "launchctl"
    launchctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launchctl.chmod(0o755)

    result = subprocess.run(
        ["bash", str(INSTALL)],
        env={
            "HOME": str(home),
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "R1_LOCAL_ARCHIVE_PATH": str(archive),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    agents: dict[str, dict[str, Any]] = {}
    for plist in sorted((home / "Library/LaunchAgents").glob("*.plist")):
        # `plistlib` is as strict as launchd: an unescaped `&&` in a <string> is not XML, and
        # `launchctl bootstrap` refuses the whole file.
        agents[plist.stem.rsplit(".", 1)[-1]] = plistlib.loads(plist.read_bytes())
    return agents


def test_every_launch_agent_is_a_plist_launchd_can_load(
    installed_agents: dict[str, dict[str, Any]],
) -> None:
    assert set(installed_agents) == {"checks-data", "checks-host", "checks-daily", "base-backup"}
    for agent in installed_agents.values():
        assert agent["ProgramArguments"][:2] == ["/bin/bash", "-lc"]


def test_both_check_agents_deliver_through_the_host_relay(
    installed_agents: dict[str, dict[str, Any]],
) -> None:
    for name in ("checks-data", "checks-host", "checks-daily"):
        agent = installed_agents[name]
        script = agent["ProgramArguments"][2]
        assert "scripts/relay_shop_alert.py" in script, name
        assert "--emit-alert" in script, name
        environment = agent["EnvironmentVariables"]
        # Paths to host files, never the values, and never a repository-tracked file.
        for key in ("R1_ALERT_TELEGRAM_TOKEN_FILE", "R1_ALERT_TELEGRAM_CHAT_ID_FILE"):
            assert environment[key].startswith(str(ROOT / ".shop/secrets/")), (name, key)
        assert environment["R1_ALERT_LOG_FILE"].endswith("alert-delivery.log")
        assert "R1_ALERT_TELEGRAM_API_BASE" not in environment, "test-only; never installed"

    data = installed_agents["checks-data"]["ProgramArguments"][2]
    docker_run = data[data.index("docker run") :]
    # Nothing Telegram crosses into the internal-network container: it could not use it.
    assert "R1_ALERT" not in docker_run
    assert "--network nha-trang-laundry-shop_database-private" in docker_run


def test_the_application_check_keeps_its_read_position_outside_the_checkout(
    installed_agents: dict[str, dict[str, Any]],
) -> None:
    """`OPS-OBSERVABILITY-009`, round 9: every run counts from where the last one stopped, so the
    position has to survive between runs -- and a `git clean` of the checkout."""

    words = shlex.split(installed_agents["checks-host"]["ProgramArguments"][2])
    assert "--check" in words and "app" in words
    (assignment,) = [word for word in words if word.startswith("R1_APP_SIGNAL_CURSOR=")]
    cursor = Path(assignment.split("=", 1)[1])
    assert cursor.name == "app-signal-cursor.json"
    assert cursor.parent.name == "giatlasachcong"
    assert cursor.parent.parent.as_posix().endswith("/home/Library/Application Support")
    assert cursor.parent.is_dir(), "install.sh creates the directory"
    assert ROOT not in cursor.parents


def _recording_docker(directory: Path, record: Path, then: str) -> None:
    """A `docker` that records its argv and its stdin, then does `then`."""

    directory.mkdir(exist_ok=True)
    docker = directory / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        '[ "$1" = network ] && exit 0\n'
        f"printf '%s\\n' \"$@\" > {record}.argv\n"
        f"cat > {record}.stdin\n"
        f"{then}\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)


def _run_data_agent(
    agent: dict[str, Any], stubs: Path, overrides: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    environment = {
        **agent["EnvironmentVariables"],
        **overrides,
        "PATH": f"{stubs}:{agent['EnvironmentVariables']['PATH']}:{os.environ['PATH']}",
    }
    # `-c` rather than the plist's `-lc`: a login shell sources /etc/profile, which on a Linux
    # test host resets PATH and would hide the stub. Everything else is the plist's command.
    return subprocess.run(
        ["/bin/bash", "-c", agent["ProgramArguments"][2]],
        cwd=agent["WorkingDirectory"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


DSN = "postgresql://laundry_migrate:s3cret-not-in-ps@postgres:5432/nha_trang_laundry"


def test_the_installed_data_agent_delivers_end_to_end(
    installed_agents: dict[str, dict[str, Any]],
    telegram_stub: _Stub,
    credentials: dict[str, str],
    tmp_path: Path,
) -> None:
    """The plist's own command line, run as launchd would, with `docker` stubbed.

    The stub stands in for the container and runs the same check script locally with a forced
    failure. What this proves is the wiring the till depends on -- plist, relay, credentials, log,
    the DSN on stdin -- not Docker, and not Telegram.
    """

    secret = tmp_path / "migration_database_url"
    secret.write_text(DSN + "\n", encoding="utf-8")
    secret.chmod(0o600)
    record = tmp_path / "docker"
    _recording_docker(
        tmp_path / "stubs",
        record,
        f"exec {sys.executable} {CHECK} --check base "
        f"--base-backup-marker {tmp_path / 'never'} --emit-alert",
    )

    result = _run_data_agent(
        installed_agents["checks-data"],
        tmp_path / "stubs",
        {
            **credentials,
            "HOME": str(tmp_path),
            "R1_DATA_CHECKS_DATABASE_URL_FILE": str(secret),
            "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base,
        },
    )

    assert result.returncode == 1, result.stderr
    assert len(telegram_stub.requests) == 1
    assert "base_backup_age" in telegram_stub.requests[0]["form"]["text"]
    # `OPS-HARDENING-002` item 6: the DSN reached the container on stdin and nowhere else.
    assert Path(f"{record}.stdin").read_text("utf-8") == DSN + "\n"
    argv = Path(f"{record}.argv").read_text("utf-8")
    assert "s3cret" not in argv and "DATABASE_URL" not in argv
    assert "-i" in argv.splitlines() and "--database-url-stdin" in argv.splitlines()


def test_a_missing_dsn_file_is_an_alert_not_a_silent_skip(
    installed_agents: dict[str, dict[str, Any]],
    telegram_stub: _Stub,
    credentials: dict[str, str],
    tmp_path: Path,
) -> None:
    record = tmp_path / "docker"
    _recording_docker(tmp_path / "stubs", record, "exit 0")

    result = _run_data_agent(
        installed_agents["checks-data"],
        tmp_path / "stubs",
        {
            **credentials,
            "HOME": str(tmp_path),
            "R1_DATA_CHECKS_DATABASE_URL_FILE": str(tmp_path / "absent"),
            "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base,
        },
    )

    assert result.returncode == 1, result.stderr
    assert not Path(f"{record}.argv").exists(), "nothing runs without its input"
    assert len(telegram_stub.requests) == 1
    assert "absent" in telegram_stub.requests[0]["form"]["text"]


# --- `OPS-HARDENING-002` item 6: the superuser DSN is not an argument ---------------------------


def test_no_launch_agent_puts_the_dsn_on_a_command_line(
    installed_agents: dict[str, dict[str, Any]],
) -> None:
    """`-e DATABASE_URL="$(cat …)"` put the migration identity's password in `ps` and `inspect`."""

    for name, agent in installed_agents.items():
        script = agent["ProgramArguments"][2]
        assert "$(cat" not in script, name
        assert "DATABASE_URL=" not in script, name
    data = installed_agents["checks-data"]
    assert '--stdin-file "$R1_DATA_CHECKS_DATABASE_URL_FILE"' in data["ProgramArguments"][2]
    assert data["EnvironmentVariables"]["R1_DATA_CHECKS_DATABASE_URL_FILE"] == str(
        ROOT / ".shop/secrets/migration_database_url"
    )


def test_the_check_reads_its_dsn_from_stdin() -> None:
    """Proves the value was read: unread, it is "wal needs --database-url", not a connect error."""

    port = _closed_port()
    result = subprocess.run(
        [sys.executable, str(CHECK), "--database-url-stdin", "--check", "wal", "--emit-alert"],
        cwd=ROOT,
        env=_environment({}),
        input=f"postgresql://nobody:pw@127.0.0.1:{port}/none\n",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 1
    assert "needs --database-url" not in result.stderr
    assert "the check itself failed: OperationalError" in result.stderr
    assert "pw@" not in result.stdout + result.stderr

    empty = subprocess.run(
        [sys.executable, str(CHECK), "--database-url-stdin", "--check", "wal"],
        cwd=ROOT,
        env=_environment({}),
        input="",
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert empty.returncode != 0 and "stdin" in empty.stderr


@pytest.mark.parametrize("dsn_source", ["environment", "secret-file"])
def test_shop_admin_passes_the_dsn_on_stdin_not_as_an_argument(
    tmp_path: Path, dsn_source: str
) -> None:
    workdir = tmp_path / "checkout"
    (workdir / "scripts").mkdir(parents=True)
    (workdir / "scripts/bootstrap_store.py").write_text("", encoding="utf-8")
    environment = {"PATH": f"{tmp_path / 'stubs'}:/usr/bin:/bin"}
    if dsn_source == "environment":
        environment["DATABASE_URL"] = DSN
    else:
        (workdir / ".shop/secrets").mkdir(parents=True)
        secret = workdir / ".shop/secrets/migration_database_url"
        secret.write_text(DSN, encoding="utf-8")  # no trailing newline, as an editor may leave it
        secret.chmod(0o600)
    record = tmp_path / "docker"
    _recording_docker(tmp_path / "stubs", record, "exit 7")

    result = subprocess.run(
        [shutil.which("dash") or "sh", str(ROOT / "scripts/shop-admin"), "bootstrap_store.py"],
        cwd=workdir,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 7, result.stderr  # docker's own status is the wrapper's
    argv = Path(f"{record}.argv").read_text("utf-8")
    assert DSN not in argv and "s3cret" not in argv
    assert "-i" in argv.splitlines()
    assert Path(f"{record}.stdin").read_text("utf-8").strip() == DSN


def test_shop_admin_reads_the_dsn_inside_the_container_and_nowhere_else(tmp_path: Path) -> None:
    """The in-container half, run for real under dash: stdin becomes the environment of python."""

    text = (ROOT / "scripts/shop-admin").read_text("utf-8")
    snippet = text.split("IN_CONTAINER='", 1)[1].split("'", 1)[0]
    probe = tmp_path / "python"
    probe.write_text(
        '#!/bin/sh\nprintf "%s|%s|%s\\n" "$DATABASE_URL" "$SUPERUSER_DATABASE_URL" "$*"\n',
        encoding="utf-8",
    )
    probe.chmod(0o755)
    for given in (DSN + "\n", DSN):
        result = subprocess.run(
            [shutil.which("dash") or "sh", "-c", snippet, "shop-admin", "scripts/x.py", "--a", "b"],
            env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
            input=given,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"{DSN}|{DSN}|scripts/x.py --a b"


# --- PLATFORM-RESIDUAL-009B L4: an undelivered alert is sent again until it is delivered --------


def _api_line(status: int, at: datetime, route: str = "/internal/v1/orders") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "occurred_at": at.isoformat(),
            "severity": "INFO",
            "component": "api",
            "event": "http.request.completed",
            "outcome": "completed",
            "correlation_id": f"{route}:{status}:{at.isoformat()}",
            "trace_id": "0" * 32,
            "fields": {"method": "POST", "route": route, "status_code": status},
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _pending_files(credentials: dict[str, str]) -> list[Path]:
    return sorted(Path(credentials["R1_ALERT_LOG_FILE"]).parent.glob("alert-pending-*.json"))


def test_an_application_alert_that_was_not_delivered_is_sent_by_the_next_run(
    telegram_stub: _Stub, credentials: dict[str, str], tmp_path: Path
) -> None:
    """The case L4 names. The app check counts each line once, so a 500 whose alert failed was
    never mentioned again: the next run, with no new 500, passed and sent nothing."""

    now = datetime.now(UTC)
    log = tmp_path / "logs" / "api.jsonl"
    log.parent.mkdir()
    log.write_text(
        "\n".join(
            [
                _api_line(200, now - timedelta(seconds=40), "/readyz"),
                _api_line(500, now - timedelta(seconds=20)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cursor = tmp_path / "state" / "app-signal-cursor.json"
    command = _check("--check", "app", "--app-logs", str(log), "--app-signal-cursor", str(cursor))
    environment = {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base}

    telegram_stub.status = 502
    first = _relay(command, environment, label="checks-host")
    assert first.returncode == 3, first.stderr
    assert "kept to resend next run" in first.stderr
    assert len(_pending_files(credentials)) == 1

    telegram_stub.status = 200
    second = _relay(command, environment, label="checks-host")
    assert second.returncode == 0, second.stderr  # the check itself passes now: no new 500
    assert len(telegram_stub.requests) == 2
    text = telegram_stub.requests[1]["form"]["text"]
    assert text.startswith("Bảng vận hành — cần xem ngay:\nCảnh báo trước đó chưa gửi được:")
    assert "application_signals" in text and "server_errors 1" in text
    assert "(1 lần chưa gửi được)" in text
    assert _pending_files(credentials) == []
    assert "1 earlier alert(s) resent" in Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")

    third = _relay(command, environment, label="checks-host")
    assert third.returncode == 0 and len(telegram_stub.requests) == 2  # delivered once, then quiet


def test_alerts_wait_through_several_failed_runs_and_go_out_together(
    telegram_stub: _Stub, credentials: dict[str, str], tmp_path: Path
) -> None:
    missing = tmp_path / "missing"
    failing = _check("--check", "base", "--base-backup-marker", str(missing))
    environment = {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base}

    telegram_stub.status = 500
    for _ in range(3):
        assert _relay(failing, environment).returncode == 3
    (pending,) = _pending_files(credentials)
    saved = json.loads(pending.read_text("utf-8"))
    # The same words three times are one waiting alert, tried three times -- not three alerts.
    assert [alert["attempts"] for alert in saved["alerts"]] == [3]

    telegram_stub.status = 200
    marker = tmp_path / "last-success"
    marker.write_text("base-20260925T023000Z 3001743", encoding="utf-8")
    passing = _check("--check", "base", "--base-backup-marker", str(marker))
    result = _relay(passing, environment)
    assert result.returncode == 0, result.stderr
    text = telegram_stub.requests[-1]["form"]["text"]
    assert "base_backup_age: no base backup has ever completed" in text
    assert "(3 lần chưa gửi được)" in text
    assert _pending_files(credentials) == []


def test_a_new_alert_and_an_old_one_go_in_one_message(
    telegram_stub: _Stub, credentials: dict[str, str], tmp_path: Path
) -> None:
    environment = {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base}
    telegram_stub.status = 500
    assert (
        _relay(
            _check("--check", "base", "--base-backup-marker", str(tmp_path / "a")), environment
        ).returncode
        == 3
    )

    telegram_stub.status = 200
    result = _relay(
        _check("--check", "base", "--base-backup-marker", str(tmp_path / "b")), environment
    )
    assert result.returncode == 1  # the current check failed, and its alert (with the old) went out
    text = telegram_stub.requests[-1]["form"]["text"]
    current, _, earlier = text.partition("Cảnh báo trước đó chưa gửi được:")
    assert str(tmp_path / "b") in current and str(tmp_path / "a") in earlier
    assert _pending_files(credentials) == []


def test_without_a_place_to_keep_it_the_log_says_it_will_not_be_retried(
    telegram_stub: _Stub, credentials: dict[str, str], tmp_path: Path
) -> None:
    environment = {key: value for key, value in credentials.items() if key != "R1_ALERT_LOG_FILE"}
    telegram_stub.status = 500
    result = _relay(
        _check("--check", "base", "--base-backup-marker", str(tmp_path / "missing")),
        {**environment, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base},
    )
    assert result.returncode == 3
    assert "NOT kept for a retry" in result.stderr


def test_a_full_message_leaves_the_rest_waiting_and_drops_are_reported() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import shop_alert_pending as pending  # type: ignore[import-not-found]

    moment = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    state = pending.PendingState()
    for index in range(pending.MAX_PENDING + 5):
        text = f"Bảng vận hành — cần xem ngay:\n• application_signals: burst {index} " + "x" * 300
        state = pending.after_failure(state, text, ("application_signals",), now=moment)
    assert len(state.alerts) == pending.MAX_PENDING and state.dropped == 5

    message, carried, reported, waiting, *_ = pending.compose(None, state, limit=3800, now=moment)
    assert message is not None and len(message) <= 3800
    assert 0 < len(carried) < pending.MAX_PENDING and not reported and waiting == 0
    rest = pending.after_success(state, carried, reported)
    assert len(rest.alerts) == pending.MAX_PENDING - len(carried) and rest.dropped == 5
    while rest.alerts:
        message, carried, reported, *_ = pending.compose(None, rest, limit=3800, now=moment)
        assert message is not None and len(message) <= 3800
        rest = pending.after_success(rest, carried, reported)
    assert rest.empty and "5 cảnh báo cũ hơn" in str(message)


def test_a_warning_is_logged_by_the_relay_and_sends_nothing(
    telegram_stub: _Stub, credentials: dict[str, str]
) -> None:
    """`DEC-051`: past a million outbox rows the data checks warn. The relay writes it to the alert
    log every run (where `launchctl`/cron mail and the owner's monthly look find it) and pages
    nobody: it is a decision coming due, not an outage."""

    document = {
        "schema": "nha-trang-laundry.shop-alert.v1",
        "passed": True,
        "results": {"outbox_rows": {"passed": True, "detail": "1 500 000 outbox rows, past"}},
        "alert": None,
        "suppressed": [],
        "warnings": {"outbox_rows": "1 500 000 outbox rows, past"},
    }
    fake_check = [sys.executable, "-c", f"print({json.dumps(json.dumps(document))})"]
    result = _relay(fake_check, {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base})
    assert result.returncode == 0, result.stderr
    assert telegram_stub.requests == []
    log = Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")
    assert "WARNING outbox_rows: 1 500 000 outbox rows" in log


# --- Round-9b verifier: a resent alert keeps DEC-025's hours, and a long one cannot wedge -------

SHOP = ZoneInfo("Asia/Ho_Chi_Minh")
HEADING = "Bảng vận hành — cần xem ngay:"
CONSOLE_ALERT = (
    f"{HEADING}\n• console_reachable: https://console.giatlasachcong.lan:8443/readyz answered 503"
)
WAL_LINE = "• wal_archive_gap: the last WAL segment was archived 3600 s ago"
RAISED = datetime(2026, 9, 30, 20, 50, tzinfo=SHOP)  # the verifier's 20:50, inside opening hours


def _pending() -> ModuleType:
    sys.path.insert(0, str(ROOT / "scripts"))
    return importlib.import_module("shop_alert_pending")


def _kept(text: str, checks: tuple[str, ...]) -> Any:
    pending = _pending()
    return pending.after_failure(pending.PendingState(), text, checks, now=RAISED)


@pytest.mark.parametrize(
    ("hour", "minute", "sent"),
    [
        (20, 59, True),  # the minute before quiet hours
        (21, 0, False),
        (23, 59, False),
        (2, 0, False),
        (5, 56, False),  # the verifier's observed resend
        (6, 59, False),
        (7, 0, True),  # opening
        (12, 0, True),
    ],
)
def test_a_kept_console_alert_waits_for_opening_hours(hour: int, minute: int, sent: bool) -> None:
    """The P1. A console alert raised at 20:50 whose send failed was resent by the next run at any
    hour; `DEC-025` says the console check alerts only from 07:00 to 21:00."""

    pending = _pending()
    state = _kept(CONSOLE_ALERT, ("console_reachable",))
    day = 30 if hour >= 20 else 1
    month = 9 if day == 30 else 10
    now = datetime(2026, month, day, hour, minute, tzinfo=SHOP)
    composition = pending.compose(None, state, now=now)
    if sent:
        assert composition.text is not None and "console_reachable" in composition.text
        assert composition.carried == (0,) and composition.waiting == 0
    else:
        assert composition.text is None and composition.carried == ()
        assert composition.waiting == 1
        # Untried is not failed: nothing about it changes until a run may send it.
        assert pending.after_success(state, composition.carried, False) == state


@pytest.mark.parametrize(
    ("checks", "waits_at_night"),
    [
        (("console_reachable",), True),
        (("console_certificate",), True),
        (("wal_archive_gap",), False),
        (("application_signals",), False),
        (("checks-data",), False),  # the relay's own "the check could not run" alert
    ],
)
def test_only_quiet_hours_checks_wait(checks: tuple[str, ...], waits_at_night: bool) -> None:
    pending = _pending()
    state = _kept(f"{HEADING}\n• {checks[0]}: something", checks)
    composition = pending.compose(None, state, now=datetime(2026, 10, 1, 2, 0, tzinfo=SHOP))
    assert (composition.text is None) is waits_at_night
    assert composition.waiting == (1 if waits_at_night else 0)


def test_a_kept_alert_mixing_the_hours_goes_in_two_parts() -> None:
    """A 20:50 alert with the console and the WAL gap: at 02:00 the WAL part goes (a guarantee
    going away wakes somebody) and the console part waits for 07:00."""

    pending = _pending()
    mixed = f"{CONSOLE_ALERT}\n{WAL_LINE}"
    state = _kept(mixed, ("console_reachable", "wal_archive_gap"))
    assert [alert.checks for alert in state.alerts] == [
        ("wal_archive_gap",),
        ("console_reachable",),
    ]

    night = pending.compose(None, state, now=datetime(2026, 10, 1, 2, 0, tzinfo=SHOP))
    assert night.text is not None and "wal_archive_gap" in night.text
    assert "console_reachable" not in night.text and night.waiting == 1
    rest = pending.after_success(state, night.carried, night.reported_dropped)
    assert [alert.checks for alert in rest.alerts] == [("console_reachable",)]

    morning = pending.compose(None, rest, now=datetime(2026, 10, 1, 7, 0, tzinfo=SHOP))
    assert morning.text is not None and "console_reachable" in morning.text
    assert pending.after_success(rest, morning.carried, morning.reported_dropped).empty


def test_a_failed_send_at_night_does_not_count_against_a_waiting_alert() -> None:
    pending = _pending()
    state = _kept(CONSOLE_ALERT, ("console_reachable",))
    night = datetime(2026, 10, 1, 2, 0, tzinfo=SHOP)
    current = f"{HEADING}\n{WAL_LINE}"
    composition = pending.compose(current, state, now=night)
    assert composition.text == current and composition.waiting == 1
    after = pending.after_failure(
        state, current, ("wal_archive_gap",), now=night, attempted=composition.carried
    )
    assert [(alert.checks, alert.attempts) for alert in after.alerts] == [
        (("console_reachable",), 1),  # not tried, so not failed again
        (("wal_archive_gap",), 1),
    ]


def _load_relay() -> ModuleType:
    sys.path.insert(0, str(ROOT / "scripts"))
    return importlib.import_module("relay_shop_alert")


def _passing_check() -> list[str]:
    document = {
        "schema": "nha-trang-laundry.shop-alert.v1",
        "passed": True,
        "results": {},
        "alert": None,
        "suppressed": [],
        "warnings": {},
    }
    return [sys.executable, "-c", f"print({json.dumps(json.dumps(document))})"]


def test_the_relay_holds_a_kept_console_alert_until_opening(
    telegram_stub: _Stub,
    credentials: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verifier's reproduction, through the relay itself: the state the relay writes for a
    20:50 console alert, then a passing run at 05:56 and one at 07:05."""

    pending = _pending()
    relay = _load_relay()
    for key, value in {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base}.items():
        monkeypatch.setenv(key, value)
    file = pending.pending_path("checks-host", dict(os.environ))
    assert file is not None
    pending.save(file, _kept(CONSOLE_ALERT, ("console_reachable",)))

    monkeypatch.setattr(relay, "_now", lambda: datetime(2026, 10, 1, 5, 56, tzinfo=SHOP))
    assert relay.main(["--label", "checks-host", "--", *_passing_check()]) == 0
    assert telegram_stub.requests == []
    assert file.exists()
    log = Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")
    assert "1 earlier alert(s) wait for opening hours" in log
    assert "NOT DELIVERED" not in log

    monkeypatch.setattr(relay, "_now", lambda: datetime(2026, 10, 1, 7, 5, tzinfo=SHOP))
    assert relay.main(["--label", "checks-host", "--", *_passing_check()]) == 0
    (request,) = telegram_stub.requests
    text = request["form"]["text"]
    assert "console_reachable" in text and "lúc 20:50 30/09" in text
    assert not file.exists()


def test_the_direct_path_holds_a_kept_console_alert_until_opening(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`check_shop_operations.deliver_alert` resends the same way, so it holds the same way."""

    sys.path.insert(0, str(ROOT / "scripts"))
    checks = importlib.import_module("check_shop_operations")

    pending = _pending()
    token = tmp_path / "token"
    token.write_text("probe-token", encoding="utf-8")
    monkeypatch.setenv("R1_ALERT_TELEGRAM_TOKEN_FILE", str(token))
    monkeypatch.setenv("R1_ALERT_TELEGRAM_CHAT_ID", "1234")
    monkeypatch.setenv("R1_ALERT_PENDING_DIRECTORY", str(tmp_path))
    file = pending.pending_path("direct", {"R1_ALERT_PENDING_DIRECTORY": str(tmp_path)})
    pending.save(file, _kept(CONSOLE_ALERT, ("console_reachable",)))
    posted: list[bytes] = []

    class _Response:
        status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def _capture(request: Any, timeout: float = 0) -> _Response:
        posted.append(request.data)
        return _Response()

    monkeypatch.setattr(checks.urllib.request, "urlopen", _capture)
    assert checks.deliver_alert([], now=datetime(2026, 10, 1, 2, 0, tzinfo=SHOP)) is False
    assert posted == [] and file.exists()
    assert checks.deliver_alert([], now=datetime(2026, 10, 1, 7, 30, tzinfo=SHOP)) is True
    assert len(posted) == 1 and not file.exists()


LONG_ALERT = f"{HEADING}\n• capability_flags: " + "x" * 3750


def _document_check(text: str | None) -> list[str]:
    document = {
        "schema": "nha-trang-laundry.shop-alert.v1",
        "passed": text is None,
        "results": {},
        "alert": None if text is None else {"text": text, "checks": ["capability_flags"]},
        "suppressed": [],
        "warnings": {},
    }
    status = 0 if text is None else 1
    return [
        sys.executable,
        "-c",
        f"import sys; print({json.dumps(json.dumps(document))}); sys.exit({status})",
    ]


def test_a_long_undelivered_alert_is_delivered_by_the_next_run(
    telegram_stub: _Stub, credentials: dict[str, str]
) -> None:
    """The verifier's wedge. A ~3 800-character alert that failed once was never resent: it did
    not fit beside the redelivery heading, so every later run sent nothing, failed with "nothing to
    send" and exited 3 -- with Telegram up -- and every alert behind it starved."""

    environment = {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base}
    telegram_stub.status = 500
    assert _relay(_document_check(LONG_ALERT), environment, label="checks-host").returncode == 3

    telegram_stub.status = 200
    second = _relay(_document_check(None), environment, label="checks-host")
    assert second.returncode == 0, second.stderr
    assert len(telegram_stub.requests) == 2
    text = telegram_stub.requests[1]["form"]["text"]
    # Round-9b verifier, round 3: this used to assert the resend ended "…" -- the 3 800-character
    # alert the first attempt carried whole was resent clipped to 3 600. It goes whole now.
    assert telegram_stub.requests[0]["form"]["text"] == LONG_ALERT
    assert len(text) <= _pending().MESSAGE_CEILING and LONG_ALERT.split("\n", 1)[1] in text
    assert _pending_files(credentials) == []
    assert "nothing to send" not in Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")


def test_a_long_new_alert_beside_a_long_kept_one_loses_nothing(
    telegram_stub: _Stub, credentials: dict[str, str]
) -> None:
    """The neighbouring case: the check is still failing with a long alert of its own. Round-9b
    verifier, round 2: both used to go in one message, each clipped, and neither clipped tail was
    ever sent -- the kept one was removed as delivered and the current one was never kept. Now the
    oldest goes whole, the message says a newer one follows, and the newer one goes whole next run.
    """

    environment = {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base}
    telegram_stub.status = 500
    assert _relay(_document_check(LONG_ALERT), environment, label="checks-host").returncode == 3

    telegram_stub.status = 200
    newer = f"{HEADING}\n• capability_flags: " + "y" * 3750
    result = _relay(_document_check(newer), environment, label="checks-host")
    assert result.returncode == 1, result.stderr
    text = telegram_stub.requests[-1]["form"]["text"]
    kept = _pending().after_failure(
        _pending().PendingState(), LONG_ALERT, ("capability_flags",), now=RAISED
    )
    kept_body = kept.alerts[0].text.split("\n", 1)[1]
    assert kept_body == LONG_ALERT.split("\n", 1)[1], "kept as the first send carried it"
    assert len(text) <= _pending().MESSAGE_CEILING and kept_body in text and "yyy" not in text
    assert "Cảnh báo trước đó chưa gửi được:" in text and "gửi ở tin sau" in text
    assert len(_pending_files(credentials)) == 1

    third = _relay(_document_check(None), environment, label="checks-host")
    assert third.returncode == 0, third.stderr
    text = telegram_stub.requests[-1]["form"]["text"]
    newer_kept = _pending().after_failure(
        _pending().PendingState(), newer, ("capability_flags",), now=RAISED
    )
    assert newer_kept.alerts[0].text.split("\n", 1)[1] in text
    assert len(telegram_stub.requests) == 3 and _pending_files(credentials) == []


def _compose_beside_a_short_kept_alert(current: str) -> Any:
    pending = _pending()
    state = _kept(f"{HEADING}\n{WAL_LINE}", ("wal_archive_gap",))
    return pending.compose(current, state, now=datetime(2026, 10, 1, 12, 0, tzinfo=SHOP))


@pytest.mark.parametrize("length", [1000, 1900, 2553, 3000, 3600])
def test_a_new_alert_that_fits_is_delivered_whole_beside_a_kept_one(length: int) -> None:
    """Round-9b verifier, round 2: with anything kept, the current alert was clipped to half the
    message (1 900) even when everything fitted -- 2 553 + a 60-character kept alert came out as
    2 013 characters -- and the clipped tail was never kept, so it was lost."""

    current = f"{HEADING}\n• application_signals: " + "a" * length
    composition = _compose_beside_a_short_kept_alert(current)
    assert composition.text is not None and len(composition.text) <= 3800
    assert composition.text.startswith(current + "\n\n"), "the current alert, whole and first"
    assert WAL_LINE in composition.text and composition.carried == (0,)
    assert not composition.deferred


def test_a_new_alert_too_long_to_share_waits_whole_for_the_next_run() -> None:
    pending = _pending()
    current = f"{HEADING}\n• application_signals: " + "a" * 3750
    composition = _compose_beside_a_short_kept_alert(current)
    assert composition.text is not None and "aaa" not in composition.text
    assert WAL_LINE in composition.text and composition.deferred
    state = _kept(f"{HEADING}\n{WAL_LINE}", ("wal_archive_gap",))
    moment = datetime(2026, 10, 1, 12, 0, tzinfo=SHOP)
    after = pending.after_success(
        state,
        composition.carried,
        composition.reported_dropped,
        deferred=current,
        checks=("application_signals",),
        now=moment,
    )
    (waiting,) = after.alerts
    assert waiting.checks == ("application_signals",) and waiting.attempts == 0
    following = pending.compose(None, after, now=moment)
    assert following.text is not None and waiting.text.split("\n", 1)[1] in following.text
    assert "tin trước đã đầy" in following.text


def test_a_current_alert_over_the_limit_is_the_duplicate_of_its_clipped_kept_copy() -> None:
    """Round-9b verifier, round 2: a current alert longer than a message was not recognised as
    its own kept copy (which is stored clipped), so both went, the same words twice."""

    pending = _pending()
    current = f"{HEADING}\n• capability_flags: " + "q" * 5000
    state = _kept(current, ("capability_flags",))
    composition = pending.compose(current, state, now=datetime(2026, 10, 1, 12, 0, tzinfo=SHOP))
    assert composition.carried == (0,) and not composition.deferred
    assert composition.text is not None and len(composition.text) <= 3800
    assert "Cảnh báo trước đó chưa gửi được:" not in composition.text


def test_a_kept_alert_is_stored_no_longer_than_a_message() -> None:
    pending = _pending()
    state = _kept(f"{HEADING}\n• capability_flags: " + "z" * 9000, ("capability_flags",))
    (alert,) = state.alerts
    assert len(alert.text) <= pending.MAX_MESSAGE_CHARACTERS and alert.text.endswith("…")


def test_every_run_that_can_send_carries_the_oldest_waiting_alert() -> None:
    """A property over many queues: whatever the lengths, the current alert and the hour, a run
    with something sendable sends a message within the limit that carries the oldest sendable kept
    alert -- so the queue always drains -- and never one that waits for opening."""

    import random

    pending = _pending()
    generator = random.Random(20261002)
    names = ["console_reachable", "wal_archive_gap", "capability_flags", "application_signals"]
    for _ in range(300):
        state = pending.PendingState()
        for index in range(generator.randint(1, 8)):
            name = generator.choice(names)
            body = "w" * generator.choice([10, 500, 1900, 3700, 5000])
            state = pending.after_failure(
                state,
                f"{HEADING}\n• {name}: {index} {body}",
                (name,),
                now=RAISED + timedelta(minutes=index),
            )
        current = (
            None
            if generator.random() < 0.5
            else f"{HEADING}\n• wal_archive_gap: " + "c" * generator.choice([10, 2000, 4000])
        )
        now = datetime(2026, 10, 1, generator.randint(0, 23), tzinfo=SHOP)
        composition = pending.compose(current, state, now=now)
        sendable = [
            index
            for index, alert in enumerate(state.alerts)
            if not pending.waits_for_opening(alert, now)
        ]
        assert composition.waiting == len(state.alerts) - len(sendable)
        if current is None and not sendable:
            assert composition.text is None
            continue
        assert composition.text is not None
        ceiling = pending.MESSAGE_CEILING
        assert len(composition.text) <= ceiling < pending.TELEGRAM_MESSAGE_CHARACTERS
        if len(composition.text) > 3800:
            # Only the oldest kept alert, alone, may push a message past the packing limit.
            assert composition.carried == (sendable[0],)
            assert current is None or composition.deferred
        if sendable:
            assert sendable[0] in composition.carried
        assert set(composition.carried) <= set(sendable)
        # Nothing carried is cut: each kept alert it removes went whole, and the current alert
        # either went whole (clipped only to the limit of a message on its own) or waits.
        for index in composition.carried:
            body = state.alerts[index].text.split("\n", 1)[1]
            assert body in composition.text or (
                current is not None
                and not composition.deferred
                and body.removesuffix("…") in current
            )
        if current is not None and not composition.deferred:
            assert composition.text.startswith(pending._clip(current, 3800))
        if composition.deferred:
            assert current is not None and current.split("\n", 1)[1][:200] not in composition.text


# --- DEC-052: the renewal notice reaches the owner, from the till's daily agent ------------------


def test_the_daily_agent_tells_the_owner_when_the_certificate_needs_renewing(
    installed_agents: dict[str, dict[str, Any]],
) -> None:
    """Round-9b verifier: the 60-day renewal warning was printed only by
    `bootstrap_shop_local.py`, which nobody runs in normal operation. The till now checks the
    console certificate every morning and the relay sends what it finds."""

    agent = installed_agents["checks-daily"]
    # Every hour, not once at 09:00: a 09:00 missed while asleep ran on a night wake, where
    # `DEC-025` held the notice and nothing ran again until the next 09:00 (round-9b verifier,
    # round 2). The check's own record keeps it to one message a shop day.
    assert agent["StartInterval"] == 3600 and "StartCalendarInterval" not in agent
    words = shlex.split(agent["ProgramArguments"][2])
    assert "scripts/relay_shop_alert.py" in words and "--emit-alert" in words
    assert words[words.index("--label") + 1] == "checks-daily"
    assert words[words.index("--check") + 1] == "certificate"
    certificate = Path(words[words.index("--console-certificate") + 1])
    assert certificate == ROOT / ".shop/secrets/tls_certificate"
    record = Path(words[words.index("--certificate-notice-state") + 1])
    assert record.name == "certificate-notice.json" and record.parent.name == "giatlasachcong"
    assert ROOT not in record.parents, "outside the checkout, like the app check's cursor"
    environment = agent["EnvironmentVariables"]
    for key in ("R1_ALERT_TELEGRAM_TOKEN_FILE", "R1_ALERT_TELEGRAM_CHAT_ID_FILE"):
        assert environment[key].startswith(str(ROOT / ".shop/secrets/")), key


def _console_certificate(path: Path, *, expires: datetime) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "console.giatlasachcong.lan")])
    leaf = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(expires - timedelta(days=825))
        .not_valid_after(expires)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))


@pytest.mark.parametrize(("days_left", "sent"), [(90, False), (45, True), (-3, True)])
def test_a_certificate_near_expiry_reaches_the_stub_through_the_relay(
    telegram_stub: _Stub,
    credentials: dict[str, str],
    tmp_path: Path,
    days_left: int,
    sent: bool,
) -> None:
    """The daily agent's own command shape -- relay, `--check certificate`, `--emit-alert` -- run
    for real against a certificate on disk and a loopback stub."""

    certificate = tmp_path / "tls_certificate"
    _console_certificate(
        certificate, expires=datetime.now(UTC) + timedelta(days=days_left, hours=12)
    )
    result = _relay(
        _check("--check", "certificate", "--console-certificate", str(certificate)),
        {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base},
        label="checks-daily",
    )
    if not sent:
        assert result.returncode == 0, result.stderr
        assert telegram_stub.requests == []
        return
    # At night (in the shop's clock) DEC-025 holds it and the hourly agent's first run from 07:00
    # tells (test_a_renewal_notice_found_at_night_reaches_the_owner_that_morning); by day it goes.
    if _pending().in_quiet_hours(datetime.now(UTC)):
        assert result.returncode == 1 and telegram_stub.requests == []
        return
    assert result.returncode == 1, result.stderr
    (request,) = telegram_stub.requests
    text = request["form"]["text"]
    assert "console_certificate" in text and "--new-ca" in text
    assert ("còn" in text) if days_left > 0 else ("đã hết hạn" in text)


def _check_at(directory: Path, at: datetime, *arguments: str) -> list[str]:
    """`check_shop_operations.py` as a subprocess whose clock reads `at` -- the relay runs the
    check as a child, so the test pins the child's clock as well as the relay's."""

    wrapper = directory / "check_at.py"
    wrapper.write_text(
        "import datetime as _dt, importlib.util, sys\n"
        "at = _dt.datetime.fromisoformat(sys.argv[1])\n"
        "sys.argv = ['check_shop_operations.py', *sys.argv[2:]]\n"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        f"spec = importlib.util.spec_from_file_location('check_shop_operations', {str(CHECK)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['check_shop_operations'] = module\n"
        "spec.loader.exec_module(module)\n"
        "class _Pinned(_dt.datetime):\n"
        "    @classmethod\n"
        "    def now(cls, tz=None):\n"
        "        return at.astimezone(tz) if tz else at\n"
        "module.datetime = _Pinned\n"
        "raise SystemExit(module.main())\n",
        encoding="utf-8",
    )
    return [sys.executable, str(wrapper), at.isoformat(), *arguments, "--emit-alert"]


def _daily_run(
    relay: ModuleType, monkeypatch: pytest.MonkeyPatch, directory: Path, at: datetime
) -> int:
    """One run of the till's `checks-daily` agent, as install.sh writes it, at `at`."""

    monkeypatch.setattr(relay, "_now", lambda: at.astimezone(UTC))
    return int(
        relay.main(
            [
                "--label",
                "checks-daily",
                "--",
                *_check_at(
                    directory,
                    at,
                    "--check",
                    "certificate",
                    "--console-certificate",
                    str(directory / "tls_certificate"),
                    "--certificate-notice-state",
                    str(directory / "state" / "certificate-notice.json"),
                ),
            ]
        )
    )


def test_a_renewal_notice_found_at_night_reaches_the_owner_that_morning(
    telegram_stub: _Stub,
    credentials: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Round-9b verifier, round 2, through the real relay: the 09:00 job missed while asleep ran
    on a wake at 23:30, `DEC-025` held the notice, the relay kept nothing (rc 1, sent 0, no
    pending file), and nothing ran again until the next 09:00 -- the notice was dropped for the
    day. Now the agent looks every hour and tells the owner once, from 07:00."""

    relay = _load_relay()
    for key, value in {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base}.items():
        monkeypatch.setenv(key, value)
    night = datetime(2026, 10, 3, 23, 30, tzinfo=SHOP)
    _console_certificate(tmp_path / "tls_certificate", expires=night + timedelta(days=45))

    for hour in (23,):
        assert _daily_run(relay, monkeypatch, tmp_path, night.replace(hour=hour)) == 1
    for hour in (0, 3, 6):
        assert (
            _daily_run(relay, monkeypatch, tmp_path, datetime(2026, 10, 4, hour, 30, tzinfo=SHOP))
            == 1
        )
    assert telegram_stub.requests == []

    assert _daily_run(relay, monkeypatch, tmp_path, datetime(2026, 10, 4, 7, 30, tzinfo=SHOP)) == 1
    (request,) = telegram_stub.requests
    text = request["form"]["text"]
    assert "console_certificate" in text and "còn 4" in text and "--new-ca" in text

    for hour in (8, 12, 20, 23):  # still failing; told already today
        assert (
            _daily_run(relay, monkeypatch, tmp_path, datetime(2026, 10, 4, hour, 30, tzinfo=SHOP))
            == 1
        )
    assert len(telegram_stub.requests) == 1
    log = Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")
    assert "already told today: ['console_certificate']" in log

    assert _daily_run(relay, monkeypatch, tmp_path, datetime(2026, 10, 5, 7, 30, tzinfo=SHOP)) == 1
    assert len(telegram_stub.requests) == 2, "and again the next shop day"


def test_a_renewal_notice_whose_send_failed_is_resent_the_next_hour(
    telegram_stub: _Stub,
    credentials: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Told today is recorded when the notice is raised; a send that then fails is the relay's
    to retry (L4), so the record never swallows it."""

    relay = _load_relay()
    for key, value in {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base}.items():
        monkeypatch.setenv(key, value)
    morning = datetime(2026, 10, 4, 9, 0, tzinfo=SHOP)
    _console_certificate(tmp_path / "tls_certificate", expires=morning + timedelta(days=30))

    telegram_stub.status = 500
    assert _daily_run(relay, monkeypatch, tmp_path, morning) == 3
    assert len(_pending_files(credentials)) == 1
    telegram_stub.status = 200
    assert _daily_run(relay, monkeypatch, tmp_path, morning.replace(hour=10)) == 1
    text = telegram_stub.requests[-1]["form"]["text"]
    assert "console_certificate" in text and "lúc 09:00 04/10" in text
    assert _pending_files(credentials) == []
    assert _daily_run(relay, monkeypatch, tmp_path, morning.replace(hour=11)) == 1
    assert len(telegram_stub.requests) == 2


# --- round-9b verifier, round 3: a resend carries what the first send carried --------------------


@pytest.mark.parametrize("length", [3540, 3601, 3753, 3800])
@pytest.mark.parametrize("current", [None, "short", "long"])
def test_a_resent_alert_carries_everything_its_first_send_did(
    length: int, current: str | None
) -> None:
    """The verifier's repro: a 3 753-character alert goes whole on its first send; that send fails;
    the kept copy was clipped to 3 600, so the resend ended '0707,0708,0…' and '0739,' was gone.
    Matrix: lengths either side of the old 3 600 cut and at the 3 800 limit, resent alone, beside a
    short current alert, and beside a current alert too long to share the message."""

    pending = _pending()
    noon = datetime(2026, 10, 1, 12, 0, tzinfo=SHOP)
    prefix = f"{HEADING}\n• application_signals: "
    digits = "".join(f"{index:04d}," for index in range(800))
    alert = prefix + digits[: length - len(prefix)]
    assert len(alert) == length

    first = pending.compose(alert, pending.PendingState(), now=noon)
    assert first.text == alert, "the first send carries it whole"
    state = pending.after_failure(pending.PendingState(), alert, ("application_signals",), now=noon)
    assert state.alerts[0].text == alert, "kept as the first send carried it"

    now_text = {
        None: None,
        "short": f"{HEADING}\n{WAL_LINE}",
        "long": f"{HEADING}\n• wal_archive_gap: " + "w" * 3700,
    }[current]
    resend = pending.compose(now_text, state, now=noon + timedelta(minutes=5))
    assert resend.text is not None and resend.carried == (0,)
    assert alert.split("\n", 1)[1] in resend.text, "nothing of it is cut"
    assert len(resend.text) <= pending.MESSAGE_CEILING
    if now_text is None:
        return
    # Beside a current alert: both go when they fit the packing limit together; otherwise the kept
    # one goes now (whole, above) and the current one is kept and goes whole next run.
    together = pending._message([now_text], [pending._entry(state.alerts[0])])
    assert resend.deferred == (len(together) > pending.MAX_MESSAGE_CHARACTERS)
    if current == "long":
        assert resend.deferred
    if resend.deferred:
        after = pending.after_success(
            state,
            resend.carried,
            resend.reported_dropped,
            deferred=now_text,
            checks=("wal_archive_gap",),
            now=noon + timedelta(minutes=5),
        )
        following = pending.compose(None, after, now=noon + timedelta(minutes=10))
        assert following.text is not None and now_text.split("\n", 1)[1] in following.text
    else:
        assert resend.text.startswith(now_text + "\n\n")


def test_a_kept_alert_of_the_longest_length_is_resent_whole() -> None:
    """The worst case the ceiling is sized for: a kept alert of `KEPT_CHARACTERS`, tried a great
    many times, carried alone beside a deferred current alert (both headings, the entry line, the
    'a newer alert follows' note). It goes whole, under Telegram's limit."""

    pending = _pending()
    alert = f"{HEADING}\n• capability_flags: " + "k" * (
        pending.KEPT_CHARACTERS - len(HEADING) - len("\n• capability_flags: ")
    )
    state = pending.after_failure(pending.PendingState(), alert, ("capability_flags",), now=RAISED)
    assert state.alerts[0].text == alert
    state = pending.PendingState((dataclasses.replace(state.alerts[0], attempts=10**9),))
    current = f"{HEADING}\n• wal_archive_gap: " + "c" * 3700
    composition = pending.compose(current, state, now=RAISED + timedelta(minutes=5))
    assert composition.deferred and composition.carried == (0,)
    assert composition.text is not None and alert.split("\n", 1)[1] in composition.text
    assert len(composition.text) <= pending.MESSAGE_CEILING
    assert pending.MESSAGE_CEILING <= pending.TELEGRAM_MESSAGE_CHARACTERS - 64


def _run_relay_in_process(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    document: dict[str, object],
) -> int:
    relay = importlib.import_module("relay_shop_alert")
    for key in [key for key in os.environ if key.startswith("R1_ALERT_")]:
        monkeypatch.delenv(key)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    command = [sys.executable, "-c", f"import sys; print({json.dumps(json.dumps(document))})"]
    return int(relay.main(["--label", "checks-data", "--", *command]))


_FAILING_DOCUMENT: dict[str, object] = {
    "schema": "nha-trang-laundry.shop-alert.v1",
    "passed": False,
    "results": {},
    "alert": {"text": f"{HEADING}\n{WAL_LINE}", "checks": ["wal_archive_gap"]},
    "suppressed": [],
    "warnings": {},
}


@pytest.mark.parametrize("fault", ["directory is a file", "disk full"])
def test_a_failed_send_with_nowhere_to_keep_it_is_still_not_delivered(
    telegram_stub: _Stub,
    credentials: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Round-9b verifier, round 3 (P2): Telegram failing while the pending file cannot be written
    -- the disk the volume check warns about is full, or the directory is a file -- crashed the
    relay with a traceback and exit 1, which its contract defines as 'delivered', and the log had
    no NOT DELIVERED line. It is exit 3 and the line, saying the alert was not kept and why."""

    import errno

    pending = _pending()
    environment = {**credentials, "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base}
    if fault == "directory is a file":
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, not a directory", encoding="utf-8")
        environment["R1_ALERT_PENDING_DIRECTORY"] = str(blocker / "state")
    else:
        environment["R1_ALERT_PENDING_DIRECTORY"] = str(tmp_path / "state")

        def _full(self: Path, *_: object, **__: object) -> int:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(Path, "write_text", _full)
    telegram_stub.status = 500

    if fault == "directory is a file":
        result = _relay([*_document_command(_FAILING_DOCUMENT)], environment)
        code, stderr = result.returncode, result.stderr
    else:
        code = _run_relay_in_process(monkeypatch, environment, _FAILING_DOCUMENT)
        stderr = ""
    log = Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")
    assert code == 3, stderr
    assert "ALERT NOT DELIVERED" in log and "NOT kept for a retry" in log, log
    assert ("Not a directory" if fault == "directory is a file" else "No space left") in log
    assert "Traceback" not in stderr
    assert not list((tmp_path / "state").glob(".*.tmp")) if (tmp_path / "state").exists() else True
    assert pending.try_save(tmp_path / "unused.json", pending.PendingState()) is None


NOON = datetime(2026, 10, 1, 12, 0, tzinfo=SHOP)
OLD_WAL_ALERT = f"{HEADING}\n• wal_archive_gap: " + "w" * 3700


def _unwritable(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A pending file that can be read but not written: the host disk is full. Returns the real
    `Path.write_text`, to put back when the disk is freed."""

    import errno

    original = Path.write_text

    def _full(self: Path, *_: object, **__: object) -> int:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_text", _full)
    return original


def test_an_alert_that_cannot_be_kept_goes_now_and_nothing_promises_a_newer_one(
    telegram_stub: _Stub,
    credentials: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-9b verifier, round 4 (P2), through the real relay and the stub. A 3 700-character
    alert is kept; the disk fills; the check now fails with a 3 000-character alert of its own.
    Every run used to send only the old kept alert -- it could never be marked delivered -- ending
    "còn một cảnh báo mới hơn — gửi ở tin sau", while the newer alert, which could not be kept, was
    never sent: the same message every five minutes and a promise nothing kept. (The test this
    replaces, `test_a_deferred_alert_with_nowhere_to_keep_it_is_not_delivered`, asserted exactly
    that message.) Now the newer alert goes first and whole, the kept one goes clipped beside it
    and stays kept, and the "newer follows" line appears only once the newer alert is kept."""

    pending = _pending()
    environment = {
        **credentials,
        "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base,
        "R1_ALERT_PENDING_DIRECTORY": str(tmp_path / "state"),
    }
    path = pending.pending_path("checks-data", environment)
    pending.save(path, _kept(OLD_WAL_ALERT, ("wal_archive_gap",)))
    before = path.read_bytes()
    current = f"{HEADING}\n• volume_free: " + "v" * 3000
    document = {**_FAILING_DOCUMENT, "alert": {"text": current, "checks": ["volume_free"]}}

    original = _unwritable(monkeypatch)
    codes = [_run_relay_in_process(monkeypatch, environment, document) for _ in range(3)]
    log = Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")
    assert codes == [1, 1, 1], log
    assert "NOT DELIVERED" not in log and "cannot be kept" in log and "No space left" in log
    texts = [request["form"]["text"] for request in telegram_stub.requests]
    assert len(texts) == 3
    for text in texts:
        assert text.startswith(current + "\n\n"), "this run's alert, first and whole"
        assert pending.NEWER_FOLLOWS not in text and pending.KEPT_CLIPPED in text
        assert "www" in text and len(text) <= pending.MESSAGE_CEILING
    assert path.read_bytes() == before, "the kept alert is still kept, whole"

    # The disk is freed. The kept alert goes whole; "newer follows" is said now that it is true.
    monkeypatch.setattr(Path, "write_text", original)
    assert _run_relay_in_process(monkeypatch, environment, document) == 1
    fourth = telegram_stub.requests[-1]["form"]["text"]
    assert OLD_WAL_ALERT.split("\n", 1)[1] in fourth and pending.NEWER_FOLLOWS in fourth
    assert "vvv" not in fourth
    kept, _ = pending.load(path, now=NOON)
    assert [alert.text for alert in kept.alerts] == [current]
    passing = {**_FAILING_DOCUMENT, "passed": True, "alert": None}
    assert _run_relay_in_process(monkeypatch, environment, passing) == 0
    assert current.split("\n", 1)[1] in telegram_stub.requests[-1]["form"]["text"]
    assert not path.exists() and len(telegram_stub.requests) == 5


@pytest.mark.parametrize(
    ("length", "shape"),
    [
        (0, "whole"),  # past the 3 800 packing limit beside it, under the ceiling: whole
        (160, "whole"),  # the last length at which both fit whole, at exactly the ceiling
        (161, "clipped"),
        (1000, "clipped"),
        (3000, "clipped"),  # the verifier's repro
        (3728, "clipped"),  # the last length leaving MIN_CLIPPED_ENTRY of room
        (3729, "named"),  # no room left worth sending: only named
        (3755, "named"),  # the current alert exactly MAX_MESSAGE_CHARACTERS long
        (5000, "named"),  # clipped to 3 800 itself, as on any first send
    ],
)
def test_a_current_alert_that_cannot_be_kept_is_never_deferred(length: int, shape: str) -> None:
    pending = _pending()
    state = _kept(OLD_WAL_ALERT, ("wal_archive_gap",))
    current = f"{HEADING}\n• volume_free: " + "v" * length
    assert pending.compose(current, state, now=NOON).deferred, "would have been deferred"

    composition = pending.compose(current, state, now=NOON, defer=False)
    text = composition.text
    first = current if len(current) <= 3800 else current[:3799] + "…"
    assert text is not None and text.startswith(first + "\n\n")
    assert not composition.deferred and pending.NEWER_FOLLOWS not in text
    assert len(text) <= pending.MESSAGE_CEILING
    if shape == "whole":
        assert OLD_WAL_ALERT.split("\n", 1)[1] in text and composition.carried == (0,)
        assert not composition.kept_clipped
    else:
        assert composition.carried == () and composition.kept_clipped
        marker = pending.KEPT_CLIPPED if shape == "clipped" else pending.KEPT_LEFT_OUT
        assert text.endswith(marker) and ("www" in text) is (shape == "clipped")
        # Not delivered whole, so not delivered: it stays kept as it was.
        assert pending.after_success(state, composition.carried, False) == state


@pytest.mark.parametrize("current_length", [None, 10, 1000, 2000, 3000, 3500, 3800, 6000])
@pytest.mark.parametrize("kept_length", [10, 1000, 2000, 3000, 3500, 3700, 3800])
def test_newer_follows_is_said_only_of_a_deferred_alert(
    current_length: int | None, kept_length: int
) -> None:
    pending = _pending()
    state = _kept(f"{HEADING}\n• wal_archive_gap: " + "w" * kept_length, ("wal_archive_gap",))
    current = (
        None if current_length is None else f"{HEADING}\n• volume_free: " + "v" * current_length
    )
    for defer in (True, False):
        composition = pending.compose(current, state, now=NOON, defer=defer)
        assert composition.text is not None and len(composition.text) <= pending.MESSAGE_CEILING
        assert (pending.NEWER_FOLLOWS in composition.text) is composition.deferred
        assert not (composition.deferred and not defer)
        if current is not None and not composition.deferred:
            assert composition.text.startswith(current[:3799])


def test_a_deferred_alert_is_kept_before_the_send(tmp_path: Path) -> None:
    """`prepare` defers only an alert it has already written down: a send that then succeeds or
    fails, or a process killed in between, cannot lose it."""

    pending = _pending()
    state = _kept(OLD_WAL_ALERT, ("wal_archive_gap",))
    path = tmp_path / "alert-pending-checks-data.json"
    pending.save(path, state)
    current = f"{HEADING}\n• volume_free: " + "v" * 3000
    composition, problem = pending.prepare(current, ("volume_free",), state, path, now=NOON)
    assert problem is None and composition.deferred
    on_disk, _ = pending.load(path, now=NOON)
    assert [alert.text for alert in on_disk.alerts] == [OLD_WAL_ALERT, current]
    assert [alert.attempts for alert in on_disk.alerts] == [1, 0]

    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    composition, problem = pending.prepare(
        current, ("volume_free",), state, blocker / "state" / path.name, now=NOON
    )
    assert problem is not None and "Not a directory" in problem
    assert not composition.deferred and composition.text is not None
    assert composition.text.startswith(current) and pending.NEWER_FOLLOWS not in composition.text


def test_the_direct_path_never_defers_an_alert_it_cannot_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`check_shop_operations.deliver_alert` composes the same way, so it had the same defect."""

    pending = _pending()
    checks = importlib.import_module("check_shop_operations")
    token = tmp_path / "token"
    token.write_text("probe-token", encoding="utf-8")
    directory = tmp_path / "state"
    path = pending.pending_path("direct", {"R1_ALERT_PENDING_DIRECTORY": str(directory)})
    pending.save(path, _kept(OLD_WAL_ALERT, ("wal_archive_gap",)))
    before = path.read_bytes()
    monkeypatch.setenv("R1_ALERT_TELEGRAM_TOKEN_FILE", str(token))
    monkeypatch.setenv("R1_ALERT_TELEGRAM_CHAT_ID", "1234")
    monkeypatch.setenv("R1_ALERT_PENDING_DIRECTORY", str(directory))
    sent: list[str] = []

    class _Response:
        status = 200

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def _accepted(request: Any, timeout: float = 0) -> Any:
        sent.append(urllib.parse.parse_qs(request.data.decode())["text"][0])
        return _Response()

    monkeypatch.setattr(checks.urllib.request, "urlopen", _accepted)
    _unwritable(monkeypatch)
    failure = checks.CheckResult("volume_free", False, "v" * 3000, {})
    assert checks.deliver_alert([failure], now=NOON) is True
    stderr = capsys.readouterr().err
    assert len(sent) == 1 and sent[0].startswith(f"{HEADING}\n• volume_free: vvv")
    assert pending.NEWER_FOLLOWS not in sent[0] and pending.KEPT_CLIPPED in sent[0]
    assert "NOT DELIVERED" not in stderr and "cannot be kept" in stderr, stderr
    assert path.read_bytes() == before


def _document_command(document: dict[str, object]) -> list[str]:
    return [sys.executable, "-c", f"import sys; print({json.dumps(json.dumps(document))})"]


def test_the_direct_path_with_nowhere_to_keep_a_failed_alert_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`check_shop_operations.deliver_alert` keeps alerts the same way, so it fails the same way:
    an unwritable pending directory is a NOT DELIVERED line naming why, never a traceback."""

    sys.path.insert(0, str(ROOT / "scripts"))
    checks = importlib.import_module("check_shop_operations")
    token = tmp_path / "token"
    token.write_text("probe-token", encoding="utf-8")
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    monkeypatch.setenv("R1_ALERT_TELEGRAM_TOKEN_FILE", str(token))
    monkeypatch.setenv("R1_ALERT_TELEGRAM_CHAT_ID", "1234")
    monkeypatch.setenv("R1_ALERT_PENDING_DIRECTORY", str(blocker / "state"))

    def _refused(request: Any, timeout: float = 0) -> Any:
        raise OSError("network is unreachable")

    monkeypatch.setattr(checks.urllib.request, "urlopen", _refused)
    failure = checks.CheckResult("wal_archive_gap", False, "archive 40 min behind", {})
    assert checks.deliver_alert([failure], now=datetime(2026, 10, 1, 12, 0, tzinfo=SHOP)) is False
    stderr = capsys.readouterr().err
    assert "ALERT NOT DELIVERED: OSError" in stderr
    assert "NOT kept for a retry" in stderr and "Not a directory" in stderr, stderr


def _fills_after_first_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """The disk fills during the send: the pending file's first write (`prepare`, before the send)
    succeeds, every later one fails with ENOSPC."""

    import errno

    original = Path.write_text
    writes: list[Path] = []

    def _write(self: Path, *arguments: Any, **keywords: Any) -> int:
        writes.append(self)
        if len(writes) > 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        return original(self, *arguments, **keywords)

    monkeypatch.setattr(Path, "write_text", _write)


def test_a_deferred_alert_kept_before_a_failed_send_is_not_logged_as_not_kept(
    telegram_stub: _Stub,
    credentials: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-9b L residual (verifier, P2). `prepare` writes the deferred current alert to the
    pending file BEFORE the send. When the send then fails and the disk fills meanwhile (exactly
    when volume alerts fire), the after-failure write fails -- and the relay logged "NOT kept for a
    retry" although both alerts were on disk and went next run. It says what is true now: kept
    before the send, the failed attempt not recorded."""

    pending = _pending()
    environment = {
        **credentials,
        "R1_ALERT_TELEGRAM_API_BASE": telegram_stub.base,
        "R1_ALERT_PENDING_DIRECTORY": str(tmp_path / "state"),
    }
    path = pending.pending_path("checks-data", environment)
    pending.save(path, _kept(OLD_WAL_ALERT, ("wal_archive_gap",)))
    current = f"{HEADING}\n• volume_free: " + "v" * 3000
    document = {**_FAILING_DOCUMENT, "alert": {"text": current, "checks": ["volume_free"]}}
    telegram_stub.status = 500
    _fills_after_first_write(monkeypatch)

    code = _run_relay_in_process(monkeypatch, environment, document)
    log = Path(credentials["R1_ALERT_LOG_FILE"]).read_text("utf-8")
    assert code == 3, log
    assert "ALERT NOT DELIVERED" in log and "NOT kept" not in log, log
    assert "kept before the send" in log and "No space left" in log, log
    assert "failed attempt was not recorded" in log, log
    kept, _ = pending.load(path, now=NOON)
    assert [alert.text for alert in kept.alerts] == [OLD_WAL_ALERT, current]


def test_the_direct_path_does_not_call_a_deferred_alert_it_kept_not_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`check_shop_operations.deliver_alert._kept` had the same wording (the verifier named it)."""

    pending = _pending()
    checks = importlib.import_module("check_shop_operations")
    token = tmp_path / "token"
    token.write_text("probe-token", encoding="utf-8")
    directory = tmp_path / "state"
    path = pending.pending_path("direct", {"R1_ALERT_PENDING_DIRECTORY": str(directory)})
    pending.save(path, _kept(OLD_WAL_ALERT, ("wal_archive_gap",)))
    monkeypatch.setenv("R1_ALERT_TELEGRAM_TOKEN_FILE", str(token))
    monkeypatch.setenv("R1_ALERT_TELEGRAM_CHAT_ID", "1234")
    monkeypatch.setenv("R1_ALERT_PENDING_DIRECTORY", str(directory))

    def _refused(request: Any, timeout: float = 0) -> Any:
        raise OSError("network is unreachable")

    monkeypatch.setattr(checks.urllib.request, "urlopen", _refused)
    _fills_after_first_write(monkeypatch)
    failure = checks.CheckResult("volume_free", False, "v" * 3000, {})
    assert checks.deliver_alert([failure], now=NOON) is False
    stderr = capsys.readouterr().err
    assert "ALERT NOT DELIVERED: OSError" in stderr
    assert "NOT kept" not in stderr and "kept before the send" in stderr, stderr
    kept, _ = pending.load(path, now=NOON)
    assert [alert.checks for alert in kept.alerts] == [("wal_archive_gap",), ("volume_free",)]
