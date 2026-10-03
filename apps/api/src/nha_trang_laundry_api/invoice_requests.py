"""The API's side of `EINVOICE-REQUEST-001` (`DEC-040`): connections, the clock, the keys.

The rules are `nha_trang_laundry_domain.invoice_requests`'; the rows, their transactions and the
amounts they read are `nha_trang_laundry_db.invoice_requests`'. This module owns what neither may:
the server's clock (read once per request and passed in) and connection lifetimes.

The idempotency ledger is append-only and outlives an erasure, so the stored result of every
command here is the request's id and version only; the routes answer with the request as it reads
now, so a replay after an erasure shows the erasure rather than the buyer it removed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.invoice_requests import (
    INVOICE_CLOSE_ROLES,
    INVOICE_READ_ROLES,
    INVOICE_WRITE_ROLES,
    LIST_MAX_LIMIT,
    BuyerInput,
    CancelInvoiceRequestCommand,
    CreateInvoiceRequestCommand,
    ExportCommand,
    InvoiceExport,
    InvoiceRequestList,
    InvoiceRequestRepository,
    InvoiceRequestView,
    InvoiceSubjectRead,
    RecordIssuedCommand,
    StoredInvoiceRequest,
)
from nha_trang_laundry_domain.accounts import parse_month
from nha_trang_laundry_domain.invoice_requests import (
    InvoiceCancelReason,
    InvoiceRequestStatus,
    InvoiceSubjectKind,
)

from nha_trang_laundry_api.auth import AuthSettings

#: The path fragment every invoice-request route carries. A malformed body on one is answered
#: without the values it held (a buyer's name or email), as a customer path's is.
INVOICE_PATH_MARKER: Final = "/invoice-requests"


class InvoiceRequestsUnavailable(RuntimeError):
    """No database configured: the routes answer 503 rather than guess."""


class InvoiceRequestService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise InvoiceRequestsUnavailable("invoice-request database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._repository = InvoiceRequestRepository()

    # --- reads -------------------------------------------------------------------------------

    def list(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        status: InvoiceRequestStatus,
        limit: int,
    ) -> InvoiceRequestList:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._repository.list(
                cursor,
                store_id=store_id,
                principal=principal,
                status=status,
                limit=limit,
                now=datetime.now(UTC),
            )

    def read(
        self, *, store_id: UUID, request_id: UUID, principal: StaffPrincipal
    ) -> InvoiceRequestView:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._repository.read(
                cursor,
                store_id=store_id,
                request_id=request_id,
                principal=principal,
                now=datetime.now(UTC),
            )

    def order_subject(
        self, *, store_id: UUID, order_id: UUID, principal: StaffPrincipal
    ) -> InvoiceSubjectRead:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._repository.order_subject(
                cursor,
                store_id=store_id,
                order_id=order_id,
                principal=principal,
                now=datetime.now(UTC),
            )

    def account_month_subject(
        self, *, store_id: UUID, customer_id: UUID, month: str, principal: StaffPrincipal
    ) -> InvoiceSubjectRead:
        period = parse_month(month)
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._repository.account_month_subject(
                cursor,
                store_id=store_id,
                customer_id=customer_id,
                month=period,
                principal=principal,
                now=datetime.now(UTC),
            )

    # --- writes ------------------------------------------------------------------------------

    def create(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        subject_kind: InvoiceSubjectKind,
        order_id: UUID | None,
        customer_id: UUID | None,
        month: str | None,
        buyer: BuyerInput,
        save_profile: bool,
    ) -> tuple[StoredInvoiceRequest, InvoiceRequestView]:
        """Write the request, then read it back as it is now (never from the ledger)."""

        period: date | None = None if month is None else parse_month(month)
        with self._connection_factory(self._database_url) as connection:
            stored = self._repository.create(
                connection,
                CreateInvoiceRequestCommand(
                    store_id=store_id,
                    subject_kind=subject_kind,
                    order_id=order_id,
                    customer_id=customer_id,
                    period_month=period,
                    buyer=buyer,
                    save_profile=save_profile,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    at=datetime.now(UTC),
                ),
            )
            return stored, self._read_back(connection, store_id, stored.request_id, principal)

    def record_issued(
        self,
        *,
        store_id: UUID,
        request_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        expected_row_version: int,
        invoice_symbol: str,
        invoice_number: str,
        invoice_date: date | None,
        invoice_total_vnd: int | None,
        invoice_order_ids: tuple[UUID, ...],
        record_printed_total: bool = False,
    ) -> tuple[StoredInvoiceRequest, InvoiceRequestView]:
        with self._connection_factory(self._database_url) as connection:
            stored = self._repository.record_issued(
                connection,
                RecordIssuedCommand(
                    store_id=store_id,
                    request_id=request_id,
                    expected_row_version=expected_row_version,
                    invoice_symbol=invoice_symbol,
                    invoice_number=invoice_number,
                    invoice_date=invoice_date,
                    invoice_total_vnd=invoice_total_vnd,
                    invoice_order_ids=invoice_order_ids,
                    record_printed_total=record_printed_total,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    at=datetime.now(UTC),
                ),
            )
            return stored, self._read_back(connection, store_id, request_id, principal)

    def cancel(
        self,
        *,
        store_id: UUID,
        request_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        expected_row_version: int,
        reason: InvoiceCancelReason,
        note: str | None,
    ) -> tuple[StoredInvoiceRequest, InvoiceRequestView]:
        with self._connection_factory(self._database_url) as connection:
            stored = self._repository.cancel(
                connection,
                CancelInvoiceRequestCommand(
                    store_id=store_id,
                    request_id=request_id,
                    expected_row_version=expected_row_version,
                    reason=reason,
                    note=note,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    at=datetime.now(UTC),
                ),
            )
            return stored, self._read_back(connection, store_id, request_id, principal)

    def export_open(self, *, store_id: UUID, principal: StaffPrincipal) -> InvoiceExport:
        with self._connection_factory(self._database_url) as connection:
            return self._repository.export_open(
                connection,
                ExportCommand(
                    store_id=store_id,
                    principal=principal,
                    correlation_id=uuid4(),
                    at=datetime.now(UTC),
                ),
            )

    def _read_back(
        self, connection: Any, store_id: UUID, request_id: UUID, principal: StaffPrincipal
    ) -> InvoiceRequestView:
        with connection.cursor() as cursor:
            return self._repository.read(
                cursor,
                store_id=store_id,
                request_id=request_id,
                principal=principal,
                now=datetime.now(UTC),
            )


__all__ = [
    "INVOICE_CLOSE_ROLES",
    "INVOICE_PATH_MARKER",
    "INVOICE_READ_ROLES",
    "INVOICE_WRITE_ROLES",
    "LIST_MAX_LIMIT",
    "BuyerInput",
    "InvoiceRequestService",
    "InvoiceRequestsUnavailable",
]
