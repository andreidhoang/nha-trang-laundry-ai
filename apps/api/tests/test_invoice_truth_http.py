"""`INVOICE-TRUTH-009` (review M5, M6) over HTTP against real PostgreSQL.

What the console and the bookkeeper read through the served routes:

* an account month's request is for what each order cost -- a deposit taken before the order went
  on the account included, and said (`deposit_vnd`);
* an order's own request first leaves the month to invoice the rest (`own_request_order_count`);
* an issued request answers the figure it was issued for (`amount.fixed_at`), and a refund after it
  is a flag on the request, in the list's *Đã xuất* tab and in the bookkeeper's download --
  never a new figure.
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from nha_trang_laundry_domain.accounts import month_label, statement_month
from nha_trang_laundry_domain.catalog import CustodyResolution
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import PaymentMethod

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_customer_accounts import Shop
from test_invoice_request_routes_postgres import (
    BUYER,
    _as,
    _base,
    _send,
    client,
    connection,
)
from test_invoice_requests import _walk_in_order
from test_invoice_truth import DEPOSIT, _deposit
from test_order_step_repository import TOTAL_VND, _step
from test_order_step_repository import _read as _read_order

__all__ = ["client", "connection"]

ISSUED = {
    "invoice_symbol": "1C26TYY",
    "invoice_number": "0000901",
    "invoice_date": (datetime.now(UTC).date() - timedelta(days=1)).isoformat(),
}


def test_a_month_request_over_http_is_for_what_each_order_cost(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    shop = Shop(connection)
    base = _base(shop)
    homestay = shop.customer()
    shop.open(homestay, 5_000_000)
    with_deposit, own, plain = (shop.ready_order(homestay) for _ in range(3))
    _deposit(shop, with_deposit, DEPOSIT)
    for order_id in (with_deposit, own, plain):
        shop.charge(order_id)
    _as(shop.counter)
    assert _send(client, "POST", f"{base}/orders/{own}/invoice-requests", BUYER).status_code == 201

    month = month_label(statement_month(datetime.now(UTC)))
    path = f"{base}/customers/{homestay}/account/statements/{month}"
    subject = client.get(f"{path}/invoice").json()
    assert subject["refusal"] is None
    assert subject["amount"]["total_vnd"] == 2 * TOTAL_VND
    assert subject["amount"]["deposit_vnd"] == DEPOSIT
    assert subject["amount"]["charge_count"] == 2
    assert subject["amount"]["own_request_order_count"] == 1
    created = _send(
        client, "POST", f"{path}/invoice-requests", {**BUYER, "buyer_unit_name": "Homestay"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["amount"]["total_vnd"] == 2 * TOTAL_VND


def test_an_issued_request_over_http_keeps_its_figure_and_flags_a_refund(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    shop = Shop(connection)
    base = _base(shop)
    order_id = _walk_in_order(shop)
    _deposit(shop, order_id, TOTAL_VND)
    _as(shop.counter)
    request = _send(client, "POST", f"{base}/orders/{order_id}/invoice-requests", BUYER).json()
    request_path = f"{base}/invoice-requests/{request['invoice_request_id']}"
    _as(shop.owner)
    checked = {
        "invoice_total_vnd": request["amount"]["total_vnd"],
        "invoice_order_ids": request["covered_order_ids"],
    }
    issued = _send(client, "POST", f"{request_path}/issued", {**ISSUED, **checked}, if_match=1)
    assert issued.status_code == 200, issued.text

    view = _read_order(connection, order_id, shop.counter)
    _step(
        connection,
        order_id,
        shop.counter,
        view.row_version,
        OrderStep.CANCEL,
        custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        # GOODS-AND-DRAWER-009: the paid order's money goes back, so the cancel says how.
        refund_method=PaymentMethod.TIEN_MAT,
    )

    read = client.get(request_path).json()
    assert read["amount"]["total_vnd"] == TOTAL_VND and read["amount"]["fixed_at"] is not None
    assert read["flags"] == ["REFUNDED_AFTER_ISSUE"]
    tab = client.get(f"{base}/invoice-requests?status=ISSUED").json()
    assert tab["requests"][0]["flags"] == ["REFUNDED_AFTER_ISSUE"]
    subject = client.get(f"{base}/orders/{order_id}/invoice").json()
    assert subject["live"]["flags"] == ["REFUNDED_AFTER_ISSUE"]

    produced = _send(client, "POST", f"{base}/invoice-requests/export")
    assert produced.status_code == 200, produced.text
    export = produced.json()
    assert export["flagged_issued_count"] == 1 and export["request_count"] == 1
    rows = list(csv.reader(io.StringIO(export["content_csv"].lstrip("﻿"))))
    header = next(at for at, cells in enumerate(rows) if "Cần báo kế toán" in cells)
    body = [row for row in rows[header + 1 :] if row[0] == request["request_code"]]
    assert body[0][12] == str(TOTAL_VND)
    assert body[0][13].startswith("1C26TYY · 0000901")
    assert body[0][14] == "Đơn đã hoàn tiền sau khi xuất hóa đơn — báo kế toán."


def test_an_issue_over_http_is_fixed_at_what_the_invoice_lists(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """Review round 9, the verifier's case over HTTP: the month is downloaded (1 order), the
    invoice is made for it, a second order goes on the month, the request is read again (2 orders)
    and *Ghi số hóa đơn* is pressed. The sheet sends what the invoice says -- its total and the
    orders it lists -- so the request is fixed at the 1 order, never at the 2 it reads now."""

    shop = Shop(connection)
    base = _base(shop)
    homestay = shop.customer()
    shop.open(homestay, 5_000_000)
    first = shop.ready_order(homestay)
    shop.charge(first)
    month = month_label(statement_month(datetime.now(UTC)))
    path = f"{base}/customers/{homestay}/account/statements/{month}"
    _as(shop.counter)
    created = _send(
        client, "POST", f"{path}/invoice-requests", {**BUYER, "buyer_unit_name": "Homestay"}
    ).json()
    assert created["covered_order_ids"] == [str(first)]
    assert [line["order_id"] for line in created["lines"]] == [str(first)]
    assert created["lines"][0]["amount_vnd"] == TOTAL_VND
    request_path = f"{base}/invoice-requests/{created['invoice_request_id']}"

    _as(shop.owner)
    exported = _send(client, "POST", f"{base}/invoice-requests/export", {})
    assert exported.status_code == 200, exported.text
    late = shop.ready_order(homestay)
    shop.charge(late)
    read = client.get(request_path).json()
    assert set(read["covered_order_ids"]) == {str(first), str(late)}
    assert read["amount"]["total_vnd"] == 2 * TOTAL_VND

    # The invoice's figure is required.
    missing = _send(client, "POST", f"{request_path}/issued", ISSUED, if_match=1)
    assert missing.status_code == 422, missing.text
    # The invoice's total with both orders ticked: not what both cost.
    both = {"invoice_total_vnd": TOTAL_VND, "invoice_order_ids": read["covered_order_ids"]}
    mismatch = _send(client, "POST", f"{request_path}/issued", {**ISSUED, **both}, if_match=1)
    assert mismatch.status_code == 422, mismatch.text
    assert mismatch.json()["detail"] == {
        "reason_code": "INVOICE_TOTAL_MISMATCH",
        "field": "invoice_total_vnd",
    }
    # An order that is not the month's.
    stranger = {"invoice_total_vnd": TOTAL_VND, "invoice_order_ids": [str(uuid4())]}
    moved = _send(client, "POST", f"{request_path}/issued", {**ISSUED, **stranger}, if_match=1)
    assert moved.status_code == 422, moved.text
    assert moved.json()["detail"]["reason_code"] == "INVOICE_AMOUNT_MOVED"
    still = client.get(request_path).json()
    assert still["status"] == "REQUESTED" and still["row_version"] == 1

    listed = {"invoice_total_vnd": TOTAL_VND, "invoice_order_ids": [str(first)]}
    issued = _send(
        client,
        "POST",
        f"{request_path}/issued",
        {**ISSUED, "invoice_number": "0000902", **listed},
        if_match=1,
    )
    assert issued.status_code == 200, issued.text
    body = issued.json()
    assert body["amount"]["total_vnd"] == TOTAL_VND
    assert body["covered_order_ids"] == [str(first)]
    assert [line["order_id"] for line in body["lines"]] == [str(first)]
    assert body["flags"] == ["MONTH_ORDERS_NOT_ON_INVOICE"]
    assert body["uninvoiced_charge_count"] == 1
    _as(shop.counter)
    own = client.get(f"{base}/orders/{late}/invoice").json()
    assert own["refusal"] is None, own
