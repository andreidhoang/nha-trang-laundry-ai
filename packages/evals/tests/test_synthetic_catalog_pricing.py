from pathlib import Path

from nha_trang_laundry_evals import load_synthetic_fixture
from nha_trang_laundry_evals.synthetic_catalog_pricing import (
    execute_range_no_selection_preflight,
    execute_sheet_ambiguity_preflight,
)

ROOT = Path(__file__).resolve().parents[3]


def test_range_price_requires_human_and_has_no_accepted_final_revision() -> None:
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:bound_pillow_without_staff_selection:v1",
        version=1,
        payload_path="fixtures/bound_pillow_without_staff_selection/v1.json",
        payload_sha256=("sha256:0b7d10bc41d62db6b70db4e26fb69b29d0c4e98009dcd558832df79d8ae8f9db"),
    )
    result = execute_range_no_selection_preflight(fixture)
    assert all(result.assertion_results.values())


def test_sheet_washing_query_does_not_select_drying_or_invent_a_price() -> None:
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id="fixture:bound_sheet_washing_request:v1",
        version=1,
        payload_path="fixtures/bound_sheet_washing_request/v1.json",
        payload_sha256=("sha256:77c421ebd28b5cead2ff5e638b6d636782c77551ec0fcd317ff21f25fcfa054a"),
    )
    result = execute_sheet_ambiguity_preflight(fixture)
    assert all(result.assertion_results.values())
    assert result.side_effects == ()
