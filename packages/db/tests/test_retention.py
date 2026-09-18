"""Retention: a class disposes of exactly what a decision and an implemented store allow.

`RETENTION-001` proved the schedule could not execute and left every class refusing.
`RETENTION-STORE-001` separated the disposable payload from the append-only ledger, so
`RAW_WEBHOOK_PAYLOAD` now really deletes and the rest still refuse -- each naming which of the five
reasons applies to it. The tests below are written so that the difference between "disposed of" and
"reported as disposed of" is always asserted on the database rather than on the return value.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.retention import (
    DISPOSABLE_PAYLOAD_STORES,
    SUPPORTED_PURGE_CLASSES,
    UNSUPPORTED_STORE_REASONS,
    PurgeOutcome,
    RetentionAuthorizationError,
    RetentionClass,
    RetentionDisposition,
    RetentionRepository,
    RetentionStateError,
    UnsupportedStoreReason,
    retention_class_lock,
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


# --- fail-closed until a schedule is published --------------------------------------------------


def test_a_class_with_no_published_schedule_refuses_and_still_leaves_a_record(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Unknown policy purges nothing and says why.

    The schedule is published with no period and no decision. `DEC-008` resolving on 2026-08-18 did
    not remove this state: the resolution enables no class, so a class can still be published
    without an approved period and must still refuse. Asserting on a class that merely happens to be
    unpublished would instead depend on what earlier tests left in the shared database.
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


@pytest.mark.parametrize("class_name", sorted(UNSUPPORTED_STORE_REASONS))
def test_a_class_with_no_store_refuses_and_names_the_reason_it_refuses(
    postgres_connection: psycopg.Connection[Any], class_name: RetentionClass
) -> None:
    """Refusing is the correct behaviour; refusing anonymously is not.

    Before `RETENTION-STORE-001` every unsupported class produced one of two sentences, both about
    append-only triggers. "Nothing is stored, so the obligation is already discharged" and "a
    payload is sitting under a trigger and nobody has moved it" are opposite states of the world,
    and a run record that cannot tell them apart cannot be audited. The reason is now a value.
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

    reason, _ = UNSUPPORTED_STORE_REASONS[class_name]
    assert run.outcome is PurgeOutcome.REFUSED_UNSUPPORTED_STORE
    assert run.affected_row_count == 0
    assert run.exempt_row_count == 0
    assert run.detail is not None and run.detail.startswith(reason.value)


def test_the_supported_set_is_derived_from_the_registry_rather_than_maintained_beside_it() -> None:
    """The set cannot claim a class the code has no way to dispose of.

    `RETENTION-001`'s own evidence names widening this set without a store as the failure mode to
    prevent. A second hand-maintained constant is how that happens, so there is only one.
    """
    assert frozenset(DISPOSABLE_PAYLOAD_STORES) == SUPPORTED_PURGE_CLASSES
    assert {
        RetentionClass.RAW_WEBHOOK_PAYLOAD,
        RetentionClass.INCIDENT_EVIDENCE,
    } == SUPPORTED_PURGE_CLASSES


def test_every_retention_class_is_either_supported_or_explained() -> None:
    """A class may not be silently forgotten: the two mappings partition the enum exactly.

    Adding a class to `RetentionClass` -- `DEC-019` opens `STAFF_IDENTITY` next -- without deciding
    what happens to it fails here rather than refusing anonymously at runtime.
    """
    supported = frozenset(DISPOSABLE_PAYLOAD_STORES)
    explained = frozenset(UNSUPPORTED_STORE_REASONS)

    assert supported | explained == frozenset(RetentionClass)
    assert supported & explained == frozenset()


def test_a_class_scheduled_for_redaction_is_refused_rather_than_purged(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """`run_purge` ignored `disposition` entirely, which was safe only while nothing was supported.

    `DEC-008` schedules `CONVERSATION_BODY` and `EXACT_DELIVERY_LOCATION` as REDACT. With a store
    behind one of them, the old code would have deleted what the owner scheduled to be rewritten.
    The check runs before the store is consulted, so the refusal does not depend on what happens to
    be implemented.
    """
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    repository = RetentionRepository()
    repository.publish_configuration(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        disposition=RetentionDisposition.REDACT,
        principal=owner,
        correlation_id=uuid4(),
        enabled=True,
        retention_days=30,
        decision_ref="DEC-008",
    )
    for hold_id in repository.active_hold_ids(
        postgres_connection, class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD
    ):
        repository.release_legal_hold(
            postgres_connection, hold_id=hold_id, principal=owner, correlation_id=uuid4()
        )

    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    assert run.outcome is PurgeOutcome.REFUSED_DISPOSITION_NOT_EXECUTABLE
    assert run.affected_row_count == 0


def test_the_assistant_transcript_stays_unsupported_for_the_reason_dec_018_gave(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """A separable payload is not sufficient; a surviving duplicate makes the purge a false claim.

    `assistant_turns` could be split exactly as `webhook_events` was. It is not, because
    `command_idempotency_records.response` holds a byte-identical copy of the answer and
    `protect_idempotency_record` forbids deleting it. Deleting the original would report a disposal
    that did not happen. `ASSISTANT-RETENTION-001` removes the duplicate; until then this refuses.
    """
    reason, explanation = UNSUPPORTED_STORE_REASONS[RetentionClass.ASSISTANT_TRANSCRIPT]

    assert reason is UnsupportedStoreReason.BLOCKED_BY_UNDELETABLE_DUPLICATE
    assert "command_idempotency_records.response" in explanation
    assert "ASSISTANT-RETENTION-001" in explanation
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'command_idempotency_records' AND column_name = 'response'"
        )
        assert _row(cursor) == (1,)


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
                event_type, channel, opt_out_disposition,
                processing_status, received_at
            )
            VALUES (%s, 'TEST_PROVIDER', %s, %s, %s, 'MESSAGE', 'TEST_CHANNEL',
                    'NONE', 'DISPATCH_PENDING', %s)
            """,
            (
                uuid4(),
                f"account-{uuid4().hex}",
                f"event-{uuid4().hex}",
                f"RAW-SHA256-V1:{sha256(b'ciphertext').hexdigest()}",
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


# --- RETENTION-STORE-001: the schedule can now actually dispose of something ----------------------


def _seed_webhook(
    connection: psycopg.Connection[Any],
    *,
    received_at: datetime,
    payload: bytes = b"ciphertext",
    contact_binding_id: UUID | None = None,
) -> UUID:
    """One inbound event: an immutable ledger row plus its disposable payload row."""

    webhook_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO webhook_events (
                id, provider, channel_account_id, provider_event_id, payload_hash,
                event_type, contact_binding_id, channel, opt_out_disposition,
                processing_status, received_at
            ) VALUES (%s, 'TEST_PROVIDER', %s, %s, %s, 'MESSAGE', %s, 'TEST_CHANNEL',
                      'NONE', 'DISPATCH_PENDING', %s)
            """,
            (
                webhook_id,
                f"account-{uuid4().hex}",
                f"event-{uuid4().hex}",
                f"RAW-SHA256-V1:{sha256(payload).hexdigest()}",
                contact_binding_id,
                received_at,
            ),
        )
        cursor.execute(
            "INSERT INTO webhook_event_payloads (webhook_event_id, encrypted_payload) "
            "VALUES (%s, %s)",
            (webhook_id, payload),
        )
    return webhook_id


def _enable_raw_webhook_purge(
    connection: psycopg.Connection[Any], *, retention_days: int = 30
) -> RetentionRepository:
    owner = _staff(connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    repository = RetentionRepository()
    repository.publish_configuration(
        connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        disposition=RetentionDisposition.PURGE,
        principal=owner,
        correlation_id=uuid4(),
        enabled=True,
        retention_days=retention_days,
        decision_ref="DEC-008",
    )
    for hold_id in repository.active_hold_ids(
        connection, class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD
    ):
        repository.release_legal_hold(
            connection, hold_id=hold_id, principal=owner, correlation_id=uuid4()
        )
    return repository


def _payload_exists(connection: psycopg.Connection[Any], webhook_id: UUID) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM webhook_event_payloads WHERE webhook_event_id = %s",
            (webhook_id,),
        )
        return bool(_row(cursor)[0])


def test_a_purge_removes_the_payload_and_leaves_the_ledger_row_intact(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The whole point of the item, asserted on the database rather than on the return value.

    Before this, `SUPPORTED_PURGE_CLASSES` was empty and the COMPLETED branch reported
    `affected_row_count=0` without issuing a DELETE at all -- a control that could only ever look
    satisfied. One row is past the cutoff and one is not, so a purge that deleted everything or
    nothing fails here just as loudly as one that deleted the wrong thing.
    """
    stale = _seed_webhook(
        postgres_connection, received_at=NOW - timedelta(days=90), payload=b"old-ciphertext"
    )
    fresh = _seed_webhook(
        postgres_connection, received_at=NOW - timedelta(days=1), payload=b"new-ciphertext"
    )
    repository = _enable_raw_webhook_purge(postgres_connection)

    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    assert run.outcome is PurgeOutcome.COMPLETED
    assert run.affected_row_count == 1
    assert run.exempt_row_count == 0
    assert not _payload_exists(postgres_connection, stale)
    assert _payload_exists(postgres_connection, fresh)

    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM webhook_events WHERE id IN (%s, %s)", (stale, fresh))
        assert _row(cursor) == (2,)


def test_the_audit_chain_is_reconstructable_after_the_payload_is_gone(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """An auditor reading a purged record sees an account, not a gap.

    Everything needed to answer "what arrived, and what happened to it" survives the purge: the
    ledger row keeps the identifiers and the content hash of the exact bytes that arrived, and the
    run record names the class, the schedule version, the cutoff, the count and the role that ran
    it. Reconstructing that is the test; nothing here trusts a comment.
    """
    payload = b"customer-message"
    webhook_id = _seed_webhook(
        postgres_connection, received_at=NOW - timedelta(days=90), payload=payload
    )
    repository = _enable_raw_webhook_purge(postgres_connection)

    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT w.payload_hash, w.provider_event_id, w.received_at,
                   r.class_name, r.configuration_version, r.outcome, r.cutoff_at,
                   r.affected_row_count, r.executed_by_role,
                   (SELECT count(*) FROM audit_events WHERE aggregate_id = r.run_id),
                   (SELECT count(*) FROM webhook_event_payloads p
                     WHERE p.webhook_event_id = w.id)
              FROM webhook_events w, retention_purge_runs r
             WHERE w.id = %s AND r.run_id = %s
            """,
            (webhook_id, run.run_id),
        )
        (
            payload_hash,
            provider_event_id,
            received_at,
            class_name,
            configuration_version,
            outcome,
            cutoff_at,
            affected,
            executed_by_role,
            audit_rows,
            surviving_payloads,
        ) = _row(cursor)

    # What arrived is still provable, byte for byte, without the bytes.
    assert payload_hash == f"RAW-SHA256-V1:{sha256(payload).hexdigest()}"
    assert provider_event_id
    # And what happened to it is a record rather than an inference from absence.
    assert class_name == RetentionClass.RAW_WEBHOOK_PAYLOAD.value
    assert configuration_version >= 1
    assert outcome == PurgeOutcome.COMPLETED.value
    assert cutoff_at is not None and received_at < cutoff_at
    assert affected == 1
    assert executed_by_role  # DEC-020: the database attests who ran it, not the application
    assert audit_rows == 1
    assert surviving_payloads == 0


def test_retained_evidence_pins_a_payload_past_its_cutoff(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """DEC-018: evidence wins, the run says so, and it does not call itself COMPLETED.

    `consent_events.evidence_webhook_id` is NOT NULL and references `webhook_events(id)`, so for a
    DỪNG message the ciphertext scheduled for deletion at 30 days *is* the proof of a withdrawal
    retained forever. Deleting it destroys the only evidence the shop was told to stop, and its
    absence is indistinguishable from never having been told.
    """
    contact_binding_id = uuid4()
    pinned = _seed_webhook(
        postgres_connection,
        received_at=NOW - timedelta(days=90),
        payload=b"DUNG",
        contact_binding_id=contact_binding_id,
    )
    ordinary = _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=90))
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO consent_events (
                id, contact_binding_id, purpose, channel, event_type, evidence_webhook_id,
                occurred_at
            ) VALUES (%s, %s, 'MARKETING', 'TEST_CHANNEL', 'WITHDRAW', %s, %s)
            """,
            (uuid4(), contact_binding_id, pinned, NOW - timedelta(days=90)),
        )
    repository = _enable_raw_webhook_purge(postgres_connection)

    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    assert run.outcome is PurgeOutcome.COMPLETED_WITH_EXEMPTIONS
    assert run.affected_row_count == 1
    assert run.exempt_row_count == 1
    assert run.detail is not None and "DEC-018" in run.detail
    assert _payload_exists(postgres_connection, pinned)
    assert not _payload_exists(postgres_connection, ordinary)


def test_a_replay_conflict_and_an_agent_run_pin_their_payloads_the_same_way(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """DEC-018 applies the rule to every pointer, not only to the one that motivated it.

    `inbox_replay_conflicts.webhook_event_id` and `agent_runs.source_webhook_event_id` have the same
    shape as the consent pointer: a longer-lived record naming a row whose payload is scheduled for
    disposal. Testing only the consent case would leave the other two to be discovered in
    production, which is where this class of bug is most expensive.
    """
    conflicted = _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=90))
    sourced = _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=90))
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO inbox_replay_conflicts (
                id, webhook_event_id, expected_payload_hash, observed_payload_hash, detected_at
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                uuid4(),
                conflicted,
                f"RAW-SHA256-V1:{'a' * 64}",
                f"RAW-SHA256-V1:{'b' * 64}",
                NOW,
            ),
        )
        cursor.execute(
            "INSERT INTO stores (id, created_at) VALUES (%s, %s)",
            (store_id := uuid4(), NOW),
        )
        cursor.execute(
            """
            INSERT INTO agent_runs (
                id, source_webhook_event_id, organization_id, store_id, channel,
                conversation_binding_id, contact_binding_id, capability, deployment_stage,
                data_classification, runtime_registry_version, runtime_registry_hash,
                prompt_bundle_version, prompt_bundle_hash, tool_contract_hash, status, created_at
            ) VALUES (%s, %s, %s, %s, 'TEST_CHANNEL', %s, %s, 'INTERNAL_SHADOW', 'SHADOW',
                      'SYNTHETIC', 'v1', %s, 'v1', %s, %s, 'PENDING', %s)
            """,
            (
                uuid4(),
                sourced,
                uuid4(),
                store_id,
                uuid4(),
                uuid4(),
                f"sha256:{'c' * 64}",
                f"sha256:{'d' * 64}",
                f"sha256:{'e' * 64}",
                NOW,
            ),
        )
    repository = _enable_raw_webhook_purge(postgres_connection)

    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    assert run.outcome is PurgeOutcome.COMPLETED_WITH_EXEMPTIONS
    assert run.affected_row_count == 0
    assert run.exempt_row_count == 2
    assert _payload_exists(postgres_connection, conflicted)
    assert _payload_exists(postgres_connection, sourced)


def test_a_run_that_held_rows_back_cannot_be_recorded_as_completed(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """DEC-018's rule lives in the schema, not in a convention the next caller might not follow.

    "Never COMPLETED over a silent exemption" is only worth as much as the thing enforcing it. A
    constraint refuses the row; a code comment refuses nothing.
    """
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO retention_purge_runs (
                run_id, class_name, outcome, affected_row_count, exempt_row_count, executed_at
            ) VALUES (%s, 'RAW_WEBHOOK_PAYLOAD', 'COMPLETED', 1, 3, %s)
            """,
            (uuid4(), NOW),
        )


def test_the_purge_and_its_record_commit_together_or_not_at_all(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """A purge that committed without its record destroys data and the account of destroying it.

    The deletion and `commit_material_change` share one transaction -- psycopg turns the inner
    `transaction()` into a savepoint rather than a second transaction, and this test is what proves
    that rather than assuming it. The outer rollback must take the deleted payload rows back with
    it.
    """
    stale = _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=90))
    repository = _enable_raw_webhook_purge(postgres_connection)

    class _Abort(RuntimeError):
        pass

    with pytest.raises(_Abort), postgres_connection.transaction():
        run = repository.run_purge(
            postgres_connection,
            class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
            correlation_id=uuid4(),
        )
        assert run.affected_row_count == 1
        assert not _payload_exists(postgres_connection, stale)
        raise _Abort

    assert _payload_exists(postgres_connection, stale)
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM retention_purge_runs WHERE class_name = %s "
            "AND outcome = 'COMPLETED'",
            (RetentionClass.RAW_WEBHOOK_PAYLOAD.value,),
        )
        assert _row(cursor) == (0,)


def test_a_legal_hold_still_suspends_a_class_that_can_now_really_delete(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """`RETENTION-001`'s fail-closed behaviour must survive the class becoming executable.

    A hold that stopped a no-op proved nothing. This is the first time a hold stands between a real
    DELETE and real rows, which is the only situation it was ever built for.
    """
    stale = _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=90))
    repository = _enable_raw_webhook_purge(postgres_connection)
    approver = _staff(postgres_connection, roles=frozenset({StaffRole.OPS_APPROVER}))
    repository.place_legal_hold(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        reason="tranh chấp đang được điều tra",
        principal=approver,
        correlation_id=uuid4(),
    )

    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    assert run.outcome is PurgeOutcome.REFUSED_LEGAL_HOLD
    assert run.affected_row_count == 0
    assert _payload_exists(postgres_connection, stale)


def test_a_purge_is_idempotent_and_the_second_run_finds_nothing_left(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=90))
    repository = _enable_raw_webhook_purge(postgres_connection)

    first = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )
    second = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    assert (first.outcome, first.affected_row_count) == (PurgeOutcome.COMPLETED, 1)
    assert (second.outcome, second.affected_row_count) == (PurgeOutcome.COMPLETED, 0)


def test_the_ledger_columns_the_registry_promises_to_retain_really_exist(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """`ledger_retains` is the audit chain's inventory, so it is checked rather than described.

    A column renamed or dropped out from under this list would leave the registry describing an
    audit trail that no longer exists -- the exact failure the console disclosure work found
    elsewhere, where a true sentence quietly became false because nothing bound it.
    """
    for store in DISPOSABLE_PAYLOAD_STORES.values():
        with postgres_connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s",
                (store.ledger_table,),
            )
            columns = {str(row[0]) for row in cursor.fetchall()}
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (store.payload_table,),
            )
            assert _row(cursor) == (1,)

        assert set(store.ledger_retains) <= columns
        # And the payload really left the ledger: retaining it would make the split decorative.
        assert "encrypted_payload" not in columns


def test_only_the_purge_role_may_delete_and_only_from_the_side_table(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """DEC-020, checked against the catalog rather than against the migration text.

    The identity that serves customers must not be the identity that can erase their records. The
    grant ships in `0038` beside the table it governs, because a purgeable table whose permission
    lives somewhere else drifts silently in the direction that matters: a schedule the database
    cannot honour.
    """
    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'retention_purge'")
        assert _row(cursor) == (1,), "migration 0038 must create the purge role beside the table"

        cursor.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'retention_purge'")
        assert _row(cursor) == (False,), "a group role carrying one privilege, never a login"

        cursor.execute(
            """
            SELECT table_name
              FROM information_schema.role_table_grants
             WHERE grantee = 'retention_purge' AND privilege_type = 'DELETE'
             ORDER BY table_name
            """
        )
        deletable = [str(row[0]) for row in cursor.fetchall()]

    # Derived from the registry rather than listed here. DEC-020 names the drift it is guarding
    # against -- a purgeable table whose permission lives somewhere else -- and a test that restates
    # the list by hand is one more place for that drift to hide.
    assert deletable == sorted(store.payload_table for store in DISPOSABLE_PAYLOAD_STORES.values())


# --- positive disposal evidence, not an inference from absence -----------------------------------


def test_a_disposal_is_a_recorded_fact_an_auditor_can_read_per_record(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The packet's requirement, answered from the record rather than from its absence.

    An adversarial review of this item's design refuted "absent side row plus a COMPLETED run" as
    evidence, in both directions and correctly. Absence does not imply a lawful purge, because a
    holder of DELETE produces an identical state. And past-the-cutoff does not imply absence,
    because DEC-018 holds evidence-pinned rows back indefinitely. An inference unsound both ways
    proves nothing, so the disposal is written down in the same statement that performs it.
    """
    payload = b"customer-message"
    webhook_id = _seed_webhook(
        postgres_connection, received_at=NOW - timedelta(days=90), payload=payload
    )
    repository = _enable_raw_webhook_purge(postgres_connection)

    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )
    record = repository.disposal_record(
        postgres_connection, subject_table="webhook_events", subject_key=webhook_id
    )

    assert record is not None
    assert record.run_id == run.run_id
    assert record.class_name is RetentionClass.RAW_WEBHOOK_PAYLOAD
    assert record.subject_key == webhook_id
    assert record.decision_ref == "DEC-008"
    assert record.configuration_version is not None
    assert record.executed_by_role
    # The fact that justified the disposal survives the disposal. It is copied from the immutable
    # ledger column, never from the row that was destroyed, so "this really was past the cutoff"
    # stays re-derivable afterwards -- which is the single claim the whole disposal rests on.
    assert record.cutoff_at is not None
    assert record.subject_occurred_at < record.cutoff_at


def test_a_payload_that_was_never_purged_has_no_disposal_record(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The read has to be able to say "no", or a positive answer means nothing.

    This is also what restores, as evidence, the guarantee `encrypted_payload BYTEA NOT NULL` used
    to make structurally: a ledger row with no payload row and no disposal record lost its payload
    to something that was not a retention run, and that is now detectable.
    """
    fresh = _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=1))
    repository = _enable_raw_webhook_purge(postgres_connection)

    repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    assert _payload_exists(postgres_connection, fresh)
    assert (
        repository.disposal_record(
            postgres_connection, subject_table="webhook_events", subject_key=fresh
        )
        is None
    )


def test_an_evidence_pinned_payload_gets_no_disposal_record_because_it_was_not_disposed_of(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    contact_binding_id = uuid4()
    pinned = _seed_webhook(
        postgres_connection,
        received_at=NOW - timedelta(days=90),
        payload=b"DUNG",
        contact_binding_id=contact_binding_id,
    )
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO consent_events (
                id, contact_binding_id, purpose, channel, event_type, evidence_webhook_id,
                occurred_at
            ) VALUES (%s, %s, 'MARKETING', 'TEST_CHANNEL', 'WITHDRAW', %s, %s)
            """,
            (uuid4(), contact_binding_id, pinned, NOW - timedelta(days=90)),
        )
    repository = _enable_raw_webhook_purge(postgres_connection)

    repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    assert _payload_exists(postgres_connection, pinned)
    assert (
        repository.disposal_record(
            postgres_connection, subject_table="webhook_events", subject_key=pinned
        )
        is None
    )


def test_a_disposal_record_cannot_be_rewritten_or_removed(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Evidence of destruction that can itself be destroyed is not evidence."""

    webhook_id = _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=90))
    repository = _enable_raw_webhook_purge(postgres_connection)
    repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    for statement in (
        "UPDATE retention_disposal_records SET subject_key = gen_random_uuid() "
        "WHERE subject_key = %s",
        "DELETE FROM retention_disposal_records WHERE subject_key = %s",
    ):
        with (
            pytest.raises(psycopg.errors.RaiseException),
            postgres_connection.transaction(),
            postgres_connection.cursor() as cursor,
        ):
            cursor.execute(statement, (webhook_id,))


def test_the_disposable_payload_is_immutable_for_as_long_as_it_exists(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Disposability is added alongside immutability, never traded for it.

    `protect_webhook_event` named `encrypted_payload` in its immutable-column list, so before this
    change nobody could rewrite the ciphertext. Moving it to an unguarded table would have handed
    that power to every identity holding UPDATE -- a weakening, in the one item whose first
    constraint is that it weakens nothing. DELETE stays permitted, because DELETE is the purge.
    """
    webhook_id = _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=1))

    with (
        pytest.raises(psycopg.errors.RaiseException),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE webhook_event_payloads SET encrypted_payload = %s WHERE webhook_event_id = %s",
            (b"rewritten", webhook_id),
        )

    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM webhook_event_payloads WHERE webhook_event_id = %s", (webhook_id,)
        )
        assert cursor.rowcount == 1


def test_the_audit_event_carries_what_justified_destroying_the_data(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Section 16 asks an audited event for the facts behind it, not only that it happened.

    `audit_details` used to be `{"event_type": ...}` and the cutoff reached no immutable ledger at
    all -- it existed only in `retention_purge_runs`. The cutoff, the schedule version and the
    decision that approved it are the entire justification for destroying customer data, so they
    belong in the chain that cannot be rewritten.
    """
    _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=90))
    repository = _enable_raw_webhook_purge(postgres_connection)

    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        correlation_id=uuid4(),
    )

    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT details FROM audit_events WHERE aggregate_id = %s", (run.run_id,))
        details = _row(cursor)[0]
        cursor.execute("SELECT payload FROM domain_events WHERE aggregate_id = %s", (run.run_id,))
        payload = _row(cursor)[0]

    for document in (details, payload):
        assert document["cutoff_at"] is not None
        assert document["configuration_version"] is not None
        assert document["decision_ref"] == "DEC-008"
        assert document["affected_row_count"] == 1
        assert document["exempt_row_count"] == 0


def test_a_purge_waits_for_a_legal_hold_being_placed_concurrently(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The hold check is binding, not advisory: it runs inside the transaction that deletes.

    Under `RETENTION-001` the hold was read on its own cursor outside any transaction and took no
    lock, which was harmless because nothing was deleted. With a real purge, losing that race
    destroys data under a hold placed a millisecond earlier and records it as a clean COMPLETED.

    Rather than race two threads and assert on timing, this holds the class lock from a second
    connection -- exactly as `place_legal_hold` does -- and proves the purge blocks on it. A purge
    that did not take the lock would sail past and delete.
    """
    _seed_webhook(postgres_connection, received_at=NOW - timedelta(days=90))
    repository = _enable_raw_webhook_purge(postgres_connection)
    database_url = os.environ["DATABASE_URL"]

    blocker = psycopg.connect(database_url)
    try:
        with blocker.transaction(), blocker.cursor() as cursor:
            retention_class_lock(cursor, class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD)

            with (
                pytest.raises(psycopg.errors.LockNotAvailable),
                postgres_connection.transaction(),
            ):
                # Inside the transaction the purge will run in, so `SET LOCAL` unambiguously
                # applies to it. Set outside, it would attach to whatever implicit transaction
                # psycopg had already opened and the test could pass for the wrong reason.
                with postgres_connection.cursor() as own:
                    own.execute("SET LOCAL lock_timeout = '250ms'")
                repository.run_purge(
                    postgres_connection,
                    class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
                    correlation_id=uuid4(),
                )
    finally:
        # Deterministic teardown: a connection left open here blocks the suite's TRUNCATE reset and
        # the failure surfaces as a hang with no output. See conftest's RESET_BLOCKED_MESSAGE.
        blocker.close()
