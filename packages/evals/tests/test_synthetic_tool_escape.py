from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml
from nha_trang_laundry_evals import (
    ObservedCaseExecution,
    build_synthetic_result,
    execute_bound_clean_request_preflight,
    grade_case,
    load_synthetic_fixture,
)

ROOT = Path(__file__).resolve().parents[3]


def prompt_injection_case() -> dict[str, Any]:
    manifest = yaml.safe_load(
        (ROOT / "specs/evals/eval-manifest-v1.yaml").read_text(encoding="utf-8")
    )
    return next(case for case in manifest["cases"] if case["id"] == "P0-PROMPT-INJECTION-TOOLS")


def test_synthetic_fixed_facade_preflight_rejects_cross_contact_path() -> None:
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:bound_clean_request:v1",
        version=1,
        payload_path="fixtures/bound_clean_request/v1.json",
        payload_sha256="sha256:daf237692eba83b32bad0c536bebd9fe95bf6e9ea9ad277139ecb1969758ec4e",
    )

    preflight = execute_bound_clean_request_preflight(fixture)

    assert preflight.cross_contact_path_rejected is True
    assert preflight.tool_trace[0]["operation_id"] == "orderRequestRecordCustomerFacts"
    assert "facts" not in preflight.tool_trace[0]
    assert preflight.tool_trace[0]["argument_field_names"] == ["facts"]


def test_tool_escape_result_is_schema_valid_but_skipped_without_semantic_primary_eval() -> None:
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:bound_clean_request:v1",
        version=1,
        payload_path="fixtures/bound_clean_request/v1.json",
        payload_sha256="sha256:daf237692eba83b32bad0c536bebd9fe95bf6e9ea9ad277139ecb1969758ec4e",
    )
    preflight = execute_bound_clean_request_preflight(fixture)
    observed = ObservedCaseExecution(
        policy_outcome="REQUIRE_HUMAN",
        message_kind="INTAKE_FACT_REQUEST",
        send_eligible=False,
        tool_trace=preflight.tool_trace,
        side_effects=(),
        trace_id=preflight.trace_id,
        cross_contact_path_rejected=preflight.cross_contact_path_rejected,
    )
    grade = grade_case(prompt_injection_case(), observed)

    assert grade.status == "SKIP"
    assert {result.grader_id: result.passed for result in grade.grader_results} == {
        "schema": True,
        "trace": True,
        "safety": True,
        "semantic_vi": False,
    }
    assert next(
        result for result in grade.grader_results if result.grader_id == "semantic_vi"
    ).failure_code == ("GRADER_UNAVAILABLE")

    result = build_synthetic_result(
        root=ROOT,
        case=prompt_injection_case(),
        observed=observed,
        grade=grade,
        release_artifacts={
            "agent_tools": ROOT / "specs/contracts/agent-tools-v1.openapi.yaml",
            "eval_manifest": ROOT / "specs/evals/eval-manifest-v1.yaml",
            "fixture_registry": ROOT / "specs/evals/fixture-registry-v1.json",
            "assertion_registry": ROOT / "specs/evals/assertion-registry-v1.json",
            "runtime_registry": ROOT / "runtime/model-registry-v1.yaml",
            "public_cell": ROOT / "runtime/openclaw/public-cell/openclaw.json5",
        },
    )

    typed_result = cast(dict[str, Any], result)
    assert typed_result["summary"] == {
        "total": 1,
        "passed": 0,
        "failed": 0,
        "skipped": 1,
        "p0_failures": 0,
    }
    assert typed_result["case_results"][0]["runtime_path"] == "DETERMINISTIC_DEGRADED"
    assert typed_result["signature"]["key_id"] == "SYNTHETIC_EPHEMERAL_NOT_RELEASE"
