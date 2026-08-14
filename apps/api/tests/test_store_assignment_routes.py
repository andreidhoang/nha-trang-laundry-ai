"""`STORE-ASSIGNMENT-001` at the HTTP boundary: the first routes that grant an authorization.

The usual failure mode of an authorization test is a refused operator. Here it is the opposite — a
granted one who should not have been — so the negative cases are what carry the item.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import app, current_principal, get_operations_service
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.identity import IdentityRepository, StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.store_access import is_store_member

ORIGIN = "http://testserver"
CSRF = "z" * 40
NOW = datetime(2026, 8, 14, 13, 0, tzinfo=UTC)


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def service() -> OperationsService:
    return OperationsService(AuthSettings(database_url=_database_url()))


def _owner(connection: Any) -> StaffPrincipal:
    owner_id = IdentityRepository().bootstrap_owner(
        connection,
        oidc_subject=f"owner-{uuid4().hex}",
        display_name="Chủ cửa hàng",
        email=None,
        correlation_id=uuid4(),
    )
    return StaffPrincipal(
        owner_id, f"owner-{owner_id}", frozenset({StaffRole.OWNER_ADMIN}), True, uuid4()
    )


def _operator(connection: Any, owner: StaffPrincipal) -> StaffPrincipal:
    identity = IdentityRepository()
    staff_id = identity.create_staff(
        connection,
        oidc_subject=f"operator-{uuid4().hex}",
        display_name="Nhân viên",
        email=None,
        actor_id=owner.staff_user_id,
        correlation_id=uuid4(),
    )
    identity.assign_role(
        connection,
        staff_user_id=staff_id,
        role=StaffRole.OPERATOR,
        actor_id=owner.staff_user_id,
        correlation_id=uuid4(),
    )
    return StaffPrincipal(
        staff_id, f"operator-{staff_id}", frozenset({StaffRole.OPERATOR}), True, uuid4()
    )


def _client(service: OperationsService, principal: StaffPrincipal) -> TestClient:
    app.dependency_overrides[current_principal] = lambda: principal
    app.dependency_overrides[get_operations_service] = lambda: service
    return TestClient(app, cookies={"staff_session": "token", "staff_csrf": CSRF})


def _headers(key: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": key}


def _member(connection: Any, staff: StaffPrincipal, store_id: UUID) -> bool:
    with connection.cursor() as cursor:
        return is_store_member(cursor, staff_user_id=staff.staff_user_id, store_id=store_id)


def test_an_owner_grants_and_the_member_immediately_passes_store_scoped_routes(
    connection: Any, service: OperationsService
) -> None:
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()

    try:
        with _client(service, operator) as as_operator:
            refused = as_operator.get(f"/internal/v1/stores/{store_id}/quotes")
        assert refused.status_code == 403

        with _client(service, owner) as as_owner:
            granted = as_owner.post(
                f"/internal/v1/staff/{operator.staff_user_id}/stores/{store_id}",
                headers=_headers(f"grant-{uuid4().hex}"),
            )
        assert granted.status_code == 204

        with _client(service, operator) as as_operator:
            allowed = as_operator.get(f"/internal/v1/stores/{store_id}/quotes")
            stores = as_operator.get("/internal/v1/stores")
    finally:
        app.dependency_overrides.clear()

    assert allowed.status_code == 200
    assert stores.json() == {"store_ids": [str(store_id)]}


def test_a_non_owner_cannot_grant_and_is_told_nothing_extra(
    connection: Any, service: OperationsService
) -> None:
    owner = _owner(connection)
    operator = _operator(connection, owner)
    target = _operator(connection, owner)
    store_id = uuid4()

    try:
        with _client(service, operator) as as_operator:
            response = as_operator.post(
                f"/internal/v1/staff/{target.staff_user_id}/stores/{store_id}",
                headers=_headers(f"grant-{uuid4().hex}"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json() == {"detail": "operation denied"}
    assert not _member(connection, target, store_id)


def test_a_non_owner_cannot_revoke(connection: Any, service: OperationsService) -> None:
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()

    try:
        with _client(service, owner) as as_owner:
            as_owner.post(
                f"/internal/v1/staff/{operator.staff_user_id}/stores/{store_id}",
                headers=_headers(f"grant-{uuid4().hex}"),
            )
        with _client(service, operator) as as_operator:
            response = as_operator.delete(
                f"/internal/v1/staff/{operator.staff_user_id}/stores/{store_id}",
                headers=_headers(f"revoke-{uuid4().hex}"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    # An operator cannot even revoke their own access, because revocation is an owner's record.
    assert _member(connection, operator, store_id)


def test_granting_twice_with_one_key_replays_and_writes_one_row(
    connection: Any, service: OperationsService
) -> None:
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()
    key = f"grant-{uuid4().hex}"

    try:
        with _client(service, owner) as as_owner:
            first = as_owner.post(
                f"/internal/v1/staff/{operator.staff_user_id}/stores/{store_id}",
                headers=_headers(key),
            )
            second = as_owner.post(
                f"/internal/v1/staff/{operator.staff_user_id}/stores/{store_id}",
                headers=_headers(key),
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == second.status_code == 204
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM staff_store_assignments WHERE staff_user_id = %s",
            (operator.staff_user_id,),
        )
        rows = cursor.fetchone()
        cursor.execute(
            """
            SELECT count(*) FROM domain_events
            WHERE aggregate_type = 'STAFF_STORE_ASSIGNMENT' AND aggregate_id = %s
            """,
            (operator.staff_user_id,),
        )
        events = cursor.fetchone()
    assert rows is not None and int(rows[0]) == 1
    assert events is not None and int(events[0]) == 1, "a replay must not write a second event"


def test_revoking_removes_access_on_the_next_request(
    connection: Any, service: OperationsService
) -> None:
    owner = _owner(connection)
    operator = _operator(connection, owner)
    store_id = uuid4()

    try:
        with _client(service, owner) as as_owner:
            as_owner.post(
                f"/internal/v1/staff/{operator.staff_user_id}/stores/{store_id}",
                headers=_headers(f"grant-{uuid4().hex}"),
            )
        with _client(service, operator) as as_operator:
            before = as_operator.get(f"/internal/v1/stores/{store_id}/quotes")

        with _client(service, owner) as as_owner:
            revoked = as_owner.delete(
                f"/internal/v1/staff/{operator.staff_user_id}/stores/{store_id}",
                headers=_headers(f"revoke-{uuid4().hex}"),
            )
        with _client(service, operator) as as_operator:
            after = as_operator.get(f"/internal/v1/stores/{store_id}/quotes")
            stores = as_operator.get("/internal/v1/stores")
    finally:
        app.dependency_overrides.clear()

    assert before.status_code == 200
    assert revoked.status_code == 204
    assert after.status_code == 403, "revocation must take effect on the very next request"
    assert stores.json() == {"store_ids": []}
