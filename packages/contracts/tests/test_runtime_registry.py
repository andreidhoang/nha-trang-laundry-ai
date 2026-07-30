from __future__ import annotations

from pathlib import Path

import pytest
from nha_trang_laundry_contracts import (
    RuntimeArtifactError,
    load_public_runtime_registry,
    verify_openclaw_cli_version,
    verify_public_runtime_artifacts,
)
from nha_trang_laundry_contracts.runtime_registry import SandboxImagePin, VerificationStatus
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]


def test_public_runtime_candidate_is_fail_closed() -> None:
    registry = load_public_runtime_registry(ROOT / "runtime/model-registry-v1.yaml")

    assert registry.model.agent_runtime_id == "openclaw"
    assert registry.model.provider_transport == "responses"
    assert registry.model.fallback_model_refs == ()
    assert registry.activation.real_customer_model_calls_enabled is False
    assert registry.activation.direct_provider_send_available is False
    assert {
        "IMMUTABLE_MODEL_RELEASE_NOT_VERIFIED",
        "OPENCLAW_STORE_FALSE_ROUTE_NOT_VERIFIED",
        "SANDBOX_IMAGE_NOT_VERIFIED",
        "EFFECTIVE_PROVIDER_REQUEST_NOT_VERIFIED",
        "SECURITY_PROVIDER_DATA_APPROVAL_MISSING",
        "PRIVACY_PROVIDER_DATA_APPROVAL_MISSING",
        "REAL_CUSTOMER_DATA_DISABLED",
        "CANDIDATE_IS_EVAL_ONLY",
    }.issubset(registry.release_blockers())
    artifacts = verify_public_runtime_artifacts(ROOT, registry)
    assert "evidence/provider/openai-data-controls-review-v1.yaml" in artifacts
    assert len(artifacts) >= 8


def test_provider_data_evidence_hash_and_status_drift_fail_closed() -> None:
    registry = load_public_runtime_registry(ROOT / "runtime/model-registry-v1.yaml")
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


def test_openclaw_executable_version_must_match_registry_pin() -> None:
    assert verify_openclaw_cli_version("OpenClaw 2026.7.1-2 (0790d9f)\n", "2026.7.1-2") == (
        "0790d9f"
    )
    with pytest.raises(RuntimeArtifactError, match="version drifted"):
        verify_openclaw_cli_version("OpenClaw 2026.7.2-1 (0790d9f)", "2026.7.1-2")
    with pytest.raises(RuntimeArtifactError, match="output is malformed"):
        verify_openclaw_cli_version("2026.7.1-2", "2026.7.1-2")


def test_sandbox_image_cannot_be_verified_without_scan_and_sbom_pins() -> None:
    with pytest.raises(ValidationError, match="verification fields must be complete together"):
        SandboxImagePin.model_validate(
            {
                "repository": "openclaw-sandbox",
                "digest": f"sha256:{'1' * 64}",
                "verified": True,
                "scan_evidence_path": None,
                "scan_evidence_sha256": None,
            }
        )
