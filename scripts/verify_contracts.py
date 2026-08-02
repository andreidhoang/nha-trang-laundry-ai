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
    "specs/contracts/container-scan-evidence-v1.schema.json",
    "specs/contracts/pricebook-import-manifest-v1.json",
    "specs/contracts/provider-data-evidence-v1.schema.json",
    "specs/contracts/release-gate-manifest-v1.schema.json",
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


def main() -> None:
    for contract in JSON_CONTRACTS:
        load_json(contract)
    for contract in YAML_CONTRACTS:
        load_yaml(contract)
    validate_enum_parity()
    validate_pricebook_manifest()
    validate_release_gate_schema()
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
