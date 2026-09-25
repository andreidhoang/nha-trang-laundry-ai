"""`SHOP-CAPTURE-001` (`DEC-038`) against real PostgreSQL.

What is proved here needs the database: that a wash cycle opens and closes inside the step's own
transaction (and vanishes with it when the step fails), that skipping the machine is a counted
state, that a rewash opens a second cycle and a hold does not, that a trip cost rides on its leg
and its note never reaches a ledger payload, that Sổ thu chi totals are PostgreSQL's sums, and that
the report's margin is withheld until the month is complete.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegKind,
    DeliveryLegOutcome,
    DeliveryLegRepository,
    RecordDeliveryLegCommand,
)
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderStateError,
    OrderStepCommand,
    OrderStepResult,
)
from nha_trang_laundry_db.reports import ReportRepository, shop_today
from nha_trang_laundry_db.settlement import SettlementCommand, SettlementRepository
from nha_trang_laundry_db.shop_capture import (
    CreateMachineCommand,
    ExpenseRepository,
    MachineRepository,
    MachineSeed,
    MachineUnavailableError,
    RecordExpenseCommand,
    ShopCaptureAuthorizationError,
    ShopCaptureNotFound,
    ShopCaptureRefusal,
    UpdateMachineCommand,
    VoidExpenseCommand,
    machine_seeds_from_master,
    order_capture,
    seed_machines,
)
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    FulfillmentMode,
    ProductionStatus,
    RewashReason,
    Unit,
)
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.shop_capture import (
    CYCLE_START_CATEGORIES,
    ExpenseCategory,
    MachineCategory,
    MarginStatus,
    TripCost,
    Vehicle,
    WeightBasis,
)
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from quote_test_data import FixtureLine, accepted_quote, ensure_store

NOW = datetime.now(UTC).replace(microsecond=0)
TODAY = shop_today(NOW)

SEEDS = (
    MachineSeed("WASH-01", "Máy giặt SPINZ 32 kg", MachineCategory.WASHER),
    MachineSeed("WASH-02", "Máy giặt LG 13 kg", MachineCategory.WASHER),
    MachineSeed("DRY-01", "Máy sấy SPINZ 35 kg", MachineCategory.DRYER),
    MachineSeed("DC-01", "Máy giặt khô SPINZ 10 kg", MachineCategory.DRY_CLEANER),
)


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
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_id, role.value, NOW),
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
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", roles, mfa, uuid4())


class _Shop:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.store_id = uuid4()
        ensure_store(connection, self.store_id)
        self.owner = _person(connection, self.store_id, frozenset({StaffRole.OWNER_ADMIN}))
        self.staff = _person(connection, self.store_id, frozenset({StaffRole.OPERATOR}))
        created, present = seed_machines(
            connection, store_id=self.store_id, actor_id=self.owner.staff_user_id, seeds=SEEDS
        )
        assert (created, present) == (4, 0)
        connection.commit()
        with connection.cursor() as cursor:
            listed = MachineRepository.list(
                cursor, store_id=self.store_id, principal=self.staff, include_retired=True
            )
        self.machines = {machine.code: machine.machine_id for machine in listed.machines}

    def order(
        self,
        mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
        lines: tuple[FixtureLine, ...] | None = None,
        at: datetime | None = None,
    ) -> tuple[UUID, int]:
        quote_id, revision, quote, contact_id = accepted_quote(
            self.connection,
            store_id=self.store_id,
            principal=self.staff,
            fulfillment_mode=mode,
            lines=lines,
        )
        stored = OrderRepository().create(
            self.connection,
            CreateOrderCommand(
                self.store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                mode,
                self.staff,
                f"order-{uuid4().hex}",
                uuid4(),
                NOW,
                AcquisitionSource.WALK_IN,
            ),
        )
        received = self.step(stored.order_id, stored.row_version, OrderStep.RECEIVE, at=at)
        return stored.order_id, received.view.row_version

    def step(
        self,
        order_id: UUID,
        version: int,
        step: OrderStep,
        *,
        machine: str | None = None,
        machine_id: UUID | None = None,
        key: str | None = None,
        at: datetime | None = None,
        rewash_reason: RewashReason | None = None,
    ) -> OrderStepResult:
        return OrderRepository().execute_step(
            self.connection,
            OrderStepCommand(
                order_id=order_id,
                expected_row_version=version,
                principal=self.staff,
                idempotency_key=key or f"step-{uuid4().hex}",
                correlation_id=uuid4(),
                step=step,
                slot_approved=step is OrderStep.RECEIVE,
                occurred_at=at,
                rewash_reason=rewash_reason,
                machine_id=machine_id or (None if machine is None else self.machines[machine]),
            ),
        )


def _cycles(connection: Any, order_id: UUID) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.kind, m.code, c.started_at, c.ended_at
            FROM wash_cycles c LEFT JOIN machines m ON m.id = c.machine_id
            WHERE c.order_id = %s ORDER BY c.started_at, c.id
            """,
            (order_id,),
        )
        return [tuple(row) for row in cursor.fetchall()]


def _payloads(connection: Any, correlation_like: str) -> str:
    """Every event, audit and outbox payload written, as one searchable text."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT payload::text FROM domain_events
            UNION ALL SELECT details::text FROM audit_events
            UNION ALL SELECT payload::text FROM outbox_events
            """
        )
        return "\n".join(str(row[0]) for row in cursor.fetchall())


# --- machines ---------------------------------------------------------------------------------


def test_seeding_is_idempotent_and_never_undoes_the_owners_rename(connection: Any) -> None:
    shop = _Shop(connection)
    renamed, _ = MachineRepository().update(
        connection,
        UpdateMachineCommand(
            machine_id=shop.machines["WASH-02"],
            expected_row_version=1,
            principal=shop.owner,
            idempotency_key="rename-1",
            correlation_id=uuid4(),
            display_name="Máy LG nhỏ",
        ),
    )
    assert (renamed.display_name, renamed.row_version) == ("Máy LG nhỏ", 2)
    created, present = seed_machines(
        connection, store_id=shop.store_id, actor_id=shop.owner.staff_user_id, seeds=SEEDS
    )
    assert (created, present) == (0, 4)
    with connection.cursor() as cursor:
        listed = MachineRepository.list(cursor, store_id=shop.store_id, principal=shop.staff)
    names = {machine.code: machine.display_name for machine in listed.machines}
    assert names["WASH-02"] == "Máy LG nhỏ"
    # Each registration and the rename is a material change: event, audit and outbox together.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT (SELECT count(*) FROM domain_events WHERE aggregate_type = 'MACHINE'
                    AND aggregate_id = ANY(%(ids)s)),
                   (SELECT count(*) FROM audit_events WHERE aggregate_type = 'MACHINE'
                    AND aggregate_id = ANY(%(ids)s)),
                   (SELECT count(*) FROM outbox_events WHERE aggregate_type = 'MACHINE'
                    AND aggregate_id = ANY(%(ids)s))
            """,
            {"ids": list(shop.machines.values())},
        )
        assert tuple(cursor.fetchone()) == (5, 5, 5)


def test_only_an_owner_assigned_to_the_store_seeds_it(connection: Any) -> None:
    store_id = uuid4()
    ensure_store(connection, store_id)
    operator = _person(connection, store_id, frozenset({StaffRole.OPERATOR}))
    stranger_owner = _person(connection, None, frozenset({StaffRole.OWNER_ADMIN}))
    for actor in (operator, stranger_owner):
        with pytest.raises(ShopCaptureAuthorizationError):
            seed_machines(connection, store_id=store_id, actor_id=actor.staff_user_id, seeds=SEEDS)
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM machines WHERE store_id = %s", (store_id,))
        assert cursor.fetchone()[0] == 0


def test_the_owner_adds_renames_and_retires_and_nobody_else_can(connection: Any) -> None:
    shop = _Shop(connection)
    repository = MachineRepository()
    command = CreateMachineCommand(
        store_id=shop.store_id,
        code="wash-03",
        display_name="Máy giặt mới",
        category=MachineCategory.WASHER,
        principal=shop.owner,
        idempotency_key="add-wash-03",
        correlation_id=uuid4(),
    )
    added, replayed = repository.create(connection, command)
    assert (added.code, added.source, replayed) == ("WASH-03", "OWNER", False)
    again, replayed = repository.create(connection, command)
    assert (again.machine_id, replayed) == (added.machine_id, True)
    with pytest.raises(IdempotencyConflictError):
        repository.create(
            connection,
            CreateMachineCommand(
                **{**command.__dict__, "display_name": "Tên khác"}  # same key, changed payload
            ),
        )
    with pytest.raises(ShopCaptureRefusal) as taken:
        repository.create(
            connection,
            CreateMachineCommand(**{**command.__dict__, "idempotency_key": "add-again"}),
        )
    assert taken.value.reason_code == "MACHINE_CODE_TAKEN"
    with pytest.raises(ShopCaptureAuthorizationError):
        repository.create(
            connection,
            CreateMachineCommand(
                **{**command.__dict__, "principal": shop.staff, "idempotency_key": "op"}
            ),
        )
    retire = UpdateMachineCommand(
        machine_id=added.machine_id,
        expected_row_version=1,
        principal=shop.owner,
        idempotency_key="retire-3",
        correlation_id=uuid4(),
        retire=True,
    )
    retired, _ = repository.update(connection, retire)
    assert retired.retired_at is not None and retired.row_version == 2
    with pytest.raises(ShopCaptureRefusal) as stale:
        repository.update(
            connection,
            UpdateMachineCommand(
                **{
                    **retire.__dict__,
                    "expected_row_version": 2,
                    "idempotency_key": "rename-retired",
                    "retire": False,
                    "display_name": "X",
                }
            ),
        )
    assert stale.value.reason_code == "MACHINE_RETIRED"
    with connection.cursor() as cursor:
        active = MachineRepository.list(cursor, store_id=shop.store_id, principal=shop.staff)
        everything = MachineRepository.list(
            cursor, store_id=shop.store_id, principal=shop.owner, include_retired=True
        )
    assert "WASH-03" not in {machine.code for machine in active.machines}
    assert "WASH-03" in {machine.code for machine in everything.machines}
    # A member of another store is told the machine does not exist.
    elsewhere = uuid4()
    ensure_store(connection, elsewhere)
    other_owner = _person(connection, elsewhere, frozenset({StaffRole.OWNER_ADMIN}))
    with pytest.raises(ShopCaptureNotFound):
        repository.update(
            connection,
            UpdateMachineCommand(
                machine_id=shop.machines["WASH-01"],
                expected_row_version=1,
                principal=other_owner,
                idempotency_key="foreign",
                correlation_id=uuid4(),
                display_name="Của tôi",
            ),
        )


# --- wash cycles ------------------------------------------------------------------------------


def test_a_machine_chosen_at_start_wash_opens_a_cycle_that_quality_check_closes(
    connection: Any,
) -> None:
    shop = _Shop(connection)
    order_id, version = shop.order()
    started = shop.step(order_id, version, OrderStep.START_WASH, machine="WASH-01")
    assert started.view.production is ProductionStatus.IN_PROCESS
    [(kind, code, started_at, ended_at)] = _cycles(connection, order_id)
    assert (kind, code, ended_at) == ("WASH", "WASH-01", None)
    checked = shop.step(order_id, started.view.row_version, OrderStep.QUALITY_CHECK)
    [(_, _, _, ended_at)] = _cycles(connection, order_id)
    assert ended_at is not None and ended_at >= started_at
    assert checked.view.production is ProductionStatus.QUALITY_CHECK
    # The chooser lists the machine just used first ("remembering the last one used"), and only
    # the machines a load goes into.
    with connection.cursor() as cursor:
        chooser = MachineRepository.list(
            cursor, store_id=shop.store_id, principal=shop.staff, cycle_only=True
        )
    assert [machine.code for machine in chooser.machines] == ["WASH-01", "DC-01", "WASH-02"]
    # The cycle is its own aggregate with its own event, audit and outbox rows.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT e.event_type, e.payload FROM domain_events e
            WHERE e.aggregate_type = 'WASH_CYCLE' AND e.payload->>'order_id' = %s
            ORDER BY e.aggregate_version
            """,
            (str(order_id),),
        )
        events = cursor.fetchall()
        assert [row[0] for row in events] == ["WASH_CYCLE_STARTED", "WASH_CYCLE_ENDED"]
        assert events[0][1]["captured"] is True
        cursor.execute(
            """
            SELECT count(*) FROM outbox_events
            WHERE aggregate_type = 'WASH_CYCLE' AND payload->>'order_id' = %s
            """,
            (str(order_id),),
        )
        assert cursor.fetchone()[0] == 2


def test_skipping_the_machine_is_allowed_and_counted_as_not_captured(connection: Any) -> None:
    shop = _Shop(connection)
    order_id, version = shop.order()
    shop.step(order_id, version, OrderStep.START_WASH)
    [(kind, code, _, _)] = _cycles(connection, order_id)
    assert (kind, code) == ("WASH", None)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT payload->>'captured' FROM domain_events
            WHERE event_type = 'WASH_CYCLE_STARTED' AND payload->>'order_id' = %s
            """,
            (str(order_id),),
        )
        assert cursor.fetchone()[0] == "false"


def test_a_rewash_opens_a_second_cycle_and_a_hold_does_not(connection: Any) -> None:
    shop = _Shop(connection)
    order_id, version = shop.order()
    view = shop.step(order_id, version, OrderStep.START_WASH, machine="WASH-02").view
    view = shop.step(order_id, view.row_version, OrderStep.HOLD).view
    view = shop.step(order_id, view.row_version, OrderStep.RESUME).view
    assert view.production is ProductionStatus.IN_PROCESS
    assert len(_cycles(connection, order_id)) == 1  # the hold continued the same cycle
    view = shop.step(order_id, view.row_version, OrderStep.QUALITY_CHECK).view
    view = shop.step(
        order_id,
        view.row_version,
        OrderStep.REWASH,
        machine="WASH-01",
        rewash_reason=RewashReason.NOT_CLEAN,
    ).view
    assert view.production is ProductionStatus.IN_PROCESS
    cycles = _cycles(connection, order_id)
    assert [(row[0], row[1]) for row in cycles] == [("WASH", "WASH-02"), ("REWASH", "WASH-01")]
    assert cycles[0][3] is not None and cycles[1][3] is None
    shop.step(order_id, view.row_version, OrderStep.QUALITY_CHECK)
    assert all(row[3] is not None for row in _cycles(connection, order_id))


def test_a_machine_that_cannot_start_a_wash_refuses_the_whole_step(connection: Any) -> None:
    shop = _Shop(connection)
    order_id, version = shop.order()
    elsewhere = _Shop(connection)
    for machine_id in (
        shop.machines["DRY-01"],  # a dryer: the load does not go into it at Bắt đầu giặt
        elsewhere.machines["WASH-01"],  # another store's washer
        uuid4(),  # no such machine
    ):
        with pytest.raises(MachineUnavailableError):
            shop.step(order_id, version, OrderStep.START_WASH, machine_id=machine_id)
    retired, _ = MachineRepository().update(
        connection,
        UpdateMachineCommand(
            machine_id=shop.machines["WASH-02"],
            expected_row_version=1,
            principal=shop.owner,
            idempotency_key="retire",
            correlation_id=uuid4(),
            retire=True,
        ),
    )
    with pytest.raises(MachineUnavailableError):
        shop.step(order_id, version, OrderStep.START_WASH, machine_id=retired.machine_id)
    # Nothing was written: the order has not moved and has no cycle.
    assert _cycles(connection, order_id) == []
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT production_status, row_version FROM orders WHERE id = %s", (order_id,)
        )
        assert tuple(cursor.fetchone()) == ("NOT_STARTED", version)
    # And only a wash or a rewash names a machine.
    shop.step(order_id, version, OrderStep.START_WASH)
    with pytest.raises(OrderStateError, match="MACHINE_NOT_APPLICABLE"):
        shop.step(order_id, version + 2, OrderStep.QUALITY_CHECK, machine="WASH-01")


def test_the_cycle_commits_with_the_step_or_not_at_all(connection: Any) -> None:
    """A failure writing the cycle rolls the whole step back: no transition, no cycle, no key."""
    shop = _Shop(connection)
    order_id, version = shop.order()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE FUNCTION pg_temp.refuse_cycle() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'cycle write refused for the test'; END; $$
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER refuse_cycle_for_test BEFORE INSERT ON wash_cycles
            FOR EACH ROW EXECUTE FUNCTION pg_temp.refuse_cycle()
            """
        )
    try:
        with pytest.raises(psycopg.errors.RaiseException):
            shop.step(order_id, version, OrderStep.START_WASH, machine="WASH-01", key="atomic")
    finally:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER refuse_cycle_for_test ON wash_cycles")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT production_status, row_version FROM orders WHERE id = %s", (order_id,)
        )
        assert tuple(cursor.fetchone()) == ("NOT_STARTED", version)
        cursor.execute(
            "SELECT count(*) FROM command_idempotency_records WHERE scope = %s AND "
            "idempotency_key = 'atomic'",
            (f"order:{order_id}:step",),
        )
        assert cursor.fetchone()[0] == 0
    assert _cycles(connection, order_id) == []
    # The same key then succeeds, as a first execution.
    started = shop.step(order_id, version, OrderStep.START_WASH, machine="WASH-01", key="atomic")
    assert not started.replayed
    assert len(_cycles(connection, order_id)) == 1


def test_a_replayed_step_returns_the_first_answer_and_a_changed_machine_conflicts(
    connection: Any,
) -> None:
    shop = _Shop(connection)
    order_id, version = shop.order()
    first = shop.step(order_id, version, OrderStep.START_WASH, machine="WASH-01", key="k1")
    again = shop.step(order_id, version, OrderStep.START_WASH, machine="WASH-01", key="k1")
    assert again.replayed and again.view == first.view
    assert len(_cycles(connection, order_id)) == 1
    with pytest.raises(IdempotencyConflictError):
        shop.step(order_id, version, OrderStep.START_WASH, machine="WASH-02", key="k1")


# --- trip costs -------------------------------------------------------------------------------


def _leg(
    connection: Any, shop: _Shop, order_id: UUID, kind: DeliveryLegKind, trip: TripCost | None
) -> UUID:
    return (
        DeliveryLegRepository()
        .record(
            connection,
            RecordDeliveryLegCommand(
                order_id=order_id,
                leg_kind=kind,
                outcome=DeliveryLegOutcome.SUCCEEDED,
                principal=shop.staff,
                correlation_id=uuid4(),
                trip=trip,
            ),
        )
        .leg_id
    )


def test_a_trip_cost_rides_on_its_leg_and_its_note_reaches_no_ledger(connection: Any) -> None:
    shop = _Shop(connection)
    lines = (FixtureLine("line-1", "WET_KG", Unit.KG, "20", 20_000, 400_000),)
    order_id, _ = shop.order(FulfillmentMode.PICKUP_AND_RETURN, lines)
    note = "gửi xe chợ Đầm ghi-chú-riêng"
    trip = TripCost(Vehicle.O_TO, Decimal("6.5"), 45_000, note)
    leg_id = _leg(connection, shop, order_id, DeliveryLegKind.PICKUP, trip)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT vehicle, km, cost_vnd, note FROM delivery_leg_costs WHERE leg_id = %s",
            (leg_id,),
        )
        assert tuple(cursor.fetchone()) == ("O_TO", Decimal("6.5"), 45_000, note)
        cursor.execute("SELECT payload FROM domain_events WHERE aggregate_id = %s", (leg_id,))
        assert cursor.fetchone()[0]["trip_recorded"] is True
    assert "ghi-chú-riêng" not in _payloads(connection, "")
    # An empty trip writes no row, and the leg's event is exactly what it was before costs.
    empty_leg = _leg(
        connection, shop, order_id, DeliveryLegKind.RETURN, TripCost(None, None, None, None)
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM delivery_leg_costs WHERE leg_id = %s", (empty_leg,))
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT payload FROM domain_events WHERE aggregate_id = %s", (empty_leg,))
        assert "trip_recorded" not in cursor.fetchone()[0]
    # The order page's read: the owner's rule on 20 kg, the cycles and the legs with their costs.
    with connection.cursor() as cursor:
        capture = order_capture(cursor, order_id=order_id, principal=shop.staff)
    assert capture.suggestion.vehicle is Vehicle.O_TO
    assert capture.suggestion.weight_kg == Decimal("20")
    assert capture.suggestion.basis is WeightBasis.MEASURED
    assert [(leg.leg_kind, leg.cost_vnd) for leg in capture.legs] == [
        ("PICKUP", 45_000),
        ("RETURN", None),
    ]
    stranger = _person(connection, None, frozenset({StaffRole.OPERATOR}))
    with connection.cursor() as cursor, pytest.raises(ShopCaptureNotFound):
        order_capture(cursor, order_id=order_id, principal=stranger)


# --- Sổ thu chi -------------------------------------------------------------------------------


def _expense(
    connection: Any,
    shop: _Shop,
    category: ExpenseCategory,
    amount: int,
    *,
    spent_on: date | None = None,
    principal: StaffPrincipal | None = None,
    key: str | None = None,
    note: str | None = None,
) -> Any:
    view, _ = ExpenseRepository().record(
        connection,
        RecordExpenseCommand(
            store_id=shop.store_id,
            spent_on=spent_on or TODAY,
            category=category,
            amount_vnd=amount,
            note=note,
            principal=principal or shop.owner,
            idempotency_key=key or f"expense-{uuid4().hex}",
            correlation_id=uuid4(),
            today=TODAY,
        ),
    )
    return view


def test_a_month_of_expenses_is_summed_by_postgresql_and_a_void_stops_counting(
    connection: Any,
) -> None:
    shop = _Shop(connection)
    accountant = _person(connection, shop.store_id, frozenset({StaffRole.ACCOUNTANT}))
    auditor = _person(connection, shop.store_id, frozenset({StaffRole.AUDITOR}))
    first = TODAY.replace(day=1)
    _expense(connection, shop, ExpenseCategory.DIEN, 1_250_000, spent_on=first, note="tiền điện")
    _expense(connection, shop, ExpenseCategory.DIEN, 50_000, principal=accountant)
    wrong = _expense(connection, shop, ExpenseCategory.HOA_CHAT, 9_000_000)
    _expense(connection, shop, ExpenseCategory.HOA_CHAT, 900_000)
    # Last month's line is not this month's.
    _expense(connection, shop, ExpenseCategory.NUOC, 300_000, spent_on=first - timedelta(days=1))
    voided, _ = ExpenseRepository().void(
        connection,
        VoidExpenseCommand(
            expense_id=wrong.expense_id,
            expected_row_version=1,
            principal=accountant,
            idempotency_key="void-1",
            correlation_id=uuid4(),
        ),
    )
    assert voided.voided_at is not None and voided.row_version == 2
    with pytest.raises(ShopCaptureRefusal) as again:
        ExpenseRepository().void(
            connection,
            VoidExpenseCommand(
                expense_id=wrong.expense_id,
                expected_row_version=2,
                principal=shop.owner,
                idempotency_key="void-2",
                correlation_id=uuid4(),
            ),
        )
    assert again.value.reason_code == "EXPENSE_ALREADY_VOIDED"
    with connection.cursor() as cursor:
        month = ExpenseRepository.month(
            cursor, store_id=shop.store_id, principal=auditor, month=TODAY.strftime("%Y-%m")
        )
    totals = {total.category: (total.amount_vnd, total.entries) for total in month.totals}
    assert totals[ExpenseCategory.DIEN] == (1_300_000, 2)
    assert totals[ExpenseCategory.HOA_CHAT] == (900_000, 1)
    assert totals[ExpenseCategory.NUOC] == (0, 0)
    assert (month.total_vnd, month.entries, month.voided_entries) == (2_200_000, 3, 1)
    assert len(month.lines) == 4  # the voided line is still listed, marked
    assert not month.truncated


def test_expense_writes_are_the_owners_and_the_accountants(connection: Any) -> None:
    shop = _Shop(connection)
    auditor = _person(connection, shop.store_id, frozenset({StaffRole.AUDITOR}))
    for principal in (
        shop.staff,
        auditor,
        _person(connection, shop.store_id, frozenset({StaffRole.OWNER_ADMIN}), mfa=False),
        _person(connection, None, frozenset({StaffRole.OWNER_ADMIN})),
    ):
        with pytest.raises(ShopCaptureAuthorizationError):
            _expense(connection, shop, ExpenseCategory.KHAC, 10_000, principal=principal)
    with connection.cursor() as cursor, pytest.raises(ShopCaptureAuthorizationError):
        ExpenseRepository.month(
            cursor, store_id=shop.store_id, principal=shop.staff, month="2026-09"
        )


def test_expenses_are_idempotent_and_refuse_a_phone_in_the_note(connection: Any) -> None:
    shop = _Shop(connection)
    first = _expense(connection, shop, ExpenseCategory.LUONG, 7_000_000, key="salary")
    again = _expense(connection, shop, ExpenseCategory.LUONG, 7_000_000, key="salary")
    assert again.expense_id == first.expense_id
    with pytest.raises(IdempotencyConflictError):
        _expense(connection, shop, ExpenseCategory.LUONG, 7_500_000, key="salary")
    for note, code in (("gọi 0382 318 492", "NOTE_LOOKS_LIKE_PHONE"), ("x" * 121, "NOTE_INVALID")):
        with pytest.raises(ShopCaptureRefusal) as refused:
            _expense(connection, shop, ExpenseCategory.KHAC, 1_000, note=note)
        assert refused.value.reason_code == code
    with pytest.raises(ShopCaptureRefusal) as future:
        _expense(connection, shop, ExpenseCategory.KHAC, 1_000, spent_on=TODAY + timedelta(days=1))
    assert future.value.reason_code == "EXPENSE_DATE_IN_FUTURE"
    # No note text and no amount beyond the event reaches the audit or outbox rows.
    _expense(connection, shop, ExpenseCategory.KHAC, 5_000, note="mua-chổi-lau-nhà")
    assert "mua-chổi-lau-nhà" not in _payloads(connection, "")


# --- the report ---------------------------------------------------------------------------------


def test_the_report_counts_capture_and_withholds_margin_until_the_month_is_complete(
    connection: Any,
) -> None:
    shop = _Shop(connection)
    base = NOW - timedelta(hours=3)
    # One wash on WASH-01 of exactly 42 minutes, one skipped, one still open on WASH-01.
    timed, version = shop.order(at=base - timedelta(minutes=5))
    view = shop.step(timed, version, OrderStep.START_WASH, machine="WASH-01", at=base).view
    shop.step(timed, view.row_version, OrderStep.QUALITY_CHECK, at=base + timedelta(minutes=42))
    skipped, version = shop.order(at=base - timedelta(minutes=5))
    view = shop.step(skipped, version, OrderStep.START_WASH, at=base).view
    shop.step(skipped, view.row_version, OrderStep.QUALITY_CHECK, at=base + timedelta(minutes=50))
    running, version = shop.order(at=base - timedelta(minutes=5))
    shop.step(running, version, OrderStep.START_WASH, machine="WASH-01", at=base)
    # Two delivered orders: one costed on both legs (20.000 + 25.001), one with an uncosted pickup.
    costed, _ = shop.order(FulfillmentMode.PICKUP_AND_RETURN)
    _leg(
        connection,
        shop,
        costed,
        DeliveryLegKind.PICKUP,
        TripCost(Vehicle.XE_MAY, None, 20_000, None),
    )
    _leg(
        connection,
        shop,
        costed,
        DeliveryLegKind.RETURN,
        TripCost(Vehicle.XE_MAY, None, 25_001, None),
    )
    partial, _ = shop.order(FulfillmentMode.PICKUP_AND_RETURN)
    _leg(connection, shop, partial, DeliveryLegKind.PICKUP, None)
    _leg(
        connection,
        shop,
        partial,
        DeliveryLegKind.RETURN,
        TripCost(Vehicle.XE_MAY, None, 9_000, None),
    )
    # Money in, and two expenses: not a complete month yet.
    SettlementRepository().record(
        connection,
        SettlementCommand(
            order_id=timed,
            paid_amount_vnd=110_000,
            collected_by_customer=False,
            principal=shop.staff,
            correlation_id=uuid4(),
        ),
    )
    _expense(connection, shop, ExpenseCategory.DIEN, 30_000)
    _expense(connection, shop, ExpenseCategory.HOA_CHAT, 20_000)
    connection.commit()

    def read() -> Any:
        with connection.cursor() as cursor:
            return ReportRepository.store_report(
                cursor,
                store_id=shop.store_id,
                principal=shop.owner,
                policy=STANDARD_WASH_SLA,
                from_date=TODAY - timedelta(days=1),
                to_date=TODAY,
                as_of=datetime.now(UTC),
            )

    report = read()
    capture = report.capture
    assert (capture.cycles, capture.cycles_captured) == (3, 2)
    [machine] = capture.machines
    assert (machine.code, machine.closed_cycles, machine.average_minutes) == ("WASH-01", 1, 42)
    assert (capture.delivered_orders, capture.costed_orders) == (2, 1)
    assert (capture.legs, capture.costed_legs, capture.trip_cost_vnd) == (4, 3, 45_001)
    assert capture.cost_per_delivered_order_vnd == 45_001
    month = report.months[-1]
    assert month.month == TODAY.strftime("%Y-%m") and month.in_progress
    assert month.spending_vnd == 50_000 and month.collected_vnd == 110_000
    assert month.margin.status is MarginStatus.INCOMPLETE
    assert month.margin.amount_vnd is None
    assert [category.value for category in month.margin.missing] == ["NUOC", "LUONG", "MAT_BANG"]
    # Completing the month's core categories shows the figure, with its direction.
    for category, amount in (
        (ExpenseCategory.NUOC, 10_000),
        (ExpenseCategory.LUONG, 15_000),
        (ExpenseCategory.MAT_BANG, 5_000),
    ):
        _expense(connection, shop, category, amount)
    connection.commit()
    complete = read().months[-1]
    assert complete.margin.status is MarginStatus.COMPLETE
    assert (complete.margin.amount_vnd, complete.margin.direction) == (30_000, "IN")
    assert json.dumps([m.month for m in read().months])


def test_the_machine_master_seeds_the_confirmed_machines_with_their_categories() -> None:
    """`templates/machine-master.csv`: eight machines, all confirmed present and active by the
    owner. Four take a load at Bắt đầu giặt; the dryers and irons are listed but never offered."""
    template = Path(__file__).resolve().parents[3] / "templates" / "machine-master.csv"
    text = template.read_text(encoding="utf-8")
    seeds = machine_seeds_from_master(text)
    assert [seed.code for seed in seeds] == [
        "WASH-01",
        "WASH-02",
        "DRY-01",
        "DRY-02",
        "DC-01",
        "SHOE-01",
        "IRON-TABLE-01",
        "STEAM-IRON-01",
    ]
    assert seeds[3].display_name == "Máy sấy LG 10,2 kg"
    offered = {seed.code for seed in seeds if seed.category in CYCLE_START_CATEGORIES}
    assert offered == {"WASH-01", "WASH-02", "DC-01", "SHOE-01"}
    # A row the owner has not confirmed present and active is left out, never guessed in.
    header = text.splitlines()[0]
    row = "WASH-09,DOC-X,washer,LG,X,1,10,1,1,PENDING,OWNER_CONFIRMED_ACTIVE,100,,,,2026-07-27,x"
    assert machine_seeds_from_master(f"{header}\n{row}\n") == ()
