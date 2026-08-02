from __future__ import annotations

import json
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


def _copied_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    for root_file in ROOT.iterdir():
        if root_file.is_file():
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / root_file.name).write_bytes(root_file.read_bytes())
    for directory in (
        "context",
        "delivery",
        "docs",
        "evidence",
        "scripts",
        "specs",
        "templates",
    ):
        source = ROOT / directory
        destination = workspace / directory
        destination.mkdir(parents=True, exist_ok=True)
        for file_path in source.rglob("*"):
            if file_path.is_file():
                target = destination / file_path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(file_path.read_bytes())
    return workspace


def _active_workspace(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    workspace = _copied_workspace(tmp_path)
    queue_path = workspace / "delivery/WORK_QUEUE.yaml"
    queue = yaml.safe_load(queue_path.read_text(encoding="utf-8"))
    for candidate in queue["items"]:
        if candidate["status"] == "IN_PROGRESS":
            candidate["status"] = "PENDING"
    item = next(candidate for candidate in queue["items"] if candidate["id"] == "AGENT-001")
    item["status"] = "IN_PROGRESS"
    item.pop("blocking_condition", None)
    queue_path.write_text(yaml.safe_dump(queue, sort_keys=False), encoding="utf-8")
    state_path = workspace / "delivery/LOOP_STATE.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state.update(current_work_item=item["id"], last_result="IN_PROGRESS", blocker=None)
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    program_path = workspace / "delivery/PROGRAM_PLAN.yaml"
    program = yaml.safe_load(program_path.read_text(encoding="utf-8"))
    for phase in program["phases"]:
        phase_statuses = {
            candidate["status"] for candidate in queue["items"] if candidate["phase"] == phase["id"]
        }
        if phase_statuses == {"COMPLETE"}:
            phase["status"] = "COMPLETE"
        elif "IN_PROGRESS" in phase_statuses or "COMPLETE" in phase_statuses:
            phase["status"] = "IN_PROGRESS"
        elif phase_statuses == {"BLOCKED"}:
            phase["status"] = "BLOCKED"
        else:
            phase["status"] = "PENDING"
    program_path.write_text(yaml.safe_dump(program, sort_keys=False), encoding="utf-8")
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


def _controller_generation(workspace: Path) -> str:
    snapshot = _run_workspace(
        workspace,
        "run_delivery_loop.py",
        "--format",
        "controller-json",
    )
    assert snapshot.returncode == 0, snapshot.stderr
    generation = json.loads(snapshot.stdout)["generation"]
    assert isinstance(generation, str)
    return generation


def _reset_to_observability_pending(workspace: Path) -> None:
    queue_path = workspace / "delivery/WORK_QUEUE.yaml"
    queue = yaml.safe_load(queue_path.read_text(encoding="utf-8"))
    reset_items = {
        "OBSERVABILITY-001",
        "POLICY-001",
        "CONTAINER-001",
        "SUPPLYCHAIN-001",
    }
    while True:
        dependents = {
            item["id"]
            for item in queue["items"]
            if reset_items.intersection(item.get("depends_on", []))
        }
        expanded = reset_items | dependents
        if expanded == reset_items:
            break
        reset_items = expanded
    for item in queue["items"]:
        if item["status"] == "IN_PROGRESS":
            item["status"] = "PENDING"
        if item["id"] in reset_items:
            item["status"] = "PENDING"
    queue_path.write_text(yaml.safe_dump(queue, sort_keys=False), encoding="utf-8")
    state_path = workspace / "delivery/LOOP_STATE.yaml"
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state.update(current_work_item=None, last_result="COMPLETE", blocker=None)
    state["evidence_records"] = [
        record for record in state["evidence_records"] if record["work_item"] not in reset_items
    ]
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    program_path = workspace / "delivery/PROGRAM_PLAN.yaml"
    program = yaml.safe_load(program_path.read_text(encoding="utf-8"))
    for phase in program["phases"]:
        statuses = {item["status"] for item in queue["items"] if item["phase"] == phase["id"]}
        if statuses == {"COMPLETE"}:
            phase["status"] = "COMPLETE"
        elif "IN_PROGRESS" in statuses or "COMPLETE" in statuses:
            phase["status"] = "IN_PROGRESS"
        elif statuses == {"BLOCKED"}:
            phase["status"] = "BLOCKED"
        else:
            phase["status"] = "PENDING"
    program_path.write_text(yaml.safe_dump(program, sort_keys=False), encoding="utf-8")


def test_delivery_loop_selects_first_safe_ready_item(tmp_path: Path) -> None:
    workspace, _ = _active_workspace(tmp_path)
    result = _run_workspace(workspace, "run_delivery_loop.py")
    state = yaml.safe_load((workspace / "delivery/LOOP_STATE.yaml").read_text())
    current = state["current_work_item"]

    assert result.returncode == 0
    assert f"# Delivery loop brief: {current}" in result.stdout
    assert f"# Context packet: {current}" in result.stdout
    assert "Do not advance the queue" in result.stdout


def test_delivery_loop_emits_one_locked_controller_snapshot(tmp_path: Path) -> None:
    workspace = _copied_workspace(tmp_path)
    _reset_to_observability_pending(workspace)
    result = _run_workspace(
        workspace,
        "run_delivery_loop.py",
        "--format",
        "controller-json",
    )

    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    context_validation = snapshot["context_validation"]
    generation = snapshot["generation"]
    selected = snapshot["selected"]
    statuses = snapshot["statuses"]
    assert context_validation["work_items"] == len(statuses)
    assert len(generation) == 64
    assert int(generation, 16) >= 0
    assert isinstance(selected, dict)
    assert statuses[selected["id"]] == selected["status"]
    assert set(statuses) == {
        item["id"]
        for item in yaml.safe_load(
            (workspace / "delivery/WORK_QUEUE.yaml").read_text(encoding="utf-8")
        )["items"]
    }


def test_delivery_preflight_refuses_pending_journal_without_recovery(tmp_path: Path) -> None:
    workspace = _copied_workspace(tmp_path)
    journal = workspace / "delivery/.delivery-state.transaction.json"
    journal.write_text("{}", encoding="utf-8")

    result = _run_workspace(
        workspace,
        "run_delivery_loop.py",
        "--format",
        "controller-json",
        "--no-recover",
    )

    assert result.returncode != 0
    assert "preflight made no mutation" in result.stderr
    assert journal.read_text(encoding="utf-8") == "{}"


def test_delivery_loop_selects_local_hardening_while_agent_is_blocked(
    tmp_path: Path,
) -> None:
    workspace = _copied_workspace(tmp_path)
    queue_path = workspace / "delivery/WORK_QUEUE.yaml"
    queue = yaml.safe_load(queue_path.read_text(encoding="utf-8"))
    for item in queue["items"]:
        if item["status"] == "IN_PROGRESS":
            item["status"] = "PENDING"
        if item["id"] == "AGENT-001":
            item["status"] = "BLOCKED"
        if item["id"] == "HARDEN-CI-001":
            item["status"] = "PENDING"
            item.pop("blocking_condition", None)
    queue_path.write_text(yaml.safe_dump(queue, sort_keys=False), encoding="utf-8")

    result = _run_workspace(workspace, "run_delivery_loop.py")

    assert result.returncode == 0, result.stderr
    assert "# Delivery loop brief: HARDEN-CI-001" in result.stdout
    assert "Source: `context/tasks/TASK-harden-ci-001.md`" in result.stdout
    assert "Make PostgreSQL integration coverage" in result.stdout


def test_delivery_loop_continues_independent_hardening_after_ci_block(
    tmp_path: Path,
) -> None:
    workspace = _copied_workspace(tmp_path)
    _reset_to_observability_pending(workspace)
    queue_path = workspace / "delivery/WORK_QUEUE.yaml"
    queue = yaml.safe_load(queue_path.read_text(encoding="utf-8"))
    ci_item = next(candidate for candidate in queue["items"] if candidate["id"] == "HARDEN-CI-001")
    ci_item["status"] = "BLOCKED"
    ci_item["blocking_condition"] = "Synthetic CI runner is unavailable."
    queue_path.write_text(yaml.safe_dump(queue, sort_keys=False), encoding="utf-8")

    result = _run_workspace(workspace, "run_delivery_loop.py")

    assert result.returncode == 0, result.stderr
    assert "# Delivery loop brief: OBSERVABILITY-001" in result.stdout


def test_hardening_dependency_graph_preserves_release_boundaries() -> None:
    queue = yaml.safe_load((ROOT / "delivery/WORK_QUEUE.yaml").read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in queue["items"]}

    assert by_id["SUPPLYCHAIN-001"]["depends_on"] == ["HARDEN-CI-001", "CONTAINER-001"]
    assert set(by_id["SECURITY-001"]["depends_on"]) == {
        "AGENT-001",
        "OBSERVABILITY-001",
        "POLICY-001",
        "SUPPLYCHAIN-001",
        "HTTP-SECURITY-001",
        "TELEMETRY-001",
        "STAGING-001",
        "BACKUP-RESTORE-001",
    }
    assert by_id["WORKER-HOST-001"]["depends_on"] == [
        "RELEASE-BASELINE-001",
        "OPERATIONS-001",
        "OBSERVABILITY-001",
        "POLICY-001",
    ]
    assert by_id["AGENT-001"]["status"] == "BLOCKED"


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
    result = run_script(
        "record_delivery_evidence.py",
        "--work-item",
        "DOMAIN-001",
        "--complete",
        "--expected-generation",
        _controller_generation(ROOT),
    )

    assert result.returncode != 0
    assert "current IN_PROGRESS" in result.stderr


def test_evidence_recorder_advances_only_with_declared_passing_checks(tmp_path: Path) -> None:
    workspace, item = _active_workspace(tmp_path)
    evidence = workspace / f"evidence/delivery-loop/{item['id']}.yaml"
    evidence.parent.mkdir(parents=True, exist_ok=True)
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
            f"evidence/delivery-loop/{item['id']}.yaml",
            "--expected-generation",
            _controller_generation(workspace),
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


def test_evidence_recorder_rejects_gitignored_noncanonical_evidence(
    tmp_path: Path,
) -> None:
    workspace, item = _active_workspace(tmp_path)
    ignored_evidence = workspace / ".openclaw/ignored-evidence.yaml"
    ignored_evidence.parent.mkdir(parents=True)
    ignored_evidence.write_text("work_item: ignored\n", encoding="utf-8")

    result = _run_workspace(
        workspace,
        "record_delivery_evidence.py",
        "--work-item",
        str(item["id"]),
        "--complete",
        "--evidence",
        ".openclaw/ignored-evidence.yaml",
        "--expected-generation",
        _controller_generation(workspace),
    )

    assert result.returncode != 0
    assert f"evidence/delivery-loop/{item['id']}.yaml" in result.stderr


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
            "--expected-generation",
            _controller_generation(workspace),
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
            "--expected-generation",
            _controller_generation(workspace),
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
