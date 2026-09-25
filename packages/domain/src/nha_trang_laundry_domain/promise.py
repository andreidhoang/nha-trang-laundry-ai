"""The promised-ready time ("hẹn trả") of an order, in the shop's opening hours (`PROMISE-001`).

`DEC-037`. Every rule here is owner-confirmed in `templates/service-sla.csv`,
`templates/business-calendar-rules.csv` and `BUSINESS_TRUTH_INTAKE.md`; this module wires them and
decides nothing new:

* **Standard clothing, washed and dried** (`SLA_STANDARD_CLOTHES`): 8 hours from acceptance.
* **Shoes, curtains, blankets and sheets** (`SLA_SHOES`, `SLA_CURTAINS`, `SLA_BLANKETS_SHEETS`):
  24 or 48 hours, the staff member's choice after inspection -- 48 when they say nothing ("promise
  late, deliver early"). These are **calendar** hours -- one or two days, as a Vietnamese customer
  hears "24 giờ / 48 giờ" -- rolled into opening hours (founder ruling on `DEC-037`, 2026-09-25).
* **Everything else** -- plush toys, bags, leather, toppers, pillows, dry cleaning, ironing, the
  rest (`SLA_OTHER_SPECIAL`): `HUMAN_ETA_REQUIRED`, the staff member picks the day and hour.
* **Express, 2 hours**: only when the staff member chooses it after checking the machines, and only
  for an order of standard clothing (the one service whose fastest turnaround the owner stated).
* **The 8 h and the 2 h are counted in opening hours**, 08:00-20:00 Asia/Ho_Chi_Minh, published
  closed days skipped, because they are machine and staff work inside the working day. Laundry
  accepted at 17:00 is promised at 13:00 the next open day.
* **The 24 h and 48 h are counted on the clock** and then rolled into opening hours: landing before
  08:00 moves to 08:00 that day, after 20:00 to 08:00 the next day, and a published closed day to
  08:00 of the next open day. Friday 17:00 + 48 h is Sunday 17:00.
* **An unpublished Tết stops both.** A promise whose span touches late January or February of a
  year whose Tết days are not published is not made: the staff member sets the time
  (`TET_DATES_UNPUBLISHED`), because the software cannot know which six days the shop shuts.
* **The latest line wins**: an order's promise is the latest of its lines' promises.

The console shows the resulting day and hour beside each choice before anything is pressed, so
nobody promises a number of hours without seeing the date it means.

**Pure.** No clock, no database, no environment, no time-zone database: the shop's zone is a fixed
UTC+7 offset (Vietnam has kept one offset, without daylight saving, since 1975), so a promise
recomputed from its trace years from now lands on the same minute. Every instant is a parameter.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Any, Final

#: The configuration type the owner's turnaround policy is published under.
TURNAROUND_POLICY_CONFIG_TYPE: Final = "TURNAROUND_POLICY"
#: The document version this module parses. A different one is not a policy this code understands.
TURNAROUND_POLICY_VERSION: Final = "turnaround-policy-v1"
TURNAROUND_DECISION_REF: Final = "DEC-037"

#: Asia/Ho_Chi_Minh as a fixed offset -- see the module docstring.
SHOP_TIMEZONE: Final = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")
SHOP_TIMEZONE_NAME: Final = "Asia/Ho_Chi_Minh"

#: The part of the year Tết can fall in, inclusive: the first day of Tết is between 21 January and
#: 20 February, and the shop's six days sit around it. A promise touching any day in this window of
#: a year whose Tết days are unpublished needs a person.
TET_WINDOW_START: Final = (1, 15)
TET_WINDOW_END: Final = (2, 29)
#: How many Tết days the owner closes (`TET_CLOSE`, `days_count` 6).
TET_DAYS: Final = 6

#: `LinePromise.counting` for a 24/48 h line (founder ruling on `DEC-037`).
CALENDAR_HOURS: Final = "CALENDAR_HOURS"

#: `promise_state` calls an unfinished order "due soon" inside this distance of its promise. A
#: display threshold for the counter, not a business commitment.
DUE_SOON: Final = timedelta(hours=2)

#: How far ahead the arithmetic walks before it stops and asks a person. 48 opening hours is four
#: open days; a year is far beyond anything a rule here can reach, so hitting it means the calendar
#: is broken (every day closed), and that is refused rather than looped on.
_MAX_DAYS_WALKED: Final = 400

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_DAY_PATTERN = re.compile(r"^\d{2}-\d{2}$")
_CLOCK_PATTERN = re.compile(r"^\d{2}:\d{2}$")
_SERVICE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class TurnaroundScope(StrEnum):
    """The `service_scope` rows of `templates/service-sla.csv`, one member each."""

    STANDARD_CLOTHES = "STANDARD_CLOTHES"
    SHOES = "SHOES"
    CURTAINS = "CURTAINS"
    BLANKETS_AND_SHEETS = "BLANKETS_AND_SHEETS"
    OTHER_SPECIAL = "OTHER_SPECIAL"


class ScopeKind(StrEnum):
    #: One duration, computed without asking (standard clothing, 8 h).
    FIXED = "FIXED"
    #: The staff member picks one of `choices_hours` (24 / 48), `default_hours` when silent.
    RANGE_CHOICE = "RANGE_CHOICE"
    #: The staff member sets the day and hour.
    HUMAN = "HUMAN"


class PromiseChoice(StrEnum):
    """What the staff member said at *Nhận đồ*. Absent means "the rule".

    `H24` / `H48` apply to the 24-48 h lines; `EXPRESS_2H` to an order of standard clothing only;
    `CUSTOM` is a day and hour the staff member picked (`custom_at`), for any order.
    """

    H24 = "H24"
    H48 = "H48"
    EXPRESS_2H = "EXPRESS_2H"
    CUSTOM = "CUSTOM"


class PromiseRequirement(StrEnum):
    """What the counter must ask before *Nhận đồ* for this order."""

    #: The rule computes the promise; nothing to ask (an express choice is still offered).
    NONE = "NONE"
    #: 24 or 48 h for the order's shoes / curtains / blankets; 48 when not asked.
    RANGE_CHOICE = "RANGE_CHOICE"
    #: The staff member must pick the day and hour.
    CUSTOM = "CUSTOM"


class PromiseReason(StrEnum):
    """Why a person must set the time, one code per cause."""

    #: A line is a special item (`SLA_OTHER_SPECIAL`) -- the owner's rule is "staff set it".
    HUMAN_ETA_REQUIRED = "HUMAN_ETA_REQUIRED"
    #: The promise would run through late January-February of a year whose Tết days are unpublished.
    TET_DATES_UNPUBLISHED = "TET_DATES_UNPUBLISHED"
    #: A line's service is not in the published policy (a service added to the price list later).
    SERVICE_NOT_IN_POLICY = "SERVICE_NOT_IN_POLICY"
    #: The order has no priced line to promise anything for.
    NO_LINES = "NO_LINES"


class PromiseRefusal(StrEnum):
    """A promise the staff member asked for that the rules cannot take. Nothing is written."""

    PROMISE_CHOICE_NOT_APPLICABLE = "PROMISE_CHOICE_NOT_APPLICABLE"
    PROMISE_CUSTOM_AT_REQUIRED = "PROMISE_CUSTOM_AT_REQUIRED"
    PROMISE_CUSTOM_AT_NOT_TAKEN = "PROMISE_CUSTOM_AT_NOT_TAKEN"
    PROMISE_NOT_AFTER_ACCEPTANCE = "PROMISE_NOT_AFTER_ACCEPTANCE"
    PROMISE_OUTSIDE_OPENING_HOURS = "PROMISE_OUTSIDE_OPENING_HOURS"
    PROMISE_ON_CLOSED_DAY = "PROMISE_ON_CLOSED_DAY"
    PROMISE_UNCHANGED = "PROMISE_UNCHANGED"


class PromiseState(StrEnum):
    """Where an order stands against its current promise, at an instant."""

    ON_TRACK = "ON_TRACK"
    DUE_SOON = "DUE_SOON"
    LATE = "LATE"
    MET = "MET"
    MISSED = "MISSED"


class PromiseChangeReason(StrEnum):
    """Why a promise was moved (*Hẹn lại*). `OTHER` needs a note."""

    CUSTOMER_REQUEST = "CUSTOMER_REQUEST"
    EXTRA_TREATMENT = "EXTRA_TREATMENT"
    MACHINE_ISSUE = "MACHINE_ISSUE"
    WORKLOAD = "WORKLOAD"
    WEATHER_DRYING = "WEATHER_DRYING"
    OTHER = "OTHER"


class TurnaroundPolicyError(ValueError):
    """The document is not a turnaround policy this code can apply. Nothing is promised."""


class PromiseError(ValueError):
    """A staff choice the rules refuse, with its code."""

    def __init__(self, code: PromiseRefusal, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}" if detail else code.value)


@dataclass(frozen=True, slots=True)
class ScopeRule:
    scope: TurnaroundScope
    sla_id: str
    kind: ScopeKind
    #: `FIXED` only.
    hours: int | None = None
    #: `RANGE_CHOICE` only, ascending.
    choices_hours: tuple[int, ...] = ()
    default_hours: int | None = None


@dataclass(frozen=True, slots=True)
class TurnaroundPolicy:
    """The owner's published turnaround rules, parsed. Build with `parse_turnaround_policy`."""

    opens_at: time
    closes_at: time
    #: Closed every year on these (month, day) pairs: 30/4 and 1/5.
    recurring_closed: frozenset[tuple[int, int]]
    #: Closed on these dates: this year's Tết days and any ad-hoc day the owner entered.
    closed_dates: frozenset[date]
    #: The years whose Tết days are published (and therefore in `closed_dates`).
    tet_years: frozenset[int]
    scopes: Mapping[TurnaroundScope, ScopeRule]
    #: Service code (as a quote line names it) -> its scope. A code not here needs a person.
    services: Mapping[str, TurnaroundScope]
    express_hours: int

    def is_closed(self, day: date) -> bool:
        return day in self.closed_dates or (day.month, day.day) in self.recurring_closed

    def tet_unknown(self, day: date) -> bool:
        return _in_tet_window(day) and day.year not in self.tet_years


@dataclass(frozen=True, slots=True)
class LinePromise:
    """One line's part of the decision, for the trace and for "which lines need a person"."""

    service_code: str
    scope: TurnaroundScope | None
    sla_id: str | None
    #: The hours this line was counted at, or None when a person must set it.
    hours: int | None
    #: `OPENING_HOURS` (8 h, 2 h) or `CALENDAR_HOURS` (24 h / 48 h, rolled into opening hours).
    counting: str = "OPENING_HOURS"


@dataclass(frozen=True, slots=True)
class Promised:
    """The order's promise. `basis` says which rule produced it."""

    promised_at: datetime
    #: `RULE` (every line computed without a choice), `H24`, `H48`, `EXPRESS_2H` or `CUSTOM`.
    basis: str
    #: The rule behind the latest line (`SLA_STANDARD_CLOTHES`, `SLA_BLANKETS_SHEETS`, ...), or
    #: `STAFF_SET` for a custom time / `EXPRESS_2H` for express.
    rule_id: str
    lines: tuple[LinePromise, ...]
    accepted_at: datetime

    def trace(self) -> dict[str, Any]:
        """The calculation as JSON primitives, stored with the promise so it can be re-derived."""

        return {
            "accepted_at": _utc_text(self.accepted_at),
            "promised_at": _utc_text(self.promised_at),
            "basis": self.basis,
            "rule_id": self.rule_id,
            "timezone": SHOP_TIMEZONE_NAME,
            "lines": [
                {
                    "service_code": line.service_code,
                    "scope": None if line.scope is None else line.scope.value,
                    "sla_id": line.sla_id,
                    "hours": line.hours,
                    "counting": line.counting,
                }
                for line in self.lines
            ],
        }


@dataclass(frozen=True, slots=True)
class PromiseNeedsHuman:
    """No promise can be computed: a person must set it (`PROMISE_REQUIRED`)."""

    reason_codes: tuple[PromiseReason, ...]
    #: The service codes of the lines that need a person, in line order, de-duplicated.
    service_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChoiceOption:
    choice: PromiseChoice
    #: What pressing *Nhận đồ* now with this choice would promise; None when this choice cannot be
    #: computed (only for `CUSTOM`, which is the person's own time).
    promised_at: datetime | None


@dataclass(frozen=True, slots=True)
class PromiseOptions:
    """What the counter shows before *Nhận đồ*: the rule's answer and the choices it can take."""

    requirement: PromiseRequirement
    #: The promise pressing now without a choice would store; None when a person must set it.
    default_promised_at: datetime | None
    default_choice: PromiseChoice | None
    choices: tuple[ChoiceOption, ...]
    reason_codes: tuple[PromiseReason, ...]
    #: The lines that need a person (special items), by service code.
    human_service_codes: tuple[str, ...]


# --- the policy document ----------------------------------------------------------------------


def parse_turnaround_policy(payload: Mapping[str, Any]) -> TurnaroundPolicy:
    """Parse and check one published document. Anything unexpected refuses the whole policy."""

    try:
        if payload.get("policy_version") != TURNAROUND_POLICY_VERSION:
            raise TurnaroundPolicyError("unknown turnaround policy version")
        if payload.get("decision_ref") != TURNAROUND_DECISION_REF:
            raise TurnaroundPolicyError("the policy must cite DEC-037")
        if payload.get("timezone") != SHOP_TIMEZONE_NAME:
            raise TurnaroundPolicyError("the shop's timezone is Asia/Ho_Chi_Minh")
        opening = _mapping(payload, "opening_hours")
        opens_at = _clock(opening.get("opens"))
        closes_at = _clock(opening.get("closes"))
        if not opens_at < closes_at:
            raise TurnaroundPolicyError("the shop must open before it closes")
        recurring = frozenset(_month_day(item) for item in _list(payload, "recurring_closed"))
        tet = _mapping(payload, "tet")
        tet_years: set[int] = set()
        closed: set[date] = set()
        for year_text, days in tet.items():
            year = int(str(year_text))
            if str(year) != str(year_text) or not isinstance(days, list):
                raise TurnaroundPolicyError("tet dates are listed by year")
            parsed = [_date(item) for item in days]
            validate_tet_dates(parsed, year=year)
            tet_years.add(year)
            closed.update(parsed)
        for item in _list(payload, "adhoc_closed"):
            closed.add(_date(item))
        scopes = _scopes(_mapping(payload, "scopes"))
        services: dict[str, TurnaroundScope] = {}
        for code, scope in _mapping(payload, "services").items():
            if not isinstance(code, str) or not _SERVICE_PATTERN.fullmatch(code):
                raise TurnaroundPolicyError("a service code is malformed")
            services[code] = TurnaroundScope(str(scope))
        express = payload.get("express_hours")
        if not _positive_int(express):
            raise TurnaroundPolicyError("express hours must be a positive integer")
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, TurnaroundPolicyError):
            raise
        raise TurnaroundPolicyError(f"turnaround policy is malformed: {error}") from error
    return TurnaroundPolicy(
        opens_at=opens_at,
        closes_at=closes_at,
        recurring_closed=recurring,
        closed_dates=frozenset(closed),
        tet_years=frozenset(tet_years),
        scopes=scopes,
        services=services,
        express_hours=int(str(express)),
    )


def validate_turnaround_policy(payload: Mapping[str, Any]) -> None:
    """The configuration repository's typed validator: parse or refuse."""

    parse_turnaround_policy(payload)


def validate_tet_dates(days: Iterable[date], *, year: int | None = None) -> None:
    """Six distinct days of one year, each inside the window Tết can fall in."""

    listed = list(days)
    if len(listed) != TET_DAYS or len(set(listed)) != TET_DAYS:
        raise TurnaroundPolicyError(f"Tết is {TET_DAYS} distinct days")
    years = {day.year for day in listed}
    if len(years) != 1 or (year is not None and years != {year}):
        raise TurnaroundPolicyError("all Tết days belong to one year")
    if not all(_in_tet_window(day) for day in listed):
        raise TurnaroundPolicyError("a Tết day falls outside mid-January to February")


def scope_for_service(service_code: str, category: str) -> TurnaroundScope:
    """Which `service-sla.csv` scope a price-list service belongs to.

    From the owner's own words, most specific first. `service-sla.csv`'s `SLA_OTHER_SPECIAL` names
    toppers and pillows as special even though the price list files them under `bedding`, so only
    the blanket is `BLANKETS_AND_SHEETS`; curtains are filed under `other` and are named by their
    service codes. Everything the SLA sheet does not name -- ironing, dry cleaning, leather, drying
    only, sofas, carpets, plush toys, stain removal -- is special: a person sets the time.
    """

    if category == "standard_weight":
        return TurnaroundScope.STANDARD_CLOTHES
    if category == "shoes":
        return TurnaroundScope.SHOES
    if service_code in _CURTAIN_SERVICES:
        return TurnaroundScope.CURTAINS
    if service_code in _BLANKET_SERVICES:
        return TurnaroundScope.BLANKETS_AND_SHEETS
    return TurnaroundScope.OTHER_SPECIAL


_CURTAIN_SERVICES: Final = frozenset({"OTHER_CURTAIN", "OTHER_CURTAIN_INSTALL"})
_BLANKET_SERVICES: Final = frozenset({"BED_BLANKET"})

#: `service-sla.csv` `sla_id` by scope. The publisher checks the sheet still says these.
SLA_ID_BY_SCOPE: Final = {
    TurnaroundScope.STANDARD_CLOTHES: "SLA_STANDARD_CLOTHES",
    TurnaroundScope.SHOES: "SLA_SHOES",
    TurnaroundScope.CURTAINS: "SLA_CURTAINS",
    TurnaroundScope.BLANKETS_AND_SHEETS: "SLA_BLANKETS_SHEETS",
    TurnaroundScope.OTHER_SPECIAL: "SLA_OTHER_SPECIAL",
}


# --- opening-hour arithmetic ------------------------------------------------------------------


def add_opening_hours(policy: TurnaroundPolicy, start: datetime, hours: int) -> datetime | None:
    """`start` plus `hours` of opening time, or None when the walk touches an unknown Tết.

    The clock only runs while the shop is open: before opening it waits for 08:00, after closing
    and on a closed day it waits for the next opening. A duration that ends exactly at closing time
    ends there (20:00 is a promise, not "tomorrow 08:00"). The result is floored to the minute --
    a promise is a time a person says, and flooring never makes it later than the rule.
    """

    _require_aware(start)
    if hours <= 0:
        raise ValueError("hours must be positive")
    local = start.astimezone(SHOP_TIMEZONE)
    remaining = timedelta(hours=hours)
    day = local.date()
    cursor = local
    for _ in range(_MAX_DAYS_WALKED):
        if policy.tet_unknown(day):
            return None
        open_at = datetime.combine(day, policy.opens_at, SHOP_TIMEZONE)
        close_at = datetime.combine(day, policy.closes_at, SHOP_TIMEZONE)
        if not policy.is_closed(day):
            cursor = max(cursor, open_at)
            if cursor < close_at:
                available = close_at - cursor
                if remaining <= available:
                    return _floor_minute(cursor + remaining).astimezone(UTC)
                remaining -= available
        day = day + timedelta(days=1)
        cursor = datetime.combine(day, policy.opens_at, SHOP_TIMEZONE)
    raise TurnaroundPolicyError("the calendar has no opening hours within a year")


def add_calendar_hours(policy: TurnaroundPolicy, start: datetime, hours: int) -> datetime | None:
    """`start` plus `hours` on the clock, rolled into opening hours; None across an unknown Tết.

    Founder ruling on `DEC-037` (2026-09-25): "24 giờ / 48 giờ" is one or two calendar days, as a
    customer hears it. The instant is floored to the minute; before opening it moves to opening that
    day, after closing to opening the next day, and on a published closed day to opening on the next
    open day. Every day from `start` to the promise is checked against the Tết-unknown window.
    """

    _require_aware(start)
    if hours <= 0:
        raise ValueError("hours must be positive")
    local = start.astimezone(SHOP_TIMEZONE)
    landing = _floor_minute(local + timedelta(hours=hours))
    day = landing.date()
    clock = landing.timetz().replace(tzinfo=None)
    if clock < policy.opens_at:
        landing = datetime.combine(day, policy.opens_at, SHOP_TIMEZONE)
    elif clock > policy.closes_at:
        day = day + timedelta(days=1)
        landing = datetime.combine(day, policy.opens_at, SHOP_TIMEZONE)
    for _ in range(_MAX_DAYS_WALKED):
        if not policy.is_closed(day):
            break
        day = day + timedelta(days=1)
        landing = datetime.combine(day, policy.opens_at, SHOP_TIMEZONE)
    else:
        raise TurnaroundPolicyError("the calendar has no open day within a year")
    walked = local.date()
    while walked <= day:
        if policy.tet_unknown(walked):
            return None
        walked = walked + timedelta(days=1)
    return landing.astimezone(UTC)


def check_promise_time(policy: TurnaroundPolicy, at: datetime, *, after: datetime) -> datetime:
    """A person's own promise time, checked: later than `after`, on an open day, in opening hours.

    Returned floored to the minute and in UTC. An unpublished Tết does not refuse it: the rule
    asks a person precisely because the software cannot know those days, and this is the person.
    """

    _require_aware(at)
    _require_aware(after)
    moment = _floor_minute(at.astimezone(SHOP_TIMEZONE))
    if moment <= after:
        raise PromiseError(PromiseRefusal.PROMISE_NOT_AFTER_ACCEPTANCE)
    if policy.is_closed(moment.date()):
        raise PromiseError(PromiseRefusal.PROMISE_ON_CLOSED_DAY)
    clock = moment.timetz().replace(tzinfo=None)
    if not policy.opens_at <= clock <= policy.closes_at:
        raise PromiseError(PromiseRefusal.PROMISE_OUTSIDE_OPENING_HOURS)
    return moment.astimezone(UTC)


# --- the decision -----------------------------------------------------------------------------


def compute_promise(
    policy: TurnaroundPolicy,
    *,
    accepted_at: datetime,
    service_codes: Iterable[str],
    choice: PromiseChoice | None = None,
    custom_at: datetime | None = None,
) -> Promised | PromiseNeedsHuman:
    """The order's promise at *Nhận đồ*, or which lines need a person. Raises `PromiseError`.

    `service_codes` is the order's lines in order (a code may repeat). The promise is the latest
    line's. `choice` is the staff member's word; absent is the rule, which is 48 h for a 24-48 h
    line and nothing at all for a special item.
    """

    _require_aware(accepted_at)
    codes = tuple(service_codes)
    if custom_at is not None and choice is not PromiseChoice.CUSTOM:
        raise PromiseError(PromiseRefusal.PROMISE_CUSTOM_AT_NOT_TAKEN)
    lines = tuple(_line(policy, code) for code in codes)

    if choice is PromiseChoice.CUSTOM:
        if custom_at is None:
            raise PromiseError(PromiseRefusal.PROMISE_CUSTOM_AT_REQUIRED)
        if not codes:
            return PromiseNeedsHuman((PromiseReason.NO_LINES,), ())
        promised = check_promise_time(policy, custom_at, after=accepted_at)
        return Promised(promised, PromiseChoice.CUSTOM.value, "STAFF_SET", lines, accepted_at)

    if not codes:
        return PromiseNeedsHuman((PromiseReason.NO_LINES,), ())
    kinds = {_kind(policy, line) for line in lines}

    if choice is PromiseChoice.EXPRESS_2H:
        if kinds != {ScopeKind.FIXED} or any(
            line.scope is not TurnaroundScope.STANDARD_CLOTHES for line in lines
        ):
            raise PromiseError(
                PromiseRefusal.PROMISE_CHOICE_NOT_APPLICABLE,
                "express is for an order of standard clothing only",
            )
        at = add_opening_hours(policy, accepted_at, policy.express_hours)
        if at is None:
            return PromiseNeedsHuman((PromiseReason.TET_DATES_UNPUBLISHED,), ())
        express_lines = tuple(
            LinePromise(line.service_code, line.scope, line.sla_id, policy.express_hours)
            for line in lines
        )
        return Promised(at, choice.value, "EXPRESS_2H", express_lines, accepted_at)

    if choice in {PromiseChoice.H24, PromiseChoice.H48} and ScopeKind.RANGE_CHOICE not in kinds:
        raise PromiseError(
            PromiseRefusal.PROMISE_CHOICE_NOT_APPLICABLE,
            "24/48 h is for shoes, curtains and blankets",
        )

    unlisted = [line.service_code for line in lines if line.scope is None]
    special = [
        line.service_code
        for line in lines
        if line.scope is not None and _kind(policy, line) is ScopeKind.HUMAN
    ]
    reasons: list[PromiseReason] = []
    if special:
        reasons.append(PromiseReason.HUMAN_ETA_REQUIRED)
    if unlisted:
        reasons.append(PromiseReason.SERVICE_NOT_IN_POLICY)
    if reasons:
        needing = tuple(
            dict.fromkeys(
                line.service_code for line in lines if _kind(policy, line) is ScopeKind.HUMAN
            )
        )
        return PromiseNeedsHuman(tuple(reasons), needing)

    counted: list[LinePromise] = []
    for line in lines:
        rule = policy.scopes[line.scope] if line.scope is not None else None
        assert rule is not None
        if rule.kind is ScopeKind.FIXED:
            assert rule.hours is not None
            counted.append(LinePromise(line.service_code, line.scope, line.sla_id, rule.hours))
        else:
            counted.append(
                LinePromise(
                    line.service_code,
                    line.scope,
                    line.sla_id,
                    _range_hours(rule, choice),
                    CALENDAR_HOURS,
                )
            )

    latest: datetime | None = None
    latest_line: LinePromise | None = None
    for line in counted:
        assert line.hours is not None
        at = (
            add_calendar_hours(policy, accepted_at, line.hours)
            if line.counting == CALENDAR_HOURS
            else add_opening_hours(policy, accepted_at, line.hours)
        )
        if at is None:
            return PromiseNeedsHuman((PromiseReason.TET_DATES_UNPUBLISHED,), ())
        if latest is None or at > latest:
            latest, latest_line = at, line
    assert latest is not None and latest_line is not None and latest_line.sla_id is not None
    uses_range = ScopeKind.RANGE_CHOICE in kinds
    basis = (
        (choice.value if choice is not None else PromiseChoice.H48.value) if uses_range else "RULE"
    )
    return Promised(latest, basis, latest_line.sla_id, tuple(counted), accepted_at)


def promise_options(
    policy: TurnaroundPolicy, *, accepted_at: datetime, service_codes: Iterable[str]
) -> PromiseOptions:
    """What *Nhận đồ* would promise if pressed at `accepted_at`, and what the person may choose."""

    codes = tuple(service_codes)
    default = compute_promise(policy, accepted_at=accepted_at, service_codes=codes)
    lines = tuple(_line(policy, code) for code in codes)
    kinds = {_kind(policy, line) for line in lines}
    options: list[ChoiceOption] = []
    if isinstance(default, PromiseNeedsHuman):
        requirement = PromiseRequirement.CUSTOM
        options.append(ChoiceOption(PromiseChoice.CUSTOM, None))
        return PromiseOptions(
            requirement=requirement,
            default_promised_at=None,
            default_choice=None,
            choices=tuple(options),
            reason_codes=default.reason_codes,
            human_service_codes=default.service_codes,
        )
    if ScopeKind.RANGE_CHOICE in kinds:
        requirement = PromiseRequirement.RANGE_CHOICE
        for choice in (PromiseChoice.H24, PromiseChoice.H48):
            found = compute_promise(
                policy, accepted_at=accepted_at, service_codes=codes, choice=choice
            )
            if isinstance(found, Promised):
                options.append(ChoiceOption(choice, found.promised_at))
        default_choice: PromiseChoice | None = PromiseChoice.H48
    else:
        requirement = PromiseRequirement.NONE
        default_choice = None
        try:
            express = compute_promise(
                policy,
                accepted_at=accepted_at,
                service_codes=codes,
                choice=PromiseChoice.EXPRESS_2H,
            )
        except PromiseError:
            express = None
        if isinstance(express, Promised):
            options.append(ChoiceOption(PromiseChoice.EXPRESS_2H, express.promised_at))
    options.append(ChoiceOption(PromiseChoice.CUSTOM, None))
    return PromiseOptions(
        requirement=requirement,
        default_promised_at=default.promised_at,
        default_choice=default_choice,
        choices=tuple(options),
        reason_codes=(),
        human_service_codes=(),
    )


def promise_state(
    promise_at: datetime | None, *, ready_at: datetime | None, now: datetime
) -> PromiseState | None:
    """The order against `promise_at` at `now`: None without a promise.

    Finished laundry is `MET` or `MISSED` by when it was reported ready; unfinished laundry is
    `LATE` past the promise, `DUE_SOON` within `DUE_SOON` of it, `ON_TRACK` otherwise. A rewash
    clears the ready time, so the order is judged as unfinished again -- the truth.
    """

    if promise_at is None:
        return None
    _require_aware(promise_at)
    _require_aware(now)
    if ready_at is not None:
        _require_aware(ready_at)
        return PromiseState.MET if ready_at <= promise_at else PromiseState.MISSED
    if now > promise_at:
        return PromiseState.LATE
    if promise_at - now <= DUE_SOON:
        return PromiseState.DUE_SOON
    return PromiseState.ON_TRACK


@dataclass(frozen=True, slots=True)
class PromiseFigures:
    """An in-production order measured against its own promise, as the SLA board shows it.

    The same shape `evaluate_production_sla` gives a board row, so a promised order and one under
    the stated rule read alike: which side of the mark it is on is carried by which duration is
    non-zero, never by a sign. The clock stops at `ready_at` exactly as `0037` stopped the rule's.
    """

    #: `PENDING`, `MET` or `BREACHED` -- the board's vocabulary.
    outcome: str
    elapsed_microseconds: int
    remaining_microseconds: int
    breach_microseconds: int


def promise_figures(
    promise_at: datetime, *, accepted_at: datetime, ready_at: datetime | None, now: datetime
) -> PromiseFigures:
    """Where an accepted order stands against `promise_at`: time used, time left, time over."""

    for value in (promise_at, accepted_at, now):
        _require_aware(value)
    comparison = ready_at if ready_at is not None else now
    _require_aware(comparison)
    elapsed = max(0, _microseconds(comparison - accepted_at))
    remaining = max(0, _microseconds(promise_at - comparison))
    breach = max(0, _microseconds(comparison - promise_at))
    finished = "MET" if ready_at is not None else "PENDING"
    return PromiseFigures("BREACHED" if breach else finished, elapsed, remaining, breach)


def met_first_promise(first_promise_at: datetime, ready_at: datetime) -> bool:
    """The on-time figure's rule: ready at or before the FIRST promise, whatever was re-promised."""

    _require_aware(first_promise_at)
    _require_aware(ready_at)
    return ready_at <= first_promise_at


# --- helpers ----------------------------------------------------------------------------------


def _line(policy: TurnaroundPolicy, code: str) -> LinePromise:
    scope = policy.services.get(code)
    if scope is None:
        return LinePromise(code, None, None, None)
    rule = policy.scopes[scope]
    return LinePromise(code, scope, rule.sla_id, rule.hours)


def _kind(policy: TurnaroundPolicy, line: LinePromise) -> ScopeKind:
    if line.scope is None:
        return ScopeKind.HUMAN
    return policy.scopes[line.scope].kind


def _range_hours(rule: ScopeRule, choice: PromiseChoice | None) -> int:
    wanted = {PromiseChoice.H24: 24, PromiseChoice.H48: 48}.get(choice) if choice else None
    hours = rule.default_hours if wanted is None else wanted
    if hours is None or hours not in rule.choices_hours:
        raise PromiseError(PromiseRefusal.PROMISE_CHOICE_NOT_APPLICABLE)
    return hours


def _scopes(value: Mapping[str, Any]) -> dict[TurnaroundScope, ScopeRule]:
    scopes: dict[TurnaroundScope, ScopeRule] = {}
    for key, item in value.items():
        scope = TurnaroundScope(str(key))
        if not isinstance(item, Mapping):
            raise TurnaroundPolicyError("a scope rule is not an object")
        sla_id = str(item["sla_id"])
        if sla_id != SLA_ID_BY_SCOPE[scope]:
            raise TurnaroundPolicyError(f"{scope.value} must cite {SLA_ID_BY_SCOPE[scope]}")
        kind = ScopeKind(str(item["kind"]))
        if kind is ScopeKind.FIXED:
            hours = item.get("hours")
            if not _positive_int(hours):
                raise TurnaroundPolicyError("a fixed scope needs positive hours")
            rule = ScopeRule(scope, sla_id, kind, hours=int(str(hours)))
        elif kind is ScopeKind.RANGE_CHOICE:
            choices = item.get("choices_hours")
            default = item.get("default_hours")
            if (
                not isinstance(choices, list)
                or not choices
                or not all(_positive_int(hour) for hour in choices)
                or list(choices) != sorted(set(choices))
                or default not in choices
            ):
                raise TurnaroundPolicyError("a range scope needs ascending choices and a default")
            rule = ScopeRule(
                scope,
                sla_id,
                kind,
                choices_hours=tuple(int(hour) for hour in choices),
                default_hours=int(str(default)),
            )
        else:
            rule = ScopeRule(scope, sla_id, kind)
        scopes[scope] = rule
    if set(scopes) != set(TurnaroundScope):
        raise TurnaroundPolicyError("every service-sla.csv scope must be stated")
    return scopes


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload[key]
    if not isinstance(value, Mapping):
        raise TurnaroundPolicyError(f"{key} is not an object")
    return value


def _list(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload[key]
    if not isinstance(value, list):
        raise TurnaroundPolicyError(f"{key} is not a list")
    return value


def _clock(value: object) -> time:
    if not isinstance(value, str) or not _CLOCK_PATTERN.fullmatch(value):
        raise TurnaroundPolicyError("a clock time is HH:MM")
    return time.fromisoformat(value)


def _date(value: object) -> date:
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise TurnaroundPolicyError("a date is YYYY-MM-DD")
    return date.fromisoformat(value)


def _month_day(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or not _MONTH_DAY_PATTERN.fullmatch(value):
        raise TurnaroundPolicyError("a yearly closed day is MM-DD")
    month, day = int(value[:2]), int(value[3:])
    date(2000, month, day)  # a leap year, so 02-29 is a real day; anything else raises
    return month, day


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _in_tet_window(day: date) -> bool:
    return TET_WINDOW_START <= (day.month, day.day) <= TET_WINDOW_END


def _floor_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("a promise instant must be timezone-aware")


def _microseconds(value: timedelta) -> int:
    return value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "CALENDAR_HOURS",
    "DUE_SOON",
    "SHOP_TIMEZONE",
    "SHOP_TIMEZONE_NAME",
    "SLA_ID_BY_SCOPE",
    "TET_DAYS",
    "TURNAROUND_DECISION_REF",
    "TURNAROUND_POLICY_CONFIG_TYPE",
    "TURNAROUND_POLICY_VERSION",
    "ChoiceOption",
    "LinePromise",
    "PromiseChangeReason",
    "PromiseChoice",
    "PromiseError",
    "PromiseFigures",
    "PromiseNeedsHuman",
    "PromiseOptions",
    "PromiseReason",
    "PromiseRefusal",
    "PromiseRequirement",
    "PromiseState",
    "Promised",
    "ScopeKind",
    "ScopeRule",
    "TurnaroundPolicy",
    "TurnaroundPolicyError",
    "TurnaroundScope",
    "add_calendar_hours",
    "add_opening_hours",
    "check_promise_time",
    "compute_promise",
    "met_first_promise",
    "parse_turnaround_policy",
    "promise_figures",
    "promise_options",
    "promise_state",
    "scope_for_service",
    "validate_tet_dates",
    "validate_turnaround_policy",
]
