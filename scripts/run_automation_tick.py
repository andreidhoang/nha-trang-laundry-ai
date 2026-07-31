"""Inspect one OpenClaw automation tick and return a fail-closed next action.

This command is an executable guard for the cron controller. It is deliberately
read-only: claiming delivery work, mutating durable attempt phases, spawning a
Codex child, committing, and merging remain separate fenced operations.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
STATE_SCRIPT = ROOT / "scripts/manage_automation_state.py"
DELIVERY_SCRIPT = ROOT / "scripts/run_delivery_loop.py"
EXIT_STATE_ERROR = 4
EXIT_GUARD_BLOCKED = 5
TERMINAL_PHASE = "TERMINAL"
MAX_ATTEMPTS = 3
RESUME_ACTIONS = {
    "CHILD_RUNNING": "WAIT_FOR_CHILD",
    "VERIFYING": "RESUME_VERIFICATION",
}
DELIVERY_STATE_PATHS = frozenset(
    {
        "delivery/LOOP_STATE.yaml",
        "delivery/PROGRAM_PLAN.yaml",
        "delivery/WORK_QUEUE.yaml",
    }
)
REQUIRED_DELIVERY_STATE_PATHS = frozenset(
    {
        "delivery/LOOP_STATE.yaml",
        "delivery/WORK_QUEUE.yaml",
    }
)


class TickGuardError(RuntimeError):
    """Raised when local controller state cannot be inspected safely."""


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TickGuardError(f"{field_name} must be a mapping")
    return cast(dict[str, Any], value)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _json_command(command: list[str], field_name: str) -> dict[str, Any]:
    result = _run(command)
    if result.returncode != 0:
        raise TickGuardError(f"{field_name} command failed closed")
    try:
        parsed: object = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise TickGuardError(f"{field_name} command returned invalid JSON") from error
    return _mapping(parsed, field_name)


def _automation_state() -> dict[str, Any]:
    return _json_command([sys.executable, str(STATE_SCRIPT), "status"], "automation state")


def _delivery_snapshot(
    *,
    preflight: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, str], str]:
    command = [sys.executable, str(DELIVERY_SCRIPT), "--format", "controller-json"]
    if preflight:
        command.append("--no-recover")
    result = _run(command)
    if result.returncode != 0:
        raise TickGuardError("delivery selector failed closed")
    try:
        parsed: object = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise TickGuardError("delivery selector returned invalid JSON") from error
    snapshot = _mapping(parsed, "delivery snapshot")
    validation = _mapping(snapshot.get("context_validation"), "context validation")
    expected_counts = {
        "source_references",
        "decisions",
        "gates",
        "phases",
        "work_items",
        "capabilities",
    }
    if set(validation) != expected_counts or not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in validation.values()
    ):
        raise TickGuardError("context validation snapshot is malformed")
    generation = snapshot.get("generation")
    if (
        not isinstance(generation, str)
        or len(generation) != 64
        or any(character not in "0123456789abcdef" for character in generation)
    ):
        raise TickGuardError("delivery generation digest is malformed")
    selected_raw = snapshot.get("selected")
    selected = _mapping(selected_raw, "selected work item") if selected_raw is not None else None
    statuses_raw = _mapping(snapshot.get("statuses"), "delivery statuses")
    if not all(isinstance(value, str) for value in statuses_raw.values()):
        raise TickGuardError("delivery statuses must contain strings")
    return selected, cast(dict[str, str], statuses_raw), generation


def _git_text(*arguments: str) -> str:
    result = _run(["git", *arguments])
    if result.returncode != 0:
        raise TickGuardError("Git inspection failed closed")
    # Preserve the leading status column emitted by ``git status --porcelain``.
    return result.stdout.rstrip("\r\n")


def _git_is_ancestor(ancestor: str, descendant: str) -> bool:
    result = _run(["git", "merge-base", "--is-ancestor", ancestor, descendant])
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise TickGuardError("Git ancestry inspection failed closed")


def _git_state(automation_state: dict[str, Any]) -> dict[str, Any]:
    parent_line = _git_text("rev-list", "--parents", "-n", "1", "HEAD").split()
    if not parent_line:
        raise TickGuardError("Git parent inspection returned no commit")
    branch = _git_text("branch", "--show-current")
    head = _git_text("rev-parse", "HEAD")
    work_items = _mapping(automation_state.get("work_items"), "work_items")
    active_task_metadata: tuple[str, str] | None = None
    retry_metadata: tuple[str, str] | None = None
    for raw_item in work_items.values():
        item = _mapping(raw_item, "work item")
        if item.get("phase") in {"TASK_COMMITTED", "MERGED", "DELIVERY_COMMITTED"}:
            base_commit = item.get("base_commit")
            task_commit = item.get("task_commit")
            if isinstance(base_commit, str) and isinstance(task_commit, str):
                active_task_metadata = (base_commit, task_commit)
                break
        if (
            item.get("phase") == "TERMINAL"
            and item.get("last_result") in {"FAILED", "STOPPED", "TIMED_OUT"}
            and item.get("branch") == branch
            and isinstance(item.get("base_commit"), str)
        ):
            retry_metadata = (cast(str, item["base_commit"]), head)
    task_base_is_ancestor = (
        _git_is_ancestor(*active_task_metadata) if active_task_metadata is not None else None
    )
    task_diff_paths: tuple[str, ...] = ()
    if active_task_metadata is not None:
        task_diff_paths = tuple(
            line
            for line in _git_text(
                "diff",
                "--name-only",
                active_task_metadata[0],
                active_task_metadata[1],
            ).splitlines()
            if line
        )
    retry_head_descends_from_base = (
        _git_is_ancestor(*retry_metadata) if retry_metadata is not None else None
    )
    return {
        "branch": branch,
        "head": head,
        "main_commit": _git_text("rev-parse", "main"),
        "head_parents": tuple(parent_line[1:]),
        "head_paths": tuple(
            line
            for line in _git_text(
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "HEAD",
            ).splitlines()
            if line
        ),
        "dirty_entries": tuple(
            line
            for line in _git_text(
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ).splitlines()
            if line
        ),
        "task_base_is_ancestor": task_base_is_ancestor,
        "task_diff_paths": task_diff_paths,
        "retry_head_descends_from_base": retry_head_descends_from_base,
    }


def _blocked(reason: str, work_item: str | None = None) -> dict[str, Any]:
    return {
        "action": "BLOCKED",
        "reason": reason,
        "work_item": work_item,
    }


def _attempt_metadata(work_item: str, base_commit: str, ordinal: int) -> dict[str, Any]:
    slug = work_item.lower()
    short_base = base_commit[:12]
    child_slug = slug.replace("-", "_")
    return {
        "attempt": ordinal,
        "attempt_id": f"{slug}:{short_base}:attempt-{ordinal}",
        "base_commit": base_commit,
        "branch": f"feature/auto-dev-{slug}",
        "child_run_id": f"codex_{child_slug}_{short_base}_{ordinal}",
    }


def _dirty_paths(entries: tuple[str, ...] | list[str]) -> set[str]:
    paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str) or len(entry) < 4:
            raise TickGuardError("Git dirty entry is malformed")
        paths.add(entry[3:].replace("\\", "/"))
    return paths


def _control_commit_is_proven(
    *,
    expected_parent: str,
    head_parents: tuple[str, ...] | list[str],
    head_paths: tuple[str, ...] | list[str],
) -> bool:
    committed_paths = {path.replace("\\", "/") for path in head_paths}
    return (
        len(head_parents) == 1
        and head_parents[0] == expected_parent
        and committed_paths >= REQUIRED_DELIVERY_STATE_PATHS
        and not committed_paths - DELIVERY_STATE_PATHS
    )


def _active_payload(action: str, item_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": action,
        "work_item": item_id,
        "attempt": item.get("attempts"),
        "attempt_id": item.get("attempt_id"),
        "branch": item.get("branch"),
        "base_commit": item.get("base_commit"),
        "child_run_id": item.get("child_run_id"),
        "child_session": item.get("child_session"),
        "task_commit": item.get("task_commit"),
        "delivery_commit": item.get("delivery_commit"),
    }


def decide_tick(
    automation_state: dict[str, Any],
    selected: dict[str, Any] | None,
    delivery_statuses: dict[str, str],
    git_state: dict[str, Any],
    *,
    owner: str,
    lease_id: str,
) -> dict[str, Any]:
    """Return the only safe next controller action for the observed state."""
    lease = automation_state.get("lease")
    if not isinstance(lease, dict):
        return _blocked("ACTIVE_LEASE_REQUIRED")
    if (
        lease.get("owner") != owner
        or lease.get("lease_id") != lease_id
        or lease.get("status") != "ACTIVE"
    ):
        return _blocked("LEASE_FENCE_MISMATCH")

    work_items = _mapping(automation_state.get("work_items"), "work_items")
    active: list[tuple[str, dict[str, Any]]] = []
    for item_id, raw_item in work_items.items():
        item = _mapping(raw_item, f"work_items.{item_id}")
        attempts = item.get("attempts")
        phase = item.get("phase")
        if isinstance(attempts, int) and attempts > 0 and phase != TERMINAL_PHASE:
            active.append((item_id, item))
    if len(active) > 1:
        return _blocked("MULTIPLE_NONTERMINAL_ATTEMPTS")

    selected_id = selected.get("id") if selected is not None else None
    branch = git_state.get("branch")
    head = git_state.get("head")
    main_commit = git_state.get("main_commit")
    dirty_entries = git_state.get("dirty_entries")
    head_parents = git_state.get("head_parents")
    head_paths = git_state.get("head_paths")
    task_base_is_ancestor = git_state.get("task_base_is_ancestor")
    task_diff_paths = git_state.get("task_diff_paths", ())
    retry_head_descends_from_base = git_state.get("retry_head_descends_from_base")
    if (
        not isinstance(branch, str)
        or not isinstance(head, str)
        or not isinstance(main_commit, str)
        or not isinstance(dirty_entries, (tuple, list))
        or not isinstance(head_parents, (tuple, list))
        or not all(isinstance(parent, str) for parent in head_parents)
        or not isinstance(head_paths, (tuple, list))
        or not all(isinstance(path, str) for path in head_paths)
        or not isinstance(task_diff_paths, (tuple, list))
        or not all(isinstance(path, str) for path in task_diff_paths)
    ):
        raise TickGuardError("Git state is malformed")

    if active:
        item_id, item = active[0]
        queue_status = delivery_statuses.get(item_id)
        expected_branch = item.get("branch")
        base_commit = item.get("base_commit")
        phase = item.get("phase")
        if phase == "PREPARED":
            if not isinstance(expected_branch, str) or not isinstance(base_commit, str):
                return _blocked("ATTEMPT_RECOVERY_METADATA_MISSING", item_id)
            if queue_status not in {"PENDING", "READY", "IN_PROGRESS"}:
                return _blocked("PREPARED_DELIVERY_STATE_MISMATCH", item_id)
            if branch == "main":
                if queue_status == "IN_PROGRESS":
                    return _blocked("DELIVERY_STARTED_BEFORE_ATTEMPT_BRANCH", item_id)
                if selected_id != item_id:
                    return _blocked("PREPARED_ITEM_NOT_SELECTED", item_id)
                if head != main_commit or head != base_commit or dirty_entries:
                    return _blocked("ATTEMPT_BRANCH_PRECONDITION_FAILED", item_id)
                return _active_payload("CREATE_ATTEMPT_BRANCH", item_id, item)
            if branch == expected_branch:
                if main_commit != base_commit:
                    return _blocked("MAIN_DIVERGED_FROM_ATTEMPT_BASE", item_id)
                if dirty_entries and queue_status in {"PENDING", "READY"}:
                    return _blocked("PREPARED_WORKTREE_NOT_CLEAN", item_id)
                if queue_status in {"PENDING", "READY"} and selected_id == item_id:
                    return _active_payload("START_DELIVERY_ITEM", item_id, item)
                if queue_status == "IN_PROGRESS" and selected_id == item_id:
                    dirty_paths = _dirty_paths(cast(tuple[str, ...] | list[str], dirty_entries))
                    spawn_allowed = not dirty_paths or (
                        dirty_paths >= REQUIRED_DELIVERY_STATE_PATHS
                        and not dirty_paths - DELIVERY_STATE_PATHS
                    )
                    payload = _active_payload("RECONCILE_CHILD", item_id, item)
                    payload["spawn_allowed"] = spawn_allowed
                    return payload
            return _blocked("PREPARED_RECOVERY_STATE_MISMATCH", item_id)
        if phase == "RECOVERY_REQUIRED":
            if (
                not isinstance(expected_branch, str)
                or not isinstance(base_commit, str)
                or not isinstance(item.get("child_run_id"), str)
            ):
                return _active_payload(
                    "LEGACY_RECOVERY_REQUIRES_MANUAL_RECONCILIATION",
                    item_id,
                    item,
                )
            if queue_status in {"PENDING", "READY"} and selected_id == item_id:
                if branch == expected_branch:
                    action = "PRESERVE_FAILED_WORK" if dirty_entries else "SWITCH_TO_BASE_FOR_BLOCK"
                    return _active_payload(action, item_id, item)
                if branch == "main":
                    if head != main_commit or main_commit != base_commit or dirty_entries:
                        return _blocked("BLOCK_CONTROL_BASE_NOT_CLEAN", item_id)
                    return _active_payload(
                        "RECORD_UNSTARTED_DELIVERY_BLOCK",
                        item_id,
                        item,
                    )
                return _blocked("RECOVERY_BRANCH_MISMATCH", item_id)
            if queue_status == "IN_PROGRESS" and selected_id == item_id:
                if branch == expected_branch:
                    action = "PRESERVE_FAILED_WORK" if dirty_entries else "SWITCH_TO_BASE_FOR_BLOCK"
                    return _active_payload(action, item_id, item)
                if branch == "main":
                    if head != main_commit or main_commit != base_commit or dirty_entries:
                        return _blocked("BLOCK_CONTROL_BASE_NOT_CLEAN", item_id)
                    return _active_payload("RECORD_DELIVERY_BLOCK", item_id, item)
                return _blocked("RECOVERY_BRANCH_MISMATCH", item_id)
            if queue_status == "BLOCKED":
                if branch != "main" or head != main_commit:
                    return _blocked("BLOCK_CONTROL_MAIN_MISMATCH", item_id)
                dirty_paths = _dirty_paths(cast(tuple[str, ...] | list[str], dirty_entries))
                if dirty_paths:
                    if (
                        head != base_commit
                        or not dirty_paths >= REQUIRED_DELIVERY_STATE_PATHS
                        or dirty_paths - DELIVERY_STATE_PATHS
                    ):
                        return _blocked("BLOCK_CONTROL_DIRTY_STATE_UNSAFE", item_id)
                    return _active_payload("COMMIT_BLOCK_STATE", item_id, item)
                if not _control_commit_is_proven(
                    expected_parent=base_commit,
                    head_parents=cast(tuple[str, ...] | list[str], head_parents),
                    head_paths=cast(tuple[str, ...] | list[str], head_paths),
                ):
                    return _blocked("BLOCK_CONTROL_COMMIT_NOT_PROVEN", item_id)
                payload = _active_payload("RECORD_BLOCK_COMMIT", item_id, item)
                payload["delivery_commit"] = head
                return payload
            return _blocked("RECOVERY_DELIVERY_STATE_MISMATCH", item_id)
        if phase in RESUME_ACTIONS:
            if queue_status != "IN_PROGRESS" or selected_id != item_id:
                return _blocked("DELIVERY_AND_ATTEMPT_MISMATCH", item_id)
            if branch != expected_branch:
                return _blocked("RECOVERY_BRANCH_MISMATCH", item_id)
            return _active_payload(RESUME_ACTIONS[str(phase)], item_id, item)
        if phase == "TASK_COMMITTED":
            if queue_status != "IN_PROGRESS" or selected_id != item_id:
                return _blocked("DELIVERY_AND_ATTEMPT_MISMATCH", item_id)
            task_commit = item.get("task_commit")
            if not isinstance(task_commit, str) or not isinstance(base_commit, str):
                return _blocked("TASK_COMMIT_METADATA_MISSING", item_id)
            if task_base_is_ancestor is not True:
                return _blocked("TASK_COMMIT_NOT_DESCENDED_FROM_BASE", item_id)
            task_delta = {
                path.replace("\\", "/")
                for path in cast(tuple[str, ...] | list[str], task_diff_paths)
            }
            if not task_delta >= REQUIRED_DELIVERY_STATE_PATHS:
                return _blocked("TASK_COMMIT_MISSING_DELIVERY_CLAIM", item_id)
            if dirty_entries:
                return _blocked("TASK_COMMITTED_WORKTREE_NOT_CLEAN", item_id)
            if branch == expected_branch:
                if head != task_commit:
                    return _blocked("TASK_BRANCH_HEAD_MISMATCH", item_id)
                if main_commit != base_commit:
                    return _blocked("MAIN_DIVERGED_FROM_ATTEMPT_BASE", item_id)
                return _active_payload("RESUME_MERGE", item_id, item)
            if branch == "main" and head == main_commit == base_commit:
                if dirty_entries:
                    return _blocked("MAIN_WORKTREE_NOT_CLEAN", item_id)
                return _active_payload("RESUME_FAST_FORWARD", item_id, item)
            if branch == "main" and head == main_commit == task_commit:
                return _active_payload("RECORD_MERGED", item_id, item)
            return _blocked("MERGE_RECOVERY_STATE_MISMATCH", item_id)
        if phase == "MERGED":
            task_commit = item.get("task_commit")
            if (
                not isinstance(task_commit, str)
                or not isinstance(base_commit, str)
                or task_base_is_ancestor is not True
            ):
                return _blocked("TASK_COMMIT_METADATA_MISSING", item_id)
            if branch != "main" or head != main_commit:
                return _blocked("POST_MERGE_MAIN_MISMATCH", item_id)
            dirty_paths = _dirty_paths(cast(tuple[str, ...] | list[str], dirty_entries))
            if queue_status == "IN_PROGRESS":
                if selected_id != item_id or head != task_commit or dirty_paths:
                    return _blocked("DELIVERY_COMPLETION_PRECONDITION_FAILED", item_id)
                return _active_payload("RESUME_DELIVERY_COMPLETION", item_id, item)
            if queue_status == "COMPLETE":
                unexpected = dirty_paths - DELIVERY_STATE_PATHS
                if unexpected or (dirty_paths and not dirty_paths >= REQUIRED_DELIVERY_STATE_PATHS):
                    return _blocked("POST_MERGE_WORKTREE_NOT_CLEAN", item_id)
                if dirty_paths:
                    if head != task_commit:
                        return _blocked("DELIVERY_STATE_BASE_COMMIT_MISMATCH", item_id)
                    return _active_payload("COMMIT_DELIVERY_STATE", item_id, item)
                if not _control_commit_is_proven(
                    expected_parent=task_commit,
                    head_parents=cast(tuple[str, ...] | list[str], head_parents),
                    head_paths=cast(tuple[str, ...] | list[str], head_paths),
                ):
                    return _blocked("DELIVERY_COMMIT_NOT_PROVEN", item_id)
                payload = _active_payload("RECORD_DELIVERY_COMMIT", item_id, item)
                payload["delivery_commit"] = head
                return payload
            return _blocked("POST_MERGE_DELIVERY_STATE_MISMATCH", item_id)
        if phase == "DELIVERY_COMMITTED":
            delivery_commit = item.get("delivery_commit")
            task_commit = item.get("task_commit")
            if (
                not isinstance(delivery_commit, str)
                or not isinstance(task_commit, str)
                or queue_status != "COMPLETE"
                or branch != "main"
                or head != main_commit
                or head != delivery_commit
                or dirty_entries
                or not _control_commit_is_proven(
                    expected_parent=task_commit,
                    head_parents=cast(tuple[str, ...] | list[str], head_parents),
                    head_paths=cast(tuple[str, ...] | list[str], head_paths),
                )
            ):
                return _blocked("DELIVERY_COMMIT_STATE_MISMATCH", item_id)
            return _active_payload("FINALIZE_SUCCESSFUL_ATTEMPT", item_id, item)
        if phase == "BLOCK_COMMITTED":
            delivery_commit = item.get("delivery_commit")
            if (
                not isinstance(delivery_commit, str)
                or not isinstance(base_commit, str)
                or queue_status != "BLOCKED"
                or branch != "main"
                or head != main_commit
                or head != delivery_commit
                or dirty_entries
                or not _control_commit_is_proven(
                    expected_parent=base_commit,
                    head_parents=cast(tuple[str, ...] | list[str], head_parents),
                    head_paths=cast(tuple[str, ...] | list[str], head_paths),
                )
            ):
                return _blocked("BLOCK_COMMIT_STATE_MISMATCH", item_id)
            return _active_payload("FINALIZE_BLOCKED_ATTEMPT", item_id, item)
        return _blocked("UNKNOWN_ATTEMPT_PHASE", item_id)

    if selected is None:
        return {"action": "NO_ACTION", "reason": "NO_READY_WORK", "work_item": None}
    if not isinstance(selected_id, str):
        raise TickGuardError("selected work item lacks an ID")
    selected_status = selected.get("status")
    if delivery_statuses.get(selected_id) != selected_status:
        return _blocked("DELIVERY_SNAPSHOT_INCONSISTENT", selected_id)
    prior_raw = work_items.get(selected_id)
    prior = _mapping(prior_raw, f"work_items.{selected_id}") if prior_raw is not None else {}
    attempts = prior.get("attempts", 0)
    if not isinstance(attempts, int) or attempts < 0:
        raise TickGuardError("attempt counter is malformed")
    if attempts >= MAX_ATTEMPTS:
        return _blocked("ATTEMPT_LIMIT_REACHED", selected_id)

    if prior and prior.get("phase") == TERMINAL_PHASE:
        result = prior.get("last_result")
        if result == "PASSED":
            return _blocked("PASSED_ATTEMPT_SELECTED_AGAIN", selected_id)
        if result in {"FAILED", "STOPPED", "TIMED_OUT"}:
            expected_branch = prior.get("branch")
            base_commit = prior.get("base_commit")
            if selected_status != "IN_PROGRESS":
                return _blocked("RETRYABLE_RESULT_DELIVERY_STATE_MISMATCH", selected_id)
            if not isinstance(expected_branch, str) or not isinstance(base_commit, str):
                return _blocked("RETRY_RECOVERY_METADATA_MISSING", selected_id)
            if branch != expected_branch:
                return _blocked("RETRY_BRANCH_MISMATCH", selected_id)
            if main_commit != base_commit:
                return _blocked("MAIN_DIVERGED_FROM_RETRY_BASE", selected_id)
            if retry_head_descends_from_base is not True:
                return _blocked("RETRY_BRANCH_NOT_DESCENDED_FROM_BASE", selected_id)
            if dirty_entries:
                return {
                    **_active_payload(
                        "PRESERVE_RETRY_WORK",
                        selected_id,
                        prior,
                    ),
                    "dirty_entries": len(dirty_entries),
                }
            metadata = _attempt_metadata(selected_id, base_commit, attempts + 1)
            metadata["branch"] = expected_branch
            return {
                "action": "READY_RETRY",
                "work_item": selected_id,
                **metadata,
                "dirty_entries": 0,
            }
        if result == "BLOCKED":
            if selected_status not in {"PENDING", "READY"}:
                return _blocked("BLOCKED_RETRY_DELIVERY_STATE_MISMATCH", selected_id)
            if branch != "main" or head != main_commit or dirty_entries:
                return _blocked("BLOCKED_RETRY_REQUIRES_CLEAN_MAIN", selected_id)
            metadata = _attempt_metadata(selected_id, head, attempts + 1)
            metadata["branch"] = f"feature/auto-dev-{selected_id.lower()}-attempt-{attempts + 1}"
            return {
                "action": "READY_RETRY",
                "work_item": selected_id,
                **metadata,
                "dirty_entries": 0,
            }
        return _blocked("TERMINAL_RESULT_UNSUPPORTED", selected_id)

    if selected_status == "IN_PROGRESS":
        return _blocked("IN_PROGRESS_ITEM_LACKS_TERMINAL_ATTEMPT", selected_id)
    if selected_status not in {"PENDING", "READY"}:
        return _blocked("SELECTED_ITEM_STATUS_UNSAFE", selected_id)
    if branch != "main":
        return _blocked("NEW_WORK_REQUIRES_MAIN", selected_id)
    if head != main_commit:
        return _blocked("MAIN_HEAD_MISMATCH", selected_id)
    if dirty_entries:
        return _blocked("MAIN_WORKTREE_NOT_CLEAN", selected_id)
    return {
        "action": "READY_NEW",
        "work_item": selected_id,
        **_attempt_metadata(selected_id, head, attempts + 1),
        "dirty_entries": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--lease-id", required=True)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Inspect without rolling forward a pending delivery journal.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        selected, delivery_statuses, delivery_generation = _delivery_snapshot(
            preflight=cast(bool, arguments.preflight)
        )
        automation_state = _automation_state()
        decision = decide_tick(
            automation_state,
            selected,
            delivery_statuses,
            _git_state(automation_state),
            owner=cast(str, arguments.owner),
            lease_id=cast(str, arguments.lease_id),
        )
        decision["delivery_generation"] = delivery_generation
    except (OSError, subprocess.SubprocessError, TickGuardError) as error:
        print(
            json.dumps(
                {"error": "AUTOMATION_TICK_ERROR", "message": str(error)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return EXIT_STATE_ERROR
    print(json.dumps(decision, indent=2, sort_keys=True))
    return EXIT_GUARD_BLOCKED if decision["action"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
