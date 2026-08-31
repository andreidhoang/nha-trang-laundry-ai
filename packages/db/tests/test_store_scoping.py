"""STORE-SCOPING-001: a role is not a store. Membership is required to read or write one."""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.incidents import IncidentOpenCommand, IncidentRepository
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import OrderAuthorizationError, OrderRepository
from nha_trang_laundry_db.store_access import (
    StoreAccessError,
    is_store_member,
    member_store_ids,
    require_store_membership,
)

NOW = datetime.now(UTC)


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _staff(
    connection: psycopg.Connection[Any],
    *,
    role: StaffRole = StaffRole.OPERATOR,
    store_id: UUID | None = None,
) -> StaffPrincipal:
    staff_user_id = uuid4()
    owner_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_user_id, owner_id):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                """,
                (identifier, f"oidc-{identifier}", NOW),
            )
        if store_id is not None:
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (staff_user_id, store_id, owner_id, NOW),
            )
    return StaffPrincipal(
        staff_user_id=staff_user_id,
        oidc_subject=f"oidc-{staff_user_id}",
        roles=frozenset({role}),
        mfa_verified=True,
        session_id=uuid4(),
    )


# --- the gap this item closes -----------------------------------------------------------------


def test_a_member_of_one_store_is_refused_another_stores_orders(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The whole point: holding a role is not the same as belonging to a store."""
    own_store = uuid4()
    other_store = uuid4()
    principal = _staff(postgres_connection, store_id=own_store)

    with postgres_connection.cursor() as cursor, pytest.raises(OrderAuthorizationError):
        OrderRepository.list_for_store(cursor, store_id=other_store, principal=principal, limit=10)


def test_the_same_member_succeeds_for_their_own_store(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The fix must not be a blanket denial; the legitimate read still works."""
    own_store = uuid4()
    principal = _staff(postgres_connection, store_id=own_store)

    with postgres_connection.cursor() as cursor:
        assert (
            OrderRepository.list_for_store(
                cursor, store_id=own_store, principal=principal, limit=10
            )
            == ()
        )


def test_a_staff_member_with_no_assignment_is_refused_everywhere(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    principal = _staff(postgres_connection)

    with postgres_connection.cursor() as cursor:
        assert member_store_ids(cursor, staff_user_id=principal.staff_user_id) == frozenset()
        with pytest.raises(OrderAuthorizationError):
            OrderRepository.list_for_store(cursor, store_id=uuid4(), principal=principal, limit=10)


def test_owner_admin_is_not_implicitly_a_member_of_every_store(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Seeing all stores is an explicit assignment, never an inference from a role."""
    owner = _staff(postgres_connection, role=StaffRole.OWNER_ADMIN)

    with postgres_connection.cursor() as cursor, pytest.raises(OrderAuthorizationError):
        OrderRepository.list_for_store(cursor, store_id=uuid4(), principal=owner, limit=10)


def test_incident_reads_and_writes_are_both_membership_scoped(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    own_store = uuid4()
    other_store = uuid4()
    principal = _staff(postgres_connection, store_id=own_store)
    repository = IncidentRepository()

    with postgres_connection.cursor() as cursor, pytest.raises(StoreAccessError):
        repository.list_for_store(cursor, store_id=other_store, principal=principal, limit=10)

    with pytest.raises(StoreAccessError):
        repository.open(
            postgres_connection,
            IncidentOpenCommand(
                store_id=other_store,
                order_id=None,
                affected_message_id=None,
                affected_policy_version=None,
                contact_scope_hash=f"sha256:{'a' * 64}",
                category="SERVICE_QUALITY",
                evidence_summary_hash=f"sha256:{'b' * 64}",
                actor_id=principal.staff_user_id,
                correlation_id=uuid4(),
                opened_at=NOW,
                actor_type="STAFF",
            ),
            principal=principal,
        )


# --- the check itself ---------------------------------------------------------------------------


def test_membership_lookup_reflects_exactly_what_was_assigned(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    first = uuid4()
    second = uuid4()
    principal = _staff(postgres_connection, store_id=first)

    with postgres_connection.cursor() as cursor:
        assert is_store_member(cursor, staff_user_id=principal.staff_user_id, store_id=first)
        assert not is_store_member(cursor, staff_user_id=principal.staff_user_id, store_id=second)
        assert member_store_ids(cursor, staff_user_id=principal.staff_user_id) == frozenset({first})


def test_the_refusal_carries_the_callers_own_error_type(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Each repository keeps the error its callers already handle, so no mapping changes."""
    principal = _staff(postgres_connection)

    with postgres_connection.cursor() as cursor:
        with pytest.raises(OrderAuthorizationError):
            require_store_membership(
                cursor,
                staff_user_id=principal.staff_user_id,
                store_id=uuid4(),
                error=OrderAuthorizationError,
            )
        with pytest.raises(StoreAccessError):
            require_store_membership(
                cursor, staff_user_id=principal.staff_user_id, store_id=uuid4()
            )


def test_the_approval_queue_excludes_anything_it_cannot_attribute_to_a_member_store(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The queue shows a store its own approvals, and shows nobody else theirs.

    Rewritten on 2026-08-30. It used to assert that an approval whose *resource* did not resolve to
    an order was excluded from everyone -- true, fail-closed, and hiding two problems. Resolving the
    store through `orders ON o.id = r.resource_id` meant every `MESSAGE_DRAFT` approval, which is
    the whole manual-send queue, was invisible to the people meant to action it. And an approval
    that could not be attributed to a store in the queue could still be *decided* by anyone, because
    `decide` had no store to check membership against at all.

    Migration `0034` gives an approval its own `store_id`, so both questions have the same answer
    and this test now pins that answer: a member sees it, an outsider does not.
    """
    from nha_trang_laundry_db.approvals import ApprovalRepository

    store_id = uuid4()
    assigned = _staff(postgres_connection, store_id=store_id)
    outsider = _staff(postgres_connection, store_id=uuid4())
    unassigned = _staff(postgres_connection)
    approval_id = uuid4()
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO approval_requests (
                id, action, resource_type, resource_id, resource_version, snapshot_hash,
                rendered_hash, policy_version, required_role, reason_codes, obligations,
                execution_capability, requested_by, requested_at, expires_at, envelope,
                envelope_hash, store_id
            ) VALUES (
                %s, 'PRESENT_QUOTE', 'ORDER', %s, 1, %s, %s, 'policy-v1', 'OPS_APPROVER',
                '[]'::jsonb, '[]'::jsonb, 'cap', %s, %s, %s, '{}'::jsonb, %s, %s
            )
            """,
            (
                approval_id,
                uuid4(),  # a resource that resolves to no order
                f"JCS-SHA256-V1:{'a' * 64}",
                f"JCS-SHA256-V1:{'b' * 64}",
                assigned.staff_user_id,
                NOW,
                NOW.replace(year=NOW.year + 1),
                f"JCS-SHA256-V1:{uuid4().hex * 2}",
                store_id,
            ),
        )
        cursor.execute(
            """
            INSERT INTO approval_request_states (
                approval_request_id, status, row_version, updated_at
            ) VALUES (%s, 'REQUESTED', 1, %s)
            """,
            (approval_id, NOW),
        )

    with postgres_connection.cursor() as cursor:
        member_sees = ApprovalRepository.list_pending(cursor, principal=assigned, limit=100)
        assert approval_id in {item.approval_request_id for item in member_sees}
        for principal in (outsider, unassigned):
            listed = ApprovalRepository.list_pending(cursor, principal=principal, limit=100)
            assert approval_id not in {item.approval_request_id for item in listed}
