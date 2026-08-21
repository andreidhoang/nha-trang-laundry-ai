"""SHADOW-CONSOLE-001: attributed draft review, human-only reconciliation, store-scoped reads."""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_contracts.channel_envelope import (
    ChannelDeliveryStatus,
    ChannelMessageKind,
    ChannelOutboundReceipt,
    ChannelProvider,
    ReconciliationState,
    SendAttempt,
    SendAttemptOutcome,
    SendAuthorization,
    SendAuthorizationSource,
)
from nha_trang_laundry_db.channel import ChannelSendReceiptRepository
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.shadow_console import (
    ShadowAuthorizationError,
    ShadowConsoleRepository,
    ShadowStateError,
)
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA, evaluate_production_sla

NOW = datetime.now(UTC)
AGENT_DRAFT = "Dạ, giặt sấy 5 ký khoảng 60.000đ. Nhân viên sẽ xác nhận lại ạ."


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
        # The role rows are written too, not just carried on the principal. STORE-ASSIGNMENT-001
        # made `assign_store` verify against the database rather than trust the session object, so
        # a fixture whose OWNER_ADMIN existed only in memory was claiming a role nobody had granted.
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


def _owner(connection: psycopg.Connection[Any]) -> StaffPrincipal:
    return _staff(connection, roles=frozenset({StaffRole.OWNER_ADMIN}))


def _member(
    connection: psycopg.Connection[Any], store_id: UUID, *, roles: frozenset[StaffRole]
) -> StaffPrincipal:
    owner = _owner(connection)
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


def _draft(connection: psycopg.Connection[Any], store_id: UUID) -> UUID:
    agent_run_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO agent_runs (
                id, organization_id, store_id, channel, conversation_binding_id,
                contact_binding_id, capability, deployment_stage, data_classification,
                runtime_registry_version, runtime_registry_hash, prompt_bundle_version,
                prompt_bundle_hash, tool_contract_hash, status, created_at
            ) VALUES (
                %s, %s, %s, 'INTERNAL_TEST', %s, %s, 'INTERNAL_SHADOW', 'SHADOW', 'SYNTHETIC',
                '1.0.0-eval', %s, '1.0.0-eval', %s, %s, 'PENDING', %s
            )
            """,
            (
                agent_run_id,
                uuid4(),
                store_id,
                uuid4(),
                uuid4(),
                f"sha256:{'a' * 64}",
                f"sha256:{'b' * 64}",
                f"sha256:{'c' * 64}",
                NOW,
            ),
        )
    ShadowConsoleRepository.record_draft(
        connection,
        agent_run_id=agent_run_id,
        store_id=store_id,
        conversation_binding_id=uuid4(),
        contact_binding_id=uuid4(),
        draft_text=AGENT_DRAFT,
        terminal_outcome="DRAFT",
        terminal_code="DRAFT_REQUIRES_HUMAN",
        tool_call_count=1,
        correlation_id=uuid4(),
    )
    return agent_run_id


def _row(cursor: Any) -> tuple[Any, ...]:
    row = cursor.fetchone()
    assert row is not None
    return cast(tuple[Any, ...], row)


# --- draft review is attributed and preserves the original -------------------------------


@pytest.mark.parametrize(
    ("decision", "reason_code", "edited_text"),
    [
        ("APPROVE", None, None),
        ("EDIT", None, "Dạ, giặt sấy 5 ký là 60.000đ ạ."),
        ("REJECT", "PRICE_NOT_VERIFIABLE", None),
    ],
)
def test_each_draft_decision_is_attributed_and_auditable(
    postgres_connection: psycopg.Connection[Any],
    decision: str,
    reason_code: str | None,
    edited_text: str | None,
) -> None:
    store_id = uuid4()
    principal = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPS_APPROVER}))
    agent_run_id = _draft(postgres_connection, store_id)

    recorded = ShadowConsoleRepository().decide_draft(
        postgres_connection,
        agent_run_id=agent_run_id,
        decision=decision,
        principal=principal,
        correlation_id=uuid4(),
        reason_code=reason_code,
        edited_text=edited_text,
    )

    assert recorded.decided_by_staff_id == principal.staff_user_id
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT action, actor_id FROM audit_events WHERE aggregate_id = %s AND action = %s",
            (agent_run_id, f"AGENT_DRAFT_{decision}"),
        )
        action, actor_id = _row(cursor)
    assert action == f"AGENT_DRAFT_{decision}"
    assert UUID(str(actor_id)) == principal.staff_user_id


def test_an_edited_draft_preserves_the_agent_original_immutably(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    principal = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPS_APPROVER}))
    agent_run_id = _draft(postgres_connection, store_id)

    ShadowConsoleRepository().decide_draft(
        postgres_connection,
        agent_run_id=agent_run_id,
        decision="EDIT",
        principal=principal,
        correlation_id=uuid4(),
        edited_text="Bản đã sửa.",
    )

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT draft_text FROM agent_drafts WHERE agent_run_id = %s", (agent_run_id,)
        )
        assert _row(cursor)[0] == AGENT_DRAFT
        cursor.execute(
            "SELECT edited_text FROM agent_draft_reviews WHERE agent_run_id = %s", (agent_run_id,)
        )
        assert _row(cursor)[0] == "Bản đã sửa."


def test_the_agent_draft_row_cannot_be_rewritten(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPS_APPROVER}))
    agent_run_id = _draft(postgres_connection, store_id)

    with (
        pytest.raises(psycopg.errors.RaiseException),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE agent_drafts SET draft_text = 'tampered' WHERE agent_run_id = %s",
            (agent_run_id,),
        )


def test_a_rejection_must_state_its_reason(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    principal = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPS_APPROVER}))
    agent_run_id = _draft(postgres_connection, store_id)

    with pytest.raises(psycopg.errors.CheckViolation):
        ShadowConsoleRepository().decide_draft(
            postgres_connection,
            agent_run_id=agent_run_id,
            decision="REJECT",
            principal=principal,
            correlation_id=uuid4(),
        )


def test_a_draft_cannot_be_decided_twice(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    principal = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPS_APPROVER}))
    agent_run_id = _draft(postgres_connection, store_id)
    repository = ShadowConsoleRepository()
    repository.decide_draft(
        postgres_connection,
        agent_run_id=agent_run_id,
        decision="APPROVE",
        principal=principal,
        correlation_id=uuid4(),
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.decide_draft(
            postgres_connection,
            agent_run_id=agent_run_id,
            decision="APPROVE",
            principal=principal,
            correlation_id=uuid4(),
        )


# --- RBAC and IDOR ---------------------------------------------------------------------------


def test_a_role_without_approval_rights_cannot_decide_a_draft(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Proven at the repository, so the block holds for any caller, not only the console."""
    store_id = uuid4()
    auditor = _member(postgres_connection, store_id, roles=frozenset({StaffRole.AUDITOR}))
    agent_run_id = _draft(postgres_connection, store_id)

    with pytest.raises(ShadowAuthorizationError, match="not authorized"):
        ShadowConsoleRepository().decide_draft(
            postgres_connection,
            agent_run_id=agent_run_id,
            decision="APPROVE",
            principal=auditor,
            correlation_id=uuid4(),
        )


def test_an_auditor_may_still_read_the_queue(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    auditor = _member(postgres_connection, store_id, roles=frozenset({StaffRole.AUDITOR}))
    _draft(postgres_connection, store_id)

    pending = ShadowConsoleRepository().list_pending_drafts(
        postgres_connection, store_id=store_id, principal=auditor
    )

    assert len(pending) == 1


def test_a_staff_member_cannot_read_another_store_by_changing_the_identifier(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    own_store = uuid4()
    other_store = uuid4()
    principal = _member(postgres_connection, own_store, roles=frozenset({StaffRole.OPS_APPROVER}))
    _draft(postgres_connection, other_store)
    repository = ShadowConsoleRepository()

    with pytest.raises(ShadowAuthorizationError):
        repository.list_pending_drafts(
            postgres_connection, store_id=other_store, principal=principal
        )
    with pytest.raises(ShadowAuthorizationError):
        repository.sla_risk_board(
            postgres_connection,
            store_id=other_store,
            principal=principal,
            policy=STANDARD_WASH_SLA,
        )
    with pytest.raises(ShadowAuthorizationError):
        repository.audit_timeline(
            postgres_connection,
            store_id=other_store,
            aggregate_id=uuid4(),
            principal=principal,
        )


def test_a_staff_member_cannot_decide_a_draft_in_a_store_they_do_not_belong_to(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    own_store = uuid4()
    other_store = uuid4()
    principal = _member(postgres_connection, own_store, roles=frozenset({StaffRole.OPS_APPROVER}))
    foreign_run = _draft(postgres_connection, other_store)

    with pytest.raises(ShadowAuthorizationError):
        ShadowConsoleRepository().decide_draft(
            postgres_connection,
            agent_run_id=foreign_run,
            decision="APPROVE",
            principal=principal,
            correlation_id=uuid4(),
        )


def test_only_an_owner_may_grant_store_access(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    approver = _staff(postgres_connection, roles=frozenset({StaffRole.OPS_APPROVER}))

    with pytest.raises(ShadowAuthorizationError, match="OWNER_ADMIN"):
        ShadowConsoleRepository.assign_store(
            postgres_connection,
            staff_user_id=approver.staff_user_id,
            store_id=uuid4(),
            principal=approver,
            correlation_id=uuid4(),
        )


# --- unknown-outcome reconciliation ------------------------------------------------------------


def _unknown_receipt(connection: psycopg.Connection[Any]) -> ChannelOutboundReceipt:
    receipt = ChannelOutboundReceipt(
        receipt_id=uuid4(),
        outbox_id=uuid4(),
        idempotency_key=f"outbox-{uuid4().hex}",
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        message_kind=ChannelMessageKind.LIST_PRICE_INFO,
        authorization=SendAuthorization(
            source=SendAuthorizationSource.HUMAN_APPROVAL, approval_ref=uuid4()
        ),
        attempt=SendAttempt(attempt_number=1, started_at=NOW, outcome=SendAttemptOutcome.TIMEOUT),
        delivery_status=ChannelDeliveryStatus.FAILED,
        reconciliation_state=ReconciliationState.UNKNOWN_REQUIRES_HUMAN,
    )
    ChannelSendReceiptRepository().record_attempt(connection, receipt, correlation_id=uuid4())
    return receipt


def test_a_human_resolves_an_unknown_send_and_the_resolution_is_attributed(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    principal = _staff(postgres_connection, roles=frozenset({StaffRole.OPS_APPROVER}))
    receipt = _unknown_receipt(postgres_connection)

    ShadowConsoleRepository().resolve_unknown_send(
        postgres_connection,
        receipt_id=receipt.receipt_id,
        resolution=ReconciliationState.CONFIRMED_SENT,
        principal=principal,
        correlation_id=uuid4(),
        note="Đã kiểm tra hội thoại, khách đã nhận.",
    )

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT reconciliation_state, resolved_by, resolution_actor_id
            FROM channel_send_receipts WHERE receipt_id = %s
            """,
            (receipt.receipt_id,),
        )
        state, resolved_by, actor = _row(cursor)
    assert state == "CONFIRMED_SENT"
    assert resolved_by == "HUMAN_DECISION"
    assert actor == str(principal.staff_user_id)


def test_an_unknown_send_cannot_be_resolved_to_a_non_terminal_state(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    principal = _staff(postgres_connection, roles=frozenset({StaffRole.OPS_APPROVER}))
    receipt = _unknown_receipt(postgres_connection)

    with pytest.raises(ShadowStateError, match="CONFIRMED_SENT or CONFIRMED_NOT_SENT"):
        ShadowConsoleRepository().resolve_unknown_send(
            postgres_connection,
            receipt_id=receipt.receipt_id,
            resolution=ReconciliationState.UNKNOWN,
            principal=principal,
            correlation_id=uuid4(),
        )


def test_a_role_without_decide_rights_cannot_resolve_an_unknown_send(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    auditor = _staff(postgres_connection, roles=frozenset({StaffRole.AUDITOR}))
    receipt = _unknown_receipt(postgres_connection)

    with pytest.raises(ShadowAuthorizationError):
        ShadowConsoleRepository().resolve_unknown_send(
            postgres_connection,
            receipt_id=receipt.receipt_id,
            resolution=ReconciliationState.CONFIRMED_SENT,
            principal=auditor,
            correlation_id=uuid4(),
        )


def test_an_already_resolved_send_cannot_be_resolved_again(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    principal = _staff(postgres_connection, roles=frozenset({StaffRole.OPS_APPROVER}))
    receipt = _unknown_receipt(postgres_connection)
    repository = ShadowConsoleRepository()
    repository.resolve_unknown_send(
        postgres_connection,
        receipt_id=receipt.receipt_id,
        resolution=ReconciliationState.CONFIRMED_NOT_SENT,
        principal=principal,
        correlation_id=uuid4(),
    )

    with pytest.raises(ShadowStateError, match="awaiting human reconciliation"):
        repository.resolve_unknown_send(
            postgres_connection,
            receipt_id=receipt.receipt_id,
            resolution=ReconciliationState.CONFIRMED_SENT,
            principal=principal,
            correlation_id=uuid4(),
        )


def test_the_exception_queue_lists_only_unresolved_unknown_sends(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """An unresolved receipt appears; a resolved one does not."""
    principal = _staff(postgres_connection, roles=frozenset({StaffRole.OPS_APPROVER}))
    repository = ShadowConsoleRepository()
    assert repository.list_unknown_sends(postgres_connection, principal=principal) == ()

    resolved = _unknown_receipt(postgres_connection)
    repository.resolve_unknown_send(
        postgres_connection,
        receipt_id=resolved.receipt_id,
        resolution=ReconciliationState.CONFIRMED_NOT_SENT,
        principal=principal,
        correlation_id=uuid4(),
        note="Dọn hàng chờ trước ca.",
    )

    receipt = _unknown_receipt(postgres_connection)

    assert [
        item.receipt_id
        for item in repository.list_unknown_sends(postgres_connection, principal=principal)
    ] == [receipt.receipt_id]

    repository.resolve_unknown_send(
        postgres_connection,
        receipt_id=receipt.receipt_id,
        resolution=ReconciliationState.CONFIRMED_SENT,
        principal=principal,
        correlation_id=uuid4(),
    )

    assert repository.list_unknown_sends(postgres_connection, principal=principal) == ()


# --- deterministic read models -------------------------------------------------------------


def test_the_sla_board_runs_its_query_and_is_empty_without_orders_in_production(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    principal = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPERATOR}))

    board = ShadowConsoleRepository().sla_risk_board(
        postgres_connection, store_id=store_id, principal=principal, policy=STANDARD_WASH_SLA
    )

    assert board == ()


def test_the_sla_board_reports_exactly_what_the_domain_engine_computed() -> None:
    """Delegation is the property under test: the board must not compute risk itself.

    A stub cursor supplies one in-production order, because building a real one requires the whole
    quote chain and would test the quote machinery rather than this surface. The membership check
    and the SQL are covered against real PostgreSQL by the tests above.
    """
    store_id = uuid4()
    order_id = uuid4()
    accepted_at = NOW - timedelta(hours=6)
    principal = StaffPrincipal(
        staff_user_id=uuid4(),
        oidc_subject="oidc-stub",
        roles=frozenset({StaffRole.OPERATOR}),
        mfa_verified=True,
        session_id=uuid4(),
    )

    class _Cursor:
        def __init__(self) -> None:
            self._rows: list[tuple[Any, ...]] = []

        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, statement: str, _parameters: Any = None) -> None:
            # The membership probe returns a row; the board query returns one order.
            self._rows = (
                [(1,)]
                if "staff_store_assignments" in statement
                else [(order_id, store_id, accepted_at)]
            )

        def fetchone(self) -> tuple[Any, ...] | None:
            return self._rows[0] if self._rows else None

        def fetchall(self) -> list[tuple[Any, ...]]:
            return self._rows

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor()

    board = ShadowConsoleRepository().sla_risk_board(
        _Connection(), store_id=store_id, principal=principal, policy=STANDARD_WASH_SLA, now=NOW
    )

    expected = evaluate_production_sla(
        STANDARD_WASH_SLA, evaluated_at=NOW, production_accepted_at=accepted_at
    )
    assert len(board) == 1
    assert board[0].order_id == order_id
    assert board[0].internal_risk_due_at == expected.internal_risk_due_at
    assert board[0].overall_outcome == expected.overall_outcome.value
    assert board[0].reason_codes == tuple(code.value for code in expected.reason_codes)


def test_the_audit_timeline_returns_the_decision_chain(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    principal = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPS_APPROVER}))
    agent_run_id = _draft(postgres_connection, store_id)
    ShadowConsoleRepository().decide_draft(
        postgres_connection,
        agent_run_id=agent_run_id,
        decision="APPROVE",
        principal=principal,
        correlation_id=uuid4(),
    )

    timeline = ShadowConsoleRepository().audit_timeline(
        postgres_connection, store_id=store_id, aggregate_id=agent_run_id, principal=principal
    )

    actions = [entry.action for entry in timeline]
    assert actions == ["AGENT_DRAFT_RECORD", "AGENT_DRAFT_APPROVE"]
    assert timeline[0].actor_id is None
    assert timeline[1].actor_id == principal.staff_user_id


def test_a_decided_draft_leaves_the_queue_and_lands_in_the_review_log(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The queue forgets a draft the moment it is decided. Something has to remember it.

    Approve, edit and reject are recorded so the agent can be graded on them later, and the pending
    queue is deliberately undecided-only. Before this read existed the two facts together meant no
    screen in the console could ever show a decision again.
    """

    store_id = uuid4()
    approver = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPS_APPROVER}))
    repository = ShadowConsoleRepository()
    agent_run_id = _draft(postgres_connection, store_id)

    before = repository.list_pending_drafts(
        postgres_connection, store_id=store_id, principal=approver
    )
    assert agent_run_id in {draft.agent_run_id for draft in before}

    repository.decide_draft(
        postgres_connection,
        agent_run_id=agent_run_id,
        decision="EDIT",
        principal=approver,
        correlation_id=uuid4(),
        reason_code="OPERATOR_REWROTE",
        edited_text="Dạ bên em xin phép nhắn lại cho rõ hơn ạ.",
    )

    after = repository.list_pending_drafts(
        postgres_connection, store_id=store_id, principal=approver
    )
    assert agent_run_id not in {draft.agent_run_id for draft in after}

    reviewed = repository.list_reviewed_drafts(
        postgres_connection, store_id=store_id, principal=approver
    )
    entry = next(item for item in reviewed if item.agent_run_id == agent_run_id)
    assert entry.decision == "EDIT"
    assert entry.reason_code == "OPERATOR_REWROTE"
    assert entry.edited_text == "Dạ bên em xin phép nhắn lại cho rõ hơn ạ."
    assert entry.decided_by_staff_id == approver.staff_user_id
    # The agent's own words travel with the decision: a verdict without the thing it was a verdict
    # on grades nothing.
    assert entry.draft_text
    assert entry.terminal_outcome in {"DRAFT", "REQUIRE_HUMAN"}


def test_an_auditor_reads_the_review_log_it_did_not_write(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Reviews are the store's record, not the reader's.

    `assistant_turns` scopes a non-owner to their own questions. A draft decision is made on the
    shop's behalf, so every Shadow read role sees all of them — and an auditor, which may never
    decide anything, is exactly the role that has to be able to read decisions other people made.
    """

    store_id = uuid4()
    approver = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPS_APPROVER}))
    auditor = _member(postgres_connection, store_id, roles=frozenset({StaffRole.AUDITOR}))
    repository = ShadowConsoleRepository()
    agent_run_id = _draft(postgres_connection, store_id)
    repository.decide_draft(
        postgres_connection,
        agent_run_id=agent_run_id,
        decision="APPROVE",
        principal=approver,
        correlation_id=uuid4(),
        reason_code=None,
        edited_text=None,
    )

    seen = repository.list_reviewed_drafts(
        postgres_connection, store_id=store_id, principal=auditor
    )
    assert agent_run_id in {item.agent_run_id for item in seen}

    outsider = _staff(postgres_connection, roles=frozenset({StaffRole.AUDITOR}))
    with pytest.raises(ShadowAuthorizationError):
        repository.list_reviewed_drafts(postgres_connection, store_id=store_id, principal=outsider)


def test_paging_the_review_log_is_exact_across_a_shared_timestamp(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The sort is `decided_at DESC, review_id` ASC — mixed directions, so no tuple compare.

    Deciding drafts will not produce a tie: each decision takes its own clock reading. The pair
    sharing a timestamp is therefore inserted directly, as a fixture manufacturing a condition the
    clock will not. `decide_draft` has its own tests above; this one is about ordering.
    """

    store_id = uuid4()
    approver = _member(postgres_connection, store_id, roles=frozenset({StaffRole.OPS_APPROVER}))
    repository = ShadowConsoleRepository()
    for _ in range(3):
        repository.decide_draft(
            postgres_connection,
            agent_run_id=_draft(postgres_connection, store_id),
            decision="APPROVE",
            principal=approver,
            correlation_id=uuid4(),
            reason_code=None,
            edited_text=None,
        )
    existing = repository.list_reviewed_drafts(
        postgres_connection, store_id=store_id, principal=approver
    )
    shared_at = existing[0].decided_at
    for _ in range(2):
        run_id = _draft(postgres_connection, store_id)
        with postgres_connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agent_draft_reviews (review_id, agent_run_id, store_id, decision,
                                                 reason_code, edited_text, decided_by_staff_id,
                                                 decided_at)
                VALUES (%s, %s, %s, 'APPROVE', NULL, NULL, %s, %s)
                """,
                (uuid4(), run_id, store_id, approver.staff_user_id, shared_at),
            )

    whole = repository.list_reviewed_drafts(
        postgres_connection, store_id=store_id, principal=approver, limit=50
    )
    stamps = [item.decided_at for item in whole]
    assert len(set(stamps)) < len(stamps), "the fixture did not produce a shared timestamp"

    paged: list[UUID] = []
    anchor: UUID | None = None
    for _ in range(len(whole) + 1):
        page = repository.list_reviewed_drafts(
            postgres_connection, store_id=store_id, principal=approver, limit=2, before=anchor
        )
        if not page:
            break
        paged.extend(item.review_id for item in page)
        anchor = page[-1].review_id

    assert paged == [item.review_id for item in whole]
    assert len(paged) == len(set(paged)), "a review was returned on two pages"

    with pytest.raises(ShadowAuthorizationError):
        repository.list_reviewed_drafts(
            postgres_connection, store_id=store_id, principal=approver, before=uuid4()
        )
