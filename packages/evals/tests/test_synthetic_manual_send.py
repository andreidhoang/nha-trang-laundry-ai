from __future__ import annotations

import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_evals import (
    ObservedCaseExecution,
    execute_manual_worker_double_send_preflight,
    grade_case,
    load_synthetic_fixture,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _fixture() -> Any:
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in registry["fixtures"]
        if item["fixture_id"] == "fixture:manual_send_attestation_exists_then_worker_claim:v1"
    )
    return load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id=entry["fixture_id"],
        version=entry["version"],
        payload_path=entry["payload_path"],
        payload_sha256=entry["payload_sha256"],
    )


def _case() -> dict[str, Any]:
    manifest = yaml.safe_load(
        (ROOT / "specs/evals/eval-manifest-v1.yaml").read_text(encoding="utf-8")
    )
    return next(case for case in manifest["cases"] if case["id"] == "P0-MANUAL-WORKER-DOUBLE-SEND")


def test_manual_attestation_excludes_worker_execution_in_postgres(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    preflight = execute_manual_worker_double_send_preflight(postgres_connection, _fixture())

    assert preflight.worker_execution_rejected_by_exclusive_token is True
    assert preflight.exactly_one_execution_path_recorded is True
    assert preflight.manual_send_recorded is True
    assert preflight.provider_attempted is False


def test_manual_worker_preflight_is_non_release_skip(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    preflight = execute_manual_worker_double_send_preflight(postgres_connection, _fixture())
    grade = grade_case(
        _case(),
        ObservedCaseExecution(
            policy_outcome="DENY",
            message_kind="APPROVED_QUOTE_PRESENTATION",
            send_eligible=False,
            tool_trace=(),
            side_effects=(),
            trace_id=preflight.trace_id,
            provider_attempted=preflight.provider_attempted,
            worker_execution_rejected_by_exclusive_token=(
                preflight.worker_execution_rejected_by_exclusive_token
            ),
            exactly_one_execution_path_recorded=preflight.exactly_one_execution_path_recorded,
            manual_send_recorded=preflight.manual_send_recorded,
        ),
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )

    assert grade.status == "SKIP"
    assert {result.grader_id: result.passed for result in grade.grader_results} == {
        "trace": True,
        "safety": True,
        "runtime_path": False,
    }
