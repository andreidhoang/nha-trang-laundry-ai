"""Validate context, decision, and capability-status control artifacts."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from nha_trang_laundry_contracts import (
    ReleaseCapability,
    RepositoryArtifactResolver,
    load_and_verify_release_manifest,
    load_trusted_release_signers,
)

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_DECISION_STATUS = {"OPEN", "RESOLVED", "DEFERRED"}
ALLOWED_AUTHORIZATION = {"NOT_AUTHORIZED", "AUTHORIZED"}
ALLOWED_CODE_STATUS = {"NOT_STARTED", "IN_PROGRESS", "COMPLETE", "BLOCKED"}
ALLOWED_PHASE_STATUS = {"PENDING", "IN_PROGRESS", "COMPLETE", "BLOCKED"}
WORK_ITEM_ID_PATTERN = re.compile(r"^[A-Z]+(?:-[A-Z]+)*-[0-9]{3}$")
RELEASE_COMMIT_ENV = "RELEASE_DEPLOYED_COMMIT_SHA"
RELEASE_STAGE_ENV = "RELEASE_DEPLOYMENT_STAGE"
RELEASE_TRUST_PIN_ENV = "RELEASE_TRUSTED_SIGNERS_SHA256"


def load_yaml(relative_path: str) -> dict[str, object]:
    with (ROOT / relative_path).open(encoding="utf-8") as artifact_file:
        content = yaml.safe_load(artifact_file)
    if not isinstance(content, dict):
        raise ValueError(f"{relative_path} must contain a mapping")
    return content


def repository_file(relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError(f"Repository file reference must be relative: {relative_path}")
    root = ROOT.resolve()
    path = (root / candidate).resolve()
    if path == root or root not in path.parents:
        raise ValueError(f"Repository file reference escapes root: {relative_path}")
    if not path.is_file():
        raise ValueError(f"Missing referenced file: {relative_path}")
    return path


def require_file(relative_path: str) -> None:
    repository_file(relative_path)


def validate_context_map() -> int:
    context_map = load_yaml("context/CONTEXT_MAP.yaml")
    sources = context_map.get("global_sources")
    domains = context_map.get("domains")
    if not isinstance(sources, list) or not isinstance(domains, dict):
        raise ValueError("CONTEXT_MAP.yaml must contain global_sources and domains")
    count = 0
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise ValueError("Each global source must define a path")
        require_file(source["path"])
        count += 1
    for domain, details in domains.items():
        if not isinstance(domain, str) or not isinstance(details, dict):
            raise ValueError("Each context domain must be a mapping")
        for section in ("sources", "contracts"):
            paths = details.get(section, [])
            if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                raise ValueError(f"{domain}.{section} must be a string list")
            for path in paths:
                require_file(path)
                count += 1
        prohibitions = details.get("prohibitions")
        if not isinstance(prohibitions, list) or not prohibitions:
            raise ValueError(f"{domain}.prohibitions must be a non-empty list")
    return count


def validate_decision_registry() -> int:
    registry = load_yaml("context/DECISION_REGISTRY.yaml")
    decisions = registry.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("DECISION_REGISTRY.yaml must contain decisions")
    ids: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("Each decision must be a mapping")
        decision_id = decision.get("id")
        status = decision.get("status")
        behavior = decision.get("fail_closed_behavior")
        source = decision.get("source")
        if not isinstance(decision_id, str) or decision_id in ids:
            raise ValueError("Decision IDs must be unique strings")
        if status not in ALLOWED_DECISION_STATUS:
            raise ValueError(f"Decision {decision_id} has invalid status")
        if not isinstance(behavior, str) or not behavior:
            raise ValueError(f"Decision {decision_id} lacks fail-closed behavior")
        if not isinstance(source, str):
            raise ValueError(f"Decision {decision_id} lacks a source")
        require_file(source)
        ids.add(decision_id)
    return len(ids)


def allowed_capabilities() -> set[str]:
    with (ROOT / "specs/contracts/release-gate-manifest-v1.schema.json").open(
        encoding="utf-8"
    ) as schema_file:
        schema = json.load(schema_file)
    capability = schema.get("properties", {}).get("capability", {})
    enums = capability.get("enum") if isinstance(capability, dict) else None
    if not isinstance(enums, list) or not all(isinstance(item, str) for item in enums):
        raise ValueError("Release gate schema lacks capability enum")
    return set(enums)


def allowed_gate_ids() -> set[str]:
    with (ROOT / "specs/contracts/release-gate-manifest-v1.schema.json").open(
        encoding="utf-8"
    ) as schema_file:
        schema = json.load(schema_file)
    gate_evidence = schema.get("properties", {}).get("gate_evidence", {})
    items = gate_evidence.get("items", {}) if isinstance(gate_evidence, dict) else {}
    properties = items.get("properties", {}) if isinstance(items, dict) else {}
    gate_id = properties.get("gate_id", {}) if isinstance(properties, dict) else {}
    enums = gate_id.get("enum") if isinstance(gate_id, dict) else None
    if not isinstance(enums, list) or not all(isinstance(item, str) for item in enums):
        raise ValueError("Release gate schema lacks gate_id enum")
    return set(enums)


def require_acyclic_dependencies(label: str, dependencies: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str) -> None:
        if item_id in visiting:
            raise ValueError(f"{label} dependency cycle contains {item_id}")
        if item_id in visited:
            return
        visiting.add(item_id)
        for dependency in dependencies[item_id]:
            if dependency not in dependencies:
                raise ValueError(f"{label} {item_id} has unknown dependency {dependency}")
            visit(dependency)
        visiting.remove(item_id)
        visited.add(item_id)

    for item_id in dependencies:
        visit(item_id)


def validate_gate_registry() -> tuple[int, dict[str, list[str]]]:
    registry = load_yaml("delivery/GATE_REGISTRY.yaml")
    release_contract = registry.get("release_contract")
    gates = registry.get("gates")
    requirements = registry.get("capability_requirements")
    if release_contract != "specs/contracts/release-gate-manifest-v1.schema.json":
        raise ValueError("GATE_REGISTRY.yaml must reference the release-gate contract")
    require_file(str(release_contract))
    if not isinstance(gates, list) or not isinstance(requirements, dict):
        raise ValueError("GATE_REGISTRY.yaml must contain gates and capability_requirements")

    expected_gate_ids = allowed_gate_ids()
    dependencies: dict[str, list[str]] = {}
    for gate in gates:
        if not isinstance(gate, dict):
            raise ValueError("Each gate must be a mapping")
        gate_id = gate.get("id")
        gate_dependencies = gate.get("depends_on")
        evidence = gate.get("required_evidence")
        if not isinstance(gate_id, str) or gate_id in dependencies:
            raise ValueError("Gate IDs must be unique strings")
        if not isinstance(gate_dependencies, list) or not all(
            isinstance(item, str) for item in gate_dependencies
        ):
            raise ValueError(f"Gate {gate_id} has invalid dependencies")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"Gate {gate_id} requires evidence")
        for field in ("permits_public_ingress", "permits_automatic_send"):
            if not isinstance(gate.get(field), bool):
                raise ValueError(f"Gate {gate_id} requires boolean {field}")
        dependencies[gate_id] = gate_dependencies
    if set(dependencies) != expected_gate_ids:
        raise ValueError("Gate registry IDs must exactly match the release-gate schema")
    require_acyclic_dependencies("Gate", dependencies)

    allowed = allowed_capabilities()
    if set(requirements) != allowed:
        raise ValueError("Gate registry capability mapping must match the release schema")
    typed_requirements: dict[str, list[str]] = {}
    for capability, gate_ids in requirements.items():
        if not isinstance(capability, str) or not isinstance(gate_ids, list) or not gate_ids:
            raise ValueError("Each capability requires a non-empty gate list")
        if len(gate_ids) != len(set(gate_ids)) or not all(
            isinstance(gate_id, str) and gate_id in dependencies for gate_id in gate_ids
        ):
            raise ValueError(f"Capability {capability} has invalid gate requirements")
        required_set = set(gate_ids)
        for gate_id in gate_ids:
            if not set(dependencies[gate_id]).issubset(required_set):
                raise ValueError(f"Capability {capability} omits a dependency of {gate_id}")
        typed_requirements[capability] = gate_ids
    return len(dependencies), typed_requirements


def validate_program_plan() -> tuple[int, int]:
    program = load_yaml("delivery/PROGRAM_PLAN.yaml")
    phases = program.get("phases")
    if not isinstance(phases, list):
        raise ValueError("PROGRAM_PLAN.yaml must contain phases")
    phase_dependencies: dict[str, list[str]] = {}
    phase_statuses: dict[str, str] = {}
    for phase in phases:
        if not isinstance(phase, dict):
            raise ValueError("Each program phase must be a mapping")
        phase_id = phase.get("id")
        dependencies = phase.get("depends_on")
        if not isinstance(phase_id, str) or phase_id in phase_dependencies:
            raise ValueError("Program phase IDs must be unique strings")
        if phase.get("status") not in ALLOWED_PHASE_STATUS:
            raise ValueError(f"Program phase {phase_id} has invalid status")
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) for item in dependencies
        ):
            raise ValueError(f"Program phase {phase_id} has invalid dependencies")
        phase_dependencies[phase_id] = dependencies
        phase_statuses[phase_id] = str(phase.get("status"))
    require_acyclic_dependencies("Program phase", phase_dependencies)

    queue = load_yaml("delivery/WORK_QUEUE.yaml")
    items = queue.get("items")
    if not isinstance(items, list):
        raise ValueError("WORK_QUEUE.yaml must contain items")
    work_ids: set[str] = set()
    work_dependencies: dict[str, list[str]] = {}
    work_phases: dict[str, str] = {}
    in_progress_ids: list[str] = []
    work_statuses: dict[str, str] = {}
    work_items_by_id: dict[str, dict[str, object]] = {}
    context_map = load_yaml("context/CONTEXT_MAP.yaml")
    context_domains = context_map.get("domains")
    global_sources = context_map.get("global_sources")
    if not isinstance(context_domains, dict) or not isinstance(global_sources, list):
        raise ValueError("CONTEXT_MAP.yaml must contain global_sources and domains")
    global_paths = {
        source["path"]
        for source in global_sources
        if isinstance(source, dict) and isinstance(source.get("path"), str)
    }
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each work item must be a mapping")
        item_id = item.get("id")
        phase_id = item.get("phase")
        dependencies = item.get("depends_on")
        item_domains = item.get("context_domains")
        if not isinstance(item_id, str) or not WORK_ITEM_ID_PATTERN.fullmatch(item_id):
            raise ValueError("Work item IDs must use a stable DOMAIN-001 style")
        if item_id in work_ids:
            raise ValueError(f"Duplicate work item ID: {item_id}")
        if phase_id not in phase_dependencies:
            raise ValueError(f"Work item {item_id} has unknown phase")
        if not isinstance(dependencies, list) or not all(
            isinstance(dependency, str) for dependency in dependencies
        ):
            raise ValueError(f"Work item {item_id} has invalid dependencies")
        if not isinstance(item_domains, list) or not all(
            isinstance(domain, str) and domain in context_domains for domain in item_domains
        ):
            raise ValueError(f"Work item {item_id} has invalid context domains")
        available_paths = set(global_paths)
        for domain in item_domains:
            details = context_domains[domain]
            if not isinstance(details, dict):
                raise ValueError(f"Context domain {domain} must be a mapping")
            for section in ("sources", "contracts"):
                available_paths.update(details.get(section, []))
        for section in ("normative_sources", "contracts"):
            declared_paths = item.get(section)
            if not isinstance(declared_paths, list) or not all(
                isinstance(path, str) for path in declared_paths
            ):
                raise ValueError(f"Work item {item_id} has invalid {section}")
            for path in declared_paths:
                require_file(path)
                if path not in available_paths:
                    raise ValueError(
                        f"Work item {item_id} {section} path is absent from "
                        f"its context packet: {path}"
                    )
        task_packet = item.get("task_packet")
        if task_packet is not None:
            if not isinstance(task_packet, str):
                raise ValueError(f"Work item {item_id} has an invalid task_packet")
            require_file(task_packet)
            packet_text = (ROOT / task_packet).read_text(encoding="utf-8")
            if item_id not in packet_text:
                raise ValueError(f"Work item {item_id} task packet does not name the work item")
        item_status = item.get("status")
        if item_status not in ALLOWED_PHASE_STATUS | {"READY"}:
            raise ValueError(f"Work item {item_id} has invalid status")
        if item_status == "IN_PROGRESS":
            in_progress_ids.append(item_id)
        if item_status == "BLOCKED" and not isinstance(item.get("blocking_condition"), str):
            raise ValueError(f"Blocked work item {item_id} requires blocking_condition")
        work_ids.add(item_id)
        work_dependencies[item_id] = dependencies
        work_phases[item_id] = str(phase_id)
        work_statuses[item_id] = str(item_status)
        work_items_by_id[item_id] = item
    if len(in_progress_ids) > 1:
        raise ValueError("Only one delivery work item may be IN_PROGRESS")
    require_acyclic_dependencies("Work item", work_dependencies)
    for item_id, dependencies in work_dependencies.items():
        if work_statuses[item_id] == "COMPLETE" and any(
            work_statuses[dependency] != "COMPLETE" for dependency in dependencies
        ):
            raise ValueError(f"Complete work item {item_id} has incomplete dependencies")
    for phase_id, phase_status in phase_statuses.items():
        statuses = {
            work_statuses[item_id] for item_id in work_ids if work_phases[item_id] == phase_id
        }
        if not statuses:
            raise ValueError(f"Program phase {phase_id} has no work items")
        if statuses == {"COMPLETE"}:
            expected_phase_status = "COMPLETE"
        elif "IN_PROGRESS" in statuses or "COMPLETE" in statuses:
            expected_phase_status = "IN_PROGRESS"
        elif statuses == {"BLOCKED"}:
            expected_phase_status = "BLOCKED"
        else:
            expected_phase_status = "PENDING"
        if phase_status != expected_phase_status:
            raise ValueError(f"Program phase {phase_id} status drifted from its work items")

    state = load_yaml("delivery/LOOP_STATE.yaml")
    current = state.get("current_work_item")
    expected_current = in_progress_ids[0] if in_progress_ids else None
    if current != expected_current:
        raise ValueError("LOOP_STATE current_work_item must match the queue IN_PROGRESS item")
    evidence_records = state.get("evidence_records")
    blocked_records = state.get("blocked_records", [])
    unblocked_records = state.get("unblocked_records", [])
    if (
        not isinstance(evidence_records, list)
        or not isinstance(blocked_records, list)
        or not isinstance(unblocked_records, list)
    ):
        raise ValueError("LOOP_STATE evidence, blocked, and unblocked records must be lists")
    recorded_work_items: set[str] = set()
    for record in evidence_records:
        if not isinstance(record, dict):
            raise ValueError("LOOP_STATE evidence record must be a mapping")
        work_item = record.get("work_item")
        path = record.get("path")
        if work_statuses.get(str(work_item)) != "COMPLETE" or not isinstance(path, str):
            raise ValueError("LOOP_STATE evidence must reference a complete work item")
        require_file(path)
        if str(work_item) in recorded_work_items:
            raise ValueError("LOOP_STATE contains duplicate evidence records")
        recorded_work_items.add(str(work_item))
        evidence = load_yaml(path)
        item = work_items_by_id[str(work_item)]
        if evidence.get("work_item") != work_item:
            raise ValueError(f"Evidence {path} work_item does not match LOOP_STATE")
        details = evidence.get("evidence")
        checks = evidence.get("checks")
        if not isinstance(details, dict) or not isinstance(checks, list):
            raise ValueError(f"Evidence {path} requires evidence and checks collections")
        required_entries = item.get("required_evidence")
        acceptance_entries = item.get("acceptance_checks")
        if not isinstance(required_entries, list) or not isinstance(acceptance_entries, list):
            raise ValueError(f"Work item {work_item} has invalid evidence/check declarations")
        required = set(required_entries) - {"rollback_assessment"}
        if not required.issubset(details):
            raise ValueError(f"Evidence {path} is missing required evidence entries")
        passing_commands = {
            check.get("command")
            for check in checks
            if isinstance(check, dict) and check.get("status") == "PASSED"
        }
        if passing_commands != set(acceptance_entries):
            raise ValueError(f"Evidence {path} checks drifted from the work item")
    return len(phase_dependencies), len(work_ids)


def validate_capability_status(gate_requirements: dict[str, list[str]]) -> int:
    status = load_yaml("delivery/CAPABILITY_STATUS.yaml")
    capabilities = status.get("capabilities")
    if not isinstance(capabilities, list):
        raise ValueError("CAPABILITY_STATUS.yaml must contain capabilities")
    if status.get("default_authorization") != "NOT_AUTHORIZED":
        raise ValueError("CAPABILITY_STATUS default authorization must remain NOT_AUTHORIZED")
    allowed = allowed_capabilities()
    seen: set[str] = set()
    for capability in capabilities:
        if not isinstance(capability, dict):
            raise ValueError("Each capability status must be a mapping")
        capability_id = capability.get("id")
        authorization = capability.get("authorization")
        code_status = capability.get("code_status")
        manifest = capability.get("release_manifest")
        trusted_signers_path = capability.get("trusted_signers")
        required_gates = capability.get("required_gates")
        if (
            not isinstance(capability_id, str)
            or capability_id not in allowed
            or capability_id in seen
        ):
            raise ValueError("Capability status has unknown or duplicate ID")
        if authorization not in ALLOWED_AUTHORIZATION:
            raise ValueError(f"Capability {capability_id} has invalid authorization")
        if code_status not in ALLOWED_CODE_STATUS:
            raise ValueError(f"Capability {capability_id} has invalid code status")
        if required_gates != gate_requirements[capability_id]:
            raise ValueError(f"Capability {capability_id} gate requirements drifted")
        release_metadata = (manifest, trusted_signers_path)
        if authorization == "NOT_AUTHORIZED":
            if any(value is not None for value in release_metadata):
                raise ValueError(
                    f"Unauthorized capability {capability_id} must not retain release authority"
                )
        elif not all(isinstance(value, str) for value in release_metadata):
            raise ValueError(
                f"Authorized capability {capability_id} requires verified release metadata"
            )
        else:
            assert isinstance(manifest, str)
            assert isinstance(trusted_signers_path, str)
            deployed_commit_sha = os.environ.get(RELEASE_COMMIT_ENV)
            deployment_stage = os.environ.get(RELEASE_STAGE_ENV)
            trusted_signers_sha256 = os.environ.get(RELEASE_TRUST_PIN_ENV)
            if not all(
                isinstance(value, str) and value
                for value in (
                    deployed_commit_sha,
                    deployment_stage,
                    trusted_signers_sha256,
                )
            ):
                raise ValueError(
                    f"Authorized capability {capability_id} requires out-of-band "
                    "deployment release context"
                )
            assert isinstance(deployed_commit_sha, str)
            assert isinstance(deployment_stage, str)
            assert isinstance(trusted_signers_sha256, str)
            current = datetime.now(UTC)
            trusted_signers = load_trusted_release_signers(
                root=ROOT,
                path=repository_file(trusted_signers_path),
                expected_sha256=trusted_signers_sha256,
            )
            verified = load_and_verify_release_manifest(
                root=ROOT,
                path=repository_file(manifest),
                trusted_signers=trusted_signers,
                expected_commit_sha=deployed_commit_sha,
                expected_stage=deployment_stage,
                expected_capability=ReleaseCapability(capability_id),
                now=current,
                artifact_resolver=RepositoryArtifactResolver(ROOT),
            )
            if not verified.authorizes(
                commit_sha=deployed_commit_sha,
                stage=deployment_stage,
                capability=ReleaseCapability(capability_id),
                now=current,
            ):
                raise ValueError(
                    f"Capability {capability_id} release authorization is not currently active"
                )
        seen.add(capability_id)
    return len(allowed)


def main() -> None:
    source_count = validate_context_map()
    decision_count = validate_decision_registry()
    gate_count, gate_requirements = validate_gate_registry()
    phase_count, work_item_count = validate_program_plan()
    capability_count = validate_capability_status(gate_requirements)
    print(
        "Context drift check passed: "
        f"{source_count} source references, {decision_count} decisions, "
        f"{gate_count} gates, {phase_count} phases, {work_item_count} work items, "
        f"{capability_count} capabilities."
    )


if __name__ == "__main__":
    main()
