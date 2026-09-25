"""`PICKUP-ONLY-SETTLE-001` over HTTP against real PostgreSQL: a `PICKUP_ONLY` order, end to end.

The shop's courier fetched the laundry from the customer; the customer comes to the counter for it.
No courier carries money in either direction (`DEC-023`), so every payment is taken at the counter,
the exact quoted total and nothing else (`DEC-010`). The addendum to `DEC-032` (2026-09-25) gives
such a customer the two moments a walk-in has:

- **at collection**, in one step: pay and take the goods, `EXACT_PAYMENT_SELF_COLLECTION`;
- **in advance**, present at the counter before the laundry is finished: pay now, and the handover
  is recorded later on `POST /orders/{id}/collection` by the named staff member who makes it,
  `EXACT_PAYMENT_PREPAID_SELF_COLLECTION`.

Either way the order completes only when it is paid, released and collected. These tests drive the
counter's real sequence through the served routes -- create, intake, the courier's `PICKUP` leg,
washing, payment, handover, completion -- because the defect this item was opened on lived between
modules, and only the whole path can show it is gone.
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
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import FulfillmentMode, ProductionStatus

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from quote_test_data import accepted_quote

#: `make_quote_snapshot` bills 100,000 of service and adds a 10,000 negotiated delivery fee.
QUOTED_TOTAL = 110_000
CSRF = "q" * 40
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


def _member(
    connection: Any, store_id: UUID, owner: StaffPrincipal, role: StaffRole
) -> StaffPrincipal:
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


def _ok(response: Any, code: int = 200) -> int:
    assert response.status_code == code, response.text
    return int(response.json()["row_version"])


def _fetched_and_running(
    connection: Any, client: TestClient, store_id: UUID, staff: StaffPrincipal
) -> tuple[UUID, int]:
    """Created, taken in and running -- every step over HTTP -- with the courier's pickup recorded.

    The order is created by the route the console uses, not by the repository, so the fulfilment
    mode is the one a counter can really send. The `PICKUP` leg is recorded once the order is
    running, because `delivery_legs` records nothing against an order that is not.
    """

    quote_id, revision, quote, contact_id = accepted_quote(
        connection,
        store_id=store_id,
        principal=staff,
        fulfillment_mode=FulfillmentMode.PICKUP_ONLY,
    )
    connection.commit()
    _as(staff)
    created = _post(
        client,
        f"/internal/v1/stores/{store_id}/orders",
        {
            "bound_contact_id": str(contact_id),
            "quote_id": str(quote_id),
            "quote_revision": revision,
            "quote_snapshot_hash": quote.document.snapshot_hash,
            "fulfillment_mode": FulfillmentMode.PICKUP_ONLY.value,
            "customer_final_quote_accepted_at": datetime.now(UTC).isoformat(),
            "acquisition_source": "WALK_IN",
        },
    )
    version = _ok(created, 201)
    order_id = UUID(created.json()["order_id"])
    base = f"/internal/v1/orders/{order_id}"
    version = _ok(
        _post(
            client,
            f"{base}/intake-transition",
            {"target": "RECEIVED_PENDING_INSPECTION"},
            if_match=version,
        )
    )
    version = _ok(
        _post(
            client,
            f"{base}/intake-transition",
            {"target": "ACCEPTED", "slot_approved": True},
            if_match=version,
        )
    )
    for target in ("STORE_CONFIRMATION_PENDING", "CONFIRMED", "ACTIVE"):
        version = _ok(_post(client, f"{base}/transition", {"target": target}, if_match=version))

    pickup = _post(client, f"{base}/delivery-legs", {"leg_kind": "PICKUP", "outcome": "SUCCEEDED"})
    assert pickup.status_code == 201, pickup.text
    # The courier's trip to the customer closes nothing: the customer still has to come in.
    assert pickup.json()["completes_fulfillment"] is False
    # And there is no trip back. `MODES_EXPECTING_RETURN` does not hold this mode.
    back = _post(client, f"{base}/delivery-legs", {"leg_kind": "RETURN", "outcome": "SUCCEEDED"})
    assert back.status_code == 409, back.text
    assert "no return leg" in back.text
    read = client.get(base).json()
    assert read["fulfillment_mode"] == "PICKUP_ONLY"
    return order_id, int(read["row_version"])


def _wash(client: TestClient, order_id: UUID, version: int, *targets: ProductionStatus) -> int:
    for target in targets:
        version = _ok(
            _post(
                client,
                f"/internal/v1/orders/{order_id}/production-transition",
                {"target": target.value},
                if_match=version,
            )
        )
    return version


def _complete(client: TestClient, order_id: UUID) -> Any:
    read = client.get(f"/internal/v1/orders/{order_id}").json()
    return _post(
        client,
        f"/internal/v1/orders/{order_id}/transition",
        {"target": "COMPLETED"},
        if_match=int(read["row_version"]),
    )


def test_a_pickup_only_customer_pays_when_collecting_and_the_order_completes_over_http(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """The one-step counter path, like a walk-in: pay the exact total and take the bag."""

    store_id, owner = _store(connection)
    staff = _member(connection, store_id, owner, StaffRole.OPERATOR)
    order_id, version = _fetched_and_running(connection, client, store_id, staff)
    settle = f"/internal/v1/orders/{order_id}/settlement"

    # Ticking "collected" for laundry still in the machine is refused, as for a walk-in.
    version = _wash(client, order_id, version, ProductionStatus.QUEUED, ProductionStatus.IN_PROCESS)
    early = _post(client, settle, {"paid_amount_vnd": QUOTED_TOTAL, "collected_by_customer": True})
    assert early.status_code == 422
    assert early.json()["detail"]["reason_code"] == "GOODS_NOT_READY_FOR_HANDOVER"

    version = _wash(
        client, order_id, version, ProductionStatus.QUALITY_CHECK, ProductionStatus.READY_AT_STORE
    )
    paid = _post(client, settle, {"paid_amount_vnd": QUOTED_TOTAL, "collected_by_customer": True})
    assert paid.status_code == 201, paid.text
    assert paid.json()["settlement_shape"] == "EXACT_PAYMENT_SELF_COLLECTION"
    assert paid.json()["expected_total_vnd"] == paid.json()["paid_amount_vnd"] == QUOTED_TOTAL
    assert paid.json()["self_collection_recorded"] is True

    # Paid and collected, not released: completion still waits for production.
    blocked = _complete(client, order_id)
    assert blocked.status_code == 409, blocked.text
    assert "production is not released" in blocked.text
    read = client.get(f"/internal/v1/orders/{order_id}").json()
    _wash(client, order_id, int(read["row_version"]), ProductionStatus.RELEASED)

    done = _complete(client, order_id)
    assert done.status_code == 200, done.text
    assert done.json()["commercial"] == "COMPLETED"


def test_a_pickup_only_customer_prepays_at_the_counter_and_collects_later_over_http(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """The addendum to `DEC-032`: present at the counter while the laundry is being washed.

    Refused on the tree this item started from with `COLLECTION_WAS_NOT_BY_THE_CUSTOMER`: the only
    way to take this money was to tick "collected" for shirts still in the machine -- which the
    staging review's rule now also refuses -- so the counter had to turn the customer away.
    """

    store_id, owner = _store(connection)
    morning = _member(connection, store_id, owner, StaffRole.OPERATOR)
    evening = _member(connection, store_id, owner, StaffRole.OPERATOR)
    order_id, version = _fetched_and_running(connection, client, store_id, morning)
    base = f"/internal/v1/orders/{order_id}"
    version = _wash(client, order_id, version, ProductionStatus.QUEUED, ProductionStatus.IN_PROCESS)

    paid = _post(
        client,
        f"{base}/settlement",
        {"paid_amount_vnd": QUOTED_TOTAL, "collected_by_customer": False},
    )
    assert paid.status_code == 201, paid.text
    assert paid.json()["settlement_shape"] == "EXACT_PAYMENT_PREPAID_SELF_COLLECTION"
    assert paid.json()["paid_amount_vnd"] == QUOTED_TOTAL
    assert paid.json()["self_collection_recorded"] is False
    version = int(paid.json()["row_version"])

    # The same guards as a walk-in's prepayment. Nothing to hand over yet.
    unwashed = _post(client, f"{base}/collection", if_match=version)
    assert unwashed.status_code == 422
    assert unwashed.json()["detail"]["reason_code"] == "GOODS_NOT_READY_FOR_HANDOVER"

    version = _wash(
        client,
        order_id,
        version,
        ProductionStatus.QUALITY_CHECK,
        ProductionStatus.READY_AT_STORE,
        ProductionStatus.RELEASED,
    )
    # Paid and released, not collected: the prepayment alone does not close the order.
    not_yet = _complete(client, order_id)
    assert not_yet.status_code == 409, not_yet.text
    assert "fulfillment is incomplete" in not_yet.text

    _as(evening)
    collected = _post(client, f"{base}/collection", if_match=version)
    assert collected.status_code == 201, collected.text
    assert collected.json()["collected_by_staff_id"] == str(evening.staff_user_id)
    assert collected.json()["settlement_id"] == paid.json()["settlement_id"]
    assert collected.json()["self_collection_recorded"] is True

    done = _complete(client, order_id)
    assert done.status_code == 200, done.text
    assert done.json()["commercial"] == "COMPLETED"
    assert done.json()["balance"] == "PAID"


def test_a_pickup_only_prepayment_is_still_the_exact_total_or_nothing_over_http(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """`DEC-010` untouched: a deposit or part payment in advance is refused and writes nothing."""

    store_id, owner = _store(connection)
    staff = _member(connection, store_id, owner, StaffRole.OPERATOR)
    order_id, _ = _fetched_and_running(connection, client, store_id, staff)

    for amount in (QUOTED_TOTAL // 2, QUOTED_TOTAL - 1, QUOTED_TOTAL + 1):
        deposit = _post(
            client,
            f"/internal/v1/orders/{order_id}/settlement",
            {"paid_amount_vnd": amount, "collected_by_customer": False},
        )
        assert deposit.status_code == 422
        assert deposit.json()["detail"] == {
            "outcome": "NOT_SUPPORTED",
            "reason_code": "AMOUNT_IS_NOT_THE_EXACT_TOTAL",
            "decision": "DEC-010",
        }
    read = client.get(f"/internal/v1/orders/{order_id}").json()
    assert (read["balance"], read["self_collection_recorded"]) == ("UNPAID", False)
