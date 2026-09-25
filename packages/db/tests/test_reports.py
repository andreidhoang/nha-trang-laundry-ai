"""`REPORT-DASHBOARD-001`: the owner's numbers, on a seeded shop with known answers.

Every KPI is asserted against a count a person can check by reading the fixture below, and the
window's edges are placed on the shop's midnight on purpose: a settlement at 23:59:59 local belongs
to its day, a refund at 00:00:00 local to the next, and an order created a microsecond before the
window opens is not in it. The shop is driven through the real repositories -- orders, settlements,
refunds, incidents, remedies -- so the report reads the shapes production writes, not shapes a test
invented. The one thing a repository cannot do is backdate `orders.created_at` (the server's clock
decides it since `COUNTER-DEFECTS-001`), so the fixture moves that one column after the fact.

The rewash is recorded both ways the system has ever recorded one: a stain found at quality check
and sent back through the per-axis production route (the pre-`ORDER-STEPS-002` shape), and a
finished bag re-opened from `READY_AT_STORE` into `EXCEPTION` then `IN_PROCESS` (the transitions the
named `REWASH` step executes). An exception resumed exactly where it stopped is also recorded, and
is not a rewash.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.incidents import IncidentRepository, StaffIncidentOpenCommand
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.remedies import (
    RemedyExecutionCommand,
    RemedyProposalCommand,
    RemedyProposalRepository,
    publish_remedy_policy,
)
from nha_trang_laundry_db.reports import (
    REPORT_MAX_DAYS,
    REPORT_READ_ROLES,
    ReportAuthorizationError,
    ReportFigure,
    ReportKey,
    ReportPeriod,
    ReportRepository,
    ReportWindowError,
    StoreReport,
    report_query_version,
    shop_today,
    validate_window,
)
from nha_trang_laundry_db.settlement import SettlementCommand, SettlementRepository
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    CommitmentAuthority,
    CustodyResolution,
    FulfillmentMode,
    IntakeStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness
from nha_trang_laundry_domain.remedies import RemedyKind
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA, ProductionSlaPolicy, SlaPolicyType
from quote_test_data import accepted_quote

ROOT = Path(__file__).resolve().parents[3]
REMEDY_POLICY = json.loads(
    (ROOT / "templates" / "remedy-policy-dec-004.json").read_text(encoding="utf-8")
)
ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
READY = IntakeReadiness(True, True, True, True, True, True)
#: `make_quote_snapshot` bills 100.000 d of service and a 10.000 d delivery fee.
QUOTED_TOTAL = 110_000
#: The report is read as of a fixed instant, three days after the fixture's first day, so the
#: window is closed and every figure is reproducible whatever day the suite runs.
AS_OF = datetime.now(UTC).replace(microsecond=0)
DAY = shop_today(AS_OF) - timedelta(days=3)
NEXT = DAY + timedelta(days=1)


def local(day: date, hour: int, minute: int = 0, second: int = 0, micro: int = 0) -> datetime:
    """An instant on the shop's clock, as the UTC value the database stores."""
    return datetime(day.year, day.month, day.day, hour, minute, second, micro, tzinfo=ZONE)


#: The shop's midnight that opens `NEXT`, and the last microsecond of `DAY` before it.
MIDNIGHT = local(NEXT, 0)
LAST_MOMENT = MIDNIGHT - timedelta(microseconds=1)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _person(
    connection: Any, store_id: UUID | None, roles: frozenset[StaffRole], *, mfa: bool = True
) -> StaffPrincipal:
    """A staff member with these roles, assigned to `store_id` when one is given."""
    staff_id, assigner = uuid4(), uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_id, assigner):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                """,
                (identifier, f"oidc-{identifier}", AS_OF),
            )
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_id, role.value, AS_OF),
            )
        if store_id is not None:
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (staff_id, store_id, assigner, AS_OF),
            )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", roles, mfa, uuid4())


def _store(connection: Any) -> UUID:
    store_id = uuid4()
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng thử", created_by=None, correlation_id=uuid4()
    )
    return store_id


class _Order:
    """One order driven through the real transitions, every move at a stated shop-local instant."""

    def __init__(
        self,
        connection: Any,
        store_id: UUID,
        staff: StaffPrincipal,
        *,
        created: datetime,
        mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
    ) -> None:
        self.connection, self.staff = connection, staff
        quote_id, revision, quote, contact_id = accepted_quote(
            connection, store_id=store_id, principal=staff, fulfillment_mode=mode
        )
        stored = OrderRepository().create(
            connection,
            CreateOrderCommand(
                store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                mode,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                created,
                AcquisitionSource.WALK_IN,
            ),
        )
        self.order_id, self.version = stored.order_id, stored.row_version
        # The one column no repository lets a caller set: creation is the server's clock, and the
        # projection guard refuses a direct UPDATE. So it is moved the way
        # `test_remedy_owner_decide_postgres.py` moves a column no path writes: inside one
        # transaction that re-arms the guard before it commits.
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("ALTER TABLE orders DISABLE TRIGGER order_projection_guard")
            cursor.execute(
                "UPDATE orders SET created_at = %s WHERE id = %s", (created, self.order_id)
            )
            cursor.execute("ALTER TABLE orders ENABLE TRIGGER order_projection_guard")

    def move(self, at: datetime, **target: Any) -> _Order:
        stored = OrderRepository().transition(
            self.connection,
            OrderTransitionCommand(
                self.order_id,
                self.version,
                self.staff,
                f"step-{uuid4().hex}",
                uuid4(),
                occurred_at=at,
                **target,
            ),
        )
        self.version = stored.row_version
        return self

    def activate(self, at: datetime) -> _Order:
        self.move(at, intake_target=IntakeStatus.RECEIVED_PENDING_INSPECTION)
        self.move(
            at,
            intake_target=IntakeStatus.ACCEPTED,
            production_accepted_at=at,
            intake_readiness=READY,
        )
        for target in (
            CommercialOrderStatus.STORE_CONFIRMATION_PENDING,
            CommercialOrderStatus.CONFIRMED,
            CommercialOrderStatus.ACTIVE,
        ):
            self.move(at, commercial_target=target)
        return self

    def produce(self, *steps: tuple[ProductionStatus, datetime]) -> _Order:
        for target, at in steps:
            self.move(at, production_target=target)
        return self

    def settle(self, at: datetime, *, collected: bool) -> _Order:
        stored = SettlementRepository().record(
            self.connection,
            SettlementCommand(
                order_id=self.order_id,
                paid_amount_vnd=QUOTED_TOTAL,
                collected_by_customer=collected,
                principal=self.staff,
                correlation_id=uuid4(),
                attested_at=at,
            ),
        )
        self.version = stored.row_version
        return self


def _incident(connection: Any, order: _Order, store_id: UUID, at: datetime) -> UUID:
    return (
        IncidentRepository()
        .open_from_counter(
            connection,
            StaffIncidentOpenCommand(
                store_id=store_id,
                order_id=order.order_id,
                evidence_summary="Áo còn vết bẩn sau khi giặt",
                actor_id=order.staff.staff_user_id,
                correlation_id=uuid4(),
                opened_at=at,
            ),
            principal=order.staff,
        )
        .incident_id
    )


def _remedy(
    connection: Any,
    store_id: UUID,
    incident_id: UUID,
    staff: StaffPrincipal,
    at: datetime,
    **fields: Any,
) -> None:
    repository = RemedyProposalRepository()
    proposal = repository.propose(
        connection,
        RemedyProposalCommand(
            store_id=store_id,
            incident_id=incident_id,
            principal=staff,
            correlation_id=uuid4(),
            proposed_at=at,
            store_fault_attested=True,
            **fields,
        ),
    )
    repository.execute(
        connection,
        RemedyExecutionCommand(
            proposal_id=proposal.proposal_id,
            principal=staff,
            correlation_id=uuid4(),
            executed_at=at + timedelta(minutes=5),
        ),
    )


@dataclass(frozen=True)
class _Shop:
    store_id: UUID
    owner: StaffPrincipal


def _seeded_shop(connection: Any) -> _Shop:
    """The fixture every figure below is checked against. Read it as the day's story.

    DAY (the shop's calendar day, three days ago):
      A  created 09:00, accepted 09:00, quality check 11:00, ready 12:00 (3 h: on time), handed
         over 12:30, paid 110.000 at 12:30, completed 12:31. Two complaints on it (13:00, 13:30):
         a free rewash executed 13:10, a damage credit of 80.000 executed 13:40.
      B  created 10:00, accepted 10:00, quality check 11:00, stain: EXCEPTION 11:10 then back to
         IN_PROCESS 11:20 (the per-axis rewash), quality check 13:00, ready 19:30 (9 h 30: late).
      C  created 10:30, accepted 10:30, quality check 11:30, ready 12:00, re-opened: EXCEPTION
         12:10 then IN_PROCESS 12:20 (the REWASH step's transitions), quality check 13:00, ready
         14:00 (3 h 30 from acceptance: on time).
      E  created 11:00, accepted 11:00, IN_PROCESS 11:30, EXCEPTION 11:40 then IN_PROCESS again
         11:50 (resumed where it stopped: NOT a rewash), quality check 12:00.
      F  created 12:00, accepted 12:00, paid at drop-off 110.000 at 23:59:59.999999 -- the last
         instant of DAY.
      G  created 15:00, cancelled 16:00 before anything was received.
    DAY - 1, 23:59:59.999999: H created (one microsecond outside the window); a complaint on H at
         the same instant (outside).
    NEXT, 00:00:00 exactly: F cancelled unwashed and refunded 110.000; K created; a complaint on K.
    Another store, same day: an order created and paid -- it must never be counted here.
    """
    store_id = _store(connection)
    owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
    staff = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))
    publish_remedy_policy(connection, actor_id=staff.staff_user_id, payload=REMEDY_POLICY)
    P = ProductionStatus

    a = _Order(connection, store_id, staff, created=local(DAY, 9)).activate(local(DAY, 9))
    a.produce(
        (P.QUEUED, local(DAY, 9, 10)),
        (P.IN_PROCESS, local(DAY, 9, 20)),
        (P.QUALITY_CHECK, local(DAY, 11)),
        (P.READY_AT_STORE, local(DAY, 12)),
        (P.RELEASED, local(DAY, 12, 30)),
    )
    a.settle(local(DAY, 12, 30), collected=True)
    a.move(local(DAY, 12, 31), commercial_target=CommercialOrderStatus.COMPLETED)
    first = _incident(connection, a, store_id, local(DAY, 13))
    _remedy(connection, store_id, first, staff, local(DAY, 13, 5), kind=RemedyKind.FREE_REWASH)
    second = _incident(connection, a, store_id, local(DAY, 13, 30))
    _remedy(
        connection,
        store_id,
        second,
        staff,
        local(DAY, 13, 35),
        kind=RemedyKind.DAMAGE_COMPENSATION,
        order_line_id="line-1",
        amount_vnd=80_000,
    )

    b = _Order(connection, store_id, staff, created=local(DAY, 10)).activate(local(DAY, 10))
    b.produce(
        (P.QUEUED, local(DAY, 10, 10)),
        (P.IN_PROCESS, local(DAY, 10, 20)),
        (P.QUALITY_CHECK, local(DAY, 11)),
        (P.EXCEPTION, local(DAY, 11, 10)),
        (P.IN_PROCESS, local(DAY, 11, 20)),
        (P.QUALITY_CHECK, local(DAY, 13)),
        (P.READY_AT_STORE, local(DAY, 19, 30)),
    )

    c = _Order(connection, store_id, staff, created=local(DAY, 10, 30)).activate(local(DAY, 10, 30))
    c.produce(
        (P.QUEUED, local(DAY, 10, 40)),
        (P.IN_PROCESS, local(DAY, 10, 50)),
        (P.QUALITY_CHECK, local(DAY, 11, 30)),
        (P.READY_AT_STORE, local(DAY, 12)),
        (P.EXCEPTION, local(DAY, 12, 10)),
        (P.IN_PROCESS, local(DAY, 12, 20)),
        (P.QUALITY_CHECK, local(DAY, 13)),
        (P.READY_AT_STORE, local(DAY, 14)),
    )

    e = _Order(connection, store_id, staff, created=local(DAY, 11)).activate(local(DAY, 11))
    e.produce(
        (P.QUEUED, local(DAY, 11, 10)),
        (P.IN_PROCESS, local(DAY, 11, 30)),
        (P.EXCEPTION, local(DAY, 11, 40)),
        (P.IN_PROCESS, local(DAY, 11, 50)),
        (P.QUALITY_CHECK, local(DAY, 12)),
    )

    f = _Order(connection, store_id, staff, created=local(DAY, 12)).activate(local(DAY, 12))
    f.settle(LAST_MOMENT, collected=False)
    f.move(MIDNIGHT, commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW)
    f.move(
        MIDNIGHT,
        commercial_target=CommercialOrderStatus.CANCELLED,
        custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
    )

    g = _Order(connection, store_id, staff, created=local(DAY, 15))
    g.move(local(DAY, 16), commercial_target=CommercialOrderStatus.CANCELLED)

    h = _Order(connection, store_id, staff, created=local(DAY, 0) - timedelta(microseconds=1))
    _incident(connection, h, store_id, local(DAY, 0) - timedelta(microseconds=1))
    k = _Order(connection, store_id, staff, created=MIDNIGHT)
    _incident(connection, k, store_id, MIDNIGHT)

    elsewhere = _store(connection)
    stranger = _person(connection, elsewhere, frozenset({StaffRole.OPERATOR}))
    other = _Order(connection, elsewhere, stranger, created=local(DAY, 12)).activate(local(DAY, 12))
    other.settle(local(DAY, 12, 5), collected=False)
    _incident(connection, other, elsewhere, local(DAY, 12, 10))

    connection.commit()
    return _Shop(store_id=store_id, owner=owner)


def _read(
    connection: Any,
    shop: _Shop,
    start: date,
    end: date,
    *,
    principal: StaffPrincipal | None = None,
) -> StoreReport:
    with connection.cursor() as cursor:
        return ReportRepository.store_report(
            cursor,
            store_id=shop.store_id,
            principal=principal or shop.owner,
            policy=STANDARD_WASH_SLA,
            from_date=start,
            to_date=end,
            as_of=AS_OF,
        )


def _figures(period: ReportPeriod) -> dict[ReportKey, ReportFigure]:
    return {figure.key: figure for figure in period.figures}


def _pairs(period: ReportPeriod) -> dict[str, tuple[int, int | None]]:
    return {figure.key.value: (figure.numerator, figure.denominator) for figure in period.figures}


# --- the seeded shop, figure by figure ----------------------------------------------------------


def test_every_kpi_over_a_two_day_window_matches_the_fixture(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _seeded_shop(connection)
    report = _read(connection, shop, DAY, NEXT)

    assert _pairs(report.summary) == {
        # A, B, C, E, F, G on DAY and K at NEXT's midnight; H is one microsecond early.
        "ORDERS_CREATED": (7, None),
        "ORDERS_COMPLETED": (1, 7),
        # G on DAY, F at NEXT's midnight.
        "ORDERS_CANCELLED": (2, 7),
        # A (3 h) and C (3 h 30 from acceptance to its last completion) on time; B at 9 h 30 late.
        "ON_TIME_INTERNAL": (2, 3),
        # B (per-axis) and C (REWASH shape); E resumed in place; A, B, C, E reached quality check.
        "REWASH": (2, 4),
        # Two on A, one on K; H's is outside and the other store's is not ours.
        "COMPLAINTS": (3, 1),
        "MONEY_COLLECTED": (220_000, None),
        "MONEY_REFUNDED": (110_000, None),
        "MONEY_NET": (110_000, None),
        "REMEDIES_EXECUTED": (2, None),
    }
    figures = _figures(report.summary)
    assert figures[ReportKey.MONEY_COLLECTED].entries == 2
    assert figures[ReportKey.MONEY_REFUNDED].entries == 1
    assert figures[ReportKey.MONEY_NET].direction == "IN"
    assert figures[ReportKey.REMEDIES_EXECUTED].amount_vnd == 80_000
    assert figures[ReportKey.REMEDIES_EXECUTED].by_kind == (
        ("FREE_REWASH", 1, None),
        ("DAMAGE_COMPENSATION", 1, 80_000),
        ("LATE_DELIVERY_CREDIT", 0, 0),
        ("LOST_ITEM", 0, 0),
    )
    assert report.window.days == 2 and report.window.ends_today is False
    assert report.query_version == report_query_version(STANDARD_WASH_SLA).label


def test_the_data_quality_is_rule_assumed_on_the_on_time_figure_and_complete_elsewhere(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _seeded_shop(connection)
    figures = _figures(_read(connection, shop, DAY, DAY).summary)
    assert figures[ReportKey.ON_TIME_INTERNAL].data_quality.value == "RULE_ASSUMED"
    assert {
        key.value for key, figure in figures.items() if figure.data_quality.value == "COMPLETE"
    } == {key.value for key in ReportKey} - {"ON_TIME_INTERNAL"}


def test_each_day_is_its_own_row_and_the_midnight_boundary_files_each_fact_once(
    connection: psycopg.Connection[Any],
) -> None:
    """23:59:59.999999 is DAY's and 00:00:00 is NEXT's, in the daily rows and in 1-day windows."""
    shop = _seeded_shop(connection)
    report = _read(connection, shop, DAY, NEXT)
    assert [period.from_date for period in report.days] == [DAY, NEXT]
    on_day, on_next = (_pairs(period) for period in report.days)

    assert on_day["ORDERS_CREATED"] == (6, None)
    assert on_next["ORDERS_CREATED"] == (1, None)
    assert on_day["ORDERS_CANCELLED"] == (1, 6)
    assert on_next["ORDERS_CANCELLED"] == (1, 1)
    # F's payment at the last microsecond of DAY is DAY's; its refund at midnight is NEXT's.
    assert on_day["MONEY_COLLECTED"] == (220_000, None)
    assert on_next["MONEY_COLLECTED"] == (0, None)
    assert on_day["MONEY_REFUNDED"] == (0, None)
    assert on_next["MONEY_REFUNDED"] == (110_000, None)
    assert _figures(report.days[1])[ReportKey.MONEY_NET].direction == "OUT"
    assert on_next["MONEY_NET"] == (110_000, None)
    assert on_day["COMPLAINTS"] == (2, 1)
    assert on_next["COMPLAINTS"] == (1, 0)

    # The same facts through one-day windows: the summary and the daily row are one statement.
    assert _pairs(_read(connection, shop, DAY, DAY).summary) == on_day
    assert _pairs(_read(connection, shop, NEXT, NEXT).summary) == on_next
    # And the day before the window holds exactly H and H's complaint.
    before = _pairs(
        _read(connection, shop, DAY - timedelta(days=1), DAY - timedelta(days=1)).summary
    )
    assert before["ORDERS_CREATED"] == (1, None)
    assert before["COMPLAINTS"] == (1, 0)


def test_a_day_with_nothing_on_it_is_a_row_of_zeros_not_a_missing_row(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _seeded_shop(connection)
    start = DAY - timedelta(days=5)
    report = _read(connection, shop, start, DAY - timedelta(days=2))
    assert len(report.days) == 4
    for period in report.days:
        assert all(numerator == 0 for numerator, _ in _pairs(period).values())
    assert _figures(report.days[0])[ReportKey.REMEDIES_EXECUTED].by_kind == (
        ("FREE_REWASH", 0, None),
        ("DAMAGE_COMPENSATION", 0, 0),
        ("LATE_DELIVERY_CREDIT", 0, 0),
        ("LOST_ITEM", 0, 0),
    )


def test_the_money_over_one_day_is_the_takings_figure_the_counter_sees(
    connection: psycopg.Connection[Any],
) -> None:
    """One rule, two widths: a one-day report reads exactly what `collected-today-v2` reads."""
    shop = _seeded_shop(connection)
    operator = _person(connection, shop.store_id, frozenset({StaffRole.OPERATOR}))
    for day in (DAY, NEXT):
        figures = _figures(_read(connection, shop, day, day).summary)
        with connection.cursor() as cursor:
            takings = SettlementRepository.collected_today(
                cursor, store_id=shop.store_id, principal=operator, as_of=local(day, 12)
            )
        assert figures[ReportKey.MONEY_COLLECTED].numerator == takings.collected_vnd
        assert figures[ReportKey.MONEY_COLLECTED].entries == takings.settlement_count
        assert figures[ReportKey.MONEY_REFUNDED].numerator == takings.refunded_vnd
        assert figures[ReportKey.MONEY_REFUNDED].entries == takings.refund_count
        assert figures[ReportKey.MONEY_NET].numerator == takings.net_vnd
        assert figures[ReportKey.MONEY_NET].direction == takings.net_direction


# --- the on-time rule is the SLA board's rule ---------------------------------------------------


def test_on_time_is_the_boards_rule_at_exactly_the_mark_and_one_microsecond_past_it(
    connection: psycopg.Connection[Any],
) -> None:
    """Eight hours to the microsecond is on time; one microsecond more is not -- on both surfaces.

    Both orders are left on the shelf (READY_AT_STORE), so the SLA board lists them too, and the
    board's own outcome for each is compared with the report's count. If either surface ever
    restated the rule, this is where they would disagree.
    """
    store_id = _store(connection)
    owner = _person(connection, store_id, frozenset({StaffRole.OWNER_ADMIN}))
    staff = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))
    P = ProductionStatus
    accepted = local(DAY, 8)
    for ready in (accepted + timedelta(hours=8), accepted + timedelta(hours=8, microseconds=1)):
        order = _Order(connection, store_id, staff, created=accepted).activate(accepted)
        order.produce(
            (P.QUEUED, accepted),
            (P.IN_PROCESS, accepted),
            (P.QUALITY_CHECK, accepted),
            (P.READY_AT_STORE, ready),
        )
    connection.commit()

    report = _read(connection, _Shop(store_id, owner), DAY, DAY)
    assert _pairs(report.summary)["ON_TIME_INTERNAL"] == (1, 2)

    board = ShadowConsoleRepository().sla_risk_board(
        connection, store_id=store_id, principal=owner, policy=STANDARD_WASH_SLA, now=AS_OF
    )
    assert sorted(item.sla_outcome for item in board) == ["BREACHED", "MET"]
    assert sum(item.sla_outcome == "MET" for item in board) == 1


def test_the_report_version_is_pinned_and_moves_with_the_boards_rule() -> None:
    """Invariant 18. Editing either statement, the day boundary, or the board's rule fails this.

    `v2` (`PROMISE-001`): the on-time statement reads the first promise and a promised order is
    judged against it, and the board's version it hashes moved to `sla-risk-board-v2`.
    """
    version = report_query_version(STANDARD_WASH_SLA)
    assert version.identifier == "report-v2"
    assert version.digest == "b6e45d5c0fb41609"
    stricter = ProductionSlaPolicy(
        policy_id="SLA_STANDARD_CLOTHES",
        policy_type=SlaPolicyType.COMMITMENT,
        target_min_hours=None,
        target_max_hours=6,
        commitment_authority=CommitmentAuthority.HUMAN_CONFIRM,
    )
    assert report_query_version(stricter).digest != version.digest


# --- the window ---------------------------------------------------------------------------------


def test_the_window_is_at_most_ninety_two_days_and_never_reversed_or_in_the_future() -> None:
    today = date(2026, 9, 25)
    widest = validate_window(today - timedelta(days=REPORT_MAX_DAYS - 1), today, today=today)
    assert widest.days == 92 and widest.ends_today is True
    one_day = validate_window(today - timedelta(days=1), today - timedelta(days=1), today=today)
    assert one_day.days == 1 and one_day.ends_today is False

    refusals = {
        "REPORT_WINDOW_TOO_LONG": (today - timedelta(days=REPORT_MAX_DAYS), today),
        "REPORT_WINDOW_REVERSED": (today, today - timedelta(days=1)),
        "REPORT_WINDOW_IN_FUTURE": (today, today + timedelta(days=1)),
    }
    for reason, (start, end) in refusals.items():
        with pytest.raises(ReportWindowError) as refused:
            validate_window(start, end, today=today)
        assert refused.value.reason_code == reason


# --- who may read -------------------------------------------------------------------------------


def test_the_four_report_roles_read_and_an_operator_does_not(
    connection: psycopg.Connection[Any],
) -> None:
    assert (
        frozenset(
            {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.ACCOUNTANT, StaffRole.AUDITOR}
        )
        == REPORT_READ_ROLES
    )
    shop = _Shop(_store(connection), _person(connection, None, frozenset()))
    connection.commit()
    for role in sorted(REPORT_READ_ROLES):
        reader = _person(connection, shop.store_id, frozenset({role}))
        assert _read(connection, shop, DAY, DAY, principal=reader).window.days == 1

    refused = [
        _person(connection, shop.store_id, frozenset({StaffRole.OPERATOR})),
        _person(connection, shop.store_id, frozenset({StaffRole.DRIVER})),
        # The right role without MFA, and the right role in another store.
        _person(connection, shop.store_id, frozenset({StaffRole.OWNER_ADMIN}), mfa=False),
        _person(connection, _store(connection), frozenset({StaffRole.OWNER_ADMIN})),
    ]
    for principal in refused:
        with pytest.raises(ReportAuthorizationError):
            _read(connection, shop, DAY, DAY, principal=principal)
