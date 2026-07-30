from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_evals import load_synthetic_fixture
from nha_trang_laundry_evals.synthetic_consent import execute_stop_outbox_race_preflight

ROOT = Path(__file__).resolve().parents[3]


def test_stop_commit_denies_claimed_marketing_send_before_provider_attempt() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:marketing_outbox_claim_in_progress:v1",
        version=1,
        payload_path="fixtures/marketing_outbox_claim_in_progress/v1.json",
        payload_sha256=("sha256:be09cb636af02c5d70c045ba86d43c8e11a069505b1010e876a5512705e3ecff"),
    )
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        preflight = execute_stop_outbox_race_preflight(connection, fixture)

    assert preflight.suppression_persisted_before_model_invocation is True
    assert preflight.final_send_authorization_denied is True
    assert preflight.provider_attempted is False
