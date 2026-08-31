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
from nha_trang_laundry_db.store_access import require_store_membership
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
        with connection.cursor() as membership_cursor:
            require_store_membership(
                membership_cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=command.store_id,
                error=OrderAuthorizationError,
            )
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
                    # `accepted` replaces the old `approval_id IS NOT NULL` check. Under DEC-021
                    # (2026-08-25) an exact price is authorised by a staff acceptance attestation in
                    # `quote_acceptances`, not by an approval envelope -- an envelope is two parties
                    # and an attestation is one. The attestation names this exact revision and its
                    # digest, and names the revision it produced -- which is the one an order is
                    # created against, because a revision cannot be promoted in place. An order
                    # still cannot be created against a price nobody accepted.
                    """
                    SELECT q.store_id, r.finality, r.status, r.snapshot_hash,
                           EXISTS (
                               SELECT 1 FROM quote_acceptances a
                               WHERE a.quote_id = r.quote_id
                                 AND a.final_revision = r.revision
                           ) AS accepted,
                           r.valid_until,
                           -- The customer this quote was priced for, reached through the intake
                           -- request it is bound to. NULL when the quote names a request that does
                           -- not exist, which the guard below refuses.
                           (
                               SELECT req.contact_binding_id FROM order_requests req
                               WHERE req.id = q.bound_order_request_id
                           ) AS quoted_customer,
                           -- The fulfilment mode the price was computed under, read from the
                           -- delivery engine's own trace inside the immutable snapshot.
                           (
                               SELECT t -> 'trace' ->> 'fulfillment_mode'
                               FROM jsonb_array_elements(r.snapshot -> 'calculation_traces') AS t
                               WHERE t ->> 'component' = 'DELIVERY'
                               LIMIT 1
                           ) AS priced_mode,
                           -- Has this agreement already been turned into an order? `CONVERTED` has
                           -- been in this column's CHECK since migration 0005 and nothing ever
                           -- wrote it, so one acceptance could back unlimited orders.
                           q.lifecycle,
                           -- Did the customer agree a newer price afterwards? An acceptance is
                           -- single-shot per revision, but a quote may be re-priced and accepted
                           -- again -- and then the earlier agreement is history, not an order.
                           EXISTS (
                               SELECT 1 FROM quote_acceptances later
                               WHERE later.quote_id = r.quote_id
                                 AND later.final_revision > r.revision
                           ) AS superseded
                    FROM quotes q
                    JOIN quote_revisions r ON r.quote_id = q.id
                    WHERE q.id = %s AND r.revision = %s
                    """,
                    (command.accepted_quote_id, command.accepted_quote_revision),
                )
                quote = cursor.fetchone()
            with connection.cursor() as cursor:
                # `bound_contact_id` has been required since this table existed and nothing ever
                # checked it -- the same shape of hole `approval_id` carried until 0029. It has two
                # legitimate sources and `DEC-015` deliberately declines to unify them behind a
                # party layer, because unifying them is the customer-record layer that decision
                # says not to build. So it is checked against both rather than by one foreign key.
                cursor.execute(
                    """
                    SELECT
                        EXISTS (
                            SELECT 1 FROM counter_tickets t
                            WHERE t.id = %s AND t.store_id = %s
                        )
                        OR EXISTS (
                            SELECT 1 FROM contact_channel_bindings b
                            WHERE b.contact_binding_id = %s
                        )
                    """,
                    (command.bound_contact_id, command.store_id, command.bound_contact_id),
                )
                known_customer = bool(cursor.fetchone()[0])
            if not known_customer:
                raise OrderStateError(
                    "the order names a customer reference this store has never issued or bound"
                )
            # The order's customer must be the customer the quote was priced for. Until 2026-08-29
            # nothing checked this: a verification pass issued two counter tickets, quoted only the
            # first, and created an order for the second against the first's accepted quote. It
            # succeeded and settled at the stranger's price. The order screen builds this request
            # from free-text fields an operator pastes, so one mis-paste at a busy counter charged
            # one customer another's total, with the settlement and the immutable snapshot agreeing.
            if quote is not None and _uuid_or_none(quote[6]) != command.bound_contact_id:
                raise OrderStateError(
                    "the quote was priced for a different customer than this order names"
                )
            # And the order must be fulfilled the way it was priced. Quoting a delivery at +10,000d
            # and then creating the order as self-collect kept the fee for transport the system
            # would afterwards refuse to record; the reverse drove two legs for nothing.
            if quote is not None and str(quote[7]) != command.fulfillment_mode.value:
                raise OrderStateError(
                    "the order's fulfilment mode is not the one the quote was priced under"
                )
            # The customer's most recent agreement is the only one an order may cite. Until
            # 2026-08-30 the guard asked only that *some* acceptance named this revision, never
            # that it was still the current one -- so after "thêm cái áo này nữa" repriced 100,000d
            # down to 50,000d and the customer agreed again, an order could still be created
            # against the superseded 100,000d. It then settled at 100,000d while the system
            # actively refused the 50,000d the customer had just agreed to, with the order, the
            # settlement and the immutable snapshot all agreeing with each other on the stale price.
            #
            # It became reachable on 2026-08-29. `outbox_events.idempotency_key` is UNIQUE and the
            # acceptance key was `quote:{id}:acceptance` for every acceptance of a quote, so a
            # second one always collided and rolled back. That collision was accidentally the only
            # thing preventing two accepted revisions. Revision-scoping the key fixed a real 500 on
            # the reprice path and removed the accident with it.
            if quote is not None and bool(quote[9]):
                raise OrderStateError(
                    "the customer agreed a newer price for this quote; read the current one to them"
                )
            # And an agreement authorises exactly one order. `quote_acceptances` is UNIQUE on
            # (quote_id, accepted_revision) so "who chốt this" has one answer; converting that
            # answer into an order must be single-shot for the same reason. Three POSTs with three
            # fresh idempotency keys produced three orders and three full-price settlements against
            # one attestation -- 100,000d agreed once, 300,000d recorded as collected.
            if quote is not None and str(quote[8]) != "OPEN":
                raise OrderStateError("this quote has already been converted into an order")
            if (
                quote is None
                or _uuid(quote[0]) != command.store_id
                or str(quote[1]) != "APPROVED_EXACT"
                or str(quote[2]) != "ACCEPTED_FINAL"
                or str(quote[3]) != command.accepted_quote_snapshot_hash
                or not quote[4]
                or (
                    quote[5] is not None
                    and command.customer_final_quote_accepted_at >= _datetime(quote[5])
                )
            ):
                raise OrderStateError("accepted exact quote is missing, stale, or expired")

            order_id = uuid4()
            occurred_at = command.customer_final_quote_accepted_at

            def mutation(cursor: Any) -> None:
                # Spend the agreement in the same transaction that creates the order. The guard
                # above reads `lifecycle` before the write; this is what makes the read binding
                # under concurrency, because two simultaneous creates both pass the guard and only
                # one can move the row out of OPEN. Without it the check is advisory.
                cursor.execute(
                    """
                    UPDATE quotes
                    SET lifecycle = 'CONVERTED', row_version = row_version + 1
                    WHERE id = %s AND lifecycle = 'OPEN'
                    RETURNING id
                    """,
                    (command.accepted_quote_id,),
                )
                if cursor.fetchone() is None:
                    raise OrderStateError("this quote has already been converted into an order")
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
        # Membership is proven BEFORE the idempotency lookup, not only inside the locked write.
        # `IdempotencyRepository.execute` short-circuits to the stored response when it finds the
        # key, so every check living inside the executor is skipped on a replay -- and a staff
        # member removed from a store still got 200 and that store's full order state from a key
        # they were holding, where a fresh request was correctly refused 403. `accept_quote` and
        # `create_quote` already check outside the wrapper for exactly this reason.
        #
        # The locked check inside `transition_once` stays: this one answers "may you ask", that one
        # answers "may you write this row", and only the second can see a revocation that lands
        # mid-transaction.
        _require_store_membership_for_order(connection, command.order_id, command.principal)
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
                if row is not None:
                    # STORE-SCOPING-002. `STORE-SCOPING-001` enumerated store-scoped routes by URL
                    # shape — every path containing `/stores/{store_id}/` — and this route is keyed
                    # by `order_id`, so it was never in that list. The effect was a cross-store
                    # *write*: any principal with an operations role and MFA could confirm, cancel
                    # or complete any order in any store by supplying its identifier.
                    #
                    # The store comes from the row this method already locked, never from the
                    # request: a client-supplied identifier is not authority. The check runs on the
                    # same cursor while `FOR UPDATE` is held, so a concurrently revoked assignment
                    # cannot be raced past it.
                    require_store_membership(
                        cursor,
                        staff_user_id=command.principal.staff_user_id,
                        store_id=_uuid(row[0]),
                        error=OrderAuthorizationError,
                    )
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
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=OrderAuthorizationError,
        )
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


def _require_store_membership_for_order(
    connection: Any, order_id: UUID, principal: StaffPrincipal
) -> None:
    """Refuse a caller who is not a member of the order's store, before any idempotency lookup.

    A missing order is deliberately not an error here: the write path answers that, with the same
    opaque "order is missing or stale" a non-member would eventually get, so this check cannot be
    turned into an oracle for which order ids exist.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT store_id FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
        if row is None:
            return
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=_uuid(row[0]),
            error=OrderAuthorizationError,
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


def _uuid_or_none(value: object) -> UUID | None:
    """A customer reference read back from the database, or nothing when the join found no row."""

    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))
