from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import yaml
from nha_trang_laundry_evals import validate_eval_manifest

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "evidence/agent-shadow/local-synthetic-suite-v1.json"
ROLLBACK = ROOT / "evidence/agent-shadow/rollback-assessment-v1.yaml"
OPENCLAW_EVIDENCE = ROOT / "evidence/agent-shadow/openclaw-offline-verification-v1.json"


def test_local_suite_evidence_is_complete_current_and_explicitly_non_release() -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    result = evidence["result"]

    assert evidence["evidence_type"] == "LOCAL_SYNTHETIC_NON_RELEASE"
    assert evidence["primary_provider_evidence"] is False
    assert evidence["release_effect"] == "NONE"
    assert result["status"] == "SKIP"
    assert result["release_eligible"] is False
    assert result["release_evidence"] is False
    assert result["coverage"]["manifest_cases"] == 32
    assert result["coverage"]["executed_cases"] == 32
    assert len({case["case_id"] for case in result["cases"]}) == 32
    assert {case["status"] for case in result["cases"]} == {"SKIP"}
    manifest_report = validate_eval_manifest(ROOT, ROOT / "specs/evals/eval-manifest-v1.yaml")
    assert result["release_blockers"] == list(manifest_report.release_blockers)
    for relative, expected in evidence["artifact_hashes"].items():
        actual = f"sha256:{sha256((ROOT / relative).read_bytes()).hexdigest()}"
        assert actual == expected, f"local evidence is stale for {relative}"


def test_rollback_assessment_is_fail_closed_and_forward_only() -> None:
    assessment = yaml.safe_load(ROLLBACK.read_text(encoding="utf-8"))

    assert assessment["release_effect"] == "NONE"
    assert assessment["database_rollback"]["strategy"] == "Forward-fix only."
    assert assessment["runtime_rollback"]["required_actions"]
    assert assessment["unresolved_external_requirements"]
    assert any("NOT_AUTHORIZED" in item for item in assessment["preconditions"])


def test_openclaw_offline_evidence_is_current_and_non_release() -> None:
    evidence = json.loads(OPENCLAW_EVIDENCE.read_text(encoding="utf-8"))
    result = evidence["result"]

    assert evidence["evidence_type"] == "OPENCLAW_OFFLINE_NON_RELEASE"
    assert evidence["release_effect"] == "NONE"
    assert evidence["provider_request_executed"] is False
    assert evidence["verification_exit_code"] == 2
    assert result["status"] == "EVAL_ONLY_BLOCKED"
    assert result["security_audit_critical"] == 0
    assert result["dependency_audit_critical"] == 0
    assert result["dependency_audit_high"] == 2
    assert result["real_customer_data_allowed"] is False
    assert len(result["release_blockers"]) == 10
    assert result["release_blockers"][-1] == "OPENCLAW_DEPENDENCY_AUDIT_HIGH"
    assert result["openclaw_build_revision"]
    for relative, expected in evidence["artifact_hashes"].items():
        actual = f"sha256:{sha256((ROOT / relative).read_bytes()).hexdigest()}"
        assert actual == expected, f"OpenClaw evidence is stale for {relative}"
