"""PostgreSQL-backed quote revision, invalidation, and acknowledgment preflights."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import rfc8785
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalExecutionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteRevisionCommand
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import (
    ActorRole,
    AdjustmentDirection,
    ApprovalAction,
    FulfillmentMode,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
    Unit,
)
from nha_trang_laundry_domain.delivery import DeliveryVehicle, evaluate_delivery
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv, runtime_price_rules
from nha_trang_laundry_domain.pricing import PriceLine, price_lines
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

from .fixtures import SyntheticFixtureBundle
from .synthetic_pricing import ROOT


@dataclass(frozen=True, slots=True)
class SyntheticQuoteLifecyclePreflight:
    assertion_results: Mapping[str, bool]
    trace_id: str
    tool_trace: tuple[Mapping[str, object], ...] = ()
    side_effects: tuple[str, ...] = ()


def execute_tax_unverified_preflight(
    fixture: SyntheticFixtureBundle,
) -> SyntheticQuoteLifecyclePreflight:
    seed = _seed(fixture)
    snapshot = _snapshot(
        uuid4(),
        revision=1,
        quantity=_text(seed, "quantity_kg"),
        basis=QuantityBasis.STAFF_MEASUREMENT,
        status=QuoteRevisionStatus.REVIEW_REQUIRED,
        priced_at=_clock(fixture.payload),
        pricebook_version_id=uuid4(),
        service_version_id=uuid4(),
    )
    data = snapshot.data
    tax_unresolved = data.tax_treatment == "UNVERIFIED" and data.tax_vnd is None
    return SyntheticQuoteLifecyclePreflight(
        {
            "ASSERT_4162B28467EA1D00": (
                data.finality is QuoteFinality.ESTIMATE
                and data.status is QuoteRevisionStatus.REVIEW_REQUIRED
            ),
            "ASSERT_701C4AC98D18DFC5": tax_unresolved
            and data.finality is not QuoteFinality.APPROVED_EXACT,
            "ASSERT_4094D60FB3416AA6": ("TAX_TREATMENT_UNVERIFIED" in data.required_approvals),
        },
        _event_id(fixture),
    )


def execute_personalized_price_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticQuoteLifecyclePreflight:
    seed = _seed(fixture)
    timestamp = _clock(fixture.payload)
    quote_id, store_id, request_id, actor_id = uuid4(), uuid4(), uuid4(), uuid4()
    snapshot = _snapshot(
        quote_id,
        revision=1,
        quantity=_text(seed, "quantity_kg"),
        basis=QuantityBasis.CUSTOMER_ESTIMATE,
        status=QuoteRevisionStatus.REVIEW_REQUIRED,
        priced_at=timestamp,
        pricebook_version_id=uuid4(),
        service_version_id=uuid4(),
    )
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(store_id, request_id, snapshot, 0, 0, actor_id, uuid4(), timestamp),
    )
    rendered = canonical_document(
        {
            "quote_id": str(quote_id),
            "revision": 1,
            "display_total_vnd": snapshot.data.totals.display_total_max_vnd,
        }
    ).snapshot_hash
    approval = ApprovalRepository().request(
        connection,
        ApprovalRequestCommand(
            ApprovalAction.PRESENT_QUOTE,
            "QUOTE_REVISION",
            quote_id,
            1,
            snapshot.document.snapshot_hash,
            rendered,
            "personalized-quote-assisted-v1",
            actor_id,
            f"personalized-quote-{uuid4().hex}",
            uuid4(),
            timestamp,
        ),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ar.snapshot_hash, ar.rendered_hash, ars.status,
                   (SELECT count(*) FROM approval_executions ae
                    WHERE ae.approval_request_id = ar.id)
            FROM approval_requests ar
            JOIN approval_request_states ars ON ars.approval_request_id = ar.id
            WHERE ar.id = %s
            """,
            (approval.approval_request_id,),
        )
        row = cursor.fetchone()
    trace_id = _event_id(fixture)
    quote_arguments = {
        "lines": [{"quantity": _text(seed, "quantity_kg"), "unit": "KG"}],
        "fulfillment": {"mode": "PICKUP_AND_RETURN"},
    }
    approval_arguments = {
        "action": "PRESENT_QUOTE",
        "resource_id": str(quote_id),
        "resource_version": 1,
        "snapshot_hash": snapshot.document.snapshot_hash,
    }
    return SyntheticQuoteLifecyclePreflight(
        {
            "ASSERT_823B0B30D498E01B": row[2] == "REQUESTED" and int(row[3]) == 0,
            "ASSERT_53A4500F6420D84A": (
                row[0] == snapshot.document.snapshot_hash and row[1] == rendered
            ),
        },
        trace_id,
        (
            _tool_trace(1, "quoteEstimate", quote_arguments, trace_id),
            _tool_trace(2, "approvalRequestCreate", approval_arguments, trace_id),
        ),
        ("QUOTE_ESTIMATE_REVISION_CREATED", "APPROVAL_REQUEST_CREATED"),
    )


def execute_measurement_change_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticQuoteLifecyclePreflight:
    seed = _seed(fixture)
    timestamp = _clock(fixture.payload)
    quote_id, store_id, request_id, actor_id = uuid4(), uuid4(), uuid4(), uuid4()
    pricebook_version_id, service_version_id = uuid4(), uuid4()
    first = _snapshot(
        quote_id,
        revision=1,
        quantity=_text(seed, "prior_quantity_kg"),
        basis=QuantityBasis.CUSTOMER_ESTIMATE,
        status=QuoteRevisionStatus.REVIEW_REQUIRED,
        priced_at=timestamp,
        pricebook_version_id=pricebook_version_id,
        service_version_id=service_version_id,
    )
    repository = QuoteRepository()
    repository.create_revision(
        connection,
        QuoteRevisionCommand(store_id, request_id, first, 0, 0, actor_id, uuid4(), timestamp),
    )
    rendered_one = canonical_document({"quote_id": str(quote_id), "revision": 1}).snapshot_hash
    approvals = ApprovalRepository()
    approval = approvals.request(
        connection,
        ApprovalRequestCommand(
            ApprovalAction.SET_DELIVERY_FEE,
            "DELIVERY_FEE_PROPOSAL",
            quote_id,
            1,
            first.document.snapshot_hash,
            rendered_one,
            "delivery-policy-v1",
            actor_id,
            f"measurement-approval-{uuid4().hex}",
            uuid4(),
            timestamp,
        ),
    )
    approver = StaffPrincipal(
        uuid4(), f"synthetic-approver-{uuid4().hex}", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    approvals.decide(
        connection,
        ApprovalDecisionCommand(
            approval.approval_request_id,
            ApprovalDecision.APPROVED,
            1,
            first.document.snapshot_hash,
            rendered_one,
            "SYNTHETIC_HUMAN_REVIEW",
            approver,
            uuid4(),
            timestamp + timedelta(seconds=10),
        ),
    )
    second = _snapshot(
        quote_id,
        revision=2,
        quantity=_text(seed, "staff_measurement_kg"),
        basis=QuantityBasis.STAFF_MEASUREMENT,
        status=QuoteRevisionStatus.REVIEW_REQUIRED,
        priced_at=timestamp + timedelta(seconds=20),
        pricebook_version_id=pricebook_version_id,
        service_version_id=service_version_id,
    )
    repository.create_revision(
        connection,
        QuoteRevisionCommand(
            store_id,
            request_id,
            second,
            1,
            1,
            actor_id,
            uuid4(),
            timestamp + timedelta(seconds=20),
        ),
    )
    rendered_two = canonical_document({"quote_id": str(quote_id), "revision": 2}).snapshot_hash
    invalidated = False
    try:
        approvals.claim_execution(
            connection,
            ApprovalExecutionCommand(
                approval.approval_request_id,
                ActorRole.OUTBOX_WORKER,
                2,
                second.document.snapshot_hash,
                rendered_two,
                "delivery-policy-v1",
                uuid4(),
                timestamp + timedelta(seconds=30),
            ),
        )
    except ApprovalStateError as error:
        invalidated = "stale" in str(error)
    delivery = evaluate_delivery(
        mode=FulfillmentMode.PICKUP_AND_RETURN,
        verified_distance_m=int(seed["verified_distance_m"]),
        planned_transport_weight_kg=_text(seed, "staff_measurement_kg"),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_revision FROM quotes WHERE id = %s",
            (quote_id,),
        )
        current_revision = int(cursor.fetchone()[0])
        cursor.execute(
            "SELECT count(*) FROM approval_executions WHERE approval_request_id = %s",
            (approval.approval_request_id,),
        )
        executions = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT count(*) FROM quote_revisions
            WHERE quote_id = %s AND status = 'ACCEPTED_FINAL'
            """,
            (quote_id,),
        )
        accepted_final = int(cursor.fetchone()[0])
    return SyntheticQuoteLifecyclePreflight(
        {
            "ASSERT_3C848C4C82F09CF4": current_revision == 2,
            "ASSERT_F384FE2C4E0ED46A": invalidated and executions == 0,
            "ASSERT_D95CE7BB838C09FA": (delivery.vehicle_recommendation is DeliveryVehicle.CAR),
            "ASSERT_550C92EA0CD57A6C": accepted_final == 0,
        },
        _event_id(fixture),
    )


def execute_estimate_acknowledgment_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticQuoteLifecyclePreflight:
    seed = _seed(fixture)
    timestamp = _clock(fixture.payload)
    acknowledged_at = datetime.fromisoformat(_text(seed, "acknowledged_at").replace("Z", "+00:00"))
    quote_id, store_id, request_id, actor_id = uuid4(), uuid4(), uuid4(), uuid4()
    snapshot = _snapshot(
        quote_id,
        revision=1,
        quantity=_text(seed, "quantity_kg"),
        basis=QuantityBasis.CUSTOMER_ESTIMATE,
        status=QuoteRevisionStatus.ACKNOWLEDGED_ESTIMATE,
        priced_at=timestamp,
        acknowledged_at=acknowledged_at,
        pricebook_version_id=uuid4(),
        service_version_id=uuid4(),
    )
    QuoteRepository().create_revision(
        connection,
        QuoteRevisionCommand(store_id, request_id, snapshot, 0, 0, actor_id, uuid4(), timestamp),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT finality, status, customer_estimate_acknowledged_at
            FROM quote_revisions WHERE quote_id = %s AND revision = 1
            """,
            (quote_id,),
        )
        row = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM orders WHERE current_quote_id = %s", (quote_id,))
        active_orders = int(cursor.fetchone()[0])
    return SyntheticQuoteLifecyclePreflight(
        {
            "ASSERT_31D7E5EA1D1883DD": row[2] is not None,
            "ASSERT_550C92EA0CD57A6C": row[0] == "ESTIMATE" and row[1] != "ACCEPTED_FINAL",
            "ASSERT_15C73B10FCE65AF9": active_orders == 0,
        },
        _event_id(fixture),
    )


def _snapshot(
    quote_id: UUID,
    *,
    revision: int,
    quantity: str,
    basis: QuantityBasis,
    status: QuoteRevisionStatus,
    priced_at: datetime,
    pricebook_version_id: UUID,
    service_version_id: UUID,
    acknowledged_at: datetime | None = None,
) -> ImmutableQuoteSnapshot:
    pricebook = import_pricebook_csv((ROOT / "templates/services-pricebook.csv").read_bytes())
    price = price_lines(
        runtime_price_rules(pricebook),
        (PriceLine("STANDARD_WASH_DRY", quantity, Unit.KG, basis),),
    )["STANDARD_WASH_DRY"]
    if price.list_amount_vnd is None:
        raise ValueError("standard wash price must be exact")
    trace = capture_calculation_trace("PRICING", "pricing-v1", price.trace)
    amount = price.list_amount_vnd
    line = QuoteLineSnapshot(
        "line-1",
        "STANDARD_WASH_DRY",
        service_version_id,
        basis,
        quantity,
        Unit.KG,
        ExactLineAmounts("EXACT", price.trace.unit_price_vnd, amount, 0, amount),
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
    return build_quote_snapshot(
        QuoteRevisionData(
            schema_version=1,
            quote_id=quote_id,
            revision=revision,
            finality=QuoteFinality.ESTIMATE,
            status=status,
            priced_at=priced_at,
            valid_until=priced_at + timedelta(days=1),
            currency="VND",
            configuration_snapshots=(
                ConfigurationSnapshotReference(
                    "PRICEBOOK",
                    pricebook_version_id,
                    1,
                    pricebook.manifest.canonical_snapshot_hash,
                ),
            ),
            lines=(line,),
            adjustments=(delivery,),
            totals=QuoteTotalsSnapshot(
                amount, amount, 0, 0, amount, amount, 10_000, 0, amount + 10_000, amount + 10_000
            ),
            calculation_traces=(trace,),
            calculation_engine_version="quote-engine-v1",
            calculation_engine_hash=canonical_document({"engine": "quote-engine-v1"}).snapshot_hash,
            promotion_eligibility_event=None,
            promotion_eligibility_at=None,
            reason_codes=("TAX_TREATMENT_UNVERIFIED",),
            required_approvals=("TAX_TREATMENT_UNVERIFIED", "SLOT_CONFIRMATION"),
            approval_id=None,
            customer_estimate_acknowledged_at=acknowledged_at,
        )
    )


def _seed(fixture: SyntheticFixtureBundle) -> Mapping[str, Any]:
    seed = fixture.payload.get("database_seed")
    if not isinstance(seed, Mapping):
        raise ValueError("quote lifecycle fixture seed is invalid")
    return seed


def _text(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"quote lifecycle fixture {key} is invalid")
    return result


def _clock(payload: Mapping[str, Any]) -> datetime:
    result = datetime.fromisoformat(_text(payload, "clock").replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("quote lifecycle fixture clock is invalid")
    return result


def _event_id(fixture: SyntheticFixtureBundle) -> str:
    events = fixture.payload.get("provider_events")
    if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], Mapping):
        raise ValueError("quote lifecycle fixture must contain one event")
    return _text(events[0], "event_id")


def _tool_trace(
    sequence: int, operation_id: str, arguments: dict[str, Any], trace_id: str
) -> Mapping[str, object]:
    return {
        "sequence": sequence,
        "operation_id": operation_id,
        "argument_field_names": sorted(arguments),
        "arguments_sha256": f"sha256:{sha256(rfc8785.dumps(arguments)).hexdigest()}",
        "status_code": 200,
        "trace_id": trace_id,
    }


__all__ = [
    "SyntheticQuoteLifecyclePreflight",
    "execute_estimate_acknowledgment_preflight",
    "execute_measurement_change_preflight",
    "execute_personalized_price_preflight",
    "execute_tax_unverified_preflight",
]
