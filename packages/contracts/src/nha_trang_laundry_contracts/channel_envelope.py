"""Provider-neutral channel envelope and send receipt.

Every messaging provider maps into `ChannelInboundEnvelope` before any durable inbox write, and
every send attempt produces exactly one `ChannelOutboundReceipt`. The JSON Schemas under
`specs/contracts/` remain authoritative; these models are the typed boundary that enforces them in
process, and `packages/evals/tests/test_channel_envelope.py` proves the two agree.

Three invariants are encoded here rather than left to callers:

* identity is server-resolved — `resolved_by` is a constant, so a provider payload or a model output
  cannot supply a contact binding;
* an ambiguous provider outcome (`TIMEOUT`, `TRANSPORT_ERROR`) can never be recorded as settled;
* leaving `UNKNOWN` requires a provider confirmation or a named human, never a retry.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .runtime_registry import ReleaseCapability


class ChannelProvider(StrEnum):
    """`TELEGRAM_SANDBOX` never carries real PII or gate evidence.

    `FACEBOOK_MESSENGER` is reserved and not implemented.
    """

    ZALO_OA = "ZALO_OA"
    TELEGRAM_SANDBOX = "TELEGRAM_SANDBOX"
    FACEBOOK_MESSENGER = "FACEBOOK_MESSENGER"


class DedupeKeySource(StrEnum):
    PROVIDER_ASSIGNED = "PROVIDER_ASSIGNED"
    DERIVED = "DERIVED"


class ChannelAuthenticationScheme(StrEnum):
    PROVIDER_SIGNATURE = "PROVIDER_SIGNATURE"
    SECRET_HEADER = "SECRET_HEADER"
    MUTUAL_TLS = "MUTUAL_TLS"


class ContactVerificationState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"


class ChannelContentType(StrEnum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    FILE = "FILE"
    STICKER = "STICKER"
    LOCATION = "LOCATION"
    UNSUPPORTED = "UNSUPPORTED"


class IngressSuppressionDecision(StrEnum):
    ALLOW = "ALLOW"
    SUPPRESS_RECORDED = "SUPPRESS_RECORDED"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"


class ChannelMessageKind(StrEnum):
    """Mirrors the canonical `MessageKind`; drift is a contract-verification failure."""

    LIST_PRICE_INFO = "LIST_PRICE_INFO"
    INTAKE_FACT_REQUEST = "INTAKE_FACT_REQUEST"
    INTAKE_RECEIPT = "INTAKE_RECEIPT"
    INCIDENT_RECEIPT = "INCIDENT_RECEIPT"
    ORDER_STATUS = "ORDER_STATUS"
    APPROVED_QUOTE_PRESENTATION = "APPROVED_QUOTE_PRESENTATION"
    APPROVED_SLOT_PRESENTATION = "APPROVED_SLOT_PRESENTATION"
    FREE_FORM_TRANSACTIONAL = "FREE_FORM_TRANSACTIONAL"
    MARKETING = "MARKETING"


class SendAuthorizationSource(StrEnum):
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    CAPABILITY_AUTHORIZED = "CAPABILITY_AUTHORIZED"


class MessagingWindowState(StrEnum):
    IN_WINDOW = "IN_WINDOW"
    TEMPLATE_APPROVED = "TEMPLATE_APPROVED"
    HELD_REQUIRE_HUMAN = "HELD_REQUIRE_HUMAN"


class SendAttemptOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    TIMEOUT = "TIMEOUT"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"


class ChannelDeliveryStatus(StrEnum):
    OUTBOX_PENDING = "OUTBOX_PENDING"
    PROVIDER_ACCEPTED = "PROVIDER_ACCEPTED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    MANUAL_SEND_RECORDED = "MANUAL_SEND_RECORDED"
    CANCELLED = "CANCELLED"


class ReconciliationState(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    UNKNOWN = "UNKNOWN"
    UNKNOWN_REQUIRES_HUMAN = "UNKNOWN_REQUIRES_HUMAN"
    CONFIRMED_SENT = "CONFIRMED_SENT"
    CONFIRMED_NOT_SENT = "CONFIRMED_NOT_SENT"


AMBIGUOUS_OUTCOMES = frozenset({SendAttemptOutcome.TIMEOUT, SendAttemptOutcome.TRANSPORT_ERROR})
RESOLVED_STATES = frozenset(
    {ReconciliationState.CONFIRMED_SENT, ReconciliationState.CONFIRMED_NOT_SENT}
)


class ChannelAuthentication(BaseModel):
    """An unverified request is never normalized into an envelope and never persisted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: ChannelAuthenticationScheme
    verified: Literal[True]
    skew_seconds: int = Field(ge=0)


class ContactBindingRef(BaseModel):
    """Server-resolved identity. `resolved_by` is constant so no payload can supply a binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_user_ref: str = Field(min_length=1, max_length=200)
    contact_id: UUID
    verification_state: ContactVerificationState
    resolved_by: Literal["SERVER_BINDING_TABLE"] = "SERVER_BINDING_TABLE"


class ChannelAttachmentRef(BaseModel):
    """Reference only. Attachment bytes never enter the envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attachment_ref: str = Field(min_length=1, max_length=500)
    declared_media_type: str = Field(min_length=1, max_length=120)
    byte_size: int = Field(ge=0)


class ChannelContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_type: ChannelContentType
    text: str | None = Field(default=None, max_length=4000)
    attachments: tuple[ChannelAttachmentRef, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def unsupported_content_is_not_dropped(self) -> Self:
        if self.content_type is ChannelContentType.TEXT and self.text is None:
            raise ValueError("TEXT content requires normalized text")
        if self.content_type is ChannelContentType.UNSUPPORTED and self.text is not None:
            raise ValueError("UNSUPPORTED content is normalized without inventing text")
        return self


class IngressSuppression(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ingress_decision: IngressSuppressionDecision
    matched_rule_id: str | None = Field(default=None, min_length=1, max_length=120)


class ChannelInboundEnvelope(BaseModel):
    """The one normalized shape every provider maps into before any durable inbox write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    envelope_id: UUID
    provider: ChannelProvider
    provider_update_id: str = Field(min_length=1, max_length=200)
    dedupe_key_source: DedupeKeySource
    received_at: datetime
    provider_sent_at: datetime | None = None
    authentication: ChannelAuthentication
    contact_binding: ContactBindingRef
    content: ChannelContent
    suppression: IngressSuppression

    def canonical_document(self) -> dict[str, Any]:
        """Serialize to the shape the normative JSON Schema accepts.

        Absent optional fields are omitted rather than emitted as null, because the schema types
        `content.text` and `suppression.matched_rule_id` as strings rather than nullable strings. A
        boundary that can emit a document its own contract rejects is not a boundary.
        """
        return cast(dict[str, Any], json.loads(self.model_dump_json(exclude_none=True)))


class SendAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: SendAuthorizationSource
    approval_ref: UUID | None = None
    capability: ReleaseCapability | None = None
    egress_suppression_check: Literal["PASSED_IN_SEND_TRANSACTION"] = "PASSED_IN_SEND_TRANSACTION"
    messaging_window: MessagingWindowState | None = None

    @model_validator(mode="after")
    def authorization_carries_its_evidence(self) -> Self:
        if self.source is SendAuthorizationSource.HUMAN_APPROVAL and self.approval_ref is None:
            raise ValueError("HUMAN_APPROVAL requires approval_ref")
        if self.source is SendAuthorizationSource.CAPABILITY_AUTHORIZED and self.capability is None:
            raise ValueError("CAPABILITY_AUTHORIZED requires capability")
        return self


class SendAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_number: int = Field(ge=1)
    started_at: datetime
    completed_at: datetime | None = None
    outcome: SendAttemptOutcome
    provider_message_ref: str | None = Field(default=None, max_length=200)
    provider_error_code: str | None = Field(default=None, max_length=120)
    rate_limited: bool = False


class ReconciliationResolution(BaseModel):
    """An automatic retry may never resolve an unknown outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resolved_by: Literal["PROVIDER_CONFIRMATION", "HUMAN_DECISION"]
    resolved_at: datetime
    actor_id: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def human_resolution_names_its_actor(self) -> Self:
        if self.resolved_by == "HUMAN_DECISION" and self.actor_id is None:
            raise ValueError("HUMAN_DECISION resolution requires actor_id")
        return self


class ChannelOutboundReceipt(BaseModel):
    """One receipt per send attempt, including rejections, timeouts and transport errors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    receipt_id: UUID
    outbox_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    provider: ChannelProvider
    message_kind: ChannelMessageKind
    authorization: SendAuthorization
    attempt: SendAttempt
    delivery_status: ChannelDeliveryStatus
    reconciliation_state: ReconciliationState
    resolution: ReconciliationResolution | None = None

    @model_validator(mode="after")
    def ambiguity_is_never_settled_silently(self) -> Self:
        if (
            self.attempt.outcome in AMBIGUOUS_OUTCOMES
            and self.reconciliation_state is ReconciliationState.NOT_REQUIRED
        ):
            raise ValueError(
                "an ambiguous provider outcome cannot be reconciled as NOT_REQUIRED; "
                "it is UNKNOWN until a provider confirmation or a human resolves it"
            )
        if self.reconciliation_state in RESOLVED_STATES and self.resolution is None:
            raise ValueError("a resolved reconciliation state requires its resolution record")
        if self.reconciliation_state not in RESOLVED_STATES and self.resolution is not None:
            raise ValueError("only a resolved reconciliation state may carry a resolution record")
        return self

    def canonical_document(self) -> dict[str, Any]:
        """Serialize to the shape the normative JSON Schema accepts; see the envelope's note."""
        return cast(dict[str, Any], json.loads(self.model_dump_json(exclude_none=True)))


__all__ = [
    "AMBIGUOUS_OUTCOMES",
    "RESOLVED_STATES",
    "ChannelAttachmentRef",
    "ChannelAuthentication",
    "ChannelAuthenticationScheme",
    "ChannelContent",
    "ChannelContentType",
    "ChannelDeliveryStatus",
    "ChannelInboundEnvelope",
    "ChannelMessageKind",
    "ChannelOutboundReceipt",
    "ChannelProvider",
    "ContactBindingRef",
    "ContactVerificationState",
    "DedupeKeySource",
    "IngressSuppression",
    "IngressSuppressionDecision",
    "MessagingWindowState",
    "ReconciliationResolution",
    "ReconciliationState",
    "SendAttempt",
    "SendAttemptOutcome",
    "SendAuthorization",
    "SendAuthorizationSource",
]
