"""`EINVOICE-REQUEST-001` (`DEC-040`) against real PostgreSQL: invoice requests.

What only the database can prove, each against the rows it holds:

* **refused until the privacy notice is published**, on a database where nothing was published,
  whatever was typed -- and nothing written;
* **a request on an order** records the buyer, reads the order's charges now, and writes its event,
  audit and outbox rows with no buyer detail in any of them; a replay answers the first result and a
  changed payload under the same key is a conflict;
* **one live request per subject** (and across the two kinds): a second is refused, a cancelled one
  frees the subject, a cancelled order is unavailable;
* **an account month** reads the statement's charges, saves the buyer for next time on an explicit
  tick, and pre-fills the customer's next request; a month with nothing charged, or in the future,
  is unavailable;
* **issued is terminal and immutable**, recorded by the owner or approver only, under If-Match, with
  a symbol and number no other request of the store holds;
* **erasure** blanks the buyer of the customer's requests that were never issued, and their profile,
  and leaves an issued one whole;
* **the bookkeeper's download**: UTF-8 with a BOM, the query version, one row per service line with
  the amounts as the snapshot holds them, audited, owner or approver only, and no phone value in it.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.customers import CustomerRepository
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.invoice_requests import (
    AMOUNT_HEADER_VI,
    EXPORT_COLUMNS,
    INVOICE_EXPORT_QUERY,
    BuyerInput,
    CancelInvoiceRequestCommand,
    CreateInvoiceRequestCommand,
    ExportCommand,
    InvoiceAuthorizationError,
    InvoiceNotFoundError,
    InvoiceRequestRepository,
    InvoiceStateError,
    RecordIssuedCommand,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository
from nha_trang_laundry_domain.accounts import statement_month
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode
from nha_trang_laundry_domain.customers import CustomerKind, ErasureReason
from nha_trang_laundry_domain.invoice_requests import (
    InvoiceCancelReason,
    InvoiceRefusal,
    InvoiceRequestStatus,
    InvoiceRuleError,
    InvoiceSubjectKind,
)
from nha_trang_laundry_domain.order_steps import OrderStep
from quote_test_data import accepted_quote
from test_customer_accounts import Shop, scratch_url
from test_customer_records import _join, _mobile, _person, _store
from test_order_step_repository import TOTAL_VND, _step

__all__ = ["scratch_url"]

BUYER = BuyerInput(
    unit_name="Công ty TNHH Biển Xanh",
    tax_code="4201234567",
    address="12 Trần Phú, Nha Trang",
    email="ketoan@bienxanh.vn",
    name="Nguyễn Văn An",
)


@pytest.fixture
def connection() -> Iterator[psycopg.Connection[Any]]:
    import os

    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(url, autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def shop(connection: Any) -> Shop:
    return Shop(connection)


def _walk_in_order(shop: Shop, *, received: bool = True) -> UUID:
    quote_id, revision, quote, contact_id = accepted_quote(
        shop.connection, store_id=shop.store_id, principal=shop.counter
    )
    order_id = (
        OrderRepository()
        .create(
            shop.connection,
            CreateOrderCommand(
                shop.store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                shop.counter,
                f"order-{uuid4().hex}",
                uuid4(),
                datetime.now(UTC),
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )
    if received:
        _step(shop.connection, order_id, shop.counter, 1, OrderStep.RECEIVE, slot_approved=True)
    return order_id


def _create(
    shop: Shop,
    *,
    order_id: UUID | None = None,
    customer_id: UUID | None = None,
    month: date | None = None,
    buyer: BuyerInput = BUYER,
    principal: StaffPrincipal | None = None,
    key: str | None = None,
    save_profile: bool = False,
    at: datetime | None = None,
) -> Any:
    return InvoiceRequestRepository().create(
        shop.connection,
        CreateInvoiceRequestCommand(
            store_id=shop.store_id,
            subject_kind=(
                InvoiceSubjectKind.ORDER
                if order_id is not None
                else InvoiceSubjectKind.ACCOUNT_MONTH
            ),
            order_id=order_id,
            customer_id=customer_id,
            period_month=month,
            buyer=buyer,
            save_profile=save_profile,
            principal=principal or shop.counter,
            idempotency_key=key or f"invoice-{uuid4().hex}",
            correlation_id=uuid4(),
            at=at or datetime.now(UTC),
        ),
    )


def _read(shop: Shop, request_id: UUID, principal: StaffPrincipal | None = None) -> Any:
    with shop.connection.cursor() as cursor:
        return InvoiceRequestRepository().read(
            cursor,
            store_id=shop.store_id,
            request_id=request_id,
            principal=principal or shop.counter,
            now=datetime.now(UTC),
        )


def _order_subject(shop: Shop, order_id: UUID) -> Any:
    with shop.connection.cursor() as cursor:
        return InvoiceRequestRepository().order_subject(
            cursor,
            store_id=shop.store_id,
            order_id=order_id,
            principal=shop.counter,
            now=datetime.now(UTC),
        )


def _issue(
    shop: Shop,
    request_id: UUID,
    version: int,
    *,
    symbol: str = "1c26tyy",
    number: str = "00000123",
    on: date | None = None,
    principal: StaffPrincipal | None = None,
) -> Any:
    return InvoiceRequestRepository().record_issued(
        shop.connection,
        RecordIssuedCommand(
            store_id=shop.store_id,
            request_id=request_id,
            expected_row_version=version,
            invoice_symbol=symbol,
            invoice_number=number,
            invoice_date=on or datetime.now(UTC).date() - timedelta(days=1),
            principal=principal or shop.owner,
            idempotency_key=f"issued-{uuid4().hex}",
            correlation_id=uuid4(),
            at=datetime.now(UTC),
        ),
    )


def _cancel(
    shop: Shop,
    request_id: UUID,
    version: int,
    reason: InvoiceCancelReason = InvoiceCancelReason.CUSTOMER_WITHDREW,
    note: str | None = None,
) -> Any:
    return InvoiceRequestRepository().cancel(
        shop.connection,
        CancelInvoiceRequestCommand(
            store_id=shop.store_id,
            request_id=request_id,
            expected_row_version=version,
            reason=reason,
            note=note,
            principal=shop.counter,
            idempotency_key=f"cancel-{uuid4().hex}",
            correlation_id=uuid4(),
            at=datetime.now(UTC),
        ),
    )


def _one(connection: Any, query: str, *params: object) -> Any:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    assert row is not None
    return row[0]


def _ledger_text(connection: Any, aggregate_id: UUID) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT coalesce(string_agg(payload::text, ' '), '') FROM domain_events
            WHERE aggregate_id = %(id)s
            UNION ALL
            SELECT coalesce(string_agg(details::text, ' '), '') FROM audit_events
            WHERE aggregate_id = %(id)s
            UNION ALL
            SELECT coalesce(string_agg(payload::text, ' '), '') FROM outbox_events
            WHERE aggregate_id = %(id)s
            """,
            {"id": aggregate_id},
        )
        return " ".join(str(row[0]) for row in cursor.fetchall())


# --- the privacy notice -------------------------------------------------------------------------


def test_capture_is_refused_until_the_privacy_notice_is_published(scratch_url: str) -> None:
    with psycopg.connect(scratch_url, autocommit=True) as connection:
        apply_migrations(connection)
        store_id = _store(connection)
        counter = _join(connection, store_id, _person(connection, StaffRole.OPERATOR))
        quote_id, revision, quote, contact_id = accepted_quote(
            connection, store_id=store_id, principal=counter
        )
        order_id = (
            OrderRepository()
            .create(
                connection,
                CreateOrderCommand(
                    store_id,
                    contact_id,
                    quote_id,
                    revision,
                    quote.document.snapshot_hash,
                    FulfillmentMode.SELF_DROP_SELF_COLLECT,
                    counter,
                    f"order-{uuid4().hex}",
                    uuid4(),
                    datetime.now(UTC),
                    AcquisitionSource.WALK_IN,
                ),
            )
            .order_id
        )
        repository = InvoiceRequestRepository()
        with connection.cursor() as cursor:
            subject = repository.order_subject(
                cursor,
                store_id=store_id,
                order_id=order_id,
                principal=counter,
                now=datetime.now(UTC),
            )
        assert subject.privacy_notice_published is False
        assert subject.refusal is InvoiceRefusal.PRIVACY_NOTICE_UNPUBLISHED
        # Refused before anything typed is looked at: even an invalid tax code meets the notice.
        for buyer in (BUYER, BuyerInput("", "12", None, None, None)):
            with pytest.raises(InvoiceRuleError) as caught:
                repository.create(
                    connection,
                    CreateInvoiceRequestCommand(
                        store_id=store_id,
                        subject_kind=InvoiceSubjectKind.ORDER,
                        order_id=order_id,
                        customer_id=None,
                        period_month=None,
                        buyer=buyer,
                        save_profile=False,
                        principal=counter,
                        idempotency_key=f"k-{uuid4().hex}",
                        correlation_id=uuid4(),
                        at=datetime.now(UTC),
                    ),
                )
            assert caught.value.code is InvoiceRefusal.PRIVACY_NOTICE_UNPUBLISHED
        assert _one(connection, "SELECT count(*) FROM invoice_requests") == 0
        assert (
            _one(
                connection,
                "SELECT count(*) FROM command_idempotency_records WHERE scope LIKE 'invoice-%%'",
            )
            == 0
        )


# --- an order ------------------------------------------------------------------------------------


def test_a_request_on_an_order_keeps_the_buyer_and_reads_the_charges_now(shop: Shop) -> None:
    order_id = _walk_in_order(shop)
    before = _order_subject(shop, order_id)
    assert before.refusal is None and before.live is None
    assert before.amount.total_vnd == TOTAL_VND
    assert before.profile_savable is False and before.prefill is None

    key = f"invoice-{uuid4().hex}"
    stored = _create(shop, order_id=order_id, key=key)
    assert stored.replayed is False and stored.row_version == 1
    view = _read(shop, stored.request_id)
    assert view.status is InvoiceRequestStatus.REQUESTED
    assert view.request_code.startswith("YC-")
    assert view.buyer.unit_name == BUYER.unit_name and view.buyer.tax_code == BUYER.tax_code
    assert view.amount.source == "ORDER_CHARGES" and view.amount.total_vnd == TOTAL_VND
    assert view.subject_kind is InvoiceSubjectKind.ORDER and view.order_id == order_id
    assert view.ticket_number is not None

    # A replay answers the first result; the same key with other words is a conflict.
    again = _create(shop, order_id=order_id, key=key)
    assert again.request_id == stored.request_id and again.replayed is True
    with pytest.raises(IdempotencyConflictError):
        _create(
            shop,
            order_id=order_id,
            key=key,
            buyer=BuyerInput("Công ty khác", None, None, None, None),
        )

    # The event, the audit row and the outbox row carry ids and kinds, never the buyer.
    text = _ledger_text(shop.connection, stored.request_id)
    assert (
        _one(
            shop.connection,
            "SELECT count(*) FROM domain_events WHERE aggregate_id = %s "
            "AND event_type = 'INVOICE_REQUEST_CREATED'",
            stored.request_id,
        )
        == 1
    )
    for secret in (BUYER.unit_name, BUYER.tax_code, BUYER.address, BUYER.email, BUYER.name):
        assert secret is not None and secret not in text
    assert (
        _one(
            shop.connection,
            "SELECT count(*) FROM command_idempotency_records WHERE idempotency_key = %s "
            "AND response::text LIKE %s",
            key,
            "%Biển Xanh%",
        )
        == 0
    )

    subject = _order_subject(shop, order_id)
    assert subject.refusal is InvoiceRefusal.INVOICE_REQUEST_EXISTS
    assert subject.live is not None and subject.live.request_id == stored.request_id


def test_one_live_request_per_order_and_a_cancelled_one_frees_it(shop: Shop) -> None:
    order_id = _walk_in_order(shop)
    first = _create(shop, order_id=order_id)
    with pytest.raises(InvoiceRuleError) as caught:
        _create(shop, order_id=order_id)
    assert caught.value.code is InvoiceRefusal.INVOICE_REQUEST_EXISTS

    with pytest.raises(InvoiceRuleError) as caught:
        _cancel(shop, first.request_id, 1, InvoiceCancelReason.OTHER, None)
    assert caught.value.code is InvoiceRefusal.INVOICE_CANCEL_NOTE_REQUIRED
    with pytest.raises(InvoiceStateError):
        _cancel(shop, first.request_id, 7)
    cancelled = _cancel(shop, first.request_id, 1, InvoiceCancelReason.WRONG_DETAILS)
    assert cancelled.row_version == 2
    view = _read(shop, first.request_id)
    assert view.status is InvoiceRequestStatus.CANCELLED
    assert view.cancel_reason is InvoiceCancelReason.WRONG_DETAILS
    with pytest.raises(InvoiceRuleError) as caught:
        _cancel(shop, first.request_id, 2)
    assert caught.value.code is InvoiceRefusal.INVOICE_REQUEST_CLOSED

    second = _create(shop, order_id=order_id)
    assert second.request_id != first.request_id
    assert _read(shop, second.request_id).request_number == view.request_number + 1

    # A cancelled order has nothing to invoice.
    fresh = _walk_in_order(shop, received=False)
    _step(shop.connection, fresh, shop.counter, 1, OrderStep.CANCEL)
    assert _order_subject(shop, fresh).refusal is InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE
    with pytest.raises(InvoiceRuleError) as caught:
        _create(shop, order_id=fresh)
    assert caught.value.code is InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE

    # Another store's order is not this store's subject.
    other = Shop(shop.connection)
    elsewhere = _walk_in_order(other)
    with pytest.raises(InvoiceRuleError) as caught:
        _create(shop, order_id=elsewhere)
    assert caught.value.code is InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE
    with pytest.raises(InvoiceNotFoundError):
        _order_subject(shop, elsewhere)


def test_the_buyer_details_are_checked_before_anything_is_written(shop: Shop) -> None:
    order_id = _walk_in_order(shop)
    cases = (
        (BuyerInput(" ", None, None, None, None), InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID),
        (BuyerInput("Công ty A", "12345", "1 Lê Lợi", None, None), "INVOICE_TAX_CODE_SHAPE"),
        (BuyerInput("Công ty A", "4201234567", None, None, None), "INVOICE_ADDRESS_REQUIRED"),
        (BuyerInput("=HYPERLINK(1)", None, None, None, None), "INVOICE_BUYER_FIELD_INVALID"),
        (
            BuyerInput("Công ty A", None, None, "khong-phai-email", None),
            "INVOICE_BUYER_FIELD_INVALID",
        ),
        (
            BuyerInput("Công ty A 0905 123 456", None, None, None, None),
            "INVOICE_FIELD_LOOKS_LIKE_PHONE",
        ),
    )
    for buyer, code in cases:
        with pytest.raises(InvoiceRuleError) as caught:
            _create(shop, order_id=order_id, buyer=buyer)
        assert caught.value.code == code
    assert (
        _one(shop.connection, "SELECT count(*) FROM invoice_requests WHERE order_id = %s", order_id)
        == 0
    )
    # 10 digits, 10 + 3, or 12 are all a tax code; the schema refuses what the domain refuses.
    for tax_code in ("4201234567-001", "001234567890"):
        _create(
            shop,
            order_id=_walk_in_order(shop),
            buyer=BuyerInput("Công ty B", tax_code, "5 Yersin", None, None),
        )
    # A buyer is never edited in place (only erased whole), and a written one keeps its address.
    with pytest.raises(psycopg.errors.RaiseException), shop.connection.transaction():
        shop.connection.execute(
            "UPDATE invoice_requests SET buyer_address = NULL, row_version = row_version + 1 "
            "WHERE store_id = %s AND buyer_tax_code IS NOT NULL",
            (shop.store_id,),
        )
    with pytest.raises(psycopg.errors.CheckViolation), shop.connection.transaction():
        shop.connection.execute(
            """
            INSERT INTO invoice_requests (
                id, store_id, request_number, subject_kind, order_id, buyer_unit_name,
                buyer_tax_code, privacy_notice_version_id, status, requested_by, requested_at,
                row_version
            )
            SELECT %s, store_id, 9999, 'ORDER', %s, 'Công ty C', '4201234567',
                   privacy_notice_version_id, 'REQUESTED', requested_by, requested_at, 1
            FROM invoice_requests WHERE store_id = %s LIMIT 1
            """,
            (uuid4(), order_id, shop.store_id),
        )


# --- an account month ---------------------------------------------------------------------------


def test_an_account_month_reads_the_statement_and_saves_the_buyer_for_next_time(
    shop: Shop,
) -> None:
    customer_id = shop.customer()
    shop.open(customer_id, 5_000_000)
    first = shop.ready_order(customer_id)
    second = shop.ready_order(customer_id)
    shop.charge(first)
    shop.charge(second)
    month = statement_month(datetime.now(UTC))
    repository = InvoiceRequestRepository()
    with shop.connection.cursor() as cursor:
        subject = repository.account_month_subject(
            cursor,
            store_id=shop.store_id,
            customer_id=customer_id,
            month=month,
            principal=shop.counter,
            now=datetime.now(UTC),
        )
        statement = shop.account(customer_id).account.current_statement
    assert subject.refusal is None and subject.profile_savable is True
    # Nothing saved yet: the customer's name stands in for the unit name.
    assert subject.prefill is not None and subject.prefill.unit_name == "Homestay Biển Xanh"
    assert subject.prefill_from_profile is False
    assert subject.amount is not None
    assert subject.amount.total_vnd == statement.charges_vnd == 2 * TOTAL_VND
    assert subject.amount.charge_count == 2

    # An order charged to the month may not be requested on its own while the month is not.
    stored = _create(shop, customer_id=customer_id, month=month, save_profile=True)
    view = _read(shop, stored.request_id)
    assert view.subject_kind is InvoiceSubjectKind.ACCOUNT_MONTH and view.period_month == month
    assert view.customer_id == customer_id
    with pytest.raises(InvoiceRuleError) as caught:
        _create(shop, order_id=first)
    assert caught.value.code is InvoiceRefusal.INVOICE_REQUEST_EXISTS
    assert _order_subject(shop, first).refusal is InvoiceRefusal.INVOICE_REQUEST_EXISTS

    # The tick saved the buyer, and the customer's next order pre-fills it.
    third = shop.ready_order(customer_id)
    prefilled = _order_subject(shop, third)
    assert prefilled.prefill_from_profile is True
    assert prefilled.prefill is not None and prefilled.prefill.tax_code == BUYER.tax_code
    assert prefilled.profile_savable is True

    # No charges in the month, or a month to come: nothing to invoice.
    for other in (date(month.year - 1, month.month, 1), date(month.year + 1, month.month, 1)):
        with pytest.raises(InvoiceRuleError) as caught:
            _create(shop, customer_id=customer_id, month=other)
        assert caught.value.code is InvoiceRefusal.INVOICE_SUBJECT_UNAVAILABLE
    # "Lưu cho lần sau" only for an account customer.
    with pytest.raises(InvoiceRuleError) as caught:
        _create(shop, order_id=_walk_in_order(shop), save_profile=True)
    assert caught.value.field == "save_profile"


# --- issued and cancelled -----------------------------------------------------------------------


def test_issued_is_recorded_by_the_owner_once_and_then_nothing_moves(shop: Shop) -> None:
    approver = _join(
        shop.connection, shop.store_id, _person(shop.connection, StaffRole.OPS_APPROVER)
    )
    stored = _create(shop, order_id=_walk_in_order(shop))
    with pytest.raises(InvoiceAuthorizationError):
        _issue(shop, stored.request_id, 1, principal=shop.counter)
    tomorrow = datetime.now(UTC).date() + timedelta(days=2)
    for bad in ({"symbol": "1C26-TYY"}, {"number": "123456789"}, {"on": tomorrow}):
        with pytest.raises(InvoiceRuleError) as caught:
            _issue(shop, stored.request_id, 1, **bad)
        assert caught.value.code is InvoiceRefusal.INVOICE_ISSUED_DETAILS_INVALID
    with pytest.raises(InvoiceStateError):
        _issue(shop, stored.request_id, 5)
    issued = _issue(shop, stored.request_id, 1, principal=approver, number="0000077")
    assert issued.row_version == 2
    view = _read(shop, stored.request_id)
    assert view.status is InvoiceRequestStatus.ISSUED
    assert (view.invoice_symbol, view.invoice_number) == ("1C26TYY", "0000077")
    with pytest.raises(InvoiceRuleError) as caught:
        _issue(shop, stored.request_id, 2)
    assert caught.value.code is InvoiceRefusal.INVOICE_REQUEST_CLOSED
    with pytest.raises(InvoiceRuleError) as caught:
        _cancel(shop, stored.request_id, 2)
    assert caught.value.code is InvoiceRefusal.INVOICE_REQUEST_CLOSED

    # The same symbol and number on a second request is a typing slip.
    other = _create(shop, order_id=_walk_in_order(shop))
    with pytest.raises(InvoiceRuleError) as caught:
        _issue(shop, other.request_id, 1, number="0000077")
    assert caught.value.code is InvoiceRefusal.INVOICE_NUMBER_TAKEN

    # The schema holds it too: an issued row is immutable, and nothing is deleted.
    with pytest.raises(psycopg.errors.RaiseException), shop.connection.transaction():
        shop.connection.execute(
            "UPDATE invoice_requests SET invoice_number = '1', row_version = row_version + 1 "
            "WHERE id = %s",
            (stored.request_id,),
        )
    with pytest.raises(psycopg.errors.RaiseException), shop.connection.transaction():
        shop.connection.execute("DELETE FROM invoice_requests WHERE id = %s", (stored.request_id,))
    audit = _one(
        shop.connection,
        "SELECT details::text FROM audit_events WHERE aggregate_id = %s "
        "AND action = 'INVOICE_REQUEST_RECORD_ISSUED'",
        stored.request_id,
    )
    assert "0000077" in audit and BUYER.unit_name not in audit


def test_the_summary_counts_requests_waiting_more_than_three_shop_days(shop: Shop) -> None:
    """`SUMMARY-ATTENTION-001`: open requests recorded more than three shop days ago, only."""

    now = datetime.now(UTC)
    old = _create(shop, order_id=_walk_in_order(shop), at=now - timedelta(days=5))
    _create(shop, order_id=_walk_in_order(shop), at=now - timedelta(days=1))
    issued = _create(shop, order_id=_walk_in_order(shop), at=now - timedelta(days=6))
    _issue(shop, issued.request_id, 1)

    def waiting(at: datetime) -> int:
        with shop.connection.cursor() as cursor:
            return InvoiceRequestRepository.count_waiting_over(
                cursor, store_id=shop.store_id, principal=shop.counter, as_of=at, days=3
            )

    assert waiting(now) == 1
    # Recorded five shop days before: not yet "more than three" two days earlier.
    assert waiting(now - timedelta(days=2)) == 0
    _cancel(shop, old.request_id, 1)
    assert waiting(now) == 0
    stranger = Shop(shop.connection)
    with pytest.raises(InvoiceAuthorizationError), shop.connection.cursor() as cursor:
        InvoiceRequestRepository.count_waiting_over(
            cursor, store_id=shop.store_id, principal=stranger.counter, as_of=now, days=3
        )


def test_roles_and_stores(shop: Shop) -> None:
    auditor = _join(shop.connection, shop.store_id, _person(shop.connection, StaffRole.AUDITOR))
    stranger = _person(shop.connection, StaffRole.OPERATOR)
    unverified = _join(
        shop.connection, shop.store_id, _person(shop.connection, StaffRole.OPERATOR, mfa=False)
    )
    order_id = _walk_in_order(shop)
    for principal in (auditor, stranger, unverified):
        with pytest.raises(InvoiceAuthorizationError):
            _create(shop, order_id=order_id, principal=principal)
    stored = _create(shop, order_id=order_id)
    # The auditor reads; a stranger to the store does not.
    assert _read(shop, stored.request_id, auditor).request_id == stored.request_id
    with pytest.raises(InvoiceAuthorizationError):
        _read(shop, stored.request_id, stranger)
    with pytest.raises(InvoiceAuthorizationError):
        InvoiceRequestRepository().export_open(
            shop.connection,
            ExportCommand(shop.store_id, shop.counter, uuid4(), datetime.now(UTC)),
        )
    with pytest.raises(InvoiceNotFoundError):
        _read(shop, uuid4())


# --- erasure ------------------------------------------------------------------------------------


def test_erasure_blanks_what_was_never_issued_and_keeps_what_was(shop: Shop) -> None:
    customer_id = shop.customer()
    shop.open(customer_id, 5_000_000)
    issued_order, cancelled_order, open_order = (shop.ready_order(customer_id) for _ in range(3))
    issued = _create(shop, order_id=issued_order, save_profile=True)
    _issue(shop, issued.request_id, 1, number="4242")
    cancelled = _create(shop, order_id=cancelled_order)
    _cancel(shop, cancelled.request_id, 1, InvoiceCancelReason.OTHER, "khách đổi ý")
    requested = _create(shop, order_id=open_order)
    version = _one(shop.connection, "SELECT row_version FROM customers WHERE id = %s", customer_id)
    CustomerRepository().erase(
        shop.connection,
        store_id=shop.store_id,
        customer_id=customer_id,
        principal=shop.owner,
        expected_row_version=version,
        reason=ErasureReason.CUSTOMER_REQUEST,
        at=datetime.now(UTC),
        correlation_id=uuid4(),
    )
    kept = _read(shop, issued.request_id)
    assert kept.buyer.erased is False and kept.buyer.unit_name == BUYER.unit_name
    for request_id in (cancelled.request_id, requested.request_id):
        blanked = _read(shop, request_id)
        assert blanked.buyer.erased is True
        assert (blanked.buyer.unit_name, blanked.buyer.email, blanked.cancel_note) == (
            None,
            None,
            None,
        )
    assert _read(shop, requested.request_id).status is InvoiceRequestStatus.REQUESTED
    assert _one(
        shop.connection,
        "SELECT buyer_unit_name IS NULL AND erased_at IS NOT NULL "
        "FROM customer_invoice_profiles WHERE customer_id = %s",
        customer_id,
    )
    # The open request can still be cancelled; nothing else about it moved.
    _cancel(shop, requested.request_id, 2)


# --- the bookkeeper's download ------------------------------------------------------------------


def test_the_download_lists_open_requests_line_by_line_and_is_audited(shop: Shop) -> None:
    national, e164 = _mobile()
    customer_id = CustomerRepository().create(
        shop.connection,
        store_id=shop.store_id,
        principal=shop.counter,
        phone=national,
        display_name="Homestay Biển Xanh",
        delivery_address=None,
        note=None,
        kind=CustomerKind.BUSINESS,
        service_consent=True,
        marketing_consent=False,
        at=datetime.now(UTC),
        correlation_id=uuid4(),
    )
    shop.open(customer_id, 5_000_000)
    charged = shop.ready_order(customer_id)
    shop.charge(charged)
    walk_in = _walk_in_order(shop)
    order_request = _create(shop, order_id=walk_in)
    month = statement_month(datetime.now(UTC))
    month_request = _create(
        shop,
        customer_id=customer_id,
        month=month,
        buyer=BuyerInput("Homestay Biển Xanh", None, None, None, None),
    )
    closed = _create(shop, order_id=_walk_in_order(shop))
    _cancel(shop, closed.request_id, 1)

    produced = InvoiceRequestRepository().export_open(
        shop.connection, ExportCommand(shop.store_id, shop.owner, uuid4(), datetime.now(UTC))
    )
    content = produced.content_csv
    assert content.startswith("﻿")
    assert produced.query_version == INVOICE_EXPORT_QUERY.label
    assert produced.query_version.startswith("invoice-requests-export-v2:")
    rows = list(csv.reader(io.StringIO(content.lstrip("﻿"))))
    header_at = rows.index(list(EXPORT_COLUMNS))
    assert rows[0] == ["Phiên bản truy vấn", produced.query_version]
    assert AMOUNT_HEADER_VI in rows[header_at]
    body = rows[header_at + 1 :]
    codes = {row[0] for row in body}
    assert codes == {
        _read(shop, order_request.request_id).request_code,
        _read(shop, month_request.request_id).request_code,
    }
    order_code = _read(shop, order_request.request_id).request_code
    order_rows = [row for row in body if row[0] == order_code]
    # The fixture's service line and its delivery fee, verbatim, and the total on the first row.
    assert [row[11] for row in order_rows] == ["100000", "10000"]
    assert order_rows[0][12] == str(TOTAL_VND) and order_rows[1][12] == ""
    assert order_rows[0][2] == BUYER.unit_name and order_rows[0][3] == BUYER.tax_code
    month_rows = [
        row for row in body if row[0] == _read(shop, month_request.request_id).request_code
    ]
    assert len(month_rows) == 1 and month_rows[0][11] == str(TOTAL_VND)
    assert month_rows[0][7].startswith("Công nợ tháng")
    assert produced.request_count == 2 and produced.row_count == 3 and not produced.truncated
    assert national not in content and e164 not in content and national[1:] not in content
    # Audited: one row, the digest of these exact bytes, and its event.
    recorded = _one(
        shop.connection,
        "SELECT content_hash FROM invoice_request_exports WHERE id = %s",
        produced.export_id,
    )
    assert recorded == produced.content_hash
    assert (
        _one(
            shop.connection,
            "SELECT count(*) FROM audit_events WHERE aggregate_id = %s "
            "AND action = 'INVOICE_REQUEST_EXPORT'",
            produced.export_id,
        )
        == 1
    )
    assert BUYER.unit_name not in _ledger_text(shop.connection, produced.export_id)
