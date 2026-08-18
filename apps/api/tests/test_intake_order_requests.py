"""The staff intake API: typed create, opaque refusals, idempotent replay.

Route/contract tests over a stub operations service, exactly the way `test_assistant.py` pins the
assistant boundary: the point is what the route does with each service outcome, not what
PostgreSQL does. The repository-level facts — store scoping, the atomic write, the contact
existence check — are database facts and live in `test_intake_postgres.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_identity_service,
    get_operations_service,
)
from nha_trang_laundry_api.operations import StoredOrderRequestResult
from nha_trang_laundry_db.channel import ChannelBindingError
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.intake import OrderRequestSummary
from nha_trang_laundry_db.store_access import StoreAccessError

STAFF_ID = UUID("00000000-0000-0000-0000-0000000005a1")
STORE_ID = UUID("00000000-0000-0000-0000-0000000005a2")
CONTACT_ID = UUID("00000000-0000-0000-0000-0000000005a3")
REQUEST_ID = UUID("00000000-0000-0000-0000-0000000005a4")

ORIGIN = "http://testserver"
CSRF = "y" * 40


class StubOperationsService:
    """Answers the way the real service answers a committed draft; persistence is construction."""

    #: A request that exists in another store: the repository scopes it away, so the stub reports
    #: it exactly as it reports an id that was never recorded.
    OTHER_STORE_REQUEST_ID = UUID("00000000-0000-0000-0000-0000000005a5")

    def __init__(self, *, replayed: bool = False) -> None:
        self._replayed = replayed

    def create_order_request(self, **kwargs: Any) -> StoredOrderRequestResult:
        return StoredOrderRequestResult(
            order_request_id=REQUEST_ID,
            store_id=kwargs["store_id"],
            contact_binding_id=kwargs["contact_binding_id"],
            status="DRAFT",
            row_version=1,
            created_at=datetime.now(UTC),
            replayed=self._replayed,
        )

    def _summary(self) -> OrderRequestSummary:
        return OrderRequestSummary(
            order_request_id=REQUEST_ID,
            store_id=STORE_ID,
            contact_binding_id=CONTACT_ID,
            status="DRAFT",
            row_version=1,
            created_at=datetime.now(UTC),
        )

    def list_order_requests(self, **_: Any) -> tuple[OrderRequestSummary, ...]:
        return (self._summary(),)

    def get_order_request(self, *, order_request_id: UUID, **_: Any) -> OrderRequestSummary | None:
        # The invisible id and every unknown id come back as None, indistinguishably.
        return self._summary() if order_request_id == REQUEST_ID else None


class RefusingOperationsService:
    """Every call refuses the way the repository refuses a non-member."""

    def create_order_request(self, **_: Any) -> None:
        raise StoreAccessError("store access is not authorized")

    def list_order_requests(self, **_: Any) -> None:
        raise StoreAccessError("store access is not authorized")

    def get_order_request(self, **_: Any) -> None:
        raise StoreAccessError("store access is not authorized")


class ConflictingOperationsService:
    def create_order_request(self, **_: Any) -> None:
        raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")


class UnknownContactOperationsService:
    def create_order_request(self, **_: Any) -> None:
        raise ChannelBindingError("contact binding is not available")


def _write_headers(key: str = "intake-key-0001") -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": key}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        STAFF_ID, "owner-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    app.dependency_overrides[get_operations_service] = StubOperationsService
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


def test_create_returns_the_typed_201(client: TestClient) -> None:
    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/order-requests",
        headers=_write_headers(),
        json={"contact_binding_id": str(CONTACT_ID)},
    )

    assert response.status_code == 201
    body = response.json()
    assert body == {
        "order_request_id": str(REQUEST_ID),
        "store_id": str(STORE_ID),
        "contact_binding_id": str(CONTACT_ID),
        "status": "DRAFT",
        "row_version": 1,
        "created_at": body["created_at"],
        "replayed": False,
    }
    datetime.fromisoformat(body["created_at"])


def test_replaying_a_key_returns_the_same_request_marked_replayed(client: TestClient) -> None:
    app.dependency_overrides[get_operations_service] = lambda: StubOperationsService(replayed=True)

    first = client.post(
        f"/internal/v1/stores/{STORE_ID}/order-requests",
        headers=_write_headers(),
        json={"contact_binding_id": str(CONTACT_ID)},
    )
    replay = client.post(
        f"/internal/v1/stores/{STORE_ID}/order-requests",
        headers=_write_headers(),
        json={"contact_binding_id": str(CONTACT_ID)},
    )

    assert first.status_code == replay.status_code == 201
    assert replay.json()["order_request_id"] == first.json()["order_request_id"]
    assert replay.json()["replayed"] is True


def test_reusing_a_key_with_a_different_payload_conflicts(client: TestClient) -> None:
    app.dependency_overrides[get_operations_service] = ConflictingOperationsService

    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/order-requests",
        headers=_write_headers(),
        json={"contact_binding_id": str(uuid4())},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "IDEMPOTENCY_CONFLICT"}


def test_a_non_member_gets_the_single_opaque_refusal(client: TestClient) -> None:
    app.dependency_overrides[get_operations_service] = RefusingOperationsService

    created = client.post(
        f"/internal/v1/stores/{STORE_ID}/order-requests",
        headers=_write_headers(),
        json={"contact_binding_id": str(CONTACT_ID)},
    )
    listing = client.get(f"/internal/v1/stores/{STORE_ID}/order-requests")
    single = client.get(f"/internal/v1/stores/{STORE_ID}/order-requests/{REQUEST_ID}")

    for response in (created, listing, single):
        assert response.status_code == 403
        assert response.json() == {"detail": "operation denied"}


def test_a_role_that_cannot_quote_cannot_intake_either() -> None:
    """The role gate is the quote gate: AUDITOR reads orders but writes nothing here."""
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        STAFF_ID, "auditor-subject", frozenset({StaffRole.AUDITOR}), True
    )
    app.dependency_overrides[get_operations_service] = StubOperationsService
    try:
        client = TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
        created = client.post(
            f"/internal/v1/stores/{STORE_ID}/order-requests",
            headers=_write_headers(),
            json={"contact_binding_id": str(CONTACT_ID)},
        )
        listing = client.get(f"/internal/v1/stores/{STORE_ID}/order-requests")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 403
    assert created.json() == {"detail": "operation denied"}
    assert listing.status_code == 403
    assert listing.json() == {"detail": "operation denied"}


def test_an_operator_without_mfa_is_refused_like_every_operations_route() -> None:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        STAFF_ID, "operator-subject", frozenset({StaffRole.OPERATOR}), False
    )
    app.dependency_overrides[get_operations_service] = StubOperationsService
    try:
        client = TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
        response = client.post(
            f"/internal/v1/stores/{STORE_ID}/order-requests",
            headers=_write_headers(),
            json={"contact_binding_id": str(CONTACT_ID)},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json() == {"detail": "operation denied"}


def test_a_missing_idempotency_key_is_refused(client: TestClient) -> None:
    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/order-requests",
        headers={"Origin": ORIGIN, "X-CSRF-Token": CSRF},
        json={"contact_binding_id": str(CONTACT_ID)},
    )

    assert response.status_code == 422


def test_an_unauthenticated_call_is_refused() -> None:
    app.dependency_overrides[get_identity_service] = lambda: object()
    app.dependency_overrides[get_operations_service] = StubOperationsService
    try:
        client = TestClient(app)
        created = client.post(
            f"/internal/v1/stores/{STORE_ID}/order-requests",
            headers=_write_headers(),
            json={"contact_binding_id": str(CONTACT_ID)},
        )
        listing = client.get(f"/internal/v1/stores/{STORE_ID}/order-requests")
        single = client.get(f"/internal/v1/stores/{STORE_ID}/order-requests/{REQUEST_ID}")
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 401
    assert listing.status_code == 401
    assert single.status_code == 401


def test_an_invalid_payload_is_a_422_without_inventing_state(client: TestClient) -> None:
    extra_field = client.post(
        f"/internal/v1/stores/{STORE_ID}/order-requests",
        headers=_write_headers(),
        json={"contact_binding_id": str(CONTACT_ID), "note": "khách quen"},
    )
    not_a_uuid = client.post(
        f"/internal/v1/stores/{STORE_ID}/order-requests",
        headers=_write_headers(),
        json={"contact_binding_id": "không-phải-uuid"},
    )
    missing_field = client.post(
        f"/internal/v1/stores/{STORE_ID}/order-requests",
        headers=_write_headers(),
        json={},
    )

    assert extra_field.status_code == 422
    assert not_a_uuid.status_code == 422
    assert missing_field.status_code == 422


def test_an_unknown_contact_binding_fails_closed_require_human(client: TestClient) -> None:
    app.dependency_overrides[get_operations_service] = UnknownContactOperationsService

    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/order-requests",
        headers=_write_headers(),
        json={"contact_binding_id": str(uuid4())},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"outcome": "REQUIRE_HUMAN", "reason_codes": ["CONTACT_BINDING_UNKNOWN"]}
    }


def test_the_list_route_returns_newest_first_typed_items(client: TestClient) -> None:
    response = client.get(f"/internal/v1/stores/{STORE_ID}/order-requests?limit=100")

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    item = items[0]
    assert item == {
        "order_request_id": str(REQUEST_ID),
        "contact_binding_id": str(CONTACT_ID),
        "status": "DRAFT",
        "row_version": 1,
        "created_at": item["created_at"],
    }
    datetime.fromisoformat(item["created_at"])


def test_the_single_fetch_returns_one_typed_item(client: TestClient) -> None:
    response = client.get(f"/internal/v1/stores/{STORE_ID}/order-requests/{REQUEST_ID}")

    assert response.status_code == 200
    assert response.json()["order_request_id"] == str(REQUEST_ID)


def test_an_unknown_request_and_an_invisible_request_are_the_same_response(
    client: TestClient,
) -> None:
    unknown = client.get(f"/internal/v1/stores/{STORE_ID}/order-requests/{uuid4()}")
    invisible = client.get(
        f"/internal/v1/stores/{STORE_ID}/order-requests/"
        f"{StubOperationsService.OTHER_STORE_REQUEST_ID}"
    )

    assert unknown.status_code == invisible.status_code == 404
    assert unknown.content == invisible.content
    assert unknown.json() == {"detail": "order request not found"}
