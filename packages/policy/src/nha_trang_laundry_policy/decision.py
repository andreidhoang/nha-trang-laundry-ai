"""Conjunctive, fail-closed capability policy decision point."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from nha_trang_laundry_contracts import (
    CAPABILITY_OPERATIONS,
    AgentDataClassification,
    AgentDeploymentStage,
    AgentRunnerClaims,
    AgentToolOperation,
    ReleaseCapability,
)

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_GATE = re.compile(r"^G[1-9][A-Z0-9_]{2,99}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class PolicyOutcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"


class PolicyReason(StrEnum):
    POLICY_INPUT_MALFORMED = "POLICY_INPUT_MALFORMED"
    POLICY_STORE_UNAVAILABLE = "POLICY_STORE_UNAVAILABLE"
    POLICY_STALE = "POLICY_STALE"
    POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"
    GLOBAL_AUTOMATION_DISABLED = "GLOBAL_AUTOMATION_DISABLED"
    AGENT_PROCESSING_DISABLED = "AGENT_PROCESSING_DISABLED"
    AGENT_OUTBOUND_DISABLED = "AGENT_OUTBOUND_DISABLED"
    CHANNEL_INGRESS_DISABLED = "CHANNEL_INGRESS_DISABLED"
    CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    STAGE_MISMATCH = "STAGE_MISMATCH"
    STAGE_GATE_UNVERIFIED = "STAGE_GATE_UNVERIFIED"
    RELEASE_NOT_AUTHORIZED = "RELEASE_NOT_AUTHORIZED"
    RELEASE_METADATA_MISMATCH = "RELEASE_METADATA_MISMATCH"
    IDENTITY_UNAUTHORIZED = "IDENTITY_UNAUTHORIZED"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    CONTACT_BINDING_MISMATCH = "CONTACT_BINDING_MISMATCH"
    SUPPRESSED = "SUPPRESSED"
    SUPPRESSION_UNKNOWN = "SUPPRESSION_UNKNOWN"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    OPERATION_NOT_AUTHORIZED = "OPERATION_NOT_AUTHORIZED"
    SYNTHETIC_INTERNAL_ONLY = "SYNTHETIC_INTERNAL_ONLY"
    ALL_CONTROLS_VERIFIED = "ALL_CONTROLS_VERIFIED"


class SuppressionState(StrEnum):
    CLEAR = "CLEAR"
    SUPPRESSED = "SUPPRESSED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ApprovalState(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    SATISFIED = "SATISFIED"
    MISSING = "MISSING"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class CapabilityPolicyRequest:
    capability: ReleaseCapability
    stage: AgentDeploymentStage
    automatic_action: bool


@dataclass(frozen=True, slots=True)
class CapabilityPolicySnapshot:
    available: bool
    malformed: bool
    policy_version: str | None
    expected_policy_version: str
    expires_at: datetime | None
    all_automation_enabled: bool | None
    agent_processing_enabled: bool | None
    agent_outbound_enabled: bool | None
    channel_ingress_enabled: bool | None
    enabled_capabilities: frozenset[ReleaseCapability]
    capability: ReleaseCapability
    stage: AgentDeploymentStage
    required_gates: tuple[str, ...]
    verified_gates: frozenset[str]
    release_authorized: bool
    release_capability: ReleaseCapability | None
    release_stage: AgentDeploymentStage | None
    release_commit_sha: str | None
    deployed_commit_sha: str | None


@dataclass(frozen=True, slots=True)
class AuthorityBinding:
    identity_authorized: bool
    tenant_id: UUID
    authorized_tenant_id: UUID
    contact_binding_id: UUID
    authorized_contact_binding_id: UUID
    authorized_capability: ReleaseCapability


@dataclass(frozen=True, slots=True)
class ObligationState:
    suppression: SuppressionState
    approval: ApprovalState


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outcome: PolicyOutcome
    reason_codes: tuple[PolicyReason, ...]
    policy_version: str | None
    capability: ReleaseCapability
    stage: AgentDeploymentStage

    @property
    def allowed(self) -> bool:
        return self.outcome is PolicyOutcome.ALLOW


class PolicyDecisionPoint:
    """Evaluate immutable server context; model/tool arguments are never accepted here."""

    def evaluate(
        self,
        request: CapabilityPolicyRequest,
        snapshot: CapabilityPolicySnapshot,
        authority: AuthorityBinding,
        obligations: ObligationState,
        *,
        now: datetime,
    ) -> PolicyDecision:
        try:
            return self._evaluate(request, snapshot, authority, obligations, now=now)
        except (AttributeError, TypeError, ValueError):
            capability = (
                request.capability
                if isinstance(request.capability, ReleaseCapability)
                else ReleaseCapability.INTERNAL_SHADOW
            )
            stage = (
                request.stage
                if isinstance(request.stage, AgentDeploymentStage)
                else AgentDeploymentStage.MANUAL_TRUTH
            )
            return PolicyDecision(
                PolicyOutcome.DENY,
                (PolicyReason.POLICY_INPUT_MALFORMED,),
                None,
                capability,
                stage,
            )

    def _evaluate(
        self,
        request: CapabilityPolicyRequest,
        snapshot: CapabilityPolicySnapshot,
        authority: AuthorityBinding,
        obligations: ObligationState,
        *,
        now: datetime,
    ) -> PolicyDecision:
        if not isinstance(request.capability, ReleaseCapability) or not isinstance(
            request.stage, AgentDeploymentStage
        ):
            raise TypeError("invalid request enum")
        if not isinstance(request.automatic_action, bool) or now.tzinfo is None:
            raise TypeError("invalid policy request")
        self._validate_shapes(snapshot, authority, obligations)
        if snapshot.malformed:
            return self._deny(request, snapshot, PolicyReason.POLICY_INPUT_MALFORMED)
        if not snapshot.available:
            return self._deny(request, snapshot, PolicyReason.POLICY_STORE_UNAVAILABLE)
        if (
            snapshot.expires_at is None
            or snapshot.expires_at.tzinfo is None
            or snapshot.expires_at < now
        ):
            return self._deny(request, snapshot, PolicyReason.POLICY_STALE)
        if (
            snapshot.policy_version is None
            or not _VERSION.fullmatch(snapshot.policy_version)
            or not _VERSION.fullmatch(snapshot.expected_policy_version)
            or snapshot.policy_version != snapshot.expected_policy_version
        ):
            return self._deny(request, snapshot, PolicyReason.POLICY_VERSION_MISMATCH)

        denial_reasons: list[PolicyReason] = []
        human_reasons: list[PolicyReason] = []
        controls = (
            (snapshot.all_automation_enabled, PolicyReason.GLOBAL_AUTOMATION_DISABLED),
            (snapshot.agent_processing_enabled, PolicyReason.AGENT_PROCESSING_DISABLED),
            (snapshot.agent_outbound_enabled, PolicyReason.AGENT_OUTBOUND_DISABLED),
            (snapshot.channel_ingress_enabled, PolicyReason.CHANNEL_INGRESS_DISABLED),
        )
        denial_reasons.extend(reason for enabled, reason in controls if enabled is not True)
        if (
            request.capability not in snapshot.enabled_capabilities
            or snapshot.capability is not request.capability
        ):
            denial_reasons.append(PolicyReason.CAPABILITY_DISABLED)
        if authority.authorized_capability is not request.capability:
            denial_reasons.append(PolicyReason.CAPABILITY_MISMATCH)
        if snapshot.stage is not request.stage or snapshot.release_stage is not request.stage:
            denial_reasons.append(PolicyReason.STAGE_MISMATCH)
        if not self._valid_gates(snapshot.required_gates, snapshot.verified_gates):
            denial_reasons.append(PolicyReason.STAGE_GATE_UNVERIFIED)
        if not snapshot.release_authorized:
            denial_reasons.append(PolicyReason.RELEASE_NOT_AUTHORIZED)
        if (
            snapshot.release_capability is not request.capability
            or snapshot.release_commit_sha is None
            or snapshot.deployed_commit_sha is None
            or not _COMMIT.fullmatch(snapshot.release_commit_sha)
            or snapshot.release_commit_sha != snapshot.deployed_commit_sha
        ):
            denial_reasons.append(PolicyReason.RELEASE_METADATA_MISMATCH)
        if not authority.identity_authorized:
            denial_reasons.append(PolicyReason.IDENTITY_UNAUTHORIZED)
        if authority.tenant_id != authority.authorized_tenant_id:
            denial_reasons.append(PolicyReason.TENANT_MISMATCH)
        if authority.contact_binding_id != authority.authorized_contact_binding_id:
            denial_reasons.append(PolicyReason.CONTACT_BINDING_MISMATCH)
        if obligations.suppression is SuppressionState.SUPPRESSED:
            denial_reasons.append(PolicyReason.SUPPRESSED)
        elif obligations.suppression is SuppressionState.UNKNOWN:
            human_reasons.append(PolicyReason.SUPPRESSION_UNKNOWN)
        if obligations.approval is ApprovalState.INVALID:
            denial_reasons.append(PolicyReason.APPROVAL_INVALID)
        elif obligations.approval is ApprovalState.MISSING:
            human_reasons.append(PolicyReason.APPROVAL_REQUIRED)

        if denial_reasons:
            return PolicyDecision(
                PolicyOutcome.DENY,
                tuple(dict.fromkeys(denial_reasons)),
                snapshot.policy_version,
                request.capability,
                request.stage,
            )
        if human_reasons:
            return PolicyDecision(
                PolicyOutcome.REQUIRE_HUMAN,
                tuple(dict.fromkeys(human_reasons)),
                snapshot.policy_version,
                request.capability,
                request.stage,
            )
        return PolicyDecision(
            PolicyOutcome.ALLOW,
            (PolicyReason.ALL_CONTROLS_VERIFIED,),
            snapshot.policy_version,
            request.capability,
            request.stage,
        )

    def evaluate_synthetic_tool(
        self, claims: AgentRunnerClaims, operation: AgentToolOperation
    ) -> PolicyDecision:
        """Permit only a local, non-release synthetic Shadow tool boundary."""
        capability = claims.capabilities[0]
        allowed = (
            claims.data_classification is AgentDataClassification.SYNTHETIC
            and claims.stage is AgentDeploymentStage.SHADOW
            and capability is ReleaseCapability.INTERNAL_SHADOW
            and operation in CAPABILITY_OPERATIONS[capability]
        )
        return PolicyDecision(
            PolicyOutcome.ALLOW if allowed else PolicyOutcome.DENY,
            (
                PolicyReason.SYNTHETIC_INTERNAL_ONLY
                if allowed
                else PolicyReason.OPERATION_NOT_AUTHORIZED,
            ),
            "synthetic-internal-v1",
            capability,
            claims.stage,
        )

    @staticmethod
    def _valid_gates(required: tuple[str, ...], verified: frozenset[str]) -> bool:
        if not required or any(
            not isinstance(gate, str) or not _GATE.fullmatch(gate) for gate in required
        ):
            return False
        return set(required).issubset(verified)

    @staticmethod
    def _validate_shapes(
        snapshot: CapabilityPolicySnapshot,
        authority: AuthorityBinding,
        obligations: ObligationState,
    ) -> None:
        if not isinstance(snapshot.available, bool) or not isinstance(snapshot.malformed, bool):
            raise TypeError("invalid policy availability")
        controls = (
            snapshot.all_automation_enabled,
            snapshot.agent_processing_enabled,
            snapshot.agent_outbound_enabled,
            snapshot.channel_ingress_enabled,
        )
        if any(value is not None and not isinstance(value, bool) for value in controls):
            raise TypeError("invalid policy control")
        if (
            not isinstance(snapshot.enabled_capabilities, frozenset)
            or any(
                not isinstance(capability, ReleaseCapability)
                for capability in snapshot.enabled_capabilities
            )
            or not isinstance(snapshot.capability, ReleaseCapability)
            or not isinstance(snapshot.stage, AgentDeploymentStage)
            or not isinstance(snapshot.required_gates, tuple)
            or not isinstance(snapshot.verified_gates, frozenset)
            or any(not isinstance(gate, str) for gate in snapshot.verified_gates)
            or not isinstance(snapshot.release_authorized, bool)
            or (
                snapshot.release_capability is not None
                and not isinstance(snapshot.release_capability, ReleaseCapability)
            )
            or (
                snapshot.release_stage is not None
                and not isinstance(snapshot.release_stage, AgentDeploymentStage)
            )
        ):
            raise TypeError("invalid policy snapshot")
        if (
            not isinstance(authority.identity_authorized, bool)
            or not all(
                isinstance(value, UUID)
                for value in (
                    authority.tenant_id,
                    authority.authorized_tenant_id,
                    authority.contact_binding_id,
                    authority.authorized_contact_binding_id,
                )
            )
            or not isinstance(authority.authorized_capability, ReleaseCapability)
            or not isinstance(obligations.suppression, SuppressionState)
            or not isinstance(obligations.approval, ApprovalState)
        ):
            raise TypeError("invalid authority or obligation")

    @staticmethod
    def _deny(
        request: CapabilityPolicyRequest,
        snapshot: CapabilityPolicySnapshot,
        reason: PolicyReason,
    ) -> PolicyDecision:
        return PolicyDecision(
            PolicyOutcome.DENY,
            (reason,),
            snapshot.policy_version,
            request.capability,
            request.stage,
        )
