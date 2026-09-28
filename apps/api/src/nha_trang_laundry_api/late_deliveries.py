"""*Giao trễ cần xử lý* (`LATE-CREDIT-002`, `DEC-042`): connection lifetimes only.

How late a delivery was is `nha_trang_laundry_domain.late_delivery`'s measurement; what the credit
would be is `domain.remedies`' through the remedy repository's own probe; whose fault it was is the
person's press. This module opens a connection, passes the server's clock, and forwards.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.late_deliveries import (
    LATE_DELIVERY_ROLES,
    LIST_MAX_LIMIT,
    LateDeliveryAuthorizationError,
    LateDeliveryDecideCommand,
    LateDeliveryList,
    LateDeliveryRefused,
    LateDeliveryRepository,
    StoredLateDeliveryDecision,
)
from nha_trang_laundry_domain.late_delivery import LateDeliveryDecision, NotStoreFaultReason

from nha_trang_laundry_api.auth import AuthSettings

#: The one route whose body may carry a note a person typed; its malformed-request answers leave
#: the values out, as the customer and unclaimed routes' do.
LATE_DELIVERY_FREE_TEXT_MARKER: Final = "/late-deliveries/"


class LateDeliveryServiceUnavailable(RuntimeError):
    """Raised when the database is not configured."""


class LateDeliveryService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise LateDeliveryServiceUnavailable("late-delivery database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._repository = LateDeliveryRepository()

    def list_late(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> LateDeliveryList:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return LateDeliveryRepository.list_late(
                cursor,
                store_id=store_id,
                principal=principal,
                as_of=datetime.now(UTC),
                limit=limit,
            )

    def decide(
        self,
        *,
        store_id: UUID,
        order_id: UUID,
        decision: LateDeliveryDecision,
        reason: NotStoreFaultReason | None,
        note: str | None,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredLateDeliveryDecision:
        with self._connection_factory(self._database_url) as connection:
            return self._repository.decide(
                connection,
                LateDeliveryDecideCommand(
                    store_id=store_id,
                    order_id=order_id,
                    decision=decision,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    reason=reason,
                    note=note,
                ),
            )


__all__ = [
    "LATE_DELIVERY_FREE_TEXT_MARKER",
    "LATE_DELIVERY_ROLES",
    "LIST_MAX_LIMIT",
    "LateDeliveryAuthorizationError",
    "LateDeliveryRefused",
    "LateDeliveryService",
    "LateDeliveryServiceUnavailable",
]
