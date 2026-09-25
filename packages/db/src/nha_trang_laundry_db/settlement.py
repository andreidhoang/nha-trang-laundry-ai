"""Record that a customer paid the quoted total and took their goods, atomically.

`SETTLEMENT-001`. The shape of this module is copied from `manual_send_attestations`: a staff
member attests to something they witnessed, the attestation is immutable, and the system compares it
against a hash-pinned artefact rather than re-deriving anything.

The comparison is deterministic and the decision belongs to `packages/domain`. This module reads the
quote revision the order is already bound to, hands the two numbers to `evaluate_settlement`, and
persists only what comes back. It does not price, round, tolerate a difference, or decide what an
unusual amount means.

Two writes happen together or not at all: the settlement row, and the order's balance and collection
flags. Splitting them would allow an order marked paid with no record of who said so, which is the
state this item exists to make impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    FulfillmentMode,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.settlement import (
    QuotedTotal,
    SettlementAccepted,
    SettlementNotSupported,
    SettlementShape,
    evaluate_collection,
    evaluate_settlement,
    handover_refusal,
)

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.query_version import query_version
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Recording money taken at the counter is an operations action, not an approval one.
SETTLEMENT_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR})

#: The business day "hôm nay" means, matching `assistant.BUSINESS_TIMEZONE` and canonical-enums-v1.
#: A counter closes by the local clock, not by UTC, so a settlement at 22:00 local belongs to the
#: day the staff member worked and not to the next one.
BUSINESS_TIMEZONE = "Asia/Ho_Chi_Minh"

#: The takings figure, as one statement so that one edit moves one rule (`OPS-BOARD-001`).
#:
#: v2 (`DEC-024`): money in and money out, each on its own local business day, and the drawer's
#: net movement between them. v1 summed settlements alone and offered nothing else, so a paid order
#: cancelled with the cash handed back left the only figure on the card above the drawer by exactly
#: the refund. A refund is dated by when the money went back, not by when it first came in: a
#: refund today of yesterday's payment moves today's drawer, and yesterday's figures do not move.
#:
#: Every amount is non-negative (invariant 2). The net is a magnitude plus a direction -- the same
#: shape as `order_refunds.direction` -- because a day whose only money event was a refund really
#: did end with less in the drawer, and a minus sign is one rendering slip from a doubled figure.
#: `collected_vnd` keeps its v1 meaning, the gross sum of settlements; only new fields were added.
#:
#: The business day is a parameter rather than `now()` inside the statement, so a figure can be
#: reproduced for a named day and a test can hold the clock still.
_COLLECTED_TODAY_SQL = """
    WITH settled AS (
        SELECT coalesce(sum(paid_amount_vnd), 0) AS amount, count(*) AS entries
        FROM order_settlements
        WHERE store_id = %(store)s
          AND (attested_at AT TIME ZONE %(zone)s)::date = %(business_date)s
    ), refunded AS (
        SELECT coalesce(sum(refunded_amount_vnd), 0) AS amount, count(*) AS entries
        FROM order_refunds
        WHERE store_id = %(store)s
          AND direction = 'TO_CUSTOMER'
          AND (refunded_at AT TIME ZONE %(zone)s)::date = %(business_date)s
    )
    SELECT settled.amount, settled.entries, refunded.amount, refunded.entries,
           abs(settled.amount - refunded.amount),
           CASE WHEN settled.amount >= refunded.amount THEN 'IN' ELSE 'OUT' END
    FROM settled, refunded
"""

#: The published version of the rule above, travelling with the figure under invariant 18.
#:
#: This is the one money figure the console shows, so "which rule produced it" is not a developer
#: convenience: the day boundary is hashed with the SQL because moving it would change the total
#: while leaving the statement word for word identical.
COLLECTED_TODAY_QUERY = query_version("collected-today-v2", _COLLECTED_TODAY_SQL, BUSINESS_TIMEZONE)


#: Which way the counter's drawer moved over a day: money in (including no change) or money out.
DrawerDirection = Literal["IN", "OUT"]


class SettlementAuthorizationError(PermissionError):
    """Raised when a principal may not record a settlement for this store."""


class SettlementStateError(ValueError):
    """Raised when the order cannot be settled in the one supported shape."""

    def __init__(self, message: str, *, reason_code: str, decision: str | None = None) -> None:
        self.reason_code = reason_code
        self.decision = decision
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SettlementCommand:
    order_id: UUID
    paid_amount_vnd: int
    collected_by_customer: bool
    principal: StaffPrincipal
    correlation_id: UUID
    attested_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CollectionCommand:
    """The customer who paid at drop-off has taken their laundry (`DEC-032`).

    No amount, and nothing about the customer: the money is the settlement's, and the only new fact
    is that the goods changed hands, witnessed by the staff member in `principal`.
    `expected_row_version` is the caller's `If-Match` -- the order they read before handing over.
    """

    order_id: UUID
    expected_row_version: int
    principal: StaffPrincipal
    correlation_id: UUID
    collected_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredCollection:
    collection_id: UUID
    order_id: UUID
    settlement_id: UUID
    collected_by_staff_id: UUID
    collected_at: datetime
    self_collection_recorded: bool
    row_version: int


#: Who took the laundry away at the moment the money was attested, per shape. The pairing is also a
#: CHECK in the schema (`0033`, widened by `0048`), so neither side can drift from the other alone.
_COLLECTED_BY: dict[SettlementShape, str] = {
    SettlementShape.EXACT_PAYMENT_SELF_COLLECTION: "CUSTOMER",
    SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY: "PENDING_DELIVERY",
    SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION: "PENDING_COLLECTION",
}


@dataclass(frozen=True, slots=True)
class StoredSettlement:
    settlement_id: UUID
    order_id: UUID
    expected_total_vnd: int
    paid_amount_vnd: int
    settlement_shape: str
    balance_status: str
    self_collection_recorded: bool
    row_version: int


@dataclass(frozen=True, slots=True)
class CollectedToday:
    """What one store's counter took in and handed back, on one local business day.

    Every amount is a non-negative integer of VND (invariant 2), and all of them are computed by the
    database.

    `collected_vnd` is the figure labelled *tiền đã thu*, unchanged in meaning since v1: the sum of
    `paid_amount_vnd`, a BIGINT column the database constrains to equal `expected_total_vnd`, so
    every row in it is a customer who paid the quoted total in full -- and that money came in,
    whatever happened to the order afterwards. It is not "a customer who paid and took their
    goods": a prepaid delivery (`DEC-023`) pays before the goods leave, and an order can be
    cancelled after it was paid.

    `refunded_vnd` sums `order_refunds.refunded_amount_vnd`: the whole settled amount of an order
    cancelled under a `DEC-024` resolution that charges the customer nothing, dated by when the
    money went back.

    `net_vnd` and `net_direction` are what the drawer did that day: the magnitude of collected minus
    refunded, and `IN` when that is zero or more, `OUT` when refunds exceeded takings. A refund-only
    day really did end lighter than it began, and saying so as a direction keeps every amount
    non-negative instead of clamping the truth away or publishing a minus sign.
    """

    collected_vnd: int
    settlement_count: int
    refunded_vnd: int = 0
    refund_count: int = 0
    net_vnd: int = 0
    net_direction: DrawerDirection = "IN"


class SettlementRepository:
    """Attest a settlement and move the order's balance, in one transaction."""

    def record(self, connection: Any, command: SettlementCommand) -> StoredSettlement:
        if not command.principal.roles & SETTLEMENT_ROLES or not command.principal.mfa_verified:
            raise SettlementAuthorizationError("settlement requires an operations role with MFA")
        attested_at = command.attested_at or datetime.now(UTC)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT o.store_id, o.commercial_status, o.production_status, o.balance_status,
                       o.self_collection_recorded, o.row_version,
                       o.current_quote_id, o.current_quote_revision, o.current_quote_snapshot_hash,
                       r.display_total_min_vnd, r.display_total_max_vnd,
                       o.fulfillment_mode
                FROM orders o
                JOIN quote_revisions r
                  ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
                WHERE o.id = %s
                FOR UPDATE OF o
                """,
                (command.order_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise SettlementStateError("order is missing", reason_code="ORDER_NOT_FOUND")
            store_id = _uuid(row[0])
            # Membership on the same cursor while the order row is locked, exactly as
            # `OrderRepository.transition` does since STORE-SCOPING-002. This route is keyed by
            # order_id too, so it would have been missed by a URL-shape enumeration in the same way.
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=store_id,
                error=SettlementAuthorizationError,
            )

        if str(row[1]) != "ACTIVE":
            raise SettlementStateError(
                "only an active order can be settled", reason_code="ORDER_NOT_ACTIVE"
            )
        if str(row[3]) != "UNPAID":
            # Re-settling an order whose balance already moved would be a second payment record for
            # one handover. The UNIQUE constraint on order_id says the same thing; this says it
            # before a transaction is opened.
            raise SettlementStateError(
                "order balance is already settled", reason_code="ALREADY_SETTLED"
            )

        outcome = evaluate_settlement(
            quoted=QuotedTotal(_optional_int(row[9]), _optional_int(row[10])),
            tendered_vnd=command.paid_amount_vnd,
            collected_by_customer=command.collected_by_customer,
            # The order's own field. `DEC-023` made prepaid delivery a supported shape, and which
            # shape this is depends on where the laundry goes -- a fact the order already holds.
            fulfillment_mode=FulfillmentMode(str(row[11])),
        )
        if isinstance(outcome, SettlementNotSupported):
            raise SettlementStateError(
                "settlement shape is not supported",
                reason_code=outcome.reason_code,
                decision=outcome.decision,
            )
        assert isinstance(outcome, SettlementAccepted)
        if outcome.shape is SettlementShape.EXACT_PAYMENT_SELF_COLLECTION:
            # The staging review's finding. "The customer took their goods" was accepted for an
            # order whose laundry was still queued or in the machine -- a handover on record that
            # could not have happened, and the flag that lets the order complete set with it. The
            # money path for a customer paying early is `DEC-032`'s prepayment, not this box.
            refusal = handover_refusal(ProductionStatus(str(row[2])))
            if refusal is not None:
                raise SettlementStateError(
                    "the laundry is not finished, so it cannot have been handed over",
                    reason_code=refusal,
                )

        settlement_id = uuid4()
        next_version = int(row[5]) + 1
        quote_id, quote_revision, snapshot_hash = _uuid(row[6]), int(row[7]), str(row[8])

        collected = outcome.shape is SettlementShape.EXACT_PAYMENT_SELF_COLLECTION

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO order_settlements (
                    id, order_id, store_id, settled_quote_id, settled_quote_revision,
                    settled_quote_snapshot_hash, expected_total_vnd, paid_amount_vnd,
                    settlement_shape, collected_by, attested_by_staff_id, attested_at, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    settlement_id,
                    command.order_id,
                    store_id,
                    quote_id,
                    quote_revision,
                    snapshot_hash,
                    outcome.expected_total_vnd,
                    command.paid_amount_vnd,
                    outcome.shape.value,
                    # Who took the laundry away when the money was attested. For either prepayment
                    # that is nobody yet: a delivery leg attests arrival later, and for a walk-in
                    # paying at drop-off (`DEC-032`) the pickup record does.
                    _COLLECTED_BY[outcome.shape],
                    command.principal.staff_user_id,
                    attested_at,
                    attested_at,
                ),
            )
            # The balance and the collection fact move with the attestation, never apart from it.
            # `order_projection_guard` requires row_version to advance by exactly one.
            #
            # `self_collection_recorded` moves only for the shape that earned it. A prepaid delivery
            # is paid in full and the laundry has not reached anyone yet; setting the flag would
            # complete the order at the counter and the customer would never be recorded as having
            # received anything. A delivery leg attests that, and until one does,
            # `transition_commercial` keeps refusing.
            cursor.execute(
                """
                UPDATE orders
                SET balance_status = 'PAID',
                    self_collection_recorded = %s,
                    row_version = row_version + 1
                WHERE id = %s AND row_version = %s AND balance_status = 'UNPAID'
                RETURNING id
                """,
                (collected, command.order_id, int(row[5])),
            )
            if cursor.fetchone() is None:
                raise SettlementStateError(
                    "order changed while the settlement was being recorded",
                    reason_code="STALE_VERSION",
                )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="ORDER_SETTLEMENT",
                aggregate_id=command.order_id,
                aggregate_version=1,
                event_type="ORDER_SETTLEMENT_RECORDED",
                event_payload={
                    "settlement_id": str(settlement_id),
                    "settlement_shape": outcome.shape.value,
                    "expected_total_vnd": outcome.expected_total_vnd,
                    "quote_snapshot_hash": snapshot_hash,
                },
                audit_action="ORDER_SETTLEMENT_RECORD",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "order.settlement_recorded.v1",
                        {"order_id": str(command.order_id), "settlement_id": str(settlement_id)},
                        f"order:{command.order_id}:settlement",
                    ),
                ),
                occurred_at=attested_at,
            ),
            mutation,
        )
        return StoredSettlement(
            settlement_id=settlement_id,
            order_id=command.order_id,
            expected_total_vnd=outcome.expected_total_vnd,
            paid_amount_vnd=command.paid_amount_vnd,
            settlement_shape=outcome.shape.value,
            balance_status="PAID",
            # `collected`, not True. Hardcoded until 2026-08-29, so a prepaid delivery settlement
            # answered the operator "the customer has their laundry" while the database correctly
            # recorded that nobody had collected anything -- and the reply was frozen into the
            # idempotency ledger, so every replay repeated the same false statement.
            self_collection_recorded=collected,
            row_version=next_version,
        )

    def record_collection(self, connection: Any, command: CollectionCommand) -> StoredCollection:
        """Record that a customer who paid at drop-off has taken their laundry. `DEC-032`.

        The pickup half of a prepaid walk-in order: a row in `order_collections` naming the staff
        member who handed the goods over, and `self_collection_recorded` moving with it -- the flag
        `transition_commercial` reads before it allows COMPLETED. Both, with the domain event, the
        audit row and the outbox row, in one transaction or not at all (invariant 5); `0048` makes
        the database refuse either write without the other.

        The decision is `evaluate_collection`'s, over facts read under the order's row lock. The
        store is the row's, never the caller's, and membership is checked on the same cursor while
        the row is locked, as `record` does.
        """

        if not command.principal.roles & SETTLEMENT_ROLES or not command.principal.mfa_verified:
            raise SettlementAuthorizationError("recording a pickup requires an operations role")
        collected_at = command.collected_at or datetime.now(UTC)
        collection_id = uuid4()
        stored: list[StoredCollection] = []

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                SELECT o.store_id, o.commercial_status, o.production_status, o.balance_status,
                       o.self_collection_recorded, o.row_version,
                       s.id, s.settlement_shape
                FROM orders o
                LEFT JOIN order_settlements s ON s.order_id = o.id
                WHERE o.id = %s
                FOR UPDATE OF o
                """,
                (command.order_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise SettlementStateError("order is missing", reason_code="ORDER_NOT_FOUND")
            store_id = _uuid(row[0])
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=store_id,
                error=SettlementAuthorizationError,
            )
            if int(row[5]) != command.expected_row_version:
                # `If-Match`. The counter hands a bag over against the order it read; if the order
                # moved since, the person pressing is looking at something that is no longer true.
                raise SettlementStateError(
                    "STALE_VERSION: order changed since it was read; read it again",
                    reason_code="STALE_VERSION",
                )
            refusal = evaluate_collection(
                commercial=CommercialOrderStatus(str(row[1])),
                production=ProductionStatus(str(row[2])),
                balance=OrderBalanceStatus(str(row[3])),
                settlement_shape=None if row[7] is None else SettlementShape(str(row[7])),
                self_collection_recorded=bool(row[4]),
            )
            if refusal is not None:
                raise SettlementStateError(
                    "the pickup cannot be recorded for this order now", reason_code=refusal.value
                )
            settlement_id = _uuid(row[6])
            cursor.execute(
                """
                INSERT INTO order_collections (
                    id, order_id, store_id, settlement_id, settlement_shape,
                    collected_by_staff_id, collected_at, correlation_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    collection_id,
                    command.order_id,
                    store_id,
                    settlement_id,
                    SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION.value,
                    command.principal.staff_user_id,
                    collected_at,
                    command.correlation_id,
                    collected_at,
                ),
            )
            # Written after the collection row, which `order_prepaid_collection_consistency` in
            # `0048` requires to exist before the flag may move. `order_projection_guard` requires
            # row_version to advance by exactly one.
            cursor.execute(
                """
                UPDATE orders
                SET self_collection_recorded = TRUE, row_version = row_version + 1
                WHERE id = %s AND row_version = %s AND self_collection_recorded = FALSE
                    AND balance_status = 'PAID'
                RETURNING row_version
                """,
                (command.order_id, int(row[5])),
            )
            moved = cursor.fetchone()
            if moved is None:
                raise SettlementStateError(
                    "STALE_VERSION: order changed while the pickup was being recorded",
                    reason_code="STALE_VERSION",
                )
            stored.append(
                StoredCollection(
                    collection_id=collection_id,
                    order_id=command.order_id,
                    settlement_id=settlement_id,
                    collected_by_staff_id=command.principal.staff_user_id,
                    collected_at=collected_at,
                    self_collection_recorded=True,
                    row_version=int(moved[0]),
                )
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="ORDER_COLLECTION",
                # The order, like the settlement: one pickup per order, and keyed this way it
                # appears on the order's own audit timeline beside the payment it completes.
                aggregate_id=command.order_id,
                aggregate_version=1,
                event_type="ORDER_COLLECTION_RECORDED",
                event_payload={
                    "collection_id": str(collection_id),
                    "order_id": str(command.order_id),
                },
                audit_action="ORDER_COLLECTION_RECORD",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "order.collection_recorded.v1",
                        {"order_id": str(command.order_id), "collection_id": str(collection_id)},
                        f"order:{command.order_id}:collection",
                    ),
                ),
                occurred_at=collected_at,
            ),
            mutation,
        )
        return stored[0]

    @staticmethod
    def for_order(cursor: Any, order_id: UUID) -> StoredSettlement | None:
        cursor.execute(
            """
            SELECT s.id, s.expected_total_vnd, s.paid_amount_vnd, s.settlement_shape,
                   o.balance_status, o.self_collection_recorded, o.row_version
            FROM order_settlements s
            JOIN orders o ON o.id = s.order_id
            WHERE s.order_id = %s
            """,
            (order_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return StoredSettlement(
            settlement_id=_uuid(row[0]),
            order_id=order_id,
            expected_total_vnd=int(row[1]),
            paid_amount_vnd=int(row[2]),
            settlement_shape=str(row[3]),
            balance_status=str(row[4]),
            self_collection_recorded=bool(row[5]),
            row_version=int(row[6]),
        )

    @staticmethod
    def collected_today(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        as_of: datetime | None = None,
    ) -> CollectedToday:
        """Today's settlements, today's refunds, and the drawer's net movement, for one store.

        This is the only money figure the console reads, and its narrowness is the reason it is
        safe to show. It is not revenue: it does not know about work in progress, about an order
        delivered but unpaid, or about anything that happened outside the named local day. It is
        one question — "how much did the counter's drawer change by today" — answered from two
        append-only ledgers, money in and money out.

        The database does the arithmetic. Nothing here adds, subtracts, rounds, converts or
        reconciles, and no model is involved at any point: `SUM` over BIGINT columns is exact in a
        way that a floating-point total in application code would not be.

        `as_of` names the instant whose local business day is meant; it defaults to the server's
        clock and exists so a figure can be reproduced for a named day. It is a parameter of this
        method, not of any request.

        `coalesce` matters: a store with no settlements today must read as 0, not as null, because
        the caller renders this number and "chưa thu đồng nào" is a real answer.
        """
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=SettlementAuthorizationError,
        )
        moment = as_of or datetime.now(UTC)
        business_date = moment.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()
        cursor.execute(
            _COLLECTED_TODAY_SQL,
            {"store": store_id, "zone": BUSINESS_TIMEZONE, "business_date": business_date},
        )
        row = cursor.fetchone()
        if row is None:  # pragma: no cover - an aggregate always returns one row
            return CollectedToday(collected_vnd=0, settlement_count=0)
        direction = str(row[5])
        if direction not in ("IN", "OUT"):  # pragma: no cover - the CASE has exactly two arms
            # Not a counter-facing refusal: nothing staff did can reach this, so it has no reason
            # code and no Vietnamese note. It fails loudly rather than guessing a direction.
            raise RuntimeError("the drawer direction is not one the rule produces")
        return CollectedToday(
            collected_vnd=int(row[0]),
            settlement_count=int(row[1]),
            refunded_vnd=int(row[2]),
            refund_count=int(row[3]),
            net_vnd=int(row[4]),
            net_direction="IN" if direction == "IN" else "OUT",
        )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[call-overload]


__all__ = [
    "BUSINESS_TIMEZONE",
    "COLLECTED_TODAY_QUERY",
    "SETTLEMENT_ROLES",
    "CollectedToday",
    "CollectionCommand",
    "DrawerDirection",
    "SettlementAuthorizationError",
    "SettlementCommand",
    "SettlementRepository",
    "SettlementStateError",
    "StoredCollection",
    "StoredSettlement",
]
