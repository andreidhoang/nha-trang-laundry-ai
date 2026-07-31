from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import run_automation_tick as automation_tick
from scripts.run_automation_tick import decide_tick

OWNER = "auto-dev:run-001"
LEASE_ID = "11111111-1111-4111-8111-111111111111"
BASE_COMMIT = "a" * 40


def _state(work_items: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "lease": {
            "owner": OWNER,
            "lease_id": LEASE_ID,
            "status": "ACTIVE",
        },
        "work_items": work_items or {},
    }


def _selected(
    status: str = "PENDING",
    work_item: str = "OBSERVABILITY-001",
) -> dict[str, Any]:
    return {"id": work_item, "status": status}


def _git(
    branch: str = "main",
    *,
    head: str = BASE_COMMIT,
    head_parent: str = "d" * 40,
    head_paths: tuple[str, ...] = (),
    dirty_entries: tuple[str, ...] = (),
    task_base_is_ancestor: bool | None = True,
    task_diff_paths: tuple[str, ...] = (
        "delivery/LOOP_STATE.yaml",
        "delivery/WORK_QUEUE.yaml",
    ),
    retry_head_descends_from_base: bool | None = True,
) -> dict[str, Any]:
    return {
        "branch": branch,
        "head": head,
        "main_commit": BASE_COMMIT,
        "head_parents": (head_parent,),
        "head_paths": head_paths,
        "dirty_entries": dirty_entries,
        "task_base_is_ancestor": task_base_is_ancestor,
        "task_diff_paths": task_diff_paths,
        "retry_head_descends_from_base": retry_head_descends_from_base,
    }


def _attempt(
    *,
    phase: str,
    attempts: int = 1,
    result: str | None = None,
) -> dict[str, Any]:
    return {
        "attempts": attempts,
        "attempt_id": "observability-001:aaaaaaaaaaaa:attempt-1",
        "phase": phase,
        "branch": "feature/auto-dev-observability-001",
        "base_commit": BASE_COMMIT,
        "child_run_id": "codex-observability-001-aaaaaaaaaaaa-1",
        "child_session": (
            "codex-thread:11111111-1111-4111-8111-111111111111"
            if phase not in {"PREPARED", "RECOVERY_REQUIRED"}
            else None
        ),
        "task_commit": (
            "b" * 40 if phase in {"TASK_COMMITTED", "MERGED", "DELIVERY_COMMITTED"} else None
        ),
        "delivery_commit": (
            "c" * 40 if phase in {"DELIVERY_COMMITTED", "BLOCK_COMMITTED"} else None
        ),
        "last_result": result,
    }


def _decide(
    state: dict[str, Any],
    selected: dict[str, Any] | None,
    git_state: dict[str, Any],
    *,
    statuses: dict[str, str] | None = None,
) -> dict[str, Any]:
    delivery_statuses = statuses
    if delivery_statuses is None:
        delivery_statuses = (
            {str(selected["id"]): str(selected["status"])} if selected is not None else {}
        )
    return decide_tick(
        state,
        selected,
        delivery_statuses,
        git_state,
        owner=OWNER,
        lease_id=LEASE_ID,
    )


def test_clean_main_prepares_one_stable_new_attempt() -> None:
    decision = _decide(_state(), _selected(), _git())

    assert decision == {
        "action": "READY_NEW",
        "attempt": 1,
        "attempt_id": "observability-001:aaaaaaaaaaaa:attempt-1",
        "base_commit": BASE_COMMIT,
        "branch": "feature/auto-dev-observability-001",
        "child_run_id": "codex_observability_001_aaaaaaaaaaaa_1",
        "dirty_entries": 0,
        "work_item": "OBSERVABILITY-001",
    }


def test_preflight_snapshot_requests_nonrecovering_delivery_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []
    payload = {
        "context_validation": {
            "source_references": 1,
            "decisions": 1,
            "gates": 1,
            "phases": 1,
            "work_items": 1,
            "capabilities": 1,
        },
        "generation": "1" * 64,
        "selected": _selected(),
        "statuses": {"OBSERVABILITY-001": "PENDING"},
    }

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        observed.extend(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(automation_tick, "_run", fake_run)

    selected, statuses, generation = automation_tick._delivery_snapshot(preflight=True)

    assert "--no-recover" in observed
    assert selected == _selected()
    assert statuses == {"OBSERVABILITY-001": "PENDING"}
    assert generation == "1" * 64


def test_new_work_fails_closed_for_dirty_main_or_wrong_lease() -> None:
    dirty = _decide(_state(), _selected(), _git(dirty_entries=("?? local.txt",)))
    wrong_lease = decide_tick(
        _state(),
        _selected(),
        {"OBSERVABILITY-001": "PENDING"},
        _git(),
        owner=OWNER,
        lease_id="22222222-2222-4222-8222-222222222222",
    )

    assert dirty["reason"] == "MAIN_WORKTREE_NOT_CLEAN"
    assert wrong_lease["reason"] == "LEASE_FENCE_MISMATCH"


def test_prepared_attempt_requires_child_reconciliation_before_spawn() -> None:
    state = _state({"OBSERVABILITY-001": _attempt(phase="PREPARED")})
    decision = _decide(
        state,
        _selected("IN_PROGRESS"),
        _git("feature/auto-dev-observability-001"),
    )

    assert decision["action"] == "RECONCILE_CHILD"
    assert decision["child_run_id"] == "codex-observability-001-aaaaaaaaaaaa-1"
    assert decision["child_session"] is None
    assert decision["spawn_allowed"] is True


def test_prepared_attempt_recovers_branch_creation_and_delivery_claim_windows() -> None:
    state = _state({"OBSERVABILITY-001": _attempt(phase="PREPARED")})
    create_branch = _decide(state, _selected("PENDING"), _git())
    start_delivery = _decide(
        state,
        _selected("PENDING"),
        _git("feature/auto-dev-observability-001"),
    )

    assert create_branch["action"] == "CREATE_ATTEMPT_BRANCH"
    assert start_delivery["action"] == "START_DELIVERY_ITEM"


def test_prepared_child_reconciliation_distinguishes_claim_state_from_child_edits() -> None:
    state = _state({"OBSERVABILITY-001": _attempt(phase="PREPARED")})
    claim_only = _decide(
        state,
        _selected("IN_PROGRESS"),
        _git(
            "feature/auto-dev-observability-001",
            dirty_entries=(
                " M delivery/LOOP_STATE.yaml",
                " M delivery/WORK_QUEUE.yaml",
            ),
        ),
    )
    child_has_edited = _decide(
        state,
        _selected("IN_PROGRESS"),
        _git(
            "feature/auto-dev-observability-001",
            dirty_entries=(
                " M delivery/LOOP_STATE.yaml",
                " M delivery/WORK_QUEUE.yaml",
                " M packages/observability/src/logging.py",
            ),
        ),
    )

    assert claim_only["action"] == "RECONCILE_CHILD"
    assert claim_only["spawn_allowed"] is True
    assert child_has_edited["action"] == "RECONCILE_CHILD"
    assert child_has_edited["spawn_allowed"] is False


def test_recovery_required_resumes_blocking_and_state_queue_mismatch_is_blocking() -> None:
    recovery = _decide(
        _state({"OBSERVABILITY-001": _attempt(phase="RECOVERY_REQUIRED")}),
        _selected("IN_PROGRESS"),
        _git("feature/auto-dev-observability-001"),
    )
    mismatch = _decide(
        _state({"OBSERVABILITY-001": _attempt(phase="CHILD_RUNNING")}),
        _selected("IN_PROGRESS", "POLICY-001"),
        _git("feature/auto-dev-observability-001"),
    )

    assert recovery["action"] == "SWITCH_TO_BASE_FOR_BLOCK"
    assert mismatch["reason"] == "DELIVERY_AND_ATTEMPT_MISMATCH"


def test_preclaim_recovery_can_block_the_unstarted_selected_item() -> None:
    recovery = _decide(
        _state({"OBSERVABILITY-001": _attempt(phase="RECOVERY_REQUIRED")}),
        _selected("PENDING"),
        _git(),
    )

    assert recovery["action"] == "RECORD_UNSTARTED_DELIVERY_BLOCK"


def test_known_nonterminal_phases_return_only_their_resume_action() -> None:
    expected = {
        "CHILD_RUNNING": "WAIT_FOR_CHILD",
        "VERIFYING": "RESUME_VERIFICATION",
    }
    for phase, action in expected.items():
        decision = _decide(
            _state({"OBSERVABILITY-001": _attempt(phase=phase)}),
            _selected("IN_PROGRESS"),
            _git("feature/auto-dev-observability-001"),
        )
        assert decision["action"] == action


def test_task_commit_recovers_before_or_immediately_after_fast_forward() -> None:
    state = _state({"OBSERVABILITY-001": _attempt(phase="TASK_COMMITTED")})
    task_commit = "b" * 40
    before_merge = _decide(
        state,
        _selected("IN_PROGRESS"),
        _git("feature/auto-dev-observability-001", head=task_commit),
    )
    after_switch = _decide(
        state,
        _selected("IN_PROGRESS"),
        _git("main"),
    )
    after_merge = _decide(
        state,
        _selected("IN_PROGRESS"),
        {
            "branch": "main",
            "head": task_commit,
            "main_commit": task_commit,
            "head_parents": (BASE_COMMIT,),
            "head_paths": ("bounded.py",),
            "dirty_entries": (),
            "task_base_is_ancestor": True,
            "task_diff_paths": (
                "bounded.py",
                "delivery/LOOP_STATE.yaml",
                "delivery/WORK_QUEUE.yaml",
            ),
        },
    )
    diverged = _decide(
        state,
        _selected("IN_PROGRESS"),
        {
            "branch": "feature/auto-dev-observability-001",
            "head": task_commit,
            "main_commit": "c" * 40,
            "head_parents": (BASE_COMMIT,),
            "head_paths": ("bounded.py",),
            "dirty_entries": (),
            "task_base_is_ancestor": True,
            "task_diff_paths": (
                "bounded.py",
                "delivery/LOOP_STATE.yaml",
                "delivery/WORK_QUEUE.yaml",
            ),
        },
    )

    assert before_merge["action"] == "RESUME_MERGE"
    assert after_switch["action"] == "RESUME_FAST_FORWARD"
    assert after_merge["action"] == "RECORD_MERGED"
    assert diverged["reason"] == "MAIN_DIVERGED_FROM_ATTEMPT_BASE"


def test_task_commit_recovery_rejects_dirty_or_unrelated_history() -> None:
    state = _state({"OBSERVABILITY-001": _attempt(phase="TASK_COMMITTED")})
    task_commit = "b" * 40
    dirty = _decide(
        state,
        _selected("IN_PROGRESS"),
        _git(
            "feature/auto-dev-observability-001",
            head=task_commit,
            dirty_entries=(" M unrelated.py",),
        ),
    )
    unrelated = _decide(
        state,
        _selected("IN_PROGRESS"),
        _git(
            "feature/auto-dev-observability-001",
            head=task_commit,
            task_base_is_ancestor=False,
        ),
    )
    missing_claim = _decide(
        state,
        _selected("IN_PROGRESS"),
        _git(
            "feature/auto-dev-observability-001",
            head=task_commit,
            task_diff_paths=("bounded.py",),
        ),
    )

    assert dirty["reason"] == "TASK_COMMITTED_WORKTREE_NOT_CLEAN"
    assert unrelated["reason"] == "TASK_COMMIT_NOT_DESCENDED_FROM_BASE"
    assert missing_claim["reason"] == "TASK_COMMIT_MISSING_DELIVERY_CLAIM"


def test_real_git_task_commit_carries_delivery_claim_before_fast_forward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    delivery = workspace / "delivery"
    delivery.mkdir(parents=True)
    (delivery / "WORK_QUEUE.yaml").write_text("status: PENDING\n", encoding="utf-8")
    (delivery / "LOOP_STATE.yaml").write_text("current: null\n", encoding="utf-8")
    (delivery / "PROGRAM_PLAN.yaml").write_text("status: PENDING\n", encoding="utf-8")

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
    assert git("commit", "-m", "test: baseline").returncode == 0
    base_commit = git("rev-parse", "HEAD").stdout.strip()
    branch = "feature/auto-dev-observability-001"
    assert git("switch", "-c", branch).returncode == 0

    (delivery / "WORK_QUEUE.yaml").write_text("status: IN_PROGRESS\n", encoding="utf-8")
    (delivery / "LOOP_STATE.yaml").write_text(
        "current: OBSERVABILITY-001\n",
        encoding="utf-8",
    )
    (workspace / "bounded.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert git("add", ".").returncode == 0
    assert git("commit", "-m", "feat: bounded task and claim").returncode == 0
    task_commit = git("rev-parse", "HEAD").stdout.strip()

    item = _attempt(phase="TASK_COMMITTED")
    item.update(branch=branch, base_commit=base_commit, task_commit=task_commit)
    state = _state({"OBSERVABILITY-001": item})
    monkeypatch.setattr(automation_tick, "ROOT", workspace)

    git_state = automation_tick._git_state(state)
    decision = decide_tick(
        state,
        _selected("IN_PROGRESS"),
        {"OBSERVABILITY-001": "IN_PROGRESS"},
        git_state,
        owner=OWNER,
        lease_id=LEASE_ID,
    )

    assert {
        "delivery/LOOP_STATE.yaml",
        "delivery/WORK_QUEUE.yaml",
    } <= set(git_state["task_diff_paths"])
    assert decision["action"] == "RESUME_MERGE"


def test_merged_attempt_recovers_delivery_completion_and_state_commit() -> None:
    state = _state({"OBSERVABILITY-001": _attempt(phase="MERGED")})
    task_commit = "b" * 40
    merged_git = {
        "branch": "main",
        "head": task_commit,
        "main_commit": task_commit,
        "head_parents": (BASE_COMMIT,),
        "head_paths": ("bounded.py",),
        "dirty_entries": (),
        "task_base_is_ancestor": True,
    }
    before_completion = _decide(
        state,
        _selected("IN_PROGRESS"),
        merged_git,
    )
    uncommitted_completion = _decide(
        state,
        _selected("PENDING", "POLICY-001"),
        {
            **merged_git,
            "dirty_entries": (
                " M delivery/LOOP_STATE.yaml",
                " M delivery/WORK_QUEUE.yaml",
            ),
        },
        statuses={
            "OBSERVABILITY-001": "COMPLETE",
            "POLICY-001": "PENDING",
        },
    )
    delivery_commit = "c" * 40
    committed_completion = _decide(
        state,
        _selected("PENDING", "POLICY-001"),
        {
            "branch": "main",
            "head": delivery_commit,
            "main_commit": delivery_commit,
            "head_parents": (task_commit,),
            "head_paths": (
                "delivery/LOOP_STATE.yaml",
                "delivery/WORK_QUEUE.yaml",
            ),
            "dirty_entries": (),
            "task_base_is_ancestor": True,
        },
        statuses={
            "OBSERVABILITY-001": "COMPLETE",
            "POLICY-001": "PENDING",
        },
    )

    assert before_completion["action"] == "RESUME_DELIVERY_COMPLETION"
    assert uncommitted_completion["action"] == "COMMIT_DELIVERY_STATE"
    assert committed_completion["action"] == "RECORD_DELIVERY_COMMIT"
    assert committed_completion["delivery_commit"] == delivery_commit


def test_dirty_delivery_state_cannot_be_committed_on_an_unrelated_head() -> None:
    state = _state({"OBSERVABILITY-001": _attempt(phase="MERGED")})
    unrelated_head = "e" * 40
    decision = _decide(
        state,
        _selected("PENDING", "POLICY-001"),
        {
            "branch": "main",
            "head": unrelated_head,
            "main_commit": unrelated_head,
            "head_parents": ("d" * 40,),
            "head_paths": ("apps/api/unrelated.py",),
            "dirty_entries": (
                " M delivery/LOOP_STATE.yaml",
                " M delivery/WORK_QUEUE.yaml",
            ),
            "task_base_is_ancestor": True,
        },
        statuses={
            "OBSERVABILITY-001": "COMPLETE",
            "POLICY-001": "PENDING",
        },
    )

    assert decision["reason"] == "DELIVERY_STATE_BASE_COMMIT_MISMATCH"


def test_delivery_committed_phase_is_required_before_success_result() -> None:
    state = _state({"OBSERVABILITY-001": _attempt(phase="DELIVERY_COMMITTED")})
    delivery_commit = "c" * 40
    decision = _decide(
        state,
        _selected("PENDING", "POLICY-001"),
        {
            "branch": "main",
            "head": delivery_commit,
            "main_commit": delivery_commit,
            "head_parents": ("b" * 40,),
            "head_paths": (
                "delivery/LOOP_STATE.yaml",
                "delivery/WORK_QUEUE.yaml",
            ),
            "dirty_entries": (),
            "task_base_is_ancestor": True,
        },
        statuses={
            "OBSERVABILITY-001": "COMPLETE",
            "POLICY-001": "PENDING",
        },
    )

    assert decision["action"] == "FINALIZE_SUCCESSFUL_ATTEMPT"


def test_delivery_committed_phase_reproves_parent_and_changed_paths() -> None:
    state = _state({"OBSERVABILITY-001": _attempt(phase="DELIVERY_COMMITTED")})
    delivery_commit = "c" * 40
    statuses = {
        "OBSERVABILITY-001": "COMPLETE",
        "POLICY-001": "PENDING",
    }
    wrong_parent = _decide(
        state,
        _selected("PENDING", "POLICY-001"),
        {
            "branch": "main",
            "head": delivery_commit,
            "main_commit": delivery_commit,
            "head_parents": ("d" * 40,),
            "head_paths": (
                "delivery/LOOP_STATE.yaml",
                "delivery/WORK_QUEUE.yaml",
            ),
            "dirty_entries": (),
            "task_base_is_ancestor": True,
        },
        statuses=statuses,
    )
    merge_commit = _decide(
        state,
        _selected("PENDING", "POLICY-001"),
        {
            "branch": "main",
            "head": delivery_commit,
            "main_commit": delivery_commit,
            "head_parents": ("b" * 40, "d" * 40),
            "head_paths": (
                "delivery/LOOP_STATE.yaml",
                "delivery/WORK_QUEUE.yaml",
            ),
            "dirty_entries": (),
            "task_base_is_ancestor": True,
        },
        statuses=statuses,
    )
    wrong_paths = _decide(
        state,
        _selected("PENDING", "POLICY-001"),
        {
            "branch": "main",
            "head": delivery_commit,
            "main_commit": delivery_commit,
            "head_parents": ("b" * 40,),
            "head_paths": ("apps/api/unrelated.py",),
            "dirty_entries": (),
            "task_base_is_ancestor": True,
        },
        statuses=statuses,
    )

    assert wrong_parent["reason"] == "DELIVERY_COMMIT_STATE_MISMATCH"
    assert merge_commit["reason"] == "DELIVERY_COMMIT_STATE_MISMATCH"
    assert wrong_paths["reason"] == "DELIVERY_COMMIT_STATE_MISMATCH"


def test_blocked_recovery_requires_a_proven_control_commit_before_finalization() -> None:
    recovery_state = _state({"OBSERVABILITY-001": _attempt(phase="RECOVERY_REQUIRED")})
    record_block = _decide(
        recovery_state,
        _selected("IN_PROGRESS"),
        _git(),
    )
    commit_block = _decide(
        recovery_state,
        _selected("PENDING", "POLICY-001"),
        _git(
            dirty_entries=(
                " M delivery/LOOP_STATE.yaml",
                " M delivery/WORK_QUEUE.yaml",
            )
        ),
        statuses={
            "OBSERVABILITY-001": "BLOCKED",
            "POLICY-001": "PENDING",
        },
    )
    block_commit = "c" * 40
    record_commit = _decide(
        recovery_state,
        _selected("PENDING", "POLICY-001"),
        {
            "branch": "main",
            "head": block_commit,
            "main_commit": block_commit,
            "head_parents": (BASE_COMMIT,),
            "head_paths": (
                "delivery/LOOP_STATE.yaml",
                "delivery/WORK_QUEUE.yaml",
            ),
            "dirty_entries": (),
            "task_base_is_ancestor": None,
        },
        statuses={
            "OBSERVABILITY-001": "BLOCKED",
            "POLICY-001": "PENDING",
        },
    )
    block_state = _state({"OBSERVABILITY-001": _attempt(phase="BLOCK_COMMITTED")})
    finalize = _decide(
        block_state,
        _selected("PENDING", "POLICY-001"),
        {
            "branch": "main",
            "head": block_commit,
            "main_commit": block_commit,
            "head_parents": (BASE_COMMIT,),
            "head_paths": (
                "delivery/LOOP_STATE.yaml",
                "delivery/WORK_QUEUE.yaml",
            ),
            "dirty_entries": (),
            "task_base_is_ancestor": None,
        },
        statuses={
            "OBSERVABILITY-001": "BLOCKED",
            "POLICY-001": "PENDING",
        },
    )

    assert record_block["action"] == "RECORD_DELIVERY_BLOCK"
    assert commit_block["action"] == "COMMIT_BLOCK_STATE"
    assert record_commit["action"] == "RECORD_BLOCK_COMMIT"
    assert record_commit["delivery_commit"] == block_commit
    assert finalize["action"] == "FINALIZE_BLOCKED_ATTEMPT"


def test_legacy_inflight_recovery_can_only_be_blocked_not_respawned() -> None:
    legacy = _attempt(phase="RECOVERY_REQUIRED")
    legacy.update(
        branch=None,
        base_commit=None,
        child_run_id=None,
        child_session=None,
        task_commit=None,
        delivery_commit=None,
    )
    decision = _decide(
        _state({"OBSERVABILITY-001": legacy}),
        _selected("IN_PROGRESS"),
        _git(),
    )

    assert decision["action"] == "LEGACY_RECOVERY_REQUIRES_MANUAL_RECONCILIATION"


def test_unblocked_pending_item_retries_from_current_main_on_a_new_branch() -> None:
    prior = _attempt(phase="TERMINAL", result="BLOCKED")
    decision = _decide(
        _state({"OBSERVABILITY-001": prior}),
        _selected("PENDING"),
        _git(),
    )

    assert decision["action"] == "READY_RETRY"
    assert decision["attempt"] == 2
    assert decision["base_commit"] == BASE_COMMIT
    assert decision["branch"] == "feature/auto-dev-observability-001-attempt-2"


def test_unrelated_post_merge_commit_cannot_impersonate_delivery_commit() -> None:
    state = _state({"OBSERVABILITY-001": _attempt(phase="MERGED")})
    decision = _decide(
        state,
        _selected("PENDING", "POLICY-001"),
        {
            "branch": "main",
            "head": "c" * 40,
            "main_commit": "c" * 40,
            "head_parents": ("d" * 40,),
            "head_paths": ("apps/api/unrelated.py",),
            "dirty_entries": (),
            "task_base_is_ancestor": True,
        },
        statuses={
            "OBSERVABILITY-001": "COMPLETE",
            "POLICY-001": "PENDING",
        },
    )

    assert decision["reason"] == "DELIVERY_COMMIT_NOT_PROVEN"


def test_failed_terminal_attempt_can_retry_only_on_its_exact_branch() -> None:
    prior = _attempt(phase="TERMINAL", result="FAILED")
    preserve = _decide(
        _state({"OBSERVABILITY-001": prior}),
        _selected("IN_PROGRESS"),
        _git("feature/auto-dev-observability-001", dirty_entries=(" M bounded.py",)),
    )
    ready = _decide(
        _state({"OBSERVABILITY-001": prior}),
        _selected("IN_PROGRESS"),
        _git("feature/auto-dev-observability-001"),
    )
    wrong_branch = _decide(
        _state({"OBSERVABILITY-001": prior}),
        _selected("IN_PROGRESS"),
        _git(),
    )
    reset_branch = _decide(
        _state({"OBSERVABILITY-001": prior}),
        _selected("IN_PROGRESS"),
        _git(
            "feature/auto-dev-observability-001",
            retry_head_descends_from_base=False,
        ),
    )

    assert preserve["action"] == "PRESERVE_RETRY_WORK"
    assert preserve["dirty_entries"] == 1
    assert ready["action"] == "READY_RETRY"
    assert ready["attempt"] == 2
    assert ready["dirty_entries"] == 0
    assert wrong_branch["reason"] == "RETRY_BRANCH_MISMATCH"
    assert reset_branch["reason"] == "RETRY_BRANCH_NOT_DESCENDED_FROM_BASE"


def test_attempt_limit_and_orphan_in_progress_item_fail_closed() -> None:
    limited = _attempt(phase="TERMINAL", attempts=3, result="FAILED")
    at_limit = _decide(
        _state({"OBSERVABILITY-001": limited}),
        _selected("IN_PROGRESS"),
        _git("feature/auto-dev-observability-001"),
    )
    orphan = _decide(
        _state(),
        _selected("IN_PROGRESS"),
        _git("feature/auto-dev-observability-001"),
    )

    assert at_limit["reason"] == "ATTEMPT_LIMIT_REACHED"
    assert orphan["reason"] == "IN_PROGRESS_ITEM_LACKS_TERMINAL_ATTEMPT"


def test_multiple_nonterminal_attempts_fail_closed() -> None:
    policy = _attempt(phase="CHILD_RUNNING")
    policy["branch"] = "feature/auto-dev-policy-001"
    decision = _decide(
        _state(
            {
                "OBSERVABILITY-001": _attempt(phase="VERIFYING"),
                "POLICY-001": policy,
            }
        ),
        _selected("IN_PROGRESS"),
        _git("feature/auto-dev-observability-001"),
    )

    assert decision["reason"] == "MULTIPLE_NONTERMINAL_ATTEMPTS"


def test_no_selected_work_returns_no_action() -> None:
    decision = _decide(_state(), None, _git())

    assert decision == {
        "action": "NO_ACTION",
        "reason": "NO_READY_WORK",
        "work_item": None,
    }
