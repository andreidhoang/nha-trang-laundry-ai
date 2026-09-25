"""`SHOP-CAPTURE-001` (`DEC-038`): the pure rules behind machines, trips, Sổ thu chi and margin."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import ProductionStatus
from nha_trang_laundry_domain.shop_capture import (
    CORE_MARGIN_CATEGORIES,
    CycleEffect,
    ExpenseCategory,
    MachineCategory,
    MarginStatus,
    ShopCaptureError,
    Vehicle,
    WeighedLine,
    WeightBasis,
    capture_note,
    cycle_effect,
    expense,
    machine_code,
    machine_name,
    month_bounds,
    month_margin,
    months_touching,
    per_order_vnd,
    starts_cycle,
    suggest_vehicle,
    trip_cost,
    trip_km,
)

P = ProductionStatus
TODAY = date(2026, 9, 25)


def kg(quantity: str, *, estimate: bool = False) -> WeighedLine:
    return WeighedLine(unit="KG", quantity=quantity, customer_estimate=estimate)


# --- machines and cycles ----------------------------------------------------------------------


def test_only_the_machines_a_load_goes_into_start_a_cycle() -> None:
    assert {c for c in MachineCategory if starts_cycle(c)} == {
        MachineCategory.WASHER,
        MachineCategory.DRY_CLEANER,
        MachineCategory.SHOE_WASHER_DRYER,
    }


def test_machine_codes_and_names_are_normalised_or_refused() -> None:
    assert machine_code(" wash-03 ") == "WASH-03"
    assert machine_name("  Máy   giặt  LG  ") == "Máy giặt LG"
    for bad in ("", "-WASH", "WASH 01", "X" * 33):
        with pytest.raises(ShopCaptureError) as refused:
            machine_code(bad)
        assert refused.value.reason_code == "MACHINE_CODE_INVALID"
    for bad in ("", "   ", "x" * 61, "Máy\x00"):
        with pytest.raises(ShopCaptureError) as refused:
            machine_name(bad)
        assert refused.value.reason_code == "MACHINE_NAME_INVALID"


@pytest.mark.parametrize(
    ("before", "after", "effect"),
    [
        # Bắt đầu giặt: QUEUED -> IN_PROCESS opens.
        (P.QUEUED, P.IN_PROCESS, CycleEffect.OPEN),
        # Giặt xong, kiểm tra đồ closes.
        (P.IN_PROCESS, P.QUALITY_CHECK, CycleEffect.CLOSE),
        # REWASH: back into IN_PROCESS from the exception the rewash opened -- opens (the
        # repository opens only when none is open, and quality check closed the first).
        (P.EXCEPTION, P.IN_PROCESS, CycleEffect.OPEN),
        # A hold resumed: the repository finds the cycle still open and opens nothing.
        (P.ON_HOLD, P.IN_PROCESS, CycleEffect.OPEN),
        # Everything else leaves the cycle alone.
        (P.NOT_STARTED, P.QUEUED, CycleEffect.NONE),
        (P.IN_PROCESS, P.ON_HOLD, CycleEffect.NONE),
        (P.IN_PROCESS, P.EXCEPTION, CycleEffect.NONE),
        (P.QUALITY_CHECK, P.READY_AT_STORE, CycleEffect.NONE),
        (P.READY_AT_STORE, P.RELEASED, CycleEffect.NONE),
        (P.QUALITY_CHECK, P.EXCEPTION, CycleEffect.NONE),
        (P.EXCEPTION, P.QUALITY_CHECK, CycleEffect.CLOSE),
    ],
)
def test_production_moves_open_and_close_cycles(
    before: ProductionStatus, after: ProductionStatus, effect: CycleEffect
) -> None:
    assert cycle_effect(before, after) is effect


# --- the vehicle rule, at exactly 20 kg ---------------------------------------------------------


@pytest.mark.parametrize(
    ("lines", "vehicle", "weight", "basis"),
    [
        ((kg("19.9"),), Vehicle.XE_MAY, Decimal("19.9"), WeightBasis.MEASURED),
        ((kg("20"),), Vehicle.O_TO, Decimal("20"), WeightBasis.MEASURED),
        ((kg("20.0"),), Vehicle.O_TO, Decimal("20.0"), WeightBasis.MEASURED),
        ((kg("12.5"), kg("7.5")), Vehicle.O_TO, Decimal("20.0"), WeightBasis.MEASURED),
        ((kg("12.5"), kg("7.4")), Vehicle.XE_MAY, Decimal("19.9"), WeightBasis.MEASURED),
        ((kg("5", estimate=True),), Vehicle.XE_MAY, Decimal("5"), WeightBasis.ESTIMATED),
    ],
)
def test_motorbike_under_20_kg_car_from_exactly_20_kg(
    lines: tuple[WeighedLine, ...], vehicle: Vehicle, weight: Decimal, basis: WeightBasis
) -> None:
    suggestion = suggest_vehicle(lines)
    assert (suggestion.vehicle, suggestion.weight_kg, suggestion.basis) == (vehicle, weight, basis)


def test_an_unknown_weight_suggests_no_vehicle() -> None:
    # A line priced by the piece: the bag's weight is not known, and unknown is not zero.
    blanket = WeighedLine(unit="ITEM", quantity="2", customer_estimate=False)
    for lines in ((), (blanket,), (kg("3"), blanket), (kg("abc"),), (kg("-1"),)):
        suggestion = suggest_vehicle(lines)
        assert suggestion.vehicle is None
        assert suggestion.weight_kg is None
        assert suggestion.basis is WeightBasis.UNKNOWN


# --- trips --------------------------------------------------------------------------------------


def test_kilometres_take_one_decimal_with_either_separator() -> None:
    assert trip_km("4,5") == Decimal("4.5")
    assert trip_km("12.3") == Decimal("12.3")
    assert trip_km("7") == Decimal("7")
    assert trip_km(None) is None
    assert trip_km("  ") is None
    for bad in ("4.55", "-1", "1e3", "abc", "1000", "4.5km"):
        with pytest.raises(ShopCaptureError) as refused:
            trip_km(bad)
        assert refused.value.reason_code == "TRIP_KM_INVALID"


def test_a_trip_cost_is_bounded_and_every_field_is_optional() -> None:
    empty = trip_cost(vehicle=None, km=None, cost_vnd=None, note=None)
    assert empty.empty
    full = trip_cost(vehicle=Vehicle.XE_MAY, km="3,2", cost_vnd=15_000, note="gửi xe")
    assert (full.vehicle, full.km, full.cost_vnd, full.note) == (
        Vehicle.XE_MAY,
        Decimal("3.2"),
        15_000,
        "gửi xe",
    )
    assert trip_cost(vehicle=None, km=None, cost_vnd=0, note=None).cost_vnd == 0
    assert trip_cost(vehicle=None, km=None, cost_vnd=10_000_000, note=None).cost_vnd == 10_000_000
    with pytest.raises(ShopCaptureError) as refused:
        trip_cost(vehicle=None, km=None, cost_vnd=10_000_001, note=None)
    assert refused.value.reason_code == "TRIP_COST_TOO_LARGE"
    with pytest.raises(TypeError):
        trip_cost(vehicle=None, km=None, cost_vnd=1.5, note=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        trip_cost(vehicle=None, km=None, cost_vnd=-1, note=None)


def test_a_note_that_looks_like_a_phone_number_is_refused() -> None:
    assert capture_note("Grab 2 chiều 45k") == "Grab 2 chiều 45k"
    assert capture_note("hoá đơn 12345678") == "hoá đơn 12345678"  # eight digits: not a phone
    for phone in ("0382318492", "0382 318 492", "0382.318.492", "gọi +84382318492", "038-231-8492"):
        with pytest.raises(ShopCaptureError) as refused:
            capture_note(phone)
        assert refused.value.reason_code == "NOTE_LOOKS_LIKE_PHONE"
    with pytest.raises(ShopCaptureError) as refused:
        capture_note("x" * 121)
    assert refused.value.reason_code == "NOTE_INVALID"
    assert capture_note("x" * 120) == "x" * 120
    assert capture_note("   ") is None


# --- Sổ thu chi ---------------------------------------------------------------------------------


def test_an_expense_is_positive_bounded_and_dated_today_or_within_a_year() -> None:
    line = expense(
        spent_on=TODAY, category=ExpenseCategory.DIEN, amount_vnd=1_250_000, note=None, today=TODAY
    )
    assert line.amount_vnd == 1_250_000
    cases = [
        (TODAY, 0, "EXPENSE_AMOUNT_REQUIRED"),
        (TODAY, 1_000_000_001, "EXPENSE_AMOUNT_TOO_LARGE"),
        (date(2026, 9, 26), 1, "EXPENSE_DATE_IN_FUTURE"),
        (date(2025, 9, 23), 1, "EXPENSE_DATE_TOO_OLD"),
    ]
    for spent_on, amount, code in cases:
        with pytest.raises(ShopCaptureError) as refused:
            expense(
                spent_on=spent_on,
                category=ExpenseCategory.KHAC,
                amount_vnd=amount,
                note=None,
                today=TODAY,
            )
        assert refused.value.reason_code == code
    # The edges themselves are admitted.
    expense(
        spent_on=date(2025, 9, 24),
        category=ExpenseCategory.KHAC,
        amount_vnd=1_000_000_000,
        note=None,
        today=TODAY,
    )


def test_months_are_calendar_months() -> None:
    assert month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))
    assert month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    for bad in ("2026-13", "2026-1", "26-09", "", "2026-00"):
        with pytest.raises(ShopCaptureError):
            month_bounds(bad)
    assert months_touching(date(2026, 8, 27), date(2026, 9, 25)) == (
        date(2026, 8, 1),
        date(2026, 9, 1),
    )
    assert months_touching(date(2026, 12, 30), date(2027, 1, 2)) == (
        date(2026, 12, 1),
        date(2027, 1, 1),
    )
    assert months_touching(TODAY, TODAY) == (date(2026, 9, 1),)


# --- margin, only when complete -----------------------------------------------------------------


def test_margin_is_withheld_until_every_core_category_has_a_line() -> None:
    partial = month_margin(
        recorded_categories=frozenset({ExpenseCategory.DIEN, ExpenseCategory.KHAC}),
        collected_vnd=10_000_000,
        refunded_vnd=0,
        spending_vnd=2_000_000,
    )
    assert partial.status is MarginStatus.INCOMPLETE
    assert partial.missing == (
        ExpenseCategory.NUOC,
        ExpenseCategory.HOA_CHAT,
        ExpenseCategory.LUONG,
        ExpenseCategory.MAT_BANG,
    )
    # No remainder is produced at all: nothing to mistake for profit.
    assert partial.amount_vnd is None and partial.direction is None

    nothing = month_margin(
        recorded_categories=frozenset(), collected_vnd=0, refunded_vnd=0, spending_vnd=0
    )
    assert nothing.missing == CORE_MARGIN_CATEGORIES


def test_a_complete_month_shows_takings_less_spending_with_a_direction() -> None:
    core = frozenset(CORE_MARGIN_CATEGORIES)
    kept = month_margin(
        recorded_categories=core,
        collected_vnd=30_000_000,
        refunded_vnd=500_000,
        spending_vnd=20_000_000,
    )
    assert (kept.status, kept.amount_vnd, kept.direction) == (
        MarginStatus.COMPLETE,
        9_500_000,
        "IN",
    )
    lost = month_margin(
        recorded_categories=core, collected_vnd=10_000_000, refunded_vnd=0, spending_vnd=12_000_000
    )
    assert (lost.amount_vnd, lost.direction) == (2_000_000, "OUT")
    even = month_margin(recorded_categories=core, collected_vnd=5, refunded_vnd=0, spending_vnd=5)
    assert (even.amount_vnd, even.direction) == (0, "IN")


@given(
    st.integers(min_value=0, max_value=10**12),
    st.integers(min_value=0, max_value=10**12),
    st.integers(min_value=0, max_value=10**12),
)
def test_margin_never_leaves_integers(collected: int, refunded: int, spending: int) -> None:
    result = month_margin(
        recorded_categories=frozenset(CORE_MARGIN_CATEGORIES),
        collected_vnd=collected,
        refunded_vnd=refunded,
        spending_vnd=spending,
    )
    assert isinstance(result.amount_vnd, int)
    signed = result.amount_vnd if result.direction == "IN" else -result.amount_vnd
    assert signed == collected - refunded - spending


def test_per_order_rounds_half_up_to_the_whole_dong() -> None:
    assert per_order_vnd(0, 0) is None
    assert per_order_vnd(30_000, 0) is None
    assert per_order_vnd(30_000, 3) == 10_000
    assert per_order_vnd(10_000, 3) == 3_333  # 3.333,33 -> 3.333
    assert per_order_vnd(20_000, 3) == 6_667  # 6.666,67 -> 6.667
    assert per_order_vnd(5, 2) == 3  # 2,5 -> 3: half up
    assert per_order_vnd(3, 2) == 2  # 1,5 -> 2
    with pytest.raises(ValueError):
        per_order_vnd(5, -1)


@given(st.integers(min_value=0, max_value=10**12), st.integers(min_value=1, max_value=10**6))
def test_per_order_is_the_nearest_integer(total: int, orders: int) -> None:
    result = per_order_vnd(total, orders)
    assert result is not None
    # |result * orders - total| <= orders / 2, and a tie goes up.
    assert 2 * abs(result * orders - total) <= orders
    if 2 * (total % orders) == orders:
        assert result * orders > total
