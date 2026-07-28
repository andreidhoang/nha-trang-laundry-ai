"""Immutable approval envelopes with maker-checker decisions and one-time execution claims."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.approvals import build_approval_envelope
from nha_trang_laundry_domain.catalog import ActorRole, ApprovalAction

from nha_trang_laundry_db.idempotency import (
    IdempotencyRepository,
    IdempotentCommand,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalStateError(ValueError):
    """Raised when an approval is stale, expired, already used, or hash-mismatched."""


class ApprovalAuthorizationError(PermissionError):
    """Raised when a principal cannot decide or execute an approval."""


@dataclass(frozen=True)
class ApprovalRequestCommand:
    action: ApprovalAction
    resource_type: str
    resource_id: UUID
    resource_version: int
    snapshot_hash: str
    rendered_hash: str
    policy_version: str
    requested_by: UUID
    idempotency_key: str
    correlation_id: UUID
    requested_at: datetime | None = None


@dataclass(frozen=True)
class ApprovalDecisionCommand:
    approval_request_id: UUID
    decision: ApprovalDecision
    observed_resource_version: int
    observed_snapshot_hash: str
    observed_rendered_hash: str
    principal: StaffPrincipal
    correlation_id: UUID
    decided_at: datetime | None = None


@dataclass(frozen=True)
class ApprovalExecutionCommand:
    approval_request_id: UUID
    worker_role: ActorRole
    observed_resource_version: int
    observed_snapshot_hash: str
    observed_rendered_hash: str
    observed_policy_version: str
    correlation_id: UUID
    claimed_at: datetime | None = None


@dataclass(frozen=True)
class StoredApproval:
    approval_request_id: UUID
    status: str
    envelope_hash: str
    required_role: ActorRole
    expires_at: datetime
    replayed: bool = False


class ApprovalRepository:
    """Persist server-derived approval policy and enforce exact, expiring, one-time use."""

    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()

    def request(self, connection: Any, command: ApprovalRequestCommand) -> StoredApproval:
        requested_at = command.requested_at or datetime.now(UTC)
        request_payload: dict[str, object] = {
            "action": command.action.value,
            "resource_type": command.resource_type,
            "resource_id": str(command.resource_id),
            "resource_version": command.resource_version,
            "snapshot_hash": command.snapshot_hash,
            "rendered_hash": command.rendered_hash,
            "policy_version": command.policy_version,
            "requested_by": str(command.requested_by),
        }
        scope = (
            f"approval:{command.resource_type}:{command.resource_id}:"
            f"{command.resource_version}:{command.action.value}"
        )

        def create() -> dict[str, object]:
            approval_id = uuid4()
            envelope = build_approval_envelope(
                approval_request_id=approval_id,
                action=command.action,
                resource_type=command.resource_type,
                resource_id=command.resource_id,
                resource_version=command.resource_version,
                snapshot_hash=command.snapshot_hash,
                rendered_hash=command.rendered_hash,
                policy_version=command.policy_version,
                requested_by=command.requested_by,
                requested_at=requested_at,
            )
            data = envelope.data

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO approval_requests (
                        id, action, resource_type, resource_id, resource_version, snapshot_hash,
                        rendered_hash, policy_version, required_role, reason_codes, obligations,
                        execution_capability, requested_by, requested_at, expires_at,
                        envelope, envelope_hash
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                        %s, %s, %s, %s, %s::jsonb, %s
                    )
                    """,
                    (
                        data.approval_request_id,
                        data.action.value,
                        data.resource_type,
                        data.resource_id,
                        data.resource_version,
                        data.snapshot_hash,
                        data.rendered_hash,
                        data.policy_version,
                        data.required_role.value,
                        json.dumps(data.reason_codes),
                        json.dumps(data.obligations),
                        data.execution_capability,
                        data.requested_by,
                        data.requested_at,
                        data.expires_at,
                        envelope.document.canonical_json.decode("utf-8"),
                        envelope.document.snapshot_hash,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO approval_request_states (
                        approval_request_id, status, row_version, updated_at
                    ) VALUES (%s, 'REQUESTED', 1, %s)
                    """,
                    (approval_id, requested_at),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="APPROVAL",
                    aggregate_id=approval_id,
                    aggregate_version=1,
                    event_type="APPROVAL_REQUESTED",
                    event_payload={
                        "action": data.action.value,
                        "resource_id": str(data.resource_id),
                        "resource_version": data.resource_version,
                        "envelope_hash": envelope.document.snapshot_hash,
                    },
                    audit_action="APPROVAL_REQUEST",
                    actor_type="STAFF",
                    actor_id=data.requested_by,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "approval.requested.v1",
                            {
                                "approval_request_id": str(approval_id),
                                "required_role": data.required_role.value,
                                "expires_at": data.expires_at.isoformat(),
                            },
                            f"approval:{approval_id}:requested",
                        ),
                    ),
                    occurred_at=requested_at,
                ),
                mutation,
            )
            return {
                "approval_request_id": str(approval_id),
                "status": "REQUESTED",
                "envelope_hash": envelope.document.snapshot_hash,
                "required_role": data.required_role.value,
                "expires_at": data.expires_at,
            }

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(scope, command.idempotency_key, request_payload, requested_at),
            create,
        )
        return _stored_approval(result.response, result.replayed)

    def decide(self, connection: Any, command: ApprovalDecisionCommand) -> StoredApproval:
        decided_at = command.decided_at or datetime.now(UTC)
        expired = False
        stored: StoredApproval | None = None
        with connection.transaction(), connection.cursor() as cursor:
            row = _lock_approval(cursor, command.approval_request_id)
            _authorize_decision(row, command.principal)
            _require_exact_binding(
                row,
                command.observed_resource_version,
                command.observed_snapshot_hash,
                command.observed_rendered_hash,
            )
            if str(row[10]) != "REQUESTED":
                raise ApprovalStateError("approval is not pending")
            if decided_at >= _datetime(row[8]):
                _change_approval_state(
                    connection,
                    approval_id=command.approval_request_id,
                    old_version=int(str(row[11])),
                    new_status="EXPIRED",
                    event_type="APPROVAL_EXPIRED",
                    audit_action="APPROVAL_EXPIRE",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    occurred_at=decided_at,
                )
                expired = True
            else:
                decision_id = uuid4()

                def mutation(change_cursor: Any) -> None:
                    change_cursor.execute(
                        """
                        UPDATE approval_request_states
                        SET status = %s, row_version = row_version + 1, updated_at = %s
                        WHERE approval_request_id = %s AND status = 'REQUESTED'
                            AND row_version = %s
                        RETURNING approval_request_id
                        """,
                        (
                            command.decision.value,
                            decided_at,
                            command.approval_request_id,
                            int(str(row[11])),
                        ),
                    )
                    if change_cursor.fetchone() is None:
                        raise ApprovalStateError("approval decision is stale")
                    change_cursor.execute(
                        """
                        INSERT INTO approval_decisions (
                            id, approval_request_id, decision, decided_by,
                            observed_resource_version, observed_snapshot_hash,
                            observed_rendered_hash, decided_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            decision_id,
                            command.approval_request_id,
                            command.decision.value,
                            command.principal.staff_user_id,
                            command.observed_resource_version,
                            command.observed_snapshot_hash,
                            command.observed_rendered_hash,
                            decided_at,
                        ),
                    )

                next_version = int(str(row[11])) + 1
                commit_material_change(
                    connection,
                    MaterialChange(
                        aggregate_type="APPROVAL",
                        aggregate_id=command.approval_request_id,
                        aggregate_version=next_version,
                        event_type="APPROVAL_DECIDED",
                        event_payload={"decision": command.decision.value},
                        audit_action="APPROVAL_DECIDE",
                        actor_type="STAFF",
                        actor_id=command.principal.staff_user_id,
                        correlation_id=command.correlation_id,
                        outbox_events=(
                            OutboxEvent(
                                "approval.decided.v1",
                                {
                                    "approval_request_id": str(command.approval_request_id),
                                    "decision": command.decision.value,
                                },
                                f"approval:{command.approval_request_id}:decision",
                            ),
                        ),
                        occurred_at=decided_at,
                    ),
                    mutation,
                )
                stored = StoredApproval(
                    command.approval_request_id,
                    command.decision.value,
                    str(row[9]),
                    ActorRole(str(row[6])),
                    _datetime(row[8]),
                )
        if expired:
            raise ApprovalStateError("approval expired")
        if stored is None:
            raise ApprovalStateError("approval decision did not complete")
        return stored

    def claim_execution(self, connection: Any, command: ApprovalExecutionCommand) -> StoredApproval:
        if command.worker_role is not ActorRole.OUTBOX_WORKER:
            raise ApprovalAuthorizationError("only OUTBOX_WORKER may execute approvals")
        claimed_at = command.claimed_at or datetime.now(UTC)
        expired = False
        stored: StoredApproval | None = None
        with connection.transaction(), connection.cursor() as cursor:
            row = _lock_approval(cursor, command.approval_request_id)
            if str(row[10]) != "APPROVED":
                raise ApprovalStateError("approval is not executable")
            cursor.execute(
                """
                SELECT status FROM manual_send_envelopes WHERE approval_request_id = %s
                """,
                (command.approval_request_id,),
            )
            manual_send = cursor.fetchone()
            if manual_send is not None:
                raise ApprovalStateError("approval is reserved for manual send")
            if claimed_at >= _datetime(row[8]):
                _change_approval_state(
                    connection,
                    approval_id=command.approval_request_id,
                    old_version=int(str(row[11])),
                    new_status="EXPIRED",
                    event_type="APPROVAL_EXPIRED",
                    audit_action="APPROVAL_EXPIRE",
                    actor_type="OUTBOX_WORKER",
                    actor_id=None,
                    correlation_id=command.correlation_id,
                    occurred_at=claimed_at,
                )
                expired = True
            else:
                _require_exact_binding(
                    row,
                    command.observed_resource_version,
                    command.observed_snapshot_hash,
                    command.observed_rendered_hash,
                )
                if not hmac.compare_digest(str(row[5]), command.observed_policy_version):
                    raise ApprovalStateError("approval policy version is stale")
                execution_id = uuid4()

                def mutation(change_cursor: Any) -> None:
                    change_cursor.execute(
                        """
                        UPDATE approval_request_states
                        SET status = 'EXECUTING', row_version = row_version + 1, updated_at = %s
                        WHERE approval_request_id = %s AND status = 'APPROVED'
                            AND row_version = %s
                        RETURNING approval_request_id
                        """,
                        (claimed_at, command.approval_request_id, int(str(row[11]))),
                    )
                    if change_cursor.fetchone() is None:
                        raise ApprovalStateError("approval execution claim is stale")
                    change_cursor.execute(
                        """
                        INSERT INTO approval_executions (
                            id, approval_request_id, claimed_by, observed_resource_version,
                            observed_snapshot_hash, observed_rendered_hash, claimed_at
                        ) VALUES (%s, %s, 'OUTBOX_WORKER', %s, %s, %s, %s)
                        """,
                        (
                            execution_id,
                            command.approval_request_id,
                            command.observed_resource_version,
                            command.observed_snapshot_hash,
                            command.observed_rendered_hash,
                            claimed_at,
                        ),
                    )

                next_version = int(str(row[11])) + 1
                commit_material_change(
                    connection,
                    MaterialChange(
                        aggregate_type="APPROVAL",
                        aggregate_id=command.approval_request_id,
                        aggregate_version=next_version,
                        event_type="APPROVAL_EXECUTION_CLAIMED",
                        event_payload={"worker_role": command.worker_role.value},
                        audit_action="APPROVAL_EXECUTION_CLAIM",
                        actor_type="OUTBOX_WORKER",
                        actor_id=None,
                        correlation_id=command.correlation_id,
                        outbox_events=(
                            OutboxEvent(
                                "approval.execution_claimed.v1",
                                {"approval_request_id": str(command.approval_request_id)},
                                f"approval:{command.approval_request_id}:execution-claimed",
                            ),
                        ),
                        occurred_at=claimed_at,
                    ),
                    mutation,
                )
                stored = StoredApproval(
                    command.approval_request_id,
                    "EXECUTING",
                    str(row[9]),
                    ActorRole(str(row[6])),
                    _datetime(row[8]),
                )
        if expired:
            raise ApprovalStateError("approval expired")
        if stored is None:
            raise ApprovalStateError("approval execution claim did not complete")
        return stored

    @staticmethod
    def list_pending(cursor: Any, *, limit: int = 100) -> tuple[StoredApproval, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("approval queue limit must be between 1 and 200")
        cursor.execute(
            """
            SELECT r.id, s.status, r.envelope_hash, r.required_role, r.expires_at
            FROM approval_requests r
            JOIN approval_request_states s ON s.approval_request_id = r.id
            WHERE s.status = 'REQUESTED'
            ORDER BY r.expires_at, r.id
            LIMIT %s
            """,
            (limit,),
        )
        return tuple(
            StoredApproval(
                _uuid(row[0]), str(row[1]), str(row[2]), ActorRole(str(row[3])), _datetime(row[4])
            )
            for row in cursor.fetchall()
        )


def _lock_approval(cursor: Any, approval_id: UUID) -> tuple[object, ...]:
    cursor.execute(
        """
        SELECT r.resource_id, r.resource_version, r.snapshot_hash, r.rendered_hash,
               r.requested_by, r.policy_version, r.required_role, r.requested_at,
               r.expires_at, r.envelope_hash, s.status, s.row_version
        FROM approval_requests r
        JOIN approval_request_states s ON s.approval_request_id = r.id
        WHERE r.id = %s
        FOR UPDATE OF s
        """,
        (approval_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ApprovalStateError("approval is unavailable")
    return tuple(row)


def _authorize_decision(row: tuple[object, ...], principal: StaffPrincipal) -> None:
    required = StaffRole(str(row[6]))
    if StaffRole.OWNER_ADMIN not in principal.roles and required not in principal.roles:
        raise ApprovalAuthorizationError("approval role is not authorized")
    if not principal.mfa_verified:
        raise ApprovalAuthorizationError("MFA is required for approval decisions")
    if principal.staff_user_id == _uuid(row[4]):
        raise ApprovalAuthorizationError("requester cannot approve their own action")


def _require_exact_binding(
    row: tuple[object, ...], version: int, snapshot_hash: str, rendered_hash: str
) -> None:
    if (
        int(str(row[1])) != version
        or not hmac.compare_digest(str(row[2]), snapshot_hash)
        or not hmac.compare_digest(str(row[3]), rendered_hash)
    ):
        raise ApprovalStateError("approval resource version or hash is stale")


def _change_approval_state(
    connection: Any,
    *,
    approval_id: UUID,
    old_version: int,
    new_status: str,
    event_type: str,
    audit_action: str,
    actor_type: str,
    actor_id: UUID | None,
    correlation_id: UUID,
    occurred_at: datetime,
) -> None:
    def mutation(cursor: Any) -> None:
        cursor.execute(
            """
            UPDATE approval_request_states
            SET status = %s, row_version = row_version + 1, updated_at = %s
            WHERE approval_request_id = %s AND row_version = %s
            RETURNING approval_request_id
            """,
            (new_status, occurred_at, approval_id, old_version),
        )
        if cursor.fetchone() is None:
            raise ApprovalStateError("approval state is stale")

    commit_material_change(
        connection,
        MaterialChange(
            aggregate_type="APPROVAL",
            aggregate_id=approval_id,
            aggregate_version=old_version + 1,
            event_type=event_type,
            event_payload={"status": new_status},
            audit_action=audit_action,
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id,
            outbox_events=(
                OutboxEvent(
                    f"approval.{new_status.casefold()}.v1",
                    {"approval_request_id": str(approval_id), "status": new_status},
                    f"approval:{approval_id}:status:{new_status.casefold()}",
                ),
            ),
            occurred_at=occurred_at,
        ),
        mutation,
    )


def _stored_approval(response: dict[str, object], replayed: bool) -> StoredApproval:
    try:
        expires_at = datetime.fromisoformat(str(response["expires_at"]).replace("Z", "+00:00"))
        return StoredApproval(
            UUID(str(response["approval_request_id"])),
            str(response["status"]),
            str(response["envelope_hash"]),
            ActorRole(str(response["required_role"])),
            expires_at,
            replayed,
        )
    except (KeyError, ValueError) as error:
        raise ApprovalStateError("stored idempotent approval result is invalid") from error


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ApprovalStateError("stored approval timestamp is invalid")
    return value
