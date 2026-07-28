import json
from pathlib import Path

import pytest
from nha_trang_laundry_domain.catalog import PriceRuleType, Unit
from nha_trang_laundry_domain.pricebook_import import (
    PricebookImportError,
    import_pricebook_csv,
    runtime_price_rules,
)
from nha_trang_laundry_domain.pricing import PriceLine, price_lines

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "templates/services-pricebook.csv"


def test_import_has_exact_approved_counts_and_semantic_units() -> None:
    pricebook = import_pricebook_csv(SOURCE.read_bytes())

    assert pricebook.manifest.source_row_count == 44
    assert pricebook.manifest.canonical_service_count == 43
    assert pricebook.manifest.canonical_rule_count == 43
    assert pricebook.manifest.non_tier_rule_count == 42
    assert pricebook.manifest.tier_count == 2
    units = {service.code: service.unit for service in pricebook.services}
    assert units["OTHER_PLUSH"] is Unit.ANIMAL_PLUSH_ITEM
    assert units["OTHER_STAIN"] is Unit.CASE


def test_standard_wash_is_one_rule_with_two_source_rows_and_two_tiers() -> None:
    pricebook = import_pricebook_csv(SOURCE.read_bytes())
    rule = next(rule for rule in pricebook.rules if rule.service_code == "STANDARD_WASH_DRY")

    assert rule.rule_type is PriceRuleType.AGGREGATE_TIER_PER_UNIT
    assert rule.source_rows == (2, 3)
    assert [
        (tier.minimum_quantity, tier.maximum_quantity_exclusive) for tier in pricebook.tiers
    ] == [
        ("0", "6"),
        ("6", None),
    ]


def test_imported_snapshot_drives_runtime_pricing_without_prose_rules() -> None:
    pricebook = import_pricebook_csv(SOURCE.read_bytes())
    rules = runtime_price_rules(pricebook)

    wash = price_lines(
        rules,
        (
            PriceLine("STANDARD_WASH_DRY", "3"),
            PriceLine("STANDARD_WASH_DRY", "3"),
        ),
    )["STANDARD_WASH_DRY"]
    plush = price_lines(rules, (PriceLine("OTHER_PLUSH", "2", Unit.ANIMAL_PLUSH_ITEM),))[
        "OTHER_PLUSH"
    ]

    assert len(rules) == 43
    assert wash.list_amount_vnd == 120_000
    assert plush.list_amount_vnd is None
    assert (plush.range_min_vnd, plush.range_max_vnd) == (40_000, 400_000)


def test_import_is_byte_and_snapshot_hash_idempotent() -> None:
    first = import_pricebook_csv(SOURCE.read_bytes()).manifest
    second = import_pricebook_csv(SOURCE.read_bytes()).manifest

    assert first == second
    assert len(first.source_sha256) == 64
    assert first.canonical_snapshot_hash.startswith("JCS-SHA256-V1:")
    assert len(first.canonical_snapshot_hash.removeprefix("JCS-SHA256-V1:")) == 64


def test_import_matches_checked_in_machine_manifest() -> None:
    actual = import_pricebook_csv(SOURCE.read_bytes()).manifest
    expected = json.loads(
        (ROOT / "specs/contracts/pricebook-import-manifest-v1.json").read_text(encoding="utf-8")
    )

    assert actual.source_path == expected["source"]["path"]
    assert actual.source_version == expected["source"]["version"]
    assert actual.source_sha256 == expected["source"]["sha256"]
    assert actual.source_row_count == expected["source"]["row_count"]
    assert actual.canonical_snapshot_hash == expected["canonical"]["snapshot_hash"]
    assert actual.canonical_service_count == expected["canonical"]["service_count"]
    assert actual.canonical_rule_count == expected["canonical"]["price_rule_count"]
    assert actual.non_tier_rule_count == expected["canonical"]["non_tier_rule_count"]
    assert actual.tier_count == expected["canonical"]["tier_count"]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (b"OWNER_CONFIRMED", b"DRAFT_________", "owner-confirmed"),
        (b"STD_WASH_DRY_GE6", b"STD_WASH_DRY_BAD", "standard-wash"),
        (b",con,", b",lit,", "canonical mapping"),
    ],
)
def test_tampered_source_fails_closed(old: bytes, new: bytes, message: str) -> None:
    source = SOURCE.read_bytes().replace(old, new, 1)

    with pytest.raises(PricebookImportError, match=message):
        import_pricebook_csv(source)
