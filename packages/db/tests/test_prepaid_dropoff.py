"""`PREPAID-DROPOFF-001`: a walk-in customer pays the exact total at drop-off, and picks up later.

`DEC-032` (2026-09-25, delegated): a self-collect customer may pay the exact quoted total when they
drop the laundry off, as `DEC-023` already allows for delivery. Collection is recorded separately
at pickup by the named staff member who hands the goods over, and an order completes only when it
is paid, released and collected. `DEC-010` is not reopened: anything but the exact total is still
refused.

Until this item the counter had two bad choices for that customer: tick "khách đã tự lấy đồ" while
the shirts were in the machine -- a handover recorded that had not happened, with the flag that
lets an order complete set at drop-off -- or turn the money away. These tests run against real
PostgreSQL through the repositories the API calls, and every refusal is checked for writing nothing.

The same file carries the staging review's minor finding: ticking "collected" while the goods are
not finished is now refused too. You cannot hand over laundry that has not been washed.
"""

from __future__ import annotations

import os
from collections.abc import Generator
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
    CollectionCommand,
    SettlementAuthorizationError,
    SettlementCommand,
    SettlementRepository,
    SettlementStateError,
)
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness
from quote_test_data import accepted_quote

NOW = datetime(2026, 9, 25, 2, tzinfo=UTC)
READY = IntakeReadiness(True, True, True, True, True, True)
#: `make_quote_snapshot` bills 100,000 of service and adds a 10,000 delivery fee.
QUOTED_TOTAL = 110_000
WASHING = (
    ProductionStatus.QUEUED,
    ProductionStatus.IN_PROCESS,
    ProductionStatus.QUALITY_CHECK,
    ProductionStatus.READY_AT_STORE,
    ProductionStatus.RELEASED,
)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _shop(connection: Any) -> UUID:
    store_id = uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )
    return store_id


def _staff(connection: Any, store_id: UUID | None, role: StaffRole) -> StaffPrincipal:
    staff_id, assigner = uuid4(), uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_id, assigner):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                """,
                (identifier, f"oidc-{identifier}", NOW),
            )
        if store_id is not None:
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (staff_id, store_id, assigner, NOW),
            )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), True, uuid4())


class _Order:
    """One order walked through the real transitions, tracking its row version."""

    def __init__(
        self,
        connection: Any,
        store_id: UUID,
        staff: StaffPrincipal,
        mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
    ) -> None:
        self.connection = connection
        self.staff = staff
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
                NOW,
                AcquisitionSource.WALK_IN,
            ),
        )
        self.order_id = stored.order_id
        self.version = stored.row_version

    def move(self, **target: Any) -> Any:
        stored = OrderRepository().transition(
            self.connection,
            OrderTransitionCommand(
                self.order_id,
                self.version,
                self.staff,
                f"step-{uuid4().hex}",
                uuid4(),
                occurred_at=NOW,
                **target,
            ),
        )
        self.version = stored.row_version
        return stored

    def to_active(self) -> _Order:
        """Dropped off: taken in, accepted, confirmed, running -- and not washed yet."""

        self.move(intake_target=IntakeStatus.RECEIVED_PENDING_INSPECTION)
        self.move(
            intake_target=IntakeStatus.ACCEPTED, production_accepted_at=NOW, intake_readiness=READY
        )
        self.move(commercial_target=CommercialOrderStatus.STORE_CONFIRMATION_PENDING)
        self.move(commercial_target=CommercialOrderStatus.CONFIRMED)
        self.move(commercial_target=CommercialOrderStatus.ACTIVE)
        return self

    def wash_to(self, target: ProductionStatus) -> _Order:
        for step in WASHING[: WASHING.index(target) + 1]:
            self.move(production_target=step)
        return self

    def pay(self, *, collected: bool, amount: int = QUOTED_TOTAL, at: datetime = NOW) -> Any:
        stored = SettlementRepository().record(
            self.connection,
            SettlementCommand(
                order_id=self.order_id,
                paid_amount_vnd=amount,
                collected_by_customer=collected,
                principal=self.staff,
                correlation_id=uuid4(),
                attested_at=at,
            ),
        )
        self.version = stored.row_version
        return stored

    def collect(self, by: StaffPrincipal | None = None, *, version: int | None = None) -> Any:
        stored = SettlementRepository().record_collection(
            self.connection,
            CollectionCommand(
                order_id=self.order_id,
                expected_row_version=self.version if version is None else version,
                principal=by or self.staff,
                correlation_id=uuid4(),
                collected_at=NOW + timedelta(days=1),
            ),
        )
        self.version = stored.row_version
        return stored

    def row(self) -> tuple[str, str, str, bool, int]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT commercial_status, production_status, balance_status,
                       self_collection_recorded, row_version
                FROM orders WHERE id = %s
                """,
                (self.order_id,),
            )
            found = cursor.fetchone()
        assert found is not None
        return str(found[0]), str(found[1]), str(found[2]), bool(found[3]), int(found[4])

    def count(self, table: str) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {table} WHERE order_id = %s", (self.order_id,))
            found = cursor.fetchone()
        assert found is not None
        return int(found[0])


def _refused(error: pytest.ExceptionInfo[SettlementStateError]) -> tuple[str, str | None]:
    return error.value.reason_code, error.value.decision


# --- exact_prepayment_at_dropoff_is_recorded_without_a_false_handover -----------------------------


def test_a_walk_in_pays_the_exact_total_at_drop_off_and_nothing_says_they_took_it(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    assert order.row()[1] == "NOT_STARTED"

    paid = order.pay(collected=False)

    assert paid.settlement_shape == "EXACT_PAYMENT_PREPAID_SELF_COLLECTION"
    assert paid.expected_total_vnd == paid.paid_amount_vnd == QUOTED_TOTAL
    assert paid.balance_status == "PAID"
    # The false handover this item exists to end: nothing records the customer taking anything.
    assert paid.self_collection_recorded is False
    assert order.row() == ("ACTIVE", "NOT_STARTED", "PAID", False, order.version)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT collected_by, attested_by_staff_id FROM order_settlements WHERE order_id = %s",
            (order.order_id,),
        )
        assert cursor.fetchall() == [("PENDING_COLLECTION", staff.staff_user_id)]
    assert order.count("order_collections") == 0


def test_the_money_is_counted_on_the_day_it_is_taken_and_pickup_adds_none(
    connection: psycopg.Connection[Any],
) -> None:
    """Takings are unchanged in meaning: money when it is taken. The pickup moves no money."""

    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    order.pay(collected=False, at=NOW)

    with connection.cursor() as cursor:
        dropoff_day = SettlementRepository.collected_today(
            cursor, store_id=store_id, principal=staff, as_of=NOW
        )
    assert (dropoff_day.collected_vnd, dropoff_day.settlement_count) == (QUOTED_TOTAL, 1)

    order.wash_to(ProductionStatus.RELEASED)
    order.collect()
    with connection.cursor() as cursor:
        pickup_day = SettlementRepository.collected_today(
            cursor, store_id=store_id, principal=staff, as_of=NOW + timedelta(days=1)
        )
        same_day_again = SettlementRepository.collected_today(
            cursor, store_id=store_id, principal=staff, as_of=NOW
        )
    assert (pickup_day.collected_vnd, pickup_day.settlement_count) == (0, 0)
    assert same_day_again.collected_vnd == QUOTED_TOTAL


# --- an_order_completes_only_when_paid_released_and_collected ------------------------------------


def test_a_prepaid_order_completes_only_when_paid_released_and_collected(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    order.pay(collected=False)

    # Paid, not released.
    with pytest.raises(OrderStateError, match="production is not released"):
        order.move(commercial_target=CommercialOrderStatus.COMPLETED)
    order.wash_to(ProductionStatus.RELEASED)
    # Paid and released, not collected: the prepayment alone does not close it.
    with pytest.raises(OrderStateError, match="fulfillment is incomplete"):
        order.move(commercial_target=CommercialOrderStatus.COMPLETED)

    collected = order.collect()
    assert collected.self_collection_recorded is True
    assert order.row()[2:4] == ("PAID", True)

    completed = order.move(commercial_target=CommercialOrderStatus.COMPLETED)
    assert completed.commercial is CommercialOrderStatus.COMPLETED


# --- pickup_is_recorded_by_a_named_staff_member --------------------------------------------------


def test_pickup_is_recorded_by_the_staff_member_who_hands_the_goods_over(
    connection: psycopg.Connection[Any],
) -> None:
    """The morning shift takes the money, the evening shift hands the bag over. Two names."""

    store_id = _shop(connection)
    morning = _staff(connection, store_id, StaffRole.OPERATOR)
    evening = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, morning).to_active()
    paid = order.pay(collected=False)
    order.wash_to(ProductionStatus.READY_AT_STORE)

    collected = order.collect(evening)

    assert collected.collected_by_staff_id == evening.staff_user_id
    assert collected.settlement_id == paid.settlement_id
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT collected_by_staff_id, settlement_id, settlement_shape, store_id
            FROM order_collections WHERE order_id = %s
            """,
            (order.order_id,),
        )
        assert cursor.fetchall() == [
            (
                evening.staff_user_id,
                paid.settlement_id,
                "EXACT_PAYMENT_PREPAID_SELF_COLLECTION",
                store_id,
            )
        ]
        cursor.execute(
            "SELECT attested_by_staff_id FROM order_settlements WHERE order_id = %s",
            (order.order_id,),
        )
        assert cursor.fetchall() == [(morning.staff_user_id,)]
        # Invariant 5: the pickup commits with its event, audit and outbox rows.
        cursor.execute(
            """
            SELECT event_type FROM domain_events
            WHERE aggregate_type = 'ORDER_COLLECTION' AND aggregate_id = %s
            """,
            (order.order_id,),
        )
        assert cursor.fetchall() == [("ORDER_COLLECTION_RECORDED",)]
        cursor.execute(
            """
            SELECT action, actor_id FROM audit_events
            WHERE aggregate_type = 'ORDER_COLLECTION' AND aggregate_id = %s
            """,
            (order.order_id,),
        )
        assert cursor.fetchall() == [("ORDER_COLLECTION_RECORD", evening.staff_user_id)]
        cursor.execute(
            """
            SELECT event_type, idempotency_key FROM outbox_events
            WHERE aggregate_type = 'ORDER_COLLECTION' AND aggregate_id = %s
            """,
            (order.order_id,),
        )
        assert cursor.fetchall() == [
            ("order.collection_recorded.v1", f"order:{order.order_id}:collection")
        ]


def test_the_pickup_record_cannot_be_rewritten(connection: psycopg.Connection[Any]) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    other = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    order.pay(collected=False)
    order.wash_to(ProductionStatus.RELEASED)
    order.collect()

    for statement, params in (
        (
            "UPDATE order_collections SET collected_by_staff_id = %s WHERE order_id = %s",
            (other.staff_user_id, order.order_id),
        ),
        ("DELETE FROM order_collections WHERE order_id = %s", (order.order_id,)),
    ):
        with pytest.raises(psycopg.Error), connection.transaction():
            connection.execute(statement, params)
    assert order.count("order_collections") == 1


# --- what the pickup command refuses, and that each refusal writes nothing -----------------------


def _assert_refused_and_nothing_written(
    order: _Order, reason_code: str, *, version: int | None = None, by: Any = None
) -> None:
    before = order.row()
    with pytest.raises(SettlementStateError) as refused:
        order.collect(by, version=version)
    assert refused.value.reason_code == reason_code
    assert order.row() == before
    assert order.count("order_collections") == 0


def test_pickup_before_payment_is_refused(connection: psycopg.Connection[Any]) -> None:
    """An unpaid customer pays at pickup through the settlement, which records both at once."""

    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active().wash_to(ProductionStatus.RELEASED)
    _assert_refused_and_nothing_written(order, "COLLECTION_REQUIRES_PAYMENT")


@pytest.mark.parametrize(
    "stage",
    [ProductionStatus.QUEUED, ProductionStatus.IN_PROCESS, ProductionStatus.QUALITY_CHECK],
)
def test_pickup_of_unfinished_laundry_is_refused(
    connection: psycopg.Connection[Any], stage: ProductionStatus
) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    order.pay(collected=False)
    order.wash_to(stage)
    _assert_refused_and_nothing_written(order, "GOODS_NOT_READY_FOR_HANDOVER")


def test_pickup_of_laundry_not_yet_started_is_refused(connection: psycopg.Connection[Any]) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    order.pay(collected=False)
    _assert_refused_and_nothing_written(order, "GOODS_NOT_READY_FOR_HANDOVER")


def test_a_second_pickup_is_refused(connection: psycopg.Connection[Any]) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    order.pay(collected=False)
    order.wash_to(ProductionStatus.RELEASED)
    order.collect()
    before = order.row()
    with pytest.raises(SettlementStateError) as refused:
        order.collect()
    assert refused.value.reason_code == "ALREADY_COLLECTED"
    assert order.row() == before
    assert order.count("order_collections") == 1


def test_an_order_paid_and_collected_at_pickup_has_no_second_pickup(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active().wash_to(ProductionStatus.RELEASED)
    order.pay(collected=True)
    _assert_refused_and_nothing_written(order, "ALREADY_COLLECTED")


def test_a_prepaid_delivery_is_not_collected_at_the_counter(
    connection: psycopg.Connection[Any],
) -> None:
    """A delivery reaches the customer by a leg (`DEC-023`), never by this command."""

    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN).to_active()
    order.pay(collected=False)
    order.wash_to(ProductionStatus.RELEASED)
    _assert_refused_and_nothing_written(order, "NOT_A_PREPAID_SELF_COLLECTION")


def test_a_stale_row_version_is_refused(connection: psycopg.Connection[Any]) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    order.pay(collected=False)
    order.wash_to(ProductionStatus.RELEASED)
    _assert_refused_and_nothing_written(order, "STALE_VERSION", version=order.version - 1)


def test_another_stores_staff_and_a_driver_cannot_record_a_pickup(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    order.pay(collected=False)
    order.wash_to(ProductionStatus.RELEASED)

    stranger = _staff(connection, _shop(connection), StaffRole.OWNER_ADMIN)
    with pytest.raises((SettlementAuthorizationError, StoreAccessError)):
        order.collect(stranger)
    driver = _staff(connection, store_id, StaffRole.DRIVER)
    with pytest.raises(SettlementAuthorizationError):
        order.collect(driver)
    assert order.count("order_collections") == 0
    assert order.row()[3] is False


# --- partial_payment_is_still_refused ------------------------------------------------------------


@pytest.mark.parametrize(
    "amount", [QUOTED_TOTAL - 1, QUOTED_TOTAL + 1, 0, QUOTED_TOTAL // 2, QUOTED_TOTAL * 2]
)
def test_a_deposit_or_any_other_amount_at_drop_off_is_still_refused(
    connection: psycopg.Connection[Any], amount: int
) -> None:
    """`DEC-010` untouched: part payment, a deposit, an overpayment -- one refusal, no row."""

    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    before = order.row()

    with pytest.raises(SettlementStateError) as refused:
        order.pay(collected=False, amount=amount)

    assert _refused(refused) == ("AMOUNT_IS_NOT_THE_EXACT_TOTAL", "DEC-010")
    assert order.row() == before
    assert order.count("order_settlements") == 0


# --- the staging review's minor finding: no handover of unwashed goods ---------------------------


@pytest.mark.parametrize(
    "stage",
    [
        None,
        ProductionStatus.QUEUED,
        ProductionStatus.IN_PROCESS,
        ProductionStatus.QUALITY_CHECK,
    ],
)
def test_ticking_collected_before_the_laundry_is_ready_is_refused(
    connection: psycopg.Connection[Any], stage: ProductionStatus | None
) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    if stage is not None:
        order.wash_to(stage)
    before = order.row()

    with pytest.raises(SettlementStateError) as refused:
        order.pay(collected=True)

    assert refused.value.reason_code == "GOODS_NOT_READY_FOR_HANDOVER"
    assert order.row() == before
    assert order.count("order_settlements") == 0


# --- positive controls: the paths that already worked still work ---------------------------------


@pytest.mark.parametrize("stage", [ProductionStatus.READY_AT_STORE, ProductionStatus.RELEASED])
def test_pay_at_pickup_is_unchanged(
    connection: psycopg.Connection[Any], stage: ProductionStatus
) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active().wash_to(stage)

    paid = order.pay(collected=True)

    assert paid.settlement_shape == "EXACT_PAYMENT_SELF_COLLECTION"
    assert paid.self_collection_recorded is True
    assert order.count("order_collections") == 0
    if stage is ProductionStatus.READY_AT_STORE:
        order.move(production_target=ProductionStatus.RELEASED)
    completed = order.move(commercial_target=CommercialOrderStatus.COMPLETED)
    assert completed.commercial is CommercialOrderStatus.COMPLETED


def test_delivery_prepay_is_unchanged(connection: psycopg.Connection[Any]) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN).to_active()

    paid = order.pay(collected=False)

    assert paid.settlement_shape == "EXACT_PAYMENT_PREPAID_DELIVERY"
    assert paid.self_collection_recorded is False
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT collected_by FROM order_settlements WHERE order_id = %s", (order.order_id,)
        )
        assert cursor.fetchall() == [("PENDING_DELIVERY",)]


def test_a_pickup_only_order_still_cannot_be_prepaid(connection: psycopg.Connection[Any]) -> None:
    """`DEC-032` speaks of a customer dropping laundry off; a `PICKUP_ONLY` customer does not. The
    shop's courier collects it and no driver carries money (`DEC-023`), so there is no drop-off
    moment to pay at. Unchanged, and fail-closed until somebody decides otherwise."""

    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff, FulfillmentMode.PICKUP_ONLY).to_active()
    with pytest.raises(SettlementStateError) as refused:
        order.pay(collected=False)
    assert refused.value.reason_code == "COLLECTION_WAS_NOT_BY_THE_CUSTOMER"


# --- the schema says the same things, for the code path nobody has written yet -------------------


def test_the_schema_refuses_a_collection_for_anything_but_a_prepaid_walk_in(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active().wash_to(ProductionStatus.RELEASED)
    paid = order.pay(collected=True)

    with pytest.raises(psycopg.Error), connection.transaction():
        connection.execute(
            """
            INSERT INTO order_collections (
                id, order_id, store_id, settlement_id, settlement_shape, collected_by_staff_id,
                collected_at, correlation_id, created_at
            ) VALUES (%s, %s, %s, %s, 'EXACT_PAYMENT_PREPAID_SELF_COLLECTION', %s, %s, %s, %s)
            """,
            (
                uuid4(),
                order.order_id,
                store_id,
                paid.settlement_id,
                staff.staff_user_id,
                NOW,
                uuid4(),
                NOW,
            ),
        )


def test_the_schema_refuses_marking_a_prepaid_order_collected_without_a_record(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = _shop(connection)
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order = _Order(connection, store_id, staff).to_active()
    order.pay(collected=False)
    order.wash_to(ProductionStatus.RELEASED)

    with (
        pytest.raises(psycopg.Error, match="without a collection record"),
        connection.transaction(),
    ):
        connection.execute(
            """
            UPDATE orders SET self_collection_recorded = TRUE, row_version = row_version + 1
            WHERE id = %s
            """,
            (order.order_id,),
        )
    assert order.row()[3] is False
