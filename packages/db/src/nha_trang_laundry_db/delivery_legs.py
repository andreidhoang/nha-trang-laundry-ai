"""Record that the shop's courier took laundry out, and whether it reached the customer.

`DEC-023`, resolved 2026-08-26. A delivery order could be taken, priced, agreed and washed and then
never closed, because `orders.required_delivery_legs_succeeded` had no writer anywhere. This is that
writer.

It moves no money. The owner decided the customer pays in full at the counter before the laundry
leaves, so a leg attests one fact -- arrival, or the absence of it -- and a driver never carries
cash. That decision is why this module is as small as it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import FulfillmentMode

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Counter work, like settlement and quote acceptance. The same set records all three.
DELIVERY_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR})


class DeliveryLegKind(StrEnum):
    #: The shop collecting laundry from the customer. Completes nothing.
    PICKUP = "PICKUP"
    #: The customer receiving their laundry. This is the leg an order completes on.
    RETURN = "RETURN"


class DeliveryLegOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


#: Which fulfilment modes expect a return leg at all. `PICKUP_ONLY` does not: the shop collects the
#: laundry and the customer comes to the counter for it, so self-collection completes that order
#: exactly as it completes a walk-in.
MODES_EXPECTING_RETURN = frozenset({FulfillmentMode.PICKUP_AND_RETURN, FulfillmentMode.RETURN_ONLY})


class DeliveryLegError(ValueError):
    """A leg could not be recorded."""


@dataclass(frozen=True)
class RecordDeliveryLegCommand:
    order_id: UUID
    leg_kind: DeliveryLegKind
    outcome: DeliveryLegOutcome
    principal: StaffPrincipal
    correlation_id: UUID
    recorded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredDeliveryLeg:
    leg_id: UUID
    order_id: UUID
    leg_kind: str
    outcome: str
    completes_fulfillment: bool


class DeliveryLegRepository:
    """Write legs, and flip the order's fulfilment flag when the customer has their laundry."""

    def record(self, connection: Any, command: RecordDeliveryLegCommand) -> StoredDeliveryLeg:
        """Record one attempt.

        A failed attempt is a row and nothing else: nothing is charged, the order stays ACTIVE and
        the laundry comes back to the shop. A retry is another row, which is why only successes are
        unique per leg kind.
        """

        if not command.principal.roles & DELIVERY_ROLES or not command.principal.mfa_verified:
            raise StoreAccessError("recording a delivery requires an operations role with MFA")
        moment = command.recorded_at or datetime.now(UTC)
        leg_id = uuid4()
        completes: list[bool] = []

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                SELECT store_id, commercial_status, fulfillment_mode,
                       required_delivery_legs_succeeded, row_version
                FROM orders WHERE id = %s FOR UPDATE
                """,
                (command.order_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise DeliveryLegError("order is missing")
            store_id = row[0] if isinstance(row[0], UUID) else UUID(str(row[0]))
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=store_id,
                error=StoreAccessError,
            )
            if str(row[1]) != "ACTIVE":
                raise DeliveryLegError("only an active order can have a delivery recorded")
            mode = FulfillmentMode(str(row[2]))
            if mode is FulfillmentMode.SELF_DROP_SELF_COLLECT:
                raise DeliveryLegError(
                    "this order has no delivery: the customer brings and collects it themselves"
                )
            if command.leg_kind is DeliveryLegKind.RETURN and mode not in MODES_EXPECTING_RETURN:
                raise DeliveryLegError("this order's fulfilment mode has no return leg")
            cursor.execute(
                """
                INSERT INTO delivery_legs (
                    id, store_id, order_id, leg_kind, outcome, recorded_by, recorded_at,
                    correlation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    leg_id,
                    store_id,
                    command.order_id,
                    command.leg_kind.value,
                    command.outcome.value,
                    command.principal.staff_user_id,
                    moment,
                    command.correlation_id,
                ),
            )
            # The flag moves only on a succeeded return, and only once. `order_projection_guard`
            # requires row_version to advance by exactly one, so a leg that changes nothing must not
            # touch the order row at all.
            finishes = (
                command.leg_kind is DeliveryLegKind.RETURN
                and command.outcome is DeliveryLegOutcome.SUCCEEDED
                and not bool(row[3])
            )
            completes.append(finishes)
            if finishes:
                cursor.execute(
                    """
                    UPDATE orders
                    SET required_delivery_legs_succeeded = TRUE, row_version = row_version + 1
                    WHERE id = %s AND row_version = %s
                        AND required_delivery_legs_succeeded = FALSE
                    RETURNING id
                    """,
                    (command.order_id, int(row[4])),
                )
                if cursor.fetchone() is None:
                    raise DeliveryLegError("order changed while the delivery was being recorded")

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="DELIVERY_LEG",
                # The leg, not the order. An order can accumulate several attempts and
                # `domain_events` is unique on (type, id, version, event) -- keying on the order
                # would let the first failed delivery block every retry from being recorded.
                aggregate_id=leg_id,
                aggregate_version=1,
                event_type="DELIVERY_LEG_RECORDED",
                event_payload={
                    "order_id": str(command.order_id),
                    "leg_id": str(leg_id),
                    "leg_kind": command.leg_kind.value,
                    "outcome": command.outcome.value,
                },
                audit_action="DELIVERY_LEG_RECORD",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "order.delivery_leg_recorded.v1",
                        {"order_id": str(command.order_id), "leg_id": str(leg_id)},
                        f"order:{command.order_id}:delivery:{leg_id}",
                    ),
                ),
                occurred_at=moment,
            ),
            mutation,
        )
        return StoredDeliveryLeg(
            leg_id=leg_id,
            order_id=command.order_id,
            leg_kind=command.leg_kind.value,
            outcome=command.outcome.value,
            completes_fulfillment=completes[0],
        )


__all__ = [
    "DELIVERY_ROLES",
    "DeliveryLegError",
    "DeliveryLegKind",
    "DeliveryLegOutcome",
    "DeliveryLegRepository",
    "RecordDeliveryLegCommand",
    "StoredDeliveryLeg",
]
