from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from nha_trang_laundry_contracts import (
    RuntimeArtifactError,
    load_public_runtime_registry,
    verify_public_runtime_artifacts,
)
from nha_trang_laundry_contracts.runtime_registry import (
    PublicRuntimeRegistry,
    RuntimeImagePin,
    VerificationStatus,
)
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]


def test_public_runtime_candidate_is_fail_closed() -> None:
    registry = load_public_runtime_registry(ROOT / "runtime/model-registry-v2.yaml")

    assert registry.schema_version == 2
    assert registry.model.agent_runtime_id == "nha-trang-responses-runtime"
    assert registry.model.provider_transport == "responses"
    assert registry.model.required_response_store is False
    assert registry.model.fallback_model_refs == ()
    assert registry.runtime_image.repository == "nha-trang-laundry-worker"
    assert registry.activation.real_customer_model_calls_enabled is False
    assert registry.activation.direct_provider_send_available is False
    # Exact, not a subset: a blocker that silently disappears is a gate that silently opened.
    # OPENCLAW-RETIRE-001 (ADR-0009) removed three that described the retired OpenClaw cell and
    # re-pointed the image blocker at the worker image the Responses runtime runs in.
    assert registry.release_blockers() == (
        "IMMUTABLE_MODEL_RELEASE_NOT_VERIFIED",
        "AGENT_RUNTIME_IMAGE_NOT_VERIFIED",
        "EFFECTIVE_PROVIDER_REQUEST_NOT_VERIFIED",
        "SECURITY_PROVIDER_DATA_APPROVAL_MISSING",
        "PRIVACY_PROVIDER_DATA_APPROVAL_MISSING",
        "DEDICATED_PROVIDER_CREDENTIAL_NOT_VERIFIED",
        "REAL_CUSTOMER_DATA_DISABLED",
        "CANDIDATE_IS_EVAL_ONLY",
    )
    artifacts = verify_public_runtime_artifacts(ROOT, registry)
    assert set(artifacts) == {
        "runtime/prompts/manifest-v1.yaml",
        "runtime/prompts/public-concierge.vi-VN.md",
        "specs/contracts/agent-tools-v1.openapi.yaml",
        "evidence/provider/openai-data-controls-review-v2.yaml",
    }


def test_a_version_one_openclaw_registry_is_refused() -> None:
    """A stale or implicit OpenClaw route fails closed at load instead of half-loading."""

    record_path = ROOT / "evidence/openclaw-retirement/retired-artifacts-v1.yaml"
    record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    retired_registry = next(
        entry["path"] for entry in record["files"] if entry["path"].startswith("runtime/model-")
    )
    previous = subprocess.run(
        ["git", "show", f"{record['last_commit_containing_every_file']}:{retired_registry}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if previous.returncode != 0:
        pytest.skip("shallow checkout without the retirement base commit")
    with pytest.raises(ValidationError):
        PublicRuntimeRegistry.model_validate(yaml.safe_load(previous.stdout))
    current = yaml.safe_load((ROOT / "runtime/model-registry-v2.yaml").read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        PublicRuntimeRegistry.model_validate({**current, "openclaw": {}})
    with pytest.raises(ValidationError):
        PublicRuntimeRegistry.model_validate(
            {**current, "model": {**current["model"], "agent_runtime_id": "openclaw"}}
        )


def test_provider_data_evidence_hash_and_status_drift_fail_closed() -> None:
    registry = load_public_runtime_registry(ROOT / "runtime/model-registry-v2.yaml")
    bad_pin = registry.provider_data_evidence.model_copy(update={"sha256": f"sha256:{'0' * 64}"})
    bad_hash_registry = registry.model_copy(update={"provider_data_evidence": bad_pin})
    with pytest.raises(RuntimeArtifactError, match="hash mismatch"):
        verify_public_runtime_artifacts(ROOT, bad_hash_registry)

    drifted_gate = registry.provider_data_gate.model_copy(
        update={"documentation_review": VerificationStatus.NOT_VERIFIED}
    )
    drifted_registry = registry.model_copy(update={"provider_data_gate": drifted_gate})
    with pytest.raises(RuntimeArtifactError, match="status drifted"):
        verify_public_runtime_artifacts(ROOT, drifted_registry)


def test_provider_evidence_for_another_runtime_is_refused() -> None:
    registry = load_public_runtime_registry(ROOT / "runtime/model-registry-v2.yaml")
    other_runtime = registry.model.model_copy(update={"agent_runtime_id": "other-runtime"})
    with pytest.raises(RuntimeArtifactError, match="scope drifted"):
        verify_public_runtime_artifacts(ROOT, registry.model_copy(update={"model": other_runtime}))


def test_runtime_image_cannot_be_verified_without_scan_and_provenance_pins() -> None:
    with pytest.raises(ValidationError, match="runtime image verification fields"):
        RuntimeImagePin.model_validate(
            {
                "repository": "nha-trang-laundry-worker",
                "digest": f"sha256:{'1' * 64}",
                "verified": True,
                "scan_evidence_path": "artifacts/worker-scan.json",
                "scan_evidence_sha256": f"sha256:{'2' * 64}",
                "provenance_path": None,
                "provenance_sha256": None,
            }
        )
