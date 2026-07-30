"""Immutable, reproducible quote revision snapshots."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final
from uuid import UUID

from nha_trang_laundry_domain.canonical import (
    CanonicalDocument,
    CanonicalizationError,
    canonical_document,
    verify_canonical_document,
)
from nha_trang_laundry_domain.catalog import (
    AdjustmentDirection,
    PromotionEligibilityEvent,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
    Unit,
)

MAX_JCS_INTEGER: Final = 9_007_199_254_740_991
DECIMAL_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,62}$")
HASH_PATTERN = re.compile(r"^JCS-SHA256-V1:[0-9a-f]{64}$")


class QuoteAdjustmentKind(StrEnum):
    PROMOTION = "PROMOTION"
    MANUAL_DISCOUNT = "MANUAL_DISCOUNT"
    SURCHARGE = "SURCHARGE"
    DELIVERY = "DELIVERY"


class QuoteSnapshotError(ValueError):
    """Raised when a quote revision cannot be made immutable safely."""


@dataclass(frozen=True)
class ConfigurationSnapshotReference:
    config_type: str
    version_id: UUID
    version: int
    snapshot_hash: str


@dataclass(frozen=True)
class ExactLineAmounts:
    kind: str
    unit_price_vnd: int | None
    list_amount_vnd: int
    discount_amount_vnd: int
    net_amount_vnd: int


@dataclass(frozen=True)
class RangeLineAmounts:
    kind: str
    unit_price_min_vnd: int
    unit_price_max_vnd: int
    list_amount_min_vnd: int
    list_amount_max_vnd: int
    discount_min_vnd: int
    discount_max_vnd: int
    net_amount_min_vnd: int
    net_amount_max_vnd: int


@dataclass(frozen=True)
class QuoteLineSnapshot:
    line_id: str
    service_code: str
    service_version_id: UUID
    quantity_basis: QuantityBasis
    quantity: str
    unit: Unit
    amounts: ExactLineAmounts | RangeLineAmounts
    price_trace_hash: str


@dataclass(frozen=True)
class QuoteAdjustmentSnapshot:
    adjustment_id: str
    kind: QuoteAdjustmentKind
    direction: AdjustmentDirection
    amount_min_vnd: int
    amount_max_vnd: int
    reason_code: str
    source_version_id: UUID | None = None
    approval_id: UUID | None = None


@dataclass(frozen=True)
class CalculationTraceSnapshot:
    component: str
    engine_version: str
    trace: CanonicalDocument


@dataclass(frozen=True)
class QuoteTotalsSnapshot:
    list_service_subtotal_min_vnd: int
    list_service_subtotal_max_vnd: int
    discount_amount_min_vnd: int
    discount_amount_max_vnd: int
    net_service_subtotal_min_vnd: int
    net_service_subtotal_max_vnd: int
    delivery_fee_vnd: int | None
    approved_surcharge_vnd: int
    display_total_min_vnd: int | None
    display_total_max_vnd: int | None


@dataclass(frozen=True)
class QuoteRevisionData:
    schema_version: int
    quote_id: UUID
    revision: int
    finality: QuoteFinality
    status: QuoteRevisionStatus
    priced_at: datetime
    valid_until: datetime | None
    currency: str
    configuration_snapshots: tuple[ConfigurationSnapshotReference, ...]
    lines: tuple[QuoteLineSnapshot, ...]
    adjustments: tuple[QuoteAdjustmentSnapshot, ...]
    totals: QuoteTotalsSnapshot
    calculation_traces: tuple[CalculationTraceSnapshot, ...]
    calculation_engine_version: str
    calculation_engine_hash: str
    promotion_eligibility_event: PromotionEligibilityEvent | None
    promotion_eligibility_at: datetime | None
    reason_codes: tuple[str, ...]
    required_approvals: tuple[str, ...]
    approval_id: UUID | None
    tax_treatment: str = "UNVERIFIED"
    tax_vnd: None = None
    customer_estimate_acknowledged_at: datetime | None = None


@dataclass(frozen=True)
class ImmutableQuoteSnapshot:
    data: QuoteRevisionData
    document: CanonicalDocument


def capture_calculation_trace(
    component: str, engine_version: str, trace: object
) -> CalculationTraceSnapshot:
    """Bind a deterministic engine trace to canonical bytes before quote composition."""
    if not CODE_PATTERN.fullmatch(component) or not engine_version.strip():
        raise QuoteSnapshotError("invalid calculation trace identity")
    try:
        document = canonical_document(trace)
    except CanonicalizationError as error:
        raise QuoteSnapshotError("calculation trace is not canonical") from error
    return CalculationTraceSnapshot(component, engine_version, document)


def build_quote_snapshot(data: QuoteRevisionData) -> ImmutableQuoteSnapshot:
    """Validate, deterministically order, and hash one immutable revision."""
    normalized = replace(
        data,
        configuration_snapshots=tuple(
            sorted(data.configuration_snapshots, key=lambda item: item.config_type)
        ),
        lines=tuple(sorted(data.lines, key=lambda item: item.line_id)),
        adjustments=tuple(sorted(data.adjustments, key=lambda item: item.adjustment_id)),
        calculation_traces=tuple(sorted(data.calculation_traces, key=lambda item: item.component)),
        reason_codes=tuple(sorted(set(data.reason_codes))),
        required_approvals=tuple(sorted(set(data.required_approvals))),
    )
    _validate_revision(normalized)
    try:
        document = canonical_document(normalized)
    except CanonicalizationError as error:
        raise QuoteSnapshotError("quote snapshot is not canonical") from error
    return ImmutableQuoteSnapshot(normalized, document)


def verify_quote_snapshot(snapshot: ImmutableQuoteSnapshot) -> bool:
    """Rebuild a revision and compare its exact canonical document and digest."""
    if not verify_canonical_document(snapshot.document):
        return False
    try:
        rebuilt = build_quote_snapshot(snapshot.data)
    except (CanonicalizationError, QuoteSnapshotError):
        return False
    return rebuilt.document == snapshot.document and rebuilt.data == snapshot.data


def _validate_revision(data: QuoteRevisionData) -> None:
    if (
        data.schema_version != 1
        or isinstance(data.schema_version, bool)
        or not isinstance(data.quote_id, UUID)
        or not _positive_int(data.revision)
        or data.currency != "VND"
    ):
        raise QuoteSnapshotError("invalid quote revision identity")
    if not isinstance(data.finality, QuoteFinality) or not isinstance(
        data.status, QuoteRevisionStatus
    ):
        raise QuoteSnapshotError("invalid quote lifecycle enum")
    _aware(data.priced_at)
    if data.valid_until is not None:
        _aware(data.valid_until)
        if data.valid_until <= data.priced_at:
            raise QuoteSnapshotError("quote validity must end after pricing")
    if data.tax_treatment != "UNVERIFIED" or data.tax_vnd is not None:
        raise QuoteSnapshotError("tax policy is not published")
    if not data.calculation_engine_version.strip() or not HASH_PATTERN.fullmatch(
        data.calculation_engine_hash
    ):
        raise QuoteSnapshotError("calculation engine identity is incomplete")
    _validate_configurations(data.configuration_snapshots)
    _validate_lines(data.lines)
    _validate_traces(data.calculation_traces, data.lines)
    _validate_totals_and_adjustments(data.lines, data.adjustments, data.totals)
    _validate_finality(data)
    if (data.promotion_eligibility_event is None) != (data.promotion_eligibility_at is None):
        raise QuoteSnapshotError("promotion eligibility event and time must resolve together")
    if data.promotion_eligibility_event is not None and not isinstance(
        data.promotion_eligibility_event, PromotionEligibilityEvent
    ):
        raise QuoteSnapshotError("invalid promotion eligibility event")
    if data.promotion_eligibility_at is not None:
        _aware(data.promotion_eligibility_at)
    for code in (*data.reason_codes, *data.required_approvals):
        if not CODE_PATTERN.fullmatch(code):
            raise QuoteSnapshotError("reason and approval codes must be canonical")


def _validate_configurations(references: tuple[ConfigurationSnapshotReference, ...]) -> None:
    if not references or len({item.config_type for item in references}) != len(references):
        raise QuoteSnapshotError("configuration snapshot references must be unique")
    if sum(item.config_type == "PRICEBOOK" for item in references) != 1:
        raise QuoteSnapshotError("exactly one pricebook snapshot is required")
    for item in references:
        if (
            not CODE_PATTERN.fullmatch(item.config_type)
            or not isinstance(item.version_id, UUID)
            or not _positive_int(item.version)
            or not HASH_PATTERN.fullmatch(item.snapshot_hash)
        ):
            raise QuoteSnapshotError("invalid configuration snapshot reference")


def _validate_lines(lines: tuple[QuoteLineSnapshot, ...]) -> None:
    if not lines or len({line.line_id for line in lines}) != len(lines):
        raise QuoteSnapshotError("quote lines must be non-empty and unique")
    for line in lines:
        if not line.line_id or not CODE_PATTERN.fullmatch(line.service_code):
            raise QuoteSnapshotError("invalid quote line identity")
        if not isinstance(line.quantity_basis, QuantityBasis) or not isinstance(line.unit, Unit):
            raise QuoteSnapshotError("invalid quote line enum")
        if not isinstance(line.service_version_id, UUID):
            raise QuoteSnapshotError("invalid service version identity")
        _canonical_positive_decimal(line.quantity)
        if (
            line.unit
            in {
                Unit.ITEM,
                Unit.PAIR,
                Unit.SET,
                Unit.ANIMAL_PLUSH_ITEM,
                Unit.CASE,
            }
            and "." in line.quantity
        ):
            raise QuoteSnapshotError("count quantity must be an integer")
        if not HASH_PATTERN.fullmatch(line.price_trace_hash):
            raise QuoteSnapshotError("quote line is missing a canonical price trace hash")
        if isinstance(line.amounts, ExactLineAmounts):
            if line.amounts.kind != "EXACT":
                raise QuoteSnapshotError("invalid exact line discriminator")
            values = (
                line.amounts.list_amount_vnd,
                line.amounts.discount_amount_vnd,
                line.amounts.net_amount_vnd,
            )
            for value in values:
                _money(value)
            if line.amounts.unit_price_vnd is not None:
                _money(line.amounts.unit_price_vnd)
            if line.amounts.discount_amount_vnd > line.amounts.list_amount_vnd or (
                line.amounts.net_amount_vnd
                != line.amounts.list_amount_vnd - line.amounts.discount_amount_vnd
            ):
                raise QuoteSnapshotError("exact line amounts do not reconcile")
        elif isinstance(line.amounts, RangeLineAmounts):
            if line.amounts.kind != "RANGE":
                raise QuoteSnapshotError("invalid range line discriminator")
            values = tuple(
                getattr(line.amounts, field)
                for field in (
                    "unit_price_min_vnd",
                    "unit_price_max_vnd",
                    "list_amount_min_vnd",
                    "list_amount_max_vnd",
                    "discount_min_vnd",
                    "discount_max_vnd",
                    "net_amount_min_vnd",
                    "net_amount_max_vnd",
                )
            )
            for value in values:
                _money(value)
            if not (
                line.amounts.unit_price_min_vnd <= line.amounts.unit_price_max_vnd
                and line.amounts.list_amount_min_vnd <= line.amounts.list_amount_max_vnd
                and line.amounts.discount_min_vnd <= line.amounts.discount_max_vnd
                and line.amounts.net_amount_min_vnd <= line.amounts.net_amount_max_vnd
                and line.amounts.net_amount_min_vnd
                == line.amounts.list_amount_min_vnd - line.amounts.discount_min_vnd
                and line.amounts.net_amount_max_vnd
                == line.amounts.list_amount_max_vnd - line.amounts.discount_max_vnd
            ):
                raise QuoteSnapshotError("range line amounts do not reconcile")
        else:
            raise QuoteSnapshotError("unknown line amount type")


def _validate_traces(
    traces: tuple[CalculationTraceSnapshot, ...], lines: tuple[QuoteLineSnapshot, ...]
) -> None:
    if not traces or len({trace.component for trace in traces}) != len(traces):
        raise QuoteSnapshotError("calculation traces must be non-empty and unique")
    trace_hashes = {trace.trace.snapshot_hash for trace in traces}
    for trace in traces:
        if (
            not CODE_PATTERN.fullmatch(trace.component)
            or not trace.engine_version.strip()
            or not verify_canonical_document(trace.trace)
        ):
            raise QuoteSnapshotError("invalid calculation trace")
    if any(line.price_trace_hash not in trace_hashes for line in lines):
        raise QuoteSnapshotError("line price trace is not embedded in the revision")


def _validate_totals_and_adjustments(
    lines: tuple[QuoteLineSnapshot, ...],
    adjustments: tuple[QuoteAdjustmentSnapshot, ...],
    totals: QuoteTotalsSnapshot,
) -> None:
    list_mins = 0
    list_maxs = 0
    discount_mins = 0
    discount_maxs = 0
    for line in lines:
        if isinstance(line.amounts, ExactLineAmounts):
            list_mins += line.amounts.list_amount_vnd
            list_maxs += line.amounts.list_amount_vnd
            discount_mins += line.amounts.discount_amount_vnd
            discount_maxs += line.amounts.discount_amount_vnd
        else:
            list_mins += line.amounts.list_amount_min_vnd
            list_maxs += line.amounts.list_amount_max_vnd
            discount_mins += line.amounts.discount_min_vnd
            discount_maxs += line.amounts.discount_max_vnd
    expected = (
        list_mins,
        list_maxs,
        discount_mins,
        discount_maxs,
        list_mins - discount_mins,
        list_maxs - discount_maxs,
    )
    actual = (
        totals.list_service_subtotal_min_vnd,
        totals.list_service_subtotal_max_vnd,
        totals.discount_amount_min_vnd,
        totals.discount_amount_max_vnd,
        totals.net_service_subtotal_min_vnd,
        totals.net_service_subtotal_max_vnd,
    )
    if actual != expected:
        raise QuoteSnapshotError("quote totals do not match line snapshots")
    for value in actual:
        _money(value)
    _money(totals.approved_surcharge_vnd)
    if totals.delivery_fee_vnd is not None:
        _money(totals.delivery_fee_vnd)
    _validate_adjustments(adjustments, totals)
    if totals.delivery_fee_vnd is None:
        if totals.display_total_min_vnd is not None or totals.display_total_max_vnd is not None:
            raise QuoteSnapshotError("display total requires a resolved delivery fee")
    else:
        expected_min = (
            totals.net_service_subtotal_min_vnd
            + totals.delivery_fee_vnd
            + totals.approved_surcharge_vnd
        )
        expected_max = (
            totals.net_service_subtotal_max_vnd
            + totals.delivery_fee_vnd
            + totals.approved_surcharge_vnd
        )
        if (totals.display_total_min_vnd, totals.display_total_max_vnd) != (
            expected_min,
            expected_max,
        ):
            raise QuoteSnapshotError("display total does not reconcile")
        _money(expected_min)
        _money(expected_max)


def _validate_adjustments(
    adjustments: tuple[QuoteAdjustmentSnapshot, ...], totals: QuoteTotalsSnapshot
) -> None:
    if len({item.adjustment_id for item in adjustments}) != len(adjustments):
        raise QuoteSnapshotError("adjustment IDs must be unique")
    credit_min = credit_max = delivery = surcharge = 0
    for item in adjustments:
        if not item.adjustment_id or not CODE_PATTERN.fullmatch(item.reason_code):
            raise QuoteSnapshotError("invalid adjustment identity")
        if not isinstance(item.kind, QuoteAdjustmentKind) or not isinstance(
            item.direction, AdjustmentDirection
        ):
            raise QuoteSnapshotError("invalid adjustment enum")
        if item.source_version_id is not None and not isinstance(item.source_version_id, UUID):
            raise QuoteSnapshotError("invalid adjustment source version")
        if item.approval_id is not None and not isinstance(item.approval_id, UUID):
            raise QuoteSnapshotError("invalid adjustment approval")
        _money(item.amount_min_vnd)
        _money(item.amount_max_vnd)
        if item.amount_min_vnd > item.amount_max_vnd:
            raise QuoteSnapshotError("adjustment range is inverted")
        if item.kind in {QuoteAdjustmentKind.PROMOTION, QuoteAdjustmentKind.MANUAL_DISCOUNT}:
            if item.direction is not AdjustmentDirection.CREDIT:
                raise QuoteSnapshotError("discount adjustments must be credits")
            if item.kind is QuoteAdjustmentKind.MANUAL_DISCOUNT and item.approval_id is None:
                raise QuoteSnapshotError("manual discounts require approval")
            credit_min += item.amount_min_vnd
            credit_max += item.amount_max_vnd
        elif item.kind is QuoteAdjustmentKind.DELIVERY:
            if (
                item.direction is not AdjustmentDirection.DEBIT
                or item.amount_min_vnd != item.amount_max_vnd
            ):
                raise QuoteSnapshotError("delivery adjustment must be an exact debit")
            delivery += item.amount_min_vnd
        elif item.kind is QuoteAdjustmentKind.SURCHARGE:
            if item.direction is not AdjustmentDirection.DEBIT or item.approval_id is None:
                raise QuoteSnapshotError("surcharges require an approved debit")
            surcharge += item.amount_min_vnd
            if item.amount_min_vnd != item.amount_max_vnd:
                raise QuoteSnapshotError("approved surcharge must be exact")
        else:
            raise QuoteSnapshotError("unsupported quote adjustment")
    if (credit_min, credit_max) != (
        totals.discount_amount_min_vnd,
        totals.discount_amount_max_vnd,
    ):
        raise QuoteSnapshotError("credit adjustments do not match line discounts")
    if delivery != (totals.delivery_fee_vnd or 0) or surcharge != totals.approved_surcharge_vnd:
        raise QuoteSnapshotError("debit adjustments do not match quote totals")


def _validate_finality(data: QuoteRevisionData) -> None:
    has_range = any(isinstance(line.amounts, RangeLineAmounts) for line in data.lines)
    if has_range != (data.finality is QuoteFinality.RANGE):
        raise QuoteSnapshotError("range lines and quote finality disagree")
    if data.finality is QuoteFinality.RANGE and data.status is QuoteRevisionStatus.ACCEPTED_FINAL:
        raise QuoteSnapshotError("range quote cannot be accepted as final")
    if data.finality is QuoteFinality.ESTIMATE and (
        data.status is QuoteRevisionStatus.ACCEPTED_FINAL or data.approval_id is not None
    ):
        raise QuoteSnapshotError("estimate cannot become approved final")
    acknowledged = data.customer_estimate_acknowledged_at
    if (data.status is QuoteRevisionStatus.ACKNOWLEDGED_ESTIMATE) != (acknowledged is not None):
        raise QuoteSnapshotError("estimate acknowledgment status requires timestamp evidence")
    if acknowledged is not None:
        _aware(acknowledged)
        if data.finality is not QuoteFinality.ESTIMATE or acknowledged < data.priced_at:
            raise QuoteSnapshotError("estimate acknowledgment evidence is invalid")
    if data.finality is QuoteFinality.APPROVED_EXACT and (
        not isinstance(data.approval_id, UUID)
        or data.required_approvals
        or data.totals.delivery_fee_vnd is None
        or any(line.quantity_basis is QuantityBasis.CUSTOMER_ESTIMATE for line in data.lines)
        or (
            any(item.config_type == "PROMOTION" for item in data.configuration_snapshots)
            and data.promotion_eligibility_event is None
        )
        or data.status
        in {
            QuoteRevisionStatus.DRAFT,
            QuoteRevisionStatus.PROVISIONAL,
            QuoteRevisionStatus.REVIEW_REQUIRED,
            QuoteRevisionStatus.ACKNOWLEDGED_ESTIMATE,
        }
    ):
        raise QuoteSnapshotError("approved exact quote lacks final evidence")


def _canonical_positive_decimal(value: str) -> None:
    if not isinstance(value, str) or not DECIMAL_PATTERN.fullmatch(value):
        raise QuoteSnapshotError("quantity must be a canonical decimal string")
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise QuoteSnapshotError("quantity must be a canonical decimal string") from error
    exponent = decimal.as_tuple().exponent
    if (
        not isinstance(exponent, int)
        or not decimal.is_finite()
        or decimal <= 0
        or decimal > Decimal("999999999.999")
        or max(0, -exponent) > 3
        or ("." in value and value.endswith("0"))
    ):
        raise QuoteSnapshotError("quantity must be normalized without rounding")


def _money(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_JCS_INTEGER:
        raise QuoteSnapshotError("money must be a nonnegative JCS-safe integer VND")


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise QuoteSnapshotError("quote timestamps must be timezone-aware")


def _positive_int(value: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
