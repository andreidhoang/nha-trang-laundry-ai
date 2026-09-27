"""`DAILY-SUMMARY-001` (`DEC-039`): the evening summary's template, line by line.

Golden tests: each line's exact Vietnamese text for the figures that choose its wording, the one
money formatter at its edges, the omission of every line whose source cannot answer, and the
structural guarantee that no input can carry personal data into the text.
"""

from __future__ import annotations

import dataclasses
import typing
from datetime import date, time

import pytest
from nha_trang_laundry_domain import daily_summary as template
from nha_trang_laundry_domain.daily_summary import (
    AccountsDueFigures,
    BoardFigures,
    DayFigures,
    LineKey,
    OmissionReason,
    OpenComplaints,
    SpendingFigures,
    SummaryInputs,
    Unavailable,
    WaitingFigures,
    format_count,
    format_day,
    format_vnd,
    render_summary,
)

#: Thứ Sáu 25/9/2026.
DAY = date(2026, 9, 25)

QUIET = DayFigures(
    orders_created=0,
    orders_completed=0,
    orders_cancelled=0,
    finished=0,
    finished_on_time=0,
    finished_without_promise=0,
    stated_rule_hours=8,
    complaints_opened=0,
    collected_vnd=0,
    collected_entries=0,
    cash_vnd=0,
    cash_entries=0,
    transfer_vnd=0,
    transfer_entries=0,
    refunded_vnd=0,
    refund_entries=0,
    net_vnd=0,
    net_direction="IN",
)

BUSY = DayFigures(
    orders_created=12,
    orders_completed=9,
    orders_cancelled=1,
    finished=6,
    finished_on_time=5,
    finished_without_promise=2,
    stated_rule_hours=8,
    complaints_opened=1,
    collected_vnd=1_250_000,
    collected_entries=5,
    cash_vnd=800_000,
    cash_entries=3,
    transfer_vnd=450_000,
    transfer_entries=2,
    refunded_vnd=0,
    refund_entries=0,
    net_vnd=1_250_000,
    net_direction="IN",
)

BOARD = BoardFigures(
    promised=7,
    promised_late=3,
    unpromised=4,
    unpromised_late=1,
    stated_rule_hours=8,
    turnaround_published=True,
)

NOT_BUILT_WAITING = Unavailable(OmissionReason.SOURCE_NOT_BUILT, "UNCLAIMED-001")
NOT_BUILT_ACCOUNTS = Unavailable(OmissionReason.SOURCE_NOT_BUILT, "PAYMENT-002")


def _inputs(**changes: object) -> SummaryInputs:
    base = SummaryInputs(
        day=DAY,
        as_of_local=time(19, 5),
        figures=BUSY,
        board=BOARD,
        open_complaints=OpenComplaints(open_count=2),
        spending=SpendingFigures(
            total_vnd=0, entries=0, by_category=(("DIEN", 0, 0), ("NUOC", 0, 0))
        ),
        waiting=NOT_BUILT_WAITING,
        accounts_due=NOT_BUILT_ACCOUNTS,
    )
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]


def _line(inputs: SummaryInputs, key: LineKey) -> str:
    lines = {line.key: line for line in render_summary(inputs).lines}
    return lines[key].text


# --- the one money formatter ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        (0, "0đ"),
        (500, "500đ"),
        (1_000, "1.000đ"),
        (120_000, "120.000đ"),
        (147_500, "147.500đ"),
        (1_250_000, "1.250.000đ"),
        (12_345_678_900, "12.345.678.900đ"),
    ],
)
def test_money_is_grouped_by_dots_with_the_dong_sign_and_nothing_else(
    amount: int, text: str
) -> None:
    assert format_vnd(amount) == text


@pytest.mark.parametrize("bad", [True, 1.5, 120_000.0, "120000", None])
def test_money_that_is_not_a_whole_number_of_dong_is_refused(bad: object) -> None:
    with pytest.raises(TypeError):
        format_vnd(bad)  # type: ignore[arg-type]


def test_a_negative_amount_is_refused_because_direction_is_a_word() -> None:
    with pytest.raises(ValueError):
        format_vnd(-1)


def test_counts_carry_their_unit_and_refuse_what_is_not_a_count() -> None:
    assert format_count(0, "đơn") == "0 đơn"
    assert format_count(3, "khoản chi") == "3 khoản chi"
    for bad in (-1, True, 1.0):
        with pytest.raises(ValueError):
            format_count(bad, "đơn")  # type: ignore[arg-type]


def test_the_day_is_named_with_its_weekday() -> None:
    assert format_day(DAY) == "thứ Sáu 25/09/2026"
    assert format_day(date(2026, 9, 27)) == "Chủ nhật 27/09/2026"
    assert format_day(date(2026, 9, 28)) == "thứ Hai 28/09/2026"


# --- golden lines ---------------------------------------------------------------------------------


def test_header_says_until_when_for_today_and_nothing_for_a_closed_day() -> None:
    assert _line(_inputs(), LineKey.HEADER) == "Tóm tắt thứ Sáu 25/09/2026, tính đến 19:05."
    assert _line(_inputs(as_of_local=None), LineKey.HEADER) == "Tóm tắt thứ Sáu 25/09/2026."


def test_orders_line() -> None:
    assert _line(_inputs(), LineKey.ORDERS) == "Nhận 12 đơn mới. Hoàn tất 9 đơn. Huỷ 1 đơn."
    assert _line(_inputs(figures=QUIET), LineKey.ORDERS) == "Nhận 0 đơn mới. Hoàn tất 0 đơn."


def test_money_line_splits_cash_and_transfer() -> None:
    assert _line(_inputs(), LineKey.MONEY) == (
        "Đã thu 1.250.000đ (5 khoản). Tiền mặt 800.000đ. Chuyển khoản 450.000đ."
    )
    assert _line(_inputs(figures=QUIET), LineKey.MONEY) == "Chưa thu khoản tiền nào."


def test_money_line_adds_refunds_only_when_there_are_any_and_says_the_direction_in_words() -> None:
    refunded_in = dataclasses.replace(
        BUSY, refunded_vnd=110_000, refund_entries=1, net_vnd=1_140_000, net_direction="IN"
    )
    assert _line(_inputs(figures=refunded_in), LineKey.MONEY) == (
        "Đã thu 1.250.000đ (5 khoản). Tiền mặt 800.000đ. Chuyển khoản 450.000đ. "
        "Hoàn lại khách 110.000đ (1 khoản). Thu trừ hoàn còn 1.140.000đ."
    )
    refunded_out = dataclasses.replace(
        QUIET, refunded_vnd=110_000, refund_entries=1, net_vnd=110_000, net_direction="OUT"
    )
    text = _line(_inputs(figures=refunded_out), LineKey.MONEY)
    assert text == (
        "Chưa thu khoản tiền nào. Hoàn lại khách 110.000đ (1 khoản). "
        "Tiền hoàn nhiều hơn tiền thu 110.000đ."
    )
    assert "-" not in text and chr(0x2212) not in text  # no minus sign of either kind


def test_finished_line_states_both_halves_of_the_fraction_and_who_had_no_promise() -> None:
    assert _line(_inputs(), LineKey.FINISHED_ON_TIME) == (
        "Giặt xong 6 đơn. 5 trên 6 đơn xong đúng hẹn. "
        "2 đơn không có giờ hẹn, được so với mốc nội bộ 8 giờ."
    )
    every_promised = dataclasses.replace(BUSY, finished_without_promise=0)
    assert _line(_inputs(figures=every_promised), LineKey.FINISHED_ON_TIME) == (
        "Giặt xong 6 đơn. 5 trên 6 đơn xong đúng hẹn."
    )
    assert _line(_inputs(figures=QUIET), LineKey.FINISHED_ON_TIME) == ("Chưa có đơn nào giặt xong.")
    text = _line(_inputs(), LineKey.FINISHED_ON_TIME)
    assert "%" not in text


def test_late_against_promise_line() -> None:
    assert _line(_inputs(), LineKey.LATE_AGAINST_PROMISE) == (
        "3 đơn chưa trả khách đã trễ giờ hẹn."
    )
    none_late = dataclasses.replace(BOARD, promised_late=0)
    assert _line(_inputs(board=none_late), LineKey.LATE_AGAINST_PROMISE) == (
        "Không có đơn chưa trả khách nào trễ giờ hẹn."
    )


def test_without_promise_line() -> None:
    assert _line(_inputs(), LineKey.WITHOUT_PROMISE) == (
        "4 đơn chưa trả khách không có giờ hẹn. 1 đơn trong số đó đã quá mốc nội bộ 8 giờ."
    )
    none_late = dataclasses.replace(BOARD, unpromised_late=0)
    assert _line(_inputs(board=none_late), LineKey.WITHOUT_PROMISE) == (
        "4 đơn chưa trả khách không có giờ hẹn."
    )
    none = dataclasses.replace(BOARD, unpromised=0, unpromised_late=0)
    assert _line(_inputs(board=none), LineKey.WITHOUT_PROMISE) == (
        "Không có đơn chưa trả khách nào thiếu giờ hẹn."
    )


def test_waiting_line_once_unclaimed_001_supplies_it() -> None:
    waiting = WaitingFigures(over_20_days=5, over_60_days=1)
    assert _line(_inputs(waiting=waiting), LineKey.WAITING_PICKUP) == (
        "5 đơn giặt xong chờ khách lấy quá 20 ngày. 1 đơn đã chờ quá 60 ngày."
    )
    assert _line(
        _inputs(waiting=WaitingFigures(over_20_days=0, over_60_days=0)), LineKey.WAITING_PICKUP
    ) == ("0 đơn giặt xong chờ khách lấy quá 20 ngày.")


def test_complaint_lines() -> None:
    assert _line(_inputs(), LineKey.COMPLAINTS_NEW) == "1 khiếu nại mới trong ngày."
    assert _line(_inputs(figures=QUIET), LineKey.COMPLAINTS_NEW) == "Không có khiếu nại mới."
    assert _line(_inputs(), LineKey.COMPLAINTS_OPEN) == "Còn 2 khiếu nại đang mở."
    assert _line(_inputs(open_complaints=OpenComplaints(0)), LineKey.COMPLAINTS_OPEN) == (
        "Không còn khiếu nại nào đang mở."
    )


def test_accounts_due_line_once_payment_002_supplies_it() -> None:
    due = AccountsDueFigures(
        due_accounts=2, due_vnd=3_400_000, overdue_accounts=1, overdue_vnd=900_000
    )
    assert _line(_inputs(accounts_due=due), LineKey.ACCOUNTS_DUE) == (
        "2 khách công nợ đến hạn trả, tổng 3.400.000đ. 1 khách công nợ đã quá hạn, tổng 900.000đ."
    )
    on_time = dataclasses.replace(due, overdue_accounts=0, overdue_vnd=0)
    assert _line(_inputs(accounts_due=on_time), LineKey.ACCOUNTS_DUE) == (
        "2 khách công nợ đến hạn trả, tổng 3.400.000đ."
    )


def test_spending_line_names_each_category_recorded_and_says_when_nothing_was() -> None:
    spending = SpendingFigures(
        total_vnd=1_200_000,
        entries=3,
        by_category=(("DIEN", 500_000, 1), ("NUOC", 0, 0), ("HOA_CHAT", 700_000, 2)),
    )
    line = next(
        line for line in render_summary(_inputs(spending=spending)).lines if line.key == "SPENDING"
    )
    assert line.text == "Ghi 3 khoản chi, tổng 1.200.000đ. Điện 500.000đ. Hoá chất 700.000đ."
    assert dict(line.figures) == {
        "spending_vnd": 1_200_000,
        "spending_entries": 3,
        "spending_dien_vnd": 500_000,
        "spending_hoa_chat_vnd": 700_000,
    }
    assert _line(_inputs(), LineKey.SPENDING) == "Chưa ghi khoản chi nào trong ngày."


def test_the_whole_summary_in_order_and_what_copy_hands_over() -> None:
    summary = render_summary(_inputs())
    assert [line.key for line in summary.lines] == [
        LineKey.HEADER,
        LineKey.ORDERS,
        LineKey.MONEY,
        LineKey.FINISHED_ON_TIME,
        LineKey.LATE_AGAINST_PROMISE,
        LineKey.WITHOUT_PROMISE,
        LineKey.COMPLAINTS_NEW,
        LineKey.COMPLAINTS_OPEN,
        LineKey.SPENDING,
    ]
    assert summary.text == "\n".join(
        [
            "Tóm tắt thứ Sáu 25/09/2026, tính đến 19:05.",
            "Nhận 12 đơn mới. Hoàn tất 9 đơn. Huỷ 1 đơn.",
            "Đã thu 1.250.000đ (5 khoản). Tiền mặt 800.000đ. Chuyển khoản 450.000đ.",
            "Giặt xong 6 đơn. 5 trên 6 đơn xong đúng hẹn. "
            "2 đơn không có giờ hẹn, được so với mốc nội bộ 8 giờ.",
            "3 đơn chưa trả khách đã trễ giờ hẹn.",
            "4 đơn chưa trả khách không có giờ hẹn. 1 đơn trong số đó đã quá mốc nội bộ 8 giờ.",
            "1 khiếu nại mới trong ngày.",
            "Còn 2 khiếu nại đang mở.",
            "Chưa ghi khoản chi nào trong ngày.",
        ]
    )
    # Deterministic: the same inputs give the same bytes.
    assert render_summary(_inputs()).text == summary.text


# --- omission -------------------------------------------------------------------------------------


def test_a_source_that_is_not_built_is_omitted_with_its_reason_never_printed_as_zero() -> None:
    summary = render_summary(_inputs())
    omitted = {item.key: item for item in summary.omitted}
    assert set(omitted) == {LineKey.WAITING_PICKUP, LineKey.ACCOUNTS_DUE}
    assert omitted[LineKey.WAITING_PICKUP].reason is OmissionReason.SOURCE_NOT_BUILT
    assert omitted[LineKey.WAITING_PICKUP].source == "UNCLAIMED-001"
    assert omitted[LineKey.ACCOUNTS_DUE].source == "PAYMENT-002"
    assert omitted[LineKey.ACCOUNTS_DUE].note == "Công nợ đến hạn: hệ thống chưa có phần này."
    assert "20 ngày" not in summary.text and "công nợ" not in summary.text


def test_a_past_day_omits_the_live_lines_and_keeps_the_report_lines() -> None:
    live_only = Unavailable(OmissionReason.LIVE_ONLY_TODAY, "SLA_BOARD")
    summary = render_summary(
        _inputs(
            as_of_local=None,
            board=live_only,
            open_complaints=Unavailable(OmissionReason.LIVE_ONLY_TODAY, "INCIDENTS"),
        )
    )
    keys = [line.key for line in summary.lines]
    assert LineKey.LATE_AGAINST_PROMISE not in keys and LineKey.COMPLAINTS_OPEN not in keys
    assert LineKey.FINISHED_ON_TIME in keys and LineKey.COMPLAINTS_NEW in keys
    reasons = {item.key: item.reason for item in summary.omitted}
    assert reasons[LineKey.LATE_AGAINST_PROMISE] is OmissionReason.LIVE_ONLY_TODAY
    assert reasons[LineKey.WITHOUT_PROMISE] is OmissionReason.LIVE_ONLY_TODAY
    assert reasons[LineKey.COMPLAINTS_OPEN] is OmissionReason.LIVE_ONLY_TODAY


def test_no_promise_anywhere_and_no_policy_omits_late_against_promise_but_keeps_the_rest() -> None:
    board = BoardFigures(
        promised=0,
        promised_late=0,
        unpromised=3,
        unpromised_late=2,
        stated_rule_hours=8,
        turnaround_published=False,
    )
    summary = render_summary(_inputs(board=board))
    keys = [line.key for line in summary.lines]
    assert LineKey.LATE_AGAINST_PROMISE not in keys
    assert LineKey.WITHOUT_PROMISE in keys
    omitted = {item.key: item for item in summary.omitted}
    assert omitted[LineKey.LATE_AGAINST_PROMISE].reason is (
        OmissionReason.TURNAROUND_POLICY_UNPUBLISHED
    )
    # A withdrawn policy with promises still on the board: the line is written.
    withdrawn = dataclasses.replace(board, promised=1, promised_late=1)
    assert LineKey.LATE_AGAINST_PROMISE in [
        line.key for line in render_summary(_inputs(board=withdrawn)).lines
    ]


def test_a_reader_the_board_refuses_gets_both_board_lines_omitted_by_role() -> None:
    refused = Unavailable(OmissionReason.ROLE_NOT_PERMITTED, "SLA_BOARD")
    summary = render_summary(_inputs(board=refused))
    omitted = {item.key: item.note for item in summary.omitted}
    assert omitted[LineKey.LATE_AGAINST_PROMISE] == (
        "Đơn trễ giờ hẹn: vai trò của bạn không xem được nguồn số liệu này."
    )
    assert LineKey.WITHOUT_PROMISE in omitted


def test_every_omission_reason_has_words() -> None:
    for reason in OmissionReason:
        summary = render_summary(_inputs(spending=Unavailable(reason)))
        (item,) = [item for item in summary.omitted if item.key is LineKey.SPENDING]
        assert item.note.startswith("Khoản chi trong ngày: ") and item.note.endswith(".")
        assert len(item.note) > len("Khoản chi trong ngày: .")
        assert LineKey.SPENDING not in [line.key for line in summary.lines]


# --- no personal data can reach the text ----------------------------------------------------------


def test_no_input_can_carry_free_text_into_the_summary() -> None:
    """Every field of every input is a number, a date/time, a flag or a fixed token.

    The template's inputs are the only way anything reaches the text, so an input with no
    free-text field is a template that cannot print a name, a phone number, an address or a note.
    The only strings are `net_direction` (IN/OUT), a Sổ thu chi category code and an omission's
    `source` (a slice id), each a token from a closed vocabulary.
    """

    token_fields = {("DayFigures", "net_direction"), ("Unavailable", "source")}
    for cls in (
        DayFigures,
        BoardFigures,
        OpenComplaints,
        SpendingFigures,
        WaitingFigures,
        AccountsDueFigures,
        Unavailable,
    ):
        hints = typing.get_type_hints(cls)
        for field in dataclasses.fields(cls):
            hint = hints[field.name]
            if (cls.__name__, field.name) in token_fields:
                continue
            if field.name == "by_category":
                assert hint == tuple[tuple[str, int, int], ...]
                continue
            assert hint in (int, bool, int | None, OmissionReason), (cls.__name__, field.name)
    # And the category token is printed only through the fixed vocabulary.
    assert set(template.EXPENSE_CATEGORY_VI) == {
        "DIEN",
        "NUOC",
        "HOA_CHAT",
        "TUI_NHAN",
        "LUONG",
        "MAT_BANG",
        "SUA_CHUA",
        "XANG_XE",
        "KHAC",
    }


def test_the_template_reads_no_clock_no_network_and_no_model() -> None:
    source = open(template.__file__, encoding="utf-8").read()  # noqa: SIM115
    for forbidden in (
        "datetime.now",
        "date.today",
        "time.time",
        "import os",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "openai",
        "anthropic",
        "random",
    ):
        assert forbidden not in source, forbidden


def test_a_shop_with_no_account_omits_the_accounts_line_in_its_own_words() -> None:
    """v2 (round 7 wave 2 integration): "feature empty" (spec §7) is an omission, not a zero."""

    summary = render_summary(
        _inputs(accounts_due=Unavailable(OmissionReason.NO_ACCOUNTS, "PAYMENT-002"))
    )
    omitted = {item.key: item for item in summary.omitted}
    assert omitted[LineKey.ACCOUNTS_DUE].reason is OmissionReason.NO_ACCOUNTS
    assert omitted[LineKey.ACCOUNTS_DUE].note == (
        "Công nợ đến hạn: cửa hàng chưa mở công nợ cho khách nào."
    )
    assert "công nợ" not in summary.text
