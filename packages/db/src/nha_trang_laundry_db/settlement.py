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
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import FulfillmentMode
from nha_trang_laundry_domain.settlement import (
    QuotedTotal,
    SettlementAccepted,
    SettlementNotSupported,
    SettlementShape,
    evaluate_settlement,
)

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Recording money taken at the counter is an operations action, not an approval one.
SETTLEMENT_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR})

#: The business day "hôm nay" means, matching `assistant.BUSINESS_TIMEZONE` and canonical-enums-v1.
#: A counter closes by the local clock, not by UTC, so a settlement at 22:00 local belongs to the
#: day the staff member worked and not to the next one.
BUSINESS_TIMEZONE = "Asia/Ho_Chi_Minh"


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
    """What one store's counter took in, on today's local business day.

    Deliberately two integers and nothing else. `collected_vnd` is a sum of `paid_amount_vnd`, a
    BIGINT column the database itself constrains to equal `expected_total_vnd`, so it cannot be a
    partial payment, a deposit or a rounded figure — every row it sums is a customer who paid the
    quoted total in full and took their goods.
    """

    collected_vnd: int
    settlement_count: int


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
                    # Who took the laundry away when the money was attested. For a prepaid delivery
                    # that is nobody yet; a delivery leg attests arrival later.
                    "CUSTOMER" if collected else "PENDING_DELIVERY",
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
            self_collection_recorded=True,
            row_version=next_version,
        )

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
        cursor: Any, *, store_id: UUID, principal: StaffPrincipal
    ) -> CollectedToday:
        """Sum today's attested settlements for one store.

        This is the only money figure the console reads, and its narrowness is the reason it is
        safe to show. It is not revenue: it does not know about work in progress, about an order
        delivered but unpaid, about a refund (the schema has no such row), or about anything that
        happened before today's local midnight. It is one question — "how much came across the
        counter today" — answered by summing an append-only ledger.

        The database does the arithmetic. Nothing here adds, rounds, converts or reconciles, and no
        model is involved at any point: `SUM` over a BIGINT column is exact in a way that a
        floating-point total in application code would not be.

        `coalesce` matters: a store with no settlements today must read as 0, not as null, because
        the caller renders this number and "chưa thu đồng nào" is a real answer.
        """
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=SettlementAuthorizationError,
        )
        cursor.execute(
            """
            SELECT coalesce(sum(paid_amount_vnd), 0), count(*)
            FROM order_settlements
            WHERE store_id = %s
              AND (attested_at AT TIME ZONE %s)::date = (now() AT TIME ZONE %s)::date
            """,
            (store_id, BUSINESS_TIMEZONE, BUSINESS_TIMEZONE),
        )
        row = cursor.fetchone()
        if row is None:  # pragma: no cover - an aggregate always returns one row
            return CollectedToday(collected_vnd=0, settlement_count=0)
        return CollectedToday(collected_vnd=int(row[0]), settlement_count=int(row[1]))


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)  # type: ignore[call-overload]


__all__ = [
    "BUSINESS_TIMEZONE",
    "SETTLEMENT_ROLES",
    "CollectedToday",
    "SettlementAuthorizationError",
    "SettlementCommand",
    "SettlementRepository",
    "SettlementStateError",
    "StoredSettlement",
]
