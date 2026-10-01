"""`DEC-050`: one clock for every measure of the customer's lateness. `MONEY-RESIDUAL-009B` J1.

`unclaimed.waiting_clock` counts the days finished laundry has waited, the days the shop held it
not counted. The storage fee (`DEC-047`), *days waiting*, disposal eligibility and the reminder day
steps all read it; nothing else in the domain or the repositories counts days from a ready time
(the last test pins that, so a fourth copy cannot creep back in).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.pickup_reminders import (
    ReminderStep,
    current_reminder,
    fee_starts_on,
)
from nha_trang_laundry_domain.unclaimed import (
    DisposalRefusal,
    StorageHold,
    StoragePolicy,
    counted_days,
    disposal_verdict,
    storage_fee,
    waiting_clock,
)

VN = timedelta(hours=7)
#: 10:00 shop time on 1 September 2026: day 0.
READY = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
POLICY = StoragePolicy(
    free_days=20,
    fee_per_started_day_vnd=5_000,
    fee_cap_percent=50,
    disposal_from_day=60,
    disposal_min_attempts=3,
    disposal_min_attempt_days=2,
)


def day(n: int, hour: int = 10) -> datetime:
    """`hour`:00 shop time, `n` shop days after the ready day."""

    return READY + timedelta(days=n, hours=hour - 10)


def lifted(start: int, end: int) -> StorageHold:
    return StorageHold(held_at=day(start, 11), resumed_at=day(end, 9))


def on_day(n: int) -> date:
    return date(2026, 9, 1) + timedelta(days=n)


# --- the matrix --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "holds", "as_of", "days", "held"),
    [
        ("no hold", (), 30, 30, 0),
        ("hold before the free days end", (lifted(10, 25),), 35, 20, 15),
        ("hold after the free days end", (lifted(25, 35),), 40, 30, 10),
        ("several holds", (lifted(5, 10), lifted(20, 30)), 50, 35, 15),
        # A hold lifted the same shop day it began takes no day out.
        ("same-day hold", (lifted(4, 4),), 10, 10, 0),
    ],
)
def test_the_clock_skips_every_lifted_hold(
    label: str, holds: tuple[StorageHold, ...], as_of: int, days: int, held: int
) -> None:
    clock = waiting_clock(READY, day(as_of), holds=holds)
    assert (clock.days, clock.held_days, clock.paused) == (days, held, False), label
    assert counted_days(READY, day(as_of), holds=holds) == days, label
    fee = storage_fee(
        POLICY, ready_at=READY, as_of=day(as_of), quoted_total_vnd=1_000_000, holds=holds
    )
    assert fee.days_waiting == days, label


def test_an_open_hold_freezes_the_count_and_has_no_future_day() -> None:
    holds = (lifted(5, 10), StorageHold(held_at=day(20, 11), resumed_at=None))
    clock = waiting_clock(READY, day(45), holds=holds, paused=True)
    assert (clock.days, clock.paused) == (15, True)
    assert clock.falls_on(60) is None
    # The same holds read as not paused (the order is no longer held for the customer): the open
    # hold is not lifted, so it takes nothing out.
    assert waiting_clock(READY, day(45), holds=holds).days == 40


def test_a_hold_before_the_last_ready_time_belongs_to_the_laundry_washed_again() -> None:
    rewashed_ready = day(12)
    clock = waiting_clock(rewashed_ready, day(16), holds=(lifted(3, 8),))
    assert (clock.days, clock.held_days) == (4, 0)


def test_disposal_counts_the_clock_and_its_eligible_day_moves_past_the_holds() -> None:
    attempts = [day(50, 9), day(50, 15), day(51, 10)]
    held = (lifted(30, 40),)
    early = disposal_verdict(
        POLICY, awaiting=True, ready_at=READY, as_of=day(65), attempt_times=attempts, holds=held
    )
    assert early.days_waiting == 55
    assert early.refusals == (DisposalRefusal.DISPOSAL_TOO_EARLY,)
    assert early.eligible_on == on_day(70)
    ready = disposal_verdict(
        POLICY, awaiting=True, ready_at=READY, as_of=day(70), attempt_times=attempts, holds=held
    )
    assert (ready.days_waiting, ready.allowed) == (60, True)


@pytest.mark.parametrize(
    ("holds", "as_of", "expected"),
    [
        # Held from day 2 to day 9 (spanning DAY_3 and DAY_7): day 9 counts as 2.
        ((lifted(2, 9),), 9, ReminderStep.READY),
        ((lifted(2, 9),), 10, ReminderStep.DAY_3),
        ((lifted(2, 9),), 14, ReminderStep.DAY_7),
        # Two holds: 26 calendar days, 20 count -- BEFORE_FEE, not past the fee.
        ((lifted(3, 5), lifted(10, 14)), 26, ReminderStep.BEFORE_FEE),
        ((lifted(3, 5), lifted(10, 14)), 27, None),
    ],
)
def test_a_reminder_step_falls_on_the_counted_day(
    holds: tuple[StorageHold, ...], as_of: int, expected: ReminderStep | None
) -> None:
    clock = waiting_clock(READY, day(as_of), holds=holds)
    assert current_reminder(clock.days, POLICY, ()) is expected


def test_the_fee_day_the_reminder_names_is_past_the_held_days() -> None:
    clock = waiting_clock(READY, day(26), holds=(lifted(3, 5), lifted(10, 14)))
    assert clock.days == 20
    assert fee_starts_on(clock, POLICY) == on_day(27)
    paused = waiting_clock(READY, day(26), holds=(StorageHold(day(20, 11), None),), paused=True)
    with pytest.raises(ValueError):
        fee_starts_on(paused, POLICY)


# --- properties -----------------------------------------------------------------------------------


@st.composite
def _holds(draw: st.DrawFn) -> tuple[tuple[StorageHold, ...], int]:
    """Up to four lifted, non-overlapping holds (in hours after the ready time) and an instant."""

    count = draw(st.integers(min_value=0, max_value=4))
    cursor = draw(st.integers(min_value=0, max_value=72))
    holds: list[StorageHold] = []
    for _ in range(count):
        start = cursor + draw(st.integers(min_value=1, max_value=24 * 15))
        end = start + draw(st.integers(min_value=0, max_value=24 * 20))
        holds.append(StorageHold(READY + timedelta(hours=start), READY + timedelta(hours=end)))
        cursor = end
    as_of = cursor + draw(st.integers(min_value=0, max_value=24 * 40))
    return tuple(holds), as_of


@given(_holds())
def test_property_the_count_is_calendar_days_less_held_days_and_never_negative(
    drawn: tuple[tuple[StorageHold, ...], int],
) -> None:
    holds, hours = drawn
    as_of = READY + timedelta(hours=hours)
    clock = waiting_clock(READY, as_of, holds=holds)
    calendar = ((as_of + VN).date() - (READY + VN).date()).days
    assert clock.days >= 0 and clock.held_days >= 0
    assert clock.days + clock.held_days == calendar
    # Never more than a clock that ignored the holds; one more lifted hold never adds a day.
    assert clock.days <= waiting_clock(READY, as_of, holds=()).days
    assert clock.days <= waiting_clock(READY, as_of, holds=holds[:-1]).days


@given(_holds(), st.integers(min_value=0, max_value=23))
def test_property_the_day_a_count_falls_on_reads_that_count(
    drawn: tuple[tuple[StorageHold, ...], int], hour: int
) -> None:
    holds, hours = drawn
    clock = waiting_clock(READY, READY + timedelta(hours=hours), holds=holds)
    for n in range(clock.days + 1):
        on = clock.falls_on(n)
        assert on is not None
        moment = datetime(on.year, on.month, on.day, hour, tzinfo=UTC) - VN
        # Only the holds that had begun by then exist at that instant.
        known = tuple(hold for hold in holds if hold.held_at <= moment)
        assert waiting_clock(READY, moment, holds=known).days == n


@given(_holds(), st.integers(min_value=0, max_value=24 * 30))
def test_property_a_paused_count_never_moves(
    drawn: tuple[tuple[StorageHold, ...], int], later: int
) -> None:
    holds, hours = drawn
    held_at = READY + timedelta(hours=hours)
    open_hold = (*holds, StorageHold(held_at, None))
    frozen = waiting_clock(READY, held_at, holds=open_hold, paused=True).days
    assert (
        frozen
        == waiting_clock(READY, held_at + timedelta(hours=later), holds=open_hold, paused=True).days
    )
    assert frozen == waiting_clock(READY, held_at, holds=holds).days


# --- one clock, not three copies --------------------------------------------------------------


ROOT = Path(__file__).resolve().parents[3]
#: Day arithmetic from a ready time outside the clock: `days_waiting(...)` calls, or a subtraction
#: of shop dates.
_COPY = re.compile(r"\bdays_waiting\(|shop_date\([^)]*\)\s*-\s*shop_date\(|\(today - ready_on\)")


def test_nothing_but_the_clock_counts_days_from_a_ready_time() -> None:
    checked = [
        ROOT / "packages/domain/src/nha_trang_laundry_domain/pickup_reminders.py",
        ROOT / "packages/db/src/nha_trang_laundry_db/unclaimed.py",
        ROOT / "packages/db/src/nha_trang_laundry_db/pickup_reminders.py",
        ROOT / "packages/db/src/nha_trang_laundry_db/storage_fees.py",
    ]
    for path in checked:
        found = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if _COPY.search(line) and not line.strip().startswith(("#", '"', "`"))
        ]
        assert found == [], (path.name, found)
    # In the domain module the two uses are the clock itself.
    unclaimed = (ROOT / "packages/domain/src/nha_trang_laundry_domain/unclaimed.py").read_text(
        encoding="utf-8"
    )
    body = unclaimed.split("def waiting_clock(", 1)[1].split("\ndef ", 1)[0]
    calls = [
        line for line in unclaimed.splitlines() if "days_waiting(" in line and "def " not in line
    ]
    assert all(line.strip() in body for line in calls), calls
