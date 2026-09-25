"""The order's promised-ready time, read and moved (`PROMISE-001`, `DEC-037`).

Connection lifetimes only. What a promise is, whether it may move, and to what, are decided by
`nha_trang_laundry_domain.promise` through `OrderPromiseRepository`; the first promise is written by
the `RECEIVE` step itself (`OrderRepository.execute_step`), never here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.order_promises import (
    OrderPromiseRead,
    OrderPromiseRepository,
    PromiseChangeCommand,
    PromiseChangeResult,
)
from nha_trang_laundry_domain.promise import PromiseChangeReason

from nha_trang_laundry_api.auth import AuthSettings


class PromiseServiceUnavailable(RuntimeError):
    """Raised when the promise surface's database is not configured."""


class PromiseService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise PromiseServiceUnavailable("promise database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._repository = OrderPromiseRepository()

    def read(
        self, *, order_id: UUID, principal: StaffPrincipal, now: datetime | None = None
    ) -> OrderPromiseRead:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return OrderPromiseRepository.read(
                cursor, order_id=order_id, principal=principal, now=now or datetime.now(UTC)
            )

    def change(
        self,
        *,
        order_id: UUID,
        expected_row_version: int,
        idempotency_key: str,
        principal: StaffPrincipal,
        new_promise_at: datetime,
        reason: PromiseChangeReason,
        note: str | None,
    ) -> PromiseChangeResult:
        with self._connection_factory(self._database_url) as connection:
            return self._repository.change(
                connection,
                PromiseChangeCommand(
                    order_id=order_id,
                    expected_row_version=expected_row_version,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    new_promise_at=new_promise_at,
                    reason=reason,
                    note=note,
                ),
            )


__all__ = ["PromiseService", "PromiseServiceUnavailable"]
