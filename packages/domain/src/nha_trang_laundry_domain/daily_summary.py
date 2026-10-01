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
#:
#: v2 (round 7 wave 2 integration): the two hooks are wired -- "Đồ chờ lấy lâu ngày" from
#: `UNCLAIMED-001`'s waiting list and "Công nợ đến hạn" from `PAYMENT-002`'s ledgers -- so a summary
#: that left both out as not built now prints them, and a shop that has opened no account omits the
#: accounts line with its own reason (`NO_ACCOUNTS`).
#:
#: v3 (round 8, `SUMMARY-ATTENTION-001`, `DEC-044`): a *Cần chú ý* block heads the summary --
#: at most five computed lines (late deliveries undecided, orders late against their promise,
#: pickup reminders and the free-storage days running out, invoices waiting to be issued, and the
#: day against the same weekday of the previous four weeks with last month's missing cost
#: categories), or one "nothing needs attention" line when every source answered and none fired.
#:
#: v4 (round 9, GOODS-AND-DRAWER-009, review M4): the money line says how refunds went back and
#: what the drawer did -- cash in minus cash handed back -- beside "Thu trừ hoàn" (every method),
#: and names the refunds of unknown method (written before `0067`) that the drawer figure excludes.
DAILY_SUMMARY_TEMPLATE_IDENTIFIER: Final = "daily-summary-v5"

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
    #: The *Cần chú ý* block (`DEC-044`), in the decision's order, between the header and the day.
    ATTENTION = "ATTENTION"
    ATTN_LATE_DELIVERIES = "ATTN_LATE_DELIVERIES"
    ATTN_OVERDUE = "ATTN_OVERDUE"
    ATTN_PICKUP = "ATTN_PICKUP"
    ATTN_INVOICES = "ATTN_INVOICES"
    ATTN_NUMBERS = "ATTN_NUMBERS"
    ATTN_NONE = "ATTN_NONE"
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
    #: The shop has opened no customer account (`PAYMENT-002`, `DEC-035`): "0 khách công nợ đến hạn"
    #: would be a figure about a feature the shop does not use (spec §7: "feature empty").
    NO_ACCOUNTS = "NO_ACCOUNTS"
    #: The day is still being traded before closing time: a half day against four whole ones
    #: would always read "lower than usual".
    DAY_NOT_OVER = "DAY_NOT_OVER"
    #: Fewer than three of the previous four same weekdays had any trade: no usual to compare to.
    TOO_LITTLE_HISTORY = "TOO_LITTLE_HISTORY"
    #: No storage policy is published: there is no free-storage period to run out.
    STORAGE_POLICY_UNPUBLISHED = "STORAGE_POLICY_UNPUBLISHED"
    #: No remedy policy is published: there is no late threshold and no credit to decide.
    REMEDY_POLICY_UNPUBLISHED = "REMEDY_POLICY_UNPUBLISHED"


#: The reason in the owner's words, shown with the omitted line. Fixed text, part of the template.
_OMISSION_NOTE_VI: Final = {
    OmissionReason.SOURCE_NOT_BUILT: "hệ thống chưa có phần này",
    OmissionReason.LIVE_ONLY_TODAY: "chỉ có số của hôm nay, không lưu cho ngày đã qua",
    OmissionReason.ROLE_NOT_PERMITTED: "vai trò của bạn không xem được nguồn số liệu này",
    OmissionReason.TURNAROUND_POLICY_UNPUBLISHED: (
        "chủ tiệm chưa công bố quy tắc hẹn trả, chưa đơn nào có giờ hẹn"
    ),
    OmissionReason.SOURCE_TRUNCATED: "danh sách quá dài để đếm đủ",
    OmissionReason.NO_ACCOUNTS: "cửa hàng chưa mở công nợ cho khách nào",
    OmissionReason.DAY_NOT_OVER: "chỉ so sánh sau giờ đóng cửa",
    OmissionReason.TOO_LITTLE_HISTORY: "chưa đủ 3 tuần có số liệu để so sánh",
    OmissionReason.STORAGE_POLICY_UNPUBLISHED: "chủ tiệm chưa công bố quy định lưu kho",
    OmissionReason.REMEDY_POLICY_UNPUBLISHED: "chủ tiệm chưa công bố quy định bồi hoàn",
}

#: What each omissible line is about, in the owner's words.
_LINE_TOPIC_VI: Final = {
    LineKey.LATE_AGAINST_PROMISE: "Đơn trễ giờ hẹn",
    LineKey.WITHOUT_PROMISE: "Đơn không có giờ hẹn",
    LineKey.WAITING_PICKUP: "Đồ chờ lấy lâu ngày",
    LineKey.COMPLAINTS_OPEN: "Khiếu nại đang mở",
    LineKey.ACCOUNTS_DUE: "Công nợ đến hạn",
    LineKey.SPENDING: "Khoản chi trong ngày",
    LineKey.ATTN_LATE_DELIVERIES: "Đơn giao trễ chưa xử lý",
    LineKey.ATTN_PICKUP: "Nhắc khách lấy đồ",
    LineKey.ATTN_INVOICES: "Hóa đơn cần xuất",
    LineKey.ATTN_NUMBERS: "So với các tuần trước",
}

#: How many lines the *Cần chú ý* block may hold (`DEC-044`): a list the owner reads at a glance.
ATTENTION_MAX_LINES: Final = 5

#: Invoice asks older than this many days are named (`DEC-044` line 4).
INVOICE_STALE_DAYS: Final = 3

#: The free-storage days are "running out" within this many days of the fee's first day.
FEE_SOON_DAYS: Final = 3

#: How many previous same weekdays the day is compared with, and how many must have had trade.
COMPARE_WEEKS: Final = 4
COMPARE_MIN_WEEKS: Final = 3

#: The band, in percent of the usual, outside which a figure is named (`DEC-044`).
COMPARE_LOW_PERCENT: Final = 70
COMPARE_HIGH_PERCENT: Final = 130

#: Last month's cost categories are asked for only after this day of the month: bills for a month
#: arrive in the first days of the next, and a nudge on the 2nd would be noise.
MISSING_COSTS_AFTER_DAY: Final = 10


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
    #: GOODS-AND-DRAWER-009 (review M4): `MONEY_REFUNDED` split by how the money went back, and
    #: `MONEY_DRAWER` -- cash in minus cash handed back -- which excludes the unknown-method ones.
    refunded_cash_vnd: int
    refunded_cash_entries: int
    refunded_transfer_vnd: int
    refunded_transfer_entries: int
    refunded_unknown_vnd: int
    refunded_unknown_entries: int
    drawer_vnd: int
    drawer_direction: str


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


class Direction(StrEnum):
    """Where a figure sits against the usual. Decided by `compare_to_usual`, printed as words."""

    LOW = "LOW"
    USUAL = "USUAL"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class UsualFigure:
    """One figure of the day beside its usual: the mean of the same weekday over the weeks that
    had trade, rounded half up to a whole number by `compare_to_usual`, never by the template."""

    today: int
    usual: int
    direction: Direction


@dataclass(frozen=True, slots=True)
class DayComparison:
    """The day against the same weekday of the previous weeks that had any trade."""

    weeks_with_data: int
    collected: UsualFigure
    orders: UsualFigure


@dataclass(frozen=True, slots=True)
class MissingCosts:
    """Last month's core margin categories with no line in Sổ thu chi (`CORE_MARGIN_CATEGORIES`
    order), and the month they are missing from (its first day)."""

    month: date
    categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FeeSoon:
    """Laundry whose free-storage days end within `FEE_SOON_DAYS`, under the published policy."""

    count: int
    free_days: int


@dataclass(frozen=True, slots=True)
class AttentionFacts:
    """The *Cần chú ý* sources (`DEC-044`). Counts and money only; each may be `Unavailable`."""

    #: `LATE-CREDIT-002`: deliveries past the threshold with no decision yet.
    late_deliveries_undecided: int | Unavailable
    #: `PICKUP-REMIND-001`: reminders due and not done.
    reminders_due: int | Unavailable
    #: `UNCLAIMED-001` under the published storage policy.
    fee_soon: FeeSoon | Unavailable
    #: `EINVOICE-REQUEST-001`: invoice asks still open after `INVOICE_STALE_DAYS`.
    invoices_waiting: int | Unavailable
    comparison: DayComparison | Unavailable
    #: `None` before `MISSING_COSTS_AFTER_DAY`, or when last month has every category.
    missing_costs: MissingCosts | Unavailable | None


def _unavailable_attention(source: str) -> AttentionFacts:
    missing = Unavailable(OmissionReason.SOURCE_NOT_BUILT, source)
    return AttentionFacts(
        late_deliveries_undecided=missing,
        reminders_due=missing,
        fee_soon=missing,
        invoices_waiting=missing,
        comparison=missing,
        missing_costs=missing,
    )


#: What a caller that reads no attention source passes: every source "not built".
NO_ATTENTION: Final = _unavailable_attention("SUMMARY-ATTENTION-001")


def compare_to_usual(today: int, previous: tuple[int, ...]) -> UsualFigure:
    """`today` against the mean of `previous` (the weeks with trade), in whole numbers only.

    The mean is rounded half up (`(2*sum + n) // (2*n)`); the band test multiplies rather than
    divides, `100*today < 70*usual` or `100*today > 130*usual`, so no fraction is ever formed.
    """
    for value in (today, *previous):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("a compared figure is a non-negative whole number")
    if not previous:
        raise ValueError("a usual needs at least one previous week")
    count = len(previous)
    usual = (2 * sum(previous) + count) // (2 * count)
    if 100 * today < COMPARE_LOW_PERCENT * usual:
        direction = Direction.LOW
    elif 100 * today > COMPARE_HIGH_PERCENT * usual:
        direction = Direction.HIGH
    else:
        direction = Direction.USUAL
    return UsualFigure(today=today, usual=usual, direction=direction)


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
    attention: AttentionFacts = NO_ATTENTION


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

    lines: list[SummaryLine] = [_header(inputs)]
    omitted: list[Omission] = []
    lines.extend(_attention(inputs, omitted))
    lines.extend((_orders(inputs.figures), _money(inputs.figures)))
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
        if figures.refunded_cash_entries:
            sentences.append(f"Hoàn tiền mặt {format_vnd(figures.refunded_cash_vnd)}.")
        if figures.refunded_transfer_entries:
            sentences.append(f"Hoàn chuyển khoản {format_vnd(figures.refunded_transfer_vnd)}.")
        if figures.net_direction == "OUT":
            sentences.append(f"Tiền hoàn nhiều hơn tiền thu {format_vnd(figures.net_vnd)}.")
        else:
            sentences.append(f"Thu trừ hoàn còn {format_vnd(figures.net_vnd)}.")
        # GOODS-AND-DRAWER-009: the drawer is cash only, said as a word, never a minus sign.
        if figures.drawer_vnd == 0:
            sentences.append("Tiền mặt trong két không đổi.")
        else:
            moved = "giảm" if figures.drawer_direction == "OUT" else "tăng"
            sentences.append(f"Tiền mặt trong két {moved} {format_vnd(figures.drawer_vnd)}.")
        if figures.refunded_unknown_entries:
            sentences.append(
                "Số tiền trong két chưa tính "
                f"{format_count(figures.refunded_unknown_entries, 'khoản hoàn')} chưa rõ cách hoàn "
                f"({format_vnd(figures.refunded_unknown_vnd)})."
            )
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
            ("refunded_cash_vnd", figures.refunded_cash_vnd),
            ("refunded_cash_entries", figures.refunded_cash_entries),
            ("refunded_transfer_vnd", figures.refunded_transfer_vnd),
            ("refunded_transfer_entries", figures.refunded_transfer_entries),
            ("refunded_unknown_vnd", figures.refunded_unknown_vnd),
            ("refunded_unknown_entries", figures.refunded_unknown_entries),
            ("drawer_vnd", figures.drawer_vnd),
            ("drawer_direction", figures.drawer_direction),
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


# --- Cần chú ý (`DEC-044`) ------------------------------------------------------------------------


def _attention(inputs: SummaryInputs, omitted: list[Omission]) -> list[SummaryLine]:
    """The block: a title line and at most `ATTENTION_MAX_LINES` lines, each only when it fires.

    "Nothing needs attention" is said only when every source answered: a source that could not
    answer is not a quiet one, so with one missing and none firing the block is left out.
    """
    facts = inputs.attention
    found: list[SummaryLine] = []
    complete = True

    def missing(key: LineKey, source: Unavailable) -> None:
        nonlocal complete
        complete = False
        # Only reasons the owner can act on or should know are listed: a source not built yet is
        # the build's business, and a past day's live list is simply not the block's to answer.
        if source.reason not in _SILENT_IN_ATTENTION:
            omitted.append(_omission(key, source))

    late = facts.late_deliveries_undecided
    if isinstance(late, Unavailable):
        missing(LineKey.ATTN_LATE_DELIVERIES, late)
    elif late:
        found.append(
            SummaryLine(
                LineKey.ATTN_LATE_DELIVERIES,
                f"{format_count(late, 'đơn')} giao trễ quá 2 giờ chưa xử lý giảm trừ.",
                (("late_deliveries_undecided", late),),
            )
        )

    board = inputs.board
    if isinstance(board, Unavailable):
        complete = False
    elif board.promised_late:
        found.append(
            SummaryLine(
                LineKey.ATTN_OVERDUE,
                f"{format_count(board.promised_late, 'đơn')} chưa trả khách đã trễ giờ hẹn.",
                (("promised_late", board.promised_late),),
            )
        )

    pickup = _pickup_sentences(facts, missing)
    if pickup:
        sentences, figures = pickup
        found.append(SummaryLine(LineKey.ATTN_PICKUP, " ".join(sentences), tuple(figures)))

    stale = facts.invoices_waiting
    if isinstance(stale, Unavailable):
        missing(LineKey.ATTN_INVOICES, stale)
    elif stale:
        found.append(
            SummaryLine(
                LineKey.ATTN_INVOICES,
                f"{format_count(stale, 'yêu cầu hóa đơn')} đã chờ quá "
                f"{INVOICE_STALE_DAYS} ngày chưa xuất.",
                (("invoices_waiting", stale),),
            )
        )

    numbers = _numbers_sentences(facts, missing)
    if numbers:
        sentences, figures = numbers
        found.append(SummaryLine(LineKey.ATTN_NUMBERS, " ".join(sentences), tuple(figures)))

    if found:
        title = SummaryLine(LineKey.ATTENTION, "Cần chú ý:", (("attention", len(found)),))
        return [title, *(_bulleted(line) for line in found[:ATTENTION_MAX_LINES])]
    if complete:
        return [SummaryLine(LineKey.ATTN_NONE, "Không có việc cần chú ý.", (("attention", 0),))]
    return []


_SILENT_IN_ATTENTION: Final = frozenset(
    {
        OmissionReason.SOURCE_NOT_BUILT,
        OmissionReason.LIVE_ONLY_TODAY,
        OmissionReason.STORAGE_POLICY_UNPUBLISHED,
        OmissionReason.REMEDY_POLICY_UNPUBLISHED,
    }
)


def _bulleted(line: SummaryLine) -> SummaryLine:
    return SummaryLine(line.key, f"- {line.text}", line.figures)


def _pickup_sentences(
    facts: AttentionFacts, missing: Callable[[LineKey, Unavailable], None]
) -> tuple[list[str], list[tuple[str, Figure]]] | None:
    sentences: list[str] = []
    figures: list[tuple[str, Figure]] = []
    due = facts.reminders_due
    if isinstance(due, Unavailable):
        missing(LineKey.ATTN_PICKUP, due)
    elif due:
        sentences.append(f"{format_count(due, 'lần nhắc khách lấy đồ')} đến hạn chưa làm.")
        figures.append(("reminders_due", due))
    soon = facts.fee_soon
    if isinstance(soon, Unavailable):
        # No published storage policy means no free period to run out: not a gap in the block.
        if soon.reason is not OmissionReason.STORAGE_POLICY_UNPUBLISHED:
            missing(LineKey.ATTN_PICKUP, soon)
    elif soon.count:
        sentences.append(
            f"{format_count(soon.count, 'đơn')} chờ lấy sắp hết {soon.free_days} ngày giữ miễn phí."
        )
        figures.append(("fee_soon", soon.count))
    return (sentences, figures) if sentences else None


def _numbers_sentences(
    facts: AttentionFacts, missing: Callable[[LineKey, Unavailable], None]
) -> tuple[list[str], list[tuple[str, Figure]]] | None:
    sentences: list[str] = []
    figures: list[tuple[str, Figure]] = []
    comparison = facts.comparison
    if isinstance(comparison, Unavailable):
        missing(LineKey.ATTN_NUMBERS, comparison)
    else:
        weeks = comparison.weeks_with_data
        collected, orders = comparison.collected, comparison.orders
        if collected.direction is not Direction.USUAL:
            sentences.append(
                f"Tiền thu {format_vnd(collected.today)}, {_direction_vi(collected.direction)} "
                f"(trung bình {weeks} tuần trước cùng thứ {format_vnd(collected.usual)})."
            )
            figures.extend((("collected_vnd", collected.today), ("usual_vnd", collected.usual)))
        if orders.direction is not Direction.USUAL:
            sentences.append(
                f"Nhận {format_count(orders.today, 'đơn')}, {_direction_vi(orders.direction)} "
                f"(trung bình {weeks} tuần trước cùng thứ {format_count(orders.usual, 'đơn')})."
            )
            figures.extend((("orders_created", orders.today), ("usual_orders", orders.usual)))
    costs = facts.missing_costs
    if isinstance(costs, Unavailable):
        missing(LineKey.ATTN_NUMBERS, costs)
    elif costs is not None and costs.categories:
        unknown = [c for c in costs.categories if c not in EXPENSE_CATEGORY_VI]
        if unknown:
            raise ValueError("a missing cost category is a Sổ thu chi code")
        names = ", ".join(EXPENSE_CATEGORY_VI[c] for c in costs.categories)
        sentences.append(
            f"Tháng {costs.month:%m/%Y} chưa ghi chi {names}: chưa tính được lãi tháng đó."
        )
        figures.append(("missing_costs_month", costs.month.isoformat()))
    return (sentences, figures) if sentences else None


def _direction_vi(direction: Direction) -> str:
    return "thấp hơn thường lệ" if direction is Direction.LOW else "cao hơn thường lệ"


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
    "ATTENTION_MAX_LINES",
    "COMPARE_HIGH_PERCENT",
    "COMPARE_LOW_PERCENT",
    "COMPARE_MIN_WEEKS",
    "COMPARE_WEEKS",
    "DAILY_SUMMARY_TEMPLATE_IDENTIFIER",
    "EXPENSE_CATEGORY_VI",
    "FEE_SOON_DAYS",
    "INVOICE_STALE_DAYS",
    "MISSING_COSTS_AFTER_DAY",
    "NO_ATTENTION",
    "AccountsDueFigures",
    "AttentionFacts",
    "BoardFigures",
    "DayComparison",
    "DayFigures",
    "Direction",
    "FeeSoon",
    "LineKey",
    "MissingCosts",
    "Omission",
    "OmissionReason",
    "OpenComplaints",
    "RenderedSummary",
    "SpendingFigures",
    "SummaryInputs",
    "SummaryLine",
    "Unavailable",
    "UsualFigure",
    "WaitingFigures",
    "compare_to_usual",
    "format_count",
    "format_day",
    "format_vnd",
    "render_summary",
]
