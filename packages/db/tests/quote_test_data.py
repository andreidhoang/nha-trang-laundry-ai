from datetime import UTC, datetime, timedelta
from uuid import UUID

from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import (
    AdjustmentDirection,
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
