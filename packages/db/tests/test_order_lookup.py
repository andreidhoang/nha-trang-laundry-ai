"""`ORDER-LOOKUP-001`: at pickup, staff can find the customer's order and see what they owe.

Before this, the only order read was `list_for_store`: the newest N rows of one store, no filter, no
cursor, and no read by identifier. A transition needs `If-Match`, and the row version was only
obtainable from that list -- so at thirty orders a day an order became unworkable after about three
days, while the customer was still coming back for their laundry. The counter ticket (`DEC-013`,
"the order is tracked by it") was shown once at intake and never again, and no order read carried
the amount the settlement form demands to the đồng.

These tests use a real database because every claim is about what the repository reads: which row,
through which store, and which stored number.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from nha_trang_laundry_contracts.channel_envelope import ChannelProvider
from nha_trang_laundry_db.channel import ContactChannelBindingRepository
from nha_trang_laundry_db.counter_tickets import BUSINESS_TIMEZONE, ticket_business_date
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    OPEN_ORDERS_SQL_PREDICATE,
    CreateOrderCommand,
    OrderAuthorizationError,
    OrderNotVisibleError,
    OrderRepository,
    OrderTransitionCommand,
    TicketReference,
)
from nha_trang_laundry_db.settlement import SettlementCommand, SettlementRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.orders import TERMINAL_COMMERCIAL_STATUSES, IntakeReadiness
from quote_test_data import accepted_quote, bulk_newer_orders

READY = IntakeReadiness(True, True, True, True, True, True)
LOCAL = ZoneInfo(BUSINESS_TIMEZONE)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(
    connection: Any, store_id: UUID | None, role: StaffRole = StaffRole.OPERATOR
) -> StaffPrincipal:
    now = datetime.now(UTC)
    if store_id is not None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM stores WHERE id = %s", (store_id,))
            exists = cursor.fetchone() is not None
        if not exists:
            StoreRepository.create(
                connection,
                store_id=store_id,
                name="Cửa hàng thử nghiệm",
                created_by=None,
                correlation_id=uuid4(),
            )
    staff_id, assigner = uuid4(), uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_id, assigner):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                """,
                (identifier, f"oidc-{identifier}", now),
            )
        if store_id is not None:
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (staff_id, store_id, assigner, now),
            )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), True, uuid4())


def _order(
    connection: Any,
    store_id: UUID,
    staff: StaffPrincipal,
    *,
    created_at: datetime | None = None,
    ticket_issued_at: datetime | None = None,
) -> UUID:
    """A real order: ticket, intake, priced, accepted, ordered -- the chain production walks."""
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, ticket_issued_at=ticket_issued_at
    )
    stored = OrderRepository().create(
        connection,
        CreateOrderCommand(
            store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            FulfillmentMode.SELF_DROP_SELF_COLLECT,
            staff,
            f"order-{uuid4().hex}",
            uuid4(),
            created_at or datetime.now(UTC),
            AcquisitionSource.WALK_IN,
        ),
        # `created_at` is the server's clock and this is the one seam tests may hold it at.
        evaluated_at=created_at,
    )
    return stored.order_id


def _advance(
    connection: Any, order_id: UUID, staff: StaffPrincipal, version: int, **target: Any
) -> int:
    return (
        OrderRepository()
        .transition(
            connection,
            OrderTransitionCommand(
                order_id, version, staff, f"step-{uuid4().hex}", uuid4(), **target
            ),
        )
        .row_version
    )


def _read(connection: Any, order_id: UUID, principal: StaffPrincipal) -> Any:
    with connection.cursor() as cursor:
        return OrderRepository.read_for_principal(cursor, order_id=order_id, principal=principal)


# --- order #101 -------------------------------------------------------------------------------


def test_an_order_beyond_the_newest_hundred_is_readable_by_id_and_transitionable(
    connection: psycopg.Connection[Any],
) -> None:
    """The failure at the counter: the 101st-newest order could not be found or moved."""
    store_id = uuid4()
    staff = _staff(connection, store_id)
    target = _order(connection, store_id, staff)
    bulk_newer_orders(
        connection,
        store_id,
        staff,
        count=100,
        after=datetime.now(UTC),
        commercial="REQUESTED",
    )

    with connection.cursor() as cursor:
        board = OrderRepository.list_for_store(
            cursor, store_id=store_id, principal=staff, limit=100
        )
    # The problem, stated: the newest hundred no longer include it.
    assert target not in {item.order_id for item in board}

    view = _read(connection, target, staff)
    assert view.order_id == target
    assert view.store_id == store_id
    assert view.commercial is CommercialOrderStatus.REQUESTED
    assert view.row_version == 1

    moved = OrderRepository().transition(
        connection,
        OrderTransitionCommand(
            target,
            view.row_version,
            staff,
            f"counter-{uuid4().hex}",
            uuid4(),
            commercial_target=CommercialOrderStatus.STORE_CONFIRMATION_PENDING,
        ),
    )
    assert moved.row_version == 2
    assert _read(connection, target, staff).row_version == 2


def test_a_member_of_another_store_learns_nothing_about_the_order(
    connection: psycopg.Connection[Any],
) -> None:
    """Membership comes from the row's store. A stranger's refusal is a missing order's refusal."""
    home, elsewhere = uuid4(), uuid4()
    resident = _staff(connection, home)
    outsider = _staff(connection, elsewhere, StaffRole.OWNER_ADMIN)
    order_id = _order(connection, home, resident)

    with pytest.raises(OrderNotVisibleError) as refused:
        _read(connection, order_id, outsider)
    with pytest.raises(OrderNotVisibleError) as missing:
        _read(connection, uuid4(), outsider)
    # Indistinguishable, so probing identifiers teaches nobody which orders exist in which store.
    assert str(refused.value) == str(missing.value)


def test_a_revoked_member_can_no_longer_read_the_order(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE staff_store_assignments
            SET revoked_at = now(), revoked_by_staff_id = staff_user_id,
                row_version = row_version + 1
            WHERE staff_user_id = %s AND store_id = %s
            """,
            (staff.staff_user_id, store_id),
        )
    with pytest.raises(OrderNotVisibleError):
        _read(connection, order_id, staff)


def test_a_role_that_may_not_read_orders_is_refused_before_anything_is_read(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    driver = _staff(connection, store_id, StaffRole.DRIVER)
    order_id = _order(connection, store_id, staff)
    with pytest.raises(OrderAuthorizationError):
        _read(connection, order_id, driver)


# --- what the customer is known by, and what they owe --------------------------------------------


def test_the_read_carries_the_ticket_and_the_quote_the_order_is_bound_to(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)

    view = _read(connection, order_id, staff)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.ticket_number, t.issued_on, o.current_quote_id, o.current_quote_revision
            FROM orders o JOIN counter_tickets t ON t.id = o.bound_contact_id
            WHERE o.id = %s
            """,
            (order_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert view.ticket_number == int(row[0]) == 1
    assert view.ticket_issued_on == row[1]
    assert (view.quote_id, view.quote_revision) == (row[2], int(row[3]))
    # And the list carries the same read model, not a thinner one.
    with connection.cursor() as cursor:
        (listed,) = OrderRepository.list_for_store(
            cursor, store_id=store_id, principal=staff, limit=10
        )
    assert listed == view


def test_an_order_bound_to_a_channel_contact_has_no_ticket_rather_than_a_guessed_one(
    connection: psycopg.Connection[Any],
) -> None:
    """`DEC-015`: two sources of customer reference, and only a counter ticket has a number.

    The row is written directly because no fixture path yet walks a channel-bound customer through
    intake to an order; `orders.bound_contact_id` has no foreign key, so the shape is one the table
    admits and the read must answer honestly for it: no ticket, not ticket zero.
    """
    store_id = uuid4()
    staff = _staff(connection, store_id)
    binding = ContactChannelBindingRepository().resolve_or_create(
        connection,
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        provider_user_ref=f"synthetic-lookup-{uuid4().hex[:12]}",
        correlation_id=uuid4(),
    )
    bulk_newer_orders(
        connection,
        store_id,
        staff,
        count=1,
        after=datetime.now(UTC),
        commercial="REQUESTED",
        contact_id=binding.binding.contact_id,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM orders WHERE store_id = %s AND bound_contact_id = %s",
            (store_id, binding.binding.contact_id),
        )
        (row,) = cursor.fetchall()
    view = _read(connection, row[0], staff)
    assert view.ticket_number is None
    assert view.ticket_issued_on is None
    assert view.payable_total_vnd == 110_000


def test_the_payable_total_is_the_persisted_snapshot_total_and_what_settlement_demands(
    connection: psycopg.Connection[Any],
) -> None:
    """The server reports a number it already stored. The client never computes money."""
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)

    view = _read(connection, order_id, staff)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT (r.snapshot -> 'totals' ->> 'display_total_min_vnd')::bigint,
                   (r.snapshot -> 'totals' ->> 'display_total_max_vnd')::bigint
            FROM orders o
            JOIN quote_revisions r
              ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
            WHERE o.id = %s
            """,
            (order_id,),
        )
        snapshot_total = cursor.fetchone()
    assert snapshot_total is not None
    assert snapshot_total[0] == snapshot_total[1]
    assert isinstance(view.payable_total_vnd, int)
    assert view.payable_total_vnd == snapshot_total[0] == 110_000

    # Drive it to the counter and settle it with exactly the number the read reported.
    version = view.row_version
    now = datetime.now(UTC)
    for step in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "production_accepted_at": now,
            "intake_readiness": READY,
        },
        {"commercial_target": CommercialOrderStatus.STORE_CONFIRMATION_PENDING},
        {"commercial_target": CommercialOrderStatus.CONFIRMED},
        {"commercial_target": CommercialOrderStatus.ACTIVE},
        {"production_target": ProductionStatus.QUEUED},
        {"production_target": ProductionStatus.IN_PROCESS},
        {"production_target": ProductionStatus.QUALITY_CHECK},
        {"production_target": ProductionStatus.READY_AT_STORE},
    ):
        version = _advance(connection, order_id, staff, version, **step)
    settled = SettlementRepository().record(
        connection,
        SettlementCommand(
            order_id=order_id,
            paid_amount_vnd=view.payable_total_vnd,
            collected_by_customer=True,
            principal=staff,
            correlation_id=uuid4(),
        ),
    )
    assert settled.expected_total_vnd == view.payable_total_vnd
    after = _read(connection, order_id, staff)
    # The figure is the quote's, so it does not move when the money is taken; the balance does.
    assert after.payable_total_vnd == view.payable_total_vnd
    assert after.balance.value == "PAID"
    assert after.row_version == settled.row_version


# --- finding today's ticket ---------------------------------------------------------------------


def test_ticket_lookup_finds_todays_ticket_and_not_yesterdays_same_number(
    connection: psycopg.Connection[Any],
) -> None:
    """Numbers restart daily, so "số 1" means today's number 1 unless a date is named."""
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    yesterday = _order(connection, store_id, staff, ticket_issued_at=now - timedelta(days=1))
    today = _order(connection, store_id, staff)
    today_date = ticket_business_date(now)

    def lookup(number: int, on: date) -> set[UUID]:
        with connection.cursor() as cursor:
            return {
                item.order_id
                for item in OrderRepository.list_for_store(
                    cursor,
                    store_id=store_id,
                    principal=staff,
                    limit=50,
                    ticket=TicketReference(number=number, issued_on=on),
                )
            }

    assert lookup(1, today_date) == {today}
    assert lookup(1, today_date - timedelta(days=1)) == {yesterday}
    assert lookup(2, today_date) == set()


def test_ticket_lookup_is_scoped_to_the_callers_store(
    connection: psycopg.Connection[Any],
) -> None:
    """Every store has a "số 1" today. Another store's is not this counter's customer."""
    home, elsewhere = uuid4(), uuid4()
    resident = _staff(connection, home)
    neighbour = _staff(connection, elsewhere)
    mine = _order(connection, home, resident)
    _order(connection, elsewhere, neighbour)
    with connection.cursor() as cursor:
        found = OrderRepository.list_for_store(
            cursor,
            store_id=home,
            principal=resident,
            limit=50,
            ticket=TicketReference(number=1, issued_on=ticket_business_date(datetime.now(UTC))),
        )
    assert [item.order_id for item in found] == [mine]


# --- open orders never fall off the board -----------------------------------------------------


def test_the_open_filter_includes_an_old_active_order_the_default_board_has_lost(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    month_ago = datetime.now(UTC) - timedelta(days=30)
    old = _order(connection, store_id, staff, created_at=month_ago)
    version = 1
    for step in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "production_accepted_at": month_ago,
            "intake_readiness": READY,
        },
        {"commercial_target": CommercialOrderStatus.STORE_CONFIRMATION_PENDING},
        {"commercial_target": CommercialOrderStatus.CONFIRMED},
        {"commercial_target": CommercialOrderStatus.ACTIVE},
    ):
        version = _advance(connection, old, staff, version, **step)
    bulk_newer_orders(
        connection, store_id, staff, count=120, after=month_ago, commercial="CANCELLED"
    )

    with connection.cursor() as cursor:
        everything = OrderRepository.list_for_store(
            cursor, store_id=store_id, principal=staff, limit=100
        )
        still_open = OrderRepository.list_for_store(
            cursor, store_id=store_id, principal=staff, limit=100, open_only=True
        )
    assert old not in {item.order_id for item in everything}
    assert [item.order_id for item in still_open] == [old]
    assert still_open[0].commercial is CommercialOrderStatus.ACTIVE
    assert still_open[0].row_version == version


def test_the_open_predicate_is_the_domains_terminal_set() -> None:
    """The SQL literal and the partial index in 0047 are spelled once; the domain owns the set."""
    assert {status.value for status in TERMINAL_COMMERCIAL_STATUSES} == {"CANCELLED", "COMPLETED"}
    assert OPEN_ORDERS_SQL_PREDICATE == "commercial_status NOT IN ('CANCELLED', 'COMPLETED')"
    migration = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "migrations"
        / "0047_order_lookup.sql"
    ).read_text(encoding="utf-8")
    assert f"WHERE {OPEN_ORDERS_SQL_PREDICATE}" in migration


def test_the_board_and_the_open_filter_are_served_by_their_indexes(
    connection: psycopg.Connection[Any],
) -> None:
    """A year of one store's orders, and the plan read out rather than assumed."""
    store_id = uuid4()
    staff = _staff(connection, store_id)
    bulk_newer_orders(
        connection,
        store_id,
        staff,
        count=6_000,
        after=datetime.now(UTC) - timedelta(days=365),
        commercial="CANCELLED",
    )
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE orders")
    from nha_trang_laundry_db import orders as module

    plans = {}
    for name, open_only in (("board", False), ("open", True)):
        sql, parameters = module._list_statement(
            store_id=store_id, limit=100, open_only=open_only, ticket=None
        )
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN (FORMAT JSON) " + sql, parameters)
            found = cursor.fetchone()
        assert found is not None
        plans[name] = str(found[0])
    assert "orders_store_created_idx" in plans["board"], plans["board"]
    assert "orders_store_open_idx" in plans["open"], plans["open"]
    for plan in plans.values():
        assert "Seq Scan on orders" not in plan, plan


# --- the ticket business day --------------------------------------------------------------------


def test_the_business_date_is_the_local_date_not_the_utc_date() -> None:
    """06:30 in Nha Trang is 23:30 UTC the evening before, and it is the morning's business."""
    early = datetime(2026, 9, 24, 6, 30, tzinfo=LOCAL)
    assert early.astimezone(UTC) == datetime(2026, 9, 23, 23, 30, tzinfo=UTC)
    assert ticket_business_date(early) == date(2026, 9, 24)
    assert ticket_business_date(early.astimezone(UTC)) == date(2026, 9, 24)
    with pytest.raises(ValueError):
        ticket_business_date(datetime(2026, 9, 24, 6, 30))
