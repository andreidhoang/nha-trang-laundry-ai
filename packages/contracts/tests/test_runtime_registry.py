from __future__ import annotations

from pathlib import Path

from nha_trang_laundry_contracts import (
    load_public_runtime_registry,
    verify_public_runtime_artifacts,
)

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
        "EFFECTIVE_PROVIDER_REQUEST_NOT_VERIFIED",
        "SECURITY_PROVIDER_DATA_APPROVAL_MISSING",
        "PRIVACY_PROVIDER_DATA_APPROVAL_MISSING",
        "REAL_CUSTOMER_DATA_DISABLED",
        "CANDIDATE_IS_EVAL_ONLY",
    }.issubset(registry.release_blockers())
    assert len(verify_public_runtime_artifacts(ROOT, registry)) >= 7
