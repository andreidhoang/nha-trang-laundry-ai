from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_evals import load_synthetic_fixture
from nha_trang_laundry_evals.synthetic_consent import execute_ambiguous_opt_out_preflight

ROOT = Path(__file__).resolve().parents[3]


def test_ambiguous_opt_out_blocks_marketing_and_records_review_evidence() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:active_marketing_consent:v1",
        version=1,
        payload_path="fixtures/active_marketing_consent/v1.json",
        payload_sha256=("sha256:2ba6982d32eebca6376b30f1ae6267b5ab0dc5ccdf8023cdbdb8a78635adc610"),
    )
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        preflight = execute_ambiguous_opt_out_preflight(connection, fixture)
    assert preflight.marketing_blocked_immediately is True
    assert preflight.human_consent_review_opened is True
