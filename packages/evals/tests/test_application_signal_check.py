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
import itertools
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

#: Every request the API answers has its own correlation id, and every line it writes for that
#: request carries it -- which is how a 503 is matched to the `database.request_refused` it answers.
_CORRELATIONS = itertools.count(1)


def _correlation() -> str:
    return f"00000000-0000-4000-8000-{next(_CORRELATIONS):012d}"


def _line(
    event: str,
    *,
    at: datetime,
    component: str = "api",
    fields: dict[str, Any] | None = None,
    outcome: str = "completed",
    correlation: str | None = None,
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
            "correlation_id": correlation or _correlation(),
            "trace_id": "0" * 32,
            "fields": fields or {},
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _request(
    status_code: int,
    *,
    at: datetime,
    route: str = "/internal/v1/x",
    correlation: str | None = None,
) -> str:
    return _line(
        "http.request.completed",
        at=at,
        fields={"method": "POST", "route": route, "status_code": status_code},
        correlation=correlation,
    )


def _refused(*, at: datetime, correlation: str | None = None) -> str:
    return _line(
        "database.request_refused",
        at=at,
        outcome="failed",
        fields={"reason_code": "DATABASE_BUSY"},
        correlation=correlation,
    )


def _refused_request(*, at: datetime) -> list[str]:
    """What the API writes for a designed database refusal: the refusal, then its 503, one id."""

    correlation = _correlation()
    return [
        _refused(at=at, correlation=correlation),
        _request(503, at=at, correlation=correlation),
    ]


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
        *_refused_request(at=_ago(25)),
        _boundary(at=_ago(40)),
        _boundary(at=_ago(41)),
        # 4xx is a refusal the application meant to give; it is not a failure signal.
        _request(409, at=_ago(50)),
        _request(422, at=_ago(51)),
        # Just outside the window: an old incident must not alert again every five minutes. (The
        # rates are judged over five minutes ending at a *new* line, so the old refusal and
        # rejection sit more than five minutes before the new ones; see the test below for what
        # an old line closer than that does.)
        _request(500, at=_ago(window + 1)),
        _refused(at=_ago(2 * window)),
        _boundary(at=_ago(2 * window)),
        # Exactly on the window's edge counts (the window is closed at its start).
        _request(500, at=_ago(window)),
    ]
    counts = CHECKS.count_application_signals(lines, now=NOW)
    assert counts.server_errors == 3
    assert counts.database_refusals == 1
    assert counts.browser_boundary_rejections == 2
    assert counts.api_lines_in_liveness_window == len(lines)


def test_an_old_line_is_context_for_a_new_one_and_never_a_signal_on_its_own() -> None:
    """A refusal already counted by the last run never alerts again by itself, but it is still
    one of the refusals in the five minutes ending at a new one: the database did refuse both."""

    window = CHECKS.APP_SIGNAL_WINDOW_SECONDS
    old = [_refused(at=_ago(window + 1)), _boundary(at=_ago(window + 1))]
    alone = CHECKS.count_application_signals([*_heartbeat(), *old], now=NOW)
    assert (alone.database_refusals, alone.browser_boundary_rejections) == (0, 0)
    with_new = CHECKS.count_application_signals(
        [*_heartbeat(), *old, _refused(at=_ago(25)), _boundary(at=_ago(40))], now=NOW
    )
    assert (with_new.database_refusals, with_new.browser_boundary_rejections) == (2, 2)


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
    since = datetime(2026, 9, 30, 3, 40, 5, 900, tzinfo=UTC)
    lines = CHECKS.read_application_log("compose", compose_file="compose.r1.yaml", since=since)
    result = CHECKS.check_application_signals(lines, now=NOW)

    assert not result.passed
    (command,) = calls
    assert command[:4] == ["docker", "compose", "-f", "compose.r1.yaml"]
    assert "logs" in command and "api" in command
    assert "--no-log-prefix" in command and "--no-color" in command
    # An absolute instant, not "the last N seconds": after a skipped run the read has to reach back
    # to where the previous run stopped, however long ago that was. Rounded down, never up.
    assert command[command.index("--since") + 1] == "2026-09-30T03:40:05Z"


def test_an_unreadable_log_source_is_a_failure_not_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    def _run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="no such service: api")

    monkeypatch.setattr(CHECKS.subprocess, "run", _run)
    with pytest.raises(RuntimeError, match="no such service"):
        CHECKS.read_application_log("compose", compose_file="compose.r1.yaml", since=NOW)


def test_reads_a_log_file(tmp_path: Path) -> None:
    log = tmp_path / "api.log"
    log.write_text("\n".join([*_heartbeat(), _refused(at=_ago(3))]) + "\n", encoding="utf-8")
    lines = CHECKS.read_application_log(str(log), compose_file="unused", since=NOW)
    assert CHECKS.count_application_signals(lines, now=NOW).database_refusals == 1


# --- end to end through main() and the existing alert delivery ---------------------------------


def _main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(CHECKS._sys, "argv", ["check_shop_operations.py", *argv])
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("R1_CONSOLE_HEALTH_URL", raising=False)
    monkeypatch.delenv("R1_PGDATA_PATH", raising=False)
    monkeypatch.delenv("R1_BASE_BACKUP_MARKER", raising=False)
    monkeypatch.delenv("R1_APP_LOGS", raising=False)
    monkeypatch.delenv("R1_APP_SIGNAL_CURSOR", raising=False)
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

    cursor = str(tmp_path / "cursor.json")
    arguments = ["--check", "app", "--app-logs", str(log), "--app-signal-cursor", cursor]
    assert _main(monkeypatch, [*arguments, "--emit-alert"]) == 1

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
    cursor = str(tmp_path / "cursor.json")
    arguments = ["--check", "app", "--app-logs", str(log), "--app-signal-cursor", cursor]
    assert _main(monkeypatch, [*arguments, "--json"]) == 0


def test_asked_for_without_a_source_refuses_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as raised:
        _main(monkeypatch, ["--check", "app", "--app-signal-cursor", str(tmp_path / "c"), "--json"])
    assert "app needs --app-logs" in str(raised.value)


def test_asked_for_without_a_read_position_refuses_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without a saved position every run could only look at a fixed window ending at its own
    start, and a line written between two windows would be counted by neither (round-9 verifier)."""

    monkeypatch.delenv("R1_APP_SIGNAL_CURSOR", raising=False)
    with pytest.raises(SystemExit) as raised:
        _main(monkeypatch, ["--check", "app", "--app-logs", str(tmp_path / "api.log"), "--json"])
    assert "app needs --app-signal-cursor" in str(raised.value)


# --- round 9, verifier issue 3: a 503 is a refusal only when the API said why ----------------
#
# `database.request_refused` explains the designed 503 and is counted as itself. The API also
# answers 503 for "staff identity unavailable" (nobody can sign in) and "operations unavailable"
# (a service it needs is not configured) and writes nothing else for them. Before, every 503 was
# left out of `server_errors`, so a sign-in outage at the start of a shift was 503 after 503 and the
# check passed.


def _sign_in_refused(*, at: datetime) -> str:
    return _request(503, at=at, route="/internal/v1/auth/session")


@pytest.mark.parametrize(
    ("lines", "server_errors", "refusals"),
    [
        pytest.param([_sign_in_refused(at=_ago(5))], 1, 0, id="503-no-refusal-line"),
        pytest.param(
            [_refused(at=_ago(6)), _sign_in_refused(at=_ago(5))],
            1,
            1,
            id="503-beside-somebody-elses-refusal",
        ),
        pytest.param(_refused_request(at=_ago(5)), 0, 1, id="503-with-its-own-refusal"),
        pytest.param(
            [_request(503, at=_ago(5), route="/readyz")], 0, 0, id="readyz-503-is-the-console-check"
        ),
        pytest.param([_request(500, at=_ago(5), route="/readyz")], 1, 0, id="readyz-500"),
        pytest.param([_request(500, at=_ago(5))], 1, 0, id="500"),
        pytest.param([_request(502, at=_ago(5))], 1, 0, id="502"),
        pytest.param([_request(504, at=_ago(5))], 1, 0, id="504"),
        pytest.param([_request(499, at=_ago(5))], 0, 0, id="499"),
    ],
)
def test_which_5xx_answers_are_server_errors(
    lines: list[str], server_errors: int, refusals: int
) -> None:
    counts = CHECKS.count_application_signals([*_heartbeat(), *lines], now=NOW)
    assert (counts.server_errors, counts.database_refusals) == (server_errors, refusals)


def test_a_sign_in_outage_at_the_start_of_a_shift_alerts() -> None:
    """The verifier's case: fifty 503s on sign-in in one window, and the check passed."""

    lines = [*_heartbeat(), *(_sign_in_refused(at=_ago(5 + index)) for index in range(50))]
    result = CHECKS.check_application_signals(lines, now=NOW)
    assert not result.passed
    assert result.fields["server_errors"] == 50
    assert "server_errors" in result.detail


def test_a_refusal_whose_503_lands_in_the_next_run_is_still_matched() -> None:
    """The refusal line is written before its 503 line; a run can end between the two."""

    correlation = _correlation()
    first = CHECKS.count_application_signals(
        [*_heartbeat(), _refused(at=_ago(1), correlation=correlation)], now=_ago(0)
    )
    second = CHECKS.count_application_signals(
        [
            *_heartbeat(),
            _refused(at=_ago(1), correlation=correlation),
            _request(503, at=NOW + timedelta(milliseconds=2), correlation=correlation),
        ],
        now=NOW + timedelta(seconds=300),
        cursor=first.cursor,
    )
    assert (first.database_refusals, second.server_errors) == (1, 0)


# --- round 9, verifier issue 2: every line is read by exactly one run --------------------------
#
# The window used to be the five minutes ending at the moment the check ran. The scheduler starts
# a run every 300 s, but the app check runs after the flags and console checks, so two consecutive
# windows only touched if those took exactly as long both times. A 500 in the jitter between them
# was counted by neither, and a run launchd delayed or skipped lost a whole window. Each run now
# starts where the previous one stopped.

T = NOW


def _runs(*runs: tuple[datetime, list[str]]) -> list[Any]:
    """Run the count once per (moment, log as it stood then), carrying the position forward."""

    cursor = None
    counted = []
    for moment, lines in runs:
        counts = CHECKS.count_application_signals(lines, now=moment, cursor=cursor)
        counted.append(counts)
        cursor = counts.cursor
    return counted


def test_a_500_in_the_jitter_between_two_runs_is_counted_by_the_second() -> None:
    before = [_request(200, at=T - timedelta(seconds=30), route="/readyz")]
    error = _request(500, at=T + timedelta(seconds=1))
    first, second = _runs((T, before), (T + timedelta(seconds=302), [*before, error]))
    assert (first.server_errors, second.server_errors) == (0, 1)


@pytest.mark.parametrize(
    "second_run_after",
    [299, 300, 301, 302, 360, 900, 3 * 3600],
    ids=lambda s: f"next-run-after-{s}s",
)
@pytest.mark.parametrize("error_after", [-1, 0, 1, 2, 250], ids=lambda s: f"500-at-{s}s")
def test_every_line_is_counted_by_exactly_one_run(second_run_after: int, error_after: int) -> None:
    """The matrix: a 500 before, at, and after the first run, read by runs early, late, skipped."""

    first_moment = T
    second_moment = T + timedelta(seconds=second_run_after)
    error_at = T + timedelta(seconds=error_after)
    heartbeat = _request(200, at=T - timedelta(seconds=30), route="/readyz")
    error = _request(500, at=error_at)
    # The first run sees the log as it stood when it read it: lines written up to that moment.
    first_log = [heartbeat, *([error] if error_at <= first_moment else [])]
    second_log = [heartbeat, error]
    first, second = _runs((first_moment, first_log), (second_moment, second_log))
    assert first.server_errors + second.server_errors == 1


def test_a_line_from_beyond_the_skew_is_counted_once_when_the_clock_reaches_it() -> None:
    heartbeat = _request(200, at=T - timedelta(seconds=30), route="/readyz")
    early = _request(500, at=T + timedelta(seconds=90))
    first, second, third = _runs(
        (T, [heartbeat, early]),
        (T + timedelta(seconds=300), [heartbeat, early]),
        (T + timedelta(seconds=600), [heartbeat, early]),
    )
    assert (first.server_errors, second.server_errors, third.server_errors) == (0, 1, 0)


def test_two_lines_at_the_cursor_instant_are_each_counted_once() -> None:
    """The position is the last line's instant; a second line at that same microsecond, read only
    by the next run, is not mistaken for the one already counted."""

    instant = T - timedelta(seconds=2)
    one = _request(500, at=instant)
    two = _request(502, at=instant)
    first, second = _runs((T, [one]), (T + timedelta(seconds=300), [one, two]))
    assert (first.server_errors, second.server_errors) == (1, 1)
    assert first.cursor.through == instant


def test_a_burst_across_two_runs_is_one_burst() -> None:
    """Rates are judged per five minutes, wherever the run boundary falls inside them."""

    threshold = CHECKS.APP_SIGNAL_THRESHOLDS["database_refusals"]
    head = [_refused(at=T - timedelta(seconds=10 - i)) for i in range(threshold - 2)]
    tail = [_refused(at=T + timedelta(seconds=5 + i)) for i in range(2)]
    heartbeat = _request(200, at=T - timedelta(seconds=30), route="/readyz")
    first, second = _runs(
        (T, [heartbeat, *head]), (T + timedelta(seconds=300), [heartbeat, *head, *tail])
    )
    assert first.database_refusals == threshold - 2
    assert second.database_refusals == threshold


def test_a_long_gap_does_not_turn_a_trickle_into_a_burst() -> None:
    """After a skipped hour the threshold still means "in five minutes", not "since last time"."""

    threshold = CHECKS.APP_SIGNAL_THRESHOLDS["database_refusals"]
    heartbeat = _request(200, at=T - timedelta(seconds=30), route="/readyz")
    trickle = [_refused(at=T + timedelta(minutes=6 * (i + 1))) for i in range(threshold + 2)]
    later = T + timedelta(hours=1)
    _, second = _runs((T, [heartbeat]), (later, [heartbeat, *trickle]))
    assert second.database_refusals == 1
    burst = [_refused(at=later - timedelta(seconds=i)) for i in range(threshold)]
    _, third = _runs((T, [heartbeat]), (later, [heartbeat, *trickle, *burst]))
    assert third.database_refusals == threshold


def test_a_saved_position_ahead_of_the_clock_reads_the_last_window() -> None:
    """A clock set back must not hide the lines written before the saved instant ever again."""

    ahead = CHECKS.AppSignalCursor(through=T + timedelta(hours=2), seen_at_through=frozenset())
    lines = [_request(200, at=T - timedelta(seconds=30), route="/readyz"), _request(500, at=T)]
    counts = CHECKS.count_application_signals(lines, now=T, cursor=ahead)
    assert counts.server_errors == 1
    assert counts.cursor_was_ahead
    assert counts.cursor.through == T


def _scheduled_run(log: Path, cursor: Path, moment: datetime) -> Any:
    return CHECKS.run_application_check(
        str(log), compose_file="unused", cursor_path=str(cursor), now=moment
    )


def test_the_scheduled_runs_carry_their_position_in_a_file(tmp_path: Path) -> None:
    """End to end through the file the till keeps: the verifier's jitter case, then no repeat."""

    log = tmp_path / "api.log"
    cursor = tmp_path / "state" / "app-signal-cursor.json"
    heartbeat = _request(200, at=T - timedelta(seconds=30), route="/readyz")
    log.write_text(heartbeat + "\n", encoding="utf-8")
    first = _scheduled_run(log, cursor, T)
    log.write_text("\n".join([heartbeat, _request(500, at=T + timedelta(seconds=1))]) + "\n")
    second = _scheduled_run(log, cursor, T + timedelta(seconds=302))
    third = _scheduled_run(log, cursor, T + timedelta(seconds=602))

    assert first.passed and not second.passed and third.passed
    assert second.fields["server_errors"] == 1 and third.fields["server_errors"] == 0
    saved = json.loads(cursor.read_text(encoding="utf-8"))
    assert saved["schema"] == CHECKS.APP_SIGNAL_CURSOR_SCHEMA
    assert datetime.fromisoformat(saved["through"]) == T + timedelta(seconds=1)


def test_the_compose_read_reaches_back_to_the_saved_position(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cursor = tmp_path / "cursor.json"
    CHECKS.save_app_signal_cursor(
        str(cursor),
        CHECKS.AppSignalCursor(through=T - timedelta(hours=3), seen_at_through=frozenset()),
    )
    calls: list[list[str]] = []

    def _run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="\n".join(_heartbeat()), stderr="")

    monkeypatch.setattr(CHECKS.subprocess, "run", _run)
    CHECKS.run_application_check("compose", compose_file="c.yaml", cursor_path=str(cursor), now=T)
    (command,) = calls
    since = datetime.fromisoformat(command[command.index("--since") + 1])
    assert since <= T - timedelta(hours=3, seconds=CHECKS.APP_SIGNAL_WINDOW_SECONDS)


def test_a_failed_read_does_not_move_the_position(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cursor = tmp_path / "cursor.json"
    saved = CHECKS.AppSignalCursor(through=T - timedelta(minutes=5), seen_at_through=frozenset())
    CHECKS.save_app_signal_cursor(str(cursor), saved)

    def _run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="daemon not running")

    monkeypatch.setattr(CHECKS.subprocess, "run", _run)
    with pytest.raises(RuntimeError):
        CHECKS.run_application_check("compose", compose_file="c", cursor_path=str(cursor), now=T)
    assert CHECKS.load_app_signal_cursor(str(cursor)) == saved


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{",
        "[]",
        '{"schema": "other", "through": "2026-09-30T04:00:00+00:00"}',
        '{"schema": "nha-trang-laundry.app-signal-cursor.v1", "through": "yesterday"}',
        '{"schema": "nha-trang-laundry.app-signal-cursor.v1", "through": "2026-09-30T04:00:00"}',
    ],
)
def test_an_unreadable_position_fails_the_check_rather_than_starting_over(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Starting over silently would skip whatever happened since; say so and say how to recover."""

    log = tmp_path / "api.log"
    log.write_text(_request(200, at=datetime.now(UTC), route="/readyz") + "\n", encoding="utf-8")
    cursor = tmp_path / "cursor.json"
    cursor.write_text(content, encoding="utf-8")
    arguments = ["--check", "app", "--app-logs", str(log), "--app-signal-cursor", str(cursor)]
    assert _main(monkeypatch, [*arguments, "--json"]) == 1
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["application_signals"]["passed"] is False
    assert "delete" in report["application_signals"]["detail"]
    assert cursor.read_text(encoding="utf-8") == content


# --- defaults in one place, and the place is documented -----------------------------------------


def test_the_runbook_states_the_same_thresholds_the_script_uses() -> None:
    runbook = (ROOT / "docs/runbooks/shop-pilot.md").read_text("utf-8")
    minutes = CHECKS.APP_SIGNAL_WINDOW_SECONDS // 60
    for signal, threshold in CHECKS.APP_SIGNAL_THRESHOLDS.items():
        # `server_errors` is a count of new lines; the other two are rates over the window.
        figure = (
            f"{threshold}" if signal == "server_errors" else f"{threshold} in {minutes} minutes"
        )
        assert f"`{signal}` | {figure} |" in runbook, signal
    assert f"judged over any {minutes} minutes" in runbook
    assert "R1_APP_SIGNAL_CURSOR" in runbook


def test_the_till_scheduler_runs_the_application_check() -> None:
    install = (ROOT / "deploy/shop-till/install.sh").read_text("utf-8")
    assert "--check app --app-logs compose" in install
