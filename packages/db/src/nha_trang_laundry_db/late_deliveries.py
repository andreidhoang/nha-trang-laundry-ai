"""*Giao trễ cần xử lý*: deliveries the server measured as late, and one tap each way (`DEC-042`).

`LATE-CREDIT-002`. Three reads and one write, and no decision about time or money made here:

* **The measurement** is `nha_trang_laundry_domain.late_delivery.late_delivery_clock`, fed the
  order's first promise, its *Hẹn lại* ledger and its delivery legs, exactly as stored. This module
  reads those rows and hands them over; it never compares two instants itself.
* **The list** is the store's delivered orders measured late by more than the published
  `late_delivery_threshold_minutes` that nobody has decided about yet, oldest arrival first,
  bounded. Each row carries the would-be credit from `remedies.read_late_credit_preview`, the
  same probe `RemedyOptions` makes, so the figure on the button is the figure a proposal records.
* **The decision.** *Lỗi của tiệm* opens the late-delivery incident (the counter's existing
  `SERVICE_QUALITY` kind: `0014` has no narrower one, and its summary says what it is), then the
  `LATE_DELIVERY_CREDIT` proposal with the server's measured minutes and the shop's fault attested
  by the person pressing, then the decision row -- in one transaction, under one
  `Idempotency-Key`. The proposal then follows the existing remedy path unchanged: staff authority
  up to the published ceiling, the owner's `APPROVE_REMEDY` envelope above it, execution through
  `POST /remedy-proposals/{id}/execution`, one credit per order. *Không phải lỗi tiệm* writes only
  the decision, with a reason.
* **The follow-up**: a credited decision whose proposal still needs executing or the owner's
  approval stays visible beside the list, with the step the remedy read model names for it.

The optional note on `OTHER` stays in `late_delivery_decisions`; no event, audit or outbox payload
carries it (the `order_promise_changes` rule), and a note that looks like a phone number is refused
(`unclaimed.clean_note`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import MODES_EXPECTING_RETURN
from nha_trang_laundry_domain.late_delivery import (
    LATE_DELIVERY_DECISION_REF,
    LateDeliveryClock,
    LateDeliveryDecision,
    LegAttempt,
    NotStoreFaultReason,
    PromiseMove,
    late_delivery_clock,
)
from nha_trang_laundry_domain.promise import PromiseChangeReason
from nha_trang_laundry_domain.remedies import RemedyKind, RemedyPolicy
from nha_trang_laundry_domain.unclaimed import clean_note

from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.incidents import IncidentRepository, StaffIncidentOpenCommand
from nha_trang_laundry_db.orders import OrderNotVisibleError
from nha_trang_laundry_db.remedies import (
    _DEAD_ENVELOPE_STATUSES,
    REMEDY_ROLES,
    RemedyProposalCommand,
    RemedyProposalRepository,
    read_late_credit_preview,
    read_published_remedy_policy,
)
from nha_trang_laundry_db.remedy_reads import (
    NEXT_STEP_AWAIT_OWNER,
    NEXT_STEP_EXECUTE,
    _next_step,
)
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Who reads the list and decides: the counter roles that propose a remedy, with MFA (`DEC-004`
#: rests the credit on a staff finding of fault, and these are the people who make it).
LATE_DELIVERY_ROLES: Final = REMEDY_ROLES
#: The longest page of the list.
LIST_MAX_LIMIT: Final = 100
#: How many delivered candidates one read measures before it stops and says the list may go on.
SCAN_LIMIT: Final = 500
#: How many credited decisions still waiting on execution or the owner the list shows.
FOLLOW_UP_LIMIT: Final = 50
#: The longest note a *Không phải lỗi tiệm · Khác* keeps (the column's CHECK says the same).
NOTE_MAX_LENGTH: Final = 120

#: Refusal codes (`DEC-042`), besides the remedy refusals, which pass through unchanged.
REMEDY_POLICY_UNPUBLISHED: Final = "REMEDY_POLICY_UNPUBLISHED"
NOT_LATE: Final = "NOT_LATE"
ALREADY_DECIDED: Final = "ALREADY_DECIDED"
#: The order is not one the clock applies to: no return leg in its mode, no promise, or not yet
#: delivered. Distinct from `NOT_LATE`, which is a measurement.
LATE_DELIVERY_NOT_MEASURABLE: Final = "LATE_DELIVERY_NOT_MEASURABLE"
REASON_REQUIRED: Final = "LATE_DELIVERY_REASON_REQUIRED"
REASON_NOT_APPLICABLE: Final = "LATE_DELIVERY_REASON_NOT_APPLICABLE"

_RETURN_MODES: Final = tuple(sorted(mode.value for mode in MODES_EXPECTING_RETURN))


class LateDeliveryAuthorizationError(PermissionError):
    """The principal may not read or decide late deliveries in this store."""


class LateDeliveryRefused(ValueError):
    """A decision the rules refuse, by one code. Nothing was written."""

    def __init__(self, code: str, detail: str = "", *, threshold_minutes: int | None = None):
        self.code = code
        self.threshold_minutes = threshold_minutes
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class LateDeliveryRow:
    """One measured late delivery nobody has decided about."""

    order_id: UUID
    ticket_number: int | None
    ticket_issued_on: date | None
    customer_name: str | None
    first_promise_at: datetime
    deadline_at: datetime
    #: `FIRST_PROMISE` or `CUSTOMER_REQUEST` (the newest *Hẹn lại* the customer asked for).
    deadline_basis: str
    delivered_at: datetime
    late_by_minutes: int
    failed_attempts_before_deadline: tuple[datetime, ...]
    settled_total_vnd: int | None
    #: The would-be credit; `None` on a refunded bill or when the domain would refuse it.
    credit_vnd: int | None
    credit_requires_owner: bool
    #: Why there is no figure (the domain's refusal code, or `ORDER_REFUNDED`), else `None`.
    credit_refusal: str | None
    refunded: bool


@dataclass(frozen=True, slots=True)
class LateDeliveryFollowUp:
    """A credited decision whose proposal still has a step to go."""

    order_id: UUID
    ticket_number: int | None
    ticket_issued_on: date | None
    late_by_minutes: int
    incident_id: UUID
    proposal_id: UUID
    proposal_status: str
    amount_vnd: int | None
    approval_id: UUID | None
    #: `EXECUTE` or `AWAIT_OWNER` (the remedy read model's `next_step`).
    next_step: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class LateDeliveryList:
    store_id: UUID
    evaluated_at: datetime
    #: False until the owner publishes `REMEDY_POLICY`: the threshold is one of its figures, so
    #: nothing can be measured as late and the list is empty with this said.
    policy_published: bool
    threshold_minutes: int | None
    staff_approval_ceiling_vnd: int | None
    limit: int
    orders: tuple[LateDeliveryRow, ...]
    #: More late deliveries exist than `orders` shows, or the scan stopped before it could tell.
    truncated: bool
    follow_up: tuple[LateDeliveryFollowUp, ...]
    follow_up_truncated: bool


@dataclass(frozen=True)
class LateDeliveryDecideCommand:
    store_id: UUID
    order_id: UUID
    #: `STORE_FAULT_CREDITED` (*Lỗi của tiệm*) or `NOT_STORE_FAULT`.
    decision: LateDeliveryDecision
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    reason: NotStoreFaultReason | None = None
    note: str | None = None
    #: The instant the decision is made. Tests hold it still; the route passes nothing.
    decided_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredLateDeliveryDecision:
    decision_id: UUID
    order_id: UUID
    decision: str
    reason_code: str | None
    late_by_minutes: int
    deadline_at: datetime
    deadline_basis: str
    delivered_at: datetime
    incident_id: UUID | None
    proposal_id: UUID | None
    proposal_status: str | None
    amount_vnd: int | None
    approval_id: UUID | None
    owner_reasons: tuple[str, ...]
    decided_at: datetime
    replayed: bool


# --- reading what the clock needs ----------------------------------------------------------------


def measure_deliveries(
    cursor: Any, rows: Sequence[tuple[UUID, datetime]]
) -> dict[UUID, LateDeliveryClock]:
    """`late_delivery_clock` for each `(order_id, first_promise)`, from the stored ledgers.

    Two statements for any number of orders: the *Hẹn lại* rows and the `RETURN` legs. Both come
    back in the ledgers' own order, which is the order the domain breaks ties by.
    """

    if not rows:
        return {}
    ids = [order_id for order_id, _ in rows]
    cursor.execute(
        """
        SELECT order_id, new_promise_at, reason_code, changed_at
        FROM order_promise_changes
        WHERE order_id = ANY(%s)
        ORDER BY order_id, changed_at, id
        """,
        (ids,),
    )
    changes: dict[UUID, list[PromiseMove]] = {}
    for row in cursor.fetchall():
        changes.setdefault(_uuid(row[0]), []).append(
            PromiseMove(
                new_promise_at=row[1], reason=PromiseChangeReason(str(row[2])), changed_at=row[3]
            )
        )
    cursor.execute(
        """
        SELECT order_id, leg_kind, outcome, recorded_at
        FROM delivery_legs
        WHERE order_id = ANY(%s) AND leg_kind = 'RETURN'
        ORDER BY order_id, recorded_at, id
        """,
        (ids,),
    )
    legs: dict[UUID, list[LegAttempt]] = {}
    for row in cursor.fetchall():
        legs.setdefault(_uuid(row[0]), []).append(LegAttempt(str(row[1]), str(row[2]), row[3]))
    return {
        order_id: late_delivery_clock(
            first_promise, changes.get(order_id, ()), legs.get(order_id, ())
        )
        for order_id, first_promise in rows
    }


#: Delivered orders of the store that the clock applies to and nobody has decided about, oldest
#: arrival first. The last predicate is a **necessary** condition, never the rule: without a
#: customer-requested *Hẹn lại* the deadline is the first promise, so an arrival no later than
#: the first promise plus the threshold cannot be late by more than it. Every row that passes is
#: measured by the domain, which alone decides.
_CANDIDATES_SQL: Final = """
    SELECT o.id, o.promised_ready_at, r.recorded_at, t.ticket_number, t.issued_on,
           CASE WHEN cu.erased_at IS NULL THEN cu.display_name END
    FROM delivery_legs r
    JOIN orders o ON o.id = r.order_id AND o.store_id = r.store_id
    LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
    LEFT JOIN customers cu ON cu.id = o.customer_id AND cu.store_id = o.store_id
    WHERE r.store_id = %(store)s
      AND r.leg_kind = 'RETURN' AND r.outcome = 'SUCCEEDED'
      AND o.promised_ready_at IS NOT NULL
      AND o.fulfillment_mode = ANY(%(modes)s)
      AND NOT EXISTS (SELECT 1 FROM late_delivery_decisions d WHERE d.order_id = o.id)
      AND (
          r.recorded_at > o.promised_ready_at + make_interval(mins => %(threshold)s)
          OR EXISTS (
              SELECT 1 FROM order_promise_changes c
              WHERE c.order_id = o.id AND c.reason_code = 'CUSTOMER_REQUEST'
          )
      )
    ORDER BY r.recorded_at, o.id
    LIMIT %(scan)s
"""

#: Credited decisions whose proposal has not been executed, with its owner envelope's state.
_FOLLOW_UP_SQL: Final = """
    SELECT d.order_id, t.ticket_number, t.issued_on, d.late_by_minutes, d.incident_id,
           p.id, p.status, p.amount_vnd, p.approval_id, st.status, ar.expires_at, d.decided_at
    FROM late_delivery_decisions d
    JOIN remedy_proposals p ON p.id = d.remedy_proposal_id
    JOIN orders o ON o.id = d.order_id AND o.store_id = d.store_id
    LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
    LEFT JOIN approval_requests ar ON ar.id = p.approval_id
    LEFT JOIN approval_request_states st ON st.approval_request_id = p.approval_id
    WHERE d.store_id = %(store)s AND d.decision = 'STORE_FAULT_CREDITED'
      AND p.status <> 'EXECUTED'
      AND NOT (
          p.status = 'OWNER_APPROVAL_REQUIRED'
          AND coalesce(st.status, 'REQUESTED') = ANY(%(dead)s)
      )
    ORDER BY d.decided_at, d.id
    LIMIT %(limit)s
"""


#: `report-v4`'s population (`LATE-CREDIT-002`): orders whose laundry reached the customer in the
#: window (the succeeded RETURN leg, as the trip figures read it), with a promise, in a mode that
#: expects a return leg. Named here, beside the list's statement, and hashed into the report's
#: version.
LATE_POPULATION_SQL: Final = """
    WITH bounds AS (
        SELECT (%(from_date)s::date)::timestamp AT TIME ZONE %(zone)s AS lower_at,
               (%(to_date)s::date + 1)::timestamp AT TIME ZONE %(zone)s AS upper_at
    )
    SELECT o.id, o.promised_ready_at
    FROM delivery_legs r
    JOIN orders o ON o.id = r.order_id AND o.store_id = r.store_id
    CROSS JOIN bounds b
    WHERE r.store_id = %(store)s AND r.leg_kind = 'RETURN' AND r.outcome = 'SUCCEEDED'
      AND r.recorded_at >= b.lower_at AND r.recorded_at < b.upper_at
      AND o.promised_ready_at IS NOT NULL
      AND o.fulfillment_mode IN ('PICKUP_AND_RETURN', 'RETURN_ONLY')
    ORDER BY r.recorded_at, o.id
"""


class LateDeliveryRepository:
    """Read the late list and record decisions, with authorization and atomic ledger rows."""

    def __init__(
        self,
        idempotency: IdempotencyRepository | None = None,
        remedies: RemedyProposalRepository | None = None,
        incidents: IncidentRepository | None = None,
    ) -> None:
        self._idempotency = idempotency or IdempotencyRepository()
        self._remedies = remedies or RemedyProposalRepository()
        self._incidents = incidents or IncidentRepository()

    @staticmethod
    def list_late(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        as_of: datetime,
        limit: int = LIST_MAX_LIMIT,
    ) -> LateDeliveryList:
        """*Giao trễ cần xử lý*, oldest arrival first, bounded, with the follow-up rows."""

        _require_role(principal)
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=LateDeliveryAuthorizationError,
        )
        if not 1 <= limit <= LIST_MAX_LIMIT:
            raise ValueError(f"the late-delivery list limit is between 1 and {LIST_MAX_LIMIT}")
        follow_up, follow_up_truncated = _follow_up(cursor, store_id=store_id, now=as_of)
        published = read_published_remedy_policy(cursor)
        if published is None:
            return LateDeliveryList(
                store_id=store_id,
                evaluated_at=as_of,
                policy_published=False,
                threshold_minutes=None,
                staff_approval_ceiling_vnd=None,
                limit=limit,
                orders=(),
                truncated=False,
                follow_up=follow_up,
                follow_up_truncated=follow_up_truncated,
            )
        policy = published.policy
        threshold = policy.late_delivery_threshold_minutes
        cursor.execute(
            _CANDIDATES_SQL,
            {
                "store": store_id,
                "modes": list(_RETURN_MODES),
                "threshold": threshold,
                "scan": SCAN_LIMIT + 1,
            },
        )
        candidates = cursor.fetchall()
        scanned_all = len(candidates) <= SCAN_LIMIT
        candidates = candidates[:SCAN_LIMIT]
        clocks = measure_deliveries(cursor, [(_uuid(row[0]), row[1]) for row in candidates])
        late = [row for row in candidates if clocks[_uuid(row[0])].is_late_beyond(threshold)]
        shown = late[:limit]
        orders = tuple(
            _late_row(cursor, row, clocks[_uuid(row[0])], policy=policy, at=as_of) for row in shown
        )
        return LateDeliveryList(
            store_id=store_id,
            evaluated_at=as_of,
            policy_published=True,
            threshold_minutes=threshold,
            staff_approval_ceiling_vnd=policy.staff_approval_ceiling_vnd,
            limit=limit,
            orders=orders,
            truncated=len(late) > limit or not scanned_all,
            follow_up=follow_up,
            follow_up_truncated=follow_up_truncated,
        )

    def decide(
        self, connection: Any, command: LateDeliveryDecideCommand
    ) -> StoredLateDeliveryDecision:
        """Record whose fault a measured late delivery was, all or nothing."""

        _require_role(command.principal)
        decided_at = command.decided_at or datetime.now(UTC)
        if decided_at.tzinfo is None:
            raise ValueError("VALIDATION_ERROR: the decision instant must be timezone-aware")
        reason, note = _checked_reason(command)
        with connection.cursor() as cursor:
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=command.store_id,
                error=LateDeliveryAuthorizationError,
            )
        payload: dict[str, object] = {
            "store_id": str(command.store_id),
            "order_id": str(command.order_id),
            "decision": command.decision.value,
            "reason_code": None if reason is None else reason.value,
            # A digest, not the words: the idempotency row is permanent and the note is not.
            "note_digest": None if note is None else sha256(note.encode()).hexdigest(),
        }

        def decide_once() -> dict[str, object]:
            return self._decide_locked(
                connection, command, reason=reason, note=note, decided_at=decided_at
            )

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"late-delivery-decide:{command.principal.staff_user_id}",
                command.idempotency_key,
                payload,
                decided_at,
            ),
            decide_once,
        )
        return _stored_decision(result.response, replayed=result.replayed)

    def _decide_locked(
        self,
        connection: Any,
        command: LateDeliveryDecideCommand,
        *,
        reason: NotStoreFaultReason | None,
        note: str | None,
        decided_at: datetime,
    ) -> dict[str, object]:
        """Runs inside the idempotency claim's transaction: every write below is one commit."""

        with connection.cursor() as cursor:
            # The order row lock serialises two counters pressing on the same delivery; the
            # second waits here and then reads the first one's decision.
            cursor.execute(
                """
                SELECT store_id, fulfillment_mode, promised_ready_at
                FROM orders WHERE id = %s AND store_id = %s FOR UPDATE
                """,
                (command.order_id, command.store_id),
            )
            order = cursor.fetchone()
            if order is None:
                raise OrderNotVisibleError()
            cursor.execute(
                "SELECT 1 FROM late_delivery_decisions WHERE order_id = %s", (command.order_id,)
            )
            if cursor.fetchone() is not None:
                raise LateDeliveryRefused(ALREADY_DECIDED, "this late delivery was already decided")
            published = read_published_remedy_policy(cursor)
            if published is None:
                raise LateDeliveryRefused(
                    REMEDY_POLICY_UNPUBLISHED,
                    "the owner has not published the remedy policy, so nothing is late yet",
                )
            threshold = published.policy.late_delivery_threshold_minutes
            if str(order[1]) not in _RETURN_MODES or order[2] is None:
                raise LateDeliveryRefused(
                    LATE_DELIVERY_NOT_MEASURABLE,
                    "the order has no return leg in its mode, or no promise",
                )
            clock = measure_deliveries(cursor, [(command.order_id, order[2])])[command.order_id]
        if clock.delivered_at is None or clock.late_by_minutes is None:
            raise LateDeliveryRefused(
                LATE_DELIVERY_NOT_MEASURABLE, "the laundry has not been delivered"
            )
        if not clock.is_late_beyond(threshold):
            raise LateDeliveryRefused(
                NOT_LATE,
                f"measured {clock.late_by_minutes} minutes late; the threshold is {threshold}",
                threshold_minutes=threshold,
            )

        decision_id = uuid4()
        incident_id: UUID | None = None
        proposal = None
        if command.decision is LateDeliveryDecision.STORE_FAULT_CREDITED:
            incident_id = self._incidents.open_from_counter(
                connection,
                StaffIncidentOpenCommand(
                    store_id=command.store_id,
                    order_id=command.order_id,
                    evidence_summary=incident_summary(clock),
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    opened_at=decided_at,
                ),
                principal=command.principal,
            ).incident_id
            # The existing remedy path, unchanged: the server's minutes, the fault attested by
            # the person who pressed, the domain's 10%, the staff limit and the owner above it.
            proposal = self._remedies.propose(
                connection,
                RemedyProposalCommand(
                    store_id=command.store_id,
                    incident_id=incident_id,
                    kind=RemedyKind.LATE_DELIVERY_CREDIT,
                    store_fault_attested=True,
                    principal=command.principal,
                    correlation_id=command.correlation_id,
                    attested_late_by_minutes=clock.late_by_minutes,
                    proposed_at=decided_at,
                ),
            )
        assert clock.delivered_at is not None and clock.late_by_minutes is not None
        late_by, delivered_at = clock.late_by_minutes, clock.delivered_at

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO late_delivery_decisions (
                    id, order_id, store_id, decision, reason_code, note, late_by_minutes,
                    deadline_at, deadline_basis, delivered_at, incident_id, remedy_proposal_id,
                    decided_by, decided_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    decision_id,
                    command.order_id,
                    command.store_id,
                    command.decision.value,
                    None if reason is None else reason.value,
                    note,
                    late_by,
                    clock.deadline,
                    clock.deadline_basis.value,
                    delivered_at,
                    incident_id,
                    None if proposal is None else proposal.proposal_id,
                    command.principal.staff_user_id,
                    decided_at,
                ),
            )

        details: dict[str, object] = {
            "order_id": str(command.order_id),
            "decision": command.decision.value,
            "reason_code": None if reason is None else reason.value,
            "late_by_minutes": late_by,
            "deadline_basis": clock.deadline_basis.value,
            "remedy_proposal_id": None if proposal is None else str(proposal.proposal_id),
            "decision_ref": LATE_DELIVERY_DECISION_REF,
        }
        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="LATE_DELIVERY_DECISION",
                aggregate_id=decision_id,
                aggregate_version=1,
                event_type="LATE_DELIVERY_DECIDED",
                # Codes and the measurement only: the note stays in its own table.
                event_payload=details,
                audit_action="LATE_DELIVERY_DECIDE",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                audit_details={
                    "decision": command.decision.value,
                    "reason_code": None if reason is None else reason.value,
                    "late_by_minutes": late_by,
                },
                outbox_events=(
                    OutboxEvent(
                        "order.late_delivery_decided.v1",
                        {"order_id": str(command.order_id), "decision_id": str(decision_id)},
                        f"order:{command.order_id}:late-delivery-decision",
                    ),
                ),
                occurred_at=decided_at,
            ),
            mutation,
        )
        return {
            "decision_id": str(decision_id),
            "order_id": str(command.order_id),
            "decision": command.decision.value,
            "reason_code": None if reason is None else reason.value,
            "late_by_minutes": late_by,
            "deadline_at": clock.deadline.isoformat(),
            "deadline_basis": clock.deadline_basis.value,
            "delivered_at": delivered_at.isoformat(),
            "incident_id": None if incident_id is None else str(incident_id),
            "proposal_id": None if proposal is None else str(proposal.proposal_id),
            "proposal_status": None if proposal is None else proposal.status.value,
            "amount_vnd": None if proposal is None else proposal.amount_vnd,
            "approval_id": (
                None
                if proposal is None or proposal.approval_id is None
                else str(proposal.approval_id)
            ),
            "owner_reasons": [] if proposal is None else list(proposal.owner_reasons),
            "decided_at": decided_at.isoformat(),
        }


def incident_summary(clock: LateDeliveryClock) -> str:
    """The incident's text: what was measured, in the counter's words, and no personal data."""

    minutes = clock.late_by_minutes or 0
    hours, rest = divmod(minutes, 60)
    span = f"{hours} giờ {rest} phút" if hours else f"{rest} phút"
    basis = (
        "giờ khách hẹn lại"
        if clock.deadline_basis.value == "CUSTOMER_REQUEST"
        else "giờ hẹn trả đầu tiên"
    )
    return (
        f"Giao trễ {span} so với {basis} (máy chủ đo). Nhân viên xác nhận lỗi của tiệm "
        f"({LATE_DELIVERY_DECISION_REF})."
    )


def _late_row(
    cursor: Any,
    row: tuple[Any, ...],
    clock: LateDeliveryClock,
    *,
    policy: RemedyPolicy,
    at: datetime,
) -> LateDeliveryRow:
    order_id = _uuid(row[0])
    preview = read_late_credit_preview(cursor, order_id=order_id, policy=policy, at=at)
    assert clock.delivered_at is not None and clock.late_by_minutes is not None
    return LateDeliveryRow(
        order_id=order_id,
        ticket_number=None if row[3] is None else int(row[3]),
        ticket_issued_on=row[4] if isinstance(row[4], date) else None,
        customer_name=None if row[5] is None else str(row[5]),
        first_promise_at=row[1],
        deadline_at=clock.deadline,
        deadline_basis=clock.deadline_basis.value,
        delivered_at=clock.delivered_at,
        late_by_minutes=clock.late_by_minutes,
        failed_attempts_before_deadline=clock.failed_attempts_before_deadline,
        settled_total_vnd=preview.settled_total_vnd,
        credit_vnd=preview.credit_vnd,
        credit_requires_owner=preview.requires_owner,
        credit_refusal=preview.refusal,
        refunded=preview.refunded,
    )


def _follow_up(
    cursor: Any, *, store_id: UUID, now: datetime
) -> tuple[tuple[LateDeliveryFollowUp, ...], bool]:
    cursor.execute(
        _FOLLOW_UP_SQL,
        {"store": store_id, "dead": list(_DEAD_ENVELOPE_STATUSES), "limit": FOLLOW_UP_LIMIT + 1},
    )
    rows = cursor.fetchall()
    found: list[LateDeliveryFollowUp] = []
    for row in rows[:FOLLOW_UP_LIMIT]:
        step = _next_step(str(row[6]), None if row[9] is None else str(row[9]), row[10], now)
        if step not in (NEXT_STEP_EXECUTE, NEXT_STEP_AWAIT_OWNER):
            continue
        found.append(
            LateDeliveryFollowUp(
                order_id=_uuid(row[0]),
                ticket_number=None if row[1] is None else int(row[1]),
                ticket_issued_on=row[2] if isinstance(row[2], date) else None,
                late_by_minutes=int(row[3]),
                incident_id=_uuid(row[4]),
                proposal_id=_uuid(row[5]),
                proposal_status=str(row[6]),
                amount_vnd=None if row[7] is None else int(row[7]),
                approval_id=None if row[8] is None else _uuid(row[8]),
                next_step=step,
                decided_at=row[11],
            )
        )
    return tuple(found), len(rows) > FOLLOW_UP_LIMIT


def _checked_reason(
    command: LateDeliveryDecideCommand,
) -> tuple[NotStoreFaultReason | None, str | None]:
    """The reason and note as stored, or a refusal. Nothing is read or written first."""

    if command.decision is LateDeliveryDecision.STORE_FAULT_CREDITED:
        if command.reason is not None or (command.note is not None and command.note.strip()):
            raise LateDeliveryRefused(
                REASON_NOT_APPLICABLE, "the shop's fault takes no reason and no note"
            )
        return None, None
    if command.reason is None:
        raise LateDeliveryRefused(REASON_REQUIRED, "say why it was not the shop's fault")
    try:
        note = clean_note(command.note, required=command.reason is NotStoreFaultReason.OTHER)
    except ValueError as error:
        raise LateDeliveryRefused(str(error.args[0])) from error
    return command.reason, note


def _require_role(principal: StaffPrincipal) -> None:
    if not principal.roles & LATE_DELIVERY_ROLES or not principal.mfa_verified:
        raise LateDeliveryAuthorizationError(
            "late deliveries are read and decided by an operations role with MFA"
        )


def _stored_decision(value: dict[str, object], *, replayed: bool) -> StoredLateDeliveryDecision:
    def optional_uuid(key: str) -> UUID | None:
        item = value.get(key)
        return None if item is None else UUID(str(item))

    reasons = value.get("owner_reasons")
    amount = value.get("amount_vnd")
    return StoredLateDeliveryDecision(
        decision_id=UUID(str(value["decision_id"])),
        order_id=UUID(str(value["order_id"])),
        decision=str(value["decision"]),
        reason_code=None if value.get("reason_code") is None else str(value["reason_code"]),
        late_by_minutes=int(str(value["late_by_minutes"])),
        deadline_at=datetime.fromisoformat(str(value["deadline_at"])),
        deadline_basis=str(value["deadline_basis"]),
        delivered_at=datetime.fromisoformat(str(value["delivered_at"])),
        incident_id=optional_uuid("incident_id"),
        proposal_id=optional_uuid("proposal_id"),
        proposal_status=(
            None if value.get("proposal_status") is None else str(value["proposal_status"])
        ),
        amount_vnd=None if amount is None else int(str(amount)),
        approval_id=optional_uuid("approval_id"),
        owner_reasons=tuple(str(item) for item in reasons) if isinstance(reasons, list) else (),
        decided_at=datetime.fromisoformat(str(value["decided_at"])),
        replayed=replayed,
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def late_orders_in(
    cursor: Any, rows: Iterable[tuple[UUID, datetime]], *, threshold_minutes: int
) -> tuple[UUID, ...]:
    """The orders among `rows` the clock measures as late by more than the threshold (the report's
    population, `report-v4`)."""

    listed = list(rows)
    clocks = measure_deliveries(cursor, listed)
    return tuple(
        order_id for order_id, _ in listed if clocks[order_id].is_late_beyond(threshold_minutes)
    )


__all__ = [
    "ALREADY_DECIDED",
    "FOLLOW_UP_LIMIT",
    "LATE_DELIVERY_NOT_MEASURABLE",
    "LATE_DELIVERY_ROLES",
    "LATE_POPULATION_SQL",
    "LIST_MAX_LIMIT",
    "NOTE_MAX_LENGTH",
    "NOT_LATE",
    "REASON_NOT_APPLICABLE",
    "REASON_REQUIRED",
    "REMEDY_POLICY_UNPUBLISHED",
    "SCAN_LIMIT",
    "LateDeliveryAuthorizationError",
    "LateDeliveryDecideCommand",
    "LateDeliveryFollowUp",
    "LateDeliveryList",
    "LateDeliveryRefused",
    "LateDeliveryRepository",
    "LateDeliveryRow",
    "StoredLateDeliveryDecision",
    "incident_summary",
    "late_orders_in",
    "measure_deliveries",
]
