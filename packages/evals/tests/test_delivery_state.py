from __future__ import annotations

import copy
import errno
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import delivery_state

ROOT = Path(__file__).resolve().parents[3]
TARGET_PATHS = (
    "delivery/WORK_QUEUE.yaml",
    "delivery/LOOP_STATE.yaml",
    "delivery/PROGRAM_PLAN.yaml",
)


def _copied_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    for directory in ("context", "delivery", "docs", "evidence", "scripts", "specs", "templates"):
        source = ROOT / directory
        destination = workspace / directory
        destination.mkdir(parents=True, exist_ok=True)
        for file_path in source.rglob("*"):
            if file_path.is_file():
                target = destination / file_path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(file_path.read_bytes())
    for file_path in ROOT.glob("*.md"):
        (workspace / file_path.name).write_bytes(file_path.read_bytes())
    return workspace


def _load_mapping(workspace: Path, relative_path: str) -> dict[str, Any]:
    loaded = yaml.safe_load((workspace / relative_path).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _load_generation(
    workspace: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _load_mapping(workspace, TARGET_PATHS[0]),
        _load_mapping(workspace, TARGET_PATHS[1]),
        _load_mapping(workspace, TARGET_PATHS[2]),
    )


def _ready_workspace(tmp_path: Path) -> Path:
    workspace = _copied_workspace(tmp_path)
    queue, state, program = _load_generation(workspace)
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
    state.update(current_work_item=None, last_result="COMPLETE", blocker=None)
    state["evidence_records"] = [
        record for record in state["evidence_records"] if record["work_item"] not in reset_items
    ]
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
    (workspace / TARGET_PATHS[0]).write_text(
        yaml.safe_dump(queue, sort_keys=False),
        encoding="utf-8",
    )
    (workspace / TARGET_PATHS[1]).write_text(
        yaml.safe_dump(state, sort_keys=False),
        encoding="utf-8",
    )
    (workspace / TARGET_PATHS[2]).write_text(
        yaml.safe_dump(program, sort_keys=False),
        encoding="utf-8",
    )
    return workspace


def _active_generation(
    workspace: Path,
    work_item: str = "OBSERVABILITY-001",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    queue, state, program = copy.deepcopy(_load_generation(workspace))
    for item in queue["items"]:
        if item["status"] == "IN_PROGRESS":
            item["status"] = "PENDING"
        if item["id"] == work_item:
            item["status"] = "IN_PROGRESS"
    state.update(current_work_item=work_item, last_result="IN_PROGRESS", blocker=None)
    for phase in program["phases"]:
        if phase["id"] == "PRODUCTION_HARDENING":
            phase["status"] = "IN_PROGRESS"
    return queue, state, program


def _run_workspace(
    workspace: Path,
    script: str,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(workspace / "scripts" / script), *arguments],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _start_process(
    workspace: Path,
    *arguments: str,
) -> subprocess.Popen[str]:
    effective_arguments = arguments
    if "--expected-generation" not in arguments:
        generation = delivery_state.delivery_generation_digest(*_load_generation(workspace))
        effective_arguments = (
            *arguments,
            "--expected-generation",
            generation,
        )
    return subprocess.Popen(
        [
            sys.executable,
            str(workspace / "scripts" / "record_delivery_evidence.py"),
            *effective_arguments,
        ],
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_mutex_times_out_across_processes_then_succeeds(tmp_path: Path) -> None:
    workspace = _copied_workspace(tmp_path)
    child_code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from delivery_state import DeliveryStateLockTimeout, delivery_state_mutex
try:
    with delivery_state_mutex(root=Path(sys.argv[2]), timeout_seconds=float(sys.argv[3])):
        pass
except DeliveryStateLockTimeout:
    raise SystemExit(23)
"""
    command = [
        sys.executable,
        "-c",
        child_code,
        str(workspace / "scripts"),
        str(workspace),
        "0.2",
    ]

    with delivery_state.delivery_state_mutex(root=workspace):
        blocked = subprocess.run(command, check=False, timeout=10)

    acquired = subprocess.run(command, check=False, timeout=10)
    assert blocked.returncode == 23
    assert acquired.returncode == 0


def test_two_concurrent_starts_claim_exactly_one_item(tmp_path: Path) -> None:
    workspace = _ready_workspace(tmp_path)
    arguments = ("--work-item", "OBSERVABILITY-001", "--start")

    with delivery_state.delivery_state_mutex(root=workspace):
        first = _start_process(workspace, *arguments)
        second = _start_process(workspace, *arguments)
        time.sleep(0.2)

    first_output = first.communicate(timeout=30)
    second_output = second.communicate(timeout=30)
    assert sorted((first.returncode, second.returncode)) == [0, 1], (
        first_output,
        second_output,
    )

    queue, state, _program = _load_generation(workspace)
    active = [item["id"] for item in queue["items"] if item["status"] == "IN_PROGRESS"]
    assert active == ["OBSERVABILITY-001"]
    assert state["current_work_item"] == "OBSERVABILITY-001"
    assert not (workspace / "delivery" / delivery_state.JOURNAL_NAME).exists()


def test_recorder_rejects_a_stale_generation_compare_and_swap(tmp_path: Path) -> None:
    workspace = _ready_workspace(tmp_path)
    stale_generation = delivery_state.delivery_generation_digest(*_load_generation(workspace))
    started = _start_process(
        workspace,
        "--work-item",
        "OBSERVABILITY-001",
        "--start",
    )
    started_output = started.communicate(timeout=30)
    assert started.returncode == 0, started_output

    stale = _start_process(
        workspace,
        "--work-item",
        "OBSERVABILITY-001",
        "--block",
        "--reason",
        "Synthetic blocker.",
        "--expected-generation",
        stale_generation,
    )
    _stale_stdout, stale_stderr = stale.communicate(timeout=30)

    assert stale.returncode == 1
    assert "Delivery generation changed" in stale_stderr
    queue, state, _program = _load_generation(workspace)
    item = next(candidate for candidate in queue["items"] if candidate["id"] == "OBSERVABILITY-001")
    assert item["status"] == "IN_PROGRESS"
    assert state["current_work_item"] == "OBSERVABILITY-001"


def test_recorder_can_block_the_next_unstarted_item_with_cas(tmp_path: Path) -> None:
    workspace = _ready_workspace(tmp_path)
    blocked = _start_process(
        workspace,
        "--work-item",
        "OBSERVABILITY-001",
        "--block",
        "--reason",
        "Synthetic pre-claim blocker.",
    )
    blocked_output = blocked.communicate(timeout=30)

    assert blocked.returncode == 0, blocked_output
    queue, state, _program = _load_generation(workspace)
    item = next(candidate for candidate in queue["items"] if candidate["id"] == "OBSERVABILITY-001")
    assert item["status"] == "BLOCKED"
    assert state["current_work_item"] is None


def test_recorder_rejects_sensitive_reason_without_echoing_it(tmp_path: Path) -> None:
    workspace = _ready_workspace(tmp_path)
    started = _start_process(
        workspace,
        "--work-item",
        "OBSERVABILITY-001",
        "--start",
    )
    assert started.communicate(timeout=30)
    assert started.returncode == 0
    secret = "Bearer synthetic-sensitive-value"
    blocked = _start_process(
        workspace,
        "--work-item",
        "OBSERVABILITY-001",
        "--block",
        "--reason",
        secret,
    )
    _blocked_stdout, blocked_stderr = blocked.communicate(timeout=30)

    assert blocked.returncode == 1
    assert "sensitive credential material" in blocked_stderr
    assert secret not in blocked_stderr


def test_recorder_rejects_malformed_cross_file_generation(tmp_path: Path) -> None:
    workspace = _ready_workspace(tmp_path)
    state_path = workspace / TARGET_PATHS[1]
    state = _load_mapping(workspace, TARGET_PATHS[1])
    state["current_work_item"] = "OBSERVABILITY-001"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")

    started = _start_process(
        workspace,
        "--work-item",
        "OBSERVABILITY-001",
        "--start",
    )
    _started_stdout, started_stderr = started.communicate(timeout=30)

    assert started.returncode == 1
    assert "current_work_item does not match" in started_stderr


def test_complete_and_block_race_produces_one_terminal_generation(tmp_path: Path) -> None:
    workspace = _ready_workspace(tmp_path)
    queue, state, program = _active_generation(workspace)
    with delivery_state.delivery_state_mutex(root=workspace):
        delivery_state.commit_delivery_state(queue, state, program, root=workspace)
    item = next(candidate for candidate in queue["items"] if candidate["id"] == "OBSERVABILITY-001")
    evidence_path = workspace / f"evidence/delivery-loop/{item['id']}.yaml"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        yaml.safe_dump(
            {
                "work_item": item["id"],
                "recorded_at": "2026-07-31T00:00:00Z",
                "requirement_contract": item["normative_sources"][0],
                "rollback_impact": "Revert the local slice.",
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

    with delivery_state.delivery_state_mutex(root=workspace):
        complete = _start_process(
            workspace,
            "--work-item",
            item["id"],
            "--complete",
            "--evidence",
            f"evidence/delivery-loop/{item['id']}.yaml",
        )
        block = _start_process(
            workspace,
            "--work-item",
            item["id"],
            "--block",
            "--reason",
            "Synthetic concurrent blocker.",
        )
        time.sleep(0.2)

    complete_output = complete.communicate(timeout=30)
    block_output = block.communicate(timeout=30)
    assert sorted((complete.returncode, block.returncode)) == [0, 1], (
        complete_output,
        block_output,
    )
    final_queue, final_state, _final_program = _load_generation(workspace)
    final_item = next(
        candidate for candidate in final_queue["items"] if candidate["id"] == item["id"]
    )
    assert final_item["status"] in {"COMPLETE", "BLOCKED"}
    assert final_state["current_work_item"] is None
    assert final_state["last_result"] == final_item["status"]
    assert not (workspace / "delivery" / delivery_state.JOURNAL_NAME).exists()


def test_recovery_rolls_all_targets_forward_after_first_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _ready_workspace(tmp_path)
    new_generation = _active_generation(workspace)
    original_replace = delivery_state._replace_target
    replacements = 0

    def stop_after_first_replace(path: Path, content: bytes) -> None:
        nonlocal replacements
        original_replace(path, content)
        replacements += 1
        if replacements == 1:
            raise RuntimeError("simulated process stop")

    monkeypatch.setattr(delivery_state, "_replace_target", stop_after_first_replace)
    with (
        delivery_state.delivery_state_mutex(root=workspace),
        pytest.raises(RuntimeError, match="simulated process stop"),
    ):
        delivery_state.commit_delivery_state(*new_generation, root=workspace)

    journal_path = workspace / "delivery" / delivery_state.JOURNAL_NAME
    assert journal_path.is_file()
    monkeypatch.setattr(delivery_state, "_replace_target", original_replace)
    with delivery_state.delivery_state_mutex(root=workspace):
        assert delivery_state.recover_delivery_state(root=workspace)

    assert _load_generation(workspace) == new_generation
    assert not journal_path.exists()
    drift = _run_workspace(workspace, "check_context_drift.py")
    assert drift.returncode == 0, drift.stderr


def test_crash_before_journal_publish_preserves_old_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _ready_workspace(tmp_path)
    old_bytes = tuple((workspace / path).read_bytes() for path in TARGET_PATHS)
    new_generation = _active_generation(workspace)

    def stop_before_publish(_journal_path: Path, _content: bytes) -> None:
        raise RuntimeError("simulated pre-commit stop")

    monkeypatch.setattr(delivery_state, "_publish_journal", stop_before_publish)
    with (
        delivery_state.delivery_state_mutex(root=workspace),
        pytest.raises(RuntimeError, match="pre-commit"),
    ):
        delivery_state.commit_delivery_state(*new_generation, root=workspace)

    assert tuple((workspace / path).read_bytes() for path in TARGET_PATHS) == old_bytes
    assert not (workspace / "delivery" / delivery_state.JOURNAL_NAME).exists()


def test_selector_recovers_committed_transaction_before_reading_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _ready_workspace(tmp_path)
    new_generation = _active_generation(workspace)
    original_replace = delivery_state._replace_target
    replacements = 0

    def stop_after_first_replace(path: Path, content: bytes) -> None:
        nonlocal replacements
        original_replace(path, content)
        replacements += 1
        if replacements == 1:
            raise RuntimeError("simulated selector recovery")

    monkeypatch.setattr(delivery_state, "_replace_target", stop_after_first_replace)
    with (
        delivery_state.delivery_state_mutex(root=workspace),
        pytest.raises(RuntimeError, match="selector recovery"),
    ):
        delivery_state.commit_delivery_state(*new_generation, root=workspace)
    monkeypatch.setattr(delivery_state, "_replace_target", original_replace)

    selected = _run_workspace(workspace, "run_delivery_loop.py")
    assert selected.returncode == 0, selected.stderr
    assert "# Delivery loop brief: OBSERVABILITY-001" in selected.stdout
    assert _load_generation(workspace) == new_generation
    assert not (workspace / "delivery" / delivery_state.JOURNAL_NAME).exists()


def test_context_checker_recovers_committed_transaction_before_drift_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _ready_workspace(tmp_path)
    new_generation = _active_generation(workspace)
    original_replace = delivery_state._replace_target
    replacements = 0

    def stop_after_first_replace(path: Path, content: bytes) -> None:
        nonlocal replacements
        original_replace(path, content)
        replacements += 1
        if replacements == 1:
            raise RuntimeError("simulated drift recovery")

    monkeypatch.setattr(delivery_state, "_replace_target", stop_after_first_replace)
    with (
        delivery_state.delivery_state_mutex(root=workspace),
        pytest.raises(RuntimeError, match="drift recovery"),
    ):
        delivery_state.commit_delivery_state(*new_generation, root=workspace)
    monkeypatch.setattr(delivery_state, "_replace_target", original_replace)

    drift = _run_workspace(workspace, "check_context_drift.py")
    assert drift.returncode == 0, drift.stderr
    assert _load_generation(workspace) == new_generation
    assert not (workspace / "delivery" / delivery_state.JOURNAL_NAME).exists()


def test_abrupt_subprocess_exit_releases_lock_and_rolls_forward(
    tmp_path: Path,
) -> None:
    workspace = _ready_workspace(tmp_path)
    new_generation = _active_generation(workspace)
    journal_payload = delivery_state._build_journal(*new_generation)
    payload_path = workspace / "transaction-input.yaml"
    payload_path.write_text(
        yaml.safe_dump(journal_payload, sort_keys=False),
        encoding="utf-8",
    )
    child_code = """
import os
import sys
from pathlib import Path
import yaml
sys.path.insert(0, sys.argv[1])
import delivery_state
root = Path(sys.argv[2])
payload = yaml.safe_load(Path(sys.argv[3]).read_text(encoding="utf-8"))
entries = delivery_state._validated_entries(payload)
mappings = [yaml.safe_load(content.decode("utf-8")) for _path, content, _hash in entries]
original = delivery_state._replace_target
replacements = 0
def stop_process(path, content):
    global replacements
    original(path, content)
    replacements += 1
    if replacements == 1:
        os._exit(77)
delivery_state._replace_target = stop_process
with delivery_state.delivery_state_mutex(root=root):
    delivery_state.commit_delivery_state(*mappings, root=root)
"""
    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            child_code,
            str(workspace / "scripts"),
            str(workspace),
            str(payload_path),
        ],
        cwd=workspace,
        check=False,
        timeout=30,
    )

    assert crashed.returncode == 77
    assert (workspace / "delivery" / delivery_state.JOURNAL_NAME).is_file()
    with delivery_state.delivery_state_mutex(root=workspace, timeout_seconds=1):
        assert delivery_state.recover_delivery_state(root=workspace)
    assert _load_generation(workspace) == new_generation


def test_recovery_rejects_any_target_outside_the_fixed_delivery_set(tmp_path: Path) -> None:
    workspace = _ready_workspace(tmp_path)
    old_generation = _load_generation(workspace)
    journal = delivery_state._build_journal(*_active_generation(workspace))
    targets = journal["targets"]
    assert isinstance(targets, list)
    targets[0]["path"] = "../escaped.yaml"
    journal_path = workspace / "delivery" / delivery_state.JOURNAL_NAME
    journal_path.write_bytes(delivery_state._serialize_journal(journal))

    with (
        delivery_state.delivery_state_mutex(root=workspace),
        pytest.raises(delivery_state.DeliveryTransactionError, match="unexpected target"),
    ):
        delivery_state.recover_delivery_state(root=workspace)

    assert _load_generation(workspace) == old_generation
    assert journal_path.exists()
    assert not (workspace.parent / "escaped.yaml").exists()


@pytest.mark.parametrize("tamper", ["hash", "canonical-yaml"])
def test_recovery_rejects_tampered_hash_or_noncanonical_yaml(
    tmp_path: Path,
    tamper: str,
) -> None:
    workspace = _ready_workspace(tmp_path)
    old_generation = _load_generation(workspace)
    journal = delivery_state._build_journal(*_active_generation(workspace))
    targets = journal["targets"]
    assert isinstance(targets, list)
    target = targets[0]
    assert isinstance(target, dict)
    if tamper == "hash":
        target["sha256"] = "0" * 64
        expected_error = "SHA-256 does not match"
    else:
        target["yaml"] = f"{target['yaml']}# noncanonical\n"
        target["sha256"] = delivery_state._sha256(str(target["yaml"]).encode("utf-8"))
        expected_error = "YAML is not canonical"
    journal_path = workspace / "delivery" / delivery_state.JOURNAL_NAME
    journal_path.write_bytes(delivery_state._serialize_journal(journal))

    with (
        delivery_state.delivery_state_mutex(root=workspace),
        pytest.raises(delivery_state.DeliveryTransactionError, match=expected_error),
    ):
        delivery_state.recover_delivery_state(root=workspace)

    assert _load_generation(workspace) == old_generation
    assert journal_path.exists()


def test_recovery_rejects_cross_file_invalid_journal_before_any_target_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _ready_workspace(tmp_path)
    old_bytes = tuple((workspace / path).read_bytes() for path in TARGET_PATHS)
    journal = delivery_state._build_journal(*_active_generation(workspace))
    targets = journal["targets"]
    assert isinstance(targets, list)
    state_target = targets[1]
    assert isinstance(state_target, dict)
    state = yaml.safe_load(state_target["yaml"])
    assert isinstance(state, dict)
    state["current_work_item"] = None
    state_yaml = delivery_state._serialize_mapping(state)
    state_target["yaml"] = state_yaml
    state_target["sha256"] = delivery_state._sha256(state_yaml.encode("utf-8"))
    delivery_state._validated_entries(journal)

    journal_path = workspace / "delivery" / delivery_state.JOURNAL_NAME
    journal_path.write_bytes(delivery_state._serialize_journal(journal))
    target_writes: list[Path] = []
    original_replace = delivery_state._replace_target

    def record_target_write(path: Path, content: bytes) -> None:
        target_writes.append(path)
        original_replace(path, content)

    monkeypatch.setattr(delivery_state, "_replace_target", record_target_write)
    with (
        delivery_state.delivery_state_mutex(root=workspace),
        pytest.raises(
            delivery_state.DeliveryTransactionError,
            match="current_work_item does not match",
        ),
    ):
        delivery_state.recover_delivery_state(root=workspace)

    assert target_writes == []
    assert tuple((workspace / path).read_bytes() for path in TARGET_PATHS) == old_bytes
    assert journal_path.exists()


def test_block_control_wrapper_commits_only_one_cas_bound_generation(
    tmp_path: Path,
) -> None:
    workspace = _ready_workspace(tmp_path)
    (workspace / ".gitignore").write_text(
        ".openclaw/\n"
        "__pycache__/\n"
        "*.pyc\n"
        "delivery/.delivery-state.lock\n"
        "delivery/.delivery-state.transaction.json\n"
        "delivery/.*.tmp\n",
        encoding="utf-8",
    )

    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    assert git("init", "-b", "main").returncode == 0
    assert git("config", "user.name", "Automation Test").returncode == 0
    assert git("config", "user.email", "automation@example.invalid").returncode == 0
    assert git("add", ".").returncode == 0
    baseline = git("commit", "-m", "test: baseline")
    assert baseline.returncode == 0, baseline.stderr
    base_commit = git("rev-parse", "HEAD").stdout.strip()

    acquired = _run_workspace(
        workspace,
        "manage_automation_state.py",
        "acquire",
        "--owner",
        "test-controller",
        "--ttl-seconds",
        "600",
    )
    assert acquired.returncode == 0, acquired.stderr
    lease_id = yaml.safe_load(acquired.stdout)["lease_id"]
    attempt_id = "observability-001:test:attempt-1"
    begun = _run_workspace(
        workspace,
        "manage_automation_state.py",
        "begin-attempt",
        "--owner",
        "test-controller",
        "--lease-id",
        lease_id,
        "--work-item",
        "OBSERVABILITY-001",
        "--attempt-id",
        attempt_id,
        "--branch",
        "feature/auto-dev-observability-001",
        "--base-commit",
        base_commit,
        "--child-run-id",
        "codex_observability_001_test_1",
    )
    assert begun.returncode == 0, begun.stderr
    recovery = _run_workspace(
        workspace,
        "manage_automation_state.py",
        "record-recovery-required",
        "--owner",
        "test-controller",
        "--lease-id",
        lease_id,
        "--work-item",
        "OBSERVABILITY-001",
        "--attempt-id",
        attempt_id,
    )
    assert recovery.returncode == 0, recovery.stderr
    blocked = _start_process(
        workspace,
        "--work-item",
        "OBSERVABILITY-001",
        "--block",
        "--reason",
        "Synthetic pre-claim blocker.",
    )
    blocked_output = blocked.communicate(timeout=30)
    assert blocked.returncode == 0, blocked_output
    generation = delivery_state.delivery_generation_digest(*_load_generation(workspace))

    committed = _run_workspace(
        workspace,
        "commit_delivery_control.py",
        "--owner",
        "test-controller",
        "--lease-id",
        lease_id,
        "--work-item",
        "OBSERVABILITY-001",
        "--attempt-id",
        attempt_id,
        "--kind",
        "block",
        "--expected-parent",
        base_commit,
        "--expected-generation",
        generation,
    )

    assert committed.returncode == 0, committed.stderr
    payload = yaml.safe_load(committed.stdout)
    assert payload["generation"] == generation
    parents = git("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    assert parents[1:] == [base_commit]
    changed_paths = {
        path.replace("\\", "/")
        for path in git(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "HEAD",
        ).stdout.splitlines()
    }
    assert changed_paths >= {
        "delivery/LOOP_STATE.yaml",
        "delivery/WORK_QUEUE.yaml",
    }
    assert changed_paths <= set(delivery_state.TARGET_RELATIVE_PATHS)


def test_directory_fsync_propagates_real_io_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_open(_path: Path, _flags: int) -> int:
        raise OSError(errno.EIO, "synthetic I/O failure")

    monkeypatch.setattr(os, "open", fail_open)
    with pytest.raises(OSError) as raised:
        delivery_state._fsync_directory(tmp_path)
    assert raised.value.errno == errno.EIO


def test_atomic_replace_preserves_existing_file_mode(tmp_path: Path) -> None:
    target = tmp_path / "state.yaml"
    target.write_bytes(b"old\n")
    target.chmod(0o640)
    original_mode = stat.S_IMODE(target.stat().st_mode)

    delivery_state._atomic_replace(target, b"new\n")

    assert target.read_bytes() == b"new\n"
    assert stat.S_IMODE(target.stat().st_mode) == original_mode
