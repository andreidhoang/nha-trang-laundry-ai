"""RETENTION-001: no class disposes of anything until a decision says it may."""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.retention import (
    LEDGER_BACKED_CLASSES,
    SUPPORTED_PURGE_CLASSES,
    PurgeOutcome,
    RetentionAuthorizationError,
    RetentionClass,
    RetentionDisposition,
    RetentionRepository,
    RetentionStateError,
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


def _staff(connection: psycopg.Connection[Any], *, roles: frozenset[StaffRole]) -> StaffPrincipal:
    staff_user_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
            """,
            (staff_user_id, f"oidc-{staff_user_id}", NOW),
        )
    return StaffPrincipal(
        staff_user_id=staff_user_id,
        oidc_subject=f"oidc-{staff_user_id}",
        roles=roles,
        mfa_verified=True,
        session_id=uuid4(),
    )


def _row(cursor: Any) -> tuple[Any, ...]:
    row = cursor.fetchone()
    assert row is not None
    return cast(tuple[Any, ...], row)


# --- fail-closed while DEC-008 is open -------------------------------------------------------


def test_a_class_with_no_published_schedule_refuses_and_still_leaves_a_record(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The DEC-008 behaviour: unknown policy purges nothing and says why.

    The schedule is published with no period and no decision, which is exactly the state an open
    DEC-008 leaves a class in. Asserting on a class that merely happens to be unpublished would
    depend on what earlier tests left in the shared database.
    """
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    repository = RetentionRepository()
    repository.publish_configuration(
        postgres_connection,
        class_name=RetentionClass.SECURITY_AUDIT_EVENT,
        disposition=RetentionDisposition.PURGE,
        principal=owner,
        correlation_id=uuid4(),
    )
    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.SECURITY_AUDIT_EVENT,
        correlation_id=uuid4(),
    )

    assert run.outcome is PurgeOutcome.REFUSED_NO_APPROVED_SCHEDULE
    assert run.affected_row_count == 0
    assert run.detail is not None and "DEC-008" in run.detail
    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT outcome FROM retention_purge_runs WHERE run_id = %s", (run.run_id,))
        assert _row(cursor)[0] == "REFUSED_NO_APPROVED_SCHEDULE"


def test_a_published_but_disabled_class_purges_nothing(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    repository = RetentionRepository()
    repository.publish_configuration(
        postgres_connection,
        class_name=RetentionClass.CONVERSATION_BODY,
        disposition=RetentionDisposition.REDACT,
        principal=owner,
        correlation_id=uuid4(),
        retention_days=180,
    )

    run = repository.run_purge(
        postgres_connection, class_name=RetentionClass.CONVERSATION_BODY, correlation_id=uuid4()
    )

    assert run.outcome is PurgeOutcome.SKIPPED_DISABLED
    assert run.affected_row_count == 0


def test_enabling_a_class_requires_a_period_and_the_decision_that_approved_it(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """DEC-008 cannot be satisfied by a deployment; the schedule must name its decision."""
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))

    with pytest.raises(RetentionStateError, match="decision that approved it"):
        RetentionRepository().publish_configuration(
            postgres_connection,
            class_name=RetentionClass.DEBUG_LOG,
            disposition=RetentionDisposition.PURGE,
            principal=owner,
            correlation_id=uuid4(),
            enabled=True,
            retention_days=30,
        )


def test_the_database_also_refuses_an_enabled_class_without_its_approval(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))

    with (
        pytest.raises(psycopg.errors.CheckViolation),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO retention_class_configurations (
                class_name, version, enabled, disposition, retention_days, decision_ref,
                published_by_staff_id, published_at
            ) VALUES ('DEBUG_LOG', 99, TRUE, 'PURGE', NULL, NULL, %s, %s)
            """,
            (owner.staff_user_id, NOW),
        )


# --- versioned, attributed configuration ------------------------------------------------------


def test_each_publication_is_a_new_immutable_version(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    repository = RetentionRepository()

    first = repository.publish_configuration(
        postgres_connection,
        class_name=RetentionClass.EXACT_DELIVERY_LOCATION,
        disposition=RetentionDisposition.REDACT,
        principal=owner,
        correlation_id=uuid4(),
        retention_days=30,
    )
    second = repository.publish_configuration(
        postgres_connection,
        class_name=RetentionClass.EXACT_DELIVERY_LOCATION,
        disposition=RetentionDisposition.REDACT,
        principal=owner,
        correlation_id=uuid4(),
        retention_days=60,
    )

    assert second.version == first.version + 1
    active = repository.active_configuration(
        postgres_connection, class_name=RetentionClass.EXACT_DELIVERY_LOCATION
    )
    assert active is not None
    assert active.retention_days == 60
    with (
        pytest.raises(psycopg.errors.RaiseException),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE retention_class_configurations SET retention_days = 1 "
            "WHERE class_name = 'EXACT_DELIVERY_LOCATION' AND version = %s",
            (first.version,),
        )


def test_publishing_a_schedule_is_owner_only(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    approver = _staff(postgres_connection, roles=frozenset({StaffRole.OPS_APPROVER}))

    with pytest.raises(RetentionAuthorizationError, match="OWNER_ADMIN"):
        RetentionRepository().publish_configuration(
            postgres_connection,
            class_name=RetentionClass.DEBUG_LOG,
            disposition=RetentionDisposition.PURGE,
            principal=approver,
            correlation_id=uuid4(),
        )


def test_publishing_is_audited_with_its_actor(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))

    RetentionRepository().publish_configuration(
        postgres_connection,
        class_name=RetentionClass.ORDER_FINANCIAL_RECORD,
        disposition=RetentionDisposition.PURGE,
        principal=owner,
        correlation_id=uuid4(),
    )

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM audit_events
            WHERE action = 'RETENTION_SCHEDULE_PUBLISH' AND actor_id = %s
            """,
            (owner.staff_user_id,),
        )
        assert _row(cursor)[0] >= 1


# --- legal hold -------------------------------------------------------------------------------


def test_a_legal_hold_suspends_purge_for_its_class(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The outcome this table exists to prevent: deleting during an investigation."""
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    repository = RetentionRepository()
    repository.publish_configuration(
        postgres_connection,
        class_name=RetentionClass.DEBUG_LOG,
        disposition=RetentionDisposition.PURGE,
        principal=owner,
        correlation_id=uuid4(),
        enabled=True,
        retention_days=30,
        decision_ref="DEC-008",
    )
    repository.place_legal_hold(
        postgres_connection,
        class_name=RetentionClass.DEBUG_LOG,
        reason="Đang có tranh chấp với khách.",
        principal=owner,
        correlation_id=uuid4(),
    )

    run = repository.run_purge(
        postgres_connection, class_name=RetentionClass.DEBUG_LOG, correlation_id=uuid4()
    )

    assert run.outcome is PurgeOutcome.REFUSED_LEGAL_HOLD
    assert run.affected_row_count == 0


def test_placing_a_hold_requires_an_authorized_role(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    auditor = _staff(postgres_connection, roles=frozenset({StaffRole.AUDITOR}))

    with pytest.raises(RetentionAuthorizationError):
        RetentionRepository().place_legal_hold(
            postgres_connection,
            class_name=RetentionClass.DEBUG_LOG,
            reason="không được phép",
            principal=auditor,
            correlation_id=uuid4(),
        )


def test_a_released_hold_stops_suspending_purge(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    repository = RetentionRepository()
    hold_id = repository.place_legal_hold(
        postgres_connection,
        class_name=RetentionClass.CONVERSATION_BODY,
        reason="tạm giữ để kiểm tra",
        principal=owner,
        correlation_id=uuid4(),
    )
    assert repository.active_hold_exists(
        postgres_connection, class_name=RetentionClass.CONVERSATION_BODY
    )

    repository.release_legal_hold(
        postgres_connection, hold_id=hold_id, principal=owner, correlation_id=uuid4()
    )

    assert not repository.active_hold_exists(
        postgres_connection, class_name=RetentionClass.CONVERSATION_BODY
    )
    with pytest.raises(RetentionStateError, match="no active legal hold"):
        repository.release_legal_hold(
            postgres_connection, hold_id=hold_id, principal=owner, correlation_id=uuid4()
        )


def test_a_legal_hold_cannot_be_hard_deleted(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    hold_id = RetentionRepository().place_legal_hold(
        postgres_connection,
        class_name=RetentionClass.ORDER_FINANCIAL_RECORD,
        reason="giữ theo yêu cầu pháp lý",
        principal=owner,
        correlation_id=uuid4(),
    )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM retention_legal_holds WHERE hold_id = %s", (hold_id,))


# --- the append-only conflict, recorded rather than worked around -----------------------------


@pytest.mark.parametrize("class_name", sorted(LEDGER_BACKED_CLASSES))
def test_a_ledger_backed_class_refuses_instead_of_silently_purging_nothing(
    postgres_connection: psycopg.Connection[Any], class_name: RetentionClass
) -> None:
    """Most customer data sits in append-only ledgers, so the schedule cannot be executed there.

    The job says so rather than reporting success having deleted nothing, because a retention
    control that silently no-ops is worse than an absent one: it looks satisfied.
    """
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    repository = RetentionRepository()
    repository.publish_configuration(
        postgres_connection,
        class_name=class_name,
        disposition=RetentionDisposition.PURGE,
        principal=owner,
        correlation_id=uuid4(),
        enabled=True,
        retention_days=30,
        decision_ref="DEC-008",
    )

    # Release anything an earlier test left holding this class: the refusal under test is the
    # store refusal, and a legal hold would shadow it.
    for hold_id in repository.active_hold_ids(postgres_connection, class_name=class_name):
        repository.release_legal_hold(
            postgres_connection, hold_id=hold_id, principal=owner, correlation_id=uuid4()
        )

    run = repository.run_purge(postgres_connection, class_name=class_name, correlation_id=uuid4())

    assert run.outcome is PurgeOutcome.REFUSED_UNSUPPORTED_STORE
    assert run.detail is not None and "append-only" in run.detail


@pytest.mark.parametrize("class_name", sorted(RetentionClass))
def test_no_class_can_dispose_of_anything_today_and_each_says_why(
    postgres_connection: psycopg.Connection[Any], class_name: RetentionClass
) -> None:
    """The honest state of the whole schedule: nothing is disposable, and nothing pretends to be.

    A class reaching COMPLETED while deleting nothing would be the silent no-op that makes a
    retention control worse than an absent one, because it looks satisfied.
    """
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    repository = RetentionRepository()
    repository.publish_configuration(
        postgres_connection,
        class_name=class_name,
        disposition=RetentionDisposition.PURGE,
        principal=owner,
        correlation_id=uuid4(),
        enabled=True,
        retention_days=30,
        decision_ref="DEC-008",
    )
    for hold_id in repository.active_hold_ids(postgres_connection, class_name=class_name):
        repository.release_legal_hold(
            postgres_connection, hold_id=hold_id, principal=owner, correlation_id=uuid4()
        )

    run = repository.run_purge(postgres_connection, class_name=class_name, correlation_id=uuid4())

    assert run.outcome is PurgeOutcome.REFUSED_UNSUPPORTED_STORE
    assert run.affected_row_count == 0
    assert run.detail is not None


def test_the_supported_purge_set_is_empty_until_a_store_is_implemented() -> None:
    assert frozenset() == SUPPORTED_PURGE_CLASSES


def test_the_append_only_trigger_really_does_refuse_a_purge(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Proves the refusal above describes reality rather than a cautious guess.

    The row inserted here is the whole test. `reject_ledger_mutation` is a `BEFORE DELETE ... FOR
    EACH ROW` trigger, so a `DELETE` matching nothing fires nothing and succeeds. Before
    `TEST-ISOLATION-001` this test passed only because earlier tests had left rows in
    `webhook_events`; against a clean database it asserted that deleting no rows raises no error.
    """
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO webhook_events (
                id, provider, channel_account_id, provider_event_id, payload_hash,
                encrypted_payload, event_type, channel, opt_out_disposition,
                processing_status, received_at
            )
            VALUES (%s, 'TEST_PROVIDER', %s, %s, %s, %s, 'MESSAGE', 'TEST_CHANNEL',
                    'NONE', 'DISPATCH_PENDING', %s)
            """,
            (
                uuid4(),
                f"account-{uuid4().hex}",
                f"event-{uuid4().hex}",
                f"RAW-SHA256-V1:{sha256(b'ciphertext').hexdigest()}",
                b"ciphertext",
                NOW,
            ),
        )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM webhook_events WHERE received_at <= now()")


# --- every run leaves a record ------------------------------------------------------------------


def test_every_outcome_writes_exactly_one_run_record_with_its_audit(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    run = RetentionRepository().run_purge(
        postgres_connection, class_name=RetentionClass.CONSENT_EVIDENCE, correlation_id=uuid4()
    )

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              (SELECT count(*) FROM retention_purge_runs WHERE run_id = %(run)s),
              (SELECT count(*) FROM audit_events WHERE aggregate_id = %(run)s),
              (SELECT count(*) FROM domain_events WHERE aggregate_id = %(run)s),
              (SELECT count(*) FROM outbox_events WHERE aggregate_id = %(run)s)
            """,
            {"run": run.run_id},
        )
        assert _row(cursor) == (1, 1, 1, 1)


def test_a_run_record_cannot_be_rewritten(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    run = RetentionRepository().run_purge(
        postgres_connection, class_name=RetentionClass.DEBUG_LOG, correlation_id=uuid4()
    )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE retention_purge_runs SET affected_row_count = 99 WHERE run_id = %s",
            (run.run_id,),
        )


def test_only_a_completed_run_may_claim_to_have_affected_rows(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO retention_purge_runs (
                run_id, class_name, outcome, affected_row_count, executed_at
            ) VALUES (%s, 'DEBUG_LOG', 'REFUSED_LEGAL_HOLD', 5, %s)
            """,
            (uuid4(), NOW),
        )
