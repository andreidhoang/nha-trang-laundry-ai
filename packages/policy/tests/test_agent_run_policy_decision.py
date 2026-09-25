"""AGENT-SHADOW-DEFECTS-001 F5: `evaluate_agent_run`, the agent-run path's policy decision."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    ReleaseCapability,
)
from nha_trang_laundry_policy import (
    STAGE_REQUIRED_GATES,
    AgentRunPolicyRequest,
    AuthorityBinding,
    CapabilityPolicySnapshot,
    PolicyDecisionPoint,
    PolicyOutcome,
    PolicyReason,
    SuppressionState,
)

NOW = datetime(2026, 9, 25, 3, tzinfo=UTC)
STORE = UUID("00000000-0000-0000-0000-000000000801")
CONTACT = UUID("00000000-0000-0000-0000-000000000802")
OTHER = UUID("00000000-0000-0000-0000-000000000803")
COMMIT = "b" * 40


def synthetic(**overrides: Any) -> AgentRunPolicyRequest:
    values: dict[str, Any] = {
        "capability": ReleaseCapability.INTERNAL_SHADOW,
        "stage": AgentDeploymentStage.SHADOW,
        "data_classification": AgentDataClassification.SYNTHETIC,
        "provider_backed": False,
    }
    values.update(overrides)
    return AgentRunPolicyRequest(**values)


def authority(**overrides: Any) -> AuthorityBinding:
    values: dict[str, Any] = {
        "identity_authorized": True,
        "tenant_id": STORE,
        "authorized_tenant_id": STORE,
        "contact_binding_id": CONTACT,
        "authorized_contact_binding_id": CONTACT,
        "authorized_capability": ReleaseCapability.INTERNAL_SHADOW,
    }
    values.update(overrides)
    return AuthorityBinding(**values)


def everything_on() -> CapabilityPolicySnapshot:
    """A snapshot in which every control passes; only the case under test is changed."""
    return CapabilityPolicySnapshot(
        available=True,
        malformed=False,
        policy_version="automation-gate-v1",
        expected_policy_version="automation-gate-v1",
        expires_at=NOW + timedelta(minutes=5),
        all_automation_enabled=True,
        agent_processing_enabled=True,
        agent_outbound_enabled=True,
        channel_ingress_enabled=True,
        enabled_capabilities=frozenset({ReleaseCapability.INTERNAL_SHADOW}),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        stage=AgentDeploymentStage.SHADOW,
        required_gates=STAGE_REQUIRED_GATES[AgentDeploymentStage.SHADOW],
        verified_gates=frozenset(STAGE_REQUIRED_GATES[AgentDeploymentStage.SHADOW]),
        release_authorized=True,
        release_capability=ReleaseCapability.INTERNAL_SHADOW,
        release_stage=AgentDeploymentStage.SHADOW,
        release_commit_sha=COMMIT,
        deployed_commit_sha=COMMIT,
    )


def decide(
    request: AgentRunPolicyRequest,
    suppression: SuppressionState,
    *,
    snapshot: CapabilityPolicySnapshot | None = None,
    binding: AuthorityBinding | None = None,
) -> Any:
    return PolicyDecisionPoint().evaluate_agent_run(
        request, snapshot, binding or authority(), suppression, now=NOW
    )


def test_a_local_synthetic_shadow_run_with_no_consent_record_is_allowed() -> None:
    decision = decide(synthetic(), SuppressionState.NOT_APPLICABLE)

    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.reason_codes == (PolicyReason.SYNTHETIC_INTERNAL_ONLY,)
    assert decision.policy_version == "synthetic-internal-v1"


@pytest.mark.parametrize(
    "request_",
    [
        synthetic(),
        synthetic(data_classification=AgentDataClassification.REAL_CUSTOMER),
        synthetic(provider_backed=True),
    ],
)
def test_suppression_denies_before_anything_else(request_: AgentRunPolicyRequest) -> None:
    decision = decide(request_, SuppressionState.SUPPRESSED, snapshot=everything_on())

    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason_codes == (PolicyReason.SUPPRESSED,)


def test_an_unknown_suppression_state_hands_a_synthetic_run_to_a_human() -> None:
    decision = decide(synthetic(), SuppressionState.UNKNOWN)

    assert decision.outcome is PolicyOutcome.REQUIRE_HUMAN
    assert decision.reason_codes == (PolicyReason.SUPPRESSION_UNKNOWN,)


@pytest.mark.parametrize(
    ("binding", "reason"),
    [
        (authority(authorized_contact_binding_id=OTHER), PolicyReason.CONTACT_BINDING_MISMATCH),
        (authority(authorized_tenant_id=OTHER), PolicyReason.TENANT_MISMATCH),
        (authority(identity_authorized=False), PolicyReason.IDENTITY_UNAUTHORIZED),
        (
            authority(authorized_capability=ReleaseCapability.PUBLIC_FAQ),
            PolicyReason.CAPABILITY_MISMATCH,
        ),
    ],
)
def test_a_synthetic_run_still_needs_its_authority_binding(
    binding: AuthorityBinding, reason: PolicyReason
) -> None:
    decision = decide(synthetic(), SuppressionState.NOT_APPLICABLE, binding=binding)

    assert decision.outcome is PolicyOutcome.DENY
    assert reason in decision.reason_codes


@pytest.mark.parametrize(
    "request_",
    [
        synthetic(data_classification=AgentDataClassification.REAL_CUSTOMER),
        synthetic(provider_backed=True),
        synthetic(capability=ReleaseCapability.PUBLIC_FAQ),
        synthetic(stage=AgentDeploymentStage.ASSISTED),
    ],
)
def test_anything_but_local_synthetic_shadow_needs_the_full_conjunctive_policy(
    request_: AgentRunPolicyRequest,
) -> None:
    missing = decide(request_, SuppressionState.CLEAR)

    assert missing.outcome is PolicyOutcome.DENY
    assert missing.reason_codes == (PolicyReason.POLICY_STORE_UNAVAILABLE,)


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        (replace(everything_on(), agent_processing_enabled=False), "AGENT_PROCESSING_DISABLED"),
        (replace(everything_on(), all_automation_enabled=None), "GLOBAL_AUTOMATION_DISABLED"),
        (replace(everything_on(), expires_at=NOW - timedelta(seconds=1)), "POLICY_STALE"),
        (replace(everything_on(), verified_gates=frozenset()), "STAGE_GATE_UNVERIFIED"),
        (replace(everything_on(), release_authorized=False), "RELEASE_NOT_AUTHORIZED"),
    ],
)
def test_a_real_customer_run_is_bound_by_every_kill_switch_and_the_release(
    changed: CapabilityPolicySnapshot, reason: str
) -> None:
    request_ = synthetic(data_classification=AgentDataClassification.REAL_CUSTOMER)

    assert decide(request_, SuppressionState.CLEAR, snapshot=everything_on()).allowed
    decision = decide(request_, SuppressionState.CLEAR, snapshot=changed)

    assert decision.outcome is PolicyOutcome.DENY
    assert reason in {code.value for code in decision.reason_codes}


def test_a_real_customer_with_no_consent_record_is_handed_to_a_human() -> None:
    request_ = synthetic(data_classification=AgentDataClassification.REAL_CUSTOMER)

    decision = decide(request_, SuppressionState.UNKNOWN, snapshot=everything_on())

    assert decision.outcome is PolicyOutcome.REQUIRE_HUMAN
    assert decision.reason_codes == (PolicyReason.SUPPRESSION_UNKNOWN,)


def test_malformed_input_is_denied() -> None:
    bad = cast(Any, synthetic())
    object.__setattr__(bad, "provider_backed", "no")

    decision = decide(bad, SuppressionState.NOT_APPLICABLE)

    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason_codes == (PolicyReason.POLICY_INPUT_MALFORMED,)
