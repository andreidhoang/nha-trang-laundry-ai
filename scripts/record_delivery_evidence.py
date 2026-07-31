"""Advance a delivery item only when its evidence record is complete and passing."""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from delivery_state import (
    commit_delivery_state,
    delivery_generation_digest,
    delivery_state_mutex,
    recover_delivery_state,
    validate_delivery_generation,
)
from run_delivery_loop import (
    QUEUE_PATH,
    ROOT,
    decision_statuses,
    select_next_item,
    validate_queue,
)

STATE_PATH = ROOT / "delivery/LOOP_STATE.yaml"
PROGRAM_PATH = ROOT / "delivery/PROGRAM_PLAN.yaml"
GENERATION_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_REASON_LENGTH = 1_000
SENSITIVE_REASON_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{4,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|key|password|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{4,}"),
    re.compile(
        r"(?<![A-Za-z0-9_-])"
        r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
        r"(?![A-Za-z0-9_-])"
    ),
)


def load_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as artifact_file:
        loaded = yaml.safe_load(artifact_file)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a mapping")
    return loaded


def repository_relative(path_text: str, work_item: str) -> Path:
    path = (ROOT / path_text).resolve()
    if ROOT not in path.parents or not path.is_file():
        raise ValueError("Evidence must be an existing file inside the repository")
    expected_path = f"evidence/delivery-loop/{work_item}.yaml"
    if path.relative_to(ROOT).as_posix() != expected_path:
        raise ValueError(f"Evidence must use the canonical path {expected_path}")
    return path


def validate_evidence(evidence_path: Path, item: dict[str, Any]) -> None:
    evidence = load_mapping(evidence_path)
    if evidence.get("work_item") != item["id"]:
        raise ValueError("Evidence work_item does not match the queue item")
    for field in (
        "recorded_at",
        "requirement_contract",
        "rollback_impact",
        "unresolved_assumptions",
    ):
        value = evidence.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Evidence requires a non-empty string {field}")
    evidence_items = evidence.get("evidence")
    if not isinstance(evidence_items, dict):
        raise ValueError("Evidence requires an evidence mapping")
    required = set(item["required_evidence"]) - {"rollback_assessment"}
    missing_evidence = required - set(evidence_items)
    if missing_evidence:
        raise ValueError(f"Evidence is missing required entries: {sorted(missing_evidence)}")
    checks = evidence.get("checks")
    if not isinstance(checks, list):
        raise ValueError("Evidence requires a checks list")
    passed = {
        check.get("command")
        for check in checks
        if isinstance(check, dict) and check.get("status") == "PASSED"
    }
    missing = set(item["acceptance_checks"]) - passed
    if missing:
        raise ValueError(f"Evidence is missing passing declared checks: {sorted(missing)}")
    if len(passed) != len(checks) or passed != set(item["acceptance_checks"]):
        raise ValueError("Evidence checks must exactly match declared passing checks")


def validated_reason(value: object) -> str:
    reason = value.strip() if isinstance(value, str) else ""
    if not reason:
        raise SystemExit("A non-empty sanitized reason is required.")
    if len(reason) > MAX_REASON_LENGTH:
        raise SystemExit(f"Reason exceeds {MAX_REASON_LENGTH} characters.")
    if "\x00" in reason:
        raise SystemExit("Reason contains a prohibited NUL character.")
    if any(pattern.search(reason) is not None for pattern in SENSITIVE_REASON_PATTERNS):
        raise SystemExit("Reason appears to contain sensitive credential material.")
    return reason


def synchronize_program(items: list[dict[str, Any]], program: dict[str, Any]) -> None:
    phases = program.get("phases")
    if not isinstance(phases, list):
        raise ValueError("PROGRAM_PLAN.yaml must contain phases")
    for phase in phases:
        if not isinstance(phase, dict) or not isinstance(phase.get("id"), str):
            raise ValueError("PROGRAM_PLAN.yaml phase is invalid")
        phase_items = [item for item in items if item.get("phase") == phase["id"]]
        statuses = {item["status"] for item in phase_items}
        if not statuses:
            raise ValueError(f"Program phase {phase['id']} has no work items")
        if statuses == {"COMPLETE"}:
            phase["status"] = "COMPLETE"
        elif "IN_PROGRESS" in statuses or "COMPLETE" in statuses:
            phase["status"] = "IN_PROGRESS"
        elif statuses == {"BLOCKED"}:
            phase["status"] = "BLOCKED"
        else:
            phase["status"] = "PENDING"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-item", required=True)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--complete", action="store_true")
    parser.add_argument("--block", action="store_true")
    parser.add_argument("--unblock", action="store_true")
    parser.add_argument("--evidence")
    parser.add_argument("--reason")
    parser.add_argument("--expected-generation", required=True)
    arguments = parser.parse_args()
    if sum((arguments.start, arguments.complete, arguments.block, arguments.unblock)) != 1:
        raise SystemExit("Choose exactly one of --start, --complete, --block, or --unblock.")

    with delivery_state_mutex():
        recover_delivery_state()
        queue = load_mapping(QUEUE_PATH)
        items = queue.get("items")
        if not isinstance(items, list):
            raise ValueError("WORK_QUEUE.yaml must contain an items list")
        validate_queue(items)
        item = next(
            (candidate for candidate in items if candidate["id"] == arguments.work_item),
            None,
        )
        if item is None:
            raise SystemExit(f"Unknown work item: {arguments.work_item}")
        state = load_mapping(STATE_PATH)
        program = load_mapping(PROGRAM_PATH)
        validate_delivery_generation(queue, state, program)
        expected_generation = arguments.expected_generation
        if (
            not isinstance(expected_generation, str)
            or GENERATION_PATTERN.fullmatch(expected_generation) is None
        ):
            raise SystemExit("--expected-generation must be a lowercase SHA-256 digest.")
        if delivery_generation_digest(queue, state, program) != expected_generation:
            raise SystemExit("Delivery generation changed; obtain a fresh controller snapshot.")
        timestamp = datetime.now(UTC).isoformat()

        if arguments.start:
            if state.get("current_work_item") is not None:
                raise SystemExit("Another delivery work item is already IN_PROGRESS.")
            if item["status"] not in {"PENDING", "READY"}:
                raise SystemExit("Only a PENDING or READY work item can be started.")
            next_item = select_next_item(items)
            if next_item is None or next_item["id"] != item["id"]:
                raise SystemExit(
                    "Only the next dependency-complete, policy-unblocked item can be started."
                )
            item["status"] = "IN_PROGRESS"
            state.update(current_work_item=item["id"], last_result="IN_PROGRESS", blocker=None)
        elif arguments.complete:
            if item["status"] != "IN_PROGRESS" or state.get("current_work_item") != item["id"]:
                raise SystemExit("Only the current IN_PROGRESS work item can be completed.")
            if not arguments.evidence:
                raise SystemExit("--complete requires --evidence.")
            evidence_path = repository_relative(arguments.evidence, str(item["id"]))
            validate_evidence(evidence_path, item)
            item["status"] = "COMPLETE"
            records = state.setdefault("evidence_records", [])
            if not isinstance(records, list):
                raise ValueError("LOOP_STATE.yaml evidence_records must be a list")
            records.append(
                {
                    "work_item": item["id"],
                    "path": evidence_path.relative_to(ROOT).as_posix(),
                    "recorded_at": timestamp,
                }
            )
            state.update(current_work_item=None, last_result="COMPLETE", blocker=None)
        elif arguments.block:
            active_block = (
                item["status"] == "IN_PROGRESS" and state.get("current_work_item") == item["id"]
            )
            unstarted_block = (
                item["status"] in {"PENDING", "READY"}
                and state.get("current_work_item") is None
                and (next_item := select_next_item(items)) is not None
                and next_item["id"] == item["id"]
            )
            if not active_block and not unstarted_block:
                raise SystemExit(
                    "Only the current or next dependency-ready work item can be blocked."
                )
            reason = validated_reason(arguments.reason)
            item["status"] = "BLOCKED"
            item["blocking_condition"] = reason
            blocked_records = state.setdefault("blocked_records", [])
            if not isinstance(blocked_records, list):
                raise ValueError("LOOP_STATE.yaml blocked_records must be a list")
            blocked_records.append(
                {"work_item": item["id"], "reason": reason, "recorded_at": timestamp}
            )
            state.update(current_work_item=None, last_result="BLOCKED", blocker=reason)
        else:
            if item["status"] != "BLOCKED":
                raise SystemExit("Only a BLOCKED work item can be unblocked.")
            reason = validated_reason(arguments.reason)
            decisions = decision_statuses()
            unresolved = [
                decision
                for decision in item["blocked_by_decisions"]
                if decisions[decision] != "RESOLVED"
            ]
            if unresolved:
                raise SystemExit(f"Work item still has unresolved decision blockers: {unresolved}")
            item["status"] = "PENDING"
            item.pop("blocking_condition", None)
            unblocked_records = state.setdefault("unblocked_records", [])
            if not isinstance(unblocked_records, list):
                raise ValueError("LOOP_STATE.yaml unblocked_records must be a list")
            unblocked_records.append(
                {"work_item": item["id"], "reason": reason, "recorded_at": timestamp}
            )
            state.update(last_result="UNBLOCKED", blocker=None)

        queue["last_updated"] = timestamp[:10]
        state["last_updated"] = timestamp
        program["last_updated"] = timestamp[:10]
        synchronize_program(items, program)
        commit_delivery_state(queue, state, program)
        result_message = f"Delivery work item {item['id']} is now {item['status']}."
    print(result_message)


if __name__ == "__main__":
    main()
