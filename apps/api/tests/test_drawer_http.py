# ruff: noqa: F401, F811  (the fixtures below are imported from the step tests and used by name)
"""GOODS-AND-DRAWER-009 (review M4) over HTTP: the drawer figure and the refund method.

`GET /stores/{id}/settlements/today` (`collected-today-v4`) carries the drawer -- cash in minus cash
handed back -- beside the all-method net, with refunds split by how they went back; the report
carries `MONEY_DRAWER`. A cancellation that hands money back takes `refund_method` on the step route
and on the per-axis route, is refused `REQUIRE_HUMAN` / `REFUND_METHOD_REQUIRED` without it, and
`refund_method` on any other step is a 422 before anything is read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from nha_trang_laundry_db.identity import StaffRole
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from test_order_steps_postgres import (
    _as,
    _Counter,
    _create_order,
    _headers,
    _person,
    _shop,
    client,
    connection,
)


def _received(client: TestClient, connection: Any, store_id: Any, staff: Any) -> dict[str, Any]:
    created = _create_order(client, connection, store_id, staff)
    return _Counter(client).step(
        client.get(f"/internal/v1/orders/{created['order_id']}").json(),
        "RECEIVE",
        slot_approved=True,
    )


def _pay(client: TestClient, order: dict[str, Any], amount: int, method: str) -> dict[str, Any]:
    paid = client.post(
        f"/internal/v1/orders/{order['order_id']}/payments",
        headers=_headers(order["row_version"]),
        json={"amount_vnd": amount, "method": method, "transfer_seen": method == "CHUYEN_KHOAN"},
    )
    assert paid.status_code == 201, paid.text
    return dict(client.get(f"/internal/v1/orders/{order['order_id']}").json())


def _cancel(client: TestClient, order: dict[str, Any], **extra: Any) -> Any:
    return client.post(
        f"/internal/v1/orders/{order['order_id']}/steps",
        headers=_headers(order["row_version"]),
        json={"step": "CANCEL", "custody_resolution": "RETURNED_UNWASHED_REFUNDED", **extra},
    )


def test_today_carries_the_drawer_beside_the_all_method_net(
    client: TestClient, connection: Any
) -> None:
    store_id, staff = _shop(connection)
    cash = _pay(client, _received(client, connection, store_id, staff), 110_000, "TIEN_MAT")
    assert cash["balance"] == "PAID"
    transfer = _pay(client, _received(client, connection, store_id, staff), 110_000, "CHUYEN_KHOAN")
    # The transfer customer is paid back in cash: the net does not move, the drawer does.
    cancel = next(item for item in transfer["next_steps"] if item["step"] == "CANCEL")
    assert cancel["requires"] == ["custody_resolution", "refund_method"]
    refused = _cancel(client, transfer)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == {
        "outcome": "REQUIRE_HUMAN",
        "reason_codes": ["REFUND_METHOD_REQUIRED"],
    }
    done = _cancel(client, transfer, refund_method="TIEN_MAT")
    assert done.status_code == 200, done.text
    assert (done.json()["commercial"], done.json()["balance"]) == ("CANCELLED", "REFUNDED")

    today = client.get(f"/internal/v1/stores/{store_id}/settlements/today")
    assert today.status_code == 200, today.text
    body = today.json()
    assert body["query_version"].startswith("collected-today-v4:")
    assert (body["collected_vnd"], body["cash_vnd"], body["transfer_vnd"]) == (
        220_000,
        110_000,
        110_000,
    )
    assert (body["refunded_vnd"], body["refunded_cash_vnd"], body["refunded_cash_count"]) == (
        110_000,
        110_000,
        1,
    )
    assert (body["refunded_transfer_vnd"], body["refunded_unknown_vnd"]) == (0, 0)
    assert (body["net_vnd"], body["net_direction"]) == (110_000, "IN")
    assert (body["drawer_vnd"], body["drawer_direction"]) == (0, "IN")

    owner = _person(connection, StaffRole.OWNER_ADMIN)
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=owner.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    _as(owner)
    day = datetime.now(UTC).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
    report = client.get(f"/internal/v1/stores/{store_id}/reports/summary?from={day}&to={day}")
    assert report.status_code == 200, report.text
    kpis = {kpi["key"]: kpi for kpi in report.json()["kpis"]}
    assert (kpis["MONEY_DRAWER"]["numerator"], kpis["MONEY_DRAWER"]["direction"]) == (0, "IN")
    assert kpis["MONEY_NET"]["numerator"] == body["net_vnd"]
    refunds = {entry["kind"]: entry for entry in kpis["MONEY_REFUNDED"]["by_kind"]}
    assert refunds["TIEN_MAT"] == {"kind": "TIEN_MAT", "count": 1, "amount_vnd": 110_000}


def test_refund_method_belongs_to_cancel_only(client: TestClient, connection: Any) -> None:
    store_id, staff = _shop(connection)
    order = _received(client, connection, store_id, staff)
    wrong = client.post(
        f"/internal/v1/orders/{order['order_id']}/steps",
        headers=_headers(order["row_version"]),
        json={"step": "START_WASH", "refund_method": "TIEN_MAT"},
    )
    assert wrong.status_code == 422
    # On a cancellation that hands nothing back it is refused rather than dropped.
    nothing = _cancel(client, order, refund_method="TIEN_MAT")
    assert nothing.status_code == 409 and "refund_method is taken only by" in nothing.text
    assert (
        client.get(f"/internal/v1/orders/{order['order_id']}").json()["row_version"]
        == (order["row_version"])
    )


def test_the_per_axis_cancellation_takes_the_method_too(
    client: TestClient, connection: Any
) -> None:
    store_id, staff = _shop(connection)
    order = _pay(client, _received(client, connection, store_id, staff), 30_000, "TIEN_MAT")
    review = client.post(
        f"/internal/v1/orders/{order['order_id']}/transition",
        headers=_headers(order["row_version"]),
        json={"target": "CANCELLATION_REVIEW"},
    )
    assert review.status_code == 200, review.text
    version = review.json()["row_version"]
    body = {"target": "CANCELLED", "custody_resolution": "RETURNED_UNWASHED_REFUNDED"}
    refused = client.post(
        f"/internal/v1/orders/{order['order_id']}/transition",
        headers=_headers(version),
        json=body,
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["reason_codes"] == ["REFUND_METHOD_REQUIRED"]
    done = client.post(
        f"/internal/v1/orders/{order['order_id']}/transition",
        headers=_headers(version),
        json={**body, "refund_method": "CHUYEN_KHOAN"},
    )
    assert done.status_code == 200, done.text
    assert done.json()["balance"] == "REFUNDED"
