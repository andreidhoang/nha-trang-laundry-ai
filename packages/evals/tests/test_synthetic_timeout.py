from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from nha_trang_laundry_evals import (
    ObservedCaseExecution,
    execute_model_timeout_preflight,
    grade_case,
    load_synthetic_fixture,
)

ROOT = Path(__file__).resolve().parents[3]


def model_timeout_case() -> dict[str, Any]:
    manifest = yaml.safe_load(
        (ROOT / "specs/evals/eval-manifest-v1.yaml").read_text(encoding="utf-8")
    )
    return next(case for case in manifest["cases"] if case["id"] == "P0-MODEL-TIMEOUT")


def test_model_timeout_fixture_runs_a_scaled_deadline_and_remains_recoverable() -> None:
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:model_wall_clock_exceeds_20_seconds:v1",
        version=1,
        payload_path="fixtures/model_wall_clock_exceeds_20_seconds/v1.json",
        payload_sha256="sha256:908c745c83be205f9fd2557b0b99f8f3c10e09b6b98d10f806ddb84a7df2c69c",
    )

    preflight = execute_model_timeout_preflight(fixture)

    assert preflight.timed_out_within_limit is True
    assert preflight.automatic_fallback_message_created is False
    assert preflight.inbox_event_recoverable is True


def test_model_timeout_synthetic_preflight_is_not_primary_p0_evidence() -> None:
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:model_wall_clock_exceeds_20_seconds:v1",
        version=1,
        payload_path="fixtures/model_wall_clock_exceeds_20_seconds/v1.json",
        payload_sha256="sha256:908c745c83be205f9fd2557b0b99f8f3c10e09b6b98d10f806ddb84a7df2c69c",
    )
    preflight = execute_model_timeout_preflight(fixture)
    grade = grade_case(
        model_timeout_case(),
        ObservedCaseExecution(
            policy_outcome="REQUIRE_HUMAN",
            message_kind=None,
            send_eligible=False,
            tool_trace=(),
            side_effects=(),
            trace_id=preflight.trace_id,
            timed_out_within_limit=preflight.timed_out_within_limit,
            automatic_fallback_message_created=preflight.automatic_fallback_message_created,
            inbox_event_recoverable=preflight.inbox_event_recoverable,
        ),
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )

    assert grade.status == "SKIP"
    assert {result.grader_id: result.passed for result in grade.grader_results} == {
        "trace": True,
        "safety": True,
        "runtime_path": False,
    }
