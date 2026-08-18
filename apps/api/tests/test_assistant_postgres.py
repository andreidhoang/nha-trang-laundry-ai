"""ASSISTANT-001 against real PostgreSQL: atomicity, append-only, and scoping.

Follows the `test_staff_operations.py` / `test_shadow_console.py` pattern: the suite runs only
with `DATABASE_URL`, and the root conftest turns this skip into a failure under
`--require-postgres-integration`. What is pinned here is what only the database can prove:

* migration 0026 applies and creates `assistant_turns`;
* `record_turn` commits turn, domain event, audit event and outbox together;
* the `reject_ledger_mutation` trigger refuses UPDATE and DELETE;
* `list_recent` shows a non-owner only their own turns and an owner the store's;
* the service-level idempotency wrapper replays and conflicts exactly as the other commands do.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_api.assistant import AssistantService
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_db.assistant import (
    AssistantAuthorizationError,
    AssistantLink,
    AssistantTurnRepository,
    RecordTurnCommand,
)
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.retention import (
    PurgeOutcome,
    RetentionClass,
    RetentionDisposition,
    RetentionRepository,
)
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


def _command(principal: StaffPrincipal, store_id: UUID, question: str) -> RecordTurnCommand:
    turn_id = uuid4()
    return RecordTurnCommand(
        turn_id=turn_id,
        store_id=store_id,
        principal=principal,
        question=question,
        intent="TODAY_OVERVIEW",
        answer="Hôm nay cửa hàng này chưa có đơn nào.",
        links=(AssistantLink(label="Mở đơn hàng hôm nay", href="#/"),),
        reason_codes=(),
        correlation_id=uuid4(),
        idempotency_key=f"assistant-turn:{turn_id}",
    )


def test_migration_0026_creates_the_append_only_table(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'assistant_turns'
            """
        )
        assert cursor.fetchone() == (1,)


def test_record_turn_commits_turn_event_audit_and_outbox_atomically(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    command = _command(member, store_id, "Hôm nay thế nào?")

    turn = AssistantTurnRepository().record_turn(postgres_connection, command)

    assert turn.turn_id == command.turn_id
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT question, intent, links, reason_codes FROM assistant_turns WHERE turn_id = %s",
            (command.turn_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "Hôm nay thế nào?"
        assert row[1] == "TODAY_OVERVIEW"
        assert row[2] == [{"label": "Mở đơn hàng hôm nay", "href": "#/"}]
        assert row[3] == []
        cursor.execute(
            """
            SELECT count(*) FROM domain_events
            WHERE aggregate_type = 'ASSISTANT_TURN' AND aggregate_id = %s
              AND event_type = 'ASSISTANT_TURN_RECORDED'
            """,
            (command.turn_id,),
        )
        assert cursor.fetchone() == (1,)
        cursor.execute(
            """
            SELECT count(*) FROM audit_events
            WHERE aggregate_type = 'ASSISTANT_TURN' AND aggregate_id = %s
              AND action = 'ASSISTANT_TURN_RECORD' AND actor_id = %s
            """,
            (command.turn_id, member.staff_user_id),
        )
        assert cursor.fetchone() == (1,)
        cursor.execute(
            """
            SELECT event_type, idempotency_key FROM outbox_events
            WHERE aggregate_type = 'ASSISTANT_TURN' AND aggregate_id = %s
            """,
            (command.turn_id,),
        )
        outbox = cursor.fetchall()
        assert outbox == [("assistant.turn_recorded.v1", f"assistant-turn:{command.turn_id}")]


def test_the_ledger_trigger_rejects_update_and_delete(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    command = _command(member, store_id, "Hôm nay thế nào?")
    AssistantTurnRepository().record_turn(postgres_connection, command)

    # A nested transaction rolls back only to its savepoint: the failed statement dies, the
    # fixture's turn survives for the DELETE check below.
    with (
        pytest.raises(psycopg.errors.RaiseException),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE assistant_turns SET answer = 'khác' WHERE turn_id = %s",
            (command.turn_id,),
        )
    with (
        pytest.raises(psycopg.errors.RaiseException),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            "DELETE FROM assistant_turns WHERE turn_id = %s",
            (command.turn_id,),
        )


def test_list_recent_scopes_a_non_owner_to_their_own_turns(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    owner = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OWNER_ADMIN}))
    operator = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    repository = AssistantTurnRepository()
    first = repository.record_turn(
        postgres_connection, _command(owner, store_id, "Câu hỏi của chủ")
    )
    second = repository.record_turn(
        postgres_connection, _command(operator, store_id, "Câu hỏi của nhân viên")
    )

    operator_turns = repository.list_recent(
        postgres_connection, store_id=store_id, principal=operator, limit=50
    )
    assert [turn.turn_id for turn in operator_turns] == [second.turn_id]

    owner_turns = repository.list_recent(
        postgres_connection, store_id=store_id, principal=owner, limit=50
    )
    assert {turn.turn_id for turn in owner_turns} == {first.turn_id, second.turn_id}
    # Newest first.
    assert owner_turns[0].created_at >= owner_turns[1].created_at

    with pytest.raises(ValueError, match="limit"):
        repository.list_recent(postgres_connection, store_id=store_id, principal=owner, limit=101)

    outsider = _staff(postgres_connection, roles=frozenset({StaffRole.OPERATOR}))
    with pytest.raises(AssistantAuthorizationError):
        repository.list_recent(postgres_connection, store_id=store_id, principal=outsider)


def test_get_scoped_makes_an_invisible_turn_indistinguishable_from_a_missing_one(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    owner = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OWNER_ADMIN}))
    operator = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    repository = AssistantTurnRepository()
    owners_turn = repository.record_turn(
        postgres_connection, _command(owner, store_id, "Câu hỏi của chủ")
    )

    found = repository.get_scoped(
        postgres_connection, store_id=store_id, principal=owner, turn_id=owners_turn.turn_id
    )
    assert found is not None
    assert found.answer == "Hôm nay cửa hàng này chưa có đơn nào."

    # Another staff member's turn and an id that was never recorded: the same None.
    invisible = repository.get_scoped(
        postgres_connection, store_id=store_id, principal=operator, turn_id=owners_turn.turn_id
    )
    missing = repository.get_scoped(
        postgres_connection, store_id=store_id, principal=operator, turn_id=uuid4()
    )
    assert invisible is None
    assert missing is None

    outsider = _staff(postgres_connection, roles=frozenset({StaffRole.OPERATOR}))
    with pytest.raises(AssistantAuthorizationError):
        repository.get_scoped(
            postgres_connection, store_id=store_id, principal=outsider, turn_id=owners_turn.turn_id
        )


def test_service_post_turn_replays_and_conflicts(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    database_url = os.environ["DATABASE_URL"]
    store_id = uuid4()
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OWNER_ADMIN}))
    # The service opens its own connections, and they see only committed rows. The fixture
    # connection holds its work in one open implicit transaction — commit it explicitly.
    postgres_connection.commit()
    service = AssistantService(AuthSettings(database_url=database_url))

    first = service.post_turn(
        principal=member,
        store_id=store_id,
        question="Hôm nay thế nào?",
        idempotency_key="assistant-postgres-0001",
    )
    replay = service.post_turn(
        principal=member,
        store_id=store_id,
        question="Hôm nay thế nào?",
        idempotency_key="assistant-postgres-0001",
    )
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.turn_id == first.turn_id

    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        service.post_turn(
            principal=member,
            store_id=store_id,
            question="Một câu hỏi khác hẳn.",
            idempotency_key="assistant-postgres-0001",
        )

    history = service.list_turns(principal=member, store_id=store_id, limit=50)
    assert [turn.turn_id for turn in history] == [first.turn_id]
    assert history[0].intent == "TODAY_OVERVIEW"

    outsider = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    with pytest.raises(StoreAccessError):
        service.post_turn(
            principal=outsider,
            store_id=store_id,
            question="Hôm nay thế nào?",
            idempotency_key="assistant-postgres-0002",
        )


def test_service_rejects_a_role_the_brain_is_not_for(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """AUDITOR can read order boards but is not an assistant role; the repo refuses it."""
    database_url = os.environ["DATABASE_URL"]
    store_id = uuid4()
    auditor = _member(postgres_connection, store_id, roles=frozenset({StaffRole.AUDITOR}))
    postgres_connection.commit()
    service = AssistantService(AuthSettings(database_url=database_url))

    with pytest.raises(AssistantAuthorizationError):
        service.post_turn(
            principal=auditor,
            store_id=store_id,
            question="Hôm nay thế nào?",
            idempotency_key="assistant-postgres-0003",
        )


def test_only_the_redacted_text_reaches_the_table(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Tier-2 memory discipline: a phone number in the question never lands in the row.

    Matching runs on the verbatim question (the intent still resolves), but the stored turn — and
    everything a later history read or idempotent replay returns — carries the redacted text only.
    """
    database_url = os.environ["DATABASE_URL"]
    store_id = uuid4()
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OWNER_ADMIN}))
    postgres_connection.commit()
    service = AssistantService(AuthSettings(database_url=database_url))

    stored = service.post_turn(
        principal=member,
        store_id=store_id,
        question="Hôm nay thế nào? đơn của chị Lan 0901234567",
        idempotency_key="assistant-postgres-redaction",
    )

    assert stored.intent == "TODAY_OVERVIEW"
    assert "0901234567" not in stored.answer
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT question, answer FROM assistant_turns WHERE turn_id = %s",
            (stored.turn_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert "0901234567" not in str(row[0])
    assert "[REDACTED]" in str(row[0])
    assert "0901234567" not in str(row[1])

    history = service.list_turns(principal=member, store_id=store_id, limit=50)
    assert "0901234567" not in history[0].question
    assert "[REDACTED]" in history[0].question


def test_the_transcript_retention_class_refuses_ledger_purge_and_records_every_run(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """ASSISTANT_TRANSCRIPT behaves exactly like every other append-only ledger class.

    The window is published configuration. With an enabled schedule the purge runs, is refused by
    the ledger-backed store (matching existing class behavior — no class in this system purges
    yet), and the refusal still writes its run record and audit event. A legal hold refuses
    earlier, and no turn is ever silently deleted.
    """
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    store_id = uuid4()
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    turn = AssistantTurnRepository().record_turn(
        postgres_connection, _command(member, store_id, "Hôm nay thế nào?")
    )
    repository = RetentionRepository()
    repository.publish_configuration(
        postgres_connection,
        class_name=RetentionClass.ASSISTANT_TRANSCRIPT,
        disposition=RetentionDisposition.PURGE,
        principal=owner,
        correlation_id=uuid4(),
        enabled=True,
        retention_days=30,
        decision_ref="DEC-008",
    )

    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.ASSISTANT_TRANSCRIPT,
        correlation_id=uuid4(),
    )

    assert run.outcome is PurgeOutcome.REFUSED_UNSUPPORTED_STORE
    assert run.detail is not None and "append-only ledger" in run.detail
    assert run.affected_row_count == 0

    repository.place_legal_hold(
        postgres_connection,
        class_name=RetentionClass.ASSISTANT_TRANSCRIPT,
        reason="kiểm tra đang mở",
        principal=owner,
        correlation_id=uuid4(),
    )
    held = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.ASSISTANT_TRANSCRIPT,
        correlation_id=uuid4(),
    )
    assert held.outcome is PurgeOutcome.REFUSED_LEGAL_HOLD

    with postgres_connection.cursor() as cursor:
        # Nothing was purged: the turn is still there, verbatim.
        cursor.execute("SELECT count(*) FROM assistant_turns WHERE turn_id = %s", (turn.turn_id,))
        assert cursor.fetchone() == (1,)
        # Both runs left their records, and the purge run left its audit event.
        cursor.execute(
            "SELECT outcome FROM retention_purge_runs WHERE run_id = ANY(%s) ORDER BY executed_at",
            ([run.run_id, held.run_id],),
        )
        assert [str(row[0]) for row in cursor.fetchall()] == [
            "REFUSED_UNSUPPORTED_STORE",
            "REFUSED_LEGAL_HOLD",
        ]
        cursor.execute(
            """
            SELECT count(*) FROM audit_events
            WHERE aggregate_type = 'RETENTION_PURGE_RUN' AND aggregate_id = ANY(%s)
              AND action = 'RETENTION_PURGE_RUN'
            """,
            ([run.run_id, held.run_id],),
        )
        assert cursor.fetchone() == (2,)
