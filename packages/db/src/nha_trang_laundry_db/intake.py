"""Atomic server-bound intake draft creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import ActorRole

from .identity import StaffPrincipal
from .store_access import StoreAccessError, require_store_membership
from .transactions import MaterialChange, OutboxEvent, commit_material_change


@dataclass(frozen=True, slots=True)
class CreateOrderRequestCommand:
    store_id: UUID
    contact_binding_id: UUID
    conversation_binding_id: UUID
    actor_id: UUID
    correlation_id: UUID
    created_at: datetime
    # The agent tool path audits as AGENT_RUNNER; the staff command path audits as STAFF, and
    # neither may impersonate the other. Same split as QuoteRevisionCommand.actor_type.
    actor_type: str = ActorRole.AGENT_RUNNER.value


@dataclass(frozen=True, slots=True)
class RecordCustomerFactsCommand:
    """A guarded fact-record write on the order-request aggregate.

    `fact_types` carries the allowlisted fact type names and nothing else. The free text a
    customer stated (`service_text`, `address_text`) is deliberately absent: the aggregate has
    no column for it and the event payload must not become one.
    """

    order_request_id: UUID
    store_id: UUID
    contact_binding_id: UUID
    conversation_binding_id: UUID
    expected_row_version: int
    fact_types: tuple[str, ...]
    actor_id: UUID
    correlation_id: UUID
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class StoredOrderRequest:
    order_request_id: UUID
    status: str
    row_version: int


@dataclass(frozen=True, slots=True)
class OrderRequestSummary:
    """A staff-facing read of one intake draft: display fields, never a free-text fact.

    The aggregate carries no customer text by design, so there is nothing here to redact. The
    contact binding id is an opaque server-owned identifier — the provider reference behind it
    lives in `contact_channel_bindings` and is deliberately not joined into this read.
    """

    order_request_id: UUID
    store_id: UUID
    contact_binding_id: UUID
    status: str
    row_version: int
    created_at: datetime


class OrderRequestBindingError(ValueError):
    """Raised when the stored request does not match the caller's claimed bindings."""


class OrderRequestStateError(ValueError):
    """Raised when the stored request version is older than the caller's If-Match."""


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
                actor_type=command.actor_type,
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

    @staticmethod
    def get_bound(
        cursor: Any,
        *,
        order_request_id: UUID,
        store_id: UUID,
        contact_binding_id: UUID,
        conversation_binding_id: UUID,
    ) -> StoredOrderRequest | None:
        """Read a request only through the full claimed binding; anything less is an oracle."""
        cursor.execute(
            """
            SELECT id, status, row_version FROM order_requests
            WHERE id = %s AND store_id = %s AND contact_binding_id = %s
                AND conversation_binding_id = %s
            """,
            (order_request_id, store_id, contact_binding_id, conversation_binding_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        identifier = row[0] if isinstance(row[0], UUID) else UUID(str(row[0]))
        return StoredOrderRequest(identifier, str(row[1]), int(row[2]))

    @staticmethod
    def list_for_store(
        cursor: Any, *, store_id: UUID, principal: StaffPrincipal, limit: int = 100
    ) -> tuple[OrderRequestSummary, ...]:
        """Newest-first intake drafts of one store, membership enforced at the repository.

        A route is a place a membership check can be forgotten; the repository is the only path
        to the rows, so the check lives here, exactly as `QuoteRepository.list_for_store` does it.
        """
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        if not 1 <= limit <= 100:
            raise ValueError("order request list limit must be between 1 and 100")
        cursor.execute(
            """
            SELECT id, store_id, contact_binding_id, status, row_version, created_at
            FROM order_requests
            WHERE store_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s
            """,
            (store_id, limit),
        )
        return tuple(_summary(row) for row in cursor.fetchall())

    @staticmethod
    def get_for_store(
        cursor: Any, *, order_request_id: UUID, store_id: UUID, principal: StaffPrincipal
    ) -> OrderRequestSummary | None:
        """One intake draft, or None — an invisible id and an unknown id are the same answer."""
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        cursor.execute(
            """
            SELECT id, store_id, contact_binding_id, status, row_version, created_at
            FROM order_requests
            WHERE id = %s AND store_id = %s
            """,
            (order_request_id, store_id),
        )
        row = cursor.fetchone()
        return None if row is None else _summary(row)

    def record_customer_facts(
        self, connection: Any, command: RecordCustomerFactsCommand
    ) -> StoredOrderRequest:
        """Bump the aggregate version under If-Match and record fact types, never fact text."""
        if command.occurred_at.tzinfo is None:
            raise ValueError("fact recording time must be timezone-aware")
        if command.expected_row_version < 1 or not command.fact_types:
            raise ValueError("fact recording requires a version and at least one fact type")
        accepted = len(command.fact_types)
        fact_types = tuple(sorted(set(command.fact_types)))

        recorded: dict[str, Any] = {}

        def mutation(cursor: Any) -> None:
            bound = self.get_bound(
                cursor,
                order_request_id=command.order_request_id,
                store_id=command.store_id,
                contact_binding_id=command.contact_binding_id,
                conversation_binding_id=command.conversation_binding_id,
            )
            if bound is None:
                raise OrderRequestBindingError("bound order request is unavailable")
            if bound.row_version != command.expected_row_version:
                raise OrderRequestStateError("bound order request version is stale")
            cursor.execute(
                """
                UPDATE order_requests
                SET row_version = row_version + 1
                WHERE id = %s AND row_version = %s
                RETURNING row_version
                """,
                (command.order_request_id, command.expected_row_version),
            )
            row = cursor.fetchone()
            if row is None:
                raise OrderRequestStateError("bound order request changed during recording")
            recorded["status"] = bound.status
            recorded["row_version"] = int(row[0])

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="ORDER_REQUEST",
                aggregate_id=command.order_request_id,
                aggregate_version=command.expected_row_version + 1,
                event_type="ORDER_REQUEST_CUSTOMER_FACTS_RECORDED",
                # Types and counts only. The text behind a fact stays in the channel
                # conversation it arrived in; this ledger is not a copy of it.
                event_payload={
                    "accepted_fact_count": accepted,
                    "fact_types": list(fact_types),
                },
                audit_action="ORDER_REQUEST_RECORD_CUSTOMER_FACTS",
                actor_type=ActorRole.AGENT_RUNNER.value,
                actor_id=command.actor_id,
                correlation_id=command.correlation_id,
                # Not in `INTERNAL_EVENT_TYPES`: like `order_request.draft_created.v1`, this
                # record is durable evidence for downstream projection, not an internal
                # dispatch the outbox worker may claim.
                outbox_events=(
                    OutboxEvent(
                        "order_request.customer_facts_recorded.v1",
                        {
                            "order_request_id": str(command.order_request_id),
                            "row_version": command.expected_row_version + 1,
                            "accepted_fact_count": accepted,
                        },
                        f"order-request:{command.order_request_id}:facts:"
                        f"{command.expected_row_version + 1}",
                    ),
                ),
                occurred_at=command.occurred_at,
            ),
            mutation,
        )
        return StoredOrderRequest(
            command.order_request_id,
            str(recorded["status"]),
            int(recorded["row_version"]),
        )


def _summary(row: Any) -> OrderRequestSummary:
    created_at = row[5]
    if not isinstance(created_at, datetime) or created_at.tzinfo is None:
        raise ValueError("stored order request timestamp is invalid")
    return OrderRequestSummary(
        row[0] if isinstance(row[0], UUID) else UUID(str(row[0])),
        row[1] if isinstance(row[1], UUID) else UUID(str(row[1])),
        row[2] if isinstance(row[2], UUID) else UUID(str(row[2])),
        str(row[3]),
        int(row[4]),
        created_at,
    )


__all__ = [
    "CreateOrderRequestCommand",
    "OrderRequestBindingError",
    "OrderRequestRepository",
    "OrderRequestStateError",
    "OrderRequestSummary",
    "RecordCustomerFactsCommand",
    "StoredOrderRequest",
]
