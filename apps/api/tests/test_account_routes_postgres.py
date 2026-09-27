"""`PAYMENT-002` (`DEC-035`, B2B half) over HTTP against real PostgreSQL: account customers.

The served routes, end to end, in the order a shop uses them: the owner opens an account with no
limit typed; the counter is refused `ACCOUNT_LIMIT_UNSET`; the owner types a limit under `If-Match`;
two orders leave on it and a third is refused over it; a payment reaches the oldest first; the
month's statement totals it; the day's takings count the payment once. And the refusals each route
owes: roles (the counter cannot open or lift, an auditor cannot collect), no `If-Match`, a stale
one, a replayed key, a reused key with a changed body, a member of another store, a bad month.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.accounts import AccountService
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_account_service,
    get_operations_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_customer_accounts import Shop
from test_customer_records import _join, _person, _store
from test_order_step_repository import TOTAL_VND

CSRF = "a" * 40
ORIGIN = "http://testserver"
DENIED = {"detail": "operation denied"}


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


def _account_path(shop: Shop, customer_id: UUID) -> str:
    return f"/internal/v1/stores/{shop.store_id}/customers/{customer_id}/account"


def test_an_account_from_opening_to_payment_over_http(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    shop = Shop(connection)
    homestay = shop.customer()
    path = _account_path(shop, homestay)
    orders = [shop.ready_order(homestay) for _ in range(3)]

    _as(shop.owner)
    before = client.get(path)
    assert before.status_code == 200, before.text
    assert before.json()["account"] is None and before.json()["open_refusal"] is None
    assert before.json()["recommended_limit_vnd"] == 3_000_000

    # Only the owner opens it.
    _as(shop.counter)
    assert _send(client, "POST", path, {"credit_limit_vnd": None}).json() == DENIED
    _as(shop.owner)
    key = f"open-{uuid4().hex}"
    opened = _send(client, "POST", path, {"credit_limit_vnd": None}, key=key)
    assert opened.status_code == 201, opened.text
    account = opened.json()["account"]
    assert account["credit_limit_vnd"] is None
    assert account["handover_refusal"] == "ACCOUNT_LIMIT_UNSET"
    replay = _send(client, "POST", path, {"credit_limit_vnd": None}, key=key)
    assert replay.status_code == 201 and replay.json()["replayed"] is True
    conflict = _send(client, "POST", path, {"credit_limit_vnd": 1_000_000}, key=key)
    assert conflict.status_code == 409 and _code(conflict) == "IDEMPOTENCY_CONFLICT"
    again = _send(client, "POST", path, {"credit_limit_vnd": 1_000_000})
    assert again.status_code == 409 and _code(again) == "ACCOUNT_ALREADY_OPEN"
    # A strict body: the limit is a whole number, not a string or a boolean.
    for bad in ("300000", True, 0):
        refused = _send(client, "PATCH", path, {"credit_limit_vnd": bad}, if_match=1)
        assert refused.status_code == 422, bad

    # No limit typed: the order page says why, and the counter is refused by name.
    _as(shop.counter)
    offer = client.get(f"/internal/v1/orders/{orders[0]}/account-handover").json()["handover"]
    assert (offer["offered"], offer["refusal"]) == (False, "ACCOUNT_LIMIT_UNSET")
    assert offer["collected_by_customer"] is True
    version = offer["row_version"]
    charge_path = f"/internal/v1/orders/{orders[0]}/account-charge"
    unset = _send(client, "POST", charge_path, {"collected_by_customer": True}, if_match=version)
    assert unset.status_code == 422 and unset.json()["detail"] == {
        "outcome": "NOT_SUPPORTED",
        "reason_code": "ACCOUNT_LIMIT_UNSET",
        "decision": "DEC-035",
    }

    # The owner types the limit, under If-Match.
    _as(shop.owner)
    limit = {"credit_limit_vnd": 2 * TOTAL_VND}
    assert _send(client, "PATCH", path, limit).status_code == 428
    assert (
        _send(client, "PATCH", path, limit, if_match=account["row_version"] + 5).status_code == 409
    )
    typed = _send(client, "PATCH", path, limit, if_match=account["row_version"])
    assert typed.status_code == 200, typed.text
    assert typed.json()["account"]["credit_limit_vnd"] == 2 * TOTAL_VND

    # Two leave on the account, the third is refused over the limit.
    _as(shop.counter)
    assert _send(client, "POST", charge_path, {"collected_by_customer": True}).status_code == 428
    key = f"charge-{uuid4().hex}"
    first = _send(
        client, "POST", charge_path, {"collected_by_customer": True}, key=key, if_match=version
    )
    assert first.status_code == 201, first.text
    assert (first.json()["balance_status"], first.json()["outstanding_after_vnd"]) == (
        "ON_ACCOUNT",
        TOTAL_VND,
    )
    replayed = _send(
        client, "POST", charge_path, {"collected_by_customer": True}, key=key, if_match=version
    )
    assert replayed.status_code == 201 and replayed.json()["replayed"] is True
    read = client.get(f"/internal/v1/orders/{orders[0]}").json()
    assert (read["balance"], read["paid_vnd"], read["remaining_vnd"]) == (
        "ON_ACCOUNT",
        0,
        TOTAL_VND,
    )
    second_version = client.get(f"/internal/v1/orders/{orders[1]}").json()["row_version"]
    second = _send(
        client,
        "POST",
        f"/internal/v1/orders/{orders[1]}/account-charge",
        {"collected_by_customer": True},
        if_match=second_version,
    )
    assert second.status_code == 201, second.text
    third_offer = client.get(f"/internal/v1/orders/{orders[2]}/account-handover").json()
    assert third_offer["handover"]["refusal"] == "ACCOUNT_LIMIT_EXCEEDED"
    third = _send(
        client,
        "POST",
        f"/internal/v1/orders/{orders[2]}/account-charge",
        {"collected_by_customer": True},
        if_match=third_offer["handover"]["row_version"],
    )
    assert third.status_code == 422 and _code(third) == "ACCOUNT_LIMIT_EXCEEDED"

    # A payment against the account, oldest first; overpayment refused; an auditor cannot collect.
    state = client.get(path).json()["account"]
    assert state["outstanding_vnd"] == 2 * TOTAL_VND
    pay_path = f"{path}/payments"
    body = {"amount_vnd": TOTAL_VND + 30_000, "method": "TIEN_MAT"}
    over = _send(
        client,
        "POST",
        pay_path,
        {**body, "amount_vnd": 3 * TOTAL_VND},
        if_match=state["row_version"],
    )
    assert over.status_code == 422 and _code(over) == "OVERPAYMENT_REFUSED"
    auditor = _join(connection, shop.store_id, _person(connection, StaffRole.AUDITOR))
    _as(auditor)
    assert _send(client, "POST", pay_path, body, if_match=state["row_version"]).json() == DENIED
    assert client.get(path).status_code == 200
    _as(shop.counter)
    paid = _send(client, "POST", pay_path, body, if_match=state["row_version"])
    assert paid.status_code == 201, paid.text
    assert [
        (item["order_id"], item["amount_vnd"], item["settled"])
        for item in paid.json()["allocations"]
    ] == [
        (str(orders[0]), TOTAL_VND, True),
        (str(orders[1]), 30_000, False),
    ]
    stale = _send(
        client, "POST", pay_path, {**body, "amount_vnd": 1}, if_match=state["row_version"]
    )
    assert stale.status_code == 409
    assert client.get(f"/internal/v1/orders/{orders[0]}").json()["balance"] == "PAID"

    # The month's statement totals it, and the day's takings count the payment once.
    month = datetime.now(UTC).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m")
    statement = client.get(f"{path}/statements/{month}")
    assert statement.status_code == 200, statement.text
    figures = statement.json()["statement"]
    assert (figures["charges_vnd"], figures["payments_vnd"], figures["closing_vnd"]) == (
        2 * TOTAL_VND,
        TOTAL_VND + 30_000,
        TOTAL_VND - 30_000,
    )
    assert figures["due_on"].endswith("-15")
    assert statement.json()["query_version"].startswith("account-statement-v1:")
    assert client.get(f"{path}/statements/2026-13").json()["detail"]["reason_code"] == (
        "MONTH_INVALID"
    )
    takings = client.get(f"/internal/v1/stores/{shop.store_id}/settlements/today").json()
    assert takings["cash_vnd"] == TOTAL_VND + 30_000

    # The lift is the owner's, with a reason.
    lift = {"reason": "ok", "until": datetime.now(UTC).date().isoformat()}
    version = client.get(path).json()["account"]["row_version"]
    assert _send(client, "POST", f"{path}/block-lift", lift, if_match=version).json() == DENIED
    _as(shop.owner)
    short = _send(client, "POST", f"{path}/block-lift", lift, if_match=version)
    assert short.status_code == 422 and _code(short) == "LIFT_REASON_REQUIRED"


def test_another_stores_member_meets_the_same_refusals(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    shop = Shop(connection)
    homestay = shop.customer()
    shop.open(homestay, 1_000_000)
    order_id = shop.ready_order(homestay)
    other = _store(connection)
    stranger = _join(connection, other, _person(connection, StaffRole.OWNER_ADMIN))
    _as(stranger)
    assert client.get(_account_path(shop, homestay)).json() == DENIED
    assert client.get(f"/internal/v1/orders/{order_id}/account-handover").json() == DENIED
    foreign = client.get(f"/internal/v1/stores/{other}/customers/{homestay}/account")
    assert foreign.status_code == 404
    charge = _send(
        client,
        "POST",
        f"/internal/v1/orders/{order_id}/account-charge",
        {"collected_by_customer": True},
        if_match=1,
    )
    assert charge.json() == DENIED
    # And an order with no customer record has nothing to offer.
    _as(shop.counter)
    assert client.get(f"/internal/v1/orders/{uuid4()}/account-handover").status_code == 404
