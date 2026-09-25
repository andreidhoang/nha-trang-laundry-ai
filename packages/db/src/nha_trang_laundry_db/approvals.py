"""Immutable approval envelopes with maker-checker decisions and one-time execution claims."""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.approvals import build_approval_envelope
from nha_trang_laundry_domain.catalog import ActorRole, ApprovalAction

from nha_trang_laundry_db.consent_egress import (
    EgressRefusedError,
    transactional_suppression_refusal,
)
from nha_trang_laundry_db.idempotency import (
    IdempotencyRepository,
    IdempotentCommand,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalStateError(ValueError):
    """Raised when an approval is stale, expired, already used, or hash-mismatched."""


class ApprovalAuthorizationError(PermissionError):
    """Raised when a principal cannot decide or execute an approval."""


#: `API-INTEGRITY-003`. The machine reason an approval is refused with when the resource it binds,
#: re-resolved by the server at the moment of the decision, is no longer the content the envelope
#: was raised for. It leads the exception's text, as `INVALID_STATE_TRANSITION:` does elsewhere, so
#: the console can match it without parsing the prose that follows.
RESOURCE_CHANGED_SINCE_REQUEST = "RESOURCE_CHANGED_SINCE_REQUEST"


class ApprovalResourceChangedError(ApprovalStateError):
    """The bound resource moved on between the envelope being raised and a person approving it.

    A subclass of `ApprovalStateError` so every caller that already refuses a stale envelope -- the
    routes' `except` tuples, `_raise_operations_error`'s 409 -- refuses this one the same way,
    without learning a new type. What it adds is a distinct reason: "somebody re-priced the quote
    after you were asked" is a different next step from "you were looking at an old copy of this
    envelope", and the approver should be told which one happened.
    """

    reason_code = RESOURCE_CHANGED_SINCE_REQUEST

    def __init__(self) -> None:
        super().__init__(
            f"{RESOURCE_CHANGED_SINCE_REQUEST}: the resource this approval binds no longer has "
            "the content it was requested for"
        )


#: The `execution_capability` of `_COUNTER_ATTESTATION` in `packages/domain`: an action one named
#: staff member records on their own, under `DEC-021` (finalising a quote) and `DEC-029` (choosing
#: a price inside a published band). Read off the stored envelope, never off the live policy table,
#: so an envelope raised while its action still needed the owner keeps needing the owner.
COUNTER_ATTESTED_CAPABILITY = "STAFF_ATTESTED_ACTION"

#: Who may make a counter attestation: the staff on duty at the counter, the same set that may
#: finalise a quote (`operations.QUOTE_ACCEPTANCE_ROLES`). `_COUNTER_ATTESTATION` names `OPERATOR`
#: as the floor; a supervisor or the owner standing at the counter is on duty too.
COUNTER_ATTESTATION_ROLES = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
)


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
    #: The shop this approval belongs to. Named rather than inferred: only two of the thirteen
    #: resource types have a backing table to infer it from, and `MESSAGE_DRAFT` -- the manual-send
    #: path, and a built capability -- is not one of them. Placed last among the required fields so
    #: adding it makes every existing caller fail to compile rather than silently shift.
    store_id: UUID = field(kw_only=True)
    requested_at: datetime | None = None
    # The staff command path audits as STAFF; the agent tool path must not impersonate it.
    actor_type: str = "STAFF"


@dataclass(frozen=True)
class ApprovalDecisionCommand:
    approval_request_id: UUID
    decision: ApprovalDecision
    observed_resource_version: int
    observed_snapshot_hash: str
    observed_rendered_hash: str
    reason_code: str
    principal: StaffPrincipal
    correlation_id: UUID
    decided_at: datetime | None = None
    note: str | None = None


@dataclass(frozen=True)
class ApprovalAttestationCommand:
    """A staff member attesting their own envelope at the counter (`DEC-021`, `DEC-029`).

    The same observed binding a decision carries, and no `decision` field: an attestation is always
    an approval by the person who raised the envelope. Declining to attest is simply not attesting,
    and the envelope expires on its own.
    """

    approval_request_id: UUID
    observed_resource_version: int
    observed_snapshot_hash: str
    observed_rendered_hash: str
    reason_code: str
    principal: StaffPrincipal
    correlation_id: UUID
    attested_at: datetime | None = None


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
    # The binding `decide` demands back, carried so a queue read can hand it to the approver.
    #
    # These were stored from the first migration and simply never projected, so the console could
    # render the queue and could not act on it: `ApprovalDecisionRequest` requires
    # `resource_version`, `snapshot_hash` and `rendered_hash`, none of which any read returned.
    # The decision route was therefore unreachable from the list route's own data, and so was the
    # manual-send envelope, which asks for the same three as `observed_*`. That is the whole of
    # the human side of the A2 gate.
    #
    # Optional, and populated by `list_pending` only. `decide` and `claim_execution` answer about
    # a state change rather than about an envelope, and the request path's return value is
    # persisted for idempotent replay -- adding required keys there would invalidate every
    # already-stored result.
    resource_type: str | None = None
    resource_id: UUID | None = None
    resource_version: int | None = None
    snapshot_hash: str | None = None
    rendered_hash: str | None = None
    # `RANGE-APPROVAL-VISIBILITY-001`. Four actions share the `QUOTE_REVISION` resource type --
    # PRESENT_QUOTE, FINALIZE_QUOTE, APPLY_PROMOTION and SET_RANGE_PRICE -- and only the last one
    # asks its approver to authorise a number that is stored nowhere on the revision. A queue that
    # returns only the resource type cannot tell them apart, so the approvals console treated all
    # four as "linkable to the quote screen, therefore reviewable", which was true of three of
    # them. Populated by `list_pending` alongside the binding, and for the same reason: the console
    # has to know what it is being asked to approve before it can know whether it can show it.
    action: str | None = None
    # `MESSAGE-DRAFT-BINDING-001`. The shop the envelope belongs to, read off the approval row. The
    # queue spans every store the approver is assigned to, and the content read for a
    # `MESSAGE_DRAFT` is store-scoped: without the envelope's own store the console could only
    # guess it from the store selected in the top bar, and would ask the wrong shop for the words
    # whenever the two differed. Populated by `list_pending` only, like the binding above.
    store_id: UUID | None = None


class ApprovalRepository:
    """Persist server-derived approval policy and enforce exact, expiring, one-time use."""

    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()

    def request(self, connection: Any, command: ApprovalRequestCommand) -> StoredApproval:
        requested_at = command.requested_at or datetime.now(UTC)
        with connection.cursor() as cursor:
            # Staff membership is a staff concept, so it is checked on the staff path only. The
            # agent path requests as `AGENT_RUNNER` with `requested_by = claims.run_id` -- an agent
            # run, not a person, and never a row in `staff_store_assignments`. Requiring membership
            # of it would refuse every agent approval rather than secure anything.
            #
            # That path is bound differently and not less: its claims are verified upstream, they
            # carry the store, and `_bound_request` checks the store/contact/conversation tuple
            # against the stored aggregate before this is reached. Either way the approval now
            # records which shop it belongs to, which is what `decide` reads.
            if command.actor_type == "STAFF":
                require_store_membership(
                    cursor,
                    staff_user_id=command.requested_by,
                    store_id=command.store_id,
                    error=ApprovalAuthorizationError,
                )
            _require_resolvable_resource(cursor, command)
            _require_recipient_not_suppressed(cursor, command)
        request_payload: dict[str, object] = {
            "action": command.action.value,
            "resource_type": command.resource_type,
            "resource_id": str(command.resource_id),
            "resource_version": command.resource_version,
            "snapshot_hash": command.snapshot_hash,
            "rendered_hash": command.rendered_hash,
            "policy_version": command.policy_version,
            "requested_by": str(command.requested_by),
            "store_id": str(command.store_id),
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
                        envelope, envelope_hash, store_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                        %s, %s, %s, %s, %s::jsonb, %s, %s
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
                        command.store_id,
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
                    actor_type=command.actor_type,
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

    def decide(
        self,
        connection: Any,
        command: ApprovalDecisionCommand,
        *,
        return_expired: bool = False,
    ) -> StoredApproval:
        _validate_human_decision(command)
        decided_at = command.decided_at or datetime.now(UTC)
        expired = False
        stored: StoredApproval | None = None
        with connection.transaction(), connection.cursor() as cursor:
            row = _lock_approval(cursor, command.approval_request_id)
            _authorize_decision(cursor, row, command.principal)
            _require_exact_binding(
                row,
                command.observed_resource_version,
                command.observed_snapshot_hash,
                command.observed_rendered_hash,
            )
            if str(row[10]) != "REQUESTED":
                raise ApprovalStateError("approval is not pending")
            # `API-INTEGRITY-003`. The echo check above proves the approver was shown the stored
            # envelope; this proves the resource is still what that envelope describes. Only an
            # approval is refused for it: a rejection authorises nothing, and an owner must be able
            # to clear an envelope whose resource has moved on without waiting for it to expire.
            # Skipped for an expired envelope, which keeps the answer and the recorded EXPIRED
            # transition it has always got. A refusal raised here rolls the whole transaction back:
            # nothing about the approval changes and nothing is written (invariant 5).
            if decided_at < _datetime(row[8]) and command.decision is ApprovalDecision.APPROVED:
                _require_resource_unchanged(cursor, row)
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
                stored = StoredApproval(
                    command.approval_request_id,
                    "EXPIRED",
                    str(row[9]),
                    ActorRole(str(row[6])),
                    _datetime(row[8]),
                )
            else:
                _record_decision(
                    connection,
                    row=row,
                    approval_id=command.approval_request_id,
                    decision=command.decision,
                    decided_by=command.principal.staff_user_id,
                    reason_code=command.reason_code,
                    note=command.note,
                    observed_resource_version=command.observed_resource_version,
                    observed_snapshot_hash=command.observed_snapshot_hash,
                    observed_rendered_hash=command.observed_rendered_hash,
                    correlation_id=command.correlation_id,
                    decided_at=decided_at,
                )
                stored = StoredApproval(
                    command.approval_request_id,
                    command.decision.value,
                    str(row[9]),
                    ActorRole(str(row[6])),
                    _datetime(row[8]),
                )
        if expired and not return_expired:
            raise ApprovalStateError("approval expired")
        if stored is None:
            raise ApprovalStateError("approval decision did not complete")
        return stored

    def attest(self, connection: Any, command: ApprovalAttestationCommand) -> StoredApproval:
        """Record a staff member's own counter attestation of the envelope they raised.

        `DEC-029` (2026-09-25) put `SET_RANGE_PRICE` under the attestation `DEC-021` uses to
        finalise a quote: attributed, immutable, no second signature. `decide` cannot carry that --
        it refuses a requester deciding their own envelope for every action, which is the
        maker-checker rule sends, cancellations and every owner-financial action depend on, and it
        stays exactly as it was. This is a separate, narrower door rather than a relaxation of it:

        * only for an envelope whose *stored* capability is `STAFF_ATTESTED_ACTION` and whose stored
          obligations carry no `SEPARATION_OF_DUTY`. The policy is read from the row written when
          the envelope was raised, so reversing `DEC-029` closes this door for every envelope
          raised after the reversal without touching this code;
        * only by the staff member who raised it, because the attestation *is* their statement and
          a colleague's name on it would record the wrong person;
        * with the store taken from the row, the exact binding re-checked and the expiry enforced,
          exactly as a decision is;
        * written as the same `approval_decisions` row a decision writes, with the attester as
          `decided_by`, so every reader of approvals already sees who authorised what, in one
          transaction with its event, audit and outbox rows (invariant 5).
        """

        _validate_reason_code(command.reason_code)
        attested_at = command.attested_at or datetime.now(UTC)
        stored: StoredApproval | None = None
        expired = False
        with connection.transaction(), connection.cursor() as cursor:
            row = _lock_approval(cursor, command.approval_request_id)
            _authorize_attestation(cursor, row, command.principal)
            _require_exact_binding(
                row,
                command.observed_resource_version,
                command.observed_snapshot_hash,
                command.observed_rendered_hash,
            )
            if str(row[10]) != "REQUESTED":
                raise ApprovalStateError("approval is not pending")
            # `API-INTEGRITY-003`, as in `decide`: an attestation is always an approval, so the
            # resource is always re-resolved before one is recorded.
            if attested_at < _datetime(row[8]):
                _require_resource_unchanged(cursor, row)
            if attested_at >= _datetime(row[8]):
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
                    occurred_at=attested_at,
                )
                expired = True
            else:
                _record_decision(
                    connection,
                    row=row,
                    approval_id=command.approval_request_id,
                    decision=ApprovalDecision.APPROVED,
                    decided_by=command.principal.staff_user_id,
                    reason_code=command.reason_code,
                    note=None,
                    observed_resource_version=command.observed_resource_version,
                    observed_snapshot_hash=command.observed_snapshot_hash,
                    observed_rendered_hash=command.observed_rendered_hash,
                    correlation_id=command.correlation_id,
                    decided_at=attested_at,
                    counter_attestation=True,
                )
                stored = StoredApproval(
                    command.approval_request_id,
                    ApprovalDecision.APPROVED.value,
                    str(row[9]),
                    ActorRole(str(row[6])),
                    _datetime(row[8]),
                )
        if expired:
            raise ApprovalStateError("approval expired")
        if stored is None:
            raise ApprovalStateError("approval attestation did not complete")
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
    def list_pending(
        cursor: Any, *, principal: StaffPrincipal, limit: int = 100
    ) -> tuple[StoredApproval, ...]:
        """Return only approvals whose resource belongs to a store the principal is assigned to.

        `approval_requests` carries no store column; it points at a resource. The queue therefore
        resolves each request through `orders` and shows only those the caller may act on. An
        approval whose resource cannot be resolved to a store is **excluded**, because a queue that
        shows an item nobody can attribute to a store is how cross-store disclosure happens. If a
        resource type is later added that is not order-backed, it becomes invisible until it is
        joined here explicitly, which is the fail-closed direction.
        """

        if not 1 <= limit <= 200:
            raise ValueError("approval queue limit must be between 1 and 200")
        # Reads the approval's own store since migration `0034`. It used to join `orders ON
        # o.id = r.resource_id`, which had two consequences: an approval whose resource was not an
        # order could not be attributed and was silently excluded -- so every `MESSAGE_DRAFT`, the
        # entire manual-send queue, was invisible here -- and the store was inferred from a
        # resource rather than read from the approval, which is why `decide` had no store to check
        # membership against at all.
        cursor.execute(
            """
            SELECT r.id, s.status, r.envelope_hash, r.required_role, r.expires_at,
                   r.resource_type, r.resource_id, r.resource_version, r.snapshot_hash,
                   r.rendered_hash, r.action, r.store_id
            FROM approval_requests r
            JOIN approval_request_states s ON s.approval_request_id = r.id
            JOIN staff_store_assignments a
              ON a.store_id = r.store_id AND a.staff_user_id = %s
             AND a.revoked_at IS NULL
            WHERE s.status = 'REQUESTED'
            ORDER BY r.expires_at, r.id
            LIMIT %s
            """,
            (principal.staff_user_id, limit),
        )
        return tuple(
            StoredApproval(
                _uuid(row[0]),
                str(row[1]),
                str(row[2]),
                ActorRole(str(row[3])),
                _datetime(row[4]),
                resource_type=str(row[5]),
                resource_id=_uuid(row[6]),
                resource_version=int(str(row[7])),
                snapshot_hash=str(row[8]),
                rendered_hash=str(row[9]),
                action=str(row[10]),
                store_id=_uuid(row[11]),
            )
            for row in cursor.fetchall()
        )


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """Everything an executor needs to prove an approval is the one it is acting on.

    `RANGE-PRICE-001`. Reading an approval's binding was previously possible only through
    `list_pending`, which by construction returns approvals that have *not* been decided. Acting on
    an approved one -- writing the amount the owner authorised into a quote revision -- needs the
    opposite, and needs it with the store attached so the caller can refuse an envelope belonging to
    another shop before reading anything else.

    This is a read, not a claim. `claim_execution` remains the only one-time claim and remains
    restricted to `OUTBOX_WORKER`; the single-use property on the range-price path comes from the
    revision itself, because applying an approval bound to revision N moves the quote to N+1 and the
    second attempt no longer matches.
    """

    approval_request_id: UUID
    store_id: UUID
    action: ApprovalAction
    resource_type: str
    resource_id: UUID
    resource_version: int
    snapshot_hash: str
    rendered_hash: str
    policy_version: str
    status: str
    expires_at: datetime


def read_approval_binding(cursor: Any, approval_id: UUID) -> ApprovalBinding | None:
    """The stored envelope binding for one approval, or `None` when there is no such approval."""

    cursor.execute(
        """
        SELECT r.id, r.store_id, r.action, r.resource_type, r.resource_id, r.resource_version,
               r.snapshot_hash, r.rendered_hash, r.policy_version, s.status, r.expires_at
        FROM approval_requests r
        JOIN approval_request_states s ON s.approval_request_id = r.id
        WHERE r.id = %s
        """,
        (approval_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return ApprovalBinding(
        approval_request_id=_uuid(row[0]),
        store_id=_uuid(row[1]),
        action=ApprovalAction(str(row[2])),
        resource_type=str(row[3]),
        resource_id=_uuid(row[4]),
        resource_version=int(str(row[5])),
        snapshot_hash=str(row[6]),
        rendered_hash=str(row[7]),
        policy_version=str(row[8]),
        status=str(row[9]),
        expires_at=_datetime(row[10]),
    )


def _lock_approval(cursor: Any, approval_id: UUID) -> tuple[object, ...]:
    cursor.execute(
        """
        SELECT r.resource_id, r.resource_version, r.snapshot_hash, r.rendered_hash,
               r.requested_by, r.policy_version, r.required_role, r.requested_at,
               r.expires_at, r.envelope_hash, s.status, s.row_version, r.action, r.store_id,
               r.resource_type, r.execution_capability, r.obligations
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


#: Which resource types this system can actually locate, and where. Those absent here and from
#: `_COMPUTED_RESOURCES` below name capabilities that are not built (`SLOT_PROPOSAL`, `B2B_TERMS`,
#: ...), and for them the store binding above is the whole of the check -- stated, not implied.
_RESOLVABLE_RESOURCES: dict[str, str] = {
    "ORDER": """
        SELECT o.store_id, o.row_version, o.current_quote_snapshot_hash
        FROM orders o WHERE o.id = %s
    """,
    # `OPS-BOARD-001` gave `EXPORT_REQUEST` a row, so it moved out of the unbuilt list above and
    # into this one. Until it had one, an export envelope could name any UUID at all with invented
    # digests and be approved and frozen into the ledger -- the exact failure the 2026-08-30 fix
    # closed for orders. Now the same three facts are checked: the request exists, it belongs to the
    # store the envelope names, and its stored digest is the one being approved.
    "EXPORT_REQUEST": """
        SELECT e.store_id, e.row_version, e.snapshot_hash
        FROM export_requests e WHERE e.id = %s
    """,
}
#: `QUOTE_REVISION` is deliberately absent, and the reason is worth stating because the first
#: attempt included it and was wrong. Migration `0029` makes `quote_revisions.approval_id` a
#: foreign key, so **the approval is written before the revision it authorises exists**. Requiring
#: the revision to resolve at request time refuses the only order those two writes can happen in.
#: For that type the store binding is the whole of the check at request time. The digest it names
#: used to be described as "verified later by `_require_exact_binding`", which compares it only with
#: the approver's echo of itself; since `API-INTEGRITY-003` it is verified against the quote at
#: decision time by `_require_resource_unchanged`, when the revision it names does exist.


def _message_draft_content(cursor: Any, resource_id: UUID) -> Any:
    # Imported here rather than at the top so this resolver stays one self-contained hunk.
    from nha_trang_laundry_db.message_drafts import read_message_draft_binding

    return read_message_draft_binding(cursor, resource_id)


#: Resource types whose digests are not stored but **computed by the server from stored content**,
#: so an envelope's `resource_version`, `snapshot_hash` *and* `rendered_hash` can all be verified.
#:
#: `API-INTEGRITY-002` moved `MESSAGE_DRAFT` here. It used to be described as content "that lives in
#: the envelope itself", which meant nothing checked it: staff could raise a `SEND_MESSAGE`
#: envelope over any UUID with digests they typed, have it approved, and spend it on a manual send.
#: The draft in fact lives in `agent_drafts` / `agent_draft_reviews`, both append-only, and
#: `message_drafts.py` derives its binding. A draft that does not exist, belongs to another store,
#: or was rejected by a reviewer resolves to `None`, and is refused with the same sentence as a
#: missing row above.
#:
#: Each resolver answers an object with `store_id`, `resource_version`, `snapshot_hash` and
#: `rendered_hash`, or `None`.
_COMPUTED_RESOURCES: dict[str, Callable[[Any, UUID], Any]] = {
    "MESSAGE_DRAFT": _message_draft_content,
}


def _require_computed_resource(cursor: Any, command: ApprovalRequestCommand) -> bool:
    """Verify an envelope against content the server derives. False when the type is not one."""
    resolver = _COMPUTED_RESOURCES.get(command.resource_type)
    if resolver is None:
        return False
    content = resolver(cursor, command.resource_id)
    if content is None or _uuid(content.store_id) != command.store_id:
        raise ApprovalStateError("the approval names a resource this store does not have")
    if (
        int(content.resource_version) != command.resource_version
        or not hmac.compare_digest(str(content.snapshot_hash), command.snapshot_hash)
        or not hmac.compare_digest(str(content.rendered_hash), command.rendered_hash)
    ):
        raise ApprovalStateError("the approval names a content digest this resource does not have")
    return True


def _require_resolvable_resource(cursor: Any, command: ApprovalRequestCommand) -> None:
    """For the two resource types that exist as rows, prove the envelope describes a real one.

    An approval is an immutable, audited artifact asserting that a named person approved a specific
    content digest of a specific resource. Until 2026-08-30 none of that was checked: `resource_id`
    was never resolved, the digests were never compared against anything, and an envelope naming a
    `QUOTE_REVISION` that did not exist, with a fabricated hash and an unpublished policy version,
    was accepted, approved and frozen into the ledger.

    What is checkable is checked here: the resource exists, it belongs to the store the request
    names, and its stored digest is the one being approved. `rendered_hash` is deliberately not
    verified -- it is a digest of a rendering this system does not store, so comparing it against
    anything would be theatre. That limit is real and is recorded in the item's evidence rather
    than papered over. Where the rendering *is* derivable from stored content -- `MESSAGE_DRAFT` --
    `_require_computed_resource` verifies it too.
    """
    if _require_computed_resource(cursor, command):
        return
    query = _RESOLVABLE_RESOURCES.get(command.resource_type)
    if query is None:
        return
    cursor.execute(query, (command.resource_id,))
    row = cursor.fetchone()
    # One message for both branches, deliberately. Separate strings told a member of any store
    # whether a UUID was a real resource in somebody else's shop -- the same one-bit leak the
    # decision path closed two functions away, reintroduced here by the fix that added this check.
    # `store_access` states the rule: the failures are "deliberately indistinguishable to the
    # caller, so probing identifiers teaches nobody which stores exist".
    if row is None or _uuid(row[0]) != command.store_id:
        raise ApprovalStateError("the approval names a resource this store does not have")
    if not hmac.compare_digest(str(row[2]), command.snapshot_hash):
        raise ApprovalStateError("the approval names a content digest this resource does not have")


def _require_recipient_not_suppressed(cursor: Any, command: ApprovalRequestCommand) -> None:
    """`DEC-033` ruling 4(iii): a `SEND_MESSAGE` over a draft whose recipient wrote STOP is refused
    when it is raised, so staff learn now rather than at the send.

    Advisory-early, and deliberately narrower than the guard: it refuses on a TRANSACTIONAL
    suppression on any channel a manual send may use -- SUPPRESSED, or an opt-out awaiting review --
    and judges neither the policy nor a service basis, both of which can change before the send.
    The definitive check is the guard at prepare and again at attest, under the advisory lock.
    """
    if (
        command.action is not ApprovalAction.SEND_MESSAGE
        or command.resource_type != "MESSAGE_DRAFT"
    ):
        return
    # Imported here: `manual_sends` is the owner of its channel list, and importing it at module
    # level would make this module's import order depend on it for one constant.
    from nha_trang_laundry_db.manual_sends import MANUAL_SEND_CHANNELS

    content = _message_draft_content(cursor, command.resource_id)
    if content is None:
        return
    contact = _uuid(content.contact_binding_id)
    for channel in sorted(MANUAL_SEND_CHANNELS):
        refusal = transactional_suppression_refusal(
            cursor, contact_binding_id=contact, channel=channel
        )
        if refusal is not None:
            raise EgressRefusedError(
                refusal, contact_binding_id=contact, channel=channel, store_id=command.store_id
            )


# --- Decision time: is the resource still what the envelope binds? ------------------------------
#
# `API-INTEGRITY-003`. `decide` and `attest` compared the observed binding with the stored envelope
# and nothing else. That proves the approver's client echoed what the server had stored, which is
# the envelope and not the resource: raise an envelope over quote revision 3, re-price the quote to
# revision 4, and an approver who submits the stored hashes exactly approved revision 3 of a quote
# that no longer offers it -- and the record said so with every check green. Invariant 8 says an
# approval binds exact content, so the content is now resolved again, by the server, on the
# transaction that holds the approval's row lock, immediately before the decision row is written.


@dataclass(frozen=True, slots=True)
class CurrentResourceBinding:
    """What a resource is right now, in the terms an envelope binds it."""

    store_id: UUID
    resource_version: int
    snapshot_hash: str
    #: `None` when this server holds no single rendering of the resource type to compare with, which
    #: is stated per type below rather than implied by a comparison that always passes.
    rendered_hash: str | None


def _current_message_draft(cursor: Any, resource_id: UUID) -> CurrentResourceBinding | None:
    """All four facts, computed from the append-only draft and review rows (`API-INTEGRITY-002`).

    A reviewer's EDIT moves the draft to revision 2 and a REJECT leaves nothing sendable, so either
    one after the envelope was raised refuses its approval here rather than at the send.
    """
    content = _message_draft_content(cursor, resource_id)
    if content is None:
        return None
    return CurrentResourceBinding(
        store_id=_uuid(content.store_id),
        resource_version=int(content.resource_version),
        snapshot_hash=str(content.snapshot_hash),
        rendered_hash=str(content.rendered_hash),
    )


def _current_export_request(cursor: Any, resource_id: UUID) -> CurrentResourceBinding | None:
    """Both digests re-derived exactly as the release derives them, so the owner and the release
    are asked the same question. Imported here: `exports` imports this module."""
    from nha_trang_laundry_db.exports import read_export_request_binding

    content = read_export_request_binding(cursor, resource_id)
    if content is None:
        return None
    return CurrentResourceBinding(
        store_id=content.store_id,
        resource_version=content.resource_version,
        snapshot_hash=content.snapshot_hash,
        rendered_hash=content.rendered_hash,
    )


def _current_quote_revision(cursor: Any, resource_id: UUID) -> CurrentResourceBinding | None:
    """The quote's CURRENT revision and its stored snapshot digest.

    The envelope's `resource_version` is a revision number, and a revision is immutable -- so the
    revision it names can never change underneath it. What can change is whether it is still the
    quote's price: re-pricing appends revision N+1 and moves `current_revision`, and an approval of
    N is then an approval of a price nobody is offering any more. That is the change this detects.

    `FOR SHARE` on the container because `QuoteRepository.create_revision` moves `current_revision`
    with an UPDATE of this row: a re-price racing the decision waits for it to commit, then lands as
    a revision after an approval rather than inside one. No path locks a quote and then an approval
    state row, so this adds no lock cycle.

    `rendered_hash` is not compared, and on purpose. Four actions share this resource type, and
    their renderings are different documents from different sources: the agent path binds
    `render_quote_presentation(revision, action)`, the counter's `SET_RANGE_PRICE` binds the amounts
    the staff member chose, and `APPLY_PROMOTION` has no server rendering at all. There is no one
    function of the resource to recompute here. The executors that consume these envelopes check
    their own rendering against content they hold -- `apply_range_prices` re-derives the digest
    from the amounts in hand -- which is where that comparison can mean something.
    """
    cursor.execute(
        """
        SELECT q.store_id, q.current_revision, r.snapshot_hash
        FROM quotes q
        JOIN quote_revisions r ON r.quote_id = q.id AND r.revision = q.current_revision
        WHERE q.id = %s
        FOR SHARE OF q
        """,
        (resource_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return CurrentResourceBinding(
        store_id=_uuid(row[0]),
        resource_version=int(row[1]),
        snapshot_hash=str(row[2]),
        rendered_hash=None,
    )


def _current_order(cursor: Any, resource_id: UUID) -> CurrentResourceBinding | None:
    """The order's row version and the quote digest it was created against.

    The version is compared: every transition bumps it, and cancelling an order the approver last
    saw in another state is not the cancellation they were asked about. `FOR SHARE` for the same
    reason as the quote. No rendering of an order is stored or derivable, so none is compared.
    """
    cursor.execute(
        """
        SELECT o.store_id, o.row_version, o.current_quote_snapshot_hash
        FROM orders o WHERE o.id = %s
        FOR SHARE OF o
        """,
        (resource_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return CurrentResourceBinding(
        store_id=_uuid(row[0]),
        resource_version=int(row[1]),
        snapshot_hash=str(row[2]),
        rendered_hash=None,
    )


def _current_remedy_proposal(cursor: Any, resource_id: UUID) -> CurrentResourceBinding | None:
    """A remedy proposal, bound as `RemedyRepository._request_owner_approval` binds it.

    Version 1 always -- a proposal is one immutable document and `_require_remedy_approval` refuses
    any other -- the order's quote digest as the snapshot, and the proposal's own document digest as
    the rendering. The proposal row is written after its envelope (its `approval_id` is a foreign
    key), which is why request time cannot check it and decision time, by which it exists, can.
    """
    cursor.execute(
        """
        SELECT p.store_id, o.current_quote_snapshot_hash, p.proposal_hash
        FROM remedy_proposals p
        JOIN orders o ON o.id = p.order_id
        WHERE p.id = %s
        """,
        (resource_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return CurrentResourceBinding(
        store_id=_uuid(row[0]),
        resource_version=1,
        snapshot_hash=str(row[1]),
        rendered_hash=str(row[2]),
    )


#: The resource types the server can resolve at decision time, and how.
DECISION_TIME_RESOLVERS: dict[str, Callable[[Any, UUID], CurrentResourceBinding | None]] = {
    "MESSAGE_DRAFT": _current_message_draft,
    "EXPORT_REQUEST": _current_export_request,
    "QUOTE_REVISION": _current_quote_revision,
    "ORDER": _current_order,
    "REMEDY_PROPOSAL": _current_remedy_proposal,
}

#: The resource types that name capabilities this system has not built: no table, no rendering,
#: nothing to resolve. For these the decision keeps exactly the check it had -- the observed binding
#: must equal the stored envelope -- and this set is where that is written down rather than being
#: what a missing dictionary entry happens to do. Together with the resolvers above it must cover
#: every value of `APPROVAL_RESOURCE_TYPES`, and `test_decision_time_resolution.py` holds it to
#: that: building one of these means moving it into `DECISION_TIME_RESOLVERS`, not leaving it here.
UNRESOLVABLE_AT_DECISION: frozenset[str] = frozenset(
    {"SLOT_PROPOSAL", "DELIVERY_FEE_PROPOSAL", "B2B_TERMS", "POLICY_VERSION"}
)


def _require_resource_unchanged(cursor: Any, row: tuple[object, ...]) -> None:
    """Refuse an approval whose resource is no longer the content its envelope binds.

    Compared with the STORED envelope (`row`), not with what the client sent: by the time this runs
    `_require_exact_binding` has already made those equal, and the stored envelope is the authority.
    A resource that no longer resolves at all -- a draft a reviewer rejected, a quote that was never
    there -- is refused the same way and with the same sentence: the approver's next step is the
    same and the caller learns nothing about which it was.

    A resource type in neither table is not waved through. `build_approval_envelope` refuses such a
    type at request time, so reaching this means a row nothing in this repository could have
    written, and an approval over something the server cannot identify is what invariant 8 forbids.
    """
    resource_type = str(row[14])
    resolver = DECISION_TIME_RESOLVERS.get(resource_type)
    if resolver is None:
        if resource_type in UNRESOLVABLE_AT_DECISION:
            return
        raise ApprovalStateError("approval resource type cannot be verified by this server")
    current = resolver(cursor, _uuid(row[0]))
    if (
        current is None
        or current.store_id != _uuid(row[13])
        or current.resource_version != int(str(row[1]))
        or not hmac.compare_digest(current.snapshot_hash, str(row[2]))
        or (
            current.rendered_hash is not None
            and not hmac.compare_digest(current.rendered_hash, str(row[3]))
        )
    ):
        raise ApprovalResourceChangedError()


def _validate_human_decision(command: ApprovalDecisionCommand) -> None:
    _validate_reason_code(command.reason_code)
    if command.note is not None and len(command.note) > 500:
        raise ApprovalStateError("approval note is too long")


def _validate_reason_code(reason_code: str) -> None:
    if re.fullmatch(r"[A-Z][A-Z0-9_]{1,99}", reason_code) is None:
        raise ApprovalStateError("approval reason code is invalid")


def _authorize_attestation(cursor: Any, row: tuple[object, ...], principal: StaffPrincipal) -> None:
    """Who may attest this envelope: its own requester, on duty, for a counter-attested action.

    One opaque refusal for every failed condition, as `_authorize_decision` gives: the caller learns
    that they may not attest this envelope, not which of the conditions measured them out.
    """

    require_store_membership(
        cursor,
        staff_user_id=principal.staff_user_id,
        store_id=_uuid(row[13]),
        error=ApprovalAuthorizationError,
    )
    obligations = row[16] if isinstance(row[16], list) else json.loads(str(row[16]))
    if (
        str(row[15]) != COUNTER_ATTESTED_CAPABILITY
        or "SEPARATION_OF_DUTY" in obligations
        or not principal.roles & COUNTER_ATTESTATION_ROLES
        or not principal.mfa_verified
        or principal.staff_user_id != _uuid(row[4])
    ):
        raise ApprovalAuthorizationError("this approval cannot be attested by this staff member")


def _record_decision(
    connection: Any,
    *,
    row: tuple[object, ...],
    approval_id: UUID,
    decision: ApprovalDecision,
    decided_by: UUID,
    reason_code: str,
    note: str | None,
    observed_resource_version: int,
    observed_snapshot_hash: str,
    observed_rendered_hash: str,
    correlation_id: UUID,
    decided_at: datetime,
    counter_attestation: bool = False,
) -> None:
    """Write one decision with its event, audit and outbox rows. Authorisation is the caller's.

    Shared by `decide` (a second person) and `attest` (the requester, at the counter). The row is
    the same in both cases; `counter_attestation` travels in the event and the audit details so a
    reader of either can tell a one-person attestation from a maker-checker approval without having
    to compare two staff ids.
    """

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
            (decision.value, decided_at, approval_id, int(str(row[11]))),
        )
        if change_cursor.fetchone() is None:
            raise ApprovalStateError("approval decision is stale")
        change_cursor.execute(
            """
            INSERT INTO approval_decisions (
                id, approval_request_id, decision_type, decision, decided_by,
                reason_code, note,
                observed_resource_version, observed_snapshot_hash,
                observed_rendered_hash, decided_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                decision_id,
                approval_id,
                str(row[12]),
                decision.value,
                decided_by,
                reason_code,
                note,
                observed_resource_version,
                observed_snapshot_hash,
                observed_rendered_hash,
                decided_at,
            ),
        )

    event_payload: dict[str, object] = {
        "decision_type": str(row[12]),
        "decision": decision.value,
        "reason_code": reason_code,
    }
    audit_details: dict[str, object] = {
        "decision_type": str(row[12]),
        "decision": decision.value,
        "reason_code": reason_code,
        "resource_version": observed_resource_version,
    }
    if counter_attestation:
        event_payload["counter_attestation"] = True
        audit_details["counter_attestation"] = True
    commit_material_change(
        connection,
        MaterialChange(
            aggregate_type="APPROVAL",
            aggregate_id=approval_id,
            aggregate_version=int(str(row[11])) + 1,
            event_type="APPROVAL_DECIDED",
            event_payload=event_payload,
            audit_action="APPROVAL_DECIDE",
            actor_type="STAFF",
            actor_id=decided_by,
            correlation_id=correlation_id,
            outbox_events=(
                OutboxEvent(
                    "approval.decided.v1",
                    {
                        "approval_request_id": str(approval_id),
                        "decision_type": str(row[12]),
                        "decision": decision.value,
                        "reason_code": reason_code,
                    },
                    f"approval:{approval_id}:decision",
                ),
            ),
            occurred_at=decided_at,
            audit_details=audit_details,
        ),
        mutation,
    )


def _authorize_decision(cursor: Any, row: tuple[object, ...], principal: StaffPrincipal) -> None:
    # The store comes from the locked row, never from the request: a client-supplied identifier is
    # not authority. Until 2026-08-30 this check did not exist at all and `_lock_approval` did not
    # even select a store -- a member of any store could approve any other store's action, and the
    # three distinct refusals leaked whether an id existed and what role it needed.
    require_store_membership(
        cursor,
        staff_user_id=principal.staff_user_id,
        store_id=_uuid(row[13]),
        error=ApprovalAuthorizationError,
    )
    required = StaffRole(str(row[6]))
    if StaffRole.OWNER_ADMIN not in principal.roles and required not in principal.roles:
        raise ApprovalAuthorizationError("approval role is not authorized")
    if not principal.mfa_verified:
        raise ApprovalAuthorizationError("MFA is required for approval decisions")
    if principal.staff_user_id == _uuid(row[4]):
        raise ApprovalAuthorizationError("requester cannot approve their own action")
    _require_separation_from_the_definer(
        cursor,
        resource_type=str(row[14]),
        resource_id=_uuid(row[0]),
        principal=principal,
    )


#: Resource types whose CONTENT was chosen by somebody other than the account that raised the
#: envelope, and the column naming that person.
#:
#: `SEPARATION_OF_DUTY` is a requirement of `_OWNER_FINANCIAL`, and the check above implements it
#: against `approval_requests.requested_by`. For most actions those two accounts are the same one:
#: proposing a range price writes the amounts and raises the envelope in one command, so the person
#: who chose the number is the person the check names.
#:
#: `EXPORT_REQUEST` is the exception, and OPS-BOARD-001 shipped with it unnoticed. An export request
#: is recorded first, by whoever decided which day of which shop leaves the building and under which
#: column list; raising the envelope against that stored row is a second, later act that any member
#: of the store may perform. So the check above compared the approver against the wrong person
#: entirely: have a colleague press "xin chủ tiệm duyệt" and the account that defined the export
#: could then approve its own release, with the maker-checker rule reading green throughout.
#:
#: A table rather than an `if`, and shaped like `_RESOLVABLE_RESOURCES` above, because the question
#: it answers is per resource type and the honest answer for the other twelve is "the envelope's
#: requester is the definer". An entry is added here only when that is untrue, and adding one is a
#: statement about how that resource comes into being.
_RESOURCE_DEFINERS: dict[str, str] = {
    "EXPORT_REQUEST": """
        SELECT e.requested_by_staff_id FROM export_requests e WHERE e.id = %s
    """,
}


def _require_separation_from_the_definer(
    cursor: Any, *, resource_type: str, resource_id: UUID, principal: StaffPrincipal
) -> None:
    """Refuse a decision from the staff member who defined the content this envelope binds.

    Silent for a resource type with no entry above, and silent for a resource that has since gone:
    this widens the maker-checker rule, it does not become a second existence check.
    `_require_resolvable_resource` already refuses an envelope naming a row that is not there, at
    request time, which is where that refusal belongs.

    The same opaque `ApprovalAuthorizationError` as the rule it extends, and the same sentence. An
    approver learns that separation of duty refused them, not which of two accounts it measured
    against -- and a caller cannot use the difference to discover who raised what.
    """
    statement = _RESOURCE_DEFINERS.get(resource_type)
    if statement is None:
        return
    cursor.execute(statement, (resource_id,))
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return
    if principal.staff_user_id == _uuid(row[0]):
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
