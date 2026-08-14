"""A staff member with no store assignment must be refused, not crashed at.

Four store-scoped routes caught only `ValueError`. The refusals they can actually receive —
`OrderAuthorizationError` and `StoreAccessError` — descend from `PermissionError`, and therefore
from `OSError`, so they fell through to a 500. `_raise_operations_error` was already written to map
both to 403; the except clauses simply never handed them over.

This matters more than a status code usually does. Having no `staff_store_assignments` row is the
default state of every staff user the API can create, because assigning a store has no HTTP route
at all. So the very first thing a newly provisioned operator does produced a server error, and a
client that treats 500 as "transient, retry" would retry-storm on the most common condition in the
system.

These are exception-mapping tests, so they use a stub service rather than PostgreSQL: the defect was
never in the database, and a test that needs one would hide behind the integration flag.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_operations_service,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.orders import OrderAuthorizationError
from nha_trang_laundry_db.store_access import StoreAccessError

STAFF_ID = UUID("00000000-0000-0000-0000-0000000002a1")
STORE_ID = UUID("00000000-0000-0000-0000-0000000002a2")
ORDER_ID = UUID("00000000-0000-0000-0000-0000000002a3")
QUOTE_ID = UUID("00000000-0000-0000-0000-0000000002a4")
SNAPSHOT_HASH = "JCS-SHA256-V1:" + "a" * 64
SCOPE_HASH = "sha256:" + "b" * 64
EVIDENCE_HASH = "sha256:" + "c" * 64

ORIGIN = "http://testserver"
CSRF = "y" * 40


class RefusingOperationsService:
    """Every store-scoped call refuses exactly the way the real repositories refuse."""

    def create_order(self, **_: Any) -> None:
        raise OrderAuthorizationError("store access is not authorized")

    def list_quotes(self, **_: Any) -> None:
        raise StoreAccessError("store access is not authorized")

    def open_incident(self, **_: Any) -> None:
        raise StoreAccessError("store access is not authorized")

    def list_incidents(self, **_: Any) -> None:
        raise StoreAccessError("store access is not authorized")

    def list_member_stores(self, **_: Any) -> tuple[UUID, ...]:
        return ()


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        STAFF_ID, "operator-subject", frozenset({StaffRole.OPERATOR}), True
    )
    app.dependency_overrides[get_operations_service] = RefusingOperationsService
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


def _write_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": "test-key-0001"}
    headers.update(extra or {})
    return headers


def test_creating_an_order_in_an_unassigned_store_is_refused_not_crashed(
    client: TestClient,
) -> None:
    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/orders",
        headers=_write_headers(),
        json={
            "bound_contact_id": str(UUID(int=1)),
            "quote_id": str(QUOTE_ID),
            "quote_revision": 1,
            "quote_snapshot_hash": SNAPSHOT_HASH,
            "fulfillment_mode": "SELF_DROP_SELF_COLLECT",
            "customer_final_quote_accepted_at": datetime.now(UTC).isoformat(),
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "operation denied"}


def test_listing_quotes_in_an_unassigned_store_is_refused_not_crashed(client: TestClient) -> None:
    response = client.get(f"/internal/v1/stores/{STORE_ID}/quotes")

    assert response.status_code == 403
    assert response.json() == {"detail": "operation denied"}


def test_opening_an_incident_in_an_unassigned_store_is_refused_not_crashed(
    client: TestClient,
) -> None:
    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/incidents",
        headers=_write_headers(),
        json={
            "order_id": str(ORDER_ID),
            "contact_scope_hash": SCOPE_HASH,
            "evidence_summary_hash": EVIDENCE_HASH,
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "operation denied"}


def test_listing_incidents_in_an_unassigned_store_is_refused_not_crashed(
    client: TestClient,
) -> None:
    response = client.get(f"/internal/v1/stores/{STORE_ID}/incidents")

    assert response.status_code == 403
    assert response.json() == {"detail": "operation denied"}


def test_the_refusal_is_the_same_string_the_role_gate_uses(client: TestClient) -> None:
    """Membership refusal and role refusal must be indistinguishable.

    Probing store identifiers must not teach a caller which stores exist, which is the reason
    `store_access.py` gives for making the two failures identical in the first place.
    """

    membership = client.get(f"/internal/v1/stores/{STORE_ID}/quotes")

    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        STAFF_ID, "driver-subject", frozenset({StaffRole.DRIVER}), True
    )
    role = client.get(f"/internal/v1/stores/{STORE_ID}/quotes")

    assert membership.status_code == role.status_code == 403
    assert membership.json() == role.json()


def test_a_staff_member_with_no_assignment_gets_an_empty_store_list_not_an_error(
    client: TestClient,
) -> None:
    """Zero stores is the default state, so it must read as data rather than as a failure."""

    response = client.get("/internal/v1/stores")

    assert response.status_code == 200
    assert response.json() == {"store_ids": []}
