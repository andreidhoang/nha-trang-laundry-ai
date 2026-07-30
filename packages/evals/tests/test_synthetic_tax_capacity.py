from pathlib import Path

from nha_trang_laundry_evals import load_synthetic_fixture
from nha_trang_laundry_evals.synthetic_capacity import execute_capacity_preflight
from nha_trang_laundry_evals.synthetic_quote_lifecycle import (
    execute_tax_unverified_preflight,
)

ROOT = Path(__file__).resolve().parents[3]

CASES = (
    (
        "fixture:exact_quote_with_tax_treatment_unverified:v1",
        "fixtures/exact_quote_with_tax_treatment_unverified/v1.json",
        "sha256:1a51774da6132a8add74f2f9957891c52230904b91bce633fedc7e85e25e53b2",
        execute_tax_unverified_preflight,
    ),
    (
        "fixture:bound_standard_order_capacity_unknown:v1",
        "fixtures/bound_standard_order_capacity_unknown/v1.json",
        "sha256:ae990cc994be280c8c48bc36be05b84914587049c0f452f88b1a0fd81ad106bc",
        execute_capacity_preflight,
    ),
)


def test_tax_and_capacity_assertions_come_from_domain_policy() -> None:
    for fixture_id, path, digest, execute in CASES:
        fixture = load_synthetic_fixture(
            ROOT / "specs/evals",
            fixture_id=fixture_id,
            version=1,
            payload_path=path,
            payload_sha256=digest,
        )
        result = execute(fixture)
        assert all(result.assertion_results.values())
