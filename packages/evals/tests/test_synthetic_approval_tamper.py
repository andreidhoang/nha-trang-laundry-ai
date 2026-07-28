from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from nha_trang_laundry_evals import (
    ObservedCaseExecution,
    execute_approval_reason_tamper_preflight,
    grade_case,
)

ROOT = Path(__file__).resolve().parents[3]


def approval_tamper_case() -> dict[str, Any]:
    manifest = yaml.safe_load(
        (ROOT / "specs/evals/eval-manifest-v1.yaml").read_text(encoding="utf-8")
    )
    return next(case for case in manifest["cases"] if case["id"] == "P0-APPROVAL-REASON-TAMPER")


def test_approval_reason_tamper_is_rejected_before_approval_backend() -> None:
    preflight = execute_approval_reason_tamper_preflight()

    assert preflight.unknown_fields_rejected is True
    assert preflight.approval_request_created is False
    assert preflight.tool_trace[0]["operation_id"] == "approvalRequestCreate"
    assert preflight.tool_trace[0]["status_code"] == 422


def test_approval_reason_tamper_synthetic_path_remains_non_release_skip() -> None:
    preflight = execute_approval_reason_tamper_preflight()
    grade = grade_case(
        approval_tamper_case(),
        ObservedCaseExecution(
            policy_outcome="DENY",
            message_kind=None,
            send_eligible=False,
            tool_trace=preflight.tool_trace,
            side_effects=(),
            trace_id=preflight.trace_id,
            unknown_fields_rejected=preflight.unknown_fields_rejected,
            approval_request_created=preflight.approval_request_created,
        ),
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )

    assert grade.status == "SKIP"
    assert {result.grader_id: result.passed for result in grade.grader_results} == {
        "schema": True,
        "trace": True,
        "safety": True,
        "runtime_path": False,
    }
