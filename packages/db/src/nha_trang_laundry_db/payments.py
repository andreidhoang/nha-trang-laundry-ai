"""Record a deposit, a part payment or the rest, with its method, atomically. `PAYMENT-001`.

`DEC-035` (2026-09-25). The decision is `payments.evaluate_payment`'s, over facts read under the
order's row lock: the bound quote revision's presentable total (the one charge owed today), the sum
of the order's payment ledger (computed by PostgreSQL), the order's state, and what the staff
member says they saw. This module persists only what comes back. It does not price, round, tolerate
a difference, or decide what an unusual amount means.

Up to four writes happen together or not at all, inside `commit_material_change` with the domain
event, the audit row and the outbox rows:

1. the settlement row, when this payment settles the order in full -- the same row, with the same
   shape rules, the exact-total settlement route writes, so pickup (`0048`), the refund binding
   (`0046`), remedies and exports read a paid order exactly as before;
2. the payment row (none for a 0 đồng settlement of a bill a credit covered in full: no money
   moved);
3. the order's balance (`PARTIALLY_PAID` or `PAID`), its collection flag when the customer takes the
   goods with the final payment, and its row version.

`0056` checks at commit that the balance, the settlement and the ledger agree; a write that left
them apart could not commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    FulfillmentMode,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.payments import (
    PaymentAccepted,
    PaymentMethod,
    PaymentRefused,
    evaluate_payment,
    normalise_bank_ref,
    owed_charges,
)
from nha_trang_laundry_domain.settlement import (
    QuotedTotal,
    SettlementNotSupported,
    SettlementShape,
    evaluate_settlement,
    handover_refusal,
)

from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.settlement import (
    SETTLEMENT_ROLES,
    SettlementAuthorizationError,
    collected_by_for_shape,
)
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: How many payments the order read carries. Each is at least 1 đồng, so in principle an order could
#: hold very many; the read is bounded and says when it stopped (`payments_truncated`).
PAYMENT_READ_LIMIT = 50


class PaymentStateError(ValueError):
    """The payment is not recorded; `reason_code` says why and what to do (`PaymentRefusal` codes,
    `SettlementRefusal` codes for the settling payment's shape, or a state such as `STALE_VERSION`).
    """

    def __init__(self, message: str, *, reason_code: str, decision: str | None = None) -> None:
        self.reason_code = reason_code
        self.decision = decision
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PaymentCommand:
    """One amount taken at the counter. `expected_row_version` is the caller's `If-Match`.

    `bank_ref_last` is the text as typed; it is normalised here, and judged by the domain.
    `transfer_seen` is the staff member's word that the transfer is in the shop's account.
    `collected_by_customer` is their word that the customer takes the goods now.
    """

    order_id: UUID
    expected_row_version: int
    amount_vnd: int
    method: PaymentMethod
    transfer_seen: bool
    bank_ref_last: str | None
    collected_by_customer: bool
    principal: StaffPrincipal
    correlation_id: UUID
    recorded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredPayment:
    """What the payment did. `payment_id` is `None` only for a 0 đồng settlement."""

    payment_id: UUID | None
    order_id: UUID
    amount_vnd: int
    method: str
    bank_ref_last: str | None
    recorded_at: datetime
    balance_status: str
    owed_vnd: int
    paid_vnd: int
    remaining_vnd: int
    settlement_id: UUID | None
    settlement_shape: str | None
    self_collection_recorded: bool
    row_version: int


@dataclass(frozen=True, slots=True)
class PaymentView:
    """One row of the order's payment ledger, as the order read carries it."""

    payment_id: UUID
    amount_vnd: int
    method: str
    bank_ref_last: str | None
    #: Recorded by a path that did not ask for a method (see `0056`); counted as cash.
    legacy: bool
    recorded_at: datetime
    recorded_by_staff_id: UUID
    recorded_by_name: str | None


#: The order read's payment columns: the ledger's sum and its first `PAYMENT_READ_LIMIT + 1` rows,
#: oldest first (one more than shown, so the reader can say it stopped). Scalar subqueries, like the
#: delivery legs beside them, so the board's plan over `orders` is unchanged; each is served by
#: `order_payments_order_idx`. The sum is PostgreSQL's, over every row, never the shown rows'.
PAYMENT_VIEW_COLUMNS = f"""
    , (
        SELECT coalesce(sum(p.amount_vnd), 0) FROM order_payments p WHERE p.order_id = o.id
    ) AS paid_vnd
    , (
        SELECT coalesce(
            jsonb_agg(
                jsonb_build_object(
                    'payment_id', recent.id,
                    'amount_vnd', recent.amount_vnd,
                    'method', recent.method,
                    'bank_ref_last', recent.bank_ref_last,
                    'legacy', recent.legacy,
                    'recorded_at', recent.recorded_at,
                    'recorded_by_staff_id', recent.recorded_by_staff_id,
                    'recorded_by_name', recent.display_name
                )
                ORDER BY recent.recorded_at, recent.id
            ),
            '[]'::jsonb
        )
        FROM (
            SELECT p.id, p.amount_vnd, p.method, p.bank_ref_last, p.legacy, p.recorded_at,
                   p.recorded_by_staff_id, su.display_name
            FROM order_payments p
            LEFT JOIN staff_users su ON su.id = p.recorded_by_staff_id
            WHERE p.order_id = o.id
            ORDER BY p.recorded_at, p.id
            LIMIT {PAYMENT_READ_LIMIT + 1}
        ) AS recent
    ) AS payments
"""


def payment_views(value: object) -> tuple[tuple[PaymentView, ...], bool]:
    """The `payments` column as views, and whether the ledger held more than the read shows."""

    if not isinstance(value, list):
        raise ValueError("stored payments are invalid")
    views: list[PaymentView] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("stored payment is invalid")
        recorded_at = datetime.fromisoformat(str(item["recorded_at"]))
        if recorded_at.tzinfo is None:
            raise ValueError("stored payment timestamp is invalid")
        name = item.get("recorded_by_name")
        reference = item.get("bank_ref_last")
        views.append(
            PaymentView(
                payment_id=UUID(str(item["payment_id"])),
                amount_vnd=int(str(item["amount_vnd"])),
                method=str(item["method"]),
                bank_ref_last=None if reference is None else str(reference),
                legacy=item["legacy"] is True,
                recorded_at=recorded_at,
                recorded_by_staff_id=UUID(str(item["recorded_by_staff_id"])),
                recorded_by_name=None if name is None else str(name),
            )
        )
    truncated = len(views) > PAYMENT_READ_LIMIT
    return tuple(views[:PAYMENT_READ_LIMIT]), truncated


class PaymentRepository:
    """Take a payment and move the order's balance, in one transaction."""

    def record(self, connection: Any, command: PaymentCommand) -> StoredPayment:
        if not command.principal.roles & SETTLEMENT_ROLES or not command.principal.mfa_verified:
            raise SettlementAuthorizationError("taking a payment requires an operations role")
        recorded_at = command.recorded_at or datetime.now(UTC)
        bank_ref = normalise_bank_ref(command.bank_ref_last)

        # Read and decide under the order's row lock, on the caller's transaction, exactly as
        # `SettlementRepository.record` does; the writes below commit inside the same one.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT o.store_id, o.commercial_status, o.production_status, o.balance_status,
                       o.self_collection_recorded, o.row_version,
                       o.current_quote_id, o.current_quote_revision, o.current_quote_snapshot_hash,
                       r.display_total_min_vnd, r.display_total_max_vnd, o.fulfillment_mode
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
                raise PaymentStateError("order is missing", reason_code="ORDER_NOT_FOUND")
            store_id = _uuid(row[0])
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=store_id,
                error=SettlementAuthorizationError,
            )
            if int(row[5]) != command.expected_row_version:
                # `If-Match`. Money is taken against the order the counter read; if it moved --
                # another payment, a cancellation -- the figure on the screen is no longer true.
                raise PaymentStateError(
                    "STALE_VERSION: order changed since it was read; read it again",
                    reason_code="STALE_VERSION",
                )
            # The ledger's sum, by PostgreSQL, under the order lock this transaction holds -- so no
            # second payment can land between this read and the write below.
            cursor.execute(
                "SELECT coalesce(sum(amount_vnd), 0), count(*) FROM order_payments "
                "WHERE order_id = %s",
                (command.order_id,),
            )
            ledger = cursor.fetchone()
        paid_so_far, entries = int(ledger[0]), int(ledger[1])
        quoted = QuotedTotal(_optional_int(row[9]), _optional_int(row[10]))
        outcome = evaluate_payment(
            commercial=CommercialOrderStatus(str(row[1])),
            balance=OrderBalanceStatus(str(row[3])),
            charges=owed_charges(quoted),
            paid_vnd=paid_so_far,
            amount_vnd=command.amount_vnd,
            method=command.method,
            transfer_seen=command.transfer_seen,
            bank_ref_last=bank_ref,
            collected_by_customer=command.collected_by_customer,
        )
        if isinstance(outcome, PaymentRefused):
            raise PaymentStateError("the payment is not recorded", reason_code=outcome.reason_code)
        assert isinstance(outcome, PaymentAccepted)

        shape: SettlementShape | None = None
        if outcome.completes:
            # The settling payment writes the settlement row, its shape decided exactly as the
            # exact-total route decides it. With one charge, what is owed *is* the quoted total,
            # which is what `evaluate_settlement` and the row's CHECK compare.
            settled = evaluate_settlement(
                quoted=quoted,
                tendered_vnd=outcome.owed_vnd,
                collected_by_customer=command.collected_by_customer,
                fulfillment_mode=FulfillmentMode(str(row[11])),
            )
            if isinstance(settled, SettlementNotSupported):
                raise PaymentStateError(
                    "the settling payment is not a supported shape",
                    reason_code=settled.reason_code,
                    decision=settled.decision,
                )
            shape = settled.shape
            if shape is SettlementShape.EXACT_PAYMENT_SELF_COLLECTION:
                refusal = handover_refusal(ProductionStatus(str(row[2])))
                if refusal is not None:
                    raise PaymentStateError(
                        "the laundry is not finished, so it cannot be handed over",
                        reason_code=refusal,
                    )

        settlement_id = uuid4() if shape is not None else None
        payment_id = uuid4() if outcome.amount_vnd > 0 else None
        ordinal = entries + 1
        collected = shape is SettlementShape.EXACT_PAYMENT_SELF_COLLECTION
        moved: list[tuple[int, bool]] = []

        def mutation(cursor: Any) -> None:
            if shape is not None:
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
                        settlement_id,
                        command.order_id,
                        store_id,
                        _uuid(row[6]),
                        int(row[7]),
                        str(row[8]),
                        outcome.owed_vnd,
                        outcome.paid_after_vnd,
                        shape.value,
                        collected_by_for_shape(shape),
                        command.principal.staff_user_id,
                        recorded_at,
                        recorded_at,
                    ),
                )
            if payment_id is not None:
                cursor.execute(
                    """
                    INSERT INTO order_payments (
                        id, order_id, store_id, amount_vnd, method, bank_ref_last, legacy,
                        settlement_id, recorded_by_staff_id, recorded_at, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s, %s)
                    """,
                    (
                        payment_id,
                        command.order_id,
                        store_id,
                        outcome.amount_vnd,
                        command.method.value,
                        bank_ref,
                        settlement_id,
                        command.principal.staff_user_id,
                        recorded_at,
                        recorded_at,
                    ),
                )
            # `order_projection_guard` requires row_version to advance by exactly one. The balance
            # moves with the ledger, never apart from it (`0056` checks both at commit).
            cursor.execute(
                """
                UPDATE orders
                SET balance_status = %s,
                    self_collection_recorded = self_collection_recorded OR %s,
                    row_version = row_version + 1
                WHERE id = %s AND row_version = %s AND balance_status = %s
                RETURNING row_version, self_collection_recorded
                """,
                (
                    outcome.balance_after.value,
                    collected,
                    command.order_id,
                    command.expected_row_version,
                    str(row[3]),
                ),
            )
            result = cursor.fetchone()
            if result is None:
                raise PaymentStateError(
                    "STALE_VERSION: order changed while the payment was being recorded",
                    reason_code="STALE_VERSION",
                )
            moved.append((int(result[0]), bool(result[1])))

        outbox = [
            OutboxEvent(
                "order.payment_recorded.v1",
                {
                    "order_id": str(command.order_id),
                    "payment_id": None if payment_id is None else str(payment_id),
                },
                f"order:{command.order_id}:payment:{ordinal}",
            )
        ]
        if settlement_id is not None:
            # The key the exact-total route uses: one settlement per order, however it was paid.
            outbox.append(
                OutboxEvent(
                    "order.settlement_recorded.v1",
                    {"order_id": str(command.order_id), "settlement_id": str(settlement_id)},
                    f"order:{command.order_id}:settlement",
                )
            )
        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="ORDER_PAYMENT",
                # The order, as for its settlement and pickup, so the payment appears on the
                # order's own audit timeline; versioned by the payment's place in the ledger.
                aggregate_id=command.order_id,
                aggregate_version=ordinal,
                event_type="ORDER_PAYMENT_RECORDED",
                # No bank reference and nothing about the customer in any payload: the amount,
                # the method and what the balance became are what a downstream reader needs.
                event_payload={
                    "payment_id": None if payment_id is None else str(payment_id),
                    "amount_vnd": outcome.amount_vnd,
                    "method": command.method.value,
                    "balance_status": outcome.balance_after.value,
                    "paid_vnd": outcome.paid_after_vnd,
                    "owed_vnd": outcome.owed_vnd,
                    "collected_by_customer": collected,
                    "settlement_id": None if settlement_id is None else str(settlement_id),
                    "settlement_shape": None if shape is None else shape.value,
                },
                audit_action="ORDER_PAYMENT_RECORD",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                outbox_events=tuple(outbox),
                occurred_at=recorded_at,
            ),
            mutation,
        )
        version, collected_now = moved[0]
        return StoredPayment(
            payment_id=payment_id,
            order_id=command.order_id,
            amount_vnd=outcome.amount_vnd,
            method=command.method.value,
            bank_ref_last=bank_ref,
            recorded_at=recorded_at,
            balance_status=outcome.balance_after.value,
            owed_vnd=outcome.owed_vnd,
            paid_vnd=outcome.paid_after_vnd,
            remaining_vnd=outcome.remaining_after_vnd,
            settlement_id=settlement_id,
            settlement_shape=None if shape is None else shape.value,
            self_collection_recorded=collected_now,
            row_version=version,
        )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


__all__ = [
    "PAYMENT_READ_LIMIT",
    "PAYMENT_VIEW_COLUMNS",
    "PaymentCommand",
    "PaymentRepository",
    "PaymentStateError",
    "PaymentView",
    "StoredPayment",
    "payment_views",
]
