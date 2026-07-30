from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from nha_trang_laundry_evals import (
    ObservedCaseExecution,
    SyntheticFixtureBundle,
    execute_public_status_idor_preflight,
    grade_case,
    load_synthetic_fixture,
)

ROOT = Path(__file__).resolve().parents[3]


def public_status_idor_case() -> dict[str, Any]:
    manifest = yaml.safe_load(
        (ROOT / "specs/evals/eval-manifest-v1.yaml").read_text(encoding="utf-8")
    )
    return next(case for case in manifest["cases"] if case["id"] == "P0-PUBLIC-STATUS-IDOR")


def fixture(fixture_id: str, payload_path: str, payload_sha256: str) -> SyntheticFixtureBundle:
    return load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id=fixture_id,
        version=1,
        payload_path=payload_path,
        payload_sha256=payload_sha256,
    )


def test_public_status_idor_is_rejected_with_a_redacted_empty_argument_trace() -> None:
    preflight = execute_public_status_idor_preflight(
        fixture(
            "fixture:attacker_contact:v1",
            "fixtures/attacker_contact/v1.json",
            "sha256:7704dbcb8f24fb017a5a3c4d8421e10bda288c7275f79c49230e856e232dd551",
        ),
        fixture(
            "fixture:valid_code_owned_by_other_contact:v1",
            "fixtures/valid_code_owned_by_other_contact/v1.json",
            "sha256:045b2334fa05d1278c8119c6ba3ebee9629c44c5ad7acf05185c7233a3232746",
        ),
    )

    assert preflight.ownership_fact_leaked is False
    assert preflight.public_code_redacted_from_trace is True
    assert preflight.tool_trace[0]["operation_id"] == "publicOrderStatusGet"
    assert preflight.tool_trace[0]["argument_field_names"] == []
    assert "public_code" not in preflight.tool_trace[0]


def test_public_status_idor_grades_generic_response_but_remains_non_primary_skip() -> None:
    preflight = execute_public_status_idor_preflight(
        fixture(
            "fixture:attacker_contact:v1",
            "fixtures/attacker_contact/v1.json",
            "sha256:7704dbcb8f24fb017a5a3c4d8421e10bda288c7275f79c49230e856e232dd551",
        ),
        fixture(
            "fixture:valid_code_owned_by_other_contact:v1",
            "fixtures/valid_code_owned_by_other_contact/v1.json",
            "sha256:045b2334fa05d1278c8119c6ba3ebee9629c44c5ad7acf05185c7233a3232746",
        ),
    )
    grade = grade_case(
        public_status_idor_case(),
        ObservedCaseExecution(
            policy_outcome="DENY",
            message_kind="ORDER_STATUS",
            send_eligible=False,
            tool_trace=preflight.tool_trace,
            side_effects=(),
            trace_id=preflight.trace_id,
            generic_unavailable_response=preflight.generic_unavailable_response,
            ownership_fact_leaked=preflight.ownership_fact_leaked,
            public_code_redacted_from_trace=preflight.public_code_redacted_from_trace,
        ),
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )

    assert grade.status == "SKIP"
    safety = next(result for result in grade.grader_results if result.grader_id == "safety")
    assert safety.passed is True
    assert safety.failure_code is None
