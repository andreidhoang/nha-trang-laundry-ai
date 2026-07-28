"""Fail-closed registry for the public OpenClaw/model release candidate."""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Sha256 = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Sha512Integrity = Annotated[str, StringConstraints(pattern=r"^sha512-[A-Za-z0-9+/]+={0,2}$")]


class CandidateStatus(StrEnum):
    EVAL_ONLY = "EVAL_ONLY"
    RELEASE_ELIGIBLE = "RELEASE_ELIGIBLE"


class ReleaseCapability(StrEnum):
    INTERNAL_SHADOW = "INTERNAL_SHADOW"
    PUBLIC_FAQ = "PUBLIC_FAQ"
    LIST_PRICE_INFO = "LIST_PRICE_INFO"
    INTAKE_QUESTION = "INTAKE_QUESTION"
    INTAKE_FACT_CAPTURE = "INTAKE_FACT_CAPTURE"
    INTAKE_RECEIPT = "INTAKE_RECEIPT"
    INCIDENT_RECEIPT = "INCIDENT_RECEIPT"
    ORDER_STATUS = "ORDER_STATUS"
    SLA_GUIDANCE = "SLA_GUIDANCE"
    QUOTE_ESTIMATE = "QUOTE_ESTIMATE"
    BOOKING = "BOOKING"
    DELIVERY_ADVISORY = "DELIVERY_ADVISORY"
    MARKETING_FOLLOWUP = "MARKETING_FOLLOWUP"


class VerificationStatus(StrEnum):
    NOT_VERIFIED = "NOT_VERIFIED"
    DOCUMENTATION_REVIEWED = "DOCUMENTATION_REVIEWED"
    VERIFIED = "VERIFIED"


class ApprovalStatus(StrEnum):
    NOT_APPROVED = "NOT_APPROVED"
    APPROVED = "APPROVED"


class OpenClawPin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}\.[0-9]+\.[0-9]+-[0-9]+$")]
    npm_integrity: Sha512Integrity
    target_os: Literal["linux"]
    runtime_type: Literal["embedded"]
    config_path: str
    config_sha256: Sha256
    plugin_id: Literal["nha-trang-laundry-tools"]
    plugin_version: str
    plugin_inventory_sha256: Sha256


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["openai"]
    openclaw_model_ref: Literal["openai/gpt-5.6-terra"]
    api_model_id: Literal["gpt-5.6-terra"]
    immutable_release_id: str | None
    immutable_release_verified: bool
    agent_runtime_id: Literal["openclaw"]
    provider_transport: Literal["responses"]
    reasoning_effort: Literal["low"]
    required_response_store: Literal[False]
    openclaw_documented_default_store: Literal[True]
    store_false_override_verified: bool
    fallback_model_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def immutable_release_is_consistent(self) -> ModelRoute:
        if self.immutable_release_verified != (self.immutable_release_id is not None):
            raise ValueError("immutable release ID and verification flag must agree")
        if self.fallback_model_refs:
            raise ValueError("uncertified fallback models are prohibited")
        return self


class ProviderDataGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: Literal["DEC-006"]
    documentation_review: VerificationStatus
    effective_request_storage_verification: VerificationStatus
    security_approval: ApprovalStatus
    privacy_approval: ApprovalStatus
    dedicated_service_credential_verified: VerificationStatus
    real_customer_data_allowed: bool


class RuntimeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_model_calls: Annotated[int, Field(ge=1, le=3)]
    max_tool_calls: Annotated[int, Field(ge=1, le=6)]
    max_input_tokens: Annotated[int, Field(ge=1, le=8000)]
    max_output_tokens: Annotated[int, Field(ge=1, le=1200)]
    hard_deadline_seconds: Literal[20]
    max_turn_cost_usd: Annotated[str, StringConstraints(pattern=r"^0\.[0-9]{2}$")]


class PromptPin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_path: str
    bundle_version: str
    bundle_sha256: Sha256


class ActivationState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    public_ingress_enabled: Literal[False]
    real_customer_model_calls_enabled: Literal[False]
    automatic_send_enabled: Literal[False]
    direct_provider_send_available: Literal[False]


class PublicRuntimeRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    registry_version: str
    candidate_status: CandidateStatus
    openclaw: OpenClawPin
    model: ModelRoute
    provider_data_gate: ProviderDataGate
    prompt: PromptPin
    tool_contract_path: str
    tool_contract_sha256: Sha256
    limits: RuntimeLimits
    activation: ActivationState

    def release_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not self.model.immutable_release_verified:
            blockers.append("IMMUTABLE_MODEL_RELEASE_NOT_VERIFIED")
        if not self.model.store_false_override_verified:
            blockers.append("OPENCLAW_STORE_FALSE_ROUTE_NOT_VERIFIED")
        gate = self.provider_data_gate
        if gate.effective_request_storage_verification is not VerificationStatus.VERIFIED:
            blockers.append("EFFECTIVE_PROVIDER_REQUEST_NOT_VERIFIED")
        if gate.security_approval is not ApprovalStatus.APPROVED:
            blockers.append("SECURITY_PROVIDER_DATA_APPROVAL_MISSING")
        if gate.privacy_approval is not ApprovalStatus.APPROVED:
            blockers.append("PRIVACY_PROVIDER_DATA_APPROVAL_MISSING")
        if gate.dedicated_service_credential_verified is not VerificationStatus.VERIFIED:
            blockers.append("DEDICATED_PROVIDER_CREDENTIAL_NOT_VERIFIED")
        if not gate.real_customer_data_allowed:
            blockers.append("REAL_CUSTOMER_DATA_DISABLED")
        if self.candidate_status is not CandidateStatus.RELEASE_ELIGIBLE:
            blockers.append("CANDIDATE_IS_EVAL_ONLY")
        return tuple(blockers)


class RuntimeArtifactError(ValueError):
    """A pinned runtime artifact is missing, unsafe, or hash-mismatched."""


def load_public_runtime_registry(path: Path) -> PublicRuntimeRegistry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return PublicRuntimeRegistry.model_validate(raw)


def verify_public_runtime_artifacts(root: Path, registry: PublicRuntimeRegistry) -> tuple[str, ...]:
    """Verify the complete local pin chain without making a provider call."""

    verified: list[str] = []
    config = _verified_file(root, registry.openclaw.config_path, registry.openclaw.config_sha256)
    verified.append(config.relative_to(root).as_posix())
    prompt_manifest = _verified_file(
        root, registry.prompt.bundle_path, registry.prompt.bundle_sha256
    )
    verified.append(prompt_manifest.relative_to(root).as_posix())
    tool_contract = _verified_file(root, registry.tool_contract_path, registry.tool_contract_sha256)
    verified.append(tool_contract.relative_to(root).as_posix())

    inventory_path = _verified_file(
        root,
        "runtime/openclaw/public-cell/plugin-inventory-v1.json",
        registry.openclaw.plugin_inventory_sha256,
    )
    verified.append(inventory_path.relative_to(root).as_posix())
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("openclaw_version") != registry.openclaw.version:
        raise RuntimeArtifactError("Plugin inventory OpenClaw version drifted")
    if inventory.get("openclaw_npm_integrity") != registry.openclaw.npm_integrity:
        raise RuntimeArtifactError("Plugin inventory OpenClaw integrity drifted")
    artifacts = inventory.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RuntimeArtifactError("Plugin inventory artifacts are missing")
    for artifact_name, artifact in artifacts.items():
        if not isinstance(artifact, dict):
            raise RuntimeArtifactError(f"Invalid plugin inventory artifact: {artifact_name}")
        path = artifact.get("path")
        digest = artifact.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str):
            raise RuntimeArtifactError(f"Incomplete plugin inventory artifact: {artifact_name}")
        verified_path = _verified_file(root, path, digest)
        verified.append(verified_path.relative_to(root).as_posix())

    prompt = yaml.safe_load(prompt_manifest.read_text(encoding="utf-8"))
    if not isinstance(prompt, dict) or prompt.get("release_authorization") is not False:
        raise RuntimeArtifactError("Prompt bundle must remain explicitly unauthorized")
    for key in (
        "contains_secrets",
        "contains_chain_of_thought",
        "contains_internal_risk_documents",
    ):
        if prompt.get(key) is not False:
            raise RuntimeArtifactError(f"Prompt bundle safety field must be false: {key}")
    prompt_file = prompt.get("prompt")
    prompt_tool_contract = prompt.get("tool_contract")
    if not isinstance(prompt_file, dict) or not isinstance(prompt_tool_contract, dict):
        raise RuntimeArtifactError("Prompt bundle artifact references are missing")
    for reference in (prompt_file, prompt_tool_contract):
        path = reference.get("path")
        digest = reference.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str):
            raise RuntimeArtifactError("Prompt bundle artifact reference is incomplete")
        verified_path = _verified_file(root, path, digest)
        verified.append(verified_path.relative_to(root).as_posix())
    if prompt_tool_contract["sha256"] != registry.tool_contract_sha256:
        raise RuntimeArtifactError("Prompt bundle and runtime tool contract hashes differ")
    return tuple(dict.fromkeys(verified))


def _verified_file(root: Path, relative_path: str, expected_digest: str) -> Path:
    root = root.resolve()
    path = (root / relative_path).resolve()
    if path != root and root not in path.parents:
        raise RuntimeArtifactError(f"Runtime artifact escapes repository: {relative_path}")
    if not path.is_file():
        raise RuntimeArtifactError(f"Runtime artifact is missing: {relative_path}")
    actual = f"sha256:{sha256(path.read_bytes()).hexdigest()}"
    if actual != expected_digest:
        raise RuntimeArtifactError(
            f"Runtime artifact hash mismatch: {relative_path}; "
            f"expected={expected_digest}; actual={actual}"
        )
    return path
