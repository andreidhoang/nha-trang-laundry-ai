"""Build the owner's turnaround policy document from the owner-confirmed sheets (`PROMISE-001`).

`scripts/publish_turnaround_policy.py` reads three files and one argument and hands them here:

* `templates/service-sla.csv` -- the hours per service scope, and the fastest turnaround;
* `templates/business-calendar-rules.csv` -- the opening hours, the yearly closed days, how many
  Tết days and how many ad-hoc days a year;
* `templates/services-pricebook.csv` (through the pricebook importer) -- which service belongs to
  which scope;
* this year's Tết dates, which the calendar sheet says must be entered year by year.

Nothing is invented: every number in the document is read from a sheet and checked against what
`DEC-037` grounds itself on, and a sheet that no longer says it refuses the whole build. The
document is what the owner publishes; `promise.parse_turnaround_policy` is what reads it back.
Pure: text in, a document out.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping
from datetime import date
from hashlib import sha256
from typing import Any, Final

from nha_trang_laundry_domain.promise import (
    SHOP_TIMEZONE_NAME,
    SLA_ID_BY_SCOPE,
    TET_DAYS,
    TURNAROUND_DECISION_REF,
    TURNAROUND_POLICY_VERSION,
    ScopeKind,
    TurnaroundPolicyError,
    TurnaroundScope,
    parse_turnaround_policy,
    scope_for_service,
    validate_tet_dates,
)

SLA_SOURCE: Final = "templates/service-sla.csv"
CALENDAR_SOURCE: Final = "templates/business-calendar-rules.csv"
PRICEBOOK_SOURCE: Final = "templates/services-pricebook.csv"

_OPEN_PATTERN = re.compile("(\\d{2}:\\d{2})\\s*[\u2013-]\\s*(\\d{2}:\\d{2})")
_FASTEST_PATTERN = re.compile(r"fastest (\d+)h")
_CONFIRMED = frozenset({"OWNER_CONFIRMED", "OWNER_CONFIRMED_RULE"})


def build_turnaround_policy(
    *,
    sla_csv: str,
    calendar_csv: str,
    services: Iterable[tuple[str, str]],
    tet_dates: Iterable[date],
    adhoc_dates: Iterable[date] = (),
    pricebook_sha256: str,
) -> dict[str, Any]:
    """The document to publish. Raises `TurnaroundPolicyError` when a sheet does not say it.

    `services` is `(service_code, category)` for every canonical price-list service.
    `tet_dates` is one year's six days; `adhoc_dates` the owner's extra closed days (at most the
    sheet's count per year).
    """

    sla_rows = {row["sla_id"]: row for row in _rows(sla_csv)}
    scopes: dict[str, Any] = {}
    for scope, sla_id in SLA_ID_BY_SCOPE.items():
        row = sla_rows.get(sla_id)
        if row is None:
            raise TurnaroundPolicyError(f"{SLA_SOURCE} has no {sla_id} row")
        if row["clock_start"] != "accepted_at" or row["clock_end"] != "ready_at_store":
            raise TurnaroundPolicyError(f"{sla_id} is not counted from acceptance to ready")
        if row["delivery_included"] != "false":
            raise TurnaroundPolicyError(f"{sla_id} must exclude delivery")
        scopes[scope.value] = _scope_rule(scope, sla_id, row)
    fastest = _FASTEST_PATTERN.search(sla_rows["SLA_STANDARD_CLOTHES"]["notes"])
    if fastest is None:
        raise TurnaroundPolicyError("SLA_STANDARD_CLOTHES no longer states the fastest turnaround")

    calendar = {row["rule_id"]: row for row in _rows(calendar_csv)}
    regular = _confirmed(calendar, "REGULAR_OPEN")
    hours = _OPEN_PATTERN.search(regular["description"])
    if regular["rule_type"] != "OPEN" or hours is None:
        raise TurnaroundPolicyError("REGULAR_OPEN no longer states the opening hours")
    recurring = sorted(
        row["month_day"]
        for row in calendar.values()
        if row["rule_type"] == "CLOSE" and row["month_day"] and row["status"] in _CONFIRMED
    )
    tet_rule = _confirmed(calendar, "TET_CLOSE")
    if int(tet_rule["days_count"]) != TET_DAYS:
        raise TurnaroundPolicyError(f"TET_CLOSE is {tet_rule['days_count']} days, not {TET_DAYS}")
    given = list(tet_dates)
    validate_tet_dates(given)
    tet = sorted(given)
    adhoc_rule = _confirmed(calendar, "ADHOC_CLOSE")
    adhoc = sorted(set(adhoc_dates))
    per_year: dict[int, int] = {}
    for day in adhoc:
        per_year[day.year] = per_year.get(day.year, 0) + 1
    if any(count > int(adhoc_rule["days_count"]) for count in per_year.values()):
        raise TurnaroundPolicyError(
            f"ADHOC_CLOSE allows {adhoc_rule['days_count']} ad-hoc days a year"
        )
    if set(adhoc) & set(tet):
        raise TurnaroundPolicyError("an ad-hoc day is already a Tết day")

    service_map = {
        code: scope_for_service(code, category).value for code, category in sorted(services)
    }
    if not service_map:
        raise TurnaroundPolicyError("the price list has no services")

    document: dict[str, Any] = {
        "policy_version": TURNAROUND_POLICY_VERSION,
        "decision_ref": TURNAROUND_DECISION_REF,
        "timezone": SHOP_TIMEZONE_NAME,
        "opening_hours": {"opens": hours.group(1), "closes": hours.group(2)},
        "recurring_closed": recurring,
        "tet": {str(tet[0].year): [day.isoformat() for day in tet]},
        "adhoc_closed": [day.isoformat() for day in adhoc],
        "scopes": scopes,
        "services": service_map,
        "express_hours": int(fastest.group(1)),
        "sources": {
            SLA_SOURCE: sha256(sla_csv.encode("utf-8")).hexdigest(),
            CALENDAR_SOURCE: sha256(calendar_csv.encode("utf-8")).hexdigest(),
            PRICEBOOK_SOURCE: pricebook_sha256,
        },
    }
    parse_turnaround_policy(document)
    return document


def _scope_rule(scope: TurnaroundScope, sla_id: str, row: Mapping[str, str]) -> dict[str, Any]:
    low, high = row["target_min_hours"], row["target_max_hours"]
    if row["agent_permission"] == "HUMAN_ETA_REQUIRED":
        if low or high:
            raise TurnaroundPolicyError(f"{sla_id} is HUMAN_ETA_REQUIRED and names hours")
        return {"sla_id": sla_id, "kind": ScopeKind.HUMAN.value}
    if row["status"] not in _CONFIRMED:
        raise TurnaroundPolicyError(f"{sla_id} is not owner-confirmed")
    if not low and high:
        return {"sla_id": sla_id, "kind": ScopeKind.FIXED.value, "hours": _hours(high)}
    if low and high:
        choices = sorted({_hours(low), _hours(high)})
        # DEC-037: "default 48: promise late, deliver early" -- the top of the owner's range.
        return {
            "sla_id": sla_id,
            "kind": ScopeKind.RANGE_CHOICE.value,
            "choices_hours": choices,
            "default_hours": choices[-1],
        }
    raise TurnaroundPolicyError(f"{sla_id} states no hours the rule can use")


def _hours(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise TurnaroundPolicyError("SLA hours are positive whole numbers")
    return int(value)


def _confirmed(calendar: Mapping[str, Mapping[str, str]], rule_id: str) -> Mapping[str, str]:
    row = calendar.get(rule_id)
    if row is None or row["status"] not in _CONFIRMED:
        raise TurnaroundPolicyError(f"{CALENDAR_SOURCE} has no owner-confirmed {rule_id}")
    return row


def _rows(text: str) -> list[dict[str, str]]:
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


__all__ = [
    "CALENDAR_SOURCE",
    "PRICEBOOK_SOURCE",
    "SLA_SOURCE",
    "build_turnaround_policy",
]
