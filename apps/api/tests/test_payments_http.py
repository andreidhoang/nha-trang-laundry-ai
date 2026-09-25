"""`PAYMENT-001` (`DEC-035`) over HTTP against real PostgreSQL: the payments route, end to end.

`POST /internal/v1/orders/{order_id}/payments` takes a deposit, a part payment or the rest, with its
method. These tests drive the counter's real sequence through the served routes -- a 50.000 ₫
transfer deposit at drop-off, the rest in cash at pickup with the handover, then closing the order
-- and the refusals the route owes: overpayment, a transfer not seen, pickup while partly paid, no
`If-Match`, a stale one, a replayed key, a reused key with a changed amount, and a member of
another shop. The order read and the day's takings are read back through their own routes.
"""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import app, get_operations_service
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from test_prepaid_dropoff_http import (
    CSRF,
    ORIGIN,
    QUOTED_TOTAL,
    _as,
    _dropped_off,
    _member,
    _post,
    _store,
)


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection(database_url: str) -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    settings = AuthSettings(database_url=database_url)
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


def _production(client: TestClient, order_id: Any, version: int, *targets: str) -> int:
    for target in targets:
        moved = _post(
            client,
            f"/internal/v1/orders/{order_id}/production-transition",
            {"target": target},
            if_match=version,
        )
        assert moved.status_code == 200, moved.text
        version = int(moved.json()["row_version"])
    return version


def _pay(client: TestClient, order_id: Any, version: int, body: dict[str, Any], **kw: Any) -> Any:
    return _post(client, f"/internal/v1/orders/{order_id}/payments", body, if_match=version, **kw)


def test_a_transfer_deposit_then_the_rest_in_cash_at_pickup_then_hand_over(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id, owner = _store(connection)
    staff = _member(connection, store_id, owner, StaffRole.OPERATOR)
    order_id, version = _dropped_off(connection, store_id, staff)
    _as(staff)

    key = f"deposit-{uuid4().hex}"
    body = {
        "amount_vnd": 50_000,
        "method": "CHUYEN_KHOAN",
        "transfer_seen": True,
        "bank_ref_last": "ft 2609",
    }
    deposit = _pay(client, order_id, version, body, key=key)
    assert deposit.status_code == 201, deposit.text
    paid = deposit.json()
    assert (paid["balance_status"], paid["paid_vnd"], paid["remaining_vnd"]) == (
        "PARTIALLY_PAID",
        50_000,
        QUOTED_TOTAL - 50_000,
    )
    assert paid["bank_ref_last"] == "FT2609" and paid["settlement_id"] is None
    # The same key and the same payment replay the first answer; a changed amount is a conflict.
    again = _pay(client, order_id, version, body, key=key)
    assert again.status_code == 201 and again.json()["replayed"] is True
    assert again.json()["payment_id"] == paid["payment_id"]
    changed = _pay(client, order_id, version, {**body, "amount_vnd": 50_001}, key=key)
    assert changed.status_code == 409 and changed.json()["detail"] == "IDEMPOTENCY_CONFLICT"

    read = client.get(f"/internal/v1/orders/{order_id}").json()
    assert (read["owed_vnd"], read["paid_vnd"], read["remaining_vnd"]) == (
        QUOTED_TOTAL,
        50_000,
        QUOTED_TOTAL - 50_000,
    )
    assert read["charges"] == [{"kind": "QUOTED_TOTAL", "amount_vnd": QUOTED_TOTAL}]
    assert [(p["amount_vnd"], p["method"], p["legacy"]) for p in read["payments"]] == [
        (50_000, "CHUYEN_KHOAN", False)
    ]
    assert read["payments_truncated"] is False
    offered = [step["step"] for step in read["next_steps"]]
    assert "TAKE_PAYMENT" in offered and "SETTLE" not in offered and "PREPAY" not in offered

    version = _production(
        client,
        order_id,
        int(read["row_version"]),
        "QUEUED",
        "IN_PROCESS",
        "QUALITY_CHECK",
        "READY_AT_STORE",
    )
    ready = client.get(f"/internal/v1/orders/{order_id}").json()
    primary = next(step["step"] for step in ready["next_steps"] if step["primary"])
    assert primary == "TAKE_PAYMENT" and ready["payment_may_hand_over"] is True

    # Overpayment is refused: the counter gives change.
    over = _pay(client, order_id, version, {"amount_vnd": QUOTED_TOTAL, "method": "TIEN_MAT"})
    assert over.status_code == 422
    assert over.json()["detail"] == {
        "outcome": "NOT_SUPPORTED",
        "reason_code": "OVERPAYMENT_REFUSED",
        "decision": None,
    }
    # Pickup is refused while only the deposit is paid.
    pickup = _post(client, f"/internal/v1/orders/{order_id}/collection", if_match=version)
    assert pickup.json()["detail"]["reason_code"] == "COLLECTION_REQUIRES_PAYMENT"
    hand_over = _post(
        client, f"/internal/v1/orders/{order_id}/steps", {"step": "HAND_OVER"}, if_match=version
    )
    assert hand_over.status_code == 409

    rest = _pay(
        client,
        order_id,
        version,
        {
            "amount_vnd": QUOTED_TOTAL - 50_000,
            "method": "TIEN_MAT",
            "collected_by_customer": True,
        },
    )
    assert rest.status_code == 201, rest.text
    settled = rest.json()
    assert (settled["balance_status"], settled["remaining_vnd"]) == ("PAID", 0)
    assert settled["settlement_shape"] == "EXACT_PAYMENT_SELF_COLLECTION"
    assert settled["self_collection_recorded"] is True

    done = _post(
        client,
        f"/internal/v1/orders/{order_id}/steps",
        {"step": "HAND_OVER"},
        if_match=int(settled["row_version"]),
    )
    assert done.status_code == 200, done.text
    assert done.json()["commercial"] == "COMPLETED"
    assert (done.json()["paid_vnd"], done.json()["remaining_vnd"]) == (QUOTED_TOTAL, 0)

    takings = client.get(f"/internal/v1/stores/{store_id}/settlements/today").json()
    assert (takings["collected_vnd"], takings["payment_count"]) == (QUOTED_TOTAL, 2)
    assert (takings["cash_vnd"], takings["transfer_vnd"]) == (QUOTED_TOTAL - 50_000, 50_000)
    assert (takings["cash_count"], takings["transfer_count"], takings["settlement_count"]) == (
        1,
        1,
        1,
    )
    assert takings["query_version"].startswith("collected-today-v3:")


def test_the_payments_route_refuses_what_it_must(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id, owner = _store(connection)
    staff = _member(connection, store_id, owner, StaffRole.OPERATOR)
    order_id, version = _dropped_off(connection, store_id, staff)
    path = f"/internal/v1/orders/{order_id}/payments"
    _as(staff)

    for body, code in (
        ({"amount_vnd": 10_000, "method": "CHUYEN_KHOAN"}, "TRANSFER_NOT_SEEN"),
        (
            {"amount_vnd": 10_000, "method": "TIEN_MAT", "bank_ref_last": "FT26"},
            "BANK_REF_INVALID",
        ),
        ({"amount_vnd": 0, "method": "TIEN_MAT"}, "PAYMENT_AMOUNT_INVALID"),
        (
            {"amount_vnd": 10_000, "method": "TIEN_MAT", "collected_by_customer": True},
            "HANDOVER_REQUIRES_FULL_PAYMENT",
        ),
    ):
        refused = _pay(client, order_id, version, body)
        assert refused.status_code == 422, refused.text
        assert refused.json()["detail"]["reason_code"] == code
    # Not a method this counter has; not an integer of đồng.
    assert _pay(client, order_id, version, {"amount_vnd": 1, "method": "THE"}).status_code == 422
    assert (
        _pay(client, order_id, version, {"amount_vnd": 1.5, "method": "TIEN_MAT"}).status_code
        == 422
    )
    # The precondition header is required, and a stale one is a conflict.
    missing = client.post(
        path,
        headers={"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": "k-1"},
        json={"amount_vnd": 1, "method": "TIEN_MAT"},
    )
    assert missing.status_code == 428
    stale = _pay(client, order_id, version - 1, {"amount_vnd": 1, "method": "TIEN_MAT"})
    assert stale.status_code == 409 and str(stale.json()["detail"]).startswith("STALE_VERSION")

    # A member of another shop learns nothing and changes nothing.
    _other_store, other_owner = _store(connection)
    _as(other_owner)
    assert (
        _pay(client, order_id, version, {"amount_vnd": 1, "method": "TIEN_MAT"}).status_code == 403
    )
    _as(staff)
    read = client.get(f"/internal/v1/orders/{order_id}").json()
    assert (read["balance"], read["paid_vnd"], read["payments"]) == ("UNPAID", 0, [])


def test_the_exact_total_settlement_route_still_works_and_lands_on_the_ledger(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id, owner = _store(connection)
    staff = _member(connection, store_id, owner, StaffRole.OPERATOR)
    order_id, _ = _dropped_off(connection, store_id, staff)
    _as(staff)
    paid = _post(
        client,
        f"/internal/v1/orders/{order_id}/settlement",
        {"paid_amount_vnd": QUOTED_TOTAL, "collected_by_customer": False},
    )
    assert paid.status_code == 201, paid.text
    read = client.get(f"/internal/v1/orders/{order_id}").json()
    assert (read["paid_vnd"], read["remaining_vnd"]) == (QUOTED_TOTAL, 0)
    assert [(p["method"], p["legacy"]) for p in read["payments"]] == [("TIEN_MAT", True)]
