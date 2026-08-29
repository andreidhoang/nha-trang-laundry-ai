"""INTAKE-UI-001 against real PostgreSQL: atomicity, actor split, and scoping.

Follows the `test_assistant_postgres.py` pattern: the suite runs only with `DATABASE_URL`, and the
root conftest turns this skip into a failure under `--require-postgres-integration`. What is
pinned here is what only the database can prove:

* the staff create commits row, domain event, audit entry and outbox together;
* the staff path audits as STAFF while the agent-bound repository default stays AGENT_RUNNER;
* a contact binding that does not exist fails closed, before any idempotency record is claimed;
* `list_for_store` and `get_for_store` scope by store and enforce membership in the repository;
* the service-level idempotency wrapper replays and conflicts exactly as the other commands do,
  and a revoked membership refuses even the replay of a held key.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_contracts.channel_envelope import ChannelProvider
from nha_trang_laundry_db.channel import (
    ChannelBindingError,
    ContactChannelBindingRepository,
)
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.intake import (
    CreateOrderRequestCommand,
    OrderRequestRepository,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.store_access import StoreAccessError

NOW = datetime.now(UTC)


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _staff(connection: psycopg.Connection[Any], *, roles: frozenset[StaffRole]) -> StaffPrincipal:
    staff_user_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, %s, 'ACTIVE', %s)
            """,
            (staff_user_id, f"oidc-{staff_user_id}", "Nhân viên thử nghiệm", NOW),
        )
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_user_id, role.value, NOW),
            )
    return StaffPrincipal(
        staff_user_id=staff_user_id,
        oidc_subject=f"oidc-{staff_user_id}",
        roles=roles,
        mfa_verified=True,
        session_id=uuid4(),
    )


def _member(
    connection: psycopg.Connection[Any], store_id: UUID, *, roles: frozenset[StaffRole]
) -> StaffPrincipal:
    owner = _staff(connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=owner.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    principal = _staff(connection, roles=roles)
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=principal.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    return principal


def _contact(connection: psycopg.Connection[Any]) -> UUID:
    """A binding recorded through the only legitimate source: the channel envelope path."""
    resolved = ContactChannelBindingRepository().resolve_or_create(
        connection,
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        provider_user_ref=f"synthetic-counter-{uuid4().hex[:12]}",
        correlation_id=uuid4(),
    )
    return resolved.binding.contact_id


def test_staff_create_commits_row_event_audit_and_outbox_atomically(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    database_url = os.environ["DATABASE_URL"]
    store_id = uuid4()
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    contact_id = _contact(postgres_connection)
    # The service opens its own connections, which see only committed rows.
    postgres_connection.commit()
    service = OperationsService(AuthSettings(database_url=database_url))

    stored = service.create_order_request(
        store_id=store_id,
        contact_binding_id=contact_id,
        idempotency_key=f"intake-{uuid4().hex}",
        principal=member,
    )

    assert stored.replayed is False
    assert stored.status == "DRAFT"
    assert stored.row_version == 1
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT store_id, contact_binding_id, status, row_version FROM order_requests
            WHERE id = %s
            """,
            (stored.order_request_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert UUID(str(row[0])) == store_id
        assert UUID(str(row[1])) == contact_id
        assert (str(row[2]), int(row[3])) == ("DRAFT", 1)
        cursor.execute(
            """
            SELECT count(*) FROM domain_events
            WHERE aggregate_type = 'ORDER_REQUEST' AND aggregate_id = %s
              AND event_type = 'ORDER_REQUEST_DRAFT_CREATED'
            """,
            (stored.order_request_id,),
        )
        assert cursor.fetchone() == (1,)
        cursor.execute(
            """
            SELECT actor_type, actor_id FROM audit_events
            WHERE aggregate_type = 'ORDER_REQUEST' AND aggregate_id = %s
              AND action = 'ORDER_REQUEST_CREATE'
            """,
            (stored.order_request_id,),
        )
        audit = cursor.fetchone()
        assert audit is not None
        assert str(audit[0]) == "STAFF"
        assert UUID(str(audit[1])) == member.staff_user_id
        cursor.execute(
            """
            SELECT event_type, idempotency_key FROM outbox_events
            WHERE aggregate_type = 'ORDER_REQUEST' AND aggregate_id = %s
            """,
            (stored.order_request_id,),
        )
        assert cursor.fetchall() == [
            ("order_request.draft_created.v1", f"order-request:{stored.order_request_id}:created")
        ]


def test_the_repository_default_keeps_the_agent_actor(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The agent tool path shares `create`; omitting actor_type must not start auditing STAFF."""
    stored = OrderRequestRepository().create(
        postgres_connection,
        CreateOrderRequestCommand(
            store_id=uuid4(),
            contact_binding_id=uuid4(),
            conversation_binding_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            created_at=datetime.now(UTC),
        ),
    )

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT actor_type FROM audit_events
            WHERE aggregate_type = 'ORDER_REQUEST' AND aggregate_id = %s
              AND action = 'ORDER_REQUEST_CREATE'
            """,
            (stored.order_request_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert str(row[0]) == "AGENT_RUNNER"


def test_an_unknown_contact_binding_fails_closed_before_any_record(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    database_url = os.environ["DATABASE_URL"]
    store_id = uuid4()
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    postgres_connection.commit()
    service = OperationsService(AuthSettings(database_url=database_url))
    key = f"intake-{uuid4().hex}"

    with pytest.raises(ChannelBindingError, match="contact binding is not available"):
        service.create_order_request(
            store_id=store_id,
            contact_binding_id=uuid4(),
            idempotency_key=key,
            principal=member,
        )

    with postgres_connection.cursor() as cursor:
        # Nothing was created, and the refusal claimed no idempotency record a later retry of the
        # same key could trip over.
        cursor.execute("SELECT count(*) FROM order_requests WHERE store_id = %s", (store_id,))
        assert cursor.fetchone() == (0,)
        cursor.execute(
            "SELECT count(*) FROM command_idempotency_records WHERE idempotency_key = %s", (key,)
        )
        assert cursor.fetchone() == (0,)


def test_a_counter_ticket_is_a_customer_reference_the_walk_in_path_accepts(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """`DEC-013`'s walk-in path, over the route the console actually calls.

    Until 2026-08-29 this route checked `contact_channel_bindings` alone, so the console's own flow
    broke at its second step: `orderRequests.js` calls `POST /counter-tickets`, puts the returned
    `ticket_id` into the contact field exactly as its docstring describes, and the submit came back
    `CONTACT_BINDING_UNKNOWN`. The stranger at the counter -- the shop's most common customer --
    could not be served by the product at all.

    It was invisible because `COUNTER-TICKET-001` measured the walk-in "through the real service
    and repository path", and `OrderRepository.create` already accepted either source. Only this
    route, which nothing had driven over HTTP, did not.
    """
    database_url = os.environ["DATABASE_URL"]
    store_id = uuid4()
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    postgres_connection.commit()
    service = OperationsService(AuthSettings(database_url=database_url))

    ticket = service.issue_counter_ticket(store_id=store_id, principal=member)
    stored = service.create_order_request(
        store_id=store_id,
        contact_binding_id=ticket.ticket_id,
        idempotency_key=f"intake-{uuid4().hex}",
        principal=member,
    )

    assert stored.contact_binding_id == ticket.ticket_id


def test_another_stores_counter_ticket_is_not_a_customer_reference_here(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The ticket check is store-scoped: a number is only a customer at the counter that issued it.

    A global lookup would let one counter open an intake naming another counter's customer -- the
    cross-store shape `STORE-SCOPING-002` closed for writes, arriving through an identifier rather
    than through a route.
    """
    database_url = os.environ["DATABASE_URL"]
    issuing_store, other_store = uuid4(), uuid4()
    issuer = _member(postgres_connection, issuing_store, roles=frozenset({StaffRole.OPERATOR}))
    outsider = _member(postgres_connection, other_store, roles=frozenset({StaffRole.OPERATOR}))
    postgres_connection.commit()
    service = OperationsService(AuthSettings(database_url=database_url))

    ticket = service.issue_counter_ticket(store_id=issuing_store, principal=issuer)

    with pytest.raises(ChannelBindingError, match="contact binding is not available"):
        service.create_order_request(
            store_id=other_store,
            contact_binding_id=ticket.ticket_id,
            idempotency_key=f"intake-{uuid4().hex}",
            principal=outsider,
        )


def test_list_scopes_to_the_store_newest_first(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    database_url = os.environ["DATABASE_URL"]
    store_a, store_b = uuid4(), uuid4()
    member_a = _member(postgres_connection, store_a, roles=frozenset({StaffRole.OPERATOR}))
    _member(postgres_connection, store_b, roles=frozenset({StaffRole.OPERATOR}))
    contact_id = _contact(postgres_connection)
    postgres_connection.commit()
    service = OperationsService(AuthSettings(database_url=database_url))

    first = service.create_order_request(
        store_id=store_a,
        contact_binding_id=contact_id,
        idempotency_key=f"intake-{uuid4().hex}",
        principal=member_a,
    )
    second = service.create_order_request(
        store_id=store_a,
        contact_binding_id=contact_id,
        idempotency_key=f"intake-{uuid4().hex}",
        principal=member_a,
    )

    listed = service.list_order_requests(store_id=store_a, principal=member_a, limit=100)
    assert [item.order_request_id for item in listed] == [
        second.order_request_id,
        first.order_request_id,
    ]
    assert all(item.store_id == store_a for item in listed)
    assert all(item.contact_binding_id == contact_id for item in listed)

    with pytest.raises(ValueError, match="limit"):
        service.list_order_requests(store_id=store_a, principal=member_a, limit=101)

    outsider = _staff(postgres_connection, roles=frozenset({StaffRole.OPERATOR}))
    with pytest.raises(StoreAccessError):
        service.list_order_requests(store_id=store_a, principal=outsider, limit=100)


def test_get_makes_an_invisible_request_indistinguishable_from_a_missing_one(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    database_url = os.environ["DATABASE_URL"]
    store_a, store_b = uuid4(), uuid4()
    member_a = _member(postgres_connection, store_a, roles=frozenset({StaffRole.OPERATOR}))
    member_b = _member(postgres_connection, store_b, roles=frozenset({StaffRole.OPERATOR}))
    contact_id = _contact(postgres_connection)
    postgres_connection.commit()
    service = OperationsService(AuthSettings(database_url=database_url))
    stored = service.create_order_request(
        store_id=store_a,
        contact_binding_id=contact_id,
        idempotency_key=f"intake-{uuid4().hex}",
        principal=member_a,
    )

    found = service.get_order_request(
        store_id=store_a, order_request_id=stored.order_request_id, principal=member_a
    )
    assert found is not None
    assert found.status == "DRAFT"
    assert found.created_at.tzinfo is not None

    # Another store's request and an id that was never recorded: the same None.
    invisible = service.get_order_request(
        store_id=store_b, order_request_id=stored.order_request_id, principal=member_b
    )
    missing = service.get_order_request(
        store_id=store_b, order_request_id=uuid4(), principal=member_b
    )
    assert invisible is None
    assert missing is None

    outsider = _staff(postgres_connection, roles=frozenset({StaffRole.OPERATOR}))
    with pytest.raises(StoreAccessError):
        service.get_order_request(
            store_id=store_a, order_request_id=stored.order_request_id, principal=outsider
        )


def test_service_create_replays_conflicts_and_refuses_a_revoked_member(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    database_url = os.environ["DATABASE_URL"]
    store_id = uuid4()
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    contact_id = _contact(postgres_connection)
    other_contact_id = _contact(postgres_connection)
    postgres_connection.commit()
    service = OperationsService(AuthSettings(database_url=database_url))
    key = f"intake-{uuid4().hex}"

    first = service.create_order_request(
        store_id=store_id, contact_binding_id=contact_id, idempotency_key=key, principal=member
    )
    replay = service.create_order_request(
        store_id=store_id, contact_binding_id=contact_id, idempotency_key=key, principal=member
    )
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.order_request_id == first.order_request_id
    assert replay.created_at == first.created_at

    # The conflict payload must still pass the fail-closed preflight, so the differing field is
    # a second real binding; an invented one is refused before the key is ever examined.
    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        service.create_order_request(
            store_id=store_id,
            contact_binding_id=other_contact_id,
            idempotency_key=key,
            principal=member,
        )

    # Membership is checked outside the idempotency wrapper: once the assignment is revoked, the
    # same key that replayed a moment ago is refused rather than replayed.
    ShadowConsoleRepository.revoke_store(
        postgres_connection,
        staff_user_id=member.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    postgres_connection.commit()
    with pytest.raises(StoreAccessError):
        service.create_order_request(
            store_id=store_id,
            contact_binding_id=contact_id,
            idempotency_key=key,
            principal=member,
        )
