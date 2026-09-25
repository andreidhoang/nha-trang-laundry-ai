"""`ORDER-STEPS-001` / `READ-ENRICH-001` over HTTP against real PostgreSQL.

The counter's whole walk-in, from order creation to "khách đã lấy đồ", driven only through
`POST /orders/{id}/steps` and the settlement route, with the number of calls counted; the HTTP
semantics of the step route (428, 400, 409 stale, 422 REQUIRE_HUMAN, replay, 403); and every read
field the task-first console needs, including the two new incident routes and their membership
refusals.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_operations_service,
    get_ops_board_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_api.ops_board import OpsBoardService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import FulfillmentMode

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from quote_test_data import accepted_quote

DENIED = {"detail": "operation denied"}
TOTAL_VND = 110_000


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
    app.dependency_overrides[get_ops_board_service] = lambda: OpsBoardService(settings)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _person(connection: Any, role: StaffRole = StaffRole.OPERATOR) -> StaffPrincipal:
    staff_user_id = uuid4()
    now = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
            """,
            (staff_user_id, f"oidc-{staff_user_id}", now),
        )
        cursor.execute(
            """
            INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
            VALUES (%s, %s, %s, %s)
            """,
            (uuid4(), staff_user_id, role.value, now),
        )
    return StaffPrincipal(staff_user_id, f"oidc-{staff_user_id}", frozenset({role}), True, uuid4())


def _shop(connection: Any) -> tuple[UUID, StaffPrincipal]:
    """A store, its owner, and an operator assigned to it."""

    store_id = uuid4()
    StoreRepository.create(
        connection, store_id=store_id, name="Tiệm thử", created_by=None, correlation_id=uuid4()
    )
    owner = _person(connection, StaffRole.OWNER_ADMIN)
    operator = _person(connection)
    for member in (owner, operator):
        ShadowConsoleRepository.assign_store(
            connection,
            staff_user_id=member.staff_user_id,
            store_id=store_id,
            principal=owner,
            correlation_id=uuid4(),
        )
    return store_id, operator


def _headers(version: int | None = None) -> dict[str, str]:
    headers = {"Idempotency-Key": f"key-{uuid4().hex}"}
    if version is not None:
        headers["If-Match"] = str(version)
    return headers


def _create_order(
    client: TestClient,
    connection: Any,
    store_id: UUID,
    staff: StaffPrincipal,
    mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
) -> dict[str, Any]:
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, fulfillment_mode=mode
    )
    _as(staff)
    # The console's hand-off: every value from the quote read, none pasted.
    listed = client.get(f"/internal/v1/stores/{store_id}/quotes").json()
    (item,) = [entry for entry in listed if entry["quote_id"] == str(quote_id)]
    assert item["contact_binding_id"] == str(contact_id)
    assert item["fulfillment_mode"] == mode.value
    created = client.post(
        f"/internal/v1/stores/{store_id}/orders",
        headers=_headers(),
        json={
            "bound_contact_id": item["contact_binding_id"],
            "quote_id": item["quote_id"],
            "quote_revision": item["revision"],
            "quote_snapshot_hash": item["snapshot_hash"],
            "fulfillment_mode": item["fulfillment_mode"],
            "customer_final_quote_accepted_at": datetime.now(UTC).isoformat(),
            "acquisition_source": "WALK_IN",
        },
    )
    assert created.status_code == 201, created.text
    assert revision == item["revision"] and quote.document.snapshot_hash == item["snapshot_hash"]
    return dict(created.json())


def _primary(view: dict[str, Any]) -> str | None:
    primaries = [item["step"] for item in view["next_steps"] if item["primary"]]
    assert len(primaries) <= 1
    return primaries[0] if primaries else None


class _Counter:
    """Counts the writes the counter makes after the order exists."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.calls: list[str] = []

    def step(self, order: dict[str, Any], step: str, **body: Any) -> dict[str, Any]:
        self.calls.append(step)
        response = self.client.post(
            f"/internal/v1/orders/{order['order_id']}/steps",
            headers=_headers(order["row_version"]),
            json={"step": step, **body},
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    def settle(self, order: dict[str, Any], collected: bool) -> dict[str, Any]:
        self.calls.append("SETTLE" if collected else "PREPAY")
        response = self.client.post(
            f"/internal/v1/orders/{order['order_id']}/settlement",
            headers=_headers(),
            json={"paid_amount_vnd": TOTAL_VND, "collected_by_customer": collected},
        )
        assert response.status_code == 201, response.text
        return dict(self.client.get(f"/internal/v1/orders/{order['order_id']}").json())


# --- the walk ------------------------------------------------------------------------------------


def test_a_walk_in_from_creation_to_completed_in_six_calls(
    client: TestClient, connection: Any
) -> None:
    store_id, staff = _shop(connection)
    created = _create_order(client, connection, store_id, staff)
    order = client.get(f"/internal/v1/orders/{created['order_id']}").json()
    assert _primary(order) == "RECEIVE"
    counter = _Counter(client)

    order = counter.step(order, "RECEIVE", slot_approved=True)
    assert (order["commercial"], order["intake"]) == ("ACTIVE", "ACCEPTED")
    assert _primary(order) == "START_WASH"
    order = counter.step(order, _primary(order) or "")
    assert _primary(order) == "QUALITY_CHECK"
    order = counter.step(order, "QUALITY_CHECK")
    assert _primary(order) == "MARK_READY"
    order = counter.step(order, "MARK_READY")
    assert _primary(order) == "SETTLE"
    order = counter.settle(order, collected=True)
    assert order["settlement_shape"] == "EXACT_PAYMENT_SELF_COLLECTION"
    assert _primary(order) == "HAND_OVER"
    order = counter.step(order, "HAND_OVER")

    assert (order["commercial"], order["production"], order["balance"]) == (
        "COMPLETED",
        "RELEASED",
        "PAID",
    )
    assert order["next_steps"] == []
    assert counter.calls == [
        "RECEIVE",
        "START_WASH",
        "QUALITY_CHECK",
        "MARK_READY",
        "SETTLE",
        "HAND_OVER",
    ]
    # Six calls, and not one audited transition skipped: 5 (receive) + 2 (queue, wash) + 1 + 1
    # + 2 (release, complete) state transitions, plus the settlement's own record.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM audit_events
            WHERE aggregate_id = %s AND action = 'ORDER_STATE_TRANSITION'
            """,
            (created["order_id"],),
        )
        assert cursor.fetchone()[0] == 11


def test_a_pickup_and_return_order_is_driven_by_its_next_steps(
    client: TestClient, connection: Any
) -> None:
    store_id, staff = _shop(connection)
    created = _create_order(client, connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN)
    counter = _Counter(client)
    order = counter.step(
        client.get(f"/internal/v1/orders/{created['order_id']}").json(),
        "RECEIVE",
        slot_approved=True,
    )
    steps = {item["step"]: item for item in order["next_steps"]}
    assert "DELIVERY_PICKUP" in steps and not steps["DELIVERY_PICKUP"]["primary"]
    picked = client.post(
        f"/internal/v1/orders/{order['order_id']}/delivery-legs",
        headers=_headers(),
        json={"leg_kind": "PICKUP", "outcome": "SUCCEEDED"},
    )
    assert picked.status_code == 201
    order = client.get(f"/internal/v1/orders/{order['order_id']}").json()
    assert [leg["leg_kind"] for leg in order["delivery_legs"]] == ["PICKUP"]
    for step in ("START_WASH", "QUALITY_CHECK", "MARK_READY"):
        order = counter.step(order, step)
    assert _primary(order) == "PREPAY"
    order = counter.settle(order, collected=False)
    assert _primary(order) == "RELEASE"
    order = counter.step(order, "RELEASE")
    assert _primary(order) == "DELIVERY_RETURN"
    returned = client.post(
        f"/internal/v1/orders/{order['order_id']}/delivery-legs",
        headers=_headers(),
        json={"leg_kind": "RETURN", "outcome": "SUCCEEDED"},
    )
    assert returned.status_code == 201
    order = client.get(f"/internal/v1/orders/{order['order_id']}").json()
    assert order["required_delivery_legs_succeeded"] is True
    assert _primary(order) == "COMPLETE"
    order = counter.step(order, "COMPLETE")
    assert order["commercial"] == "COMPLETED"


# --- the step route's HTTP semantics -------------------------------------------------------------


def test_if_match_is_required_validated_and_compared(client: TestClient, connection: Any) -> None:
    store_id, staff = _shop(connection)
    order = _create_order(client, connection, store_id, staff)
    path = f"/internal/v1/orders/{order['order_id']}/steps"
    body = {"step": "RECEIVE", "slot_approved": True}

    missing = client.post(path, headers=_headers(), json=body)
    assert missing.status_code == 428
    malformed = client.post(path, headers={**_headers(), "If-Match": "abc"}, json=body)
    assert malformed.status_code == 400
    stale = client.post(path, headers=_headers(5), json=body)
    assert stale.status_code == 409
    assert stale.json()["detail"].startswith("STALE_VERSION")
    assert client.get(f"/internal/v1/orders/{order['order_id']}").json()["row_version"] == 1


def test_receive_without_the_slot_attestation_is_require_human_and_writes_nothing(
    client: TestClient, connection: Any
) -> None:
    store_id, staff = _shop(connection)
    order = _create_order(client, connection, store_id, staff)
    refused = client.post(
        f"/internal/v1/orders/{order['order_id']}/steps",
        headers=_headers(1),
        json={"step": "RECEIVE"},
    )
    assert refused.status_code == 422
    assert refused.json() == {
        "detail": {"outcome": "REQUIRE_HUMAN", "reason_codes": ["SLOT_APPROVAL_REQUIRED"]}
    }
    after = client.get(f"/internal/v1/orders/{order['order_id']}").json()
    assert (after["row_version"], after["intake"]) == (1, "AWAITING_HANDOFF")


def test_an_illegal_step_is_a_409_in_the_existing_taxonomy(
    client: TestClient, connection: Any
) -> None:
    store_id, staff = _shop(connection)
    order = _create_order(client, connection, store_id, staff)
    refused = client.post(
        f"/internal/v1/orders/{order['order_id']}/steps",
        headers=_headers(1),
        json={"step": "HAND_OVER"},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("INVALID_STATE_TRANSITION")


def test_money_steps_are_not_accepted_on_the_step_route(
    client: TestClient, connection: Any
) -> None:
    store_id, staff = _shop(connection)
    order = _create_order(client, connection, store_id, staff)
    for step in ("SETTLE", "PREPAY", "COLLECT", "DELIVERY_RETURN", "NOT_A_STEP"):
        response = client.post(
            f"/internal/v1/orders/{order['order_id']}/steps",
            headers=_headers(1),
            json={"step": step},
        )
        assert response.status_code == 422, step


def test_a_replay_answers_the_first_result(client: TestClient, connection: Any) -> None:
    store_id, staff = _shop(connection)
    order = _create_order(client, connection, store_id, staff)
    headers = _headers(1)
    path = f"/internal/v1/orders/{order['order_id']}/steps"
    first = client.post(path, headers=headers, json={"step": "RECEIVE", "slot_approved": True})
    again = client.post(path, headers=headers, json={"step": "RECEIVE", "slot_approved": True})
    changed = client.post(path, headers=headers, json={"step": "CANCEL"})

    assert first.status_code == again.status_code == 200
    assert first.json()["replayed"] is False and again.json()["replayed"] is True
    assert {**again.json(), "replayed": False} == first.json()
    assert changed.status_code == 409 and changed.json() == {"detail": "IDEMPOTENCY_CONFLICT"}


def test_a_non_member_is_refused_on_the_step_route(client: TestClient, connection: Any) -> None:
    store_id, staff = _shop(connection)
    order = _create_order(client, connection, store_id, staff)
    _other_store, outsider = _shop(connection)
    _as(outsider)
    refused = client.post(
        f"/internal/v1/orders/{order['order_id']}/steps",
        headers=_headers(1),
        json={"step": "RECEIVE", "slot_approved": True},
    )
    assert refused.status_code == 403 and refused.json() == DENIED


# --- READ-ENRICH-001 over HTTP -------------------------------------------------------------------


def test_quote_detail_and_order_request_reads_carry_the_hand_off_values(
    client: TestClient, connection: Any
) -> None:
    store_id, staff = _shop(connection)
    order = _create_order(client, connection, store_id, staff, FulfillmentMode.RETURN_ONLY)
    view = client.get(f"/internal/v1/orders/{order['order_id']}").json()
    detail = client.get(
        f"/internal/v1/stores/{store_id}/quotes/{view['quote_id']}",
        params={"revision": view["quote_revision"]},
    ).json()
    assert detail["fulfillment_mode"] == "RETURN_ONLY"
    requests = client.get(f"/internal/v1/stores/{store_id}/order-requests").json()
    (request,) = [
        item for item in requests if item["order_request_id"] == detail["order_request_id"]
    ]
    assert request["order_id"] == order["order_id"]
    assert request["contact_binding_id"] == detail["contact_binding_id"]
    assert request["ticket_number"] == view["ticket_number"]
    assert request["ticket_issued_on"] == view["ticket_issued_on"]
    one = client.get(
        f"/internal/v1/stores/{store_id}/order-requests/{detail['order_request_id']}"
    ).json()
    assert one == request


def test_order_incidents_and_one_incident_by_id(client: TestClient, connection: Any) -> None:
    store_id, staff = _shop(connection)
    order = _create_order(client, connection, store_id, staff)
    other = _create_order(client, connection, store_id, staff)
    ids = []
    for target, summary in ((order, "Áo sơ mi bị ố"), (other, "Thiếu một chiếc tất")):
        opened = client.post(
            f"/internal/v1/stores/{store_id}/incidents",
            headers=_headers(),
            json={"order_id": target["order_id"], "evidence_summary": summary},
        )
        assert opened.status_code == 201, opened.text
        ids.append(opened.json()["incident_id"])
    ticket = client.get(f"/internal/v1/orders/{order['order_id']}").json()["ticket_number"]

    mine = client.get(f"/internal/v1/stores/{store_id}/orders/{order['order_id']}/incidents")
    assert mine.status_code == 200
    assert [item["incident_id"] for item in mine.json()] == [ids[0]]
    assert mine.json()[0]["ticket_number"] == ticket
    one = client.get(f"/internal/v1/stores/{store_id}/incidents/{ids[0]}")
    assert one.status_code == 200 and one.json() == mine.json()[0]
    listed = client.get(f"/internal/v1/stores/{store_id}/incidents").json()
    assert {item["incident_id"] for item in listed} == set(ids)
    assert all(item["ticket_number"] is not None for item in listed)
    assert client.get(f"/internal/v1/stores/{store_id}/incidents/{uuid4()}").status_code == 404

    other_store, outsider = _shop(connection)
    _as(outsider)
    for path in (
        f"/internal/v1/stores/{store_id}/orders/{order['order_id']}/incidents",
        f"/internal/v1/stores/{store_id}/incidents/{ids[0]}",
    ):
        response = client.get(path)
        assert response.status_code == 403 and response.json() == DENIED, path
    # A member of their own store asking with this store's ids gets nothing, not the incident.
    assert (
        client.get(f"/internal/v1/stores/{other_store}/orders/{order['order_id']}/incidents").json()
        == []
    )
    assert client.get(f"/internal/v1/stores/{other_store}/incidents/{ids[0]}").status_code == 404


def test_the_sla_board_items_carry_the_ticket(client: TestClient, connection: Any) -> None:
    store_id, staff = _shop(connection)
    order = _create_order(client, connection, store_id, staff)
    counter = _Counter(client)
    counter.step(
        client.get(f"/internal/v1/orders/{order['order_id']}").json(), "RECEIVE", slot_approved=True
    )
    view = client.get(f"/internal/v1/orders/{order['order_id']}").json()
    board = client.get(f"/internal/v1/stores/{store_id}/sla-board")
    assert board.status_code == 200, board.text
    (item,) = [entry for entry in board.json()["items"] if entry["order_id"] == order["order_id"]]
    assert (item["ticket_number"], item["ticket_issued_on"]) == (
        view["ticket_number"],
        view["ticket_issued_on"],
    )


# --- ORDER-STEPS-002: rewash and refuse-at-intake over HTTP --------------------------------------


def _checking(client: TestClient, connection: Any) -> tuple[UUID, StaffPrincipal, dict[str, Any]]:
    """An order being checked after its first wash, driven only through the step route."""

    store_id, staff = _shop(connection)
    created = _create_order(client, connection, store_id, staff)
    counter = _Counter(client)
    order = client.get(f"/internal/v1/orders/{created['order_id']}").json()
    order = counter.step(order, "RECEIVE", slot_approved=True)
    for step in ("START_WASH", "QUALITY_CHECK"):
        order = counter.step(order, step)
    return store_id, staff, order


def _on_the_counter(
    client: TestClient, connection: Any
) -> tuple[UUID, StaffPrincipal, dict[str, Any]]:
    """Goods received on the counter and not yet accepted (the per-axis intake move)."""

    store_id, staff = _shop(connection)
    created = _create_order(client, connection, store_id, staff)
    moved = client.post(
        f"/internal/v1/orders/{created['order_id']}/intake-transition",
        headers=_headers(1),
        json={"target": "RECEIVED_PENDING_INSPECTION"},
    )
    assert moved.status_code == 200, moved.text
    return store_id, staff, client.get(f"/internal/v1/orders/{created['order_id']}").json()


def _named_history(client: TestClient, store_id: UUID, order_id: str) -> list[tuple[Any, ...]]:
    history = client.get(f"/internal/v1/stores/{store_id}/shadow/audit/{order_id}")
    assert history.status_code == 200, history.text
    return [
        (entry["transition_target"], entry["transition_step"], entry["transition_reason"])
        for entry in history.json()
        if entry["action"] == "ORDER_STATE_TRANSITION"
    ]


def test_rewash_is_listed_with_its_reasons_and_never_primary(
    client: TestClient, connection: Any
) -> None:
    _store, _staff, order = _checking(client, connection)
    (rewash,) = [item for item in order["next_steps"] if item["step"] == "REWASH"]
    assert rewash == {
        "step": "REWASH",
        "primary": False,
        "requires": ["rewash_reason"],
        "custody_resolutions": [],
        "rewash_reasons": ["NOT_CLEAN", "MACHINE_FAULT", "OTHER"],
        "rejection_reasons": [],
    }
    assert _primary(order) == "MARK_READY"


def test_a_stain_at_quality_check_is_washed_again_and_the_order_still_completes(
    client: TestClient, connection: Any
) -> None:
    store_id, _staff, order = _checking(client, connection)
    counter = _Counter(client)
    order = counter.step(order, "REWASH", rewash_reason="NOT_CLEAN")
    assert order["production"] == "IN_PROCESS"
    assert order["payable_total_vnd"] == TOTAL_VND
    assert _primary(order) == "QUALITY_CHECK"
    for step in ("QUALITY_CHECK", "MARK_READY"):
        order = counter.step(order, step)
    order = counter.settle(order, collected=True)
    order = counter.step(order, "HAND_OVER")
    assert (order["commercial"], order["balance"]) == ("COMPLETED", "PAID")

    named = [row for row in _named_history(client, store_id, order["order_id"]) if row[1]]
    assert named == [("EXCEPTION", "REWASH", "NOT_CLEAN")]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT a.details ->> 'rewash_reason', e.payload ->> 'rewash_reason'
            FROM audit_events a
            JOIN domain_events e
              ON e.aggregate_id = a.aggregate_id AND e.correlation_id = a.correlation_id
             AND e.occurred_at = a.occurred_at
            WHERE a.aggregate_id = %s AND a.action = 'ORDER_STATE_TRANSITION'
              AND e.payload ->> 'target' = 'EXCEPTION'
            """,
            (order["order_id"],),
        )
        assert cursor.fetchall() == [("NOT_CLEAN", "NOT_CLEAN")]


def test_goods_refused_on_the_counter_close_the_order_with_nothing_left_to_do(
    client: TestClient, connection: Any
) -> None:
    store_id, _staff, order = _on_the_counter(client, connection)
    (reject,) = [item for item in order["next_steps"] if item["step"] == "REJECT_INTAKE"]
    assert reject["requires"] == ["rejection_reason"] and not reject["primary"]
    assert reject["rejection_reasons"] == ["NOT_SERVICEABLE", "DAMAGED_ON_ARRIVAL", "OTHER"]
    assert _primary(order) == "RECEIVE"

    order = _Counter(client).step(order, "REJECT_INTAKE", rejection_reason="NOT_SERVICEABLE")
    assert (order["commercial"], order["intake"], order["balance"]) == (
        "CANCELLED",
        "REJECTED",
        "UNPAID",
    )
    assert order["next_steps"] == []
    assert _named_history(client, store_id, order["order_id"])[-2:] == [
        ("REJECTED", "REJECT_INTAKE", "NOT_SERVICEABLE"),
        ("CANCELLED", None, None),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"step": "REWASH"},
        {"step": "REWASH", "rewash_reason": "DIRTY"},
        {"step": "REWASH", "rewash_reason": "NOT_CLEAN", "rejection_reason": "OTHER"},
        {
            "step": "REWASH",
            "rewash_reason": "NOT_CLEAN",
            "custody_resolution": "SHOP_FAULT_NO_CHARGE",
        },
        {"step": "MARK_READY", "rewash_reason": "NOT_CLEAN"},
        {"step": "REJECT_INTAKE"},
        {"step": "REJECT_INTAKE", "rejection_reason": "NOT_CLEAN"},
        {"step": "CANCEL", "rejection_reason": "OTHER"},
    ],
)
def test_a_missing_or_misplaced_reason_is_422_and_writes_nothing(
    client: TestClient, connection: Any, body: dict[str, Any]
) -> None:
    _store, _staff, order = _checking(client, connection)
    response = client.post(
        f"/internal/v1/orders/{order['order_id']}/steps",
        headers=_headers(order["row_version"]),
        json=body,
    )
    assert response.status_code == 422, response.text
    after = client.get(f"/internal/v1/orders/{order['order_id']}").json()
    assert (after["row_version"], after["production"]) == (order["row_version"], "QUALITY_CHECK")


def test_a_rewash_is_version_checked_replayed_and_conflicts_on_a_changed_reason(
    client: TestClient, connection: Any
) -> None:
    _store, _staff, order = _checking(client, connection)
    path = f"/internal/v1/orders/{order['order_id']}/steps"
    body = {"step": "REWASH", "rewash_reason": "MACHINE_FAULT"}

    assert client.post(path, headers=_headers(), json=body).status_code == 428
    stale = client.post(path, headers=_headers(order["row_version"] - 1), json=body)
    assert stale.status_code == 409 and stale.json()["detail"].startswith("STALE_VERSION")

    headers = _headers(order["row_version"])
    first = client.post(path, headers=headers, json=body)
    again = client.post(path, headers=headers, json=body)
    changed = client.post(path, headers=headers, json={**body, "rewash_reason": "OTHER"})
    assert first.status_code == again.status_code == 200
    assert (first.json()["replayed"], again.json()["replayed"]) == (False, True)
    assert {**again.json(), "replayed": False} == first.json()
    assert changed.status_code == 409 and changed.json() == {"detail": "IDEMPOTENCY_CONFLICT"}
    assert first.json()["row_version"] == order["row_version"] + 2


def test_a_refusal_is_version_checked_and_replayed(client: TestClient, connection: Any) -> None:
    _store, _staff, order = _on_the_counter(client, connection)
    path = f"/internal/v1/orders/{order['order_id']}/steps"
    body = {"step": "REJECT_INTAKE", "rejection_reason": "DAMAGED_ON_ARRIVAL"}
    stale = client.post(path, headers=_headers(order["row_version"] + 3), json=body)
    assert stale.status_code == 409 and stale.json()["detail"].startswith("STALE_VERSION")
    headers = _headers(order["row_version"])
    first = client.post(path, headers=headers, json=body)
    again = client.post(path, headers=headers, json=body)
    assert first.status_code == again.status_code == 200
    assert again.json()["replayed"] is True
    assert {**again.json(), "replayed": False} == first.json()


def test_rewash_and_refusal_are_409_where_the_domain_refuses_them(
    client: TestClient, connection: Any
) -> None:
    store_id, staff = _shop(connection)
    order = _create_order(client, connection, store_id, staff)
    path = f"/internal/v1/orders/{order['order_id']}/steps"
    # Nothing received: nothing to refuse, and nothing washed to rewash.
    for body in (
        {"step": "REJECT_INTAKE", "rejection_reason": "OTHER"},
        {"step": "REWASH", "rewash_reason": "OTHER"},
    ):
        refused = client.post(path, headers=_headers(1), json=body)
        assert refused.status_code == 409, body
        assert refused.json()["detail"].startswith("INVALID_STATE_TRANSITION")
    assert client.get(f"/internal/v1/orders/{order['order_id']}").json()["row_version"] == 1


def test_a_non_member_cannot_rewash(client: TestClient, connection: Any) -> None:
    _store, _staff, order = _checking(client, connection)
    _other, outsider = _shop(connection)
    _as(outsider)
    refused = client.post(
        f"/internal/v1/orders/{order['order_id']}/steps",
        headers=_headers(order["row_version"]),
        json={"step": "REWASH", "rewash_reason": "NOT_CLEAN"},
    )
    assert refused.status_code == 403 and refused.json() == DENIED
