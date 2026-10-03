"""Whether a conformance run exercised every declared control -- on a stack used before, too.

`verify_workflow_conformance.py` ends with one line: "every declared control was exercised". A
control listed and never pressed is a gap, not assumed fine.

Round-9b integration (the slice-I verifier's residual): Đếm két's first-entry forms -- the opening
float's and the closing count's amount and save -- are offered once per store per business day
(`DEC-049`). A second full run on the same stack and day found both already recorded by the first
run, corrected them through Sửa instead (as the counter would), and the line failed 207/211 on
four controls no run could press there. So the count no longer depends on what an earlier run left
behind: a control may be excused only with the server's evidence that it is closed for a reason
older than this run (`recorded_before_this_run`), the report names each excused control and the
evidence, and every other untouched control still fails the line. On a fresh stack nothing is
excused and the forms are pressed.

Pure: no browser, no clock, no network -- the tests load it by path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, NamedTuple

_KIND_WORDS = {"OPENING_FLOAT": "tiền đầu ngày", "CLOSING_COUNT": "đếm cuối ngày"}


class Coverage(NamedTuple):
    """The gate's answer. A `NamedTuple`: the tests load this module by file path."""

    touched: int
    declared: int
    #: Declared and not touched, in declared order.
    missed: tuple[str, ...]
    #: The missed controls with evidence that this stack could not offer them in this run.
    excused: tuple[str, ...]
    #: The missed controls with no such evidence: each is a gap.
    unexcused: tuple[str, ...]
    #: One report line per missed control, saying which and why.
    notes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.unexcused

    @property
    def line(self) -> str:
        excused = (
            f"; {len(self.excused)} not offered on this stack today, with the server's evidence"
            if self.excused
            else ""
        )
        return f"every declared control was exercised ({self.touched}/{self.declared}{excused})"


def coverage(
    declared: Iterable[str], touched: Iterable[str], not_offered: Mapping[str, str]
) -> Coverage:
    """The gate over `declared`, given what was `touched` and what was proven `not_offered`.

    `not_offered` maps a control to the evidence that it was closed before this run began; it
    excuses only a control that was in fact not touched.
    """

    declared = tuple(declared)
    pressed = set(touched)
    missed = tuple(control for control in declared if control not in pressed)
    excused = tuple(control for control in missed if not_offered.get(control))
    unexcused = tuple(control for control in missed if control not in excused)
    notes = tuple(
        f"not offered on this stack today: {control} -- {not_offered[control]}"
        if control in excused
        else f"not exercised: {control}"
        for control in missed
    )
    return Coverage(
        touched=sum(1 for control in declared if control in pressed),
        declared=len(declared),
        missed=missed,
        excused=excused,
        unexcused=unexcused,
        notes=notes,
    )


def recorded_before_this_run(sheet: Mapping[str, Any], kind: str, started: datetime) -> str | None:
    """The evidence that today's `kind` (an Đếm két entry) was FIRST recorded before this run
    began -- so its first-entry form is closed for the day by an earlier run, not by this one --
    or `None`.

    Only the day's original entry counts (`supersedes_id` null): a correction does not close the
    form, and an original this run recorded itself is no excuse (the form should have been
    pressed).
    """

    for entry in sheet.get("entries") or ():
        if entry.get("kind") != kind or entry.get("supersedes_id") is not None:
            continue
        try:
            recorded = datetime.fromisoformat(str(entry.get("recorded_at")))
        except ValueError:
            return None
        if recorded.tzinfo is None or recorded >= started:
            return None
        words = _KIND_WORDS.get(kind, kind)
        return (
            f"today's {words} ({kind}) was first recorded at {recorded.isoformat()}, before this "
            f"run began ({started.isoformat()}); the form is offered once per store per business "
            "day (DEC-049), so this run corrected it through Sửa -- a fresh stack presses the form"
        )
    return None


__all__ = ["Coverage", "coverage", "recorded_before_this_run"]
