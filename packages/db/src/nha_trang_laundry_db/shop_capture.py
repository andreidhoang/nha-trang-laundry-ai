"""Machines, wash cycles, trip costs and Sổ thu chi (`SHOP-CAPTURE-001`, `DEC-038`).

The rules are `nha_trang_laundry_domain.shop_capture`'s; this module stores and reads them.

* **Machines** are seeded from `templates/machine-master.csv` (`seed_machines`, run by
  `scripts/seed_machines.py`), and the owner adds, renames or retires one. Every change is a
  material change: the row, a `MACHINE_*` domain event, an audit row and an outbox row, together.
* **Wash cycles** are opened and closed by `apply_cycle_effect`, which `OrderRepository` calls from
  inside the order transition that moves production -- the same transaction, so a step that
  commits has its cycle and a step that is refused has none. The cycle is its own aggregate
  (`WASH_CYCLE`) with its own event, audit and outbox rows, so the order's transition events keep
  exactly the payloads they always had.
* **Trip costs** are one optional row per delivery leg, written by `insert_trip_cost` inside the
  leg's own mutation.
* **Sổ thu chi** lines are recorded and voided here, and a month is read with its totals summed by
  PostgreSQL. Python never adds an amount.

No note is ever copied into an event, audit or outbox payload: a note is the shop's own words
about a cost, stored once, read by the people allowed to read the books.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import ProductionStatus
from nha_trang_laundry_domain.shop_capture import (
    CycleEffect,
    ExpenseCategory,
    MachineCategory,
    ShopCaptureError,
    TripCost,
    Vehicle,
    VehicleSuggestion,
    WeighedLine,
    cycle_effect,
    expense,
    machine_code,
    machine_name,
    missing_core_categories,
    month_bounds,
    starts_cycle,
    suggest_vehicle,
)
from psycopg.errors import UniqueViolation

from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.store_access import is_store_member, require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Who reads the machine list: the counter picks from it, the auditor reads it.
MACHINE_READ_ROLES: Final = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR, StaffRole.AUDITOR}
)
#: `DEC-038`: "the owner can add, rename or retire one".
MACHINE_WRITE_ROLES: Final = frozenset({StaffRole.OWNER_ADMIN})
#: Sổ thu chi is the owner's and the accountant's to write, and the auditor's to read.
EXPENSE_WRITE_ROLES: Final = frozenset({StaffRole.OWNER_ADMIN, StaffRole.ACCOUNTANT})
EXPENSE_READ_ROLES: Final = EXPENSE_WRITE_ROLES | {StaffRole.AUDITOR}
#: The order page's capture read: the roles that read an order.
ORDER_CAPTURE_READ_ROLES: Final = MACHINE_READ_ROLES

MACHINE_LIST_LIMIT: Final = 100
EXPENSE_LIST_LIMIT: Final = 200


class ShopCaptureAuthorizationError(PermissionError):
    """Wrong role, no MFA, or not a member of the store. One opaque refusal for all three."""


class ShopCaptureNotFound(LookupError):
    """The machine, expense or order does not exist in a store the caller belongs to."""


class ShopCaptureRefusal(ValueError):
    """A named refusal: `reason_code` is stable and the console words it."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def _refusal(error: ShopCaptureError) -> ShopCaptureRefusal:
    return ShopCaptureRefusal(error.reason_code, str(error))


def _require(principal: StaffPrincipal, roles: frozenset[StaffRole]) -> None:
    if not principal.roles & roles or not principal.mfa_verified:
        raise ShopCaptureAuthorizationError("this shop record requires another role with MFA")


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


# --- machines ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MachineView:
    machine_id: UUID
    store_id: UUID
    code: str
    display_name: str
    category: MachineCategory
    starts_cycle: bool
    source: str
    retired_at: datetime | None
    row_version: int
    last_used_at: datetime | None
    cycles: int


@dataclass(frozen=True, slots=True)
class MachineList:
    store_id: UUID
    machines: tuple[MachineView, ...]
    truncated: bool


_MACHINE_COLUMNS: Final = """
    m.id, m.store_id, m.code, m.display_name, m.category, m.source, m.retired_at, m.row_version,
    (SELECT max(c.started_at) FROM wash_cycles c WHERE c.machine_id = m.id) AS last_used_at,
    (SELECT count(*) FROM wash_cycles c WHERE c.machine_id = m.id) AS cycles
"""


def _machine(row: Any) -> MachineView:
    category = MachineCategory(str(row[4]))
    return MachineView(
        machine_id=_uuid(row[0]),
        store_id=_uuid(row[1]),
        code=str(row[2]),
        display_name=str(row[3]),
        category=category,
        starts_cycle=starts_cycle(category),
        source=str(row[5]),
        retired_at=row[6],
        row_version=int(row[7]),
        last_used_at=row[8],
        cycles=int(row[9]),
    )


@dataclass(frozen=True, slots=True)
class MachineSeed:
    code: str
    display_name: str
    category: MachineCategory


@dataclass(frozen=True)
class CreateMachineCommand:
    store_id: UUID
    code: str
    display_name: str
    category: MachineCategory
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class UpdateMachineCommand:
    machine_id: UUID
    expected_row_version: int
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    display_name: str | None = None
    retire: bool = False
    occurred_at: datetime | None = None


def _machine_change(
    *,
    machine_id: UUID,
    version: int,
    event_type: str,
    payload: dict[str, object],
    audit_action: str,
    actor_id: UUID | None,
    actor_type: str,
    correlation_id: UUID,
    occurred_at: datetime,
) -> MaterialChange:
    return MaterialChange(
        aggregate_type="MACHINE",
        aggregate_id=machine_id,
        aggregate_version=version,
        event_type=event_type,
        event_payload=payload,
        audit_action=audit_action,
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=correlation_id,
        outbox_events=(
            OutboxEvent(
                "shop.machine_changed.v1",
                {"machine_id": str(machine_id), "row_version": version},
                f"machine:{machine_id}:version:{version}",
            ),
        ),
        occurred_at=occurred_at,
    )


class MachineRepository:
    """The machine list: read by the counter, seeded by a script, edited by the owner."""

    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()

    @staticmethod
    def list(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        cycle_only: bool = False,
        include_retired: bool = False,
    ) -> MachineList:
        """The store's machines, the most recently used first (`DEC-038`: "remembering the last
        one used"), then by code. `cycle_only` keeps the machines a wash can start on, retired
        ones never among them."""
        _require(principal, MACHINE_READ_ROLES)
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=ShopCaptureAuthorizationError,
        )
        cursor.execute(
            f"""
            SELECT {_MACHINE_COLUMNS}
            FROM machines m
            WHERE m.store_id = %s AND (%s OR m.retired_at IS NULL)
            ORDER BY (m.retired_at IS NOT NULL), last_used_at DESC NULLS LAST, m.code
            LIMIT %s
            """,
            (store_id, include_retired and not cycle_only, MACHINE_LIST_LIMIT + 1),
        )
        rows = [_machine(row) for row in cursor.fetchall()]
        truncated = len(rows) > MACHINE_LIST_LIMIT
        machines = tuple(
            machine
            for machine in rows[:MACHINE_LIST_LIMIT]
            if not cycle_only or machine.starts_cycle
        )
        return MachineList(store_id=store_id, machines=machines, truncated=truncated)

    def create(self, connection: Any, command: CreateMachineCommand) -> tuple[MachineView, bool]:
        """The owner adds a machine. Idempotent on the key; a code already used is refused."""
        _require(command.principal, MACHINE_WRITE_ROLES)
        with connection.cursor() as cursor:
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=command.store_id,
                error=ShopCaptureAuthorizationError,
            )
        try:
            code = machine_code(command.code)
            name = machine_name(command.display_name)
        except ShopCaptureError as error:
            raise _refusal(error) from error
        moment = command.occurred_at or datetime.now(UTC)
        machine_id = uuid4()

        def create_once() -> dict[str, object]:
            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO machines (
                        id, store_id, code, display_name, category, source, created_by,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 'OWNER', %s, %s, %s)
                    """,
                    (
                        machine_id,
                        command.store_id,
                        code,
                        name,
                        command.category.value,
                        command.principal.staff_user_id,
                        moment,
                        moment,
                    ),
                )

            try:
                commit_material_change(
                    connection,
                    _machine_change(
                        machine_id=machine_id,
                        version=1,
                        event_type="MACHINE_REGISTERED",
                        payload={
                            "machine_id": str(machine_id),
                            "store_id": str(command.store_id),
                            "code": code,
                            "category": command.category.value,
                            "source": "OWNER",
                        },
                        audit_action="MACHINE_REGISTER",
                        actor_id=command.principal.staff_user_id,
                        actor_type="STAFF",
                        correlation_id=command.correlation_id,
                        occurred_at=moment,
                    ),
                    mutation,
                )
            except UniqueViolation as error:
                raise ShopCaptureRefusal(
                    "MACHINE_CODE_TAKEN", "this store already has a machine with that code"
                ) from error
            return {"machine_id": str(machine_id)}

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"store:{command.store_id}:machine-create",
                command.idempotency_key,
                {"code": code, "display_name": name, "category": command.category.value},
                moment,
            ),
            create_once,
        )
        with connection.cursor() as cursor:
            return (
                _read_machine(cursor, _uuid(result.response["machine_id"])),
                result.replayed,
            )

    def update(self, connection: Any, command: UpdateMachineCommand) -> tuple[MachineView, bool]:
        """Rename or retire a machine, with the row version the owner read (`If-Match`)."""
        _require(command.principal, MACHINE_WRITE_ROLES)
        with connection.cursor() as cursor:
            cursor.execute("SELECT store_id FROM machines WHERE id = %s", (command.machine_id,))
            found = cursor.fetchone()
            if found is None:
                raise ShopCaptureNotFound("machine is missing")
            # A member of another store is told the machine does not exist.
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=_uuid(found[0]),
                error=ShopCaptureNotFound,
            )
        try:
            name = None if command.display_name is None else machine_name(command.display_name)
        except ShopCaptureError as error:
            raise _refusal(error) from error
        if name is None and not command.retire:
            raise ShopCaptureRefusal("NOTHING_TO_CHANGE", "rename or retire; nothing was asked")
        moment = command.occurred_at or datetime.now(UTC)

        def update_once() -> dict[str, object]:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT row_version, retired_at, display_name
                    FROM machines WHERE id = %s FOR UPDATE
                    """,
                    (command.machine_id,),
                )
                row = cursor.fetchone()
            if row is None:  # pragma: no cover - checked above, and rows are never deleted
                raise ShopCaptureNotFound("machine is missing")
            if int(row[0]) != command.expected_row_version:
                raise ShopCaptureRefusal("STALE_VERSION", "the machine changed; read it again")
            if row[1] is not None:
                raise ShopCaptureRefusal("MACHINE_RETIRED", "a retired machine stays retired")
            version = command.expected_row_version + 1

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    UPDATE machines
                    SET display_name = coalesce(%s, display_name),
                        retired_at = CASE WHEN %s THEN %s ELSE retired_at END,
                        updated_at = %s, row_version = row_version + 1
                    WHERE id = %s AND row_version = %s
                    """,
                    (
                        name,
                        command.retire,
                        moment,
                        moment,
                        command.machine_id,
                        command.expected_row_version,
                    ),
                )

            commit_material_change(
                connection,
                _machine_change(
                    machine_id=command.machine_id,
                    version=version,
                    event_type="MACHINE_RETIRED" if command.retire else "MACHINE_RENAMED",
                    payload={
                        "machine_id": str(command.machine_id),
                        "renamed": name is not None,
                        "retired": command.retire,
                    },
                    audit_action="MACHINE_RETIRE" if command.retire else "MACHINE_RENAME",
                    actor_id=command.principal.staff_user_id,
                    actor_type="STAFF",
                    correlation_id=command.correlation_id,
                    occurred_at=moment,
                ),
                mutation,
            )
            return {"machine_id": str(command.machine_id)}

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"machine:{command.machine_id}:update",
                command.idempotency_key,
                {
                    "expected_row_version": command.expected_row_version,
                    "display_name": name,
                    "retire": command.retire,
                },
                moment,
            ),
            update_once,
        )
        with connection.cursor() as cursor:
            return _read_machine(cursor, command.machine_id), result.replayed


def _read_machine(cursor: Any, machine_id: UUID) -> MachineView:
    cursor.execute(f"SELECT {_MACHINE_COLUMNS} FROM machines m WHERE m.id = %s", (machine_id,))
    row = cursor.fetchone()
    if row is None:
        raise ShopCaptureNotFound("machine is missing")
    return _machine(row)


#: The name a seeded machine starts with, by category; the owner renames it as the shop says it.
_SEED_NAME_PREFIX: Final = {
    MachineCategory.WASHER: "Máy giặt",
    MachineCategory.DRYER: "Máy sấy",
    MachineCategory.DRY_CLEANER: "Máy giặt khô",
    MachineCategory.SHOE_WASHER_DRYER: "Máy giặt giày",
    MachineCategory.VACUUM_IRONING_TABLE: "Cầu là hút chân không",
    MachineCategory.BOILER_IRON_SET: "Bàn là nồi hơi",
    MachineCategory.OTHER: "Máy",
}


def machine_seeds_from_master(text: str) -> tuple[MachineSeed, ...]:
    """The machines `templates/machine-master.csv` says are in the shop and working.

    A row is seeded only when the owner confirmed it both present and active
    (`OWNER_CONFIRMED_PRESENT`, `OWNER_CONFIRMED_ACTIVE`); anything else is left out rather than
    guessed into the list. The starting name is the category, the brand and the nominal load as
    the document prints it ("Máy sấy LG 10,2 kg"); a row with a category this system does not know
    is refused, not filed under "other".
    """
    seeds: list[MachineSeed] = []
    for row in csv.DictReader(io.StringIO(text)):
        if (
            row.get("presence_status") != "OWNER_CONFIRMED_PRESENT"
            or row.get("operational_status") != "OWNER_CONFIRMED_ACTIVE"
        ):
            continue
        category = MachineCategory(str(row["category"]).strip())
        parts = [_SEED_NAME_PREFIX[category]]
        brand = str(row.get("brand") or "").strip()
        if brand:
            parts.append(brand)
        load = str(row.get("nominal_load_kg") or "").strip()
        if load:
            parts.append(f"{load.replace('.', ',')} kg")
        seeds.append(
            MachineSeed(
                code=machine_code(str(row["machine_id"])),
                display_name=machine_name(" ".join(parts)),
                category=category,
            )
        )
    return tuple(seeds)


def seed_machines(
    connection: Any,
    *,
    store_id: UUID,
    actor_id: UUID,
    seeds: tuple[MachineSeed, ...],
    occurred_at: datetime | None = None,
) -> tuple[int, int]:
    """Register the machine master's machines in a store; `(created, already_present)`.

    Idempotent by code: a machine the store already has -- seeded before, or added by the owner
    under the same code -- is left exactly as it is, name included, so re-running never undoes a
    rename or revives a retired machine. Only an active `OWNER_ADMIN` assigned to the store may
    seed it; anyone else writes nothing.
    """
    moment = occurred_at or datetime.now(UTC)
    created = 0
    present = 0
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM staff_users u
                JOIN staff_role_assignments r ON r.staff_user_id = u.id
                WHERE u.id = %s AND u.status = 'ACTIVE'
                  AND r.role = 'OWNER_ADMIN' AND r.revoked_at IS NULL
                """,
                (actor_id,),
            )
            if cursor.fetchone() is None or not is_store_member(
                cursor, staff_user_id=actor_id, store_id=store_id
            ):
                raise ShopCaptureAuthorizationError(
                    "only an active owner assigned to the store seeds its machines"
                )
        correlation_id = uuid4()
        for seed in seeds:
            code = machine_code(seed.code)
            name = machine_name(seed.display_name)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM machines WHERE store_id = %s AND code = %s", (store_id, code)
                )
                if cursor.fetchone() is not None:
                    present += 1
                    continue
            machine_id = uuid4()

            def mutation(
                cursor: Any,
                machine_id: UUID = machine_id,
                code: str = code,
                name: str = name,
                category: MachineCategory = seed.category,
            ) -> None:
                cursor.execute(
                    """
                    INSERT INTO machines (
                        id, store_id, code, display_name, category, source, created_by,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 'MACHINE_MASTER', %s, %s, %s)
                    """,
                    (machine_id, store_id, code, name, category.value, actor_id, moment, moment),
                )

            commit_material_change(
                connection,
                _machine_change(
                    machine_id=machine_id,
                    version=1,
                    event_type="MACHINE_REGISTERED",
                    payload={
                        "machine_id": str(machine_id),
                        "store_id": str(store_id),
                        "code": code,
                        "category": seed.category.value,
                        "source": "MACHINE_MASTER",
                    },
                    audit_action="MACHINE_REGISTER",
                    actor_id=actor_id,
                    actor_type="STAFF",
                    correlation_id=correlation_id,
                    occurred_at=moment,
                ),
                mutation,
            )
            created += 1
    return created, present


# --- wash cycles --------------------------------------------------------------------------------


class MachineUnavailableError(ValueError):
    """The machine named for a wash is not one this order's store can start a wash on."""

    reason_code = "MACHINE_UNAVAILABLE"


def require_cycle_machine(cursor: Any, *, machine_id: UUID, store_id: UUID) -> None:
    """Refuse a machine of another store, a retired one, or one a load does not go into."""
    cursor.execute(
        "SELECT store_id, category, retired_at FROM machines WHERE id = %s", (machine_id,)
    )
    row = cursor.fetchone()
    if (
        row is None
        or _uuid(row[0]) != store_id
        or row[2] is not None
        or not starts_cycle(MachineCategory(str(row[1])))
    ):
        raise MachineUnavailableError(
            "MACHINE_UNAVAILABLE: that machine cannot start a wash in this store"
        )


def apply_cycle_effect(
    connection: Any,
    *,
    order_id: UUID,
    store_id: UUID,
    before: ProductionStatus,
    after: ProductionStatus,
    machine_id: UUID | None,
    actor_id: UUID,
    correlation_id: UUID,
    occurred_at: datetime,
) -> None:
    """Open or close the order's wash cycle for one production move, inside the caller's
    transaction. Called by `OrderRepository` after the move itself is written.

    Opens only when the order has no open cycle (a hold or an exception resumed where it stopped
    continues the cycle it interrupted); closes only one that is open. `machine_id` is used only
    when a cycle opens; absent, the cycle is recorded as not captured.
    """
    effect = cycle_effect(before, after)
    if effect is CycleEffect.NONE:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, machine_id, kind FROM wash_cycles
            WHERE order_id = %s AND ended_at IS NULL
            FOR UPDATE
            """,
            (order_id,),
        )
        open_cycle = cursor.fetchone()
        prior = 0
        if effect is CycleEffect.OPEN and open_cycle is None:
            cursor.execute("SELECT count(*) FROM wash_cycles WHERE order_id = %s", (order_id,))
            counted = cursor.fetchone()
            prior = int(counted[0]) if counted else 0
    if effect is CycleEffect.OPEN:
        if open_cycle is not None:
            return
        cycle_id = uuid4()
        kind = "REWASH" if prior else "WASH"

        def open_mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO wash_cycles (
                    id, store_id, order_id, machine_id, kind, started_at, started_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (cycle_id, store_id, order_id, machine_id, kind, occurred_at, actor_id),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="WASH_CYCLE",
                aggregate_id=cycle_id,
                aggregate_version=1,
                event_type="WASH_CYCLE_STARTED",
                event_payload={
                    "cycle_id": str(cycle_id),
                    "order_id": str(order_id),
                    "machine_id": None if machine_id is None else str(machine_id),
                    "kind": kind,
                    "captured": machine_id is not None,
                },
                audit_action="WASH_CYCLE_START",
                actor_type="STAFF",
                actor_id=actor_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "shop.wash_cycle_started.v1",
                        {"cycle_id": str(cycle_id), "order_id": str(order_id)},
                        f"wash-cycle:{cycle_id}:started",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            open_mutation,
        )
        return
    if open_cycle is None:
        return
    cycle_id = _uuid(open_cycle[0])

    def close_mutation(cursor: Any) -> None:
        cursor.execute(
            """
            UPDATE wash_cycles SET ended_at = %s, ended_by = %s
            WHERE id = %s AND ended_at IS NULL
            RETURNING id
            """,
            (occurred_at, actor_id, cycle_id),
        )
        if cursor.fetchone() is None:  # pragma: no cover - the row is locked above
            raise ShopCaptureRefusal("STALE_VERSION", "the wash cycle closed concurrently")

    commit_material_change(
        connection,
        MaterialChange(
            aggregate_type="WASH_CYCLE",
            aggregate_id=cycle_id,
            aggregate_version=2,
            event_type="WASH_CYCLE_ENDED",
            event_payload={"cycle_id": str(cycle_id), "order_id": str(order_id)},
            audit_action="WASH_CYCLE_END",
            actor_type="STAFF",
            actor_id=actor_id,
            correlation_id=correlation_id,
            outbox_events=(
                OutboxEvent(
                    "shop.wash_cycle_ended.v1",
                    {"cycle_id": str(cycle_id), "order_id": str(order_id)},
                    f"wash-cycle:{cycle_id}:ended",
                ),
            ),
            occurred_at=occurred_at,
        ),
        close_mutation,
    )


# --- trip costs ---------------------------------------------------------------------------------


def insert_trip_cost(
    cursor: Any,
    *,
    leg_id: UUID,
    store_id: UUID,
    order_id: UUID,
    trip: TripCost,
    actor_id: UUID,
    recorded_at: datetime,
) -> None:
    """One leg's trip cost, inside the leg's own mutation. Nothing is written for an empty one."""
    if trip.empty:
        return
    cursor.execute(
        """
        INSERT INTO delivery_leg_costs (
            leg_id, store_id, order_id, vehicle, km, cost_vnd, note, recorded_by, recorded_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            leg_id,
            store_id,
            order_id,
            None if trip.vehicle is None else trip.vehicle.value,
            trip.km,
            trip.cost_vnd,
            trip.note,
            actor_id,
            recorded_at,
        ),
    )


def trip_audit_details(trip: TripCost) -> dict[str, object]:
    """What the leg's audit row says about the trip: never the note."""
    return {
        "trip": {
            "vehicle": None if trip.vehicle is None else trip.vehicle.value,
            "km": None if trip.km is None else str(trip.km),
            "cost_vnd": trip.cost_vnd,
            "note_recorded": trip.note is not None,
        }
    }


# --- the order page's capture read --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CycleView:
    cycle_id: UUID
    kind: str
    machine_id: UUID | None
    machine_code: str | None
    machine_name: str | None
    started_at: datetime
    ended_at: datetime | None
    minutes: int | None


@dataclass(frozen=True, slots=True)
class LegCostView:
    leg_id: UUID
    leg_kind: str
    outcome: str
    recorded_at: datetime
    vehicle: Vehicle | None
    km: Decimal | None
    cost_vnd: int | None
    note: str | None


@dataclass(frozen=True, slots=True)
class OrderCapture:
    order_id: UUID
    store_id: UUID
    suggestion: VehicleSuggestion
    cycles: tuple[CycleView, ...]
    legs: tuple[LegCostView, ...]


def order_capture(cursor: Any, *, order_id: UUID, principal: StaffPrincipal) -> OrderCapture:
    """The wash cycles and trip costs of one order, and the vehicle the owner's rule suggests.

    A caller outside the order's store is told the order does not exist, as the order read does.
    Minutes are whole minutes, rounded half up by PostgreSQL; an open cycle has none.
    """
    _require(principal, ORDER_CAPTURE_READ_ROLES)
    cursor.execute(
        """
        SELECT o.store_id, r.snapshot -> 'lines'
        FROM orders o
        JOIN quote_revisions r
          ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
        WHERE o.id = %s
        """,
        (order_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ShopCaptureNotFound("order is missing")
    # Outside the order's store the order does not exist, as on the order read.
    require_store_membership(
        cursor,
        staff_user_id=principal.staff_user_id,
        store_id=_uuid(row[0]),
        error=ShopCaptureNotFound,
    )
    store_id = _uuid(row[0])
    lines = row[1] if isinstance(row[1], list) else []
    weighed = tuple(
        WeighedLine(
            unit=str(line.get("unit", "")),
            quantity=str(line.get("quantity", "")),
            customer_estimate=line.get("quantity_basis") == "CUSTOMER_ESTIMATE",
        )
        for line in lines
        if isinstance(line, dict)
    )
    cursor.execute(
        """
        SELECT c.id, c.kind, c.machine_id, m.code, m.display_name, c.started_at, c.ended_at,
               CASE WHEN c.ended_at IS NOT NULL
                    THEN round(extract(epoch FROM c.ended_at - c.started_at) / 60)::integer
               END
        FROM wash_cycles c
        LEFT JOIN machines m ON m.id = c.machine_id
        WHERE c.order_id = %s
        ORDER BY c.started_at, c.id
        """,
        (order_id,),
    )
    cycles = tuple(
        CycleView(
            cycle_id=_uuid(item[0]),
            kind=str(item[1]),
            machine_id=None if item[2] is None else _uuid(item[2]),
            machine_code=None if item[3] is None else str(item[3]),
            machine_name=None if item[4] is None else str(item[4]),
            started_at=item[5],
            ended_at=item[6],
            minutes=None if item[7] is None else int(item[7]),
        )
        for item in cursor.fetchall()
    )
    cursor.execute(
        """
        SELECT d.id, d.leg_kind, d.outcome, d.recorded_at, k.vehicle, k.km, k.cost_vnd, k.note
        FROM delivery_legs d
        LEFT JOIN delivery_leg_costs k ON k.leg_id = d.id
        WHERE d.order_id = %s
        ORDER BY d.recorded_at, d.id
        """,
        (order_id,),
    )
    legs = tuple(
        LegCostView(
            leg_id=_uuid(item[0]),
            leg_kind=str(item[1]),
            outcome=str(item[2]),
            recorded_at=item[3],
            vehicle=None if item[4] is None else Vehicle(str(item[4])),
            km=None if item[5] is None else Decimal(str(item[5])),
            cost_vnd=None if item[6] is None else int(item[6]),
            note=None if item[7] is None else str(item[7]),
        )
        for item in cursor.fetchall()
    )
    return OrderCapture(
        order_id=order_id,
        store_id=store_id,
        suggestion=suggest_vehicle(weighed),
        cycles=cycles,
        legs=legs,
    )


# --- Sổ thu chi ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExpenseView:
    expense_id: UUID
    spent_on: date
    category: ExpenseCategory
    amount_vnd: int
    note: str | None
    recorded_by: UUID
    recorded_by_name: str | None
    recorded_at: datetime
    voided_at: datetime | None
    row_version: int


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    category: ExpenseCategory
    amount_vnd: int
    entries: int


@dataclass(frozen=True, slots=True)
class ExpenseMonth:
    store_id: UUID
    month: str
    from_date: date
    to_date: date
    lines: tuple[ExpenseView, ...]
    truncated: bool
    #: Every category, in the vocabulary's order, zero where nothing was recorded.
    totals: tuple[CategoryTotal, ...]
    total_vnd: int
    entries: int
    voided_entries: int
    #: The core categories (`DEC-038`) with no line this month: margin waits for them.
    core_missing: tuple[ExpenseCategory, ...]


@dataclass(frozen=True)
class RecordExpenseCommand:
    store_id: UUID
    spent_on: date
    category: ExpenseCategory
    amount_vnd: int
    note: str | None
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    #: The shop's today, passed in: the domain refuses a line dated after it.
    today: date
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class VoidExpenseCommand:
    expense_id: UUID
    expected_row_version: int
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    occurred_at: datetime | None = None


_EXPENSE_COLUMNS: Final = """
    e.id, e.spent_on, e.category, e.amount_vnd, e.note, e.recorded_by, u.display_name,
    e.recorded_at, e.voided_at, e.row_version
"""


def _expense(row: Any) -> ExpenseView:
    return ExpenseView(
        expense_id=_uuid(row[0]),
        spent_on=row[1],
        category=ExpenseCategory(str(row[2])),
        amount_vnd=int(row[3]),
        note=None if row[4] is None else str(row[4]),
        recorded_by=_uuid(row[5]),
        recorded_by_name=None if row[6] is None else str(row[6]),
        recorded_at=row[7],
        voided_at=row[8],
        row_version=int(row[9]),
    )


#: The month's totals: one row per category and the grand total, from one aggregation, so the
#: categories and the total can never disagree. Voided lines are not spending.
_EXPENSE_TOTALS_SQL: Final = """
    SELECT category, GROUPING(category) = 1 AS is_total,
           coalesce(sum(amount_vnd), 0) AS amount_vnd, count(*) AS entries
    FROM expenses
    WHERE store_id = %(store)s AND spent_on BETWEEN %(from_date)s AND %(to_date)s
      AND voided_at IS NULL
    GROUP BY ROLLUP (category)
"""


def expense_totals(
    cursor: Any, *, store_id: UUID, from_date: date, to_date: date
) -> tuple[tuple[CategoryTotal, ...], int, int]:
    """Spending by category between two days inclusive, summed by PostgreSQL."""
    cursor.execute(
        _EXPENSE_TOTALS_SQL, {"store": store_id, "from_date": from_date, "to_date": to_date}
    )
    by_category: dict[str, tuple[int, int]] = {}
    total, entries = 0, 0
    for row in cursor.fetchall():
        if bool(row[1]):
            total, entries = int(row[2]), int(row[3])
        else:
            by_category[str(row[0])] = (int(row[2]), int(row[3]))
    totals = tuple(
        CategoryTotal(category, *by_category.get(category.value, (0, 0)))
        for category in ExpenseCategory
    )
    return totals, total, entries


class ExpenseRepository:
    """Sổ thu chi: record a line, void a wrong one, read a month."""

    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()

    def record(self, connection: Any, command: RecordExpenseCommand) -> tuple[ExpenseView, bool]:
        _require(command.principal, EXPENSE_WRITE_ROLES)
        with connection.cursor() as cursor:
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=command.store_id,
                error=ShopCaptureAuthorizationError,
            )
        try:
            line = expense(
                spent_on=command.spent_on,
                category=command.category,
                amount_vnd=command.amount_vnd,
                note=command.note,
                today=command.today,
            )
        except ShopCaptureError as error:
            raise _refusal(error) from error
        moment = command.occurred_at or datetime.now(UTC)
        expense_id = uuid4()

        def record_once() -> dict[str, object]:
            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO expenses (
                        id, store_id, spent_on, category, amount_vnd, note, recorded_by,
                        recorded_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        expense_id,
                        command.store_id,
                        line.spent_on,
                        line.category.value,
                        line.amount_vnd,
                        line.note,
                        command.principal.staff_user_id,
                        moment,
                    ),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="EXPENSE",
                    aggregate_id=expense_id,
                    aggregate_version=1,
                    event_type="EXPENSE_RECORDED",
                    event_payload={
                        "expense_id": str(expense_id),
                        "store_id": str(command.store_id),
                        "spent_on": line.spent_on.isoformat(),
                        "category": line.category.value,
                        "amount_vnd": line.amount_vnd,
                    },
                    audit_action="EXPENSE_RECORD",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "shop.expense_recorded.v1",
                            {"expense_id": str(expense_id), "store_id": str(command.store_id)},
                            f"expense:{expense_id}:recorded",
                        ),
                    ),
                    occurred_at=moment,
                ),
                mutation,
            )
            return {"expense_id": str(expense_id)}

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"store:{command.store_id}:expense",
                command.idempotency_key,
                {
                    "spent_on": line.spent_on.isoformat(),
                    "category": line.category.value,
                    "amount_vnd": line.amount_vnd,
                    "note": line.note,
                },
                moment,
            ),
            record_once,
        )
        with connection.cursor() as cursor:
            return _read_expense(cursor, _uuid(result.response["expense_id"])), result.replayed

    def void(self, connection: Any, command: VoidExpenseCommand) -> tuple[ExpenseView, bool]:
        """Void a wrong line, once. The line stays readable; it stops counting."""
        _require(command.principal, EXPENSE_WRITE_ROLES)
        with connection.cursor() as cursor:
            cursor.execute("SELECT store_id FROM expenses WHERE id = %s", (command.expense_id,))
            found = cursor.fetchone()
            if found is None:
                raise ShopCaptureNotFound("expense is missing")
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=_uuid(found[0]),
                error=ShopCaptureNotFound,
            )
        moment = command.occurred_at or datetime.now(UTC)

        def void_once() -> dict[str, object]:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT row_version, voided_at FROM expenses WHERE id = %s FOR UPDATE",
                    (command.expense_id,),
                )
                row = cursor.fetchone()
            if row is None:  # pragma: no cover - checked above, and rows are never deleted
                raise ShopCaptureNotFound("expense is missing")
            if row[1] is not None:
                raise ShopCaptureRefusal("EXPENSE_ALREADY_VOIDED", "this line is already voided")
            if int(row[0]) != command.expected_row_version:
                raise ShopCaptureRefusal("STALE_VERSION", "the line changed; read it again")

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    UPDATE expenses SET voided_at = %s, voided_by = %s, row_version = 2
                    WHERE id = %s AND voided_at IS NULL
                    """,
                    (moment, command.principal.staff_user_id, command.expense_id),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="EXPENSE",
                    aggregate_id=command.expense_id,
                    aggregate_version=2,
                    event_type="EXPENSE_VOIDED",
                    event_payload={"expense_id": str(command.expense_id)},
                    audit_action="EXPENSE_VOID",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "shop.expense_voided.v1",
                            {"expense_id": str(command.expense_id)},
                            f"expense:{command.expense_id}:voided",
                        ),
                    ),
                    occurred_at=moment,
                ),
                mutation,
            )
            return {"expense_id": str(command.expense_id)}

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"expense:{command.expense_id}:void",
                command.idempotency_key,
                {"expected_row_version": command.expected_row_version},
                moment,
            ),
            void_once,
        )
        with connection.cursor() as cursor:
            return _read_expense(cursor, command.expense_id), result.replayed

    @staticmethod
    def month(
        cursor: Any, *, store_id: UUID, principal: StaffPrincipal, month: str
    ) -> ExpenseMonth:
        """One calendar month of Sổ thu chi: its lines, newest day first, and its totals."""
        _require(principal, EXPENSE_READ_ROLES)
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=ShopCaptureAuthorizationError,
        )
        try:
            first, last = month_bounds(month)
        except ShopCaptureError as error:
            raise _refusal(error) from error
        cursor.execute(
            f"""
            SELECT {_EXPENSE_COLUMNS}
            FROM expenses e
            LEFT JOIN staff_users u ON u.id = e.recorded_by
            WHERE e.store_id = %s AND e.spent_on BETWEEN %s AND %s
            ORDER BY e.spent_on DESC, e.recorded_at DESC, e.id
            LIMIT %s
            """,
            (store_id, first, last, EXPENSE_LIST_LIMIT + 1),
        )
        rows = [_expense(row) for row in cursor.fetchall()]
        totals, total, entries = expense_totals(
            cursor, store_id=store_id, from_date=first, to_date=last
        )
        cursor.execute(
            """
            SELECT count(*) FROM expenses
            WHERE store_id = %s AND spent_on BETWEEN %s AND %s AND voided_at IS NOT NULL
            """,
            (store_id, first, last),
        )
        voided = cursor.fetchone()
        return ExpenseMonth(
            store_id=store_id,
            month=first.strftime("%Y-%m"),
            from_date=first,
            to_date=last,
            lines=tuple(rows[:EXPENSE_LIST_LIMIT]),
            truncated=len(rows) > EXPENSE_LIST_LIMIT,
            totals=totals,
            total_vnd=total,
            entries=entries,
            voided_entries=int(voided[0]) if voided else 0,
            core_missing=missing_core_categories(
                frozenset(total.category for total in totals if total.entries > 0)
            ),
        )


def _read_expense(cursor: Any, expense_id: UUID) -> ExpenseView:
    cursor.execute(
        f"""
        SELECT {_EXPENSE_COLUMNS}
        FROM expenses e LEFT JOIN staff_users u ON u.id = e.recorded_by
        WHERE e.id = %s
        """,
        (expense_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ShopCaptureNotFound("expense is missing")
    return _expense(row)


__all__ = [
    "EXPENSE_LIST_LIMIT",
    "EXPENSE_READ_ROLES",
    "EXPENSE_WRITE_ROLES",
    "MACHINE_LIST_LIMIT",
    "MACHINE_READ_ROLES",
    "MACHINE_WRITE_ROLES",
    "CategoryTotal",
    "CreateMachineCommand",
    "CycleView",
    "ExpenseMonth",
    "ExpenseRepository",
    "ExpenseView",
    "LegCostView",
    "MachineList",
    "MachineRepository",
    "MachineSeed",
    "MachineUnavailableError",
    "MachineView",
    "OrderCapture",
    "RecordExpenseCommand",
    "ShopCaptureAuthorizationError",
    "ShopCaptureNotFound",
    "ShopCaptureRefusal",
    "UpdateMachineCommand",
    "VoidExpenseCommand",
    "apply_cycle_effect",
    "expense_totals",
    "insert_trip_cost",
    "machine_seeds_from_master",
    "order_capture",
    "require_cycle_machine",
    "seed_machines",
    "trip_audit_details",
]
