"""`VIETQR-001` (`DEC-041`) over HTTP against real PostgreSQL.

The two QR routes and the order search by transfer code, at wall time: refused
`BANK_ACCOUNT_UNPUBLISHED` before the owner publishes the account; afterwards the order's QR asks
for exactly what the payment ledger says remains -- after a part payment taken through the served
payment route too -- and says `NOTHING_OWED` once paid; the matrix is rows of 0/1 with the quiet
zone and no markup; an account month's QR asks for the statement's unpaid figure; and the order
search resolves a transfer code within the caller's store only.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator, Mapping
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
import segno
from fastapi.testclient import TestClient
from nha_trang_laundry_api.accounts import AccountService
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_account_service,
    get_operations_service,
    get_vietqr_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_api.vietqr import VietQrService, qr_modules
from nha_trang_laundry_db.bank_transfer import publish_bank_account
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.vietqr import (
    bank_account_document,
    bank_account_withdrawal_document,
    parse_payload,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_customer_accounts import Shop
from test_customer_records import _join, _person, _store
from test_order_step_repository import TOTAL_VND

CSRF = "q" * 40
ORIGIN = "http://testserver"
HCM = ZoneInfo("Asia/Ho_Chi_Minh")
ACCOUNT = bank_account_document(
    bank_bin="970416",
    account_number="257678859",
    account_name="TIEM GIAT NHA TRANG",
    bank_display_name="ACB",
    test_transfer_confirmed_at="2026-09-28T09:15:00+07:00",
)


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
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    app.dependency_overrides[get_account_service] = lambda: AccountService(settings)
    app.dependency_overrides[get_vietqr_service] = lambda: VietQrService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Iterator[Shop]:
    """A store with no bank account in force before or after: the account is one per deployment."""

    made = Shop(connection)
    _withdraw(connection, made)
    yield made
    _withdraw(connection, made)


def _withdraw(connection: Any, shop: Shop) -> None:
    publish_bank_account(
        connection,
        actor_id=shop.owner.staff_user_id,
        payload=bank_account_withdrawal_document(),
    )


def _publish(connection: Any, shop: Shop) -> None:
    publish_bank_account(connection, actor_id=shop.owner.staff_user_id, payload=ACCOUNT)


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _post(
    client: TestClient, path: str, body: Mapping[str, object], *, if_match: int | None = None
) -> Any:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": uuid4().hex}
    if if_match is not None:
        headers["If-Match"] = f'"{if_match}"'
    return client.post(path, headers=headers, json=body)


def _qr(client: TestClient, order_id: UUID) -> dict[str, Any]:
    response = client.get(f"/internal/v1/orders/{order_id}/vietqr")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _pay(client: TestClient, order_id: UUID, amount: int) -> None:
    version = client.get(f"/internal/v1/orders/{order_id}").json()["row_version"]
    paid = _post(
        client,
        f"/internal/v1/orders/{order_id}/payments",
        {
            "amount_vnd": amount,
            "method": "CHUYEN_KHOAN",
            "transfer_seen": True,
            "bank_ref_last": None,
            "collected_by_customer": False,
        },
        if_match=version,
    )
    assert paid.status_code in {200, 201}, paid.text


def test_the_order_qr_is_refused_until_published_then_asks_for_exactly_what_remains(
    connection: Any, client: TestClient, shop: Shop
) -> None:
    order_id = shop.ready_order(shop.customer())
    _as(shop.counter)
    read = client.get(f"/internal/v1/orders/{order_id}").json()

    before = _qr(client, order_id)
    assert before["refusal"] == "BANK_ACCOUNT_UNPUBLISHED"
    assert (before["payload"], before["modules"], before["amount_vnd"]) == (None, None, None)
    code = before["transfer_code"]
    assert code == f"NTL{date.fromisoformat(read['ticket_issued_on']):%d%m}" + (
        f"{read['ticket_number']:03d}"
    )

    _publish(connection, shop)
    after = _qr(client, order_id)
    assert after["refusal"] is None
    assert after["amount_vnd"] == read["remaining_vnd"] == TOTAL_VND
    assert after["amount_source"] == "BALANCE_DUE"
    assert (after["account_name"], after["bank_display_name"]) == ("TIEM GIAT NHA TRANG", "ACB")
    parsed = parse_payload(after["payload"])
    assert (parsed.amount_vnd, parsed.purpose, parsed.bank_bin, parsed.account_number) == (
        TOTAL_VND,
        code,
        "970416",
        "257678859",
    )

    # The matrix: square rows of 0/1, a 4-module light quiet zone, the finder patterns in three
    # corners, and exactly the symbol segno draws for this payload at level M.
    modules = after["modules"]
    size = len(modules)
    assert size >= 21 + 8 and all(len(row) == size for row in modules)
    assert {value for row in modules for value in row} == {0, 1}
    for index in range(4):
        assert set(modules[index]) == set(modules[size - 1 - index]) == {0}
        assert {row[index] for row in modules} == {row[size - 1 - index] for row in modules} == {0}
    finder = [row[4:11] for row in modules[4:11]]
    assert finder[0] == [1] * 7 and finder[3] == [1, 0, 1, 1, 1, 0, 1]
    assert modules == qr_modules(after["payload"])
    expected = segno.make_qr(after["payload"], error="M", boost_error=False)
    assert expected.error == "M" and len(modules) == expected.symbol_size(border=4)[0]
    assert "<" not in str(after)  # no markup crosses the API

    _pay(client, order_id, 40_000)
    part = _qr(client, order_id)
    assert part["amount_vnd"] == TOTAL_VND - 40_000
    assert (
        part["amount_vnd"] == client.get(f"/internal/v1/orders/{order_id}").json()["remaining_vnd"]
    )
    assert parse_payload(part["payload"]).amount_vnd == TOTAL_VND - 40_000

    _pay(client, order_id, TOTAL_VND - 40_000)
    paid = _qr(client, order_id)
    assert paid["refusal"] == "NOTHING_OWED"
    assert paid["modules"] is None and paid["transfer_code"] == code


def test_the_order_qr_is_the_order_reads_to_give(
    connection: Any, client: TestClient, shop: Shop
) -> None:
    order_id = shop.ready_order(shop.customer())
    _publish(connection, shop)
    stranger = _join(connection, _store(connection), _person(connection, StaffRole.OPERATOR))
    _as(stranger)
    assert client.get(f"/internal/v1/orders/{order_id}/vietqr").status_code == 404
    _as(shop.counter)
    assert client.get(f"/internal/v1/orders/{uuid4()}/vietqr").status_code == 404


def test_the_order_search_resolves_a_transfer_code_in_the_callers_store(
    connection: Any, client: TestClient, shop: Shop
) -> None:
    order_id = shop.ready_order(shop.customer())
    _as(shop.counter)
    code = _qr(client, order_id)["transfer_code"]
    base = f"/internal/v1/stores/{shop.store_id}/orders"

    for typed in (code, code.lower(), f" {code[:6]} {code[6:]} ", f"NTL{order_id.hex[:8]}"):
        found = client.get(base, params={"transfer_code": typed})
        assert found.status_code == 200, found.text
        assert str(order_id) in [item["order_id"] for item in found.json()], typed

    def refused(params: dict[str, str | int]) -> Any:
        response = client.get(base, params=params)
        assert response.status_code == 422, response.text
        return response.json()["detail"]

    assert refused({"transfer_code": "NTL3213012"}) == {"reason_code": "TRANSFER_CODE_INVALID"}
    assert refused({"transfer_code": "hello"}) == {"reason_code": "TRANSFER_CODE_INVALID"}
    assert refused({"transfer_code": "NTLCN0F1E2D0926"}) == {
        "reason_code": "TRANSFER_CODE_ACCOUNT_MONTH"
    }
    assert refused({"transfer_code": code, "ticket": 1}) == "ticket and transfer_code are exclusive"

    other = Shop(connection)
    other_order = other.ready_order(other.customer())
    _as(shop.counter)
    found = client.get(base, params={"transfer_code": f"NTL{other_order.hex[:8]}"})
    assert found.status_code == 200 and found.json() == []


def _local(day: date, hour: int = 10) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=HCM)


def test_the_account_month_qr_asks_for_the_statements_unpaid_figure(
    connection: Any, client: TestClient, shop: Shop
) -> None:
    hotel = shop.customer()
    shop.open(hotel, 1_000_000)
    orders = [shop.ready_order(hotel) for _ in range(2)]
    now = datetime.now(UTC)
    for order_id in orders:
        shop.charge(order_id, at=now)
    shop.pay(hotel, 60_000, at=now)
    month = f"{now.astimezone(HCM):%Y-%m}"
    path = (
        f"/internal/v1/stores/{shop.store_id}/customers/{hotel}/account/statements/{month}/vietqr"
    )

    _as(shop.counter)
    refused = client.get(path)
    assert refused.status_code == 200, refused.text
    assert refused.json()["refusal"] == "BANK_ACCOUNT_UNPUBLISHED"

    _publish(connection, shop)
    body = client.get(path).json()
    account_id = UUID(body["account_id"])
    assert body["refusal"] is None
    assert body["amount_vnd"] == 2 * TOTAL_VND - 60_000
    assert body["amount_source"] == "STATEMENT_UNPAID"
    assert body["unpaid_before_month_vnd"] == 0
    assert body["transfer_code"] == f"NTLCN{account_id.hex[:6].upper()}{now.astimezone(HCM):%m%y}"
    assert parse_payload(body["payload"]).purpose == body["transfer_code"]
    assert body["query_version"].startswith("account-month-unpaid-v1:")

    bad = client.get(path.replace(month, "2026-13"))
    assert bad.status_code == 422
    stranger = _join(connection, _store(connection), _person(connection, StaffRole.OPERATOR))
    _as(stranger)
    assert client.get(path).status_code == 403
