from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import PriceRuleType, QuoteFinality, Unit
from nha_trang_laundry_domain.pricing import (
    PriceLine,
    PriceRule,
    PriceTier,
    PricingError,
    price_lines,
)

STANDARD = PriceRule(
    "STANDARD_WASH_DRY",
    PriceRuleType.AGGREGATE_TIER_PER_UNIT,
    tiers=(
        PriceTier(Decimal("0"), Decimal("6"), 25_000, Decimal("1")),
        PriceTier(Decimal("6"), None, 20_000),
    ),
)


@pytest.mark.parametrize(
    ("quantity", "expected"), [("0.6", 25_000), ("5.9", 147_500), ("6", 120_000), ("6.1", 122_000)]
)
def test_standard_wash_aggregate_six_kg_boundary(quantity: str, expected: int) -> None:
    result = price_lines(
        {STANDARD.service_code: STANDARD}, (PriceLine(STANDARD.service_code, quantity),)
    )
    assert result[STANDARD.service_code].list_amount_vnd == expected


def test_same_service_lines_aggregate_before_tier_selection() -> None:
    result = price_lines(
        {STANDARD.service_code: STANDARD},
        (PriceLine(STANDARD.service_code, "3"), PriceLine(STANDARD.service_code, "3")),
    )
    assert result[STANDARD.service_code].list_amount_vnd == 120_000
    assert result[STANDARD.service_code].trace.aggregate_quantity == "6"
    assert result[STANDARD.service_code].trace.input_quantities == ("3", "3")


def test_range_price_requires_human() -> None:
    rule = PriceRule(
        "PILLOW", PriceRuleType.RANGE_PER_UNIT, range_min_vnd=21_000, range_max_vnd=63_000
    )
    result = price_lines({"PILLOW": rule}, (PriceLine("PILLOW", "1"),))["PILLOW"]
    assert result.requires_human is True
    assert (result.range_min_vnd, result.range_max_vnd) == (21_000, 63_000)
    assert result.finality is QuoteFinality.RANGE
    assert result.trace.rounding == "ROUND_HALF_UP_1_VND"


@pytest.mark.parametrize("quantity", ["0", "-1", "not-a-number"])
def test_invalid_quantity_fails_closed(quantity: str) -> None:
    with pytest.raises(PricingError):
        price_lines(
            {STANDARD.service_code: STANDARD}, (PriceLine(STANDARD.service_code, quantity),)
        )


@pytest.mark.parametrize("quantity", ["1e0", " 1", "1.0000", "1000000000"])
def test_noncanonical_or_out_of_range_quantity_fails_closed(quantity: str) -> None:
    with pytest.raises(PricingError, match="MISSING_REQUIRED_FACT"):
        price_lines(
            {STANDARD.service_code: STANDARD}, (PriceLine(STANDARD.service_code, quantity),)
        )


def test_count_unit_requires_integer_quantity_and_exact_unit_match() -> None:
    rule = PriceRule(
        "IRON_KNIT", PriceRuleType.FIXED_PER_UNIT, unit_price_vnd=15_000, unit=Unit.ITEM
    )

    with pytest.raises(PricingError, match="MISSING_REQUIRED_FACT"):
        price_lines({rule.service_code: rule}, (PriceLine(rule.service_code, "1.5", Unit.ITEM),))
    with pytest.raises(PricingError, match="INCOMPATIBLE_UNIT"):
        price_lines({rule.service_code: rule}, (PriceLine(rule.service_code, "1", Unit.KG),))


def test_vnd_calculation_rounds_half_up_and_never_becomes_approved_exact() -> None:
    rule = PriceRule("ROUNDING_TEST", PriceRuleType.FIXED_PER_UNIT, unit_price_vnd=500)
    result = price_lines({rule.service_code: rule}, (PriceLine(rule.service_code, "0.001"),))[
        rule.service_code
    ]

    assert result.list_amount_vnd == 1
    assert result.finality is QuoteFinality.ESTIMATE
    assert result.requires_human is False


def test_invalid_or_manual_price_rules_fail_closed() -> None:
    invalid_range = PriceRule(
        "INVALID_RANGE", PriceRuleType.RANGE_PER_UNIT, range_min_vnd=20_000, range_max_vnd=10_000
    )
    manual = PriceRule("MANUAL", PriceRuleType.MANUAL)

    for rule in (invalid_range, manual):
        with pytest.raises(PricingError, match="PRICE_RULE_UNRESOLVED"):
            price_lines({rule.service_code: rule}, (PriceLine(rule.service_code, "1"),))


def test_empty_input_rule_key_mismatch_and_invalid_tiers_fail_closed() -> None:
    with pytest.raises(PricingError, match="MISSING_REQUIRED_FACT"):
        price_lines({}, ())
    with pytest.raises(PricingError, match="PRICE_RULE_UNRESOLVED"):
        price_lines({"REQUESTED": STANDARD}, (PriceLine("REQUESTED", "1"),))

    gap = PriceRule(
        "GAP",
        PriceRuleType.AGGREGATE_TIER_PER_UNIT,
        tiers=(PriceTier(Decimal("1"), None, 10_000),),
    )
    with pytest.raises(PricingError, match="PRICE_RULE_UNRESOLVED"):
        price_lines({gap.service_code: gap}, (PriceLine(gap.service_code, "1"),))


@given(st.integers(min_value=1, max_value=999_999))
def test_standard_wash_matches_approved_formula_for_thousandth_kg_steps(
    quantity_thousandths: int,
) -> None:
    quantity = Decimal(quantity_thousandths) / 1000
    result = price_lines(
        {STANDARD.service_code: STANDARD},
        (PriceLine(STANDARD.service_code, format(quantity, "f")),),
    )[STANDARD.service_code]
    unit_price = 25_000 if quantity < 6 else 20_000
    billable = max(quantity, Decimal(1)) if quantity < 6 else quantity
    expected = int((billable * unit_price).quantize(Decimal(1), rounding="ROUND_HALF_UP"))

    assert result.list_amount_vnd == expected


@given(
    st.integers(min_value=1, max_value=10_000),
    st.integers(min_value=1, max_value=10_000),
)
def test_standard_wash_aggregation_is_invariant_to_line_split(
    first_thousandths: int, second_thousandths: int
) -> None:
    first = Decimal(first_thousandths) / 1000
    second = Decimal(second_thousandths) / 1000
    split = price_lines(
        {STANDARD.service_code: STANDARD},
        (
            PriceLine(STANDARD.service_code, format(first, "f")),
            PriceLine(STANDARD.service_code, format(second, "f")),
        ),
    )[STANDARD.service_code]
    combined = price_lines(
        {STANDARD.service_code: STANDARD},
        (PriceLine(STANDARD.service_code, format(first + second, "f")),),
    )[STANDARD.service_code]

    assert split.list_amount_vnd == combined.list_amount_vnd
