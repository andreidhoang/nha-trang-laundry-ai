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
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_contracts.channel_envelope import ReconciliationState
from nha_trang_laundry_domain import sla
from nha_trang_laundry_domain.sla import (
    ProductionSlaPolicy,
    ProductionSlaResult,
    evaluate_production_sla,
)
from psycopg.errors import UniqueViolation

from .identity import StaffPrincipal, StaffRole
from .query_version import QueryVersion, query_version, rule_source
from .store_access import require_store_membership
from .stores import StoreRepository
from .transactions import MaterialChange, OutboxEvent, commit_material_change

#: Roles allowed to read a Shadow surface at all. Membership is checked separately and always.
SHADOW_READ_ROLES = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR, StaffRole.AUDITOR}
)
#: Roles allowed to decide a draft or resolve an unknown send. An auditor reads; it never approves.
SHADOW_DECIDE_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER})

DRAFT_DECISIONS = frozenset({"APPROVE", "EDIT", "REJECT"})

#: The board's population and its order, as one statement, so that one edit moves one rule.
#:
#: The predicate is the 2026-08 original with `0037`'s meaning intact: an order is on the board from
#: the moment production accepted it until production physically released it, and a cancelled order
#: is not work. The keyset arm is written as a row comparison so PostgreSQL can satisfy the whole
#: WHERE and the whole ORDER BY from one index; `%(after_accepted)s::timestamptz IS NULL` ahead of
#: it is the first page, and the cast is required because a bare NULL parameter has no type.
_SLA_BOARD_SQL = """
    SELECT id, store_id, production_accepted_at, production_ready_at,
           commercial_status, production_status
    FROM orders
    WHERE store_id = %(store)s
      AND production_accepted_at IS NOT NULL
      AND production_status <> 'RELEASED'
      AND commercial_status <> 'CANCELLED'
      AND (
          %(after_accepted)s::timestamptz IS NULL
          OR (production_accepted_at, id) > (%(after_accepted)s::timestamptz, %(after_id)s::uuid)
      )
    ORDER BY production_accepted_at, id
    LIMIT %(limit)s
"""

#: The published identifier of the board's rule.
#:
#: `v1` is the first *published* identifier, not the first shape of the query: the `0037` clock fix
#: predates it. What the identifier promises from here on is that a change to the rule cannot reach
#: a printout without a new name, which `packages/db/tests/test_ops_board.py` enforces by pinning
#: the digest.
SLA_BOARD_QUERY_IDENTIFIER = "sla-risk-board-v1"

#: The domain SLA engine, as a structural digest of its own module rather than a hand-kept string.
#:
#: The SQL alone is not the rule behind a board figure. It selects rows; `evaluate_production_sla`
#: decides what each row *means* -- where the clock stops, where the mark falls, which reason codes
#: travel with it -- and a change there moves every number on the board while leaving the statement
#: untouched. A published version that could not notice that is the exact failure `query_version`
#: exists to prevent.
#:
#: `rule_source` is used rather than the raw text so that the digest tracks behaviour: comments,
#: docstrings and formatting are dropped, a renamed local or a reordered branch is not. The whole
#: module is read, not just the entry point, because the answer is produced with `_validate_policy`,
#: `_lifecycle` and the published policy constants as much as with the function that calls them.
_SLA_ENGINE_RULE = rule_source(sla)

#: One screen of the board. Matches the historical default so the assistant's counts do not move.
SLA_BOARD_DEFAULT_LIMIT = 50
#: The ceiling a caller may ask for, mirroring `ApprovalRepository.list_pending`.
SLA_BOARD_MAX_LIMIT = 200


def _policy_identity(policy: ProductionSlaPolicy) -> str:
    """Everything about a policy that can change a board figure, as one line.

    The policy is an argument rather than a constant -- choosing one per order is an open business
    decision -- so the version cannot be a module constant either. Its identifier is not enough on
    its own: `SLA_STANDARD_CLOTHES` with an eight-hour mark and `SLA_STANDARD_CLOTHES` with a
    twelve-hour mark produce different breaches under one name, so the hours and the type are hashed
    with it. `commitment_authority` is included because the engine refuses anything but
    `HUMAN_CONFIRM`, and the day that widens is a day every figure here means something else.
    """
    return "|".join(
        (
            policy.policy_id,
            policy.policy_type.value,
            str(policy.target_min_hours),
            str(policy.target_max_hours),
            policy.commitment_authority.value,
        )
    )


def sla_board_query_version(policy: ProductionSlaPolicy) -> QueryVersion:
    """The version that travels with a board figure: the statement, the engine, and the policy.

    Invariant 18 wants the identifier beside a number to name the rule that produced it. Three
    things produce a board number and any one of them can change without the other two: the SQL
    that chooses the population, the engine that evaluates each row, and the policy the caller
    evaluates it under. Hashing only the first published `v1` beside figures the other two had
    already moved.
    """
    return query_version(
        SLA_BOARD_QUERY_IDENTIFIER, _SLA_BOARD_SQL, _SLA_ENGINE_RULE, _policy_identity(policy)
    )


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
class ReviewedDraft:
    """One decision a person made about an agent draft, with the draft it was made about.

    Carries both sides because a decision on its own answers nothing: grading the agent means
    reading what it proposed next to what a human did with it. `edited_text` is the replacement a
    reviewer wrote and is null for anything but an EDIT, which the table's own CHECK enforces.
    """

    review_id: UUID
    agent_run_id: UUID
    store_id: UUID
    decision: str
    reason_code: str | None
    edited_text: str | None
    decided_by_staff_id: UUID
    decided_at: datetime
    draft_text: str
    terminal_outcome: str
    terminal_code: str
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
    """One in-production order as the domain SLA engine reported it, plus the row it was read from.

    `OPS-BOARD-001` widened this. The first six fields answered the assistant's question — how many
    orders are in production and how many passed the internal risk mark — and a count is all they
    can answer. A staff member standing at the counter needs the three things a count cannot give
    them: *which* order, *how long* is left, and *what state* it is in so they know what to do.

    Every added field is carried from somewhere that already decided it. `sla_outcome`,
    `elapsed_microseconds` and `breach_microseconds` are `evaluate_production_sla`'s own outputs;
    `remaining_microseconds` is the complement of the breach, derived from the engine's due
    timestamp and its own elapsed figure rather than by re-deciding when the clock stops. The two
    statuses and `production_ready_at` come straight off the order row. Nothing here is a second
    opinion about risk.

    Durations are microseconds and non-negative, and which side of the mark an order is on is
    carried by *which* duration is non-zero rather than by a sign: `remaining_microseconds` counts
    down to the mark and `breach_microseconds` counts past it, and exactly one of them can be
    non-zero at a time. Invariant 2 is about money, but the habit it encodes — a magnitude never
    carries a direction — is what keeps a board from rendering "-3 giờ còn lại".
    """

    order_id: UUID
    store_id: UUID
    production_accepted_at: datetime
    internal_risk_due_at: datetime | None
    overall_outcome: str
    reason_codes: tuple[str, ...]
    commercial_status: str
    production_status: str
    production_ready_at: datetime | None
    sla_outcome: str
    policy_id: str
    policy_type: str
    elapsed_microseconds: int | None
    remaining_microseconds: int | None
    breach_microseconds: int
    evaluated_at: datetime


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
        # `connection.transaction()` and not a bare cursor. A plain SELECT opens an implicit
        # transaction that nothing here closes, so the `commit_material_change` below nests inside
        # it as a savepoint and ends in RELEASE rather than COMMIT: the write looks successful, is
        # invisible to every other session, and is discarded when the connection closes. A revoked
        # store assignment silently staying live is the worst instance of that available here.
        #
        # STORE-REGISTRY-001 found this defect and fixed it in `stores.py`, then added a new SELECT
        # to this still-unfixed block in the same commit. So it is applied to every pre-check in
        # this module rather than to the two that were reported -- on a read it costs a clean
        # commit, and it means the pattern cannot come back one function at a time.
        with connection.transaction(), connection.cursor() as cursor:
            _require_active_owner(cursor, principal)
            # STORE-REGISTRY-001 gave `store_id` a foreign key, which is what stops a mistyped
            # identifier becoming a membership of a store that does not exist. Left to the
            # constraint alone the operator would get HTTP 500 from a `ForeignKeyViolation`
            # raised deep inside the transaction -- the same shape as the crash
            # COUNTER-DEFECTS-001 closed. The refusal is decided here so it reads as one.
            if not StoreRepository.exists(cursor, store_id):
                raise ShadowStateError("no such store")
            # The same pre-check the role path has had all along (`identity._lock_staff_version`),
            # missing here. An owner tidying up after somebody leaves got two different answers from
            # two panels on the same screen: "Gán vai trò" refused the departed person, and
            # "Gán cửa hàng" accepted them and wrote a live membership row. Nobody gained access --
            # a disabled
            # account cannot authenticate -- but the shop's record then said a person who had left
            # belonged to the shop, and the two panels disagreed about who that person was.
            cursor.execute("SELECT status FROM staff_users WHERE id = %s", (staff_user_id,))
            staff_row = cursor.fetchone()
            if staff_row is None or str(staff_row[0]) != "ACTIVE":
                raise ShadowStateError("staff user is missing or disabled")
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
        with connection.transaction(), connection.cursor() as cursor:
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
        with connection.transaction(), connection.cursor() as cursor:
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

    def list_reviewed_drafts(
        self,
        connection: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        limit: int = 50,
        before: UUID | None = None,
    ) -> tuple[ReviewedDraft, ...]:
        """The decisions already made, newest first, with the draft each one was about.

        The pending queue is undecided-only, so a draft leaves it the moment somebody rules on it
        and every screen forgets it. That is correct for a queue and wrong for a record: approve,
        edit and reject are captured precisely so the agent can be graded on them later, and until
        this read existed nothing in the console ever looked at them again.

        Visibility is the store's, not the reader's. Unlike `assistant_turns`, where a non-owner
        sees only their own questions, a review is a decision made on the shop's behalf, so anyone
        holding a Shadow read role sees all of them — including `AUDITOR`, whose whole job is
        reading decisions it did not make.

        `before` is the oldest review the caller already holds and pages backwards from it. It is
        resolved through the same gate as the page, so an id naming a review this caller may not
        read is refused rather than quietly answered with the newest page — a silent restart at the
        top is indistinguishable, to a reader paging backwards, from reaching the end.
        """
        if not 1 <= limit <= 200:
            raise ShadowStateError("review log limit must be between 1 and 200")
        with connection.transaction(), connection.cursor() as cursor:
            self._require_store_access(
                cursor, principal=principal, store_id=store_id, roles=SHADOW_READ_ROLES
            )
            anchor: tuple[Any, Any] | None = None
            if before is not None:
                cursor.execute(
                    "SELECT decided_at, review_id FROM agent_draft_reviews "
                    "WHERE store_id = %s AND review_id = %s",
                    (store_id, before),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ShadowAuthorizationError("review log cursor is not visible")
                anchor = (row[0], row[1])
            # The sort is `decided_at DESC, review_id` ASC, so the row after `(d, r)` is one with an
            # older `decided_at`, or the same `decided_at` and a larger `review_id`. The directions
            # differ, which is why this is written out rather than as a row comparison: a tuple
            # compare orders the tie the wrong way and silently drops or repeats reviews that share
            # a timestamp. `agent_draft_reviews_store_idx` is `(store_id, decided_at DESC,
            # review_id)` since migration 0028, so this reads as a range on that index.
            cursor.execute(
                """
                SELECT r.review_id, r.agent_run_id, r.store_id, r.decision, r.reason_code,
                       r.edited_text, r.decided_by_staff_id, r.decided_at,
                       d.draft_text, d.terminal_outcome, d.terminal_code, d.produced_at
                FROM agent_draft_reviews r
                JOIN agent_drafts d ON d.agent_run_id = r.agent_run_id
                WHERE r.store_id = %s
                  AND (
                    %s::timestamptz IS NULL
                    OR r.decided_at < %s
                    OR (r.decided_at = %s AND r.review_id > %s)
                  )
                ORDER BY r.decided_at DESC, r.review_id
                LIMIT %s
                """,
                (
                    store_id,
                    anchor[0] if anchor else None,
                    anchor[0] if anchor else None,
                    anchor[0] if anchor else None,
                    anchor[1] if anchor else None,
                    limit,
                ),
            )
            return tuple(
                ReviewedDraft(
                    review_id=_uuid(row[0]),
                    agent_run_id=_uuid(row[1]),
                    store_id=_uuid(row[2]),
                    decision=str(row[3]),
                    reason_code=None if row[4] is None else str(row[4]),
                    edited_text=None if row[5] is None else str(row[5]),
                    decided_by_staff_id=_uuid(row[6]),
                    decided_at=row[7],
                    draft_text=str(row[8]),
                    terminal_outcome=str(row[9]),
                    terminal_code=str(row[10]),
                    produced_at=row[11],
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
        with connection.transaction(), connection.cursor() as cursor:
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

        try:
            self._commit_draft_review(
                connection,
                agent_run_id=agent_run_id,
                review_id=review_id,
                decision=decision,
                reason_code=reason_code,
                edited_text=edited_text,
                principal=principal,
                correlation_id=correlation_id,
                timestamp=timestamp,
                mutation=mutation,
            )
        except UniqueViolation as error:
            # `0021` puts `UNIQUE (agent_run_id)` on the review table so a draft has one terminal
            # decision, and its comment says "a second review loses at the constraint, not in the
            # UI". It lost as an unhandled driver exception, which the route turned into a 500 and
            # the console into "Máy chủ gặp lỗi. Đừng thử lại" -- telling the second reviewer the
            # system is broken when what happened is that a colleague decided first. The client
            # already had the right words for this (`shadow.js:476`, "nhiều khả năng đã có người
            # quyết định bản nháp này"); it was waiting for a 409 the server never sent.
            raise ShadowStateError("this draft has already been decided") from error
        return DraftDecision(
            review_id=review_id,
            agent_run_id=agent_run_id,
            decision=decision,
            reason_code=reason_code,
            edited_text=edited_text,
            decided_by_staff_id=principal.staff_user_id,
            decided_at=timestamp,
        )

    @staticmethod
    def _commit_draft_review(
        connection: Any,
        *,
        agent_run_id: UUID,
        review_id: UUID,
        decision: str,
        reason_code: str | None,
        edited_text: str | None,
        principal: StaffPrincipal,
        correlation_id: UUID,
        timestamp: datetime,
        mutation: Any,
    ) -> None:
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

    # --- unknown-outcome reconciliation -------------------------------------------------------

    @classmethod
    def list_unknown_sends(
        cls, connection: Any, *, store_id: UUID, principal: StaffPrincipal, limit: int = 50
    ) -> tuple[UnknownSend, ...]:
        """One store's sends whose outcome nobody observed, oldest first.

        `API-INTEGRITY-002`. This read had no store at all -- `channel_send_receipts` had no store
        column -- so any Shadow reader of any shop listed every shop's receipts, without MFA. It
        is now the store's, like the draft queue beside it: a Shadow read role, membership of this
        store, and MFA. MFA because this is the input to a decision a person makes on the shop's
        behalf, exactly as the approval queue is; `AUDITOR` holds MFA by construction
        (`SENSITIVE_MFA_ROLES`), so the one reader this drops is an unverified `OPERATOR`.

        A receipt written before `0049` whose store could not be derived has a NULL store and so is
        in no store's queue: unattributed means unshown, never shown to everyone.
        """
        # The same bound every other list in this repository applies. Without it a negative limit
        # reaches `LIMIT %s` and PostgreSQL raises, which surfaces as a 500 on a read that a client
        # is allowed to make, and an unbounded limit returns the whole table in one page.
        if not 1 <= limit <= 200:
            raise ShadowStateError("exception queue limit must be between 1 and 200")
        with connection.transaction(), connection.cursor() as cursor:
            if not principal.mfa_verified:
                raise ShadowAuthorizationError("exception queue access requires MFA")
            cls._require_store_access(
                cursor, principal=principal, store_id=store_id, roles=SHADOW_READ_ROLES
            )
            cursor.execute(
                """
                SELECT receipt_id, outbox_id, provider, message_kind, attempt_number,
                       reconciliation_state, recorded_at
                FROM channel_send_receipts
                WHERE store_id = %s
                  AND reconciliation_state IN ('UNKNOWN', 'UNKNOWN_REQUIRES_HUMAN')
                ORDER BY recorded_at, receipt_id
                LIMIT %s
                """,
                (store_id, limit),
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
        """Resolve one unknown send by a named human. There is no automatic path to this method.

        `API-INTEGRITY-002`: by a named human *of the shop the send belongs to*. Any approver of
        any store could resolve any receipt before, recording as settled a send in a shop they had
        no access to. The store is read off the receipt row, locked in the same transaction as the
        update, and never taken from the request.

        A receipt that does not exist, belongs to another store, or has no attributable store at
        all is one refusal -- `ShadowAuthorizationError`, the route's opaque 403 -- so probing
        receipt identifiers teaches a caller nothing about another shop's sends.
        """

        if resolution not in {
            ReconciliationState.CONFIRMED_SENT,
            ReconciliationState.CONFIRMED_NOT_SENT,
        }:
            raise ShadowStateError("resolution must be CONFIRMED_SENT or CONFIRMED_NOT_SENT")
        refused = "resolving this unknown send is not authorized"
        if not principal.roles & SHADOW_DECIDE_ROLES or not principal.mfa_verified:
            raise ShadowAuthorizationError(refused)
        timestamp = now or datetime.now(UTC)

        def mutation(cursor: Any) -> None:
            cursor.execute(
                "SELECT store_id FROM channel_send_receipts WHERE receipt_id = %s FOR UPDATE",
                (receipt_id,),
            )
            owner = cursor.fetchone()
            if owner is None or owner[0] is None:
                raise ShadowAuthorizationError(refused)
            try:
                self._require_store_access(
                    cursor,
                    principal=principal,
                    store_id=_uuid(owner[0]),
                    roles=SHADOW_DECIDE_ROLES,
                )
            except ShadowAuthorizationError as error:
                raise ShadowAuthorizationError(refused) from error
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
        limit: int = SLA_BOARD_DEFAULT_LIMIT,
        after: tuple[datetime, UUID] | None = None,
    ) -> tuple[SlaRisk, ...]:
        """Rank in-production orders by the domain SLA engine, never by a model or a heuristic.

        The policy is a required argument rather than a default, because choosing one per order is a
        business decision. There is no stored promised-at column: the promise is computed, and this
        surface reports exactly what `evaluate_production_sla` returns, including its reason codes.

        **This is the only SLA read in the system, and `OPS-BOARD-001` extended it in place rather
        than adding a second.** `#/assistant` counts what this returns and the board screen lists
        it, so the two cannot disagree about a store at an instant. A second statement that computed
        risk would also be a second place to re-introduce the `0037` clock bug, which this project
        has already found and paid for once.

        `after` pages forward by `(production_accepted_at, id)` — a keyset, not an offset, because
        an offset over a board whose population changes while a shift reads it silently skips rows.
        The pair is exactly the ORDER BY, so paging cannot repeat or lose an order.

        **The board is ordered by production acceptance, oldest first — and that is all it is.**
        It is tempting to call that least-time-remaining-first, and under a `COMMITMENT` policy it
        nearly is: the mark is `production_accepted_at + target_max_hours`, one fixed offset, so
        between two orders *whose clocks are both still running* the older acceptance really does
        mean the less time left. It stops being true for the exact population migration `0037`
        exists to serve. A `READY_AT_STORE` order's clock stopped at `production_ready_at`, so its
        remaining time froze at whatever was left when the washing finished while the order
        accepted beside it keeps spending. Two orders accepted in the same minute — one finished an
        hour ago, one still running — share a sort key and have different time remaining, and the
        finished one can sit above an order about to breach. Under `GUIDANCE_RANGE` or
        `HUMAN_ETA_REQUIRED` there is no mark at all, so there is no remaining time to rank by.

        Ordering by remaining time itself would mean `coalesce(production_ready_at, <now>) -
        production_accepted_at` in the ORDER BY: a second copy of the clock-stop rule written in
        SQL, and one that no index can serve, because the key depends on the instant of evaluation.
        `orders_sla_board_idx` would stop covering both the sort and the keyset and a page would
        become a sort of the store's whole board. So this method does not claim a ranking it cannot
        produce. It orders by acceptance, the surfaces above it say so in those words, and every row
        carries `sla_outcome`, `remaining_microseconds` and `breach_microseconds` so a reader ranks
        by the figure rather than by the position. Re-sorting a page in Python would rank one page
        against itself and break the keyset, so that is not done either.
        """

        if not 1 <= limit <= SLA_BOARD_MAX_LIMIT:
            raise ShadowStateError(
                f"the SLA board limit must be between 1 and {SLA_BOARD_MAX_LIMIT}"
            )
        timestamp = now or datetime.now(UTC)
        with connection.transaction(), connection.cursor() as cursor:
            self._require_store_access(
                cursor, principal=principal, store_id=store_id, roles=SHADOW_READ_ROLES
            )
            cursor.execute(
                _SLA_BOARD_SQL,
                {
                    "store": store_id,
                    "after_accepted": None if after is None else after[0],
                    "after_id": None if after is None else after[1],
                    "limit": limit,
                },
            )
            rows = cursor.fetchall()
        board = []
        for row in rows:
            accepted_at = row[2]
            # `0037`. Without this the clock never stopped: `comparison_at = ready_at_store or
            # evaluated_at`, and the population holds every order until it is physically RELEASED --
            # so a washed order waiting overnight for its owner accrued elapsed time until it read
            # SLA_BREACHED, and SLA_MET was unreachable from this surface entirely.
            ready_at = row[3]
            result = evaluate_production_sla(
                policy,
                evaluated_at=timestamp,
                production_accepted_at=accepted_at,
                ready_at_store=ready_at,
            )
            board.append(
                SlaRisk(
                    order_id=_uuid(row[0]),
                    store_id=_uuid(row[1]),
                    production_accepted_at=accepted_at,
                    internal_risk_due_at=result.internal_risk_due_at,
                    overall_outcome=str(result.overall_outcome.value),
                    reason_codes=tuple(str(code.value) for code in result.reason_codes),
                    commercial_status=str(row[4]),
                    production_status=str(row[5]),
                    production_ready_at=ready_at,
                    sla_outcome=str(result.outcome.value),
                    policy_id=result.trace.policy_id,
                    policy_type=str(result.trace.policy_type.value),
                    elapsed_microseconds=result.trace.actual_elapsed_microseconds,
                    remaining_microseconds=_remaining_microseconds(result, accepted_at),
                    breach_microseconds=result.trace.breach_microseconds,
                    evaluated_at=timestamp,
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
        with connection.transaction(), connection.cursor() as cursor:
            self._require_store_access(
                cursor, principal=principal, store_id=store_id, roles=SHADOW_READ_ROLES
            )
            # `_require_store_access` above proves the caller belongs to the store they NAMED.
            # It says nothing about the aggregate, and until 2026-08-30 nothing else did either:
            # substituting another store's order id while naming your own store returned that
            # store's full audit timeline -- creation, every transition, the settlement, and the
            # actor id of its operator. It doubled as an existence oracle over any UUID in the
            # system, since a real aggregate returned rows and a random one returned none.
            #
            # `audit_events` carries no store column, so the aggregate's owner is resolved from the
            # aggregate itself. Fail-closed by construction: an aggregate type not listed here
            # resolves to nothing and the timeline is empty, which is also what a caller sees for an
            # identifier that does not exist -- so probing still teaches nobody which stores exist.
            cursor.execute(
                """
                SELECT occurred_at, action, actor_type, actor_id, aggregate_type, aggregate_id
                FROM audit_events
                WHERE aggregate_id = %(aggregate)s
                  AND EXISTS (
                      SELECT 1 FROM orders t
                       WHERE t.id = %(aggregate)s AND t.store_id = %(store)s
                      UNION ALL
                      SELECT 1 FROM quotes t
                       WHERE t.id = %(aggregate)s AND t.store_id = %(store)s
                      UNION ALL
                      SELECT 1 FROM order_requests t
                       WHERE t.id = %(aggregate)s AND t.store_id = %(store)s
                      UNION ALL
                      SELECT 1 FROM counter_tickets t
                       WHERE t.id = %(aggregate)s AND t.store_id = %(store)s
                      UNION ALL
                      SELECT 1 FROM order_settlements t
                       WHERE t.id = %(aggregate)s AND t.store_id = %(store)s
                      UNION ALL
                      SELECT 1 FROM delivery_legs t
                       WHERE t.id = %(aggregate)s AND t.store_id = %(store)s
                      UNION ALL
                      SELECT 1 FROM quote_acceptances t
                       WHERE t.id = %(aggregate)s AND t.store_id = %(store)s
                      UNION ALL
                      SELECT 1 FROM customer_incidents t
                       WHERE t.id = %(aggregate)s AND t.store_id = %(store)s
                      UNION ALL
                      SELECT 1 FROM agent_runs t
                       WHERE t.id = %(aggregate)s AND t.store_id = %(store)s
                      UNION ALL
                      -- Added 2026-08-31. Omitting it made this filter refuse the caller's OWN
                      -- store's approval trail: an owner reading an approval they had just decided
                      -- saw nothing. A scoping fix that blinds the people it is meant to serve is
                      -- a defect in the same way the leak was.
                      SELECT 1 FROM approval_requests t
                       WHERE t.id = %(aggregate)s AND t.store_id = %(store)s
                  )
                ORDER BY occurred_at, id
                LIMIT %(limit)s
                """,
                {"aggregate": aggregate_id, "store": store_id, "limit": limit},
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


def _remaining_microseconds(result: ProductionSlaResult, accepted_at: datetime) -> int | None:
    """How much of the internal mark is left, derived from the engine and never re-decided.

    The tempting one-liner is `internal_risk_due_at - now`, and it is wrong in exactly the way
    `0037` was wrong: it keeps counting for an order whose laundry is already finished and waiting
    on the counter for its owner. The engine already knows where the clock stopped — that knowledge
    is inside `actual_elapsed_microseconds`, which it measured to `ready_at_store` when there is one
    and to the evaluation instant when there is not.

    So the budget is taken from the engine's own two timestamps (`internal_risk_due_at` minus the
    acceptance the board read) and the elapsed figure is subtracted from it. Restating the
    clock-stop rule here would be the second SLA engine this item exists to not build.

    `None` when the policy sets no mark: `GUIDANCE_RANGE` is guidance and carries
    `GUIDANCE_DOES_NOT_CREATE_BREACH`, `HUMAN_ETA_REQUIRED` waits for a person, and neither has a
    deadline to have time left against. Unknown means unknown, not zero.
    """
    due_at = result.internal_risk_due_at
    elapsed = result.trace.actual_elapsed_microseconds
    if due_at is None or elapsed is None:
        return None
    budget = (due_at - accepted_at) // timedelta(microseconds=1)
    return max(0, budget - elapsed)


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
    "SLA_BOARD_DEFAULT_LIMIT",
    "SLA_BOARD_MAX_LIMIT",
    "SLA_BOARD_QUERY_IDENTIFIER",
    "AuditEntry",
    "DraftDecision",
    "PendingDraft",
    "ShadowAuthorizationError",
    "ShadowConsoleRepository",
    "ShadowStateError",
    "SlaRisk",
    "UnknownSend",
    "sla_board_query_version",
]
