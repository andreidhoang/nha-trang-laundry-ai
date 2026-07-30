from __future__ import annotations

import json
import os
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_evals import load_synthetic_fixture
from nha_trang_laundry_evals.synthetic_quote_lifecycle import (
    SyntheticQuoteLifecyclePreflight,
    execute_estimate_acknowledgment_preflight,
    execute_measurement_change_preflight,
    execute_personalized_price_preflight,
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


@pytest.mark.parametrize(
    ("fixture_id", "execute"),
    (
        (
            "fixture:final_measurement_changes_19_9_to_20kg:v1",
            execute_measurement_change_preflight,
        ),
        (
            "fixture:customer_acknowledges_estimate:v1",
            execute_estimate_acknowledgment_preflight,
        ),
        (
            "fixture:bound_verified_distance_5km_6_1kg:v1",
            execute_personalized_price_preflight,
        ),
    ),
)
def test_quote_lifecycle_assertions_come_from_postgres_state(
    postgres_connection: psycopg.Connection[Any],
    fixture_id: str,
    execute: Callable[[Any, Any], SyntheticQuoteLifecyclePreflight],
) -> None:
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    entry = next(item for item in registry["fixtures"] if item["fixture_id"] == fixture_id)
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id=fixture_id,
        version=entry["version"],
        payload_path=entry["payload_path"],
        payload_sha256=entry["payload_sha256"],
    )

    result = execute(postgres_connection, fixture)

    assert all(result.assertion_results.values())
