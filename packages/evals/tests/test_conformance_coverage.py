"""The conformance run's "every declared control was exercised" gate, on a stack used before.

Round-9b integration (the slice-I verifier's residual, P2): Đếm két's first-entry forms -- the
opening float's and the closing count's amount and save -- are offered once per store per business
day (`DEC-049`). A second full run on the same stack and day found both recorded by the first run,
took the Sửa path instead, and the gate failed "every declared control was exercised (207/211)" on
four controls no run could press there. A desk run followed by a phone run on one stack, as the
round asks, therefore failed. The gate's count no longer depends on what an earlier run left
behind: a control is excused only with the server's evidence that it is closed for a reason older
than this run, and the report names each; an untouched control without that evidence still fails.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
STARTED = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
FIRST_ENTRY = (
    "cashCount.float-amount",
    "cashCount.float-save",
    "cashCount.close-amount",
    "cashCount.close-save",
)
DECLARED = (*FIRST_ENTRY, "cashCount.correct", "today.cash-count", "shell.nav.today")


def _coverage() -> ModuleType:
    path = ROOT / "scripts" / "conformance_coverage.py"
    specification = importlib.util.spec_from_file_location("_r9b_conformance_coverage", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _entry(kind: str, at: datetime, *, supersedes: str | None = None) -> dict[str, object]:
    return {
        "entry_id": f"{kind}-{at.isoformat()}",
        "kind": kind,
        "supersedes_id": supersedes,
        "recorded_at": at.isoformat(),
        "recorded_by_name": "Demo Nhân viên vận hành",
    }


def _second_run_sheet() -> dict[str, object]:
    """Today's sheet as the second run reads it: the first run's float, correction and close."""

    earlier = STARTED - timedelta(minutes=40)
    return {
        "business_day": "2026-10-03",
        "entries": [
            _entry("OPENING_FLOAT", earlier),
            _entry("OPENING_FLOAT", earlier + timedelta(minutes=1), supersedes="x"),
            _entry("CLOSING_COUNT", earlier + timedelta(minutes=5)),
        ],
    }


def test_a_second_run_is_not_failed_by_the_first_runs_cash_count() -> None:
    coverage = _coverage()
    sheet = _second_run_sheet()
    excused: dict[str, str] = {}
    for kind, prefix in (("OPENING_FLOAT", "float"), ("CLOSING_COUNT", "close")):
        evidence = coverage.recorded_before_this_run(sheet, kind, STARTED)
        assert evidence is not None and "DEC-049" in evidence, evidence
        for control in (f"cashCount.{prefix}-amount", f"cashCount.{prefix}-save"):
            excused[control] = evidence
    touched = set(DECLARED) - set(FIRST_ENTRY)

    result = coverage.coverage(DECLARED, touched, excused)
    assert result.passed, result
    assert result.missed == FIRST_ENTRY and result.unexcused == ()
    assert "3/7" in result.line and "4 not offered" in result.line, result.line
    assert all(control in "\n".join(result.notes) for control in FIRST_ENTRY), result.notes


def test_an_untouched_control_without_evidence_still_fails() -> None:
    coverage = _coverage()
    evidence = coverage.recorded_before_this_run(_second_run_sheet(), "OPENING_FLOAT", STARTED)
    excused = dict.fromkeys(("cashCount.float-amount", "cashCount.float-save"), evidence)
    touched = set(DECLARED) - set(FIRST_ENTRY)

    result = coverage.coverage(DECLARED, touched, excused)
    assert not result.passed
    assert result.unexcused == ("cashCount.close-amount", "cashCount.close-save")
    # And with nothing excused: exactly the old gate.
    plain = coverage.coverage(DECLARED, touched, {})
    assert not plain.passed and plain.unexcused == FIRST_ENTRY


def test_an_entry_recorded_during_this_run_is_no_evidence() -> None:
    """A first entry this run made itself (or one the sheet does not show) excuses nothing: then
    the form should have been pressed, and its absence is a defect the gate must report."""

    coverage = _coverage()
    sheet = {"entries": [_entry("OPENING_FLOAT", STARTED + timedelta(seconds=5))]}
    assert coverage.recorded_before_this_run(sheet, "OPENING_FLOAT", STARTED) is None
    assert coverage.recorded_before_this_run({"entries": []}, "OPENING_FLOAT", STARTED) is None
    # Only the day's ORIGINAL entry counts: a correction made earlier is not the first entry.
    correction_only = {
        "entries": [
            _entry("CLOSING_COUNT", STARTED + timedelta(seconds=5)),
            _entry("CLOSING_COUNT", STARTED - timedelta(minutes=1), supersedes="y"),
        ]
    }
    assert coverage.recorded_before_this_run(correction_only, "CLOSING_COUNT", STARTED) is None


def test_the_conformance_run_reports_its_gate_through_it() -> None:
    source = (ROOT / "scripts" / "verify_workflow_conformance.py").read_text(encoding="utf-8")
    assert "from conformance_coverage import" in source
    assert "recorded_before_this_run(" in source and "coverage(" in source
    assert "missed = [control for control in DECLARED_CONTROLS if control not in TOUCHED]" not in (
        source
    )
