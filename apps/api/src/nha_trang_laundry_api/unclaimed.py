"""Laundry waiting for pickup: the list, contact attempts, waivers, disposal (`UNCLAIMED-001`).

Connection lifetimes only. What waiting means, what the storage fee is, whether a waiver or a
disposal is legal and what it does to the money are decided by `nha_trang_laundry_domain.unclaimed`
through `UnclaimedRepository`; the fee reaches what an order owes through the order read itself.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.unclaimed import (
    DISPOSAL_ROLES,
    UNCLAIMED_READ_ROLES,
    WAIVER_ROLES,
    AwaitingPickupList,
    ContactAttemptCommand,
    DisposalCommand,
    OrderStorageRead,
    StoragePolicySummary,
    StoredContactAttempt,
    UnclaimedAuthorizationError,
    UnclaimedOrderResult,
    UnclaimedRefused,
    UnclaimedRepository,
    WaiverCommand,
)
from nha_trang_laundry_db.unclaimed import LIST_MAX_LIMIT as UNCLAIMED_LIST_MAX_LIMIT
from nha_trang_laundry_domain.unclaimed import (
    ContactChannel,
    ContactOutcome,
    DisposalVerdict,
    OrderStorageFee,
)

from nha_trang_laundry_api.auth import AuthSettings

#: The two routes whose body carries free text a person typed (a contact note, a waiver reason).
#: Their malformed-request answers leave the values out, as the customer routes' do.
UNCLAIMED_FREE_TEXT_PATH_SUFFIXES: Final = ("/contact-attempts", "/storage-fee-waiver")


class UnclaimedServiceUnavailable(RuntimeError):
    """Raised when the database is not configured."""


class UnclaimedService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise UnclaimedServiceUnavailable("unclaimed-laundry database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._repository = UnclaimedRepository()

    def awaiting_pickup(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> AwaitingPickupList:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return UnclaimedRepository.list_awaiting_pickup(
                cursor,
                store_id=store_id,
                principal=principal,
                as_of=datetime.now(UTC),
                limit=limit,
            )

    def order_storage(self, *, order_id: UUID, principal: StaffPrincipal) -> OrderStorageRead:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return UnclaimedRepository.read_order_storage(
                cursor, order_id=order_id, principal=principal, as_of=datetime.now(UTC)
            )

    def record_contact_attempt(
        self,
        *,
        order_id: UUID,
        idempotency_key: str,
        principal: StaffPrincipal,
        channel: ContactChannel,
        outcome: ContactOutcome,
        note: str | None,
    ) -> StoredContactAttempt:
        with self._connection_factory(self._database_url) as connection:
            return self._repository.record_contact_attempt(
                connection,
                ContactAttemptCommand(
                    order_id=order_id,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    channel=channel,
                    outcome=outcome,
                    note=note,
                ),
            )

    def waive_storage_fee(
        self,
        *,
        order_id: UUID,
        expected_row_version: int,
        idempotency_key: str,
        principal: StaffPrincipal,
        reason: str,
    ) -> UnclaimedOrderResult:
        with self._connection_factory(self._database_url) as connection:
            return self._repository.waive_storage_fee(
                connection,
                WaiverCommand(
                    order_id=order_id,
                    expected_row_version=expected_row_version,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    reason=reason,
                ),
            )

    def dispose(
        self,
        *,
        order_id: UUID,
        expected_row_version: int,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> UnclaimedOrderResult:
        with self._connection_factory(self._database_url) as connection:
            return self._repository.dispose(
                connection,
                DisposalCommand(
                    order_id=order_id,
                    expected_row_version=expected_row_version,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                ),
            )


__all__ = [
    "DISPOSAL_ROLES",
    "UNCLAIMED_FREE_TEXT_PATH_SUFFIXES",
    "UNCLAIMED_LIST_MAX_LIMIT",
    "UNCLAIMED_READ_ROLES",
    "WAIVER_ROLES",
    "ContactChannel",
    "ContactOutcome",
    "DisposalVerdict",
    "OrderStorageFee",
    "StoragePolicySummary",
    "UnclaimedAuthorizationError",
    "UnclaimedRefused",
    "UnclaimedService",
    "UnclaimedServiceUnavailable",
]
