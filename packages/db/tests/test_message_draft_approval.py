"""`API-INTEGRITY-002`, finding 1: what was approved is what is recorded as sent, to whom.

Before this item a `SEND_MESSAGE` envelope named a `MESSAGE_DRAFT` nothing resolved, with digests
the requester typed, and the manual send took its recipient from the request body. So an approval
could bind content that did not exist, and the attestation could name a recipient nobody approved.

Every refusal below is paired with the legitimate path it must not break.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from message_draft_test_data import (
    SeededDraft,
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
    ApprovalStateError,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.manual_sends import (
    ManualSendAttestationCommand,
    ManualSendPrepareCommand,
    ManualSendRepository,
    ManualSendStateError,
)
from nha_trang_laundry_db.message_drafts import MessageDraftBinding
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import ApprovalAction

POLICY = "manual-send-policy-v1"
FORGED = "JCS-SHA256-V1:" + "f" * 64
UNKNOWN_RESOURCE = "the approval names a resource this store does not have"
UNKNOWN_DIGEST = "the approval names a content digest this resource does not have"


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _staff(role: StaffRole) -> StaffPrincipal:
    return StaffPrincipal(
        uuid4(), f"draft-{role.value.casefold()}-{uuid4().hex}", frozenset({role}), True
    )


def _store(connection: Any, *members: StaffPrincipal, store_id: UUID | None = None) -> UUID:
    store_id = store_id or uuid4()
    moment = datetime.now(UTC)
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM stores WHERE id = %s", (store_id,))
        exists = cursor.fetchone() is not None
    if not exists:
        StoreRepository.create(
            connection,
            store_id=store_id,
            name="Cửa hàng thử nghiệm",
            created_by=None,
            correlation_id=uuid4(),
        )
    assigner = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier, subject in [(assigner, f"oidc-{assigner}")] + [
            (m.staff_user_id, m.oidc_subject) for m in members
        ]:
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s) ON CONFLICT (id) DO NOTHING
                """,
                (identifier, subject, moment),
            )
        for member in members:
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1) ON CONFLICT DO NOTHING
                """,
                (member.staff_user_id, store_id, assigner, moment),
            )
    return store_id


def _request(
    requester: StaffPrincipal,
    store_id: UUID,
    resource_id: UUID,
    binding: MessageDraftBinding,
    *,
    resource_version: int | None = None,
    snapshot_hash: str | None = None,
    rendered_hash: str | None = None,
) -> ApprovalRequestCommand:
    return ApprovalRequestCommand(
        ApprovalAction.SEND_MESSAGE,
        "MESSAGE_DRAFT",
        resource_id,
        binding.resource_version if resource_version is None else resource_version,
        snapshot_hash or binding.snapshot_hash,
        rendered_hash or binding.rendered_hash,
        POLICY,
        requester.staff_user_id,
        f"draft-approval-{uuid4().hex}",
        uuid4(),
        store_id=store_id,
    )


class _Shop:
    """One store with a requester, an approver, a sender and a draft, as production would have."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self.requester = _staff(StaffRole.OPERATOR)
        self.approver = _staff(StaffRole.OPS_APPROVER)
        self.sender = _staff(StaffRole.OPERATOR)
        self.store_id = _store(connection, self.requester, self.approver, self.sender)
        self.draft: SeededDraft = seed_message_draft(connection, self.store_id)
        # DEC-033: the manual send checks a published policy and a basis; the customer wrote.
        grant_service_basis(
            connection, self.draft.contact_binding_id, received_at=datetime.now(UTC)
        )

    def approve(self, connection: psycopg.Connection[Any], binding: MessageDraftBinding) -> UUID:
        repository = ApprovalRepository()
        created = repository.request(
            connection, _request(self.requester, self.store_id, self.draft.agent_run_id, binding)
        )
        repository.decide(
            connection,
            ApprovalDecisionCommand(
                created.approval_request_id,
                ApprovalDecision.APPROVED,
                binding.resource_version,
                binding.snapshot_hash,
                binding.rendered_hash,
                "HUMAN_REVIEW_COMPLETE",
                self.approver,
                uuid4(),
            ),
        )
        return created.approval_request_id

    def prepare(
        self, connection: psycopg.Connection[Any], approval_id: UUID, binding: MessageDraftBinding
    ) -> Any:
        return ManualSendRepository().prepare(
            connection,
            ManualSendPrepareCommand(
                approval_request_id=approval_id,
                observed_resource_version=binding.resource_version,
                observed_snapshot_hash=binding.snapshot_hash,
                observed_rendered_hash=binding.rendered_hash,
                channel="INTERNAL_TEST",
                purpose="TRANSACTIONAL",
                deployment_stage=AgentDeploymentStage.SHADOW,
                principal=self.sender,
                correlation_id=uuid4(),
            ),
        )

    def review(self, connection: psycopg.Connection[Any], decision: str, **values: Any) -> None:
        ShadowConsoleRepository().decide_draft(
            connection,
            agent_run_id=self.draft.agent_run_id,
            decision=decision,
            principal=self.approver,
            correlation_id=uuid4(),
            **values,
        )


# --- the envelope must describe a real draft ------------------------------------------------


def test_an_envelope_over_a_draft_that_does_not_exist_is_refused(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(postgres_connection)
    binding = current_binding(postgres_connection, shop.draft.agent_run_id)

    with pytest.raises(ApprovalStateError, match=UNKNOWN_RESOURCE):
        ApprovalRepository().request(
            postgres_connection, _request(shop.requester, shop.store_id, uuid4(), binding)
        )


@pytest.mark.parametrize(
    "forgery",
    [
        {"snapshot_hash": FORGED},
        {"rendered_hash": FORGED},
        {"resource_version": 2},
    ],
    ids=["made-up-snapshot", "made-up-rendering", "revision-that-does-not-exist"],
)
def test_an_envelope_naming_content_the_draft_does_not_have_is_refused(
    postgres_connection: psycopg.Connection[Any], forgery: dict[str, Any]
) -> None:
    shop = _Shop(postgres_connection)
    binding = current_binding(postgres_connection, shop.draft.agent_run_id)

    with pytest.raises(ApprovalStateError, match=UNKNOWN_DIGEST):
        ApprovalRepository().request(
            postgres_connection,
            _request(shop.requester, shop.store_id, shop.draft.agent_run_id, binding, **forgery),
        )


def test_another_stores_draft_is_refused_exactly_like_a_missing_one(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """One message for both, so a member of store B cannot probe which drafts store A holds."""
    theirs = _Shop(postgres_connection)
    requester = _staff(StaffRole.OPERATOR)
    our_store = _store(postgres_connection, requester)
    binding = current_binding(postgres_connection, theirs.draft.agent_run_id)

    with pytest.raises(ApprovalStateError) as refused:
        ApprovalRepository().request(
            postgres_connection,
            _request(requester, our_store, theirs.draft.agent_run_id, binding),
        )
    assert str(refused.value) == UNKNOWN_RESOURCE


def test_a_draft_a_reviewer_rejected_cannot_be_approved_for_sending(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(postgres_connection)
    binding = current_binding(postgres_connection, shop.draft.agent_run_id)
    shop.review(postgres_connection, "REJECT", reason_code="PRICE_NOT_VERIFIABLE")

    with pytest.raises(ApprovalStateError, match=UNKNOWN_RESOURCE):
        ApprovalRepository().request(
            postgres_connection,
            _request(shop.requester, shop.store_id, shop.draft.agent_run_id, binding),
        )


# --- the recipient is the draft's, and nobody else's --------------------------------------


def test_the_manual_send_command_has_no_recipient_for_a_caller_to_choose() -> None:
    assert "recipient_binding_id" not in {f.name for f in fields(ManualSendPrepareCommand)}


def test_the_approved_draft_is_sent_to_the_drafts_own_recipient(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Positive control: the server-computed binding is accepted end to end."""
    shop = _Shop(postgres_connection)
    binding = current_binding(postgres_connection, shop.draft.agent_run_id)
    approval_id = shop.approve(postgres_connection, binding)

    prepared = shop.prepare(postgres_connection, approval_id, binding)
    assert prepared.recipient_binding_id == shop.draft.contact_binding_id

    recorded = ManualSendRepository().attest(
        postgres_connection,
        ManualSendAttestationCommand(
            prepared.manual_send_envelope_id,
            binding.resource_version,
            binding.rendered_hash,
            shop.sender,
            uuid4(),
        ),
    )
    assert recorded.recipient_binding_id == shop.draft.contact_binding_id
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT e.recipient_binding_id, a.recipient_binding_id
            FROM manual_send_envelopes e
            JOIN manual_send_attestations a ON a.manual_send_envelope_id = e.id
            WHERE e.id = %s
            """,
            (prepared.manual_send_envelope_id,),
        )
        assert cursor.fetchone() == (shop.draft.contact_binding_id,) * 2


def test_the_database_refuses_an_envelope_whose_recipient_is_not_the_drafts(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The repository derives the recipient; the schema refuses any row that disagrees with it."""
    shop = _Shop(postgres_connection)
    binding = current_binding(postgres_connection, shop.draft.agent_run_id)
    approval_id = shop.approve(postgres_connection, binding)
    moment = datetime.now(UTC)

    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO manual_send_envelopes (
                id, approval_request_id, resource_id, resource_version, snapshot_hash,
                rendered_hash, recipient_binding_id, channel, purpose, status, row_version,
                prepared_by, prepared_at, updated_at
            ) VALUES (%s, %s, %s, 1, %s, %s, %s, 'INTERNAL_TEST', 'TRANSACTIONAL',
                      'APPROVED_FOR_MANUAL_SEND', 1, %s, %s, %s)
            """,
            (
                uuid4(),
                approval_id,
                shop.draft.agent_run_id,
                binding.snapshot_hash,
                binding.rendered_hash,
                uuid4(),
                shop.sender.staff_user_id,
                moment,
                moment,
            ),
        )


# --- editing invalidates approval (invariant 8) --------------------------------------------


def test_an_edit_after_approval_invalidates_it_and_the_edited_text_can_be_approved(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(postgres_connection)
    original = current_binding(postgres_connection, shop.draft.agent_run_id)
    stale_approval = shop.approve(postgres_connection, original)
    shop.review(postgres_connection, "EDIT", edited_text="Dạ, đồ đã xong, tiệm mở đến 21 giờ ạ.")

    with pytest.raises(ManualSendStateError, match="content binding is stale"):
        shop.prepare(postgres_connection, stale_approval, original)

    edited = current_binding(postgres_connection, shop.draft.agent_run_id)
    assert edited.resource_version == 2
    assert edited.text == "Dạ, đồ đã xong, tiệm mở đến 21 giờ ạ."
    assert edited.rendered_hash != original.rendered_hash
    fresh = shop.approve(postgres_connection, edited)
    prepared = shop.prepare(postgres_connection, fresh, edited)
    assert prepared.recipient_binding_id == shop.draft.contact_binding_id


def test_an_unchanged_approve_by_a_reviewer_keeps_the_revision(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Revisions follow content: approving the agent's words as written changes nothing bound."""
    shop = _Shop(postgres_connection)
    before = current_binding(postgres_connection, shop.draft.agent_run_id)
    approval_id = shop.approve(postgres_connection, before)
    shop.review(postgres_connection, "APPROVE")

    assert current_binding(postgres_connection, shop.draft.agent_run_id) == before
    assert shop.prepare(postgres_connection, approval_id, before).status == (
        "APPROVED_FOR_MANUAL_SEND"
    )


def test_a_draft_rejected_after_its_envelope_was_approved_cannot_be_sent(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(postgres_connection)
    binding = current_binding(postgres_connection, shop.draft.agent_run_id)
    approval_id = shop.approve(postgres_connection, binding)
    shop.review(postgres_connection, "REJECT", reason_code="TONE_NOT_APPROPRIATE")

    with pytest.raises(ManualSendStateError, match="content binding is stale"):
        shop.prepare(postgres_connection, approval_id, binding)


def test_an_edit_between_prepare_and_attest_blocks_the_attestation(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(postgres_connection)
    binding = current_binding(postgres_connection, shop.draft.agent_run_id)
    approval_id = shop.approve(postgres_connection, binding)
    prepared = shop.prepare(postgres_connection, approval_id, binding)
    shop.review(postgres_connection, "EDIT", edited_text="Dạ, tiệm sẽ giao tận nơi ạ.")
    attestation = ManualSendAttestationCommand(
        prepared.manual_send_envelope_id,
        binding.resource_version,
        binding.rendered_hash,
        shop.sender,
        uuid4(),
    )

    with pytest.raises(ManualSendStateError, match="content binding is stale"):
        ManualSendRepository().attest(postgres_connection, attestation)
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM manual_send_attestations WHERE manual_send_envelope_id = %s",
            (prepared.manual_send_envelope_id,),
        )
        assert cursor.fetchone() == (0,)
    # The refusal is not a transient: the same attestation later is still refused.
    with pytest.raises(ManualSendStateError, match="content binding is stale"):
        ManualSendRepository().attest(
            postgres_connection,
            replace(attestation, recorded_at=datetime.now(UTC) + timedelta(seconds=1)),
        )


# --- MESSAGE-DRAFT-BINDING-001: an approval decides the draft as it stands -------------------


def _decide(
    shop: _Shop, approval_id: UUID, binding: MessageDraftBinding, decision: ApprovalDecision
) -> ApprovalDecisionCommand:
    return ApprovalDecisionCommand(
        approval_id,
        decision,
        binding.resource_version,
        binding.snapshot_hash,
        binding.rendered_hash,
        "HUMAN_REVIEW_COMPLETE",
        shop.approver,
        uuid4(),
    )


def _decision_rows(connection: psycopg.Connection[Any], approval_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM approval_decisions WHERE approval_request_id = %s",
            (approval_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("review", ["EDIT", "REJECT"])
def test_a_draft_changed_after_its_envelope_was_raised_cannot_be_approved_but_can_be_refused(
    postgres_connection: psycopg.Connection[Any], review: str
) -> None:
    """The envelope still names revision 1; approving it would record words the draft lost.

    Before this item `decide` compared the decision with the stored envelope only, so the approval
    went through and the ledger said a person approved text a reviewer had since replaced or
    rejected. The refusal is the approval's own stale-binding sentence; refusing stays open, because
    refusing a stale envelope is how an approver clears it from the queue.
    """
    shop = _Shop(postgres_connection)
    original = current_binding(postgres_connection, shop.draft.agent_run_id)
    repository = ApprovalRepository()
    created = repository.request(
        postgres_connection,
        _request(shop.requester, shop.store_id, shop.draft.agent_run_id, original),
    )
    if review == "EDIT":
        shop.review(postgres_connection, "EDIT", edited_text="Dạ, tiệm mở cửa đến 21 giờ ạ.")
    else:
        shop.review(postgres_connection, "REJECT", reason_code="TONE_NOT_APPROPRIATE")

    with pytest.raises(ApprovalStateError, match=r"^RESOURCE_CHANGED_SINCE_REQUEST:"):
        repository.decide(
            postgres_connection,
            _decide(shop, created.approval_request_id, original, ApprovalDecision.APPROVED),
        )
    assert _decision_rows(postgres_connection, created.approval_request_id) == 0

    refused = repository.decide(
        postgres_connection,
        _decide(shop, created.approval_request_id, original, ApprovalDecision.REJECTED),
    )
    assert refused.status == "REJECTED"
    assert _decision_rows(postgres_connection, created.approval_request_id) == 1


def test_an_unchanged_draft_is_still_approved_at_decision_time(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Positive control for the check above, including a reviewer APPROVE that keeps revision 1."""
    shop = _Shop(postgres_connection)
    binding = current_binding(postgres_connection, shop.draft.agent_run_id)
    repository = ApprovalRepository()
    created = repository.request(
        postgres_connection,
        _request(shop.requester, shop.store_id, shop.draft.agent_run_id, binding),
    )
    shop.review(postgres_connection, "APPROVE")

    approved = repository.decide(
        postgres_connection,
        _decide(shop, created.approval_request_id, binding, ApprovalDecision.APPROVED),
    )
    assert approved.status == "APPROVED"
