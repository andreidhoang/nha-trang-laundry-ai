"""The owner's evening summary as a fixed, versioned template (`DAILY-SUMMARY-001`, `DEC-039`).

`DEC-006` -- which model provider may see shop data, and on what legal terms -- is the owner's and
is open, so no language model writes this text. `FR-RPT-007` already said a model could at most
restate figures that versioned queries computed; this module is the part that needs no model at
all: a template that turns the day's figures into short Vietnamese sentences the owner can paste
into Zalo.

**Pure.** Every input is a figure a read model already computed and passed in -- the report, the
SLA board, Sổ thu chi, the open-complaint count -- and the only thing done to a figure here is to
print it. There is no clock, no database, no network and no environment read: the instant the
figures were read at arrives as a parameter, so a summary is reproducible from its inputs.

**Rules the template keeps**, each asserted by a golden test:

* One fact per sentence. A count always carries its unit ("3 đơn", "2 khoản chi"); an amount always
  goes through `format_vnd`, the one money formatter, which only groups digits.
* No figure is derived here. Where the report gives a fraction, the sentence states both halves
  ("5 trên 6 đơn") rather than a difference or a rate the report did not publish.
* No personal data. The inputs carry counts and amounts only -- no name, phone, address, note or
  order identifier ever reaches this module, so none can reach the text.
* A source that cannot answer is not a zero. It arrives as `Unavailable` with a reason, its line is
  left out, and `omitted` names it with that reason in words.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum
from typing import Final

#: The template's published identifier. The digest beside it is taken over this module's rules
#: (`nha_trang_laundry_db.daily_summary.daily_summary_template_version`), and a test pins it: a
#: changed sentence fails the suite until the identifier is read and moved.
DAILY_SUMMARY_TEMPLATE_IDENTIFIER: Final = "daily-summary-v1"

#: `Asia/Ho_Chi_Minh` weekday names, Monday first, as the counter says them.
_WEEKDAY_VI: Final = ("thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ nhật")

#: Sổ thu chi's categories (`ExpenseCategory`) in the owner's words -- the same words
#: `EXPENSE_CATEGORY_VI` prints on the console. Listed here, in the enum's order, because the text
#: is built on the server and the console never re-words it.
EXPENSE_CATEGORY_VI: Final = {
    "DIEN": "Điện",
    "NUOC": "Nước",
    "HOA_CHAT": "Hoá chất",
    "TUI_NHAN": "Túi, nhãn",
    "LUONG": "Lương",
    "MAT_BANG": "Mặt bằng",
    "SUA_CHUA": "Sửa chữa",
    "XANG_XE": "Xăng xe",
    "KHAC": "Khác",
}


class LineKey(StrEnum):
    """Every line the template can write, in the order it writes them."""

    HEADER = "HEADER"
    ORDERS = "ORDERS"
    MONEY = "MONEY"
    FINISHED_ON_TIME = "FINISHED_ON_TIME"
    LATE_AGAINST_PROMISE = "LATE_AGAINST_PROMISE"
    WITHOUT_PROMISE = "WITHOUT_PROMISE"
    WAITING_PICKUP = "WAITING_PICKUP"
    COMPLAINTS_NEW = "COMPLAINTS_NEW"
    COMPLAINTS_OPEN = "COMPLAINTS_OPEN"
    ACCOUNTS_DUE = "ACCOUNTS_DUE"
    SPENDING = "SPENDING"


class OmissionReason(StrEnum):
    """Why a line is not in the summary. Never "the figure was zero": zero is a line."""

    #: The read that would supply the line is not built on this deployment yet (a wave-2 slice).
    SOURCE_NOT_BUILT = "SOURCE_NOT_BUILT"
    #: The source describes the shop as it is now and keeps no history: it can answer for today
    #: only, and a past day's figure was never recorded.
    LIVE_ONLY_TODAY = "LIVE_ONLY_TODAY"
    #: The reader's role may not read the source (the SLA board is not an accountant's read).
    ROLE_NOT_PERMITTED = "ROLE_NOT_PERMITTED"
    #: No turnaround policy is published and no order carries a promise: "0 late against promise"
    #: would be a figure about promises nobody made.
    TURNAROUND_POLICY_UNPUBLISHED = "TURNAROUND_POLICY_UNPUBLISHED"
    #: The source answered with more rows than the summary reads; a count of a truncated list is a
    #: floor, not a figure.
    SOURCE_TRUNCATED = "SOURCE_TRUNCATED"


#: The reason in the owner's words, shown with the omitted line. Fixed text, part of the template.
_OMISSION_NOTE_VI: Final = {
    OmissionReason.SOURCE_NOT_BUILT: "hệ thống chưa có phần này",
    OmissionReason.LIVE_ONLY_TODAY: "chỉ có số của hôm nay, không lưu cho ngày đã qua",
    OmissionReason.ROLE_NOT_PERMITTED: "vai trò của bạn không xem được nguồn số liệu này",
    OmissionReason.TURNAROUND_POLICY_UNPUBLISHED: (
        "chủ tiệm chưa công bố quy tắc hẹn trả, chưa đơn nào có giờ hẹn"
    ),
    OmissionReason.SOURCE_TRUNCATED: "danh sách quá dài để đếm đủ",
}

#: What each omissible line is about, in the owner's words.
_LINE_TOPIC_VI: Final = {
    LineKey.LATE_AGAINST_PROMISE: "Đơn trễ giờ hẹn",
    LineKey.WITHOUT_PROMISE: "Đơn không có giờ hẹn",
    LineKey.WAITING_PICKUP: "Đồ chờ lấy lâu ngày",
    LineKey.COMPLAINTS_OPEN: "Khiếu nại đang mở",
    LineKey.ACCOUNTS_DUE: "Công nợ đến hạn",
    LineKey.SPENDING: "Khoản chi trong ngày",
}


@dataclass(frozen=True, slots=True)
class Unavailable:
    """A source that could not answer, and why. `source` names the slice or read it waits for."""

    reason: OmissionReason
    source: str = ""


@dataclass(frozen=True, slots=True)
class DayFigures:
    """The report's figures for one shop-local day (`report-v3`, window `[day, day]`), copied."""

    orders_created: int
    orders_completed: int
    orders_cancelled: int
    #: `ON_TIME_INTERNAL`: numerator, denominator, and how many of the denominator had no promise.
    finished: int
    finished_on_time: int
    finished_without_promise: int
    #: The stated rule's mark in hours, which judged the orders without a promise.
    stated_rule_hours: int | None
    complaints_opened: int
    #: `MONEY_COLLECTED`, its entries and its split by method; `MONEY_REFUNDED`; `MONEY_NET`.
    collected_vnd: int
    collected_entries: int
    cash_vnd: int
    cash_entries: int
    transfer_vnd: int
    transfer_entries: int
    refunded_vnd: int
    refund_entries: int
    net_vnd: int
    net_direction: str


@dataclass(frozen=True, slots=True)
class BoardFigures:
    """The SLA board read now, counted by what the board itself decided for each row.

    `promised_*` are rows the board measured against the order's own promise (`ORDER_PROMISE`);
    `unpromised_*` are rows it measured by the stated rule (`STATED_RULE`). `*_late` counts the rows
    whose outcome the board gave as `BREACHED`. The board holds an order from acceptance until it
    is handed over, so these are orders not yet returned to the customer.
    """

    promised: int
    promised_late: int
    unpromised: int
    unpromised_late: int
    stated_rule_hours: int | None
    #: Whether the owner's turnaround policy is in force now.
    turnaround_published: bool


@dataclass(frozen=True, slots=True)
class OpenComplaints:
    """Complaints whose status is `OPEN` or `UNDER_REVIEW` now."""

    open_count: int


@dataclass(frozen=True, slots=True)
class SpendingFigures:
    """Sổ thu chi for the day: total, entries, and per category (`category`, amount, entries)."""

    total_vnd: int
    entries: int
    by_category: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True, slots=True)
class WaitingFigures:
    """`UNCLAIMED-001`'s waiting list, counted: ready and not collected for over 20 / 60 days."""

    over_20_days: int
    over_60_days: int


@dataclass(frozen=True, slots=True)
class AccountsDueFigures:
    """`PAYMENT-002`'s account statements: due and not paid, and past their due date."""

    due_accounts: int
    due_vnd: int
    overdue_accounts: int
    overdue_vnd: int


@dataclass(frozen=True, slots=True)
class SummaryInputs:
    """Everything the template reads. `as_of_local` is set only when the day is still being traded
    (the day is the shop's today): the figures are then "so far", and the header says until when."""

    day: date
    as_of_local: time | None
    figures: DayFigures
    board: BoardFigures | Unavailable
    open_complaints: OpenComplaints | Unavailable
    spending: SpendingFigures | Unavailable
    waiting: WaitingFigures | Unavailable
    accounts_due: AccountsDueFigures | Unavailable


#: A figure attached to a line: an integer, or a short token/string (a date, a direction).
Figure = int | str | None


@dataclass(frozen=True, slots=True)
class SummaryLine:
    key: LineKey
    text: str
    figures: tuple[tuple[str, Figure], ...]


@dataclass(frozen=True, slots=True)
class Omission:
    key: LineKey
    reason: OmissionReason
    #: The slice or read the line waits for, when there is one (`UNCLAIMED-001`, `PAYMENT-002`).
    source: str
    #: The line's topic and the reason, in the owner's words: "Công nợ đến hạn: hệ thống chưa có…".
    note: str


@dataclass(frozen=True, slots=True)
class RenderedSummary:
    day: date
    lines: tuple[SummaryLine, ...]
    omitted: tuple[Omission, ...]

    @property
    def text(self) -> str:
        """What *Sao chép* and *Chia sẻ* hand over: the lines, one per row, nothing added."""
        return "\n".join(line.text for line in self.lines)


def format_vnd(amount_vnd: int) -> str:
    """The one money formatter: "1.250.000đ". Groups digits; never rounds, adds or signs.

    A negative, fractional or boolean amount is a contract violation from the read model, not a
    rounding opportunity, so it is refused rather than printed.
    """
    if isinstance(amount_vnd, bool) or not isinstance(amount_vnd, int):
        raise TypeError("a VND amount is a whole number of đồng")
    if amount_vnd < 0:
        raise ValueError("an amount in the summary is a magnitude; direction is a word")
    return f"{amount_vnd:,}".replace(",", ".") + "đ"


def format_count(count: int, unit: str) -> str:
    """A count with its unit: "3 đơn". Vietnamese nouns do not inflect, so one form serves all."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("a count in the summary is a non-negative whole number")
    return f"{count} {unit}"


def format_day(day: date) -> str:
    """ "thứ Sáu 25/09/2026"."""
    return f"{_WEEKDAY_VI[day.weekday()]} {day:%d/%m/%Y}"


def render_summary(inputs: SummaryInputs) -> RenderedSummary:
    """Write the summary. Deterministic: the same inputs give the same lines, byte for byte."""

    lines: list[SummaryLine] = [_header(inputs), _orders(inputs.figures), _money(inputs.figures)]
    omitted: list[Omission] = []
    lines.append(_finished(inputs.figures))

    board = inputs.board
    if isinstance(board, Unavailable):
        omitted.append(_omission(LineKey.LATE_AGAINST_PROMISE, board))
        omitted.append(_omission(LineKey.WITHOUT_PROMISE, board))
    else:
        if board.promised == 0 and not board.turnaround_published:
            omitted.append(
                _omission(
                    LineKey.LATE_AGAINST_PROMISE,
                    Unavailable(OmissionReason.TURNAROUND_POLICY_UNPUBLISHED, "PROMISE-001"),
                )
            )
        else:
            lines.append(_late_against_promise(board))
        lines.append(_without_promise(board))

    _optional(lines, omitted, LineKey.WAITING_PICKUP, inputs.waiting, _waiting)
    lines.append(_complaints_new(inputs.figures))
    _optional(lines, omitted, LineKey.COMPLAINTS_OPEN, inputs.open_complaints, _complaints_open)
    _optional(lines, omitted, LineKey.ACCOUNTS_DUE, inputs.accounts_due, _accounts_due)
    _optional(lines, omitted, LineKey.SPENDING, inputs.spending, _spending)
    return RenderedSummary(day=inputs.day, lines=tuple(lines), omitted=tuple(omitted))


# --- lines ----------------------------------------------------------------------------------------


def _header(inputs: SummaryInputs) -> SummaryLine:
    if inputs.as_of_local is None:
        text = f"Tóm tắt {format_day(inputs.day)}."
        as_of = None
    else:
        as_of = f"{inputs.as_of_local:%H:%M}"
        text = f"Tóm tắt {format_day(inputs.day)}, tính đến {as_of}."
    return SummaryLine(LineKey.HEADER, text, (("date", inputs.day.isoformat()), ("as_of", as_of)))


def _orders(figures: DayFigures) -> SummaryLine:
    sentences = [
        f"Nhận {format_count(figures.orders_created, 'đơn')} mới.",
        f"Hoàn tất {format_count(figures.orders_completed, 'đơn')}.",
    ]
    if figures.orders_cancelled:
        sentences.append(f"Huỷ {format_count(figures.orders_cancelled, 'đơn')}.")
    return SummaryLine(
        LineKey.ORDERS,
        " ".join(sentences),
        (
            ("orders_created", figures.orders_created),
            ("orders_completed", figures.orders_completed),
            ("orders_cancelled", figures.orders_cancelled),
        ),
    )


def _money(figures: DayFigures) -> SummaryLine:
    if figures.collected_entries == 0:
        sentences = ["Chưa thu khoản tiền nào."]
    else:
        sentences = [
            f"Đã thu {format_vnd(figures.collected_vnd)} "
            f"({format_count(figures.collected_entries, 'khoản')}).",
            f"Tiền mặt {format_vnd(figures.cash_vnd)}.",
            f"Chuyển khoản {format_vnd(figures.transfer_vnd)}.",
        ]
    if figures.refund_entries:
        sentences.append(
            f"Hoàn lại khách {format_vnd(figures.refunded_vnd)} "
            f"({format_count(figures.refund_entries, 'khoản')})."
        )
        if figures.net_direction == "OUT":
            sentences.append(f"Tiền hoàn nhiều hơn tiền thu {format_vnd(figures.net_vnd)}.")
        else:
            sentences.append(f"Thu trừ hoàn còn {format_vnd(figures.net_vnd)}.")
    return SummaryLine(
        LineKey.MONEY,
        " ".join(sentences),
        (
            ("collected_vnd", figures.collected_vnd),
            ("collected_entries", figures.collected_entries),
            ("cash_vnd", figures.cash_vnd),
            ("cash_entries", figures.cash_entries),
            ("transfer_vnd", figures.transfer_vnd),
            ("transfer_entries", figures.transfer_entries),
            ("refunded_vnd", figures.refunded_vnd),
            ("refund_entries", figures.refund_entries),
            ("net_vnd", figures.net_vnd),
            ("net_direction", figures.net_direction),
        ),
    )


def _mark(hours: int | None) -> str:
    return "mốc nội bộ" if hours is None else f"mốc nội bộ {hours} giờ"


def _finished(figures: DayFigures) -> SummaryLine:
    if figures.finished == 0:
        sentences = ["Chưa có đơn nào giặt xong."]
    else:
        sentences = [
            f"Giặt xong {format_count(figures.finished, 'đơn')}.",
            f"{figures.finished_on_time} trên {format_count(figures.finished, 'đơn')} "
            "xong đúng hẹn.",
        ]
        if figures.finished_without_promise:
            sentences.append(
                f"{format_count(figures.finished_without_promise, 'đơn')} không có giờ hẹn, "
                f"được so với {_mark(figures.stated_rule_hours)}."
            )
    return SummaryLine(
        LineKey.FINISHED_ON_TIME,
        " ".join(sentences),
        (
            ("finished", figures.finished),
            ("finished_on_time", figures.finished_on_time),
            ("finished_without_promise", figures.finished_without_promise),
        ),
    )


def _late_against_promise(board: BoardFigures) -> SummaryLine:
    if board.promised_late == 0:
        text = "Không có đơn chưa trả khách nào trễ giờ hẹn."
    else:
        text = f"{format_count(board.promised_late, 'đơn')} chưa trả khách đã trễ giờ hẹn."
    return SummaryLine(
        LineKey.LATE_AGAINST_PROMISE,
        text,
        (("promised", board.promised), ("promised_late", board.promised_late)),
    )


def _without_promise(board: BoardFigures) -> SummaryLine:
    if board.unpromised == 0:
        text = "Không có đơn chưa trả khách nào thiếu giờ hẹn."
    else:
        text = f"{format_count(board.unpromised, 'đơn')} chưa trả khách không có giờ hẹn."
        if board.unpromised_late:
            text += (
                f" {format_count(board.unpromised_late, 'đơn')} trong số đó đã quá "
                f"{_mark(board.stated_rule_hours)}."
            )
    return SummaryLine(
        LineKey.WITHOUT_PROMISE,
        text,
        (("unpromised", board.unpromised), ("unpromised_late", board.unpromised_late)),
    )


def _waiting(figures: WaitingFigures) -> SummaryLine:
    sentences = [
        f"{format_count(figures.over_20_days, 'đơn')} giặt xong chờ khách lấy quá 20 ngày.",
    ]
    if figures.over_60_days:
        sentences.append(f"{format_count(figures.over_60_days, 'đơn')} đã chờ quá 60 ngày.")
    return SummaryLine(
        LineKey.WAITING_PICKUP,
        " ".join(sentences),
        (("over_20_days", figures.over_20_days), ("over_60_days", figures.over_60_days)),
    )


def _complaints_new(figures: DayFigures) -> SummaryLine:
    if figures.complaints_opened == 0:
        text = "Không có khiếu nại mới."
    else:
        text = f"{format_count(figures.complaints_opened, 'khiếu nại')} mới trong ngày."
    return SummaryLine(
        LineKey.COMPLAINTS_NEW, text, (("complaints_opened", figures.complaints_opened),)
    )


def _complaints_open(figures: OpenComplaints) -> SummaryLine:
    if figures.open_count == 0:
        text = "Không còn khiếu nại nào đang mở."
    else:
        text = f"Còn {format_count(figures.open_count, 'khiếu nại')} đang mở."
    return SummaryLine(LineKey.COMPLAINTS_OPEN, text, (("open_count", figures.open_count),))


def _accounts_due(figures: AccountsDueFigures) -> SummaryLine:
    sentences = [
        f"{format_count(figures.due_accounts, 'khách công nợ')} đến hạn trả, "
        f"tổng {format_vnd(figures.due_vnd)}.",
    ]
    if figures.overdue_accounts:
        sentences.append(
            f"{format_count(figures.overdue_accounts, 'khách công nợ')} đã quá hạn, "
            f"tổng {format_vnd(figures.overdue_vnd)}."
        )
    return SummaryLine(
        LineKey.ACCOUNTS_DUE,
        " ".join(sentences),
        (
            ("due_accounts", figures.due_accounts),
            ("due_vnd", figures.due_vnd),
            ("overdue_accounts", figures.overdue_accounts),
            ("overdue_vnd", figures.overdue_vnd),
        ),
    )


def _spending(figures: SpendingFigures) -> SummaryLine:
    if figures.entries == 0:
        sentences = ["Chưa ghi khoản chi nào trong ngày."]
    else:
        sentences = [
            f"Ghi {format_count(figures.entries, 'khoản chi')}, "
            f"tổng {format_vnd(figures.total_vnd)}."
        ]
        sentences.extend(
            f"{EXPENSE_CATEGORY_VI.get(category, category)} {format_vnd(amount)}."
            for category, amount, entries in figures.by_category
            if entries
        )
    return SummaryLine(
        LineKey.SPENDING,
        " ".join(sentences),
        (
            ("spending_vnd", figures.total_vnd),
            ("spending_entries", figures.entries),
            *(
                (f"spending_{category.lower()}_vnd", amount)
                for category, amount, entries in figures.by_category
                if entries
            ),
        ),
    )


# --- omission ------------------------------------------------------------------------------------


def _omission(key: LineKey, missing: Unavailable) -> Omission:
    return Omission(
        key=key,
        reason=missing.reason,
        source=missing.source,
        note=f"{_LINE_TOPIC_VI[key]}: {_OMISSION_NOTE_VI[missing.reason]}.",
    )


def _optional[T](
    lines: list[SummaryLine],
    omitted: list[Omission],
    key: LineKey,
    source: T | Unavailable,
    write: Callable[[T], SummaryLine],
) -> None:
    if isinstance(source, Unavailable):
        omitted.append(_omission(key, source))
        return
    lines.append(write(source))


__all__ = [
    "DAILY_SUMMARY_TEMPLATE_IDENTIFIER",
    "EXPENSE_CATEGORY_VI",
    "AccountsDueFigures",
    "BoardFigures",
    "DayFigures",
    "LineKey",
    "Omission",
    "OmissionReason",
    "OpenComplaints",
    "RenderedSummary",
    "SpendingFigures",
    "SummaryInputs",
    "SummaryLine",
    "Unavailable",
    "WaitingFigures",
    "format_count",
    "format_day",
    "format_vnd",
    "render_summary",
]
