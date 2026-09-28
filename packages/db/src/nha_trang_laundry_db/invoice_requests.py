"""Invoice requests: capture, the list, issued and cancelled, the bookkeeper's download.

`EINVOICE-REQUEST-001`, `DEC-040`. Every rule is `nha_trang_laundry_domain.invoice_requests`'s; the
amounts are the ledgers' own reads; this module holds the rows and their transactions.

**The software never issues an invoice.** A request is the buyer's details at the moment the
customer asked, for one order or one account customer's calendar month. The bookkeeper issues the
invoice in the provider's own portal from the downloaded list, and staff record its symbol, number
and date, which closes the request as issued. No tax is split and no rate is named anywhere here.

**Who may do what** (role, MFA and store membership on every one):

* create and cancel a request -- the operations roles (`INVOICE_WRITE_ROLES`);
* record an issued invoice and download the open list -- the owner and the approver
  (`INVOICE_CLOSE_ROLES`);
* read requests and a subject's state -- whoever reads the customer (`INVOICE_READ_ROLES`).

**Refused until the privacy notice is published.** A buyer's name or email can identify a
person, so capture refuses `PRIVACY_NOTICE_UNPUBLISHED` before anything typed is looked at, as a
customer record does (`DEC-034`). The notice in force is recorded on the request.

**Amounts are read, never stored.** An order's amount is its charges -- the quoted total, and the
storage fee as a second charge when there is one -- through `payments.owed_charges`; an account
month's is the month's account charges through `accounts._statement_figures`, the statement read
`PAYMENT-002` publishes. The bookkeeper's list prints each order's service lines straight from the
stored quote snapshot (their net amounts, the delivery fee and an approved surcharge, which the
snapshot guarantees reconcile to its total) and the storage fee; an account month prints one line
per order charged to it. Nothing here adds, splits or rounds a figure.

**One live request per subject**, by index, and across the two kinds by the create's own check: an
order charged to an account month that has a request is already covered, and so is a month one of
whose orders has its own. Creates in a store are serialised by an advisory lock, which also hands
out the request number.

**No personal data in any ledger row.** Events, audit rows and outbox rows carry ids, kinds, states
and the invoice's own symbol and number -- never a buyer's name, address, email or tax code, and
never the cancellation note. The idempotency ledger stores the request's id and version only; a
replay answers with the request as it reads *now*, so a replay after an erasure shows the erasure.

Nothing here reads a clock: every instant is passed in.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from typing import Any, Final
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from nha_trang_laundry_domain.accounts import (
    ACCOUNT_TIMEZONE,
    local_day,
    month_has_ended,
    month_label,
    month_start_instant,
    next_month,
    statement_month,
)
from nha_trang_laundry_domain.invoice_requests import (
    INVOICE_DECISION,
    UNIT_VI,
    BuyerDetails,
    InvoiceCancelReason,
    InvoiceRefusal,
    InvoiceRequestStatus,
    InvoiceRuleError,
    InvoiceSubjectKind,
    clean_buyer,
    clean_cancel_note,
    clean_issued,
    request_code,
    require_open,
)
from nha_trang_laundry_domain.payments import owed_charges, owed_total
from nha_trang_laundry_domain.quotes import ExactLineAmounts, parse_quote_revision
from nha_trang_laundry_domain.settlement import QuotedTotal
from psycopg.errors import UniqueViolation

from nha_trang_laundry_db.accounts import STATEMENT_QUERY, _statement_figures
from nha_trang_laundry_db.configurations import ConfigurationRepository
from nha_trang_laundry_db.customers import CUSTOMER_READ_ROLES
from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.privacy_notice import read_published_privacy_notice
from nha_trang_laundry_db.query_version import QueryVersion, query_version
from nha_trang_laundry_db.quotes import QuoteRepository
from nha_trang_laundry_db.settlement import BUSINESS_TIMEZONE
from nha_trang_laundry_db.storage_fees import storage_fee_for_order
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Reading requests and a subject's state: whoever reads the customer (`DEC-034`).
INVOICE_READ_ROLES: Final = CUSTOMER_READ_ROLES
#: `DEC-040`: "operations roles create and cancel their own store's requests".
INVOICE_WRITE_ROLES: Final = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
)
#: `DEC-040`: "owner and approver record an issued invoice and download".
INVOICE_CLOSE_ROLES: Final = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER})

LIST_DEFAULT_LIMIT: Final = 100
LIST_MAX_LIMIT: Final = 200
#: The requests one download carries, oldest first; the file says when it stopped.
EXPORT_MAX_REQUESTS: Final = 500
#: A subject's earlier requests (cancelled ones, and the live one) on the order or month page.
SUBJECT_HISTORY_LIMIT: Final = 20

#: The header row's money wording (`REMAINING_GAPS_SPEC_V1.md` §1): amounts as the shop charged
#: them, with no tax split out.
AMOUNT_HEADER_VI: Final = "Số tiền theo giá tiệm đã thu (chưa tách thuế)"
TOTAL_HEADER_VI: Final = "Tổng theo giá tiệm đã thu (chưa tách thuế)"

#: The download's columns, in order.
EXPORT_COLUMNS: Final = (
    "Mã yêu cầu",
    "Ngày yêu cầu",
    "Tên đơn vị",
    "Mã số thuế",
    "Địa chỉ",
    "Email nhận hóa đơn",
    "Người mua hàng",
    "Phiếu hoặc tháng công nợ",
    "Mô tả",
    "Số lượng",
    "Đơn vị tính",
    AMOUNT_HEADER_VI,
    TOTAL_HEADER_VI,
)

#: The key/value rows the file opens with, before the column header (the round-6 export's shape).
EXPORT_HEADER_KEYS: Final = ("Phiên bản truy vấn", "Lập lúc", "Số yêu cầu", "Ghi chú")

EXPORT_NOTE_VI: Final = (
    "Danh sách yêu cầu của khách. Kế toán xuất hóa đơn trên cổng của nhà cung cấp hóa đơn điện tử, "
    "rồi ghi ký hiệu, số và ngày vào từng yêu cầu. " + AMOUNT_HEADER_VI + "."
)

#: The words a line that is not a service reads as.
DELIVERY_LINE_VI: Final = "Phí giao nhận"
SURCHARGE_LINE_VI: Final = "Phụ thu đã duyệt"
STORAGE_LINE_VI: Final = "Phí lưu kho"

_REQUEST_COLUMNS: Final = """
    r.id, r.store_id, r.request_number, r.subject_kind, r.order_id, r.account_id, r.period_month,
    r.customer_id, r.buyer_unit_name, r.buyer_tax_code, r.buyer_address, r.buyer_email,
    r.buyer_name, r.buyer_erased_at, r.status, r.invoice_symbol, r.invoice_number, r.invoice_date,
    r.cancel_reason, r.cancel_note, r.requested_by, rb.display_name, r.requested_at, r.closed_by,
    cb.display_name, r.closed_at, r.row_version, t.ticket_number, t.issued_on, c.display_name
"""

_REQUEST_JOINS: Final = """
    FROM invoice_requests r
    LEFT JOIN orders o ON o.id = r.order_id AND o.store_id = r.store_id
    LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
    LEFT JOIN customers c ON c.id = r.customer_id AND c.store_id = r.store_id
    LEFT JOIN staff_users rb ON rb.id = r.requested_by
    LEFT JOIN staff_users cb ON cb.id = r.closed_by
"""

#: The list, by status. Open requests are the work queue, oldest first; closed ones read newest
#: closed first, because a tab of the oldest issued invoices stops being useful after a month.
_LIST_SQL: Final = (
    "SELECT "
    + _REQUEST_COLUMNS
    + _REQUEST_JOINS
    + """
    WHERE r.store_id = %(store)s AND r.status = %(status)s
    ORDER BY
        CASE WHEN r.status = 'REQUESTED' THEN r.requested_at END ASC,
        CASE WHEN r.status <> 'REQUESTED' THEN r.closed_at END DESC,
        r.request_number
    LIMIT %(limit)s
"""
)

_COUNTS_SQL: Final = """
    SELECT status, count(*) FROM invoice_requests WHERE store_id = %s GROUP BY status
"""

#: The order's amount: its bound quote's single total, for `owed_charges` with the storage fee.
_ORDER_TOTAL_SQL: Final = """
    SELECT r.display_total_min_vnd, r.display_total_max_vnd
    FROM orders o
    JOIN quote_revisions r
      ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
    WHERE o.id = %s
"""

#: The published version of the amounts a request reads. The statement's own version and the
#: shop's time zone ride along: moving either would change a figure without changing this text.
INVOICE_LIST_QUERY: Final[QueryVersion] = query_version(
    "invoice-requests-v1", _LIST_SQL, _ORDER_TOTAL_SQL, STATEMENT_QUERY.label, ACCOUNT_TIMEZONE
)

#: The published version of the bookkeeper's file: the list, the lines it prints and its columns.
INVOICE_EXPORT_QUERY: Final[QueryVersion] = query_version(
    "invoice-requests-export-v1",
    INVOICE_LIST_QUERY.label,
    "|".join(EXPORT_COLUMNS),
    "|".join(EXPORT_HEADER_KEYS),
    DELIVERY_LINE_VI,
    SURCHARGE_LINE_VI,
    STORAGE_LINE_VI,
)


#: `count_waiting_over`'s statement: open requests recorded before the cut-off, in one store.
INVOICE_WAITING_OVER_SQL: Final = """
    SELECT count(*) FROM invoice_requests
    WHERE store_id = %(store)s AND status = 'REQUESTED' AND requested_at < %(cutoff)s
"""


class InvoiceAuthorizationError(PermissionError):
    """The role, MFA or store membership does not allow this."""


class InvoiceNotFoundError(LookupError):
    """No such request, order or account in this store -- another store's is the same answer."""


class InvoiceStateError(ValueError):
    """The request moved since the caller read it (`STALE_VERSION: …`)."""


# --- read models --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuyerView:
    unit_name: str | None
    tax_code: str | None
    address: str | None
    email: str | None
    name: str | None
    erased: bool


@dataclass(frozen=True, slots=True)
class InvoiceAmount:
    """What a request is for, read now. `total_vnd` is null when the order has no single total."""

    #: `ORDER_CHARGES` or `ACCOUNT_STATEMENT`.
    source: str
    total_vnd: int | None
    #: The storage fee among an order's charges, when there is one.
    storage_fee_vnd: int | None
    #: An account month: how many orders were charged to it, and whether it has ended.
    charge_count: int | None
    month_ended: bool | None


@dataclass(frozen=True, slots=True)
class InvoiceRequestView:
    request_id: UUID
    store_id: UUID
    request_number: int
    request_code: str
    subject_kind: InvoiceSubjectKind
    order_id: UUID | None
    ticket_number: int | None
    ticket_issued_on: date | None
    account_id: UUID | None
    period_month: date | None
    customer_id: UUID | None
    customer_name: str | None
    buyer: BuyerView
    status: InvoiceRequestStatus
    invoice_symbol: str | None
    invoice_number: str | None
    invoice_date: date | None
    cancel_reason: InvoiceCancelReason | None
    cancel_note: str | None
    requested_by_staff_id: UUID
    requested_by_name: str | None
    requested_at: datetime
    closed_by_staff_id: UUID | None
    closed_by_name: str | None
    closed_at: datetime | None
    row_version: int
    amount: InvoiceAmount


@dataclass(frozen=True, slots=True)
class InvoiceRequestList:
    store_id: UUID
    status: InvoiceRequestStatus
    evaluated_at: datetime
    limit: int
    total_count: int
    truncated: bool
    counts: dict[str, int]
    requests: tuple[InvoiceRequestView, ...]
    query_version: str


@dataclass(frozen=True, slots=True)
class InvoiceSubjectRead:
    """Whether *Khách cần hóa đơn* may be pressed for this order or month now, and what it has."""

    subject_kind: InvoiceSubjectKind
    order_id: UUID | None
    account_id: UUID | None
    period_month: date | None
    customer_id: UUID | None
    customer_name: str | None
    ticket_number: int | None
    ticket_issued_on: date | None
    privacy_notice_published: bool
    #: Why a new request would be refused now (`PRIVACY_NOTICE_UNPUBLISHED`,
    #: `INVOICE_SUBJECT_UNAVAILABLE`, `INVOICE_REQUEST_EXISTS`), or `None`.
    refusal: InvoiceRefusal | None
    #: "Lưu cho lần sau" is offered: the subject's customer has an account and is on record.
    profile_savable: bool
    #: The account customer's saved details, or their name as the unit name when none are saved.
    prefill: BuyerView | None
    prefill_from_profile: bool
    amount: InvoiceAmount | None
    #: The live request (REQUESTED or ISSUED) covering this subject, if one.
    live: InvoiceRequestView | None
    #: Every request of this subject, newest first, at most `SUBJECT_HISTORY_LIMIT`.
    history: tuple[InvoiceRequestView, ...]
    history_truncated: bool
    evaluated_at: datetime
    query_version: str


@dataclass(frozen=True, slots=True)
class StoredInvoiceRequest:
    request_id: UUID
    store_id: UUID
    row_version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class InvoiceExport:
    export_id: UUID
    store_id: UUID
    filename: str
    content_csv: str
    content_hash: str
    query_version: str
    request_count: int
    row_count: int
    truncated: bool
    produced_at: datetime


# --- commands -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuyerInput:
    """What was typed, before the domain cleans it."""

    unit_name: str | None
    tax_code: str | None
    address: str | None
    email: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class CreateInvoiceRequestCommand:
    store_id: UUID
    subject_kind: InvoiceSubjectKind
    #: `ORDER`: the order. `ACCOUNT_MONTH`: the customer whose account it is, and the month.
    order_id: UUID | None
    customer_id: UUID | None
    period_month: date | None
    buyer: BuyerInput
    save_profile: bool
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class RecordIssuedCommand:
    store_id: UUID
    request_id: UUID
    expected_row_version: int
    invoice_symbol: str
    invoice_number: str
    invoice_date: date | None
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class CancelInvoiceRequestCommand:
    store_id: UUID
    request_id: UUID
    expected_row_version: int
    reason: InvoiceCancelReason
    note: str | None
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class ExportCommand:
    store_id: UUID
    principal: StaffPrincipal
    correlation_id: UUID
    at: datetime


# --- the repository -----------------------------------------------------------------------------


class InvoiceRequestRepository:
    """The only path to `invoice_requests`, `customer_invoice_profiles` and their exports."""

    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()

    # --- authorisation -------------------------------------------------------------------------

    @staticmethod
    def _require(
        cursor: Any, principal: StaffPrincipal, store_id: UUID, roles: frozenset[StaffRole]
    ) -> None:
        if not principal.roles & roles or not principal.mfa_verified:
            raise InvoiceAuthorizationError("this invoice-request action is not authorized")
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=InvoiceAuthorizationError,
        )

    @staticmethod
    def authorize_read(cursor: Any, *, store_id: UUID, principal: StaffPrincipal) -> None:
        InvoiceRequestRepository._require(cursor, principal, store_id, INVOICE_READ_ROLES)

    @staticmethod
    def authorize_write(cursor: Any, *, store_id: UUID, principal: StaffPrincipal) -> None:
        InvoiceRequestRepository._require(cursor, principal, store_id, INVOICE_WRITE_ROLES)

    @staticmethod
    def authorize_close(cursor: Any, *, store_id: UUID, principal: StaffPrincipal) -> None:
        InvoiceRequestRepository._require(cursor, principal, store_id, INVOICE_CLOSE_ROLES)

    # --- reads ---------------------------------------------------------------------------------

    @staticmethod
    def count_waiting_over(
        cursor: Any, *, store_id: UUID, principal: StaffPrincipal, as_of: datetime, days: int
    ) -> int:
        """Requests still `REQUESTED` more than `days` shop days after they were recorded.

        `SUMMARY-ATTENTION-001` (`DEC-044`): the evening summary's "yêu cầu hóa đơn đã chờ quá 3
        ngày". A request recorded on shop day D has waited more than `days` days on shop day T
        when T - D > days, i.e. when it was recorded before the local midnight that starts day
        T - days. Counts only, under the list's own gate.
        """
        InvoiceRequestRepository._require(cursor, principal, store_id, INVOICE_READ_ROLES)
        zone = ZoneInfo(BUSINESS_TIMEZONE)
        cutoff_day = as_of.astimezone(zone).date().toordinal() - days
        cutoff = datetime.combine(date.fromordinal(cutoff_day), datetime.min.time(), zone)
        cursor.execute(INVOICE_WAITING_OVER_SQL, {"store": store_id, "cutoff": cutoff})
        row = cursor.fetchone()
        return 0 if row is None else int(row[0])

    def list(
        self,
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        status: InvoiceRequestStatus,
        limit: int,
        now: datetime,
    ) -> InvoiceRequestList:
        """One tab of *Hóa đơn cần xuất*: bounded, with every tab's count."""

        _aware(now)
        if not 1 <= limit <= LIST_MAX_LIMIT:
            raise ValueError("limit must be between 1 and the list bound")
        self.authorize_read(cursor, store_id=store_id, principal=principal)
        cursor.execute(_COUNTS_SQL, (store_id,))
        counts = {item.value: 0 for item in InvoiceRequestStatus}
        for row in cursor.fetchall():
            counts[str(row[0])] = int(row[1])
        cursor.execute(_LIST_SQL, {"store": store_id, "status": status.value, "limit": limit + 1})
        rows = cursor.fetchall()
        views = tuple(_view(cursor, row, now) for row in rows[:limit])
        return InvoiceRequestList(
            store_id=store_id,
            status=status,
            evaluated_at=now,
            limit=limit,
            total_count=counts[status.value],
            truncated=len(rows) > limit,
            counts=counts,
            requests=views,
            query_version=INVOICE_LIST_QUERY.label,
        )

    def read(
        self,
        cursor: Any,
        *,
        store_id: UUID,
        request_id: UUID,
        principal: StaffPrincipal,
        now: datetime,
    ) -> InvoiceRequestView:
        _aware(now)
        self.authorize_read(cursor, store_id=store_id, principal=principal)
        return _read_view(cursor, store_id=store_id, request_id=request_id, now=now)

    def order_subject(
        self,
        cursor: Any,
        *,
        store_id: UUID,
        order_id: UUID,
        principal: StaffPrincipal,
        now: datetime,
    ) -> InvoiceSubjectRead:
        """The order page's *Hóa đơn* row: its requests, and whether a new one may be made now."""

        _aware(now)
        self.authorize_read(cursor, store_id=store_id, principal=principal)
        order = _order_subject_row(cursor, store_id=store_id, order_id=order_id)
        if order is None:
            raise InvoiceNotFoundError("order not found in this store")
        commercial, customer_id, customer_name, erased, ticket, issued_on, account_id = order
        notice = read_published_privacy_notice(cursor) is not None
        history, truncated = _history(
            cursor, store_id=store_id, where="r.order_id = %(subject)s", subject=order_id, now=now
        )
        live = _live(history)
        refusal: InvoiceRefusal | None = None
        if not notice:
            refusal = InvoiceRefusal.PRIVACY_NOTICE_UNPUBLISHED
        elif commercial == "CANCELLED":
            refusal = InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE
        elif live is not None or _order_covered_by_month(cursor, order_id):
            refusal = InvoiceRefusal.INVOICE_REQUEST_EXISTS
        savable = account_id is not None and not erased
        prefill, from_profile = _prefill(
            cursor, customer_id=customer_id, name=customer_name, savable=savable
        )
        return InvoiceSubjectRead(
            subject_kind=InvoiceSubjectKind.ORDER,
            order_id=order_id,
            account_id=account_id,
            period_month=None,
            customer_id=customer_id,
            customer_name=customer_name,
            ticket_number=ticket,
            ticket_issued_on=issued_on,
            privacy_notice_published=notice,
            refusal=refusal,
            profile_savable=savable,
            prefill=prefill,
            prefill_from_profile=from_profile,
            amount=_order_amount(cursor, order_id, now),
            live=live,
            history=history,
            history_truncated=truncated,
            evaluated_at=now,
            query_version=INVOICE_LIST_QUERY.label,
        )

    def account_month_subject(
        self,
        cursor: Any,
        *,
        store_id: UUID,
        customer_id: UUID,
        month: date,
        principal: StaffPrincipal,
        now: datetime,
    ) -> InvoiceSubjectRead:
        """The account's *Hóa đơn tháng* row for one calendar month."""

        _aware(now)
        month_start_instant(month)  # refuses a date that is not a first day
        self.authorize_read(cursor, store_id=store_id, principal=principal)
        account = _account_row(cursor, store_id=store_id, customer_id=customer_id)
        if account is None:
            raise InvoiceNotFoundError("no account for this customer in this store")
        account_id, customer_name, erased = account
        notice = read_published_privacy_notice(cursor) is not None
        history, truncated = _history(
            cursor,
            store_id=store_id,
            where="r.account_id = %(subject)s AND r.period_month = %(month)s",
            subject=account_id,
            month=month,
            now=now,
        )
        live = _live(history)
        amount = _account_amount(cursor, account_id, month, now)
        refusal: InvoiceRefusal | None = None
        if not notice:
            refusal = InvoiceRefusal.PRIVACY_NOTICE_UNPUBLISHED
        elif month > statement_month(now) or not amount.charge_count:
            refusal = InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE
        elif live is not None or _month_covered_by_order(cursor, account_id, month):
            refusal = InvoiceRefusal.INVOICE_REQUEST_EXISTS
        savable = not erased
        prefill, from_profile = _prefill(
            cursor, customer_id=customer_id, name=customer_name, savable=savable
        )
        return InvoiceSubjectRead(
            subject_kind=InvoiceSubjectKind.ACCOUNT_MONTH,
            order_id=None,
            account_id=account_id,
            period_month=month,
            customer_id=customer_id,
            customer_name=customer_name,
            ticket_number=None,
            ticket_issued_on=None,
            privacy_notice_published=notice,
            refusal=refusal,
            profile_savable=savable,
            prefill=prefill,
            prefill_from_profile=from_profile,
            amount=amount,
            live=live,
            history=history,
            history_truncated=truncated,
            evaluated_at=now,
            query_version=INVOICE_LIST_QUERY.label,
        )

    # --- writes --------------------------------------------------------------------------------

    def create(self, connection: Any, command: CreateInvoiceRequestCommand) -> StoredInvoiceRequest:
        """*Khách cần hóa đơn*: one request for an order or an account month.

        Refusals, in order: role and store; the privacy notice; the buyer's details; then, under
        the store's lock, the subject (`INVOICE_SUBJECT_UNAVAILABLE`) and whether a live request
        already covers it (`INVOICE_REQUEST_EXISTS`).
        """

        _aware(command.at)
        with connection.cursor() as cursor:
            # Before the idempotency claim, as a customer record's create does: somebody who lost
            # the store must not replay a key into a write, and an unpublished notice is refused
            # whatever was typed.
            self.authorize_write(cursor, store_id=command.store_id, principal=command.principal)
            notice = read_published_privacy_notice(cursor)
            if notice is None:
                raise InvoiceRuleError(InvoiceRefusal.PRIVACY_NOTICE_UNPUBLISHED)
        buyer = clean_buyer(
            unit_name=command.buyer.unit_name,
            tax_code=command.buyer.tax_code,
            address=command.buyer.address,
            email=command.buyer.email,
            name=command.buyer.name,
        )
        kind = InvoiceSubjectKind(command.subject_kind)
        if kind is InvoiceSubjectKind.ORDER:
            if command.order_id is None:
                raise InvoiceRuleError(InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE)
        elif command.customer_id is None or command.period_month is None:
            raise InvoiceRuleError(InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE)
        else:
            month_start_instant(command.period_month)
        notice_version = notice.version_id
        # The ledger hashes this document with the deployment's key and never stores it; the
        # response it stores is the request's id and version only.
        payload: dict[str, object] = {
            "store_id": str(command.store_id),
            "subject_kind": kind.value,
            "order_id": None if command.order_id is None else str(command.order_id),
            "customer_id": None if command.customer_id is None else str(command.customer_id),
            "period_month": (
                None if command.period_month is None else command.period_month.isoformat()
            ),
            "buyer_unit_name": buyer.unit_name,
            "buyer_tax_code": buyer.tax_code,
            "buyer_address": buyer.address,
            "buyer_email": buyer.email,
            "buyer_name": buyer.name,
            "save_profile": command.save_profile,
        }

        def create_once() -> dict[str, object]:
            with connection.cursor() as cursor:
                _lock_store(cursor, command.store_id)
                subject = _resolve_subject(cursor, command, kind)
                cursor.execute(
                    "SELECT coalesce(max(request_number), 0) + 1 FROM invoice_requests "
                    "WHERE store_id = %s",
                    (command.store_id,),
                )
                numbered = cursor.fetchone()
            number = int(numbered[0]) if numbered else 1
            request_id = uuid4()
            if command.save_profile and not subject.profile_savable:
                raise InvoiceRuleError(
                    InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID, field="save_profile"
                )

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO invoice_requests (
                        id, store_id, request_number, subject_kind, order_id, account_id,
                        period_month, buyer_unit_name, buyer_tax_code, buyer_address, buyer_email,
                        buyer_name, privacy_notice_version_id, status, requested_by, requested_at,
                        row_version
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'REQUESTED', %s, %s, 1
                    )
                    """,
                    (
                        request_id,
                        command.store_id,
                        number,
                        kind.value,
                        subject.order_id,
                        subject.account_id,
                        subject.period_month,
                        buyer.unit_name,
                        buyer.tax_code,
                        buyer.address,
                        buyer.email,
                        buyer.name,
                        notice_version,
                        command.principal.staff_user_id,
                        command.at,
                    ),
                )
                if command.save_profile and subject.customer_id is not None:
                    _save_profile(
                        cursor,
                        customer_id=subject.customer_id,
                        store_id=command.store_id,
                        buyer=buyer,
                        actor=command.principal.staff_user_id,
                        at=command.at,
                    )

            subject_facts: dict[str, object] = {
                "store_id": str(command.store_id),
                "subject_kind": kind.value,
                "request_number": number,
            }
            if subject.order_id is not None:
                subject_facts["order_id"] = str(subject.order_id)
            if subject.account_id is not None and subject.period_month is not None:
                subject_facts["account_id"] = str(subject.account_id)
                subject_facts["period_month"] = month_label(subject.period_month)
            try:
                commit_material_change(
                    connection,
                    MaterialChange(
                        aggregate_type="INVOICE_REQUEST",
                        aggregate_id=request_id,
                        aggregate_version=1,
                        event_type="INVOICE_REQUEST_CREATED",
                        # Ids and kinds only: the buyer's details stay in their row.
                        event_payload={
                            **subject_facts,
                            "has_tax_code": buyer.tax_code is not None,
                            "profile_saved": bool(command.save_profile),
                            "privacy_notice_version_id": str(notice_version),
                        },
                        audit_action="INVOICE_REQUEST_CREATE",
                        actor_type="STAFF",
                        actor_id=command.principal.staff_user_id,
                        correlation_id=command.correlation_id,
                        audit_details={
                            **subject_facts,
                            "decision": INVOICE_DECISION,
                            "profile_saved": bool(command.save_profile),
                        },
                        outbox_events=(
                            OutboxEvent(
                                "invoice_request.created.v1",
                                {
                                    "invoice_request_id": str(request_id),
                                    "store_id": str(command.store_id),
                                    "subject_kind": kind.value,
                                },
                                f"invoice-request:{request_id}:created",
                            ),
                        ),
                        occurred_at=command.at,
                    ),
                    mutation,
                )
            except UniqueViolation as error:
                # The partial index is the last word on "one live request per subject".
                raise InvoiceRuleError(InvoiceRefusal.INVOICE_REQUEST_EXISTS) from error
            return {"invoice_request_id": str(request_id), "row_version": 1}

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                scope=f"invoice-request-create:{command.principal.staff_user_id}",
                key=command.idempotency_key,
                payload=payload,
                occurred_at=command.at,
            ),
            create_once,
        )
        return _stored(result.response, store_id=command.store_id, replayed=result.replayed)

    def record_issued(self, connection: Any, command: RecordIssuedCommand) -> StoredInvoiceRequest:
        """*Ghi số hóa đơn*: the bookkeeper's symbol, number and date close it as issued."""

        _aware(command.at)
        with connection.cursor() as cursor:
            self.authorize_close(cursor, store_id=command.store_id, principal=command.principal)
        issued = clean_issued(
            symbol=command.invoice_symbol,
            number=command.invoice_number,
            issued_on=command.invoice_date,
            today=local_day(command.at),
        )

        def record_once() -> dict[str, object]:
            version = _lock_open(connection, command.store_id, command.request_id, command)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT 1 FROM invoice_requests
                    WHERE store_id = %s AND status = 'ISSUED' AND invoice_symbol = %s
                      AND invoice_number = %s
                    """,
                    (command.store_id, issued.symbol, issued.number),
                )
                if cursor.fetchone() is not None:
                    raise InvoiceRuleError(InvoiceRefusal.INVOICE_NUMBER_TAKEN)

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    UPDATE invoice_requests
                    SET status = 'ISSUED', invoice_symbol = %s, invoice_number = %s,
                        invoice_date = %s, closed_by = %s, closed_at = %s,
                        row_version = row_version + 1
                    WHERE id = %s AND store_id = %s AND row_version = %s
                    RETURNING row_version
                    """,
                    (
                        issued.symbol,
                        issued.number,
                        issued.issued_on,
                        command.principal.staff_user_id,
                        command.at,
                        command.request_id,
                        command.store_id,
                        version,
                    ),
                )
                if cursor.fetchone() is None:
                    raise InvoiceStateError("STALE_VERSION: invoice request changed")

            try:
                commit_material_change(
                    connection,
                    MaterialChange(
                        aggregate_type="INVOICE_REQUEST",
                        aggregate_id=command.request_id,
                        aggregate_version=version + 1,
                        event_type="INVOICE_REQUEST_ISSUED_RECORDED",
                        event_payload={
                            "store_id": str(command.store_id),
                            "status": InvoiceRequestStatus.ISSUED.value,
                        },
                        audit_action="INVOICE_REQUEST_RECORD_ISSUED",
                        actor_type="STAFF",
                        actor_id=command.principal.staff_user_id,
                        correlation_id=command.correlation_id,
                        # The invoice's own identifiers, as the provider printed them: not personal.
                        audit_details={
                            "store_id": str(command.store_id),
                            "invoice_symbol": issued.symbol,
                            "invoice_number": issued.number,
                            "invoice_date": issued.issued_on.isoformat(),
                        },
                        outbox_events=(
                            OutboxEvent(
                                "invoice_request.issued_recorded.v1",
                                {
                                    "invoice_request_id": str(command.request_id),
                                    "store_id": str(command.store_id),
                                },
                                f"invoice-request:{command.request_id}:issued",
                            ),
                        ),
                        occurred_at=command.at,
                    ),
                    mutation,
                )
            except UniqueViolation as error:
                raise InvoiceRuleError(InvoiceRefusal.INVOICE_NUMBER_TAKEN) from error
            return {"invoice_request_id": str(command.request_id), "row_version": version + 1}

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                scope=f"invoice-request-issued:{command.principal.staff_user_id}",
                key=command.idempotency_key,
                payload={
                    "store_id": str(command.store_id),
                    "invoice_request_id": str(command.request_id),
                    "expected_row_version": command.expected_row_version,
                    "invoice_symbol": issued.symbol,
                    "invoice_number": issued.number,
                    "invoice_date": issued.issued_on.isoformat(),
                },
                occurred_at=command.at,
            ),
            record_once,
        )
        return _stored(result.response, store_id=command.store_id, replayed=result.replayed)

    def cancel(self, connection: Any, command: CancelInvoiceRequestCommand) -> StoredInvoiceRequest:
        """*Huỷ yêu cầu*: with a reason, and a note when the reason is ``OTHER``."""

        _aware(command.at)
        with connection.cursor() as cursor:
            self.authorize_write(cursor, store_id=command.store_id, principal=command.principal)
        reason = InvoiceCancelReason(command.reason)
        note = clean_cancel_note(reason, command.note)

        def cancel_once() -> dict[str, object]:
            version = _lock_open(connection, command.store_id, command.request_id, command)

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    UPDATE invoice_requests
                    SET status = 'CANCELLED', cancel_reason = %s, cancel_note = %s,
                        closed_by = %s, closed_at = %s, row_version = row_version + 1
                    WHERE id = %s AND store_id = %s AND row_version = %s
                    RETURNING row_version
                    """,
                    (
                        reason.value,
                        note,
                        command.principal.staff_user_id,
                        command.at,
                        command.request_id,
                        command.store_id,
                        version,
                    ),
                )
                if cursor.fetchone() is None:
                    raise InvoiceStateError("STALE_VERSION: invoice request changed")

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="INVOICE_REQUEST",
                    aggregate_id=command.request_id,
                    aggregate_version=version + 1,
                    event_type="INVOICE_REQUEST_CANCELLED",
                    # The reason code only: the note is free text a person typed; it stays in
                    # its row.
                    event_payload={
                        "store_id": str(command.store_id),
                        "status": InvoiceRequestStatus.CANCELLED.value,
                        "reason": reason.value,
                    },
                    audit_action="INVOICE_REQUEST_CANCEL",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    audit_details={
                        "store_id": str(command.store_id),
                        "reason": reason.value,
                        "has_note": note is not None,
                    },
                    outbox_events=(
                        OutboxEvent(
                            "invoice_request.cancelled.v1",
                            {
                                "invoice_request_id": str(command.request_id),
                                "store_id": str(command.store_id),
                                "reason": reason.value,
                            },
                            f"invoice-request:{command.request_id}:cancelled",
                        ),
                    ),
                    occurred_at=command.at,
                ),
                mutation,
            )
            return {"invoice_request_id": str(command.request_id), "row_version": version + 1}

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                scope=f"invoice-request-cancel:{command.principal.staff_user_id}",
                key=command.idempotency_key,
                payload={
                    "store_id": str(command.store_id),
                    "invoice_request_id": str(command.request_id),
                    "expected_row_version": command.expected_row_version,
                    "reason": reason.value,
                    "note": note,
                },
                occurred_at=command.at,
            ),
            cancel_once,
        )
        return _stored(result.response, store_id=command.store_id, replayed=result.replayed)

    def export_open(self, connection: Any, command: ExportCommand) -> InvoiceExport:
        """*Tải danh sách cho kế toán*: every open request, oldest first, as a UTF-8 CSV with a BOM.

        The owner or the approver only. Audited: one `invoice_request_exports` row (who, when, how
        many, the digest of the exact bytes, the query version) with its event, audit and outbox
        rows. The bytes are returned once and never stored -- not in a table and not in the
        idempotency ledger -- which is why this command is not idempotent: a second press is a
        second download, recorded as one.
        """

        _aware(command.at)
        with connection.cursor() as cursor:
            self.authorize_close(cursor, store_id=command.store_id, principal=command.principal)
            cursor.execute(
                _LIST_SQL,
                {
                    "store": command.store_id,
                    "status": InvoiceRequestStatus.REQUESTED.value,
                    "limit": EXPORT_MAX_REQUESTS + 1,
                },
            )
            fetched = cursor.fetchall()
            truncated = len(fetched) > EXPORT_MAX_REQUESTS
            rows: list[tuple[object, ...]] = []
            for row in fetched[:EXPORT_MAX_REQUESTS]:
                view = _view(cursor, row, command.at)
                rows.extend(_export_rows(cursor, view, command.at))
        request_count = min(len(fetched), EXPORT_MAX_REQUESTS)
        label = INVOICE_EXPORT_QUERY.label
        content = _csv_text(
            rows,
            header=(
                label,
                command.at.isoformat(),
                f"{request_count}" + (" (chưa hết danh sách)" if truncated else ""),
                EXPORT_NOTE_VI,
            ),
        )
        content_hash = f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"
        export_id = uuid4()
        local = command.at.astimezone(ZoneInfo(ACCOUNT_TIMEZONE))
        filename = f"yeu-cau-hoa-don-{local:%Y%m%d-%H%M}.csv"

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO invoice_request_exports (
                    id, store_id, query_version, request_count, row_count, truncated, content_hash,
                    produced_by, produced_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    export_id,
                    command.store_id,
                    label,
                    request_count,
                    len(rows),
                    truncated,
                    content_hash,
                    command.principal.staff_user_id,
                    command.at,
                ),
            )

        facts: dict[str, object] = {
            "store_id": str(command.store_id),
            "request_count": request_count,
            "row_count": len(rows),
            "truncated": truncated,
            "content_hash": content_hash,
            "query_version": label,
        }
        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="INVOICE_REQUEST_EXPORT",
                aggregate_id=export_id,
                aggregate_version=1,
                event_type="INVOICE_REQUESTS_EXPORTED",
                event_payload=facts,
                audit_action="INVOICE_REQUEST_EXPORT",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                audit_details=facts,
                outbox_events=(
                    OutboxEvent(
                        "invoice_requests.exported.v1",
                        {"export_id": str(export_id), "store_id": str(command.store_id)},
                        f"invoice-requests-export:{export_id}",
                    ),
                ),
                occurred_at=command.at,
            ),
            mutation,
        )
        return InvoiceExport(
            export_id=export_id,
            store_id=command.store_id,
            filename=filename,
            content_csv=content,
            content_hash=content_hash,
            query_version=label,
            request_count=request_count,
            row_count=len(rows),
            truncated=truncated,
            produced_at=command.at,
        )


# --- subject resolution -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Subject:
    order_id: UUID | None
    account_id: UUID | None
    period_month: date | None
    customer_id: UUID | None
    profile_savable: bool


def _lock_store(cursor: Any, store_id: UUID) -> None:
    """Serialise the store's creates: the request number and the cross-kind check need it."""

    cursor.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"invoice_requests:{store_id}",),
    )


def _resolve_subject(
    cursor: Any, command: CreateInvoiceRequestCommand, kind: InvoiceSubjectKind
) -> _Subject:
    if kind is InvoiceSubjectKind.ORDER:
        assert command.order_id is not None
        order = _order_subject_row(cursor, store_id=command.store_id, order_id=command.order_id)
        if order is None or order[0] == "CANCELLED":
            raise InvoiceRuleError(InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE)
        _, customer_id, _, erased, _, _, account_id = order
        cursor.execute(
            """
            SELECT 1 FROM invoice_requests
            WHERE order_id = %s AND subject_kind = 'ORDER' AND status IN ('REQUESTED', 'ISSUED')
            """,
            (command.order_id,),
        )
        if cursor.fetchone() is not None or _order_covered_by_month(cursor, command.order_id):
            raise InvoiceRuleError(InvoiceRefusal.INVOICE_REQUEST_EXISTS)
        return _Subject(
            order_id=command.order_id,
            account_id=None,
            period_month=None,
            customer_id=customer_id,
            profile_savable=account_id is not None and not erased,
        )
    assert command.customer_id is not None and command.period_month is not None
    account = _account_row(cursor, store_id=command.store_id, customer_id=command.customer_id)
    if account is None or command.period_month > statement_month(command.at):
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE)
    account_id, _, erased = account
    figures = _statement_figures(cursor, account_id=account_id, month=command.period_month)
    if figures.charge_count < 1:
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE)
    cursor.execute(
        """
        SELECT 1 FROM invoice_requests
        WHERE account_id = %s AND period_month = %s AND subject_kind = 'ACCOUNT_MONTH'
          AND status IN ('REQUESTED', 'ISSUED')
        """,
        (account_id, command.period_month),
    )
    if cursor.fetchone() is not None or _month_covered_by_order(
        cursor, account_id, command.period_month
    ):
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_REQUEST_EXISTS)
    return _Subject(
        order_id=None,
        account_id=account_id,
        period_month=command.period_month,
        customer_id=command.customer_id,
        profile_savable=not erased,
    )


def _order_subject_row(
    cursor: Any, *, store_id: UUID, order_id: UUID
) -> tuple[str, UUID | None, str | None, bool, int | None, date | None, UUID | None] | None:
    """(commercial status, customer, the customer's name, erased, ticket, ticket day, account)."""

    cursor.execute(
        """
        SELECT o.commercial_status, o.customer_id, c.display_name, c.erased_at IS NOT NULL,
               t.ticket_number, t.issued_on, a.id
        FROM orders o
        LEFT JOIN customers c ON c.id = o.customer_id AND c.store_id = o.store_id
        LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
        LEFT JOIN customer_accounts a ON a.customer_id = o.customer_id AND a.store_id = o.store_id
        WHERE o.id = %s AND o.store_id = %s
        """,
        (order_id, store_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return (
        str(row[0]),
        None if row[1] is None else _uuid(row[1]),
        None if row[2] is None else str(row[2]),
        bool(row[3]),
        None if row[4] is None else int(row[4]),
        row[5],
        None if row[6] is None else _uuid(row[6]),
    )


def _account_row(
    cursor: Any, *, store_id: UUID, customer_id: UUID
) -> tuple[UUID, str | None, bool] | None:
    """(account, the customer's name, erased) for the customer's account in this store."""

    cursor.execute(
        """
        SELECT a.id, c.display_name, c.erased_at IS NOT NULL
        FROM customer_accounts a
        JOIN customers c ON c.id = a.customer_id AND c.store_id = a.store_id
        WHERE a.customer_id = %s AND a.store_id = %s
        """,
        (customer_id, store_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return _uuid(row[0]), None if row[1] is None else str(row[1]), bool(row[2])


def _order_covered_by_month(cursor: Any, order_id: UUID) -> bool:
    """The order was charged to an account month that has a live request of its own."""

    cursor.execute(
        """
        SELECT 1
        FROM customer_account_charges ch
        JOIN invoice_requests r
          ON r.account_id = ch.account_id AND r.subject_kind = 'ACCOUNT_MONTH'
         AND r.status IN ('REQUESTED', 'ISSUED')
         AND r.period_month = date_trunc('month', ch.charged_at AT TIME ZONE %s)::date
        WHERE ch.order_id = %s
        """,
        (ACCOUNT_TIMEZONE, order_id),
    )
    return cursor.fetchone() is not None


def _month_covered_by_order(cursor: Any, account_id: UUID, month: date) -> bool:
    """One of the orders charged to the account in the month has a live request of its own."""

    cursor.execute(
        """
        SELECT 1
        FROM customer_account_charges ch
        JOIN invoice_requests r
          ON r.order_id = ch.order_id AND r.subject_kind = 'ORDER'
         AND r.status IN ('REQUESTED', 'ISSUED')
        WHERE ch.account_id = %s AND ch.charged_at >= %s AND ch.charged_at < %s
        """,
        (account_id, month_start_instant(month), month_start_instant(next_month(month))),
    )
    return cursor.fetchone() is not None


def _prefill(
    cursor: Any, *, customer_id: UUID | None, name: str | None, savable: bool
) -> tuple[BuyerView | None, bool]:
    """An account customer's saved details, or their name as the unit name; nothing otherwise."""

    if customer_id is None or not savable:
        return None, False
    cursor.execute(
        """
        SELECT buyer_unit_name, buyer_tax_code, buyer_address, buyer_email, buyer_name
        FROM customer_invoice_profiles
        WHERE customer_id = %s AND erased_at IS NULL
        """,
        (customer_id,),
    )
    row = cursor.fetchone()
    if row is not None:
        return (
            BuyerView(
                unit_name=_text(row[0]),
                tax_code=_text(row[1]),
                address=_text(row[2]),
                email=_text(row[3]),
                name=_text(row[4]),
                erased=False,
            ),
            True,
        )
    if name is None:
        return None, False
    return BuyerView(name, None, None, None, None, erased=False), False


def _save_profile(
    cursor: Any,
    *,
    customer_id: UUID,
    store_id: UUID,
    buyer: BuyerDetails,
    actor: UUID,
    at: datetime,
) -> None:
    cursor.execute(
        """
        INSERT INTO customer_invoice_profiles (
            customer_id, store_id, buyer_unit_name, buyer_tax_code, buyer_address, buyer_email,
            buyer_name, updated_by, updated_at, row_version
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
        ON CONFLICT (customer_id) DO UPDATE
        SET buyer_unit_name = EXCLUDED.buyer_unit_name,
            buyer_tax_code = EXCLUDED.buyer_tax_code,
            buyer_address = EXCLUDED.buyer_address,
            buyer_email = EXCLUDED.buyer_email,
            buyer_name = EXCLUDED.buyer_name,
            updated_by = EXCLUDED.updated_by,
            updated_at = EXCLUDED.updated_at,
            row_version = customer_invoice_profiles.row_version + 1
        """,
        (
            customer_id,
            store_id,
            buyer.unit_name,
            buyer.tax_code,
            buyer.address,
            buyer.email,
            buyer.name,
            actor,
            at,
        ),
    )


def _lock_open(
    connection: Any,
    store_id: UUID,
    request_id: UUID,
    command: RecordIssuedCommand | CancelInvoiceRequestCommand,
) -> int:
    """Lock the request and return its version, or refuse: missing, closed, or stale."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT store_id, status, row_version FROM invoice_requests
            WHERE id = %s FOR UPDATE
            """,
            (request_id,),
        )
        row = cursor.fetchone()
        if row is None or _uuid(row[0]) != store_id:
            raise InvoiceNotFoundError("invoice request not found in this store")
        # Membership again, on the cursor that holds the row locked.
        require_store_membership(
            cursor,
            staff_user_id=command.principal.staff_user_id,
            store_id=store_id,
            error=InvoiceAuthorizationError,
        )
    require_open(InvoiceRequestStatus(str(row[1])))
    version = int(row[2])
    if version != command.expected_row_version:
        raise InvoiceStateError("STALE_VERSION: invoice request changed since it was read")
    return version


# --- views and amounts --------------------------------------------------------------------------


def _read_view(
    cursor: Any, *, store_id: UUID, request_id: UUID, now: datetime
) -> InvoiceRequestView:
    cursor.execute(
        "SELECT " + _REQUEST_COLUMNS + _REQUEST_JOINS + " WHERE r.id = %s AND r.store_id = %s",
        (request_id, store_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise InvoiceNotFoundError("invoice request not found in this store")
    return _view(cursor, row, now)


def _history(
    cursor: Any,
    *,
    store_id: UUID,
    where: str,
    subject: UUID,
    now: datetime,
    month: date | None = None,
) -> tuple[tuple[InvoiceRequestView, ...], bool]:
    cursor.execute(
        "SELECT "
        + _REQUEST_COLUMNS
        + _REQUEST_JOINS
        + " WHERE r.store_id = %(store)s AND "
        + where
        + " ORDER BY r.requested_at DESC, r.request_number DESC LIMIT %(limit)s",
        {"store": store_id, "subject": subject, "month": month, "limit": SUBJECT_HISTORY_LIMIT + 1},
    )
    rows = cursor.fetchall()
    return (
        tuple(_view(cursor, row, now) for row in rows[:SUBJECT_HISTORY_LIMIT]),
        len(rows) > SUBJECT_HISTORY_LIMIT,
    )


def _live(history: tuple[InvoiceRequestView, ...]) -> InvoiceRequestView | None:
    return next(
        (
            item
            for item in history
            if item.status in (InvoiceRequestStatus.REQUESTED, InvoiceRequestStatus.ISSUED)
        ),
        None,
    )


def _view(cursor: Any, row: tuple[Any, ...], now: datetime) -> InvoiceRequestView:
    kind = InvoiceSubjectKind(str(row[3]))
    order_id = None if row[4] is None else _uuid(row[4])
    account_id = None if row[5] is None else _uuid(row[5])
    period_month = row[6]
    if kind is InvoiceSubjectKind.ORDER:
        assert order_id is not None
        amount = _order_amount(cursor, order_id, now)
    else:
        assert account_id is not None and isinstance(period_month, date)
        amount = _account_amount(cursor, account_id, period_month, now)
    number = int(row[2])
    return InvoiceRequestView(
        request_id=_uuid(row[0]),
        store_id=_uuid(row[1]),
        request_number=number,
        request_code=request_code(number),
        subject_kind=kind,
        order_id=order_id,
        ticket_number=None if row[27] is None else int(row[27]),
        ticket_issued_on=row[28],
        account_id=account_id,
        period_month=period_month,
        customer_id=None if row[7] is None else _uuid(row[7]),
        customer_name=_text(row[29]),
        buyer=BuyerView(
            unit_name=_text(row[8]),
            tax_code=_text(row[9]),
            address=_text(row[10]),
            email=_text(row[11]),
            name=_text(row[12]),
            erased=row[13] is not None,
        ),
        status=InvoiceRequestStatus(str(row[14])),
        invoice_symbol=_text(row[15]),
        invoice_number=_text(row[16]),
        invoice_date=row[17],
        cancel_reason=None if row[18] is None else InvoiceCancelReason(str(row[18])),
        cancel_note=_text(row[19]),
        requested_by_staff_id=_uuid(row[20]),
        requested_by_name=_text(row[21]),
        requested_at=row[22],
        closed_by_staff_id=None if row[23] is None else _uuid(row[23]),
        closed_by_name=_text(row[24]),
        closed_at=row[25],
        row_version=int(row[26]),
        amount=amount,
    )


def _order_amount(cursor: Any, order_id: UUID, now: datetime) -> InvoiceAmount:
    """The order's charges now: the quoted total and, when there is one, the storage fee."""

    cursor.execute(_ORDER_TOTAL_SQL, (order_id,))
    row = cursor.fetchone()
    quoted = QuotedTotal(
        None if row is None or row[0] is None else int(row[0]),
        None if row is None or row[1] is None else int(row[1]),
    )
    storage = storage_fee_for_order(cursor, order_id=order_id, moment=now)
    charges = owed_charges(quoted, storage_fee_vnd=storage.fee.amount_vnd)
    fee = storage.fee.amount_vnd
    return InvoiceAmount(
        source="ORDER_CHARGES",
        total_vnd=None if charges is None else owed_total(charges),
        storage_fee_vnd=fee if charges is not None and fee > 0 else None,
        charge_count=None,
        month_ended=None,
    )


def _account_amount(cursor: Any, account_id: UUID, month: date, now: datetime) -> InvoiceAmount:
    """The month's account charges, as the statement reads them."""

    figures = _statement_figures(cursor, account_id=account_id, month=month)
    return InvoiceAmount(
        source="ACCOUNT_STATEMENT",
        total_vnd=figures.charges_vnd,
        storage_fee_vnd=None,
        charge_count=figures.charge_count,
        month_ended=month_has_ended(month, now),
    )


# --- the bookkeeper's file ----------------------------------------------------------------------


def _subject_label(view: InvoiceRequestView) -> str:
    if view.subject_kind is InvoiceSubjectKind.ACCOUNT_MONTH:
        assert view.period_month is not None
        return f"Công nợ tháng {view.period_month:%m/%Y}"
    if view.ticket_number is not None and view.ticket_issued_on is not None:
        return f"Phiếu {view.ticket_number} ngày {view.ticket_issued_on:%d/%m/%Y}"
    assert view.order_id is not None
    return f"Đơn {str(view.order_id)[:8].upper()}"


def _export_rows(cursor: Any, view: InvoiceRequestView, now: datetime) -> list[tuple[object, ...]]:
    """One row per line the request is for; the request's facts and its total on the first."""

    lines = (
        _order_lines(cursor, view)
        if view.subject_kind is InvoiceSubjectKind.ORDER
        else _month_lines(cursor, view)
    )
    if not lines:
        lines = [("", "", "", None)]
    buyer = view.buyer
    head: tuple[object, ...] = (
        view.request_code,
        local_day(view.requested_at).isoformat(),
        buyer.unit_name or ("Khách đã xoá thông tin" if buyer.erased else ""),
        buyer.tax_code,
        buyer.address,
        buyer.email,
        buyer.name,
        _subject_label(view),
    )
    rows: list[tuple[object, ...]] = []
    for position, (description, quantity, unit, amount) in enumerate(lines):
        rows.append(
            (
                *(head if position == 0 else (view.request_code, *("",) * 7)),
                description,
                quantity,
                unit,
                amount,
                view.amount.total_vnd if position == 0 else None,
            )
        )
    return rows


def _order_lines(cursor: Any, view: InvoiceRequestView) -> list[tuple[str, str, str, int | None]]:
    """The order's service lines from its stored quote snapshot, then the fees that are not lines.

    Read verbatim: each line's net amount (after any promotion or credit the snapshot allocated to
    it), the delivery fee and an approved surcharge -- which the snapshot guarantees sum to its
    total -- and the storage fee the order's charges carry now.
    """

    assert view.order_id is not None
    cursor.execute(
        "SELECT current_quote_id, current_quote_revision FROM orders WHERE id = %s",
        (view.order_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return []
    stored = QuoteRepository.get_revision(cursor, _uuid(row[0]), int(row[1]))
    if stored is None:
        return []
    data = parse_quote_revision(json.loads(stored.document.canonical_json)).data
    names = _service_names(cursor, data)
    lines: list[tuple[str, str, str, int | None]] = []
    for line in data.lines:
        amounts = line.amounts
        if isinstance(amounts, ExactLineAmounts):
            amount: int | None = amounts.net_amount_vnd
        elif amounts.net_amount_min_vnd == amounts.net_amount_max_vnd:
            amount = amounts.net_amount_min_vnd
        else:
            amount = None
        lines.append(
            (
                names.get(line.service_code, line.service_code),
                line.quantity,
                UNIT_VI.get(line.unit.value, line.unit.value.lower()),
                amount,
            )
        )
    if data.totals.delivery_fee_vnd:
        lines.append((DELIVERY_LINE_VI, "1", "lần", data.totals.delivery_fee_vnd))
    if data.totals.approved_surcharge_vnd:
        lines.append((SURCHARGE_LINE_VI, "1", "lần", data.totals.approved_surcharge_vnd))
    if view.amount.storage_fee_vnd:
        lines.append((STORAGE_LINE_VI, "1", "lần", view.amount.storage_fee_vnd))
    return lines


def _service_names(cursor: Any, data: Any) -> dict[str, str]:
    """The service names in the pricebook the quote was priced under, by code."""

    reference = next(
        (item for item in data.configuration_snapshots if item.config_type == "PRICEBOOK"), None
    )
    if reference is None:
        return {}
    payload = ConfigurationRepository.get_published(cursor, reference.version_id)
    services = payload.get("services") if isinstance(payload, dict) else None
    if not isinstance(services, list):
        return {}
    return {
        str(item["code"]): str(item["display_name"])
        for item in services
        if isinstance(item, dict) and "code" in item and "display_name" in item
    }


def _month_lines(cursor: Any, view: InvoiceRequestView) -> list[tuple[str, str, str, int | None]]:
    """One line per order charged to the account in the month, with what went on the account."""

    assert view.account_id is not None and view.period_month is not None
    cursor.execute(
        """
        SELECT c.order_id, c.charged_at, c.amount_vnd, t.ticket_number, t.issued_on
        FROM customer_account_charges c
        JOIN orders o ON o.id = c.order_id AND o.store_id = c.store_id
        LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
        WHERE c.account_id = %s AND c.charged_at >= %s AND c.charged_at < %s
        ORDER BY c.charged_at, c.id
        """,
        (
            view.account_id,
            month_start_instant(view.period_month),
            month_start_instant(next_month(view.period_month)),
        ),
    )
    lines: list[tuple[str, str, str, int | None]] = []
    for order_id, charged_at, amount, ticket, issued_on in cursor.fetchall():
        name = (
            f"Phiếu {int(ticket)} ngày {issued_on:%d/%m/%Y}"
            if ticket is not None and issued_on is not None
            else f"Đơn {str(order_id)[:8].upper()}"
        )
        lines.append(
            (
                f"Giặt ủi — {name} (ghi công nợ {local_day(charged_at):%d/%m})",
                "1",
                "đơn",
                int(amount),
            )
        )
    return lines


_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: object) -> str:
    """One CSV cell. Every text this file carries was refused at capture if it began with what a
    spreadsheet executes; this refuses again, over the bytes that leave, rather than trust it."""

    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        raise InvoiceStateError("a value in this list would be executed by a spreadsheet")
    return text


def _csv_text(rows: list[tuple[object, ...]], *, header: tuple[object, ...]) -> str:
    """UTF-8 with a byte-order mark, for Excel; the key/value rows, a blank row, the columns."""

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for key, value in zip(EXPORT_HEADER_KEYS, header, strict=True):
        writer.writerow([key, _cell(value)])
    writer.writerow([])
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return "﻿" + buffer.getvalue()


# --- small helpers ------------------------------------------------------------------------------


def _stored(value: dict[str, object], *, store_id: UUID, replayed: bool) -> StoredInvoiceRequest:
    return StoredInvoiceRequest(
        request_id=UUID(str(value["invoice_request_id"])),
        store_id=store_id,
        row_version=int(str(value["row_version"])),
        replayed=replayed,
    )


def _aware(moment: datetime) -> None:
    if moment.tzinfo is None:
        raise ValueError("an invoice-request instant must be timezone-aware")


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _text(value: object) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "AMOUNT_HEADER_VI",
    "EXPORT_COLUMNS",
    "EXPORT_MAX_REQUESTS",
    "INVOICE_CLOSE_ROLES",
    "INVOICE_EXPORT_QUERY",
    "INVOICE_LIST_QUERY",
    "INVOICE_READ_ROLES",
    "INVOICE_WAITING_OVER_SQL",
    "INVOICE_WRITE_ROLES",
    "LIST_DEFAULT_LIMIT",
    "LIST_MAX_LIMIT",
    "BuyerInput",
    "BuyerView",
    "CancelInvoiceRequestCommand",
    "CreateInvoiceRequestCommand",
    "ExportCommand",
    "InvoiceAmount",
    "InvoiceAuthorizationError",
    "InvoiceExport",
    "InvoiceNotFoundError",
    "InvoiceRequestList",
    "InvoiceRequestRepository",
    "InvoiceRequestView",
    "InvoiceStateError",
    "InvoiceSubjectRead",
    "RecordIssuedCommand",
    "StoredInvoiceRequest",
]
