"""`OPS-OBSERVABILITY-009` (review P2): the application's own failures reach somebody.

Before this, every check in `scripts/check_shop_operations.py` looked at infrastructure -- the WAL
archive, the disk, the flags, whether the console answers -- and none at what the application was
doing. Payments could return 500 all afternoon and the only record was a log line nobody read,
kept on a rotation that dropped it within days.

The check reads the API's structured log lines (the same stream the shop deployment keeps in
Docker's json-file log) and counts three things over a window: 5xx answers,
`database.request_refused` and `auth.browser_boundary`. Every test here feeds synthetic lines in
exactly the shape `SafeStructuredLogger` writes, and the API-side test
(`apps/api/tests/test_application_signal_logging.py`) feeds lines the real API wrote, so the parser
and the writer are held to one format.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

CHECKS = importlib.import_module("check_shop_operations")

NOW = datetime(2026, 9, 30, 4, 0, tzinfo=UTC)  # 11:00 in Nha Trang, the shop is open


def _line(
    event: str,
    *,
    at: datetime,
    component: str = "api",
    fields: dict[str, Any] | None = None,
    outcome: str = "completed",
) -> str:
    """One line exactly as `StructuredEvent.serialize` writes it."""

    return json.dumps(
        {
            "schema_version": 1,
            "occurred_at": at.isoformat(),
            "severity": "INFO",
            "component": component,
            "event": event,
            "outcome": outcome,
            "correlation_id": "00000000-0000-4000-8000-000000000001",
            "trace_id": "0" * 32,
            "fields": fields or {},
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _request(status_code: int, *, at: datetime, route: str = "/internal/v1/x") -> str:
    return _line(
        "http.request.completed",
        at=at,
        fields={"method": "POST", "route": route, "status_code": status_code},
    )


def _refused(*, at: datetime) -> str:
    return _line(
        "database.request_refused",
        at=at,
        outcome="failed",
        fields={"reason_code": "DATABASE_BUSY"},
    )


def _boundary(*, at: datetime) -> str:
    return _line(
        "auth.browser_boundary",
        at=at,
        outcome="rejected",
        fields={"reason_code": "CSRF_TOKEN_MISMATCH"},
    )


def _ago(seconds: int) -> datetime:
    return NOW - timedelta(seconds=seconds)


def _heartbeat() -> list[str]:
    """What an idle, healthy shop writes: the console check's `/readyz` every five minutes."""

    return [_request(200, at=_ago(seconds), route="/readyz") for seconds in (30, 330, 630)]


# --- counting -----------------------------------------------------------------------------------


def test_counts_each_signal_inside_the_window_and_nothing_outside_it() -> None:
    window = CHECKS.APP_SIGNAL_WINDOW_SECONDS
    lines = [
        *_heartbeat(),
        _request(500, at=_ago(10)),
        _request(502, at=_ago(20)),
        # A 503 is the designed database refusal; it is counted once, as a refusal, never twice.
        _request(503, at=_ago(25)),
        _refused(at=_ago(25)),
        _boundary(at=_ago(40)),
        _boundary(at=_ago(41)),
        # 4xx is a refusal the application meant to give; it is not a failure signal.
        _request(409, at=_ago(50)),
        _request(422, at=_ago(51)),
        # Just outside the window: an old incident must not alert again every five minutes.
        _request(500, at=_ago(window + 1)),
        _refused(at=_ago(window + 1)),
        _boundary(at=_ago(window + 1)),
        # Exactly on the window's edge counts (the window is closed at its start).
        _request(500, at=_ago(window)),
    ]
    counts = CHECKS.count_application_signals(lines, now=NOW)
    assert counts.server_errors == 3
    assert counts.database_refusals == 1
    assert counts.browser_boundary_rejections == 2
    assert counts.api_lines_in_liveness_window == len(lines)


def test_lines_that_are_not_api_events_are_ignored_rather_than_fatal() -> None:
    lines = [
        *_heartbeat(),
        "",
        "INFO:     Started server process [1]",
        "Traceback (most recent call last):",
        '{"not": "an event"}',
        "[1, 2, 3]",
        '{"event": "http.request.completed", "occurred_at": "not a time", "component": "api"}',
        _request(500, at=_ago(5), route="/x").replace('"api"', '"worker"'),
        _line("operations.wal_archive_gap", at=_ago(5), component="operations"),
        _request(500, at=_ago(5))[:-5],  # a line torn by rotation
    ]
    counts = CHECKS.count_application_signals(lines, now=NOW)
    assert counts.server_errors == 0
    assert counts.database_refusals == 0
    assert counts.browser_boundary_rejections == 0
    assert counts.api_lines_in_liveness_window == 3
    assert counts.unreadable_lines >= 5


def test_a_line_from_the_future_is_not_counted() -> None:
    """Clock skew between the container and the host must not let one line alert for ever."""

    counts = CHECKS.count_application_signals(
        [*_heartbeat(), _request(500, at=NOW + timedelta(seconds=90))], now=NOW
    )
    assert counts.server_errors == 0


# --- thresholds: the matrix ---------------------------------------------------------------------

SIGNALS = {
    "server_errors": lambda at: _request(500, at=at),
    "database_refusals": lambda at: _refused(at=at),
    "browser_boundary_rejections": lambda at: _boundary(at=at),
}


@pytest.mark.parametrize("signal", sorted(SIGNALS))
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_each_signal_alerts_exactly_at_its_threshold(signal: str, offset: int) -> None:
    threshold = CHECKS.APP_SIGNAL_THRESHOLDS[signal]
    count = threshold + offset
    lines = [*_heartbeat(), *(SIGNALS[signal](_ago(5 + index)) for index in range(max(count, 0)))]

    result = CHECKS.check_application_signals(lines, now=NOW)

    assert result.name == "application_signals"
    assert result.fields[signal] == max(count, 0)
    assert result.passed is (count < threshold)
    if not result.passed:
        assert signal in result.detail


def test_one_unexpected_server_error_is_enough() -> None:
    """A 500 is an answer whose outcome the counter cannot know ("Đừng thử lại"); somebody must
    reconcile it. A 503 refusal is a known outcome -- nothing was written -- so it takes several."""

    assert CHECKS.APP_SIGNAL_THRESHOLDS["server_errors"] == 1
    assert CHECKS.APP_SIGNAL_THRESHOLDS["database_refusals"] > 1
    assert CHECKS.APP_SIGNAL_THRESHOLDS["browser_boundary_rejections"] > 1


def test_several_signals_are_named_together() -> None:
    lines = [
        *_heartbeat(),
        _request(500, at=_ago(5)),
        *(_refused(at=_ago(6)) for _ in range(CHECKS.APP_SIGNAL_THRESHOLDS["database_refusals"])),
    ]
    result = CHECKS.check_application_signals(lines, now=NOW)
    assert not result.passed
    assert "server_errors" in result.detail and "database_refusals" in result.detail
    assert "browser_boundary_rejections" not in result.detail


def test_a_quiet_healthy_stream_passes() -> None:
    result = CHECKS.check_application_signals(_heartbeat(), now=NOW)
    assert result.passed, result.detail


def test_silence_is_not_health() -> None:
    """No API line at all means the stream is not reaching the check, not that nothing failed.

    The console check asks `/readyz` every five minutes, so a healthy API always has lines here.
    """

    empty = CHECKS.check_application_signals([], now=NOW)
    stale = CHECKS.check_application_signals(
        [_request(500, at=_ago(CHECKS.APP_LIVENESS_WINDOW_SECONDS + 60))], now=NOW
    )
    for result in (empty, stale):
        assert not result.passed
        assert "no API log line" in result.detail


# --- the log source -----------------------------------------------------------------------------


def test_reads_the_api_service_log_the_deployment_keeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docker compose logs` over the json-file driver: the source the shop actually has."""

    calls: list[list[str]] = []

    def _run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, stdout="\n".join([*_heartbeat(), _request(500, at=_ago(5))]), stderr=""
        )

    monkeypatch.setattr(CHECKS.subprocess, "run", _run)
    lines = CHECKS.read_application_log("compose", compose_file="compose.r1.yaml")
    result = CHECKS.check_application_signals(lines, now=NOW)

    assert not result.passed
    (command,) = calls
    assert command[:4] == ["docker", "compose", "-f", "compose.r1.yaml"]
    assert "logs" in command and "api" in command
    assert "--no-log-prefix" in command and "--no-color" in command
    since = command[command.index("--since") + 1]
    assert since == f"{CHECKS.APP_LIVENESS_WINDOW_SECONDS}s"


def test_an_unreadable_log_source_is_a_failure_not_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="no such service: api")

    monkeypatch.setattr(CHECKS.subprocess, "run", _run)
    with pytest.raises(RuntimeError, match="no such service"):
        CHECKS.read_application_log("compose", compose_file="compose.r1.yaml")


def test_reads_a_log_file(tmp_path: Path) -> None:
    log = tmp_path / "api.log"
    log.write_text("\n".join([*_heartbeat(), _refused(at=_ago(3))]) + "\n", encoding="utf-8")
    lines = CHECKS.read_application_log(str(log), compose_file="unused")
    assert CHECKS.count_application_signals(lines, now=NOW).database_refusals == 1


# --- end to end through main() and the existing alert delivery ---------------------------------


def _main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(CHECKS._sys, "argv", ["check_shop_operations.py", *argv])
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("R1_CONSOLE_HEALTH_URL", raising=False)
    monkeypatch.delenv("R1_PGDATA_PATH", raising=False)
    monkeypatch.delenv("R1_BASE_BACKUP_MARKER", raising=False)
    monkeypatch.delenv("R1_APP_LOGS", raising=False)
    result: int = CHECKS.main()
    return result


def test_an_error_burst_reaches_the_alert_document_at_any_hour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Through `--emit-alert`, the path `relay_shop_alert.py` delivers from on the host."""

    real_now = datetime.now(UTC)
    log = tmp_path / "api.log"
    log.write_text(
        "\n".join(
            [
                _request(200, at=real_now - timedelta(seconds=30), route="/readyz"),
                _request(500, at=real_now - timedelta(seconds=20)),
            ]
        ),
        encoding="utf-8",
    )

    assert _main(monkeypatch, ["--check", "app", "--app-logs", str(log), "--emit-alert"]) == 1

    stdout = capsys.readouterr().out.strip().splitlines()
    document = json.loads(stdout[-1])
    assert document["schema"] == CHECKS.ALERT_DOCUMENT_SCHEMA
    assert document["results"]["application_signals"]["passed"] is False
    assert document["alert"] is not None
    assert "application_signals" in document["alert"]["checks"]
    assert "server_errors" in document["alert"]["text"]
    # Not held for opening hours: a failing payment at 22:00 is still a failing payment.
    assert "application_signals" not in CHECKS.QUIET_HOURS_CHECKS
    night = datetime(2026, 9, 2, 20, tzinfo=UTC)  # 03:00 local
    failing = CHECKS.CheckResult("application_signals", passed=False, detail="x", fields={})
    assert CHECKS.alert_document([failing], now=night)["alert"] is not None


def test_a_healthy_stream_exits_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real_now = datetime.now(UTC)
    log = tmp_path / "api.log"
    log.write_text(
        _request(200, at=real_now - timedelta(seconds=30), route="/readyz"), encoding="utf-8"
    )
    assert _main(monkeypatch, ["--check", "app", "--app-logs", str(log), "--json"]) == 0


def test_asked_for_without_a_source_refuses_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as raised:
        _main(monkeypatch, ["--check", "app", "--json"])
    assert "app needs --app-logs" in str(raised.value)


# --- defaults in one place, and the place is documented -----------------------------------------


def test_the_runbook_states_the_same_thresholds_the_script_uses() -> None:
    runbook = (ROOT / "docs/runbooks/shop-pilot.md").read_text("utf-8")
    for signal, threshold in CHECKS.APP_SIGNAL_THRESHOLDS.items():
        assert f"`{signal}` | {threshold} |" in runbook, signal
    assert f"over the last {CHECKS.APP_SIGNAL_WINDOW_SECONDS // 60} minutes" in runbook


def test_the_till_scheduler_runs_the_application_check() -> None:
    install = (ROOT / "deploy/shop-till/install.sh").read_text("utf-8")
    assert "--check app --app-logs compose" in install
