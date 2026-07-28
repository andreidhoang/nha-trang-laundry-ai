import json
from pathlib import Path

import pytest
from nha_trang_laundry_domain.catalog import (
    DOMAIN_ENUM_REGISTRY,
    CatalogError,
    ServiceAlias,
    ServiceDefinition,
    ServiceRegistry,
    Unit,
    parse_domain_enum,
    standard_wash_aliases,
)

ROOT = Path(__file__).resolve().parents[3]


def standard_registry() -> ServiceRegistry:
    return ServiceRegistry(
        (
            ServiceDefinition(
                "STANDARD_WASH_DRY", "Giặt sấy tiêu chuẩn", "STANDARD_WEIGHT", Unit.KG
            ),
        ),
        standard_wash_aliases(),
    )


def test_domain_enum_registry_matches_canonical_contract() -> None:
    contract = json.loads(
        (ROOT / "specs/contracts/canonical-enums-v1.json").read_text(encoding="utf-8")
    )

    for name, enum_type in DOMAIN_ENUM_REGISTRY.items():
        assert [member.value for member in enum_type] == contract["enums"][name]


def test_parse_domain_enum_rejects_unknown_name_and_value() -> None:
    assert parse_domain_enum("Unit", "KG") is Unit.KG
    with pytest.raises(CatalogError, match=r"unknown canonical enum$"):
        parse_domain_enum("UnregisteredEnum", "KG")
    with pytest.raises(CatalogError, match="unknown canonical enum value"):
        parse_domain_enum("Unit", "LITER")


@pytest.mark.parametrize("legacy_code", ["STD_WASH_DRY_LT6", "STD_WASH_DRY_GE6"])
def test_confirmed_standard_wash_aliases_resolve_to_one_service(legacy_code: str) -> None:
    registry = standard_registry()

    assert registry.resolve(legacy_code, Unit.KG).code == "STANDARD_WASH_DRY"
    assert registry.service_count == 1
    assert registry.alias_count == 2


def test_unknown_or_incompatible_service_fails_closed() -> None:
    registry = standard_registry()

    with pytest.raises(CatalogError, match="AMBIGUOUS_SERVICE"):
        registry.resolve("MADE_UP_SERVICE")
    with pytest.raises(CatalogError, match="INCOMPATIBLE_UNIT"):
        registry.resolve("STANDARD_WASH_DRY", Unit.ITEM)


@pytest.mark.parametrize(
    "alias",
    [
        ServiceAlias("OWNER", "STANDARD_WASH_DRY"),
        ServiceAlias("UNKNOWN_SOURCE", "MISSING_TARGET"),
        ServiceAlias("STANDARD_WASH_DRY", "STANDARD_WASH_DRY"),
    ],
)
def test_invalid_aliases_are_rejected(alias: ServiceAlias) -> None:
    with pytest.raises(CatalogError, match="VALIDATION_ERROR"):
        ServiceRegistry(
            (
                ServiceDefinition(
                    "STANDARD_WASH_DRY", "Giặt sấy tiêu chuẩn", "STANDARD_WEIGHT", Unit.KG
                ),
            ),
            (alias,),
        )


def test_duplicate_source_alias_is_rejected_even_when_targets_match() -> None:
    alias = ServiceAlias("LEGACY_WASH", "STANDARD_WASH_DRY")
    with pytest.raises(CatalogError, match="duplicate source alias"):
        ServiceRegistry(
            (
                ServiceDefinition(
                    "STANDARD_WASH_DRY", "Giặt sấy tiêu chuẩn", "STANDARD_WEIGHT", Unit.KG
                ),
            ),
            (alias, alias),
        )


def test_service_display_name_must_already_be_unicode_nfc() -> None:
    with pytest.raises(CatalogError, match="must be non-empty NFC"):
        ServiceRegistry(
            (ServiceDefinition("TEST_SERVICE", "A\N{COMBINING ACUTE ACCENT}", "OTHER", Unit.ITEM),)
        )
