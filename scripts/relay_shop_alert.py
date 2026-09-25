"""Deliver a shop-operations alert from the host, because the check that raised it cannot (DEC-025).

**Why a relay.** The data checks -- WAL archive gap, base-backup age, database volume -- have to run
inside the database's own network, and that network is `internal: true` by design (ADR-0007 §1): it
has no route to the internet. The check used to post to Telegram from in there anyway, catch the
inevitable `URLError`, and return as if nothing had happened. The two failures `DEC-025` calls the
ones nobody goes looking for could therefore tell nobody, and nothing said so.

So the check no longer sends. With `--emit-alert` it prints one JSON document on stdout and needs no
egress at all; this script runs it, reads that document, and posts the alert from the host, which
has internet. The database network stays internal.

    relay_shop_alert.py --label checks-data -- docker run ... check_shop_operations.py --emit-alert

**Exit status is the contract.** `launchctl list` shows it and the scheduler logs it:

    0   the check passed; nothing to say
    1   the check failed, and the alert was delivered (or held for quiet hours, per DEC-025)
    2   this script was invoked wrongly; nothing was run
    3   the check failed and the alert was NOT delivered -- a log line says why

A failed delivery is never swallowed. It is exit 3 and one line on stderr and in R1_ALERT_LOG_FILE,
both of which the owner can find without knowing where to look for a Python traceback.

**DEC-025's constraints, as properties of this file.** A separate bot, whose token is read from a
host file (`R1_ALERT_TELEGRAM_TOKEN_FILE`) and never from the repository or a command line. One
recipient, read from a host file (`R1_ALERT_TELEGRAM_CHAT_ID_FILE`) -- nothing in an alert chooses
who receives it. No inbound handler: this script only ever makes one POST to `sendMessage`. And no
outbox, channel adapter or consent machinery: it imports nothing from the application, so
`FEATURE_AUTOMATED_SENDS_ENABLED` stays false and stays meaningful.

`R1_ALERT_TELEGRAM_API_BASE` exists for tests, which point it at a local stub. It accepts only the
real API or a plain-http loopback address, so it cannot be used to send the token anywhere else.
"""

from __future__ import annotations

# Put this directory on sys.path before importing the workspace bootstrap, like every entry point.
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path

#: The document `check_shop_operations.py --emit-alert` prints. Anything else on stdout is the
#: check's structured log stream and is passed through untouched.
ALERT_SCHEMA = "nha-trang-laundry.shop-alert.v1"

TELEGRAM_API_BASE = "https://api.telegram.org"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

EXIT_PASSED = 0
EXIT_CHECK_FAILED = 1
EXIT_USAGE = 2
EXIT_NOT_DELIVERED = 3

#: Below launchd's five-minute interval, so a hung `docker run` cannot stack a second tick on top.
CHECK_TIMEOUT_SECONDS = 240
SEND_TIMEOUT_SECONDS = 10
#: Telegram's limit is 4096 characters; a clipped alert is still an alert.
MAX_MESSAGE_CHARACTERS = 3800

ALERT_HEADING = "Bảng vận hành — cần xem ngay:"

#: A bot token is `<digits>:<url-safe>`. Checked so a malformed file fails as "misconfigured" rather
#: than as a request to some other path on the API.
TOKEN_FORMAT = re.compile(r"[0-9]{3,20}:[A-Za-z0-9_-]{20,}")
CHAT_ID_FORMAT = re.compile(r"-?[0-9]{1,20}")


class UsageError(Exception):
    """The relay was invoked in a way that cannot be right; nothing is run."""


class DeliveryError(Exception):
    """The alert did not reach Telegram. The message never contains the token."""


@dataclass(frozen=True)
class Outcome:
    failed: bool
    text: str | None
    checks: tuple[str, ...]
    note: str


def resolve_api_base(value: str | None) -> str:
    """The real API, or -- for tests only -- a plain-http loopback stub. Nothing else."""

    if not value or value == TELEGRAM_API_BASE:
        return TELEGRAM_API_BASE
    parts = urllib.parse.urlsplit(value)
    if (
        parts.scheme == "http"
        and parts.hostname in LOOPBACK_HOSTS
        and parts.username is None
        and parts.password is None
        and parts.path in ("", "/")
        and not parts.query
        and not parts.fragment
    ):
        return value.rstrip("/")
    raise UsageError(
        "R1_ALERT_TELEGRAM_API_BASE may only be the real API or a local http://127.0.0.1:<port> "
        "stub for tests; refusing to send a bot token to " + repr(value)
    )


def _read_single_line(path_value: str | None, variable: str, pattern: re.Pattern[str]) -> str:
    if not path_value:
        raise DeliveryError(f"{variable} is not set")
    path = _Path(path_value)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise DeliveryError(f"{variable}: cannot read {path} ({error.strerror})") from error
    if not pattern.fullmatch(value):
        raise DeliveryError(f"{variable}: {path} does not hold a well-formed value")
    return value


def load_credentials(environment: dict[str, str]) -> tuple[str, str]:
    token = _read_single_line(
        environment.get("R1_ALERT_TELEGRAM_TOKEN_FILE"),
        "R1_ALERT_TELEGRAM_TOKEN_FILE",
        TOKEN_FORMAT,
    )
    chat_file = environment.get("R1_ALERT_TELEGRAM_CHAT_ID_FILE")
    if chat_file:
        chat_id = _read_single_line(chat_file, "R1_ALERT_TELEGRAM_CHAT_ID_FILE", CHAT_ID_FORMAT)
    else:
        # The Linux server's crontab shape passes the chat as a value; it is not a secret.
        chat_id = environment.get("R1_ALERT_TELEGRAM_CHAT_ID", "").strip()
        if not CHAT_ID_FORMAT.fullmatch(chat_id):
            raise DeliveryError(
                "neither R1_ALERT_TELEGRAM_CHAT_ID_FILE nor a well-formed R1_ALERT_TELEGRAM_CHAT_ID"
            )
    return token, chat_id


def send_message(text: str, *, token: str, chat_id: str, api_base: str) -> None:
    """One POST to `sendMessage`. Raises `DeliveryError` unless Telegram says `ok: true`."""

    body = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base}/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as response:
            status = int(response.status)
            payload = response.read(65536)
    except urllib.error.HTTPError as error:
        raise DeliveryError(f"telegram answered HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        raise DeliveryError(
            f"telegram unreachable: {str(reason).replace(token, '<token>')}"
        ) from None
    if not 200 <= status < 300:
        raise DeliveryError(f"telegram answered HTTP {status}")
    try:
        answer = json.loads(payload)
    except ValueError:
        raise DeliveryError(
            f"telegram answered HTTP {status} with a body that is not JSON"
        ) from None
    if not isinstance(answer, dict) or answer.get("ok") is not True:
        description = str(answer.get("description", ""))[:120] if isinstance(answer, dict) else ""
        raise DeliveryError(f"telegram answered ok=false: {description}".rstrip(": "))


def _documents(stdout: str) -> tuple[list[dict[str, object]], list[str]]:
    documents: list[dict[str, object]] = []
    other: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and ALERT_SCHEMA in stripped:
            try:
                parsed = json.loads(stripped)
            except ValueError:
                other.append(line)
                continue
            if isinstance(parsed, dict) and parsed.get("schema") == ALERT_SCHEMA:
                documents.append(parsed)
                continue
        other.append(line)
    return documents, other


def _tail(text: str, limit: int = 300) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return " | ".join(lines[-3:])[-limit:]


def interpret(label: str, returncode: int, stdout: str, stderr: str) -> tuple[Outcome, list[str]]:
    """What the check said, and what to tell the owner. Pure: no clock, no network."""

    documents, passthrough = _documents(stdout)
    if len(documents) == 1:
        document = documents[0]
        alert = document.get("alert")
        passed = document.get("passed") is True and returncode == 0
        if isinstance(alert, dict) and isinstance(alert.get("text"), str):
            checks = tuple(str(name) for name in alert.get("checks", []) if isinstance(name, str))
            return Outcome(True, str(alert["text"]), checks, "check failed"), passthrough
        if passed:
            return Outcome(False, None, (), "every check passed"), passthrough
        if document.get("passed") is False:
            suppressed = document.get("suppressed") or []
            return (
                Outcome(True, None, (), f"check failed; held for quiet hours: {suppressed}"),
                passthrough,
            )
        # A passing document with a failing exit status: something after the checks broke.
        reason = f"exited {returncode} after reporting a pass"
    elif not documents:
        reason = f"exited {returncode} without a report"
    else:
        reason = f"printed {len(documents)} reports instead of one"

    # The container never answered: Docker not running, the image missing, the network gone. That
    # is exactly when the backup checks are blind, so it is the alert rather than a log line.
    detail = _tail(stderr) or "(no error output)"
    text = f"{ALERT_HEADING}\n• {label}: kiểm tra không chạy được -- {reason}: {detail}"
    return Outcome(True, text, (label,), f"check could not run: {reason}"), passthrough


def _log(label: str, message: str, log_file: str | None) -> None:
    moment = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{moment} relay_shop_alert[{label}] {message}"
    print(line, file=_sys.stderr, flush=True)
    if log_file:
        try:
            with open(log_file, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as error:
            print(
                f"{moment} relay_shop_alert[{label}] cannot append to {log_file}: {error.strerror}",
                file=_sys.stderr,
                flush=True,
            )


def _run_check(
    command: list[str], environment: dict[str, str], stdin_file: str | None
) -> tuple[int, str, str]:
    """Run the check. Every way it can fail to run becomes a status and a reason, never a raise."""

    payload: bytes | None = None
    if stdin_file is not None:
        if not stdin_file:
            return 66, "", "--stdin-file is empty: the variable naming the secret file is not set"
        try:
            payload = _Path(stdin_file).read_bytes()
        except OSError as error:
            return 66, "", f"cannot read {stdin_file}: {error.strerror}"
    try:
        completed = subprocess.run(
            command,
            env=environment,
            input=payload if payload is not None else b"",
            capture_output=True,
            timeout=CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        partial = error.stdout.decode("utf-8", "replace") if isinstance(error.stdout, bytes) else ""
        return -1, partial, f"timed out after {CHECK_TIMEOUT_SECONDS}s"
    except OSError as error:
        return 127, "", f"{command[0]}: {error.strerror}"
    return (
        completed.returncode,
        completed.stdout.decode("utf-8", "replace"),
        completed.stderr.decode("utf-8", "replace"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a shop check and deliver its alert from the host (DEC-025)."
    )
    parser.add_argument("--label", required=True, help="which schedule this is, e.g. checks-data")
    parser.add_argument(
        "--log-file",
        default=os.environ.get("R1_ALERT_LOG_FILE"),
        help="append one line per delivery attempt here as well as to stderr",
    )
    parser.add_argument(
        "--stdin-file",
        help=(
            "feed this host file to the check on stdin -- how the database URL reaches a container "
            "without appearing in `ps` or `docker inspect` (OPS-HARDENING-002)"
        ),
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- then the check to run")
    arguments = parser.parse_args(argv)
    command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
    label = str(arguments.label)
    log_file = arguments.log_file or None

    try:
        if not command:
            raise UsageError("give the check to run after --")
        if not re.fullmatch(r"[a-z0-9-]{1,40}", label):
            raise UsageError(f"--label must be a short lowercase name, not {label!r}")
        api_base = resolve_api_base(os.environ.get("R1_ALERT_TELEGRAM_API_BASE"))
    except UsageError as error:
        print(f"relay_shop_alert: {error}", file=_sys.stderr)
        return EXIT_USAGE

    # The check gets no Telegram setting: it has no business sending, and a check run on the host
    # would otherwise be able to send a second copy of the same alert.
    child_environment = {
        key: value for key, value in os.environ.items() if not key.startswith("R1_ALERT_")
    }
    returncode, stdout, stderr = _run_check(command, child_environment, arguments.stdin_file)

    outcome, passthrough = interpret(label, returncode, stdout, stderr)
    for line in passthrough:
        print(line)
    if stderr:
        _sys.stderr.write(stderr if stderr.endswith("\n") else stderr + "\n")
    _sys.stdout.flush()

    if outcome.text is None:
        if outcome.failed:
            _log(label, outcome.note, log_file)
            return EXIT_CHECK_FAILED
        try:
            load_credentials(dict(os.environ))
        except DeliveryError as error:
            # Nothing to say this tick, but the next failure would go nowhere: say so every time.
            _log(label, f"WARNING alert delivery is not configured: {error}", log_file)
        return EXIT_PASSED

    checks = ", ".join(outcome.checks) or label
    try:
        token, chat_id = load_credentials(dict(os.environ))
        send_message(
            outcome.text[:MAX_MESSAGE_CHARACTERS], token=token, chat_id=chat_id, api_base=api_base
        )
    except DeliveryError as error:
        _log(label, f"ALERT NOT DELIVERED: {error} -- failing: {checks}", log_file)
        return EXIT_NOT_DELIVERED
    _log(label, f"ALERT DELIVERED -- failing: {checks}", log_file)
    return EXIT_CHECK_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
