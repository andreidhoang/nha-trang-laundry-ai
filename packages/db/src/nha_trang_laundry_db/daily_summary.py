"""The owner's evening summary: its reads, one snapshot, and the template's version.

`DAILY-SUMMARY-001` (`DEC-039`). This module computes no figure of its own that another read
already computes. It gathers, in one read-only snapshot:

* **the report** (`ReportRepository.store_report`, `report-v3`) for the one-day window `[day, day]`
  -- orders taken in, completed and cancelled; finished on time against the first promise and how
  many had none; complaints opened; money in by method, refunds and the net. The report checks the
  reader's role, MFA and store membership before any statement runs, so this read inherits exactly
  the report's gate;
* **the SLA board** (`ShadowConsoleRepository.sla_risk_board`), counted by the outcome the board
  gave each row -- orders not yet handed back that are late against their promise, and those with
  no promise. The board describes the shop *now* and keeps no history, so it is read only when the
  day is the shop's today, and only for a role the board admits;
* **Sổ thu chi** for the day, through `expense_totals`, the statement the report's months use;
* **complaints open now** (`OPEN` or `UNDER_REVIEW`): the one count here with no other home, a
  single versioned statement, today only for the same reason as the board;
* two **hooks** for reads that wave-2 slices built in parallel, wired at their integration:
  `waiting_pickup_figures` counts `UNCLAIMED-001`'s waiting list (`UnclaimedRepository.
  count_waiting`: the list's own population and day rule), today only, since the list describes
  the shelf now; `accounts_due_figures` sums `PAYMENT-002`'s ledgers (`AccountRepository.
  accounts_due`), as of the day's end for a past day, since those ledgers are append-only. Each is
  read under its own source's role set, like the board, and answers in counts and money only.

The words are `nha_trang_laundry_domain.daily_summary.render_summary`'s. The version beside the
text hashes that template's rules and this module's own statements, so a changed sentence or a
changed count cannot travel under the old identifier (`test_daily_summary_repository.py` pins
the digest).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

from nha_trang_laundry_domain import daily_summary as template
from nha_trang_laundry_domain.daily_summary import (
    COMPARE_MIN_WEEKS,
    COMPARE_WEEKS,
    DAILY_SUMMARY_TEMPLATE_IDENTIFIER,
    FEE_SOON_DAYS,
    MISSING_COSTS_AFTER_DAY,
    AccountsDueFigures,
    AttentionFacts,
    BoardFigures,
    DayComparison,
    DayFigures,
    FeeSoon,
    MissingCosts,
    OmissionReason,
    OpenComplaints,
    RenderedSummary,
    SpendingFigures,
    SummaryInputs,
    Unavailable,
    WaitingFigures,
    compare_to_usual,
    render_summary,
)
from nha_trang_laundry_domain.shop_capture import ExpenseCategory, missing_core_categories
from nha_trang_laundry_domain.sla import ProductionSlaPolicy

from nha_trang_laundry_db.accounts import (
    ACCOUNT_READ_ROLES,
    ACCOUNTS_DUE_QUERY,
    AccountRepository,
)
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.promise_policy import read_published_turnaround_policy
from nha_trang_laundry_db.query_version import QueryVersion, query_version, rule_source
from nha_trang_laundry_db.reports import (
    REPORT_READ_ROLES,
    ReportAuthorizationError,
    ReportKey,
    ReportRepository,
    StoreReport,
)
from nha_trang_laundry_db.settlement import BUSINESS_TIMEZONE
from nha_trang_laundry_db.shadow_console import (
    RULE_SOURCE_ORDER_PROMISE,
    SHADOW_READ_ROLES,
    SLA_BOARD_MAX_LIMIT,
    ShadowConsoleRepository,
    SlaRisk,
    sla_board_query_version,
)
from nha_trang_laundry_db.shop_capture import _EXPENSE_TOTALS_SQL, expense_totals
from nha_trang_laundry_db.storage_fees import read_published_storage_policy
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.unclaimed import (
    UNCLAIMED_READ_ROLES,
    WAITING_COUNT_QUERY,
    WAITING_SUMMARY_THRESHOLDS,
    UnclaimedRepository,
)

#: The summary restates the report, so it is read by exactly the report's readers.
DAILY_SUMMARY_READ_ROLES: Final = REPORT_READ_ROLES

#: The board is read in full pages up to this many; beyond it the late counts would be a floor, so
#: the two board lines are omitted (`SOURCE_TRUNCATED`) rather than printed low.
BOARD_MAX_PAGES: Final = 5

#: The board outcome counted as late. The board decides it per row; this only counts.
_LATE_OUTCOME: Final = "BREACHED"

#: Complaints still being handled now. `customer_incidents.status` is `OPEN -> UNDER_REVIEW ->
#: CLOSED` (`0014`); the store predicate keeps it inside the shop the report already admitted.
#: The day's comparison with the same weekday waits for closing time on the day itself: a half day
#: against whole ones would always read "lower than usual". 20:00 is the shop's closing time
#: (`templates/business-calendar-rules.csv`, the opening hours `PROMISE-001` counts in).
COMPARE_FROM_LOCAL: Final = time(20, 0)

#: Whether the shop took any order in a month: last month's missing costs are asked for only when
#: the shop was trading then (a shop that started this month has no last month to complete).
_MONTH_TRADED_SQL: Final = """
    SELECT EXISTS (
        SELECT 1 FROM orders
        WHERE store_id = %(store)s AND created_at >= %(start)s AND created_at < %(end)s
    )
"""

_OPEN_COMPLAINTS_SQL: Final = """
    SELECT count(*)
    FROM customer_incidents
    WHERE store_id = %(store)s AND status IN ('OPEN', 'UNDER_REVIEW')
"""


def daily_summary_template_version() -> QueryVersion:
    """`daily-summary-v1:<digest>`: the template's rules and this module's own statements.

    The template module is hashed structurally (`rule_source`, docstrings and comments dropped), so
    any changed sentence, unit, order of lines or omission rule moves the digest; so do the open-
    complaint count, the spending statement, the late outcome and the board page ceiling. The
    report's and the board's own versions travel beside it in `sources`, because they move for
    reasons of their own.
    """
    return query_version(
        DAILY_SUMMARY_TEMPLATE_IDENTIFIER,
        rule_source(template),
        _OPEN_COMPLAINTS_SQL,
        _EXPENSE_TOTALS_SQL,
        _LATE_OUTCOME,
        RULE_SOURCE_ORDER_PROMISE,
        str(BOARD_MAX_PAGES),
        str(SLA_BOARD_MAX_LIMIT),
        BUSINESS_TIMEZONE,
        # v2: the two wired hooks' statements, so a changed count moves the summary's version.
        WAITING_COUNT_QUERY.label,
        ACCOUNTS_DUE_QUERY.label,
        # v3: the attention block's own rules.
        _MONTH_TRADED_SQL,
        COMPARE_FROM_LOCAL.isoformat(),
        str(COMPARE_WEEKS),
        str(COMPARE_MIN_WEEKS),
        str(FEE_SOON_DAYS),
        str(MISSING_COSTS_AFTER_DAY),
    )


@dataclass(frozen=True, slots=True)
class DailySummary:
    store_id: UUID
    day: date
    template_version: str
    #: The instant every figure was read at, in one snapshot.
    evaluated_at: datetime
    #: The day is the shop's today: its figures are "so far".
    so_far: bool
    rendered: RenderedSummary
    #: `(source, query version)` for every read model the lines came from.
    sources: tuple[tuple[str, str], ...]


class DailySummaryRepository:
    """Read one store's day and write its summary. Role, MFA and membership: the report's."""

    @staticmethod
    def read(
        connection: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        policy: ProductionSlaPolicy,
        day: date,
        as_of: datetime,
    ) -> DailySummary:
        """Every source in one `REPEATABLE READ, READ ONLY` snapshot, then the template.

        `as_of` is the caller's instant, passed in: it decides whether the day is today (and so
        whether the live sources may answer) and is the board's evaluation instant. Nothing reads
        a clock here. A refused reader, a missing membership or a day after the shop's today is
        refused by the report before any other source is read.
        """
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            # The report's gate, stated here as well as inside `store_report`, so this read's own
            # source shows the check (`test_store_scope_enumeration.py` reads it) and a later edit
            # that read a live source first could not run ahead of it.
            if not principal.roles & DAILY_SUMMARY_READ_ROLES or not principal.mfa_verified:
                raise ReportAuthorizationError(
                    "the summary requires an owner, approver, accountant or auditor with MFA"
                )
            require_store_membership(
                cursor,
                staff_user_id=principal.staff_user_id,
                store_id=store_id,
                error=ReportAuthorizationError,
            )
            report = ReportRepository.store_report(
                cursor,
                store_id=store_id,
                principal=principal,
                policy=policy,
                from_date=day,
                to_date=day,
                as_of=as_of,
            )
            live = report.window.ends_today
            sources: list[tuple[str, str]] = [("report", report.query_version)]

            board: BoardFigures | Unavailable
            if not live:
                board = Unavailable(OmissionReason.LIVE_ONLY_TODAY, "SLA_BOARD")
            elif not principal.roles & SHADOW_READ_ROLES:
                board = Unavailable(OmissionReason.ROLE_NOT_PERMITTED, "SLA_BOARD")
            else:
                board = _board(
                    connection,
                    cursor,
                    store_id=store_id,
                    principal=principal,
                    policy=policy,
                    as_of=as_of,
                )
                sources.append(("sla_board", sla_board_query_version(policy).label))

            open_complaints: OpenComplaints | Unavailable
            if live:
                cursor.execute(_OPEN_COMPLAINTS_SQL, {"store": store_id})
                row = cursor.fetchone()
                open_complaints = OpenComplaints(open_count=int(row[0]))
            else:
                open_complaints = Unavailable(OmissionReason.LIVE_ONLY_TODAY, "INCIDENTS")

            totals, total_vnd, entries = expense_totals(
                cursor, store_id=store_id, from_date=day, to_date=day
            )
            spending = SpendingFigures(
                total_vnd=total_vnd,
                entries=entries,
                by_category=tuple(
                    (total.category.value, total.amount_vnd, total.entries) for total in totals
                ),
            )
            waiting = waiting_pickup_figures(
                cursor, store_id=store_id, principal=principal, live=live, as_of=as_of
            )
            if isinstance(waiting, WaitingFigures):
                sources.append(("awaiting_pickup", WAITING_COUNT_QUERY.label))
            accounts = accounts_due_figures(
                cursor, store_id=store_id, principal=principal, day=day, live=live, as_of=as_of
            )
            if isinstance(accounts, AccountsDueFigures):
                sources.append(("accounts", ACCOUNTS_DUE_QUERY.label))
            attention = attention_facts(
                cursor,
                store_id=store_id,
                principal=principal,
                policy=policy,
                day=day,
                live=live,
                as_of=as_of,
                today=report,
            )

        rendered = render_summary(
            SummaryInputs(
                day=day,
                as_of_local=(
                    as_of.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
                    .time()
                    .replace(second=0, microsecond=0)
                    if live
                    else None
                ),
                figures=day_figures(report),
                board=board,
                open_complaints=open_complaints,
                spending=spending,
                waiting=waiting,
                accounts_due=accounts,
                attention=attention,
            )
        )
        return DailySummary(
            store_id=store_id,
            day=day,
            template_version=daily_summary_template_version().label,
            evaluated_at=as_of,
            so_far=live,
            rendered=rendered,
            sources=tuple(sources),
        )


# --- the wave-2 reads: waiting for pickup, and accounts ------------------------------------------


def waiting_pickup_figures(
    cursor: Any, *, store_id: UUID, principal: StaffPrincipal, live: bool, as_of: datetime
) -> WaitingFigures | Unavailable:
    """`UNCLAIMED-001` (`DEC-036`): laundry ready and not collected for over 20 / 60 days.

    `UnclaimedRepository.count_waiting` -- the waiting list's own population (`AWAITING_PICKUP_SQL`)
    and its own shop-day rule, never a second definition here. The list describes the shelf now
    and keeps no history (a collected order is no longer on it), so a past day's line is omitted
    `LIVE_ONLY_TODAY`, as the board's are. A reader the list does not admit (an accountant) gets
    `ROLE_NOT_PERMITTED`; more waiting orders than the count reads, `SOURCE_TRUNCATED`.
    """
    if not live:
        return Unavailable(OmissionReason.LIVE_ONLY_TODAY, "UNCLAIMED-001")
    if not principal.roles & UNCLAIMED_READ_ROLES:
        return Unavailable(OmissionReason.ROLE_NOT_PERMITTED, "UNCLAIMED-001")
    counts = UnclaimedRepository.count_waiting(
        cursor, store_id=store_id, principal=principal, as_of=as_of
    )
    if counts.truncated:
        return Unavailable(OmissionReason.SOURCE_TRUNCATED, "UNCLAIMED-001")
    over = dict(counts.over)
    low, high = WAITING_SUMMARY_THRESHOLDS
    return WaitingFigures(over_20_days=over[low], over_60_days=over[high])


def accounts_due_figures(
    cursor: Any,
    *,
    store_id: UUID,
    principal: StaffPrincipal,
    day: date,
    live: bool,
    as_of: datetime,
) -> AccountsDueFigures | Unavailable:
    """`PAYMENT-002` (`DEC-035`, B2B half): account money due on a closed statement, and overdue.

    `AccountRepository.accounts_due` -- PostgreSQL's sums over the account ledgers, split by the
    statement rule the overdue block uses. Counts and money only: no account customer's name
    reaches the summary. The ledgers are append-only, so a past day is answered as of its end.
    Omitted `NO_ACCOUNTS` while the shop has opened no account (spec §7, "feature empty"), and
    `ROLE_NOT_PERMITTED` for a reader the account card does not admit (an accountant).
    """
    if not principal.roles & ACCOUNT_READ_ROLES:
        return Unavailable(OmissionReason.ROLE_NOT_PERMITTED, "PAYMENT-002")
    day_end = datetime.combine(day + timedelta(days=1), time(0, 0), ZoneInfo(BUSINESS_TIMEZONE))
    due = AccountRepository().accounts_due(
        cursor,
        store_id=store_id,
        principal=principal,
        day=day,
        as_of=as_of if live else min(as_of, day_end),
    )
    if due.accounts == 0:
        return Unavailable(OmissionReason.NO_ACCOUNTS, "PAYMENT-002")
    return AccountsDueFigures(
        due_accounts=due.due_accounts,
        due_vnd=due.due_vnd,
        overdue_accounts=due.overdue_accounts,
        overdue_vnd=due.overdue_vnd,
    )


# --- Cần chú ý (`SUMMARY-ATTENTION-001`, `DEC-044`) -----------------------------------------------


def attention_facts(
    cursor: Any,
    *,
    store_id: UUID,
    principal: StaffPrincipal,
    policy: ProductionSlaPolicy,
    day: date,
    live: bool,
    as_of: datetime,
    today: StoreReport,
) -> AttentionFacts:
    """The attention block's sources. Each read is another module's; this only counts and compares.

    The three round-8 lists (late deliveries, reminders, invoices) are wired at their integration;
    until then they answer `SOURCE_NOT_BUILT`, which the template never lists to the owner.
    """
    not_built = Unavailable(OmissionReason.SOURCE_NOT_BUILT, "ROUND-8")
    return AttentionFacts(
        late_deliveries_undecided=not_built,
        reminders_due=not_built,
        fee_soon=fee_soon_figures(
            cursor, store_id=store_id, principal=principal, live=live, as_of=as_of
        ),
        invoices_waiting=not_built,
        comparison=day_comparison(
            cursor,
            store_id=store_id,
            principal=principal,
            policy=policy,
            day=day,
            live=live,
            as_of=as_of,
            today=today,
        ),
        missing_costs=missing_costs(cursor, store_id=store_id, day=day),
    )


def fee_soon_figures(
    cursor: Any, *, store_id: UUID, principal: StaffPrincipal, live: bool, as_of: datetime
) -> FeeSoon | Unavailable:
    """Laundry whose free-storage days end within `FEE_SOON_DAYS`, by the waiting list's own count.

    "Within three days" of a fee starting on day `free_days + 1` is waiting more than
    `free_days - 3` days and not more than `free_days`: two thresholds of the same count, subtracted
    as counts.
    """
    if not live:
        return Unavailable(OmissionReason.LIVE_ONLY_TODAY, "UNCLAIMED-001")
    if not principal.roles & UNCLAIMED_READ_ROLES:
        return Unavailable(OmissionReason.ROLE_NOT_PERMITTED, "UNCLAIMED-001")
    published = read_published_storage_policy(cursor)
    if published is None:
        return Unavailable(OmissionReason.STORAGE_POLICY_UNPUBLISHED, "UNCLAIMED-001")
    free_days = published.policy.free_days
    low = max(0, free_days - FEE_SOON_DAYS)
    counts = UnclaimedRepository.count_waiting(
        cursor, store_id=store_id, principal=principal, as_of=as_of, thresholds=(low, free_days)
    )
    if counts.truncated:
        return Unavailable(OmissionReason.SOURCE_TRUNCATED, "UNCLAIMED-001")
    over = dict(counts.over)
    return FeeSoon(count=over[low] - over[free_days], free_days=free_days)


def day_comparison(
    cursor: Any,
    *,
    store_id: UUID,
    principal: StaffPrincipal,
    policy: ProductionSlaPolicy,
    day: date,
    live: bool,
    as_of: datetime,
    today: StoreReport,
) -> DayComparison | Unavailable:
    """The day against the same weekday of the previous `COMPARE_WEEKS` weeks that had trade.

    Every figure is the report's own (`MONEY_COLLECTED`, `ORDERS_CREATED`) for a one-day window --
    the same definition the owner reads on *Báo cáo* -- so "usual" can never mean something the
    report does not. A week "had trade" when it took an order or money; a closed day is not a zero.
    """
    local = as_of.astimezone(ZoneInfo(BUSINESS_TIMEZONE))
    if live and local.time() < COMPARE_FROM_LOCAL:
        return Unavailable(OmissionReason.DAY_NOT_OVER, "REPORT")
    collected: list[int] = []
    orders: list[int] = []
    for weeks in range(1, COMPARE_WEEKS + 1):
        past = day - timedelta(days=7 * weeks)
        figures = day_figures(
            ReportRepository.store_report(
                cursor,
                store_id=store_id,
                principal=principal,
                policy=policy,
                from_date=past,
                to_date=past,
                as_of=as_of,
            )
        )
        if figures.orders_created or figures.collected_entries:
            collected.append(figures.collected_vnd)
            orders.append(figures.orders_created)
    if len(collected) < COMPARE_MIN_WEEKS:
        return Unavailable(OmissionReason.TOO_LITTLE_HISTORY, "REPORT")
    now = day_figures(today)
    return DayComparison(
        weeks_with_data=len(collected),
        collected=compare_to_usual(now.collected_vnd, tuple(collected)),
        orders=compare_to_usual(now.orders_created, tuple(orders)),
    )


def missing_costs(cursor: Any, *, store_id: UUID, day: date) -> MissingCosts | None:
    """Last month's core margin categories with no Sổ thu chi line, asked after the 10th.

    `None` before `MISSING_COSTS_AFTER_DAY`, when the shop took no order last month, or when every
    category has a line. The categories are `missing_core_categories`' -- the rule the month margin
    uses to refuse -- so the nudge and the margin can never disagree about what is missing.
    """
    if day.day <= MISSING_COSTS_AFTER_DAY:
        return None
    this_month = day.replace(day=1)
    last_end = this_month - timedelta(days=1)
    last_start = last_end.replace(day=1)
    zone = ZoneInfo(BUSINESS_TIMEZONE)
    cursor.execute(
        _MONTH_TRADED_SQL,
        {
            "store": store_id,
            "start": datetime.combine(last_start, time(0, 0), zone),
            "end": datetime.combine(this_month, time(0, 0), zone),
        },
    )
    row = cursor.fetchone()
    if row is None or not row[0]:
        return None
    totals, _, _ = expense_totals(cursor, store_id=store_id, from_date=last_start, to_date=last_end)
    recorded = frozenset(ExpenseCategory(t.category) for t in totals if t.entries)
    missing = missing_core_categories(recorded)
    if not missing:
        return None
    return MissingCosts(month=last_start, categories=tuple(c.value for c in missing))


# --- the report and the board, copied into the template's inputs ----------------------------------


def day_figures(report: StoreReport) -> DayFigures:
    """The report's one-day window figures, copied field by field. Nothing is added or divided."""

    figures = {figure.key: figure for figure in report.summary.figures}
    on_time = figures[ReportKey.ON_TIME_INTERNAL]
    collected = figures[ReportKey.MONEY_COLLECTED]
    by_method = {kind: (count, amount or 0) for kind, count, amount in collected.by_kind or ()}
    refunded = figures[ReportKey.MONEY_REFUNDED]
    net = figures[ReportKey.MONEY_NET]
    return DayFigures(
        orders_created=figures[ReportKey.ORDERS_CREATED].numerator,
        orders_completed=figures[ReportKey.ORDERS_COMPLETED].numerator,
        orders_cancelled=figures[ReportKey.ORDERS_CANCELLED].numerator,
        finished=on_time.denominator or 0,
        finished_on_time=on_time.numerator,
        finished_without_promise=on_time.rule_assumed or 0,
        stated_rule_hours=report.policy_target_max_hours,
        complaints_opened=figures[ReportKey.COMPLAINTS].numerator,
        collected_vnd=collected.numerator,
        collected_entries=collected.entries or 0,
        cash_vnd=by_method.get("TIEN_MAT", (0, 0))[1],
        cash_entries=by_method.get("TIEN_MAT", (0, 0))[0],
        transfer_vnd=by_method.get("CHUYEN_KHOAN", (0, 0))[1],
        transfer_entries=by_method.get("CHUYEN_KHOAN", (0, 0))[0],
        refunded_vnd=refunded.numerator,
        refund_entries=refunded.entries or 0,
        net_vnd=net.numerator,
        net_direction=net.direction or "IN",
    )


def _board(
    connection: Any,
    cursor: Any,
    *,
    store_id: UUID,
    principal: StaffPrincipal,
    policy: ProductionSlaPolicy,
    as_of: datetime,
) -> BoardFigures | Unavailable:
    """Every page of the board at one instant, counted by the board's own per-row outcome."""

    rows: list[SlaRisk] = []
    after: tuple[datetime, UUID] | None = None
    for _ in range(BOARD_MAX_PAGES):
        page = ShadowConsoleRepository().sla_risk_board(
            connection,
            store_id=store_id,
            principal=principal,
            policy=policy,
            now=as_of,
            limit=SLA_BOARD_MAX_LIMIT,
            after=after,
        )
        rows.extend(page)
        if len(page) < SLA_BOARD_MAX_LIMIT:
            break
        last = page[-1]
        if last.due_at is None:  # pragma: no cover - every board row carries its keyset
            return Unavailable(OmissionReason.SOURCE_TRUNCATED, "SLA_BOARD")
        after = (last.due_at, last.order_id)
    else:
        return Unavailable(OmissionReason.SOURCE_TRUNCATED, "SLA_BOARD")

    promised = [row for row in rows if row.rule_source == RULE_SOURCE_ORDER_PROMISE]
    unpromised = [row for row in rows if row.rule_source != RULE_SOURCE_ORDER_PROMISE]
    return BoardFigures(
        promised=len(promised),
        promised_late=sum(1 for row in promised if row.sla_outcome == _LATE_OUTCOME),
        unpromised=len(unpromised),
        unpromised_late=sum(1 for row in unpromised if row.sla_outcome == _LATE_OUTCOME),
        stated_rule_hours=policy.target_max_hours,
        turnaround_published=read_published_turnaround_policy(cursor) is not None,
    )


__all__ = [
    "BOARD_MAX_PAGES",
    "DAILY_SUMMARY_READ_ROLES",
    "DailySummary",
    "DailySummaryRepository",
    "accounts_due_figures",
    "attention_facts",
    "daily_summary_template_version",
    "day_comparison",
    "day_figures",
    "fee_soon_figures",
    "missing_costs",
    "waiting_pickup_figures",
]
