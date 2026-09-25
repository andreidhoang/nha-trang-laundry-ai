"""The shop's day as versioned deterministic queries: the SLA board, the day's counts, the export.

`OPS-BOARD-001`. This module owns no rule. Every number it returns was decided somewhere else and
is carried here with the identifier of the rule that decided it, because invariant 18 says an
operational figure is produced by a versioned deterministic query and that AI may explain such a
figure but never originate one.

Three surfaces, and the reason each exists is different:

* **The SLA board** already existed and could not be worked from. `ShadowConsoleRepository
  .sla_risk_board` selects in-production orders and evaluates each through
  `evaluate_production_sla`, with migration `0037`'s clock fix inside it, and its only reachable
  path was `#/assistant` answering "N in production, M past the mark". A count is not actionable:
  staff cannot see which order, how long is left, or what to do first. So this lists the same query.
  It does not re-run SLA logic, and a second statement that computed risk would be a defect rather
  than an optimisation — it would also be a second place to lose the `0037` fix.

* **The day's counts** existed as `today_status_counts` and fed one sentence of an assistant answer.
  `#/today` never rendered them; it renders per-queue tile counts and the takings figure. So the
  genuinely absent thing was a route, not a query.

* **The export** did not exist at all, in either half. `packages/db/exports.py` is the act; this is
  the seam that owns connection lifetimes for it.

What this deliberately is not: there is no trend, no forecast, no per-staff figure, and no *doanh
thu*. The takings figure keeps its own route and its own `DEC-014` gate, untouched — it is not
re-served here, because a second route to the same money under a second gate is how a role gate
gets quietly widened.

**Honesty carried forward, not re-invented.** `SLA_POLICY` is one stated rule and `#/assistant`
already says so in Vietnamese. Per-order SLA policy is an unresolved business decision, so the board
states the rule it used in those same words and never implies the shop promised a customer anything.
The sentence lives in `assistant.sla_policy_notice_vi` and is imported by both surfaces rather than
copied, so the two cannot drift into telling staff two different things about what the shop owes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from nha_trang_laundry_db.assistant import (
    BUSINESS_TIMEZONE,
    TODAY_STATUS_COUNTS_QUERY,
    today_status_counts,
)
from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.exports import (
    ExportApprovalDisclosure,
    ExportDataset,
    ExportExecutionCommand,
    ExportRequestCommand,
    ProducedExport,
    SanitizedExportRepository,
    StoredExportRequest,
)
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.reports import ReportRepository, StoreReport
from nha_trang_laundry_db.shadow_console import (
    SLA_BOARD_DEFAULT_LIMIT,
    SLA_BOARD_MAX_LIMIT,
    ShadowConsoleRepository,
    SlaRisk,
    sla_board_query_version,
)
from nha_trang_laundry_domain.sla import ProductionSlaPolicy
from nha_trang_laundry_observability import CorrelationContext, current_correlation

from nha_trang_laundry_api.auth import AuthSettings


class OpsBoardUnavailable(RuntimeError):
    """Raised when the operations board's database is not configured."""


@dataclass(frozen=True, slots=True)
class SlaBoardPage:
    """One page of the board, with the rule that produced it attached to the page itself."""

    items: tuple[SlaRisk, ...]
    query_version: str
    policy_id: str
    policy_type: str
    policy_target_max_hours: int | None
    #: The instant the whole page was evaluated against, taken once by the repository and identical
    #: on every row. Reported per page rather than per row because that is what lets a reader check
    #: that two figures on one screen were read from the same moment.
    evaluated_at: datetime
    #: The keyset to ask for the next page with, or `None` when this page is the end of the board.
    next_accepted_at: datetime | None
    next_order_id: UUID | None


@dataclass(frozen=True, slots=True)
class DaySummary:
    """The store's own day, counted by commercial status. No money: that has its own gated route."""

    counts: tuple[tuple[str, int], ...]
    query_version: str
    business_timezone: str


class OpsBoardService:
    """Own connection lifetimes while repositories own transactional and authorization semantics."""

    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise OpsBoardUnavailable("operations board database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._console = ShadowConsoleRepository()
        self._exports = SanitizedExportRepository()

    def sla_board(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        policy: ProductionSlaPolicy,
        limit: int = SLA_BOARD_DEFAULT_LIMIT,
        after_accepted_at: datetime | None = None,
        after_order_id: UUID | None = None,
        now: datetime | None = None,
    ) -> SlaBoardPage:
        """List the existing board. The repository decides risk, membership and order.

        A keyset needs both halves or neither: a caller who sent only a timestamp would be asking
        for "everything after that instant", which is a different and lossier question than "after
        that exact order", so the mismatch is refused rather than reinterpreted.

        `next_*` is offered only when the page came back full. A short page is the end of the board,
        and handing out a cursor for it would make a console render a "more" control that fetches
        nothing.
        """
        if (after_accepted_at is None) != (after_order_id is None):
            raise ValueError("paging the SLA board needs both the timestamp and the order id")
        if not 1 <= limit <= SLA_BOARD_MAX_LIMIT:
            raise ValueError(f"the SLA board limit must be between 1 and {SLA_BOARD_MAX_LIMIT}")
        after = (
            None
            if after_accepted_at is None or after_order_id is None
            else (after_accepted_at, after_order_id)
        )
        with self._connection_factory(self._database_url) as connection:
            items = self._console.sla_risk_board(
                connection,
                store_id=store_id,
                principal=principal,
                policy=policy,
                now=now,
                limit=limit,
                after=after,
            )
        full_page = len(items) == limit
        return SlaBoardPage(
            items=items,
            # Derived from the policy this page was evaluated under, not read off a module
            # constant: the engine and the policy decide the figures as much as the statement does,
            # and a version that could not move with them would name a rule that had already
            # changed (invariant 18).
            query_version=sla_board_query_version(policy).label,
            policy_id=policy.policy_id,
            policy_type=str(policy.policy_type.value),
            policy_target_max_hours=policy.target_max_hours,
            # An empty board still reports the instant it was empty at. Omitting the field and
            # letting a console fill in its own clock would put a browser's idea of "now" beside a
            # server's figures.
            evaluated_at=items[0].evaluated_at if items else (now or datetime.now(UTC)),
            next_accepted_at=items[-1].production_accepted_at if full_page else None,
            next_order_id=items[-1].order_id if full_page else None,
        )

    def day_summary(self, *, store_id: UUID, principal: StaffPrincipal) -> DaySummary:
        """The day's counts by commercial status, from the query the assistant already uses."""
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            counts = today_status_counts(cursor, store_id=store_id, principal=principal)
        return DaySummary(
            counts=counts,
            query_version=TODAY_STATUS_COUNTS_QUERY.label,
            business_timezone=BUSINESS_TIMEZONE,
        )

    def store_report(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        policy: ProductionSlaPolicy,
        from_date: date,
        to_date: date,
        as_of: datetime | None = None,
    ) -> StoreReport:
        """The owner's numbers for a window of shop-local days (`REPORT-DASHBOARD-001`).

        One read-only cursor, one statement for the counted facts and one for the on-time
        population; the repository checks role, MFA and membership before either runs. `policy` is
        the same constant the SLA board is evaluated under, passed by the route, so the report's
        on-time figure and the board cannot be computed under two different rules.
        """
        with (
            self._connection_factory(self._database_url) as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            # One snapshot for both statements: the counted facts and the on-time population must
            # describe the same instant, or an order finishing between them is half in the report.
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            return ReportRepository.store_report(
                cursor,
                store_id=store_id,
                principal=principal,
                policy=policy,
                from_date=from_date,
                to_date=to_date,
                as_of=as_of or datetime.now(UTC),
            )

    def request_export(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        business_date: date,
        idempotency_key: str,
    ) -> StoredExportRequest:
        """Record what somebody wants exported. Nothing leaves the system on this call."""
        with self._connection_factory(self._database_url) as connection:
            return self._exports.request(
                connection,
                ExportRequestCommand(
                    store_id=store_id,
                    dataset=ExportDataset.STORE_DAY_ORDERS_V1,
                    business_date=business_date,
                    principal=principal,
                    correlation_id=_correlation_id(),
                    idempotency_key=idempotency_key,
                ),
            )

    def read_export_for_approval(
        self, *, approval_id: UUID, principal: StaffPrincipal
    ) -> ExportApprovalDisclosure | None:
        """What one export envelope authorises, for the owner about to decide it.

        A read, on its own cursor, taking nothing from the caller but the approval identifier. The
        store is read off the request row inside the repository and membership is required against
        that value, so this route cannot be used to ask whether an approval belongs to a shop the
        caller is not in.
        """
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._exports.read_for_approval(
                cursor, approval_id=approval_id, principal=principal
            )

    def execute_export(
        self,
        *,
        store_id: UUID,
        export_request_id: UUID,
        approval_request_id: UUID,
        principal: StaffPrincipal,
    ) -> ProducedExport:
        """Release the file once, against an approval the repository proves names this request."""
        with self._connection_factory(self._database_url) as connection:
            return self._exports.execute(
                connection,
                ExportExecutionCommand(
                    export_request_id=export_request_id,
                    approval_request_id=approval_request_id,
                    principal=principal,
                    correlation_id=_correlation_id(),
                    expected_store_id=store_id,
                ),
            )


def _correlation_id() -> UUID:
    """The request's own correlation id, so an export's audit row joins the HTTP call that made it.

    Taken from the ambient correlation context rather than minted here: the middleware already put
    one on this request, and a fresh UUID would break the join the audit trail exists to support.
    """
    context = current_correlation()
    return (
        context.correlation_id if context is not None else CorrelationContext.new().correlation_id
    )


__all__ = [
    "DaySummary",
    "OpsBoardService",
    "OpsBoardUnavailable",
    "SlaBoardPage",
]
