"""Exact-hash manual-send attestations for pre-channel Shadow only."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_contracts import AgentDeploymentStage
from nha_trang_laundry_domain.catalog import ApprovalAction

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change


class ManualSendAuthorizationError(PermissionError):
    """A staff principal cannot prepare or attest a manual Shadow send."""


class ManualSendStateError(ValueError):
    """A manual-send envelope is stale, consumed, or incompatible with its approval."""


@dataclass(frozen=True)
class ManualSendPrepareCommand:
    approval_request_id: UUID
    observed_resource_version: int
    observed_snapshot_hash: str
    observed_rendered_hash: str
    recipient_binding_id: UUID
    channel: str
    purpose: str
    deployment_stage: AgentDeploymentStage
    principal: StaffPrincipal
    correlation_id: UUID
    prepared_at: datetime | None = None


@dataclass(frozen=True)
class ManualSendAttestationCommand:
    manual_send_envelope_id: UUID
    observed_resource_version: int
    exact_rendered_hash: str
    principal: StaffPrincipal
    correlation_id: UUID
    sent_at: datetime | None = None
    recorded_at: datetime | None = None


@dataclass(frozen=True)
class StoredManualSend:
    manual_send_envelope_id: UUID
    approval_request_id: UUID
    status: str
    recipient_binding_id: UUID
    rendered_hash: str


class ManualSendRepository:
    """Create a manual lock before copy and consume it with one staff attestation.

    The same approval row is locked by ``ApprovalRepository.claim_execution``.  An envelope in
    either manual state makes that worker path fail closed; a worker claim that wins first makes the
    manual lock fail closed.
    """

    def prepare(self, connection: Any, command: ManualSendPrepareCommand) -> StoredManualSend:
        prepared_at = command.prepared_at or datetime.now(UTC)
        _require_manual_sender(command.principal)
        if command.deployment_stage is not AgentDeploymentStage.SHADOW:
            raise ManualSendStateError("manual send is permitted only in shadow stage")
        if command.purpose != "TRANSACTIONAL":
            raise ManualSendStateError("manual marketing send is not supported")
        channel = _channel(command.channel)
        with connection.transaction(), connection.cursor() as cursor:
            approval = _lock_approval(cursor, command.approval_request_id)
            _require_approved_transactional_binding(approval, command, prepared_at)
            cursor.execute(
                "SELECT id FROM manual_send_envelopes WHERE approval_request_id = %s",
                (command.approval_request_id,),
            )
            if cursor.fetchone() is not None:
                raise ManualSendStateError("manual-send envelope already exists")
            envelope_id = uuid4()

            def mutation(change_cursor: Any) -> None:
                change_cursor.execute(
                    """
                    INSERT INTO manual_send_envelopes (
                        id, approval_request_id, resource_id, resource_version, snapshot_hash,
                        rendered_hash, recipient_binding_id, channel, purpose, status, row_version,
                        prepared_by, prepared_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'TRANSACTIONAL',
                              'APPROVED_FOR_MANUAL_SEND', 1, %s, %s, %s)
                    """,
                    (
                        envelope_id,
                        command.approval_request_id,
                        _uuid(approval[0]),
                        command.observed_resource_version,
                        command.observed_snapshot_hash,
                        command.observed_rendered_hash,
                        command.recipient_binding_id,
                        channel,
                        command.principal.staff_user_id,
                        prepared_at,
                        prepared_at,
                    ),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="MANUAL_SEND",
                    aggregate_id=envelope_id,
                    aggregate_version=1,
                    event_type="MANUAL_SEND_APPROVED",
                    event_payload={
                        "approval_request_id": str(command.approval_request_id),
                        "resource_version": command.observed_resource_version,
                    },
                    audit_action="MANUAL_SEND_PREPARE",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "message.manual_send_prepared.v1",
                            {"manual_send_envelope_id": str(envelope_id)},
                            f"manual-send:{envelope_id}:prepared",
                        ),
                    ),
                    occurred_at=prepared_at,
                ),
                mutation,
            )
        return StoredManualSend(
            envelope_id,
            command.approval_request_id,
            "APPROVED_FOR_MANUAL_SEND",
            command.recipient_binding_id,
            command.observed_rendered_hash,
        )

    def attest(self, connection: Any, command: ManualSendAttestationCommand) -> StoredManualSend:
        recorded_at = command.recorded_at or datetime.now(UTC)
        sent_at = command.sent_at or recorded_at
        _require_manual_sender(command.principal)
        if sent_at.tzinfo is None or sent_at.utcoffset() is None or sent_at > recorded_at:
            raise ManualSendStateError("manual send timestamp is invalid")
        with connection.transaction(), connection.cursor() as cursor:
            envelope = _lock_envelope(cursor, command.manual_send_envelope_id)
            approval = _lock_approval(cursor, _uuid(envelope[1]))
            if str(envelope[9]) != "APPROVED_FOR_MANUAL_SEND":
                raise ManualSendStateError("manual-send envelope is not attestable")
            if str(approval[7]) != "APPROVED" or recorded_at >= _datetime(approval[6]):
                raise ManualSendStateError("manual-send approval is unavailable")
            if int(
                str(envelope[3])
            ) != command.observed_resource_version or not hmac.compare_digest(
                str(envelope[5]), command.exact_rendered_hash
            ):
                raise ManualSendStateError("manual-send content binding is stale")
            attestation_id = uuid4()

            def mutation(change_cursor: Any) -> None:
                change_cursor.execute(
                    """
                    UPDATE manual_send_envelopes
                    SET status = 'MANUAL_SEND_RECORDED', row_version = row_version + 1,
                        updated_at = %s
                    WHERE id = %s AND status = 'APPROVED_FOR_MANUAL_SEND' AND row_version = %s
                    RETURNING id
                    """,
                    (recorded_at, command.manual_send_envelope_id, int(str(envelope[10]))),
                )
                if change_cursor.fetchone() is None:
                    raise ManualSendStateError("manual-send attestation is stale")
                change_cursor.execute(
                    """
                    INSERT INTO manual_send_attestations (
                        id, manual_send_envelope_id, approval_request_id, exact_rendered_hash,
                        resource_version, actor_id, channel, recipient_binding_id, sent_at,
                        recorded_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        attestation_id,
                        command.manual_send_envelope_id,
                        _uuid(envelope[1]),
                        command.exact_rendered_hash,
                        command.observed_resource_version,
                        command.principal.staff_user_id,
                        str(envelope[7]),
                        _uuid(envelope[6]),
                        sent_at,
                        recorded_at,
                    ),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="MANUAL_SEND",
                    aggregate_id=command.manual_send_envelope_id,
                    aggregate_version=int(str(envelope[10])) + 1,
                    event_type="MANUAL_SEND_RECORDED",
                    event_payload={"approval_request_id": str(envelope[1])},
                    audit_action="MANUAL_SEND_ATTEST",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "message.manual_send_attested.v1",
                            {"manual_send_envelope_id": str(command.manual_send_envelope_id)},
                            f"manual-send:{command.manual_send_envelope_id}:attested",
                        ),
                    ),
                    occurred_at=recorded_at,
                ),
                mutation,
            )
        return StoredManualSend(
            command.manual_send_envelope_id,
            _uuid(envelope[1]),
            "MANUAL_SEND_RECORDED",
            _uuid(envelope[6]),
            str(envelope[5]),
        )


def _lock_approval(cursor: Any, approval_id: UUID) -> tuple[object, ...]:
    cursor.execute(
        """
        SELECT r.resource_id, r.resource_version, r.snapshot_hash, r.rendered_hash,
               r.action, r.policy_version, r.expires_at, s.status, s.row_version, r.requested_by
        FROM approval_requests r JOIN approval_request_states s ON s.approval_request_id = r.id
        WHERE r.id = %s FOR UPDATE OF s
        """,
        (approval_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ManualSendStateError("approval is unavailable")
    return tuple(row)


def _lock_envelope(cursor: Any, envelope_id: UUID) -> tuple[object, ...]:
    cursor.execute(
        """
        SELECT id, approval_request_id, resource_id, resource_version, snapshot_hash, rendered_hash,
               recipient_binding_id, channel, purpose, status, row_version
        FROM manual_send_envelopes WHERE id = %s FOR UPDATE
        """,
        (envelope_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ManualSendStateError("manual-send envelope is unavailable")
    return tuple(row)


def _require_approved_transactional_binding(
    approval: tuple[object, ...], command: ManualSendPrepareCommand, prepared_at: datetime
) -> None:
    if (
        str(approval[4]) != ApprovalAction.SEND_MESSAGE.value
        or str(approval[7]) != "APPROVED"
        or prepared_at >= _datetime(approval[6])
    ):
        raise ManualSendStateError("approval is not available for manual send")
    if (
        int(str(approval[1])) != command.observed_resource_version
        or not hmac.compare_digest(str(approval[2]), command.observed_snapshot_hash)
        or not hmac.compare_digest(str(approval[3]), command.observed_rendered_hash)
    ):
        raise ManualSendStateError("manual-send content binding is stale")


def _require_manual_sender(principal: StaffPrincipal) -> None:
    permitted = {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
    if not principal.mfa_verified or not principal.roles.intersection(permitted):
        raise ManualSendAuthorizationError("MFA and an authorized staff role are required")


def _channel(value: str) -> str:
    normalized = value.strip()
    if normalized != "INTERNAL_TEST":
        raise ManualSendStateError("manual-send channel is not configured")
    return normalized


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ManualSendStateError("stored timestamp is invalid")
    return value


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
