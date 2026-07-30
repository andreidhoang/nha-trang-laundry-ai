from pathlib import Path

import pytest
from nha_trang_laundry_evals import load_synthetic_fixture
from nha_trang_laundry_evals.synthetic_delivery import execute_delivery_preflight

ROOT = Path(__file__).resolve().parents[3]

FIXTURES = (
    (
        "fixture:bound_verified_distance_2km_6kg:v1",
        "fixtures/bound_verified_distance_2km_6kg/v1.json",
        "sha256:8bc093a2c66731ba666eb9c7eded575af879d6b619faad92c280dbb9fc3b0daf",
    ),
    (
        "fixture:bound_verified_distance_6_001km:v1",
        "fixtures/bound_verified_distance_6_001km/v1.json",
        "sha256:08349d4c7817b8ecb528cbcc2df44086d765e74eda51dd8436575a4b7b4c42f6",
    ),
    (
        "fixture:bound_verified_distance_5km_20kg:v1",
        "fixtures/bound_verified_distance_5km_20kg/v1.json",
        "sha256:16c6b4fa0bf468add2988a082ca86191bdd39847b257291be61f005cff4677fb",
    ),
)


@pytest.mark.parametrize(("fixture_id", "path", "digest"), FIXTURES)
def test_delivery_boundary_assertions_come_from_domain_engine(
    fixture_id: str, path: str, digest: str
) -> None:
    fixture = load_synthetic_fixture(
        ROOT / "specs/evals",
        fixture_id=fixture_id,
        version=1,
        payload_path=path,
        payload_sha256=digest,
    )
    result = execute_delivery_preflight(fixture)
    assert all(result.assertion_results.values())
    assert result.tool_trace[0]["argument_field_names"] == ["fulfillment_mode"]
