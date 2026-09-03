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
so the outcome is in the same stream as everything else. Alert *routing* is `DEC-025` and is the
owner's: there is deliberately no channel, no SMTP and no paging provider here, and an alert nobody
receives is not an alert.
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
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
import workspace_env  # noqa: F401  # keep first: puts the workspace on sys.path
from nha_trang_laundry_observability import (
    CorrelationContext,
    EventSeverity,
    SafeStructuredLogger,
    StructuredEvent,
    configure_structured_logging,
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


def check_wal_archive_gap(database_url: str, *, now: datetime | None = None) -> CheckResult:
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
        # Not a failure on a provider-managed database, where PITR is the provider's. It is a
        # failure on the self-managed branch, and the operator knows which they are running.
        return CheckResult(
            "wal_archive_gap",
            passed=True,
            detail=f"archive_mode is {archive_mode!r}; recovery is the provider's (managed branch)",
            fields={"archive_mode": str(archive_mode)},
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


def check_console_reachable(url: str, *, timeout: float = 5.0) -> CheckResult:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(256).decode("utf-8", "replace")
            healthy = response.status == 200
    except (urllib.error.URLError, OSError, ValueError) as error:
        return CheckResult(
            "console_reachable",
            passed=False,
            detail=f"{url} did not answer: {error}",
            fields={"url": url},
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
        choices=["wal", "volume", "flags", "console", "all"],
        help="repeatable; defaults to every check that is configured",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--volume-path", default=os.environ.get("R1_PGDATA_PATH"))
    parser.add_argument("--compose-file", default="compose.r1.yaml")
    parser.add_argument("--console-url", default=os.environ.get("R1_CONSOLE_HEALTH_URL"))
    parser.add_argument("--json", action="store_true", help="one JSON object, for a wrapper")
    arguments = parser.parse_args()

    selected = set(arguments.check or ["all"])
    run_all = "all" in selected
    results: list[CheckResult] = []

    if (run_all or "wal" in selected) and arguments.database_url:
        results.append(check_wal_archive_gap(arguments.database_url))
    if (run_all or "volume" in selected) and arguments.volume_path:
        results.append(check_database_volume(arguments.volume_path))
    if run_all or "flags" in selected:
        results.append(check_capability_flags(arguments.compose_file))
    if (run_all or "console" in selected) and arguments.console_url:
        results.append(check_console_reachable(arguments.console_url))

    if not results:
        raise SystemExit(
            "No check could run. Each one needs its input: --database-url, --volume-path, "
            "--console-url. A check that silently does not run is worse than one that fails."
        )

    configure_structured_logging()
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

    if arguments.json:
        print(
            json.dumps(
                {r.name: {"passed": r.passed, "detail": r.detail} for r in results},
                ensure_ascii=False,
            )
        )
    else:
        for result in results:
            print(f"  {'OK ' if result.passed else '!!!'} {result.name}: {result.detail}")

    failures = [result for result in results if not result.passed]
    if failures:
        deliver_alert(failures, now=datetime.now(UTC))

    return 0 if not failures else 1


#: `DEC-025`. `console_reachable` is the only check whose failure is a visible outage rather than a
#: silent loss of a guarantee, and a console down at 03:00 that recovers before opening does not
#: need anybody woken. The other three alert at any hour: a stale archive and a filling disk are
#: losses nobody would otherwise notice, and a capability flag enabled without a signed manifest is
#: a security incident under the operations spec.
QUIET_HOURS_CHECKS = frozenset({"console_reachable"})
QUIET_HOURS_START = 21
QUIET_HOURS_END = 7


def deliver_alert(failures: list[CheckResult], *, now: datetime) -> bool:
    """Send one message to the owner, per `DEC-025`. Returns whether anything was sent.

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

    quiet = now.hour >= QUIET_HOURS_START or now.hour < QUIET_HOURS_END
    reportable = [
        failure for failure in failures if not (quiet and failure.name in QUIET_HOURS_CHECKS)
    ]
    if not reportable:
        return False

    try:
        token = _Path(token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return False

    lines = ["Bảng vận hành — cần xem ngay:"]
    lines.extend(f"• {failure.name}: {failure.detail}" for failure in reportable)
    body = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": "\n".join(lines), "disable_web_page_preview": "true"}
    ).encode()

    try:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return bool(200 <= response.status < 300)
    except (urllib.error.URLError, OSError, ValueError):
        # A failed alert must not mask the failure it was carrying. The exit code and the structured
        # line already stand on their own; swallowing this keeps the check's own verdict intact.
        return False


if __name__ == "__main__":
    raise SystemExit(main())
