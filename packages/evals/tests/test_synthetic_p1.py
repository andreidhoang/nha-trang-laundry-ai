from __future__ import annotations

import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_evals import load_synthetic_fixture
from nha_trang_laundry_evals.synthetic_p1 import (
    execute_bound_intake_preflight,
    execute_list_price_preflight,
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


def _fixture(fixture_id: str) -> Any:
    registry = json.loads(
        (ROOT / "specs/evals/fixture-registry-v1.json").read_text(encoding="utf-8")
    )
    entry = next(item for item in registry["fixtures"] if item["fixture_id"] == fixture_id)
    return load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id=fixture_id,
        version=entry["version"],
        payload_path=entry["payload_path"],
        payload_sha256=entry["payload_sha256"],
    )


def test_list_price_assertions_come_from_published_pricebook_template() -> None:
    result = execute_list_price_preflight(
        _fixture("fixture:current_published_standard_wash_list_price:v1")
    )
    assert all(result.assertion_results.values())


def test_bound_intake_assertions_come_from_atomic_postgres_state(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    result = execute_bound_intake_preflight(
        postgres_connection, _fixture("fixture:contact_bound_conversation:v1")
    )
    assert all(result.assertion_results.values())
