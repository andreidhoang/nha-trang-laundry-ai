"""`SHOP-OBSERVABILITY-001`: the checks that can actually fire, and the ones that cannot.

The value of this file is as much in what it asserts is *absent* as in the thresholds. Five of the
seven paging conditions in `PRODUCTION_OPERATIONS_SPEC_V1` §4.1 have no referent in a deployment
with no channel, no sender, no provider and no agent cell. Shipping them anyway is how an on-call
learns the pager is noise.
"""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

# Imported dynamically the way `test_delivery_control_commit.py` imports its scripts: `scripts/` is
# not a package, and a static import would be a type error rather than a working test.
CHECKS = importlib.import_module("check_shop_operations")


class _Cursor:
    def __init__(self, rows: list[object]) -> None:
        self._rows = list(rows)
        self._last: object = None

    def execute(self, statement: str, *_: object) -> None:
        self._last = "archive_mode" if "archive_mode" in statement else "archiver"

    def fetchone(self) -> object:
        return self._rows.pop(0)

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _Connection:
    def __init__(self, rows: list[object]) -> None:
        self._cursor = _Cursor(rows)

    def cursor(self) -> _Cursor:
        return self._cursor

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _archiver(monkeypatch: pytest.MonkeyPatch, rows: list[object]) -> None:
    monkeypatch.setattr(CHECKS.psycopg, "connect", lambda _url: _Connection(rows))


def test_an_idle_afternoon_is_a_failure_not_a_quiet_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure this check exists for: everything reads healthy and recovery is hours behind.

    A shop with no orders between 14:00 and 18:00 archives nothing in that window unless
    `archive_timeout` forces a segment. Nothing else in the system notices, because nothing else is
    looking at the age of the last archive.
    """

    now = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    _archiver(monkeypatch, [(1_200, now - timedelta(hours=4), 0, None), ["on"]])

    result = CHECKS.check_wal_archive_gap("postgresql://synthetic", now=now)

    assert result.passed is False
    assert "limit 900s" in result.detail
    assert result.fields["backup_age_s"] == 4 * 3600


def test_a_recent_archive_passes_and_a_failing_one_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)

    _archiver(monkeypatch, [(1_200, now - timedelta(seconds=60), 0, None), ["on"]])
    assert CHECKS.check_wal_archive_gap("postgresql://synthetic", now=now).passed is True

    # Archived recently, but the most recent attempt failed: the gap is about to open and the
    # count alone would have said "healthy" for another fourteen minutes.
    _archiver(
        monkeypatch,
        [(1_200, now - timedelta(seconds=60), 3, now - timedelta(seconds=10)), ["on"]],
    )
    failing = CHECKS.check_wal_archive_gap("postgresql://synthetic", now=now)
    assert failing.passed is False
    assert "most recent attempt failed" in failing.detail


def test_archiving_off_is_a_pass_only_on_the_branch_that_was_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`archive_mode = off` means two opposite things and the check cannot tell them apart itself.

    On a provider-managed database PITR is the provider's and archiving off is correct. On the
    self-managed branch it means nothing is being archived at all -- the exact failure this check
    exists for. The first version returned PASS for both and said "the operator knows which they
    are running", which was true of the operator and false of the check; the earlier version of
    this test asserted that blind pass and was cited as evidence the check worked.

    Undeclared is neither, and neither means stop.
    """

    _archiver(monkeypatch, [(0, None, 0, None), ["off"]])
    managed = CHECKS.check_wal_archive_gap(
        "postgresql://synthetic", recovery_mode="provider-managed"
    )
    assert managed.passed is True
    assert "provider" in managed.detail

    _archiver(monkeypatch, [(0, None, 0, None), ["off"]])
    self_managed = CHECKS.check_wal_archive_gap(
        "postgresql://synthetic", recovery_mode="self-managed"
    )
    assert self_managed.passed is False
    assert "no WAL is being archived" in self_managed.detail

    _archiver(monkeypatch, [(0, None, 0, None), ["off"]])
    undeclared = CHECKS.check_wal_archive_gap("postgresql://synthetic")
    assert undeclared.passed is False
    assert "undeclared" in undeclared.detail


def test_archiving_on_with_nothing_ever_archived_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _archiver(monkeypatch, [(0, None, 0, None), ["on"]])
    result = CHECKS.check_wal_archive_gap("postgresql://synthetic")
    assert result.passed is False
    assert "nothing has ever been archived" in result.detail


def test_the_volume_check_fires_before_postgres_refuses_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A full volume is how a correctly configured archiver kills the shop.

    Ten percent, not one: at one percent there is no time to act, and a counter that cannot take an
    order is the shop being closed.
    """

    class _Usage:
        def __init__(self, free_fraction: float) -> None:
            self.total = 100 * 1024**3
            self.free = int(self.total * free_fraction)
            self.used = self.total - self.free

    monkeypatch.setattr(CHECKS.shutil, "disk_usage", lambda _p: _Usage(0.05))
    assert CHECKS.check_database_volume(str(tmp_path)).passed is False

    monkeypatch.setattr(CHECKS.shutil, "disk_usage", lambda _p: _Usage(0.40))
    assert CHECKS.check_database_volume(str(tmp_path)).passed is True


def test_every_flag_the_deployment_must_never_enable_is_covered() -> None:
    """Including the two worker flags, which `x-disabled-capabilities` does not carry."""

    assert set(CHECKS.CAPABILITY_FLAGS) == {
        "FEATURE_PUBLIC_CHANNELS_ENABLED",
        "FEATURE_AUTOMATED_SENDS_ENABLED",
        "FEATURE_AGENT_RUNTIME_ENABLED",
        "WORKER_INTERNAL_OUTBOX_ENABLED",
        "WORKER_AGENT_QUEUE_ENABLED",
    }


def test_the_thresholds_match_the_published_backup_policy() -> None:
    """A threshold that drifts from the policy it enforces is worse than no threshold."""

    import json

    policy = json.loads((ROOT / "deploy/backup/backup-policy-v1.json").read_text("utf-8"))
    assert policy["target_rpo_seconds"] == CHECKS.MAX_ARCHIVE_GAP_SECONDS
    # The policy also bounds the archive interval at 300s, tighter than the recovery point. The
    # check sits at the recovery point rather than the interval on purpose: one late segment is not
    # yet a lost guarantee, and paging on it would be paging on ordinary variance.
    assert policy["wal_archive_max_interval_seconds"] < CHECKS.MAX_ARCHIVE_GAP_SECONDS


def test_an_unconfigured_alert_channel_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DEC-025` keeps the host scheduler's mail as the floor, so silence must stay honest.

    A deployment that has not set the token still surfaces failures through the exit code. What it
    must never do is report that an alert went out when none did.
    """

    monkeypatch.delenv("R1_ALERT_TELEGRAM_TOKEN_FILE", raising=False)
    monkeypatch.delenv("R1_ALERT_TELEGRAM_CHAT_ID", raising=False)

    sent = CHECKS.deliver_alert(
        [_failure("database_volume")], now=datetime(2026, 9, 3, 10, tzinfo=UTC)
    )

    assert sent is False


def test_a_silent_loss_alerts_at_any_hour_and_an_outage_does_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The distinction `DEC-025` drew, and the reason it is not "always" or "office hours".

    A console down at 03:00 that recovers before the shop opens needs nobody woken. A stale archive
    or a filling disk at 03:00 is a guarantee quietly going away, and nothing else will mention it.
    """

    token = tmp_path / "token"
    token.write_text("probe-token", encoding="utf-8")
    monkeypatch.setenv("R1_ALERT_TELEGRAM_TOKEN_FILE", str(token))
    monkeypatch.setenv("R1_ALERT_TELEGRAM_CHAT_ID", "1234")

    posted: list[str] = []

    def _capture(request: object, timeout: float = 0) -> object:
        posted.append(getattr(request, "data", b"").decode())

        class _Response:
            status = 200

            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *_: object) -> None:
                return None

        return _Response()

    monkeypatch.setattr(CHECKS.urllib.request, "urlopen", _capture)

    # Instants are given in UTC because that is what the caller passes, and named by the shop's
    # clock because that is what the decision is written in. The previous version of this test
    # called 03:00 UTC "night" -- it is 10:00 in Nha Trang, mid-morning -- so it passed while
    # pinning an alerting window that was inverted by seven hours.
    night = datetime(2026, 9, 2, 20, tzinfo=UTC)  # 03:00 local, quiet
    assert CHECKS.deliver_alert([_failure("console_reachable")], now=night) is False
    assert posted == []

    # A guarantee going away wakes somebody whatever the hour.
    assert CHECKS.deliver_alert([_failure("wal_archive_gap")], now=night) is True
    assert len(posted) == 1

    # The case `production-deploy-day.md` names by name: "một máy hỏng lúc 07:45 thì cần mọi
    # người". 07:45 local is 00:45 UTC, and under the UTC comparison it was suppressed.
    opening = datetime(2026, 9, 3, 0, 45, tzinfo=UTC)  # 07:45 local, the shop is opening
    assert CHECKS.deliver_alert([_failure("console_reachable")], now=opening) is True
    assert len(posted) == 2

    # And the other edge: 22:00 local is 15:00 UTC, which the UTC comparison called working hours.
    late = datetime(2026, 9, 3, 15, tzinfo=UTC)  # 22:00 local, closed
    assert CHECKS.deliver_alert([_failure("console_reachable")], now=late) is False
    assert len(posted) == 2

    day = datetime(2026, 9, 3, 3, tzinfo=UTC)  # 10:00 local, open
    assert CHECKS.deliver_alert([_failure("console_reachable")], now=day) is True
    assert len(posted) == 3


def test_a_failed_alert_does_not_mask_the_failure_it_was_carrying(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = tmp_path / "token"
    token.write_text("probe-token", encoding="utf-8")
    monkeypatch.setenv("R1_ALERT_TELEGRAM_TOKEN_FILE", str(token))
    monkeypatch.setenv("R1_ALERT_TELEGRAM_CHAT_ID", "1234")

    def _explode(*_: object, **__: object) -> object:
        raise OSError("no route to host")

    monkeypatch.setattr(CHECKS.urllib.request, "urlopen", _explode)

    # Returns False rather than raising: the check's own verdict and exit code stand on their own,
    # and a broken alerting path must not become a broken check.
    assert (
        CHECKS.deliver_alert(
            [_failure("database_volume")], now=datetime(2026, 9, 3, 10, tzinfo=UTC)
        )
        is False
    )


def test_alerting_never_touches_the_send_machinery() -> None:
    """It is a monitoring notification, not an automated send, and the boundary is load-bearing.

    Going through the outbox would make this an outbound customer-messaging path in everything but
    intent, and `FEATURE_AUTOMATED_SENDS_ENABLED` would stop meaning what it says.
    """

    source = (ROOT / "scripts/check_shop_operations.py").read_text("utf-8")
    imports = [
        line
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) or line.lstrip().startswith(("import ", "from "))
    ]
    # Asserted over the import lines rather than the whole file: the module docstring names the
    # outbox and suppression precisely to explain that neither has a referent here, and a naive
    # substring search over prose flags its own explanation.
    joined = "\n".join(imports)
    for forbidden in ("outbox", "channel", "consent", "manual_send", "OutboxEvent"):
        assert forbidden not in joined, f"{forbidden} reached the alerting path"
    assert "urllib.request" in joined, "the alert posts directly, so this import is the mechanism"


def _failure(name: str) -> object:
    return CHECKS.CheckResult(name=name, passed=False, detail="synthetic", fields={})


def test_a_check_that_was_asked_for_and_cannot_run_refuses_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 0 with the question unanswered is the worst outcome available.

    `--check wal` with no `--database-url` used to skip silently and, if any other check passed,
    exit 0. Cron mail reads that as success, so the operator believes an answer they never got.
    """

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        CHECKS._sys, "argv", ["check_shop_operations.py", "--check", "wal", "--json"]
    )
    with pytest.raises(SystemExit) as raised:
        CHECKS.main()
    assert "wal needs --database-url" in str(raised.value)


def test_one_broken_check_does_not_discard_the_others_or_the_alert(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`docker` missing from cron's PATH used to abort before any event or alert.

    That is the condition most likely to coincide with a real outage, so it was the one that
    produced no alert. A check that raises is now a failing check, which is what it is.
    """

    def _explode(_compose: str) -> object:
        raise FileNotFoundError("[Errno 2] No such file or directory: 'docker'")

    monkeypatch.setattr(CHECKS, "check_capability_flags", _explode)
    monkeypatch.setattr(
        CHECKS,
        "check_database_volume",
        lambda _path: CHECKS.CheckResult("database_volume", passed=True, detail="fine", fields={}),
    )
    delivered: list[list[Any]] = []

    def _record(failures: list[Any], *, now: object) -> bool:
        delivered.append(failures)
        return True

    monkeypatch.setattr(CHECKS, "deliver_alert", _record)
    monkeypatch.setattr(
        CHECKS._sys,
        "argv",
        ["check_shop_operations.py", "--volume-path", str(tmp_path), "--json"],
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("R1_CONSOLE_HEALTH_URL", raising=False)

    assert CHECKS.main() == 1
    assert delivered, "a check raising must not stop the alert from going out"
    assert [failure.name for failure in delivered[0]] == ["capability_flags"]
    assert "FileNotFoundError" in delivered[0][0].detail


def test_a_wal_chain_with_no_base_backup_is_reported(tmp_path: Path) -> None:
    """There was a WAL archive check and no base-backup check at all.

    A WAL chain restores nothing without a base backup to apply it to, so the shop could hold a
    green tick on the only backup signal it watched while holding nothing it could restore from.
    The marker is written by `base-backup.sh` after the artifact is verified and uploaded, so its
    age is the age of a backup that exists rather than of an attempt.
    """

    marker = tmp_path / "last-success"

    never = CHECKS.check_base_backup_age(str(marker))
    assert never.passed is False
    assert "has ever completed" in never.detail

    marker.write_text("base-20260906T100000Z 3001743", encoding="utf-8")
    fresh = CHECKS.check_base_backup_age(str(marker))
    assert fresh.passed is True

    stale = CHECKS.check_base_backup_age(str(marker), now=datetime.now(UTC) + timedelta(hours=27))
    assert stale.passed is False
    assert stale.fields["base_backup_age_s"] > CHECKS.MAX_BASE_BACKUP_AGE_SECONDS
