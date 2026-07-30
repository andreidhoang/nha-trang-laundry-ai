from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_evals import (
    ObservedCaseExecution,
    SyntheticFixtureBundle,
    execute_audit_write_failure_preflight,
    grade_case,
    load_synthetic_fixture,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_HASH = "sha256:717f7c1dac3cce651d51a8c7347d6e49cbc85b71c0b78c6e1cb1ec9124c9b611"


def _case() -> dict[str, Any]:
    manifest = yaml.safe_load(
        (ROOT / "specs/evals/eval-manifest-v1.yaml").read_text(encoding="utf-8")
    )
    return next(case for case in manifest["cases"] if case["id"] == "P0-AUDIT-WRITE-FAILURE")


def _fixture() -> SyntheticFixtureBundle:
    return load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:inject_audit_insert_failure:v1",
        version=1,
        payload_path="fixtures/inject_audit_insert_failure/v1.json",
        payload_sha256=FIXTURE_HASH,
    )


def test_audit_failure_safety_assertions_require_all_atomic_records_absent() -> None:
    observed = ObservedCaseExecution(
        policy_outcome="DENY",
        message_kind=None,
        send_eligible=False,
        tool_trace=(
            {
                "operation_id": "orderRequestRecordCustomerFacts",
                "argument_field_names": ["facts"],
                "status_code": 500,
            },
        ),
        side_effects=(),
        trace_id="synthetic-audit-write-failure-001",
        business_mutation_rolled_back=True,
        domain_event_rolled_back=True,
        required_outbox_event_rolled_back=True,
    )

    grade = grade_case(
        _case(), observed, non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED"
    )

    assert grade.status == "SKIP"
    assert {result.grader_id: result.passed for result in grade.grader_results} == {
        "trace": True,
        "safety": True,
        "runtime_path": False,
    }


def test_postgres_audit_failure_rolls_back_agent_tool_material_change() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        preflight = execute_audit_write_failure_preflight(connection, _fixture())

    assert preflight.business_mutation_rolled_back is True
    assert preflight.domain_event_rolled_back is True
    assert preflight.required_outbox_event_rolled_back is True
    assert preflight.tool_trace[0]["status_code"] == 500
