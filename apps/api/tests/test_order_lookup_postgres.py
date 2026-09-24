"""`ORDER-LOOKUP-001` over HTTP against real PostgreSQL.

The repository tests prove which rows are read. These prove the counter can reach them: that the
read by id is a served route with the store taken from the row, that its refusal is opaque, that
"phiếu số 1" means today's number 1 on the server's clock, and that what the read reports is enough
to drive the next command -- a transition's `If-Match` and a settlement's exact amount.
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
from nha_trang_laundry_api.main import app, current_principal, get_operations_service
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.counter_tickets import ticket_business_date
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness

# See `test_ops_board_postgres.py`: the real quote chain lives beside the repository tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from quote_test_data import accepted_quote, bulk_newer_orders

READY = IntakeReadiness(True, True, True, True, True, True)
CSRF = "y" * 40
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


def _staff(connection: Any, *, roles: frozenset[StaffRole]) -> StaffPrincipal:
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
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_user_id, role.value, now),
            )
    return StaffPrincipal(
        staff_user_id=staff_user_id,
        oidc_subject=f"oidc-{staff_user_id}",
        roles=roles,
        mfa_verified=True,
        session_id=uuid4(),
    )


def _store(connection: Any) -> tuple[UUID, StaffPrincipal]:
    store_id = uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )
    owner = _staff(connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
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
    member = _staff(connection, roles=frozenset({role}))
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=member.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    connection.commit()
    return member


def _order(
    connection: Any,
    store_id: UUID,
    staff: StaffPrincipal,
    *,
    created_at: datetime | None = None,
    ticket_issued_at: datetime | None = None,
) -> tuple[UUID, UUID, int]:
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, ticket_issued_at=ticket_issued_at
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
            created_at or datetime.now(UTC),
            AcquisitionSource.WALK_IN,
        ),
        evaluated_at=created_at,
    )
    connection.commit()
    return stored.order_id, quote_id, revision


def _post(client: TestClient, path: str, body: dict[str, object], **headers: str) -> Any:
    return client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": CSRF,
            "Idempotency-Key": f"http-{uuid4().hex}",
            **headers,
        },
        json=body,
    )


def test_order_101_is_readable_by_id_and_its_version_drives_a_transition(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id, owner = _store(connection)
    operator = _member(connection, store_id, owner, StaffRole.OPERATOR)
    target, quote_id, revision = _order(connection, store_id, operator)
    bulk_newer_orders(
        connection, store_id, operator, count=100, after=datetime.now(UTC), commercial="REQUESTED"
    )
    connection.commit()
    _as(operator)

    board = client.get(f"/internal/v1/stores/{store_id}/orders", params={"limit": 100})
    assert board.status_code == 200
    assert str(target) not in {item["order_id"] for item in board.json()}

    read = client.get(f"/internal/v1/orders/{target}")
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["order_id"] == str(target)
    assert body["store_id"] == str(store_id)
    assert body["commercial"] == "REQUESTED"
    assert body["row_version"] == 1
    assert body["replayed"] is False
    assert body["ticket_number"] == 1
    assert body["ticket_issued_on"] == ticket_business_date(datetime.now(UTC)).isoformat()
    assert body["quote_id"] == str(quote_id)
    assert body["quote_revision"] == revision
    assert body["payable_total_vnd"] == 110_000
    assert body["fulfillment_mode"] == "SELF_DROP_SELF_COLLECT"

    moved = _post(
        client,
        f"/internal/v1/orders/{target}/transition",
        {"target": "STORE_CONFIRMATION_PENDING"},
        **{"If-Match": f'"{body["row_version"]}"'},
    )
    assert moved.status_code == 200, moved.text
    assert client.get(f"/internal/v1/orders/{target}").json()["row_version"] == 2


def test_another_stores_staff_get_the_same_answer_as_for_an_order_that_does_not_exist(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    home, home_owner = _store(connection)
    _elsewhere, elsewhere_owner = _store(connection)
    operator = _member(connection, home, home_owner, StaffRole.OPERATOR)
    order_id, _, _ = _order(connection, home, operator)

    _as(elsewhere_owner)
    refused = client.get(f"/internal/v1/orders/{order_id}")
    missing = client.get(f"/internal/v1/orders/{uuid4()}")
    assert refused.status_code == missing.status_code == 404
    assert refused.json() == missing.json()
    assert str(order_id) not in refused.text

    # A role that may not read orders at all is refused before anything is looked up.
    driver = _member(connection, home, home_owner, StaffRole.DRIVER)
    _as(driver)
    assert client.get(f"/internal/v1/orders/{order_id}").status_code == 403


def test_ticket_lookup_uses_todays_business_date_unless_a_date_is_named(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id, owner = _store(connection)
    operator = _member(connection, store_id, owner, StaffRole.OPERATOR)
    now = datetime.now(UTC)
    yesterday, _, _ = _order(
        connection, store_id, operator, ticket_issued_at=now - timedelta(days=1)
    )
    today, _, _ = _order(connection, store_id, operator)
    _as(operator)

    found = client.get(f"/internal/v1/stores/{store_id}/orders", params={"ticket": 1})
    assert found.status_code == 200, found.text
    assert [item["order_id"] for item in found.json()] == [str(today)]

    earlier = ticket_business_date(now - timedelta(days=1)).isoformat()
    named = client.get(
        f"/internal/v1/stores/{store_id}/orders", params={"ticket": 1, "ticket_date": earlier}
    )
    assert [item["order_id"] for item in named.json()] == [str(yesterday)]
    assert named.json()[0]["ticket_issued_on"] == earlier

    none = client.get(f"/internal/v1/stores/{store_id}/orders", params={"ticket": 2})
    assert none.status_code == 200 and none.json() == []
    assert (
        client.get(f"/internal/v1/stores/{store_id}/orders", params={"ticket": 0}).status_code
        == 422
    )


def test_the_open_filter_keeps_an_old_active_order_on_the_board(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    store_id, owner = _store(connection)
    operator = _member(connection, store_id, owner, StaffRole.OPERATOR)
    month_ago = datetime.now(UTC) - timedelta(days=30)
    old, _, _ = _order(connection, store_id, operator, created_at=month_ago)
    version = 1
    for step in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "production_accepted_at": month_ago,
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
                OrderTransitionCommand(old, version, operator, f"s-{uuid4().hex}", uuid4(), **step),
            )
            .row_version
        )
    bulk_newer_orders(
        connection, store_id, operator, count=120, after=month_ago, commercial="CANCELLED"
    )
    connection.commit()
    _as(operator)

    default = client.get(f"/internal/v1/stores/{store_id}/orders", params={"limit": 100}).json()
    assert str(old) not in {item["order_id"] for item in default}
    still_open = client.get(
        f"/internal/v1/stores/{store_id}/orders", params={"limit": 100, "open": "true"}
    )
    assert still_open.status_code == 200
    assert [item["order_id"] for item in still_open.json()] == [str(old)]
    assert still_open.json()[0]["commercial"] == "ACTIVE"
    assert still_open.json()[0]["row_version"] == version


def test_the_quote_read_reports_when_the_customer_agreed_the_revision(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """What the order form's "Thời điểm khách chốt giá" is prefilled from: the server's record."""
    store_id, owner = _store(connection)
    operator = _member(connection, store_id, owner, StaffRole.OPERATOR)
    quote_id, revision, _, _ = accepted_quote(connection, store_id=store_id, principal=operator)
    connection.commit()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT accepted_at FROM quote_acceptances WHERE quote_id = %s AND final_revision = %s",
            (quote_id, revision),
        )
        recorded = cursor.fetchone()
    assert recorded is not None
    _as(operator)

    accepted = client.get(
        f"/internal/v1/stores/{store_id}/quotes/{quote_id}", params={"revision": revision}
    )
    assert accepted.status_code == 200, accepted.text
    assert datetime.fromisoformat(accepted.json()["customer_accepted_at"]) == recorded[0]
    priced = client.get(f"/internal/v1/stores/{store_id}/quotes/{quote_id}", params={"revision": 1})
    assert priced.json()["customer_accepted_at"] is None
