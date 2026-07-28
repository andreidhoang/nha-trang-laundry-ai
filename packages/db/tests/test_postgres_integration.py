"""Live PostgreSQL evidence for forward-only migrations and atomic ledgers."""

from __future__ import annotations

import os
from collections.abc import Generator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    snapshot_hash,
)
from nha_trang_laundry_db.identity import IdentityRepository, IdentityStateError, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteRevisionCommand, QuoteStateError
from quote_test_data import make_quote_snapshot


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def test_postgres_append_only_ledger_trigger(postgres_connection: psycopg.Connection[Any]) -> None:
    event_id = uuid4()
    aggregate_id = uuid4()
    correlation_id = uuid4()
    occurred_at = datetime.now(UTC)
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO domain_events (
                id, aggregate_type, aggregate_id, aggregate_version, event_type, payload,
                correlation_id, occurred_at
            ) VALUES (%s, 'TEST', %s, 1, 'TEST_CREATED', '{}'::jsonb, %s, %s)
            """,
            (event_id, aggregate_id, correlation_id, occurred_at),
        )

    with (
        pytest.raises(psycopg.Error, match="append-only"),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE domain_events SET event_type = 'MUTATED' WHERE id = %s", (event_id,))

    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT event_type FROM domain_events WHERE id = %s", (event_id,))
        assert cursor.fetchone() == ("TEST_CREATED",)


def test_postgres_audit_failure_rolls_back_configuration_draft(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    config_id = uuid4()
    config_type = f"TEST_{uuid4().hex.upper()}"
    created_by = UUID("00000000-0000-0000-0000-000000000011")
    correlation_id = uuid4()

    def validate(payload: Mapping[str, object]) -> None:
        if set(payload) != {"services"}:
            raise ValueError("test configuration must contain services")

    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION test_reject_audit_write() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'injected audit failure';
            END;
            $$
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER test_audit_failure
            BEFORE INSERT ON audit_events
            FOR EACH ROW EXECUTE FUNCTION test_reject_audit_write()
            """
        )

    try:
        with pytest.raises(psycopg.Error, match="injected audit failure"):
            ConfigurationRepository({config_type: validate}).create_draft(
                postgres_connection,
                ConfigurationDraft(
                    config_type=config_type,
                    version=1,
                    payload={"services": []},
                    created_by=created_by,
                    config_id=config_id,
                ),
                correlation_id=correlation_id,
            )
        with postgres_connection.cursor() as cursor:
            cursor.execute("SELECT id FROM configuration_versions WHERE id = %s", (config_id,))
            assert cursor.fetchone() is None
    finally:
        with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER IF EXISTS test_audit_failure ON audit_events")
            cursor.execute("DROP FUNCTION IF EXISTS test_reject_audit_write()")


def test_postgres_published_configuration_is_immutable_and_hides_drafts(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    config_id = uuid4()
    config_type = f"TEST_{uuid4().hex.upper()}"
    staff_id = UUID("00000000-0000-0000-0000-000000000011")
    payload: dict[str, object] = {"services": []}

    def validate(candidate: Mapping[str, object]) -> None:
        if set(candidate) != {"services"}:
            raise ValueError("test configuration must contain services")

    repository = ConfigurationRepository({config_type: validate})
    repository.create_draft(
        postgres_connection,
        ConfigurationDraft(config_type, 1, payload, staff_id, config_id=config_id),
        correlation_id=uuid4(),
    )
    with postgres_connection.cursor() as cursor:
        assert repository.get_published(cursor, config_id) is None

    repository.publish(
        postgres_connection,
        config_id=config_id,
        version=1,
        snapshot_hash_value=snapshot_hash(payload),
        published_by=staff_id,
        correlation_id=uuid4(),
    )
    with postgres_connection.cursor() as cursor:
        assert repository.get_published(cursor, config_id) == payload

    with (
        pytest.raises(psycopg.Error, match="immutable"),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE configuration_versions SET payload = '{}'::jsonb WHERE id = %s",
            (config_id,),
        )


def test_postgres_staff_session_requires_mfa_and_honors_revocation(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = IdentityRepository()
    owner_id, subject = _active_or_bootstrapped_owner(postgres_connection, repository)

    with pytest.raises(IdentityStateError, match="MFA proof"):
        repository.create_session(
            postgres_connection,
            oidc_subject=subject,
            mfa_verified=False,
            correlation_id=uuid4(),
        )

    session = repository.create_session(
        postgres_connection,
        oidc_subject=subject,
        mfa_verified=True,
        correlation_id=uuid4(),
    )
    principal = repository.authenticate_session(postgres_connection, session.value)
    assert principal.staff_user_id == owner_id
    assert principal.roles == frozenset({StaffRole.OWNER_ADMIN})

    repository.revoke_session(
        postgres_connection,
        session_id=session.session_id,
        actor_id=owner_id,
        correlation_id=uuid4(),
    )
    with pytest.raises(IdentityStateError, match="inactive, expired, or stale"):
        repository.authenticate_session(postgres_connection, session.value)


def test_postgres_identity_enforces_owner_authority_versions_and_disable(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = IdentityRepository()
    owner_id, _ = _active_or_bootstrapped_owner(postgres_connection, repository)
    with pytest.raises(IdentityStateError, match="already bootstrapped"):
        repository.bootstrap_owner(
            postgres_connection,
            oidc_subject=f"second-owner-{uuid4().hex}",
            display_name="Second Test Owner",
            email=None,
            correlation_id=uuid4(),
        )

    staff_id = repository.create_staff(
        postgres_connection,
        oidc_subject=f"operator-{uuid4().hex}",
        display_name="Test Operator",
        email=None,
        actor_id=owner_id,
        correlation_id=uuid4(),
    )
    with pytest.raises(IdentityStateError, match="owner authorization"):
        repository.assign_role(
            postgres_connection,
            staff_user_id=staff_id,
            role=StaffRole.OPERATOR,
            actor_id=staff_id,
            correlation_id=uuid4(),
        )

    repository.assign_role(
        postgres_connection,
        staff_user_id=staff_id,
        role=StaffRole.OPERATOR,
        actor_id=owner_id,
        correlation_id=uuid4(),
    )
    repository.assign_role(
        postgres_connection,
        staff_user_id=staff_id,
        role=StaffRole.DRIVER,
        actor_id=owner_id,
        correlation_id=uuid4(),
    )
    start = datetime(2026, 7, 28, tzinfo=UTC)
    session = repository.create_session(
        postgres_connection,
        oidc_subject=_subject_for_staff(postgres_connection, staff_id),
        mfa_verified=False,
        correlation_id=uuid4(),
        now=start,
        idle_ttl=timedelta(hours=1),
        absolute_ttl=timedelta(hours=4),
    )
    repository.authenticate_session(
        postgres_connection, session.value, now=start + timedelta(minutes=30)
    )
    repository.authenticate_session(
        postgres_connection, session.value, now=start + timedelta(minutes=75)
    )

    repository.disable_staff(
        postgres_connection,
        staff_user_id=staff_id,
        actor_id=owner_id,
        correlation_id=uuid4(),
    )
    with pytest.raises(IdentityStateError, match="inactive, expired, or stale"):
        repository.authenticate_session(
            postgres_connection, session.value, now=start + timedelta(minutes=76)
        )
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT aggregate_version, event_type
            FROM domain_events
            WHERE aggregate_type = 'STAFF_USER' AND aggregate_id = %s
            ORDER BY aggregate_version
            """,
            (staff_id,),
        )
        assert cursor.fetchall() == [
            (1, "STAFF_CREATED"),
            (2, "STAFF_ROLE_ASSIGNED"),
            (3, "STAFF_ROLE_ASSIGNED"),
            (4, "STAFF_DISABLED"),
        ]


def test_postgres_quote_revisions_are_atomic_hash_verified_and_immutable(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    quote_id = uuid4()
    store_id = uuid4()
    request_id = uuid4()
    actor_id = UUID("00000000-0000-0000-0000-000000000011")
    repository = QuoteRepository()
    first = make_quote_snapshot(quote_id, 1, 100_000)
    second = make_quote_snapshot(quote_id, 2, 120_000)

    repository.create_revision(
        postgres_connection,
        QuoteRevisionCommand(store_id, request_id, first, 0, 0, actor_id, uuid4()),
    )
    repository.create_revision(
        postgres_connection,
        QuoteRevisionCommand(store_id, request_id, second, 1, 1, actor_id, uuid4()),
    )

    with postgres_connection.cursor() as cursor:
        stored_first = repository.get_revision(cursor, quote_id, 1)
        stored_second = repository.get_revision(cursor, quote_id, 2)
        cursor.execute(
            "SELECT current_revision, row_version FROM quotes WHERE id = %s", (quote_id,)
        )
        assert cursor.fetchone() == (2, 2)
    assert stored_first is not None and stored_first.document == first.document
    assert stored_second is not None and stored_second.document == second.document
    assert stored_first.document.snapshot_hash != stored_second.document.snapshot_hash

    with (
        pytest.raises(psycopg.Error, match="immutable"),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE quote_revisions SET status = 'DRAFT' WHERE quote_id = %s AND revision = 1",
            (quote_id,),
        )

    with pytest.raises(QuoteStateError, match="missing, closed, or stale"):
        repository.create_revision(
            postgres_connection,
            QuoteRevisionCommand(store_id, request_id, second, 1, 1, actor_id, uuid4()),
        )


def _subject_for_staff(connection: psycopg.Connection[Any], staff_id: UUID) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT oidc_subject FROM staff_users WHERE id = %s", (staff_id,))
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def _active_or_bootstrapped_owner(
    connection: psycopg.Connection[Any], repository: IdentityRepository
) -> tuple[UUID, str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.id, u.oidc_subject
            FROM staff_users u
            JOIN staff_role_assignments r ON r.staff_user_id = u.id
            WHERE u.status = 'ACTIVE' AND r.role = %s AND r.revoked_at IS NULL
            ORDER BY u.created_at
            LIMIT 1
            """,
            (StaffRole.OWNER_ADMIN,),
        )
        row = cursor.fetchone()
    if row is not None:
        return _uuid(row[0]), str(row[1])
    subject = f"owner-{uuid4().hex}"
    return (
        repository.bootstrap_owner(
            connection,
            oidc_subject=subject,
            display_name="Test Owner",
            email=None,
            correlation_id=uuid4(),
        ),
        subject,
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
