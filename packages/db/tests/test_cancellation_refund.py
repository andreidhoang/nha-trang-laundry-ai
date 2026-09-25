"""A paid order cancelled under `DEC-024` hands the money back, and the books say so.

Found independently by two reviewers. `OrderRepository.transition` recorded the custody resolution
only in the event payload: `balance_status` stayed `PAID`, the `order_settlements` row stayed, and
`collected_today` summed every settlement regardless of what happened to the order afterwards. So a
prepaid delivery order (`DEC-023`) paid at the counter, never washed, and cancelled as
`RETURNED_UNWASHED_REFUNDED` with the cash handed back left *tiền đã thu* (`DEC-014`) above the
drawer by exactly the refunded amount -- the one money figure the console shows, wrong in the
direction that makes a staff member look like they took money.

`DEC-024` names two resolutions under which the customer is not charged:
`RETURNED_UNWASHED_REFUNDED` ("any prepayment refunded in full") and `SHOP_FAULT_NO_CHARGE`
("nothing is charged"). The third,
`NOT_RECEIVED`, means nothing was taken in, and a paid order always has custody recorded -- the
domain refuses it for such an order before any money question arises.

The tests below run against PostgreSQL through the real repositories and nothing else.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderStateError,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.settlement import (
    CollectedToday,
    SettlementCommand,
    SettlementRepository,
)
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness
from quote_test_data import accepted_quote

READY = IntakeReadiness(True, True, True, True, True, True)
#: `make_quote_snapshot` bills 100,000 of service and adds a 10,000 delivery fee.
QUOTED_TOTAL = 110_000


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(connection: Any, store_id: UUID) -> StaffPrincipal:
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng thử", created_by=None, correlation_id=uuid4()
    )
    staff_id, assigner = uuid4(), uuid4()
    moment = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_id, assigner):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                """,
                (identifier, f"oidc-{identifier}", moment),
            )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (staff_id, store_id, assigner, moment),
        )
    return StaffPrincipal(
        staff_id, f"oidc-{staff_id}", frozenset({StaffRole.OPERATOR}), True, uuid4()
    )


class _Order:
    """One order driven through the real transitions, carrying its own row version."""

    def __init__(
        self,
        connection: Any,
        store_id: UUID,
        staff: StaffPrincipal,
        mode: FulfillmentMode,
        *,
        at: datetime,
    ) -> None:
        self.connection, self.staff, self.at = connection, staff, at
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
                at,
                AcquisitionSource.WALK_IN,
            ),
        )
        self.order_id, self.version = stored.order_id, stored.row_version

    def move(self, *, at: datetime | None = None, key: str | None = None, **target: Any) -> Any:
        stored = OrderRepository().transition(
            self.connection,
            OrderTransitionCommand(
                self.order_id,
                self.version,
                self.staff,
                key or f"step-{uuid4().hex}",
                uuid4(),
                occurred_at=at or self.at,
                **target,
            ),
        )
        self.version = stored.row_version
        return stored

    def to_active(self) -> _Order:
        self.move(intake_target=IntakeStatus.RECEIVED_PENDING_INSPECTION)
        self.move(
            intake_target=IntakeStatus.ACCEPTED,
            production_accepted_at=self.at,
            intake_readiness=READY,
        )
        self.move(commercial_target=CommercialOrderStatus.STORE_CONFIRMATION_PENDING)
        self.move(commercial_target=CommercialOrderStatus.CONFIRMED)
        self.move(commercial_target=CommercialOrderStatus.ACTIVE)
        return self

    def release(self) -> _Order:
        for target in (
            ProductionStatus.QUEUED,
            ProductionStatus.IN_PROCESS,
            ProductionStatus.QUALITY_CHECK,
            ProductionStatus.READY_AT_STORE,
            ProductionStatus.RELEASED,
        ):
            self.move(production_target=target)
        return self

    def settle(self, *, collected: bool, at: datetime) -> Any:
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
        return stored

    def cancel_after_review(
        self, resolution: CustodyResolution, *, at: datetime, key: str | None = None
    ) -> Any:
        self.move(commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW, at=at)
        return self.move(
            commercial_target=CommercialOrderStatus.CANCELLED,
            custody_resolution=resolution,
            at=at,
            key=key,
        )


def _balance(connection: Any, order_id: UUID) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT balance_status FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def _refunds(connection: Any, order_id: UUID) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.refunded_amount_vnd, r.direction, r.custody_resolution, r.refunded_at,
                   r.settlement_id = s.id, r.attested_by_staff_id
            FROM order_refunds r
            JOIN order_settlements s ON s.order_id = r.order_id
            WHERE r.order_id = %s
            """,
            (order_id,),
        )
        return list(cursor.fetchall())


def _takings(
    connection: Any, store_id: UUID, staff: StaffPrincipal, as_of: datetime
) -> CollectedToday:
    """Read the figure, and hold every read in every scenario to invariant 2.

    Every numeric field of the result is money or a count, and none of them may ever be negative --
    including on a refund-only day, where the drawer's fall is a direction, not a sign.
    """
    with connection.cursor() as cursor:
        takings = SettlementRepository.collected_today(
            cursor, store_id=store_id, principal=staff, as_of=as_of
        )
    numeric = {
        name: value
        for name, value in asdict(takings).items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    assert set(numeric) == {
        "collected_vnd",
        "settlement_count",
        "refunded_vnd",
        "refund_count",
        "net_vnd",
        # `collected-today-v3` (`PAYMENT-001`): the same money in, split by method.
        "payment_count",
        "cash_vnd",
        "cash_count",
        "transfer_vnd",
        "transfer_count",
    }
    assert all(value >= 0 for value in numeric.values()), numeric
    assert takings.cash_vnd + takings.transfer_vnd == takings.collected_vnd
    assert takings.cash_count + takings.transfer_count == takings.payment_count
    assert takings.net_direction in ("IN", "OUT")
    return takings


def _refund_table_rows(connection: Any, order_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM order_refunds WHERE order_id = %s", (order_id,))
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


# --- the reviewers' scenario ---------------------------------------------------------------------


def test_a_prepaid_delivery_cancelled_unwashed_and_refunded_leaves_takings_at_the_drawer(
    connection: psycopg.Connection[Any],
) -> None:
    """The exact scenario both reviewers found: DEC-023 prepaid, DEC-024 refunded in full.

    Before the fix the card read 110,000 collected with 0 in the drawer, the order read `PAID`, and
    no record anywhere said money had gone back across the counter. `collected_vnd` keeps meaning
    what was collected; the refund and the drawer's net movement now travel beside it.
    """
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=now)
    order.to_active()
    paid = order.settle(collected=False, at=now)
    assert paid.settlement_shape == "EXACT_PAYMENT_PREPAID_DELIVERY"
    assert _takings(connection, store_id, staff, now).collected_vnd == QUOTED_TOTAL

    cancelled = order.cancel_after_review(CustodyResolution.RETURNED_UNWASHED_REFUNDED, at=now)

    assert cancelled.commercial is CommercialOrderStatus.CANCELLED
    assert cancelled.balance is OrderBalanceStatus.REFUNDED
    assert _balance(connection, order.order_id) == "REFUNDED"
    # One refund, equal to what was settled -- nobody typed the amount -- recorded as money going
    # to the customer, bound to the settlement it reverses and to the staff member who handed it.
    assert _refunds(connection, order.order_id) == [
        (
            QUOTED_TOTAL,
            "TO_CUSTOMER",
            "RETURNED_UNWASHED_REFUNDED",
            now,
            True,
            staff.staff_user_id,
        )
    ]

    takings = _takings(connection, store_id, staff, now)
    assert (takings.collected_vnd, takings.settlement_count) == (QUOTED_TOTAL, 1)
    assert (takings.refunded_vnd, takings.refund_count) == (QUOTED_TOTAL, 1)
    # The drawer took 110,000 and handed 110,000 back: it did not move.
    assert (takings.net_vnd, takings.net_direction) == (0, "IN")


def test_a_walk_in_prepaid_at_drop_off_and_cancelled_unwashed_is_refunded_in_full(
    connection: psycopg.Connection[Any],
) -> None:
    """`DEC-032` made this reachable: a walk-in pays at drop-off, then changes their mind.

    The third settlement shape goes through `DEC-024`'s refund exactly as the prepaid delivery
    above does -- the whole settled amount back, bound to the settlement, dated by when it went
    back -- and nothing about the pickup record is involved, because nobody collected anything.
    """
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.SELF_DROP_SELF_COLLECT, at=now)
    order.to_active()
    paid = order.settle(collected=False, at=now)
    assert paid.settlement_shape == "EXACT_PAYMENT_PREPAID_SELF_COLLECTION"

    cancelled = order.cancel_after_review(CustodyResolution.RETURNED_UNWASHED_REFUNDED, at=now)

    assert cancelled.commercial is CommercialOrderStatus.CANCELLED
    assert _balance(connection, order.order_id) == "REFUNDED"
    assert _refunds(connection, order.order_id) == [
        (
            QUOTED_TOTAL,
            "TO_CUSTOMER",
            "RETURNED_UNWASHED_REFUNDED",
            now,
            True,
            staff.staff_user_id,
        )
    ]
    takings = _takings(connection, store_id, staff, now)
    assert (takings.collected_vnd, takings.refunded_vnd, takings.net_vnd) == (
        QUOTED_TOTAL,
        QUOTED_TOTAL,
        0,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM order_collections WHERE order_id = %s", (order.order_id,)
        )
        assert cursor.fetchone() == (0,)


def test_shop_fault_no_charge_on_a_paid_collected_order_refunds_the_settled_amount(
    connection: psycopg.Connection[Any],
) -> None:
    """`SHOP_FAULT_NO_CHARGE`: "nothing is charged", so what was taken goes back in full.

    The order was washed, released and collected, so `RETURNED_UNWASHED_REFUNDED` is refused by the
    domain and this is the only resolution a paid order in this state can be cancelled under.
    `DEC-004` says an incident opens as well; that is deliberately *not* done here (see report).
    """
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.SELF_DROP_SELF_COLLECT, at=now)
    order.to_active().release()
    order.settle(collected=True, at=now)

    cancelled = order.cancel_after_review(CustodyResolution.SHOP_FAULT_NO_CHARGE, at=now)

    assert cancelled.balance is OrderBalanceStatus.REFUNDED
    refunds = _refunds(connection, order.order_id)
    assert [(row[0], row[1], row[2]) for row in refunds] == [
        (QUOTED_TOTAL, "TO_CUSTOMER", "SHOP_FAULT_NO_CHARGE")
    ]
    takings = _takings(connection, store_id, staff, now)
    assert (takings.collected_vnd, takings.refunded_vnd) == (QUOTED_TOTAL, QUOTED_TOTAL)
    assert (takings.net_vnd, takings.net_direction) == (0, "IN")


def test_a_refund_today_of_yesterdays_payment_reduces_today_and_leaves_yesterday_alone(
    connection: psycopg.Connection[Any],
) -> None:
    """Each leg of the money lands on its own Asia/Ho_Chi_Minh business day.

    Yesterday the drawer took 110,000 and that stays true of yesterday. Today one customer paid
    110,000 and the other was handed 110,000 back, so today's drawer moved by nothing.
    """
    store_id = uuid4()
    staff = _staff(connection, store_id)
    # 10:00 local, so "one day earlier" is unambiguously yesterday in the shop's own timezone.
    today = datetime(2026, 9, 24, 3, tzinfo=UTC)
    yesterday = today - timedelta(days=1)

    early = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=today)
    early.to_active()
    early.settle(collected=False, at=yesterday)
    later = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=today)
    later.to_active()
    later.settle(collected=False, at=today)

    early.cancel_after_review(CustodyResolution.RETURNED_UNWASHED_REFUNDED, at=today)

    on_the_day = _takings(connection, store_id, staff, today)
    assert (on_the_day.collected_vnd, on_the_day.refunded_vnd) == (QUOTED_TOTAL, QUOTED_TOTAL)
    assert (on_the_day.net_vnd, on_the_day.net_direction) == (0, "IN")
    the_day_before = _takings(connection, store_id, staff, yesterday)
    assert the_day_before.collected_vnd == QUOTED_TOTAL
    assert (the_day_before.refunded_vnd, the_day_before.refund_count) == (0, 0)
    assert (the_day_before.net_vnd, the_day_before.net_direction) == (QUOTED_TOTAL, "IN")


def test_a_refund_only_day_says_the_drawer_went_down_without_a_negative_number(
    connection: psycopg.Connection[Any],
) -> None:
    """The drawer's fall is a direction, not a sign, and it is not clamped away either.

    A day on which the only money event was 110,000 handed back to a customer is a day the drawer
    ended 110,000 lighter than it started. Showing "no change" would be the original defect in the
    other direction; showing -110,000 would break invariant 2. So: 110,000, OUT.
    """
    store_id = uuid4()
    staff = _staff(connection, store_id)
    today = datetime(2026, 9, 24, 3, tzinfo=UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=today)
    order.to_active()
    order.settle(collected=False, at=today - timedelta(days=1))

    order.cancel_after_review(CustodyResolution.RETURNED_UNWASHED_REFUNDED, at=today)

    takings = _takings(connection, store_id, staff, today)
    assert (takings.collected_vnd, takings.settlement_count) == (0, 0)
    assert (takings.refunded_vnd, takings.refund_count) == (QUOTED_TOTAL, 1)
    assert (takings.net_vnd, takings.net_direction) == (QUOTED_TOTAL, "OUT")


# --- atomicity and the database's own second line ----------------------------------------------


def test_the_refund_the_transition_and_its_ledger_rows_commit_together(
    connection: psycopg.Connection[Any],
) -> None:
    """Invariant 5: mutation, event, audit and outbox -- and the refund is part of the mutation."""
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=now)
    order.to_active()
    order.settle(collected=False, at=now)
    order.move(commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW)
    cancel_version = order.version + 1

    # Occupy the outbox key the cancellation will need, so the last write in its transaction fails.
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                correlation_id, occurred_at
            ) VALUES (%s, 'ORDER', %s, 'test.collision.v1', '{}'::jsonb, %s, %s, %s)
            """,
            (uuid4(), order.order_id, f"order:{order.order_id}:refund", uuid4(), now),
        )

    with pytest.raises(psycopg.errors.UniqueViolation):
        order.move(
            commercial_target=CommercialOrderStatus.CANCELLED,
            custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        )

    # Nothing of the cancellation survived: no refund, still paid, still under review.
    assert _refund_table_rows(connection, order.order_id) == 0
    assert _balance(connection, order.order_id) == "PAID"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT commercial_status, row_version FROM orders WHERE id = %s", (order.order_id,)
        )
        assert cursor.fetchone() == ("CANCELLATION_REVIEW", cancel_version - 1)
        cursor.execute(
            """
            SELECT count(*) FROM domain_events
            WHERE aggregate_id = %s AND payload->>'target' = 'CANCELLED'
            """,
            (order.order_id,),
        )
        assert cursor.fetchone() == (0,)


def test_the_cancellation_event_audit_and_outbox_name_the_refund(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=now)
    order.to_active()
    order.settle(collected=False, at=now)
    order.cancel_after_review(CustodyResolution.RETURNED_UNWASHED_REFUNDED, at=now)

    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM order_refunds WHERE order_id = %s", (order.order_id,))
        refund_row = cursor.fetchone()
        assert refund_row is not None
        refund_id = str(refund_row[0])
        cursor.execute(
            """
            SELECT payload->'refund'->>'refund_id',
                   (payload->'refund'->>'refunded_amount_vnd')::bigint
            FROM domain_events
            WHERE aggregate_id = %s AND payload->>'target' = 'CANCELLED'
            """,
            (order.order_id,),
        )
        assert cursor.fetchone() == (refund_id, QUOTED_TOTAL)
        cursor.execute(
            """
            SELECT details->'refund'->>'refund_id' FROM audit_events
            WHERE aggregate_id = %s AND details->'refund' IS NOT NULL
            """,
            (order.order_id,),
        )
        assert cursor.fetchall() == [(refund_id,)]
        cursor.execute(
            """
            SELECT event_type, payload->>'refund_id', (payload->>'refunded_amount_vnd')::bigint
            FROM outbox_events WHERE idempotency_key = %s
            """,
            (f"order:{order.order_id}:refund",),
        )
        assert cursor.fetchall() == [("order.refund_recorded.v1", refund_id, QUOTED_TOTAL)]


def test_replaying_the_cancellation_returns_the_prior_result_and_refunds_once(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=now)
    order.to_active()
    order.settle(collected=False, at=now)
    order.move(commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW)
    command = OrderTransitionCommand(
        order.order_id,
        order.version,
        staff,
        "cancel-once",
        uuid4(),
        commercial_target=CommercialOrderStatus.CANCELLED,
        custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        occurred_at=now,
    )
    first = OrderRepository().transition(connection, command)
    again = OrderRepository().transition(connection, replace(command, correlation_id=uuid4()))

    assert again.replayed
    assert (again.balance, again.row_version) == (first.balance, first.row_version)
    assert _refund_table_rows(connection, order.order_id) == 1
    takings = _takings(connection, store_id, staff, now)
    # One refund, not two: a replayed cancellation would otherwise show the drawer going OUT.
    assert (takings.refunded_vnd, takings.refund_count) == (QUOTED_TOTAL, 1)
    assert (takings.net_vnd, takings.net_direction) == (0, "IN")


def test_the_database_refuses_a_paid_order_cancelled_without_a_refund(
    connection: psycopg.Connection[Any],
) -> None:
    """The second line of defence, for the day somebody writes a second cancellation path.

    A raw UPDATE that cancels a paid order and leaves its balance `PAID` is exactly the state the
    defect produced, and the schema now refuses it rather than trusting every writer to remember.
    """
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=now)
    order.to_active()
    order.settle(collected=False, at=now)
    order.move(commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW)

    for balance in ("PAID", "REFUNDED"):
        with (
            pytest.raises(psycopg.errors.RaiseException),
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            # PAID: cancelled with the money still counted. REFUNDED: claims a refund that no
            # `order_refunds` row records.
            cursor.execute(
                """
                UPDATE orders
                SET commercial_status = 'CANCELLED', balance_status = %s,
                    row_version = row_version + 1
                WHERE id = %s
                """,
                (balance, order.order_id),
            )


def test_a_refund_cannot_differ_from_the_settlement_or_be_edited(
    connection: psycopg.Connection[Any],
) -> None:
    """The amount is bound to the settlement by the schema, not by whichever code writes it."""
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=now)
    order.to_active()
    settlement = order.settle(collected=False, at=now)

    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO order_refunds (
                id, order_id, store_id, settlement_id, refunded_amount_vnd, direction,
                custody_resolution, attested_by_staff_id, refunded_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, 'TO_CUSTOMER', 'RETURNED_UNWASHED_REFUNDED', %s, %s, %s)
            """,
            (
                uuid4(),
                order.order_id,
                store_id,
                settlement.settlement_id,
                QUOTED_TOTAL - 10_000,
                staff.staff_user_id,
                now,
                now,
            ),
        )

    order.cancel_after_review(CustodyResolution.RETURNED_UNWASHED_REFUNDED, at=now)
    for statement in (
        "UPDATE order_refunds SET refunded_amount_vnd = 0 WHERE order_id = %s",
        "DELETE FROM order_refunds WHERE order_id = %s",
    ):
        with (
            pytest.raises(psycopg.errors.RaiseException),
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, (order.order_id,))
    assert _refund_table_rows(connection, order.order_id) == 1


# --- positive controls: what must not change -----------------------------------------------------


def test_cancelling_an_unpaid_order_is_unchanged(connection: psycopg.Connection[Any]) -> None:
    """No money was taken, so none goes back, under either cancellation path."""
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)

    reviewed = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=now)
    reviewed.to_active()
    cancelled = reviewed.cancel_after_review(CustodyResolution.RETURNED_UNWASHED_REFUNDED, at=now)
    assert (cancelled.commercial, cancelled.balance) == (
        CommercialOrderStatus.CANCELLED,
        OrderBalanceStatus.UNPAID,
    )
    assert _refund_table_rows(connection, reviewed.order_id) == 0

    # The counter case: the customer changes their mind before handing anything over.
    direct = _Order(connection, store_id, staff, FulfillmentMode.SELF_DROP_SELF_COLLECT, at=now)
    direct.move(commercial_target=CommercialOrderStatus.STORE_CONFIRMATION_PENDING)
    direct.move(commercial_target=CommercialOrderStatus.CONFIRMED)
    gone = direct.move(commercial_target=CommercialOrderStatus.CANCELLED)
    assert (gone.commercial, gone.balance) == (
        CommercialOrderStatus.CANCELLED,
        OrderBalanceStatus.UNPAID,
    )
    assert _refund_table_rows(connection, direct.order_id) == 0

    assert _takings(connection, store_id, staff, now) == CollectedToday(
        collected_vnd=0, settlement_count=0
    )


def test_a_paid_order_that_completes_stays_paid_and_counted(
    connection: psycopg.Connection[Any],
) -> None:
    """The ordinary day: paid, collected, completed. Nothing about it is a refund."""
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.SELF_DROP_SELF_COLLECT, at=now)
    order.to_active().release()
    order.settle(collected=True, at=now)
    done = order.move(commercial_target=CommercialOrderStatus.COMPLETED)

    assert done.balance is OrderBalanceStatus.PAID
    assert _refund_table_rows(connection, order.order_id) == 0
    takings = _takings(connection, store_id, staff, now)
    assert (takings.collected_vnd, takings.refunded_vnd) == (QUOTED_TOTAL, 0)
    assert (takings.net_vnd, takings.net_direction) == (QUOTED_TOTAL, "IN")


def test_a_paid_order_cannot_be_resolved_as_never_received(
    connection: psycopg.Connection[Any],
) -> None:
    """The one resolution that refunds nothing is refused for an order that took money.

    A paid order has custody recorded, so the domain already refuses `NOT_RECEIVED` on the custody
    fact. The point of this test is the consequence: no path cancels a paid order and keeps the
    money counted.
    """
    store_id = uuid4()
    staff = _staff(connection, store_id)
    now = datetime.now(UTC)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN, at=now)
    order.to_active()
    order.settle(collected=False, at=now)
    order.move(commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW)

    with pytest.raises(OrderStateError):
        order.move(
            commercial_target=CommercialOrderStatus.CANCELLED,
            custody_resolution=CustodyResolution.NOT_RECEIVED,
        )
    assert _balance(connection, order.order_id) == "PAID"
    assert _refund_table_rows(connection, order.order_id) == 0
