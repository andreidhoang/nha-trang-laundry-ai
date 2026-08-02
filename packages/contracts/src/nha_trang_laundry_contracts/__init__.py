"""Typed adapters; structured files in specs/contracts remain authoritative."""

from .agent_runner import (
    CAPABILITY_OPERATIONS,
    AgentDataClassification,
    AgentDeploymentStage,
    AgentRunnerClaims,
    operation_is_authorized,
)
from .release_manifest import (
    ReleaseManifestError,
    ReleaseSignatureAlgorithm,
    ReleaseSignerFunction,
    RepositoryArtifactResolver,
    TrustedReleaseSigner,
    VerifiedReleaseAuthorization,
    load_and_verify_release_manifest,
    load_trusted_release_signers,
    verify_release_manifest,
)
from .runtime_registry import (
    PublicRuntimeRegistry,
    ReleaseCapability,
    RuntimeArtifactError,
    load_public_runtime_registry,
    verify_openclaw_cli_version,
    verify_public_runtime_artifacts,
)
from .supply_chain import (
    SupplyChainEvidenceError,
    VerifiedSupplyChainEvidence,
    verify_supply_chain_evidence,
)
from .tool_registry import (
    OPENCLAW_TOOL_NAMES,
    AgentToolOperation,
    AgentToolRegistry,
    AgentToolSideEffect,
    ToolArgumentsInvalid,
    ToolRegistryError,
    load_agent_tool_registry,
)

__all__ = [
    "CAPABILITY_OPERATIONS",
    "OPENCLAW_TOOL_NAMES",
    "AgentDataClassification",
    "AgentDeploymentStage",
    "AgentRunnerClaims",
    "AgentToolOperation",
    "AgentToolRegistry",
    "AgentToolSideEffect",
    "PublicRuntimeRegistry",
    "ReleaseCapability",
    "ReleaseManifestError",
    "ReleaseSignatureAlgorithm",
    "ReleaseSignerFunction",
    "RepositoryArtifactResolver",
    "RuntimeArtifactError",
    "SupplyChainEvidenceError",
    "ToolArgumentsInvalid",
    "ToolRegistryError",
    "TrustedReleaseSigner",
    "VerifiedReleaseAuthorization",
    "VerifiedSupplyChainEvidence",
    "load_agent_tool_registry",
    "load_and_verify_release_manifest",
    "load_public_runtime_registry",
    "load_trusted_release_signers",
    "operation_is_authorized",
    "verify_openclaw_cli_version",
    "verify_public_runtime_artifacts",
    "verify_release_manifest",
    "verify_supply_chain_evidence",
]
