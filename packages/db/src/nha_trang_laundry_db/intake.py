"""Atomic server-bound intake draft creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import ActorRole

from .transactions import MaterialChange, OutboxEvent, commit_material_change


@dataclass(frozen=True, slots=True)
class CreateOrderRequestCommand:
    store_id: UUID
    contact_binding_id: UUID
    conversation_binding_id: UUID
    actor_id: UUID
    correlation_id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredOrderRequest:
    order_request_id: UUID
    status: str
    row_version: int


class OrderRequestRepository:
    def create(self, connection: Any, command: CreateOrderRequestCommand) -> StoredOrderRequest:
        if command.created_at.tzinfo is None:
            raise ValueError("order request creation time must be timezone-aware")
        request_id = uuid4()

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO order_requests (
                    id, store_id, contact_binding_id, conversation_binding_id,
                    status, row_version, created_at
                ) VALUES (%s, %s, %s, %s, 'DRAFT', 1, %s)
                """,
                (
                    request_id,
                    command.store_id,
                    command.contact_binding_id,
                    command.conversation_binding_id,
                    command.created_at,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="ORDER_REQUEST",
                aggregate_id=request_id,
                aggregate_version=1,
                event_type="ORDER_REQUEST_DRAFT_CREATED",
                event_payload={"status": "DRAFT"},
                audit_action="ORDER_REQUEST_CREATE",
                actor_type=ActorRole.AGENT_RUNNER.value,
                actor_id=command.actor_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "order_request.draft_created.v1",
                        {"order_request_id": str(request_id)},
                        f"order-request:{request_id}:created",
                    ),
                ),
                occurred_at=command.created_at,
            ),
            mutation,
        )
        return StoredOrderRequest(request_id, "DRAFT", 1)


__all__ = ["CreateOrderRequestCommand", "OrderRequestRepository", "StoredOrderRequest"]
