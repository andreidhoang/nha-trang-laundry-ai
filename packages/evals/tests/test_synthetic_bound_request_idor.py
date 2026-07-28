from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from nha_trang_laundry_evals import (
    ObservedCaseExecution,
    execute_bound_request_idor_preflight,
    grade_case,
)

ROOT = Path(__file__).resolve().parents[3]


def bound_request_idor_case() -> dict[str, Any]:
    manifest = yaml.safe_load(
        (ROOT / "specs/evals/eval-manifest-v1.yaml").read_text(encoding="utf-8")
    )
    return next(case for case in manifest["cases"] if case["id"] == "P0-BOUND-REQUEST-IDOR")


def test_bound_request_idor_is_rejected_before_quote_backend_or_revision() -> None:
    preflight = execute_bound_request_idor_preflight()

    assert preflight.bound_request_path_rejected is True
    assert preflight.quote_revision_created is False
    assert preflight.tool_trace[0]["operation_id"] == "quoteEstimate"
    assert preflight.tool_trace[0]["status_code"] == 403


def test_bound_request_idor_synthetic_path_is_a_non_release_skip() -> None:
    preflight = execute_bound_request_idor_preflight()
    grade = grade_case(
        bound_request_idor_case(),
        ObservedCaseExecution(
            policy_outcome="DENY",
            message_kind=None,
            send_eligible=False,
            tool_trace=preflight.tool_trace,
            side_effects=(),
            trace_id=preflight.trace_id,
            bound_request_path_rejected=preflight.bound_request_path_rejected,
            quote_revision_created=preflight.quote_revision_created,
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
