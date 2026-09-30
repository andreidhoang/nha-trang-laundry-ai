"""`EINVOICE-REQUEST-001` (`DEC-040`) over HTTP against real PostgreSQL: invoice requests.

The served routes end to end, the way a shop uses them: the order page reads its *Hóa đơn* row; the
counter records *Khách cần hóa đơn* on an order and on an account month (the buyer saved for next
time); the list's tabs count them; the owner downloads the open list for the bookkeeper and records
the invoice's symbol, number and date; the counter cancels another. And the refusals each route
owes: roles, no `If-Match`, a stale one, a replayed key, a reused key with a changed body, a
malformed body answered without the buyer's words, another store, a bad month.
"""

from __future__ import annotations

import csv
import io
import os
import sys
from collections.abc import Generator, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.invoice_requests import InvoiceRequestService
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_invoice_request_service,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.accounts import month_label, statement_month

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_customer_accounts import Shop
from test_customer_records import _join, _person
from test_invoice_requests import _walk_in_order
from test_order_step_repository import TOTAL_VND

CSRF = "a" * 40
ORIGIN = "http://testserver"
DENIED = {"detail": "operation denied"}

BUYER = {
    "buyer_unit_name": "Công ty TNHH Biển Xanh",
    "buyer_tax_code": "4201234567",
    "buyer_address": "12 Trần Phú, Nha Trang",
    "buyer_email": "ketoan@bienxanh.vn",
    "buyer_name": "Nguyễn Văn An",
}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = AuthSettings(database_url=_database_url())
    app.dependency_overrides[get_invoice_request_service] = lambda: InvoiceRequestService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _send(
    client: TestClient,
    method: str,
    path: str,
    body: Mapping[str, object] | None = None,
    *,
    key: str | None = None,
    if_match: int | None = None,
) -> Any:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": key or uuid4().hex}
    if if_match is not None:
        headers["If-Match"] = f'"{if_match}"'
    return client.request(method, path, headers=headers, json=body)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return str(detail.get("reason_code")) if isinstance(detail, dict) else str(detail)


def _base(shop: Shop) -> str:
    return f"/internal/v1/stores/{shop.store_id}"


def test_invoice_requests_from_the_counter_to_the_bookkeeper_over_http(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    shop = Shop(connection)
    base = _base(shop)
    walk_in = _walk_in_order(shop)
    homestay = shop.customer()
    shop.open(homestay, 5_000_000)
    charged = shop.ready_order(homestay)
    shop.charge(charged)
    month = month_label(statement_month(datetime.now(UTC)))

    # The order page's row: nothing yet, and a request may be made.
    _as(shop.counter)
    row = client.get(f"{base}/orders/{walk_in}/invoice")
    assert row.status_code == 200, row.text
    subject = row.json()
    assert subject["refusal"] is None and subject["live"] is None
    assert subject["amount"]["total_vnd"] == TOTAL_VND
    assert subject["decision"] == "DEC-040" and subject["privacy_notice_published"] is True

    # Khách cần hóa đơn on the walk-in order; a replay answers the first result.
    key = f"inv-{uuid4().hex}"
    created = _send(client, "POST", f"{base}/orders/{walk_in}/invoice-requests", BUYER, key=key)
    assert created.status_code == 201, created.text
    request = created.json()
    assert request["status"] == "REQUESTED" and request["request_code"].startswith("YC-")
    assert request["buyer"]["tax_code"] == "4201234567"
    assert request["amount"] == {
        "source": "ORDER_CHARGES",
        "total_vnd": TOTAL_VND,
        "storage_fee_vnd": None,
        "charge_count": None,
        "month_ended": None,
        "fixed_at": None,
        "deposit_vnd": None,
        "own_request_order_count": None,
    }
    assert request["flags"] == [] and request["live_total_vnd"] is None
    replay = _send(client, "POST", f"{base}/orders/{walk_in}/invoice-requests", BUYER, key=key)
    assert replay.status_code == 201 and replay.json()["replayed"] is True
    assert replay.json()["invoice_request_id"] == request["invoice_request_id"]
    conflict = _send(
        client,
        "POST",
        f"{base}/orders/{walk_in}/invoice-requests",
        {**BUYER, "buyer_name": "Người khác"},
        key=key,
    )
    assert conflict.status_code == 409 and _code(conflict) == "IDEMPOTENCY_CONFLICT"
    again = _send(client, "POST", f"{base}/orders/{walk_in}/invoice-requests", BUYER)
    assert again.status_code == 422 and _code(again) == "INVOICE_REQUEST_EXISTS"

    # The account month, with the buyer saved for next time.
    month_path = f"{base}/customers/{homestay}/account/statements/{month}"
    month_row = client.get(f"{month_path}/invoice").json()
    assert month_row["refusal"] is None and month_row["profile_savable"] is True
    assert month_row["amount"]["total_vnd"] == TOTAL_VND
    assert month_row["prefill"]["unit_name"] == "Homestay Biển Xanh"
    saved = _send(
        client,
        "POST",
        f"{month_path}/invoice-requests",
        {**BUYER, "buyer_unit_name": "Homestay Biển Xanh", "save_profile": True},
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["period_month"] == month
    # INVOICE-TRUTH-009: a month is the orders it covers at what each cost (review M5).
    assert saved.json()["amount"]["source"] == "ACCOUNT_MONTH_ORDERS"
    assert saved.json()["amount"]["deposit_vnd"] == 0
    covered = client.get(f"{base}/orders/{charged}/invoice").json()
    assert covered["refusal"] == "INVOICE_REQUEST_EXISTS"
    assert covered["prefill_from_profile"] is True
    assert covered["prefill"]["unit_name"] == "Homestay Biển Xanh"

    # A third, to cancel.
    other = _walk_in_order(shop)
    third = _send(
        client,
        "POST",
        f"{base}/orders/{other}/invoice-requests",
        {"buyer_unit_name": "Spa Ngọc Lan"},
    ).json()

    # The list: open ones oldest first, every tab counted.
    listed = client.get(f"{base}/invoice-requests?status=REQUESTED&limit=2")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["counts"] == {"REQUESTED": 3, "ISSUED": 0, "CANCELLED": 0}
    assert body["total_count"] == 3 and body["truncated"] is True
    assert body["requests"][0]["invoice_request_id"] == request["invoice_request_id"]
    assert body["query_version"].startswith("invoice-requests-v2:")

    # Only the owner or the approver downloads, and records what the bookkeeper issued.
    assert _send(client, "POST", f"{base}/invoice-requests/export").json() == DENIED
    _as(shop.owner)
    produced = _send(client, "POST", f"{base}/invoice-requests/export")
    assert produced.status_code == 200, produced.text
    export = produced.json()
    assert export["content_csv"].startswith("﻿")
    assert export["query_version"].startswith("invoice-requests-export-v2:")
    assert export["flagged_issued_count"] == 0
    assert export["request_count"] == 3 and export["filename"].endswith(".csv")
    rows = list(csv.reader(io.StringIO(export["content_csv"].lstrip("﻿"))))
    assert any("Số tiền theo giá tiệm đã thu (chưa tách thuế)" in line for line in rows)

    issued_path = f"{base}/invoice-requests/{request['invoice_request_id']}/issued"
    issued_body = {
        "invoice_symbol": "1C26TYY",
        "invoice_number": "0000123",
        "invoice_date": (datetime.now(UTC).date() - timedelta(days=1)).isoformat(),
    }
    assert _send(client, "POST", issued_path, issued_body).status_code == 428
    stale = _send(client, "POST", issued_path, issued_body, if_match=9)
    assert stale.status_code == 409 and _code(stale).startswith("STALE_VERSION")
    wrong = _send(client, "POST", issued_path, {**issued_body, "invoice_number": "12a"}, if_match=1)
    assert wrong.status_code == 422 and _code(wrong) == "INVOICE_ISSUED_DETAILS_INVALID"
    assert wrong.json()["detail"]["field"] == "invoice_number"
    issued = _send(client, "POST", issued_path, issued_body, if_match=1)
    assert issued.status_code == 200, issued.text
    assert issued.json()["status"] == "ISSUED" and issued.json()["invoice_number"] == "0000123"
    assert issued.json()["amount"]["total_vnd"] == TOTAL_VND
    assert issued.json()["amount"]["fixed_at"] is not None and issued.json()["flags"] == []
    closed = _send(client, "POST", issued_path, issued_body, if_match=2)
    assert closed.status_code == 422 and _code(closed) == "INVOICE_REQUEST_CLOSED"

    # The counter cancels the third; the counter cannot record an issued invoice.
    _as(shop.counter)
    assert (
        _send(
            client,
            "POST",
            f"{base}/invoice-requests/{third['invoice_request_id']}/issued",
            issued_body,
            if_match=1,
        ).json()
        == DENIED
    )
    cancel_path = f"{base}/invoice-requests/{third['invoice_request_id']}/cancellation"
    note_needed = _send(client, "POST", cancel_path, {"reason": "OTHER"}, if_match=1)
    assert note_needed.status_code == 422 and _code(note_needed) == "INVOICE_CANCEL_NOTE_REQUIRED"
    cancelled = _send(client, "POST", cancel_path, {"reason": "DUPLICATE"}, if_match=1)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    tabs = client.get(f"{base}/invoice-requests?status=CANCELLED").json()
    assert tabs["counts"] == {"REQUESTED": 1, "ISSUED": 1, "CANCELLED": 1}
    assert [item["invoice_request_id"] for item in tabs["requests"]] == [
        third["invoice_request_id"]
    ]
    one = client.get(f"{base}/invoice-requests/{third['invoice_request_id']}")
    assert one.status_code == 200 and one.json()["cancel_reason"] == "DUPLICATE"


def test_refusals_say_the_field_and_never_the_words(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    shop = Shop(connection)
    base = _base(shop)
    order_id = _walk_in_order(shop)
    path = f"{base}/orders/{order_id}/invoice-requests"
    _as(shop.counter)
    refused = _send(client, "POST", path, {**BUYER, "buyer_address": None})
    assert refused.status_code == 422
    # A typing slip names its field and no owner decision.
    assert refused.json()["detail"] == {
        "reason_code": "INVOICE_ADDRESS_REQUIRED",
        "field": "buyer_address",
    }
    phone = _send(client, "POST", path, {"buyer_unit_name": "Chị Lan 0905 123 456"})
    assert phone.status_code == 422 and _code(phone) == "INVOICE_FIELD_LOOKS_LIKE_PHONE"
    assert "0905" not in phone.text
    # A malformed body on these paths is answered without the values it held.
    malformed = _send(client, "POST", path, {**BUYER, "buyer_unit_name": 7, "extra": "x"})
    assert malformed.status_code == 422
    assert "Biển Xanh" not in malformed.text and "ketoan@" not in malformed.text

    # Roles and stores.
    auditor = _join(connection, shop.store_id, _person(connection, StaffRole.AUDITOR))
    stranger = _person(connection, StaffRole.OPERATOR)
    _as(auditor)
    assert _send(client, "POST", path, BUYER).json() == DENIED
    assert client.get(f"{base}/invoice-requests").status_code == 200
    _as(stranger)
    assert client.get(f"{base}/invoice-requests").json() == DENIED
    assert client.get(f"{base}/orders/{order_id}/invoice").json() == DENIED
    _as(shop.counter)
    other = Shop(connection)
    assert client.get(f"{base}/orders/{_walk_in_order(other)}/invoice").status_code == 404
    assert client.get(f"{base}/invoice-requests/{uuid4()}").status_code == 404
    homestay = shop.customer()
    shop.open(homestay, 1_000_000)
    bad_month = client.get(f"{base}/customers/{homestay}/account/statements/2026-13/invoice")
    assert bad_month.status_code == 422 and _code(bad_month) == "MONTH_INVALID"
    nothing = client.get(
        f"{base}/customers/{homestay}/account/statements/"
        f"{month_label(statement_month(datetime.now(UTC)))}/invoice"
    ).json()
    assert nothing["refusal"] == "INVOICE_SUBJECT_UNAVAILABLE"
    missing = client.get(f"{base}/customers/{UUID(int=5)}/account/statements/2026-09/invoice")
    assert missing.status_code == 404
