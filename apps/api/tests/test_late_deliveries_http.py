"""`LATE-CREDIT-002` (`DEC-042`) over HTTP against real PostgreSQL.

The two routes at wall time: the list refuses to measure anything before the owner publishes the
remedy policy, then shows a delivery late by 3 hours with the server's minutes and the remedy
probe's credit; *Lỗi của tiệm* records the incident, the proposal at those minutes and the decision
and replays on the same key; *Không phải lỗi tiệm* needs a reason and never echoes its note; the
roles, another shop's staff and the report's new block.

Harness step (documented): the delivery leg is recorded with its own `recorded_at` three hours after
the promise the published policy computed (the leg command takes the instant as a parameter).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.late_deliveries import LateDeliveryService
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_late_delivery_service,
    get_ops_board_service,
)
from nha_trang_laundry_api.ops_board import OpsBoardService
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.promise_policy import publish_turnaround_policy
from nha_trang_laundry_db.remedies import publish_remedy_policy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_late_deliveries import Shop, _delivery_order, _leg
from test_order_promise_repository import _document
from test_remedies import POLICY as REMEDY_POLICY

CSRF = "l" * 40
ORIGIN = "http://testserver"
DENIED = {"detail": "operation denied"}
VN = ZoneInfo("Asia/Ho_Chi_Minh")


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
    app.dependency_overrides[get_late_delivery_service] = lambda: LateDeliveryService(settings)
    app.dependency_overrides[get_ops_board_service] = lambda: OpsBoardService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Shop:
    made = Shop(connection)
    publish_turnaround_policy(connection, actor_id=made.owner.staff_user_id, payload=_document())
    connection.commit()
    return made


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _post(client: TestClient, path: str, body: dict[str, object], key: str | None = None) -> Any:
    return client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": CSRF,
            "Idempotency-Key": key or f"http-{uuid4().hex}",
        },
        json=body,
    )


def _late_order(connection: Any, shop: Shop, *, hours: int = 3) -> tuple[UUID, datetime]:
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=hours))
    connection.commit()
    return order_id, promise


def _publish(connection: Any, shop: Shop) -> None:
    publish_remedy_policy(connection, actor_id=shop.owner.staff_user_id, payload=REMEDY_POLICY)
    connection.commit()


def _list_path(shop: Shop) -> str:
    return f"/internal/v1/stores/{shop.store_id}/late-deliveries"


def _decide_path(shop: Shop, order_id: UUID) -> str:
    return f"/internal/v1/stores/{shop.store_id}/late-deliveries/{order_id}/decision"


def test_the_list_refuses_to_measure_until_published_then_shows_the_servers_minutes(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id, promise = _late_order(connection, shop)
    _as(shop.operator)
    before = client.get(_list_path(shop))
    assert before.status_code == 200, before.text
    assert before.json()["policy_published"] is False and before.json()["orders"] == []
    refused = _post(client, _decide_path(shop, order_id), {"decision": "STORE_FAULT"})
    assert refused.status_code == 422
    assert refused.json()["detail"] == {
        "reason_code": "REMEDY_POLICY_UNPUBLISHED",
        "decision": "DEC-042",
    }

    _publish(connection, shop)
    body = client.get(_list_path(shop)).json()
    assert body["policy_published"] is True and body["threshold_minutes"] == 120
    assert body["truncated"] is False and body["follow_up"] == []
    [row] = body["orders"]
    assert row["order_id"] == str(order_id) and row["late_by_minutes"] == 180
    assert datetime.fromisoformat(row["deadline_at"]) == promise
    assert row["deadline_basis"] == "FIRST_PROMISE"
    assert row["credit_vnd"] > 0 and row["credit_requires_owner"] is False
    assert row["refunded"] is False and row["credit_refusal"] is None


def test_store_fault_records_the_credit_at_the_measured_minutes_and_replays(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id, _ = _late_order(connection, shop)
    _as(shop.operator)
    credit = client.get(_list_path(shop)).json()["orders"][0]["credit_vnd"]
    made = _post(client, _decide_path(shop, order_id), {"decision": "STORE_FAULT"}, key="late-1")
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["decision"] == "STORE_FAULT_CREDITED" and body["late_by_minutes"] == 180
    assert body["proposal_status"] == "STAFF_AUTHORIZED" and body["amount_vnd"] == credit
    assert body["replayed"] is False and body["incident_id"] and body["proposal_id"]
    again = _post(client, _decide_path(shop, order_id), {"decision": "STORE_FAULT"}, key="late-1")
    assert again.status_code == 201 and again.json()["replayed"] is True
    assert again.json()["proposal_id"] == body["proposal_id"]
    conflict = _post(
        client,
        _decide_path(shop, order_id),
        {"decision": "NOT_STORE_FAULT", "reason_code": "CUSTOMER_ABSENT"},
        key="late-1",
    )
    assert conflict.status_code == 409
    second = _post(
        client,
        _decide_path(shop, order_id),
        {"decision": "NOT_STORE_FAULT", "reason_code": "CUSTOMER_ABSENT"},
    )
    assert second.status_code == 422
    assert second.json()["detail"]["reason_code"] == "ALREADY_DECIDED"
    after = client.get(_list_path(shop)).json()
    assert after["orders"] == []
    assert [item["order_id"] for item in after["follow_up"]] == [str(order_id)]
    assert after["follow_up"][0]["next_step"] == "EXECUTE"
    # The existing execution route pays it, unchanged.
    executed = _post(client, f"/internal/v1/remedy-proposals/{body['proposal_id']}/execution", {})
    assert executed.status_code == 201, executed.text
    assert executed.json()["amount_vnd"] == credit
    assert client.get(_list_path(shop)).json()["follow_up"] == []


def test_not_store_fault_needs_a_reason_and_never_echoes_the_note(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id, _ = _late_order(connection, shop)
    _as(shop.operator)
    missing = _post(client, _decide_path(shop, order_id), {"decision": "NOT_STORE_FAULT"})
    assert missing.json()["detail"]["reason_code"] == "LATE_DELIVERY_REASON_REQUIRED"
    phone = "gọi 0905 123 456"
    looks = _post(
        client,
        _decide_path(shop, order_id),
        {"decision": "NOT_STORE_FAULT", "reason_code": "OTHER", "note": phone},
    )
    assert looks.status_code == 422 and "0905" not in looks.text
    assert looks.json()["detail"]["reason_code"] == "NOTE_LOOKS_LIKE_PHONE"
    malformed = _post(
        client,
        _decide_path(shop, order_id),
        {"decision": "NOT_STORE_FAULT", "reason_code": "OTHER", "note": phone, "minutes": 1},
    )
    assert malformed.status_code == 422 and "0905" not in malformed.text
    done = _post(
        client,
        _decide_path(shop, order_id),
        {"decision": "NOT_STORE_FAULT", "reason_code": "CUSTOMER_WRONG_ADDRESS"},
    )
    assert done.status_code == 201, done.text
    assert done.json()["proposal_id"] is None and done.json()["incident_id"] is None
    assert client.get(_list_path(shop)).json()["orders"] == []


def test_no_minutes_and_no_amount_are_accepted_from_the_client(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id, _ = _late_order(connection, shop)
    _as(shop.operator)
    for extra in ({"late_by_minutes": 500}, {"amount_vnd": 1}):
        refused = _post(client, _decide_path(shop, order_id), {"decision": "STORE_FAULT", **extra})
        assert refused.status_code == 422


def test_roles_and_store_membership(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id, _ = _late_order(connection, shop)
    _as(shop.auditor)
    assert client.get(_list_path(shop)).json() == DENIED
    assert _post(client, _decide_path(shop, order_id), {"decision": "STORE_FAULT"}).json() == DENIED
    other = Shop(connection)
    connection.commit()
    _as(other.operator)
    assert client.get(_list_path(shop)).status_code == 403
    denied = _post(client, _decide_path(shop, order_id), {"decision": "STORE_FAULT"})
    assert denied.status_code == 403
    # Another shop's order under the caller's own store is the same 404 as one that does not exist.
    missing = _post(client, _decide_path(other, order_id), {"decision": "STORE_FAULT"})
    assert missing.status_code == 404


def test_the_report_carries_the_late_delivery_block(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id, promise = _late_order(connection, shop)
    day = (promise + timedelta(hours=3)).astimezone(VN).date()
    _as(shop.owner)
    path = f"/internal/v1/stores/{shop.store_id}/reports/summary?from={day}&to={day}"
    block = client.get(path).json()["late_deliveries"]
    assert block["status"] == "COMPLETE" and block["late"] == 1 and block["undecided"] == 1
    # report-v5 since GOODS-AND-DRAWER-009 (the refund split and MONEY_DRAWER).
    assert block["query_version"].startswith("report-v5:")
    _as(shop.operator)
    _post(
        client,
        _decide_path(shop, order_id),
        {"decision": "NOT_STORE_FAULT", "reason_code": "CUSTOMER_ABSENT"},
    )
    _as(shop.owner)
    block = client.get(path).json()["late_deliveries"]
    assert (block["not_store_fault"], block["undecided"], block["store_fault"]) == (1, 0, 0)
    assert block["credited_vnd"] == 0
    assert datetime.now(UTC) > promise
