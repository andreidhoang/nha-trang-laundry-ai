"""The policy decision taken on every claimed agent run, before the runner may touch a model.

AGENT-SHADOW-DEFECTS-001 F5. `PolicyDecisionPoint.evaluate` -- kill switches, stale policy, the
release manifest, suppression, approval -- had no production caller, and nothing on the agent-run
path read suppression, so invariant #10 ("suppression is checked deterministically before model
use") held nowhere. The runner's own real-customer refusal trusted a classification the enqueuer
chose.

This gate reads the run's facts from PostgreSQL (the derived classification, every suppression
state recorded for the contact, the source event's contact binding, the capability's kill-switch
row) and hands them to the policy point. Anything it cannot read is a denial, never a default.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    VerifiedReleaseAuthorization,
)
from nha_trang_laundry_db.agent_runs import (
    AgentRunPolicyFacts,
    ClaimedAgentRun,
    read_agent_run_policy_facts,
)
from nha_trang_laundry_policy import (
    STAGE_REQUIRED_GATES,
    AgentRunPolicyRequest,
    AuthorityBinding,
    CapabilityPolicySnapshot,
    PolicyDecision,
    PolicyDecisionPoint,
    PolicyOutcome,
    PolicyReason,
    SuppressionState,
)

PolicyFactsReader = Callable[[Any, ClaimedAgentRun], AgentRunPolicyFacts]

#: An inbound event with no contact binding authorizes no contact at all.
_NO_AUTHORIZED_CONTACT = UUID(int=0)


class AgentRunPolicyGate:
    """Server-read facts in, a `PolicyDecision` out. The model is never an input."""

    def __init__(
        self,
        *,
        facts_reader: PolicyFactsReader = read_agent_run_policy_facts,
        policy: PolicyDecisionPoint | None = None,
        release_authorization: VerifiedReleaseAuthorization | None = None,
        deployed_commit_sha: str | None = None,
    ) -> None:
        if (release_authorization is None) != (deployed_commit_sha is None):
            raise ValueError("release authorization and deployed commit SHA go together")
        self._facts_reader = facts_reader
        self._policy = policy or PolicyDecisionPoint()
        self._release_authorization = release_authorization
        self._deployed_commit_sha = deployed_commit_sha

    def decide(
        self,
        connection: Any,
        claimed: ClaimedAgentRun,
        *,
        provider_backed: bool,
        now: datetime,
    ) -> PolicyDecision:
        try:
            facts = self._facts_reader(connection, claimed)
        except Exception:
            return _deny(claimed, PolicyReason.POLICY_STORE_UNAVAILABLE)
        if facts.data_classification is not claimed.data_classification:
            # One row, read twice; a disagreement means something rewrote it in between.
            return _deny(claimed, PolicyReason.POLICY_INPUT_MALFORMED)
        authorized_contact = (
            (facts.source_contact_binding_id or _NO_AUTHORIZED_CONTACT)
            if facts.has_source_event
            else claimed.contact_binding_id
        )
        return self._policy.evaluate_agent_run(
            AgentRunPolicyRequest(
                capability=claimed.capability,
                stage=claimed.deployment_stage,
                data_classification=facts.data_classification,
                provider_backed=provider_backed,
            ),
            self._snapshot(claimed, facts, now),
            AuthorityBinding(
                # `claim_next` admits only AGENT_RUNNER, and the store is a foreign key.
                identity_authorized=True,
                tenant_id=claimed.store_id,
                authorized_tenant_id=claimed.store_id,
                contact_binding_id=claimed.contact_binding_id,
                authorized_contact_binding_id=authorized_contact,
                authorized_capability=claimed.capability,
            ),
            fold_suppression(facts.suppression_states, facts.data_classification),
            now=now,
        )

    def _snapshot(
        self, claimed: ClaimedAgentRun, facts: AgentRunPolicyFacts, now: datetime
    ) -> CapabilityPolicySnapshot | None:
        gate = facts.gate
        if gate is None:
            return None
        version = f"automation-gate-v{gate['version']}"
        switches = ("capability_enabled", "stage_policy_allows", "pdp_allows")
        capability_on = all(gate[name] is True for name in switches)
        authorization = self._release_authorization
        release_authorized = authorization is not None and authorization.authorizes(
            commit_sha=str(self._deployed_commit_sha),
            stage=claimed.deployment_stage.value,
            capability=claimed.capability,
            now=now,
        )
        return CapabilityPolicySnapshot(
            available=True,
            malformed=False,
            # The gate row carries one version; there is no separately published expectation to
            # compare it with, so the version check here proves presence, not agreement.
            policy_version=version,
            expected_policy_version=version,
            expires_at=gate["expires_at"],  # type: ignore[arg-type]
            all_automation_enabled=_flag(gate["global_automation_enabled"]),
            agent_processing_enabled=_flag(gate["agent_processing_enabled"]),
            agent_outbound_enabled=_flag(gate["agent_outbound_enabled"]),
            channel_ingress_enabled=_flag(gate["channel_ingress_enabled"]),
            enabled_capabilities=frozenset({claimed.capability}) if capability_on else frozenset(),
            capability=claimed.capability,
            stage=claimed.deployment_stage,
            required_gates=STAGE_REQUIRED_GATES[claimed.deployment_stage],
            # No store of verified gate evidence exists. Claiming any gate verified here would be
            # a release decision this code has no standing to make.
            verified_gates=frozenset(),
            release_authorized=release_authorized,
            release_capability=authorization.capability if authorization is not None else None,
            release_stage=(
                AgentDeploymentStage(authorization.stage) if authorization is not None else None
            ),
            release_commit_sha=authorization.commit_sha if authorization is not None else None,
            deployed_commit_sha=self._deployed_commit_sha,
        )


def fold_suppression(
    states: tuple[str, ...], classification: AgentDataClassification
) -> SuppressionState:
    """Every recorded state counts; a STOP anywhere for this contact outranks everything else."""

    if "SUPPRESSED" in states:
        return SuppressionState.SUPPRESSED
    if any(state != "CLEAR" for state in states):
        # PENDING_REVIEW_BLOCKED, UNKNOWN_BLOCKED, or a state this code does not know.
        return SuppressionState.UNKNOWN
    if states:
        return SuppressionState.CLEAR
    # Nobody has recorded a consent decision. For a real customer that is unknown, and unknown
    # hands off; a synthetic contact has no consent to record.
    if classification is AgentDataClassification.SYNTHETIC:
        return SuppressionState.NOT_APPLICABLE
    return SuppressionState.UNKNOWN


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _deny(claimed: ClaimedAgentRun, reason: PolicyReason) -> PolicyDecision:
    return PolicyDecision(
        PolicyOutcome.DENY, (reason,), None, claimed.capability, claimed.deployment_stage
    )


__all__ = ["AgentRunPolicyGate", "PolicyFactsReader", "fold_suppression"]
