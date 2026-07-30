from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from nha_trang_laundry_domain.canonical import (
    CanonicalizationError,
    canonical_document,
    canonical_document_from_bytes,
)
from nha_trang_laundry_domain.catalog import (
    AdjustmentDirection,
    PriceRuleType,
    PromotionEligibilityEvent,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
    Unit,
)
from nha_trang_laundry_domain.pricing import PriceLine, PriceRule, price_lines
from nha_trang_laundry_domain.quotes import (
    ConfigurationSnapshotReference,
    ExactLineAmounts,
    ImmutableQuoteSnapshot,
    QuoteAdjustmentKind,
    QuoteAdjustmentSnapshot,
    QuoteLineSnapshot,
    QuoteRevisionData,
    QuoteSnapshotError,
    QuoteTotalsSnapshot,
    RangeLineAmounts,
    build_quote_snapshot,
    capture_calculation_trace,
    verify_quote_snapshot,
)

QUOTE_ID = UUID("00000000-0000-0000-0000-000000000101")
SERVICE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000102")
PRICEBOOK_ID = UUID("00000000-0000-0000-0000-000000000103")
PROMOTION_ID = UUID("00000000-0000-0000-0000-000000000104")
APPROVAL_ID = UUID("00000000-0000-0000-0000-000000000105")
HASH_A = "JCS-SHA256-V1:" + "a" * 64
HASH_B = "JCS-SHA256-V1:" + "b" * 64
PRICED_AT = datetime(2026, 8, 1, 2, tzinfo=UTC)


def estimate_data() -> QuoteRevisionData:
    price = price_lines(
        {
            "TEST_SERVICE": PriceRule(
                "TEST_SERVICE", PriceRuleType.FIXED_PER_UNIT, unit_price_vnd=100_000
            )
        },
        (PriceLine("TEST_SERVICE", "1"),),
    )["TEST_SERVICE"]
    price_trace = capture_calculation_trace("PRICING", "pricing-v1", price.trace)
    promotion_trace = capture_calculation_trace(
        "PROMOTION",
        "promotion-v1",
        {"rate_bps": 3_000, "delivery_discount_vnd": 0},
    )
    line = QuoteLineSnapshot(
        line_id="line-b",
        service_code="TEST_SERVICE",
        service_version_id=SERVICE_VERSION_ID,
        quantity_basis=QuantityBasis.CUSTOMER_ESTIMATE,
        quantity="1",
        unit=Unit.KG,
        amounts=ExactLineAmounts("EXACT", 100_000, 100_000, 30_000, 70_000),
        price_trace_hash=price_trace.trace.snapshot_hash,
    )
    return QuoteRevisionData(
        schema_version=1,
        quote_id=QUOTE_ID,
        revision=1,
        finality=QuoteFinality.ESTIMATE,
        status=QuoteRevisionStatus.PROVISIONAL,
        priced_at=PRICED_AT,
        valid_until=PRICED_AT + timedelta(days=1),
        currency="VND",
        configuration_snapshots=(
            ConfigurationSnapshotReference("PROMOTION", PROMOTION_ID, 1, HASH_B),
            ConfigurationSnapshotReference("PRICEBOOK", PRICEBOOK_ID, 1, HASH_A),
        ),
        lines=(line,),
        adjustments=(
            QuoteAdjustmentSnapshot(
                "delivery",
                QuoteAdjustmentKind.DELIVERY,
                AdjustmentDirection.DEBIT,
                10_000,
                10_000,
                "DELIVERY_ZONE_MID",
            ),
            QuoteAdjustmentSnapshot(
                "promotion",
                QuoteAdjustmentKind.PROMOTION,
                AdjustmentDirection.CREDIT,
                30_000,
                30_000,
                "PROMOTION_WET_30",
                source_version_id=PROMOTION_ID,
            ),
        ),
        totals=QuoteTotalsSnapshot(
            100_000,
            100_000,
            30_000,
            30_000,
            70_000,
            70_000,
            10_000,
            0,
            80_000,
            80_000,
        ),
        calculation_traces=(promotion_trace, price_trace),
        calculation_engine_version="quote-engine-v1",
        calculation_engine_hash=canonical_document({"engine": "quote-engine-v1"}).snapshot_hash,
        promotion_eligibility_event=None,
        promotion_eligibility_at=None,
        reason_codes=("TAX_TREATMENT_UNVERIFIED", "PROMOTION_ELIGIBILITY_UNRESOLVED"),
        required_approvals=("TAX_TREATMENT_UNVERIFIED", "PROMOTION_ELIGIBILITY"),
        approval_id=None,
    )


def test_acknowledged_estimate_requires_timestamp_and_never_becomes_final() -> None:
    acknowledged_at = PRICED_AT + timedelta(minutes=1)
    acknowledged = build_quote_snapshot(
        replace(
            estimate_data(),
            status=QuoteRevisionStatus.ACKNOWLEDGED_ESTIMATE,
            customer_estimate_acknowledged_at=acknowledged_at,
        )
    )
    assert acknowledged.data.customer_estimate_acknowledged_at == acknowledged_at
    assert acknowledged.data.finality is QuoteFinality.ESTIMATE

    with pytest.raises(QuoteSnapshotError, match="acknowledgment status"):
        build_quote_snapshot(
            replace(estimate_data(), status=QuoteRevisionStatus.ACKNOWLEDGED_ESTIMATE)
        )
    with pytest.raises(QuoteSnapshotError, match="acknowledgment status"):
        build_quote_snapshot(
            replace(estimate_data(), customer_estimate_acknowledged_at=acknowledged_at)
        )


def test_quote_snapshot_is_order_independent_reproducible_and_self_verifying() -> None:
    data = estimate_data()
    first = build_quote_snapshot(data)
    second = build_quote_snapshot(
        replace(
            data,
            configuration_snapshots=tuple(reversed(data.configuration_snapshots)),
            adjustments=tuple(reversed(data.adjustments)),
            calculation_traces=tuple(reversed(data.calculation_traces)),
            reason_codes=tuple(reversed(data.reason_codes)),
        )
    )

    assert first == second
    assert first.document.snapshot_hash.startswith("JCS-SHA256-V1:")
    assert canonical_document_from_bytes(first.document.canonical_json) == first.document
    assert verify_quote_snapshot(first) is True


def test_snapshot_hash_changes_for_business_revision_and_old_snapshot_stays_unchanged() -> None:
    first = build_quote_snapshot(estimate_data())
    changed_data = replace(estimate_data(), revision=2, valid_until=PRICED_AT + timedelta(days=2))
    second = build_quote_snapshot(changed_data)

    assert first.document.snapshot_hash != second.document.snapshot_hash
    assert first.data.revision == 1
    assert first.data.valid_until == PRICED_AT + timedelta(days=1)


def test_old_quote_keeps_its_pricebook_snapshot_after_new_publication() -> None:
    first = build_quote_snapshot(estimate_data())
    references = tuple(
        replace(item, version=2, snapshot_hash=HASH_B) if item.config_type == "PRICEBOOK" else item
        for item in estimate_data().configuration_snapshots
    )
    second = build_quote_snapshot(
        replace(estimate_data(), revision=2, configuration_snapshots=references)
    )

    first_pricebook = next(
        item for item in first.data.configuration_snapshots if item.config_type == "PRICEBOOK"
    )
    second_pricebook = next(
        item for item in second.data.configuration_snapshots if item.config_type == "PRICEBOOK"
    )
    assert (first_pricebook.version, first_pricebook.snapshot_hash) == (1, HASH_A)
    assert (second_pricebook.version, second_pricebook.snapshot_hash) == (2, HASH_B)
    assert first.document.snapshot_hash != second.document.snapshot_hash


def test_snapshot_and_nested_data_are_frozen() -> None:
    snapshot = build_quote_snapshot(estimate_data())

    with pytest.raises(FrozenInstanceError):
        snapshot.data.revision = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        snapshot.document.snapshot_hash = HASH_A  # type: ignore[misc]


def test_estimate_cannot_become_final_by_status_or_quantity_basis_edit() -> None:
    data = estimate_data()
    with pytest.raises(QuoteSnapshotError, match="estimate cannot become approved final"):
        build_quote_snapshot(replace(data, status=QuoteRevisionStatus.ACCEPTED_FINAL))

    measured_line = replace(data.lines[0], quantity_basis=QuantityBasis.STAFF_MEASUREMENT)
    with pytest.raises(QuoteSnapshotError, match="approved exact quote lacks final evidence"):
        build_quote_snapshot(
            replace(
                data,
                lines=(measured_line,),
                finality=QuoteFinality.APPROVED_EXACT,
                status=QuoteRevisionStatus.APPROVED,
                approval_id=APPROVAL_ID,
                required_approvals=(),
            )
        )


def test_human_approved_exact_requires_resolved_promotion_and_no_pending_approvals() -> None:
    data = estimate_data()
    measured_line = replace(data.lines[0], quantity_basis=QuantityBasis.STAFF_MEASUREMENT)
    exact = replace(
        data,
        revision=2,
        lines=(measured_line,),
        finality=QuoteFinality.APPROVED_EXACT,
        status=QuoteRevisionStatus.APPROVED,
        approval_id=APPROVAL_ID,
        required_approvals=(),
        promotion_eligibility_event=PromotionEligibilityEvent.PRODUCTION_ACCEPTED,
        promotion_eligibility_at=PRICED_AT,
    )

    snapshot = build_quote_snapshot(exact)
    assert snapshot.data.finality is QuoteFinality.APPROVED_EXACT
    assert snapshot.data.approval_id == APPROVAL_ID


def test_range_revision_cannot_be_customer_accepted_as_final() -> None:
    data = estimate_data()
    price_trace = data.calculation_traces[1]
    range_line = replace(
        data.lines[0],
        amounts=RangeLineAmounts(
            "RANGE", 30_000, 90_000, 30_000, 90_000, 9_000, 27_000, 21_000, 63_000
        ),
        price_trace_hash=price_trace.trace.snapshot_hash,
    )
    promotion = replace(data.adjustments[1], amount_min_vnd=9_000, amount_max_vnd=27_000)
    range_data = replace(
        data,
        finality=QuoteFinality.RANGE,
        status=QuoteRevisionStatus.REVIEW_REQUIRED,
        lines=(range_line,),
        adjustments=(data.adjustments[0], promotion),
        totals=QuoteTotalsSnapshot(
            30_000, 90_000, 9_000, 27_000, 21_000, 63_000, 10_000, 0, 31_000, 73_000
        ),
    )
    assert build_quote_snapshot(range_data).data.finality is QuoteFinality.RANGE

    with pytest.raises(QuoteSnapshotError, match="range quote cannot be accepted as final"):
        build_quote_snapshot(replace(range_data, status=QuoteRevisionStatus.ACCEPTED_FINAL))


def test_delivery_is_a_separate_debit_and_never_hidden_in_discount() -> None:
    data = estimate_data()
    assert build_quote_snapshot(data).data.totals.display_total_min_vnd == 80_000

    wrong = replace(data.totals, discount_amount_min_vnd=40_000)
    with pytest.raises(QuoteSnapshotError, match="totals do not match line snapshots"):
        build_quote_snapshot(replace(data, totals=wrong))


def test_missing_trace_unresolved_delivery_and_noncanonical_quantity_fail_closed() -> None:
    data = estimate_data()
    with pytest.raises(QuoteSnapshotError, match="not embedded"):
        build_quote_snapshot(replace(data, calculation_traces=data.calculation_traces[:1]))

    unresolved_totals = replace(
        data.totals,
        delivery_fee_vnd=None,
        display_total_min_vnd=None,
        display_total_max_vnd=None,
    )
    no_delivery_adjustment = tuple(
        item for item in data.adjustments if item.kind is not QuoteAdjustmentKind.DELIVERY
    )
    unresolved = build_quote_snapshot(
        replace(data, totals=unresolved_totals, adjustments=no_delivery_adjustment)
    )
    assert unresolved.data.totals.display_total_min_vnd is None

    with pytest.raises(QuoteSnapshotError, match="normalized without rounding"):
        build_quote_snapshot(replace(data, lines=(replace(data.lines[0], quantity="1.000"),)))


def test_canonical_document_rejects_floats_and_tampered_hash() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_document({"money": cast(object, 1.5)})

    snapshot = build_quote_snapshot(estimate_data())
    tampered = ImmutableQuoteSnapshot(
        snapshot.data,
        replace(snapshot.document, snapshot_hash=HASH_A),
    )
    assert verify_quote_snapshot(tampered) is False
