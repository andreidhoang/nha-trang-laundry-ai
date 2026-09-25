"""Post-approval edit preflight using the real PostgreSQL approval gate.

This path seeds a synthetic draft, approves its first revision, records a reviewer's edit, then
attempts to claim the approval with the edited revision.  It never invokes an outbox provider.

Since `API-INTEGRITY-002` the digests are the server's, derived from the stored draft; the fixture's
declared digests are placeholders a real draft cannot have. Its revisions -- 1 approved, 2 after the
edit -- are checked against the derived ones, and its demand that the rendering change is kept.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalExecutionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_domain.catalog import ActorRole, ApprovalAction

from .fixtures import SyntheticFixtureBundle
from .synthetic_store import current_message_binding, seed_message_draft, seed_store_membership

#: The reviewer's replacement text: the edit that must invalidate the approval of revision 1.
EDITED_TEXT = "Dạ, đồ đã xong, tiệm mở cửa đến 21 giờ ạ."


class SyntheticApprovalError(ValueError):
    """The approved-edit fixture cannot be evaluated safely."""


@dataclass(frozen=True, slots=True)
class _MessageBinding:
    resource_version: int
    snapshot_hash: str
    rendered_hash: str


@dataclass(frozen=True, slots=True)
class SyntheticPostApprovalEditPreflight:
    """Hash-only outcome of the real approval-execution boundary."""

    rendered_hash_mismatch_detected: bool
    new_revision_and_approval_required: bool
    provider_attempted: bool
    trace_id: str


def execute_post_approval_edit_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticPostApprovalEditPreflight:
    """Reject an edited message before an approval execution record can be created.

    ``ApprovalRepository.claim_execution`` is the pre-provider gate.  A stale rendered hash keeps
    the approval in ``APPROVED`` and rolls back the execution insert, requiring the edited revision
    to obtain a new approval before an outbox worker can proceed.
    """

    approved, edited = _hash_bindings(fixture.payload)
    occurred_at = _fixture_time(fixture.payload)
    repository = ApprovalRepository()
    requester = _principal(StaffRole.OPS_APPROVER)
    owner = _principal(StaffRole.OWNER_ADMIN)
    store_id = seed_store_membership(
        connection, principals=(requester, owner), occurred_at=occurred_at
    )
    resource_id, derived = seed_message_draft(
        connection,
        store_id=store_id,
        conversation_binding_id=uuid4(),
        contact_binding_id=uuid4(),
        occurred_at=occurred_at,
    )
    if derived.resource_version != approved.resource_version:
        raise SyntheticApprovalError("fixture approved revision is not the draft's revision")
    approved = _MessageBinding(
        derived.resource_version, derived.snapshot_hash, derived.rendered_hash
    )
    request = ApprovalRequestCommand(
        ApprovalAction.SEND_MESSAGE,
        "MESSAGE_DRAFT",
        resource_id,
        approved.resource_version,
        approved.snapshot_hash,
        approved.rendered_hash,
        "synthetic-approval-policy-v1",
        requester.staff_user_id,
        f"synthetic-post-approval-edit-{uuid4().hex}",
        uuid4(),
        occurred_at,
        store_id=store_id,
    )
    created = repository.request(connection, request)
    repository.decide(
        connection,
        ApprovalDecisionCommand(
            created.approval_request_id,
            ApprovalDecision.APPROVED,
            approved.resource_version,
            approved.snapshot_hash,
            approved.rendered_hash,
            "SYNTHETIC_HUMAN_REVIEW",
            owner,
            uuid4(),
            occurred_at + timedelta(seconds=1),
        ),
    )

    # The edit itself, by a reviewer, which is what moves a draft to its next revision.
    ShadowConsoleRepository().decide_draft(
        connection,
        agent_run_id=resource_id,
        decision="EDIT",
        principal=owner,
        correlation_id=uuid4(),
        edited_text=EDITED_TEXT,
        now=occurred_at + timedelta(seconds=1, milliseconds=500),
    )
    edited_derived = current_message_binding(connection, resource_id)
    if edited_derived.resource_version != edited.resource_version:
        raise SyntheticApprovalError("fixture edited revision is not the draft's revision")
    edited = _MessageBinding(
        edited_derived.resource_version, edited_derived.snapshot_hash, edited_derived.rendered_hash
    )
    if edited.rendered_hash == approved.rendered_hash:
        raise SyntheticApprovalError("the edit did not change the rendered hash")

    mismatch_detected = False
    try:
        repository.claim_execution(
            connection,
            ApprovalExecutionCommand(
                created.approval_request_id,
                ActorRole.OUTBOX_WORKER,
                edited.resource_version,
                edited.snapshot_hash,
                edited.rendered_hash,
                "synthetic-approval-policy-v1",
                uuid4(),
                occurred_at + timedelta(seconds=2),
            ),
        )
    except ApprovalStateError as error:
        if "hash is stale" not in str(error):
            raise SyntheticApprovalError(
                "approval execution was rejected for an unexpected reason"
            ) from error
        mismatch_detected = True
    if not mismatch_detected:
        raise SyntheticApprovalError("edited content was accepted for approval execution")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM approval_request_states WHERE approval_request_id = %s",
            (created.approval_request_id,),
        )
        state = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM approval_executions WHERE approval_request_id = %s",
            (created.approval_request_id,),
        )
        execution_count = cursor.fetchone()
    if state != ("APPROVED",) or execution_count != (0,):
        raise SyntheticApprovalError("stale approval changed state or reached execution")
    return SyntheticPostApprovalEditPreflight(
        rendered_hash_mismatch_detected=True,
        new_revision_and_approval_required=True,
        provider_attempted=False,
        trace_id="synthetic-post-approval-edit-001",
    )


def _hash_bindings(payload: Mapping[str, Any]) -> tuple[_MessageBinding, _MessageBinding]:
    seed = payload.get("database_seed")
    if not isinstance(seed, Mapping):
        raise SyntheticApprovalError("fixture database seed is invalid")
    approved = seed.get("approved_message")
    edited = seed.get("edited_message")
    if not isinstance(approved, Mapping) or not isinstance(edited, Mapping):
        raise SyntheticApprovalError("fixture requires approved and edited message bindings")
    approved_binding = _message_binding(approved)
    edited_binding = _message_binding(edited)
    if approved_binding.rendered_hash == edited_binding.rendered_hash:
        raise SyntheticApprovalError("fixture must change rendered hash after approval")
    return approved_binding, edited_binding


def _message_binding(value: Mapping[str, Any]) -> _MessageBinding:
    resource_version = value.get("resource_version")
    snapshot_hash = value.get("snapshot_hash")
    rendered_hash = value.get("rendered_hash")
    if (
        not isinstance(resource_version, int)
        or not isinstance(snapshot_hash, str)
        or not isinstance(rendered_hash, str)
    ):
        raise SyntheticApprovalError("fixture message bindings are malformed")
    return _MessageBinding(resource_version, snapshot_hash, rendered_hash)


def _fixture_time(payload: Mapping[str, Any]) -> datetime:
    value = payload.get("clock")
    if not isinstance(value, str):
        raise SyntheticApprovalError("fixture clock is invalid")
    try:
        occurred_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SyntheticApprovalError("fixture clock is invalid") from error
    if occurred_at.tzinfo is None:
        raise SyntheticApprovalError("fixture clock must include timezone")
    return occurred_at.astimezone(UTC)


def _principal(role: StaffRole) -> StaffPrincipal:
    return StaffPrincipal(
        uuid4(), f"synthetic-{role.value.casefold()}-{uuid4().hex}", frozenset({role}), True
    )
