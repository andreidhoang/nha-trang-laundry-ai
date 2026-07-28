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
    execute_post_approval_edit_preflight,
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


def _post_approval_edit_fixture() -> Any:
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in registry["fixtures"]
        if item["fixture_id"] == "fixture:approved_message_then_content_edited:v1"
    )
    return load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id=entry["fixture_id"],
        version=entry["version"],
        payload_path=entry["payload_path"],
        payload_sha256=entry["payload_sha256"],
    )


def _post_approval_edit_case() -> dict[str, Any]:
    manifest = yaml.safe_load(
        (ROOT / "specs/evals/eval-manifest-v1.yaml").read_text(encoding="utf-8")
    )
    return next(case for case in manifest["cases"] if case["id"] == "P0-POST-APPROVAL-EDIT")


def test_edited_content_cannot_claim_approved_execution_or_reach_provider_gate(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    preflight = execute_post_approval_edit_preflight(
        postgres_connection, _post_approval_edit_fixture()
    )

    assert preflight.rendered_hash_mismatch_detected is True
    assert preflight.new_revision_and_approval_required is True
    assert preflight.provider_attempted is False


def test_post_approval_edit_postgres_preflight_is_non_release_skip(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    preflight = execute_post_approval_edit_preflight(
        postgres_connection, _post_approval_edit_fixture()
    )
    grade = grade_case(
        _post_approval_edit_case(),
        ObservedCaseExecution(
            policy_outcome="DENY",
            message_kind="FREE_FORM_TRANSACTIONAL",
            send_eligible=False,
            tool_trace=(),
            side_effects=(),
            trace_id=preflight.trace_id,
            rendered_hash_mismatch_detected=preflight.rendered_hash_mismatch_detected,
            new_revision_and_approval_required=preflight.new_revision_and_approval_required,
            provider_attempted=preflight.provider_attempted,
        ),
        non_primary_reason="PRIMARY_PROVIDER_RUNTIME_NOT_EXECUTED",
    )

    assert grade.status == "SKIP"
    assert {result.grader_id: result.passed for result in grade.grader_results} == {
        "trace": True,
        "safety": True,
        "runtime_path": False,
    }
