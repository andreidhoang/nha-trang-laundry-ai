"""`INVOICE-TRUTH-009` (review M5, M6) against real PostgreSQL: what an invoice request is for.

**M5 -- a month invoice lists what each order cost.** An order that took a deposit before it went on
the account owes the account only the rest (`customer_account_charges.amount_vnd`), but it cost the
customer its whole total (`owed_vnd`). The month's request lists each order at `owed_vnd`; the
account statement keeps reading `amount_vnd`, because that is what the account owes.

**M5 -- no revenue becomes un-invoiceable, in either order of asking.** Every order is covered by
exactly one live request, or may still be requested:

* an order's own request first: the month's request is allowed and leaves that order out;
* the month first: while it is open, its orders are refused on their own; once it is issued, it
  covers exactly the orders its snapshot lists, so an order charged to the month afterwards (or one
  whose own request was cancelled) is requestable on its own, and the month says so.

**M6 -- an issued request keeps its figure.** It is fixed at issue (`0068`) and never moves; a fee
that accrues, a refund or a cancellation afterwards is a flag on the request and a line in the
bookkeeper's download, never a new figure.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import nha_trang_laundry_db.invoice_requests as invoice_requests_module
import psycopg
import pytest
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.invoice_requests import (
    EXPORT_COLUMNS,
    INVOICE_EXPORT_QUERY,
    ExportCommand,
    InvoiceRequestRepository,
    RecordIssuedCommand,
)
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_domain.accounts import ACCOUNT_TIMEZONE, statement_month
from nha_trang_laundry_domain.catalog import CustodyResolution
from nha_trang_laundry_domain.invoice_requests import (
    InvoiceRefusal,
    InvoiceRequestStatus,
    InvoiceRuleError,
)
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import PaymentMethod
from nha_trang_laundry_domain.unclaimed import withdrawal_document
from test_customer_accounts import Shop
from test_invoice_requests import (
    _cancel,
    _create,
    _issue,
    _order_subject,
    _read,
    _walk_in_order,
    connection,
    shop,
)
from test_order_step_repository import TOTAL_VND, _step
from test_order_step_repository import _read as _read_order
from test_unclaimed_laundry import policy_payload

__all__ = ["connection", "shop"]

DEPOSIT = 40_000
#: The download's column that tells the bookkeeper what moved after an invoice was issued.
FLAG_COLUMN = "Cần báo kế toán"


def _deposit(shop: Shop, order_id: UUID, amount: int) -> None:
    PaymentRepository().record(
        shop.connection,
        PaymentCommand(
            order_id=order_id,
            expected_row_version=_read_order(shop.connection, order_id, shop.counter).row_version,
            amount_vnd=amount,
            method=PaymentMethod.TIEN_MAT,
            transfer_seen=False,
            bank_ref_last=None,
            collected_by_customer=False,
            principal=shop.counter,
            correlation_id=uuid4(),
        ),
    )


def _month_subject(shop: Shop, customer_id: UUID, now: datetime | None = None) -> Any:
    with shop.connection.cursor() as cursor:
        return InvoiceRequestRepository().account_month_subject(
            cursor,
            store_id=shop.store_id,
            customer_id=customer_id,
            month=statement_month(datetime.now(UTC)),
            principal=shop.counter,
            now=now or datetime.now(UTC),
        )


def _read_at(shop: Shop, request_id: UUID, now: datetime) -> Any:
    with shop.connection.cursor() as cursor:
        return InvoiceRequestRepository().read(
            cursor, store_id=shop.store_id, request_id=request_id, principal=shop.owner, now=now
        )


def _live_requests(shop: Shop) -> list[Any]:
    found: list[Any] = []
    with shop.connection.cursor() as cursor:
        for status in (InvoiceRequestStatus.REQUESTED, InvoiceRequestStatus.ISSUED):
            listed = InvoiceRequestRepository().list(
                cursor,
                store_id=shop.store_id,
                principal=shop.owner,
                status=status,
                limit=200,
                now=datetime.now(UTC),
            )
            assert not listed.truncated
            found.extend(listed.requests)
    return found


def _assert_every_order_invoiceable_once(shop: Shop, orders: list[UUID]) -> None:
    """The invariant M5 names: covered by exactly one live request, or requestable now."""

    live = _live_requests(shop)
    for order_id in orders:
        covering = [item.request_code for item in live if order_id in item.covered_order_ids]
        assert len(covering) <= 1, f"{order_id} is on two invoices: {covering}"
        if not covering:
            assert _order_subject(shop, order_id).refusal is None, (
                f"{order_id} is on no invoice and cannot be requested"
            )


def _month_request(shop: Shop, customer_id: UUID) -> Any:
    return _create(shop, customer_id=customer_id, month=statement_month(datetime.now(UTC)))


def _export(shop: Shop, at: datetime | None = None) -> list[list[str]]:
    produced = InvoiceRequestRepository().export_open(
        shop.connection,
        ExportCommand(shop.store_id, shop.owner, uuid4(), at or datetime.now(UTC)),
    )
    assert produced.query_version == INVOICE_EXPORT_QUERY.label
    rows = list(csv.reader(io.StringIO(produced.content_csv.lstrip("﻿"))))
    return rows[rows.index(list(EXPORT_COLUMNS)) + 1 :]


@pytest.fixture
def account(shop: Shop) -> UUID:
    customer_id = shop.customer()
    shop.open(customer_id, 5_000_000)
    return customer_id


# --- M5: what a month invoice lists -------------------------------------------------------------


def test_a_month_invoice_lists_each_order_at_what_it_cost_including_a_deposit(
    shop: Shop, account: UUID
) -> None:
    """The review's case: 110k order, 40k deposit, 70k on the account -> the month says 110k."""

    with_deposit = shop.ready_order(account)
    _deposit(shop, with_deposit, DEPOSIT)
    shop.charge(with_deposit)
    plain = shop.ready_order(account)
    shop.charge(plain)

    subject = _month_subject(shop, account)
    assert subject.refusal is None
    assert subject.amount.total_vnd == 2 * TOTAL_VND
    assert subject.amount.source == "ACCOUNT_MONTH_ORDERS"
    assert subject.amount.charge_count == 2
    assert subject.amount.deposit_vnd == DEPOSIT
    assert subject.amount.own_request_order_count == 0
    # The statement is the account's ledger: what went on the account, unchanged.
    statement = shop.account(account).account.current_statement
    assert statement.charges_vnd == 2 * TOTAL_VND - DEPOSIT

    stored = _month_request(shop, account)
    view = _read(shop, stored.request_id)
    assert view.amount.total_vnd == 2 * TOTAL_VND
    body = [row for row in _export(shop) if row[0] == view.request_code]
    assert [row[11] for row in body] == [str(TOTAL_VND), str(TOTAL_VND)]
    assert body[0][12] == str(2 * TOTAL_VND)
    assert "trả trước 40.000đ" in body[0][8]
    assert set(view.covered_order_ids) == {with_deposit, plain}
    _assert_every_order_invoiceable_once(shop, [with_deposit, plain])


def test_an_order_requested_first_leaves_the_month_to_invoice_the_rest(
    shop: Shop, account: UUID
) -> None:
    own, rest = shop.ready_order(account), shop.ready_order(account)
    shop.charge(own)
    shop.charge(rest)
    own_request = _create(shop, order_id=own)

    subject = _month_subject(shop, account)
    assert subject.refusal is None
    assert subject.amount.total_vnd == TOTAL_VND
    assert subject.amount.charge_count == 1 and subject.amount.own_request_order_count == 1
    month = _month_request(shop, account)
    assert _read(shop, month.request_id).covered_order_ids == (rest,)
    _assert_every_order_invoiceable_once(shop, [own, rest])

    # The order's own request issued, then the month's: still once each.
    _issue(shop, own_request.request_id, 1, number="101")
    _issue(shop, month.request_id, 1, number="102")
    issued_month = _read(shop, month.request_id)
    assert issued_month.covered_order_ids == (rest,)
    assert issued_month.amount.total_vnd == TOTAL_VND and issued_month.flags == ()
    _assert_every_order_invoiceable_once(shop, [own, rest])


def test_every_order_with_its_own_request_leaves_nothing_for_the_month(
    shop: Shop, account: UUID
) -> None:
    first, second = shop.ready_order(account), shop.ready_order(account)
    for order_id in (first, second):
        shop.charge(order_id)
        _create(shop, order_id=order_id)
    assert _month_subject(shop, account).refusal is InvoiceRefusal.INVOICE_REQUEST_EXISTS
    with pytest.raises(InvoiceRuleError) as caught:
        _month_request(shop, account)
    assert caught.value.code is InvoiceRefusal.INVOICE_REQUEST_EXISTS


def test_a_cancelled_own_request_puts_the_order_back_on_the_open_month(
    shop: Shop, account: UUID
) -> None:
    own, rest = shop.ready_order(account), shop.ready_order(account)
    shop.charge(own)
    shop.charge(rest)
    own_request = _create(shop, order_id=own)
    month = _month_request(shop, account)
    assert _read(shop, month.request_id).amount.total_vnd == TOTAL_VND
    _cancel(shop, own_request.request_id, 1)
    reopened = _read(shop, month.request_id)
    assert reopened.amount.total_vnd == 2 * TOTAL_VND
    assert set(reopened.covered_order_ids) == {own, rest}
    assert _order_subject(shop, own).refusal is InvoiceRefusal.INVOICE_REQUEST_EXISTS
    _assert_every_order_invoiceable_once(shop, [own, rest])


def test_while_the_month_is_open_its_orders_are_refused_on_their_own(
    shop: Shop, account: UUID
) -> None:
    charged = shop.ready_order(account)
    shop.charge(charged)
    _month_request(shop, account)
    assert _order_subject(shop, charged).refusal is InvoiceRefusal.INVOICE_REQUEST_EXISTS
    with pytest.raises(InvoiceRuleError) as caught:
        _create(shop, order_id=charged)
    assert caught.value.code is InvoiceRefusal.INVOICE_REQUEST_EXISTS


def test_an_order_charged_after_the_month_was_issued_stays_invoiceable(
    shop: Shop, account: UUID
) -> None:
    """The month first, then an order: the review's other direction."""

    early = shop.ready_order(account)
    shop.charge(early)
    month = _month_request(shop, account)
    _issue(shop, month.request_id, 1, number="201")
    late = shop.ready_order(account)
    shop.charge(late)

    # The invoice already issued still covers the early one; the late one is requestable alone.
    assert _order_subject(shop, late).refusal is None
    assert _order_subject(shop, early).refusal is InvoiceRefusal.INVOICE_REQUEST_EXISTS
    issued = _read(shop, month.request_id)
    assert issued.amount.total_vnd == TOTAL_VND
    assert issued.covered_order_ids == (early,)
    assert issued.flags == ("MONTH_ORDERS_NOT_ON_INVOICE",)
    assert issued.uninvoiced_charge_count == 1
    _assert_every_order_invoiceable_once(shop, [early, late])
    _create(shop, order_id=late)
    assert _read(shop, month.request_id).flags == ()
    _assert_every_order_invoiceable_once(shop, [early, late])


def test_an_order_requested_before_it_went_on_the_account_is_not_invoiced_twice(
    shop: Shop, account: UUID
) -> None:
    """Own request while it waited (with a deposit), then charged into a month already open."""

    first = shop.ready_order(account)
    shop.charge(first)
    month = _month_request(shop, account)
    waiting = shop.ready_order(account)
    _deposit(shop, waiting, DEPOSIT)
    _create(shop, order_id=waiting)
    shop.charge(waiting)
    view = _read(shop, month.request_id)
    assert view.amount.total_vnd == TOTAL_VND
    assert view.covered_order_ids == (first,)
    _assert_every_order_invoiceable_once(shop, [first, waiting])
    _issue(shop, month.request_id, 1, number="301")
    assert _read(shop, month.request_id).covered_order_ids == (first,)
    _assert_every_order_invoiceable_once(shop, [first, waiting])


# --- M6: an issued request keeps its figure -----------------------------------------------------


def _paid_received_order(shop: Shop, amount: int = TOTAL_VND) -> UUID:
    order_id = _walk_in_order(shop)
    _deposit(shop, order_id, amount)
    return order_id


def _cancel_order(shop: Shop, order_id: UUID) -> None:
    view = _read_order(shop.connection, order_id, shop.counter)
    _step(
        shop.connection,
        order_id,
        shop.counter,
        view.row_version,
        OrderStep.CANCEL,
        custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
    )


def test_a_refund_after_issue_is_flagged_and_the_figure_stays(shop: Shop) -> None:
    order_id = _paid_received_order(shop)
    stored = _create(shop, order_id=order_id)
    _issue(shop, stored.request_id, 1, number="401")
    fixed = _read(shop, stored.request_id)
    assert fixed.amount.total_vnd == TOTAL_VND and fixed.amount.fixed_at is not None
    assert fixed.flags == ()

    _cancel_order(shop, order_id)
    after = _read(shop, stored.request_id)
    assert after.amount == fixed.amount
    assert after.flags == ("REFUNDED_AFTER_ISSUE",)
    rows = [row for row in _export(shop) if row[0] == after.request_code]
    assert rows, "a flagged issued request is in the bookkeeper's download"
    assert [row[11] for row in rows] == ["100000", "10000"]
    assert rows[0][12] == str(TOTAL_VND)
    flag_at = EXPORT_COLUMNS.index(FLAG_COLUMN)
    assert "Đơn đã hoàn tiền sau khi xuất hóa đơn — báo kế toán" in rows[0][flag_at]


def test_a_cancellation_without_money_after_issue_is_flagged(shop: Shop) -> None:
    order_id = _walk_in_order(shop)
    stored = _create(shop, order_id=order_id)
    _issue(shop, stored.request_id, 1, number="402")
    _cancel_order(shop, order_id)
    after = _read(shop, stored.request_id)
    assert after.amount.total_vnd == TOTAL_VND
    assert after.flags == ("CANCELLED_AFTER_ISSUE",)


def test_an_order_already_refunded_when_issued_is_not_flagged_as_refunded_after(
    shop: Shop,
) -> None:
    order_id = _paid_received_order(shop)
    stored = _create(shop, order_id=order_id)
    _cancel_order(shop, order_id)
    _issue(shop, stored.request_id, 1, number="403")
    assert _read(shop, stored.request_id).flags == ()


@pytest.fixture
def storage_policy(shop: Shop) -> Iterator[None]:
    publish_storage_policy(
        shop.connection, actor_id=shop.owner.staff_user_id, payload=policy_payload()
    )
    try:
        yield
    finally:
        publish_storage_policy(
            shop.connection, actor_id=shop.owner.staff_user_id, payload=withdrawal_document()
        )


def test_a_storage_fee_accruing_after_issue_is_flagged_not_added(
    shop: Shop, account: UUID, storage_policy: None
) -> None:
    order_id = shop.ready_order(account)
    stored = _create(shop, order_id=order_id)
    _issue(shop, stored.request_id, 1, number="404")
    now = datetime.now(UTC)
    later = now + timedelta(days=40)
    # Before issue the read would have moved with the clock; after it the figure is fixed.
    moved = _read_at(shop, stored.request_id, later)
    assert moved.amount.total_vnd == TOTAL_VND and moved.amount.storage_fee_vnd is None
    assert _read_at(shop, stored.request_id, now).flags == ()
    assert moved.flags == ("AMOUNT_CHANGED_AFTER_ISSUE",)
    assert moved.live_total_vnd is not None and moved.live_total_vnd > TOTAL_VND
    rows = [row for row in _export(shop, later) if row[0] == moved.request_code]
    assert [row[11] for row in rows] == ["100000", "10000"]
    flag_at = EXPORT_COLUMNS.index(FLAG_COLUMN)
    assert "khác số trên hóa đơn — báo kế toán" in rows[0][flag_at]


def test_an_unflagged_issued_request_is_not_in_the_download(shop: Shop) -> None:
    stored = _create(shop, order_id=_walk_in_order(shop))
    _issue(shop, stored.request_id, 1, number="405")
    code = _read(shop, stored.request_id).request_code
    assert not [row for row in _export(shop) if row[0] == code]


def test_a_replayed_issue_takes_no_second_snapshot(shop: Shop) -> None:
    stored = _create(shop, order_id=_walk_in_order(shop))
    repository = InvoiceRequestRepository()
    command = RecordIssuedCommand(
        store_id=shop.store_id,
        request_id=stored.request_id,
        expected_row_version=1,
        invoice_symbol="1C26TYY",
        invoice_number="406",
        invoice_date=datetime.now(UTC).date() - timedelta(days=1),
        invoice_total_vnd=TOTAL_VND,
        invoice_order_ids=(_read(shop, stored.request_id).order_id,),
        principal=shop.owner,
        idempotency_key=f"issued-{uuid4().hex}",
        correlation_id=uuid4(),
        at=datetime.now(UTC),
    )
    first = repository.record_issued(shop.connection, command)
    again = repository.record_issued(shop.connection, command)
    assert again.replayed is True and again.row_version == first.row_version
    with shop.connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM invoice_request_snapshots WHERE request_id = %s",
            (stored.request_id,),
        )
        assert cursor.fetchone() == (1,)


# --- the schema holds it too --------------------------------------------------------------------


def test_the_schema_keeps_a_snapshot_whole_and_immutable(shop: Shop, account: UUID) -> None:
    stored = _create(shop, order_id=_walk_in_order(shop))
    issued = _create(shop, order_id=_walk_in_order(shop))
    _issue(shop, issued.request_id, 1, number="407")
    conn = shop.connection

    # Issued without a snapshot: refused at commit.
    missing = pytest.raises(psycopg.errors.RaiseException, match="INVOICE_SNAPSHOT_MISSING")
    with missing, conn.transaction():
        conn.execute(
            """
            UPDATE invoice_requests
            SET status = 'ISSUED', invoice_symbol = 'X1', invoice_number = '9',
                invoice_date = current_date - 1, closed_by = requested_by,
                closed_at = requested_at, row_version = row_version + 1
            WHERE id = %s
            """,
            (stored.request_id,),
        )
    # A snapshot for a request that is not issued: refused at commit.
    with pytest.raises(psycopg.errors.RaiseException), conn.transaction():
        conn.execute(
            """
            INSERT INTO invoice_request_snapshots (
                request_id, store_id, subject_kind, origin, total_vnd, quote_id, quote_revision,
                order_count, taken_at
            )
            SELECT r.id, r.store_id, 'ORDER', 'AT_ISSUE', 1, o.current_quote_id,
                   o.current_quote_revision, 1, now()
            FROM invoice_requests r JOIN orders o ON o.id = r.order_id WHERE r.id = %s
            """,
            (stored.request_id,),
        )
    # Append-only, and nothing added afterwards.
    for statement in (
        "UPDATE invoice_request_snapshots SET total_vnd = 0 WHERE request_id = %s",
        "DELETE FROM invoice_request_snapshots WHERE request_id = %s",
        "UPDATE invoice_request_snapshot_orders SET amount_vnd = 0 WHERE request_id = %s",
        "DELETE FROM invoice_request_snapshot_orders WHERE request_id = %s",
    ):
        with pytest.raises(psycopg.errors.RaiseException), conn.transaction():
            conn.execute(statement, (issued.request_id,))
    other = _walk_in_order(shop)
    with pytest.raises(psycopg.errors.RaiseException), conn.transaction():
        conn.execute(
            """
            INSERT INTO invoice_request_snapshot_orders (
                request_id, position, order_id, amount_vnd, cancelled_at_issue, refunded_at_issue
            ) VALUES (%s, 2, %s, 0, false, false)
            """,
            (issued.request_id, other),
        )
    view = _read(shop, issued.request_id)
    assert view.status is InvoiceRequestStatus.ISSUED and view.amount.total_vnd == TOTAL_VND


def test_the_issue_writes_its_snapshot_in_the_same_transaction(shop: Shop) -> None:
    stored = _create(shop, order_id=_walk_in_order(shop))
    _issue(shop, stored.request_id, 1, number="408")
    with shop.connection.cursor() as cursor:
        cursor.execute(
            "SELECT s.total_vnd, s.origin, s.taken_at = r.closed_at "
            "FROM invoice_request_snapshots s JOIN invoice_requests r ON r.id = s.request_id "
            "WHERE s.request_id = %s",
            (stored.request_id,),
        )
        assert cursor.fetchone() == (TOTAL_VND, "AT_ISSUE", True)


# --- review round 9: an issue is fixed only at what the invoice says ----------------------------


def _checked(view: Any) -> tuple[int | None, tuple[UUID, ...]]:
    """An invoice made for everything the request reads: its figure and the orders it covers."""

    return view.amount.total_vnd, view.covered_order_ids


def _record(
    shop: Shop,
    request_id: UUID,
    *,
    checked: tuple[int | None, tuple[UUID, ...]],
    number: str,
    at: datetime | None = None,
    key: str | None = None,
) -> Any:
    """*Ghi số hóa đơn* with what the invoice says: `checked` = (its total, the orders it lists)."""

    return InvoiceRequestRepository().record_issued(
        shop.connection,
        RecordIssuedCommand(
            store_id=shop.store_id,
            request_id=request_id,
            expected_row_version=1,
            invoice_symbol="1C26TYY",
            invoice_number=number,
            invoice_date=datetime.now(UTC).date() - timedelta(days=1),
            invoice_total_vnd=checked[0],
            invoice_order_ids=checked[1],
            principal=shop.owner,
            idempotency_key=key or f"issued-{uuid4().hex}",
            correlation_id=uuid4(),
            at=at or datetime.now(UTC),
        ),
    )


def test_an_order_charged_between_the_check_and_the_press_is_not_fixed_as_invoiced(
    shop: Shop, account: UUID
) -> None:
    """The invoice was made for A from what the sheet showed; C went on the account; then *Ghi số
    hóa đơn*. C is never fixed as covered by an invoice that did not list it: the request is fixed
    at A, and C is on no invoice and requestable. (Until this fix round the press was refused,
    leaving the invoice already issued for A unrecordable.)"""

    a = shop.ready_order(account)
    shop.charge(a)
    month = _month_request(shop, account)
    checked = _checked(_read(shop, month.request_id))
    assert checked == (TOTAL_VND, (a,))
    c = shop.ready_order(account)
    shop.charge(c)
    _record(shop, month.request_id, checked=checked, number="990")
    fixed = _read(shop, month.request_id)
    assert fixed.status is InvoiceRequestStatus.ISSUED
    assert fixed.covered_order_ids == (a,) and fixed.amount.total_vnd == TOTAL_VND
    assert fixed.flags == ("MONTH_ORDERS_NOT_ON_INVOICE",) and fixed.uninvoiced_charge_count == 1
    assert _order_subject(shop, c).refusal is None
    _assert_every_order_invoiceable_once(shop, [a, c])


def test_an_own_request_cancelled_between_the_check_and_the_press_is_not_fixed_as_invoiced(
    shop: Shop, account: UUID
) -> None:
    own, rest = shop.ready_order(account), shop.ready_order(account)
    shop.charge(own)
    shop.charge(rest)
    own_request = _create(shop, order_id=own)
    month = _month_request(shop, account)
    checked = _checked(_read(shop, month.request_id))
    assert checked == (TOTAL_VND, (rest,))
    _cancel(shop, own_request.request_id, 1)
    # The open month now reads both; the invoice lists `rest`: fixed at `rest` alone.
    assert set(_read(shop, month.request_id).covered_order_ids) == {own, rest}
    _record(shop, month.request_id, checked=checked, number="991")
    fixed = _read(shop, month.request_id)
    assert fixed.covered_order_ids == (rest,) and fixed.amount.total_vnd == TOTAL_VND
    assert fixed.flags == ("MONTH_ORDERS_NOT_ON_INVOICE",)
    assert _order_subject(shop, own).refusal is None
    _assert_every_order_invoiceable_once(shop, [own, rest])


def test_an_order_charged_in_with_its_own_request_does_not_move_the_month(
    shop: Shop, account: UUID
) -> None:
    """A move that is not the month's changes nothing: the figure and the orders are the same."""

    first = shop.ready_order(account)
    shop.charge(first)
    month = _month_request(shop, account)
    checked = _checked(_read(shop, month.request_id))
    waiting = shop.ready_order(account)
    _create(shop, order_id=waiting)
    shop.charge(waiting)
    _record(shop, month.request_id, checked=checked, number="992")
    fixed = _read(shop, month.request_id)
    assert fixed.covered_order_ids == (first,) and fixed.amount.total_vnd == TOTAL_VND
    assert fixed.flags == ()
    _assert_every_order_invoiceable_once(shop, [first, waiting])


def test_a_fee_accrued_between_the_check_and_the_press_is_not_fixed_as_invoiced(
    shop: Shop, account: UUID, storage_policy: None
) -> None:
    order_id = shop.ready_order(account)
    stored = _create(shop, order_id=order_id)
    checked_on = datetime.now(UTC) + timedelta(days=20)
    pressed_on = datetime.now(UTC) + timedelta(days=25)
    checked = _checked(_read_at(shop, stored.request_id, checked_on))
    moved = _read_at(shop, stored.request_id, pressed_on).amount.total_vnd
    assert checked[0] is not None and moved is not None and moved > checked[0]

    with pytest.raises(InvoiceRuleError) as caught:
        _record(shop, stored.request_id, checked=checked, number="993", at=pressed_on)
    assert caught.value.code is InvoiceRefusal.INVOICE_TOTAL_MISMATCH
    _assert_nothing_fixed(shop, stored.request_id, pressed_on)
    # An invoice made for what it is for at the press is fixed at that.
    now_checked = _checked(_read_at(shop, stored.request_id, pressed_on))
    _record(shop, stored.request_id, checked=now_checked, number="993", at=pressed_on)
    fixed = _read_at(shop, stored.request_id, pressed_on)
    assert fixed.amount.total_vnd == moved and fixed.flags == ()


def test_the_checked_figure_is_part_of_the_issue_key(shop: Shop, account: UUID) -> None:
    order_id = shop.ready_order(account)
    stored = _create(shop, order_id=order_id)
    checked = _checked(_read(shop, stored.request_id))
    key = f"issued-{uuid4().hex}"
    first = _record(shop, stored.request_id, checked=checked, number="994", key=key)
    again = _record(shop, stored.request_id, checked=checked, number="994", key=key)
    assert again.replayed is True and again.row_version == first.row_version
    with pytest.raises(IdempotencyConflictError):
        _record(shop, stored.request_id, checked=(1, checked[1]), number="994", key=key)


# --- review round 9: the download reads every issued request, and says it stopped only when it did


@pytest.fixture
def small_bound(monkeypatch: pytest.MonkeyPatch) -> int:
    """The download's bound, made small so a test can pass it."""

    monkeypatch.setattr(invoice_requests_module, "EXPORT_MAX_REQUESTS", 3)
    return 3


def _produce(shop: Shop) -> Any:
    return InvoiceRequestRepository().export_open(
        shop.connection, ExportCommand(shop.store_id, shop.owner, uuid4(), datetime.now(UTC))
    )


def _issued_request(shop: Shop, number: str, *, refunded_after: bool = False) -> Any:
    order_id = _paid_received_order(shop) if refunded_after else _walk_in_order(shop)
    stored = _create(shop, order_id=order_id)
    _issue(shop, stored.request_id, 1, number=number)
    if refunded_after:
        _cancel_order(shop, order_id)
    return _read(shop, stored.request_id)


def test_many_issued_invoices_with_nothing_flagged_are_not_a_cut_short_download(
    shop: Shop, small_bound: int
) -> None:
    for index in range(small_bound + 1):
        _issued_request(shop, f"50{index}")
    produced = _produce(shop)
    assert (produced.request_count, produced.flagged_issued_count) == (0, 0)
    assert produced.truncated is False
    assert "chưa hết danh sách" not in produced.content_csv


def test_a_flagged_invoice_older_than_the_bound_is_still_in_the_download(
    shop: Shop, small_bound: int
) -> None:
    old = _issued_request(shop, "600", refunded_after=True)
    assert old.flags == ("REFUNDED_AFTER_ISSUE",)
    for index in range(small_bound + 1):
        _issued_request(shop, f"61{index}")
    produced = _produce(shop)
    assert produced.flagged_issued_count == 1 and produced.truncated is False
    assert old.request_code in produced.content_csv


def test_the_download_says_it_stopped_when_a_flagged_invoice_was_left_out(
    shop: Shop, small_bound: int
) -> None:
    flagged = [_issued_request(shop, f"70{index}", refunded_after=True) for index in range(4)]
    produced = _produce(shop)
    assert produced.flagged_issued_count == small_bound and produced.truncated is True
    assert "chưa hết danh sách" in produced.content_csv
    # Newest first: the oldest is the one left out, and the file says the list stopped.
    listed = [item.request_code in produced.content_csv for item in flagged]
    assert listed == [False, True, True, True]


def test_the_download_still_stops_at_the_bound_of_open_requests(
    shop: Shop, small_bound: int
) -> None:
    for _ in range(small_bound + 1):
        _create(shop, order_id=_walk_in_order(shop))
    produced = _produce(shop)
    assert produced.request_count == small_bound and produced.truncated is True


# --- review round 9: the month's flag says only what it measured ---------------------------------


def test_an_own_request_cancelled_after_the_month_was_issued_is_not_called_a_later_charge(
    shop: Shop, account: UUID
) -> None:
    """X had its own request when the month was issued (covering Y); X's is then cancelled. X was
    charged before the issue, so "charged after the issue" would be untrue; the flag says only
    that X is on no invoice and no request, and what to do."""

    x, y = shop.ready_order(account), shop.ready_order(account)
    shop.charge(x)
    shop.charge(y)
    own = _create(shop, order_id=x)
    month = _month_request(shop, account)
    _issue(shop, month.request_id, 1, number="801")
    _cancel(shop, own.request_id, 1)
    view = _read(shop, month.request_id)
    assert view.covered_order_ids == (y,)
    assert view.flags == ("MONTH_ORDERS_NOT_ON_INVOICE",) and view.uninvoiced_charge_count == 1
    rows = [row for row in _export(shop) if row[0] == view.request_code]
    said = rows[0][EXPORT_COLUMNS.index(FLAG_COLUMN)]
    assert said == (
        "1 đơn ghi công nợ tháng này không có trên hóa đơn và chưa có yêu cầu nào — lập yêu cầu "
        "riêng cho từng đơn."
    )
    assert "sau khi xuất" not in said
    assert _order_subject(shop, x).refusal is None
    _assert_every_order_invoiceable_once(shop, [x, y])


# --- review round 9 (fix round): the issue is fixed at what the invoice lists --------------------
#
# The verifier's case: the bookkeeper downloads (the month = A), issues the invoice for A in the
# portal; C goes on the account; the owner opens *Ghi số hóa đơn* -- which now reads A and C -- and
# presses. Sending back what the sheet showed fixed the request at A + C, an invoice that never
# listed C. The sheet now sends what the INVOICE says: its total, typed off the invoice, and the
# orders it lists. The request is fixed at exactly those, or nothing is fixed.


def _assert_nothing_fixed(shop: Shop, request_id: UUID, at: datetime | None = None) -> None:
    view = _read_at(shop, request_id, at or datetime.now(UTC))
    assert view.status is InvoiceRequestStatus.REQUESTED and view.row_version == 1
    with shop.connection.cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT count(*) FROM invoice_request_snapshots WHERE request_id = %(r)s),"
            " (SELECT count(*) FROM domain_events WHERE aggregate_id = %(r)s"
            "  AND event_type = 'INVOICE_REQUEST_ISSUED_RECORDED')",
            {"r": request_id},
        )
        assert cursor.fetchone() == (0, 0)


def _refused(
    shop: Shop,
    request_id: UUID,
    *,
    invoice: tuple[int | None, tuple[UUID, ...]],
    number: str,
    at: datetime | None = None,
) -> InvoiceRuleError:
    with pytest.raises(InvoiceRuleError) as caught:
        _record(shop, request_id, checked=invoice, number=number, at=at)
    _assert_nothing_fixed(shop, request_id, at)
    return caught.value


def _fixed_count(shop: Shop, request_id: UUID) -> int:
    with shop.connection.cursor() as cursor:
        cursor.execute(
            "SELECT order_count FROM invoice_request_snapshots WHERE request_id = %s",
            (request_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def test_an_invoice_made_from_the_download_is_fixed_at_what_it_lists_not_the_month_now(
    shop: Shop, account: UUID
) -> None:
    a = shop.ready_order(account)
    shop.charge(a)
    month = _month_request(shop, account)
    code = _read(shop, month.request_id).request_code
    downloaded = [row for row in _export(shop) if row[0] == code]
    assert len(downloaded) == 1 and downloaded[0][12] == str(TOTAL_VND)
    c = shop.ready_order(account)
    shop.charge(c)

    # The sheet opened now reads A and C, both ticked; the owner types the invoice's total.
    opened = _read(shop, month.request_id)
    assert set(opened.covered_order_ids) == {a, c}
    error = _refused(
        shop, month.request_id, invoice=(TOTAL_VND, opened.covered_order_ids), number="995"
    )
    assert error.code is InvoiceRefusal.INVOICE_TOTAL_MISMATCH
    assert error.field == "invoice_total_vnd"

    # Only A ticked -- what the invoice lists -- at its total: fixed at A alone.
    _record(shop, month.request_id, checked=(TOTAL_VND, (a,)), number="995")
    fixed = _read(shop, month.request_id)
    assert fixed.status is InvoiceRequestStatus.ISSUED
    assert fixed.covered_order_ids == (a,) and fixed.amount.total_vnd == TOTAL_VND
    assert _fixed_count(shop, month.request_id) == 1
    # C is on no invoice: the month says so, and C can be requested on its own.
    assert fixed.flags == ("MONTH_ORDERS_NOT_ON_INVOICE",) and fixed.uninvoiced_charge_count == 1
    assert _order_subject(shop, c).refusal is None
    _assert_every_order_invoiceable_once(shop, [a, c])
    _create(shop, order_id=c)
    assert _read(shop, month.request_id).flags == ()
    _assert_every_order_invoiceable_once(shop, [a, c])


def test_an_invoice_listing_every_order_of_the_month_is_fixed_at_all_of_them(
    shop: Shop, account: UUID
) -> None:
    a, c = shop.ready_order(account), shop.ready_order(account)
    shop.charge(a)
    shop.charge(c)
    month = _month_request(shop, account)
    _record(shop, month.request_id, checked=(2 * TOTAL_VND, (c, a)), number="996")
    fixed = _read(shop, month.request_id)
    assert set(fixed.covered_order_ids) == {a, c} and fixed.flags == ()
    assert fixed.amount.total_vnd == 2 * TOTAL_VND and _fixed_count(shop, month.request_id) == 2


@pytest.mark.parametrize(
    ("invoice", "code"),
    [
        # The total of both, but only one ticked.
        (lambda a, c, other: (2 * TOTAL_VND, (a,)), "INVOICE_TOTAL_MISMATCH"),
        # A typing slip on the total.
        (lambda a, c, other: (TOTAL_VND - 1, (a,)), "INVOICE_TOTAL_MISMATCH"),
        (lambda a, c, other: (None, (a,)), "INVOICE_TOTAL_MISMATCH"),
        # An order the month does not cover now (its own request), an unknown one, none, twice.
        (lambda a, c, other: (2 * TOTAL_VND, (a, other)), "INVOICE_AMOUNT_MOVED"),
        (lambda a, c, other: (TOTAL_VND, (uuid4(),)), "INVOICE_AMOUNT_MOVED"),
        (lambda a, c, other: (0, ()), "INVOICE_AMOUNT_MOVED"),
        (lambda a, c, other: (2 * TOTAL_VND, (a, a)), "INVOICE_AMOUNT_MOVED"),
    ],
)
def test_a_month_is_fixed_only_at_orders_it_covers_and_their_exact_total(
    shop: Shop, account: UUID, invoice: Any, code: str
) -> None:
    a, c, other = (shop.ready_order(account) for _ in range(3))
    for order_id in (a, c, other):
        shop.charge(order_id)
    _create(shop, order_id=other)
    month = _month_request(shop, account)
    assert set(_read(shop, month.request_id).covered_order_ids) == {a, c}
    error = _refused(shop, month.request_id, invoice=invoice(a, c, other), number="997")
    assert error.code == code
    _assert_every_order_invoiceable_once(shop, [a, c, other])


def test_an_order_invoice_is_fixed_only_at_the_orders_total_now(
    shop: Shop, account: UUID, storage_policy: None
) -> None:
    """An order is one line: its invoice's total is the order's total at the press, or nothing is
    fixed (a fee accrued since the download -- DECISIONS NEEDED: record the invoice's figure)."""

    order_id = shop.ready_order(account)
    stored = _create(shop, order_id=order_id)
    downloaded_on = datetime.now(UTC) + timedelta(days=20)
    pressed_on = datetime.now(UTC) + timedelta(days=25)
    on_invoice = _read_at(shop, stored.request_id, downloaded_on).amount.total_vnd
    now = _read_at(shop, stored.request_id, pressed_on).amount.total_vnd
    assert on_invoice is not None and now is not None and now > on_invoice
    error = _refused(
        shop, stored.request_id, invoice=(on_invoice, (order_id,)), number="998", at=pressed_on
    )
    assert error.code is InvoiceRefusal.INVOICE_TOTAL_MISMATCH
    stranger = _refused(
        shop, stored.request_id, invoice=(now, (uuid4(),)), number="998", at=pressed_on
    )
    assert stranger.code is InvoiceRefusal.INVOICE_AMOUNT_MOVED
    _record(shop, stored.request_id, checked=(now, (order_id,)), number="998", at=pressed_on)
    assert _read_at(shop, stored.request_id, pressed_on).amount.total_vnd == now


# --- review round 9 (fix round): the download reads only what can still move --------------------


def _settled_issued_request(shop: Shop, number: str) -> Any:
    order_id = _paid_received_order(shop)
    stored = _create(shop, order_id=order_id)
    _issue(shop, stored.request_id, 1, number=number)
    return _read(shop, stored.request_id)


def _count_views(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    seen: list[UUID] = []
    real = invoice_requests_module._view

    def counting(cursor: Any, row: tuple[Any, ...], now: datetime) -> Any:
        seen.append(row[0])
        return real(cursor, row, now)

    monkeypatch.setattr(invoice_requests_module, "_view", counting)
    return seen


def test_the_download_does_not_reread_every_invoice_ever_issued(
    shop: Shop, account: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settled orders' invoices and a whole month with nothing new cannot move: the download does
    not read them one by one (the verifier's N+1). The one that moved is read and listed."""

    quiet = [_settled_issued_request(shop, f"85{index}") for index in range(6)]
    charged = shop.ready_order(account)
    shop.charge(charged)
    month = _month_request(shop, account)
    _issue(shop, month.request_id, 1, number="860")
    moved = _issued_request(shop, "861", refunded_after=True)
    assert all(item.flags == () for item in quiet) and moved.flags == ("REFUNDED_AFTER_ISSUE",)

    seen = _count_views(monkeypatch)
    produced = _produce(shop)
    assert produced.flagged_issued_count == 1 and moved.request_code in produced.content_csv
    assert seen == [moved.request_id]


def _scan(shop: Shop) -> set[UUID]:
    with shop.connection.cursor() as cursor:
        cursor.execute(
            invoice_requests_module._ISSUED_SCAN_SQL,
            {"store": shop.store_id, "zone": ACCOUNT_TIMEZONE},
        )
        return {row[0] for row in cursor.fetchall()}


def _every_issued(shop: Shop, at: datetime) -> list[Any]:
    with shop.connection.cursor() as cursor:
        listed = InvoiceRequestRepository().list(
            cursor,
            store_id=shop.store_id,
            principal=shop.owner,
            status=InvoiceRequestStatus.ISSUED,
            limit=200,
            now=at,
        )
    assert not listed.truncated
    return list(listed.requests)


def test_the_download_scan_keeps_every_issued_request_a_read_flags(
    shop: Shop, account: UUID, storage_policy: None
) -> None:
    """The scan is a superset of what `_view` flags, for every way an issued request is flagged,
    and leaves out the ones that cannot move: the matrix behind the N+1 fix."""

    quiet = _settled_issued_request(shop, "870")
    refunded = _issued_request(shop, "871", refunded_after=True)
    cancelled_order = _walk_in_order(shop)
    cancelled = _create(shop, order_id=cancelled_order)
    _issue(shop, cancelled.request_id, 1, number="872")
    _cancel_order(shop, cancelled_order)
    # A storage fee accruing on an order not settled (flagged 40 days on).
    waiting = shop.ready_order(account)
    accruing = _create(shop, order_id=waiting)
    _issue(shop, accruing.request_id, 1, number="873")
    # Months: one with nothing new, one with an order charged after, one whose own request was
    # cancelled after, one with a deposit order (no flag: the line is its whole cost).
    other = shop.customer()
    shop.open(other, 5_000_000)
    third = shop.customer()
    shop.open(third, 5_000_000)
    fourth = shop.customer()
    shop.open(fourth, 5_000_000)
    still = shop.ready_order(other)
    _deposit(shop, still, DEPOSIT)
    shop.charge(still)
    still_month = _month_request(shop, other)
    _issue(shop, still_month.request_id, 1, number="874")
    early = shop.ready_order(third)
    shop.charge(early)
    later_month = _month_request(shop, third)
    _issue(shop, later_month.request_id, 1, number="875")
    late = shop.ready_order(third)
    shop.charge(late)
    x, y = shop.ready_order(fourth), shop.ready_order(fourth)
    shop.charge(x)
    shop.charge(y)
    own = _create(shop, order_id=x)
    reopened_month = _month_request(shop, fourth)
    _issue(shop, reopened_month.request_id, 1, number="876")
    _cancel(shop, own.request_id, 1)

    for at in (datetime.now(UTC), datetime.now(UTC) + timedelta(days=40)):
        issued = _every_issued(shop, at)
        flagged = {item.request_id for item in issued if item.flags}
        scanned = _scan(shop)
        assert flagged <= scanned, f"the scan misses {flagged - scanned}"
    assert flagged == {
        refunded.request_id,
        cancelled.request_id,
        accruing.request_id,
        later_month.request_id,
        reopened_month.request_id,
    }
    # Cannot move: a settled order's invoice, a month with nothing new.
    assert quiet.request_id not in scanned and still_month.request_id not in scanned
    # Not settled, so read (its fee moves with the clock), though not flagged today.
    assert accruing.request_id in scanned
