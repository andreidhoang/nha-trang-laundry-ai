"""`MONEY-LIFECYCLE-009` (review M1) over HTTP: the served routes a counter uses around a storage
fee that a part payment already covered some of.

Before this item, once a later event lowered the recomputed fee below what the ledger held, the
order read, the board and the waiting list answered 500 for that store -- one order took the whole
board down -- and the waiver that left what is owed equal to what was paid stranded the order
"partly paid" with nothing to take. Here, through the routes: the storage read states what the
waiver will do before the press (A3); the waiver settles; 0 đồng settles a ledger that already
covers everything (A2); the goods leave and the order completes.
"""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import app, get_operations_service, get_unclaimed_service
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_api.unclaimed import UnclaimedService
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_domain.unclaimed import withdrawal_document
from test_prepaid_dropoff_http import CSRF, QUOTED_TOTAL, _as, _post
from test_unclaimed_http import Shop, _age, _publish, _ready


def _url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_url()) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = AuthSettings(database_url=_url())
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    app.dependency_overrides[get_unclaimed_service] = lambda: UnclaimedService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Generator[Shop, None, None]:
    """One store with each role; no storage policy in force before or after the test."""

    made = Shop(connection)
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )
    yield made
    connection.rollback()
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )


FEE_DAY_25 = 25_000
PART_OF_FEE = QUOTED_TOTAL + 3_000


def _read(client: TestClient, order_id: UUID) -> dict[str, Any]:
    answer = client.get(f"/internal/v1/orders/{order_id}")
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


def _pay(client: TestClient, order_id: UUID, amount: int, *, collected: bool = False) -> Any:
    return _post(
        client,
        f"/internal/v1/orders/{order_id}/payments",
        {"amount_vnd": amount, "method": "TIEN_MAT", "collected_by_customer": collected},
        if_match=int(_read(client, order_id)["row_version"]),
    )


def _step(client: TestClient, order_id: UUID, step: str) -> Any:
    return _post(
        client,
        f"/internal/v1/orders/{order_id}/steps",
        {"step": step},
        if_match=int(_read(client, order_id)["row_version"]),
    )


def _part_paid(client: TestClient, connection: Any, shop: Shop) -> UUID:
    order_id = _ready(client, connection, shop)
    _age(connection, order_id, 25)
    _as(shop.operator)
    assert _read(client, order_id)["owed_vnd"] == QUOTED_TOTAL + FEE_DAY_25
    paid = _pay(client, order_id, PART_OF_FEE)
    assert paid.status_code == 201, paid.text
    assert paid.json()["remaining_vnd"] == FEE_DAY_25 - 3_000
    return order_id


def test_the_waiver_sheet_is_told_it_settles_and_the_waiver_settles(
    connection: psycopg.Connection[Any], client: TestClient, shop: Shop
) -> None:
    _publish(connection, shop)
    connection.commit()
    order_id = _part_paid(client, connection, shop)

    storage = client.get(f"/internal/v1/orders/{order_id}/storage")
    assert storage.status_code == 200, storage.text
    body = storage.json()
    assert body["storage_fee"]["status"] == "ACCRUING"
    assert body["storage_fee"]["already_paid_vnd"] == 3_000
    assert body["waiver_effect"] == {
        "waived_vnd": FEE_DAY_25 - 3_000,
        "kept_vnd": 3_000,
        "owed_after_vnd": PART_OF_FEE,
        "remaining_after_vnd": 0,
        "settles": True,
    }

    _as(shop.approver)
    waived = _post(
        client,
        f"/internal/v1/orders/{order_id}/storage-fee-waiver",
        {"reason": "khách quen"},
        if_match=int(body["row_version"]),
    )
    assert waived.status_code == 200, waived.text
    view = waived.json()
    assert (view["balance"], view["owed_vnd"], view["paid_vnd"], view["remaining_vnd"]) == (
        "PAID",
        PART_OF_FEE,
        PART_OF_FEE,
        0,
    )
    assert view["settlement_shape"] == "EXACT_PAYMENT_PREPAID_SELF_COLLECTION"
    after = client.get(f"/internal/v1/orders/{order_id}/storage").json()
    assert after["storage_fee"] == {
        "status": "FIXED",
        "amount_vnd": 3_000,
        "chargeable_days": None,
        "fee_per_started_day_vnd": None,
        "cap_vnd": None,
        "capped": False,
        "already_paid_vnd": 0,
    }
    assert after["waiver_effect"] is None

    _as(shop.operator)
    collected = _post(
        client,
        f"/internal/v1/orders/{order_id}/collection",
        if_match=int(_read(client, order_id)["row_version"]),
    )
    assert collected.status_code == 201, collected.text
    done = _step(client, order_id, "HAND_OVER")
    assert done.status_code == 200, done.text
    assert done.json()["commercial"] == "COMPLETED"


def test_a_held_order_never_takes_the_board_down_and_zero_dong_settles_it_after_a_rewash(
    connection: psycopg.Connection[Any], client: TestClient, shop: Shop
) -> None:
    _publish(connection, shop)
    connection.commit()
    order_id = _part_paid(client, connection, shop)
    neighbour = _ready(client, connection, shop)
    _as(shop.operator)

    held = _step(client, order_id, "HOLD")
    assert held.status_code == 200, held.text
    # Every read of the store answers, and agrees.
    read = _read(client, order_id)
    assert (read["owed_vnd"], read["paid_vnd"], read["remaining_vnd"]) == (
        PART_OF_FEE,
        PART_OF_FEE,
        0,
    )
    board = client.get(f"/internal/v1/stores/{shop.store_id}/orders?limit=200")
    assert board.status_code == 200, board.text
    listed = {row["order_id"]: row for row in board.json()}
    assert listed[str(order_id)]["owed_vnd"] == PART_OF_FEE
    assert str(neighbour) in listed
    waiting = client.get(f"/internal/v1/stores/{shop.store_id}/orders/awaiting-pickup")
    assert waiting.status_code == 200, waiting.text
    storage = client.get(f"/internal/v1/orders/{order_id}/storage").json()
    assert (storage["storage_fee"]["status"], storage["storage_fee"]["amount_vnd"]) == (
        "ALREADY_PAID",
        3_000,
    )
    assert storage["waiver_effect"] is None

    # Resume, then a rewash: the laundry is ready again today and its free days restart.
    assert _step(client, order_id, "RESUME").status_code == 200
    rewash = _post(
        client,
        f"/internal/v1/orders/{order_id}/steps",
        {"step": "REWASH", "rewash_reason": "NOT_CLEAN"},
        if_match=int(_read(client, order_id)["row_version"]),
    )
    assert rewash.status_code == 200, rewash.text
    for step in ("QUALITY_CHECK", "MARK_READY"):
        assert _step(client, order_id, step).status_code == 200
    ready = _read(client, order_id)
    assert (ready["balance"], ready["remaining_vnd"], ready["payment_may_hand_over"]) == (
        "PARTIALLY_PAID",
        0,
        True,
    )
    assert "TAKE_PAYMENT" in {step["step"] for step in ready["next_steps"]}

    more = _pay(client, order_id, 1)
    assert more.status_code == 422
    assert more.json()["detail"]["reason_code"] == "NOTHING_OWED"
    settled = _pay(client, order_id, 0, collected=True)
    assert settled.status_code == 201, settled.text
    answer = settled.json()
    assert (answer["payment_id"], answer["amount_vnd"], answer["balance_status"]) == (
        None,
        0,
        "PAID",
    )
    assert answer["self_collection_recorded"] is True
    done = _step(client, order_id, "HAND_OVER")
    assert done.status_code == 200, done.text
    assert (done.json()["commercial"], done.json()["paid_vnd"]) == ("COMPLETED", PART_OF_FEE)
