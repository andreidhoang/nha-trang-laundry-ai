"""Read an order's promise, and move it with a reason (*Hẹn lại*) (`PROMISE-001`, `DEC-037`).

The first promise is written by `RECEIVE` (`promise_policy.write_receive_promise`) and never changes
again: the on-time figure counts against it. What the customer was last told
(`orders.current_promise_at`) moves only here, with a reason the counter picks, one
`order_promise_changes` row, an `ORDER_PROMISE_CHANGED` event, an audit row and an outbox row --
in one transaction, at a new row version, behind `If-Match` and an `Idempotency-Key`. The optional
note stays in the change row; no event, audit or outbox payload carries it.

The read answers what the counter needs before and after *Nhận đồ*: whether the owner's policy is
in force, the promise and its state now, its history, and -- while the goods are not yet accepted
-- what pressing *Nhận đồ* would promise and which choices it takes. Every figure is the domain's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain.promise import (
    PromiseChangeReason,
    PromiseError,
    PromiseOptions,
    PromiseRefusal,
    PromiseState,
    check_promise_time,
    promise_options,
    promise_state,
)

from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.orders import (
    OrderAuthorizationError,
    OrderNotVisibleError,
    OrderPromiseRefused,
    OrderStateError,
    OrderView,
    _order_view_document,
    _order_view_from_document,
    _order_view_row,
    _read_view_row,
    _require_order_mutation,
    _require_order_read,
)
from nha_trang_laundry_db.promise_policy import (
    TURNAROUND_POLICY_UNPUBLISHED,
    order_service_codes,
    read_published_turnaround_policy,
)
from nha_trang_laundry_db.store_access import is_store_member, require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: How many changes the read returns, newest last. A promise moved more often than this is itself
#: the finding; the count says how many there were.
CHANGE_HISTORY_LIMIT: Final = 20
#: The longest note a *Hẹn lại* keeps (the column's CHECK says the same).
NOTE_MAX_LENGTH: Final = 120

#: Orders whose goods are not yet accepted for work: the only ones *Nhận đồ* can still promise.
_BEFORE_ACCEPTANCE: Final = frozenset(
    {"DRAFT", "REQUESTED", "STORE_CONFIRMATION_PENDING", "CONFIRMED"}
)


@dataclass(frozen=True, slots=True)
class PromiseChange:
    previous_promise_at: datetime
    new_promise_at: datetime
    reason_code: str
    note: str | None
    changed_by_staff_id: UUID
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class OrderPromiseRead:
    order_id: UUID
    policy_published: bool
    policy_version: int | None
    opens_at: time | None
    closes_at: time | None
    promised_ready_at: datetime | None
    current_promise_at: datetime | None
    promise_basis: str | None
    promise_rule_id: str | None
    state: PromiseState | None
    changes: tuple[PromiseChange, ...]
    change_count: int
    #: Present only while *Nhận đồ* can still promise: policy in force, no promise yet, goods not
    #: yet accepted. Computed at `evaluated_at`.
    options: PromiseOptions | None
    evaluated_at: datetime


@dataclass(frozen=True)
class PromiseChangeCommand:
    order_id: UUID
    expected_row_version: int
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    new_promise_at: datetime
    reason: PromiseChangeReason
    note: str | None = None
    #: The instant the change is judged at ("later than now"). Tests hold it still; the route
    #: passes nothing and the server's clock is read here.
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class PromiseChangeResult:
    view: OrderView
    replayed: bool


class OrderPromiseRepository:
    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()

    @staticmethod
    def read(
        cursor: Any, *, order_id: UUID, principal: StaffPrincipal, now: datetime
    ) -> OrderPromiseRead:
        """The order's promise as the counter reads it. 404-shaped for a stranger's order."""

        _require_order_read(principal)
        cursor.execute(
            """
            SELECT store_id, commercial_status, production_accepted_at, production_ready_at,
                   promised_ready_at, current_promise_at, promise_basis, promise_rule_id
            FROM orders WHERE id = %s
            """,
            (order_id,),
        )
        row = cursor.fetchone()
        if row is None or not is_store_member(
            cursor, staff_user_id=principal.staff_user_id, store_id=_uuid(row[0])
        ):
            raise OrderNotVisibleError()
        commercial = str(row[1])
        ready_at, first, current = row[3], row[4], row[5]
        published = read_published_turnaround_policy(cursor)
        cursor.execute(
            """
            SELECT previous_promise_at, new_promise_at, reason_code, note, changed_by_staff_id,
                   changed_at, count(*) OVER ()
            FROM order_promise_changes
            WHERE order_id = %s
            ORDER BY changed_at DESC, id DESC
            LIMIT %s
            """,
            (order_id, CHANGE_HISTORY_LIMIT),
        )
        found = cursor.fetchall()
        changes = tuple(
            PromiseChange(
                previous_promise_at=item[0],
                new_promise_at=item[1],
                reason_code=str(item[2]),
                note=None if item[3] is None else str(item[3]),
                changed_by_staff_id=_uuid(item[4]),
                changed_at=item[5],
            )
            for item in reversed(found)
        )
        options = None
        if published is not None and first is None and commercial in _BEFORE_ACCEPTANCE:
            options = promise_options(
                published.policy,
                accepted_at=now,
                service_codes=order_service_codes(cursor, order_id),
            )
        return OrderPromiseRead(
            order_id=order_id,
            policy_published=published is not None,
            policy_version=None if published is None else published.version,
            opens_at=None if published is None else published.policy.opens_at,
            closes_at=None if published is None else published.policy.closes_at,
            promised_ready_at=first,
            current_promise_at=current,
            promise_basis=None if row[6] is None else str(row[6]),
            promise_rule_id=None if row[7] is None else str(row[7]),
            state=None
            if commercial == "CANCELLED"
            else promise_state(current, ready_at=ready_at, now=now),
            changes=changes,
            change_count=int(found[0][6]) if found else 0,
            options=options,
            evaluated_at=now,
        )

    def change(self, connection: Any, command: PromiseChangeCommand) -> PromiseChangeResult:
        """*Hẹn lại*: move what the customer was told, with a reason, all or nothing."""

        _require_order_mutation(command.principal)
        if command.expected_row_version < 1:
            raise OrderStateError("a valid row version is required")
        note = None if command.note is None else command.note.strip() or None
        if note is not None and len(note) > NOTE_MAX_LENGTH:
            raise OrderPromiseRefused(
                "PROMISE_NOTE_TOO_LONG", f"PROMISE_NOTE_TOO_LONG: at most {NOTE_MAX_LENGTH}"
            )
        if command.reason is PromiseChangeReason.OTHER and note is None:
            raise OrderPromiseRefused(
                "PROMISE_NOTE_REQUIRED", "PROMISE_NOTE_REQUIRED: say why in a few words"
            )
        if command.new_promise_at.tzinfo is None:
            raise OrderStateError("VALIDATION_ERROR: the new promise must be timezone-aware")
        occurred_at = command.occurred_at or datetime.now(UTC)
        with connection.cursor() as cursor:
            cursor.execute("SELECT store_id FROM orders WHERE id = %s", (command.order_id,))
            located = cursor.fetchone()
            if located is not None:
                require_store_membership(
                    cursor,
                    staff_user_id=command.principal.staff_user_id,
                    store_id=_uuid(located[0]),
                    error=OrderAuthorizationError,
                )
        payload: dict[str, object] = {
            "order_id": str(command.order_id),
            "expected_row_version": command.expected_row_version,
            "new_promise_at": command.new_promise_at.astimezone(UTC).isoformat(),
            "reason": command.reason.value,
            "note": note,
        }

        def change_once() -> dict[str, object]:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT store_id, commercial_status, production_status, current_promise_at,
                           row_version
                    FROM orders WHERE id = %s FOR UPDATE
                    """,
                    (command.order_id,),
                )
                row = cursor.fetchone()
                if row is not None:
                    require_store_membership(
                        cursor,
                        staff_user_id=command.principal.staff_user_id,
                        store_id=_uuid(row[0]),
                        error=OrderAuthorizationError,
                    )
                if row is None or int(row[4]) != command.expected_row_version:
                    raise OrderStateError("STALE_VERSION: order is missing or stale")
                published = read_published_turnaround_policy(cursor)
            if published is None:
                raise OrderPromiseRefused(
                    TURNAROUND_POLICY_UNPUBLISHED,
                    f"{TURNAROUND_POLICY_UNPUBLISHED}: the owner has not published the "
                    "turnaround policy",
                )
            store_id = _uuid(row[0])
            previous = row[3]
            if previous is None:
                raise OrderPromiseRefused(
                    "PROMISE_NOT_SET", "PROMISE_NOT_SET: only Nhận đồ sets an order's first promise"
                )
            if str(row[1]) in {"CANCELLED", "COMPLETED"} or str(row[2]) in {
                "READY_AT_STORE",
                "RELEASED",
            }:
                raise OrderPromiseRefused(
                    "PROMISE_ORDER_DONE",
                    "PROMISE_ORDER_DONE: the laundry is finished or the order is closed",
                )
            try:
                new_at = check_promise_time(
                    published.policy, command.new_promise_at, after=occurred_at
                )
            except PromiseError as error:
                raise OrderPromiseRefused(error.code.value, str(error)) from error
            if new_at == previous:
                raise OrderPromiseRefused(
                    PromiseRefusal.PROMISE_UNCHANGED.value, "PROMISE_UNCHANGED"
                )
            next_version = command.expected_row_version + 1

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO order_promise_changes (
                        id, order_id, store_id, previous_promise_at, new_promise_at, reason_code,
                        note, changed_by_staff_id, changed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        uuid4(),
                        command.order_id,
                        store_id,
                        previous,
                        new_at,
                        command.reason.value,
                        note,
                        command.principal.staff_user_id,
                        occurred_at,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE orders SET current_promise_at = %s, row_version = row_version + 1
                    WHERE id = %s AND row_version = %s
                    RETURNING id
                    """,
                    (new_at, command.order_id, command.expected_row_version),
                )
                if cursor.fetchone() is None:
                    raise OrderStateError("STALE_VERSION: order changed during the change")

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="ORDER",
                    aggregate_id=command.order_id,
                    aggregate_version=next_version,
                    event_type="ORDER_PROMISE_CHANGED",
                    # The reason code only: the note is free text and stays in its own table.
                    event_payload={
                        "previous_promise_at": previous.isoformat(),
                        "new_promise_at": new_at.isoformat(),
                        "reason_code": command.reason.value,
                    },
                    audit_action="ORDER_PROMISE_CHANGE",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    audit_details={"reason_code": command.reason.value},
                    outbox_events=(
                        OutboxEvent(
                            "order.promise_changed.v1",
                            {
                                "order_id": str(command.order_id),
                                "new_promise_at": new_at.isoformat(),
                                "row_version": next_version,
                            },
                            f"order:{command.order_id}:promise:{next_version}",
                        ),
                    ),
                    occurred_at=occurred_at,
                ),
                mutation,
            )
            return _order_view_document(
                _order_view_row(_read_view_row(connection, command.order_id))
            )

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"order:{command.order_id}:promise",
                command.idempotency_key,
                payload,
                occurred_at,
            ),
            change_once,
        )
        try:
            view = _order_view_from_document(result.response)
        except (KeyError, TypeError, ValueError) as error:
            raise OrderStateError("stored idempotent promise result is invalid") from error
        return PromiseChangeResult(view=view, replayed=result.replayed)


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "CHANGE_HISTORY_LIMIT",
    "NOTE_MAX_LENGTH",
    "OrderPromiseRead",
    "OrderPromiseRepository",
    "PromiseChange",
    "PromiseChangeCommand",
    "PromiseChangeResult",
]
