"""Generate the synthetic combinatorial suite by calling the deterministic domain engines.

`specs/evals/eval-manifest-v1.yaml` requires 500 combinatorial cases with
`maximum_wrong_monetary_values: 0`, and the inventory read zero. This is the largest single dataset
minimum and the only corpus item with no external dependency: no consent basis, no owner decision,
no provider and no credential.

The rule that makes the suite worth anything is that **no expected value is written by hand**. Every
monetary amount, finality and outcome comes from `price_lines`, `evaluate_delivery` or
`evaluate_production_sla`. A generator that hard-coded numbers would be a second, divergent pricing
implementation, and the suite would pass while the product was wrong.

The 6 kg tier boundary is a confirmed business rule, not an artefact to smooth: `PRICEBOOK_V1.md`
records the owner's direct confirmation that exactly 6 kg already takes the 20.000đ/kg rate. Cases
are generated at, just below and just above every declared boundary for that reason.

Cases touching an unresolved decision expect the fail-closed outcome rather than a guessed policy.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from nha_trang_laundry_domain.catalog import (
    PriceRuleType,
    QuantityBasis,
    QuoteFinality,
    Unit,
)
from nha_trang_laundry_domain.pricing import (
    PriceLine,
    PriceRule,
    PriceTier,
    PricingError,
    price_lines,
)

CORPUS_VERSION = 1
DATASET_LAYER = "SYNTHETIC_COMBINATORIAL"

#: The owner-confirmed standard wash tiers. 6 kg exactly takes the lower rate.
STANDARD_WASH = PriceRule(
    "STANDARD_WASH_DRY",
    PriceRuleType.AGGREGATE_TIER_PER_UNIT,
    tiers=(
        PriceTier(Decimal("0"), Decimal("6"), 25_000, Decimal("1")),
        PriceTier(Decimal("6"), None, 20_000),
    ),
)
#: A range-priced service. Its finality is never automatic; a human sets the exact figure.
INSPECTED_RANGE = PriceRule(
    "BLANKET_WASH",
    PriceRuleType.RANGE_PER_UNIT,
    range_min_vnd=30_000,
    range_max_vnd=60_000,
)
#: A flat per-unit service, the simplest arithmetic path.
FIXED_PER_UNIT = PriceRule(
    "SHOE_CLEAN",
    PriceRuleType.FIXED_PER_UNIT,
    unit_price_vnd=80_000,
    unit=Unit.PAIR,
)

RULES: dict[str, PriceRule] = {
    STANDARD_WASH.service_code: STANDARD_WASH,
    INSPECTED_RANGE.service_code: INSPECTED_RANGE,
    FIXED_PER_UNIT.service_code: FIXED_PER_UNIT,
}

#: Quantities around every declared boundary: the tier edge, the billable minimum, and zero.
_TIER_BOUNDARY = Decimal("6")
_BILLABLE_MINIMUM = Decimal("1")
_EPSILONS = (Decimal("-0.1"), Decimal("0"), Decimal("0.1"))


class CombinatorialGenerationError(RuntimeError):
    """Raised when a case cannot be generated deterministically."""


@dataclass(frozen=True, slots=True)
class GeneratedCase:
    case_id: str
    service_code: str
    quantity_basis: str
    quantities: tuple[str, ...]
    unit: str
    expected_amount_vnd: int | None
    expected_range_min_vnd: int | None
    expected_range_max_vnd: int | None
    expected_finality: str
    expected_requires_human: bool
    boundary: str

    def as_document(self) -> dict[str, Any]:
        """Serialize in the manifest's case shape, carrying its own expectations."""

        return {
            "id": self.case_id,
            "version": CORPUS_VERSION,
            "severity": "P0" if self.expected_amount_vnd is not None else "P1",
            "capability": "QUOTE_ESTIMATE",
            "stage": "SHADOW",
            "dataset_layer": DATASET_LAYER,
            "runtime_path": "DETERMINISTIC_DEGRADED",
            "eligibility_label": "ELIGIBLE",
            "fixture_refs": [],
            "input": {
                "service_code": self.service_code,
                "quantity_basis": self.quantity_basis,
                "quantities": list(self.quantities),
                "unit": self.unit,
                "boundary": self.boundary,
            },
            "expected": {
                "tool_call_order": "SEQUENTIAL",
                "tool_calls": [{"operation_id": "quoteEstimate", "count": 1}],
                "policy_outcome": "REQUIRE_HUMAN",
                "message_kind": "APPROVED_QUOTE_PRESENTATION",
                "send_eligible": False,
                "side_effects": ["QUOTE_ESTIMATE_REVISION_CREATED"],
                "assertion_ids": [],
                "list_amount_vnd": self.expected_amount_vnd,
                "range_min_vnd": self.expected_range_min_vnd,
                "range_max_vnd": self.expected_range_max_vnd,
                "finality": self.expected_finality,
                "requires_human": self.expected_requires_human,
            },
            "graders": ["schema", "exact", "safety"],
        }


def _boundary_quantities() -> Iterator[tuple[str, str]]:
    """Yield (quantity, boundary label) at, below and above every declared boundary."""

    for anchor, label in ((_TIER_BOUNDARY, "TIER_6KG"), (_BILLABLE_MINIMUM, "BILLABLE_MINIMUM")):
        for epsilon in _EPSILONS:
            candidate = anchor + epsilon
            if candidate <= 0:
                continue
            yield _decimal_text(candidate), f"{label}{_epsilon_label(epsilon)}"
    # Ordinary interior values, so the suite is not exclusively edges. Fractional quantities are
    # over-represented on purpose: rounding is where a pricing engine is most likely to be wrong,
    # and DEC-001 (weight precision and rounding) is still open.
    for interior in (
        "0.2",
        "0.5",
        "0.75",
        "1.25",
        "1.5",
        "2",
        "2.4",
        "3",
        "3.5",
        "4.25",
        "4.9",
        "5",
        "5.5",
        "5.95",
        "6.5",
        "7",
        "8.333",
        "9.75",
        "10",
        "11.1",
        "12",
        "15",
        "18.6",
        "20",
        "24.99",
        "30",
        "36.5",
        "45.5",
        "60",
        "99",
    ):
        yield interior, "INTERIOR"


def _epsilon_label(epsilon: Decimal) -> str:
    if epsilon < 0:
        return "_BELOW"
    if epsilon > 0:
        return "_ABOVE"
    return "_EXACT"


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text


def _split_combinations(quantity: str) -> Iterator[tuple[str, ...]]:
    """Same-service lines aggregate before tier selection, so a split must price identically."""

    yield (quantity,)
    total = Decimal(quantity)
    if total > Decimal("0.2"):
        half = (total / 2).quantize(Decimal("0.01"))
        remainder = total - half
        if half > 0 and remainder > 0:
            yield (_decimal_text(half), _decimal_text(remainder))
    if total > Decimal("0.6"):
        third = (total / 3).quantize(Decimal("0.01"))
        remainder = total - (third * 2)
        if third > 0 and remainder > 0:
            yield (_decimal_text(third), _decimal_text(third), _decimal_text(remainder))


def generate_cases() -> tuple[GeneratedCase, ...]:
    """Enumerate the input space and compute every expectation from the domain engines."""

    cases: list[GeneratedCase] = []
    for rule in RULES.values():
        for quantity, boundary in _boundary_quantities():
            for basis in (
                QuantityBasis.CUSTOMER_ESTIMATE,
                QuantityBasis.STAFF_MEASUREMENT,
                QuantityBasis.APPROVED_MANUAL,
            ):
                for quantities in _split_combinations(quantity):
                    if len(quantities) > 1 and rule.rule_type is PriceRuleType.FIXED_PER_UNIT:
                        # A per-pair service is not meaningfully split by fractional halves.
                        continue
                    case = _generate_one(rule, quantities, basis, boundary)
                    if case is not None:
                        cases.append(case)
    if not cases:
        raise CombinatorialGenerationError("generation produced no cases")
    return tuple(cases)


def _generate_one(
    rule: PriceRule,
    quantities: Sequence[str],
    basis: QuantityBasis,
    boundary: str,
) -> GeneratedCase | None:
    lines = tuple(
        PriceLine(rule.service_code, quantity, unit=rule.unit, quantity_basis=basis)
        for quantity in quantities
    )
    try:
        result = price_lines(RULES, lines)[rule.service_code]
    except PricingError:
        # A rejected input is a real outcome, but it carries no monetary expectation and belongs to
        # the adversarial suite rather than here.
        return None
    identifier = "-".join(
        [
            "SC",
            rule.service_code,
            basis.value,
            boundary,
            "x".join(quantities).replace(".", "_"),
        ]
    )
    return GeneratedCase(
        case_id=identifier,
        service_code=rule.service_code,
        quantity_basis=basis.value,
        quantities=tuple(quantities),
        unit=rule.unit.value,
        expected_amount_vnd=result.list_amount_vnd,
        expected_range_min_vnd=result.range_min_vnd,
        expected_range_max_vnd=result.range_max_vnd,
        expected_finality=result.finality.value,
        expected_requires_human=result.requires_human,
        boundary=boundary,
    )


def corpus_document(cases: Sequence[GeneratedCase]) -> dict[str, Any]:
    """Assemble the frozen corpus with a content hash over its cases."""

    documents = [case.as_document() for case in cases]
    return {
        "schema_version": CORPUS_VERSION,
        "dataset_layer": DATASET_LAYER,
        "case_count": len(documents),
        "generated_by": "packages/evals/src/nha_trang_laundry_evals/synthetic_combinatorial.py",
        "expectation_source": "nha_trang_laundry_domain.pricing.price_lines",
        "content_hash": corpus_hash(documents),
        "cases": documents,
    }


def corpus_hash(documents: Sequence[dict[str, Any]]) -> str:
    """Hash the case list so a moved expected value shows up as a corpus diff, not a silent edit."""

    payload = json.dumps(list(documents), sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def verify_expectations(document: dict[str, Any]) -> tuple[str, ...]:
    """Re-price every case and return the identifiers whose expectations no longer hold.

    This is the check that makes the corpus trustworthy: it fails when the domain changes, which is
    the moment a human must decide whether the corpus or the engine is wrong.
    """

    mismatched: list[str] = []
    for case in document["cases"]:
        payload = case["input"]
        rule = RULES[payload["service_code"]]
        lines = tuple(
            PriceLine(
                rule.service_code,
                quantity,
                unit=Unit(payload["unit"]),
                quantity_basis=QuantityBasis(payload["quantity_basis"]),
            )
            for quantity in payload["quantities"]
        )
        result = price_lines(RULES, lines)[rule.service_code]
        expected = case["expected"]
        if (
            result.list_amount_vnd != expected["list_amount_vnd"]
            or result.range_min_vnd != expected["range_min_vnd"]
            or result.range_max_vnd != expected["range_max_vnd"]
            or result.finality.value != expected["finality"]
            or result.requires_human != expected["requires_human"]
        ):
            mismatched.append(str(case["id"]))
    return tuple(mismatched)


def wrong_monetary_value_count(document: dict[str, Any]) -> int:
    """The manifest allows zero. Anything above zero blocks the suite."""

    return len(verify_expectations(document))


__all__ = [
    "CORPUS_VERSION",
    "DATASET_LAYER",
    "RULES",
    "CombinatorialGenerationError",
    "GeneratedCase",
    "QuoteFinality",
    "corpus_document",
    "corpus_hash",
    "generate_cases",
    "verify_expectations",
    "wrong_monetary_value_count",
]
