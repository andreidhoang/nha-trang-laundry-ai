from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/manage_automation_state.py"
STATE_JSON_NAME = "state.json"
STATE_MARKDOWN_NAME = "state.md"
EXIT_BUSY = 3
EXIT_STATE_ERROR = 4


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


def test_acquire_is_exclusive_and_busy_has_distinct_exit_code(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    first = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "--state-dir",
            str(state_dir),
            "acquire",
            "--owner",
            "cron-a",
            "--ttl-seconds",
            "60",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "--state-dir",
            str(state_dir),
            "acquire",
            "--owner",
            "cron-b",
            "--ttl-seconds",
            "60",
        ],
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
    assert json.loads(outcomes[0][0])["action"] == "ACQUIRED"


def test_expired_lease_can_be_taken_over(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    acquired = run_cli(
        state_dir,
        "acquire",
        "--owner",
        "cron-old",
        "--ttl-seconds",
        "1",
    )
    assert acquired.returncode == 0, acquired.stderr

    time.sleep(1.1)
    takeover = run_cli(
        state_dir,
        "acquire",
        "--owner",
        "cron-new",
        "--ttl-seconds",
        "60",
    )

    assert takeover.returncode == 0, takeover.stderr
    assert parsed_stdout(takeover)["action"] == "STALE_TAKEOVER"
    current = parsed_stdout(run_cli(state_dir, "status"))
    assert current["lease"]["owner"] == "cron-new"
    assert current["lease"]["status"] == "ACTIVE"


def test_release_is_owner_checked(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    acquired = run_cli(state_dir, "acquire", "--owner", "cron-a")
    assert acquired.returncode == 0, acquired.stderr
    assert parsed_stdout(run_cli(state_dir, "status"))["lease"]["ttl_seconds"] == 3900

    rejected = run_cli(state_dir, "release", "--owner", "cron-b")
    assert rejected.returncode == EXIT_BUSY
    assert json.loads(rejected.stderr)["error"] == "LEASE_BUSY"

    released = run_cli(state_dir, "release", "--owner", "cron-a")
    assert released.returncode == 0, released.stderr
    assert parsed_stdout(released)["action"] == "RELEASED"
    assert parsed_stdout(run_cli(state_dir, "status"))["lease"] is None


def test_attempts_increment_and_result_is_recorded_in_json_and_markdown(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    acquired = run_cli(state_dir, "acquire", "--owner", "taskflow-main")
    assert acquired.returncode == 0, acquired.stderr

    first = run_cli(
        state_dir,
        "begin-attempt",
        "--owner",
        "taskflow-main",
        "--work-item",
        "HARDEN-CI-001",
        "--attempt-id",
        "attempt-001",
    )
    second = run_cli(
        state_dir,
        "begin-attempt",
        "--owner",
        "taskflow-main",
        "--work-item",
        "HARDEN-CI-001",
        "--attempt-id",
        "attempt-002",
    )
    recorded = run_cli(
        state_dir,
        "record-result",
        "--owner",
        "taskflow-main",
        "--work-item",
        "HARDEN-CI-001",
        "--attempt-id",
        "attempt-002",
        "--result",
        "PASSED",
        "--summary",
        "CI database and plugin gates passed.",
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert recorded.returncode == 0, recorded.stderr
    assert parsed_stdout(first)["attempt"] == 1
    assert parsed_stdout(second)["attempt"] == 2
    status = parsed_stdout(run_cli(state_dir, "status"))
    work_item = status["work_items"]["HARDEN-CI-001"]
    assert work_item["attempts"] == 2
    assert work_item["attempt_id"] == "attempt-002"
    assert work_item["last_result"] == "PASSED"
    assert work_item["last_summary"] == "CI database and plugin gates passed."

    markdown = (state_dir / STATE_MARKDOWN_NAME).read_text(encoding="utf-8")
    assert "# Automation state" in markdown
    assert "HARDEN-CI-001" in markdown
    assert "CI database and plugin gates passed." in markdown


def test_record_result_requires_attempt_and_active_owner(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    acquired = run_cli(state_dir, "acquire", "--owner", "cron-a")
    assert acquired.returncode == 0, acquired.stderr

    no_attempt = run_cli(
        state_dir,
        "record-result",
        "--owner",
        "cron-a",
        "--work-item",
        "OBSERVABILITY-001",
        "--attempt-id",
        "attempt-missing",
        "--result",
        "FAILED",
        "--summary",
        "No attempt was started.",
    )
    wrong_owner = run_cli(
        state_dir,
        "begin-attempt",
        "--owner",
        "cron-b",
        "--work-item",
        "OBSERVABILITY-001",
        "--attempt-id",
        "attempt-owner-check",
    )

    assert no_attempt.returncode == EXIT_STATE_ERROR
    assert json.loads(no_attempt.stderr)["error"] == "AUTOMATION_STATE_ERROR"
    assert wrong_owner.returncode == EXIT_BUSY


def test_state_files_are_atomically_replaced_and_contain_no_secret_fields(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    for attempt in range(3):
        acquired = run_cli(state_dir, "acquire", "--owner", "cron-atomic")
        assert acquired.returncode == 0, acquired.stderr
        begun = run_cli(
            state_dir,
            "begin-attempt",
            "--owner",
            "cron-atomic",
            "--work-item",
            "POLICY-001",
            "--attempt-id",
            f"attempt-{attempt + 1:03d}",
        )
        assert begun.returncode == 0, begun.stderr
        assert parsed_stdout(begun)["attempt"] == attempt + 1

    state_text = (state_dir / STATE_JSON_NAME).read_text(encoding="utf-8")
    state = json.loads(state_text)
    assert state["schema_version"] == 1
    assert state["work_items"]["POLICY-001"]["attempts"] == 3
    assert not list(state_dir.glob("*.tmp"))
    lowered = state_text.lower()
    for prohibited in ("password", "token", "credential", "api_key", "secret"):
        assert prohibited not in lowered


def test_attempt_and_result_commands_are_idempotent_and_current_attempt_bound(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    acquired = run_cli(state_dir, "acquire", "--owner", "cron-idempotent")
    assert acquired.returncode == 0, acquired.stderr

    arguments = (
        "begin-attempt",
        "--owner",
        "cron-idempotent",
        "--work-item",
        "CONTAINER-001",
        "--attempt-id",
        "container-attempt-001",
    )
    begun = run_cli(state_dir, *arguments)
    repeated_begin = run_cli(state_dir, *arguments)
    assert begun.returncode == 0, begun.stderr
    assert repeated_begin.returncode == 0, repeated_begin.stderr
    assert parsed_stdout(begun)["attempt"] == 1
    assert parsed_stdout(repeated_begin)["action"] == "ATTEMPT_ALREADY_BEGUN"
    assert parsed_stdout(repeated_begin)["attempt"] == 1

    result_arguments = (
        "record-result",
        "--owner",
        "cron-idempotent",
        "--work-item",
        "CONTAINER-001",
        "--attempt-id",
        "container-attempt-001",
        "--result",
        "PASSED",
        "--summary",
        "Synthetic container verification passed.",
    )
    recorded = run_cli(state_dir, *result_arguments)
    repeated_result = run_cli(state_dir, *result_arguments)
    assert recorded.returncode == 0, recorded.stderr
    assert repeated_result.returncode == 0, repeated_result.stderr
    assert parsed_stdout(repeated_result)["action"] == "RESULT_ALREADY_RECORDED"

    changed_result = run_cli(
        state_dir,
        "record-result",
        "--owner",
        "cron-idempotent",
        "--work-item",
        "CONTAINER-001",
        "--attempt-id",
        "container-attempt-001",
        "--result",
        "FAILED",
        "--summary",
        "Synthetic container verification failed.",
    )
    assert changed_result.returncode == EXIT_STATE_ERROR

    next_attempt = run_cli(
        state_dir,
        "begin-attempt",
        "--owner",
        "cron-idempotent",
        "--work-item",
        "CONTAINER-001",
        "--attempt-id",
        "container-attempt-002",
    )
    stale_result = run_cli(state_dir, *result_arguments)
    assert next_attempt.returncode == 0, next_attempt.stderr
    assert parsed_stdout(next_attempt)["attempt"] == 2
    assert stale_result.returncode == EXIT_STATE_ERROR
    assert "not the current attempt" in json.loads(stale_result.stderr)["message"]

    current = parsed_stdout(run_cli(state_dir, "status"))["work_items"]["CONTAINER-001"]
    assert current["attempts"] == 2
    assert current["attempt_id"] == "container-attempt-002"
    assert current["last_result"] is None


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
def test_sensitive_result_summaries_are_rejected(tmp_path: Path, summary: str) -> None:
    state_dir = tmp_path / "state"
    assert run_cli(state_dir, "acquire", "--owner", "cron-summary").returncode == 0
    begun = run_cli(
        state_dir,
        "begin-attempt",
        "--owner",
        "cron-summary",
        "--work-item",
        "SUPPLYCHAIN-001",
        "--attempt-id",
        "summary-attempt-001",
    )
    assert begun.returncode == 0, begun.stderr

    rejected = run_cli(
        state_dir,
        "record-result",
        "--owner",
        "cron-summary",
        "--work-item",
        "SUPPLYCHAIN-001",
        "--attempt-id",
        "summary-attempt-001",
        "--result",
        "BLOCKED",
        "--summary",
        summary,
    )

    assert rejected.returncode == EXIT_STATE_ERROR
    assert "sensitive credential material" in json.loads(rejected.stderr)["message"]


def test_non_sensitive_credential_status_summary_is_allowed(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    assert run_cli(state_dir, "acquire", "--owner", "cron-summary").returncode == 0
    begun = run_cli(
        state_dir,
        "begin-attempt",
        "--owner",
        "cron-summary",
        "--work-item",
        "SUPPLYCHAIN-001",
        "--attempt-id",
        "summary-attempt-allowed",
    )
    assert begun.returncode == 0, begun.stderr

    recorded = run_cli(
        state_dir,
        "record-result",
        "--owner",
        "cron-summary",
        "--work-item",
        "SUPPLYCHAIN-001",
        "--attempt-id",
        "summary-attempt-allowed",
        "--result",
        "BLOCKED",
        "--summary",
        "Provider credential unavailable; retry blocked.",
    )

    assert recorded.returncode == 0, recorded.stderr
    item = parsed_stdout(run_cli(state_dir, "status"))["work_items"]["SUPPLYCHAIN-001"]
    assert item["last_summary"] == "Provider credential unavailable; retry blocked."
