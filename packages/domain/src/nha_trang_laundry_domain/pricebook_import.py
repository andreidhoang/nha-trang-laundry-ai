"""Strict one-way importer from the approved v1 pricebook CSV into typed canonical records."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any, Final

import rfc8785

from nha_trang_laundry_domain.catalog import (
    PriceResolution,
    PriceRuleType,
    ServiceDefinition,
    ServiceRegistry,
    Unit,
    standard_wash_aliases,
)
from nha_trang_laundry_domain.pricing import PriceRule, PriceTier

EXPECTED_HEADERS: Final = (
    "service_id",
    "category",
    "service_name",
    "unit",
    "min_price_vnd",
    "max_price_vnd",
    "pricing_rule",
    "agent_permission",
    "status",
    "source_version",
)
EXPECTED_SOURCE_ROWS = 44
EXPECTED_SERVICE_COUNT = 43
EXPECTED_RULE_COUNT = 43
EXPECTED_NON_TIER_RULE_COUNT = 42
EXPECTED_TIER_COUNT = 2
STANDARD_SOURCE_CODES: Final = frozenset({"STD_WASH_DRY_LT6", "STD_WASH_DRY_GE6"})
UNIT_BY_SOURCE: Final = {
    "kg": Unit.KG,
    "cái": Unit.ITEM,
    "đôi": Unit.PAIR,
    "bộ": Unit.SET,
    "con": Unit.ANIMAL_PLUSH_ITEM,
    "m2": Unit.M2,
    "trường hợp": Unit.CASE,
}
RESOLUTION_BY_PERMISSION: Final = {
    "AUTO_ESTIMATE": PriceResolution.AUTO_FIXED,
    "LIST_PRICE_ONLY": PriceResolution.AUTO_FIXED,
    "RANGE_ONLY_HUMAN_FINAL": PriceResolution.SHOW_RANGE,
}


class PricebookImportError(ValueError):
    """Raised when source evidence cannot produce the one approved canonical snapshot."""


@dataclass(frozen=True)
class ImportedPriceRule:
    service_code: str
    unit: Unit
    rule_type: PriceRuleType
    resolution: PriceResolution
    min_unit_price_vnd: int | None
    max_unit_price_vnd: int | None
    source_rows: tuple[int, ...]


@dataclass(frozen=True)
class ImportedPriceTier:
    service_code: str
    minimum_quantity: str
    maximum_quantity_exclusive: str | None
    unit_price_vnd: int
    minimum_billable_quantity: str | None


@dataclass(frozen=True)
class PricebookImportManifest:
    source_path: str
    source_version: str
    source_sha256: str
    canonical_snapshot_hash: str
    source_row_count: int
    canonical_service_count: int
    canonical_rule_count: int
    non_tier_rule_count: int
    tier_count: int


@dataclass(frozen=True)
class CanonicalPricebook:
    services: tuple[ServiceDefinition, ...]
    rules: tuple[ImportedPriceRule, ...]
    tiers: tuple[ImportedPriceTier, ...]
    manifest: PricebookImportManifest


def canonical_pricebook_payload(pricebook: CanonicalPricebook) -> dict[str, Any]:
    """Return the exact document whose digest is `manifest.canonical_snapshot_hash`.

    A quote revision references a published pricebook by version and hash, so something has to
    publish one. Exposing the hashed document itself — rather than a second, similar document
    assembled at publication time — is what makes that reference verifiable: a publisher can assert
    that what it stored hashes to what the importer computed, and a reader can re-run the check.
    """
    return _snapshot(pricebook.services, pricebook.rules, pricebook.tiers)


def published_price_rules(payload: Mapping[str, Any]) -> dict[str, PriceRule]:
    """Rebuild runtime price rules from a published pricebook document.

    This is how a running process gets prices. It deliberately does not read the CSV: the CSV is
    source evidence for an import that a human ran and approved, and a service that re-reads it at
    request time would be pricing against a file rather than against the thing that was published.
    The API image does not even ship `templates/`, which is the correct shape rather than an
    oversight — there is one runtime source of truth and it lives in the database.

    The reconstruction is checked rather than trusted. After parsing, the typed records are
    re-serialized through the same `_snapshot` the importer hashed, and the bytes must match the
    payload exactly. A field this parser silently dropped, reordered or coerced would change those
    bytes, so a lossless round trip is the evidence that the rules in memory are the rules that were
    approved. Anything else raises, and an unresolved pricebook means no quote.
    """
    try:
        services = tuple(
            ServiceDefinition(
                code=str(item["code"]),
                display_name=str(item["display_name"]),
                category=str(item["category"]),
                unit=Unit(str(item["unit"])),
            )
            for item in _sequence(payload, "services")
        )
        rules = tuple(
            ImportedPriceRule(
                service_code=str(item["service_code"]),
                unit=Unit(str(item["unit"])),
                rule_type=PriceRuleType(str(item["rule_type"])),
                resolution=PriceResolution(str(item["resolution"])),
                min_unit_price_vnd=_optional_int(item["min_unit_price_vnd"]),
                max_unit_price_vnd=_optional_int(item["max_unit_price_vnd"]),
                source_rows=tuple(int(row) for row in item["source_rows"]),
            )
            for item in _sequence(payload, "rules")
        )
        tiers = tuple(
            ImportedPriceTier(
                service_code=str(item["service_code"]),
                minimum_quantity=str(item["minimum_quantity"]),
                maximum_quantity_exclusive=_optional_text(item["maximum_quantity_exclusive"]),
                unit_price_vnd=int(item["unit_price_vnd"]),
                minimum_billable_quantity=_optional_text(item["minimum_billable_quantity"]),
            )
            for item in _sequence(payload, "tiers")
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PricebookImportError(
            "published pricebook payload is not a canonical pricebook"
        ) from (error)
    _require_counts(services, rules, tiers)
    if rfc8785.dumps(_snapshot(services, rules, tiers)) != rfc8785.dumps(dict(payload)):
        raise PricebookImportError("published pricebook did not survive a lossless round trip")
    return _rules_from_records(rules, tiers)


def _sequence(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise PricebookImportError(f"published pricebook {key} is not a list of objects")
    return list(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def runtime_price_rules(pricebook: CanonicalPricebook) -> dict[str, PriceRule]:
    """Build immutable-runtime rule inputs from a previously validated canonical snapshot."""
    return _rules_from_records(pricebook.rules, pricebook.tiers)


def _rules_from_records(
    imported_rules: tuple[ImportedPriceRule, ...], imported_tiers: tuple[ImportedPriceTier, ...]
) -> dict[str, PriceRule]:
    tiers_by_service: dict[str, list[PriceTier]] = {}
    for tier in imported_tiers:
        tiers_by_service.setdefault(tier.service_code, []).append(
            PriceTier(
                Decimal(tier.minimum_quantity),
                Decimal(tier.maximum_quantity_exclusive)
                if tier.maximum_quantity_exclusive is not None
                else None,
                tier.unit_price_vnd,
                Decimal(tier.minimum_billable_quantity)
                if tier.minimum_billable_quantity is not None
                else None,
            )
        )
    runtime: dict[str, PriceRule] = {}
    for rule in imported_rules:
        runtime[rule.service_code] = PriceRule(
            service_code=rule.service_code,
            rule_type=rule.rule_type,
            unit_price_vnd=(
                rule.min_unit_price_vnd
                if rule.rule_type in {PriceRuleType.FIXED_PER_UNIT, PriceRuleType.FIXED_PER_ORDER}
                else None
            ),
            range_min_vnd=(
                rule.min_unit_price_vnd if rule.rule_type is PriceRuleType.RANGE_PER_UNIT else None
            ),
            range_max_vnd=(
                rule.max_unit_price_vnd if rule.rule_type is PriceRuleType.RANGE_PER_UNIT else None
            ),
            tiers=tuple(tiers_by_service.get(rule.service_code, [])),
            unit=rule.unit,
        )
    return runtime


def import_pricebook_csv(
    source: bytes, *, source_path: str = "templates/services-pricebook.csv"
) -> CanonicalPricebook:
    rows = _read_rows(source)
    if len(rows) != EXPECTED_SOURCE_ROWS:
        raise PricebookImportError(f"expected {EXPECTED_SOURCE_ROWS} source rows")
    if len({row["service_id"] for row in rows}) != len(rows):
        raise PricebookImportError("source service IDs must be unique")

    services: dict[str, ServiceDefinition] = {}
    rules: dict[str, ImportedPriceRule] = {}
    standard_rows: dict[str, tuple[int, dict[str, str]]] = {}
    for row_number, row in enumerate(rows, start=2):
        _require_source_metadata(row)
        source_code = row["service_id"]
        if source_code in STANDARD_SOURCE_CODES:
            standard_rows[source_code] = (row_number, row)
            continue
        service = _service(row)
        if service.code in services:
            raise PricebookImportError("canonical service code collision")
        services[service.code] = service
        minimum = _positive_vnd(row["min_price_vnd"])
        maximum = _positive_vnd(row["max_price_vnd"])
        if minimum > maximum:
            raise PricebookImportError("minimum price exceeds maximum price")
        permission = _resolution(row["agent_permission"])
        rule_type = (
            PriceRuleType.FIXED_PER_UNIT if minimum == maximum else PriceRuleType.RANGE_PER_UNIT
        )
        if rule_type is PriceRuleType.FIXED_PER_UNIT and permission is PriceResolution.SHOW_RANGE:
            raise PricebookImportError("fixed source price cannot use range permission")
        if (
            rule_type is PriceRuleType.RANGE_PER_UNIT
            and permission is not PriceResolution.SHOW_RANGE
        ):
            raise PricebookImportError("range source price must require human final selection")
        rules[service.code] = ImportedPriceRule(
            service.code,
            service.unit,
            rule_type,
            permission,
            minimum,
            maximum,
            (row_number,),
        )

    standard_service, standard_rule, tiers = _standard_wash(standard_rows)
    services[standard_service.code] = standard_service
    rules[standard_rule.service_code] = standard_rule
    ordered_services = tuple(services[code] for code in sorted(services))
    ordered_rules = tuple(rules[code] for code in sorted(rules))
    ServiceRegistry(ordered_services, standard_wash_aliases())
    _require_counts(ordered_services, ordered_rules, tiers)
    snapshot = _snapshot(ordered_services, ordered_rules, tiers)
    source_versions = {row["source_version"] for row in rows}
    if source_versions != {"PRICEBOOK_V1"}:
        raise PricebookImportError("source version must be exactly PRICEBOOK_V1")
    manifest = PricebookImportManifest(
        source_path=source_path,
        source_version="PRICEBOOK_V1",
        source_sha256=sha256(source).hexdigest(),
        canonical_snapshot_hash=f"JCS-SHA256-V1:{sha256(rfc8785.dumps(snapshot)).hexdigest()}",
        source_row_count=len(rows),
        canonical_service_count=len(ordered_services),
        canonical_rule_count=len(ordered_rules),
        non_tier_rule_count=sum(
            rule.rule_type is not PriceRuleType.AGGREGATE_TIER_PER_UNIT for rule in ordered_rules
        ),
        tier_count=len(tiers),
    )
    return CanonicalPricebook(ordered_services, ordered_rules, tiers, manifest)


def _read_rows(source: bytes) -> list[dict[str, str]]:
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise PricebookImportError("pricebook must be UTF-8") from error
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != EXPECTED_HEADERS:
        raise PricebookImportError("pricebook headers do not match the v1 import contract")
    rows: list[dict[str, str]] = []
    for row in reader:
        if None in row or any(not isinstance(row.get(header), str) for header in EXPECTED_HEADERS):
            raise PricebookImportError("pricebook row has missing or extra fields")
        rows.append({header: str(row[header]).strip() for header in EXPECTED_HEADERS})
    return rows


def _require_source_metadata(row: dict[str, str]) -> None:
    if row["status"] != "OWNER_CONFIRMED" or row["source_version"] != "PRICEBOOK_V1":
        raise PricebookImportError("pricebook row is not owner-confirmed PRICEBOOK_V1")
    if not row["pricing_rule"]:
        raise PricebookImportError("pricing-rule provenance text is required but never executed")


def _service(row: dict[str, str]) -> ServiceDefinition:
    try:
        unit = UNIT_BY_SOURCE[row["unit"]]
    except KeyError as error:
        raise PricebookImportError("source unit has no approved canonical mapping") from error
    return ServiceDefinition(row["service_id"], row["service_name"], row["category"], unit)


def _positive_vnd(value: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise PricebookImportError("source VND must contain base-10 integer digits")
    amount = int(value)
    if amount <= 0:
        raise PricebookImportError("source VND must be positive")
    return amount


def _resolution(permission: str) -> PriceResolution:
    try:
        return RESOLUTION_BY_PERMISSION[permission]
    except KeyError as error:
        raise PricebookImportError("unknown source agent permission") from error


def _standard_wash(
    rows: dict[str, tuple[int, dict[str, str]]],
) -> tuple[ServiceDefinition, ImportedPriceRule, tuple[ImportedPriceTier, ...]]:
    if set(rows) != STANDARD_SOURCE_CODES:
        raise PricebookImportError("both standard-wash source tiers are required")
    low_number, low = rows["STD_WASH_DRY_LT6"]
    high_number, high = rows["STD_WASH_DRY_GE6"]
    low_prices = (_positive_vnd(low["min_price_vnd"]), _positive_vnd(low["max_price_vnd"]))
    high_prices = (
        _positive_vnd(high["min_price_vnd"]),
        _positive_vnd(high["max_price_vnd"]),
    )
    expected = (
        low["unit"] == high["unit"] == "kg"
        and low["category"] == high["category"]
        and low["agent_permission"] == high["agent_permission"] == "AUTO_ESTIMATE"
        and low_prices == (25_000, 25_000)
        and high_prices == (20_000, 20_000)
    )
    if not expected:
        raise PricebookImportError("standard-wash source tiers drifted from the approved rule")
    service = ServiceDefinition(
        "STANDARD_WASH_DRY", "Giặt sấy tiêu chuẩn", low["category"], Unit.KG
    )
    rule = ImportedPriceRule(
        service.code,
        Unit.KG,
        PriceRuleType.AGGREGATE_TIER_PER_UNIT,
        PriceResolution.AUTO_FIXED,
        None,
        None,
        (low_number, high_number),
    )
    tiers = (
        ImportedPriceTier(service.code, "0", "6", 25_000, "1"),
        ImportedPriceTier(service.code, "6", None, 20_000, None),
    )
    return service, rule, tiers


def _require_counts(
    services: tuple[ServiceDefinition, ...],
    rules: tuple[ImportedPriceRule, ...],
    tiers: tuple[ImportedPriceTier, ...],
) -> None:
    non_tier = sum(rule.rule_type is not PriceRuleType.AGGREGATE_TIER_PER_UNIT for rule in rules)
    if (
        len(services),
        len(rules),
        non_tier,
        len(tiers),
    ) != (
        EXPECTED_SERVICE_COUNT,
        EXPECTED_RULE_COUNT,
        EXPECTED_NON_TIER_RULE_COUNT,
        EXPECTED_TIER_COUNT,
    ):
        raise PricebookImportError("canonical pricebook counts do not match the approved manifest")


def _snapshot(
    services: tuple[ServiceDefinition, ...],
    rules: tuple[ImportedPriceRule, ...],
    tiers: tuple[ImportedPriceTier, ...],
) -> dict[str, Any]:
    return {
        "aliases": [
            {"source_code": alias.source_code, "canonical_code": alias.canonical_code}
            for alias in standard_wash_aliases()
        ],
        "rules": [
            {
                "service_code": rule.service_code,
                "unit": rule.unit.value,
                "rule_type": rule.rule_type.value,
                "resolution": rule.resolution.value,
                "min_unit_price_vnd": rule.min_unit_price_vnd,
                "max_unit_price_vnd": rule.max_unit_price_vnd,
                "source_rows": list(rule.source_rows),
            }
            for rule in rules
        ],
        "services": [
            {
                "code": service.code,
                "display_name": service.display_name,
                "category": service.category,
                "unit": service.unit.value,
            }
            for service in services
        ],
        "tiers": [
            {
                "service_code": tier.service_code,
                "minimum_quantity": tier.minimum_quantity,
                "maximum_quantity_exclusive": tier.maximum_quantity_exclusive,
                "unit_price_vnd": tier.unit_price_vnd,
                "minimum_billable_quantity": tier.minimum_billable_quantity,
            }
            for tier in tiers
        ],
    }
