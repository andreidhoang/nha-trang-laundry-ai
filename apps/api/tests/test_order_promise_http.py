"""`PROMISE-001` (`DEC-037`) over HTTP against real PostgreSQL.

The routes at wall time: before the owner publishes, nothing refuses and nothing is promised; after,
`RECEIVE` stores the promise or refuses `PROMISE_REQUIRED` for a special item, the order read and
the board carry it, and *Hẹn lại* is a versioned, idempotent, store-scoped write with its refusals.
Exact instants are the domain's and the repository's tests; here the clock is the server's.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_operations_service,
    get_ops_board_service,
    get_promise_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_api.ops_board import OpsBoardService
from nha_trang_laundry_api.promises import PromiseService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository
from nha_trang_laundry_db.promise_policy import publish_turnaround_policy
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode, Unit
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv
from nha_trang_laundry_domain.promise import parse_turnaround_policy
from nha_trang_laundry_domain.turnaround_source import build_turnaround_policy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from quote_test_data import FixtureLine, accepted_quote

VN = ZoneInfo("Asia/Ho_Chi_Minh")
TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
DENIED = {"detail": "operation denied"}

STANDARD = FixtureLine("line-0", "STANDARD_WASH_DRY", Unit.KG, "5", 25_000, 125_000)
BLANKET = FixtureLine("line-1", "BED_BLANKET", Unit.KG, "3", 30_000, 90_000)
PLUSH = FixtureLine("line-2", "OTHER_PLUSH", Unit.ANIMAL_PLUSH_ITEM, "1", None, 50_000)


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
    app.dependency_overrides[get_promise_service] = lambda: PromiseService(settings)
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


def _shop(connection: Any) -> tuple[UUID, StaffPrincipal, StaffPrincipal]:
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
    return store_id, owner, operator


def _document() -> dict[str, Any]:
    """The real document, with Tết published for this year and the next.

    The routes run at wall time; a test run in January or February of a year whose Tết is not
    published would (correctly) be asked for a person's time. This year's and next year's days are
    fixture dates inside the window -- the one place a test states Tết days.
    """

    pricebook = (TEMPLATES / "services-pricebook.csv").read_bytes()
    book = import_pricebook_csv(pricebook)
    year = datetime.now(VN).year
    document = build_turnaround_policy(
        sla_csv=(TEMPLATES / "service-sla.csv").read_text(encoding="utf-8"),
        calendar_csv=(TEMPLATES / "business-calendar-rules.csv").read_text(encoding="utf-8"),
        services=[(service.code, service.category) for service in book.services],
        tet_dates=[date(year, 2, day) for day in range(1, 7)],
        pricebook_sha256=hashlib.sha256(pricebook).hexdigest(),
    )
    document["tet"][str(year + 1)] = [date(year + 1, 2, day).isoformat() for day in range(1, 7)]
    parse_turnaround_policy(document)
    return document


def _publish(connection: Any, owner: StaffPrincipal) -> None:
    publish_turnaround_policy(connection, actor_id=owner.staff_user_id, payload=_document())


def _open_day_at(hour: int, days_ahead: int = 2) -> datetime:
    """An instant in opening hours on an open day, `days_ahead` or more days from now."""

    policy = parse_turnaround_policy(_document())
    day = datetime.now(VN).date() + timedelta(days=days_ahead)
    while policy.is_closed(day):
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, tzinfo=VN)


def _order(
    connection: Any, store_id: UUID, staff: StaffPrincipal, lines: tuple[FixtureLine, ...]
) -> UUID:
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, lines=lines
    )
    return (
        OrderRepository()
        .create(
            connection,
            CreateOrderCommand(
                store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                datetime.now(UTC),
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )


def _headers(version: int | None = None, key: str | None = None) -> dict[str, str]:
    headers = {"Idempotency-Key": key or f"key-{uuid4().hex}"}
    if version is not None:
        headers["If-Match"] = str(version)
    return headers


def _read(client: TestClient, order_id: UUID) -> dict[str, Any]:
    response = client.get(f"/internal/v1/orders/{order_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _receive(client: TestClient, order_id: UUID, **body: Any) -> Any:
    order = _read(client, order_id)
    return client.post(
        f"/internal/v1/orders/{order_id}/steps",
        headers=_headers(order["row_version"]),
        json={"step": "RECEIVE", "slot_approved": True, **body},
    )


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_before_publication_nothing_refuses_and_nothing_is_promised(
    client: TestClient, connection: psycopg.Connection[Any]
) -> None:
    store_id, _owner, operator = _shop(connection)
    order_id = _order(connection, store_id, operator, (PLUSH,))
    _as(operator)
    promise = client.get(f"/internal/v1/orders/{order_id}/promise").json()
    assert promise["policy_published"] is False and promise["options"] is None

    received = _receive(client, order_id)
    assert received.status_code == 200, received.text
    body = received.json()
    assert body["promised_ready_at"] is None and body["promise_state"] is None

    refused = client.post(
        f"/internal/v1/orders/{order_id}/promise",
        headers=_headers(body["row_version"]),
        json={"promise_at": _open_day_at(10).isoformat(), "reason": "WORKLOAD"},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == {
        "reason_code": "TURNAROUND_POLICY_UNPUBLISHED",
        "decision": "DEC-037",
    }


def test_after_publication_receive_stores_the_promise_the_read_offered(
    client: TestClient, connection: psycopg.Connection[Any]
) -> None:
    store_id, owner, operator = _shop(connection)
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD, BLANKET))
    _as(operator)
    offered = client.get(f"/internal/v1/orders/{order_id}/promise").json()
    assert offered["policy_published"] is True
    assert (offered["opens_at"], offered["closes_at"]) == ("08:00", "20:00")
    options = offered["options"]
    assert options["requirement"] == "RANGE_CHOICE" and options["default_choice"] == "H48"
    assert [item["choice"] for item in options["choices"]] == ["H24", "H48", "CUSTOM"]
    h24 = _instant(options["choices"][0]["promised_at"])

    received = _receive(client, order_id, promise_choice="H24")
    assert received.status_code == 200, received.text
    body = received.json()
    stored = _instant(body["promised_ready_at"])
    # Pressed a moment after the read: the same promise, or a minute later at most.
    assert timedelta(0) <= stored - h24 <= timedelta(minutes=1)
    assert body["current_promise_at"] == body["promised_ready_at"]
    assert (body["promise_basis"], body["promise_rule_id"]) == ("H24", "SLA_BLANKETS_SHEETS")
    assert body["promise_state"] in {"ON_TRACK", "DUE_SOON"}
    after = client.get(f"/internal/v1/orders/{order_id}/promise").json()
    assert after["options"] is None and after["promised_ready_at"] == body["promised_ready_at"]

    listed = client.get(f"/internal/v1/stores/{store_id}/orders").json()
    (item,) = [entry for entry in listed if entry["order_id"] == str(order_id)]
    assert item["current_promise_at"] == body["current_promise_at"]
    assert item["promise_state"] == body["promise_state"]


def test_a_special_item_needs_a_person_and_takes_their_time(
    client: TestClient, connection: psycopg.Connection[Any]
) -> None:
    store_id, owner, operator = _shop(connection)
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD, PLUSH))
    _as(operator)
    offered = client.get(f"/internal/v1/orders/{order_id}/promise").json()["options"]
    assert offered["requirement"] == "CUSTOM"
    assert offered["human_service_codes"] == ["OTHER_PLUSH"]

    refused = _receive(client, order_id)
    assert refused.status_code == 422
    assert refused.json()["detail"] == {
        "outcome": "REQUIRE_HUMAN",
        "reason_codes": ["PROMISE_REQUIRED", "HUMAN_ETA_REQUIRED"],
    }
    chosen = _open_day_at(10)
    received = _receive(client, order_id, promise_choice="CUSTOM", custom_at=chosen.isoformat())
    assert received.status_code == 200, received.text
    assert _instant(received.json()["promised_ready_at"]) == chosen
    assert received.json()["promise_rule_id"] == "STAFF_SET"

    outside = _order(connection, store_id, operator, (PLUSH,))
    late_night = _receive(
        client,
        outside,
        promise_choice="CUSTOM",
        custom_at=_open_day_at(21).isoformat(),
    )
    assert late_night.status_code == 422
    assert late_night.json()["detail"] == {
        "reason_code": "PROMISE_OUTSIDE_OPENING_HOURS",
        "decision": "DEC-037",
    }


@pytest.mark.parametrize(
    "body",
    [
        {"step": "START_WASH", "promise_choice": "H24"},
        {"step": "RECEIVE", "slot_approved": True, "promise_choice": "CUSTOM"},
        {"step": "RECEIVE", "slot_approved": True, "custom_at": "2026-09-30T10:00:00+07:00"},
        {
            "step": "RECEIVE",
            "slot_approved": True,
            "promise_choice": "CUSTOM",
            "custom_at": "2026-09-30T10:00:00",
        },
    ],
)
def test_a_malformed_promise_request_is_422_before_anything_runs(
    client: TestClient, connection: psycopg.Connection[Any], body: dict[str, Any]
) -> None:
    store_id, _owner, operator = _shop(connection)
    order_id = _order(connection, store_id, operator, (STANDARD,))
    _as(operator)
    order = _read(client, order_id)
    response = client.post(
        f"/internal/v1/orders/{order_id}/steps", headers=_headers(order["row_version"]), json=body
    )
    assert response.status_code == 422
    assert _read(client, order_id)["row_version"] == order["row_version"]


def test_hen_lai_is_versioned_idempotent_and_keeps_the_first_promise(
    client: TestClient, connection: psycopg.Connection[Any]
) -> None:
    store_id, owner, operator = _shop(connection)
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD,))
    _as(operator)
    body = _receive(client, order_id).json()
    first = body["promised_ready_at"]
    new_at = _open_day_at(18, days_ahead=3)

    missing = client.post(
        f"/internal/v1/orders/{order_id}/promise",
        headers=_headers(),
        json={"promise_at": new_at.isoformat(), "reason": "WORKLOAD"},
    )
    assert missing.status_code == 428

    headers = _headers(body["row_version"], key="hen-lai-1")
    request = {"promise_at": new_at.isoformat(), "reason": "OTHER", "note": "Máy sấy hỏng"}
    moved = client.post(f"/internal/v1/orders/{order_id}/promise", headers=headers, json=request)
    assert moved.status_code == 200, moved.text
    view = moved.json()
    assert view["promised_ready_at"] == first
    assert _instant(view["current_promise_at"]) == new_at
    assert view["row_version"] == body["row_version"] + 1
    replay = client.post(f"/internal/v1/orders/{order_id}/promise", headers=headers, json=request)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["current_promise_at"] == view["current_promise_at"]

    stale = client.post(
        f"/internal/v1/orders/{order_id}/promise",
        headers=_headers(body["row_version"]),
        json={"promise_at": _open_day_at(17, days_ahead=3).isoformat(), "reason": "WORKLOAD"},
    )
    assert stale.status_code == 409

    history = client.get(f"/internal/v1/orders/{order_id}/promise").json()
    assert history["change_count"] == 1 and history["truncated"] is False
    assert [(item["reason_code"], item["note"]) for item in history["changes"]] == [
        ("OTHER", "Máy sấy hỏng")
    ]

    auditor = _person(connection, StaffRole.AUDITOR)
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=auditor.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    _as(auditor)
    assert client.get(f"/internal/v1/orders/{order_id}/promise").status_code == 200
    denied = client.post(
        f"/internal/v1/orders/{order_id}/promise",
        headers=_headers(view["row_version"]),
        json={"promise_at": new_at.isoformat(), "reason": "WORKLOAD"},
    )
    assert denied.status_code == 403 and denied.json() == DENIED


def test_the_promise_is_store_scoped(
    client: TestClient, connection: psycopg.Connection[Any]
) -> None:
    store_id, owner, operator = _shop(connection)
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD,))
    _as(operator)
    body = _receive(client, order_id).json()
    _other_store, _other_owner, stranger = _shop(connection)
    _as(stranger)
    assert client.get(f"/internal/v1/orders/{order_id}/promise").status_code == 404
    assert client.get(f"/internal/v1/orders/{uuid4()}/promise").status_code == 404
    refused = client.post(
        f"/internal/v1/orders/{order_id}/promise",
        headers=_headers(body["row_version"]),
        json={"promise_at": _open_day_at(18).isoformat(), "reason": "WORKLOAD"},
    )
    assert refused.status_code == 403 and refused.json() == DENIED


def test_the_board_ranks_by_the_promise_and_says_so(
    client: TestClient, connection: psycopg.Connection[Any]
) -> None:
    store_id, owner, operator = _shop(connection)
    unpromised = _order(connection, store_id, operator, (STANDARD,))
    _as(operator)
    assert _receive(client, unpromised).status_code == 200
    _publish(connection, owner)
    promised = _order(connection, store_id, operator, (BLANKET,))
    assert _receive(client, promised).status_code == 200

    board = client.get(f"/internal/v1/stores/{store_id}/sla-board?limit=1").json()
    (row,) = board["items"]
    # Whatever the wall clock: the stated rule's mark is eight hours after acceptance, and a
    # blanket's promise is at least 48 calendar hours away -- so the blanket ranks second.
    assert (row["order_id"], row["rule_source"], row["promise_rule_id"]) == (
        str(unpromised),
        "STATED_RULE",
        None,
    )
    assert board["next_due_at"] == row["due_at"] and board["next_order_id"] == str(unpromised)
    rest = client.get(
        f"/internal/v1/stores/{store_id}/sla-board",
        params={"after_due_at": board["next_due_at"], "after_order_id": board["next_order_id"]},
    ).json()
    (second,) = rest["items"]
    assert (second["order_id"], second["rule_source"], second["promise_rule_id"]) == (
        str(promised),
        "ORDER_PROMISE",
        "SLA_BLANKETS_SHEETS",
    )
    assert second["due_at"] == second["internal_risk_due_at"]
    assert "giờ hẹn" in board["policy_notice_vi"]
