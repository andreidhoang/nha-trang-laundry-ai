from __future__ import annotations

import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def load_context_drift_module() -> ModuleType:
    spec = spec_from_file_location(
        "check_context_drift_for_test", ROOT / "scripts/check_context_drift.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load context drift validator")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_context_drift_check_passes() -> None:
    result = run_script("scripts/check_context_drift.py")

    assert result.returncode == 0, result.stderr
    assert "Context drift check passed" in result.stdout
    assert "4 gates" in result.stdout
    assert "9 phases" in result.stdout
    # 36 baseline items, plus 19 production-path items from ADR-0004 through ADR-0007, plus
    # AGENT-PIPELINE-001 and SHADOW-CONSOLE-001 from the 2026-08-12 readiness assessment, plus the
    # seven items the 2026-08-13 gate-coverage audit found unowned: ENV-INTEGRITY-001, the three
    # eval-suite minima, RETENTION-001, OPS-RUNBOOK-001 and SLO-VERIFY-001, plus the three the
    # execution pass surfaced: EVIDENCE-REPIN-001, RETENTION-STORE-001, STORE-SCOPING-001
    # and TEST-ISOLATION-001, plus MODEL-ROUTE-001 and MULTIMODAL-PERCEPTION-001 from the
    # 2026-08-13 tiered-inference assessment (ADR-0008), plus the two authorization gaps the
    # 2026-08-14 staff-console work found by reading the route table against the repositories:
    # STORE-SCOPING-002, the order-transition route that STORE-SCOPING-001's URL-shape enumeration
    # could not see because it is keyed by order_id, and STORE-ASSIGNMENT-001, membership having no
    # write path at all, plus ASSISTANT-001, the owner-directed internal assistant registered after
    # the fact on 2026-08-16, plus the two the 2026-08-18 harness session enqueued after measuring
    # the gaps they close: SPEC-ROUTE-SURFACE-001, because 38 operations were served and exactly one
    # was named anywhere in specs/, and DISCLOSURE-CONTRACT-001, because 58 disclosure slots in
    # apps/web make factual claims about system behaviour and none appears in any test. The
    # eightieth is DISCLOSURE-BIND-002, a corrective item: DISCLOSURE-CONTRACT-001 was completed
    # with four SERVER_GATE bindings that could not fail, proven by mutation, so the corrective
    # route CONTINUATION_PROTOCOL.md requires for an immutable COMPLETE item was opened. The
    # eighty-first and eighty-second are CONSOLE-ORDER-GAP-001 and ASSISTANT-BRAIN-002, both
    # corrective: a concurrent session driving the live stack proved no order can be created at all
    # because no quote can reach APPROVED_EXACT, and that a money question naming a timeframe is
    # answered with an order count instead of refused. The eighty-third is
    # QUOTE-APPROVAL-INTEGRITY-001, opened by the owner on 2026-08-22: quote_revisions.approval_id
    # was required to be non-null for an approved exact price and carried no foreign key, so the
    # shipped eval fixture generator wrote a completed order citing an approval envelope that had
    # never been requested. It is the part of DEC-021 the decision packet itself classifies as a
    # schema correction rather than a policy question, which is why it lands before the decision.
    # The eighty-fourth is DISCLOSURE-TRUTH-003, the fix an xfail(strict=True) marker had been
    # carrying as a reported-not-fixed incident: the settlement screen told the shop owner that
    # partial payment and credit are refused with a decision code that was still open, when DEC-010
    # had been resolved four days earlier as deliberately deferred. The eighty-fifth is
    # DECISION-GATE-TRUTH-001, found while triaging which open decisions actually cost anything:
    # six non-complete items were gated on decisions already resolved, and RETENTION-STORE-001 named
    # only a resolved one while its own prose named two open ones, leaving a hand-set BLOCKED status
    # as the only thing between the controller and work two open decisions forbid. The eighty-sixth
    # is QUOTE-DELIVERY-FEE-001: every quote the shop could produce carried no delivery fee and
    # therefore no total, because compose_quote_revision never called evaluate_delivery, whose
    # answers the owner had already ratified. The shop could price laundry and not tell a customer
    # the price. The eighty-seventh is QUOTE-ACCEPT-001, which implements DEC-021 and DEC-022 as
    # the owner ratified them on 2026-08-25 and produces the first quote revision order creation
    # will accept -- the first order in this system's history was created on that day.
    assert "87 work items" in result.stdout
    assert "13 capabilities" in result.stdout


def test_context_packet_contains_continuation_protocol() -> None:
    result = run_script(
        "scripts/assemble_context.py", "--task-id", "DOMAIN-001", "--domain", "pricing"
    )

    assert result.returncode == 0, result.stderr
    assert "context/CONTINUATION_PROTOCOL.md" in result.stdout


def test_context_packet_contains_agent_tool_contract() -> None:
    result = run_script(
        "scripts/assemble_context.py",
        "--task-id",
        "TASK-agent-001",
        "--domain",
        "agent_tools",
        "--domain",
        "runtime_architecture",
    )

    assert result.returncode == 0, result.stderr
    assert "specs/contracts/agent-tools-v1.openapi.yaml" in result.stdout
    assert "direct-send capability" in result.stdout
    assert "docs/adr/0002-production-agent-runtime-and-trust-boundaries.md" in result.stdout


def test_delivery_status_report_marks_capabilities_unauthorized() -> None:
    result = run_script("scripts/report_delivery_status.py")

    assert result.returncode == 0, result.stderr
    assert "INTERNAL_SHADOW | authorization=NOT_AUTHORIZED" in result.stdout
    assert "MARKETING_FOLLOWUP | authorization=NOT_AUTHORIZED" in result.stdout
    assert len([line for line in result.stdout.splitlines() if " | authorization=" in line]) == 13


def test_authorized_capability_requires_cryptographic_release_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check_context_drift = load_context_drift_module()
    _, gate_requirements = check_context_drift.validate_gate_registry()
    status = yaml.safe_load((ROOT / "delivery/CAPABILITY_STATUS.yaml").read_text(encoding="utf-8"))
    status["capabilities"][0]["authorization"] = "AUTHORIZED"
    status["capabilities"][0]["release_manifest"] = (
        "specs/contracts/release-gate-manifest-v1.schema.json"
    )
    monkeypatch.setattr(check_context_drift, "load_yaml", lambda _: status)

    with pytest.raises(ValueError, match="requires verified release metadata"):
        check_context_drift.validate_capability_status(gate_requirements)


def test_authorized_capability_requires_out_of_band_deployment_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check_context_drift = load_context_drift_module()
    _, gate_requirements = check_context_drift.validate_gate_registry()
    status = yaml.safe_load((ROOT / "delivery/CAPABILITY_STATUS.yaml").read_text(encoding="utf-8"))
    status["capabilities"][0].update(
        authorization="AUTHORIZED",
        release_manifest="specs/contracts/release-gate-manifest-v1.schema.json",
        trusted_signers="specs/contracts/trusted-release-signers-v1.schema.json",
    )
    monkeypatch.setattr(check_context_drift, "load_yaml", lambda _: status)
    for variable in (
        check_context_drift.RELEASE_COMMIT_ENV,
        check_context_drift.RELEASE_STAGE_ENV,
        check_context_drift.RELEASE_TRUST_PIN_ENV,
    ):
        monkeypatch.delenv(variable, raising=False)

    with pytest.raises(ValueError, match="requires out-of-band deployment release context"):
        check_context_drift.validate_capability_status(gate_requirements)
