"""Atomic server-bound intake draft creation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import ActorRole

from .customers import CustomerRepository
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
    #: `CUSTOMER-001`: the customer record the intake is opened for, when the counter picked one.
    #: The order converted from it inherits the same record. `None` for a walk-in or a binding.
    customer_id: UUID | None = None


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
    #: `READ-ENRICH-001`. The walk-in ticket the request's customer reference names, when it is a
    #: counter ticket of this store (`DEC-013`); null for a channel binding.
    ticket_number: int | None = None
    ticket_issued_on: date | None = None
    #: The order this request became, once its quote was converted; null until then. A request has
    #: one quote and a quote converts into at most one order, so there is at most one.
    order_id: UUID | None = None
    #: `CUSTOMER-001`: the customer record the intake was opened for, and the name they gave --
    #: null when none, or once the record was erased. Read live, never copied into a ledger.
    customer_id: UUID | None = None
    customer_name: str | None = None


#: The summary columns, one statement for the list and the read by id. The ticket is joined through
#: its own store-scoped key, never a bare id, and the order is reached through the request's quote.
_SUMMARY_SELECT = """
    SELECT req.id, req.store_id, req.contact_binding_id, req.status, req.row_version,
           req.created_at, ticket.ticket_number, ticket.issued_on,
           (
               SELECT o.id FROM orders o
               JOIN quotes q ON q.id = o.current_quote_id
               WHERE q.bound_order_request_id = req.id AND o.store_id = req.store_id
               ORDER BY o.created_at, o.id
               LIMIT 1
           ) AS order_id,
           req.customer_id, customer.display_name
    FROM order_requests req
    LEFT JOIN counter_tickets ticket
      ON ticket.id = req.contact_binding_id AND ticket.store_id = req.store_id
    LEFT JOIN customers customer
      ON customer.id = req.customer_id AND customer.store_id = req.store_id
"""


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
            if command.customer_id is not None:
                # Same transaction: the customer's activity moves with the intake, or neither
                # happens. An erased record or another store's is refused as not found, before
                # anything is written.
                CustomerRepository.touch_for_intake(
                    cursor,
                    store_id=command.store_id,
                    customer_id=command.customer_id,
                    at=command.created_at,
                )
            # The customer column is named only for an intake that has one, so an intake for a
            # ticket or a binding is the same statement it was before 0055.
            customer = () if command.customer_id is None else (command.customer_id,)
            cursor.execute(
                f"""
                INSERT INTO order_requests (
                    id, store_id, contact_binding_id, conversation_binding_id,
                    status, row_version, created_at{", customer_id" if customer else ""}
                ) VALUES (%s, %s, %s, %s, 'DRAFT', 1, %s{", %s" if customer else ""})
                """,
                (
                    request_id,
                    command.store_id,
                    command.contact_binding_id,
                    command.conversation_binding_id,
                    command.created_at,
                    *customer,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="ORDER_REQUEST",
                aggregate_id=request_id,
                aggregate_version=1,
                event_type="ORDER_REQUEST_DRAFT_CREATED",
                # The customer's id when there is one -- an opaque key, never a name or a number.
                event_payload=(
                    {"status": "DRAFT"}
                    if command.customer_id is None
                    else {"status": "DRAFT", "customer_id": str(command.customer_id)}
                ),
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
            _SUMMARY_SELECT
            + """
            WHERE req.store_id = %s
            ORDER BY req.created_at DESC, req.id DESC
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
            _SUMMARY_SELECT + " WHERE req.id = %s AND req.store_id = %s",
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
        mentioned = len(command.fact_types)
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
                # conversation it arrived in; this ledger is not a copy of it. It used to call the
                # count "accepted", which no fact's content was (AGENT-SHADOW-DEFECTS-001 F10).
                event_payload={
                    "mentioned_fact_count": mentioned,
                    "fact_types": list(fact_types),
                    "fact_text_recorded": False,
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
                            "mentioned_fact_count": mentioned,
                            "fact_text_recorded": False,
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
        ticket_number=None if row[6] is None else int(row[6]),
        ticket_issued_on=row[7] if isinstance(row[7], date) else None,
        order_id=None
        if row[8] is None
        else (row[8] if isinstance(row[8], UUID) else UUID(str(row[8]))),
        customer_id=None
        if row[9] is None
        else (row[9] if isinstance(row[9], UUID) else UUID(str(row[9]))),
        customer_name=None if row[10] is None else str(row[10]),
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
