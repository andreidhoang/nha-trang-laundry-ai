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

import json
import os
import plistlib
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
from typing import Any

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
    assert set(installed_agents) == {"checks-data", "checks-host", "base-backup"}
    for agent in installed_agents.values():
        assert agent["ProgramArguments"][:2] == ["/bin/bash", "-lc"]


def test_both_check_agents_deliver_through_the_host_relay(
    installed_agents: dict[str, dict[str, Any]],
) -> None:
    for name in ("checks-data", "checks-host"):
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
