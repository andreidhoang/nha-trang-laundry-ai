"""Laundry waiting for pickup: the list, contact attempts, waivers, disposal (`UNCLAIMED-001`).

`DEC-036`. Every decision is `nha_trang_laundry_domain.unclaimed`'s, over facts read here:

* **The waiting list** ("Đồ chờ lấy"): each running order whose laundry is finished and on the shelf
  for the customer to collect, oldest first, with its days waiting, its contact attempts and the
  storage fee it owes now. A linked customer's name and phone ride along -- the phone only for the
  roles that call customers (`PHONE_VISIBLE_ROLES`); an auditor reads the last four digits.
* **Contact attempts**: append-only rows, recorded by the operations roles, legal only while the
  order is waiting. They work before the storage policy is published. Since `PICKUP-REMIND-001`
  (`DEC-043`, `0065`) an attempt may name the pickup reminder it answers (`reminder_step`), checked
  by `pickup_reminders.check_reminder_attempt` under the order's lock; `MESSAGE_SENT` needs one and
  the egress guard's allowance. Every attempt, reminder or not, counts toward disposal.
* **The waiver** (*Miễn phí lưu kho*): an `OPS_APPROVER` or the owner, with a reason, while a fee is
  accruing. Nothing accrues on the order afterwards. The order's row version advances, because what
  it owes changed: a payment sheet opened before the waiver is refused `STALE_VERSION`.
  `MONEY-LIFECYCLE-009`: it waives only the part of the fee the ledger does not cover -- what was
  paid stays owed-for -- and when that leaves nothing owed it settles the order in the same
  transaction (settlement row, the kept fee part fixed beside it, balance `PAID`).
* **Disposal** (*Thanh lý*): the owner, with MFA, when the domain's verdict allows it. One
  `order_disposals` row (what was owed, kept and written off), then the order goes
  ACTIVE -> CANCELLATION_REVIEW -> CANCELLED with custody resolution `UNCLAIMED_DISPOSED`, each move
  written by the same `_apply_locked_transition` every cancellation uses. Money already paid stays
  on the ledger; `0060` checks at commit that the disposal kept exactly what the payments sum to.

No phone number, note or reason appears in any event, audit or outbox payload: notes and reasons
stay in their own tables, and the list reads the phone from the sealed column at read time only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    ProductionStatus,
)
from nha_trang_laundry_domain.customers import national_from_e164
from nha_trang_laundry_domain.payments import owed_charges, payment_position
from nha_trang_laundry_domain.pickup_reminders import (
    PICKUP_REMINDER_DECISION,
    ReminderRefusal,
    ReminderStep,
)
from nha_trang_laundry_domain.settlement import (
    QuotedTotal,
    SettlementNotSupported,
    SettlementShape,
    evaluate_settlement,
)
from nha_trang_laundry_domain.unclaimed import (
    ContactChannel,
    ContactOutcome,
    DisposalVerdict,
    OrderStorageFee,
    StorageClock,
    StorageFeeStatus,
    WaiverEffect,
    awaiting_pickup,
    clean_note,
    days_waiting,
    disposal_money,
    disposal_rule_vi,
    disposal_verdict,
    order_storage_fee,
    receipt_line_vi,
    waiver_effect,
)

from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.orders import (
    _LOCK_ORDER_FOR_TRANSITION_SQL,
    OrderNotVisibleError,
    OrderRepository,
    OrderStateError,
    OrderTransitionCommand,
    OrderView,
    _order_view_document,
    _order_view_from_document,
    _order_view_row,
    _read_view_row,
)
from nha_trang_laundry_db.personal_data import open_phone
from nha_trang_laundry_db.query_version import QueryVersion, query_version
from nha_trang_laundry_db.settlement import collected_by_for_shape
from nha_trang_laundry_db.storage_fees import (
    STORAGE_POLICY_UNPUBLISHED,
    PublishedStoragePolicy,
    insert_fixed_storage_fee,
    pause_from_columns,
    read_published_storage_policy,
    storage_fee_for_order,
)
from nha_trang_laundry_db.store_access import is_store_member, require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Who reads the waiting list and an order's storage facts: the operations roles and the auditor.
UNCLAIMED_READ_ROLES: Final = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR, StaffRole.AUDITOR}
)
#: The roles that call customers, and so see the full number on the list (`DEC-034`).
PHONE_VISIBLE_ROLES: Final = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
)
#: Who records a contact attempt: the people at the counter who make the call.
CONTACT_ROLES: Final = PHONE_VISIBLE_ROLES
#: `DEC-036`: "an OPS_APPROVER or the owner may waive it in one press".
WAIVER_ROLES: Final = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER})
#: `DEC-036`: "the owner may approve thanh lý".
DISPOSAL_ROLES: Final = frozenset({StaffRole.OWNER_ADMIN})

#: The waiting list is bounded; `truncated` and `total_count` say when it stopped.
LIST_DEFAULT_LIMIT: Final = 100
LIST_MAX_LIMIT: Final = 200
#: How many attempts an order's storage read lists (newest last); `attempts_total` is all of them.
ATTEMPT_READ_LIMIT: Final = 50
#: How many attempt times per order the list reads to judge the disposal rule. The rule needs a
#: handful; an order with more than this many attempts meets it on the ones read.
ATTEMPT_TIMES_LIMIT: Final = 200

#: The four conditions of `unclaimed.awaiting_pickup`, as SQL over `orders o`. A test pins the two
#: statements together.
AWAITING_PICKUP_SQL: Final = (
    "o.commercial_status = 'ACTIVE' AND o.production_status = 'READY_AT_STORE' "
    "AND NOT o.self_collection_recorded "
    "AND o.fulfillment_mode NOT IN ('PICKUP_AND_RETURN', 'RETURN_ONLY')"
)


#: `DAILY-SUMMARY-001`'s waiting line (round 7 wave 2 integration): the days the evening summary
#: counts past -- "chờ quá 20 ngày", "quá 60 ngày" -- in the same shop-local calendar days the list
#: prints (`unclaimed.days_waiting`). The summary's own words, not the owner's published figures:
#: the list and its count work before the storage policy is published, and so does the line.
WAITING_SUMMARY_THRESHOLDS: Final = (20, 60)
#: How many waiting orders the count reads. A shop's shelf is far smaller; past it the count would
#: be a floor, and the caller omits the line (`SOURCE_TRUNCATED`) rather than print it low.
WAITING_COUNT_READ_LIMIT: Final = 5000

#: The count's statement: the waiting population of the list, exactly (`AWAITING_PICKUP_SQL`), and
#: each order's ready time, from which the domain counts the days.
_WAITING_COUNT_SQL: Final = f"""
    SELECT o.production_ready_at
    FROM orders o
    WHERE o.store_id = %s AND {AWAITING_PICKUP_SQL}
    ORDER BY o.production_ready_at ASC NULLS FIRST, o.id
    LIMIT %s
"""

#: The published version of the count, carried among the evening summary's sources.
WAITING_COUNT_QUERY: Final[QueryVersion] = query_version(
    "awaiting-pickup-count-v1",
    _WAITING_COUNT_SQL,
    ",".join(str(days) for days in WAITING_SUMMARY_THRESHOLDS),
    str(WAITING_COUNT_READ_LIMIT),
)


class UnclaimedAuthorizationError(PermissionError):
    """The caller's role, MFA or store does not allow this."""


class UnclaimedRefused(ValueError):
    """A request the rules refuse, by code (`reason_codes` lists every one that applies)."""

    def __init__(self, code: str, *, reason_codes: tuple[str, ...] = ()) -> None:
        self.code = code
        self.reason_codes = reason_codes or (code,)
        super().__init__(code)


class ReminderRefused(UnclaimedRefused):
    """A pickup-reminder request the rules refuse (`PICKUP-REMIND-001`), answered as the refusals
    above are, naming `DEC-043`."""

    decision = PICKUP_REMINDER_DECISION


# --- read models ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoragePolicySummary:
    version: int
    free_days: int
    fee_per_started_day_vnd: int
    fee_cap_percent: int
    disposal_from_day: int
    disposal_min_attempts: int
    disposal_min_attempt_days: int
    receipt_line_vi: str
    disposal_rule_vi: str


def _summary(published: PublishedStoragePolicy | None) -> StoragePolicySummary | None:
    if published is None:
        return None
    policy = published.policy
    return StoragePolicySummary(
        version=published.version,
        free_days=policy.free_days,
        fee_per_started_day_vnd=policy.fee_per_started_day_vnd,
        fee_cap_percent=policy.fee_cap_percent,
        disposal_from_day=policy.disposal_from_day,
        disposal_min_attempts=policy.disposal_min_attempts,
        disposal_min_attempt_days=policy.disposal_min_attempt_days,
        receipt_line_vi=receipt_line_vi(policy),
        disposal_rule_vi=disposal_rule_vi(policy),
    )


@dataclass(frozen=True, slots=True)
class AwaitingPickupRow:
    order_id: UUID
    row_version: int
    balance: str
    ticket_number: int | None
    ticket_issued_on: date | None
    customer_id: UUID | None
    customer_name: str | None
    #: The national form for a role that calls customers; `None` otherwise, or when none is on file.
    phone: str | None
    phone_last4: str | None
    has_phone: bool
    ready_at: datetime | None
    days_waiting: int | None
    attempts_count: int
    last_attempt_at: datetime | None
    last_attempt_outcome: str | None
    fee: OrderStorageFee
    remaining_vnd: int | None
    disposal: DisposalVerdict


@dataclass(frozen=True, slots=True)
class AwaitingPickupList:
    store_id: UUID
    evaluated_at: datetime
    policy: StoragePolicySummary | None
    limit: int
    total_count: int
    truncated: bool
    phone_visible: bool
    orders: tuple[AwaitingPickupRow, ...]


@dataclass(frozen=True, slots=True)
class WaitingCounts:
    """Đồ chờ lấy, counted: how many orders wait, and how many have waited past each threshold."""

    evaluated_at: datetime
    waiting: int
    #: `(days, count)` for each of `WAITING_SUMMARY_THRESHOLDS`: orders waiting MORE than `days`.
    over: tuple[tuple[int, int], ...]
    #: More waiting orders than the count reads; the figures are then a floor.
    truncated: bool


@dataclass(frozen=True, slots=True)
class ContactAttemptView:
    attempt_id: UUID
    channel: str
    outcome: str
    note: str | None
    attempted_by_staff_id: UUID
    attempted_by_name: str | None
    attempted_at: datetime
    reminder_step: str | None = None


@dataclass(frozen=True, slots=True)
class WaiverView:
    waived_amount_vnd: int
    days_waiting: int
    reason: str
    waived_by_staff_id: UUID
    waived_by_name: str | None
    waived_at: datetime


@dataclass(frozen=True, slots=True)
class DisposalView:
    days_waiting: int
    attempts_counted: int
    attempt_days: int
    owed_vnd: int
    storage_fee_vnd: int
    kept_vnd: int
    written_off_vnd: int
    disposed_by_staff_id: UUID
    disposed_by_name: str | None
    disposed_at: datetime


@dataclass(frozen=True, slots=True)
class OrderStorageRead:
    order_id: UUID
    store_id: UUID
    row_version: int
    evaluated_at: datetime
    policy: StoragePolicySummary | None
    awaiting: bool
    ready_at: datetime | None
    days_waiting: int | None
    fee: OrderStorageFee
    waiver: WaiverView | None
    attempts: tuple[ContactAttemptView, ...]
    attempts_total: int
    disposal_verdict: DisposalVerdict
    disposal: DisposalView | None
    #: `MONEY-LIFECYCLE-009` (A3): what *Miễn phí lưu kho* would do now -- the part waived, the
    #: part kept because it was paid, and whether it settles the order -- or `None` when nothing
    #: unpaid is left to waive. The waiver sheet states it before the press.
    waiver_effect: WaiverEffect | None = None


# --- commands -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ContactAttemptCommand:
    order_id: UUID
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    channel: ContactChannel
    outcome: ContactOutcome
    note: str | None = None
    #: Tests hold the clock still; the route passes nothing and the server's clock is read here.
    attempted_at: datetime | None = None
    #: `PICKUP-REMIND-001`: the reminder this attempt answers, or None for a plain attempt.
    reminder_step: ReminderStep | None = None


@dataclass(frozen=True)
class StoredContactAttempt:
    attempt_id: UUID
    order_id: UUID
    ordinal: int
    channel: str
    outcome: str
    attempted_at: datetime
    replayed: bool
    reminder_step: str | None = None


@dataclass(frozen=True)
class WaiverCommand:
    order_id: UUID
    expected_row_version: int
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    reason: str
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class DisposalCommand:
    order_id: UUID
    expected_row_version: int
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class UnclaimedOrderResult:
    view: OrderView
    replayed: bool


# --- the repository -------------------------------------------------------------------------------


class UnclaimedRepository:
    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()
        self._orders = OrderRepository(self._idempotency)

    # --- reads ---------------------------------------------------------------------------------

    @staticmethod
    def list_awaiting_pickup(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        as_of: datetime,
        limit: int = LIST_DEFAULT_LIMIT,
    ) -> AwaitingPickupList:
        """Đồ chờ lấy: the store's waiting orders, longest-waiting first, bounded."""

        _require(principal, UNCLAIMED_READ_ROLES, "reading laundry waiting for pickup")
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=UnclaimedAuthorizationError,
        )
        if not 1 <= limit <= LIST_MAX_LIMIT:
            raise ValueError(f"the waiting list limit is between 1 and {LIST_MAX_LIMIT}")
        published = read_published_storage_policy(cursor)
        cursor.execute(
            f"SELECT count(*) FROM orders o WHERE o.store_id = %s AND {AWAITING_PICKUP_SQL}",
            (store_id,),
        )
        counted = cursor.fetchone()
        total = int(counted[0]) if counted else 0
        cursor.execute(
            f"""
            SELECT o.id, o.row_version, o.balance_status, t.ticket_number, t.issued_on,
                   o.customer_id, cu.display_name, cu.phone_ciphertext, cu.phone_last4,
                   cu.erased_at, o.production_ready_at,
                   CASE WHEN r.display_total_min_vnd = r.display_total_max_vnd
                        THEN r.display_total_min_vnd END AS quoted_total,
                   EXISTS (SELECT 1 FROM order_settlements s WHERE s.order_id = o.id),
                   (SELECT f.amount_vnd FROM order_storage_fees f WHERE f.order_id = o.id),
                   EXISTS (SELECT 1 FROM storage_fee_waivers w WHERE w.order_id = o.id),
                   (SELECT coalesce(sum(p.amount_vnd), 0) FROM order_payments p
                     WHERE p.order_id = o.id),
                   (SELECT count(*) FROM order_contact_attempts a WHERE a.order_id = o.id),
                   (
                       SELECT coalesce(jsonb_agg(recent.attempted_at ORDER BY recent.attempted_at),
                                       '[]'::jsonb)
                       FROM (
                           SELECT a.attempted_at FROM order_contact_attempts a
                           WHERE a.order_id = o.id
                           ORDER BY a.attempted_at DESC, a.id DESC
                           LIMIT {ATTEMPT_TIMES_LIMIT}
                       ) AS recent
                   ),
                   (
                       SELECT jsonb_build_object('at', a.attempted_at, 'outcome', a.outcome)
                       FROM order_contact_attempts a WHERE a.order_id = o.id
                       ORDER BY a.attempted_at DESC, a.id DESC LIMIT 1
                   ),
                   o.commercial_status, o.production_status, o.fulfillment_mode,
                   o.self_collection_recorded, o.storage_paused_at, o.storage_paused_days
            FROM orders o
            JOIN quote_revisions r
              ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
            LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
            LEFT JOIN customers cu ON cu.id = o.customer_id AND cu.store_id = o.store_id
            WHERE o.store_id = %s AND {AWAITING_PICKUP_SQL}
            ORDER BY o.production_ready_at ASC NULLS FIRST, o.id
            LIMIT %s
            """,
            (store_id, limit + 1),
        )
        rows = cursor.fetchall()
        visible = bool(principal.roles & PHONE_VISIBLE_ROLES)
        policy = None if published is None else published.policy
        found: list[AwaitingPickupRow] = []
        for row in rows[:limit]:
            order_id = _uuid(row[0])
            erased = row[9] is not None
            sealed = row[7]
            ready_at = row[10] if isinstance(row[10], datetime) else None
            awaiting = awaiting_pickup(
                commercial=CommercialOrderStatus(str(row[19])),
                production=ProductionStatus(str(row[20])),
                fulfillment_mode=FulfillmentMode(str(row[21])),
                self_collection_recorded=bool(row[22]),
            )
            quoted = None if row[11] is None else int(str(row[11]))
            paid = int(str(row[15]))
            fee = order_storage_fee(
                policy,
                # Every row here waits for pickup (`AWAITING_PICKUP_SQL`); the days of the holds
                # lifted since it was ready do not count (`DEC-047`).
                clock=StorageClock.RUNNING if awaiting else StorageClock.STOPPED,
                ready_at=ready_at,
                as_of=as_of,
                quoted_total_vnd=quoted,
                waived=bool(row[14]),
                settled=bool(row[12]),
                fixed_vnd=None if row[13] is None else int(str(row[13])),
                paid_vnd=paid,
                pause=pause_from_columns(row[23], row[24]),
            )
            times = tuple(datetime.fromisoformat(str(value)) for value in (row[17] or []))
            last = row[18] if isinstance(row[18], dict) else None
            found.append(
                AwaitingPickupRow(
                    order_id=order_id,
                    row_version=int(str(row[1])),
                    balance=str(row[2]),
                    ticket_number=None if row[3] is None else int(str(row[3])),
                    ticket_issued_on=row[4] if isinstance(row[4], date) else None,
                    customer_id=None if row[5] is None else _uuid(row[5]),
                    customer_name=None if erased or row[6] is None else str(row[6]),
                    phone=(
                        national_from_e164(
                            open_phone(sealed, customer_id=_uuid(row[5]), store_id=store_id)
                        )
                        if visible and sealed is not None and not erased
                        else None
                    ),
                    phone_last4=None if erased or row[8] is None else str(row[8]),
                    has_phone=sealed is not None and not erased,
                    ready_at=ready_at,
                    days_waiting=None if ready_at is None else days_waiting(ready_at, as_of),
                    attempts_count=int(str(row[16])),
                    last_attempt_at=(
                        None if last is None else datetime.fromisoformat(str(last["at"]))
                    ),
                    last_attempt_outcome=None if last is None else str(last["outcome"]),
                    fee=fee,
                    remaining_vnd=payment_position(
                        owed_charges(QuotedTotal(quoted, quoted), storage_fee_vnd=fee.amount_vnd),
                        paid,
                    ).remaining_vnd,
                    disposal=disposal_verdict(
                        policy,
                        awaiting=awaiting,
                        ready_at=ready_at,
                        as_of=as_of,
                        attempt_times=times,
                    ),
                )
            )
        return AwaitingPickupList(
            store_id=store_id,
            evaluated_at=as_of,
            policy=_summary(published),
            limit=limit,
            total_count=total,
            truncated=len(rows) > limit,
            phone_visible=visible,
            orders=tuple(found),
        )

    @staticmethod
    def count_waiting(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        as_of: datetime,
        thresholds: tuple[int, ...] = WAITING_SUMMARY_THRESHOLDS,
    ) -> WaitingCounts:
        """The waiting list's population, counted past the summary's thresholds at `as_of`.

        The list's own conditions (`AWAITING_PICKUP_SQL`) and the list's own day rule
        (`unclaimed.days_waiting`): an order is "over 20 days" exactly when the list would print
        more than 20 beside it. Counts only -- no customer, no phone -- under the list's own gate.
        `thresholds` defaults to the summary's two; `SUMMARY-ATTENTION-001` also asks for the days
        just before the published storage fee starts.
        """

        _require(principal, UNCLAIMED_READ_ROLES, "counting laundry waiting for pickup")
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=UnclaimedAuthorizationError,
        )
        cursor.execute(_WAITING_COUNT_SQL, (store_id, WAITING_COUNT_READ_LIMIT + 1))
        rows = cursor.fetchall()
        truncated = len(rows) > WAITING_COUNT_READ_LIMIT
        waited = [
            days_waiting(row[0], as_of)
            for row in rows[:WAITING_COUNT_READ_LIMIT]
            if isinstance(row[0], datetime)
        ]
        return WaitingCounts(
            evaluated_at=as_of,
            waiting=min(len(rows), WAITING_COUNT_READ_LIMIT),
            over=tuple(
                (threshold, sum(1 for days in waited if days > threshold))
                for threshold in thresholds
            ),
            truncated=truncated,
        )

    @staticmethod
    def read_order_storage(
        cursor: Any, *, order_id: UUID, principal: StaffPrincipal, as_of: datetime
    ) -> OrderStorageRead:
        """One order's storage facts: the fee now, the waiver, the attempts, the disposal verdict.

        404-shaped for a stranger's order, as the order read is.
        """

        _require(principal, UNCLAIMED_READ_ROLES, "reading an order's storage")
        cursor.execute("SELECT store_id, row_version FROM orders WHERE id = %s", (order_id,))
        located = cursor.fetchone()
        if located is None or not is_store_member(
            cursor, staff_user_id=principal.staff_user_id, store_id=_uuid(located[0])
        ):
            raise OrderNotVisibleError()
        return _storage_read(
            cursor,
            order_id=order_id,
            store_id=_uuid(located[0]),
            row_version=int(str(located[1])),
            as_of=as_of,
        )

    # --- contact attempts ----------------------------------------------------------------------

    def record_contact_attempt(
        self, connection: Any, command: ContactAttemptCommand
    ) -> StoredContactAttempt:
        """Ghi lần liên hệ: one attempt, append-only, while the order is waiting for pickup."""

        _require(command.principal, CONTACT_ROLES, "recording a contact attempt", mfa=True)
        try:
            note = clean_note(command.note)
        except ValueError as error:
            raise UnclaimedRefused(str(error)) from error
        step = command.reminder_step
        if command.outcome is ContactOutcome.MESSAGE_SENT and step is None:
            # A reminder's message is what `MESSAGE_SENT` records; without the step there is no
            # message the guard could have allowed.
            raise ReminderRefused(ReminderRefusal.REMINDER_STEP_REQUIRED.value)
        attempted_at = command.attempted_at or datetime.now(UTC)
        _require_member_of_order(connection, command.order_id, command.principal)
        payload: dict[str, object] = {
            "order_id": str(command.order_id),
            "channel": command.channel.value,
            "outcome": command.outcome.value,
            "note": note,
        }
        if step is not None:
            # Only when named, so a plain attempt's request digest is what it was before `0065`.
            payload["reminder_step"] = step.value

        def record_once() -> dict[str, object]:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT store_id, commercial_status, production_status, fulfillment_mode,
                           self_collection_recorded
                    FROM orders WHERE id = %s FOR UPDATE
                    """,
                    (command.order_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise OrderStateError("STALE_VERSION: order is missing or stale")
                store_id = _uuid(row[0])
                require_store_membership(
                    cursor,
                    staff_user_id=command.principal.staff_user_id,
                    store_id=store_id,
                    error=UnclaimedAuthorizationError,
                )
                if not awaiting_pickup(
                    commercial=CommercialOrderStatus(str(row[1])),
                    production=ProductionStatus(str(row[2])),
                    fulfillment_mode=FulfillmentMode(str(row[3])),
                    self_collection_recorded=bool(row[4]),
                ):
                    raise UnclaimedRefused("NOT_AWAITING_PICKUP")
                egress_record: dict[str, object] | None = None
                if step is not None:
                    # Imported here: `pickup_reminders` builds on this module's list and roles.
                    from nha_trang_laundry_db.pickup_reminders import check_reminder_attempt

                    egress = check_reminder_attempt(
                        cursor,
                        order_id=command.order_id,
                        step=step,
                        channel=command.channel,
                        outcome=command.outcome,
                        at=attempted_at,
                    )
                    egress_record = None if egress is None else egress.record()
                # Under the order lock, so two attempts at once cannot take one ordinal.
                cursor.execute(
                    "SELECT count(*) FROM order_contact_attempts WHERE order_id = %s",
                    (command.order_id,),
                )
                counted = cursor.fetchone()
            ordinal = (int(counted[0]) if counted else 0) + 1
            attempt_id = uuid4()

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO order_contact_attempts (
                        id, order_id, store_id, channel, outcome, note, attempted_by_staff_id,
                        attempted_at, created_at, reminder_step
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        attempt_id,
                        command.order_id,
                        store_id,
                        command.channel.value,
                        command.outcome.value,
                        note,
                        command.principal.staff_user_id,
                        attempted_at,
                        attempted_at,
                        None if step is None else step.value,
                    ),
                )

            reminder_facts: dict[str, object] = (
                {} if step is None else {"reminder_step": step.value}
            )
            audit_reminder: dict[str, object] = dict(reminder_facts)
            if egress_record is not None:
                # Why the guard allowed the message: policy version and basis, never a number.
                audit_reminder["egress"] = egress_record

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="ORDER_CONTACT_ATTEMPT",
                    # The order, versioned by the attempt's place in the list, as payments are.
                    aggregate_id=command.order_id,
                    aggregate_version=ordinal,
                    event_type="ORDER_CONTACT_ATTEMPT_RECORDED",
                    # Channel and outcome only: the note stays in its table, and no number is
                    # anywhere here -- the shop called "the customer", not a value.
                    event_payload={
                        "attempt_id": str(attempt_id),
                        "channel": command.channel.value,
                        "outcome": command.outcome.value,
                        "ordinal": ordinal,
                        **reminder_facts,
                    },
                    audit_action="ORDER_CONTACT_ATTEMPT_RECORD",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    audit_details={
                        "channel": command.channel.value,
                        "outcome": command.outcome.value,
                        "has_note": note is not None,
                        **audit_reminder,
                    },
                    outbox_events=(
                        OutboxEvent(
                            "order.contact_attempt_recorded.v1",
                            {
                                "order_id": str(command.order_id),
                                "attempt_id": str(attempt_id),
                                "outcome": command.outcome.value,
                                **reminder_facts,
                            },
                            f"order:{command.order_id}:contact-attempt:{ordinal}",
                        ),
                    ),
                    occurred_at=attempted_at,
                ),
                mutation,
            )
            return {
                "attempt_id": str(attempt_id),
                "order_id": str(command.order_id),
                "ordinal": ordinal,
                "channel": command.channel.value,
                "outcome": command.outcome.value,
                "attempted_at": attempted_at.isoformat(),
                **reminder_facts,
            }

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"order:{command.order_id}:contact-attempt",
                command.idempotency_key,
                payload,
                attempted_at,
            ),
            record_once,
        )
        stored = result.response
        return StoredContactAttempt(
            attempt_id=UUID(str(stored["attempt_id"])),
            order_id=UUID(str(stored["order_id"])),
            ordinal=int(str(stored["ordinal"])),
            channel=str(stored["channel"]),
            outcome=str(stored["outcome"]),
            attempted_at=datetime.fromisoformat(str(stored["attempted_at"])),
            replayed=result.replayed,
            reminder_step=(
                None if stored.get("reminder_step") is None else str(stored["reminder_step"])
            ),
        )

    # --- the waiver ----------------------------------------------------------------------------

    def waive_storage_fee(self, connection: Any, command: WaiverCommand) -> UnclaimedOrderResult:
        """Miễn phí lưu kho: an approver or the owner, with a reason, while a fee is accruing."""

        _require(command.principal, WAIVER_ROLES, "waiving a storage fee", mfa=True)
        if command.expected_row_version < 1:
            raise OrderStateError("a valid row version is required")
        try:
            reason = clean_note(command.reason, required=True)
        except ValueError as error:
            raise UnclaimedRefused(str(error)) from error
        assert reason is not None
        occurred_at = command.occurred_at or datetime.now(UTC)
        _require_member_of_order(connection, command.order_id, command.principal)
        payload: dict[str, object] = {
            "order_id": str(command.order_id),
            "expected_row_version": command.expected_row_version,
            "reason": reason,
        }

        def waive_once() -> dict[str, object]:
            store_id = _lock_versioned(connection, command.order_id, command)
            with connection.cursor() as cursor:
                storage = storage_fee_for_order(
                    cursor, order_id=command.order_id, moment=occurred_at
                )
            status = storage.fee.status
            if storage.waived:
                raise UnclaimedRefused("STORAGE_FEE_ALREADY_WAIVED")
            if status is StorageFeeStatus.POLICY_UNPUBLISHED:
                raise UnclaimedRefused(STORAGE_POLICY_UNPUBLISHED)
            # MONEY-LIFECYCLE-009 (M1, A3): the waiver takes off the part of the fee the ledger
            # does not cover and keeps the part it does -- it never lowers what is owed below what
            # is paid. Nothing unpaid (no fee, or the ledger already covers it) is nothing to waive.
            effect = waiver_effect(
                storage.fee, quoted_total_vnd=storage.quoted_total_vnd, paid_vnd=storage.paid_vnd
            )
            if effect is None:
                raise UnclaimedRefused("NO_STORAGE_FEE_OWED")
            assert storage.fee.fee is not None and storage.published is not None
            amount = effect.waived_vnd
            policy_version_id = storage.published.version_id
            days = storage.fee.fee.days_waiting
            next_version = command.expected_row_version + 1
            # When the waiver leaves nothing owed it settles the order in this transaction, as the
            # payment that brings the ledger to what is owed would: otherwise the balance reads
            # "partly paid" with nothing left to take, and the goods could never leave.
            settlement = (
                _waiver_settlement(connection, command.order_id, store_id, effect)
                if effect.settles
                else None
            )

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO storage_fee_waivers (
                        id, order_id, store_id, waived_amount_vnd, days_waiting, reason,
                        policy_version_id, waived_by_staff_id, waived_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        uuid4(),
                        command.order_id,
                        store_id,
                        amount,
                        days,
                        reason,
                        policy_version_id,
                        command.principal.staff_user_id,
                        occurred_at,
                    ),
                )
                if settlement is None:
                    _advance_version(cursor, command.order_id, command.expected_row_version)
                    return
                # The waiver row first (`0060`/`0061` refuse a waiver beside a settlement or a
                # fixed fee), then the settlement, the kept fee part fixed beside it (`0066`'s
                # ALREADY_PAID), and the balance last, when `0056` can see every row it checks.
                cursor.execute(
                    """
                    INSERT INTO order_settlements (
                        id, order_id, store_id, settled_quote_id, settled_quote_revision,
                        settled_quote_snapshot_hash, expected_total_vnd, paid_amount_vnd,
                        settlement_shape, collected_by, attested_by_staff_id, attested_at,
                        created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        settlement.settlement_id,
                        command.order_id,
                        store_id,
                        settlement.quote_id,
                        settlement.quote_revision,
                        settlement.snapshot_hash,
                        effect.owed_after_vnd,
                        effect.owed_after_vnd,
                        settlement.shape.value,
                        collected_by_for_shape(settlement.shape),
                        command.principal.staff_user_id,
                        occurred_at,
                        occurred_at,
                    ),
                )
                if effect.kept_vnd > 0:
                    insert_fixed_storage_fee(
                        cursor,
                        order_id=command.order_id,
                        store_id=store_id,
                        settlement_id=settlement.settlement_id,
                        storage=storage,
                        amount_vnd=effect.kept_vnd,
                        fixed_by_staff_id=command.principal.staff_user_id,
                        fixed_at=occurred_at,
                    )
                cursor.execute(
                    """
                    UPDATE orders
                    SET balance_status = 'PAID', row_version = row_version + 1
                    WHERE id = %s AND row_version = %s AND balance_status = 'PARTIALLY_PAID'
                    RETURNING id
                    """,
                    (command.order_id, command.expected_row_version),
                )
                if cursor.fetchone() is None:
                    raise OrderStateError("STALE_VERSION: order changed during the waiver")

            settled_facts: dict[str, object] = (
                {}
                if settlement is None
                else {
                    "kept_vnd": effect.kept_vnd,
                    "settlement_id": str(settlement.settlement_id),
                    "settlement_shape": settlement.shape.value,
                    "balance_status": "PAID",
                }
            )
            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="ORDER",
                    aggregate_id=command.order_id,
                    aggregate_version=next_version,
                    event_type="ORDER_STORAGE_FEE_WAIVED",
                    # The amount and the days; the reason stays in its table.
                    event_payload={
                        "waived_amount_vnd": amount,
                        "days_waiting": days,
                        **settled_facts,
                    },
                    audit_action="ORDER_STORAGE_FEE_WAIVE",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    audit_details={
                        "waived_amount_vnd": amount,
                        "days_waiting": days,
                        **settled_facts,
                    },
                    outbox_events=(
                        OutboxEvent(
                            "order.storage_fee_waived.v1",
                            {
                                "order_id": str(command.order_id),
                                "waived_amount_vnd": amount,
                                "row_version": next_version,
                            },
                            f"order:{command.order_id}:storage-fee-waiver",
                        ),
                        # The key every settlement of this order uses, however it was paid.
                        *(
                            ()
                            if settlement is None
                            else (
                                OutboxEvent(
                                    "order.settlement_recorded.v1",
                                    {
                                        "order_id": str(command.order_id),
                                        "settlement_id": str(settlement.settlement_id),
                                    },
                                    f"order:{command.order_id}:settlement",
                                ),
                            )
                        ),
                    ),
                    occurred_at=occurred_at,
                ),
                mutation,
            )
            return _order_view_document(
                _order_view_row(_read_view_row(connection, command.order_id), as_of=occurred_at)
            )

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"order:{command.order_id}:storage-fee-waiver",
                command.idempotency_key,
                payload,
                occurred_at,
            ),
            waive_once,
        )
        return _result(result.response, result.replayed)

    # --- disposal ------------------------------------------------------------------------------

    def dispose(self, connection: Any, command: DisposalCommand) -> UnclaimedOrderResult:
        """Thanh lý: the owner closes an order whose laundry nobody came back for (`DEC-036`)."""

        _require(command.principal, DISPOSAL_ROLES, "disposing of unclaimed laundry", mfa=True)
        if command.expected_row_version < 1:
            raise OrderStateError("a valid row version is required")
        occurred_at = command.occurred_at or datetime.now(UTC)
        _require_member_of_order(connection, command.order_id, command.principal)
        payload: dict[str, object] = {
            "order_id": str(command.order_id),
            "expected_row_version": command.expected_row_version,
        }

        def dispose_once() -> dict[str, object]:
            store_id = _lock_versioned(connection, command.order_id, command)
            with connection.cursor() as cursor:
                storage = storage_fee_for_order(
                    cursor, order_id=command.order_id, moment=occurred_at
                )
                times = _attempt_times(cursor, command.order_id)
                cursor.execute(
                    """
                    SELECT CASE WHEN r.display_total_min_vnd = r.display_total_max_vnd
                                THEN r.display_total_min_vnd END,
                           (SELECT coalesce(sum(p.amount_vnd), 0) FROM order_payments p
                             WHERE p.order_id = o.id)
                    FROM orders o
                    JOIN quote_revisions r
                      ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
                    WHERE o.id = %s
                    """,
                    (command.order_id,),
                )
                money_row = cursor.fetchone()
            published = storage.published
            verdict = disposal_verdict(
                None if published is None else published.policy,
                awaiting=storage.awaiting,
                ready_at=storage.ready_at,
                as_of=occurred_at,
                attempt_times=times,
            )
            if not verdict.allowed:
                codes = tuple(refusal.value for refusal in verdict.refusals)
                raise UnclaimedRefused(codes[0], reason_codes=codes)
            assert published is not None and verdict.days_waiting is not None
            if money_row is None or money_row[0] is None:
                raise UnclaimedRefused("NO_SINGLE_TOTAL")
            fee_vnd = storage.fee.amount_vnd
            money = disposal_money(
                owed_vnd=int(str(money_row[0])) + fee_vnd, paid_vnd=int(str(money_row[1]))
            )

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO order_disposals (
                        id, order_id, store_id, days_waiting, attempts_counted, attempt_days,
                        owed_vnd, storage_fee_vnd, kept_vnd, written_off_vnd, policy_version_id,
                        disposed_by_staff_id, disposed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        uuid4(),
                        command.order_id,
                        store_id,
                        verdict.days_waiting,
                        verdict.attempts_counted,
                        verdict.attempt_days,
                        money.owed_vnd,
                        fee_vnd,
                        money.kept_vnd,
                        money.written_off_vnd,
                        published.version_id,
                        command.principal.staff_user_id,
                        occurred_at,
                    ),
                )

            figures = {
                "days_waiting": verdict.days_waiting,
                "attempts_counted": verdict.attempts_counted,
                "attempt_days": verdict.attempt_days,
                "owed_vnd": money.owed_vnd,
                "storage_fee_vnd": fee_vnd,
                "kept_vnd": money.kept_vnd,
                "written_off_vnd": money.written_off_vnd,
            }
            # The disposal record first: `0060`'s refund rule admits a paid order closing without
            # a refund only beside it.
            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="ORDER_DISPOSAL",
                    aggregate_id=command.order_id,
                    aggregate_version=1,
                    event_type="ORDER_UNCLAIMED_DISPOSED",
                    event_payload=figures,
                    audit_action="ORDER_UNCLAIMED_DISPOSE",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    audit_details=figures,
                    outbox_events=(
                        OutboxEvent(
                            "order.unclaimed_disposed.v1",
                            {"order_id": str(command.order_id), **figures},
                            f"order:{command.order_id}:disposal",
                        ),
                    ),
                    occurred_at=occurred_at,
                ),
                mutation,
            )
            # Then the order closes as every cancellation of an active order does: through review,
            # one written transition at a time, the second carrying the custody resolution.
            version = command.expected_row_version
            targets = (
                (CommercialOrderStatus.CANCELLATION_REVIEW, None),
                (CommercialOrderStatus.CANCELLED, CustodyResolution.UNCLAIMED_DISPOSED),
            )
            for index, (target, resolution) in enumerate(targets, start=1):
                moment = occurred_at + timedelta(microseconds=index)
                with connection.cursor() as cursor:
                    cursor.execute(_LOCK_ORDER_FOR_TRANSITION_SQL, (command.order_id,))
                    locked = cursor.fetchone()
                if locked is None or int(locked[10]) != version:
                    raise OrderStateError("STALE_VERSION: order changed during the disposal")
                written = self._orders._apply_locked_transition(
                    connection,
                    OrderTransitionCommand(
                        command.order_id,
                        version,
                        command.principal,
                        command.idempotency_key,
                        command.correlation_id,
                        commercial_target=target,
                        occurred_at=moment,
                        custody_resolution=resolution,
                        unclaimed_disposal_verified=resolution is not None,
                    ),
                    locked,
                    moment,
                )
                version = int(str(written["row_version"]))
            return _order_view_document(
                _order_view_row(_read_view_row(connection, command.order_id), as_of=occurred_at)
            )

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"order:{command.order_id}:disposal",
                command.idempotency_key,
                payload,
                occurred_at,
            ),
            dispose_once,
        )
        return _result(result.response, result.replayed)


# --- helpers --------------------------------------------------------------------------------------


def _require(
    principal: StaffPrincipal, roles: frozenset[StaffRole], what: str, *, mfa: bool = False
) -> None:
    if not principal.roles & roles or (mfa and not principal.mfa_verified):
        raise UnclaimedAuthorizationError(f"{what} is not authorized for this caller")


def _require_member_of_order(connection: Any, order_id: UUID, principal: StaffPrincipal) -> None:
    """Membership of the order's store before any idempotency lookup, so a revoked member cannot
    replay a key. A missing order is answered by the write path, not here."""

    with connection.cursor() as cursor:
        cursor.execute("SELECT store_id FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
        if row is None:
            return
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=_uuid(row[0]),
            error=UnclaimedAuthorizationError,
        )


def _lock_versioned(
    connection: Any, order_id: UUID, command: WaiverCommand | DisposalCommand
) -> UUID:
    """Lock the order, check membership on that cursor and `If-Match`; return its store."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT store_id, row_version FROM orders WHERE id = %s FOR UPDATE", (order_id,)
        )
        row = cursor.fetchone()
        if row is not None:
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=_uuid(row[0]),
                error=UnclaimedAuthorizationError,
            )
    if row is None or int(str(row[1])) != command.expected_row_version:
        raise OrderStateError("STALE_VERSION: order is missing or stale")
    return _uuid(row[0])


def _advance_version(cursor: Any, order_id: UUID, expected: int) -> None:
    cursor.execute(
        """
        UPDATE orders SET row_version = row_version + 1
        WHERE id = %s AND row_version = %s
        RETURNING id
        """,
        (order_id, expected),
    )
    if cursor.fetchone() is None:
        raise OrderStateError("STALE_VERSION: order changed during the change")


@dataclass(frozen=True, slots=True)
class _WaiverSettlement:
    """The settlement a waiver writes when it leaves nothing owed (`MONEY-LIFECYCLE-009`)."""

    settlement_id: UUID
    quote_id: UUID
    quote_revision: int
    snapshot_hash: str
    shape: SettlementShape


def _waiver_settlement(
    connection: Any, order_id: UUID, store_id: UUID, effect: WaiverEffect
) -> _WaiverSettlement:
    """The settlement row's facts, read under the order lock the waiver holds.

    The shape is `evaluate_settlement`'s, over the quoted total, exactly as the payment that settles
    an order decides it: the customer is not taking the goods in this press (a waiver is not a
    handover), so a counter order reads "paid, still to collect" and *Khách đã nhận đồ* follows.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT o.current_quote_id, o.current_quote_revision, o.current_quote_snapshot_hash,
                   o.fulfillment_mode, r.display_total_min_vnd, r.display_total_max_vnd,
                   o.store_id
            FROM orders o
            JOIN quote_revisions r
              ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
            WHERE o.id = %s
            """,
            (order_id,),
        )
        row = cursor.fetchone()
    if row is None or _uuid(row[6]) != store_id:
        raise OrderStateError("STALE_VERSION: order is missing or stale")
    quoted = QuotedTotal(
        None if row[4] is None else int(str(row[4])),
        None if row[5] is None else int(str(row[5])),
    )
    settled = evaluate_settlement(
        quoted=quoted,
        tendered_vnd=effect.quoted_total_vnd,
        collected_by_customer=False,
        fulfillment_mode=FulfillmentMode(str(row[3])),
    )
    if isinstance(settled, SettlementNotSupported):
        raise UnclaimedRefused(settled.reason_code)
    return _WaiverSettlement(
        settlement_id=uuid4(),
        quote_id=_uuid(row[0]),
        quote_revision=int(str(row[1])),
        snapshot_hash=str(row[2]),
        shape=settled.shape,
    )


def _attempt_times(cursor: Any, order_id: UUID) -> tuple[datetime, ...]:
    cursor.execute(
        """
        SELECT attempted_at FROM order_contact_attempts WHERE order_id = %s
        ORDER BY attempted_at DESC, id DESC LIMIT %s
        """,
        (order_id, ATTEMPT_TIMES_LIMIT),
    )
    return tuple(row[0] for row in cursor.fetchall())


def _storage_read(
    cursor: Any, *, order_id: UUID, store_id: UUID, row_version: int, as_of: datetime
) -> OrderStorageRead:
    storage = storage_fee_for_order(cursor, order_id=order_id, moment=as_of)
    published = storage.published
    times = _attempt_times(cursor, order_id)
    cursor.execute(
        """
        SELECT a.id, a.channel, a.outcome, a.note, a.attempted_by_staff_id, su.display_name,
               a.attempted_at, count(*) OVER (), a.reminder_step
        FROM order_contact_attempts a
        LEFT JOIN staff_users su ON su.id = a.attempted_by_staff_id
        WHERE a.order_id = %s
        ORDER BY a.attempted_at DESC, a.id DESC
        LIMIT %s
        """,
        (order_id, ATTEMPT_READ_LIMIT),
    )
    attempt_rows = cursor.fetchall()
    cursor.execute(
        """
        SELECT w.waived_amount_vnd, w.days_waiting, w.reason, w.waived_by_staff_id,
               su.display_name, w.waived_at
        FROM storage_fee_waivers w
        LEFT JOIN staff_users su ON su.id = w.waived_by_staff_id
        WHERE w.order_id = %s
        """,
        (order_id,),
    )
    waiver_row = cursor.fetchone()
    cursor.execute(
        """
        SELECT d.days_waiting, d.attempts_counted, d.attempt_days, d.owed_vnd, d.storage_fee_vnd,
               d.kept_vnd, d.written_off_vnd, d.disposed_by_staff_id, su.display_name,
               d.disposed_at
        FROM order_disposals d
        LEFT JOIN staff_users su ON su.id = d.disposed_by_staff_id
        WHERE d.order_id = %s
        """,
        (order_id,),
    )
    disposal_row = cursor.fetchone()
    policy = None if published is None else published.policy
    return OrderStorageRead(
        waiver_effect=(
            None
            if storage.waived
            else waiver_effect(
                storage.fee, quoted_total_vnd=storage.quoted_total_vnd, paid_vnd=storage.paid_vnd
            )
        ),
        order_id=order_id,
        store_id=store_id,
        row_version=row_version,
        evaluated_at=as_of,
        policy=_summary(published),
        awaiting=storage.awaiting,
        ready_at=storage.ready_at,
        days_waiting=None if storage.ready_at is None else days_waiting(storage.ready_at, as_of),
        fee=storage.fee,
        waiver=(
            None
            if waiver_row is None
            else WaiverView(
                waived_amount_vnd=int(str(waiver_row[0])),
                days_waiting=int(str(waiver_row[1])),
                reason=str(waiver_row[2]),
                waived_by_staff_id=_uuid(waiver_row[3]),
                waived_by_name=None if waiver_row[4] is None else str(waiver_row[4]),
                waived_at=waiver_row[5],
            )
        ),
        attempts=tuple(
            ContactAttemptView(
                attempt_id=_uuid(item[0]),
                channel=str(item[1]),
                outcome=str(item[2]),
                note=None if item[3] is None else str(item[3]),
                attempted_by_staff_id=_uuid(item[4]),
                attempted_by_name=None if item[5] is None else str(item[5]),
                attempted_at=item[6],
                reminder_step=None if item[8] is None else str(item[8]),
            )
            for item in reversed(attempt_rows)
        ),
        attempts_total=int(str(attempt_rows[0][7])) if attempt_rows else 0,
        disposal_verdict=disposal_verdict(
            policy,
            awaiting=storage.awaiting,
            ready_at=storage.ready_at,
            as_of=as_of,
            attempt_times=times,
        ),
        disposal=(
            None
            if disposal_row is None
            else DisposalView(
                days_waiting=int(str(disposal_row[0])),
                attempts_counted=int(str(disposal_row[1])),
                attempt_days=int(str(disposal_row[2])),
                owed_vnd=int(str(disposal_row[3])),
                storage_fee_vnd=int(str(disposal_row[4])),
                kept_vnd=int(str(disposal_row[5])),
                written_off_vnd=int(str(disposal_row[6])),
                disposed_by_staff_id=_uuid(disposal_row[7]),
                disposed_by_name=None if disposal_row[8] is None else str(disposal_row[8]),
                disposed_at=disposal_row[9],
            )
        ),
    )


def _result(response: dict[str, object], replayed: bool) -> UnclaimedOrderResult:
    try:
        view = _order_view_from_document(response)
    except (KeyError, TypeError, ValueError) as error:
        raise OrderStateError("stored idempotent result is invalid") from error
    return UnclaimedOrderResult(view=view, replayed=replayed)


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "AWAITING_PICKUP_SQL",
    "CONTACT_ROLES",
    "DISPOSAL_ROLES",
    "LIST_MAX_LIMIT",
    "PHONE_VISIBLE_ROLES",
    "UNCLAIMED_READ_ROLES",
    "WAITING_COUNT_QUERY",
    "WAITING_COUNT_READ_LIMIT",
    "WAITING_SUMMARY_THRESHOLDS",
    "WAIVER_ROLES",
    "AwaitingPickupList",
    "AwaitingPickupRow",
    "ContactAttemptCommand",
    "ContactAttemptView",
    "DisposalCommand",
    "DisposalView",
    "OrderStorageRead",
    "ReminderRefused",
    "StoragePolicySummary",
    "StoredContactAttempt",
    "UnclaimedAuthorizationError",
    "UnclaimedOrderResult",
    "UnclaimedRefused",
    "UnclaimedRepository",
    "WaitingCounts",
    "WaiverCommand",
    "WaiverView",
]
