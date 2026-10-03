"""`MONEY-RESIDUAL-009B` J6 against real PostgreSQL: invoices that are already issued.

* **(a)** An invoice the bookkeeper already issued at the provider for a figure that has since
  moved (a storage fee accrued between the download and *Ghi số hóa đơn*) is recorded AT THE
  INVOICE'S PRINTED FIGURE when the owner confirms it -- flagged "Số trên hóa đơn khác số hiện tại
  — báo kế toán" on the request and in the bookkeeper's download -- never refused into a dead end.
  Unconfirmed, the press is still refused `INVOICE_TOTAL_MISMATCH` first: a typing slip is the
  common cause, and nothing is fixed.
* **(b)** is proven against a database migrated from before `0068`
  (`test_migration_0071_populated.py`).
* **(c)** The download states when it hit its row cap: `test_invoice_truth.py`
  (`test_the_download_says_it_stopped_when_a_flagged_invoice_was_left_out`,
  `test_the_download_still_stops_at_the_bound_of_open_requests`) -- verified in round 9b, unchanged.
* **(d)** A request whose quote presents no single total cannot be ISSUED
  (`INVOICE_TOTAL_UNKNOWN`); it can be cancelled, or wait.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import nha_trang_laundry_db.invoice_requests as invoice_requests_module
import pytest
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.invoice_requests import (
    EXPORT_COLUMNS,
    InvoiceAmount,
    InvoiceRequestRepository,
    RecordIssuedCommand,
)
from nha_trang_laundry_domain.accounts import statement_month
from nha_trang_laundry_domain.invoice_requests import (
    InvoiceRefusal,
    InvoiceRequestStatus,
    InvoiceRuleError,
)
from test_customer_accounts import Shop
from test_invoice_requests import _cancel, _create, _read, _walk_in_order, connection, shop
from test_invoice_truth import (
    FLAG_COLUMN,
    _assert_nothing_fixed,
    _export,
    _read_at,
    account,
    storage_policy,
)
from test_order_step_repository import TOTAL_VND

__all__ = ["account", "connection", "shop", "storage_policy"]


def _press(
    shop: Shop,
    request_id: UUID,
    *,
    total: int | None,
    orders: tuple[UUID, ...],
    printed: bool,
    at: datetime,
    key: str | None = None,
    number: str = "771",
) -> Any:
    return InvoiceRequestRepository().record_issued(
        shop.connection,
        RecordIssuedCommand(
            store_id=shop.store_id,
            request_id=request_id,
            expected_row_version=1,
            invoice_symbol="1C26TYY",
            invoice_number=number,
            invoice_date=datetime.now(UTC).date(),
            invoice_total_vnd=total,
            invoice_order_ids=orders,
            principal=shop.owner,
            idempotency_key=key or f"issued-{uuid4().hex}",
            correlation_id=uuid4(),
            at=at,
            record_printed_total=printed,
        ),
    )


def _snapshot_row(shop: Shop, request_id: UUID) -> tuple[Any, ...]:
    with shop.connection.cursor() as cursor:
        cursor.execute(
            "SELECT total_vnd, printed_total_vnd, order_count FROM invoice_request_snapshots "
            "WHERE request_id = %s",
            (request_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return tuple(row)


# --- (a) the printed figure ------------------------------------------------------------------


def test_an_order_invoice_printed_before_a_fee_accrued_is_recorded_at_its_printed_figure(
    shop: Shop, account: UUID, storage_policy: None
) -> None:
    order_id = shop.ready_order(account)
    stored = _create(shop, order_id=order_id)
    downloaded_on = datetime.now(UTC) + timedelta(days=20)
    pressed_on = datetime.now(UTC) + timedelta(days=25)
    printed = _read_at(shop, stored.request_id, downloaded_on).amount.total_vnd
    now = _read_at(shop, stored.request_id, pressed_on).amount.total_vnd
    assert printed == TOTAL_VND and now is not None and now > printed

    # Unconfirmed: refused first, nothing fixed -- the round-9 rule, kept for typing slips.
    with pytest.raises(InvoiceRuleError) as refused:
        _press(
            shop, stored.request_id, total=printed, orders=(order_id,), printed=False, at=pressed_on
        )
    assert refused.value.code is InvoiceRefusal.INVOICE_TOTAL_MISMATCH
    _assert_nothing_fixed(shop, stored.request_id, pressed_on)

    # Confirmed "this is what the invoice prints": recorded at it, flagged, never a dead end.
    _press(shop, stored.request_id, total=printed, orders=(order_id,), printed=True, at=pressed_on)
    view = _read_at(shop, stored.request_id, pressed_on)
    assert view.status is InvoiceRequestStatus.ISSUED
    assert view.amount.total_vnd == printed
    assert view.shop_total_vnd == now
    assert view.flags == ("PRINTED_TOTAL_DIFFERS",)
    assert _snapshot_row(shop, stored.request_id) == (now, printed, 1)
    with shop.connection.cursor() as cursor:
        cursor.execute(
            "SELECT e.payload ->> 'printed_total_vnd', a.details ->> 'printed_total_vnd' "
            "FROM domain_events e JOIN audit_events a ON a.aggregate_id = e.aggregate_id "
            "AND a.correlation_id = e.correlation_id "
            "WHERE e.aggregate_id = %s AND e.event_type = 'INVOICE_REQUEST_ISSUED_RECORDED'",
            (stored.request_id,),
        )
        assert cursor.fetchone() == (str(printed), str(printed))
    # The bookkeeper's download lists it at the printed figure, with the sentence.
    rows = [row for row in _export(shop, pressed_on) if row[0] == view.request_code]
    assert rows and rows[0][12] == str(printed)
    flag = rows[0][EXPORT_COLUMNS.index(FLAG_COLUMN)]
    assert flag.startswith("Số trên hóa đơn khác số hiện tại (")
    assert "báo kế toán" in flag


def test_a_month_invoice_printed_at_another_figure_is_recorded_at_it(
    shop: Shop, account: UUID
) -> None:
    a, b = shop.ready_order(account), shop.ready_order(account)
    shop.charge(a)
    shop.charge(b)
    month = _create(shop, customer_id=account, month=statement_month(datetime.now(UTC)))
    at = datetime.now(UTC)
    printed = 2 * TOTAL_VND - 15_000
    with pytest.raises(InvoiceRuleError):
        _press(shop, month.request_id, total=printed, orders=(a, b), printed=False, at=at)
    _press(shop, month.request_id, total=printed, orders=(b, a), printed=True, at=at)
    view = _read(shop, month.request_id)
    assert view.amount.total_vnd == printed and view.shop_total_vnd == 2 * TOTAL_VND
    assert set(view.covered_order_ids) == {a, b}
    assert view.flags == ("PRINTED_TOTAL_DIFFERS",)
    # The lines are still what each order cost: `0068`'s whole-snapshot check holds.
    assert _snapshot_row(shop, month.request_id) == (2 * TOTAL_VND, printed, 2)


def test_a_confirmed_figure_that_agrees_records_none_and_the_confirmation_is_in_the_key(
    shop: Shop,
) -> None:
    order_id = _walk_in_order(shop)
    stored = _create(shop, order_id=order_id)
    at = datetime.now(UTC)
    key = f"issued-{uuid4().hex}"
    first = _press(
        shop, stored.request_id, total=TOTAL_VND, orders=(order_id,), printed=True, at=at, key=key
    )
    again = _press(
        shop, stored.request_id, total=TOTAL_VND, orders=(order_id,), printed=True, at=at, key=key
    )
    assert (first.replayed, again.replayed) == (False, True)
    assert _snapshot_row(shop, stored.request_id) == (TOTAL_VND, None, 1)
    assert _read(shop, stored.request_id).flags == ()
    with pytest.raises(IdempotencyConflictError):
        _press(
            shop,
            stored.request_id,
            total=TOTAL_VND,
            orders=(order_id,),
            printed=False,
            at=at,
            key=key,
        )


def test_the_schema_keeps_a_printed_figure_only_beside_a_different_total(shop: Shop) -> None:
    import psycopg

    order_id = _walk_in_order(shop)
    stored = _create(shop, order_id=order_id)
    _press(
        shop,
        stored.request_id,
        total=TOTAL_VND,
        orders=(order_id,),
        printed=False,
        at=datetime.now(UTC),
    )
    with shop.connection.cursor() as cursor:
        for printed in (TOTAL_VND, -1):
            with pytest.raises(psycopg.errors.CheckViolation), shop.connection.transaction():
                cursor.execute(
                    """
                    INSERT INTO invoice_request_snapshots (
                        request_id, store_id, subject_kind, origin, total_vnd, quote_id,
                        quote_revision, order_count, taken_at, printed_total_vnd
                    )
                    SELECT %s, store_id, subject_kind, origin, total_vnd, quote_id,
                           quote_revision, order_count, taken_at, %s
                    FROM invoice_request_snapshots WHERE request_id = %s
                    """,
                    (uuid4(), printed, stored.request_id),
                )


# --- (d) no single total ------------------------------------------------------------------------


def test_a_request_with_no_single_total_cannot_be_issued_and_can_be_cancelled(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Harness: an order whose quote presents a range is unreachable through today's acceptance
    (a range is refused `RANGE_PRICE_REQUIRES_HUMAN` until it is closed) and exists only in older
    data, so the order's amount is read as having no single total -- `_order_amount`'s answer for
    such a quote -- and the rule is held against it."""

    order_id = _walk_in_order(shop)
    stored = _create(shop, order_id=order_id)

    def no_single_total(cursor: Any, order: UUID, now: datetime) -> InvoiceAmount:
        return InvoiceAmount("ORDER_CHARGES", None, None, None, None)

    monkeypatch.setattr(invoice_requests_module, "_order_amount", no_single_total)
    at = datetime.now(UTC)
    for total, printed in ((None, False), (TOTAL_VND, False), (TOTAL_VND, True)):
        with pytest.raises(InvoiceRuleError) as refused:
            _press(shop, stored.request_id, total=total, orders=(order_id,), printed=printed, at=at)
        assert refused.value.code is InvoiceRefusal.INVOICE_TOTAL_UNKNOWN
        assert refused.value.field is None
        _assert_nothing_fixed(shop, stored.request_id, at)
    cancelled = _cancel(shop, stored.request_id, 1)
    assert cancelled.row_version == 2
    assert _read(shop, stored.request_id).status is InvoiceRequestStatus.CANCELLED
