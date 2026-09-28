"""Fail-closed registry for the public agent runtime and model release candidate.

Schema version 2 (`OPENCLAW-RETIRE-001`, ADR-0009). Version 1 described an OpenClaw public cell:
a repackaged npm distribution, a sandbox image, a plugin inventory, and an `agent_runtime_id` of
`openclaw`. Production never ran it -- the worker is wired to `BoundedResponsesRuntime` -- and
ADR-0009 retired it, so version 2 describes the runtime that does run: the custom Responses
adapter inside the worker image. A version-1 file is refused, which is how a stale or implicit
OpenClaw route fails closed at startup rather than half-loading.
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

import yaml
from jsonschema import Draft202012Validator, FormatChecker
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


class RuntimeImagePin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The image the Responses runtime runs in. Unverified until a scanned digest with provenance
    #: is recorded; until then `AGENT_RUNTIME_IMAGE_NOT_VERIFIED` blocks release.
    repository: Literal["nha-trang-laundry-worker"]
    digest: Sha256 | None
    verified: bool
    scan_evidence_path: str | None
    scan_evidence_sha256: Sha256 | None
    provenance_path: str | None
    provenance_sha256: Sha256 | None

    @model_validator(mode="after")
    def verification_fields_are_consistent(self) -> RuntimeImagePin:
        complete = all(
            value is not None
            for value in (
                self.digest,
                self.scan_evidence_path,
                self.scan_evidence_sha256,
                self.provenance_path,
                self.provenance_sha256,
            )
        )
        if self.verified != complete:
            raise ValueError("runtime image verification fields must be complete together")
        return self


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["openai"]
    api_model_id: Literal["gpt-5.6-terra"]
    immutable_release_id: str | None
    immutable_release_verified: bool
    #: `apps/worker/.../responses_runtime.py` (`BoundedResponsesRuntime`). Its request type fixes
    #: `store` to `Literal[False]`, so no configuration can ask the provider to keep a response;
    #: whether the provider honours that is `EFFECTIVE_PROVIDER_REQUEST_NOT_VERIFIED`'s question.
    agent_runtime_id: Literal["nha-trang-responses-runtime"]
    provider_transport: Literal["responses"]
    reasoning_effort: Literal["low"]
    required_response_store: Literal[False]
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


class ProviderDataEvidencePin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: Sha256


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

    schema_version: Literal[2]
    registry_version: str
    candidate_status: CandidateStatus
    runtime_image: RuntimeImagePin
    model: ModelRoute
    provider_data_gate: ProviderDataGate
    provider_data_evidence: ProviderDataEvidencePin
    prompt: PromptPin
    tool_contract_path: str
    tool_contract_sha256: Sha256
    limits: RuntimeLimits
    activation: ActivationState

    def release_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not self.model.immutable_release_verified:
            blockers.append("IMMUTABLE_MODEL_RELEASE_NOT_VERIFIED")
        if not self.runtime_image.verified:
            blockers.append("AGENT_RUNTIME_IMAGE_NOT_VERIFIED")
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
    runtime_image = registry.runtime_image
    if runtime_image.verified:
        assert runtime_image.digest is not None
        assert runtime_image.scan_evidence_path is not None
        assert runtime_image.scan_evidence_sha256 is not None
        assert runtime_image.provenance_path is not None
        assert runtime_image.provenance_sha256 is not None
        expected_runtime_image = f"{runtime_image.repository}@{runtime_image.digest}"
        scan = _verified_file(
            root, runtime_image.scan_evidence_path, runtime_image.scan_evidence_sha256
        )
        sbom = _verify_container_scan_evidence(root, scan, expected_runtime_image)
        provenance = _verified_file(
            root, runtime_image.provenance_path, runtime_image.provenance_sha256
        )
        provenance_data = json.loads(provenance.read_text(encoding="utf-8"))
        if (
            provenance_data.get("image_digest") != runtime_image.digest
            or provenance_data.get("predicate_type") != "https://slsa.dev/provenance/v1"
        ):
            raise RuntimeArtifactError("Agent runtime provenance drifted from image pin")
        verified.extend(
            (
                scan.relative_to(root).as_posix(),
                sbom.relative_to(root).as_posix(),
                provenance.relative_to(root).as_posix(),
            )
        )
    prompt_manifest = _verified_file(
        root, registry.prompt.bundle_path, registry.prompt.bundle_sha256
    )
    verified.append(prompt_manifest.relative_to(root).as_posix())
    tool_contract = _verified_file(root, registry.tool_contract_path, registry.tool_contract_sha256)
    verified.append(tool_contract.relative_to(root).as_posix())
    provider_evidence = _verified_file(
        root, registry.provider_data_evidence.path, registry.provider_data_evidence.sha256
    )
    _verify_provider_data_evidence(root, provider_evidence, registry)
    verified.append(provider_evidence.relative_to(root).as_posix())

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


def _verify_provider_data_evidence(root: Path, path: Path, registry: PublicRuntimeRegistry) -> None:
    evidence = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(evidence, dict):
        raise RuntimeArtifactError("Provider data evidence must be an object")
    schema_path = root / "specs/contracts/provider-data-evidence-v2.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeArtifactError("Provider data evidence schema cannot be loaded") from error
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(evidence), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(error.message for error in errors)
        raise RuntimeArtifactError(f"Provider data evidence violates schema: {details}")

    scope = evidence["scope"]
    policy = evidence["required_product_policy"]
    verification = evidence["verification"]
    decision = evidence["decision_binding"]
    release_effect = evidence["release_effect"]
    assert isinstance(scope, dict)
    assert isinstance(policy, dict)
    assert isinstance(verification, dict)
    assert isinstance(decision, dict)
    assert isinstance(release_effect, dict)
    if (
        scope["provider"] != registry.model.provider
        or scope["api"].lower() != registry.model.provider_transport
        or scope["model_candidate"] != registry.model.api_model_id
        or scope["agent_runtime_id"] != registry.model.agent_runtime_id
    ):
        raise RuntimeArtifactError("Provider data evidence scope drifted from runtime registry")
    if (
        policy["response_store"] is not registry.model.required_response_store
        or policy["dedicated_service_credential"] is not True
        or policy["minimum_data"] is not True
        or policy["hidden_chain_of_thought_storage"] is not False
    ):
        raise RuntimeArtifactError("Provider data evidence policy violates product requirements")

    gate = registry.provider_data_gate
    parity = {
        "documentation_review": gate.documentation_review.value,
        "captured_effective_request_store_false": (
            gate.effective_request_storage_verification.value
        ),
        "dedicated_service_credential": gate.dedicated_service_credential_verified.value,
        "security_approval": gate.security_approval.value,
        "privacy_approval": gate.privacy_approval.value,
        "immutable_model_release": (
            "VERIFIED" if registry.model.immutable_release_verified else "NOT_VERIFIED"
        ),
    }
    if any(verification[key] != expected for key, expected in parity.items()):
        raise RuntimeArtifactError("Provider data evidence status drifted from runtime registry")
    if decision["decision_id"] != gate.decision_id:
        raise RuntimeArtifactError("Provider data evidence decision binding drifted")
    approvals_complete = (
        gate.security_approval is ApprovalStatus.APPROVED
        and gate.privacy_approval is ApprovalStatus.APPROVED
    )
    if (decision["decision_status"] == "APPROVED") != approvals_complete:
        raise RuntimeArtifactError("Provider data evidence decision status contradicts approvals")
    if (
        release_effect["synthetic_eval_allowed"] is not True
        or release_effect["real_customer_data_allowed"] is not gate.real_customer_data_allowed
        or release_effect["public_ingress_allowed"] is not False
        or release_effect["automatic_send_allowed"] is not False
    ):
        raise RuntimeArtifactError(
            "Provider data evidence release effect drifted or over-authorized"
        )

    effective_verified = gate.effective_request_storage_verification is VerificationStatus.VERIFIED
    fully_approved = (
        effective_verified
        and registry.model.immutable_release_verified
        and gate.dedicated_service_credential_verified is VerificationStatus.VERIFIED
        and approvals_complete
        and gate.real_customer_data_allowed
    )
    expected_status = (
        "VERIFIED_APPROVED"
        if fully_approved
        else (
            "EFFECTIVE_REQUEST_VERIFIED_APPROVALS_PENDING"
            if effective_verified
            else "DOCUMENTATION_REVIEWED_EFFECTIVE_REQUEST_NOT_VERIFIED"
        )
    )
    if evidence["status"] != expected_status:
        raise RuntimeArtifactError("Provider data evidence lifecycle status is inconsistent")


def _verify_container_scan_evidence(root: Path, path: Path, image_ref: str) -> Path:
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeArtifactError("Container scan evidence is not valid JSON") from error
    schema_path = root / "specs/contracts/container-scan-evidence-v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeArtifactError("Container scan evidence schema cannot be loaded") from error
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(evidence), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(error.message for error in errors)
        raise RuntimeArtifactError(f"Container scan evidence violates schema: {details}")
    if evidence["image_ref"] != image_ref:
        raise RuntimeArtifactError("Container scan evidence image reference drifted")
    sbom = evidence["sbom"]
    assert isinstance(sbom, dict)
    return _verified_file(root, str(sbom["uri"]), str(sbom["sha256"]))


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
