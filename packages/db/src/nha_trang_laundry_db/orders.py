"""Transactional order commands backed by deterministic orthogonal state machines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    IntakeRejectionReason,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
    RewashReason,
)
from nha_trang_laundry_domain.order_steps import (
    COMPOSITE_STEPS,
    NextStep,
    OrderStep,
    QuoteReadinessFacts,
    StepFacts,
    StepRequiresHuman,
    derive_intake_readiness,
    next_steps,
    payment_may_hand_over,
    plan_step,
)
from nha_trang_laundry_domain.orders import (
    IntakeReadiness,
    OrderState,
    OrderTransitionError,
    transition_commercial,
    transition_intake,
    transition_production,
)
from nha_trang_laundry_domain.payments import (
    ChargeKind,
    OrderCharge,
    owed_charges,
    payment_position,
)
from nha_trang_laundry_domain.settlement import QuotedTotal, SettlementShape

from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.payments import PAYMENT_VIEW_COLUMNS, PaymentView, payment_views
from nha_trang_laundry_db.quotes import PRICED_FULFILLMENT_MODE_SQL
from nha_trang_laundry_db.remedies import RemedyStateError, spend_reserved_remedy_credits
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change


class OrderStateError(ValueError):
    """Raised for missing, stale, or unsafe persisted order state."""


class OrderAuthorizationError(PermissionError):
    """Raised when a staff principal lacks an order command permission."""


class OrderStepRequiresHuman(OrderStateError):
    """`ORDER-STEPS-001`: a step a person must unblock, with one reason code per missing fact.

    Raised before anything is written, so the whole step is refused and the order is unchanged.
    The message keeps the domain's `HUMAN_APPROVAL_REQUIRED:` prefix; `reason_codes` is what the
    route hands the console.
    """

    def __init__(self, message: str, reason_codes: tuple[str, ...]) -> None:
        super().__init__(message)
        self.reason_codes = reason_codes


class OrderNotVisibleError(LookupError):
    """The order does not exist, or it is in a store the caller is not a member of.

    One error for both, with one message whatever the caller passes, because telling them apart
    would let anyone holding an operations role learn which order ids exist in which store by
    probing -- the promise `store_access` makes in as many words, and the reason
    `OperationsService._derive_intake_readiness` filters rather than raising a second error.
    """

    def __init__(self, _detail: str = "") -> None:
        super().__init__("order is not visible to this caller")


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
    #: `ACQUISITION-ATTRIBUTION-001`. Where the customer says they found the shop, attested by the
    #: staff member taking the order. Required and without a default on purpose: a default here
    #: would let every caller that forgets the field record `UNKNOWN` silently, which is the exact
    #: difference between "nobody asked" and "the code did not ask", and only the first is true.
    acquisition_source: AcquisitionSource


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
    #: `DEC-024`. What a named staff member says happened to the laundry and the money when an
    #: order is cancelled after work began. `transition_commercial` has always taken the two flags
    #: this fills and no caller ever passed them, so the reviewed cancellation path could not
    #: succeed while the unreviewed one always did. Supplying a resolution *is* the approval --
    #: there is no separate boolean, because a second field nobody sets is how this defect started.
    custody_resolution: CustodyResolution | None = None
    #: `ORDER-STEPS-002`. Set only on the first transition of a `REWASH` / `REJECT_INTAKE` step,
    #: by `execute_step` from the domain's plan; the per-axis routes never set them. Written onto
    #: that transition's event payload and audit details, with the step's name, so the order's
    #: history can say "Giặt lại · Chưa sạch" instead of "Sản xuất · Sự cố".
    step: OrderStep | None = None
    rewash_reason: RewashReason | None = None
    rejection_reason: IntakeRejectionReason | None = None


@dataclass(frozen=True)
class OrderStepCommand:
    """`ORDER-STEPS-001`: one named business step, executed as its domain transitions.

    `expected_row_version` is the caller's `If-Match`, checked once against the row the step locks;
    the step's own transitions then advance it one version each. `slot_approved` is the operator's
    attestation for `RECEIVE`, the only readiness fact a caller supplies. `custody_resolution` is
    the `DEC-024` statement a cancellation through review requires. `rewash_reason` and
    `rejection_reason` are what a named staff member says about a `REWASH` / `REJECT_INTAKE`
    (`ORDER-STEPS-002`); each is taken only by its own step.
    """

    order_id: UUID
    expected_row_version: int
    principal: StaffPrincipal
    idempotency_key: str
    correlation_id: UUID
    step: OrderStep
    slot_approved: bool = False
    custody_resolution: CustodyResolution | None = None
    occurred_at: datetime | None = None
    rewash_reason: RewashReason | None = None
    rejection_reason: IntakeRejectionReason | None = None


@dataclass(frozen=True)
class OrderStepResult:
    """The order as it stood when a step committed, and whether this answer is a replay."""

    view: OrderView
    replayed: bool


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


#: An order still in play: anything not in `TERMINAL_COMMERCIAL_STATUSES`. Spelled once, because
#: migration 0047's partial index `orders_store_open_idx` carries the same text and the planner only
#: uses a partial index whose predicate the query implies; `test_order_lookup.py` pins both.
OPEN_ORDERS_SQL_PREDICATE: Final = "commercial_status NOT IN ('CANCELLED', 'COMPLETED')"

#: The hard ceiling on one board page. Unchanged from before the read model grew.
MAX_BOARD_LIMIT: Final = 200


@dataclass(frozen=True)
class TicketReference:
    """A walk-in ticket as the counter says it: a number, on one business day (`DEC-013`).

    Numbers restart every morning, so a number alone names one customer per day the shop has been
    open. The day is required here and is never defaulted inside the repository: "today" is a
    reading of a clock, and the caller that owns the clock supplies it.
    """

    number: int
    issued_on: date


@dataclass(frozen=True)
class OrderView:
    """One order as the counter reads it. Every field is a stored fact, except `next_steps`.

    `next_steps` (`ORDER-STEPS-001`) is computed, and only by the domain: `order_steps.next_steps`
    over the stored facts this row was read with. No money is computed anywhere in this view.

    The first seven are `StoredOrder`'s, with the same names, so a caller that read the old list
    item reads this one unchanged.

    `payable_total_vnd` is the bound quote revision's display total, read from the same columns
    `SettlementRepository.record` checks a payment against -- so the figure the counter is shown is
    the figure the settlement will accept, to the đồng. It is null when that revision presents no
    single total (an unresolved delivery fee or a range), which `evaluate_settlement` refuses for
    the same reason; it is never zero in that case. It does not change when the money is taken:
    `balance` says whether it has been.

    `ticket_number` / `ticket_issued_on` are null when the order's customer reference is a channel
    binding rather than a counter ticket (`DEC-015` keeps the two sources separate). A ticket is
    only ever read within the order's own store.
    """

    order_id: UUID
    store_id: UUID
    commercial: CommercialOrderStatus
    intake: IntakeStatus
    production: ProductionStatus
    balance: OrderBalanceStatus
    row_version: int
    fulfillment_mode: FulfillmentMode
    created_at: datetime
    quote_id: UUID
    quote_revision: int
    payable_total_vnd: int | None
    ticket_number: int | None
    ticket_issued_on: date | None
    #: Whether the customer is recorded as having taken the goods at the counter. `DEC-032` made it
    #: something pickup needs to read: a walk-in who paid at drop-off shows `balance` PAID and this
    #: false until the staff member handing the bag over records it. The stored flag, not inferred.
    self_collection_recorded: bool
    #: Where the customer said they found the shop, as the staff member who asked recorded it at
    #: creation (`ACQUISITION-ATTRIBUTION-001`). Immutable by trigger, so this is only ever a
    #: readback: a counter that mis-tapped can now see it, and still cannot change it. Staff read
    #: model only -- the agent tool contract never names this field.
    acquisition_source: AcquisitionSource
    #: `READ-ENRICH-001`. Every delivery attempt recorded for the order, oldest first: the stored
    #: rows of `delivery_legs`, including failed attempts. Empty for an order with none.
    delivery_legs: tuple[DeliveryLegView, ...] = ()
    #: The stored flag a succeeded `RETURN` leg sets, which `transition_commercial` reads.
    required_delivery_legs_succeeded: bool = False
    #: The order's settlement shape (`EXACT_PAYMENT_SELF_COLLECTION`, `..._PREPAID_DELIVERY`,
    #: `..._PREPAID_SELF_COLLECTION`), or null when nothing has been settled.
    settlement_shape: str | None = None
    #: `ORDER-STEPS-001`. The legal next steps, computed by `order_steps.next_steps` from the stored
    #: facts this row was read with -- the one computed field, and computed only by the domain.
    next_steps: tuple[NextStep, ...] = ()
    # --- `PAYMENT-001` (`DEC-035`): Tổng · Đã trả · Còn lại -------------------------------------
    #: What is owed, as a list of charges: today the bound quote's presentable total alone
    #: (`QUOTED_TOTAL`); `UNCLAIMED-001` adds the storage fee here. Empty when the quote presents
    #: no single total. `owed_vnd` is their sum and `remaining_vnd` what is still owed, both
    #: computed by the domain (`payments.payment_position`), and both null in that case, never 0.
    charges: tuple[OrderCharge, ...] = ()
    owed_vnd: int | None = None
    #: The payment ledger's sum, by PostgreSQL. Null only in a step reply stored before this field
    #: existed (an idempotent replay repeats what it said then).
    paid_vnd: int | None = None
    remaining_vnd: int | None = None
    #: The ledger's rows, oldest first, at most `PAYMENT_READ_LIMIT`; `payments_truncated` says so.
    payments: tuple[PaymentView, ...] = ()
    payments_truncated: bool = False
    #: Whether the payment that settles the order may also record that the customer takes the
    #: goods now (`order_steps.payment_may_hand_over`), so the console knows to offer that tick.
    payment_may_hand_over: bool = False


@dataclass(frozen=True, slots=True)
class DeliveryLegView:
    """One recorded delivery attempt, as `delivery_legs` stores it."""

    leg_kind: str
    outcome: str
    recorded_at: datetime


#: The four readiness facts the bound quote revision holds, as columns over the revision aliased
#: `r`. The one statement of them: the order read (for `next_steps`), the `RECEIVE` step and the
#: per-axis intake route (`OrderRepository.intake_readiness_for`) all read these columns, so no two
#: of them can disagree about whether an order may be accepted.
#:
#: * quantity: every line was weighed or counted by staff, none is the customer's estimate;
#: * services: every line names a service code, which a pricebook-priced line always does;
#: * exact price: the revision is `APPROVED_EXACT` and `ACCEPTED_FINAL`;
#: * agreement: a `DEC-021` acceptance attestation produced this revision.
_QUOTE_READINESS_COLUMNS: Final = """
    CASE WHEN jsonb_typeof(r.snapshot -> 'lines') = 'array'
              AND jsonb_array_length(r.snapshot -> 'lines') > 0
         THEN NOT EXISTS (
             SELECT 1 FROM jsonb_array_elements(r.snapshot -> 'lines') AS line
             WHERE line ->> 'quantity_basis' = 'CUSTOMER_ESTIMATE'
         )
         ELSE FALSE END AS quantity_basis_approved,
    CASE WHEN jsonb_typeof(r.snapshot -> 'lines') = 'array'
              AND jsonb_array_length(r.snapshot -> 'lines') > 0
         THEN NOT EXISTS (
             SELECT 1 FROM jsonb_array_elements(r.snapshot -> 'lines') AS line
             WHERE coalesce(line ->> 'service_code', '') = ''
         )
         ELSE FALSE END AS service_classified,
    (r.finality = 'APPROVED_EXACT' AND r.status = 'ACCEPTED_FINAL') AS exact_price_approved,
    EXISTS (
        SELECT 1 FROM quote_acceptances a
        WHERE a.quote_id = r.quote_id AND a.final_revision = r.revision
    ) AS customer_agreed
"""

#: The read model, shared by the board and the read by id so the two cannot disagree about a field.
#: `CASE` rather than `display_total_min_vnd` alone: a range has no single amount owed, and the
#: settlement refuses it (`TOTAL_IS_A_RANGE`), so reporting its lower bound would put a number on
#: screen that the counter cannot take. An order's revision is always APPROVED_EXACT, where 0006
#: already forces the two equal; the CASE states the rule rather than relying on that.
#:
#: Columns 16 onward are `ORDER-STEPS-001` / `READ-ENRICH-001`: the stored facts `next_steps` is
#: computed from, and the delivery legs and settlement shape the order page shows. The settlement
#: and the legs are scalar subqueries rather than joins so the board's plan -- one index scan over
#: `orders`, stopped at the LIMIT -- is unchanged; each is served by its own order-keyed index.
_ORDER_VIEW_SELECT: Final = (
    """
    SELECT o.id, o.store_id, o.commercial_status, o.intake_status, o.production_status,
           o.balance_status, o.row_version, o.fulfillment_mode, o.created_at,
           o.current_quote_id, o.current_quote_revision,
           CASE WHEN r.display_total_min_vnd = r.display_total_max_vnd
                THEN r.display_total_min_vnd END AS payable_total_vnd,
           t.ticket_number, t.issued_on, o.self_collection_recorded, o.acquisition_source,
           o.required_delivery_legs_succeeded, o.production_resume_status,
           o.production_accepted_at, r.display_total_min_vnd, r.display_total_max_vnd,
           (
               SELECT s.settlement_shape FROM order_settlements s WHERE s.order_id = o.id
           ) AS settlement_shape,
           (
               SELECT coalesce(
                   jsonb_agg(
                       jsonb_build_object(
                           'leg_kind', d.leg_kind,
                           'outcome', d.outcome,
                           'recorded_at', d.recorded_at
                       )
                       ORDER BY d.recorded_at, d.id
                   ),
                   '[]'::jsonb
               )
               FROM delivery_legs d WHERE d.order_id = o.id
           ) AS delivery_legs,
    """
    + _QUOTE_READINESS_COLUMNS
    # `PAYMENT-001`: row[27] the ledger's sum, row[28] its first rows (`payments.py`).
    + PAYMENT_VIEW_COLUMNS
    + """
    FROM orders o
    JOIN quote_revisions r
      ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
    LEFT JOIN counter_tickets t
      ON t.id = o.bound_contact_id AND t.store_id = o.store_id
"""
)

#: The row a transition locks and decides from. One statement for the per-axis routes and for each
#: transition of a composite step, so the two paths read the same facts under the same lock.
_LOCK_ORDER_FOR_TRANSITION_SQL: Final = """
    SELECT store_id, commercial_status, intake_status, production_status,
           fulfillment_mode, balance_status,
           required_delivery_legs_succeeded, self_collection_recorded,
           production_accepted_at, production_resume_status, row_version
    FROM orders
    WHERE id = %s
    FOR UPDATE
"""


def _list_statement(
    *, store_id: UUID, limit: int, open_only: bool, ticket: TicketReference | None
) -> tuple[str, dict[str, object]]:
    """The board's statement and parameters, exposed so the plan test reads the query it runs."""

    clauses = ["o.store_id = %(store_id)s"]
    parameters: dict[str, object] = {"store_id": store_id, "limit": limit}
    if open_only:
        clauses.append(f"o.{OPEN_ORDERS_SQL_PREDICATE}")
    if ticket is not None:
        # Through the ticket's own store-scoped unique key, never a bare number: every store has a
        # "số 1" today, and another store's is not this counter's customer.
        clauses.append(
            """o.bound_contact_id IN (
                SELECT ct.id FROM counter_tickets ct
                WHERE ct.store_id = %(store_id)s
                  AND ct.issued_on = %(ticket_issued_on)s
                  AND ct.ticket_number = %(ticket_number)s
            )"""
        )
        parameters["ticket_issued_on"] = ticket.issued_on
        parameters["ticket_number"] = ticket.number
    sql = (
        _ORDER_VIEW_SELECT
        + " WHERE "
        + " AND ".join(clauses)
        + " ORDER BY o.created_at DESC, o.id LIMIT %(limit)s"
    )
    return sql, parameters


def _ready_clock_effect(
    *,
    moving_production: bool,
    before: ProductionStatus,
    after: ProductionStatus,
    resume_to: ProductionStatus | None,
    moment: datetime,
) -> tuple[datetime | None, bool]:
    """When `orders.production_ready_at` may change, and to what.

    The column answers one question -- when the laundry was last reported finished -- and three
    separate defects came from writing it as a side effect of whatever command happened to run.

    * A commercial or intake move must not touch it at all. An order finished at 09:00 and sent to
      cancellation review at 16:00 recorded 16:00, because one UPDATE serves all three dimensions
      and the write read the resulting production status without asking which dimension had moved.
    * A hold is a pause, not rework. `ON_HOLD` from `READY_AT_STORE` records
      `production_resume_status = READY_AT_STORE` and the domain permits exactly one exit, back to
      where it was held from, so nothing happens to the laundry while it is held -- and lifting the
      hold is not a second completion either.
    * A rewash is the one case that must move it. `DEC-024` makes
      `READY_AT_STORE -> EXCEPTION -> IN_PROCESS` legal because a stain found at quality check needs
      one, and while the work is being redone the order is not finished at all.

    Returns `(stamp, keep)`: `stamp` is written when it is not None, and `keep` decides whether the
    existing value survives when nothing is stamped.
    """

    if not moving_production:
        return None, True
    if after is ProductionStatus.READY_AT_STORE:
        # Resuming a hold returns to the state the hold interrupted; it is the same completion.
        return (None, True) if before is ProductionStatus.ON_HOLD else (moment, False)
    if after is ProductionStatus.RELEASED:
        return None, True
    if after is ProductionStatus.ON_HOLD and resume_to is ProductionStatus.READY_AT_STORE:
        return None, True
    return None, False


class OrderRepository:
    """Create and transition orders with authorization, stale checks, audit, and outbox."""

    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()

    def create(
        self,
        connection: Any,
        command: CreateOrderCommand,
        *,
        evaluated_at: datetime | None = None,
    ) -> StoredOrder:
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
        # COUNTER-DEFECTS-001. Expiry used to be decided against
        # `command.customer_final_quote_accepted_at`, which is a request body field validated only
        # for timezone-awareness -- so an expired quote became orderable by claiming an earlier
        # acceptance time, and the shop was held to a price it had already withdrawn. When a quote
        # stopped being valid is not something the caller may assert. `evaluated_at` exists for
        # tests to hold the clock still; it is a method parameter rather than a command field
        # precisely so no request can reach it.
        moment = evaluated_at or datetime.now(UTC)
        scope = f"store:{command.store_id}:contact:{command.bound_contact_id}:order:create"
        payload: dict[str, object] = {
            "store_id": str(command.store_id),
            "bound_contact_id": str(command.bound_contact_id),
            "accepted_quote_id": str(command.accepted_quote_id),
            "accepted_quote_revision": command.accepted_quote_revision,
            "accepted_quote_snapshot_hash": command.accepted_quote_snapshot_hash,
            "fulfillment_mode": command.fulfillment_mode.value,
            "customer_final_quote_accepted_at": command.customer_final_quote_accepted_at,
            "acquisition_source": command.acquisition_source.value,
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
                           """
                    + PRICED_FULFILLMENT_MODE_SQL
                    + """ AS priced_mode,
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
                    -- Locks the quote row, and that is what makes every fact above binding rather
                    -- than merely recently true. Without it this was a plain read, and the only
                    -- compare-and-swap at write time is `lifecycle = 'OPEN'` -- which says nothing
                    -- about supersession. So the losing interleaving was: the guard reads
                    -- `superseded = false`; a `quote_acceptances` insert plus `create_revision`
                    -- commits on another connection; this transaction's UPDATE still finds
                    -- lifecycle OPEN under READ COMMITTED's re-check and succeeds. The order then
                    -- binds `current_quote_revision` to the older revision, and
                    -- `SettlementRepository.record` prices off exactly that row -- so the counter
                    -- collects a price the customer had already replaced.
                    --
                    -- `create_revision`'s own CAS updates `quotes`, so it takes the same row lock:
                    -- accept-then-create now waits and then reads `superseded = true` and refuses,
                    -- and create-then-accept waits and then fails its `lifecycle = 'OPEN'` match,
                    -- which is correct -- a quote that became an order cannot be re-priced.
                    --
                    -- `OF q` and not a bare `FOR UPDATE`: only the agreement needs serialising, and
                    -- `quote_revisions` is append-only, so locking its rows would add contention
                    -- for nothing.
                    FOR UPDATE OF q
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
                or (quote[5] is not None and moment >= _datetime(quote[5]))
            ):
                raise OrderStateError("accepted exact quote is missing, stale, or expired")

            order_id = uuid4()
            # The server's clock, not the caller's. `customer_final_quote_accepted_at` is an
            # attested business fact -- when the customer agreed the price -- and it keeps its own
            # column. It is not when this row was written, and it is not when this action happened,
            # which is what `created_at` and every audit/event/outbox `occurred_at` below mean.
            #
            # COUNTER-DEFECTS-001 already established that this field cannot be trusted to decide
            # whether a quote had expired, and introduced `moment` for exactly that reason; the
            # record clock was left behind. Trusting it here let a caller backdate an order's
            # creation and its whole audit trail: the order timeline sorts on `occurred_at`, and
            # the channel report in `scripts/report_acquisition_sources.py` ranges over
            # `created_at`, so a stale acceptance time filed today lands in last week's numbers.
            occurred_at = moment

            def mutation(cursor: Any) -> None:
                # Spend the agreement in the same transaction that creates the order. The guard
                # above reads `lifecycle` before the write; this is what makes the read binding
                # under concurrency, because two simultaneous creates both pass the guard and only
                # one can move the row out of OPEN. Without it the check is advisory.
                # COUNTER-DEFECTS-001. `order_requests.status` was written by nothing: every row
                # was born `DRAFT` and stayed `DRAFT`, so `SUBMITTED` and `CANCELLED` were
                # unreachable CHECK values and the intake list showed a request that had already
                # become an order as though it were still waiting. The request is submitted at the
                # moment its agreement is spent, in the same transaction, because that is the
                # moment the fact becomes true.
                cursor.execute(
                    """
                    UPDATE order_requests
                    SET status = 'SUBMITTED', row_version = row_version + 1
                    WHERE id = (SELECT bound_order_request_id FROM quotes WHERE id = %s)
                      AND status = 'DRAFT'
                    """,
                    (command.accepted_quote_id,),
                )
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
                        customer_final_quote_accepted_at, acquisition_source,
                        row_version, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, 'REQUESTED', 'AWAITING_HANDOFF',
                        'NOT_STARTED', NULL, %s, 'UNPAID', %s, %s, 1, %s
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
                        command.acquisition_source.value,
                        occurred_at,
                    ),
                )
                # The credit-lifecycle fix: a remedy credit on this bill is spent now, in the same
                # transaction as the order it discounts, and not when it was first put on the quote.
                # A credit another order already spent refuses this one and rolls it back -- the
                # customer was read a total that included a discount the shop can no longer give.
                try:
                    spend_reserved_remedy_credits(
                        connection,
                        store_id=command.store_id,
                        quote_id=command.accepted_quote_id,
                        revision=command.accepted_quote_revision,
                        order_id=order_id,
                        actor_id=command.principal.staff_user_id,
                        correlation_id=command.correlation_id,
                        occurred_at=occurred_at,
                    )
                except RemedyStateError as error:
                    raise OrderStateError(f"{error.reason_code}: {error}") from error

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
            # `production_accepted_at` is deliberately NOT here, and the difference matters. This
            # payload answers "did the caller ask for the same thing?", and that timestamp is not
            # something a caller asks for: `OperationsService.transition_intake` mints it with
            # `datetime.now(UTC)` on every call. Hashing it made the same Idempotency-Key, resent
            # for the identical intent, produce a different digest -- so the retry an operator makes
            # when the counter's wifi stutters came back IDEMPOTENCY_CONFLICT, and the console told
            # them the order had not changed when it had. Nothing is lost by leaving it out: the
            # timestamp exists exactly when `intake_target` is ACCEPTED, and that field is above.
            # `occurred_at`, the other server-minted clock, was already excluded for this reason.
            # What staff said happened to the customer's laundry and their money is part of the
            # command's identity, not decoration on it. Left out of this payload, two cancellations
            # differing only in their resolution hashed to the same digest, so the second returned
            # the first's stored response: the ledger recorded NOT_RECEIVED while the staff member
            # who pressed the button had recorded SHOP_FAULT_NO_CHARGE, and nothing anywhere
            # reported a conflict. Every other field that changes what gets written is here; this
            # one decides both `cancellation_approved` and what the event says.
            "custody_resolution": (
                command.custody_resolution.value if command.custody_resolution else None
            ),
        }

        def transition_once() -> dict[str, object]:
            with connection.cursor() as cursor:
                cursor.execute(_LOCK_ORDER_FOR_TRANSITION_SQL, (command.order_id,))
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
            return self._apply_locked_transition(connection, command, row, occurred_at)

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

    def _apply_locked_transition(
        self,
        connection: Any,
        command: OrderTransitionCommand,
        row: tuple[object, ...],
        occurred_at: datetime,
    ) -> dict[str, object]:
        """Decide and write one transition of a row already locked, member-checked and
        version-checked by the caller.

        The whole of what a transition writes: the order row, any refund, the domain event, the
        audit row and the outbox rows, through `commit_material_change`. Shared by the per-axis
        routes (`transition`) and by every transition of a composite step (`execute_step`), so a
        step's audit trail is the per-axis audit trail, row for row.
        """

        current = _order_state(row)
        try:
            if command.commercial_target is not None:
                # DEC-024: a resolution supplied by a named staff member is the approval. Both
                # flags derive from the one field, so there is no way to approve a cancellation
                # without saying what happened to the customer's laundry and their money.
                resolved = command.custody_resolution is not None
                next_state = transition_commercial(
                    current,
                    command.commercial_target,
                    cancellation_approved=resolved,
                    custody_and_financial_resolution_recorded=resolved,
                    custody_resolution=command.custody_resolution,
                )
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
        # DEC-024. The domain decided whether money goes back; this reads how much, from the
        # settlement ledger, under the order lock already held. Nobody types the amount: it is
        # the settled amount, and `order_refunds`' composite key to `order_settlements` makes it
        # impossible for the row to say anything else.
        refund = (
            _refund_for_cancellation(
                connection,
                order_id=command.order_id,
                resolution=command.custody_resolution,
                partly_paid=current.balance is OrderBalanceStatus.PARTIALLY_PAID,
            )
            if current.balance in {OrderBalanceStatus.PAID, OrderBalanceStatus.PARTIALLY_PAID}
            and next_state.balance is OrderBalanceStatus.REFUNDED
            else None
        )
        closed_at = (
            occurred_at if next_state.commercial is CommercialOrderStatus.COMPLETED else None
        )
        # `0037`. When production last said the laundry was finished, which is what stops the
        # SLA clock. Three cases, and the middle one is why this is not a plain COALESCE:
        #
        #   READY_AT_STORE  stamp it now, replacing any earlier stamp. `DEC-024` makes
        #                   READY_AT_STORE -> EXCEPTION -> IN_PROCESS legal on purpose, because
        #                   a stain found at quality check needs a rewash. The first "finished"
        #                   was wrong, so keeping it would be wrong.
        #   RELEASED        keep what is there. The laundry left; nothing was redone.
        #   anything else   clear it. While an order is being rewashed it is NOT finished, and
        #                   a stale stamp made the risk board report SLA_MET for exactly the
        #                   order most likely to be late.
        #
        # A first version kept the earliest stamp forever, on the reasoning that the question is
        # when the laundry was done rather than how often the board was touched. That reasoning
        # is wrong the moment a rewash exists: it freezes MET permanently for a rewashed order.
        #
        # All three of those cases are about the *production* dimension, and the second version
        # read them off the resulting production status without asking which dimension had
        # actually moved. One UPDATE serves all three, so an order finished at 09:00 and sent to
        # cancellation review at 16:00 recorded 16:00 as the moment its laundry was done --
        # nothing had happened to the laundry at 16:00, and this column exists precisely so the
        # SLA board can tell those two times apart. A commercial or intake move now leaves the
        # clock exactly as it found it.
        #
        # A hold is the third case, and it is not rework. `ON_HOLD` from `READY_AT_STORE`
        # records `production_resume_status = READY_AT_STORE` and the domain permits exactly one
        # exit, back to where it was held from -- so nothing happens to the laundry while it is
        # held. Treating every state that is not READY_AT_STORE or RELEASED as rework cleared
        # the clock for a finished bag put on hold for a shelf audit, and restamped it when
        # somebody lifted the hold. `0037`'s own comment says a hold does not reset it; the code
        # did. (That comment also still says "write-once" and "the first moment", which the
        # rewash correction replaced with the last completion. The migration is applied and
        # checksummed, so it is not edited; the rule lives here.)
        # `0042`. When the customer physically took their laundry away, which is what starts
        # the `DEC-004` remedy windows for a self-collected order. Distinct from
        # `production_ready_at` (the work was finished, possibly days earlier) and from
        # `closed_at` (the commercial order completed, possibly later still); a remedy window
        # measured against either would be measured against the wrong event.
        #
        # Write-once through the COALESCE below: `RELEASED` is terminal on the production
        # dimension, so there is no second release to record.
        released_now = (
            occurred_at
            if command.production_target is not None
            and next_state.production is ProductionStatus.RELEASED
            else None
        )
        ready_now, ready_keep = _ready_clock_effect(
            moving_production=command.production_target is not None,
            before=current.production,
            after=next_state.production,
            resume_to=next_state.production_resume_status,
            moment=occurred_at,
        )

        def mutation(cursor: Any) -> None:
            # The refund row first: `order_refund_consistency` refuses to let the order read
            # REFUNDED unless one exists, and the deferred check on `order_refunds` refuses to
            # commit one beside an order that is not cancelled. Either write alone fails.
            if refund is not None:
                cursor.execute(
                    """
                    INSERT INTO order_refunds (
                        id, order_id, store_id, settlement_id, refunded_amount_vnd,
                        direction, custody_resolution, attested_by_staff_id, refunded_at,
                        created_at
                    ) VALUES (%s, %s, %s, %s, %s, 'TO_CUSTOMER', %s, %s, %s, %s)
                    """,
                    (
                        refund.refund_id,
                        command.order_id,
                        refund.store_id,
                        refund.settlement_id,
                        refund.amount_vnd,
                        refund.resolution.value,
                        command.principal.staff_user_id,
                        occurred_at,
                        occurred_at,
                    ),
                )
            cursor.execute(
                """
                UPDATE orders
                SET commercial_status = %s, intake_status = %s, production_status = %s,
                    balance_status = %s,
                    production_resume_status = %s, production_accepted_at = %s,
                    production_ready_at = COALESCE(
                        %s, CASE WHEN %s THEN production_ready_at ELSE NULL END
                    ),
                    production_released_at = COALESCE(production_released_at, %s),
                    closed_at = COALESCE(closed_at, %s), row_version = row_version + 1
                WHERE id = %s AND row_version = %s
                RETURNING id
                """,
                (
                    next_state.commercial.value,
                    next_state.intake.value,
                    next_state.production.value,
                    next_state.balance.value,
                    (
                        next_state.production_resume_status.value
                        if next_state.production_resume_status
                        else None
                    ),
                    next_state.production_accepted_at,
                    ready_now,
                    ready_keep,
                    released_now,
                    closed_at,
                    command.order_id,
                    command.expected_row_version,
                ),
            )
            if cursor.fetchone() is None:
                raise OrderStateError("STALE_VERSION: order transition lost concurrency race")
            # A cancelled order ends its intake request with it. Without this the request stays
            # `SUBMITTED` in "Tiếp nhận gần đây" and reads as live intake for a customer who
            # has gone home -- the console cannot tell the difference, because until
            # COUNTER-DEFECTS-001 no status but `DRAFT` was ever written.
            if next_state.commercial is CommercialOrderStatus.CANCELLED:
                cursor.execute(
                    """
                    UPDATE order_requests
                    SET status = 'CANCELLED', row_version = row_version + 1
                    WHERE id = (
                        SELECT q.bound_order_request_id
                        FROM quotes q
                        JOIN orders o ON o.current_quote_id = q.id
                        WHERE o.id = %s
                    )
                      AND status <> 'CANCELLED'
                    """,
                    (command.order_id,),
                )

        # ORDER-STEPS-002: what a person said about a rewash or a refusal, recorded once, on the
        # transition that starts the step. Absent for every other transition, so the payload of a
        # per-axis move or an ordinary step is exactly what it always was.
        step_note: dict[str, object] = {}
        if command.rewash_reason is not None:
            step_note["rewash_reason"] = command.rewash_reason.value
        if command.rejection_reason is not None:
            step_note["rejection_reason"] = command.rejection_reason.value
        if step_note and command.step is not None:
            step_note["step"] = command.step.value
        audit_details: dict[str, object] = {
            **({} if refund is None else {"refund": refund.document()}),
            **step_note,
        }

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="ORDER",
                aggregate_id=command.order_id,
                aggregate_version=next_version,
                event_type="ORDER_STATE_TRANSITIONED",
                # DEC-024's attestation lives here rather than in a table of its own. The
                # event ledger is already append-only, already carries the acting staff id, and
                # `domain_events` is unique on (type, id, version) -- so the resolution is
                # attributed and immutable without a fifth aggregate. `DEC-021` got its own
                # table because a quote acceptance is spent by a later command; nothing spends
                # a cancellation.
                event_payload=(
                    {"dimension": dimension, "target": target, **step_note}
                    if command.custody_resolution is None
                    else {
                        "dimension": dimension,
                        "target": target,
                        "custody_resolution": command.custody_resolution.value,
                        **({} if refund is None else {"refund": refund.document()}),
                        **step_note,
                    }
                ),
                audit_action="ORDER_STATE_TRANSITION",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                audit_details=audit_details or None,
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
                    # Money leaving the drawer is its own downstream fact, keyed once per order
                    # because an order is refunded at most once -- the same shape as
                    # `order:{id}:settlement` for the money that came in.
                    *(
                        ()
                        if refund is None
                        else (
                            OutboxEvent(
                                "order.refund_recorded.v1",
                                {"order_id": str(command.order_id), **refund.document()},
                                f"order:{command.order_id}:refund",
                            ),
                        )
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

    def execute_step(self, connection: Any, command: OrderStepCommand) -> OrderStepResult:
        """Execute one named business step as its domain transitions, all or nothing.

        `ORDER-STEPS-001`. The step is planned by `order_steps.plan_step` against the facts read
        under the order's row lock -- every transition dry-run by the real domain function before
        anything is written -- and then each planned transition is written by the same
        `_apply_locked_transition` the per-axis routes use: its own `ORDER_STATE_TRANSITIONED`
        event, `ORDER_STATE_TRANSITION` audit row and `order.state_transitioned.v1` outbox row, at
        its own `row_version`. All of them share one correlation id, and all of them commit in the
        one transaction the idempotency claim opened, or none do.

        Each transition is stamped one microsecond after the one before it. They happen in that
        order inside one command, and the audit timeline sorts on `occurred_at`; identical stamps
        would let "ACTIVE" print above "RECEIVED" on the order's history.

        Authorization is the per-axis route's, twice over for the same reason: membership of the
        order's store before the idempotency lookup (so a revoked member cannot replay a key), and
        again on the cursor that holds the row lock. `If-Match` is compared once, against the row
        the step starts from.

        The idempotent result is the order view as it stood when the step committed, next steps
        included, so a replay returns exactly what the first call did (`replayed=True`).
        """

        _require_order_mutation(command.principal)
        if command.step not in COMPOSITE_STEPS or command.expected_row_version < 1:
            raise OrderStateError("a composite order step and a valid row version are required")
        _require_store_membership_for_order(connection, command.order_id, command.principal)
        occurred_at = command.occurred_at or datetime.now(UTC)
        # What the caller asked for, and nothing the server mints: `occurred_at` and the acceptance
        # instant are left out for the reason `transition` gives.
        payload: dict[str, object] = {
            "order_id": str(command.order_id),
            "expected_row_version": command.expected_row_version,
            "step": command.step.value,
            "slot_approved": command.slot_approved,
            "custody_resolution": (
                command.custody_resolution.value if command.custody_resolution else None
            ),
            # ORDER-STEPS-002. Present only when given, so a step recorded before these existed
            # hashes exactly as it did and its key still replays; and part of the identity when
            # given, so the same key resent with a different reason is a conflict, not a replay of
            # what somebody else said.
            **(
                {}
                if command.rewash_reason is None
                else {"rewash_reason": command.rewash_reason.value}
            ),
            **(
                {}
                if command.rejection_reason is None
                else {"rejection_reason": command.rejection_reason.value}
            ),
        }

        def step_once() -> dict[str, object]:
            with connection.cursor() as cursor:
                cursor.execute(_LOCK_ORDER_FOR_TRANSITION_SQL, (command.order_id,))
                row = cursor.fetchone()
                if row is not None:
                    # The store is the locked row's, as in `transition`; never the request's.
                    require_store_membership(
                        cursor,
                        staff_user_id=command.principal.staff_user_id,
                        store_id=_uuid(row[0]),
                        error=OrderAuthorizationError,
                    )
            if row is None or int(row[10]) != command.expected_row_version:
                raise OrderStateError("STALE_VERSION: order is missing or stale")
            facts_row = _read_view_row(connection, command.order_id)
            try:
                plan = plan_step(
                    command.step,
                    _step_facts(facts_row),
                    slot_approved=command.slot_approved,
                    custody_resolution=command.custody_resolution,
                    accepted_at=occurred_at,
                    rewash_reason=command.rewash_reason,
                    rejection_reason=command.rejection_reason,
                )
            except StepRequiresHuman as error:
                raise OrderStepRequiresHuman(str(error), error.reason_codes) from error
            except OrderTransitionError as error:
                raise OrderStateError(str(error)) from error

            version = command.expected_row_version
            for index, planned in enumerate(plan):
                moment = occurred_at + timedelta(microseconds=index)
                with connection.cursor() as cursor:
                    cursor.execute(_LOCK_ORDER_FOR_TRANSITION_SQL, (command.order_id,))
                    locked = cursor.fetchone()
                if locked is None or int(locked[10]) != version:
                    raise OrderStateError("STALE_VERSION: order changed during the step")
                accepting = planned.intake_target is IntakeStatus.ACCEPTED
                written = self._apply_locked_transition(
                    connection,
                    OrderTransitionCommand(
                        command.order_id,
                        version,
                        command.principal,
                        command.idempotency_key,
                        command.correlation_id,
                        commercial_target=planned.commercial_target,
                        intake_target=planned.intake_target,
                        production_target=planned.production_target,
                        intake_readiness=planned.intake_readiness,
                        production_accepted_at=moment if accepting else None,
                        occurred_at=moment,
                        custody_resolution=planned.custody_resolution,
                        step=command.step,
                        rewash_reason=planned.rewash_reason,
                        rejection_reason=planned.rejection_reason,
                    ),
                    locked,
                    moment,
                )
                version = int(str(written["row_version"]))
            return _order_view_document(
                _order_view_row(_read_view_row(connection, command.order_id))
            )

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                f"order:{command.order_id}:step",
                command.idempotency_key,
                payload,
                occurred_at,
            ),
            step_once,
        )
        try:
            view = _order_view_from_document(result.response)
        except (KeyError, TypeError, ValueError) as error:
            raise OrderStateError("stored idempotent step result is invalid") from error
        return OrderStepResult(view=view, replayed=result.replayed)

    @staticmethod
    def intake_readiness_for(
        cursor: Any, *, order_id: UUID, staff_user_id: UUID, slot_approved: bool
    ) -> IntakeReadiness:
        """The six intake readiness facts for the per-axis intake route: five read, one attested.

        The same `_QUOTE_READINESS_COLUMNS` and the same `derive_intake_readiness` the `RECEIVE`
        step and `next_steps` use. Scoped to the caller's own stores by filtering, not by a second
        error, so a non-member and a stranger's identifier get the same "order is missing" -- the
        reason `OperationsService._derive_intake_readiness` gives.
        """

        cursor.execute(
            "SELECT o.intake_status, "
            + _QUOTE_READINESS_COLUMNS
            + """
            FROM orders o
            JOIN quote_revisions r
              ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
            WHERE o.id = %s
              AND EXISTS (
                  SELECT 1 FROM staff_store_assignments s
                  WHERE s.staff_user_id = %s
                    AND s.store_id = o.store_id
                    AND s.revoked_at IS NULL
              )
            """,
            (order_id, staff_user_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise OrderStateError("order is missing")
        return derive_intake_readiness(
            IntakeStatus(str(row[0])),
            QuoteReadinessFacts(bool(row[1]), bool(row[2]), bool(row[3]), bool(row[4])),
            slot_approved=slot_approved,
        )

    @staticmethod
    def list_for_store(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        limit: int = 100,
        open_only: bool = False,
        ticket: TicketReference | None = None,
    ) -> tuple[OrderView, ...]:
        """The store's orders, newest first, optionally narrowed to open ones or to one ticket.

        `open_only` is what keeps an order in play from falling off the board by age: at thirty
        orders a day the newest hundred is three days, and laundry is collected later than that.
        `ticket` answers the question the counter actually asks at pickup, "phiếu số 17".
        """

        _require_order_read(principal)
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=OrderAuthorizationError,
        )
        if not 1 <= limit <= MAX_BOARD_LIMIT:
            raise ValueError("order board limit must be between 1 and 200")
        if ticket is not None and ticket.number < 1:
            raise ValueError("a ticket number starts at 1")
        sql, parameters = _list_statement(
            store_id=store_id, limit=limit, open_only=open_only, ticket=ticket
        )
        cursor.execute(sql, parameters)
        return tuple(_order_view_row(row) for row in cursor.fetchall())

    @staticmethod
    def read_for_principal(cursor: Any, *, order_id: UUID, principal: StaffPrincipal) -> OrderView:
        """One order by id, for a caller who is a member of the store the order belongs to.

        The store is the row's, never the request's: this route carries no store in its path, and a
        client-supplied store would be exactly the authority `STORE-SCOPING-002` removed from the
        transition route. A missing order and another store's order raise the same error.
        """

        _require_order_read(principal)
        cursor.execute(_ORDER_VIEW_SELECT + " WHERE o.id = %(order_id)s", {"order_id": order_id})
        row = cursor.fetchone()
        if row is None:
            raise OrderNotVisibleError()
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=_uuid(row[1]),
            error=OrderNotVisibleError,
        )
        return _order_view_row(row)


@dataclass(frozen=True)
class _CancellationRefund:
    """The whole settled amount going back to the customer, as `DEC-024` resolves it."""

    refund_id: UUID
    #: `None` for a deposit refunded before the order was paid in full (`DEC-035`): there is no
    #: settlement to name, and `0056` binds the amount to the payment ledger instead.
    settlement_id: UUID | None
    store_id: UUID
    amount_vnd: int
    resolution: CustodyResolution

    def document(self) -> dict[str, object]:
        return {
            "refund_id": str(self.refund_id),
            "settlement_id": None if self.settlement_id is None else str(self.settlement_id),
            "refunded_amount_vnd": self.amount_vnd,
            "direction": "TO_CUSTOMER",
            "custody_resolution": self.resolution.value,
        }


def _refund_for_cancellation(
    connection: Any,
    *,
    order_id: UUID,
    resolution: CustodyResolution | None,
    partly_paid: bool = False,
) -> _CancellationRefund:
    """Read the settlement a refunding cancellation reverses, or refuse.

    Called only after the domain answered `REFUNDED`, which it does only for a resolution in
    `CUSTOMER_NOT_CHARGED_RESOLUTIONS` -- so both refusals here are states no command writes. An
    order that reads `PAID` with no settlement row means the books already disagree, and refunding
    against a guess would make that worse rather than visible.

    `partly_paid` (`DEC-035`): a deposit goes back through the same path, "up to what was paid" --
    exactly the payment ledger's sum, computed by PostgreSQL under the order lock the caller holds.
    There is no settlement to name; `0056` checks at commit that the refund equals the ledger.
    """

    if resolution is None:
        raise OrderStateError("HUMAN_APPROVAL_REQUIRED: a refund needs a custody resolution")
    if partly_paid:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT o.store_id, coalesce(sum(p.amount_vnd), 0)
                FROM orders o
                LEFT JOIN order_payments p ON p.order_id = o.id
                WHERE o.id = %s
                GROUP BY o.store_id
                """,
                (order_id,),
            )
            ledger = cursor.fetchone()
        if ledger is None or int(ledger[1]) <= 0:
            raise OrderStateError(
                "the order reads partly paid but no payment is recorded; nothing can be refunded"
            )
        return _CancellationRefund(
            refund_id=uuid4(),
            settlement_id=None,
            store_id=_uuid(ledger[0]),
            amount_vnd=int(ledger[1]),
            resolution=resolution,
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, store_id, paid_amount_vnd FROM order_settlements WHERE order_id = %s",
            (order_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise OrderStateError(
            "the order reads paid but no settlement records the payment; nothing can be refunded"
        )
    return _CancellationRefund(
        refund_id=uuid4(),
        settlement_id=_uuid(row[0]),
        store_id=_uuid(row[1]),
        amount_vnd=int(row[2]),
        resolution=resolution,
    )


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


def _order_view_row(row: tuple[object, ...]) -> OrderView:
    facts = _step_facts(row)
    return OrderView(
        order_id=_uuid(row[0]),
        store_id=_uuid(row[1]),
        commercial=CommercialOrderStatus(str(row[2])),
        intake=IntakeStatus(str(row[3])),
        production=ProductionStatus(str(row[4])),
        balance=OrderBalanceStatus(str(row[5])),
        row_version=int(str(row[6])),
        fulfillment_mode=FulfillmentMode(str(row[7])),
        created_at=_datetime(row[8]),
        quote_id=_uuid(row[9]),
        quote_revision=int(str(row[10])),
        payable_total_vnd=None if row[11] is None else int(str(row[11])),
        ticket_number=None if row[12] is None else int(str(row[12])),
        ticket_issued_on=_optional_date(row[13]),
        self_collection_recorded=bool(row[14]),
        acquisition_source=AcquisitionSource(str(row[15])),
        delivery_legs=_delivery_legs(row[22]),
        required_delivery_legs_succeeded=bool(row[16]),
        settlement_shape=None if row[21] is None else str(row[21]),
        next_steps=next_steps(facts),
        **_money_fields(row, facts),
    )


def _money_fields(row: tuple[object, ...], facts: StepFacts) -> dict[str, Any]:
    """`PAYMENT-001`: Tổng · Đã trả · Còn lại. The sum is SQL's; the rest is the domain's."""

    position = payment_position(owed_charges(facts.quoted_total), int(str(row[27])))
    try:
        payments, truncated = payment_views(row[28])
    except (KeyError, ValueError) as error:
        raise OrderStateError("stored payments are invalid") from error
    return {
        "charges": position.charges,
        "owed_vnd": position.owed_vnd,
        "paid_vnd": position.paid_vnd,
        "remaining_vnd": position.remaining_vnd,
        "payments": payments,
        "payments_truncated": truncated,
        "payment_may_hand_over": payment_may_hand_over(facts),
    }


def _read_view_row(connection: Any, order_id: UUID) -> tuple[object, ...]:
    """The order's read-model row, inside the caller's transaction and under its row lock."""

    with connection.cursor() as cursor:
        cursor.execute(_ORDER_VIEW_SELECT + " WHERE o.id = %(order_id)s", {"order_id": order_id})
        row = cursor.fetchone()
    if row is None:
        raise OrderStateError("STALE_VERSION: order is missing or stale")
    return tuple(row)


def _delivery_legs(value: object) -> tuple[DeliveryLegView, ...]:
    if not isinstance(value, list):
        raise OrderStateError("stored delivery legs are invalid")
    legs = []
    for item in value:
        if not isinstance(item, dict):
            raise OrderStateError("stored delivery leg is invalid")
        recorded_at = datetime.fromisoformat(str(item["recorded_at"]))
        legs.append(
            DeliveryLegView(
                leg_kind=str(item["leg_kind"]),
                outcome=str(item["outcome"]),
                recorded_at=_datetime(recorded_at),
            )
        )
    return tuple(legs)


def _step_facts(row: tuple[object, ...]) -> StepFacts:
    """The stored facts `order_steps` decides from, out of one `_ORDER_VIEW_SELECT` row."""

    legs = _delivery_legs(row[22])
    return StepFacts(
        state=OrderState(
            commercial=CommercialOrderStatus(str(row[2])),
            intake=IntakeStatus(str(row[3])),
            production=ProductionStatus(str(row[4])),
            fulfillment_mode=FulfillmentMode(str(row[7])),
            balance=OrderBalanceStatus(str(row[5])),
            required_delivery_legs_succeeded=bool(row[16]),
            self_collection_recorded=bool(row[14]),
            production_accepted_at=_optional_datetime(row[18]),
            production_resume_status=(None if row[17] is None else ProductionStatus(str(row[17]))),
        ),
        quote_readiness=QuoteReadinessFacts(
            bool(row[23]), bool(row[24]), bool(row[25]), bool(row[26])
        ),
        quoted_total=QuotedTotal(_optional_int(row[19]), _optional_int(row[20])),
        settlement_shape=None if row[21] is None else SettlementShape(str(row[21])),
        pickup_leg_succeeded=any(
            leg.leg_kind == "PICKUP" and leg.outcome == "SUCCEEDED" for leg in legs
        ),
    )


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _order_view_document(view: OrderView) -> dict[str, object]:
    """An order view as JSON primitives, for the idempotency ledger to store as a step's result."""

    return {
        "order_id": str(view.order_id),
        "store_id": str(view.store_id),
        "commercial": view.commercial.value,
        "intake": view.intake.value,
        "production": view.production.value,
        "balance": view.balance.value,
        "row_version": view.row_version,
        "fulfillment_mode": view.fulfillment_mode.value,
        "created_at": view.created_at.isoformat(),
        "quote_id": str(view.quote_id),
        "quote_revision": view.quote_revision,
        "payable_total_vnd": view.payable_total_vnd,
        "ticket_number": view.ticket_number,
        "ticket_issued_on": (
            None if view.ticket_issued_on is None else view.ticket_issued_on.isoformat()
        ),
        "self_collection_recorded": view.self_collection_recorded,
        "acquisition_source": view.acquisition_source.value,
        "delivery_legs": [
            {
                "leg_kind": leg.leg_kind,
                "outcome": leg.outcome,
                "recorded_at": leg.recorded_at.isoformat(),
            }
            for leg in view.delivery_legs
        ],
        "required_delivery_legs_succeeded": view.required_delivery_legs_succeeded,
        "settlement_shape": view.settlement_shape,
        "next_steps": [
            {
                "step": item.step.value,
                "primary": item.primary,
                "requires": list(item.requires),
                "custody_resolutions": [value.value for value in item.custody_resolutions],
                "rewash_reasons": [value.value for value in item.rewash_reasons],
                "rejection_reasons": [value.value for value in item.rejection_reasons],
            }
            for item in view.next_steps
        ],
        "charges": [
            {"kind": charge.kind.value, "amount_vnd": charge.amount_vnd} for charge in view.charges
        ],
        "owed_vnd": view.owed_vnd,
        "paid_vnd": view.paid_vnd,
        "remaining_vnd": view.remaining_vnd,
        "payments": [
            {
                "payment_id": str(item.payment_id),
                "amount_vnd": item.amount_vnd,
                "method": item.method,
                "bank_ref_last": item.bank_ref_last,
                "legacy": item.legacy,
                "recorded_at": item.recorded_at.isoformat(),
                "recorded_by_staff_id": str(item.recorded_by_staff_id),
                "recorded_by_name": item.recorded_by_name,
            }
            for item in view.payments
        ],
        "payments_truncated": view.payments_truncated,
        "payment_may_hand_over": view.payment_may_hand_over,
    }


def _order_view_from_document(document: dict[str, object]) -> OrderView:
    def text(key: str) -> str:
        return str(document[key])

    def optional_int(key: str) -> int | None:
        value = document[key]
        return None if value is None else int(str(value))

    legs = document["delivery_legs"]
    steps = document["next_steps"]
    if not isinstance(legs, list) or not isinstance(steps, list):
        raise ValueError("stored step result lists are invalid")
    issued_on = document["ticket_issued_on"]
    shape = document["settlement_shape"]
    return OrderView(
        order_id=UUID(text("order_id")),
        store_id=UUID(text("store_id")),
        commercial=CommercialOrderStatus(text("commercial")),
        intake=IntakeStatus(text("intake")),
        production=ProductionStatus(text("production")),
        balance=OrderBalanceStatus(text("balance")),
        row_version=int(text("row_version")),
        fulfillment_mode=FulfillmentMode(text("fulfillment_mode")),
        created_at=_datetime(datetime.fromisoformat(text("created_at"))),
        quote_id=UUID(text("quote_id")),
        quote_revision=int(text("quote_revision")),
        payable_total_vnd=optional_int("payable_total_vnd"),
        ticket_number=optional_int("ticket_number"),
        ticket_issued_on=None if issued_on is None else date.fromisoformat(str(issued_on)),
        self_collection_recorded=document["self_collection_recorded"] is True,
        acquisition_source=AcquisitionSource(text("acquisition_source")),
        delivery_legs=_delivery_legs(legs),
        required_delivery_legs_succeeded=document["required_delivery_legs_succeeded"] is True,
        settlement_shape=None if shape is None else str(shape),
        next_steps=tuple(
            NextStep(
                OrderStep(str(item["step"])),
                item["primary"] is True,
                tuple(str(value) for value in item["requires"]),
                tuple(CustodyResolution(str(value)) for value in item["custody_resolutions"]),
                # Absent from a result stored before ORDER-STEPS-002; such a result lists neither
                # step, so the empty tuple is what it said.
                tuple(RewashReason(str(value)) for value in item.get("rewash_reasons", ())),
                tuple(
                    IntakeRejectionReason(str(value)) for value in item.get("rejection_reasons", ())
                ),
            )
            for item in steps
        ),
        # `PAYMENT-001`. Absent from a result stored before it; such a reply said nothing about
        # payments, so it replays with nothing (null figures, no rows) rather than an invented 0.
        **_money_fields_from_document(document),
    )


def _money_fields_from_document(document: dict[str, object]) -> dict[str, Any]:
    charges = document.get("charges", [])
    payments = document.get("payments", [])
    if not isinstance(charges, list) or not isinstance(payments, list):
        raise ValueError("stored payment fields are invalid")
    views, _ = payment_views(payments)

    def optional_int(key: str) -> int | None:
        value = document.get(key)
        return None if value is None else int(str(value))

    return {
        "charges": tuple(
            OrderCharge(ChargeKind(str(item["kind"])), int(str(item["amount_vnd"])))
            for item in charges
        ),
        "owed_vnd": optional_int("owed_vnd"),
        "paid_vnd": optional_int("paid_vnd"),
        "remaining_vnd": optional_int("remaining_vnd"),
        "payments": views,
        "payments_truncated": document.get("payments_truncated") is True,
        "payment_may_hand_over": document.get("payment_may_hand_over") is True,
    }


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, date) or isinstance(value, datetime):
        raise OrderStateError("stored date is invalid")
    return value


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
