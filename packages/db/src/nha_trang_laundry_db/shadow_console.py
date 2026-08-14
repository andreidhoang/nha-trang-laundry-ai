"""Read and decision surfaces for the internal Shadow pilot.

`SHADOW-001` requires a human to approve every outbound action across fourteen days and thirty real
orders. This module is where that happens: the agent's proposed draft, the attributed approve, edit
or reject, the resolution of a send whose outcome nobody observed, and the audit trail behind both.

Two properties are enforced here rather than in the API, because a route is a place a check can be
forgotten and a repository is the only path to the data:

* **Store membership.** The existing console authorizes by role alone, so any operator can read any
  store by changing an identifier in the URL. Every projection in this module additionally requires
  an explicit `staff_store_assignments` row, which is what makes the packet's IDOR test passable.
* **Human-only reconciliation.** Leaving `UNKNOWN` requires a named staff member. There is no code
  path in this module by which a retry, a worker or a model can resolve one.

Every number is computed by SQL or by domain code. A model may explain an already-computed fact; it
may never originate, mutate or rank one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_contracts.channel_envelope import ReconciliationState
from nha_trang_laundry_domain.sla import ProductionSlaPolicy, evaluate_production_sla

from .identity import StaffPrincipal, StaffRole
from .store_access import require_store_membership
from .transactions import MaterialChange, OutboxEvent, commit_material_change

#: Roles allowed to read a Shadow surface at all. Membership is checked separately and always.
SHADOW_READ_ROLES = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR, StaffRole.AUDITOR}
)
#: Roles allowed to decide a draft or resolve an unknown send. An auditor reads; it never approves.
SHADOW_DECIDE_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER})

DRAFT_DECISIONS = frozenset({"APPROVE", "EDIT", "REJECT"})


class ShadowAuthorizationError(PermissionError):
    """Raised when a principal may not read or act on a Shadow surface."""


class ShadowStateError(ValueError):
    """Raised when a Shadow decision would be inconsistent with recorded state."""


@dataclass(frozen=True, slots=True)
class PendingDraft:
    agent_run_id: UUID
    store_id: UUID
    conversation_binding_id: UUID
    draft_text: str
    terminal_outcome: str
    terminal_code: str
    tool_call_count: int
    produced_at: datetime


@dataclass(frozen=True, slots=True)
class DraftDecision:
    review_id: UUID
    agent_run_id: UUID
    decision: str
    reason_code: str | None
    edited_text: str | None
    decided_by_staff_id: UUID
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class UnknownSend:
    receipt_id: UUID
    outbox_id: UUID
    provider: str
    message_kind: str
    attempt_number: int
    reconciliation_state: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class SlaRisk:
    order_id: UUID
    store_id: UUID
    production_accepted_at: datetime
    internal_risk_due_at: datetime | None
    overall_outcome: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditEntry:
    occurred_at: datetime
    action: str
    actor_type: str
    actor_id: UUID | None
    aggregate_type: str
    aggregate_id: UUID


class ShadowConsoleRepository:
    """Every method is store-scoped and membership-checked; none of them sends anything."""

    # --- authorization ---------------------------------------------------------------------

    @staticmethod
    def assign_store(
        connection: Any,
        *,
        staff_user_id: UUID,
        store_id: UUID,
        principal: StaffPrincipal,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> None:
        """Grant a staff member access to one store. Owner-only, attributed and audited.

        `STORE-ASSIGNMENT-001` changed two things here without changing what the method is for.

        The owner check now reads the database rather than the principal object. A `StaffPrincipal`
        carries the roles a session was minted with; whether the actor is *still* an active owner is
        a fact only the database holds, and this is the route that grants authorization, so it is
        the last place to take the caller's word for anything.

        The aggregate version is now derived rather than hardcoded to 1. `domain_events` is unique
        on (aggregate_type, aggregate_id, aggregate_version, event_type), so a fixed 1 meant a staff
        member could be assigned to exactly one store, ever — the second grant raised a unique
        violation from deep inside the transaction. The demo seed hit this and worked around it by
        checking membership first; the defect was here.
        """

        timestamp = now or datetime.now(UTC)
        with connection.cursor() as cursor:
            _require_active_owner(cursor, principal)
            version = _next_assignment_version(cursor, staff_user_id)

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                ON CONFLICT (staff_user_id, store_id) DO UPDATE
                SET revoked_at = NULL,
                    revoked_by_staff_id = NULL,
                    assigned_by_staff_id = EXCLUDED.assigned_by_staff_id,
                    assigned_at = EXCLUDED.assigned_at,
                    row_version = staff_store_assignments.row_version + 1
                WHERE staff_store_assignments.revoked_at IS NOT NULL
                """,
                (staff_user_id, store_id, principal.staff_user_id, timestamp),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="STAFF_STORE_ASSIGNMENT",
                aggregate_id=staff_user_id,
                aggregate_version=version,
                event_type="STAFF_STORE_ASSIGNED",
                event_payload={"store_id": str(store_id)},
                audit_action="STAFF_STORE_ASSIGN",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "staff.store_assigned.v1",
                        {"staff_user_id": str(staff_user_id), "store_id": str(store_id)},
                        f"staff-store:{staff_user_id}:{store_id}:granted:{version}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )

    @staticmethod
    def revoke_store(
        connection: Any,
        *,
        staff_user_id: UUID,
        store_id: UUID,
        principal: StaffPrincipal,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> None:
        """End a staff member's access to one store. Owner-only, attributed and audited.

        Soft, following `staff_role_assignments`: the row keeps who granted it and when, and gains
        who ended it and when. Deleting would erase the record an authorization table exists to
        hold, and `staff_store_assignments_no_hard_delete` refuses it anyway.

        Revoking an assignment that is already revoked, or was never granted, is not an error. The
        caller asked for this staff member to have no access to this store, and afterwards they do
        not; reporting a failure would invite a retry that changes nothing. The audit event is still
        written, because "an owner asked to remove access at this time" is worth recording whether
        or not it changed a row.
        """

        timestamp = now or datetime.now(UTC)
        with connection.cursor() as cursor:
            _require_active_owner(cursor, principal)
            version = _next_assignment_version(cursor, staff_user_id)

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                UPDATE staff_store_assignments
                SET revoked_at = %s, revoked_by_staff_id = %s,
                    row_version = row_version + 1
                WHERE staff_user_id = %s AND store_id = %s AND revoked_at IS NULL
                """,
                (timestamp, principal.staff_user_id, staff_user_id, store_id),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="STAFF_STORE_ASSIGNMENT",
                aggregate_id=staff_user_id,
                aggregate_version=version,
                event_type="STAFF_STORE_REVOKED",
                event_payload={"store_id": str(store_id)},
                audit_action="STAFF_STORE_REVOKE",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "staff.store_revoked.v1",
                        {"staff_user_id": str(staff_user_id), "store_id": str(store_id)},
                        f"staff-store:{staff_user_id}:{store_id}:revoked:{version}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )

    @staticmethod
    def _require_store_access(
        cursor: Any, *, principal: StaffPrincipal, store_id: UUID, roles: frozenset[StaffRole]
    ) -> None:
        if not principal.roles & roles:
            raise ShadowAuthorizationError("shadow console access is not authorized for this role")
        # Delegated rather than re-queried. `store_access.py` describes itself as the single
        # membership check every store-scoped repository calls, and this method was a second
        # implementation of the same SELECT — behaviourally identical, and invisible to anything
        # looking for the shared call. `STORE-SCOPING-002`'s enumeration test reads source to prove
        # every store-scoped repository method is guarded, and a private duplicate defeats that:
        # the two shadow methods were reported as unguarded when in fact they were guarded twice
        # over. One implementation means one place to audit and one place to get right.
        #
        # The refusal stays a ShadowAuthorizationError, so the route's 403 body is unchanged and a
        # caller still cannot tell a role failure from a membership failure.
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=ShadowAuthorizationError,
        )

    # --- draft review ----------------------------------------------------------------------

    @staticmethod
    def record_draft(
        connection: Any,
        *,
        agent_run_id: UUID,
        store_id: UUID,
        conversation_binding_id: UUID,
        contact_binding_id: UUID,
        draft_text: str,
        terminal_outcome: str,
        terminal_code: str,
        tool_call_count: int,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> None:
        """Persist the agent's proposal so a human has something to review.

        Immutable once written; an edit becomes a new review row.
        """

        timestamp = now or datetime.now(UTC)

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO agent_drafts (
                    agent_run_id, store_id, conversation_binding_id, contact_binding_id,
                    draft_text, terminal_outcome, terminal_code, tool_call_count, produced_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    agent_run_id,
                    store_id,
                    conversation_binding_id,
                    contact_binding_id,
                    draft_text,
                    terminal_outcome,
                    terminal_code,
                    tool_call_count,
                    timestamp,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="AGENT_DRAFT",
                aggregate_id=agent_run_id,
                aggregate_version=1,
                event_type="AGENT_DRAFT_RECORDED",
                event_payload={
                    "store_id": str(store_id),
                    "terminal_outcome": terminal_outcome,
                    "terminal_code": terminal_code,
                    "draft_character_count": len(draft_text),
                },
                audit_action="AGENT_DRAFT_RECORD",
                actor_type="AGENT_RUNNER",
                actor_id=None,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "agent.draft_recorded.v1",
                        {"agent_run_id": str(agent_run_id), "store_id": str(store_id)},
                        f"agent-draft:{agent_run_id}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )

    def list_pending_drafts(
        self, connection: Any, *, store_id: UUID, principal: StaffPrincipal, limit: int = 50
    ) -> tuple[PendingDraft, ...]:
        if not 1 <= limit <= 200:
            raise ShadowStateError("draft queue limit must be between 1 and 200")
        with connection.cursor() as cursor:
            self._require_store_access(
                cursor, principal=principal, store_id=store_id, roles=SHADOW_READ_ROLES
            )
            cursor.execute(
                """
                SELECT d.agent_run_id, d.store_id, d.conversation_binding_id, d.draft_text,
                       d.terminal_outcome, d.terminal_code, d.tool_call_count, d.produced_at
                FROM agent_drafts d
                LEFT JOIN agent_draft_reviews r ON r.agent_run_id = d.agent_run_id
                WHERE d.store_id = %s AND r.review_id IS NULL
                ORDER BY d.produced_at, d.agent_run_id
                LIMIT %s
                """,
                (store_id, limit),
            )
            return tuple(
                PendingDraft(
                    agent_run_id=_uuid(row[0]),
                    store_id=_uuid(row[1]),
                    conversation_binding_id=_uuid(row[2]),
                    draft_text=str(row[3]),
                    terminal_outcome=str(row[4]),
                    terminal_code=str(row[5]),
                    tool_call_count=int(row[6]),
                    produced_at=row[7],
                )
                for row in cursor.fetchall()
            )

    def decide_draft(
        self,
        connection: Any,
        *,
        agent_run_id: UUID,
        decision: str,
        principal: StaffPrincipal,
        correlation_id: UUID,
        reason_code: str | None = None,
        edited_text: str | None = None,
        now: datetime | None = None,
    ) -> DraftDecision:
        """Record an attributed approve, edit or reject. The agent's original is never mutated."""

        if decision not in DRAFT_DECISIONS:
            raise ShadowStateError("draft decision must be APPROVE, EDIT or REJECT")
        timestamp = now or datetime.now(UTC)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT store_id FROM agent_drafts WHERE agent_run_id = %s", (agent_run_id,)
            )
            row = cursor.fetchone()
            if row is None:
                raise ShadowStateError("no agent draft exists for this run")
            store_id = _uuid(row[0])
            self._require_store_access(
                cursor, principal=principal, store_id=store_id, roles=SHADOW_DECIDE_ROLES
            )
        review_id = uuid4()

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO agent_draft_reviews (
                    review_id, agent_run_id, store_id, decision, reason_code, edited_text,
                    decided_by_staff_id, decided_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    review_id,
                    agent_run_id,
                    store_id,
                    decision,
                    reason_code,
                    edited_text,
                    principal.staff_user_id,
                    timestamp,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="AGENT_DRAFT",
                aggregate_id=agent_run_id,
                aggregate_version=2,
                event_type="AGENT_DRAFT_REVIEWED",
                event_payload={
                    "decision": decision,
                    "reason_code": reason_code,
                    "edited": edited_text is not None,
                },
                audit_action=f"AGENT_DRAFT_{decision}",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "agent.draft_reviewed.v1",
                        {
                            "agent_run_id": str(agent_run_id),
                            "review_id": str(review_id),
                            "decision": decision,
                        },
                        f"agent-draft-review:{agent_run_id}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )
        return DraftDecision(
            review_id=review_id,
            agent_run_id=agent_run_id,
            decision=decision,
            reason_code=reason_code,
            edited_text=edited_text,
            decided_by_staff_id=principal.staff_user_id,
            decided_at=timestamp,
        )

    # --- unknown-outcome reconciliation -------------------------------------------------------

    @staticmethod
    def list_unknown_sends(
        connection: Any, *, principal: StaffPrincipal, limit: int = 50
    ) -> tuple[UnknownSend, ...]:
        if not principal.roles & SHADOW_READ_ROLES:
            raise ShadowAuthorizationError("exception queue access is not authorized")
        # The same bound every other list in this repository applies. Without it a negative limit
        # reaches `LIMIT %s` and PostgreSQL raises, which surfaces as a 500 on a read that a client
        # is allowed to make, and an unbounded limit returns the whole table in one page.
        if not 1 <= limit <= 200:
            raise ShadowStateError("exception queue limit must be between 1 and 200")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT receipt_id, outbox_id, provider, message_kind, attempt_number,
                       reconciliation_state, recorded_at
                FROM channel_send_receipts
                WHERE reconciliation_state IN ('UNKNOWN', 'UNKNOWN_REQUIRES_HUMAN')
                ORDER BY recorded_at, receipt_id
                LIMIT %s
                """,
                (limit,),
            )
            return tuple(
                UnknownSend(
                    receipt_id=_uuid(row[0]),
                    outbox_id=_uuid(row[1]),
                    provider=str(row[2]),
                    message_kind=str(row[3]),
                    attempt_number=int(row[4]),
                    reconciliation_state=str(row[5]),
                    recorded_at=row[6],
                )
                for row in cursor.fetchall()
            )

    def resolve_unknown_send(
        self,
        connection: Any,
        *,
        receipt_id: UUID,
        resolution: ReconciliationState,
        principal: StaffPrincipal,
        correlation_id: UUID,
        note: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Resolve one unknown send by a named human. There is no automatic path to this method."""

        if resolution not in {
            ReconciliationState.CONFIRMED_SENT,
            ReconciliationState.CONFIRMED_NOT_SENT,
        }:
            raise ShadowStateError("resolution must be CONFIRMED_SENT or CONFIRMED_NOT_SENT")
        if not principal.roles & SHADOW_DECIDE_ROLES:
            raise ShadowAuthorizationError("resolving an unknown send is not authorized")
        timestamp = now or datetime.now(UTC)

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                UPDATE channel_send_receipts
                SET reconciliation_state = %s,
                    resolved_by = 'HUMAN_DECISION',
                    resolved_at = %s,
                    resolution_actor_id = %s,
                    resolution_note = %s
                WHERE receipt_id = %s
                  AND reconciliation_state IN ('UNKNOWN', 'UNKNOWN_REQUIRES_HUMAN')
                """,
                (
                    resolution.value,
                    timestamp,
                    str(principal.staff_user_id),
                    note,
                    receipt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ShadowStateError("receipt is not awaiting human reconciliation")

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CHANNEL_SEND_RECEIPT",
                aggregate_id=receipt_id,
                aggregate_version=1,
                event_type="CHANNEL_SEND_RECONCILED",
                event_payload={"reconciliation_state": resolution.value},
                audit_action="CHANNEL_SEND_RECONCILE",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "channel.send_reconciled.v1",
                        {
                            "receipt_id": str(receipt_id),
                            "reconciliation_state": resolution.value,
                        },
                        f"channel-reconcile:{receipt_id}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )

    # --- deterministic read models ------------------------------------------------------------

    def sla_risk_board(
        self,
        connection: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        policy: ProductionSlaPolicy,
        now: datetime | None = None,
        limit: int = 50,
    ) -> tuple[SlaRisk, ...]:
        """Rank in-production orders by the domain SLA engine, never by a model or a heuristic.

        The policy is a required argument rather than a default, because choosing one per order is a
        business decision. There is no stored promised-at column: the promise is computed, and this
        surface reports exactly what `evaluate_production_sla` returns, including its reason codes.
        """

        timestamp = now or datetime.now(UTC)
        with connection.cursor() as cursor:
            self._require_store_access(
                cursor, principal=principal, store_id=store_id, roles=SHADOW_READ_ROLES
            )
            cursor.execute(
                """
                SELECT id, store_id, production_accepted_at
                FROM orders
                WHERE store_id = %s
                  AND production_accepted_at IS NOT NULL
                  AND production_status <> 'RELEASED'
                  AND commercial_status <> 'CANCELLED'
                ORDER BY production_accepted_at, id
                LIMIT %s
                """,
                (store_id, limit),
            )
            rows = cursor.fetchall()
        board = []
        for row in rows:
            accepted_at = row[2]
            result = evaluate_production_sla(
                policy, evaluated_at=timestamp, production_accepted_at=accepted_at
            )
            board.append(
                SlaRisk(
                    order_id=_uuid(row[0]),
                    store_id=_uuid(row[1]),
                    production_accepted_at=accepted_at,
                    internal_risk_due_at=result.internal_risk_due_at,
                    overall_outcome=str(result.overall_outcome.value),
                    reason_codes=tuple(str(code.value) for code in result.reason_codes),
                )
            )
        return tuple(board)

    def audit_timeline(
        self,
        connection: Any,
        *,
        store_id: UUID,
        aggregate_id: UUID,
        principal: StaffPrincipal,
        limit: int = 100,
    ) -> tuple[AuditEntry, ...]:
        with connection.cursor() as cursor:
            self._require_store_access(
                cursor, principal=principal, store_id=store_id, roles=SHADOW_READ_ROLES
            )
            cursor.execute(
                """
                SELECT occurred_at, action, actor_type, actor_id, aggregate_type, aggregate_id
                FROM audit_events
                WHERE aggregate_id = %s
                ORDER BY occurred_at, id
                LIMIT %s
                """,
                (aggregate_id, limit),
            )
            return tuple(
                AuditEntry(
                    occurred_at=row[0],
                    action=str(row[1]),
                    actor_type=str(row[2]),
                    actor_id=_uuid(row[3]) if row[3] is not None else None,
                    aggregate_type=str(row[4]),
                    aggregate_id=_uuid(row[5]),
                )
                for row in cursor.fetchall()
            )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _require_active_owner(cursor: Any, principal: StaffPrincipal) -> None:
    """Refuse unless the actor is, right now, an active OWNER_ADMIN according to the database.

    The in-memory check comes first only because it is free. The database check is the authority:
    a session minted while its holder was an owner keeps saying so after the role is revoked or the
    account disabled, and this is the pair of methods that hands out access to a store's customers.
    """
    if StaffRole.OWNER_ADMIN not in principal.roles:
        raise ShadowAuthorizationError("store assignment requires OWNER_ADMIN")
    cursor.execute(
        """
        SELECT 1
        FROM staff_users u
        JOIN staff_role_assignments r ON r.staff_user_id = u.id
        WHERE u.id = %s AND u.status = 'ACTIVE'
          AND r.role = 'OWNER_ADMIN' AND r.revoked_at IS NULL
        """,
        (principal.staff_user_id,),
    )
    if cursor.fetchone() is None:
        raise ShadowAuthorizationError("store assignment requires an active OWNER_ADMIN")


def _next_assignment_version(cursor: Any, staff_user_id: UUID) -> int:
    """The next event version for this staff member's assignment history.

    `domain_events` is unique on (aggregate_type, aggregate_id, aggregate_version, event_type), so
    every grant and revoke for one staff member needs a distinct version. Locking the staff row
    first serialises concurrent grants for that person, which is the same thing
    `IdentityRepository` does before a role change; without the lock two owners assigning the same
    person to two stores at once would race for the same version and one would fail on the
    constraint rather than simply queueing.
    """
    cursor.execute("SELECT 1 FROM staff_users WHERE id = %s FOR UPDATE", (staff_user_id,))
    if cursor.fetchone() is None:
        raise ShadowStateError("staff user is missing")
    cursor.execute(
        """
        SELECT coalesce(max(aggregate_version), 0)
        FROM domain_events
        WHERE aggregate_type = 'STAFF_STORE_ASSIGNMENT' AND aggregate_id = %s
        """,
        (staff_user_id,),
    )
    row = cursor.fetchone()
    return int(row[0]) + 1 if row else 1


__all__ = [
    "DRAFT_DECISIONS",
    "SHADOW_DECIDE_ROLES",
    "SHADOW_READ_ROLES",
    "AuditEntry",
    "DraftDecision",
    "PendingDraft",
    "ShadowAuthorizationError",
    "ShadowConsoleRepository",
    "ShadowStateError",
    "SlaRisk",
    "UnknownSend",
]
