from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
commit_delivery_control = importlib.import_module("commit_delivery_control")
delivery_state = importlib.import_module("delivery_state")
TARGET_PATHS = delivery_state.TARGET_RELATIVE_PATHS
WORK_ITEM = "OBSERVABILITY-001"
ATTEMPT_ID = "observability-001:control-test:attempt-1"


def _copy_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
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
    for file_path in ROOT.glob("*.md"):
        (workspace / file_path.name).write_bytes(file_path.read_bytes())
    return workspace


def _mapping(workspace: Path, relative_path: str) -> dict[str, Any]:
    loaded = yaml.safe_load((workspace / relative_path).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _generation(
    workspace: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _mapping(workspace, TARGET_PATHS[0]),
        _mapping(workspace, TARGET_PATHS[1]),
        _mapping(workspace, TARGET_PATHS[2]),
    )


def _git(workspace: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _script(
    workspace: Path,
    name: str,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(workspace / "scripts" / name), *arguments],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _ready_workspace(tmp_path: Path) -> Path:
    workspace = _copy_workspace(tmp_path)
    queue, state, program = _generation(workspace)
    reset_items = {
        "OBSERVABILITY-001",
        "POLICY-001",
        "CONTAINER-001",
        "SUPPLYCHAIN-001",
    }
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
    (workspace / ".gitignore").write_text(
        ".openclaw/\n"
        "__pycache__/\n"
        "*.pyc\n"
        "delivery/.delivery-state.lock\n"
        "delivery/.delivery-state.transaction.json\n"
        "delivery/.*.tmp\n",
        encoding="utf-8",
    )
    (workspace / ".gitattributes").write_text(
        "delivery/*.yaml filter=control-race\n",
        encoding="utf-8",
    )
    (workspace / "rogue.txt").write_text("safe\n", encoding="utf-8")
    assert _git(workspace, "init", "-b", "main").returncode == 0
    assert _git(workspace, "config", "user.name", "Automation Test").returncode == 0
    assert (
        _git(
            workspace,
            "config",
            "user.email",
            "automation@example.invalid",
        ).returncode
        == 0
    )
    assert _git(workspace, "add", ".").returncode == 0
    baseline = _git(workspace, "commit", "-m", "test: baseline")
    assert baseline.returncode == 0, baseline.stderr
    return workspace


def _prepare_block(
    tmp_path: Path,
) -> tuple[Path, str, str, str]:
    workspace = _ready_workspace(tmp_path)
    base_commit = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    acquired = _script(
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
    begun = _script(
        workspace,
        "manage_automation_state.py",
        "begin-attempt",
        "--owner",
        "test-controller",
        "--lease-id",
        lease_id,
        "--work-item",
        WORK_ITEM,
        "--attempt-id",
        ATTEMPT_ID,
        "--branch",
        "feature/auto-dev-observability-001",
        "--base-commit",
        base_commit,
        "--child-run-id",
        "codex_observability_001_control_test_1",
    )
    assert begun.returncode == 0, begun.stderr
    recovery = _script(
        workspace,
        "manage_automation_state.py",
        "record-recovery-required",
        "--owner",
        "test-controller",
        "--lease-id",
        lease_id,
        "--work-item",
        WORK_ITEM,
        "--attempt-id",
        ATTEMPT_ID,
    )
    assert recovery.returncode == 0, recovery.stderr
    generation = delivery_state.delivery_generation_digest(*_generation(workspace))
    blocked = _script(
        workspace,
        "record_delivery_evidence.py",
        "--work-item",
        WORK_ITEM,
        "--block",
        "--reason",
        "Synthetic controller blocker.",
        "--expected-generation",
        generation,
    )
    assert blocked.returncode == 0, blocked.stderr
    return (
        workspace,
        base_commit,
        lease_id,
        delivery_state.delivery_generation_digest(*_generation(workspace)),
    )


def _commit_block(
    workspace: Path,
    *,
    base_commit: str,
    lease_id: str,
    generation: str,
) -> subprocess.CompletedProcess[str]:
    return _script(
        workspace,
        "commit_delivery_control.py",
        "--owner",
        "test-controller",
        "--lease-id",
        lease_id,
        "--work-item",
        WORK_ITEM,
        "--attempt-id",
        ATTEMPT_ID,
        "--kind",
        "block",
        "--expected-parent",
        base_commit,
        "--expected-generation",
        generation,
    )


@pytest.mark.parametrize("kind", ["complete", "block"])
def test_delivery_outcome_requires_the_named_item_terminal_record(kind: str) -> None:
    if kind == "complete":
        queue = {"items": [{"id": WORK_ITEM, "status": "COMPLETE"}]}
        state = {
            "current_work_item": None,
            "last_result": "COMPLETE",
            "blocker": None,
            "evidence_records": [
                {
                    "work_item": "HARDEN-CI-001",
                    "path": "evidence/delivery-loop/HARDEN-CI-001.yaml",
                    "recorded_at": "2026-07-31T00:00:00+00:00",
                }
            ],
        }
    else:
        queue = {
            "items": [
                {
                    "id": WORK_ITEM,
                    "status": "BLOCKED",
                    "blocking_condition": "Synthetic controller blocker.",
                }
            ]
        }
        state = {
            "current_work_item": None,
            "last_result": "BLOCKED",
            "blocker": "Synthetic controller blocker.",
            "blocked_records": [
                {
                    "work_item": "POLICY-001",
                    "reason": "Synthetic controller blocker.",
                    "recorded_at": "2026-07-31T00:00:00+00:00",
                }
            ],
        }

    with pytest.raises(
        commit_delivery_control.ControlCommitError,
        match="matching named-item",
    ):
        commit_delivery_control._require_delivery_outcome(
            queue=queue,
            state=state,
            work_item=WORK_ITEM,
            kind=kind,
        )


def test_completion_outcome_rejects_gitignored_evidence_path() -> None:
    queue = {"items": [{"id": WORK_ITEM, "status": "COMPLETE"}]}
    state = {
        "current_work_item": None,
        "last_result": "COMPLETE",
        "blocker": None,
        "evidence_records": [
            {
                "work_item": WORK_ITEM,
                "path": ".openclaw/ignored-evidence.yaml",
                "recorded_at": "2026-07-31T00:00:00+00:00",
            }
        ],
    }

    with pytest.raises(
        commit_delivery_control.ControlCommitError,
        match="matching named-item evidence",
    ):
        commit_delivery_control._require_delivery_outcome(
            queue=queue,
            state=state,
            work_item=WORK_ITEM,
            kind="complete",
        )


def test_completion_evidence_must_be_a_blob_in_task_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "evidence_records": [
            {
                "work_item": WORK_ITEM,
                "path": f"evidence/delivery-loop/{WORK_ITEM}.yaml",
                "recorded_at": "2026-07-31T00:00:00+00:00",
            }
        ]
    }
    monkeypatch.setattr(commit_delivery_control, "_git_text", lambda *_args: "tree")

    with pytest.raises(
        commit_delivery_control.ControlCommitError,
        match="not committed in the task commit",
    ):
        commit_delivery_control._require_committed_completion_evidence(
            state=state,
            work_item=WORK_ITEM,
            expected_parent="a" * 40,
        )


def test_semantic_generation_mismatch_never_moves_head(tmp_path: Path) -> None:
    workspace, base_commit, lease_id, _generation_digest = _prepare_block(tmp_path)
    state_path = workspace / TARGET_PATHS[1]
    state = _mapping(workspace, TARGET_PATHS[1])
    state["blocked_records"][-1]["work_item"] = "POLICY-001"
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    mismatched_generation = delivery_state.delivery_generation_digest(*_generation(workspace))

    committed = _commit_block(
        workspace,
        base_commit=base_commit,
        lease_id=lease_id,
        generation=mismatched_generation,
    )

    assert committed.returncode == 4
    assert "matching named-item" in committed.stderr
    assert _git(workspace, "rev-parse", "HEAD").stdout.strip() == base_commit


def test_pre_commit_hook_cannot_inject_an_unrelated_path(tmp_path: Path) -> None:
    workspace, base_commit, lease_id, generation = _prepare_block(tmp_path)
    hook = workspace / ".git" / "hooks" / "pre-commit"
    marker = workspace / ".git" / "pre-commit-ran"
    hook.write_text(
        "#!/bin/sh\n"
        "printf 'injected\\n' > rogue.txt\n"
        "git add -- rogue.txt\n"
        "printf 'ran\\n' > .git/pre-commit-ran\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)

    committed = _commit_block(
        workspace,
        base_commit=base_commit,
        lease_id=lease_id,
        generation=generation,
    )

    assert committed.returncode == 0, committed.stderr
    commit = yaml.safe_load(committed.stdout)["commit"]
    assert not marker.exists()
    assert (workspace / "rogue.txt").read_text(encoding="utf-8") == "safe\n"
    changed_paths = {
        path.replace("\\", "/")
        for path in _git(
            workspace,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
        ).stdout.splitlines()
    }
    assert changed_paths >= {
        "delivery/WORK_QUEUE.yaml",
        "delivery/LOOP_STATE.yaml",
    }
    assert changed_paths <= set(TARGET_PATHS)
    assert _git(workspace, "status", "--porcelain=v1").stdout == ""


def test_shared_index_race_fails_before_head_compare_and_swap(tmp_path: Path) -> None:
    workspace, base_commit, lease_id, generation = _prepare_block(tmp_path)
    filter_script = workspace / ".git" / "inject_control_race.py"
    filter_script.write_text(
        "from __future__ import annotations\n"
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        "payload = sys.stdin.buffer.read()\n"
        "if os.environ.get('GIT_INDEX_FILE'):\n"
        "    root = Path(__file__).resolve().parents[1]\n"
        "    (root / 'rogue.txt').write_text('injected\\n', encoding='utf-8')\n"
        "    environment = os.environ.copy()\n"
        "    environment.pop('GIT_INDEX_FILE', None)\n"
        "    subprocess.run(\n"
        "        ['git', 'add', '--', 'rogue.txt'], cwd=root, env=environment,\n"
        "        check=True, capture_output=True,\n"
        "    )\n"
        "sys.stdout.buffer.write(payload)\n",
        encoding="utf-8",
    )
    filter_command = f'"{Path(sys.executable).as_posix()}" "{filter_script.as_posix()}"'
    configured = _git(
        workspace,
        "config",
        "filter.control-race.clean",
        filter_command,
    )
    assert configured.returncode == 0, configured.stderr
    assert (
        _git(
            workspace,
            "config",
            "filter.control-race.required",
            "true",
        ).returncode
        == 0
    )

    committed = _commit_block(
        workspace,
        base_commit=base_commit,
        lease_id=lease_id,
        generation=generation,
    )

    assert committed.returncode == 4
    assert "Git state changed before the control ref update" in committed.stderr
    assert _git(workspace, "rev-parse", "HEAD").stdout.strip() == base_commit
    assert (
        "rogue.txt"
        in _git(
            workspace,
            "diff",
            "--cached",
            "--name-only",
        ).stdout.splitlines()
    )
