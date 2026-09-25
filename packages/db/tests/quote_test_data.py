from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.approvals import ApprovalRepository, ApprovalRequestCommand
from nha_trang_laundry_db.counter_tickets import CounterTicketRepository
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.quotes import (
    QuoteAcceptanceCommand,
    QuoteAcceptanceRepository,
    QuoteRepository,
    QuoteRevisionCommand,
)
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import (
    AdjustmentDirection,
    ApprovalAction,
    FulfillmentMode,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
    Unit,
)
from nha_trang_laundry_domain.quote_composition import ComposedQuote, accept_quote_revision
from nha_trang_laundry_domain.quotes import (
    ConfigurationSnapshotReference,
    ExactLineAmounts,
    ImmutableQuoteSnapshot,
    QuoteAdjustmentKind,
    QuoteAdjustmentSnapshot,
    QuoteLineSnapshot,
    QuoteRevisionData,
    QuoteTotalsSnapshot,
    build_quote_snapshot,
    capture_calculation_trace,
)

PRICEBOOK_ID = UUID("00000000-0000-0000-0000-000000000201")
SERVICE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000202")
# COUNTER-DEFECTS-001. This was a fixed calendar date, and `QUOTE_VALIDITY` is one day -- so every
# quote this module built had expired long before any test ran it. Nothing noticed, because expiry
# was decided against a timestamp the caller supplied and the fixture supplied one inside the
# window. Once the server's own clock decides, a fixed past date builds a quote no shop could sell
# from. It is priced a few minutes ago instead, which is what a quote at a counter is.
PRICED_AT = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)


def ensure_store(connection: Any, store_id: UUID, *, at: datetime | None = None) -> None:
    """A store must exist before anything can belong to it.

    Fixtures used to mint a bare UUID and hand it to `staff_store_assignments`, which is what
    production could never do and now cannot: `STORE-REGISTRY-001` gave `store_id` a table and a
    foreign key. The deploy-day runbook creates the store with `scripts/bootstrap_store.py` before
    anyone is assigned to it, so the fixture calls the same repository rather than an INSERT of its
    own -- a fixture that builds a shape production cannot has caught this repository out four
    times already.
    """

    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
        occurred_at=at or PRICED_AT,
    )


@dataclass(frozen=True)
class FixtureLine:
    """One exactly priced line for a multi-line fixture, as the snapshot will record it.

    `DEC-031` made the unit and the recorded unit price matter to a remedy ceiling, so a test about
    three shirts or a bag by weight has to be able to say so. The amounts are stated, not computed:
    `unit_price_vnd` may be `None`, as it is on a band closed at the counter.
    """

    line_id: str
    service_code: str
    unit: Unit
    quantity: str
    unit_price_vnd: int | None
    amount_vnd: int


def make_quote_snapshot(
    quote_id: UUID,
    revision: int,
    amount_vnd: int = 100_000,
    *,
    lines: tuple[FixtureLine, ...] | None = None,
    pricebook: ConfigurationSnapshotReference | None = None,
) -> ImmutableQuoteSnapshot:
    if lines is not None:
        return _multi_line_snapshot(quote_id, revision, lines, pricebook)
    trace = capture_calculation_trace(
        "PRICING", "pricing-v1", {"list_amount_vnd": amount_vnd, "rounding": "NONE"}
    )
    line = QuoteLineSnapshot(
        "line-1",
        "TEST_SERVICE",
        SERVICE_VERSION_ID,
        QuantityBasis.CUSTOMER_ESTIMATE,
        "1",
        Unit.ITEM,
        ExactLineAmounts("EXACT", amount_vnd, amount_vnd, 0, amount_vnd),
        trace.trace.snapshot_hash,
    )
    delivery = QuoteAdjustmentSnapshot(
        "delivery",
        QuoteAdjustmentKind.DELIVERY,
        AdjustmentDirection.DEBIT,
        10_000,
        10_000,
        "DELIVERY_ZONE_MID",
    )
    data = QuoteRevisionData(
        1,
        quote_id,
        revision,
        QuoteFinality.ESTIMATE,
        QuoteRevisionStatus.REVIEW_REQUIRED,
        PRICED_AT,
        PRICED_AT + timedelta(days=1),
        "VND",
        (
            ConfigurationSnapshotReference(
                "PRICEBOOK", PRICEBOOK_ID, 1, "JCS-SHA256-V1:" + "a" * 64
            ),
        ),
        (line,),
        (delivery,),
        QuoteTotalsSnapshot(
            amount_vnd,
            amount_vnd,
            0,
            0,
            amount_vnd,
            amount_vnd,
            10_000,
            0,
            amount_vnd + 10_000,
            amount_vnd + 10_000,
        ),
        (trace,),
        "quote-engine-v1",
        canonical_document({"engine": "quote-engine-v1"}).snapshot_hash,
        None,
        None,
        ("TAX_TREATMENT_UNVERIFIED",),
        ("TAX_TREATMENT_UNVERIFIED", "SLOT_CONFIRMATION"),
        None,
    )
    return build_quote_snapshot(data)


def _multi_line_snapshot(
    quote_id: UUID,
    revision: int,
    lines: tuple[FixtureLine, ...],
    pricebook: ConfigurationSnapshotReference | None,
) -> ImmutableQuoteSnapshot:
    """The same estimate as above with several stated lines, each with its own pricing trace."""

    traces = tuple(
        capture_calculation_trace(
            f"PRICING_{index}",
            "pricing-v1",
            {"line_id": item.line_id, "list_amount_vnd": item.amount_vnd, "rounding": "NONE"},
        )
        for index, item in enumerate(lines)
    )
    snapshot_lines = tuple(
        QuoteLineSnapshot(
            item.line_id,
            item.service_code,
            SERVICE_VERSION_ID,
            QuantityBasis.CUSTOMER_ESTIMATE,
            item.quantity,
            item.unit,
            ExactLineAmounts("EXACT", item.unit_price_vnd, item.amount_vnd, 0, item.amount_vnd),
            trace.trace.snapshot_hash,
        )
        for item, trace in zip(lines, traces, strict=True)
    )
    subtotal = sum(item.amount_vnd for item in lines)
    delivery = QuoteAdjustmentSnapshot(
        "delivery",
        QuoteAdjustmentKind.DELIVERY,
        AdjustmentDirection.DEBIT,
        10_000,
        10_000,
        "DELIVERY_ZONE_MID",
    )
    data = QuoteRevisionData(
        1,
        quote_id,
        revision,
        QuoteFinality.ESTIMATE,
        QuoteRevisionStatus.REVIEW_REQUIRED,
        PRICED_AT,
        PRICED_AT + timedelta(days=1),
        "VND",
        (
            pricebook
            or ConfigurationSnapshotReference(
                "PRICEBOOK", PRICEBOOK_ID, 1, "JCS-SHA256-V1:" + "a" * 64
            ),
        ),
        snapshot_lines,
        (delivery,),
        QuoteTotalsSnapshot(
            subtotal,
            subtotal,
            0,
            0,
            subtotal,
            subtotal,
            10_000,
            0,
            subtotal + 10_000,
            subtotal + 10_000,
        ),
        traces,
        "quote-engine-v1",
        canonical_document({"engine": "quote-engine-v1"}).snapshot_hash,
        None,
        None,
        ("TAX_TREATMENT_UNVERIFIED",),
        ("TAX_TREATMENT_UNVERIFIED", "SLOT_CONFIRMATION"),
        None,
    )
    return build_quote_snapshot(data)


def create_approval_envelope(
    connection: Any,
    *,
    store_id: UUID,
    requested_by: UUID,
    resource_id: UUID | None = None,
    requested_at: datetime | None = None,
) -> UUID:
    """Create a real approval envelope and return its id.

    Migration `0029` gives `quote_revisions.approval_id` a foreign key, so a fixture that wants an
    `APPROVED_EXACT` revision has to earn an envelope rather than invent a UUID. Inventing one is
    what these fixtures used to do, and what `synthetic_incidents.py` did in shipped source: the
    result was a revision citing an approval that had never been requested by anyone.

    The action is `PRESENT_QUOTE` because `APPROVAL_RESOURCE_TYPES` binds that one to
    `QUOTE_REVISION`, so it is the pair a caller can form about a quote without inventing
    anything. It is deliberately not a claim about which approval *finalises* a quote --
    `DEC-021` owns that question, the foreign key constrains neither `action` nor `resource_id`,
    and when the decision lands this helper is where the real binding belongs.
    """

    # The requester must belong to the store the approval names (migration 0034), so the fixture
    # seeds what production requires rather than the check being relaxed to fit it.
    ensure_store(connection, store_id, at=requested_at)
    assigner = uuid4()
    with connection.cursor() as cursor:
        parties = ((requested_by, f"oidc-{requested_by}"), (assigner, f"oidc-{assigner}"))
        for ident, subject in parties:
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (ident, subject, requested_at or PRICED_AT),
            )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            ON CONFLICT DO NOTHING
            """,
            (requested_by, store_id, assigner, requested_at or PRICED_AT),
        )

    moment = requested_at or PRICED_AT
    stored = ApprovalRepository().request(
        connection,
        ApprovalRequestCommand(
            ApprovalAction.PRESENT_QUOTE,
            "QUOTE_REVISION",
            resource_id or uuid4(),
            1,
            "JCS-SHA256-V1:" + "a" * 64,
            "JCS-SHA256-V1:" + "b" * 64,
            "quote-approval-fixture-v1",
            requested_by,
            f"quote-approval-fixture-{uuid4().hex}",
            uuid4(),
            moment,
            store_id=store_id,
        ),
    )
    return stored.approval_request_id


def accepted_quote(
    connection: Any,
    *,
    store_id: UUID,
    principal: StaffPrincipal,
    quote_id: UUID | None = None,
    fulfillment_mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
    ticket_issued_at: datetime | None = None,
    lines: tuple[FixtureLine, ...] | None = None,
    pricebook: ConfigurationSnapshotReference | None = None,
) -> tuple[UUID, int, ImmutableQuoteSnapshot, UUID]:
    """Price a revision and accept it the way production does, returning the orderable revision.

    Fixtures used to mint an `APPROVED_EXACT` revision 1 directly. Since `QUOTE-ACCEPT-001` an
    order requires an acceptance attestation naming the revision it produced, and a revision
    cannot be promoted in place -- so the real shape is revision 1 priced, an attestation, then
    revision 2 accepted.
    Building fixtures that way is not extra ceremony: an order fixture that could not exist in
    production would test a path no customer can reach.
    """

    identifier = quote_id or uuid4()
    ensure_store(connection, store_id)
    # A real intake request bound to a real counter ticket. `OrderRepository.create` checks that the
    # order's customer is the customer the quote was priced for, reached through this request, so a
    # fixture that invents a `bound_order_request_id` builds a chain no customer could walk.
    contact_id = counter_ticket(
        connection, store_id=store_id, principal=principal, issued_at=ticket_issued_at
    )
    request_id = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO order_requests (
                id, store_id, contact_binding_id, conversation_binding_id, status, row_version,
                created_at
            ) VALUES (%s, %s, %s, %s, 'SUBMITTED', 1, %s)
            """,
            (request_id, store_id, contact_id, uuid4(), PRICED_AT),
        )
    estimate = make_quote_snapshot(identifier, 1, lines=lines, pricebook=pricebook)
    priced = build_quote_snapshot(
        replace(
            estimate.data,
            lines=tuple(
                replace(line, quantity_basis=QuantityBasis.STAFF_MEASUREMENT)
                for line in estimate.data.lines
            ),
            # A DELIVERY trace, because every revision the composer produces has one and
            # `OrderRepository.create` reads the priced fulfilment mode out of it. A fixture without
            # one reads as "priced under no mode at all" and is refused -- correctly, since no real
            # quote can be in that state. Only the field the guard reads is populated; this is not a
            # substitute for the engine's own trace.
            calculation_traces=(
                *estimate.data.calculation_traces,
                capture_calculation_trace(
                    "DELIVERY",
                    "delivery-v1",
                    {"fulfillment_mode": str(fulfillment_mode), "fee_rule": "FIXTURE"},
                ),
            ),
            # No outstanding approvals, because a revision that has one cannot be accepted.
            # `make_quote_snapshot` puts two on every estimate it builds, and this fixture used to
            # carry them all the way to an accepted revision anyway: `accept_quote_revision` passed
            # `required_approvals=()` into the assembly, which satisfied
            # `quotes._validate_finality`'s `APPROVED_EXACT` gate by deleting the tuple the gate
            # reads instead of by emptying it honestly. No production path populates the field today
            # -- the promotion withholds a discount rather than asking for an envelope nobody can
            # supply -- so the blanking harmed nothing in practice, and that is precisely why it
            # survived: a guard is not allowed to be correct only because nothing has reached it.
            #
            # Acceptance refuses now, so the fixture has to state the precondition production
            # states: nothing is outstanding when the customer says yes. The two codes are therefore
            # cleared *before* revision 1 is stored, which means neither revision this fixture
            # writes carries one -- it is a fixture for the orderable path, and the orderable path
            # has none. A stored revision that does carry them, and the refusal it earns, is
            # `test_postgres_integration.py`'s
            # `test_a_stored_revision_with_an_outstanding_approval_cannot_be_accepted`.
            required_approvals=(),
        )
    )
    repository = QuoteRepository()
    repository.create_revision(
        connection,
        QuoteRevisionCommand(
            store_id, request_id, priced, 0, 0, principal.staff_user_id, uuid4(), PRICED_AT
        ),
    )
    QuoteAcceptanceRepository().record(
        connection,
        QuoteAcceptanceCommand(
            store_id=store_id,
            quote_id=identifier,
            accepted_revision=1,
            accepted_snapshot_hash=priced.document.snapshot_hash,
            final_revision=2,
            display_total_vnd=priced.data.totals.display_total_min_vnd or 0,
            accepted_by=principal.staff_user_id,
            correlation_id=uuid4(),
            policy_version="quote-acceptance-dec-021-v1",
            accepted_at=PRICED_AT,
        ),
    )
    composition = accept_quote_revision(priced=priced, revision=2)
    assert isinstance(composition, ComposedQuote), "the fixture must produce an acceptable quote"
    repository.create_revision(
        connection,
        QuoteRevisionCommand(
            store_id,
            request_id,
            composition.snapshot,
            1,
            1,
            principal.staff_user_id,
            uuid4(),
            PRICED_AT,
        ),
    )
    return identifier, 2, composition.snapshot, contact_id


def counter_ticket(
    connection: Any,
    *,
    store_id: UUID,
    principal: StaffPrincipal,
    issued_at: datetime | None = None,
) -> UUID:
    """Issue a walk-in ticket and return the reference an order carries.

    Since `COUNTER-TICKET-001` an order's `bound_contact_id` must name a ticket this store
    issued or a channel binding that exists. Fixtures used to pass `uuid4()`, which the schema
    accepted because the column had no foreign key and the guard never looked -- the same hole
    `approval_id` carried until 0029.
    """

    return (
        CounterTicketRepository()
        .issue(
            connection,
            store_id=store_id,
            principal=principal,
            correlation_id=uuid4(),
            issued_at=issued_at,
        )
        .ticket_id
    )


def bulk_newer_orders(
    connection: Any,
    store_id: UUID,
    staff: StaffPrincipal,
    *,
    count: int,
    after: datetime,
    commercial: str,
    contact_id: UUID | None = None,
) -> None:
    """`count` order rows created after `after`, reusing one real quote chain.

    The `test_ops_board` technique: the rows satisfy every constraint and foreign key, and skip the
    transition history no read in these tests looks at. `contact_id` overrides the customer
    reference, for a row bound to something other than the fixture's counter ticket.
    """
    quote_id, revision, quote, ticket_id = accepted_quote(
        connection, store_id=store_id, principal=staff
    )
    bound = contact_id or ticket_id
    with (
        connection.transaction(),
        connection.cursor() as cursor,
        cursor.copy(
            """
            COPY orders (
                id, store_id, bound_contact_id, current_quote_id, current_quote_revision,
                current_quote_snapshot_hash, commercial_status, intake_status, production_status,
                fulfillment_mode, balance_status, customer_final_quote_accepted_at,
                row_version, created_at, acquisition_source
            ) FROM STDIN
            """
        ) as copy,
    ):
        for index in range(count):
            created = after + timedelta(minutes=index + 1)
            copy.write_row(
                (
                    uuid4(),
                    store_id,
                    bound,
                    quote_id,
                    revision,
                    quote.document.snapshot_hash,
                    commercial,
                    "AWAITING_HANDOFF",
                    "NOT_STARTED",
                    "SELF_DROP_SELF_COLLECT",
                    "UNPAID",
                    created,
                    1,
                    created,
                    "WALK_IN",
                )
            )
