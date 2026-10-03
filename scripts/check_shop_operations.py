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
import bisect
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

import psycopg
import shop_alert_pending as pending_alerts
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
    #: Passed, and something is coming due that a person should decide (`outbox_rows`). Shown and
    #: logged; never an alert -- a warning that paged every five minutes would teach the owner to
    #: ignore the pager.
    warning: bool = False
    #: Failing, and the owner was already told today (`console_certificate`, once a shop day): not
    #: alerted again by this run, and the run still fails.
    told_today: bool = False


#: `DEC-051`. Record-only outbox rows are kept -- the outbox trigger refuses deletion and no
#: retention window has been approved -- and the decision is revisited when the table passes this
#: many rows. At the pilot's pace (about a thousand rows a busy day) that is years away; the check
#: says so before it is a surprise.
OUTBOX_ROW_REVIEW_LINE = 1_000_000


def _spaced(number: int) -> str:
    """`1 000 000`, the way the shop writes a large number."""

    return f"{number:,}".replace(",", " ")


def check_outbox_rows(database_url: str) -> CheckResult:
    """How many rows `outbox_events` holds, warning past `OUTBOX_ROW_REVIEW_LINE` (`DEC-051`)."""

    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM outbox_events")
        row = cursor.fetchone()
    rows = int(row[0]) if row else 0
    over = rows > OUTBOX_ROW_REVIEW_LINE
    detail = f"{_spaced(rows)} outbox rows"
    if over:
        detail += (
            f", past the {_spaced(OUTBOX_ROW_REVIEW_LINE)} line DEC-051 set: decide outbox "
            "retention now (record-only rows are kept until a retention window is approved)"
        )
    return CheckResult(
        "outbox_rows",
        passed=True,
        detail=detail,
        fields={"outbox_rows": rows, "review_line": OUTBOX_ROW_REVIEW_LINE},
        warning=over,
    )


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


#: `DEC-052`: the shop CA's key is not kept, so a console certificate cannot be re-signed; near its
#: expiry a new CA is minted (`bootstrap_shop_local.py --new-ca`) and trusted on every tablet. From
#: this many days out the till's daily check tells the owner -- the same line the bootstrap uses,
#: which a test holds equal. Before round 9b only the bootstrap said it, and nobody runs the
#: bootstrap in normal operation: the first sign would have been every tablet refusing the console.
CERTIFICATE_RENEW_BEFORE_DAYS = 60
CERTIFICATE_RENEWAL_STEP = (
    "Gia hạn: chạy scripts/bootstrap_shop_local.py --backup-recipient <khóa age1…> --new-ca, rồi "
    "cài .shop/ca/ca.crt mới lên từng máy và bật tin cậy (docs/runbooks/shop-pilot.md §2)."
)


def check_console_certificate(path: str, *, now: datetime) -> CheckResult:
    """How long the console's TLS certificate has left, from the certificate itself (`DEC-052`).

    Fails from `CERTIFICATE_RENEW_BEFORE_DAYS` before expiry, when expired, and when the file is
    missing or not a certificate -- each is a renewal to do, and the message says how. Pure: the
    till's agent runs it through `run_certificate_check`, which keeps it to one message a shop day.
    """

    from cryptography import x509  # the host checks' venv has it; the data-check image need not

    try:
        leaf = x509.load_pem_x509_certificate(_Path(path).read_bytes())
    except FileNotFoundError:
        return CheckResult(
            "console_certificate",
            passed=False,
            detail=f"không thấy chứng chỉ console ở {path} (no certificate). "
            + CERTIFICATE_RENEWAL_STEP,
            fields={"path": path, "readable": False},
        )
    except (OSError, ValueError) as error:
        return CheckResult(
            "console_certificate",
            passed=False,
            detail=f"không đọc được chứng chỉ console {path} ({type(error).__name__}). "
            + CERTIFICATE_RENEWAL_STEP,
            fields={"path": path, "readable": False},
        )
    expires = leaf.not_valid_after_utc
    remaining = expires - now
    shown = expires.astimezone(SHOP_TIMEZONE).strftime("%d/%m/%Y")
    fields: dict[str, object] = {"expires_at": expires.isoformat(), "days_left": remaining.days}
    if remaining <= timedelta(0):
        return CheckResult(
            "console_certificate",
            passed=False,
            detail=f"chứng chỉ console đã hết hạn ngày {shown} (expired). "
            + CERTIFICATE_RENEWAL_STEP,
            fields=fields,
        )
    if remaining <= timedelta(days=CERTIFICATE_RENEW_BEFORE_DAYS):
        return CheckResult(
            "console_certificate",
            passed=False,
            detail=(
                f"chứng chỉ console hết hạn ngày {shown} (còn {remaining.days} ngày). "
                + CERTIFICATE_RENEWAL_STEP
            ),
            fields=fields,
        )
    return CheckResult(
        "console_certificate",
        passed=True,
        detail=f"console certificate valid until {shown} ({remaining.days} days)",
        fields=fields,
    )


CERTIFICATE_NOTICE_SCHEMA = "nha-trang-laundry.certificate-notice.v1"


def run_certificate_check(path: str, *, notice_state: str | None, now: datetime) -> CheckResult:
    """`check_console_certificate`, told to the owner once a shop day, from opening (`DEC-052`).

    The till's `checks-daily` agent runs this every hour. It used to run once, at 09:00 -- and a
    09:00 missed while the Mac slept ran on its next wake; at 23:30 `DEC-025` held the notice (a
    quiet-hours check), the relay had nothing to keep, and nothing ran again until the next 09:00:
    the notice was dropped for the day, not held for the morning (round-9b verifier, round 2).

    So `notice_state` records the shop day the owner was told. A failing run in opening hours that
    finds no record for today tells (and records); one that finds today's record is `told_today`,
    which still fails but alerts nobody; at night nothing is recorded, so the first run from 07:00
    tells. Recorded when raised, not when delivered: a send that fails is the relay's to retry
    (`shop_alert_pending`), so the record never swallows a notice. An unreadable record tells again
    rather than never; one that cannot be written tells and says so. Without `notice_state` (a
    check run by hand) every failing run tells.
    """

    result = check_console_certificate(path, now=now)
    if result.passed or notice_state is None or pending_alerts.in_quiet_hours(now):
        return result
    today = now.astimezone(SHOP_TIMEZONE).date().isoformat()
    record = _Path(notice_state)
    try:
        document = json.loads(record.read_text(encoding="utf-8"))
        told_on = (
            document.get("told_on")
            if isinstance(document, dict) and document.get("schema") == CERTIFICATE_NOTICE_SCHEMA
            else None
        )
    except (OSError, ValueError):
        told_on = None
    if told_on == today:
        return replace(result, told_today=True)
    try:
        record.parent.mkdir(parents=True, exist_ok=True)
        temporary = record.with_name(f".{record.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps({"schema": CERTIFICATE_NOTICE_SCHEMA, "told_on": today}) + "\n", "utf-8"
        )
        os.replace(temporary, record)
    except OSError as error:
        return replace(
            result,
            detail=f"{result.detail} (không ghi được {record} -- {error.strerror}; "
            "sẽ báo lại mỗi giờ)",
        )
    return result


# --- OPS-OBSERVABILITY-009: what the application itself is doing ---------------------------------
#
# Every check above looks at infrastructure. None looked at the application, so payments could
# answer 500 all afternoon and nobody would be told (review finding P2). This one reads the API's
# own structured log -- the lines `SafeStructuredLogger` writes, which the API also appends to a
# file on a host directory (`STRUCTURED_LOG_FILE`, `PLATFORM-RESIDUAL-009B` L4) so they outlive the
# container -- and counts three things.
#
# **The defaults, in one place.** `docs/runbooks/shop-pilot.md` §6 prints this table and a test
# holds the two together. A count at or above its figure fails the check.
#
#   server_errors                 1   an answer 500-599, except the two 503s that are answers by
#                                     design: one the API wrote `database.request_refused` for
#                                     (same correlation id; counted below instead), and `/readyz`
#                                     saying the database is gone, which is the console check's
#                                     finding and held at night by `DEC-025`. Every other 503 --
#                                     "staff identity unavailable", "operations unavailable" -- is
#                                     an outage nothing else reports, so it counts here. A 500 tells
#                                     staff "Máy chủ gặp lỗi. Đừng thử lại": the outcome is unknown
#                                     and somebody has to reconcile it, so one is enough.
#   database_refusals             5   `database.request_refused` (the designed 503: busy or
#                                     unreachable database, nothing written, the console retries).
#                                     One is a normal collision; five in five minutes is a database
#                                     in trouble.
#   browser_boundary_rejections  10   `auth.browser_boundary` (origin or CSRF refused). A tab left
#                                     open across a deploy can produce a few; ten in five minutes is
#                                     somebody or something forging requests.
#
# **Every line is read by exactly one run.** The scheduler starts a run every five minutes, but the
# window used to end at whatever moment this check got to run, after the flags and console checks;
# two runs only met edge to edge when those took exactly as long both times, and a 500 in between
# was counted by neither (round-9 verifier). A run delayed or skipped by launchd lost all of its
# five minutes. Now each run saves the instant of the last line it counted (`AppSignalCursor`) and
# the next one counts from there, however long ago that was.
#
# **And exactly once when lines arrive out of order** (`PLATFORM-RESIDUAL-009B` L4). Lines are not
# written in `occurred_at` order -- a request that began first can finish and log second -- and a
# line read through another transport (a rotated file, `docker compose logs`) is the same event in
# different bytes. A position that was only an instant lost the first kind for good and counted the
# second twice. So each run re-reads `APP_SIGNAL_OVERLAP_SECONDS` behind the newest line counted
# and identifies a line by what it is -- event, correlation id, `occurred_at` -- not by its bytes:
# a line in the overlap is counted unless its identity is among those already counted, which the
# position keeps (`recent`) for exactly that long. A line that arrives more than the overlap late
# is beyond the guarantee and is not counted; fifteen minutes is three runs, and a log line that
# late is a stalled host, which the liveness rule below reports.
#
# `server_errors` is the number of new lines; the two rates are the most that fell in any five
# minutes ending at a new line -- reaching back before the saved instant, so a burst split by a run
# boundary is still one burst, and an hour of skipped runs does not turn a trickle into one.
# **Liveness uses a fixed look-back:** the console check asks `/readyz` every five minutes and
# Docker's healthcheck asks `/healthz` every thirty seconds, so a healthy API always has a line in
# the last fifteen, and none at all means the stream is not reaching this check -- which is not the
# same as nothing having failed.
APP_SIGNAL_THRESHOLDS: Mapping[str, int] = MappingProxyType(
    {"server_errors": 1, "database_refusals": 5, "browser_boundary_rejections": 10}
)
APP_SIGNAL_WINDOW_SECONDS = 5 * 60
APP_LIVENESS_WINDOW_SECONDS = 15 * 60
#: Lines written up to this far after `now` are accepted as clock skew between the container and
#: the host; a line beyond it is left for the run whose clock has reached it.
APP_CLOCK_SKEW_SECONDS = 60
#: How far behind the newest counted line each run re-reads, for lines that arrive out of order.
APP_SIGNAL_OVERLAP_SECONDS = 15 * 60
APP_SIGNAL_CURSOR_SCHEMA = "nha-trang-laundry.app-signal-cursor.v2"
#: Round 9's position: an instant and the byte-hashes of the lines at it. Still read, so a till
#: upgraded mid-week neither starts over nor counts the lines at its saved instant twice.
APP_SIGNAL_CURSOR_SCHEMA_V1 = "nha-trang-laundry.app-signal-cursor.v1"
#: `/readyz` answering 503 is the console check's finding (`console_reachable`, quiet at night).
_READINESS_ROUTE = "/readyz"


@dataclass(frozen=True)
class AppSignalCursor:
    """Where the last run stopped.

    `through` is the newest instant counted. Every line at or before `settled_through` is final:
    counted or, if it arrives now, too late to be. A line after it is counted unless its identity
    is in `recent` -- the identities (with their instants) of the signal lines already counted
    after `settled_through`. A v1 position (round 9) has no `recent`; its `seen_at_through` are the
    byte-hashes of the lines at `through`, and everything before `through` is settled.
    """

    through: datetime
    seen_at_through: frozenset[str] = frozenset()
    recent: tuple[tuple[str, datetime], ...] = ()
    settled_through: datetime | None = None

    def settled(self) -> datetime:
        if self.settled_through is not None:
            return self.settled_through
        # v1: lines at `through` not among `seen_at_through` are still to count.
        return self.through - timedelta(microseconds=1)

    def known(self) -> frozenset[str]:
        return self.seen_at_through | frozenset(key for key, _ in self.recent)


@dataclass(frozen=True)
class ApplicationSignalCounts:
    server_errors: int
    #: The most refusals in any five minutes ending at a line this run counted.
    database_refusals: int
    browser_boundary_rejections: int
    api_lines_in_liveness_window: int
    unreadable_lines: int
    counted_after: datetime
    cursor: AppSignalCursor
    cursor_was_ahead: bool = False


@dataclass(frozen=True)
class _ApiEvent:
    event: str
    fields: dict[str, object]
    occurred_at: datetime
    correlation_id: str
    #: What the line is: event, correlation id and instant. The same event read twice -- from a
    #: rotated file and the live one, or re-serialised by another transport -- has the same key.
    key: str
    #: The line's own bytes, hashed: how round 9 identified a line, kept to read a v1 position.
    text_key: str


class _Unreadable:
    """A line that should have been an event and could not be read as one."""


_UNREADABLE = _Unreadable()


def _api_event(line: str) -> _ApiEvent | _Unreadable | None:
    """The API event on this line; `None` for a blank line or another component's event."""

    text = line.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return _UNREADABLE
    if not isinstance(parsed, dict) or not isinstance(parsed.get("event"), str):
        return _UNREADABLE
    if parsed.get("component") != "api":
        return None
    try:
        occurred = datetime.fromisoformat(str(parsed.get("occurred_at")))
    except ValueError:
        return _UNREADABLE
    if occurred.tzinfo is None:
        return _UNREADABLE
    fields = parsed.get("fields")
    correlation = parsed.get("correlation_id")
    correlation_id = correlation if isinstance(correlation, str) else ""
    text_key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    # Every API line carries the correlation id of the request it belongs to, and no request writes
    # the same event twice at the same microsecond. A line without one falls back to its bytes.
    identity = (
        f"{parsed['event']}|{correlation_id}|{occurred.astimezone(UTC).isoformat()}"
        if correlation_id
        else f"bytes|{text_key}"
    )
    return _ApiEvent(
        event=parsed["event"],
        fields=fields if isinstance(fields, dict) else {},
        occurred_at=occurred,
        correlation_id=correlation_id,
        key=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
        text_key=text_key,
    )


_SIGNAL_EVENTS = frozenset({"database.request_refused", "auth.browser_boundary"})


def _could_count(event: _ApiEvent) -> bool:
    """Whether a line can move a figure -- only those need remembering across runs."""

    if event.event in _SIGNAL_EVENTS:
        return True
    status_code = event.fields.get("status_code")
    return (
        event.event == "http.request.completed"
        and isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and status_code >= 500
    )


def _is_server_error(event: _ApiEvent, refused: frozenset[str]) -> bool:
    status_code = event.fields.get("status_code")
    if not isinstance(status_code, int) or isinstance(status_code, bool) or status_code < 500:
        return False
    if status_code != 503:
        return True
    if event.correlation_id and event.correlation_id in refused:
        return False
    return event.fields.get("route") != _READINESS_ROUTE


def _most_in_any_window(
    new: list[_ApiEvent], every: list[_ApiEvent], name: str, window: timedelta
) -> int:
    """The most `name` events in any `window` that holds one of the `new` ones.

    Windows ending at a new line reach back before the saved position, so a burst split by a run
    boundary is one burst. Windows ending *after* a new line matter for a line that arrived late:
    it belongs to the burst that followed it, which was counted a run ago (L4).
    """

    instants = sorted(event.occurred_at for event in every if event.event == name)
    fresh = sorted(event.occurred_at for event in new if event.event == name)
    if not fresh:
        return 0
    # Each instant is tried once as a window's end -- when a new line lies inside the window ending
    # there -- rather than once per new line that reaches it. Trying it per new line visited every
    # instant of a burst once for each line of the burst: 16 000 lines took 85 s, and a burst big
    # enough to pass the relay's timeout was re-read and timed out on every run after (round-9b
    # verifier, round 3). Now it is O(n log n) and gives the same figure.
    most = 0
    for end in sorted(set(instants)):
        reaches = bisect.bisect_left(fresh, end - window)
        if reaches == len(fresh) or fresh[reaches] > end:
            continue  # no new line in [end - window, end]: not a window this run counts
        inside = bisect.bisect_right(instants, end) - bisect.bisect_left(instants, end - window)
        most = max(most, inside)
    return most


def count_application_signals(
    lines: Iterable[str], *, now: datetime, cursor: AppSignalCursor | None = None
) -> ApplicationSignalCounts:
    """Count the signals in the lines after `cursor` (or the last window, on a first run).

    Pure: the caller supplies `now` and the saved position, and stores the one this returns.
    """

    window = timedelta(seconds=APP_SIGNAL_WINDOW_SECONDS)
    overlap = timedelta(seconds=APP_SIGNAL_OVERLAP_SECONDS)
    latest = now + timedelta(seconds=APP_CLOCK_SKEW_SECONDS)
    liveness_start = now - timedelta(seconds=APP_LIVENESS_WINDOW_SECONDS)
    ahead = cursor is not None and cursor.through > latest
    if cursor is None or ahead:
        # A first run counts the last window, closed at its start (a v1-shaped position).
        cursor = AppSignalCursor(through=now - window)
    settled = cursor.settled()
    known = cursor.known()

    events: list[_ApiEvent] = []
    unreadable = 0
    for line in lines:
        event = _api_event(line)
        if isinstance(event, _Unreadable):
            unreadable += 1
        elif event is not None and event.occurred_at <= latest:
            events.append(event)

    # One event read twice in this run (a rotated file and the live one) is still one event.
    unique: dict[str, _ApiEvent] = {}
    for event in events:
        unique.setdefault(event.key, event)
    events = list(unique.values())

    new = [
        event
        for event in events
        if event.occurred_at > settled and event.key not in known and event.text_key not in known
    ]
    refused = frozenset(
        event.correlation_id for event in events if event.event == "database.request_refused"
    )
    server_errors = sum(
        1
        for event in new
        if event.event == "http.request.completed" and _is_server_error(event, refused)
    )
    alive = sum(1 for event in events if event.occurred_at >= liveness_start)

    through = max([cursor.through, *(event.occurred_at for event in new)])
    settled_now = max(settled, through - overlap)
    remembered = {key: instant for key, instant in cursor.recent}
    remembered.update(
        (key, cursor.through) for key in cursor.seen_at_through
    )  # a v1 position's lines are all at its instant
    remembered.update((event.key, event.occurred_at) for event in new if _could_count(event))
    moved = AppSignalCursor(
        through=through,
        recent=tuple(
            sorted(
                ((key, instant) for key, instant in remembered.items() if instant > settled_now),
                key=lambda item: (item[1], item[0]),
            )
        ),
        settled_through=settled_now,
    )
    return ApplicationSignalCounts(
        server_errors=server_errors,
        database_refusals=_most_in_any_window(new, events, "database.request_refused", window),
        browser_boundary_rejections=_most_in_any_window(
            new, events, "auth.browser_boundary", window
        ),
        api_lines_in_liveness_window=alive,
        unreadable_lines=unreadable,
        counted_after=cursor.through,
        cursor=moved,
        cursor_was_ahead=ahead,
    )


def _application_result(counts: ApplicationSignalCounts) -> CheckResult:
    observed = {
        "server_errors": counts.server_errors,
        "database_refusals": counts.database_refusals,
        "browser_boundary_rejections": counts.browser_boundary_rejections,
    }
    fields: dict[str, object] = {
        **observed,
        "api_lines": counts.api_lines_in_liveness_window,
        "window_s": APP_SIGNAL_WINDOW_SECONDS,
        "counted_after": counts.counted_after.isoformat(),
    }
    if counts.cursor_was_ahead:
        fields["cursor_was_ahead"] = True
    if counts.api_lines_in_liveness_window == 0:
        return CheckResult(
            "application_signals",
            passed=False,
            detail=(
                f"no API log line in the last {APP_LIVENESS_WINDOW_SECONDS // 60} minutes: the "
                "log stream is not reaching this check, so nothing about the application is known"
            ),
            fields=fields,
        )
    over = [
        f"{name} {observed[name]} (alert at {threshold})"
        for name, threshold in APP_SIGNAL_THRESHOLDS.items()
        if observed[name] >= threshold
    ]
    summary = "; ".join(over) or ", ".join(f"{name} {count}" for name, count in observed.items())
    since = counts.counted_after.astimezone(SHOP_TIMEZONE).strftime("%d/%m %H:%M:%S")
    note = " (the saved position was ahead of this clock)" if counts.cursor_was_ahead else ""
    return CheckResult(
        "application_signals",
        passed=not over,
        detail=(
            f"since {since}{note}, rates per {APP_SIGNAL_WINDOW_SECONDS // 60} minutes: {summary}"
        ),
        fields=fields,
    )


def check_application_signals(
    lines: Iterable[str], *, now: datetime, cursor: AppSignalCursor | None = None
) -> CheckResult:
    return _application_result(count_application_signals(lines, now=now, cursor=cursor))


def load_app_signal_cursor(path: str) -> AppSignalCursor | None:
    """The saved position; `None` when there is none yet (the first run reads the last window).

    Anything else unreadable raises: starting over silently would skip whatever happened since.
    """

    file = _Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    recovery = (
        f"the saved read position {path} is unreadable; delete it and the next run counts the "
        f"last {APP_SIGNAL_WINDOW_SECONDS // 60} minutes (anything older has not been checked)"
    )
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict) or parsed.get("schema") not in (
            APP_SIGNAL_CURSOR_SCHEMA,
            APP_SIGNAL_CURSOR_SCHEMA_V1,
        ):
            raise ValueError("not a cursor document")
        through = _aware(parsed["through"])
        seen = parsed.get("seen_at_through", [])
        if not isinstance(seen, list) or not all(isinstance(key, str) for key in seen):
            raise ValueError("seen_at_through is not a list of strings")
        if parsed["schema"] == APP_SIGNAL_CURSOR_SCHEMA_V1:
            return AppSignalCursor(through=through, seen_at_through=frozenset(seen))
        recent = parsed.get("recent", [])
        if not isinstance(recent, list) or not all(
            isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)
            for item in recent
        ):
            raise ValueError("recent is not a list of [key, instant] pairs")
        return AppSignalCursor(
            through=through,
            seen_at_through=frozenset(seen),
            recent=tuple((str(key), _aware(instant)) for key, instant in recent),
            settled_through=(
                None if parsed.get("settled_through") is None else _aware(parsed["settled_through"])
            ),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError(recovery) from error


def _aware(value: object) -> datetime:
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        raise ValueError("no time zone")
    return moment


def save_app_signal_cursor(path: str, cursor: AppSignalCursor) -> None:
    """Replace the saved position in one step, so a run killed mid-write leaves the old one."""

    file = _Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {
        "schema": APP_SIGNAL_CURSOR_SCHEMA,
        "through": cursor.through.isoformat(),
        "seen_at_through": sorted(cursor.seen_at_through),
        "recent": [[key, instant.isoformat()] for key, instant in cursor.recent],
    }
    if cursor.settled_through is not None:
        document["settled_through"] = cursor.settled_through.isoformat()
    temporary = file.with_name(f".{file.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, file)


def run_application_check(
    source: str, *, compose_file: str, cursor_path: str, now: datetime
) -> CheckResult:
    """Read from the saved position, count, and save the new one -- only once the count is done.

    A failed read raises before anything is saved, so the next run reads the same lines again.
    """

    cursor = load_app_signal_cursor(cursor_path)
    latest = now + timedelta(seconds=APP_CLOCK_SKEW_SECONDS)
    # From the settled instant, not the newest: the overlap behind it is re-read for late lines.
    start = (
        cursor.settled()
        if cursor is not None and cursor.through <= latest
        else now - timedelta(seconds=APP_SIGNAL_WINDOW_SECONDS)
    )
    since = min(
        now - timedelta(seconds=APP_LIVENESS_WINDOW_SECONDS),
        start - timedelta(seconds=APP_SIGNAL_WINDOW_SECONDS),
    )
    lines = read_application_log(source, compose_file=compose_file, since=since)
    counts = count_application_signals(lines, now=now, cursor=cursor)
    save_app_signal_cursor(cursor_path, counts.cursor)
    return _application_result(counts)


def read_application_log(source: str, *, compose_file: str, since: datetime) -> list[str]:
    """The API's log lines from `since` on.

    A file path is the API's own log file on the host (`STRUCTURED_LOG_FILE`, bind-mounted from
    `R1_API_LOG_DIRECTORY`), read with its rotated siblings; that is where the shop keeps fourteen
    days, and it outlives the container (`PLATFORM-RESIDUAL-009B` L4). `compose` reads the `api`
    service through `docker compose logs`: Docker's json-file log, which is deleted with the
    container on every recreation, so it is a live view and not the record. The count decides
    which lines are new either way.
    """

    if source == "compose":
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                compose_file,
                "logs",
                "--no-color",
                "--no-log-prefix",
                "--since",
                # Whole seconds, rounded down: a read that starts a fraction early only re-reads
                # lines the count already knows; one that starts late loses them.
                since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "api",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"could not read the api log: {(result.stderr or result.stdout).strip()[:120]}"
            )
        return result.stdout.splitlines()
    return _read_log_files(_Path(source), since=since)


def _read_log_files(path: _Path, *, since: datetime) -> list[str]:
    """The live file and the rotated ones (`api.jsonl.1` ... ) that can hold a line from `since`.

    The API's `RotatingFileHandler` renames `api.jsonl` to `.1`, `.1` to `.2`, and so on, so a line
    written just before a rotation is in `.1` when the next run reads. A rotated file last written
    before `since` holds nothing newer and is skipped; oldest first, so lines come out in the order
    they were written. A line in two files (a rotation between two reads) is one event to the count.
    """

    rotated: list[tuple[int, _Path]] = []
    for sibling in path.parent.glob(f"{path.name}.*"):
        suffix = sibling.name[len(path.name) + 1 :]
        if suffix.isdigit():
            rotated.append((int(suffix), sibling))
    floor = since.timestamp()
    lines: list[str] = []
    for _, sibling in sorted(rotated, reverse=True):
        try:
            if sibling.stat().st_mtime < floor:
                continue
            lines.extend(sibling.read_text(encoding="utf-8", errors="replace").splitlines())
        except FileNotFoundError:
            continue  # rotated away between the listing and the read; the next run has it
    lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="append",
        choices=[
            "wal",
            "base",
            "volume",
            "outbox",
            "flags",
            "console",
            "app",
            "certificate",
            "all",
        ],
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
        "--console-certificate",
        default=os.environ.get("R1_CONSOLE_CERTIFICATE_FILE"),
        help=(
            "the console's own certificate (.shop/secrets/tls_certificate): `--check certificate` "
            "says from 60 days out that it needs renewing (DEC-052)"
        ),
    )
    parser.add_argument(
        "--certificate-notice-state",
        default=os.environ.get("R1_CERTIFICATE_NOTICE_STATE"),
        help=(
            "the file where `--check certificate` records the shop day it last told the owner, so "
            "an hourly agent tells once a day, from 07:00 (round 9b); without it, every run tells"
        ),
    )
    parser.add_argument(
        "--recovery-mode",
        choices=("self-managed", "provider-managed"),
        default=os.environ.get("R1_RECOVERY_MODE"),
        help="who owns point-in-time recovery; undeclared makes the WAL check refuse to guess",
    )
    parser.add_argument(
        "--app-logs",
        default=os.environ.get("R1_APP_LOGS"),
        help=(
            "where the API's structured log is: the API's own file on the host (with its rotated "
            "siblings), e.g. .shop/logs/api/api.jsonl -- the record that outlives the container; "
            "`compose` reads `docker compose -f <--compose-file> logs api`, a live view only"
        ),
    )
    parser.add_argument(
        "--app-signal-cursor",
        default=os.environ.get("R1_APP_SIGNAL_CURSOR"),
        help=(
            "the file where `--check app` keeps the instant of the last API line it counted, so "
            "the next run counts from there and no line falls between two runs"
        ),
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
        ("outbox", "outbox_rows", arguments.database_url, "--database-url"),
        ("volume", "database_volume", arguments.volume_path, "--volume-path"),
        ("base", "base_backup_age", arguments.base_backup_marker, "--base-backup-marker"),
        ("flags", "capability_flags", arguments.compose_file, "--compose-file"),
        ("console", "console_reachable", arguments.console_url, "--console-url"),
        (
            "certificate",
            "console_certificate",
            arguments.console_certificate,
            "--console-certificate",
        ),
        # Both, or the check cannot run: without a saved position a run can only look at a window
        # ending at its own start, and a line written between two such windows is counted by
        # neither (round-9 verifier).
        (
            "app",
            "application_signals",
            arguments.app_logs and arguments.app_signal_cursor,
            "--app-signal-cursor" if arguments.app_logs else "--app-logs",
        ),
    ]

    runners = {
        "wal": lambda: check_wal_archive_gap(
            str(arguments.database_url), recovery_mode=arguments.recovery_mode
        ),
        "outbox": lambda: check_outbox_rows(str(arguments.database_url)),
        "volume": lambda: check_database_volume(str(arguments.volume_path)),
        "base": lambda: check_base_backup_age(str(arguments.base_backup_marker)),
        "flags": lambda: check_capability_flags(str(arguments.compose_file)),
        "console": lambda: check_console_reachable(
            str(arguments.console_url), ca_file=arguments.console_ca_file
        ),
        "certificate": lambda: run_certificate_check(
            str(arguments.console_certificate),
            notice_state=arguments.certificate_notice_state,
            now=datetime.now(UTC),
        ),
        "app": lambda: run_application_check(
            str(arguments.app_logs),
            compose_file=arguments.compose_file,
            cursor_path=str(arguments.app_signal_cursor),
            now=datetime.now(UTC),
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
            "--console-url, --console-certificate, --app-logs with --app-signal-cursor. A check "
            "that silently does not "
            "run is worse than one that fails."
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
                outcome=(
                    "failure" if not result.passed else "warning" if result.warning else "success"
                ),
                correlation=correlation,
                severity=(
                    EventSeverity.ERROR
                    if not result.passed
                    else EventSeverity.WARNING
                    if result.warning
                    else EventSeverity.INFO
                ),
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
            mark = "!!!" if not result.passed else "WARN" if result.warning else "OK "
            print(f"  {mark} {result.name}: {result.detail}", file=human)

    failures = [result for result in results if not result.passed]
    moment = datetime.now(UTC)
    if arguments.emit_alert:
        # The relay on the host delivers. Nothing here opens a socket, which is the point: on the
        # self-managed branch this runs on `database-private`, and that network has no way out.
        print(json.dumps(alert_document(results, now=moment), ensure_ascii=False), flush=True)
    else:
        # Also when this run passed: an alert an earlier run could not send is sent now (L4).
        deliver_alert(failures, now=moment)

    return 0 if not failures else 1


#: `DEC-025`'s hours, read from `shop_alert_pending` -- one copy, because the relay applies them
#: too, to an alert it resends (round-9b verifier: a 20:50 console alert whose send failed was
#: resent at 02:00). The reasoning is written there.
QUIET_HOURS_CHECKS = pending_alerts.QUIET_HOURS_CHECKS
QUIET_HOURS_START = pending_alerts.QUIET_HOURS_START
QUIET_HOURS_END = pending_alerts.QUIET_HOURS_END

#: The shop is in Nha Trang and the decision is written in the shop's hours. Comparing
#: `datetime.now(UTC).hour` against them inverted the window exactly: at UTC+7 the script alerted
#: from 14:00 to 04:00 local and stayed silent from 04:00 to 14:00, so the case
#: `production-deploy-day.md` names by name -- "one down at 07:45 needs everybody" -- was 00:45 UTC
#: and suppressed, while a 22:00 outage nobody needed to see woke the owner. The test encoded the
#: same UTC hours and called 03:00 UTC "night", so it passed while pinning the bug.
SHOP_TIMEZONE = pending_alerts.SHOP_TIMEZONE


#: What `--emit-alert` prints and `scripts/relay_shop_alert.py` reads. Versioned because the two run
#: in different places -- the check in a container image, the relay from the host checkout -- and an
#: image built from an older checkout must be refused rather than half-understood.
ALERT_DOCUMENT_SCHEMA = "nha-trang-laundry.shop-alert.v1"
ALERT_HEADING = "Bảng vận hành — cần xem ngay:"


def _reportable(
    failures: list[CheckResult], *, now: datetime
) -> tuple[list[CheckResult], list[CheckResult]]:
    """Split failures into those that alert now and those held: by `DEC-025` for opening hours,
    or because the owner was told today (`told_today`)."""

    quiet = pending_alerts.in_quiet_hours(now)
    held = [
        failure
        for failure in failures
        if failure.told_today or (quiet and failure.name in QUIET_HOURS_CHECKS)
    ]
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
        "suppressed": [failure.name for failure in held if not failure.told_today],
        # Failing and already told today: the relay logs it and sends nothing.
        "told_today": [failure.name for failure in held if failure.told_today],
        # Passed with something coming due (`DEC-051`): the relay logs these and sends nothing.
        "warnings": {r.name: r.detail for r in results if r.passed and r.warning},
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

    # `PLATFORM-RESIDUAL-009B` L4, as the relay does it: with `R1_ALERT_PENDING_DIRECTORY` set, an
    # alert that could not be sent is kept and sent again by the next run until it gets through.
    current = alert_text(failures, now=now)
    pending_file = (
        pending_alerts.pending_path("direct", {"R1_ALERT_PENDING_DIRECTORY": directory})
        if (directory := os.environ.get("R1_ALERT_PENDING_DIRECTORY", "").strip())
        else None
    )
    state = pending_alerts.PendingState()
    if pending_file is not None:
        state, problem = pending_alerts.load(pending_file, now=now)
        if problem:
            _not_delivered(problem)
    # A kept console alert waits for 07:00 like a new one (`DEC-025`); `compose` leaves it untried.
    # A current alert is deferred only once `prepare` has kept it (round-9b verifier, round 4).
    checks = tuple(failure.name for failure in _reportable(failures, now=now)[0])
    composition, not_deferred = pending_alerts.prepare(
        current, checks, state, pending_file, now=now
    )
    if not_deferred is not None:
        print(
            "check_shop_operations: WARNING this run's alert did not fit beside the oldest kept "
            f"alert and cannot be kept for the next run ({not_deferred}): it goes now, first and "
            "whole, beside it",
            file=_sys.stderr,
            flush=True,
        )
    text = composition.text
    if text is None:
        return False

    def _kept() -> None:
        if pending_file is not None:
            problem = pending_alerts.try_save(
                pending_file,
                pending_alerts.after_failure(
                    state,
                    current,
                    checks,
                    now=now,
                    attempted=composition.carried,
                    deferred=composition.deferred,
                ),
            )
            if problem is not None and composition.deferred:
                # `prepare` kept this run's (deferred) alert before the send: it is on disk and
                # goes next run (round-9b L residual: this said "NOT kept" of a kept alert).
                _not_delivered(
                    "this run's alert was kept before the send and goes next run, the earlier "
                    f"alert(s) stay kept; the failed attempt was not recorded: {problem}"
                )
            elif problem is not None:
                _not_delivered(f"NOT kept for a retry: {problem}")

    try:
        token = _Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as error:
        _not_delivered(f"cannot read R1_ALERT_TELEGRAM_TOKEN_FILE ({error.strerror})")
        _kept()
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
        _kept()
        return False
    if not delivered:
        _not_delivered("telegram did not answer 2xx")
        _kept()
    elif pending_file is not None:
        remaining = pending_alerts.after_success(
            state,
            composition.carried,
            composition.reported_dropped,
            deferred=current if composition.deferred else None,
            checks=checks,
            now=now,
        )
        # Unchanged (a clipped kept alert stays kept): nothing to write. A deferred alert is
        # already kept whatever the write says: `prepare` wrote it before the send.
        problem = None if remaining == state else pending_alerts.try_save(pending_file, remaining)
        if problem is not None:
            print(
                f"check_shop_operations: WARNING the kept alerts were not updated ({problem}): "
                "an earlier alert delivered now may be sent again",
                file=_sys.stderr,
                flush=True,
            )
    return delivered


def _not_delivered(reason: str) -> None:
    print(f"check_shop_operations: ALERT NOT DELIVERED: {reason}", file=_sys.stderr, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
