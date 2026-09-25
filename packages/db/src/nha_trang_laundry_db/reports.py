"""The owner's numbers over a window of days, as one versioned read model (`REPORT-DASHBOARD-001`).

`FR-RPT-001` asks for a dashboard of the order funnel, the on-time rate, rewash and incidents, and
money; `FR-RPT-005` asks that every figure on it carry its numerator, its denominator, its time
window and a data-quality status. This module is the only place those figures are computed, and it
computes no rate: it returns the two numbers and lets the reader see both. A percentage, where one
is shown at all, is the console formatting this fraction -- never a figure this module published.

**Every input is a stored fact, and every one is read where it is already the authority.**

* Orders created: `orders.created_at`, the server's clock at creation (`COUNTER-DEFECTS-001` stopped
  callers backdating it).
* Orders completed / cancelled: the `ORDER_STATE_TRANSITIONED` event on the commercial dimension
  that moved the order there, dated by its `occurred_at`. `orders.closed_at` holds the completion
  instant too, but a cancellation has no column of its own, so both come from the one ledger.
* Rewash: defined on the *transitions*, never on a step name, so a rewash recorded through the
  per-axis production route before `ORDER-STEPS-002` and one recorded through its named `REWASH`
  step are the same fact. A rewash is a production move out of `EXCEPTION` to a state *earlier in
  `PRODUCTION_SEQUENCE`* than the one the exception interrupted -- the backward move `DEC-024`
  made legal precisely so a stain found at quality check could be washed again. An exception that
  resumes where it was interrupted (a machine paused, then restarted) is not a rewash: nothing is
  washed twice. The interrupted state is the target of the production event just before the
  `EXCEPTION` event, because the domain admits an exception only from a working state
  (`transition_production`), never from `ON_HOLD`.
* Reached quality check: a production move into `QUALITY_CHECK`.
* On time (internal): the SLA board's own rule, applied by the SLA board's own engine to the SLA
  board's own clock -- `evaluate_production_sla(policy, production_accepted_at, ready_at_store=
  production_ready_at)`, the exact call `ShadowConsoleRepository.sla_risk_board` makes. The
  population is the orders whose recorded completion (`production_ready_at`, the clock stop `0037`
  added and the rewash correction in `OrderRepository.transition` keeps honest) falls in the
  window. Nothing here restates the eight hours: the version below hashes the board's version, so
  the day the board's rule moves, this identifier moves with it.
  **`PROMISE-001` (`DEC-037`)**: an order with a promise is on time when it was ready at or before
  its **first** promise (`orders.promised_ready_at`, immutable), whatever it was re-promised to --
  so re-promising cannot improve the figure (`promise.met_first_promise`). Only an order taken
  before the owner published a turnaround policy is still judged by the stated rule, and the figure
  is `COMPLETE` when none of its orders were, `RULE_ASSUMED` otherwise; `rule_assumed` says how many
  of the denominator the stated rule judged.
* Complaints: `customer_incidents.opened_at`, every category.
* Money: `order_settlements` in, `order_refunds` out, each on its own local day -- the two ledgers
  and the two predicates of `collected-today-v2`, widened from one day to a range. Summed by
  PostgreSQL over BIGINT columns; Python never adds an amount.
* Remedies: `remedy_proposals` that reached `EXECUTED`, dated by `executed_at`, by kind, with the
  credit value the database sums. A free rewash carries no money, so its amount is `None`, not 0.

**The day boundary is the shop's**, `Asia/Ho_Chi_Minh`, the one `settlement.BUSINESS_TIMEZONE`
names: a window `[from, to]` of calendar days means `[from 00:00 local, (to + 1) 00:00 local)`, and
each fact is filed under `(instant AT TIME ZONE zone)::date`. A settlement at 23:59:59 local is in
that day; one a microsecond after midnight is in the next.

**Margin is not here** (`FR-RPT-002`). No cost is captured anywhere in the schema -- machine
minutes, chemicals, labour and delivery cost all wait on `SHOP-INSTRUMENT-001` -- and a margin
computed without them would be the remaining 70 % of a price called profit, which `FR-RPT-002`
forbids by name. The report says so rather than leaving the tile out silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from nha_trang_laundry_domain.catalog import SlaOutcome
from nha_trang_laundry_domain.orders import PRODUCTION_SEQUENCE
from nha_trang_laundry_domain.promise import met_first_promise
from nha_trang_laundry_domain.sla import ProductionSlaPolicy, evaluate_production_sla

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.query_version import QueryVersion, query_version
from nha_trang_laundry_db.settlement import BUSINESS_TIMEZONE
from nha_trang_laundry_db.shadow_console import sla_board_query_version
from nha_trang_laundry_db.store_access import require_store_membership

#: `COUNTER_COMPLETENESS_SPEC_V1.md` §3.5. The four roles that read the owner's numbers. `OPERATOR`
#: is refused on purpose: the counter sees today's takings (`DEC-014`) and today's counts, and the
#: period report is the owner's, the approver's and the books' -- not a counter screen.
REPORT_READ_ROLES: Final = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.ACCOUNTANT, StaffRole.AUDITOR}
)

#: The longest window a report answers, in shop-local calendar days (a quarter).
REPORT_MAX_DAYS: Final = 92

#: The published identifier. `report-v2:<digest>` travels with every figure (invariant 18).
#: `v2` (`PROMISE-001`): the on-time figure counts a promised order against its first promise.
REPORT_QUERY_IDENTIFIER: Final = "report-v2"

#: The production sequence the rewash rule compares positions in, as the SQL receives it.
_SEQUENCE: Final = tuple(status.value for status in PRODUCTION_SEQUENCE)


class DataQuality(StrEnum):
    """How much of a figure is measured rather than assumed (`FR-RPT-005`)."""

    #: Every input is a stored fact and the rule producing the figure is a decided one.
    COMPLETE = "COMPLETE"
    #: The inputs are stored facts, but the rule applied to them is one stated internal rule
    #: standing in for a missing fact (here: an order taken before the owner published the
    #: turnaround policy has no promise, so the SLA board's stated rule judged it).
    RULE_ASSUMED = "RULE_ASSUMED"


class ReportKey(StrEnum):
    ORDERS_CREATED = "ORDERS_CREATED"
    ORDERS_COMPLETED = "ORDERS_COMPLETED"
    ORDERS_CANCELLED = "ORDERS_CANCELLED"
    ON_TIME_INTERNAL = "ON_TIME_INTERNAL"
    REWASH = "REWASH"
    COMPLAINTS = "COMPLAINTS"
    MONEY_COLLECTED = "MONEY_COLLECTED"
    MONEY_REFUNDED = "MONEY_REFUNDED"
    MONEY_NET = "MONEY_NET"
    REMEDIES_EXECUTED = "REMEDIES_EXECUTED"


#: Remedy kinds in the order the report lists them. The schema's CHECK is the authority; a kind
#: added there and not here is reported under nothing, which the pinned digest makes visible.
REMEDY_KINDS: Final = ("FREE_REWASH", "DAMAGE_COMPENSATION", "LATE_DELIVERY_CREDIT", "LOST_ITEM")

#: Every fact the report counts, dated and filed under a shop-local day, in one statement so that
#: one edit moves one rule. `GROUPING SETS ((day), ())` gives the per-day rows and the window row
#: from the same aggregation, so a distinct count over the window is a distinct count over the
#: window -- an order rewashed on two days is one rewashed order in the total, not two -- and the
#: summary can never disagree with the daily list about what was counted.
#:
#: `days` is generated, and joined LEFT, so a day with nothing on it is a row of zeros rather than a
#: missing row: a gap in a bar list reads as "no data", and zero orders is a real answer.
_REPORT_SQL = """
    WITH bounds AS (
        SELECT (%(from_date)s::date)::timestamp AT TIME ZONE %(zone)s AS lower_at,
               (%(to_date)s::date + 1)::timestamp AT TIME ZONE %(zone)s AS upper_at
    ), days AS (
        SELECT generate_series(%(from_date)s::date, %(to_date)s::date, interval '1 day')::date
               AS day
    ), transitions AS (
        SELECT e.aggregate_id AS order_id,
               e.occurred_at,
               e.payload->>'dimension' AS dimension,
               e.payload->>'target' AS target,
               lag(e.payload->>'target', 1) OVER axis AS previous_target,
               lag(e.payload->>'target', 2) OVER axis AS interrupted_target
        FROM domain_events e
        JOIN orders o ON o.id = e.aggregate_id AND o.store_id = %(store)s
        CROSS JOIN bounds b
        WHERE e.aggregate_type = 'ORDER'
          AND e.event_type = 'ORDER_STATE_TRANSITIONED'
          AND e.occurred_at < b.upper_at
        WINDOW axis AS (
            PARTITION BY e.aggregate_id, e.payload->>'dimension' ORDER BY e.aggregate_version
        )
    ), facts AS (
        SELECT 'ORDERS_CREATED' AS fact, o.created_at AS at, o.id AS subject,
               NULL::bigint AS amount
        FROM orders o
        WHERE o.store_id = %(store)s
        UNION ALL
        SELECT CASE t.target WHEN 'COMPLETED' THEN 'ORDERS_COMPLETED' ELSE 'ORDERS_CANCELLED' END,
               t.occurred_at, t.order_id, NULL::bigint
        FROM transitions t
        WHERE t.dimension = 'commercial' AND t.target IN ('COMPLETED', 'CANCELLED')
        UNION ALL
        SELECT 'ORDERS_REACHED_QUALITY_CHECK', t.occurred_at, t.order_id, NULL::bigint
        FROM transitions t
        WHERE t.dimension = 'production' AND t.target = 'QUALITY_CHECK'
        UNION ALL
        SELECT 'ORDERS_REWASHED', t.occurred_at, t.order_id, NULL::bigint
        FROM transitions t
        WHERE t.dimension = 'production'
          AND t.previous_target = 'EXCEPTION'
          AND array_position(%(sequence)s::text[], t.target)
              < array_position(%(sequence)s::text[], t.interrupted_target)
        UNION ALL
        SELECT 'INCIDENTS_OPENED', i.opened_at, i.id, NULL::bigint
        FROM customer_incidents i
        WHERE i.store_id = %(store)s
        UNION ALL
        SELECT 'MONEY_COLLECTED', s.attested_at, s.id, s.paid_amount_vnd
        FROM order_settlements s
        WHERE s.store_id = %(store)s
        UNION ALL
        SELECT 'MONEY_REFUNDED', r.refunded_at, r.id, r.refunded_amount_vnd
        FROM order_refunds r
        WHERE r.store_id = %(store)s AND r.direction = 'TO_CUSTOMER'
        UNION ALL
        SELECT 'REMEDY_' || p.kind, p.executed_at, p.id, p.amount_vnd
        FROM remedy_proposals p
        WHERE p.store_id = %(store)s AND p.status = 'EXECUTED'
    ), windowed AS (
        SELECT f.fact, f.subject, f.amount, (f.at AT TIME ZONE %(zone)s)::date AS day
        FROM facts f
        CROSS JOIN bounds b
        WHERE f.at >= b.lower_at AND f.at < b.upper_at
    ), counted AS (
        SELECT d.day,
               GROUPING(d.day) = 1 AS is_window,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'ORDERS_CREATED') AS created,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'ORDERS_COMPLETED') AS completed,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'ORDERS_CANCELLED') AS cancelled,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'ORDERS_REACHED_QUALITY_CHECK')
                   AS reached_quality_check,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'ORDERS_REWASHED') AS rewashed,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'INCIDENTS_OPENED') AS incidents,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'MONEY_COLLECTED')
                   AS settlement_count,
               coalesce(sum(w.amount) FILTER (WHERE w.fact = 'MONEY_COLLECTED'), 0)
                   AS collected_vnd,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'MONEY_REFUNDED') AS refund_count,
               coalesce(sum(w.amount) FILTER (WHERE w.fact = 'MONEY_REFUNDED'), 0)
                   AS refunded_vnd,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'REMEDY_FREE_REWASH')
                   AS remedy_free_rewash,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'REMEDY_DAMAGE_COMPENSATION')
                   AS remedy_damage,
               coalesce(sum(w.amount) FILTER (WHERE w.fact = 'REMEDY_DAMAGE_COMPENSATION'), 0)
                   AS remedy_damage_vnd,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'REMEDY_LATE_DELIVERY_CREDIT')
                   AS remedy_late,
               coalesce(sum(w.amount) FILTER (WHERE w.fact = 'REMEDY_LATE_DELIVERY_CREDIT'), 0)
                   AS remedy_late_vnd,
               count(DISTINCT w.subject) FILTER (WHERE w.fact = 'REMEDY_LOST_ITEM')
                   AS remedy_lost,
               coalesce(sum(w.amount) FILTER (WHERE w.fact = 'REMEDY_LOST_ITEM'), 0)
                   AS remedy_lost_vnd,
               count(DISTINCT w.subject) FILTER (WHERE left(w.fact, 7) = 'REMEDY_') AS remedies,
               coalesce(sum(w.amount) FILTER (WHERE left(w.fact, 7) = 'REMEDY_'), 0)
                   AS remedies_vnd
        FROM days d
        LEFT JOIN windowed w ON w.day = d.day
        GROUP BY GROUPING SETS ((d.day), ())
    )
    SELECT day, is_window, created, completed, cancelled, reached_quality_check, rewashed,
           incidents, settlement_count, collected_vnd, refund_count, refunded_vnd,
           abs(collected_vnd - refunded_vnd) AS net_vnd,
           CASE WHEN collected_vnd >= refunded_vnd THEN 'IN' ELSE 'OUT' END AS net_direction,
           remedy_free_rewash, remedy_damage, remedy_damage_vnd, remedy_late, remedy_late_vnd,
           remedy_lost, remedy_lost_vnd, remedies, remedies_vnd
    FROM counted
    ORDER BY is_window, day
"""

#: The on-time population: the SLA board's two clock columns, for every order whose recorded
#: completion falls in the window. Evaluated row by row by `evaluate_production_sla`, because the
#: rule lives in the engine and a `ready_at - accepted_at <= interval '8 hours'` here would be a
#: second copy of it that the board's version could not see move.
_ON_TIME_SQL = """
    SELECT id, production_accepted_at, production_ready_at,
           (production_ready_at AT TIME ZONE %(zone)s)::date AS day, promised_ready_at
    FROM orders
    WHERE store_id = %(store)s
      AND production_ready_at >= (%(from_date)s::date)::timestamp AT TIME ZONE %(zone)s
      AND production_ready_at < (%(to_date)s::date + 1)::timestamp AT TIME ZONE %(zone)s
    ORDER BY production_ready_at, id
"""


def report_query_version(policy: ProductionSlaPolicy) -> QueryVersion:
    """The version that travels with every report figure.

    Everything that can change a figure is hashed: both statements, the business timezone the day
    boundary is taken in, the production sequence the rewash rule compares positions in, the
    window ceiling, and -- for the on-time figure -- the SLA board's own version under the same
    policy, which already hashes the board's statement, the whole SLA engine and the policy. If the
    board's rule moves, the report's identifier is forced to move with it.
    """
    return query_version(
        REPORT_QUERY_IDENTIFIER,
        _REPORT_SQL,
        _ON_TIME_SQL,
        BUSINESS_TIMEZONE,
        "|".join(_SEQUENCE),
        "|".join(REMEDY_KINDS),
        str(REPORT_MAX_DAYS),
        sla_board_query_version(policy).label,
    )


class ReportAuthorizationError(PermissionError):
    """Raised when a principal may not read this store's report."""


class ReportWindowError(ValueError):
    """Raised when the requested window is not one the report answers."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ReportWindow:
    """A closed range of shop-local calendar days, and whether it reaches the current day.

    `ends_today` is true when `to` is the shop's today: the last day is still being traded, so
    its figures are "so far", not final. A window cannot end after today -- nothing is recorded
    there -- and a figure of zero for a day that has not happened would read as a quiet day.
    """

    from_date: date
    to_date: date
    days: int
    business_timezone: str
    ends_today: bool


@dataclass(frozen=True, slots=True)
class ReportFigure:
    """One KPI, in the shape `FR-RPT-005` demands. No rate: two integers and their provenance."""

    key: ReportKey
    numerator: int
    denominator: int | None
    #: The KPI whose numerator is this one's denominator, so a reader can find where it came from.
    denominator_key: ReportKey | None
    unit: str
    data_quality: DataQuality
    #: `MONEY_NET` only: the drawer's direction, `IN` (including no change) or `OUT`.
    direction: str | None = None
    #: `REMEDIES_EXECUTED` only: counts and credit value per kind (`None` value = the kind moves
    #: no money, which is not the same as zero).
    by_kind: tuple[tuple[str, int, int | None], ...] | None = None
    #: `MONEY_COLLECTED` / `MONEY_REFUNDED`: how many ledger rows the amount sums.
    entries: int | None = None
    #: `REMEDIES_EXECUTED`: the total credit value the executed remedies carried.
    amount_vnd: int | None = None
    #: `ON_TIME_INTERNAL` only (`PROMISE-001`): how many of the denominator had no promise and were
    #: judged by the stated rule. Zero is what makes the figure `COMPLETE`.
    rule_assumed: int | None = None


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    """The figures for one window: the whole report, or one day of it."""

    from_date: date
    to_date: date
    figures: tuple[ReportFigure, ...]


@dataclass(frozen=True, slots=True)
class StoreReport:
    store_id: UUID
    window: ReportWindow
    summary: ReportPeriod
    days: tuple[ReportPeriod, ...]
    query_version: str
    policy_id: str
    policy_type: str
    policy_target_max_hours: int | None
    #: The instant the report was read at. The figures of a window that ends today are "so far".
    evaluated_at: datetime


def validate_window(from_date: date, to_date: date, *, today: date) -> ReportWindow:
    """Refuse a window the report does not answer; otherwise describe it.

    Pure: `today` is the caller's shop-local date, passed in, so the rule can be tested at the
    exact boundary and reproduced for a named day.
    """
    if from_date > to_date:
        raise ReportWindowError(
            "the window starts after it ends", reason_code="REPORT_WINDOW_REVERSED"
        )
    if to_date > today:
        raise ReportWindowError(
            "the window ends after the shop's today; nothing is recorded there yet",
            reason_code="REPORT_WINDOW_IN_FUTURE",
        )
    days = (to_date - from_date).days + 1
    if days > REPORT_MAX_DAYS:
        raise ReportWindowError(
            f"the window is {days} days; a report covers at most {REPORT_MAX_DAYS}",
            reason_code="REPORT_WINDOW_TOO_LONG",
        )
    return ReportWindow(
        from_date=from_date,
        to_date=to_date,
        days=days,
        business_timezone=BUSINESS_TIMEZONE,
        ends_today=to_date == today,
    )


def shop_today(moment: datetime) -> date:
    """The shop's calendar day at an instant, in the one business timezone."""
    return moment.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()


class ReportRepository:
    """Read one store's report. Role, MFA and membership are checked here, not only at a route."""

    @staticmethod
    def store_report(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        policy: ProductionSlaPolicy,
        from_date: date,
        to_date: date,
        as_of: datetime,
    ) -> StoreReport:
        """The window's figures and one row per day, from one statement and one population.

        `as_of` is the instant the read is taken at and is a parameter, never `now()` inside the
        rule: it decides only what "today" is for the window check and what `evaluated_at` says.
        No figure depends on it -- the on-time evaluation is made at each order's own completion
        instant, where the engine's outcome is fixed -- so the same window read a year later
        returns the same numbers under the same version.
        """
        if not principal.roles & REPORT_READ_ROLES or not principal.mfa_verified:
            raise ReportAuthorizationError(
                "the report requires an owner, approver, accountant or auditor with MFA"
            )
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=ReportAuthorizationError,
        )
        window = validate_window(from_date, to_date, today=shop_today(as_of))
        parameters = {
            "store": store_id,
            "zone": BUSINESS_TIMEZONE,
            "from_date": window.from_date,
            "to_date": window.to_date,
            "sequence": list(_SEQUENCE),
        }
        cursor.execute(_REPORT_SQL, parameters)
        rows = cursor.fetchall()
        cursor.execute(_ON_TIME_SQL, parameters)
        population = cursor.fetchall()

        on_time_by_day: dict[date, tuple[int, int, int]] = {}
        on_time_window = (0, 0, 0)
        for row in population:
            accepted_at, ready_at, day, first_promise = row[1], row[2], row[3], row[4]
            if first_promise is not None:
                # PROMISE-001: against the order's FIRST promise, never a later one.
                met = 1 if met_first_promise(first_promise, ready_at) else 0
                assumed = 0
            else:
                result = evaluate_production_sla(
                    policy,
                    # The completion instant, not the wall clock: for an order whose clock has
                    # stopped the outcome is fixed there, and a report must not depend on when it
                    # was read.
                    evaluated_at=ready_at,
                    production_accepted_at=accepted_at,
                    ready_at_store=ready_at,
                )
                met = 1 if result.outcome is SlaOutcome.MET else 0
                assumed = 1
            day_met, day_total, day_assumed = on_time_by_day.get(day, (0, 0, 0))
            on_time_by_day[day] = (day_met + met, day_total + 1, day_assumed + assumed)
            on_time_window = (
                on_time_window[0] + met,
                on_time_window[1] + 1,
                on_time_window[2] + assumed,
            )

        summary: ReportPeriod | None = None
        daily: list[ReportPeriod] = []
        for row in rows:
            if bool(row[1]):
                summary = _period(row, window.from_date, window.to_date, on_time_window)
            else:
                day = row[0]
                daily.append(_period(row, day, day, on_time_by_day.get(day, (0, 0, 0))))
        if summary is None:  # pragma: no cover - GROUPING SETS always yields the () row
            raise RuntimeError("the report statement returned no window row")
        return StoreReport(
            store_id=store_id,
            window=window,
            summary=summary,
            days=tuple(daily),
            query_version=report_query_version(policy).label,
            policy_id=policy.policy_id,
            policy_type=str(policy.policy_type.value),
            policy_target_max_hours=policy.target_max_hours,
            evaluated_at=as_of,
        )


def _period(
    row: Any, from_date: date, to_date: date, on_time: tuple[int, int, int]
) -> ReportPeriod:
    """One row of the statement as figures. Copies integers; adds, divides and rounds nothing."""
    created, completed, cancelled = int(row[2]), int(row[3]), int(row[4])
    reached_quality_check, rewashed, incidents = int(row[5]), int(row[6]), int(row[7])
    direction = str(row[13])
    if direction not in ("IN", "OUT"):  # pragma: no cover - the CASE has exactly two arms
        raise RuntimeError("the drawer direction is not one the rule produces")
    by_kind = (
        ("FREE_REWASH", int(row[14]), None),
        ("DAMAGE_COMPENSATION", int(row[15]), int(row[16])),
        ("LATE_DELIVERY_CREDIT", int(row[17]), int(row[18])),
        ("LOST_ITEM", int(row[19]), int(row[20])),
    )
    figures = (
        ReportFigure(ReportKey.ORDERS_CREATED, created, None, None, "ORDERS", DataQuality.COMPLETE),
        ReportFigure(
            ReportKey.ORDERS_COMPLETED,
            completed,
            created,
            ReportKey.ORDERS_CREATED,
            "ORDERS",
            DataQuality.COMPLETE,
        ),
        ReportFigure(
            ReportKey.ORDERS_CANCELLED,
            cancelled,
            created,
            ReportKey.ORDERS_CREATED,
            "ORDERS",
            DataQuality.COMPLETE,
        ),
        ReportFigure(
            ReportKey.ON_TIME_INTERNAL,
            on_time[0],
            on_time[1],
            None,
            "ORDERS",
            DataQuality.RULE_ASSUMED if on_time[2] else DataQuality.COMPLETE,
            rule_assumed=on_time[2],
        ),
        ReportFigure(
            ReportKey.REWASH, rewashed, reached_quality_check, None, "ORDERS", DataQuality.COMPLETE
        ),
        ReportFigure(
            ReportKey.COMPLAINTS,
            incidents,
            completed,
            ReportKey.ORDERS_COMPLETED,
            "INCIDENTS",
            DataQuality.COMPLETE,
        ),
        ReportFigure(
            ReportKey.MONEY_COLLECTED,
            int(row[9]),
            None,
            None,
            "VND",
            DataQuality.COMPLETE,
            entries=int(row[8]),
        ),
        ReportFigure(
            ReportKey.MONEY_REFUNDED,
            int(row[11]),
            None,
            None,
            "VND",
            DataQuality.COMPLETE,
            entries=int(row[10]),
        ),
        ReportFigure(
            ReportKey.MONEY_NET,
            int(row[12]),
            None,
            None,
            "VND",
            DataQuality.COMPLETE,
            direction=direction,
        ),
        ReportFigure(
            ReportKey.REMEDIES_EXECUTED,
            int(row[21]),
            None,
            None,
            "REMEDIES",
            DataQuality.COMPLETE,
            by_kind=by_kind,
            amount_vnd=int(row[22]),
        ),
    )
    return ReportPeriod(from_date=from_date, to_date=to_date, figures=figures)


def window_days(from_date: date, to_date: date) -> tuple[date, ...]:
    """Every calendar day of a window, in order. Used by tests to reason about the daily rows."""
    return tuple(
        from_date + timedelta(days=offset) for offset in range((to_date - from_date).days + 1)
    )


__all__ = [
    "REMEDY_KINDS",
    "REPORT_MAX_DAYS",
    "REPORT_QUERY_IDENTIFIER",
    "REPORT_READ_ROLES",
    "DataQuality",
    "ReportAuthorizationError",
    "ReportFigure",
    "ReportKey",
    "ReportPeriod",
    "ReportRepository",
    "ReportWindow",
    "ReportWindowError",
    "StoreReport",
    "report_query_version",
    "shop_today",
    "validate_window",
    "window_days",
]
