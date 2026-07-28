from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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


def test_delivery_loop_selects_first_safe_ready_item() -> None:
    result = run_script("run_delivery_loop.py")
    state = yaml.safe_load((ROOT / "delivery/LOOP_STATE.yaml").read_text())
    current = state["current_work_item"]

    assert result.returncode == 0
    assert f"# Delivery loop brief: {current}" in result.stdout
    assert f"# Context packet: {current}" in result.stdout
    assert "Do not advance the queue" in result.stdout


def test_evidence_recorder_rejects_completion_without_an_active_slice() -> None:
    result = run_script("record_delivery_evidence.py", "--work-item", "DOMAIN-001", "--complete")

    assert result.returncode != 0
    assert "current IN_PROGRESS" in result.stderr


def test_evidence_recorder_advances_only_with_declared_passing_checks(tmp_path: Path) -> None:
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

    queue = yaml.safe_load((workspace / "delivery/WORK_QUEUE.yaml").read_text())
    item = next(candidate for candidate in queue["items"] if candidate["status"] == "IN_PROGRESS")
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

    queue = yaml.safe_load((workspace / "delivery/WORK_QUEUE.yaml").read_text())
    item = next(candidate for candidate in queue["items"] if candidate["status"] == "IN_PROGRESS")
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
