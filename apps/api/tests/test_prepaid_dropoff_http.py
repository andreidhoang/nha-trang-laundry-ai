"""`PREPAID-DROPOFF-001` over HTTP against real PostgreSQL: the new pickup route, end to end.

`POST /internal/v1/orders/{order_id}/collection` records that the customer who paid at drop-off
(`DEC-032`) has taken their laundry. It takes no body: the staff member comes from the session, the
store from the order row, and the row version from `If-Match`. These tests drive the counter's real
sequence through the served routes -- pay at drop-off, wash, hand over, complete -- and then the
refusals a route owes: no `If-Match`, a stale one, a replayed key, a reused key with a changed
precondition, and a member of another shop.
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
from nha_trang_laundry_api.main import app, current_principal, get_operations_service
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository, OrderTransitionCommand
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from quote_test_data import accepted_quote

READY = IntakeReadiness(True, True, True, True, True, True)
QUOTED_TOTAL = 110_000
CSRF = "p" * 40
ORIGIN = "http://testserver"


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


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _staff(connection: Any, role: StaffRole) -> StaffPrincipal:
    staff_user_id = uuid4()
    now = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên thử nghiệm', 'ACTIVE', %s)
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


def _store(connection: Any) -> tuple[UUID, StaffPrincipal]:
    store_id = uuid4()
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
    )
    owner = _staff(connection, StaffRole.OWNER_ADMIN)
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=owner.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    connection.commit()
    return store_id, owner


def _member(connection: Any, store_id: UUID, owner: StaffPrincipal, role: StaffRole) -> Any:
    member = _staff(connection, role)
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=member.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    connection.commit()
    return member


def _dropped_off(connection: Any, store_id: UUID, staff: StaffPrincipal) -> tuple[UUID, int]:
    """A walk-in order taken in and running, laundry not yet washed."""

    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff
    )
    stored = OrderRepository().create(
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
    version = stored.row_version
    for step in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "production_accepted_at": datetime.now(UTC),
            "intake_readiness": READY,
        },
        {"commercial_target": CommercialOrderStatus.STORE_CONFIRMATION_PENDING},
        {"commercial_target": CommercialOrderStatus.CONFIRMED},
        {"commercial_target": CommercialOrderStatus.ACTIVE},
    ):
        version = (
            OrderRepository()
            .transition(
                connection,
                OrderTransitionCommand(
                    stored.order_id, version, staff, f"step-{uuid4().hex}", uuid4(), **step
                ),
            )
            .row_version
        )
    connection.commit()
    return stored.order_id, version


def _post(
    client: TestClient,
    path: str,
    body: dict[str, object] | None = None,
    *,
    key: str | None = None,
    if_match: int | None = None,
) -> Any:
    headers = {
        "Origin": ORIGIN,
        "X-CSRF-Token": CSRF,
        "Idempotency-Key": key or f"http-{uuid4().hex}",
    }
    if if_match is not None:
        headers["If-Match"] = f'"{if_match}"'
    return client.post(path, headers=headers, json=body)


def _wash(client: TestClient, order_id: UUID, version: int) -> int:
    for target in (
        ProductionStatus.QUEUED,
        ProductionStatus.IN_PROCESS,
        ProductionStatus.QUALITY_CHECK,
        ProductionStatus.READY_AT_STORE,
        ProductionStatus.RELEASED,
    ):
        moved = _post(
            client,
            f"/internal/v1/orders/{order_id}/production-transition",
            {"target": target.value},
            if_match=version,
        )
        assert moved.status_code == 200, moved.text
        version = int(moved.json()["row_version"])
    return version


def test_pay_at_drop_off_then_pickup_then_complete_over_http(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id, owner = _store(connection)
    morning = _member(connection, store_id, owner, StaffRole.OPERATOR)
    evening = _member(connection, store_id, owner, StaffRole.OPERATOR)
    order_id, _ = _dropped_off(connection, store_id, morning)

    _as(morning)
    paid = _post(
        client,
        f"/internal/v1/orders/{order_id}/settlement",
        {"paid_amount_vnd": QUOTED_TOTAL, "collected_by_customer": False},
    )
    assert paid.status_code == 201, paid.text
    assert paid.json()["settlement_shape"] == "EXACT_PAYMENT_PREPAID_SELF_COLLECTION"
    assert paid.json()["self_collection_recorded"] is False
    read = client.get(f"/internal/v1/orders/{order_id}").json()
    assert (read["balance"], read["self_collection_recorded"]) == ("PAID", False)

    version = _wash(client, order_id, int(paid.json()["row_version"]))

    _as(evening)
    key = f"pickup-{uuid4().hex}"
    collected = _post(
        client, f"/internal/v1/orders/{order_id}/collection", key=key, if_match=version
    )
    assert collected.status_code == 201, collected.text
    body = collected.json()
    assert body["order_id"] == str(order_id)
    assert body["collected_by_staff_id"] == str(evening.staff_user_id)
    assert body["self_collection_recorded"] is True
    assert body["row_version"] == version + 1
    assert body["replayed"] is False

    # Same key, same precondition: the prior result, not a second pickup.
    again = _post(client, f"/internal/v1/orders/{order_id}/collection", key=key, if_match=version)
    assert again.status_code == 201, again.text
    assert again.json()["collection_id"] == body["collection_id"]
    assert again.json()["replayed"] is True
    # Same key, changed precondition: a conflict, never a silent replay of something else.
    changed = _post(
        client, f"/internal/v1/orders/{order_id}/collection", key=key, if_match=version + 1
    )
    assert changed.status_code == 409
    assert changed.json()["detail"] == "IDEMPOTENCY_CONFLICT"

    read = client.get(f"/internal/v1/orders/{order_id}").json()
    assert read["self_collection_recorded"] is True
    done = _post(
        client,
        f"/internal/v1/orders/{order_id}/transition",
        {"target": "COMPLETED"},
        if_match=int(read["row_version"]),
    )
    assert done.status_code == 200, done.text
    assert done.json()["commercial"] == "COMPLETED"


def test_the_collection_route_refuses_what_it_must(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id, owner = _store(connection)
    staff = _member(connection, store_id, owner, StaffRole.OPERATOR)
    order_id, version = _dropped_off(connection, store_id, staff)
    path = f"/internal/v1/orders/{order_id}/collection"
    _as(staff)

    # Not paid yet.
    unpaid = _post(client, path, if_match=version)
    assert unpaid.status_code == 422
    assert unpaid.json()["detail"]["reason_code"] == "COLLECTION_REQUIRES_PAYMENT"

    paid = _post(
        client,
        f"/internal/v1/orders/{order_id}/settlement",
        {"paid_amount_vnd": QUOTED_TOTAL, "collected_by_customer": False},
    )
    version = int(paid.json()["row_version"])

    # Paid, but the laundry has not been washed.
    unwashed = _post(client, path, if_match=version)
    assert unwashed.status_code == 422
    assert unwashed.json()["detail"] == {
        "outcome": "NOT_SUPPORTED",
        "reason_code": "GOODS_NOT_READY_FOR_HANDOVER",
        "decision": None,
    }

    # The precondition header is required, and a stale one is a conflict.
    missing = client.post(
        path, headers={"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": "k-1"}
    )
    assert missing.status_code == 428
    version = _wash(client, order_id, version)
    stale = _post(client, path, if_match=version - 1)
    assert stale.status_code == 409
    assert str(stale.json()["detail"]).startswith("STALE_VERSION")

    # A member of another shop learns nothing and changes nothing.
    _other_store, other_owner = _store(connection)
    _as(other_owner)
    assert _post(client, path, if_match=version).status_code == 403
    _as(staff)
    assert client.get(f"/internal/v1/orders/{order_id}").json()["self_collection_recorded"] is False


def test_a_deposit_at_drop_off_is_refused_over_http(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id, owner = _store(connection)
    staff = _member(connection, store_id, owner, StaffRole.OPERATOR)
    order_id, _ = _dropped_off(connection, store_id, staff)
    _as(staff)

    deposit = _post(
        client,
        f"/internal/v1/orders/{order_id}/settlement",
        {"paid_amount_vnd": QUOTED_TOTAL // 2, "collected_by_customer": False},
    )
    assert deposit.status_code == 422
    assert deposit.json()["detail"] == {
        "outcome": "NOT_SUPPORTED",
        "reason_code": "AMOUNT_IS_NOT_THE_EXACT_TOTAL",
        "decision": "DEC-010",
    }
    assert client.get(f"/internal/v1/orders/{order_id}").json()["balance"] == "UNPAID"
