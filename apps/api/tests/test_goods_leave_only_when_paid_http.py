# ruff: noqa: F811  (the fixtures below are imported from the step tests and used by name)
"""GOODS-AND-DRAWER-009 (review M2) over HTTP: the named refusal the console words.

Every door the goods leave by answers the same way when the money does not allow it: 422
`{"outcome": "NOT_SUPPORTED", "reason_code", "decision"}`, the shape the payment and collection
routes already send, and nothing written. `RELEASE` on the step route and `RELEASED` on the
per-axis production route say `RELEASE_REQUIRES_PAYMENT` (DEC-035); a return trip of either outcome
says `DELIVERY_REQUIRES_PAYMENT` (DEC-023), or `GOODS_NOT_READY_FOR_HANDOVER` while the laundry is
not finished. Then the review's delivery, driven by its `next_steps` alone, to COMPLETED.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_domain.catalog import FulfillmentMode
from test_order_steps_postgres import (  # noqa: F401  (fixtures re-exported)
    _Counter,
    _create_order,
    _headers,
    _primary,
    _shop,
    client,
    connection,
)

DELIVERY_MODES = [FulfillmentMode.PICKUP_AND_RETURN, FulfillmentMode.RETURN_ONLY]


def _to_the_shelf(client: TestClient, connection: Any, mode: FulfillmentMode) -> dict[str, Any]:
    store_id, staff = _shop(connection)
    created = _create_order(client, connection, store_id, staff, mode)
    counter = _Counter(client)
    order = counter.step(
        client.get(f"/internal/v1/orders/{created['order_id']}").json(),
        "RECEIVE",
        slot_approved=True,
    )
    for step in ("START_WASH", "QUALITY_CHECK", "MARK_READY"):
        order = counter.step(order, step)
    return order


def _leg(client: TestClient, order: dict[str, Any], outcome: str) -> Any:
    return client.post(
        f"/internal/v1/orders/{order['order_id']}/delivery-legs",
        headers=_headers(),
        json={"leg_kind": "RETURN", "outcome": outcome},
    )


def _refused(response: Any, code: str, decision: str | None) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == {
        "outcome": "NOT_SUPPORTED",
        "reason_code": code,
        "decision": decision,
    }


@pytest.mark.parametrize("mode", DELIVERY_MODES, ids=[mode.value for mode in DELIVERY_MODES])
@pytest.mark.parametrize("deposit", [False, True], ids=["unpaid", "deposit"])
def test_an_unpaid_delivery_is_refused_by_name_at_every_door(
    client: TestClient,
    connection: Any,
    mode: FulfillmentMode,
    deposit: bool,
) -> None:
    order = _to_the_shelf(client, connection, mode)
    if deposit:
        paid = client.post(
            f"/internal/v1/orders/{order['order_id']}/payments",
            headers=_headers(order["row_version"]),
            json={"amount_vnd": 50_000, "method": "TIEN_MAT"},
        )
        assert paid.status_code == 201, paid.text
        order = client.get(f"/internal/v1/orders/{order['order_id']}").json()
        assert order["balance"] == "PARTIALLY_PAID"
    assert _primary(order) == "TAKE_PAYMENT"
    assert not {"RELEASE", "DELIVERY_RETURN"} & {item["step"] for item in order["next_steps"]}

    released = client.post(
        f"/internal/v1/orders/{order['order_id']}/steps",
        headers=_headers(order["row_version"]),
        json={"step": "RELEASE"},
    )
    _refused(released, "RELEASE_REQUIRES_PAYMENT", "DEC-035")
    moved = client.post(
        f"/internal/v1/orders/{order['order_id']}/production-transition",
        headers=_headers(order["row_version"]),
        json={"target": "RELEASED"},
    )
    _refused(moved, "RELEASE_REQUIRES_PAYMENT", "DEC-035")
    for outcome in ("SUCCEEDED", "FAILED"):
        _refused(_leg(client, order, outcome), "DELIVERY_REQUIRES_PAYMENT", "DEC-023")

    after = client.get(f"/internal/v1/orders/{order['order_id']}").json()
    assert after["row_version"] == order["row_version"]
    assert (after["production"], after["delivery_legs"]) == ("READY_AT_STORE", [])


def test_a_walk_in_unpaid_release_is_the_same_named_refusal(
    client: TestClient,
    connection: Any,
) -> None:
    """The self-collect half used to be a 409 with English text the console could not word."""

    order = _to_the_shelf(client, connection, FulfillmentMode.SELF_DROP_SELF_COLLECT)
    released = client.post(
        f"/internal/v1/orders/{order['order_id']}/steps",
        headers=_headers(order["row_version"]),
        json={"step": "RELEASE"},
    )
    _refused(released, "RELEASE_REQUIRES_PAYMENT", "DEC-035")


def test_a_return_trip_for_laundry_still_being_washed_is_refused(
    client: TestClient,
    connection: Any,
) -> None:
    store_id, staff = _shop(connection)
    created = _create_order(client, connection, store_id, staff, FulfillmentMode.RETURN_ONLY)
    counter = _Counter(client)
    order = counter.step(
        client.get(f"/internal/v1/orders/{created['order_id']}").json(),
        "RECEIVE",
        slot_approved=True,
    )
    order = counter.pay(order, collected=False)
    assert order["balance"] == "PAID" and order["production"] == "NOT_STARTED"
    for outcome in ("SUCCEEDED", "FAILED"):
        _refused(_leg(client, order, outcome), "GOODS_NOT_READY_FOR_HANDOVER", None)


def test_the_review_delivery_goes_right_by_its_next_steps(
    client: TestClient,
    connection: Any,
) -> None:
    order = _to_the_shelf(client, connection, FulfillmentMode.PICKUP_AND_RETURN)
    counter = _Counter(client)
    assert _primary(order) == "TAKE_PAYMENT"
    order = counter.pay(order, collected=False)
    assert order["settlement_shape"] == "EXACT_PAYMENT_PREPAID_DELIVERY"
    assert _primary(order) == "RELEASE"
    order = counter.step(order, "RELEASE")
    assert _primary(order) == "DELIVERY_RETURN"
    assert _leg(client, order, "FAILED").status_code == 201
    assert _leg(client, order, "SUCCEEDED").status_code == 201
    order = client.get(f"/internal/v1/orders/{order['order_id']}").json()
    assert _primary(order) == "COMPLETE"
    order = counter.step(order, "COMPLETE")
    assert (order["commercial"], order["balance"]) == ("COMPLETED", "PAID")
