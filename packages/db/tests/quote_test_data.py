from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.approvals import ApprovalRepository, ApprovalRequestCommand
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import (
    AdjustmentDirection,
    ApprovalAction,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
    Unit,
)
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
