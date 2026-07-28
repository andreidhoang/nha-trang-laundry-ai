"""Typed canonical service registry built from an already validated catalog snapshot."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class Unit(StrEnum):
    KG = "KG"
    ITEM = "ITEM"
    PAIR = "PAIR"
    SET = "SET"
    ANIMAL_PLUSH_ITEM = "ANIMAL_PLUSH_ITEM"
    CASE = "CASE"
    M2 = "M2"


class PriceRuleType(StrEnum):
    FIXED_PER_UNIT = "FIXED_PER_UNIT"
    RANGE_PER_UNIT = "RANGE_PER_UNIT"
    AGGREGATE_TIER_PER_UNIT = "AGGREGATE_TIER_PER_UNIT"
    FIXED_PER_ORDER = "FIXED_PER_ORDER"
    MANUAL = "MANUAL"


class PriceResolution(StrEnum):
    AUTO_FIXED = "AUTO_FIXED"
    SHOW_RANGE = "SHOW_RANGE"
    HUMAN_EXACT = "HUMAN_EXACT"
    NOT_PRICED = "NOT_PRICED"


class QuantityBasis(StrEnum):
    CUSTOMER_ESTIMATE = "CUSTOMER_ESTIMATE"
    STAFF_MEASUREMENT = "STAFF_MEASUREMENT"
    APPROVED_MANUAL = "APPROVED_MANUAL"


class QuoteFinality(StrEnum):
    ESTIMATE = "ESTIMATE"
    RANGE = "RANGE"
    APPROVED_EXACT = "APPROVED_EXACT"


class QuoteContainerStatus(StrEnum):
    OPEN = "OPEN"
    CONVERTED = "CONVERTED"
    CLOSED = "CLOSED"


class QuoteRevisionStatus(StrEnum):
    DRAFT = "DRAFT"
    PROVISIONAL = "PROVISIONAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    PRESENTED = "PRESENTED"
    ACKNOWLEDGED_ESTIMATE = "ACKNOWLEDGED_ESTIMATE"
    ACCEPTED_FINAL = "ACCEPTED_FINAL"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class AdjustmentDirection(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class PolicyOutcome(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"
    DENY = "DENY"


class ActorRole(StrEnum):
    OWNER_ADMIN = "OWNER_ADMIN"
    OPS_APPROVER = "OPS_APPROVER"
    OPERATOR = "OPERATOR"
    DRIVER = "DRIVER"
    ACCOUNTANT = "ACCOUNTANT"
    AUDITOR = "AUDITOR"
    PUBLIC_AGENT = "PUBLIC_AGENT"
    PRIVATE_AGENT = "PRIVATE_AGENT"
    AGENT_RUNNER = "AGENT_RUNNER"
    OUTBOX_WORKER = "OUTBOX_WORKER"


class PromotionResolution(StrEnum):
    AUTO_IF_TARGETED = "AUTO_IF_TARGETED"
    HUMAN_CONFIRM = "HUMAN_CONFIRM"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class PromotionStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PROVISIONAL = "PROVISIONAL"
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"


class PromotionEligibilityEvent(StrEnum):
    QUOTE_PRESENTED = "QUOTE_PRESENTED"
    CUSTOMER_ESTIMATE_ACKNOWLEDGED = "CUSTOMER_ESTIMATE_ACKNOWLEDGED"
    CUSTOMER_FINAL_QUOTE_ACCEPTED = "CUSTOMER_FINAL_QUOTE_ACCEPTED"
    STORE_COMMERCIAL_ACCEPTED = "STORE_COMMERCIAL_ACCEPTED"
    PRODUCTION_ACCEPTED = "PRODUCTION_ACCEPTED"


class CommitmentAuthority(StrEnum):
    INFORMATION_ONLY = "INFORMATION_ONLY"
    DRAFT_ONLY = "DRAFT_ONLY"
    HUMAN_CONFIRM = "HUMAN_CONFIRM"
    AUTO_WITHIN_ENVELOPE = "AUTO_WITHIN_ENVELOPE"
    PROHIBITED = "PROHIBITED"


class FulfillmentMode(StrEnum):
    SELF_DROP_SELF_COLLECT = "SELF_DROP_SELF_COLLECT"
    PICKUP_AND_RETURN = "PICKUP_AND_RETURN"
    PICKUP_ONLY = "PICKUP_ONLY"
    RETURN_ONLY = "RETURN_ONLY"


class CommercialOrderStatus(StrEnum):
    DRAFT = "DRAFT"
    REQUESTED = "REQUESTED"
    STORE_CONFIRMATION_PENDING = "STORE_CONFIRMATION_PENDING"
    CONFIRMED = "CONFIRMED"
    ACTIVE = "ACTIVE"
    CANCELLATION_REVIEW = "CANCELLATION_REVIEW"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class IntakeStatus(StrEnum):
    AWAITING_HANDOFF = "AWAITING_HANDOFF"
    RECEIVED_PENDING_INSPECTION = "RECEIVED_PENDING_INSPECTION"
    WAITING_PRICE_APPROVAL = "WAITING_PRICE_APPROVAL"
    WAITING_CUSTOMER_RECONFIRMATION = "WAITING_CUSTOMER_RECONFIRMATION"
    WAITING_SLOT_APPROVAL = "WAITING_SLOT_APPROVAL"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ProductionStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    QUEUED = "QUEUED"
    IN_PROCESS = "IN_PROCESS"
    QUALITY_CHECK = "QUALITY_CHECK"
    READY_AT_STORE = "READY_AT_STORE"
    RELEASED = "RELEASED"
    ON_HOLD = "ON_HOLD"
    EXCEPTION = "EXCEPTION"


class DeliveryLegStatus(StrEnum):
    PLANNED = "PLANNED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class OrderBalanceStatus(StrEnum):
    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    OVERPAID = "OVERPAID"
    ON_ACCOUNT = "ON_ACCOUNT"


class ApprovalAction(StrEnum):
    PRESENT_QUOTE = "PRESENT_QUOTE"
    CONFIRM_SLOT = "CONFIRM_SLOT"
    SET_RANGE_PRICE = "SET_RANGE_PRICE"
    SET_DELIVERY_FEE = "SET_DELIVERY_FEE"
    APPLY_PROMOTION = "APPLY_PROMOTION"
    SEND_MESSAGE = "SEND_MESSAGE"
    ACCEPT_ORDER = "ACCEPT_ORDER"
    CANCEL_ACTIVE_ORDER = "CANCEL_ACTIVE_ORDER"
    APPROVE_REMEDY = "APPROVE_REMEDY"
    APPROVE_B2B_TERMS = "APPROVE_B2B_TERMS"
    PUBLISH_POLICY = "PUBLISH_POLICY"
    EXPORT_SANITIZED_DATA = "EXPORT_SANITIZED_DATA"


class MessageKind(StrEnum):
    LIST_PRICE_INFO = "LIST_PRICE_INFO"
    INTAKE_FACT_REQUEST = "INTAKE_FACT_REQUEST"
    INTAKE_RECEIPT = "INTAKE_RECEIPT"
    INCIDENT_RECEIPT = "INCIDENT_RECEIPT"
    ORDER_STATUS = "ORDER_STATUS"
    APPROVED_QUOTE_PRESENTATION = "APPROVED_QUOTE_PRESENTATION"
    APPROVED_SLOT_PRESENTATION = "APPROVED_SLOT_PRESENTATION"
    FREE_FORM_TRANSACTIONAL = "FREE_FORM_TRANSACTIONAL"
    MARKETING = "MARKETING"


class MessageDeliveryStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED_FOR_WORKER_SEND = "APPROVED_FOR_WORKER_SEND"
    APPROVED_FOR_MANUAL_SEND = "APPROVED_FOR_MANUAL_SEND"
    OUTBOX_PENDING = "OUTBOX_PENDING"
    PROVIDER_ACCEPTED = "PROVIDER_ACCEPTED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    MANUAL_SEND_RECORDED = "MANUAL_SEND_RECORDED"
    CANCELLED = "CANCELLED"


class SlaLifecycle(StrEnum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class SlaOutcome(StrEnum):
    PENDING = "PENDING"
    MET = "MET"
    BREACHED = "BREACHED"
    WAIVED = "WAIVED"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AMBIGUOUS_SERVICE = "AMBIGUOUS_SERVICE"
    INCOMPATIBLE_UNIT = "INCOMPATIBLE_UNIT"
    MISSING_REQUIRED_FACT = "MISSING_REQUIRED_FACT"
    PRICE_RULE_UNRESOLVED = "PRICE_RULE_UNRESOLVED"
    MEASUREMENT_POLICY_UNRESOLVED = "MEASUREMENT_POLICY_UNRESOLVED"
    PROMOTION_ELIGIBILITY_UNRESOLVED = "PROMOTION_ELIGIBILITY_UNRESOLVED"
    RANGE_PRICE_REQUIRES_HUMAN = "RANGE_PRICE_REQUIRES_HUMAN"
    DELIVERY_DISTANCE_UNVERIFIED = "DELIVERY_DISTANCE_UNVERIFIED"
    DELIVERY_FEE_REQUIRES_HUMAN = "DELIVERY_FEE_REQUIRES_HUMAN"
    SLOT_APPROVAL_REQUIRED = "SLOT_APPROVAL_REQUIRED"
    CUSTOMER_RECONFIRMATION_REQUIRED = "CUSTOMER_RECONFIRMATION_REQUIRED"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    STALE_VERSION = "STALE_VERSION"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    SUPPRESSED_CONTACT = "SUPPRESSED_CONTACT"
    POLICY_DENIED = "POLICY_DENIED"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"


DOMAIN_ENUM_REGISTRY: Final = MappingProxyType(
    {
        "Unit": Unit,
        "PriceRuleType": PriceRuleType,
        "PriceResolution": PriceResolution,
        "QuantityBasis": QuantityBasis,
        "QuoteFinality": QuoteFinality,
        "QuoteContainerStatus": QuoteContainerStatus,
        "QuoteRevisionStatus": QuoteRevisionStatus,
        "AdjustmentDirection": AdjustmentDirection,
        "PolicyOutcome": PolicyOutcome,
        "ActorRole": ActorRole,
        "PromotionResolution": PromotionResolution,
        "PromotionStatus": PromotionStatus,
        "PromotionEligibilityEvent": PromotionEligibilityEvent,
        "CommitmentAuthority": CommitmentAuthority,
        "FulfillmentMode": FulfillmentMode,
        "CommercialOrderStatus": CommercialOrderStatus,
        "IntakeStatus": IntakeStatus,
        "ProductionStatus": ProductionStatus,
        "DeliveryLegStatus": DeliveryLegStatus,
        "PaymentStatus": PaymentStatus,
        "OrderBalanceStatus": OrderBalanceStatus,
        "ApprovalAction": ApprovalAction,
        "MessageKind": MessageKind,
        "MessageDeliveryStatus": MessageDeliveryStatus,
        "SlaLifecycle": SlaLifecycle,
        "SlaOutcome": SlaOutcome,
        "ErrorCode": ErrorCode,
    }
)

SERVICE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,62}$")
FORBIDDEN_ALIASES: Final = frozenset({"S1_SHADOW", "OWNER", "ACCOUNTING", "PUBLIC AGENT"})
STANDARD_WASH_DRY = "STANDARD_WASH_DRY"
CONFIRMED_LEGACY_ALIASES: Final = MappingProxyType(
    {
        "STD_WASH_DRY_LT6": STANDARD_WASH_DRY,
        "STD_WASH_DRY_GE6": STANDARD_WASH_DRY,
    }
)


class CatalogError(ValueError):
    """Raised when a service snapshot or lookup violates the canonical contract."""


@dataclass(frozen=True)
class ServiceDefinition:
    code: str
    display_name: str
    category: str
    unit: Unit


@dataclass(frozen=True)
class ServiceAlias:
    source_code: str
    canonical_code: str


class ServiceRegistry:
    """Resolve canonical codes and explicit aliases without fuzzy/model interpretation."""

    def __init__(
        self,
        services: tuple[ServiceDefinition, ...],
        aliases: tuple[ServiceAlias, ...] = (),
    ) -> None:
        canonical: dict[str, ServiceDefinition] = {}
        for service in services:
            _require_code(service.code)
            if service.code in canonical:
                raise CatalogError("VALIDATION_ERROR: duplicate canonical service code")
            _require_nfc_text(service.display_name, "service display name")
            _require_nfc_text(service.category, "service category")
            canonical[service.code] = service

        normalized_aliases: dict[str, str] = {}
        for alias in aliases:
            _require_code(alias.source_code)
            _require_code(alias.canonical_code)
            if alias.source_code in FORBIDDEN_ALIASES:
                raise CatalogError("VALIDATION_ERROR: forbidden alias")
            if alias.source_code in canonical:
                raise CatalogError("VALIDATION_ERROR: alias collides with canonical service")
            if alias.canonical_code not in canonical:
                raise CatalogError("VALIDATION_ERROR: alias target is not canonical")
            if alias.source_code in normalized_aliases:
                raise CatalogError("VALIDATION_ERROR: duplicate source alias")
            normalized_aliases[alias.source_code] = alias.canonical_code

        self._services = MappingProxyType(canonical)
        self._aliases = MappingProxyType(normalized_aliases)

    def resolve(self, service_code: str, unit: Unit | None = None) -> ServiceDefinition:
        canonical_code = self._aliases.get(service_code, service_code)
        service = self._services.get(canonical_code)
        if service is None:
            raise CatalogError("AMBIGUOUS_SERVICE")
        if unit is not None and service.unit is not unit:
            raise CatalogError("INCOMPATIBLE_UNIT")
        return service

    @property
    def service_count(self) -> int:
        return len(self._services)

    @property
    def alias_count(self) -> int:
        return len(self._aliases)


def parse_domain_enum(enum_name: str, value: str) -> StrEnum:
    enum_type = DOMAIN_ENUM_REGISTRY.get(enum_name)
    if enum_type is None:
        raise CatalogError("VALIDATION_ERROR: unknown canonical enum")
    try:
        return enum_type(value)
    except ValueError as error:
        raise CatalogError("VALIDATION_ERROR: unknown canonical enum value") from error


def standard_wash_aliases() -> tuple[ServiceAlias, ...]:
    return tuple(
        ServiceAlias(source, target) for source, target in CONFIRMED_LEGACY_ALIASES.items()
    )


def _require_code(value: str) -> None:
    if not SERVICE_CODE_PATTERN.fullmatch(value):
        raise CatalogError("VALIDATION_ERROR: invalid canonical code")


def _require_nfc_text(value: str, field: str) -> None:
    if not value or unicodedata.normalize("NFC", value) != value:
        raise CatalogError(f"VALIDATION_ERROR: {field} must be non-empty NFC")
