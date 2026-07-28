"""Post-approval edit preflight using the real PostgreSQL approval gate.

The fixture has hashes only.  This path creates and approves a synthetic envelope, then attempts
to claim it with the edited revision.  It never invokes an outbox provider.
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
from nha_trang_laundry_domain.catalog import ActorRole, ApprovalAction

from .fixtures import SyntheticFixtureBundle


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
    resource_id = uuid4()
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
            owner,
            uuid4(),
            occurred_at + timedelta(seconds=1),
        ),
    )

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
