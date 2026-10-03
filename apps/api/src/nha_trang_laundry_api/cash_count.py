"""The API's side of `CASH-COUNT-009` (`DEC-049`): connections in, the repository decides.

This service owns connection lifetimes and the shop's "today" -- the one clock the domain takes as
a parameter -- for the three cash-count routes: today's sheet, recording an entry, and the owner's
history over a report window.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_db.cash_counts import (
    CashCountDay,
    CashCountEntry,
    CashCountHistory,
    CashCountRepository,
    RecordCashCountCommand,
)
from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.reports import shop_today
from nha_trang_laundry_domain.cash_count import CashCountKind

from nha_trang_laundry_api.auth import AuthSettings

#: The drawer's write paths that carry free text a person typed, refused by the rules when it looks
#: like a phone number: a cash-count correction's reason, and the Sổ thu chi line's note (the body
#: that carries the "Trả từ két" tick). A body the framework itself refuses on these paths is
#: answered without the values it held (`main._customer_validation_failed`).
CASH_COUNT_FREE_TEXT_PATH_SUFFIXES: Final = ("/cash-count", "/expenses")


class CashCountUnavailable(RuntimeError):
    """Raised when the database is not configured."""


class CashCountService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise CashCountUnavailable("cash count database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._counts = CashCountRepository()

    def today(
        self, *, store_id: UUID, principal: StaffPrincipal, now: datetime | None = None
    ) -> CashCountDay:
        moment = now or datetime.now(UTC)
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return CashCountRepository.today(
                cursor, store_id=store_id, principal=principal, today=shop_today(moment)
            )

    def history(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        from_date: date,
        to_date: date,
        now: datetime | None = None,
    ) -> CashCountHistory:
        moment = now or datetime.now(UTC)
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return CashCountRepository.history(
                cursor,
                store_id=store_id,
                principal=principal,
                from_date=from_date,
                to_date=to_date,
                today=shop_today(moment),
            )

    def record(
        self,
        *,
        store_id: UUID,
        business_day: date,
        kind: CashCountKind,
        counted_vnd: int,
        supersedes_entry_id: UUID | None,
        reason: str | None,
        principal: StaffPrincipal,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> tuple[CashCountEntry, CashCountDay, bool]:
        moment = now or datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            return self._counts.record(
                connection,
                RecordCashCountCommand(
                    store_id=store_id,
                    business_day=business_day,
                    kind=kind,
                    counted_vnd=counted_vnd,
                    supersedes_entry_id=supersedes_entry_id,
                    reason=reason,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    today=shop_today(moment),
                    occurred_at=moment,
                ),
            )


__all__ = ["CashCountService", "CashCountUnavailable"]
