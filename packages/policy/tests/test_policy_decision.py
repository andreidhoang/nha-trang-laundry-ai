from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    AgentRunnerClaims,
    AgentToolOperation,
    ReleaseCapability,
)
from nha_trang_laundry_policy import (
    ApprovalState,
    AuthorityBinding,
    CapabilityPolicyRequest,
    CapabilityPolicySnapshot,
    ObligationState,
    PolicyDecisionPoint,
    PolicyOutcome,
    PolicyReason,
    SuppressionState,
)

NOW = datetime(2026, 8, 1, 3, tzinfo=UTC)
TENANT_ID = UUID("00000000-0000-0000-0000-000000000701")
CONTACT_ID = UUID("00000000-0000-0000-0000-000000000702")
COMMIT = "a" * 40


def request() -> CapabilityPolicyRequest:
    return CapabilityPolicyRequest(
        ReleaseCapability.LIST_PRICE_INFO,
        AgentDeploymentStage.ASSISTED,
        True,
    )


def snapshot() -> CapabilityPolicySnapshot:
    return CapabilityPolicySnapshot(
        available=True,
        malformed=False,
        policy_version="policy-test-v1",
        expected_policy_version="policy-test-v1",
        expires_at=NOW + timedelta(minutes=5),
        all_automation_enabled=True,
        agent_processing_enabled=True,
        agent_outbound_enabled=True,
        channel_ingress_enabled=True,
        enabled_capabilities=frozenset({ReleaseCapability.LIST_PRICE_INFO}),
        capability=ReleaseCapability.LIST_PRICE_INFO,
        stage=AgentDeploymentStage.ASSISTED,
        required_gates=("G1_INTERNAL_SHADOW_READY", "G2_PUBLIC_ASSISTED_ENTRY"),
        verified_gates=frozenset({"G1_INTERNAL_SHADOW_READY", "G2_PUBLIC_ASSISTED_ENTRY"}),
        release_authorized=True,
        release_capability=ReleaseCapability.LIST_PRICE_INFO,
        release_stage=AgentDeploymentStage.ASSISTED,
        release_commit_sha=COMMIT,
        deployed_commit_sha=COMMIT,
    )


def authority() -> AuthorityBinding:
    return AuthorityBinding(
        identity_authorized=True,
        tenant_id=TENANT_ID,
        authorized_tenant_id=TENANT_ID,
        contact_binding_id=CONTACT_ID,
        authorized_contact_binding_id=CONTACT_ID,
        authorized_capability=ReleaseCapability.LIST_PRICE_INFO,
    )


def obligations() -> ObligationState:
    return ObligationState(SuppressionState.CLEAR, ApprovalState.SATISFIED)


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        (
            replace(snapshot(), all_automation_enabled=False),
            PolicyReason.GLOBAL_AUTOMATION_DISABLED,
        ),
        (
            replace(snapshot(), all_automation_enabled=None),
            PolicyReason.GLOBAL_AUTOMATION_DISABLED,
        ),
        (
            replace(snapshot(), agent_processing_enabled=False),
            PolicyReason.AGENT_PROCESSING_DISABLED,
        ),
        (
            replace(snapshot(), agent_outbound_enabled=False),
            PolicyReason.AGENT_OUTBOUND_DISABLED,
        ),
        (
            replace(snapshot(), channel_ingress_enabled=False),
            PolicyReason.CHANNEL_INGRESS_DISABLED,
        ),
        (replace(snapshot(), enabled_capabilities=frozenset()), PolicyReason.CAPABILITY_DISABLED),
        (replace(snapshot(), verified_gates=frozenset()), PolicyReason.STAGE_GATE_UNVERIFIED),
        (replace(snapshot(), release_authorized=False), PolicyReason.RELEASE_NOT_AUTHORIZED),
    ],
)
def test_every_conjunctive_control_fails_closed(
    changed: CapabilityPolicySnapshot, reason: PolicyReason
) -> None:
    decision = PolicyDecisionPoint().evaluate(
        request(), changed, authority(), obligations(), now=NOW
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert reason in decision.reason_codes


def test_expired_missing_malformed_and_unavailable_policy_fail_closed() -> None:
    cases = (
        (replace(snapshot(), available=False), PolicyReason.POLICY_STORE_UNAVAILABLE),
        (replace(snapshot(), malformed=True), PolicyReason.POLICY_INPUT_MALFORMED),
        (replace(snapshot(), expires_at=None), PolicyReason.POLICY_STALE),
        (replace(snapshot(), expires_at=NOW - timedelta(seconds=1)), PolicyReason.POLICY_STALE),
        (
            replace(snapshot(), policy_version="other-version"),
            PolicyReason.POLICY_VERSION_MISMATCH,
        ),
    )

    for changed, reason in cases:
        decision = PolicyDecisionPoint().evaluate(
            request(), changed, authority(), obligations(), now=NOW
        )
        assert decision.outcome is PolicyOutcome.DENY
        assert decision.reason_codes == (reason,)


def test_malformed_boolean_type_does_not_become_truthy_authority() -> None:
    changed = replace(snapshot(), all_automation_enabled=cast(bool, "true"))

    decision = PolicyDecisionPoint().evaluate(
        request(), changed, authority(), obligations(), now=NOW
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason_codes == (PolicyReason.POLICY_INPUT_MALFORMED,)


def test_malformed_capability_collection_cannot_enable_a_named_capability() -> None:
    changed = replace(
        snapshot(),
        enabled_capabilities=cast(frozenset[ReleaseCapability], frozenset({"LIST_PRICE_INFO"})),
    )

    decision = PolicyDecisionPoint().evaluate(
        request(), changed, authority(), obligations(), now=NOW
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason_codes == (PolicyReason.POLICY_INPUT_MALFORMED,)


def test_release_stage_capability_and_commit_mismatch_are_denied() -> None:
    changed = replace(
        snapshot(),
        release_capability=ReleaseCapability.PUBLIC_FAQ,
        release_stage=AgentDeploymentStage.BOUNDED,
        deployed_commit_sha="b" * 40,
    )

    decision = PolicyDecisionPoint().evaluate(
        request(), changed, authority(), obligations(), now=NOW
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert PolicyReason.STAGE_MISMATCH in decision.reason_codes
    assert PolicyReason.RELEASE_METADATA_MISMATCH in decision.reason_codes


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        (
            replace(
                authority(),
                authorized_capability=ReleaseCapability.PUBLIC_FAQ,
            ),
            PolicyReason.CAPABILITY_MISMATCH,
        ),
        (
            replace(
                authority(),
                authorized_tenant_id=UUID("00000000-0000-0000-0000-000000000799"),
            ),
            PolicyReason.TENANT_MISMATCH,
        ),
        (
            replace(
                authority(),
                authorized_contact_binding_id=UUID("00000000-0000-0000-0000-000000000798"),
            ),
            PolicyReason.CONTACT_BINDING_MISMATCH,
        ),
        (replace(authority(), identity_authorized=False), PolicyReason.IDENTITY_UNAUTHORIZED),
    ],
)
def test_server_authority_substitution_is_denied(
    changed: AuthorityBinding, reason: PolicyReason
) -> None:
    decision = PolicyDecisionPoint().evaluate(
        request(), snapshot(), changed, obligations(), now=NOW
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert reason in decision.reason_codes


@pytest.mark.parametrize(
    ("changed", "outcome", "reason"),
    [
        (
            ObligationState(SuppressionState.SUPPRESSED, ApprovalState.SATISFIED),
            PolicyOutcome.DENY,
            PolicyReason.SUPPRESSED,
        ),
        (
            ObligationState(SuppressionState.UNKNOWN, ApprovalState.SATISFIED),
            PolicyOutcome.REQUIRE_HUMAN,
            PolicyReason.SUPPRESSION_UNKNOWN,
        ),
        (
            ObligationState(SuppressionState.CLEAR, ApprovalState.MISSING),
            PolicyOutcome.REQUIRE_HUMAN,
            PolicyReason.APPROVAL_REQUIRED,
        ),
        (
            ObligationState(SuppressionState.CLEAR, ApprovalState.INVALID),
            PolicyOutcome.DENY,
            PolicyReason.APPROVAL_INVALID,
        ),
    ],
)
def test_suppression_and_approval_obligations_fail_closed(
    changed: ObligationState, outcome: PolicyOutcome, reason: PolicyReason
) -> None:
    decision = PolicyDecisionPoint().evaluate(request(), snapshot(), authority(), changed, now=NOW)

    assert decision.outcome is outcome
    assert reason in decision.reason_codes


def test_all_explicit_server_controls_allow_exact_capability_only() -> None:
    decision = PolicyDecisionPoint().evaluate(
        request(), snapshot(), authority(), obligations(), now=NOW
    )

    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.reason_codes == (PolicyReason.ALL_CONTROLS_VERIFIED,)


def test_only_synthetic_internal_shadow_tool_path_bypasses_release_evidence() -> None:
    claims = AgentRunnerClaims.model_validate(
        {
            "iss": "https://control-plane.test",
            "aud": "agent-tool-facade",
            "sub": "AGENT_RUNNER",
            "iat": 1,
            "exp": 21,
            "jti": "00000000-0000-0000-0000-000000000710",
            "run_id": "00000000-0000-0000-0000-000000000711",
            "organization_id": str(TENANT_ID),
            "store_id": "00000000-0000-0000-0000-000000000712",
            "channel": "INTERNAL_TEST",
            "conversation_binding_id": "00000000-0000-0000-0000-000000000713",
            "contact_binding_id": str(CONTACT_ID),
            "capabilities": ["INTERNAL_SHADOW"],
            "stage": "SHADOW",
            "data_classification": "SYNTHETIC",
        }
    )

    allowed = PolicyDecisionPoint().evaluate_synthetic_tool(
        claims, AgentToolOperation.CATALOG_RESOLVE
    )
    real_customer = claims.model_copy(
        update={"data_classification": AgentDataClassification.REAL_CUSTOMER}
    )
    denied = PolicyDecisionPoint().evaluate_synthetic_tool(
        real_customer, AgentToolOperation.CATALOG_RESOLVE
    )

    assert allowed.outcome is PolicyOutcome.ALLOW
    assert allowed.reason_codes == (PolicyReason.SYNTHETIC_INTERNAL_ONLY,)
    assert denied.outcome is PolicyOutcome.DENY
    assert denied.reason_codes == (PolicyReason.OPERATION_NOT_AUTHORIZED,)
