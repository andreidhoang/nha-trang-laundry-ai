from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import yaml
from nha_trang_laundry_evals import validate_eval_manifest

ROOT = Path(__file__).resolve().parents[3]
BUNDLE_INDEX = ROOT / "evidence/agent-shadow/bundle-index-v1.yaml"
EVIDENCE = ROOT / "evidence/agent-shadow/local-synthetic-suite-v2.json"
SUPERSEDED_EVIDENCE = ROOT / "evidence/agent-shadow/local-synthetic-suite-v1.json"
ROLLBACK = ROOT / "evidence/agent-shadow/rollback-assessment-v1.yaml"
OPENCLAW_EVIDENCE = ROOT / "evidence/agent-shadow/openclaw-offline-verification-v1.json"


def _stale_pins(evidence: dict[str, object]) -> list[str]:
    """Return the pinned paths whose recorded hash no longer describes the tree."""
    hashes = evidence["artifact_hashes"]
    assert isinstance(hashes, dict)
    return [
        relative
        for relative, expected in hashes.items()
        if f"sha256:{sha256((ROOT / relative).read_bytes()).hexdigest()}" != expected
    ]


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
    assert _stale_pins(evidence) == []


def test_superseded_bundle_is_retained_byte_for_byte() -> None:
    """EVIDENCE-REPIN-001: re-derivation preserves the record, it does not replace it."""
    index = yaml.safe_load(BUNDLE_INDEX.read_text(encoding="utf-8"))
    superseded = index["superseded"]
    assert len(superseded) == 1
    entry = superseded[0]

    assert entry["path"] == SUPERSEDED_EVIDENCE.relative_to(ROOT).as_posix()
    assert SUPERSEDED_EVIDENCE.is_file()
    actual = f"sha256:{sha256(SUPERSEDED_EVIDENCE.read_bytes()).hexdigest()}"
    assert actual == entry["sha256"], "a superseded bundle's bytes must never change"


def test_bundle_index_names_exactly_one_current_bundle() -> None:
    index = yaml.safe_load(BUNDLE_INDEX.read_text(encoding="utf-8"))

    assert index["current"]["path"] == EVIDENCE.relative_to(ROOT).as_posix()
    assert index["current"]["release_effect"] == "NONE"
    superseded_paths = {entry["path"] for entry in index["superseded"]}
    assert index["current"]["path"] not in superseded_paths


def test_hand_edited_bundle_is_rejected_rather_than_accepted_as_derived() -> None:
    """A bundle whose hashes were adjusted to match the tree is a fabricated attestation."""
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert _stale_pins(evidence) == []

    forged = deepcopy(evidence)
    pinned_path = "runtime/model-registry-v1.yaml"
    forged["artifact_hashes"][pinned_path] = f"sha256:{'0' * 64}"

    assert _stale_pins(forged) == [pinned_path]


def test_re_derivation_changed_no_skip_and_no_release_blocker() -> None:
    """The superseded and current bundles must attest the same outcome about the same cases."""
    superseded = json.loads(SUPERSEDED_EVIDENCE.read_text(encoding="utf-8"))
    current = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    before, after = superseded["result"], current["result"]

    assert before["release_blockers"] == after["release_blockers"]
    assert {case["case_id"] for case in before["cases"]} == {
        case["case_id"] for case in after["cases"]
    }
    assert {case["status"] for case in after["cases"]} == {"SKIP"}
    assert before["runtime_path"] == after["runtime_path"] == "DETERMINISTIC_DEGRADED"
    assert (superseded["release_effect"], superseded["primary_provider_evidence"]) == (
        current["release_effect"],
        current["primary_provider_evidence"],
    )


def test_capture_refuses_to_overwrite_a_superseded_bundle() -> None:
    """A bare or misdirected re-run must not be able to destroy a retained record."""
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/capture_local_agent_evidence.py"),
            "--output",
            str(SUPERSEDED_EVIDENCE),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode != 0
    assert "superseded evidence bundle" in completed.stderr


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
    assert evidence["verification_exit_code"] == 0
    assert result["status"] == "EVAL_ONLY_VERIFIED"
    assert result["security_audit_critical"] == 0
    assert result["dependency_audit_critical"] == 0
    assert result["dependency_audit_high"] == 0
    assert result["real_customer_data_allowed"] is False
    assert len(result["release_blockers"]) == 10
    assert "OPENCLAW_DEPENDENCY_AUDIT_HIGH" not in result["release_blockers"]
    assert result["openclaw_build_revision"]
    for relative, expected in evidence["artifact_hashes"].items():
        actual = f"sha256:{sha256((ROOT / relative).read_bytes()).hexdigest()}"
        assert actual == expected, f"OpenClaw evidence is stale for {relative}"
