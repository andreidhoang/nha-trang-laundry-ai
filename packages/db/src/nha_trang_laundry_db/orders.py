"""Transactional order commands backed by deterministic orthogonal state machines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.orders import (
    IntakeReadiness,
    OrderState,
    OrderTransitionError,
    transition_commercial,
    transition_intake,
    transition_production,
)

from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change


class OrderStateError(ValueError):
    """Raised for missing, stale, or unsafe persisted order state."""


class OrderAuthorizationError(PermissionError):
    """Raised when a staff principal lacks an order command permission."""


@dataclass(frozen=True)
class CreateOrderCommand:
    store_id: UUID
    bound_contact_id: UUID
    accepted_quote_id: UUID
    accepted_quote_revision: int
    accepted_quote_snapshot_hash: str
    fulfillment_mode: FulfillmentMode
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    customer_final_quote_accepted_at: datetime


@dataclass(frozen=True)
class OrderTransitionCommand:
    order_id: UUID
    expected_row_version: int
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    commercial_target: CommercialOrderStatus | None = None
    intake_target: IntakeStatus | None = None
    production_target: ProductionStatus | None = None
    intake_readiness: IntakeReadiness | None = None
    production_accepted_at: datetime | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class StoredOrder:
    order_id: UUID
    store_id: UUID
    commercial: CommercialOrderStatus
    intake: IntakeStatus
    production: ProductionStatus
    balance: OrderBalanceStatus
    row_version: int
    replayed: bool = False


class OrderRepository:
    """Create and transition orders with authorization, stale checks, audit, and outbox."""

    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()

    def create(self, connection: Any, command: CreateOrderCommand) -> StoredOrder:
        _require_order_mutation(command.principal)
        if (
            command.accepted_quote_revision < 1
            or command.customer_final_quote_accepted_at.tzinfo is None
        ):
            raise OrderStateError("invalid accepted quote command")
        scope = f"store:{command.store_id}:contact:{command.bound_contact_id}:order:create"
        payload: dict[str, object] = {
            "store_id": str(command.store_id),
            "bound_contact_id": str(command.bound_contact_id),
            "accepted_quote_id": str(command.accepted_quote_id),
            "accepted_quote_revision": command.accepted_quote_revision,
            "accepted_quote_snapshot_hash": command.accepted_quote_snapshot_hash,
            "fulfillment_mode": command.fulfillment_mode.value,
            "customer_final_quote_accepted_at": command.customer_final_quote_accepted_at,
        }

        def create_once() -> dict[str, object]:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT q.store_id, r.finality, r.status, r.snapshot_hash, r.approval_id,
                           r.valid_until
                    FROM quotes q
                    JOIN quote_revisions r ON r.quote_id = q.id
                    WHERE q.id = %s AND r.revision = %s
                    """,
                    (command.accepted_quote_id, command.accepted_quote_revision),
                )
                quote = cursor.fetchone()
            if (
                quote is None
                or _uuid(quote[0]) != command.store_id
                or str(quote[1]) != "APPROVED_EXACT"
                or str(quote[2]) != "ACCEPTED_FINAL"
                or str(quote[3]) != command.accepted_quote_snapshot_hash
                or quote[4] is None
                or (
                    quote[5] is not None
                    and command.customer_final_quote_accepted_at >= _datetime(quote[5])
                )
            ):
                raise OrderStateError("accepted exact quote is missing, stale, or expired")

            order_id = uuid4()
            occurred_at = command.customer_final_quote_accepted_at

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO orders (
                        id, store_id, bound_contact_id, current_quote_id,
                        current_quote_revision, current_quote_snapshot_hash,
                        commercial_status, intake_status, production_status,
                        production_resume_status, fulfillment_mode, balance_status,
                        customer_final_quote_accepted_at, row_version, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, 'REQUESTED', 'AWAITING_HANDOFF',
                        'NOT_STARTED', NULL, %s, 'UNPAID', %s, 1, %s
                    )
                    """,
                    (
                        order_id,
                        command.store_id,
                        command.bound_contact_id,
                        command.accepted_quote_id,
                        command.accepted_quote_revision,
                        command.accepted_quote_snapshot_hash,
                        command.fulfillment_mode.value,
                        command.customer_final_quote_accepted_at,
                        occurred_at,
                    ),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="ORDER",
                    aggregate_id=order_id,
                    aggregate_version=1,
                    event_type="ORDER_REQUESTED",
                    event_payload={
                        "quote_id": str(command.accepted_quote_id),
                        "quote_revision": command.accepted_quote_revision,
                        "quote_snapshot_hash": command.accepted_quote_snapshot_hash,
                    },
                    audit_action="ORDER_CREATE_FROM_FINAL_QUOTE",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "order.requested.v1",
                            {"order_id": str(order_id), "store_id": str(command.store_id)},
                            f"order:{order_id}:requested",
                        ),
                    ),
                    occurred_at=occurred_at,
                ),
                mutation,
            )
            return {
                "order_id": str(order_id),
                "store_id": str(command.store_id),
                "commercial": CommercialOrderStatus.REQUESTED.value,
                "intake": IntakeStatus.AWAITING_HANDOFF.value,
                "production": ProductionStatus.NOT_STARTED.value,
                "balance": OrderBalanceStatus.UNPAID.value,
                "row_version": 1,
            }

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                scope,
                command.idempotency_key,
                payload,
                command.customer_final_quote_accepted_at,
            ),
            create_once,
        )
        return _stored_order(result.response, result.replayed)

    def transition(self, connection: Any, command: OrderTransitionCommand) -> StoredOrder:
        _require_order_mutation(command.principal)
        dimension_count = sum(
            target is not None
            for target in (
                command.commercial_target,
                command.intake_target,
                command.production_target,
            )
        )
        if dimension_count != 1 or command.expected_row_version < 1:
            raise OrderStateError("exactly one valid order state target is required")
        occurred_at = command.occurred_at or datetime.now(UTC)
        payload: dict[str, object] = {
            "order_id": str(command.order_id),
            "expected_row_version": command.expected_row_version,
            "commercial_target": (
                command.commercial_target.value if command.commercial_target else None
            ),
            "intake_target": command.intake_target.value if command.intake_target else None,
            "production_target": (
                command.production_target.value if command.production_target else None
            ),
            "intake_readiness": command.intake_readiness,
            "production_accepted_at": command.production_accepted_at,
        }

        def transition_once() -> dict[str, object]:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT store_id, commercial_status, intake_status, production_status,
                           fulfillment_mode, balance_status,
                           required_delivery_legs_succeeded, self_collection_recorded,
                           production_accepted_at, production_resume_status, row_version
                    FROM orders
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (command.order_id,),
                )
                row = cursor.fetchone()
            if row is None or int(row[10]) != command.expected_row_version:
                raise OrderStateError("STALE_VERSION: order is missing or stale")
            current = _order_state(row)
            try:
                if command.commercial_target is not None:
                    next_state = transition_commercial(current, command.commercial_target)
                    dimension = "commercial"
                    target = command.commercial_target.value
                elif command.intake_target is not None:
                    next_state = transition_intake(
                        current,
                        command.intake_target,
                        readiness=command.intake_readiness,
                        production_accepted_at=command.production_accepted_at,
                    )
                    dimension = "intake"
                    target = command.intake_target.value
                elif command.production_target is not None:
                    next_state = transition_production(current, command.production_target)
                    dimension = "production"
                    target = command.production_target.value
                else:
                    raise OrderStateError("order transition target is missing")
            except OrderTransitionError as error:
                raise OrderStateError(str(error)) from error

            next_version = command.expected_row_version + 1
            closed_at = (
                occurred_at if next_state.commercial is CommercialOrderStatus.COMPLETED else None
            )

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    UPDATE orders
                    SET commercial_status = %s, intake_status = %s, production_status = %s,
                        production_resume_status = %s, production_accepted_at = %s,
                        closed_at = COALESCE(closed_at, %s), row_version = row_version + 1
                    WHERE id = %s AND row_version = %s
                    RETURNING id
                    """,
                    (
                        next_state.commercial.value,
                        next_state.intake.value,
                        next_state.production.value,
                        (
                            next_state.production_resume_status.value
                            if next_state.production_resume_status
                            else None
                        ),
                        next_state.production_accepted_at,
                        closed_at,
                        command.order_id,
                        command.expected_row_version,
                    ),
                )
                if cursor.fetchone() is None:
                    raise OrderStateError("STALE_VERSION: order transition lost concurrency race")

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="ORDER",
                    aggregate_id=command.order_id,
                    aggregate_version=next_version,
                    event_type="ORDER_STATE_TRANSITIONED",
                    event_payload={"dimension": dimension, "target": target},
                    audit_action="ORDER_STATE_TRANSITION",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "order.state_transitioned.v1",
                            {
                                "order_id": str(command.order_id),
                                "dimension": dimension,
                                "target": target,
                                "row_version": next_version,
                            },
                            f"order:{command.order_id}:version:{next_version}",
                        ),
                    ),
                    occurred_at=occurred_at,
                ),
                mutation,
            )
            return {
                "order_id": str(command.order_id),
                "store_id": str(row[0]),
                "commercial": next_state.commercial.value,
                "intake": next_state.intake.value,
                "production": next_state.production.value,
                "balance": next_state.balance.value,
                "row_version": next_version,
            }

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"order:{command.order_id}:state-transition",
                command.idempotency_key,
                payload,
                occurred_at,
            ),
            transition_once,
        )
        return _stored_order(result.response, result.replayed)

    @staticmethod
    def list_for_store(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        limit: int = 100,
    ) -> tuple[StoredOrder, ...]:
        _require_order_read(principal)
        if not 1 <= limit <= 200:
            raise ValueError("order board limit must be between 1 and 200")
        cursor.execute(
            """
            SELECT id, store_id, commercial_status, intake_status, production_status,
                   balance_status, row_version
            FROM orders
            WHERE store_id = %s
            ORDER BY created_at DESC, id
            LIMIT %s
            """,
            (store_id, limit),
        )
        return tuple(_stored_order_row(row) for row in cursor.fetchall())


def _order_state(row: tuple[object, ...]) -> OrderState:
    resume = ProductionStatus(str(row[9])) if row[9] is not None else None
    return OrderState(
        commercial=CommercialOrderStatus(str(row[1])),
        intake=IntakeStatus(str(row[2])),
        production=ProductionStatus(str(row[3])),
        fulfillment_mode=FulfillmentMode(str(row[4])),
        balance=OrderBalanceStatus(str(row[5])),
        required_delivery_legs_succeeded=bool(row[6]),
        self_collection_recorded=bool(row[7]),
        production_accepted_at=_optional_datetime(row[8]),
        production_resume_status=resume,
    )


def _require_order_mutation(principal: StaffPrincipal) -> None:
    allowed = {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
    if not principal.roles & allowed:
        raise OrderAuthorizationError("order mutation role is not authorized")


def _require_order_read(principal: StaffPrincipal) -> None:
    allowed = {
        StaffRole.OWNER_ADMIN,
        StaffRole.OPS_APPROVER,
        StaffRole.OPERATOR,
        StaffRole.AUDITOR,
    }
    if not principal.roles & allowed:
        raise OrderAuthorizationError("order board access is not authorized")


def _stored_order(response: dict[str, object], replayed: bool) -> StoredOrder:
    try:
        return StoredOrder(
            UUID(str(response["order_id"])),
            UUID(str(response["store_id"])),
            CommercialOrderStatus(str(response["commercial"])),
            IntakeStatus(str(response["intake"])),
            ProductionStatus(str(response["production"])),
            OrderBalanceStatus(str(response["balance"])),
            int(str(response["row_version"])),
            replayed,
        )
    except (KeyError, ValueError) as error:
        raise OrderStateError("stored idempotent order result is invalid") from error


def _stored_order_row(row: tuple[object, ...]) -> StoredOrder:
    return StoredOrder(
        _uuid(row[0]),
        _uuid(row[1]),
        CommercialOrderStatus(str(row[2])),
        IntakeStatus(str(row[3])),
        ProductionStatus(str(row[4])),
        OrderBalanceStatus(str(row[5])),
        int(str(row[6])),
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _datetime(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OrderStateError("stored timestamp is invalid")
    return value


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)
