from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from nha_trang_laundry_evals import ObservedCaseExecution, grade_case, load_synthetic_fixture
from nha_trang_laundry_evals.synthetic_pricing import execute_pricing_preflight

ROOT = Path(__file__).resolve().parents[3]

CASES = (
    (
        "P0-PRICE-BOUNDARY-5_9",
        "fixture:bound_customer_estimate_5_9kg:v1",
        "fixtures/bound_customer_estimate_5_9kg/v1.json",
        "sha256:b9ec3d852332211b533eefcb8efa8a8a3875d7ff136110a44e8697cf3e6ef95c",
    ),
    (
        "P0-PRICE-BOUNDARY-6_0",
        "fixture:bound_customer_estimate_6kg:v1",
        "fixtures/bound_customer_estimate_6kg/v1.json",
        "sha256:239b808e10e71f88c6802cd287e002e45052ec6cc678afef3b7d1e52150852bc",
    ),
    (
        "P0-PRICE-MINIMUM-0_6",
        "fixture:bound_customer_estimate_0_6kg:v1",
        "fixtures/bound_customer_estimate_0_6kg/v1.json",
        "sha256:c98a1003ad4808f8aaed3fae5081ebd64ab28d18a3e60f6021c8c9d492eb4598",
    ),
)


def _case(case_id: str) -> dict[str, Any]:
    manifest = yaml.safe_load(
        (ROOT / "specs/evals/eval-manifest-v1.yaml").read_text(encoding="utf-8")
    )
    return next(item for item in manifest["cases"] if item["id"] == case_id)


@pytest.mark.parametrize(("case_id", "fixture_id", "path", "digest"), CASES)
def test_pricing_boundary_uses_domain_engines_and_grades_non_primary_skip(
    case_id: str, fixture_id: str, path: str, digest: str
) -> None:
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id=fixture_id,
        version=1,
        payload_path=path,
        payload_sha256=digest,
    )
    preflight = execute_pricing_preflight(fixture)
    grade = grade_case(
        _case(case_id),
        ObservedCaseExecution(
            policy_outcome="REQUIRE_HUMAN",
            message_kind="APPROVED_QUOTE_PRESENTATION",
            send_eligible=False,
            tool_trace=preflight.tool_trace,
            side_effects=preflight.side_effects,
            trace_id=preflight.trace_id,
            assertion_results=preflight.assertion_results,
        ),
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )
    assert grade.status == "SKIP"
    results = {item.grader_id: item.passed for item in grade.grader_results}
    assert results["schema"] is True
    assert results["trace"] is True
    assert results["exact"] is True
    if "safety" in results:
        assert results["safety"] is True
    assert results["runtime_path"] is False


def test_promotion_expiry_and_unresolved_event_are_derived_from_domain_policy() -> None:
    six_kg = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:bound_customer_estimate_6kg:v1",
        version=1,
        payload_path="fixtures/bound_customer_estimate_6kg/v1.json",
        payload_sha256=CASES[1][3],
    )
    expired = execute_pricing_preflight(
        six_kg,
        scenario="PROMO_EXPIRED",
        evaluation_at=datetime.fromisoformat("2026-09-01T00:00:00+07:00"),
    )
    assert all(expired.assertion_results.values())

    unresolved_fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:bound_request_missing_promotion_event:v1",
        version=1,
        payload_path="fixtures/bound_request_missing_promotion_event/v1.json",
        payload_sha256=("sha256:76a9a54c1f94c94141daf7ebe5f6301dba41d2918990147ff92eab0267cf1a3b"),
    )
    unresolved = execute_pricing_preflight(unresolved_fixture, scenario="PROMO_UNRESOLVED")
    assert all(unresolved.assertion_results.values())
