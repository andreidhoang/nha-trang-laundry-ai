"""Fail fast when the repository's normative structured contracts are malformed."""

from __future__ import annotations

import json
from csv import DictReader
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from nha_trang_laundry_contracts import (
    load_agent_tool_registry,
    load_public_runtime_registry,
    verify_public_runtime_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
JSON_CONTRACTS = (
    "specs/contracts/capability-status-v1.schema.json",
    "specs/contracts/canonical-enums-v1.json",
    "specs/contracts/channel-inbound-envelope-v1.schema.json",
    "specs/contracts/channel-outbound-receipt-v1.schema.json",
    "specs/contracts/container-scan-evidence-v1.schema.json",
    "specs/contracts/openclaw-repackage-manifest-v1.schema.json",
    "specs/contracts/openclaw-repackage-manifest-v2.schema.json",
    "specs/contracts/openclaw-cross-platform-result-v1.schema.json",
    "specs/contracts/pricebook-import-manifest-v1.json",
    "specs/contracts/provider-data-evidence-v1.schema.json",
    "specs/contracts/public-policy-bundle-v1.schema.json",
    "specs/contracts/release-gate-manifest-v1.schema.json",
    "specs/contracts/release-gate-manifest-v2.schema.json",
    "specs/contracts/supply-chain-evidence-v1.schema.json",
    "specs/contracts/trusted-release-signers-v1.schema.json",
    "specs/evals/assertion-registry-v1.json",
    "specs/evals/eval-case-v1.schema.json",
    "specs/evals/eval-result-v1.schema.json",
    "specs/evals/fixture-registry-v1.json",
)
YAML_CONTRACTS = (
    "specs/contracts/agent-tools-v1.openapi.yaml",
    "specs/evals/eval-manifest-v1.yaml",
)


def load_json(relative_path: str) -> Any:
    with (ROOT / relative_path).open(encoding="utf-8") as contract_file:
        return json.load(contract_file)


def load_yaml(relative_path: str) -> Any:
    with (ROOT / relative_path).open(encoding="utf-8") as contract_file:
        return yaml.safe_load(contract_file)


def validate_enum_parity() -> None:
    canonical = load_json("specs/contracts/canonical-enums-v1.json")
    openapi = load_yaml("specs/contracts/agent-tools-v1.openapi.yaml")
    if not isinstance(canonical, dict) or not isinstance(openapi, dict):
        raise ValueError("Canonical enum and OpenAPI contracts must be mappings")
    enums = canonical.get("enums")
    components = openapi.get("components")
    schemas = components.get("schemas") if isinstance(components, dict) else None
    if not isinstance(enums, dict) or not isinstance(schemas, dict):
        raise ValueError("Canonical enum or OpenAPI schema registry is missing")
    compared = 0
    for name, values in enums.items():
        schema = schemas.get(name)
        if not isinstance(schema, dict) or "enum" not in schema:
            continue
        exposed = schema["enum"]
        if schema.get("x-canonical-subset") is True:
            if not isinstance(exposed, list) or exposed != [
                value for value in values if value in exposed
            ]:
                raise ValueError(f"OpenAPI enum {name} is not an ordered canonical subset")
        elif exposed != values:
            raise ValueError(f"OpenAPI enum {name} drifted from canonical-enums-v1.json")
        compared += 1
    if compared == 0:
        raise ValueError("OpenAPI exposes no canonical enum for parity validation")


def validate_pricebook_manifest() -> None:
    manifest = load_json("specs/contracts/pricebook-import-manifest-v1.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("source"), dict):
        raise ValueError("Pricebook import manifest requires a source mapping")
    source = manifest["source"]
    relative_path = source.get("path")
    expected_hash = source.get("sha256")
    expected_rows = source.get("row_count")
    if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
        raise ValueError("Pricebook import manifest source path/hash is invalid")
    source_path = (ROOT / relative_path).resolve()
    if ROOT not in source_path.parents or not source_path.is_file():
        raise ValueError("Pricebook manifest source must be a repository file")
    source_bytes = source_path.read_bytes()
    if sha256(source_bytes).hexdigest() != expected_hash:
        raise ValueError("Pricebook source hash drifted from its import manifest")
    with source_path.open(encoding="utf-8-sig", newline="") as source_file:
        row_count = sum(1 for _ in DictReader(source_file))
    if row_count != expected_rows:
        raise ValueError("Pricebook source row count drifted from its import manifest")


def validate_release_gate_schema() -> None:
    schema = load_json("specs/contracts/release-gate-manifest-v1.schema.json")
    Draft202012Validator.check_schema(schema)
    signer_schema = load_json("specs/contracts/trusted-release-signers-v1.schema.json")
    Draft202012Validator.check_schema(signer_schema)
    provider_schema = load_json("specs/contracts/provider-data-evidence-v1.schema.json")
    Draft202012Validator.check_schema(provider_schema)
    container_schema = load_json("specs/contracts/container-scan-evidence-v1.schema.json")
    Draft202012Validator.check_schema(container_schema)
    capability_schema = load_json("specs/contracts/capability-status-v1.schema.json")
    Draft202012Validator.check_schema(capability_schema)
    capability_status = load_yaml("delivery/CAPABILITY_STATUS.yaml")
    errors = sorted(
        Draft202012Validator(capability_schema).iter_errors(capability_status),
        key=lambda error: list(error.path),
    )
    if errors:
        details = "; ".join(error.message for error in errors)
        raise ValueError(f"Capability status violates schema: {details}")
    release_capabilities = schema["properties"]["capability"]["enum"]
    status_capabilities = capability_schema["$defs"]["CapabilityId"]["enum"]
    if status_capabilities != release_capabilities:
        raise ValueError("Capability status IDs drifted from the release-gate contract")

    # ADR-0006: v2 amends only the signer distinctness rule and adds compensating controls.
    # Any other divergence from v1 means the amendment has silently become a rewrite.
    v2 = load_json("specs/contracts/release-gate-manifest-v2.schema.json")
    Draft202012Validator.check_schema(v2)
    if v2["properties"]["schema_version"]["const"] != 2:
        raise ValueError("Release gate v2 must declare schema_version 2")
    if "compensating_controls" not in v2.get("required", []):
        raise ValueError("Release gate v2 must require compensating_controls")
    added = set(v2["properties"]) - set(schema["properties"])
    removed = set(schema["properties"]) - set(v2["properties"])
    if added != {"compensating_controls"} or removed:
        raise ValueError("Release gate v2 diverged from v1 beyond the ADR-0006 amendment")
    if v2["properties"]["capability"]["enum"] != release_capabilities:
        raise ValueError("Release gate v2 capability enum drifted from v1")

    policy_schema = load_json("specs/contracts/public-policy-bundle-v1.schema.json")
    Draft202012Validator.check_schema(policy_schema)
    bundle_capabilities = policy_schema["properties"]["authorized_capabilities"]["items"]["enum"]
    if not set(bundle_capabilities).issubset(set(release_capabilities)):
        raise ValueError("Public policy bundle authorizes an unknown capability")


def validate_channel_contracts() -> None:
    """Channel transport contracts are structurally valid and do not fork canonical vocabulary.

    Channel enums intentionally live outside canonical-enums-v1.json because they are transport
    identifiers rather than business vocabulary. Any field that does mirror a canonical enum must
    match it exactly, so provider churn can never silently redefine a domain term.
    """
    inbound = load_json("specs/contracts/channel-inbound-envelope-v1.schema.json")
    receipt = load_json("specs/contracts/channel-outbound-receipt-v1.schema.json")
    Draft202012Validator.check_schema(inbound)
    Draft202012Validator.check_schema(receipt)

    canonical = load_json("specs/contracts/canonical-enums-v1.json")
    enums = canonical.get("enums")
    if not isinstance(enums, dict):
        raise ValueError("Canonical enum registry is missing")

    mirrored = {
        "message_kind": ("MessageKind", receipt["properties"]["message_kind"]["enum"]),
        "capability": (
            "AutomationCapability",
            receipt["properties"]["authorization"]["properties"]["capability"]["enum"],
        ),
    }
    for field, (canonical_name, exposed) in mirrored.items():
        if exposed != enums[canonical_name]:
            raise ValueError(
                f"Channel receipt {field} drifted from canonical enum {canonical_name}"
            )

    delivery = receipt["properties"]["delivery_status"]["enum"]
    canonical_delivery = enums["MessageDeliveryStatus"]
    if delivery != [value for value in canonical_delivery if value in delivery]:
        raise ValueError("Channel receipt delivery_status is not an ordered canonical subset")

    providers = inbound["properties"]["provider"]["enum"]
    if providers != receipt["properties"]["provider"]["enum"]:
        raise ValueError("Channel provider enums differ between inbound and outbound contracts")


def main() -> None:
    for contract in JSON_CONTRACTS:
        load_json(contract)
    for contract in YAML_CONTRACTS:
        load_yaml(contract)
    validate_enum_parity()
    validate_pricebook_manifest()
    validate_release_gate_schema()
    validate_channel_contracts()
    load_agent_tool_registry(ROOT / "specs/contracts/agent-tools-v1.openapi.yaml")
    runtime_registry = load_public_runtime_registry(ROOT / "runtime/model-registry-v1.yaml")
    runtime_artifacts = verify_public_runtime_artifacts(ROOT, runtime_registry)
    print(f"Validated {len(JSON_CONTRACTS)} JSON and {len(YAML_CONTRACTS)} YAML contracts.")
    print(
        f"Validated {len(runtime_artifacts)} pinned public-runtime artifacts; "
        f"release blockers={len(runtime_registry.release_blockers())}."
    )


if __name__ == "__main__":
    main()
