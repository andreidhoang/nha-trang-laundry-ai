"""P0 manual-attestation versus outbox-worker exclusivity preflight."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_contracts import AgentDeploymentStage
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalExecutionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.manual_sends import (
    ManualSendAttestationCommand,
    ManualSendPrepareCommand,
    ManualSendRepository,
)
from nha_trang_laundry_domain.catalog import ActorRole, ApprovalAction

from .fixtures import SyntheticFixtureBundle


class SyntheticManualSendError(ValueError):
    """A synthetic manual-send fixture does not meet the exact lock contract."""


@dataclass(frozen=True, slots=True)
class SyntheticManualSendPreflight:
    worker_execution_rejected_by_exclusive_token: bool
    exactly_one_execution_path_recorded: bool
    manual_send_recorded: bool
    provider_attempted: bool
    trace_id: str


def execute_manual_worker_double_send_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticManualSendPreflight:
    """Attest one synthetic transactional send then prove the worker path is closed."""

    binding = _binding(fixture.payload)
    occurred_at = _fixture_time(fixture.payload)
    approvals = ApprovalRepository()
    requester = _principal(StaffRole.OPS_APPROVER)
    owner = _principal(StaffRole.OWNER_ADMIN)
    sender = _principal(StaffRole.OPERATOR)
    approval = approvals.request(
        connection,
        ApprovalRequestCommand(
            ApprovalAction.SEND_MESSAGE,
            "MESSAGE_DRAFT",
            uuid4(),
            binding.resource_version,
            binding.snapshot_hash,
            binding.rendered_hash,
            "synthetic-manual-send-policy-v1",
            requester.staff_user_id,
            f"synthetic-manual-send-{uuid4().hex}",
            uuid4(),
            occurred_at,
        ),
    )
    approvals.decide(
        connection,
        ApprovalDecisionCommand(
            approval.approval_request_id,
            ApprovalDecision.APPROVED,
            binding.resource_version,
            binding.snapshot_hash,
            binding.rendered_hash,
            owner,
            uuid4(),
            occurred_at + timedelta(seconds=1),
        ),
    )
    manual = ManualSendRepository()
    prepared = manual.prepare(
        connection,
        ManualSendPrepareCommand(
            approval.approval_request_id,
            binding.resource_version,
            binding.snapshot_hash,
            binding.rendered_hash,
            binding.recipient_binding_id,
            binding.channel,
            "TRANSACTIONAL",
            AgentDeploymentStage.SHADOW,
            sender,
            uuid4(),
            occurred_at + timedelta(seconds=2),
        ),
    )
    recorded = manual.attest(
        connection,
        ManualSendAttestationCommand(
            prepared.manual_send_envelope_id,
            binding.resource_version,
            binding.rendered_hash,
            sender,
            uuid4(),
            occurred_at + timedelta(seconds=3),
            occurred_at + timedelta(seconds=4),
        ),
    )
    worker_rejected = False
    try:
        approvals.claim_execution(
            connection,
            ApprovalExecutionCommand(
                approval.approval_request_id,
                ActorRole.OUTBOX_WORKER,
                binding.resource_version,
                binding.snapshot_hash,
                binding.rendered_hash,
                "synthetic-manual-send-policy-v1",
                uuid4(),
                occurred_at + timedelta(seconds=5),
            ),
        )
    except ApprovalStateError as error:
        if "reserved for manual send" not in str(error):
            raise SyntheticManualSendError(
                "worker claim failed for an unexpected reason"
            ) from error
        worker_rejected = True
    if not worker_rejected or recorded.status != "MANUAL_SEND_RECORDED":
        raise SyntheticManualSendError("manual-send lock did not consume the worker path")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM manual_send_attestations WHERE manual_send_envelope_id = %s",
            (prepared.manual_send_envelope_id,),
        )
        attestation_count = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM approval_executions WHERE approval_request_id = %s",
            (approval.approval_request_id,),
        )
        worker_execution_count = cursor.fetchone()
    if attestation_count != (1,) or worker_execution_count != (0,):
        raise SyntheticManualSendError("more than one send execution path was recorded")
    return SyntheticManualSendPreflight(
        worker_execution_rejected_by_exclusive_token=True,
        exactly_one_execution_path_recorded=True,
        manual_send_recorded=True,
        provider_attempted=False,
        trace_id="synthetic-manual-worker-double-send-001",
    )


@dataclass(frozen=True, slots=True)
class _Binding:
    resource_version: int
    snapshot_hash: str
    rendered_hash: str
    recipient_binding_id: UUID
    channel: str


def _binding(payload: Mapping[str, Any]) -> _Binding:
    seed = payload.get("database_seed")
    context = payload.get("authenticated_context")
    if not isinstance(seed, Mapping) or not isinstance(context, Mapping):
        raise SyntheticManualSendError("manual-send fixture is invalid")
    message = seed.get("approved_message")
    if not isinstance(message, Mapping):
        raise SyntheticManualSendError("manual-send fixture has no approved message binding")
    version = message.get("resource_version")
    snapshot_hash = message.get("snapshot_hash")
    rendered_hash = message.get("rendered_hash")
    recipient = context.get("contact_binding_id")
    channel = context.get("channel")
    if (
        not isinstance(version, int)
        or not isinstance(snapshot_hash, str)
        or not isinstance(rendered_hash, str)
        or not isinstance(recipient, str)
        or not isinstance(channel, str)
    ):
        raise SyntheticManualSendError("manual-send fixture binding is malformed")
    try:
        return _Binding(version, snapshot_hash, rendered_hash, UUID(recipient), channel)
    except ValueError as error:
        raise SyntheticManualSendError("manual-send recipient binding is invalid") from error


def _fixture_time(payload: Mapping[str, Any]) -> datetime:
    value = payload.get("clock")
    if not isinstance(value, str):
        raise SyntheticManualSendError("manual-send fixture clock is invalid")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SyntheticManualSendError("manual-send fixture clock is invalid") from error
    if timestamp.tzinfo is None:
        raise SyntheticManualSendError("manual-send fixture clock must include timezone")
    return timestamp.astimezone(UTC)


def _principal(role: StaffRole) -> StaffPrincipal:
    return StaffPrincipal(
        uuid4(), f"synthetic-{role.value.casefold()}-{uuid4().hex}", frozenset({role}), True
    )
