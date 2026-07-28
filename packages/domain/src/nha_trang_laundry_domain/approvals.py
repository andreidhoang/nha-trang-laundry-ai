"""Server-derived immutable approval envelopes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final
from uuid import UUID

from nha_trang_laundry_domain.canonical import CanonicalDocument, canonical_document
from nha_trang_laundry_domain.catalog import ActorRole, ApprovalAction

HASH_PATTERN = re.compile(r"^JCS-SHA256-V1:[0-9a-f]{64}$")
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")


class ApprovalEnvelopeError(ValueError):
    """Raised when an approval cannot safely bind its exact action."""


@dataclass(frozen=True)
class ApprovalPolicy:
    required_role: ActorRole
    maximum_ttl: timedelta
    reason_codes: tuple[str, ...]
    obligations: tuple[str, ...]
    execution_capability: str


@dataclass(frozen=True)
class ApprovalEnvelopeData:
    schema_version: int
    approval_request_id: UUID
    action: ApprovalAction
    resource_type: str
    resource_id: UUID
    resource_version: int
    snapshot_hash: str
    rendered_hash: str
    policy_version: str
    required_role: ActorRole
    reason_codes: tuple[str, ...]
    obligations: tuple[str, ...]
    execution_capability: str
    requested_by: UUID
    requested_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ImmutableApprovalEnvelope:
    data: ApprovalEnvelopeData
    document: CanonicalDocument


_OPS_30_MIN = ApprovalPolicy(
    ActorRole.OPS_APPROVER,
    timedelta(minutes=30),
    ("CUSTOMER_FACING_COMMITMENT",),
    ("RECHECK_RESOURCE_VERSION", "RECHECK_POLICY_AT_EXECUTION"),
    "HUMAN_APPROVED_ACTION",
)
_OPS_15_MIN = ApprovalPolicy(
    ActorRole.OPS_APPROVER,
    timedelta(minutes=15),
    ("CAPACITY_OR_ORDER_CONFIRMATION",),
    ("RECHECK_RESOURCE_VERSION", "RECHECK_CAPACITY_AT_EXECUTION"),
    "HUMAN_APPROVED_ACTION",
)
_OWNER_FINANCIAL = ApprovalPolicy(
    ActorRole.OWNER_ADMIN,
    timedelta(minutes=10),
    ("FINANCIAL_OR_POLICY_ACTION",),
    ("MFA_REQUIRED", "SEPARATION_OF_DUTY", "RECHECK_RESOURCE_VERSION"),
    "OWNER_APPROVED_ACTION",
)

APPROVAL_POLICIES: Final = MappingProxyType(
    {
        ApprovalAction.PRESENT_QUOTE: _OPS_30_MIN,
        ApprovalAction.SEND_MESSAGE: _OPS_30_MIN,
        ApprovalAction.CONFIRM_SLOT: _OPS_15_MIN,
        ApprovalAction.ACCEPT_ORDER: _OPS_15_MIN,
        ApprovalAction.SET_RANGE_PRICE: _OWNER_FINANCIAL,
        ApprovalAction.SET_DELIVERY_FEE: _OWNER_FINANCIAL,
        ApprovalAction.APPLY_PROMOTION: _OWNER_FINANCIAL,
        ApprovalAction.CANCEL_ACTIVE_ORDER: _OWNER_FINANCIAL,
        ApprovalAction.APPROVE_REMEDY: _OWNER_FINANCIAL,
        ApprovalAction.APPROVE_B2B_TERMS: _OWNER_FINANCIAL,
        ApprovalAction.PUBLISH_POLICY: _OWNER_FINANCIAL,
        ApprovalAction.EXPORT_SANITIZED_DATA: _OWNER_FINANCIAL,
    }
)

APPROVAL_RESOURCE_TYPES: Final = MappingProxyType(
    {
        ApprovalAction.PRESENT_QUOTE: "QUOTE_REVISION",
        ApprovalAction.CONFIRM_SLOT: "SLOT_PROPOSAL",
        ApprovalAction.SET_RANGE_PRICE: "QUOTE_REVISION",
        ApprovalAction.SET_DELIVERY_FEE: "DELIVERY_FEE_PROPOSAL",
        ApprovalAction.APPLY_PROMOTION: "QUOTE_REVISION",
        ApprovalAction.SEND_MESSAGE: "MESSAGE_DRAFT",
        ApprovalAction.ACCEPT_ORDER: "ORDER",
        ApprovalAction.CANCEL_ACTIVE_ORDER: "ORDER",
        ApprovalAction.APPROVE_REMEDY: "REMEDY_PROPOSAL",
        ApprovalAction.APPROVE_B2B_TERMS: "B2B_TERMS",
        ApprovalAction.PUBLISH_POLICY: "POLICY_VERSION",
        ApprovalAction.EXPORT_SANITIZED_DATA: "EXPORT_REQUEST",
    }
)


def build_approval_envelope(
    *,
    approval_request_id: UUID,
    action: ApprovalAction,
    resource_type: str,
    resource_id: UUID,
    resource_version: int,
    snapshot_hash: str,
    rendered_hash: str,
    policy_version: str,
    requested_by: UUID,
    requested_at: datetime,
) -> ImmutableApprovalEnvelope:
    """Derive policy-controlled fields and hash the complete one-time action envelope."""
    policy = APPROVAL_POLICIES.get(action)
    if policy is None:
        raise ApprovalEnvelopeError("POLICY_DENIED: approval action is not configured")
    if (
        APPROVAL_RESOURCE_TYPES.get(action) != resource_type
        or not CODE_PATTERN.fullmatch(resource_type)
        or resource_version < 1
        or not HASH_PATTERN.fullmatch(snapshot_hash)
        or not HASH_PATTERN.fullmatch(rendered_hash)
        or not policy_version.strip()
        or requested_at.tzinfo is None
        or requested_at.utcoffset() is None
    ):
        raise ApprovalEnvelopeError("VALIDATION_ERROR: invalid approval binding")
    data = ApprovalEnvelopeData(
        schema_version=1,
        approval_request_id=approval_request_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_version=resource_version,
        snapshot_hash=snapshot_hash,
        rendered_hash=rendered_hash,
        policy_version=policy_version,
        required_role=policy.required_role,
        reason_codes=policy.reason_codes,
        obligations=policy.obligations,
        execution_capability=policy.execution_capability,
        requested_by=requested_by,
        requested_at=requested_at,
        expires_at=requested_at + policy.maximum_ttl,
    )
    return ImmutableApprovalEnvelope(data, canonical_document(data))
