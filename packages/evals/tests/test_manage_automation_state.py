from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/manage_automation_state.py"
STATE_JSON_NAME = "state.json"
STATE_MARKDOWN_NAME = "state.md"
EXIT_BUSY = 3
EXIT_STATE_ERROR = 4
BASE_COMMIT = "a" * 40
TASK_COMMIT = "b" * 40
DELIVERY_COMMIT = "c" * 40


def run_cli(state_dir: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--state-dir", str(state_dir), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def parsed_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    parsed: object = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return parsed


def acquire(
    state_dir: Path,
    owner: str = "controller-main",
    ttl_seconds: int = 3900,
) -> str:
    result = run_cli(
        state_dir,
        "acquire",
        "--owner",
        owner,
        "--ttl-seconds",
        str(ttl_seconds),
    )
    assert result.returncode == 0, result.stderr
    lease_id = parsed_stdout(result)["lease_id"]
    assert isinstance(lease_id, str)
    return lease_id


def begin(
    state_dir: Path,
    lease_id: str,
    *,
    owner: str = "controller-main",
    work_item: str = "HARDEN-CI-001",
    attempt_id: str = "attempt-001",
    branch: str = "feature/auto-dev-harden-ci-001",
    base_commit: str = BASE_COMMIT,
    child_run_id: str = "run-harden-ci-001-01",
) -> subprocess.CompletedProcess[str]:
    return run_cli(
        state_dir,
        "begin-attempt",
        "--owner",
        owner,
        "--lease-id",
        lease_id,
        "--work-item",
        work_item,
        "--attempt-id",
        attempt_id,
        "--branch",
        branch,
        "--base-commit",
        base_commit,
        "--child-run-id",
        child_run_id,
    )


def record_result(
    state_dir: Path,
    lease_id: str,
    *,
    owner: str = "controller-main",
    work_item: str = "HARDEN-CI-001",
    attempt_id: str = "attempt-001",
    result: str = "PASSED",
    summary: str = "Synthetic verification passed.",
) -> subprocess.CompletedProcess[str]:
    return run_cli(
        state_dir,
        "record-result",
        "--owner",
        owner,
        "--lease-id",
        lease_id,
        "--work-item",
        work_item,
        "--attempt-id",
        attempt_id,
        "--result",
        result,
        "--summary",
        summary,
    )


def transition(
    state_dir: Path,
    command: str,
    lease_id: str,
    *,
    owner: str = "controller-main",
    work_item: str = "HARDEN-CI-001",
    attempt_id: str = "attempt-001",
    extra: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return run_cli(
        state_dir,
        command,
        "--owner",
        owner,
        "--lease-id",
        lease_id,
        "--work-item",
        work_item,
        "--attempt-id",
        attempt_id,
        *extra,
    )


def v1_item(
    *,
    attempts: int = 1,
    attempt_id: str | None = "legacy-attempt-001",
    result: str | None = None,
    summary: str | None = None,
) -> dict[str, object]:
    timestamp = "2026-07-31T01:00:00Z"
    return {
        "attempts": attempts,
        "attempt_id": attempt_id,
        "last_attempt_at": timestamp if attempts else None,
        "last_result": result,
        "last_summary": summary,
        "last_result_at": timestamp if result is not None else None,
    }


def write_state(state_dir: Path, payload: dict[str, object]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / STATE_JSON_NAME).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_acquire_is_exclusive_and_busy_has_distinct_exit_code(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    commands = [
        sys.executable,
        str(SCRIPT),
        "--state-dir",
        str(state_dir),
        "acquire",
        "--owner",
        "cron-a",
        "--ttl-seconds",
        "60",
    ]
    first = subprocess.Popen(
        commands,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second_commands = [*commands]
    second_commands[second_commands.index("cron-a")] = "cron-b"
    second = subprocess.Popen(
        second_commands,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_stdout, first_stderr = first.communicate(timeout=15)
    second_stdout, second_stderr = second.communicate(timeout=15)

    outcomes = {
        first.returncode: (first_stdout, first_stderr),
        second.returncode: (second_stdout, second_stderr),
    }
    assert sorted((first.returncode, second.returncode)) == [0, EXIT_BUSY]
    assert json.loads(outcomes[EXIT_BUSY][1])["error"] == "LEASE_BUSY"
    acquired = json.loads(outcomes[0][0])
    assert acquired["action"] == "ACQUIRED"
    assert acquired["lease_id"]


def test_renewal_keeps_lease_id_and_stale_takeover_fences_old_holder(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    first_id = acquire(state_dir, ttl_seconds=60)
    unfenced = run_cli(
        state_dir,
        "acquire",
        "--owner",
        "controller-main",
        "--ttl-seconds",
        "60",
    )
    assert unfenced.returncode == EXIT_BUSY
    renewed = run_cli(
        state_dir,
        "acquire",
        "--owner",
        "controller-main",
        "--lease-id",
        first_id,
        "--ttl-seconds",
        "60",
    )
    assert renewed.returncode == 0, renewed.stderr
    assert parsed_stdout(renewed)["action"] == "RENEWED"
    assert parsed_stdout(renewed)["lease_id"] == first_id

    persisted = json.loads((state_dir / STATE_JSON_NAME).read_text(encoding="utf-8"))
    persisted["lease"]["acquired_at"] = "2020-01-01T00:00:00Z"
    persisted["lease"]["expires_at"] = "2020-01-01T00:01:00Z"
    write_state(state_dir, persisted)
    takeover = run_cli(
        state_dir,
        "acquire",
        "--owner",
        "controller-main",
        "--ttl-seconds",
        "60",
    )
    assert takeover.returncode == 0, takeover.stderr
    second_id = parsed_stdout(takeover)["lease_id"]
    assert parsed_stdout(takeover)["action"] == "STALE_TAKEOVER"
    assert second_id != first_id

    fenced = begin(state_dir, first_id)
    assert fenced.returncode == EXIT_BUSY
    assert json.loads(fenced.stderr)["error"] == "LEASE_BUSY"
    accepted = begin(state_dir, second_id)
    assert accepted.returncode == 0, accepted.stderr


def test_lease_expiry_is_checked_after_waiting_for_the_state_mutex(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir, ttl_seconds=1)
    holder_code = """
import sys
import time
from pathlib import Path
from scripts.manage_automation_state import _state_mutex

with _state_mutex(Path(sys.argv[1])):
    print("LOCKED", flush=True)
    time.sleep(1.5)
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(state_dir)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "LOCKED"

    attempted = begin(state_dir, lease_id)
    holder_stdout, holder_stderr = holder.communicate(timeout=5)

    assert holder.returncode == 0, f"{holder_stdout}\n{holder_stderr}"
    assert attempted.returncode == EXIT_BUSY
    assert json.loads(attempted.stderr)["error"] == "LEASE_BUSY"
    current = parsed_stdout(run_cli(state_dir, "status"))
    assert current["work_items"] == {}
    assert current["lease"]["status"] == "STALE"


def test_release_requires_matching_owner_and_lease_id(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir, "cron-a")
    wrong_owner = run_cli(
        state_dir,
        "release",
        "--owner",
        "cron-b",
        "--lease-id",
        lease_id,
    )
    wrong_lease = run_cli(
        state_dir,
        "release",
        "--owner",
        "cron-a",
        "--lease-id",
        "00000000-0000-4000-8000-000000000000",
    )
    assert wrong_owner.returncode == EXIT_BUSY
    assert wrong_lease.returncode == EXIT_BUSY

    released = run_cli(
        state_dir,
        "release",
        "--owner",
        "cron-a",
        "--lease-id",
        lease_id,
    )
    assert released.returncode == 0, released.stderr
    assert parsed_stdout(run_cli(state_dir, "status"))["lease"] is None


def test_full_state_machine_round_trip_and_idempotent_transitions(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir)
    begun = begin(state_dir, lease_id)
    replayed = begin(state_dir, lease_id)
    assert begun.returncode == 0, begun.stderr
    assert replayed.returncode == 0, replayed.stderr
    assert parsed_stdout(replayed)["action"] == "ATTEMPT_ALREADY_BEGUN"
    assert parsed_stdout(replayed)["attempt"] == 1

    attach_args = ("--child-session", "codex-thread:session-001")
    attached = transition(state_dir, "attach-child", lease_id, extra=attach_args)
    attached_again = transition(state_dir, "attach-child", lease_id, extra=attach_args)
    assert attached.returncode == 0, attached.stderr
    assert parsed_stdout(attached_again)["action"] == "CHILD_ALREADY_ATTACHED"

    verifying = transition(state_dir, "begin-verification", lease_id)
    verifying_again = transition(state_dir, "begin-verification", lease_id)
    assert verifying.returncode == 0, verifying.stderr
    assert parsed_stdout(verifying_again)["action"] == "VERIFICATION_ALREADY_BEGUN"

    regression = transition(state_dir, "attach-child", lease_id, extra=attach_args)
    assert regression.returncode == EXIT_STATE_ERROR
    assert "requires PREPARED phase" in json.loads(regression.stderr)["message"]

    commit_args = ("--task-commit", TASK_COMMIT.upper())
    committed = transition(state_dir, "record-task-commit", lease_id, extra=commit_args)
    committed_again = transition(state_dir, "record-task-commit", lease_id, extra=commit_args)
    assert committed.returncode == 0, committed.stderr
    assert parsed_stdout(committed_again)["action"] == "TASK_COMMIT_ALREADY_RECORDED"

    merged = transition(state_dir, "record-merged", lease_id)
    merged_again = transition(state_dir, "record-merged", lease_id)
    assert merged.returncode == 0, merged.stderr
    assert parsed_stdout(merged_again)["action"] == "MERGE_ALREADY_RECORDED"

    delivery_commit_args = ("--delivery-commit", DELIVERY_COMMIT.upper())
    delivery_committed = transition(
        state_dir,
        "record-delivery-commit",
        lease_id,
        extra=delivery_commit_args,
    )
    delivery_committed_again = transition(
        state_dir,
        "record-delivery-commit",
        lease_id,
        extra=delivery_commit_args,
    )
    assert delivery_committed.returncode == 0, delivery_committed.stderr
    assert parsed_stdout(delivery_committed_again)["action"] == "DELIVERY_COMMIT_ALREADY_RECORDED"

    recorded = record_result(
        state_dir,
        lease_id,
        summary="CI database and plugin gates passed.",
    )
    recorded_again = record_result(
        state_dir,
        lease_id,
        summary="CI database and plugin gates passed.",
    )
    assert recorded.returncode == 0, recorded.stderr
    assert parsed_stdout(recorded_again)["action"] == "RESULT_ALREADY_RECORDED"

    status = parsed_stdout(run_cli(state_dir, "status"))
    assert status["schema_version"] == 2
    item = status["work_items"]["HARDEN-CI-001"]
    assert item == {
        "attempts": 1,
        "attempt_id": "attempt-001",
        "last_attempt_at": item["last_attempt_at"],
        "last_result": "PASSED",
        "last_summary": "CI database and plugin gates passed.",
        "last_result_at": item["last_result_at"],
        "phase": "TERMINAL",
        "branch": "feature/auto-dev-harden-ci-001",
        "base_commit": BASE_COMMIT,
        "child_run_id": "run-harden-ci-001-01",
        "child_session": "codex-thread:session-001",
        "task_commit": TASK_COMMIT,
        "delivery_commit": DELIVERY_COMMIT,
        "legacy_migrated": False,
    }
    markdown = (state_dir / STATE_MARKDOWN_NAME).read_text(encoding="utf-8")
    assert "HARDEN-CI-001" in markdown
    assert "TERMINAL" in markdown
    assert "codex-thread:session-001" in markdown


def test_attempt_id_is_immutably_bound_to_recovery_metadata(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir)
    assert begin(state_dir, lease_id).returncode == 0

    changed = begin(
        state_dir,
        lease_id,
        branch="feature/different",
    )
    assert changed.returncode == EXIT_STATE_ERROR
    assert "different recovery metadata" in json.loads(changed.stderr)["message"]
    assert (
        parsed_stdout(run_cli(state_dir, "status"))["work_items"]["HARDEN-CI-001"]["attempts"] == 1
    )


def test_attempt_and_child_run_ids_cannot_be_reused_for_new_attempts(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir)
    assert begin(state_dir, lease_id).returncode == 0
    failed = record_result(
        state_dir,
        lease_id,
        result="FAILED",
        summary="Synthetic first attempt failure.",
    )
    assert failed.returncode == 0, failed.stderr

    reused_child = begin(
        state_dir,
        lease_id,
        attempt_id="attempt-002",
        child_run_id="run-harden-ci-001-01",
    )
    reused_attempt = begin(
        state_dir,
        lease_id,
        work_item="OBSERVABILITY-001",
        attempt_id="attempt-001",
        branch="feature/auto-dev-observability-001",
        child_run_id="run-observability-001-01",
    )

    assert reused_child.returncode == EXIT_STATE_ERROR
    assert "must match the next attempt ordinal" in json.loads(reused_child.stderr)["message"]
    assert reused_attempt.returncode == EXIT_STATE_ERROR
    assert "attempt ID is already assigned" in json.loads(reused_attempt.stderr)["message"]

    second = begin(
        state_dir,
        lease_id,
        attempt_id="attempt-002",
        child_run_id="run-harden-ci-001-02",
    )
    assert second.returncode == 0, second.stderr
    second_failed = record_result(
        state_dir,
        lease_id,
        attempt_id="attempt-002",
        result="FAILED",
        summary="Synthetic second attempt failure.",
    )
    assert second_failed.returncode == 0, second_failed.stderr
    recycled = begin(
        state_dir,
        lease_id,
        attempt_id="attempt-001",
        child_run_id="run-harden-ci-001-01",
    )
    assert recycled.returncode == EXIT_STATE_ERROR
    assert "must match the next attempt ordinal" in json.loads(recycled.stderr)["message"]


def test_new_attempt_is_blocked_until_current_attempt_is_terminal(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir)
    assert begin(state_dir, lease_id).returncode == 0

    same_item = begin(
        state_dir,
        lease_id,
        attempt_id="attempt-002",
        child_run_id="run-harden-ci-001-02",
    )
    other_item = begin(
        state_dir,
        lease_id,
        work_item="OBSERVABILITY-001",
        attempt_id="observability-attempt-001",
        branch="feature/auto-dev-observability-001",
        child_run_id="run-observability-001-01",
    )
    assert same_item.returncode == EXIT_STATE_ERROR
    assert other_item.returncode == EXIT_STATE_ERROR
    assert "non-terminal" in json.loads(other_item.stderr)["message"]


def test_attempt_limit_is_enforced_and_third_attempt_replay_does_not_increment(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir)
    for attempt in range(1, 4):
        attempt_id = f"attempt-{attempt:03d}"
        child_run_id = f"run-harden-ci-001-{attempt:02d}"
        begun = begin(
            state_dir,
            lease_id,
            attempt_id=attempt_id,
            child_run_id=child_run_id,
        )
        assert begun.returncode == 0, begun.stderr
        if attempt == 3:
            recovery = transition(
                state_dir,
                "record-recovery-required",
                lease_id,
                attempt_id=attempt_id,
            )
            assert recovery.returncode == 0, recovery.stderr
            block_commit = transition(
                state_dir,
                "record-block-commit",
                lease_id,
                attempt_id=attempt_id,
                extra=("--delivery-commit", DELIVERY_COMMIT),
            )
            assert block_commit.returncode == 0, block_commit.stderr
        terminal = record_result(
            state_dir,
            lease_id,
            attempt_id=attempt_id,
            result="FAILED" if attempt < 3 else "BLOCKED",
            summary=f"Synthetic attempt {attempt} ended safely.",
        )
        assert terminal.returncode == 0, terminal.stderr

    replay = begin(
        state_dir,
        lease_id,
        attempt_id="attempt-003",
        child_run_id="run-harden-ci-001-03",
    )
    fourth = begin(
        state_dir,
        lease_id,
        attempt_id="attempt-004",
        child_run_id="run-harden-ci-001-04",
    )
    assert replay.returncode == 0, replay.stderr
    assert parsed_stdout(replay)["attempt"] == 3
    assert fourth.returncode == EXIT_STATE_ERROR
    error = json.loads(fourth.stderr)
    assert error == {
        "attempts": 3,
        "error": "ATTEMPT_LIMIT_REACHED",
        "max_attempts": 3,
        "work_item": "HARDEN-CI-001",
    }
    current = parsed_stdout(run_cli(state_dir, "status"))
    assert current["work_items"]["HARDEN-CI-001"]["attempts"] == 3


def test_recovery_required_is_idempotent_and_terminal_is_immutable(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir)
    assert begin(state_dir, lease_id).returncode == 0
    recovery = transition(state_dir, "record-recovery-required", lease_id)
    recovery_again = transition(state_dir, "record-recovery-required", lease_id)
    assert recovery.returncode == 0, recovery.stderr
    assert parsed_stdout(recovery_again)["action"] == "RECOVERY_ALREADY_REQUIRED"
    block_commit = transition(
        state_dir,
        "record-block-commit",
        lease_id,
        extra=("--delivery-commit", DELIVERY_COMMIT),
    )
    block_commit_again = transition(
        state_dir,
        "record-block-commit",
        lease_id,
        extra=("--delivery-commit", DELIVERY_COMMIT),
    )
    assert block_commit.returncode == 0, block_commit.stderr
    assert parsed_stdout(block_commit_again)["action"] == "BLOCK_COMMIT_ALREADY_RECORDED"

    terminal = record_result(
        state_dir,
        lease_id,
        result="BLOCKED",
        summary="Deterministic child lookup was unavailable.",
    )
    assert terminal.returncode == 0, terminal.stderr
    rejected = transition(state_dir, "record-recovery-required", lease_id)
    assert rejected.returncode == EXIT_STATE_ERROR


def test_block_commit_after_task_commit_preserves_a_valid_recoverable_state(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir)
    assert begin(state_dir, lease_id).returncode == 0
    attached = transition(
        state_dir,
        "attach-child",
        lease_id,
        extra=("--child-session", "codex-thread:session-001"),
    )
    assert attached.returncode == 0, attached.stderr
    assert transition(state_dir, "begin-verification", lease_id).returncode == 0
    committed = transition(
        state_dir,
        "record-task-commit",
        lease_id,
        extra=("--task-commit", TASK_COMMIT),
    )
    assert committed.returncode == 0, committed.stderr
    assert transition(state_dir, "record-recovery-required", lease_id).returncode == 0
    blocked = transition(
        state_dir,
        "record-block-commit",
        lease_id,
        extra=("--delivery-commit", DELIVERY_COMMIT),
    )

    assert blocked.returncode == 0, blocked.stderr
    item = parsed_stdout(run_cli(state_dir, "status"))["work_items"]["HARDEN-CI-001"]
    assert item["phase"] == "BLOCK_COMMITTED"
    assert item["task_commit"] == TASK_COMMIT
    assert item["delivery_commit"] == DELIVERY_COMMIT


def test_result_cannot_skip_required_success_phases(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir)
    assert begin(state_dir, lease_id).returncode == 0
    rejected = record_result(state_dir, lease_id)
    assert rejected.returncode == EXIT_STATE_ERROR
    assert (
        "PASSED result requires DELIVERY_COMMITTED phase" in json.loads(rejected.stderr)["message"]
    )
    current = parsed_stdout(run_cli(state_dir, "status"))
    assert current["work_items"]["HARDEN-CI-001"]["phase"] == "PREPARED"


def test_passing_and_non_passing_results_are_bound_to_safe_terminal_phases(
    tmp_path: Path,
) -> None:
    recovery_dir = tmp_path / "recovery"
    recovery_lease = acquire(recovery_dir)
    assert begin(recovery_dir, recovery_lease).returncode == 0
    assert (
        transition(
            recovery_dir,
            "record-recovery-required",
            recovery_lease,
        ).returncode
        == 0
    )
    false_pass = record_result(recovery_dir, recovery_lease)
    assert false_pass.returncode == EXIT_STATE_ERROR
    assert (
        "PASSED result requires DELIVERY_COMMITTED phase"
        in json.loads(false_pass.stderr)["message"]
    )

    merged_dir = tmp_path / "merged"
    merged_lease = acquire(merged_dir)
    assert begin(merged_dir, merged_lease).returncode == 0
    assert (
        transition(
            merged_dir,
            "attach-child",
            merged_lease,
            extra=("--child-session", "codex-thread:session-result-phase"),
        ).returncode
        == 0
    )
    assert transition(merged_dir, "begin-verification", merged_lease).returncode == 0
    assert (
        transition(
            merged_dir,
            "record-task-commit",
            merged_lease,
            extra=("--task-commit", TASK_COMMIT),
        ).returncode
        == 0
    )
    assert transition(merged_dir, "record-merged", merged_lease).returncode == 0
    false_failure = record_result(
        merged_dir,
        merged_lease,
        result="FAILED",
        summary="Synthetic verification failed.",
    )
    assert false_failure.returncode == EXIT_STATE_ERROR
    assert (
        "retryable result requires a pre-merge execution phase"
        in json.loads(false_failure.stderr)["message"]
    )


def test_undeclared_result_values_are_rejected_for_new_and_persisted_state(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "new-result"
    lease_id = acquire(state_dir)
    assert begin(state_dir, lease_id).returncode == 0
    rejected = record_result(
        state_dir,
        lease_id,
        result="COMPLETE",
        summary="Synthetic undeclared result.",
    )
    assert rejected.returncode == EXIT_STATE_ERROR
    assert "result must be one of" in json.loads(rejected.stderr)["message"]

    persisted_dir = tmp_path / "persisted-result"
    write_state(
        persisted_dir,
        {
            "schema_version": 1,
            "updated_at": "2026-07-31T01:00:00Z",
            "lease": None,
            "work_items": {
                "HARDEN-CI-001": v1_item(
                    result="COMPLETE",
                    summary="Synthetic undeclared result.",
                )
            },
        },
    )
    persisted = run_cli(persisted_dir, "status")
    assert persisted.returncode == EXIT_STATE_ERROR
    assert "last_result is invalid" in json.loads(persisted.stderr)["message"]


@pytest.mark.parametrize(
    "summary",
    [
        "Authorization: Bearer synthetic-value",
        "Authorization: Basic c3ludGhldGlj",
        "key=synthetic-value",
        "password:synthetic-value",
        "secret = synthetic-value",
        "token: synthetic-value",
        "api_key=synthetic-value",
        "sk-synthetic-value",
        "aaaaaaaa.bbbbbbbb.cccccccc",
    ],
)
def test_sensitive_new_result_summaries_are_rejected(
    tmp_path: Path,
    summary: str,
) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir)
    assert begin(state_dir, lease_id).returncode == 0
    rejected = record_result(
        state_dir,
        lease_id,
        result="BLOCKED",
        summary=summary,
    )
    assert rejected.returncode == EXIT_STATE_ERROR
    assert "sensitive credential material" in json.loads(rejected.stderr)["message"]
    assert summary not in rejected.stderr


@pytest.mark.parametrize(
    "command",
    [
        ("status",),
        ("acquire", "--owner", "controller-main"),
    ],
)
def test_persisted_sensitive_summary_fails_closed_without_echoing_it(
    tmp_path: Path,
    command: tuple[str, ...],
) -> None:
    state_dir = tmp_path / "state"
    secret = "Bearer persisted-sensitive-value"
    write_state(
        state_dir,
        {
            "schema_version": 2,
            "updated_at": "2026-07-31T01:00:00Z",
            "lease": None,
            "work_items": {
                "POLICY-001": {
                    **v1_item(
                        result="FAILED",
                        summary=f"Authorization: {secret}",
                    ),
                    "phase": "TERMINAL",
                    "branch": None,
                    "base_commit": None,
                    "child_run_id": None,
                    "child_session": None,
                    "task_commit": None,
                    "delivery_commit": None,
                    "legacy_migrated": False,
                }
            },
        },
    )
    rejected = run_cli(state_dir, *command)
    assert rejected.returncode == EXIT_STATE_ERROR
    assert json.loads(rejected.stderr)["error"] == "AUTOMATION_STATE_ERROR"
    assert secret not in rejected.stderr


def test_schema_v1_completed_attempt_migrates_to_terminal(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    write_state(
        state_dir,
        {
            "schema_version": 1,
            "updated_at": "2026-07-31T01:00:00Z",
            "lease": None,
            "work_items": {
                "HARDEN-CI-001": v1_item(
                    result="PASSED",
                    summary="Legacy attempt passed.",
                )
            },
        },
    )
    current = parsed_stdout(run_cli(state_dir, "status"))
    item = current["work_items"]["HARDEN-CI-001"]
    assert current["schema_version"] == 2
    assert item["phase"] == "TERMINAL"
    assert item["branch"] is None
    assert item["child_session"] is None
    assert item["legacy_migrated"] is True


def test_schema_v2_cannot_forge_a_legacy_terminal_without_commit_metadata(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    write_state(
        state_dir,
        {
            "schema_version": 2,
            "updated_at": "2026-07-31T01:00:00Z",
            "lease": None,
            "work_items": {
                "HARDEN-CI-001": {
                    **v1_item(
                        result="PASSED",
                        summary="Forged schema-v2 terminal.",
                    ),
                    "phase": "TERMINAL",
                    "branch": None,
                    "base_commit": None,
                    "child_run_id": None,
                    "child_session": None,
                    "task_commit": None,
                    "delivery_commit": None,
                    "legacy_migrated": False,
                }
            },
        },
    )

    rejected = run_cli(state_dir, "status")
    assert rejected.returncode == EXIT_STATE_ERROR
    assert "passed without complete commit metadata" in json.loads(rejected.stderr)["message"]


def test_schema_v1_in_flight_attempt_migrates_to_recovery_required(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    write_state(
        state_dir,
        {
            "schema_version": 1,
            "updated_at": "2026-07-31T01:00:00Z",
            "lease": None,
            "work_items": {"HARDEN-CI-001": v1_item()},
        },
    )
    item = parsed_stdout(run_cli(state_dir, "status"))["work_items"]["HARDEN-CI-001"]
    assert item["phase"] == "RECOVERY_REQUIRED"
    assert item["branch"] is None
    assert item["base_commit"] is None
    assert item["child_run_id"] is None
    assert item["legacy_migrated"] is True


def test_legacy_recovery_cannot_publish_an_invalid_block_commit(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    write_state(
        state_dir,
        {
            "schema_version": 1,
            "updated_at": "2026-07-31T01:00:00Z",
            "lease": None,
            "work_items": {"HARDEN-CI-001": v1_item()},
        },
    )
    lease_id = acquire(state_dir)
    rejected = transition(
        state_dir,
        "record-block-commit",
        lease_id,
        attempt_id="legacy-attempt-001",
        extra=("--delivery-commit", DELIVERY_COMMIT),
    )

    assert rejected.returncode == EXIT_STATE_ERROR
    status_result = run_cli(state_dir, "status")
    assert status_result.returncode == 0, status_result.stderr
    item = parsed_stdout(status_result)["work_items"]["HARDEN-CI-001"]
    assert item["phase"] == "RECOVERY_REQUIRED"
    assert item["delivery_commit"] is None
    assert item["legacy_migrated"] is True


def test_schema_v1_lease_gets_stable_migrated_fencing_id(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    now = datetime.now(UTC)
    write_state(
        state_dir,
        {
            "schema_version": 1,
            "updated_at": "2026-07-31T01:00:00Z",
            "lease": {
                "owner": "legacy-controller",
                "acquired_at": (now - timedelta(minutes=30)).isoformat(),
                "expires_at": (now + timedelta(minutes=30)).isoformat(),
                "ttl_seconds": 1800,
            },
            "work_items": {},
        },
    )
    first = parsed_stdout(run_cli(state_dir, "status"))["lease"]["lease_id"]
    second = parsed_stdout(run_cli(state_dir, "status"))["lease"]["lease_id"]
    assert first == second
    renewed = run_cli(
        state_dir,
        "acquire",
        "--owner",
        "legacy-controller",
        "--lease-id",
        first,
        "--ttl-seconds",
        "1800",
    )
    assert renewed.returncode == 0, renewed.stderr
    assert parsed_stdout(renewed)["lease_id"] == first
    persisted = parsed_stdout(run_cli(state_dir, "status"))
    assert persisted["schema_version"] == 2
    assert persisted["lease"]["lease_id"] == first


def test_attempts_above_limit_fail_closed_on_load(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    item = v1_item(
        attempts=4,
        attempt_id="attempt-004",
        result="FAILED",
        summary="Synthetic legacy failure.",
    )
    write_state(
        state_dir,
        {
            "schema_version": 2,
            "updated_at": "2026-07-31T01:00:00Z",
            "lease": None,
            "work_items": {
                "HARDEN-CI-001": {
                    **item,
                    "phase": "TERMINAL",
                    "branch": None,
                    "base_commit": None,
                    "child_run_id": None,
                    "child_session": None,
                    "task_commit": None,
                    "delivery_commit": None,
                    "legacy_migrated": False,
                }
            },
        },
    )
    rejected = run_cli(state_dir, "status")
    assert rejected.returncode == EXIT_STATE_ERROR
    assert "attempts is invalid" in json.loads(rejected.stderr)["message"]


def test_persisted_third_retryable_result_fails_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    item = v1_item(
        attempts=3,
        attempt_id="attempt-003",
        result="FAILED",
        summary="Synthetic third failure.",
    )
    write_state(
        state_dir,
        {
            "schema_version": 2,
            "updated_at": "2026-07-31T01:00:00Z",
            "lease": None,
            "work_items": {
                "HARDEN-CI-001": {
                    **item,
                    "phase": "TERMINAL",
                    "branch": "feature/auto-dev-harden-ci-001",
                    "base_commit": BASE_COMMIT,
                    "child_run_id": "run-harden-ci-001-03",
                    "child_session": None,
                    "task_commit": None,
                    "delivery_commit": None,
                    "legacy_migrated": False,
                }
            },
        },
    )

    rejected = run_cli(state_dir, "status")
    assert rejected.returncode == EXIT_STATE_ERROR
    assert "exhausted retry budget" in json.loads(rejected.stderr)["message"]


def test_more_than_one_nonterminal_attempt_fails_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    prepared = {
        **v1_item(),
        "phase": "PREPARED",
        "branch": "feature/one",
        "base_commit": BASE_COMMIT,
        "child_run_id": "run-one",
        "child_session": None,
        "task_commit": None,
        "delivery_commit": None,
        "legacy_migrated": False,
    }
    write_state(
        state_dir,
        {
            "schema_version": 2,
            "updated_at": "2026-07-31T01:00:00Z",
            "lease": None,
            "work_items": {
                "HARDEN-CI-001": prepared,
                "OBSERVABILITY-001": {
                    **prepared,
                    "branch": "feature/two",
                    "child_run_id": "run-two",
                },
            },
        },
    )
    rejected = run_cli(state_dir, "status")
    assert rejected.returncode == EXIT_STATE_ERROR
    assert "more than one non-terminal" in json.loads(rejected.stderr)["message"]


def test_atomic_state_files_have_no_orphaned_temporary_files(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    lease_id = acquire(state_dir)
    assert begin(state_dir, lease_id).returncode == 0
    recovery = transition(state_dir, "record-recovery-required", lease_id)
    assert recovery.returncode == 0, recovery.stderr
    block_commit = transition(
        state_dir,
        "record-block-commit",
        lease_id,
        extra=("--delivery-commit", DELIVERY_COMMIT),
    )
    assert block_commit.returncode == 0, block_commit.stderr
    terminal = record_result(
        state_dir,
        lease_id,
        result="BLOCKED",
        summary="Synthetic recovery path ended safely.",
    )
    assert terminal.returncode == 0, terminal.stderr
    state_text = (state_dir / STATE_JSON_NAME).read_text(encoding="utf-8")
    assert json.loads(state_text)["schema_version"] == 2
    assert not list(state_dir.glob("*.tmp"))


def test_state_mutex_times_out_instead_of_waiting_forever(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    holder_code = """
import sys
import time
from pathlib import Path
from scripts.manage_automation_state import _state_mutex

with _state_mutex(Path(sys.argv[1])):
    print("LOCKED", flush=True)
    time.sleep(1.5)
"""
    contender_code = """
import sys
from pathlib import Path
from scripts.manage_automation_state import AutomationStateError, _state_mutex

try:
    with _state_mutex(Path(sys.argv[1]), timeout_seconds=0.2):
        pass
except AutomationStateError as error:
    print(str(error))
    raise SystemExit(4)
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(state_dir)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "LOCKED"

    started = time.monotonic()
    contender = subprocess.run(
        [sys.executable, "-c", contender_code, str(state_dir)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    elapsed = time.monotonic() - started
    holder_stdout, holder_stderr = holder.communicate(timeout=5)

    assert holder.returncode == 0, f"{holder_stdout}\n{holder_stderr}"
    assert contender.returncode == EXIT_STATE_ERROR
    assert "timed out acquiring automation state mutex" in contender.stdout
    assert elapsed < 2
