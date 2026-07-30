from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]


def run_script(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _active_workspace(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    workspace = tmp_path / "workspace"
    for directory in ("context", "delivery", "scripts", "specs"):
        source = ROOT / directory
        destination = workspace / directory
        destination.mkdir(parents=True, exist_ok=True)
        for file_path in source.rglob("*"):
            if file_path.is_file():
                target = destination / file_path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(file_path.read_bytes())
    queue_path = workspace / "delivery/WORK_QUEUE.yaml"
    queue = yaml.safe_load(queue_path.read_text(encoding="utf-8"))
    item = next(candidate for candidate in queue["items"] if candidate["id"] == "AGENT-001")
    item["status"] = "IN_PROGRESS"
    item.pop("blocking_condition", None)
    queue_path.write_text(yaml.safe_dump(queue, sort_keys=False), encoding="utf-8")
    state_path = workspace / "delivery/LOOP_STATE.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state.update(current_work_item=item["id"], last_result="IN_PROGRESS", blocker=None)
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    return workspace, item


def _run_workspace(
    workspace: Path, script: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(workspace / "scripts" / script), *arguments],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )


def test_delivery_loop_selects_first_safe_ready_item(tmp_path: Path) -> None:
    workspace, _ = _active_workspace(tmp_path)
    result = _run_workspace(workspace, "run_delivery_loop.py")
    state = yaml.safe_load((workspace / "delivery/LOOP_STATE.yaml").read_text())
    current = state["current_work_item"]

    assert result.returncode == 0
    assert f"# Delivery loop brief: {current}" in result.stdout
    assert f"# Context packet: {current}" in result.stdout
    assert "Do not advance the queue" in result.stdout


def test_agent_context_packet_contains_release_support_contracts() -> None:
    result = run_script(
        "assemble_context.py",
        "--task-id",
        "AGENT-001",
        "--domain",
        "runtime_architecture",
        "--domain",
        "agent_tools",
        "--domain",
        "evaluation_release",
        "--domain",
        "privacy_consent",
    )

    assert result.returncode == 0, result.stderr
    for contract in (
        "specs/contracts/trusted-release-signers-v1.schema.json",
        "specs/contracts/provider-data-evidence-v1.schema.json",
        "specs/contracts/container-scan-evidence-v1.schema.json",
    ):
        assert f"- `{contract}`" in result.stdout


def test_evidence_recorder_rejects_completion_without_an_active_slice() -> None:
    result = run_script("record_delivery_evidence.py", "--work-item", "DOMAIN-001", "--complete")

    assert result.returncode != 0
    assert "current IN_PROGRESS" in result.stderr


def test_evidence_recorder_advances_only_with_declared_passing_checks(tmp_path: Path) -> None:
    workspace, item = _active_workspace(tmp_path)
    evidence = workspace / "evidence.yaml"
    evidence.write_text(
        yaml.safe_dump(
            {
                "work_item": item["id"],
                "recorded_at": "2026-07-28T00:00:00Z",
                "requirement_contract": item["normative_sources"][0],
                "rollback_impact": "Revert this local slice; no release capability change.",
                "unresolved_assumptions": "None.",
                "evidence": {
                    name: "passed"
                    for name in item["required_evidence"]
                    if name != "rollback_assessment"
                },
                "checks": [
                    {"command": command, "status": "PASSED"}
                    for command in item["acceptance_checks"]
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    complete = subprocess.run(
        [
            sys.executable,
            str(workspace / "scripts" / "record_delivery_evidence.py"),
            "--work-item",
            item["id"],
            "--complete",
            "--evidence",
            "evidence.yaml",
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )

    assert complete.returncode == 0
    completed_queue = yaml.safe_load((workspace / "delivery" / "WORK_QUEUE.yaml").read_text())
    completed = next(
        candidate for candidate in completed_queue["items"] if candidate["id"] == item["id"]
    )
    assert completed["status"] == "COMPLETE"


def test_evidence_recorder_records_blocker_and_releases_active_slot(tmp_path: Path) -> None:
    workspace, item = _active_workspace(tmp_path)
    blocked = subprocess.run(
        [
            sys.executable,
            str(workspace / "scripts" / "record_delivery_evidence.py"),
            "--work-item",
            item["id"],
            "--block",
            "--reason",
            "Non-production OIDC tenant is unavailable.",
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )

    assert blocked.returncode == 0, blocked.stderr
    blocked_queue = yaml.safe_load((workspace / "delivery" / "WORK_QUEUE.yaml").read_text())
    blocked_item = next(
        candidate for candidate in blocked_queue["items"] if candidate["id"] == item["id"]
    )
    state = yaml.safe_load((workspace / "delivery" / "LOOP_STATE.yaml").read_text())
    assert blocked_item["status"] == "BLOCKED"
    assert state["current_work_item"] is None
    assert state["blocked_records"][0]["work_item"] == item["id"]

    unblocked = subprocess.run(
        [
            sys.executable,
            str(workspace / "scripts" / "record_delivery_evidence.py"),
            "--work-item",
            item["id"],
            "--unblock",
            "--reason",
            "The external test condition is now verified available.",
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )

    assert unblocked.returncode == 0, unblocked.stderr
    unblocked_queue = yaml.safe_load((workspace / "delivery" / "WORK_QUEUE.yaml").read_text())
    unblocked_item = next(
        candidate for candidate in unblocked_queue["items"] if candidate["id"] == item["id"]
    )
    assert unblocked_item["status"] == "PENDING"
