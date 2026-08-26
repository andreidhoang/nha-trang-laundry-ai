from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.approvals import ApprovalRepository, ApprovalRequestCommand
from nha_trang_laundry_db.quotes import (
    QuoteAcceptanceCommand,
    QuoteAcceptanceRepository,
    QuoteRepository,
    QuoteRevisionCommand,
)
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import (
    AdjustmentDirection,
    ApprovalAction,
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
PRICED_AT = datetime(2026, 8, 1, tzinfo=UTC)


def make_quote_snapshot(
    quote_id: UUID, revision: int, amount_vnd: int = 100_000
) -> ImmutableQuoteSnapshot:
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


def create_approval_envelope(
    connection: Any,
    *,
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
        ),
    )
    return stored.approval_request_id


def accepted_quote(
    connection: Any,
    *,
    store_id: UUID,
    staff_user_id: UUID,
    quote_id: UUID | None = None,
) -> tuple[UUID, int, ImmutableQuoteSnapshot]:
    """Price a revision and accept it the way production does, returning the orderable revision.

    Fixtures used to mint an `APPROVED_EXACT` revision 1 directly. Since `QUOTE-ACCEPT-001` an
    order requires an acceptance attestation naming the revision it produced, and a revision
    cannot be promoted in place -- so the real shape is revision 1 priced, an attestation, then
    revision 2 accepted.
    Building fixtures that way is not extra ceremony: an order fixture that could not exist in
    production would test a path no customer can reach.
    """

    identifier = quote_id or uuid4()
    estimate = make_quote_snapshot(identifier, 1)
    priced = build_quote_snapshot(
        replace(
            estimate.data,
            lines=tuple(
                replace(line, quantity_basis=QuantityBasis.STAFF_MEASUREMENT)
                for line in estimate.data.lines
            ),
        )
    )
    repository = QuoteRepository()
    repository.create_revision(
        connection,
        QuoteRevisionCommand(store_id, uuid4(), priced, 0, 0, staff_user_id, uuid4(), PRICED_AT),
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
            accepted_by=staff_user_id,
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
            store_id, uuid4(), composition.snapshot, 1, 1, staff_user_id, uuid4(), PRICED_AT
        ),
    )
    return identifier, 2, composition.snapshot
