"""EVAL-SYNTHETIC-COMBINATORIAL-001: expectations come from the domain, never from a hand."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from nha_trang_laundry_evals.synthetic_combinatorial import (
    DATASET_LAYER,
    corpus_hash,
    generate_cases,
    verify_expectations,
    wrong_monetary_value_count,
)

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "specs/evals/synthetic-combinatorial-v1.json"
MINIMUM_CASES = 500


def _document() -> dict[str, Any]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_the_corpus_meets_the_declared_minimum() -> None:
    document = _document()

    assert document["case_count"] >= MINIMUM_CASES
    assert len(document["cases"]) == document["case_count"]


def test_the_manifest_inventory_never_claims_more_than_the_corpus_holds() -> None:
    """Publishing the count is blocked: `local-synthetic-suite-v1.json` hash-pins the manifest.

    Until that pin is re-established by AGENT-002, the inventory stays at zero. It may lag the
    corpus, but a number the corpus does not contain would clear a release blocker on paper.
    """
    import yaml

    manifest = yaml.safe_load((ROOT / "specs/evals/eval-manifest-v1.yaml").read_text("utf-8"))
    declared = manifest["actual_dataset_inventory"][DATASET_LAYER]

    assert declared in (0, _document()["case_count"])


def test_every_expected_value_is_reproduced_by_the_domain_engines() -> None:
    """The manifest allows zero wrong monetary values; this is that check."""
    assert wrong_monetary_value_count(_document()) == 0


def test_a_corrupted_expected_value_is_detected() -> None:
    document = _document()
    priced = next(
        case for case in document["cases"] if case["expected"]["list_amount_vnd"] is not None
    )
    priced["expected"]["list_amount_vnd"] += 1

    mismatched = verify_expectations(document)

    assert priced["id"] in mismatched


def test_the_content_hash_covers_the_cases() -> None:
    document = _document()

    assert document["content_hash"] == corpus_hash(document["cases"])

    document["cases"][0]["expected"]["finality"] = "TAMPERED"
    assert document["content_hash"] != corpus_hash(document["cases"])


def test_generation_is_deterministic() -> None:
    first = [case.as_document() for case in generate_cases()]
    second = [case.as_document() for case in generate_cases()]

    assert first == second
    assert corpus_hash(first) == _document()["content_hash"]


def test_the_six_kilogram_cliff_is_preserved_rather_than_smoothed() -> None:
    """A confirmed business rule: exactly 6 kg already takes the lower per-kilogram rate."""
    by_quantity = {
        tuple(case["input"]["quantities"]): case["expected"]["list_amount_vnd"]
        for case in _document()["cases"]
        if case["input"]["service_code"] == "STANDARD_WASH_DRY"
        and case["input"]["quantity_basis"] == "CUSTOMER_ESTIMATE"
    }

    assert by_quantity[("5.9",)] == 147_500
    assert by_quantity[("6",)] == 120_000
    assert by_quantity[("6.1",)] == 122_000


def test_every_declared_boundary_is_covered_at_below_and_above() -> None:
    boundaries = {case["input"]["boundary"] for case in _document()["cases"]}

    for anchor in ("TIER_6KG", "BILLABLE_MINIMUM"):
        for edge in ("_BELOW", "_EXACT", "_ABOVE"):
            assert f"{anchor}{edge}" in boundaries


def test_splitting_one_quantity_across_lines_prices_identically() -> None:
    """Same-service lines aggregate before tier selection; a split must not change the total."""
    cases = _document()["cases"]
    singles = {
        (
            case["input"]["service_code"],
            case["input"]["quantity_basis"],
            case["input"]["quantities"][0],
        ): case
        for case in cases
        if len(case["input"]["quantities"]) == 1
    }
    compared = 0
    for case in cases:
        quantities = case["input"]["quantities"]
        if len(quantities) < 2:
            continue
        total = sum((Decimal(value) for value in quantities), start=Decimal())
        key = (
            case["input"]["service_code"],
            case["input"]["quantity_basis"],
            format(total.normalize(), "f"),
        )
        single = singles.get(key)
        if single is None:
            continue
        assert case["expected"]["list_amount_vnd"] == single["expected"]["list_amount_vnd"]
        compared += 1
    assert compared > 0


def test_a_range_priced_service_never_claims_an_exact_amount() -> None:
    for case in _document()["cases"]:
        if case["expected"]["finality"] != "RANGE":
            continue
        assert case["expected"]["list_amount_vnd"] is None
        assert case["expected"]["requires_human"] is True


def test_no_case_carries_a_personal_identifier() -> None:
    """The suite is generated from the domain, so it must contain no customer data at all."""
    text = CORPUS.read_text(encoding="utf-8")

    assert "@" not in text
    assert not any(token in text for token in ("phone", "address", "customer_name"))


@pytest.mark.parametrize("field", ["id", "version", "severity", "capability", "dataset_layer"])
def test_every_case_carries_the_manifest_required_fields(field: str) -> None:
    assert all(field in case for case in _document()["cases"])
