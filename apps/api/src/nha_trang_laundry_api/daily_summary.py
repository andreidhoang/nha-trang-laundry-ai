"""The API's side of `DAILY-SUMMARY-001` (`DEC-039`): a connection in, the repository decides.

`GET /internal/v1/stores/{store_id}/reports/daily-summary?date=` returns the owner's evening
summary: fixed Vietnamese sentences a versioned template wrote from the day's figures, with the
figures beside each sentence and the lines it could not write listed with the reason. No language
model, no provider call and no network are involved at any point: the text is built here, on the
server, from read models that already computed every number.

This module owns the connection's lifetime, the one clock read (the request's instant, passed down
as `as_of`), and the wire shape.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.daily_summary import DailySummary, DailySummaryRepository
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.reports import shop_today
from nha_trang_laundry_domain.sla import ProductionSlaPolicy
from pydantic import BaseModel, ConfigDict

from nha_trang_laundry_api.auth import AuthSettings


class DailySummaryUnavailable(RuntimeError):
    """Raised when the database is not configured."""


class DailySummaryLineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    #: The sentence(s), exactly as the template wrote them.
    text: str
    #: The integers (and the odd token) the sentence states, so a reader can check it.
    figures: dict[str, int | str | None]


class DailySummaryOmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    reason: str
    #: The slice or read the line waits for (`UNCLAIMED-001`, `PAYMENT-002`, `SLA_BOARD`…).
    source: str
    #: The line's topic and the reason in the owner's words, from the same template.
    note: str


class DailySummarySourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    query_version: str


class DailySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: UUID
    date: date
    template_version: str
    evaluated_at: datetime
    #: The day is the shop's today, still being traded: its figures are "so far".
    so_far: bool
    lines: list[DailySummaryLineResponse]
    omitted: list[DailySummaryOmissionResponse]
    #: The lines joined one per row: what *Sao chép* copies and *Chia sẻ* shares, byte for byte.
    text: str
    sources: list[DailySummarySourceResponse]


def daily_summary_response(summary: DailySummary) -> DailySummaryResponse:
    rendered = summary.rendered
    return DailySummaryResponse(
        store_id=summary.store_id,
        date=summary.day,
        template_version=summary.template_version,
        evaluated_at=summary.evaluated_at,
        so_far=summary.so_far,
        lines=[
            DailySummaryLineResponse(key=line.key.value, text=line.text, figures=dict(line.figures))
            for line in rendered.lines
        ],
        omitted=[
            DailySummaryOmissionResponse(
                key=omission.key.value,
                reason=omission.reason.value,
                source=omission.source,
                note=omission.note,
            )
            for omission in rendered.omitted
        ],
        text=rendered.text,
        sources=[
            DailySummarySourceResponse(key=key, query_version=version)
            for key, version in summary.sources
        ],
    )


class DailySummaryService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise DailySummaryUnavailable("daily summary database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory

    def read(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        policy: ProductionSlaPolicy,
        day: date | None,
        as_of: datetime | None = None,
    ) -> DailySummary:
        """The summary for `day` (the shop's today when absent), read at one instant."""
        instant = as_of or datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            return DailySummaryRepository.read(
                connection,
                store_id=store_id,
                principal=principal,
                policy=policy,
                day=day or shop_today(instant),
                as_of=instant,
            )


__all__ = [
    "DailySummaryResponse",
    "DailySummaryService",
    "DailySummaryUnavailable",
    "daily_summary_response",
]
