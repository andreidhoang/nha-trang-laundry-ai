"""Pickup reminders: which one is due, who can receive it, and its fixed text. `PICKUP-REMIND-001`.

`DEC-043` (2026-09-28). What a Vietnamese shop loses today is simple: nobody remembers to message
the customer on day 3, day 7 and day 14, and the laundry reaches the storage fee with no attempt on
record. The server decides who, when and what; a person sends it from the shop's own phone.

* **The schedule, in shop days from *đồ đã xong*.** Day 0 `READY`, then `DAY_3`, `DAY_7` and
  `DAY_14`. The fifth, `BEFORE_FEE`, falls on the last shop day before the storage fee starts --
  day `free_days` of the owner's published storage policy -- and exists only while that policy is
  published. Days are shop-local calendar days (Asia/Ho_Chi_Minh) from the same ready time
  `UNCLAIMED-001` counts from (`unclaimed.days_waiting`): the ready day is day 0.
* **Only the newest due step shows.** A step is due from its day until a contact attempt is recorded
  for it; a later step supersedes an earlier one nobody did. On a day two steps share (a policy
  whose free days end on day 3, 7 or 14), `BEFORE_FEE` is the later: its text carries the fee.
* **The schedule ends where the storage fee begins.** With a published policy, nothing is due from
  day `free_days + 1`: `DEC-043` hands the order to *Đồ chờ lấy* from there. A fixed step that
  would fall on or after that day is not in the schedule. Without a policy there is no fee day, and
  the last step (`DAY_14`) stays due until it is done.
* **The text is fixed** (`pickup-reminder-v1`), built from the facts passed in: the shop's name,
  the ticket and its day, the ready day, the balance, the opening hours and -- for `BEFORE_FEE` --
  the owner's published fee per day, cap and the day it starts, quoted as the receipt quotes them.
  It carries no name and no phone number. No model writes it and nothing here sends it.

Pure: no clock, no database, no environment. Every fact and the instant judged are parameters.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from enum import StrEnum
from typing import Final

from nha_trang_laundry_domain.unclaimed import StoragePolicy

#: The template's version. A change to any sentence below is a new version, never an edit.
PICKUP_REMINDER_TEMPLATE: Final = "pickup-reminder-v1"
PICKUP_REMINDER_DECISION: Final = "DEC-043"


class ReminderStep(StrEnum):
    """One reminder of the schedule, in schedule order."""

    READY = "READY"
    DAY_3 = "DAY_3"
    DAY_7 = "DAY_7"
    DAY_14 = "DAY_14"
    BEFORE_FEE = "BEFORE_FEE"


#: The four fixed steps and their shop day (days since the laundry was ready).
FIXED_STEP_DAYS: Final[tuple[tuple[ReminderStep, int], ...]] = (
    (ReminderStep.READY, 0),
    (ReminderStep.DAY_3, 3),
    (ReminderStep.DAY_7, 7),
    (ReminderStep.DAY_14, 14),
)


class Reachability(StrEnum):
    """How the shop can reach the customer about this order."""

    #: A phone number is on the customer's record (not erased).
    PHONE = "PHONE"
    #: No phone, but the order came in on a chat channel the shop can answer on.
    CHAT = "CHAT"
    #: A ticket alone: nobody to message. Counted, never hidden.
    NONE = "NONE"


class ReminderRefusal(StrEnum):
    """Why a reminder's text or a reminder attempt is refused, beside the egress guard's codes."""

    #: No phone on the customer's record and no chat channel (`Reachability.NONE`).
    NO_CONTACT = "NO_CONTACT"
    #: No step is due for this order now (not waiting, done, or past the fee day).
    NO_REMINDER_DUE = "NO_REMINDER_DUE"
    #: The step named is not the one due now (the list moved under the screen).
    REMINDER_STEP_NOT_DUE = "REMINDER_STEP_NOT_DUE"
    #: `MESSAGE_SENT` records a reminder's message, so it names the step it sent.
    REMINDER_STEP_REQUIRED = "REMINDER_STEP_REQUIRED"
    #: `MESSAGE_SENT` is a message: Zalo or SMS. A call keeps its own outcomes.
    MESSAGE_SENT_CHANNEL_INVALID = "MESSAGE_SENT_CHANNEL_INVALID"


def schedule(policy: StoragePolicy | None) -> tuple[tuple[ReminderStep, int], ...]:
    """Every step with its shop day, in the order they fall (ties: `BEFORE_FEE` last).

    With a policy, the fixed steps that would fall after the free days end are dropped and
    `BEFORE_FEE` is added on day `free_days`; without one, the four fixed steps.
    """

    if policy is None:
        return FIXED_STEP_DAYS
    last_day = policy.free_days
    kept = tuple((step, day) for step, day in FIXED_STEP_DAYS if day <= last_day)
    return (*kept, (ReminderStep.BEFORE_FEE, last_day))


def _waited(ready_on: date, today: date) -> int:
    return max(0, (today - ready_on).days)


def reminder_steps(
    ready_on: date, today: date, storage_policy: StoragePolicy | None
) -> tuple[ReminderStep, ...]:
    """The steps whose day has come, in schedule order -- none once the storage fee has started.

    `ready_on` and `today` are shop-local calendar days (`unclaimed.shop_date`). A clock that
    reads earlier than the ready day counts as the ready day, as `unclaimed.days_waiting` does.
    """

    waited = _waited(ready_on, today)
    if storage_policy is not None and waited > storage_policy.free_days:
        return ()
    return tuple(step for step, day in schedule(storage_policy) if day <= waited)


def current_reminder(
    ready_on: date,
    today: date,
    storage_policy: StoragePolicy | None,
    done: Iterable[ReminderStep],
) -> ReminderStep | None:
    """The one reminder to show now: the newest due step, unless an attempt already did it.

    An earlier step nobody did is superseded by a later one, so it never comes back.
    """

    due = reminder_steps(ready_on, today, storage_policy)
    if not due:
        return None
    newest = due[-1]
    return None if newest in frozenset(done) else newest


def step_day(step: ReminderStep, storage_policy: StoragePolicy | None) -> int | None:
    """The shop day `step` falls on under `storage_policy`, or None when it is not scheduled."""

    return next((day for named, day in schedule(storage_policy) if named is step), None)


def fee_starts_on(ready_on: date, policy: StoragePolicy) -> date:
    """The first shop day a fee is charged for: the day after the free days end (`DEC-036`)."""

    return ready_on + timedelta(days=policy.free_days + 1)


def reachability(*, has_phone: bool, has_chat: bool) -> Reachability:
    if has_phone:
        return Reachability.PHONE
    if has_chat:
        return Reachability.CHAT
    return Reachability.NONE


_NATIONAL: Final = re.compile(r"^0\d{9,10}$")


def zalo_url(national_phone: str | None) -> str | None:
    """`https://zalo.me/<số điện thoại>` for a national number (`0905123456`); None otherwise.

    Zalo's public link opens a chat with that number's account on the staff member's own phone or
    computer. The software sends nothing through it.
    """

    if national_phone is None or not _NATIONAL.fullmatch(national_phone):
        return None
    return f"https://zalo.me/{national_phone}"


# --- the egress answer, over every channel the reminder is judged on -----------------------------

#: The suppression refusals of `service_messaging.TransactionalRefusal`, strongest first: a STOP is
#: the truest reason and is the one staff read.
_SUPPRESSION_ORDER: Final = ("SUPPRESSED", "PENDING_REVIEW", "SUPPRESSION_UNKNOWN")


@dataclass(frozen=True, slots=True)
class EgressVerdict:
    """One TRANSACTIONAL egress answer for one (contact, channel): allowed, or its refusal code."""

    allowed: bool
    reason_code: str | None


def combine_egress(verdicts: Sequence[EgressVerdict]) -> str | None:
    """The reminder's refusal code over every (contact, channel) it was judged on, or None.

    A reminder goes out from the shop's own Zalo or SMS, which is not a channel the system models,
    so a block on *any* of the customer's references refuses it (`SUPPRESSED` first). Otherwise one
    allowed judgement is enough; with none, the policy's absence is named before a missing basis.
    No judgement at all is not an allowance.
    """

    codes = [verdict.reason_code for verdict in verdicts if not verdict.allowed]
    for code in _SUPPRESSION_ORDER:
        if code in codes:
            return code
    if any(verdict.allowed for verdict in verdicts):
        return None
    if "MESSAGING_POLICY_UNPUBLISHED" in codes:
        return "MESSAGING_POLICY_UNPUBLISHED"
    return "NO_SERVICE_BASIS"


# --- the text -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReminderFee:
    """The owner's published figures, as `BEFORE_FEE` quotes them."""

    fee_per_started_day_vnd: int
    fee_cap_percent: int
    starts_on: date


@dataclass(frozen=True, slots=True)
class ReminderFacts:
    """Everything the text is built from. No name, no phone: there is no field for either."""

    #: The store's registered name; None for a store registered without one.
    shop_name: str | None
    #: The counter ticket and the day it was issued; None for an order that came in by chat.
    ticket_number: int | None
    ticket_issued_on: date | None
    #: The shop day the order was taken in (for an order with no ticket).
    received_on: date
    ready_on: date
    days_waiting: int
    #: What is still owed; None when the quote has no single total (the shop says it at pickup).
    remaining_vnd: int | None
    #: The published opening hours, or None when the owner has not published them.
    opening_hours: tuple[time, time] | None
    #: `BEFORE_FEE` only.
    fee: ReminderFee | None = None


def _day(value: date) -> str:
    return f"{value.day:02d}/{value.month:02d}/{value.year}"


def _vnd(amount: int) -> str:
    """`5000` -> `5.000 ₫`, as the receipt's storage line writes it (`receipt_line_vi`)."""

    return f"{amount:,}".replace(",", ".") + " ₫"


def _clock(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _reference(facts: ReminderFacts) -> str:
    if facts.ticket_number is not None and facts.ticket_issued_on is not None:
        return f"phiếu số {facts.ticket_number} (nhận ngày {_day(facts.ticket_issued_on)})"
    return f"đơn nhận ngày {_day(facts.received_on)}"


def reminder_text(step: ReminderStep, facts: ReminderFacts) -> str:
    """The `pickup-reminder-v1` text for one step: the lines a person copies into Zalo or SMS.

    Raises `ValueError` for `BEFORE_FEE` without the published fee, or for a negative figure.
    """

    if facts.days_waiting < 0 or (facts.remaining_vnd is not None and facts.remaining_vnd < 0):
        raise ValueError("a reminder's figures are non-negative")
    who = facts.shop_name.strip() if facts.shop_name and facts.shop_name.strip() else "Tiệm giặt"
    reference = _reference(facts)
    ready = _day(facts.ready_on)
    if step is ReminderStep.READY:
        lines = [
            f"{who} xin báo: đồ giặt {reference} đã xong ngày {ready}.",
            "Mời anh/chị qua tiệm lấy đồ.",
        ]
    elif step is ReminderStep.BEFORE_FEE:
        if facts.fee is None:
            raise ValueError("BEFORE_FEE quotes the published storage fee")
        fee = facts.fee
        lines = [
            f"{who} xin nhắc: đồ giặt {reference} đã xong từ ngày {ready} và đang chờ anh/chị "
            "qua lấy.",
            f"Từ ngày {_day(fee.starts_on)} tiệm tính phí lưu kho "
            f"{_vnd(fee.fee_per_started_day_vnd)}/ngày (tối đa {fee.fee_cap_percent}% tiền giặt).",
            "Mời anh/chị qua lấy trước ngày đó.",
        ]
    else:
        lines = [
            f"{who} xin nhắc: đồ giặt {reference} đã xong từ ngày {ready}, đến nay đã "
            f"{facts.days_waiting} ngày.",
            "Mời anh/chị sắp xếp qua tiệm lấy đồ.",
        ]
    if facts.remaining_vnd is None:
        lines.append("Tiệm sẽ báo số tiền khi anh/chị qua lấy.")
    elif facts.remaining_vnd == 0:
        lines.append("Đơn đã thanh toán đủ.")
    else:
        lines.append(f"Số tiền còn lại: {_vnd(facts.remaining_vnd)}.")
    if facts.opening_hours is not None:
        opens, closes = facts.opening_hours
        lines.append(f"Giờ mở cửa: từ {_clock(opens)} đến {_clock(closes)}.")
    lines.append("Cảm ơn anh/chị!")
    return "\n".join(lines)


__all__ = [
    "FIXED_STEP_DAYS",
    "PICKUP_REMINDER_DECISION",
    "PICKUP_REMINDER_TEMPLATE",
    "EgressVerdict",
    "Reachability",
    "ReminderFacts",
    "ReminderFee",
    "ReminderRefusal",
    "ReminderStep",
    "combine_egress",
    "current_reminder",
    "fee_starts_on",
    "reachability",
    "reminder_steps",
    "reminder_text",
    "schedule",
    "step_day",
    "zalo_url",
]
