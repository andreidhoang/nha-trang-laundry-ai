"""Pickup reminders: the due list, the fixed text behind the egress guard, and the attempt check.

`PICKUP-REMIND-001`, `DEC-043`. Every decision is `nha_trang_laundry_domain.pickup_reminders`'s,
over facts read here:

* **The due list** (*Nhắc khách lấy đồ*): the store's self-collect orders waiting for pickup (the
  population of `UNCLAIMED-001`'s list, `AWAITING_PICKUP_SQL`) whose newest due reminder nobody has
  done yet, oldest ready first, bounded. Each row: the step, the days waiting, the customer's name
  when there is a record, how the shop can reach them (`PHONE` / `CHAT` / `NONE`), the balance, and
  -- for the roles that call customers only, as `UNCLAIMED-001`'s list returns its number -- the
  national number for *Gọi* and the `zalo.me` link, both derived here from the sealed column at
  read time. Unreachable orders are rows too, and counted (`unreachable_count`).
* **The text** (`pickup-reminder-v1`) for (order, step), only after the egress guard
  (`consent_egress.check_egress_allowed`, TRANSACTIONAL) allows it, inside the transaction that
  reads the facts. Refusals: `NO_CONTACT`, then the guard's codes -- `SUPPRESSED`,
  `PENDING_REVIEW`, `SUPPRESSION_UNKNOWN`, `MESSAGING_POLICY_UNPUBLISHED`, `NO_SERVICE_BASIS`.
* **The attempt check** `UNCLAIMED-001`'s contact-attempt write calls when an attempt names a
  reminder step: the step must be the newest one due, and `MESSAGE_SENT` must pass exactly what the
  text route would -- the guard re-run under its advisory lock in the attempt's own transaction.

**Which contact and channel the guard judges.** A reminder is sent from the shop's own Zalo or SMS,
which is not a channel the system models. So the guard runs, for the order's own contact reference
and every reference linked to the same customer, on the channels this deployment's manual sends may
name (`MANUAL_SEND_CHANNELS`) and on every channel a TRANSACTIONAL suppression row names for those
references. A STOP on any of them refuses (`pickup_reminders.combine_egress`); otherwise one allowed
judgement is enough. Unknown blocks: more references than `CONTACT_REFS_LIMIT` is refused as
`SUPPRESSION_UNKNOWN`, never judged on a sample.

No phone number is in any event, audit or outbox payload, error, or the text itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final
from uuid import UUID

from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    FulfillmentMode,
    ProductionStatus,
)
from nha_trang_laundry_domain.customers import national_from_e164
from nha_trang_laundry_domain.payments import owed_charges, payment_position
from nha_trang_laundry_domain.pickup_reminders import (
    PICKUP_REMINDER_TEMPLATE,
    EgressVerdict,
    Reachability,
    ReminderFacts,
    ReminderFee,
    ReminderRefusal,
    ReminderStep,
    combine_egress,
    current_reminder,
    fee_starts_on,
    reachability,
    reminder_steps,
    reminder_text,
    zalo_url,
)
from nha_trang_laundry_domain.settlement import QuotedTotal
from nha_trang_laundry_domain.unclaimed import (
    ContactChannel,
    ContactOutcome,
    StoragePolicy,
    awaiting_pickup,
    days_waiting,
    order_storage_fee,
    shop_date,
)

from nha_trang_laundry_db.consent_egress import (
    TRANSACTIONAL,
    EgressCheck,
    check_egress_allowed,
    evaluate_transactional_egress,
)
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.manual_sends import MANUAL_SEND_CHANNELS
from nha_trang_laundry_db.orders import OrderNotVisibleError
from nha_trang_laundry_db.personal_data import open_phone
from nha_trang_laundry_db.promise_policy import read_published_turnaround_policy
from nha_trang_laundry_db.service_messaging import read_published_messaging_policy
from nha_trang_laundry_db.storage_fees import read_published_storage_policy
from nha_trang_laundry_db.store_access import is_store_member, require_store_membership
from nha_trang_laundry_db.unclaimed import (
    AWAITING_PICKUP_SQL,
    CONTACT_ROLES,
    PHONE_VISIBLE_ROLES,
    UNCLAIMED_READ_ROLES,
    ReminderRefused,
    UnclaimedAuthorizationError,
)

#: Who reads the due list: `UNCLAIMED-001`'s readers (the auditor reads it without the links).
REMINDER_READ_ROLES: Final = UNCLAIMED_READ_ROLES
#: Who reads a reminder's text and records one: the people at the counter who send it.
REMINDER_SEND_ROLES: Final = CONTACT_ROLES

LIST_DEFAULT_LIMIT: Final = 100
LIST_MAX_LIMIT: Final = 200
#: How many waiting orders the due list reads to find the due ones. A shop's shelf is far smaller;
#: past it the list says `truncated`, and the count is a floor.
SCAN_LIMIT: Final = 5000
#: How many contact references of one customer the guard judges. More is refused, not sampled.
CONTACT_REFS_LIMIT: Final = 20

#: The channels an attempt with `MESSAGE_SENT` may name: a message goes by Zalo or SMS.
MESSAGE_CHANNELS: Final = frozenset({ContactChannel.ZALO, ContactChannel.SMS})


# --- read models ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReminderRow:
    order_id: UUID
    row_version: int
    balance: str
    ticket_number: int | None
    ticket_issued_on: date | None
    customer_id: UUID | None
    customer_name: str | None
    step: ReminderStep
    ready_at: datetime
    days_waiting: int
    reachable: Reachability
    #: The national number and the Zalo link, for the roles that call customers; None otherwise.
    phone: str | None
    zalo_url: str | None
    phone_last4: str | None
    remaining_vnd: int | None
    #: What the text route would answer now, as advice (no lock): None when it would give the text.
    message_refusal: str | None


@dataclass(frozen=True, slots=True)
class ReminderList:
    store_id: UUID
    evaluated_at: datetime
    storage_policy_published: bool
    messaging_policy_published: bool
    limit: int
    #: Every order with a reminder due now (a floor when `truncated`).
    total_count: int
    #: Of those, the ones with no phone and no chat channel.
    unreachable_count: int
    truncated: bool
    phone_visible: bool
    orders: tuple[ReminderRow, ...]


@dataclass(frozen=True, slots=True)
class ReminderMessage:
    order_id: UUID
    step: ReminderStep
    template: str
    text: str
    evaluated_at: datetime
    #: Why the guard allowed it: the published messaging policy version and the basis.
    policy_version: int | None
    basis: str | None


@dataclass(frozen=True, slots=True)
class ReminderEgress:
    """The guard's answer over every (contact, channel) judged, and each judgement's record."""

    refusal: str | None
    checks: tuple[EgressCheck, ...]

    def record(self) -> dict[str, object]:
        """What the audit keeps about why a reminder message was allowed. JSON-safe, no phone."""

        allowed = next((check for check in self.checks if check.send_allowed), None)
        return {
            "purpose": TRANSACTIONAL,
            "refusal": self.refusal,
            "judged": len(self.checks),
            "basis": None if allowed is None else allowed.basis,
            "policy_version": None if allowed is None else allowed.policy_version,
            "policy_snapshot_hash": None if allowed is None else allowed.policy_snapshot_hash,
        }


# --- the facts of one order -----------------------------------------------------------------------

#: One waiting order's facts, for the text route and the attempt check. `%s` is the order id.
_ORDER_FACTS_SQL: Final = """
    SELECT o.store_id, o.commercial_status, o.production_status, o.fulfillment_mode,
           o.self_collection_recorded, o.production_ready_at, o.created_at, o.bound_contact_id,
           o.customer_id, t.ticket_number, t.issued_on,
           (cu.phone_ciphertext IS NOT NULL AND cu.erased_at IS NULL),
           EXISTS (SELECT 1 FROM contact_channel_bindings b
                   WHERE b.contact_binding_id = o.bound_contact_id),
           CASE WHEN r.display_total_min_vnd = r.display_total_max_vnd
                THEN r.display_total_min_vnd END,
           EXISTS (SELECT 1 FROM order_settlements s WHERE s.order_id = o.id),
           (SELECT f.amount_vnd FROM order_storage_fees f WHERE f.order_id = o.id),
           EXISTS (SELECT 1 FROM storage_fee_waivers w WHERE w.order_id = o.id),
           (SELECT coalesce(sum(p.amount_vnd), 0) FROM order_payments p WHERE p.order_id = o.id),
           st.name
    FROM orders o
    JOIN quote_revisions r
      ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
    JOIN stores st ON st.id = o.store_id
    LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
    LEFT JOIN customers cu ON cu.id = o.customer_id AND cu.store_id = o.store_id
    WHERE o.id = %s
"""


@dataclass(frozen=True, slots=True)
class _OrderFacts:
    order_id: UUID
    store_id: UUID
    awaiting: bool
    ready_at: datetime | None
    created_at: datetime
    bound_contact_id: UUID
    ticket_number: int | None
    ticket_issued_on: date | None
    reachable: Reachability
    quoted_total: int | None
    settled: bool
    fixed_vnd: int | None
    waived: bool
    paid_vnd: int
    shop_name: str | None


def _order_facts(cursor: Any, order_id: UUID) -> _OrderFacts | None:
    cursor.execute(_ORDER_FACTS_SQL, (order_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    return _OrderFacts(
        order_id=order_id,
        store_id=_uuid(row[0]),
        awaiting=awaiting_pickup(
            commercial=CommercialOrderStatus(str(row[1])),
            production=ProductionStatus(str(row[2])),
            fulfillment_mode=FulfillmentMode(str(row[3])),
            self_collection_recorded=bool(row[4]),
        ),
        ready_at=row[5] if isinstance(row[5], datetime) else None,
        created_at=row[6],
        bound_contact_id=_uuid(row[7]),
        ticket_number=None if row[9] is None else int(str(row[9])),
        ticket_issued_on=row[10] if isinstance(row[10], date) else None,
        reachable=reachability(has_phone=bool(row[11]), has_chat=bool(row[12])),
        quoted_total=None if row[13] is None else int(str(row[13])),
        settled=bool(row[14]),
        fixed_vnd=None if row[15] is None else int(str(row[15])),
        waived=bool(row[16]),
        paid_vnd=int(str(row[17])),
        shop_name=None if row[18] is None else str(row[18]),
    )


def _remaining(
    policy: StoragePolicy | None,
    *,
    awaiting: bool,
    ready_at: datetime | None,
    as_of: datetime,
    quoted_total: int | None,
    settled: bool,
    fixed_vnd: int | None,
    waived: bool,
    paid_vnd: int,
) -> int | None:
    """What the order still owes now, exactly as `UNCLAIMED-001`'s list computes it."""

    fee = order_storage_fee(
        policy,
        awaiting=awaiting,
        ready_at=ready_at,
        as_of=as_of,
        quoted_total_vnd=quoted_total,
        waived=waived,
        settled=settled,
        fixed_vnd=fixed_vnd,
    )
    return payment_position(
        owed_charges(QuotedTotal(quoted_total, quoted_total), storage_fee_vnd=fee.amount_vnd),
        paid_vnd,
    ).remaining_vnd


def _done_steps(cursor: Any, order_id: UUID, ready_at: datetime) -> tuple[ReminderStep, ...]:
    """The steps an attempt named since the laundry was (last) ready. A rewash starts over."""

    cursor.execute(
        """
        SELECT DISTINCT reminder_step FROM order_contact_attempts
        WHERE order_id = %s AND reminder_step IS NOT NULL AND attempted_at >= %s
        """,
        (order_id, ready_at),
    )
    return tuple(ReminderStep(str(row[0])) for row in cursor.fetchall())


# --- the guard ------------------------------------------------------------------------------------


def reminder_egress(cursor: Any, *, order_id: UUID, at: datetime, lock: bool) -> ReminderEgress:
    """The TRANSACTIONAL egress answer for a reminder about `order_id` at `at`.

    `lock=True` is the guard (`check_egress_allowed`, under the advisory lock the STOP writer takes)
    and belongs inside the transaction that answers or writes; `lock=False` is the same evaluation
    as advice, for the list. Pairs are judged in a fixed order, so two guards never lock in turn.
    """

    cursor.execute(
        """
        SELECT ref FROM (
            SELECT o.bound_contact_id AS ref, 0 AS rank FROM orders o WHERE o.id = %(order)s
            UNION ALL
            SELECT l.ref_id, 1 FROM customer_links l
            JOIN orders o ON o.customer_id = l.customer_id AND o.store_id = l.store_id
            WHERE o.id = %(order)s
            UNION ALL
            SELECT other.bound_contact_id, 2 FROM orders other
            JOIN orders o ON o.customer_id = other.customer_id AND o.store_id = other.store_id
            WHERE o.id = %(order)s
        ) refs
        GROUP BY ref
        ORDER BY min(rank), ref
        LIMIT %(limit)s
        """,
        {"order": order_id, "limit": CONTACT_REFS_LIMIT + 1},
    )
    refs = [_uuid(row[0]) for row in cursor.fetchall()]
    if not refs or len(refs) > CONTACT_REFS_LIMIT:
        return ReminderEgress("SUPPRESSION_UNKNOWN", ())
    cursor.execute(
        """
        SELECT DISTINCT contact_binding_id, channel FROM suppression_entries
        WHERE purpose = 'TRANSACTIONAL' AND contact_binding_id = ANY(%s)
        """,
        (refs,),
    )
    pairs = {(_uuid(row[0]), str(row[1])) for row in cursor.fetchall()}
    pairs |= {(ref, channel) for ref in refs for channel in MANUAL_SEND_CHANNELS}
    checks: list[EgressCheck] = []
    for ref, channel in sorted(pairs, key=lambda pair: (str(pair[0]), pair[1])):
        if lock:
            check = check_egress_allowed(
                cursor, contact_binding_id=ref, channel=channel, purpose=TRANSACTIONAL, at=at
            )
        else:
            check = evaluate_transactional_egress(
                cursor, contact_binding_id=ref, channel=channel, at=at
            )
        checks.append(check)
    refusal = combine_egress(
        [EgressVerdict(check.send_allowed, check.reason_code) for check in checks]
    )
    return ReminderEgress(refusal, tuple(checks))


def newest_due_step(
    facts: _OrderFacts, *, policy: StoragePolicy | None, as_of: datetime
) -> ReminderStep | None:
    """The newest step whose day has come, done or not; None when none is due."""

    if not facts.awaiting or facts.ready_at is None:
        return None
    due = reminder_steps(shop_date(facts.ready_at), shop_date(as_of), policy)
    return due[-1] if due else None


def _require_step(
    facts: _OrderFacts, step: ReminderStep, *, policy: StoragePolicy | None, as_of: datetime
) -> None:
    newest = newest_due_step(facts, policy=policy, as_of=as_of)
    if newest is None:
        raise ReminderRefused(ReminderRefusal.NO_REMINDER_DUE.value)
    if newest is not step:
        raise ReminderRefused(ReminderRefusal.REMINDER_STEP_NOT_DUE.value)


def _require_message_allowed(cursor: Any, facts: _OrderFacts, *, at: datetime) -> ReminderEgress:
    if facts.reachable is Reachability.NONE:
        raise ReminderRefused(ReminderRefusal.NO_CONTACT.value)
    egress = reminder_egress(cursor, order_id=facts.order_id, at=at, lock=True)
    if egress.refusal is not None:
        raise ReminderRefused(egress.refusal)
    return egress


def check_reminder_attempt(
    cursor: Any,
    *,
    order_id: UUID,
    step: ReminderStep,
    channel: ContactChannel,
    outcome: ContactOutcome,
    at: datetime,
) -> ReminderEgress | None:
    """`UNCLAIMED-001`'s attempt write, for an attempt that names `step`, under the order's lock.

    The step must be the newest due. `MESSAGE_SENT` passes exactly what the text route would: a
    contact to send to, and the guard, in this transaction. Returns the guard's answer for the audit
    when one was needed.
    """

    facts = _order_facts(cursor, order_id)
    if facts is None:
        raise ReminderRefused(ReminderRefusal.NO_REMINDER_DUE.value)
    published = read_published_storage_policy(cursor)
    _require_step(facts, step, policy=None if published is None else published.policy, as_of=at)
    if outcome is not ContactOutcome.MESSAGE_SENT:
        return None
    if channel not in MESSAGE_CHANNELS:
        raise ReminderRefused(ReminderRefusal.MESSAGE_SENT_CHANNEL_INVALID.value)
    return _require_message_allowed(cursor, facts, at=at)


# --- the repository -------------------------------------------------------------------------------


class PickupReminderRepository:
    @staticmethod
    def list_due(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        as_of: datetime,
        limit: int = LIST_DEFAULT_LIMIT,
    ) -> ReminderList:
        """Nhắc khách lấy đồ: the orders whose newest due reminder nobody has done, oldest first."""

        _require(principal, REMINDER_READ_ROLES, "reading the pickup reminders")
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=UnclaimedAuthorizationError,
        )
        if not 1 <= limit <= LIST_MAX_LIMIT:
            raise ValueError(f"the reminder list limit is between 1 and {LIST_MAX_LIMIT}")
        published = read_published_storage_policy(cursor)
        policy = None if published is None else published.policy
        messaging = read_published_messaging_policy(cursor)
        cursor.execute(
            f"""
            SELECT o.id, o.production_ready_at,
                   coalesce(
                       (SELECT array_agg(DISTINCT a.reminder_step) FROM order_contact_attempts a
                         WHERE a.order_id = o.id AND a.reminder_step IS NOT NULL
                           AND a.attempted_at >= o.production_ready_at),
                       '{{}}'::text[]),
                   (cu.phone_ciphertext IS NOT NULL AND cu.erased_at IS NULL),
                   EXISTS (SELECT 1 FROM contact_channel_bindings b
                           WHERE b.contact_binding_id = o.bound_contact_id)
            FROM orders o
            LEFT JOIN customers cu ON cu.id = o.customer_id AND cu.store_id = o.store_id
            WHERE o.store_id = %s AND {AWAITING_PICKUP_SQL} AND o.production_ready_at IS NOT NULL
            ORDER BY o.production_ready_at ASC, o.id
            LIMIT %s
            """,
            (store_id, SCAN_LIMIT + 1),
        )
        scanned = cursor.fetchall()
        scan_truncated = len(scanned) > SCAN_LIMIT
        today = shop_date(as_of)
        due: list[tuple[UUID, ReminderStep, Reachability]] = []
        for row in scanned[:SCAN_LIMIT]:
            step = current_reminder(shop_date(row[1]), today, policy, _steps(row[2] or []))
            if step is not None:
                reach = reachability(has_phone=bool(row[3]), has_chat=bool(row[4]))
                due.append((_uuid(row[0]), step, reach))
        chosen = due[:limit]
        details = _details(cursor, [order_id for order_id, _, _ in chosen])
        visible = bool(principal.roles & PHONE_VISIBLE_ROLES)
        rows: list[ReminderRow] = []
        for order_id, step, reach in chosen:
            detail = details[order_id]
            erased = detail["erased"]
            sealed = detail["sealed"]
            national = (
                national_from_e164(
                    open_phone(sealed, customer_id=detail["customer_id"], store_id=store_id)
                )
                if visible and sealed is not None and not erased
                else None
            )
            ready_at: datetime = detail["ready_at"]
            rows.append(
                ReminderRow(
                    order_id=order_id,
                    row_version=detail["row_version"],
                    balance=detail["balance"],
                    ticket_number=detail["ticket_number"],
                    ticket_issued_on=detail["ticket_issued_on"],
                    customer_id=detail["customer_id"],
                    customer_name=None if erased else detail["customer_name"],
                    step=step,
                    ready_at=ready_at,
                    days_waiting=days_waiting(ready_at, as_of),
                    reachable=reach,
                    phone=national,
                    zalo_url=zalo_url(national),
                    phone_last4=None if erased else detail["phone_last4"],
                    remaining_vnd=_remaining(
                        policy,
                        awaiting=True,
                        ready_at=ready_at,
                        as_of=as_of,
                        quoted_total=detail["quoted_total"],
                        settled=detail["settled"],
                        fixed_vnd=detail["fixed_vnd"],
                        waived=detail["waived"],
                        paid_vnd=detail["paid_vnd"],
                    ),
                    message_refusal=(
                        ReminderRefusal.NO_CONTACT.value
                        if reach is Reachability.NONE
                        else reminder_egress(
                            cursor, order_id=order_id, at=as_of, lock=False
                        ).refusal
                    ),
                )
            )
        return ReminderList(
            store_id=store_id,
            evaluated_at=as_of,
            storage_policy_published=published is not None,
            messaging_policy_published=messaging is not None,
            limit=limit,
            total_count=len(due),
            unreachable_count=sum(1 for _, _, reach in due if reach is Reachability.NONE),
            truncated=len(due) > limit or scan_truncated,
            phone_visible=visible,
            orders=tuple(rows),
        )

    @staticmethod
    def read_message(
        connection: Any,
        *,
        order_id: UUID,
        step: ReminderStep,
        principal: StaffPrincipal,
        as_of: datetime,
    ) -> ReminderMessage:
        """The `pickup-reminder-v1` text for (order, step), after the egress guard allows it.

        404-shaped for a stranger's order, as the order read is. Writes nothing: the guard's lock is
        held for the read's own transaction, and the attempt that records the send runs it again.
        """

        _require(principal, REMINDER_SEND_ROLES, "reading a pickup reminder", mfa=True)
        with connection.transaction(), connection.cursor() as cursor:
            facts = _order_facts(cursor, order_id)
            if facts is None or not is_store_member(
                cursor, staff_user_id=principal.staff_user_id, store_id=facts.store_id
            ):
                raise OrderNotVisibleError()
            published = read_published_storage_policy(cursor)
            policy = None if published is None else published.policy
            _require_step(facts, step, policy=policy, as_of=as_of)
            egress = _require_message_allowed(cursor, facts, at=as_of)
            assert facts.ready_at is not None
            turnaround = read_published_turnaround_policy(cursor)
            ready_on = shop_date(facts.ready_at)
            text = reminder_text(
                step,
                ReminderFacts(
                    shop_name=facts.shop_name,
                    ticket_number=facts.ticket_number,
                    ticket_issued_on=facts.ticket_issued_on,
                    received_on=shop_date(facts.created_at),
                    ready_on=ready_on,
                    days_waiting=days_waiting(facts.ready_at, as_of),
                    remaining_vnd=_remaining(
                        policy,
                        awaiting=facts.awaiting,
                        ready_at=facts.ready_at,
                        as_of=as_of,
                        quoted_total=facts.quoted_total,
                        settled=facts.settled,
                        fixed_vnd=facts.fixed_vnd,
                        waived=facts.waived,
                        paid_vnd=facts.paid_vnd,
                    ),
                    opening_hours=(
                        None
                        if turnaround is None
                        else (turnaround.policy.opens_at, turnaround.policy.closes_at)
                    ),
                    fee=(
                        ReminderFee(
                            fee_per_started_day_vnd=policy.fee_per_started_day_vnd,
                            fee_cap_percent=policy.fee_cap_percent,
                            starts_on=fee_starts_on(ready_on, policy),
                        )
                        if step is ReminderStep.BEFORE_FEE and policy is not None
                        else None
                    ),
                ),
            )
        allowed = next((check for check in egress.checks if check.send_allowed), None)
        return ReminderMessage(
            order_id=order_id,
            step=step,
            template=PICKUP_REMINDER_TEMPLATE,
            text=text,
            evaluated_at=as_of,
            policy_version=None if allowed is None else allowed.policy_version,
            basis=None if allowed is None else allowed.basis,
        )


# --- helpers --------------------------------------------------------------------------------------


def _details(cursor: Any, order_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    if not order_ids:
        return {}
    cursor.execute(
        """
        SELECT o.id, o.row_version, o.balance_status, t.ticket_number, t.issued_on,
               o.customer_id, cu.display_name, cu.phone_ciphertext, cu.phone_last4, cu.erased_at,
               o.production_ready_at,
               CASE WHEN r.display_total_min_vnd = r.display_total_max_vnd
                    THEN r.display_total_min_vnd END,
               EXISTS (SELECT 1 FROM order_settlements s WHERE s.order_id = o.id),
               (SELECT f.amount_vnd FROM order_storage_fees f WHERE f.order_id = o.id),
               EXISTS (SELECT 1 FROM storage_fee_waivers w WHERE w.order_id = o.id),
               (SELECT coalesce(sum(p.amount_vnd), 0) FROM order_payments p
                 WHERE p.order_id = o.id)
        FROM orders o
        JOIN quote_revisions r
          ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
        LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
        LEFT JOIN customers cu ON cu.id = o.customer_id AND cu.store_id = o.store_id
        WHERE o.id = ANY(%s)
        """,
        (order_ids,),
    )
    found: dict[UUID, dict[str, Any]] = {}
    for row in cursor.fetchall():
        found[_uuid(row[0])] = {
            "row_version": int(str(row[1])),
            "balance": str(row[2]),
            "ticket_number": None if row[3] is None else int(str(row[3])),
            "ticket_issued_on": row[4] if isinstance(row[4], date) else None,
            "customer_id": None if row[5] is None else _uuid(row[5]),
            "customer_name": None if row[6] is None else str(row[6]),
            "sealed": row[7],
            "phone_last4": None if row[8] is None else str(row[8]),
            "erased": row[9] is not None,
            "ready_at": row[10],
            "quoted_total": None if row[11] is None else int(str(row[11])),
            "settled": bool(row[12]),
            "fixed_vnd": None if row[13] is None else int(str(row[13])),
            "waived": bool(row[14]),
            "paid_vnd": int(str(row[15])),
        }
    return found


def _steps(values: Iterable[object]) -> tuple[ReminderStep, ...]:
    return tuple(ReminderStep(str(value)) for value in values)


def _require(
    principal: StaffPrincipal, roles: frozenset[Any], what: str, *, mfa: bool = False
) -> None:
    if not principal.roles & roles or (mfa and not principal.mfa_verified):
        raise UnclaimedAuthorizationError(f"{what} is not authorized for this caller")


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "CONTACT_REFS_LIMIT",
    "LIST_DEFAULT_LIMIT",
    "LIST_MAX_LIMIT",
    "MESSAGE_CHANNELS",
    "REMINDER_READ_ROLES",
    "REMINDER_SEND_ROLES",
    "SCAN_LIMIT",
    "PickupReminderRepository",
    "ReminderEgress",
    "ReminderList",
    "ReminderMessage",
    "ReminderRefused",
    "ReminderRow",
    "check_reminder_attempt",
    "newest_due_step",
    "reminder_egress",
]
