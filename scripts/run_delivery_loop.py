"""Select one safe, unblocked engineering slice and render its Codex brief.

This is a delivery-control utility. It never invokes a model, changes product
state, authorizes a release, or enables a customer-facing capability.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from assemble_context import assemble_packet, load_context_map
from check_context_drift import validate_all
from delivery_state import (
    JOURNAL_NAME,
    delivery_generation_digest,
    delivery_state_mutex,
    recover_delivery_state,
)

ROOT = Path(__file__).resolve().parents[1]
QUEUE_PATH = ROOT / "delivery/WORK_QUEUE.yaml"
STATE_PATH = ROOT / "delivery/LOOP_STATE.yaml"
PROGRAM_PATH = ROOT / "delivery/PROGRAM_PLAN.yaml"
DECISIONS_PATH = ROOT / "context/DECISION_REGISTRY.yaml"
ALLOWED_STATUSES = {"PENDING", "READY", "IN_PROGRESS", "COMPLETE", "BLOCKED"}


def load_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as artifact_file:
        loaded = yaml.safe_load(artifact_file)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a mapping")
    return loaded


def queue_items() -> list[dict[str, Any]]:
    queue = load_mapping(QUEUE_PATH)
    items = queue.get("items")
    if queue.get("schema_version") != 1 or not isinstance(items, list):
        raise ValueError("WORK_QUEUE.yaml must have schema_version 1 and an items list")
    return items


def decision_statuses() -> dict[str, str]:
    registry = load_mapping(DECISIONS_PATH)
    decisions = registry.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("DECISION_REGISTRY.yaml must contain a decisions list")
    statuses: dict[str, str] = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("Each decision must be a mapping")
        decision_id = decision.get("id")
        status = decision.get("status")
        if not isinstance(decision_id, str) or not isinstance(status, str):
            raise ValueError("Each decision requires string id and status")
        statuses[decision_id] = status
    return statuses


def validate_queue(items: list[dict[str, Any]]) -> None:
    context_domains = load_context_map().get("domains")
    if not isinstance(context_domains, dict):
        raise ValueError("CONTEXT_MAP.yaml must contain a domains mapping")
    decision_ids = decision_statuses()
    ids: set[str] = set()
    in_progress = 0
    for item in items:
        item_id = item.get("id")
        status = item.get("status")
        domains = item.get("context_domains")
        dependencies = item.get("depends_on")
        blockers = item.get("blocked_by_decisions")
        checks = item.get("acceptance_checks")
        phase = item.get("phase")
        task_packet = item.get("task_packet")
        if not isinstance(item_id, str) or item_id in ids:
            raise ValueError("Each work item needs a unique string id")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Work item {item_id} has invalid status")
        if not isinstance(phase, str) or not phase:
            raise ValueError(f"Work item {item_id} requires a stable phase")
        if status == "IN_PROGRESS":
            in_progress += 1
        if not isinstance(domains, list) or not all(
            domain in context_domains for domain in domains
        ):
            raise ValueError(f"Work item {item_id} has unknown context domain")
        if not isinstance(dependencies, list) or not all(
            isinstance(value, str) for value in dependencies
        ):
            raise ValueError(f"Work item {item_id} has invalid dependencies")
        if not isinstance(blockers, list) or not all(value in decision_ids for value in blockers):
            raise ValueError(f"Work item {item_id} has unknown decision blocker")
        if (
            not isinstance(checks, list)
            or not checks
            or not all(isinstance(value, str) for value in checks)
        ):
            raise ValueError(f"Work item {item_id} requires acceptance checks")
        if task_packet is not None:
            if not isinstance(task_packet, str) or not task_packet:
                raise ValueError(f"Work item {item_id} has an invalid task packet")
            packet_path = (ROOT / task_packet).resolve()
            if not packet_path.is_relative_to(ROOT.resolve()) or not packet_path.is_file():
                raise ValueError(f"Work item {item_id} task packet does not exist")
        ids.add(item_id)
    if in_progress > 1:
        raise ValueError("Only one delivery work item may be IN_PROGRESS")
    for item in items:
        unknown_dependencies = set(item["depends_on"]) - ids
        if unknown_dependencies:
            raise ValueError(
                f"Work item {item['id']} has unknown dependencies: {unknown_dependencies}"
            )


def select_next_item(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    current = [item for item in items if item["status"] == "IN_PROGRESS"]
    if current:
        if len(current) != 1:
            raise ValueError("Only one delivery work item may be IN_PROGRESS")
        return current[0]
    by_id = {str(item["id"]): item for item in items}
    decisions = decision_statuses()
    candidates: list[dict[str, Any]] = []
    for item in items:
        if item["status"] not in {"PENDING", "READY"}:
            continue
        if any(by_id[dependency]["status"] != "COMPLETE" for dependency in item["depends_on"]):
            continue
        if any(decisions[decision] != "RESOLVED" for decision in item["blocked_by_decisions"]):
            continue
        candidates.append(item)
    if not candidates:
        return None
    return min(candidates, key=lambda item: (int(item.get("priority", 9999)), str(item["id"])))


def render_brief(item: dict[str, Any]) -> str:
    context = assemble_packet(str(item["id"]), list(item["context_domains"]))
    checks = "\n".join(f"- `{command}`" for command in item["acceptance_checks"])
    evidence = "\n".join(f"- {entry}" for entry in item["required_evidence"])
    task_packet_section = ""
    task_packet = item.get("task_packet")
    if isinstance(task_packet, str):
        packet_text = (ROOT / task_packet).read_text(encoding="utf-8").strip()
        task_packet_section = (
            f"## Atomic task packet\n\nSource: `{task_packet}`\n\n{packet_text}\n\n"
        )
    return (
        f"# Delivery loop brief: {item['id']}\n\n"
        f"**Phase:** {item['phase']}  \n"
        f"**Outcome:** {item['title']}\n\n"
        f"{context}\n"
        f"{task_packet_section}"
        "## Acceptance checks\n\n"
        f"{checks}\n\n"
        "## Required evidence\n\n"
        f"{evidence}\n\n"
        "## Codex execution rule\n\n"
        "Implement only this reviewable slice. Do not advance the queue until an "
        "evidence record reports every declared check as `PASSED`. If an unknown "
        "policy is encountered, record the decision blocker and implement the "
        "specified fail-closed behavior.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "controller-json"),
        default="markdown",
    )
    parser.add_argument(
        "--no-recover",
        action="store_true",
        help="Fail closed on a pending journal instead of rolling it forward.",
    )
    arguments = parser.parse_args()
    if arguments.no_recover and arguments.format != "controller-json":
        raise SystemExit("--no-recover is supported only with --format controller-json.")

    with delivery_state_mutex():
        journal_path = ROOT / "delivery" / JOURNAL_NAME
        if arguments.no_recover:
            if journal_path.exists():
                raise SystemExit(
                    "Pending delivery transaction requires recovery; preflight made no mutation."
                )
        else:
            recover_delivery_state()
        context_validation = validate_all() if arguments.format == "controller-json" else None
        queue = load_mapping(QUEUE_PATH)
        items = queue.get("items")
        if queue.get("schema_version") != 1 or not isinstance(items, list):
            raise ValueError("WORK_QUEUE.yaml must have schema_version 1 and an items list")
        validate_queue(items)
        selected = select_next_item(items)
        if selected is None and arguments.format != "controller-json":
            raise SystemExit(
                "No current or dependency-complete, policy-unblocked work item exists."
            )
        if arguments.format == "controller-json":
            generation = delivery_generation_digest(
                queue,
                load_mapping(STATE_PATH),
                load_mapping(PROGRAM_PATH),
            )
            output = json.dumps(
                {
                    "context_validation": context_validation,
                    "generation": generation,
                    "selected": selected,
                    "statuses": {str(item["id"]): str(item["status"]) for item in items},
                },
                indent=2,
                sort_keys=True,
            )
        elif arguments.format == "json":
            output = json.dumps(selected, indent=2, sort_keys=True)
        else:
            assert selected is not None
            output = render_brief(selected)
    print(output, end="" if output.endswith("\n") else "\n")


if __name__ == "__main__":
    main()
