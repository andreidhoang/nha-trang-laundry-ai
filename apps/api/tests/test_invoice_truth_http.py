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

import psycopg
from fastapi.testclient import TestClient
from nha_trang_laundry_domain.accounts import month_label, statement_month
from nha_trang_laundry_domain.catalog import CustodyResolution
from nha_trang_laundry_domain.order_steps import OrderStep

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
    issued = _send(client, "POST", f"{request_path}/issued", ISSUED, if_match=1)
    assert issued.status_code == 200, issued.text

    view = _read_order(connection, order_id, shop.counter)
    _step(
        connection,
        order_id,
        shop.counter,
        view.row_version,
        OrderStep.CANCEL,
        custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
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
