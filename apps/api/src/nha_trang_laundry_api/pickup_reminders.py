"""Pickup reminders: the due list and the fixed text (`PICKUP-REMIND-001`, `DEC-043`).

Connection lifetimes only. Which reminder is due, who can receive it, whether the egress guard
allows the text and what the text says are decided by `nha_trang_laundry_domain.pickup_reminders`
through `PickupReminderRepository`; recording that a reminder was sent is `UNCLAIMED-001`'s
contact-attempt route, which checks the step and the guard in its own transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.pickup_reminders import (
    LIST_MAX_LIMIT,
    REMINDER_READ_ROLES,
    REMINDER_SEND_ROLES,
    PickupReminderRepository,
    ReminderList,
    ReminderMessage,
    ReminderRefused,
)
from nha_trang_laundry_domain.pickup_reminders import (
    PICKUP_REMINDER_TEMPLATE,
    Reachability,
    ReminderStep,
)

from nha_trang_laundry_api.auth import AuthSettings

REMINDER_LIST_MAX_LIMIT = LIST_MAX_LIMIT


class PickupReminderServiceUnavailable(RuntimeError):
    """Raised when the database is not configured."""


class PickupReminderService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise PickupReminderServiceUnavailable("pickup reminders database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory

    def due(self, *, store_id: UUID, principal: StaffPrincipal, limit: int) -> ReminderList:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return PickupReminderRepository.list_due(
                cursor,
                store_id=store_id,
                principal=principal,
                as_of=datetime.now(UTC),
                limit=limit,
            )

    def message(
        self, *, order_id: UUID, step: ReminderStep, principal: StaffPrincipal
    ) -> ReminderMessage:
        with self._connection_factory(self._database_url) as connection:
            return PickupReminderRepository.read_message(
                connection,
                order_id=order_id,
                step=step,
                principal=principal,
                as_of=datetime.now(UTC),
            )


__all__ = [
    "PICKUP_REMINDER_TEMPLATE",
    "REMINDER_LIST_MAX_LIMIT",
    "REMINDER_READ_ROLES",
    "REMINDER_SEND_ROLES",
    "PickupReminderService",
    "PickupReminderServiceUnavailable",
    "Reachability",
    "ReminderRefused",
    "ReminderStep",
]
