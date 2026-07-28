"""Pure deterministic price calculation; no model, float, or implicit policy fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from nha_trang_laundry_domain.catalog import (
    ErrorCode,
    PriceRuleType,
    QuantityBasis,
    QuoteFinality,
    Unit,
)

QUANTITY_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
MAX_NUMERIC_QUANTITY: Final = Decimal("999999999.999")
MAX_BIGINT_VND = 9_223_372_036_854_775_807
COUNT_UNITS: Final = frozenset({Unit.ITEM, Unit.PAIR, Unit.SET, Unit.ANIMAL_PLUSH_ITEM, Unit.CASE})


class PricingError(ValueError):
    """Raised for invalid or unresolved deterministic price inputs."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class PriceTier:
    minimum_quantity: Decimal
    maximum_quantity_exclusive: Decimal | None
    unit_price_vnd: int
    minimum_billable_quantity: Decimal | None = None


@dataclass(frozen=True)
class PriceRule:
    service_code: str
    rule_type: PriceRuleType
    unit_price_vnd: int | None = None
    range_min_vnd: int | None = None
    range_max_vnd: int | None = None
    tiers: tuple[PriceTier, ...] = ()
    unit: Unit = Unit.KG


@dataclass(frozen=True)
class PriceLine:
    service_code: str
    quantity: str
    unit: Unit = Unit.KG
    quantity_basis: QuantityBasis = QuantityBasis.CUSTOMER_ESTIMATE


@dataclass(frozen=True)
class CalculationTrace:
    service_code: str
    rule_type: PriceRuleType
    unit: Unit
    input_quantities: tuple[str, ...]
    quantity_bases: tuple[QuantityBasis, ...]
    aggregate_quantity: str
    billable_quantity: str
    selected_tier: int | None
    unit_price_vnd: int | None
    range_min_unit_vnd: int | None
    range_max_unit_vnd: int | None
    rounding: str


@dataclass(frozen=True)
class PriceResult:
    list_amount_vnd: int | None
    range_min_vnd: int | None
    range_max_vnd: int | None
    requires_human: bool
    finality: QuoteFinality
    trace: CalculationTrace


def price_lines(
    rules: dict[str, PriceRule], lines: tuple[PriceLine, ...]
) -> dict[str, PriceResult]:
    """Price canonical grouped quantities; same-service lines select a tier from their total."""
    if not lines:
        raise PricingError(ErrorCode.MISSING_REQUIRED_FACT)
    grouped: dict[str, list[tuple[PriceLine, Decimal]]] = {}
    for line in lines:
        rule = rules.get(line.service_code)
        if rule is None or rule.service_code != line.service_code:
            raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
        if line.unit is not rule.unit:
            raise PricingError(ErrorCode.INCOMPATIBLE_UNIT)
        quantity = _quantity(line.quantity, line.unit)
        grouped.setdefault(line.service_code, []).append((line, quantity))
    return {
        service_code: _price(rules[service_code], grouped_lines)
        for service_code, grouped_lines in grouped.items()
    }


def _price(rule: PriceRule, lines: list[tuple[PriceLine, Decimal]]) -> PriceResult:
    _validate_rule(rule)
    quantity = sum((value for _, value in lines), start=Decimal())
    if quantity > MAX_NUMERIC_QUANTITY:
        raise PricingError(ErrorCode.MISSING_REQUIRED_FACT)
    input_quantities = tuple(_decimal_text(value) for _, value in lines)
    bases = tuple(line.quantity_basis for line, _ in lines)
    aggregate_text = _decimal_text(quantity)

    if rule.rule_type is PriceRuleType.FIXED_PER_UNIT:
        assert rule.unit_price_vnd is not None
        amount = _round_vnd(quantity * rule.unit_price_vnd)
        trace = CalculationTrace(
            rule.service_code,
            rule.rule_type,
            rule.unit,
            input_quantities,
            bases,
            aggregate_text,
            aggregate_text,
            None,
            rule.unit_price_vnd,
            None,
            None,
            "ROUND_HALF_UP_1_VND",
        )
        return PriceResult(amount, None, None, False, QuoteFinality.ESTIMATE, trace)

    if rule.rule_type is PriceRuleType.FIXED_PER_ORDER:
        assert rule.unit_price_vnd is not None
        if quantity != Decimal(1):
            raise PricingError(ErrorCode.INCOMPATIBLE_UNIT)
        trace = CalculationTrace(
            rule.service_code,
            rule.rule_type,
            rule.unit,
            input_quantities,
            bases,
            aggregate_text,
            "1",
            None,
            rule.unit_price_vnd,
            None,
            None,
            "ROUND_HALF_UP_1_VND",
        )
        return PriceResult(rule.unit_price_vnd, None, None, False, QuoteFinality.ESTIMATE, trace)

    if rule.rule_type is PriceRuleType.RANGE_PER_UNIT:
        assert rule.range_min_vnd is not None
        assert rule.range_max_vnd is not None
        minimum = _round_vnd(quantity * rule.range_min_vnd)
        maximum = _round_vnd(quantity * rule.range_max_vnd)
        trace = CalculationTrace(
            rule.service_code,
            rule.rule_type,
            rule.unit,
            input_quantities,
            bases,
            aggregate_text,
            aggregate_text,
            None,
            None,
            rule.range_min_vnd,
            rule.range_max_vnd,
            "ROUND_HALF_UP_1_VND",
        )
        return PriceResult(None, minimum, maximum, True, QuoteFinality.RANGE, trace)

    if rule.rule_type is PriceRuleType.AGGREGATE_TIER_PER_UNIT:
        selected = next(
            (
                (index, candidate)
                for index, candidate in enumerate(rule.tiers)
                if quantity >= candidate.minimum_quantity
                and (
                    candidate.maximum_quantity_exclusive is None
                    or quantity < candidate.maximum_quantity_exclusive
                )
            ),
            None,
        )
        if selected is None:
            raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
        selected_tier, tier = selected
        billable = max(quantity, tier.minimum_billable_quantity or Decimal())
        amount = _round_vnd(billable * tier.unit_price_vnd)
        trace = CalculationTrace(
            rule.service_code,
            rule.rule_type,
            rule.unit,
            input_quantities,
            bases,
            aggregate_text,
            _decimal_text(billable),
            selected_tier,
            tier.unit_price_vnd,
            None,
            None,
            "ROUND_HALF_UP_1_VND",
        )
        return PriceResult(amount, None, None, False, QuoteFinality.ESTIMATE, trace)

    raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)


def _validate_rule(rule: PriceRule) -> None:
    if rule.rule_type in {PriceRuleType.FIXED_PER_UNIT, PriceRuleType.FIXED_PER_ORDER}:
        if not _valid_positive_vnd(rule.unit_price_vnd):
            raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
        if rule.range_min_vnd is not None or rule.range_max_vnd is not None or rule.tiers:
            raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
        return
    if rule.rule_type is PriceRuleType.RANGE_PER_UNIT:
        minimum = rule.range_min_vnd
        maximum = rule.range_max_vnd
        if (
            not _valid_positive_vnd(minimum)
            or not _valid_positive_vnd(maximum)
            or rule.unit_price_vnd is not None
            or rule.tiers
        ):
            raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
        assert minimum is not None and maximum is not None
        if minimum > maximum:
            raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
        return
    if rule.rule_type is PriceRuleType.AGGREGATE_TIER_PER_UNIT:
        if (
            rule.unit_price_vnd is not None
            or rule.range_min_vnd is not None
            or rule.range_max_vnd is not None
            or not rule.tiers
        ):
            raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
        expected_minimum = Decimal()
        for tier in rule.tiers:
            invalid_maximum = (
                tier.maximum_quantity_exclusive is not None
                and not tier.maximum_quantity_exclusive.is_finite()
            )
            invalid_minimum_billable = (
                tier.minimum_billable_quantity is not None
                and not tier.minimum_billable_quantity.is_finite()
            )
            if (
                not tier.minimum_quantity.is_finite()
                or invalid_maximum
                or invalid_minimum_billable
                or tier.minimum_quantity != expected_minimum
                or not _valid_positive_vnd(tier.unit_price_vnd)
                or (
                    tier.minimum_billable_quantity is not None
                    and tier.minimum_billable_quantity < tier.minimum_quantity
                )
            ):
                raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
            if tier.maximum_quantity_exclusive is None:
                expected_minimum = Decimal(-1)
            elif tier.maximum_quantity_exclusive <= tier.minimum_quantity:
                raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
            else:
                expected_minimum = tier.maximum_quantity_exclusive
        if expected_minimum != Decimal(-1):
            raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
        return
    raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)


def _quantity(value: str, unit: Unit) -> Decimal:
    if not isinstance(value, str) or not QUANTITY_PATTERN.fullmatch(value):
        raise PricingError(ErrorCode.MISSING_REQUIRED_FACT)
    try:
        quantity = Decimal(value)
    except InvalidOperation as error:
        raise PricingError(ErrorCode.MISSING_REQUIRED_FACT) from error
    exponent = quantity.as_tuple().exponent
    if not isinstance(exponent, int):
        raise PricingError(ErrorCode.MISSING_REQUIRED_FACT)
    decimal_places = max(0, -exponent)
    if (
        not quantity.is_finite()
        or quantity <= 0
        or quantity > MAX_NUMERIC_QUANTITY
        or decimal_places > 3
        or (unit in COUNT_UNITS and quantity != quantity.to_integral_value())
    ):
        raise PricingError(ErrorCode.MISSING_REQUIRED_FACT)
    return quantity


def _round_vnd(value: Decimal) -> int:
    rounded = int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if rounded < 0 or rounded > MAX_BIGINT_VND:
        raise PricingError(ErrorCode.PRICE_RULE_UNRESOLVED)
    return rounded


def _valid_positive_vnd(value: int | None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_BIGINT_VND


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
