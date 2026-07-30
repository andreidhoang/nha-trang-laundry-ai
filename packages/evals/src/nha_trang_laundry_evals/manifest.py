"""Contract-only evaluation validation; never substitutes for integrated model evals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from nha_trang_laundry_contracts import load_agent_tool_registry

from .fixtures import FixtureBundleError, load_synthetic_fixture


class EvalManifestError(ValueError):
    """The evaluation manifest or one of its registries is inconsistent."""


@dataclass(frozen=True, slots=True)
class EvalContractReport:
    manifest_id: str
    manifest_version: str
    total_cases: int
    p0_cases: int
    referenced_tool_calls: int
    unimplemented_fixture_payloads: int
    unimplemented_assertions: int
    release_eligible: bool
    release_blockers: tuple[str, ...]


def validate_eval_manifest(root: Path, manifest_path: Path) -> EvalContractReport:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise EvalManifestError("Eval manifest must be a mapping")
    contracts = manifest.get("normative_contracts")
    cases = manifest.get("cases")
    graders = manifest.get("graders")
    runner = manifest.get("runner")
    if (
        not isinstance(contracts, dict)
        or not isinstance(cases, list)
        or not isinstance(graders, dict)
        or not isinstance(runner, dict)
    ):
        raise EvalManifestError("Eval manifest lacks contracts, graders, or cases")

    tool_path = _resolved_contract_path(root, manifest_path, contracts.get("tools"))
    case_schema_path = _resolved_contract_path(root, manifest_path, contracts.get("case_schema"))
    assertion_path = _resolved_contract_path(
        root, manifest_path, contracts.get("assertion_registry")
    )
    fixture_path = _resolved_contract_path(root, manifest_path, contracts.get("fixture_registry"))
    tool_registry = load_agent_tool_registry(tool_path)
    case_schema = json.loads(case_schema_path.read_text(encoding="utf-8"))
    case_validator = Draft202012Validator(case_schema, format_checker=FormatChecker())
    assertion_registry = json.loads(assertion_path.read_text(encoding="utf-8"))
    fixture_registry = json.loads(fixture_path.read_text(encoding="utf-8"))
    assertions = assertion_registry.get("assertions")
    fixtures = fixture_registry.get("fixtures")
    if not isinstance(assertions, list) or not isinstance(fixtures, list):
        raise EvalManifestError("Assertion and fixture registries must contain lists")
    assertion_by_id = _unique_registry(assertions, "assertion_id", "assertion")
    fixture_by_id = _unique_registry(fixtures, "fixture_id", "fixture")
    for fixture in fixture_by_id.values():
        if fixture.get("status") != "IMPLEMENTED":
            continue
        payload_path = fixture.get("payload_path")
        payload_sha256 = fixture.get("payload_sha256")
        version = fixture.get("version")
        fixture_id = fixture.get("fixture_id")
        if (
            not isinstance(payload_path, str)
            or not isinstance(payload_sha256, str)
            or not isinstance(version, int)
            or not isinstance(fixture_id, str)
        ):
            raise EvalManifestError(f"Implemented fixture {fixture_id} lacks a valid payload pin")
        try:
            load_synthetic_fixture(
                fixture_path.parent,
                fixture_id=fixture_id,
                version=version,
                payload_path=payload_path,
                payload_sha256=payload_sha256,
            )
        except FixtureBundleError as error:
            raise EvalManifestError(f"Implemented fixture {fixture_id} is invalid") from error

    seen_cases: set[tuple[str, int]] = set()
    referenced_calls = 0
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise EvalManifestError(f"Case at index {index} must be a mapping")
        schema_errors = sorted(case_validator.iter_errors(case), key=lambda error: list(error.path))
        if schema_errors:
            details = "; ".join(error.message for error in schema_errors)
            raise EvalManifestError(f"Case {case.get('id', index)} violates schema: {details}")
        identity = (str(case["id"]), int(case["version"]))
        if identity in seen_cases:
            raise EvalManifestError(f"Duplicate eval case identity: {identity}")
        seen_cases.add(identity)
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise EvalManifestError(f"Case {case['id']} lacks expected contract")
        for expected_call in expected.get("tool_calls", []):
            if not isinstance(expected_call, dict) or not isinstance(
                expected_call.get("operation_id"), str
            ):
                raise EvalManifestError(f"Case {case['id']} has malformed expected tool call")
            tool_registry.get(expected_call["operation_id"])
            referenced_calls += int(expected_call.get("count", 1))
        for assertion_id in expected.get("assertion_ids", []):
            if assertion_id not in assertion_by_id:
                raise EvalManifestError(
                    f"Case {case['id']} references unknown assertion {assertion_id}"
                )
        for fixture_id in case.get("fixture_refs", []):
            if fixture_id not in fixture_by_id:
                raise EvalManifestError(
                    f"Case {case['id']} references unknown fixture {fixture_id}"
                )
        for grader_id in case.get("graders", []):
            if grader_id not in graders:
                raise EvalManifestError(f"Case {case['id']} references unknown grader {grader_id}")

    blockers = manifest.get("release_blockers")
    if not isinstance(blockers, list) or not all(isinstance(item, str) for item in blockers):
        raise EvalManifestError("release_blockers must be a string list")
    unimplemented_fixtures = sum(
        fixture.get("status") != "IMPLEMENTED" for fixture in fixtures if isinstance(fixture, dict)
    )
    unimplemented_assertions = sum(
        assertion.get("implementation_status") != "IMPLEMENTED"
        for assertion in assertions
        if isinstance(assertion, dict)
    )
    fixture_blocker = "FIXTURE_PAYLOADS_NOT_IMPLEMENTED" in blockers
    assertion_blocker = "ASSERTION_GRADERS_NOT_IMPLEMENTED" in blockers
    if fixture_blocker != (unimplemented_fixtures > 0):
        raise EvalManifestError("fixture implementation blocker contradicts fixture registry")
    if assertion_blocker != (unimplemented_assertions > 0):
        raise EvalManifestError("assertion implementation blocker contradicts assertion registry")
    if unimplemented_fixtures == 0 and (
        fixture_registry.get("status") != "IMPLEMENTED_SYNTHETIC_NON_RELEASE"
    ):
        raise EvalManifestError("fixture registry completion status is stale")
    if unimplemented_assertions == 0 and (
        assertion_registry.get("status") != "IMPLEMENTED_SYNTHETIC_NON_RELEASE"
    ):
        raise EvalManifestError("assertion registry completion status is stale")
    if unimplemented_fixtures == 0 and unimplemented_assertions == 0:
        if manifest.get("status") != "LOCAL_SYNTHETIC_IMPLEMENTED_NON_RELEASE":
            raise EvalManifestError("eval manifest implementation status is stale")
        if (
            manifest.get("execution_state")
            != "LOCAL_SYNTHETIC_32_CASE_DEGRADED_COMPLETE_INTEGRATED_PROVIDER_PATH_NOT_RUN"
        ):
            raise EvalManifestError("eval manifest execution state is stale")
        if (
            runner.get("implementation_status")
            != "LOCAL_SYNTHETIC_32_CASE_DEGRADED_IMPLEMENTED_NON_RELEASE"
        ):
            raise EvalManifestError("eval runner implementation status is stale")
    release_eligible = manifest.get("release_eligible")
    if release_eligible is not False:
        raise EvalManifestError("Baseline manifest must remain release_eligible:false")
    return EvalContractReport(
        manifest_id=str(manifest.get("manifest_id")),
        manifest_version=str(manifest.get("manifest_version")),
        total_cases=len(cases),
        p0_cases=sum(case.get("severity") == "P0" for case in cases if isinstance(case, dict)),
        referenced_tool_calls=referenced_calls,
        unimplemented_fixture_payloads=unimplemented_fixtures,
        unimplemented_assertions=unimplemented_assertions,
        release_eligible=False,
        release_blockers=tuple(blockers),
    )


def _resolved_contract_path(root: Path, manifest: Path, value: Any) -> Path:
    if not isinstance(value, str):
        raise EvalManifestError("Normative contract path must be a string")
    path = (manifest.parent / value).resolve()
    if path != root and root not in path.parents:
        raise EvalManifestError(f"Contract path escapes repository: {value}")
    if not path.is_file():
        raise EvalManifestError(f"Contract path is missing: {value}")
    return path


def _unique_registry(entries: list[Any], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get(key), str):
            raise EvalManifestError(f"Every {label} requires {key}")
        entry_id = entry[key]
        if entry_id in result:
            raise EvalManifestError(f"Duplicate {label}: {entry_id}")
        result[entry_id] = entry
    return result
