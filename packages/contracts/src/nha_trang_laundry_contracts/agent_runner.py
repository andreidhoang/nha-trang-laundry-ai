"""Typed server-owned run bindings shared by Agent Runner and Tool Facade."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .runtime_registry import ReleaseCapability
from .tool_registry import AgentToolOperation


class AgentDeploymentStage(StrEnum):
    MANUAL_TRUTH = "MANUAL_TRUTH"
    SHADOW = "SHADOW"
    ASSISTED = "ASSISTED"
    BOUNDED = "BOUNDED"


class AgentDataClassification(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    REAL_CUSTOMER = "REAL_CUSTOMER"


class AgentRunnerClaims(BaseModel):
    """Server-issued claims. No field in this model comes from LLM tool arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    iss: str
    aud: str
    sub: Literal["AGENT_RUNNER"]
    iat: int
    exp: int
    jti: UUID
    run_id: UUID
    organization_id: UUID
    store_id: UUID
    channel: str = Field(min_length=1, max_length=50, pattern=r"^[A-Z0-9_]+$")
    conversation_binding_id: UUID
    contact_binding_id: UUID
    capabilities: tuple[ReleaseCapability, ...] = Field(min_length=1, max_length=1)
    stage: AgentDeploymentStage
    data_classification: AgentDataClassification
    order_request_id: UUID | None = None
    public_code: str | None = Field(default=None, min_length=16, max_length=64)

    @model_validator(mode="after")
    def claim_set_is_consistent(self) -> AgentRunnerClaims:
        if self.exp <= self.iat or self.exp - self.iat > 60:
            raise ValueError("runner bearer lifetime must be between 1 and 60 seconds")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("runner bearer capabilities must be unique")
        return self


CAPABILITY_OPERATIONS: dict[ReleaseCapability, frozenset[AgentToolOperation]] = {
    ReleaseCapability.INTERNAL_SHADOW: frozenset(AgentToolOperation),
    ReleaseCapability.PUBLIC_FAQ: frozenset({AgentToolOperation.CATALOG_RESOLVE}),
    ReleaseCapability.LIST_PRICE_INFO: frozenset(
        {AgentToolOperation.CATALOG_RESOLVE, AgentToolOperation.MESSAGE_DRAFT_CREATE}
    ),
    ReleaseCapability.INTAKE_QUESTION: frozenset(
        {AgentToolOperation.CATALOG_RESOLVE, AgentToolOperation.MESSAGE_DRAFT_CREATE}
    ),
    ReleaseCapability.INTAKE_FACT_CAPTURE: frozenset(
        {
            AgentToolOperation.CATALOG_RESOLVE,
            AgentToolOperation.ORDER_REQUEST_CREATE,
            AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS,
        }
    ),
    ReleaseCapability.INTAKE_RECEIPT: frozenset({AgentToolOperation.MESSAGE_DRAFT_CREATE}),
    ReleaseCapability.INCIDENT_RECEIPT: frozenset(
        {AgentToolOperation.INCIDENT_OPEN, AgentToolOperation.MESSAGE_DRAFT_CREATE}
    ),
    ReleaseCapability.ORDER_STATUS: frozenset(
        {AgentToolOperation.PUBLIC_ORDER_STATUS_GET, AgentToolOperation.MESSAGE_DRAFT_CREATE}
    ),
    ReleaseCapability.SLA_GUIDANCE: frozenset(
        {AgentToolOperation.CAPACITY_CHECK, AgentToolOperation.MESSAGE_DRAFT_CREATE}
    ),
    ReleaseCapability.QUOTE_ESTIMATE: frozenset(
        {
            AgentToolOperation.QUOTE_ESTIMATE,
            AgentToolOperation.DELIVERY_EVALUATE,
            AgentToolOperation.MESSAGE_DRAFT_CREATE,
            AgentToolOperation.APPROVAL_REQUEST_CREATE,
        }
    ),
    ReleaseCapability.BOOKING: frozenset(
        {AgentToolOperation.CAPACITY_CHECK, AgentToolOperation.APPROVAL_REQUEST_CREATE}
    ),
    ReleaseCapability.DELIVERY_ADVISORY: frozenset(
        {AgentToolOperation.DELIVERY_EVALUATE, AgentToolOperation.MESSAGE_DRAFT_CREATE}
    ),
    ReleaseCapability.MARKETING_FOLLOWUP: frozenset(),
}


def operation_is_authorized(claims: AgentRunnerClaims, operation: AgentToolOperation) -> bool:
    return any(operation in CAPABILITY_OPERATIONS[capability] for capability in claims.capabilities)
