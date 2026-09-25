"""The checks that can actually fire on a one-host deterministic deployment.

`specs/PRODUCTION_OPERATIONS_SPEC_V1.md` §4.1 names seven conditions that page a human. **Five of
them have no referent in R1**, and saying so is more useful than shipping alerts that can never
fire -- an on-call who learns the pager is noise is how the real alert gets missed:

    outbox rows in UNKNOWN reconciliation   no channel and no sender; WORKER_INTERNAL_OUTBOX_ENABLED
                                            is false, so there is no send to be unsure about
    suppression check failure or bypass     nothing is ever sent
    provider auth / token refresh failure   no provider exists; nothing reads a provider credential
    policy decision point unavailable       the PDP is in-process, not a service. Its failure is an
                                            API 5xx, which the console-reachability check sees
    agent cell reaching an unexpected host  there is no Zone A

Two of the seven do apply -- the WAL archive gap and a capability flag enabled without a signed
manifest -- and two more are needed that §4.1 does not name, because that spec was written assuming
managed infrastructure and a channel-carrying system, and R1 is neither:

    database volume filling      how a correctly configured archiver kills the shop. A failing
                                 `archive_command` pins WAL segments forever, the disk fills,
                                 PostgreSQL stops accepting writes, and the counter cannot take an
                                 order. Nobody is watching a provider dashboard on one host.
    console unreachable          in R1 the console *is* the business. No channel, no agent, no
                                 fallback path.

Each check exits non-zero on failure so a host scheduler surfaces it, and emits one structured line
so the outcome is in the same stream as everything else. Alert *routing* is `DEC-025`: one Telegram
message to the owner. With `--emit-alert` this script sends nothing and prints the alert as one JSON
document instead, and `scripts/relay_shop_alert.py` delivers it from the host -- because the data
checks run on `database-private`, which has no route out, and an alert nobody receives is not an
alert (`SHOP-ALERT-DELIVERY-001`).
"""

from __future__ import annotations

# Put this directory on sys.path before importing the workspace bootstrap, so the script
# behaves identically whether it is run as __main__ or loaded by file path from a test.
import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import argparse
import json
import os
import shutil
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_observability import (
    CorrelationContext,
    EventSeverity,
    SafeStructuredLogger,
    StructuredEvent,
    configure_structured_logging,
    structured_logging_is_live,
)

#: `deploy/backup/backup-policy-v1.json` sets the recovery point at 900 seconds. An archive gap
#: wider than that means the recovery guarantee is silently gone, which is the failure this whole
#: check exists for: nothing else in the system notices.
MAX_ARCHIVE_GAP_SECONDS = 900

#: PostgreSQL stops accepting writes long before a volume is literally full, and a shop that cannot
#: take an order is down. Ten percent is the point at which somebody still has a day to act.
MIN_FREE_VOLUME_FRACTION = 0.10

#: Deploying the code never enables a capability. A flag reading true without a signed manifest in
#: `releases/gates/` is an authorization bypass, and §4.1 says to treat it as a security incident
#: rather than operational noise.
CAPABILITY_FLAGS = (
    "FEATURE_PUBLIC_CHANNELS_ENABLED",
    "FEATURE_AUTOMATED_SENDS_ENABLED",
    "FEATURE_AGENT_RUNTIME_ENABLED",
    "WORKER_INTERNAL_OUTBOX_ENABLED",
    "WORKER_AGENT_QUEUE_ENABLED",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    fields: dict[str, object]


def check_wal_archive_gap(
    database_url: str, *, now: datetime | None = None, recovery_mode: str | None = None
) -> CheckResult:
    """How long since a WAL segment was archived, and whether archiving is failing outright.

    `archive_timeout` bounds this on an idle shop: without it, an afternoon with no orders produces
    a recovery point hours old while every other signal reads healthy.
    """

    moment = now or datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT archived_count, last_archived_time, failed_count, last_failed_time"
            " FROM pg_stat_archiver"
        )
        row = cursor.fetchone()
        cursor.execute("SELECT current_setting('archive_mode', true)")
        archive_mode = (cursor.fetchone() or [None])[0]

    if archive_mode != "on":
        # "The operator knows which branch they are running" was the whole defect: the check did
        # not, and returned PASS either way. On the self-managed branch that is a blind pass on the
        # one guarantee the pilot week rests on -- archiving off, nothing archived, green tick. The
        # branch is now declared, and an undeclared branch is unknown, which means stop.
        if recovery_mode == "provider-managed":
            return CheckResult(
                "wal_archive_gap",
                passed=True,
                detail=(
                    f"archive_mode is {archive_mode!r} and the provider owns PITR "
                    "(declared managed branch)"
                ),
                fields={"archive_mode": str(archive_mode), "recovery_mode": recovery_mode},
            )
        return CheckResult(
            "wal_archive_gap",
            passed=False,
            detail=(
                f"archive_mode is {archive_mode!r} and no WAL is being archived"
                if recovery_mode == "self-managed"
                else (
                    f"archive_mode is {archive_mode!r} and the recovery branch is undeclared; "
                    "set --recovery-mode or R1_RECOVERY_MODE to self-managed or provider-managed"
                )
            ),
            fields={"archive_mode": str(archive_mode), "recovery_mode": recovery_mode},
        )

    archived, last_archived, failed, last_failed = row if row else (0, None, 0, None)
    if last_archived is None:
        return CheckResult(
            "wal_archive_gap",
            passed=False,
            detail="archiving is on and nothing has ever been archived",
            fields={"archived_count": int(archived or 0)},
        )

    gap = (moment - last_archived).total_seconds()
    failing = bool(failed) and last_failed is not None and last_failed > last_archived
    passed = gap <= MAX_ARCHIVE_GAP_SECONDS and not failing
    return CheckResult(
        "wal_archive_gap",
        passed=passed,
        detail=(
            f"last archived {gap:.0f}s ago (limit {MAX_ARCHIVE_GAP_SECONDS}s)"
            + (", and the most recent attempt failed" if failing else "")
        ),
        fields={"backup_age_s": int(gap), "failed_count": int(failed or 0)},
    )


def check_database_volume(path: str) -> CheckResult:
    usage = shutil.disk_usage(path)
    free = usage.free / usage.total if usage.total else 0.0
    return CheckResult(
        "database_volume",
        passed=free >= MIN_FREE_VOLUME_FRACTION,
        detail=(
            f"{free:.1%} free of {usage.total // (1024**3)}GiB"
            f" (limit {MIN_FREE_VOLUME_FRACTION:.0%})"
        ),
        fields={"free_fraction": round(free, 4)},
    )


#: A daily base backup, with room for one to be late before anybody is woken. Beyond this the
#: recovery point is not what the policy says it is, however healthy WAL archiving looks.
MAX_BASE_BACKUP_AGE_SECONDS = 26 * 3600


def check_base_backup_age(marker_path: str, *, now: datetime | None = None) -> CheckResult:
    """When a base backup last completed, read from the marker `base-backup.sh` writes on success.

    There was a WAL archive check and no base-backup check at all, and a WAL chain with no base
    backup restores nothing -- so the shop could have had a green tick on the only backup signal it
    watched while holding nothing it could actually restore from. The marker is written after the
    artifact is verified and uploaded, so its age is the age of a backup that exists rather than of
    an attempt.
    """

    moment = now or datetime.now(UTC)
    marker = _Path(marker_path)
    if not marker.is_file():
        return CheckResult(
            "base_backup_age",
            passed=False,
            detail=f"no base backup has ever completed (no marker at {marker_path})",
            fields={"marker": marker_path},
        )
    age = moment.timestamp() - marker.stat().st_mtime
    passed = age <= MAX_BASE_BACKUP_AGE_SECONDS
    return CheckResult(
        "base_backup_age",
        passed=passed,
        detail=(
            f"last base backup {age / 3600:.1f}h ago "
            f"(limit {MAX_BASE_BACKUP_AGE_SECONDS / 3600:.0f}h): "
            f"{marker.read_text(encoding='utf-8').strip()[:80]}"
        ),
        fields={"base_backup_age_s": int(age)},
    )


def check_capability_flags(compose_file: str) -> CheckResult:
    """Every capability flag must read false on the running containers, not in the compose file.

    `scripts/report_delivery_status.py` reads repository YAML and knows nothing about a host, so it
    cannot answer this. A flag set by hand on a running container is exactly the bypass it would
    miss.
    """

    enabled: list[str] = []
    services = ("api", "worker")
    for service in services:
        result = subprocess.run(
            ["docker", "compose", "-f", compose_file, "exec", "-T", service, "env"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return CheckResult(
                "capability_flags",
                passed=False,
                detail=(
                    f"could not read the environment of {service}: {result.stderr.strip()[:120]}"
                ),
                fields={"service": service},
            )
        environment = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        enabled.extend(
            f"{service}.{flag}"
            for flag in CAPABILITY_FLAGS
            if environment.get(flag, "false").strip().casefold() not in {"false", ""}
        )

    manifests = sorted(
        path.name
        for path in (_Path(__file__).resolve().parents[1] / "releases/gates").glob("*.json")
    )
    return CheckResult(
        "capability_flags",
        passed=not enabled,
        detail=(
            "every capability flag reads false"
            if not enabled
            else f"enabled without a signed manifest ({manifests or 'none'}): {enabled}"
        ),
        fields={"enabled": enabled, "signed_manifests": manifests},
    )


def check_console_reachable(
    url: str, *, timeout: float = 5.0, ca_file: str | None = None
) -> CheckResult:
    """Reach the console the way a tablet does: by its own name, over TLS it can verify.

    **The certificate authority is not optional here.** The console's hostname is internal, so no
    public CA can issue for it and the certificate is the shop's own -- which means the system trust
    store cannot verify it and `urlopen` fails with `CERTIFICATE_VERIFY_FAILED`. Measured against
    the pilot stack: this check reported the console unreachable while the console was serving
    perfectly, which is a permanent false alarm on the one check the runbooks call "the console is
    the business", every five minutes, for the whole week.

    Turning verification off would have made it green and worthless: it could then no longer tell
    the shop's console from anything at all answering on that address.
    """

    context = ssl.create_default_context(cafile=ca_file) if ca_file else None
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
            body = response.read(256).decode("utf-8", "replace")
            healthy = response.status == 200
    except urllib.error.HTTPError as error:
        # `/readyz` answers 503 when the console is up and its database is not. That is an answer,
        # not silence, and "did not answer" would send the owner to the network instead of the
        # database. (`HTTPError` is a `URLError`, so it has to be caught first.)
        answered = error.read(256).decode("utf-8", "replace") if error.fp else ""
        return CheckResult(
            "console_reachable",
            passed=False,
            detail=f"{url} answered {error.code} {answered.strip()[:80]}",
            fields={"url": url, "status": error.code},
        )
    except (urllib.error.URLError, OSError, ValueError) as error:
        return CheckResult(
            "console_reachable",
            passed=False,
            detail=(
                f"{url} did not answer: {error}"
                + (
                    ""
                    if ca_file
                    else " -- and no CA file was given, so a private certificate cannot be"
                    " verified. Set --console-ca-file / R1_CONSOLE_CA_FILE."
                )
            ),
            fields={"url": url, "ca_file_given": bool(ca_file)},
        )
    return CheckResult(
        "console_reachable",
        passed=healthy,
        detail=f"{url} answered {response.status} {body.strip()[:60]}",
        fields={"url": url, "status": response.status},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="append",
        choices=["wal", "base", "volume", "flags", "console", "all"],
        help="repeatable; defaults to every check that is configured",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--database-url-stdin",
        action="store_true",
        help=(
            "read the database URL from the first line of stdin, so it is never an argument or an "
            "environment variable of `docker run` -- both are visible to `ps` and `docker inspect`"
        ),
    )
    parser.add_argument("--volume-path", default=os.environ.get("R1_PGDATA_PATH"))
    parser.add_argument("--compose-file", default="compose.r1.yaml")
    parser.add_argument(
        "--base-backup-marker",
        default=os.environ.get("R1_BASE_BACKUP_MARKER"),
        help="the last-success marker base-backup.sh writes; its age is the backup's age",
    )
    parser.add_argument("--console-url", default=os.environ.get("R1_CONSOLE_HEALTH_URL"))
    parser.add_argument(
        "--console-ca-file",
        default=os.environ.get("R1_CONSOLE_CA_FILE"),
        help="the private CA that signed the console certificate; without it TLS cannot verify",
    )
    parser.add_argument(
        "--recovery-mode",
        choices=("self-managed", "provider-managed"),
        default=os.environ.get("R1_RECOVERY_MODE"),
        help="who owns point-in-time recovery; undeclared makes the WAL check refuse to guess",
    )
    parser.add_argument("--json", action="store_true", help="one JSON object, for a wrapper")
    parser.add_argument(
        "--emit-alert",
        action="store_true",
        help=(
            "print one alert document on stdout for scripts/relay_shop_alert.py and send nothing: "
            "the check then needs no network egress at all (SHOP-ALERT-DELIVERY-001)"
        ),
    )
    arguments = parser.parse_args()
    if arguments.database_url_stdin:
        supplied = _sys.stdin.readline().strip()
        if not supplied:
            raise SystemExit("--database-url-stdin was given and stdin held no database URL.")
        arguments.database_url = supplied

    selected = set(arguments.check or ["all"])
    run_all = "all" in selected

    # Name, the input it needs, and the flag that supplies it. Keeping the three together is what
    # makes the "explicitly asked for, cannot run" case expressible at all.
    planned: list[tuple[str, str, object, str]] = [
        ("wal", "wal_archive_gap", arguments.database_url, "--database-url"),
        ("volume", "database_volume", arguments.volume_path, "--volume-path"),
        ("base", "base_backup_age", arguments.base_backup_marker, "--base-backup-marker"),
        ("flags", "capability_flags", arguments.compose_file, "--compose-file"),
        ("console", "console_reachable", arguments.console_url, "--console-url"),
    ]

    runners = {
        "wal": lambda: check_wal_archive_gap(
            str(arguments.database_url), recovery_mode=arguments.recovery_mode
        ),
        "volume": lambda: check_database_volume(str(arguments.volume_path)),
        "base": lambda: check_base_backup_age(str(arguments.base_backup_marker)),
        "flags": lambda: check_capability_flags(str(arguments.compose_file)),
        "console": lambda: check_console_reachable(
            str(arguments.console_url), ca_file=arguments.console_ca_file
        ),
    }

    # A check named on the command line and then skipped for want of its input is the worst
    # outcome available: the operator asked for it, the exit code was 0, and nothing said the
    # question had gone unanswered. Cron mail reads as success. Refuse instead.
    unusable = [
        f"{selector} needs {flag}"
        for selector, _, value, flag in planned
        if selector in selected and not value
    ]
    if unusable:
        raise SystemExit(
            "These checks were asked for and cannot run: " + "; ".join(unusable) + ". "
            "A check that silently does not run is worse than one that fails."
        )

    results: list[CheckResult] = []
    for selector, name, value, _ in planned:
        if not ((run_all or selector in selected) and value):
            continue
        try:
            results.append(runners[selector]())
        except Exception as error:
            # `docker` absent from cron's PATH, or the database unreachable, used to abort main()
            # before any event was emitted and before `deliver_alert` ran -- so the one condition
            # most likely to coincide with a real outage was the one that produced no alert.
            results.append(
                CheckResult(
                    name,
                    passed=False,
                    detail=f"the check itself failed: {type(error).__name__}: {error}"[:200],
                    fields={"check_error": type(error).__name__},
                )
            )

    if not results:
        raise SystemExit(
            "No check could run. Each one needs its input: --database-url, --volume-path, "
            "--console-url. A check that silently does not run is worse than one that fails."
        )

    configure_structured_logging()
    if not structured_logging_is_live():
        # The events below are this script's entire durable output; the printed lines are for
        # whoever is watching a terminal. If the stream is dead there is nothing to find later,
        # and saying so on stderr costs nothing and does not change the exit code.
        print(
            "check_shop_operations: the structured log stream is not live; "
            "these results will not be recorded anywhere",
            file=_sys.stderr,
        )
    logger = SafeStructuredLogger()
    correlation = CorrelationContext.new()
    for result in results:
        logger.emit(
            StructuredEvent(
                component="operations",
                name=result.name,
                outcome="success" if result.passed else "failure",
                correlation=correlation,
                severity=EventSeverity.INFO if result.passed else EventSeverity.ERROR,
                fields=result.fields,
            )
        )

    # With `--emit-alert`, stdout carries the structured stream and the one alert document the relay
    # parses, so the lines meant for a person go to stderr -- which is the scheduler's log anyway.
    human = _sys.stderr if arguments.emit_alert else _sys.stdout
    if arguments.json:
        print(
            json.dumps(
                {r.name: {"passed": r.passed, "detail": r.detail} for r in results},
                ensure_ascii=False,
            )
        )
    else:
        for result in results:
            print(
                f"  {'OK ' if result.passed else '!!!'} {result.name}: {result.detail}", file=human
            )

    failures = [result for result in results if not result.passed]
    moment = datetime.now(UTC)
    if arguments.emit_alert:
        # The relay on the host delivers. Nothing here opens a socket, which is the point: on the
        # self-managed branch this runs on `database-private`, and that network has no way out.
        print(json.dumps(alert_document(results, now=moment), ensure_ascii=False), flush=True)
    elif failures:
        deliver_alert(failures, now=moment)

    return 0 if not failures else 1


#: `DEC-025`. `console_reachable` is the only check whose failure is a visible outage rather than a
#: silent loss of a guarantee, and a console down at 03:00 that recovers before opening does not
#: need anybody woken. The other three alert at any hour: a stale archive and a filling disk are
#: losses nobody would otherwise notice, and a capability flag enabled without a signed manifest is
#: a security incident under the operations spec.
QUIET_HOURS_CHECKS = frozenset({"console_reachable"})
QUIET_HOURS_START = 21
QUIET_HOURS_END = 7

#: The shop is in Nha Trang and the decision is written in the shop's hours. Comparing
#: `datetime.now(UTC).hour` against them inverted the window exactly: at UTC+7 the script alerted
#: from 14:00 to 04:00 local and stayed silent from 04:00 to 14:00, so the case
#: `production-deploy-day.md` names by name -- "one down at 07:45 needs everybody" -- was 00:45 UTC
#: and suppressed, while a 22:00 outage nobody needed to see woke the owner. The test encoded the
#: same UTC hours and called 03:00 UTC "night", so it passed while pinning the bug.
SHOP_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


#: What `--emit-alert` prints and `scripts/relay_shop_alert.py` reads. Versioned because the two run
#: in different places -- the check in a container image, the relay from the host checkout -- and an
#: image built from an older checkout must be refused rather than half-understood.
ALERT_DOCUMENT_SCHEMA = "nha-trang-laundry.shop-alert.v1"
ALERT_HEADING = "Bảng vận hành — cần xem ngay:"


def _reportable(
    failures: list[CheckResult], *, now: datetime
) -> tuple[list[CheckResult], list[CheckResult]]:
    """Split failures into those that alert now and those `DEC-025` holds for opening hours."""

    local_hour = now.astimezone(SHOP_TIMEZONE).hour
    quiet = local_hour >= QUIET_HOURS_START or local_hour < QUIET_HOURS_END
    held = [failure for failure in failures if quiet and failure.name in QUIET_HOURS_CHECKS]
    return [failure for failure in failures if failure not in held], held


def alert_text(failures: list[CheckResult], *, now: datetime) -> str | None:
    """The one message the owner receives, or `None` when nothing is reportable at this hour."""

    reportable, _ = _reportable(failures, now=now)
    if not reportable:
        return None
    lines = [ALERT_HEADING]
    lines.extend(f"• {failure.name}: {failure.detail}" for failure in reportable)
    return "\n".join(lines)


def alert_document(results: list[CheckResult], *, now: datetime) -> dict[str, object]:
    """Everything the host relay needs to deliver, and nothing it would have to decide itself.

    The quiet-hours rule is applied here, where the failure was seen, so the relay stays a courier:
    it delivers `alert.text` verbatim or, when `alert` is null, delivers nothing.
    """

    failures = [result for result in results if not result.passed]
    reportable, held = _reportable(failures, now=now)
    text = alert_text(failures, now=now)
    return {
        "schema": ALERT_DOCUMENT_SCHEMA,
        "passed": not failures,
        "results": {r.name: {"passed": r.passed, "detail": r.detail} for r in results},
        "alert": (
            None
            if text is None
            else {"text": text, "checks": [failure.name for failure in reportable]}
        ),
        "suppressed": [failure.name for failure in held],
    }


def deliver_alert(failures: list[CheckResult], *, now: datetime) -> bool:
    """Send one message to the owner, per `DEC-025`. Returns whether anything was sent.

    **Prefer `--emit-alert` and `scripts/relay_shop_alert.py`.** This direct path only works where
    the check itself has internet -- never on `database-private`, which is where the data checks
    run -- and it was the reason those checks could not tell anybody anything. It stays for a check
    run by hand on a host with egress.

    **This is not a customer channel and must never become one.** It posts directly over HTTPS from
    this script -- never through the outbox, the channel adapter or the consent machinery -- so it
    is not an automated send and `FEATURE_AUTOMATED_SENDS_ENABLED` stays false and stays meaningful.
    The bot is separate from any future customer bot, the recipient is a single configured chat, and
    there is no inbound handler anywhere.

    Unconfigured is not an error. `DEC-025` also keeps the host scheduler's own mail as the floor,
    so a deployment that has not set the token still surfaces failures through a non-zero exit --
    what it must not do is pretend an alert went out.
    """

    token_file = os.environ.get("R1_ALERT_TELEGRAM_TOKEN_FILE")
    chat_id = os.environ.get("R1_ALERT_TELEGRAM_CHAT_ID")
    if not token_file or not chat_id:
        return False

    text = alert_text(failures, now=now)
    if text is None:
        return False

    try:
        token = _Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as error:
        _not_delivered(f"cannot read R1_ALERT_TELEGRAM_TOKEN_FILE ({error.strerror})")
        return False

    body = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode()

    try:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            delivered = bool(200 <= response.status < 300)
    except (urllib.error.URLError, OSError, ValueError) as error:
        # A failed alert must not mask the failure it was carrying: the exit code and the structured
        # line stand on their own, so this returns rather than raises. But it is never silent --
        # `SHOP-ALERT-DELIVERY-001` was a swallowed `URLError` here that nobody could see.
        reason = str(getattr(error, "reason", error)).replace(token, "<token>")
        _not_delivered(f"{type(error).__name__}: {reason}"[:200])
        return False
    if not delivered:
        _not_delivered("telegram did not answer 2xx")
    return delivered


def _not_delivered(reason: str) -> None:
    print(f"check_shop_operations: ALERT NOT DELIVERED: {reason}", file=_sys.stderr, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
