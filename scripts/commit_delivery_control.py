"""Commit one proven delivery-control generation while holding its mutex.

This wrapper is intentionally narrow. It can stage and commit only the three
delivery state files, never task code, and never pushes or merges.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml
from delivery_state import (
    TARGET_RELATIVE_PATHS,
    delivery_generation_digest,
    delivery_state_mutex,
    recover_delivery_state,
    validate_delivery_generation,
)

ROOT = Path(__file__).resolve().parents[1]
STATE_SCRIPT = ROOT / "scripts/manage_automation_state.py"
ALLOWED_PATHS = frozenset(TARGET_RELATIVE_PATHS)
REQUIRED_PATHS = frozenset(
    {
        "delivery/WORK_QUEUE.yaml",
        "delivery/LOOP_STATE.yaml",
    }
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
GENERATION_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WORK_ITEM_PATTERN = re.compile(r"^[A-Z]+(?:-[A-Z]+)*-[0-9]{3}$")


class ControlCommitError(RuntimeError):
    """Raised when a delivery control commit cannot be proven safe."""


def _run(
    command: list[str],
    *,
    environment: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    effective_environment = os.environ.copy()
    if environment is not None:
        effective_environment.update(environment)
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=effective_environment,
        input=input_text,
        timeout=30,
    )


def _git_text(
    *arguments: str,
    environment: Mapping[str, str] | None = None,
) -> str:
    result = _run(["git", *arguments], environment=environment)
    if result.returncode != 0:
        raise ControlCommitError("Git control command failed closed")
    # Preserve porcelain status' leading column; only remove record terminators.
    return result.stdout.rstrip("\r\n")


def _load_mapping(relative_path: str) -> dict[str, Any]:
    loaded = yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ControlCommitError("Delivery control file must contain a mapping")
    return cast(dict[str, Any], loaded)


def _generation() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    queue = _load_mapping(TARGET_RELATIVE_PATHS[0])
    state = _load_mapping(TARGET_RELATIVE_PATHS[1])
    program = _load_mapping(TARGET_RELATIVE_PATHS[2])
    validate_delivery_generation(queue, state, program)
    return queue, state, program, delivery_generation_digest(queue, state, program)


def _mapping_from_index(
    relative_path: str,
    *,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    loaded = yaml.safe_load(_git_text("show", f":{relative_path}", environment=environment))
    if not isinstance(loaded, dict):
        raise ControlCommitError("Staged delivery control file must contain a mapping")
    return cast(dict[str, Any], loaded)


def _generation_from_index(
    *,
    environment: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    queue = _mapping_from_index(TARGET_RELATIVE_PATHS[0], environment=environment)
    state = _mapping_from_index(TARGET_RELATIVE_PATHS[1], environment=environment)
    program = _mapping_from_index(TARGET_RELATIVE_PATHS[2], environment=environment)
    validate_delivery_generation(queue, state, program)
    return queue, state, program, delivery_generation_digest(queue, state, program)


def _porcelain_paths(output: str) -> set[str]:
    paths: set[str] = set()
    for line in output.splitlines():
        if len(line) < 4 or " -> " in line:
            raise ControlCommitError("Git control status contains an unsupported entry")
        paths.add(line[3:].replace("\\", "/"))
    return paths


def _last_record(state: dict[str, Any], field: str) -> dict[str, Any]:
    records = state.get(field)
    if not isinstance(records, list) or not records or not isinstance(records[-1], dict):
        raise ControlCommitError("Delivery outcome is missing its terminal loop-state record")
    return cast(dict[str, Any], records[-1])


def _require_delivery_outcome(
    *,
    queue: dict[str, Any],
    state: dict[str, Any],
    work_item: str,
    kind: str,
) -> None:
    items = queue.get("items")
    if not isinstance(items, list):
        raise ControlCommitError("Delivery queue is malformed")
    matches = [item for item in items if isinstance(item, dict) and item.get("id") == work_item]
    if len(matches) != 1:
        raise ControlCommitError("Named delivery work item is not unique")
    item = matches[0]
    if state.get("current_work_item") is not None:
        raise ControlCommitError("Delivery outcome still has an active work item")

    if kind == "complete":
        if (
            item.get("status") != "COMPLETE"
            or state.get("last_result") != "COMPLETE"
            or state.get("blocker") is not None
        ):
            raise ControlCommitError("Completion commit is not bound to the named delivery item")
        record = _last_record(state, "evidence_records")
        expected_evidence_path = f"evidence/delivery-loop/{work_item}.yaml"
        if (
            record.get("work_item") != work_item
            or record.get("path") != expected_evidence_path
            or not isinstance(record.get("recorded_at"), str)
            or not record["recorded_at"].strip()
        ):
            raise ControlCommitError("Completion commit lacks matching named-item evidence")
        return

    blocking_condition = item.get("blocking_condition")
    if (
        item.get("status") != "BLOCKED"
        or not isinstance(blocking_condition, str)
        or not blocking_condition.strip()
        or state.get("last_result") != "BLOCKED"
        or state.get("blocker") != blocking_condition
    ):
        raise ControlCommitError("Block commit is not bound to the named delivery item")
    record = _last_record(state, "blocked_records")
    if (
        record.get("work_item") != work_item
        or record.get("reason") != blocking_condition
        or not isinstance(record.get("recorded_at"), str)
        or not record["recorded_at"].strip()
    ):
        raise ControlCommitError("Block commit lacks a matching named-item blocker record")


def _require_committed_completion_evidence(
    *,
    state: dict[str, Any],
    work_item: str,
    expected_parent: str,
) -> None:
    record = _last_record(state, "evidence_records")
    expected_path = f"evidence/delivery-loop/{work_item}.yaml"
    if record.get("work_item") != work_item or record.get("path") != expected_path:
        raise ControlCommitError("Completion evidence path is not canonical")
    if _git_text("cat-file", "-t", f"{expected_parent}:{expected_path}") != "blob":
        raise ControlCommitError("Completion evidence is not committed in the task commit")


def _require_active_attempt(
    *,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    kind: str,
    expected_parent: str,
) -> None:
    result = _run([sys.executable, str(STATE_SCRIPT), "status"])
    if result.returncode != 0:
        raise ControlCommitError("Automation state status failed closed")
    try:
        state: object = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ControlCommitError("Automation state status returned invalid JSON") from error
    if not isinstance(state, dict):
        raise ControlCommitError("Automation state status is malformed")
    lease = state.get("lease")
    if (
        not isinstance(lease, dict)
        or lease.get("owner") != owner
        or lease.get("lease_id") != lease_id
        or lease.get("status") != "ACTIVE"
    ):
        raise ControlCommitError("Automation lease fence is not active")
    work_items = state.get("work_items")
    item = work_items.get(work_item) if isinstance(work_items, dict) else None
    if not isinstance(item, dict) or item.get("attempt_id") != attempt_id:
        raise ControlCommitError("Automation attempt fence does not match")
    required_phase = "MERGED" if kind == "complete" else "RECOVERY_REQUIRED"
    parent_field = "task_commit" if kind == "complete" else "base_commit"
    if item.get("phase") != required_phase or item.get(parent_field) != expected_parent:
        raise ControlCommitError("Automation phase does not authorize this control commit")


def _temporary_index_environment(index_path: Path) -> dict[str, str]:
    return {"GIT_INDEX_FILE": str(index_path.resolve())}


def _staged_paths(*, environment: Mapping[str, str]) -> set[str]:
    return {
        path.replace("\\", "/")
        for path in _git_text(
            "diff",
            "--cached",
            "--name-only",
            environment=environment,
        ).splitlines()
        if path
    }


def _commit_paths(commit: str) -> set[str]:
    return {
        path.replace("\\", "/")
        for path in _git_text(
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
        ).splitlines()
        if path
    }


def _synchronize_allowed_index_entries(commit: str) -> None:
    command = ["git", "update-index"]
    for path in sorted(ALLOWED_PATHS):
        entry = _git_text("ls-tree", commit, "--", path)
        try:
            metadata, returned_path = entry.split("\t", maxsplit=1)
            mode, object_type, object_id = metadata.split()
        except ValueError as error:
            raise ControlCommitError("Created control tree is malformed") from error
        if object_type != "blob" or returned_path.replace("\\", "/") != path:
            raise ControlCommitError("Created control tree does not contain an allowed file")
        command.extend(("--cacheinfo", f"{mode},{object_id},{path}"))
    updated = _run(command)
    if updated.returncode != 0:
        raise ControlCommitError("Unable to synchronize bounded control index entries")


def commit_control_generation(
    *,
    owner: str,
    lease_id: str,
    work_item: str,
    attempt_id: str,
    kind: str,
    expected_parent: str,
    expected_generation: str,
) -> dict[str, str]:
    if WORK_ITEM_PATTERN.fullmatch(work_item) is None:
        raise ControlCommitError("Work item ID is invalid")
    if kind not in {"complete", "block"}:
        raise ControlCommitError("Control commit kind is invalid")
    if COMMIT_PATTERN.fullmatch(expected_parent) is None:
        raise ControlCommitError("Expected parent commit is invalid")
    if GENERATION_PATTERN.fullmatch(expected_generation) is None:
        raise ControlCommitError("Expected generation is invalid")

    with delivery_state_mutex(root=ROOT):
        recover_delivery_state(root=ROOT)
        queue, state, _program, current_generation = _generation()
        if current_generation != expected_generation:
            raise ControlCommitError(
                "Delivery generation changed; obtain a fresh controller snapshot"
            )
        _require_delivery_outcome(
            queue=queue,
            state=state,
            work_item=work_item,
            kind=kind,
        )
        _require_active_attempt(
            owner=owner,
            lease_id=lease_id,
            work_item=work_item,
            attempt_id=attempt_id,
            kind=kind,
            expected_parent=expected_parent,
        )
        if _git_text("branch", "--show-current") != "main":
            raise ControlCommitError("Delivery control commits require main")
        if _git_text("rev-parse", "HEAD") != expected_parent:
            raise ControlCommitError("Main does not equal the expected control parent")
        if kind == "complete":
            _require_committed_completion_evidence(
                state=state,
                work_item=work_item,
                expected_parent=expected_parent,
            )
        dirty_paths = _porcelain_paths(
            _git_text("status", "--porcelain=v1", "--untracked-files=all")
        )
        if not dirty_paths or not dirty_paths >= REQUIRED_PATHS or dirty_paths - ALLOWED_PATHS:
            raise ControlCommitError("Dirty paths do not form a bounded delivery generation")

        with tempfile.TemporaryDirectory(prefix="delivery-control-index-") as directory:
            index_environment = _temporary_index_environment(Path(directory) / "index")
            read_tree = _run(
                ["git", "read-tree", expected_parent],
                environment=index_environment,
            )
            if read_tree.returncode != 0:
                raise ControlCommitError("Unable to initialize isolated control index")
            added = _run(
                ["git", "add", "--", *sorted(ALLOWED_PATHS)],
                environment=index_environment,
            )
            if added.returncode != 0:
                raise ControlCommitError("Unable to stage isolated delivery control files")
            staged_paths = _staged_paths(environment=index_environment)
            if not staged_paths >= REQUIRED_PATHS or staged_paths - ALLOWED_PATHS:
                raise ControlCommitError(
                    "Isolated staged paths do not form a bounded delivery generation"
                )
            (
                staged_queue,
                staged_state,
                _staged_program,
                staged_generation,
            ) = _generation_from_index(environment=index_environment)
            if staged_generation != expected_generation:
                raise ControlCommitError("Delivery generation changed while preparing its commit")
            _require_delivery_outcome(
                queue=staged_queue,
                state=staged_state,
                work_item=work_item,
                kind=kind,
            )
            tree = _git_text("write-tree", environment=index_environment)

        outcome = "completion" if kind == "complete" else "block"
        committed = _run(
            ["git", "commit-tree", tree, "-p", expected_parent],
            input_text=f"chore({work_item.lower()}): record delivery {outcome}\n",
        )
        if committed.returncode != 0:
            raise ControlCommitError("Unable to create bounded delivery control commit")
        commit = committed.stdout.strip()
        if COMMIT_PATTERN.fullmatch(commit) is None:
            raise ControlCommitError("Created control commit ID is malformed")
        parents = _git_text("rev-list", "--parents", "-n", "1", commit).split()[1:]
        committed_paths = _commit_paths(commit)
        if (
            parents != [expected_parent]
            or not committed_paths >= REQUIRED_PATHS
            or committed_paths - ALLOWED_PATHS
        ):
            raise ControlCommitError("Created control commit failed pre-update proof")

        # Update only the allowlisted shared-index entries. The commit object is
        # already immutable, so a concurrent or hook-created unrelated staged
        # path can never enter it.
        _synchronize_allowed_index_entries(commit)
        if (
            _git_text("branch", "--show-current") != "main"
            or _git_text("rev-parse", "HEAD") != expected_parent
        ):
            raise ControlCommitError("Main changed before the control ref update")
        final_dirty_paths = _porcelain_paths(
            _git_text("status", "--porcelain=v1", "--untracked-files=all")
        )
        if not final_dirty_paths >= REQUIRED_PATHS or final_dirty_paths - ALLOWED_PATHS:
            raise ControlCommitError("Git state changed before the control ref update")
        updated = _run(["git", "update-ref", "refs/heads/main", commit, expected_parent])
        if updated.returncode != 0:
            raise ControlCommitError("Main compare-and-swap update failed")
        return {"commit": commit, "generation": staged_generation, "kind": kind}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--lease-id", required=True)
    parser.add_argument("--work-item", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--kind", choices=("complete", "block"), required=True)
    parser.add_argument("--expected-parent", required=True)
    parser.add_argument("--expected-generation", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        output = commit_control_generation(
            owner=cast(str, arguments.owner),
            lease_id=cast(str, arguments.lease_id),
            work_item=cast(str, arguments.work_item),
            attempt_id=cast(str, arguments.attempt_id),
            kind=cast(str, arguments.kind),
            expected_parent=cast(str, arguments.expected_parent),
            expected_generation=cast(str, arguments.expected_generation),
        )
    except (ControlCommitError, OSError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {"error": "DELIVERY_CONTROL_COMMIT_ERROR", "message": str(error)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
