"""`PICKUP-REMIND-001` (`DEC-043`) over HTTP against real PostgreSQL.

The two new routes and the contact-attempt route's `reminder_step`, at wall time: the due list,
the fixed text refused before the owner publishes the messaging policy and for a customer who wrote
STOP, *Đã nhắc* with `MESSAGE_SENT` behind the same guard, and the refusals each route owes --
roles, another shop's staff, a step that is not due -- every one naming `DEC-043`.

Fixtures (documented): an order that came in on a chat channel is made by binding a chat
reference to the order's contact id, and the customer's STOP is recorded through the ingress path
production uses (`InboxRepository.record`); no channel adapter exists yet to do either.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import (
    app,
    get_operations_service,
    get_pickup_reminder_service,
    get_unclaimed_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_api.pickup_reminders import PickupReminderService
from nha_trang_laundry_api.unclaimed import UnclaimedService
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_domain.consent import OptOutDisposition
from nha_trang_laundry_domain.unclaimed import withdrawal_document
from test_prepaid_dropoff_http import CSRF, QUOTED_TOTAL, _as, _post
from test_unclaimed_http import DENIED, Shop, _age, _ready

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from message_draft_test_data import (
    publish_test_messaging_policy,
    record_customer_message,
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
    app.dependency_overrides[get_unclaimed_service] = lambda: UnclaimedService(settings)
    app.dependency_overrides[get_pickup_reminder_service] = lambda: PickupReminderService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Generator[Shop, None, None]:
    """One store with each role, and no storage policy in force before or after the test."""

    made = Shop(connection)
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )
    yield made
    connection.rollback()
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )


def _due(client: TestClient, shop: Shop) -> dict[str, Any]:
    response = client.get(f"/internal/v1/stores/{shop.store_id}/pickup-reminders")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _text(client: TestClient, order_id: UUID, step: str) -> Any:
    return client.get(f"/internal/v1/orders/{order_id}/pickup-reminder", params={"step": step})


def _remind(client: TestClient, order_id: UUID, body: dict[str, object]) -> Any:
    return _post(client, f"/internal/v1/orders/{order_id}/contact-attempts", body)


def _chat_order(client: TestClient, connection: Any, shop: Shop) -> UUID:
    order_id = _ready(client, connection, shop)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO contact_channel_bindings (
                provider, provider_user_ref, contact_binding_id, verification_state, row_version,
                created_at, updated_at
            )
            SELECT 'ZALO_OA', %s, o.bound_contact_id, 'UNVERIFIED', 1, now(), now()
            FROM orders o WHERE o.id = %s
            """,
            (f"user-{uuid4().hex}", order_id),
        )
    connection.commit()
    return order_id


def _refusal(response: Any, code: str) -> bool:
    detail = response.json().get("detail") or {}
    return (
        response.status_code == 422
        and detail.get("reason_code") == code
        and detail.get("decision") == "DEC-043"
    )


def test_the_due_list_counts_a_ticket_unreachable_and_refuses_its_text(
    client: TestClient,
    connection: psycopg.Connection[Any],
    shop: Shop,
) -> None:
    order_id = _ready(client, connection, shop)
    stale = _ready(client, connection, shop)
    _age(connection, stale, 3)
    listing = _due(client, shop)
    assert [(row["order_id"], row["step"]) for row in listing["orders"]] == [
        (str(stale), "DAY_3"),
        (str(order_id), "READY"),
    ]
    row = listing["orders"][1]
    assert (row["reachable"], row["zalo_url"], row["phone"]) == ("NONE", None, None)
    assert row["message_refusal"] == "NO_CONTACT" and row["remaining_vnd"] == QUOTED_TOTAL
    assert (listing["total_count"], listing["unreachable_count"]) == (2, 2)
    assert listing["messaging_policy_published"] is False
    assert _refusal(_text(client, order_id, "READY"), "NO_CONTACT")
    assert _refusal(_text(client, order_id, "DAY_3"), "REMINDER_STEP_NOT_DUE")
    assert _text(client, order_id, "DAY_5").status_code == 422

    _as(shop.auditor)
    assert _due(client, shop)["total_count"] == 2
    denied = _text(client, order_id, "READY")
    assert (denied.status_code, denied.json()) == (403, DENIED)
    other = Shop(connection)
    _as(other.operator)
    assert client.get(f"/internal/v1/stores/{shop.store_id}/pickup-reminders").status_code == 403
    assert _text(client, order_id, "READY").status_code == 404


def test_the_text_waits_for_the_owner_and_da_nhac_records_the_message(
    client: TestClient,
    connection: psycopg.Connection[Any],
    shop: Shop,
) -> None:
    order_id = _chat_order(client, connection, shop)
    _as(shop.operator)
    [row] = _due(client, shop)["orders"]
    assert row["reachable"] == "CHAT"
    assert row["message_refusal"] == "MESSAGING_POLICY_UNPUBLISHED"
    assert _refusal(_text(client, order_id, "READY"), "MESSAGING_POLICY_UNPUBLISHED")
    sent: dict[str, object] = {
        "channel": "ZALO",
        "outcome": "MESSAGE_SENT",
        "reminder_step": "READY",
    }
    assert _refusal(_remind(client, order_id, sent), "MESSAGING_POLICY_UNPUBLISHED")
    assert _refusal(
        _remind(client, order_id, {"channel": "ZALO", "outcome": "MESSAGE_SENT"}),
        "REMINDER_STEP_REQUIRED",
    )

    publish_test_messaging_policy(connection)
    connection.commit()
    message = _text(client, order_id, "READY")
    assert message.status_code == 200, message.text
    body = message.json()
    assert body["template"] == "pickup-reminder-v2" and body["basis"] == "OPEN_ORDER"
    assert body["text"].startswith("Cửa hàng xin báo: đồ giặt phiếu số ")
    assert "Số tiền còn lại: 110.000 ₫." in body["text"]

    recorded = _remind(client, order_id, sent)
    assert recorded.status_code == 201, recorded.text
    assert (recorded.json()["outcome"], recorded.json()["reminder_step"]) == (
        "MESSAGE_SENT",
        "READY",
    )
    assert _due(client, shop)["orders"] == []
    storage = client.get(f"/internal/v1/orders/{order_id}/storage").json()
    assert [(a["outcome"], a["reminder_step"]) for a in storage["attempts"]] == [
        ("MESSAGE_SENT", "READY")
    ]
    assert storage["disposal_verdict"]["attempts_counted"] == 1


def test_a_customer_who_wrote_stop_gets_no_text_and_a_call_is_still_recorded(
    client: TestClient,
    connection: psycopg.Connection[Any],
    shop: Shop,
) -> None:
    order_id = _chat_order(client, connection, shop)
    publish_test_messaging_policy(connection)
    connection.commit()
    bound = connection.execute(
        "SELECT bound_contact_id FROM orders WHERE id = %s", (order_id,)
    ).fetchone()
    assert bound is not None
    record_customer_message(
        connection,
        bound[0],
        received_at=datetime.now(UTC) - timedelta(minutes=2),
        disposition=OptOutDisposition.WITHDRAW,
    )
    connection.commit()
    _as(shop.operator)
    assert _due(client, shop)["orders"][0]["message_refusal"] == "SUPPRESSED"
    assert _refusal(_text(client, order_id, "READY"), "SUPPRESSED")
    assert _refusal(
        _remind(
            client,
            order_id,
            {"channel": "SMS", "outcome": "MESSAGE_SENT", "reminder_step": "READY"},
        ),
        "SUPPRESSED",
    )
    called = _remind(
        client, order_id, {"channel": "CALL", "outcome": "REACHED", "reminder_step": "READY"}
    )
    assert called.status_code == 201, called.text
    assert _due(client, shop)["orders"] == []
