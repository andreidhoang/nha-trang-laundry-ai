"""`MESSAGE-DRAFT-BINDING-001`: reading what a `SEND_MESSAGE` envelope over a draft binds.

`API-INTEGRITY-002` made the server compute a draft's binding and exposed it nowhere, so no console
could raise the envelope and no approver could read the words being approved. The read is store
scoped: role and MFA, then membership of the named store, then a draft of another store answers
exactly as a missing one does.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from message_draft_test_data import (
    DRAFT_TEXT,
    current_binding,
    grant_service_basis,
    seed_message_draft,
)
from nha_trang_laundry_contracts import AgentDeploymentStage
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.manual_sends import (
    ManualSendAttestationCommand,
    ManualSendPrepareCommand,
    ManualSendRepository,
)
from nha_trang_laundry_db.message_drafts import (
    EDITED_TEXT_REVISION,
    read_message_draft_binding_for_store,
    read_message_draft_send_state_for_store,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import ApprovalAction


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url, autocommit=True) as established:
        apply_migrations(established)
        yield established


def _staff(role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
    return StaffPrincipal(uuid4(), f"binding-{uuid4().hex}", frozenset({role}), mfa)


def _store(connection: Any, *members: StaffPrincipal) -> UUID:
    store_id = uuid4()
    moment = datetime.now(UTC)
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
    )
    with connection.transaction(), connection.cursor() as cursor:
        for member in members:
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s) ON CONFLICT (id) DO NOTHING
                """,
                (member.staff_user_id, member.oidc_subject, moment),
            )
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (member.staff_user_id, store_id, member.staff_user_id, moment),
            )
    return store_id


def _read(connection: Any, principal: StaffPrincipal, store_id: UUID, draft_id: UUID) -> Any:
    with connection.transaction(), connection.cursor() as cursor:
        return read_message_draft_binding_for_store(
            cursor, store_id=store_id, agent_run_id=draft_id, principal=principal
        )


@pytest.mark.parametrize(
    "role", [StaffRole.OPS_APPROVER, StaffRole.OWNER_ADMIN, StaffRole.OPERATOR]
)
def test_a_member_reads_exactly_the_binding_the_server_compares_against(
    connection: Any, role: StaffRole
) -> None:
    reader = _staff(role)
    store_id = _store(connection, reader)
    draft = seed_message_draft(connection, store_id)

    read = _read(connection, reader, store_id, draft.agent_run_id)

    assert read == current_binding(connection, draft.agent_run_id)
    assert read.text == DRAFT_TEXT
    assert read.contact_binding_id == draft.contact_binding_id
    assert read.resource_version == 1


def test_a_reviewers_edit_is_what_is_read_and_it_moves_the_revision(connection: Any) -> None:
    approver = _staff(StaffRole.OPS_APPROVER)
    store_id = _store(connection, approver)
    draft = seed_message_draft(connection, store_id)
    before = _read(connection, approver, store_id, draft.agent_run_id)
    ShadowConsoleRepository().decide_draft(
        connection,
        agent_run_id=draft.agent_run_id,
        decision="EDIT",
        principal=approver,
        correlation_id=uuid4(),
        edited_text="Dạ, tiệm giao tận nơi trong hôm nay ạ.",
    )

    after = _read(connection, approver, store_id, draft.agent_run_id)

    assert after.text == "Dạ, tiệm giao tận nơi trong hôm nay ạ."
    assert after.resource_version == EDITED_TEXT_REVISION
    assert after.rendered_hash != before.rendered_hash
    assert after.snapshot_hash != before.snapshot_hash


def test_a_rejected_draft_has_nothing_to_read(connection: Any) -> None:
    approver = _staff(StaffRole.OPS_APPROVER)
    store_id = _store(connection, approver)
    draft = seed_message_draft(connection, store_id)
    ShadowConsoleRepository().decide_draft(
        connection,
        agent_run_id=draft.agent_run_id,
        decision="REJECT",
        principal=approver,
        correlation_id=uuid4(),
        reason_code="TONE_NOT_APPROPRIATE",
    )

    assert _read(connection, approver, store_id, draft.agent_run_id) is None


def test_another_stores_draft_through_ones_own_store_is_indistinguishable_from_none(
    connection: Any,
) -> None:
    ours, theirs = _staff(StaffRole.OPS_APPROVER), _staff(StaffRole.OPS_APPROVER)
    our_store, their_store = _store(connection, ours), _store(connection, theirs)
    their_draft = seed_message_draft(connection, their_store)

    assert _read(connection, ours, our_store, their_draft.agent_run_id) is None
    assert _read(connection, ours, our_store, uuid4()) is None


def test_another_store_and_an_unknown_store_are_one_refusal(connection: Any) -> None:
    ours, theirs = _staff(StaffRole.OPS_APPROVER), _staff(StaffRole.OPS_APPROVER)
    _store(connection, ours)
    their_store = _store(connection, theirs)
    their_draft = seed_message_draft(connection, their_store)

    refusals = []
    for store_id in (their_store, uuid4()):
        with pytest.raises(StoreAccessError) as refused:
            _read(connection, ours, store_id, their_draft.agent_run_id)
        refusals.append(str(refused.value))
    assert refusals[0] == refusals[1]


@pytest.mark.parametrize(
    ("role", "mfa"),
    [(StaffRole.AUDITOR, True), (StaffRole.OPERATOR, False)],
    ids=["auditor", "operator-without-mfa"],
)
def test_a_role_without_a_part_in_a_send_is_refused_even_as_a_member(
    connection: Any, role: StaffRole, mfa: bool
) -> None:
    principal = _staff(role, mfa=mfa)
    store_id = _store(connection, principal)
    draft = seed_message_draft(connection, store_id)

    with pytest.raises(StoreAccessError):
        _read(connection, principal, store_id, draft.agent_run_id)


# --- MANUAL-SEND-RESUME: how far the latest send over the draft has gone ------------------------


def _state(connection: Any, principal: StaffPrincipal, store_id: UUID, draft_id: UUID) -> Any:
    with connection.transaction(), connection.cursor() as cursor:
        return read_message_draft_send_state_for_store(
            cursor, store_id=store_id, agent_run_id=draft_id, principal=principal
        )


def _request(
    connection: Any,
    store_id: UUID,
    draft_id: UUID,
    requester: StaffPrincipal,
    *,
    at: datetime | None = None,
) -> UUID:
    binding = current_binding(connection, draft_id)
    created = ApprovalRepository().request(
        connection,
        ApprovalRequestCommand(
            ApprovalAction.SEND_MESSAGE,
            "MESSAGE_DRAFT",
            draft_id,
            binding.resource_version,
            binding.snapshot_hash,
            binding.rendered_hash,
            "manual-send-policy-v1",
            requester.staff_user_id,
            f"resume-{uuid4().hex}",
            uuid4(),
            store_id=store_id,
            requested_at=at,
        ),
    )
    return created.approval_request_id


def _approve(connection: Any, approval_id: UUID, draft_id: UUID, decider: StaffPrincipal) -> None:
    binding = current_binding(connection, draft_id)
    ApprovalRepository().decide(
        connection,
        ApprovalDecisionCommand(
            approval_id,
            ApprovalDecision.APPROVED,
            binding.resource_version,
            binding.snapshot_hash,
            binding.rendered_hash,
            "HUMAN_REVIEW_COMPLETE",
            decider,
            uuid4(),
        ),
    )


def test_no_approval_yet_is_no_progress(connection: Any) -> None:
    operator = _staff(StaffRole.OPERATOR)
    store_id = _store(connection, operator)
    draft = seed_message_draft(connection, store_id)

    binding, progress = _state(connection, operator, store_id, draft.agent_run_id)

    assert binding == current_binding(connection, draft.agent_run_id)
    assert progress is None


def test_progress_carries_every_value_a_new_session_needs_through_to_recorded(
    connection: Any,
) -> None:
    operator, approver, other = (
        _staff(StaffRole.OPERATOR),
        _staff(StaffRole.OPS_APPROVER),
        _staff(StaffRole.OPERATOR),
    )
    store_id = _store(connection, operator, approver, other)
    draft = seed_message_draft(connection, store_id)
    grant_service_basis(connection, draft.contact_binding_id, received_at=datetime.now(UTC))
    approval_id = _request(connection, store_id, draft.agent_run_id, operator)
    binding = current_binding(connection, draft.agent_run_id)

    _, requested = _state(connection, operator, store_id, draft.agent_run_id)
    assert requested.approval_request_id == approval_id
    assert requested.status == "REQUESTED"
    assert requested.past_expiry is False
    assert requested.expires_at > datetime.now(UTC)
    assert (requested.resource_version, requested.snapshot_hash, requested.rendered_hash) == (
        binding.resource_version,
        binding.snapshot_hash,
        binding.rendered_hash,
    )
    assert requested.requested_by_you is True
    assert requested.envelope_id is None
    assert requested.envelope_status is None
    assert requested.envelope_row_version is None
    assert requested.prepared_by_you is None
    assert _state(connection, other, store_id, draft.agent_run_id)[1].requested_by_you is False

    _approve(connection, approval_id, draft.agent_run_id, approver)
    _, approved = _state(connection, operator, store_id, draft.agent_run_id)
    assert approved.status == "APPROVED"
    assert approved.envelope_id is None

    # Locked from nothing but the progress read -- the zero-paste path the console takes.
    manual = ManualSendRepository()
    prepared = manual.prepare(
        connection,
        ManualSendPrepareCommand(
            approved.approval_request_id,
            approved.resource_version,
            approved.snapshot_hash,
            approved.rendered_hash,
            "INTERNAL_TEST",
            "TRANSACTIONAL",
            AgentDeploymentStage.SHADOW,
            operator,
            uuid4(),
        ),
    )
    _, locked = _state(connection, operator, store_id, draft.agent_run_id)
    assert locked.envelope_id == prepared.manual_send_envelope_id
    assert locked.envelope_status == "APPROVED_FOR_MANUAL_SEND"
    assert locked.envelope_row_version == 1
    assert locked.prepared_by_you is True
    assert _state(connection, other, store_id, draft.agent_run_id)[1].prepared_by_you is False

    # Attested from nothing but the progress read, too.
    manual.attest(
        connection,
        ManualSendAttestationCommand(
            locked.envelope_id,
            locked.resource_version,
            locked.rendered_hash,
            operator,
            uuid4(),
            datetime.now(UTC),
            expected_envelope_row_version=locked.envelope_row_version,
        ),
    )
    _, recorded = _state(connection, operator, store_id, draft.agent_run_id)
    assert recorded.envelope_status == "MANUAL_SEND_RECORDED"
    assert recorded.envelope_row_version == 2


def test_the_latest_approval_is_the_one_reported_and_a_lapsed_one_says_so(
    connection: Any,
) -> None:
    operator = _staff(StaffRole.OPERATOR)
    store_id = _store(connection, operator)
    draft = seed_message_draft(connection, store_id)
    lapsed = _request(
        connection, store_id, draft.agent_run_id, operator, at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    _, old = _state(connection, operator, store_id, draft.agent_run_id)
    assert old.approval_request_id == lapsed
    # Stored as it was written; the database clock says it has lapsed.
    assert old.status == "REQUESTED"
    assert old.past_expiry is True

    fresh = _request(connection, store_id, draft.agent_run_id, operator)
    _, new = _state(connection, operator, store_id, draft.agent_run_id)
    assert new.approval_request_id == fresh
    assert new.past_expiry is False


def test_an_edit_after_the_approval_leaves_it_bound_to_the_older_revision(
    connection: Any,
) -> None:
    operator, approver = _staff(StaffRole.OPERATOR), _staff(StaffRole.OPS_APPROVER)
    store_id = _store(connection, operator, approver)
    draft = seed_message_draft(connection, store_id)
    _request(connection, store_id, draft.agent_run_id, operator)
    ShadowConsoleRepository().decide_draft(
        connection,
        agent_run_id=draft.agent_run_id,
        decision="EDIT",
        principal=approver,
        correlation_id=uuid4(),
        edited_text="Dạ, tiệm giao tận nơi trong hôm nay ạ.",
    )

    binding, progress = _state(connection, operator, store_id, draft.agent_run_id)

    assert binding.resource_version == EDITED_TEXT_REVISION
    assert progress.resource_version == 1
    assert progress.rendered_hash != binding.rendered_hash


def test_another_stores_approval_over_the_same_identifier_never_surfaces(
    connection: Any,
) -> None:
    """The progress read is bounded by the store the caller was admitted to, not just the id.

    No legitimate path raises an approval in shop B over shop A's draft -- `request` resolves the
    draft in its own store -- so the row is written directly, newer than ours, to prove the read's
    own `store_id` bound rather than an upstream refusal.
    """

    ours, theirs = _staff(StaffRole.OPERATOR), _staff(StaffRole.OPERATOR)
    our_store, their_store = _store(connection, ours), _store(connection, theirs)
    draft = seed_message_draft(connection, our_store)
    our_approval = _request(connection, our_store, draft.agent_run_id, ours)
    forged, moment = uuid4(), datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO approval_requests (
                id, action, resource_type, resource_id, resource_version, snapshot_hash,
                rendered_hash, policy_version, required_role, reason_codes, obligations,
                execution_capability, requested_by, requested_at, expires_at, envelope,
                envelope_hash, store_id
            ) VALUES (
                %s, 'SEND_MESSAGE', 'MESSAGE_DRAFT', %s, 1, %s, %s, 'manual-send-policy-v1',
                'OPS_APPROVER', '[]', '[]', 'MANUAL_SEND', %s, %s, %s, '{}', %s, %s
            )
            """,
            (
                forged,
                draft.agent_run_id,
                "JCS-SHA256-V1:" + "1" * 64,
                "JCS-SHA256-V1:" + "2" * 64,
                theirs.staff_user_id,
                moment + timedelta(minutes=1),
                moment + timedelta(hours=1),
                "JCS-SHA256-V1:" + uuid4().hex * 2,
                their_store,
            ),
        )
        cursor.execute(
            """
            INSERT INTO approval_request_states (approval_request_id, status, row_version,
                                                 updated_at)
            VALUES (%s, 'APPROVED', 1, %s)
            """,
            (forged, moment),
        )

    _, progress = _state(connection, ours, our_store, draft.agent_run_id)

    assert progress.approval_request_id == our_approval
    assert progress.status == "REQUESTED"
