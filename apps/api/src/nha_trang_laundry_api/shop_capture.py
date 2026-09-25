"""The API's side of `SHOP-CAPTURE-001` (`DEC-038`): connections in, repositories decide.

Machines, Sổ thu chi and the order page's capture read. The wash cycle itself has no route: it is
opened and closed by the order steps, inside their own transaction (`OrderRepository`), and the
trip cost rides on the delivery-leg route. This service owns connection lifetimes and the shop's
"today" for the expense date check -- the one clock the domain takes as a parameter.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.reports import shop_today
from nha_trang_laundry_db.shop_capture import (
    CreateMachineCommand,
    ExpenseMonth,
    ExpenseRepository,
    ExpenseView,
    MachineList,
    MachineRepository,
    MachineView,
    OrderCapture,
    RecordExpenseCommand,
    ShopCaptureNotFound,
    UpdateMachineCommand,
    VoidExpenseCommand,
    order_capture,
)
from nha_trang_laundry_domain.shop_capture import ExpenseCategory, MachineCategory

from nha_trang_laundry_api.auth import AuthSettings


class ShopCaptureUnavailable(RuntimeError):
    """Raised when the database is not configured."""


class ShopCaptureService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise ShopCaptureUnavailable("shop capture database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._machines = MachineRepository()
        self._expenses = ExpenseRepository()

    # --- machines -----------------------------------------------------------------------------

    def list_machines(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        cycle_only: bool,
        include_retired: bool,
    ) -> MachineList:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return MachineRepository.list(
                cursor,
                store_id=store_id,
                principal=principal,
                cycle_only=cycle_only,
                include_retired=include_retired,
            )

    def create_machine(
        self,
        *,
        store_id: UUID,
        code: str,
        display_name: str,
        category: MachineCategory,
        principal: StaffPrincipal,
        idempotency_key: str,
    ) -> tuple[MachineView, bool]:
        with self._connection_factory(self._database_url) as connection:
            return self._machines.create(
                connection,
                CreateMachineCommand(
                    store_id=store_id,
                    code=code,
                    display_name=display_name,
                    category=category,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                ),
            )

    def update_machine(
        self,
        *,
        store_id: UUID,
        machine_id: UUID,
        expected_row_version: int,
        display_name: str | None,
        retire: bool,
        principal: StaffPrincipal,
        idempotency_key: str,
    ) -> tuple[MachineView, bool]:
        with self._connection_factory(self._database_url) as connection:
            _require_in_store(connection, "machines", machine_id, store_id)
            return self._machines.update(
                connection,
                UpdateMachineCommand(
                    machine_id=machine_id,
                    expected_row_version=expected_row_version,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    display_name=display_name,
                    retire=retire,
                ),
            )

    # --- Sổ thu chi ---------------------------------------------------------------------------

    def expense_month(
        self, *, store_id: UUID, principal: StaffPrincipal, month: str
    ) -> ExpenseMonth:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return ExpenseRepository.month(
                cursor, store_id=store_id, principal=principal, month=month
            )

    def record_expense(
        self,
        *,
        store_id: UUID,
        spent_on: date,
        category: ExpenseCategory,
        amount_vnd: int,
        note: str | None,
        principal: StaffPrincipal,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> tuple[ExpenseView, bool]:
        moment = now or datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            return self._expenses.record(
                connection,
                RecordExpenseCommand(
                    store_id=store_id,
                    spent_on=spent_on,
                    category=category,
                    amount_vnd=amount_vnd,
                    note=note,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    today=shop_today(moment),
                    occurred_at=moment,
                ),
            )

    def void_expense(
        self,
        *,
        store_id: UUID,
        expense_id: UUID,
        expected_row_version: int,
        principal: StaffPrincipal,
        idempotency_key: str,
    ) -> tuple[ExpenseView, bool]:
        with self._connection_factory(self._database_url) as connection:
            _require_in_store(connection, "expenses", expense_id, store_id)
            return self._expenses.void(
                connection,
                VoidExpenseCommand(
                    expense_id=expense_id,
                    expected_row_version=expected_row_version,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                ),
            )

    # --- the order page -----------------------------------------------------------------------

    def order_capture(self, *, order_id: UUID, principal: StaffPrincipal) -> OrderCapture:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return order_capture(cursor, order_id=order_id, principal=principal)


def _require_in_store(connection: Any, table: str, row_id: UUID, store_id: UUID) -> None:
    """A row addressed under a store's path belongs to that store, or it does not exist."""
    if table not in ("machines", "expenses"):  # pragma: no cover - two literal callers
        raise ValueError("unknown table")
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT store_id FROM {table} WHERE id = %s", (row_id,))
        row = cursor.fetchone()
    if row is None or str(row[0]) != str(store_id):
        raise ShopCaptureNotFound(f"{table[:-1]} is missing")


__all__ = ["ShopCaptureService", "ShopCaptureUnavailable"]
