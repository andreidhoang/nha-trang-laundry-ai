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
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
from nha_trang_laundry_db.stores import StoreRepository

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


def _ensure_store(connection: Any, store_id: UUID) -> None:
    """`STORE-REGISTRY-001`: `store_id` is a foreign key, so the shop exists before anyone joins it.

    The deploy-day runbook runs `scripts/bootstrap_store.py` before assigning anyone, and this is
    the fixture standing in for that step rather than an INSERT that skips it.
    """

    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )


def _member(
    connection: psycopg.Connection[Any], store_id: UUID, *, roles: frozenset[StaffRole]
) -> StaffPrincipal:
    _ensure_store(connection, store_id)
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
            "SELECT p.question, t.intent, t.links, t.reason_codes FROM assistant_turns t "
            "JOIN assistant_turn_payloads p ON p.turn_id = t.turn_id WHERE t.turn_id = %s",
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
            "UPDATE assistant_turns SET intent = 'KHAC' WHERE turn_id = %s",
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
    assert stored.answer is not None and "0901234567" not in stored.answer
    with postgres_connection.cursor() as cursor:
        # The text moved to `assistant_turn_payloads` in `0040` so the 180-day schedule can dispose
        # of it. Redaction still happens before it is written, which is what this test is about.
        cursor.execute(
            "SELECT question, answer FROM assistant_turn_payloads WHERE turn_id = %s",
            (stored.turn_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert "0901234567" not in str(row[0])
    assert "[REDACTED]" in str(row[0])
    assert "0901234567" not in str(row[1])

    history = service.list_turns(principal=member, store_id=store_id, limit=50)
    assert history[0].question is not None
    assert "0901234567" not in history[0].question
    assert "[REDACTED]" in history[0].question


def test_the_transcript_is_disposable_now_that_the_answer_is_stored_only_once(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The inverse of the test this replaces, and the acceptance test for ASSISTANT-RETENTION-001.

    This class used to refuse. Not because `assistant_turns` was append-only -- `0040` moved the
    text out from under that trigger exactly as `0038` did for the webhook payload -- but because
    `DEC-018` found the answer stored a second time in `command_idempotency_records.response`, in a
    row `protect_idempotency_record` lets nobody delete. Purging the first while the second survived
    would have reported a disposal that had not happened.

    `_turn_mapping` now returns the turn id alone, so no new record carries an answer, and a replay
    rehydrates from `assistant_turns`. With the duplicate gone the schedule can finally execute.

    One turn past the 180-day cutoff and one inside it, so a purge that took everything or nothing
    fails here as loudly as one that took the wrong row.
    """
    owner = _staff(postgres_connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    store_id = uuid4()
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    repository_turns = AssistantTurnRepository()
    stale = _command(member, store_id, "Câu hỏi cũ")
    fresh = _command(member, store_id, "Câu hỏi mới")
    repository_turns.record_turn(
        postgres_connection, replace(stale, created_at=datetime.now(UTC) - timedelta(days=200))
    )
    repository_turns.record_turn(postgres_connection, fresh)

    repository = RetentionRepository()
    repository.publish_configuration(
        postgres_connection,
        class_name=RetentionClass.ASSISTANT_TRANSCRIPT,
        disposition=RetentionDisposition.PURGE,
        principal=owner,
        correlation_id=uuid4(),
        enabled=True,
        retention_days=180,
        decision_ref="DEC-008",
    )
    run = repository.run_purge(
        postgres_connection,
        class_name=RetentionClass.ASSISTANT_TRANSCRIPT,
        correlation_id=uuid4(),
    )

    assert (run.outcome, run.affected_row_count) == (PurgeOutcome.COMPLETED, 1)
    record = repository.disposal_record(
        postgres_connection, subject_table="assistant_turns", subject_key=stale.turn_id
    )
    assert record is not None and record.decision_ref == "DEC-008"

    # The words are gone. Everything that makes the turn auditable is not.
    history = AssistantTurnRepository.list_recent(
        postgres_connection, store_id=store_id, principal=member, limit=50
    )
    by_id = {item.turn_id: item for item in history}
    assert by_id[stale.turn_id].question is None
    assert by_id[stale.turn_id].answer is None
    assert by_id[stale.turn_id].intent == "TODAY_OVERVIEW"
    assert by_id[stale.turn_id].staff_user_id == member.staff_user_id
    assert by_id[fresh.turn_id].question == "Câu hỏi mới"

    # And a legal hold still suspends the class, now that suspending it means something.
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
        cursor.execute(
            "SELECT count(*) FROM assistant_turns WHERE turn_id = ANY(%s)",
            ([stale.turn_id, fresh.turn_id],),
        )
        assert cursor.fetchone() == (2,)
        cursor.execute(
            "SELECT outcome FROM retention_purge_runs WHERE run_id = ANY(%s) ORDER BY executed_at",
            ([run.run_id, held.run_id],),
        )
        assert [str(row[0]) for row in cursor.fetchall()] == ["COMPLETED", "REFUSED_LEGAL_HOLD"]
        cursor.execute(
            """
            SELECT count(*) FROM audit_events
            WHERE aggregate_type = 'RETENTION_PURGE_RUN' AND aggregate_id = ANY(%s)
              AND action = 'RETENTION_PURGE_RUN'
            """,
            ([run.run_id, held.run_id],),
        )
        assert cursor.fetchone() == (2,)


def test_no_idempotency_record_carries_an_assistant_answer_any_more(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The DEC-018 blocker itself, asserted on the table that held the duplicate.

    A replay must still return the identical document -- that is the whole point of the idempotency
    layer -- but it must do so by reading the turn rather than by remembering a copy nothing can
    delete.
    """
    store_id = uuid4()
    member = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    # The service opens its own connections and sees only committed rows.
    postgres_connection.commit()
    service = AssistantService(AuthSettings(database_url=os.environ["DATABASE_URL"]))

    first = service.post_turn(
        principal=member,
        store_id=store_id,
        question="Hôm nay thế nào?",
        idempotency_key="assistant-no-duplicate",
    )
    replay = service.post_turn(
        principal=member,
        store_id=store_id,
        question="Hôm nay thế nào?",
        idempotency_key="assistant-no-duplicate",
    )

    assert replay.replayed is True
    assert (replay.turn_id, replay.answer, replay.intent) == (
        first.turn_id,
        first.answer,
        first.intent,
    )
    assert replay.links == first.links and replay.created_at == first.created_at

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT response FROM command_idempotency_records WHERE scope = %s",
            (f"assistant-turn:{member.staff_user_id}",),
        )
        rows = [row[0] for row in cursor.fetchall()]

    assert rows, "the idempotency record must exist; only its contents changed"
    for response in rows:
        assert set(response) == {"turn_id"}, f"a record still carries payload: {sorted(response)}"


def test_paging_backwards_is_exact_across_a_shared_timestamp(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """No duplicate, no gap, and a tie on `created_at` split the same way both times.

    The sort is `created_at DESC, turn_id` ASC. Those directions differ, so the keyset predicate
    cannot be a row comparison, and the case that catches a wrong one is two turns sharing a
    timestamp: a tuple compare orders the tie the other way and either repeats one of them on the
    next page or drops it.

    Recording turns does not produce that tie -- each one takes its own clock reading, and they
    differ by microseconds -- so the pair sharing a timestamp is inserted directly. The insert is a
    fixture manufacturing a condition the clock will not, not a claim about the write path; the
    ledger trigger it bypasses guards UPDATE and DELETE, which this does neither of, and
    `record_turn` has its own test above.
    """

    store_id = uuid4()
    owner = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OWNER_ADMIN}))
    repository = AssistantTurnRepository()
    recorded = [
        repository.record_turn(postgres_connection, _command(owner, store_id, f"Câu hỏi {index}"))
        for index in range(5)
    ]
    shared_at = recorded[0].created_at
    tied = [uuid4(), uuid4()]
    with postgres_connection.cursor() as cursor:
        for index, turn_id in enumerate(tied):
            cursor.execute(
                """
                INSERT INTO assistant_turns (turn_id, store_id, staff_user_id, intent,
                                             links, reason_codes, correlation_id, created_at)
                VALUES (%s, %s, %s, 'GREETING', '[]', '[]', %s, %s)
                """,
                (turn_id, store_id, owner.staff_user_id, str(uuid4()), shared_at),
            )
            cursor.execute(
                """
                INSERT INTO assistant_turn_payloads (turn_id, question, answer)
                VALUES (%s, %s, 'xin chào')
                """,
                (turn_id, f"Cùng mốc thời gian {index}"),
            )

    whole = repository.list_recent(
        postgres_connection, store_id=store_id, principal=owner, limit=50
    )
    assert len(whole) == len(recorded) + len(tied)
    timestamps = [turn.created_at for turn in whole]
    assert len(set(timestamps)) < len(timestamps), "the fixture did not produce a shared timestamp"

    paged: list[UUID] = []
    anchor: UUID | None = None
    for _ in range(len(whole) + 1):
        page = repository.list_recent(
            postgres_connection, store_id=store_id, principal=owner, limit=2, before=anchor
        )
        if not page:
            break
        paged.extend(turn.turn_id for turn in page)
        anchor = page[-1].turn_id

    assert paged == [turn.turn_id for turn in whole]
    assert len(paged) == len(set(paged)), "a turn was returned on two pages"


def test_a_cursor_the_caller_may_not_see_is_refused_rather_than_restarting_them(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """A bad anchor must not silently answer with the newest page.

    To a reader paging backwards, a restart at the top is indistinguishable from reaching the end
    of the history, which is the quiet kind of wrong this transcript exists to avoid.
    """

    store_id = uuid4()
    owner = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OWNER_ADMIN}))
    operator = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))
    repository = AssistantTurnRepository()
    owners_turn = repository.record_turn(
        postgres_connection, _command(owner, store_id, "Chỉ chủ thấy câu này")
    )
    repository.record_turn(postgres_connection, _command(operator, store_id, "Câu của nhân viên"))

    # The operator holds a turn id that exists in this store and is not theirs to read.
    with pytest.raises(AssistantAuthorizationError):
        repository.list_recent(
            postgres_connection,
            store_id=store_id,
            principal=operator,
            limit=50,
            before=owners_turn.turn_id,
        )

    # And an id belonging to no turn at all is refused the same way, so the two are alike.
    with pytest.raises(AssistantAuthorizationError):
        repository.list_recent(
            postgres_connection, store_id=store_id, principal=owner, limit=50, before=uuid4()
        )
