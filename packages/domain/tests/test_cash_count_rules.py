"""`CASH-COUNT-009` (`DEC-049`): what the drawer should hold, and how far the count is from it."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.cash_count import (
    CASH_COUNT_RULE_IDENTIFIER,
    COUNTED_MAX_VND,
    NO_MOVEMENT,
    CashCountError,
    DifferenceDirection,
    DrawerMovement,
    ExpectedStatus,
    cash_difference,
    closing_trace,
    correction_reason,
    counted_amount,
    expected_cash,
    recording_day,
    replay_trace,
)
from nha_trang_laundry_domain.shop_capture import ExpenseCategory, ShopCaptureError, expense

DAY = date(2026, 10, 1)


def movement(
    cash_in: int = 0,
    refunded: int = 0,
    expenses: int = 0,
    unknown: int = 0,
    *,
    unknown_entries: int | None = None,
) -> DrawerMovement:
    return DrawerMovement(
        cash_in_vnd=cash_in,
        cash_in_entries=1 if cash_in else 0,
        cash_refunded_vnd=refunded,
        cash_refunded_entries=1 if refunded else 0,
        unknown_refunds_vnd=unknown,
        unknown_refunds_entries=(1 if unknown else 0)
        if unknown_entries is None
        else unknown_entries,
        drawer_expenses_vnd=expenses,
        drawer_expenses_entries=1 if expenses else 0,
    )


# --- expected = float + cash taken - cash refunded - drawer expenses ---


def test_the_conformance_day_float_sales_refund_expense() -> None:
    """The `cash_count` scenario's own day: 500.000 float, 300.000 taken in cash, 20.000 handed
    back in cash, 50.000 paid out of the drawer for chemicals -> 730.000 expected."""
    expected = expected_cash(500_000, movement(300_000, 20_000, 50_000))

    assert expected.status is ExpectedStatus.COMPLETE
    assert expected.expected_vnd == 730_000
    assert expected.float_vnd == 500_000
    short = cash_difference(720_000, expected)
    assert short is not None
    assert (short.amount_vnd, short.direction) == (10_000, DifferenceDirection.SHORT)


@pytest.mark.parametrize(
    ("float_vnd", "moved", "expected_vnd"),
    [
        (0, NO_MOVEMENT, 0),
        (500_000, NO_MOVEMENT, 500_000),
        (0, movement(cash_in=125_000), 125_000),
        (200_000, movement(refunded=200_000), 0),
        (200_000, movement(expenses=200_000), 0),
        (1, movement(cash_in=2, refunded=1, expenses=2), 0),
        (COUNTED_MAX_VND, movement(cash_in=COUNTED_MAX_VND), 2 * COUNTED_MAX_VND),
    ],
)
def test_expected_is_the_four_figures_and_nothing_else(
    float_vnd: int, moved: DrawerMovement, expected_vnd: int
) -> None:
    expected = expected_cash(float_vnd, moved)

    assert expected.status is ExpectedStatus.COMPLETE
    assert expected.expected_vnd == expected_vnd
    assert isinstance(expected.expected_vnd, int)


def test_no_float_is_not_a_float_of_zero() -> None:
    """Unknown is not zero: a day with no opening float has no expected figure and no difference."""
    expected = expected_cash(None, movement(cash_in=300_000))

    assert expected.status is ExpectedStatus.FLOAT_MISSING
    assert expected.expected_vnd is None and expected.float_vnd is None
    assert not expected.produced
    assert cash_difference(300_000, expected) is None


def test_refunds_of_unknown_method_are_excluded_and_mark_the_figure_incomplete() -> None:
    """`DEC-048`: never guessed as cash. The figure is the same as without them, marked."""
    without = expected_cash(500_000, movement(300_000, 20_000, 50_000))
    excluded = expected_cash(500_000, movement(300_000, 20_000, 50_000, unknown=40_000))

    assert excluded.expected_vnd == without.expected_vnd == 730_000
    assert excluded.status is ExpectedStatus.INCOMPLETE
    assert excluded.produced
    assert excluded.movement.unknown_refunds_vnd == 40_000
    # A refund of unknown method with a zero amount is still one the figure did not see.
    zero = expected_cash(500_000, movement(unknown=0, unknown_entries=1))
    assert zero.status is ExpectedStatus.INCOMPLETE


@pytest.mark.parametrize(
    ("float_vnd", "moved", "books_over"),
    [
        (0, movement(refunded=1), 1),
        (100_000, movement(expenses=150_000), 50_000),
        (100_000, movement(cash_in=10_000, refunded=60_000, expenses=60_000), 10_000),
    ],
)
def test_books_below_zero_are_not_clamped(
    float_vnd: int, moved: DrawerMovement, books_over: int
) -> None:
    """A drawer cannot hold less than nothing: no figure, and the size of the gap is said."""
    expected = expected_cash(float_vnd, moved)

    assert expected.status is ExpectedStatus.BOOKS_BELOW_ZERO
    assert expected.expected_vnd is None
    assert expected.books_over_vnd == books_over
    assert cash_difference(0, expected) is None


@pytest.mark.parametrize(
    ("counted", "amount", "direction"),
    [
        (730_000, 0, DifferenceDirection.EVEN),
        (729_999, 1, DifferenceDirection.SHORT),
        (730_001, 1, DifferenceDirection.OVER),
        (720_000, 10_000, DifferenceDirection.SHORT),
        (0, 730_000, DifferenceDirection.SHORT),
        (1_000_000, 270_000, DifferenceDirection.OVER),
    ],
)
def test_the_difference_is_a_size_and_a_word(
    counted: int, amount: int, direction: DifferenceDirection
) -> None:
    difference = cash_difference(counted, expected_cash(730_000, NO_MOVEMENT))

    assert difference is not None
    assert (difference.amount_vnd, difference.direction) == (amount, direction)
    assert difference.amount_vnd >= 0


@given(
    float_vnd=st.integers(0, COUNTED_MAX_VND),
    cash_in=st.integers(0, 10**9),
    refunded=st.integers(0, 10**9),
    expenses=st.integers(0, 10**9),
    unknown=st.integers(0, 10**9),
    counted=st.integers(0, COUNTED_MAX_VND),
)
def test_every_day_is_whole_dong_and_never_signed(
    float_vnd: int, cash_in: int, refunded: int, expenses: int, unknown: int, counted: int
) -> None:
    moved = movement(cash_in, refunded, expenses, unknown)
    expected = expected_cash(float_vnd, moved)
    held = float_vnd + cash_in - refunded - expenses

    if held < 0:
        assert expected.status is ExpectedStatus.BOOKS_BELOW_ZERO
        assert expected.books_over_vnd == -held
        assert cash_difference(counted, expected) is None
        return
    assert expected.expected_vnd == held
    assert expected.status is (ExpectedStatus.INCOMPLETE if unknown else ExpectedStatus.COMPLETE)
    difference = cash_difference(counted, expected)
    assert difference is not None
    assert isinstance(difference.amount_vnd, int) and difference.amount_vnd >= 0
    # The word and the size together give back the count exactly; no sign is ever needed.
    if difference.direction is DifferenceDirection.EVEN:
        assert difference.amount_vnd == 0 and counted == held
    elif difference.direction is DifferenceDirection.OVER:
        assert counted == held + difference.amount_vnd and difference.amount_vnd > 0
    else:
        assert counted == held - difference.amount_vnd and difference.amount_vnd > 0


@pytest.mark.parametrize("bad", [1.0, True, "1000", -1])
def test_the_inputs_are_whole_non_negative_dong(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        expected_cash(bad, NO_MOVEMENT)  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError)):
        movement(cash_in=bad)  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError)):
        cash_difference(bad, expected_cash(0, NO_MOVEMENT))  # type: ignore[arg-type]


# --- what an entry may be ---


@pytest.mark.parametrize("value", [0, 1, 500_000, COUNTED_MAX_VND])
def test_a_count_is_any_whole_amount_up_to_the_ceiling(value: int) -> None:
    assert counted_amount(value) == value


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (-1, "CASH_COUNT_AMOUNT_INVALID"),
        (True, "CASH_COUNT_AMOUNT_INVALID"),
        (1.5, "CASH_COUNT_AMOUNT_INVALID"),
        (COUNTED_MAX_VND + 1, "CASH_COUNT_AMOUNT_TOO_LARGE"),
    ],
)
def test_a_count_outside_the_rules_is_refused_by_name(value: object, code: str) -> None:
    with pytest.raises(CashCountError) as refused:
        counted_amount(value)  # type: ignore[arg-type]
    assert refused.value.reason_code == code


def test_entries_are_for_the_shops_today_only() -> None:
    assert recording_day(DAY, today=DAY) == DAY
    for other in (date(2026, 9, 30), date(2026, 10, 2)):
        with pytest.raises(CashCountError) as refused:
            recording_day(other, today=DAY)
        assert refused.value.reason_code == "CASH_COUNT_DAY_NOT_TODAY"


@pytest.mark.parametrize(
    ("value", "correcting", "result", "code"),
    [
        (None, False, None, None),
        ("   ", False, None, None),
        ("đếm sót tờ 50.000", True, "đếm sót tờ 50.000", None),
        ("  đếm   lại  ", True, "đếm lại", None),
        (None, True, None, "CASH_COUNT_REASON_REQUIRED"),
        ("  ", True, None, "CASH_COUNT_REASON_REQUIRED"),
        ("lý do", False, None, "CASH_COUNT_REASON_NOT_EXPECTED"),
        ("gọi 0905 123 456", True, None, "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"),
        ("khach (090) 512 3456", True, None, "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"),
        ("sdt 0905/123/456", True, None, "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"),
        ("khach 0905 - 123 - 456 tra thieu", True, None, "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"),
        ("khach 0905 . 123 . 456", True, None, "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"),
        ("khach 0905\u2013123\u2013456", True, None, "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"),
        ("khach 0905/123 456", True, None, "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"),
        ("khach (090) 512/3456", True, None, "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"),
        ("khach [0905] 123 456", True, None, "CASH_COUNT_REASON_LOOKS_LIKE_PHONE"),
        ("đếm lại 02/10/2026 150k", True, "đếm lại 02/10/2026 150k", None),
        ("đếm lại 02/10 150.000", True, "đếm lại 02/10 150.000", None),
        ("x" * 121, True, None, "CASH_COUNT_REASON_INVALID"),
    ],
)
def test_a_correction_says_why_and_an_original_does_not(
    value: str | None, correcting: bool, result: str | None, code: str | None
) -> None:
    if code is None:
        assert correction_reason(value, correcting=correcting) == result
        return
    with pytest.raises(CashCountError) as refused:
        correction_reason(value, correcting=correcting)
    assert refused.value.reason_code == code


# --- the stored trace ---


def test_a_closing_trace_is_reproducible_from_its_own_document() -> None:
    expected = expected_cash(500_000, movement(300_000, 20_000, 50_000, unknown=40_000))
    trace = closing_trace(
        business_day=DAY,
        counted_vnd=720_000,
        expected=expected,
        difference=cash_difference(720_000, expected),
    )

    assert trace.trace_hash.startswith("JCS-SHA256-V1:")
    assert trace.document["rule"] == CASH_COUNT_RULE_IDENTIFIER
    assert trace.document["status"] == "INCOMPLETE"
    assert trace.document["expected_vnd"] == 730_000
    assert trace.document["difference_direction"] == "SHORT"
    assert trace.document["difference_vnd"] == 10_000
    assert trace.document["excluded_unknown_refunds_vnd"] == 40_000
    replayed = replay_trace(dict(trace.document))
    assert replayed == trace
    # Any figure moved in the stored document no longer replays to the stored hash.
    tampered = {**trace.document, "cash_in_vnd": 300_001}
    assert replay_trace(tampered).trace_hash != trace.trace_hash


def test_a_trace_without_a_float_replays_to_no_figure() -> None:
    expected = expected_cash(None, movement(cash_in=10_000))
    trace = closing_trace(business_day=DAY, counted_vnd=10_000, expected=expected, difference=None)

    assert trace.document["expected_vnd"] is None
    assert trace.document["difference_direction"] is None
    assert replay_trace(dict(trace.document)) == trace


def test_a_trace_from_another_rule_is_not_replayed_under_this_one() -> None:
    expected = expected_cash(0, NO_MOVEMENT)
    trace = closing_trace(business_day=DAY, counted_vnd=0, expected=expected, difference=None)
    with pytest.raises(ValueError, match="another rule"):
        replay_trace({**trace.document, "rule": "cash-count-v0"})


# --- Sổ thu chi's "Trả từ két" ---


def test_an_expense_is_not_from_the_drawer_unless_said_so() -> None:
    line = expense(
        spent_on=DAY, category=ExpenseCategory.HOA_CHAT, amount_vnd=50_000, note=None, today=DAY
    )
    drawer = expense(
        spent_on=DAY,
        category=ExpenseCategory.HOA_CHAT,
        amount_vnd=50_000,
        note=None,
        today=DAY,
        paid_from_drawer=True,
    )

    assert line.paid_from_drawer is False
    assert drawer.paid_from_drawer is True
    with pytest.raises(ShopCaptureError) as refused:
        expense(
            spent_on=DAY,
            category=ExpenseCategory.HOA_CHAT,
            amount_vnd=50_000,
            note=None,
            today=DAY,
            paid_from_drawer="yes",  # type: ignore[arg-type]
        )
    assert refused.value.reason_code == "EXPENSE_DRAWER_FLAG_INVALID"
