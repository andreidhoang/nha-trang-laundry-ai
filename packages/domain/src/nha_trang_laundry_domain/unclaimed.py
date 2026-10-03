"""Laundry waiting for pickup: contact attempts, the storage fee, disposal. `UNCLAIMED-001`.

`DEC-036` (2026-09-25), grounded in the shop's own customer terms as the owner supplied them:
*"Lấy đồ trong 20 ngày; sau đó tính phí lưu kho; sau 60 ngày hiện có điều khoản thanh lý."*

* **Waiting for pickup** is one fact about an order, stated once here (`awaiting_pickup`): it is
  running, its laundry is finished and on the shelf, nobody is recorded as having taken it, and the
  customer -- not the shop's courier -- is the one who comes for it.
* **The storage fee.** Free through day `free_days` after the laundry was ready; from the next day,
  `fee_per_started_day_vnd` per order for every day that has started, capped at
  `fee_cap_percent` of the order's quoted total. Days are **shop-local calendar days**
  (Asia/Ho_Chi_Minh): laundry ready at 19:55 on the 1st has waited one day on the 2nd. Day *n* is
  the *n*-th calendar day after the ready day, so the ready day itself is day 0.
* **A hold pauses the fee; it never erases it** (`DEC-047`, 2026-09-30). Laundry put on hold
  (`HOLD`, production `ON_HOLD` resuming to `READY_AT_STORE`) keeps the fee it had accrued up to
  the hold; the days on hold do not count; `RESUME` continues the count where it stopped. Each hold
  of finished laundry is an interval (`StorageHold`, migration `0066`'s `order_storage_holds`); the
  days that count are the shop-local days from the ready day, less the days of each hold lifted
  since, frozen at the start of the current one (`counted_days`). A rewash (the shop's fault) still
  restarts the free days -- holds before the new ready time do not count -- and a withdrawn policy
  stops accrual; in every case the part of the fee already paid stays owed-for (`fee_already_paid`).
* **Disposal (thanh lý).** From day `disposal_from_day`, and only when at least
  `disposal_min_attempts` contact attempts are recorded on at least `disposal_min_attempt_days`
  different shop days since the laundry was ready.
* **Nothing is charged and nothing is disposed of until the owner publishes the figures**
  (`scripts/publish_storage_policy.py`). The waiting list and the contact attempts work without it.

The fee is money, so the rounding is stated: the cap is `quoted_total * percent // 100`, rounded
down -- in the customer's favour -- and nothing else here divides.

Pure: no clock, no database, no environment. The instant a figure is computed for is a parameter.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from nha_trang_laundry_domain.catalog import (
    MODES_EXPECTING_RETURN,
    CommercialOrderStatus,
    FulfillmentMode,
    ProductionStatus,
)
from nha_trang_laundry_domain.promise import SHOP_TIMEZONE, SHOP_TIMEZONE_NAME
from nha_trang_laundry_domain.settlement import MAX_SETTLEMENT_VND

#: The configuration type the owner's storage policy is published under.
STORAGE_POLICY_CONFIG_TYPE: Final = "STORAGE_POLICY"
#: The document version this module parses. A different one is not a policy this code understands.
STORAGE_POLICY_VERSION: Final = "storage-policy-v1"
STORAGE_DECISION_REF: Final = "DEC-036"

#: The longest note a contact attempt or a waiver reason keeps (the columns' CHECKs say the same).
NOTE_MAX_LENGTH: Final = 120

#: Seven or more digits in a row, allowing one space, dot or dash between them, with an optional
#: leading `+`: what a phone number looks like however it is typed. A note is for what happened on
#: the call ("hẹn chiều mai qua"), never for a number -- the number lives, sealed, on the customer.
PHONE_LIKE: Final = re.compile(r"\+?\d(?:[\s.\-]?\d){6,}")


class StoragePolicyError(ValueError):
    """The document is not a storage policy this code will charge money under."""


class ContactChannel(StrEnum):
    """How the shop tried to reach the customer (`DEC-036`)."""

    CALL = "CALL"
    ZALO = "ZALO"
    SMS = "SMS"
    #: Somebody went to the customer's address.
    VISIT = "VISIT"


class ContactOutcome(StrEnum):
    """What came of one attempt. Every outcome is an attempt; the rule counts attempts."""

    REACHED = "REACHED"
    NO_ANSWER = "NO_ANSWER"
    WRONG_NUMBER = "WRONG_NUMBER"
    PROMISED_TO_COME = "PROMISED_TO_COME"
    #: `PICKUP-REMIND-001` (`DEC-043`, migration `0065`): a reminder's message went out on Zalo or
    #: SMS. Legal only with the reminder step it sent, and only when the egress guard allows it.
    MESSAGE_SENT = "MESSAGE_SENT"


class NoteRefusal(StrEnum):
    NOTE_TOO_LONG = "NOTE_TOO_LONG"
    #: The note holds something that looks like a phone number (`PHONE_LIKE`).
    NOTE_LOOKS_LIKE_PHONE = "NOTE_LOOKS_LIKE_PHONE"
    #: A waiver needs a reason; an attempt's note is optional.
    NOTE_REQUIRED = "NOTE_REQUIRED"


def clean_note(text: str | None, *, required: bool = False) -> str | None:
    """The note as stored -- trimmed, inner whitespace collapsed, `None` when blank -- or a refusal.

    Raises `ValueError` whose first argument is the `NoteRefusal` code.
    """

    cleaned = None if text is None else " ".join(text.split()) or None
    if cleaned is None:
        if required:
            raise ValueError(NoteRefusal.NOTE_REQUIRED.value)
        return None
    if len(cleaned) > NOTE_MAX_LENGTH:
        raise ValueError(NoteRefusal.NOTE_TOO_LONG.value)
    if PHONE_LIKE.search(cleaned):
        raise ValueError(NoteRefusal.NOTE_LOOKS_LIKE_PHONE.value)
    return cleaned


# --- the published policy -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoragePolicy:
    """The owner's figures (`DEC-036`). Every field is an `int`; no figure has a default."""

    free_days: int
    fee_per_started_day_vnd: int
    fee_cap_percent: int
    disposal_from_day: int
    disposal_min_attempts: int
    disposal_min_attempt_days: int


_POLICY_KEYS: Final = frozenset(
    {
        "policy_version",
        "decision_ref",
        "timezone",
        "free_days_after_ready",
        "fee_per_started_day_vnd",
        "fee_cap_percent_of_quoted_total",
        "disposal_from_day",
        "disposal_min_contact_attempts",
        "disposal_min_contact_days",
    }
)
_WITHDRAWAL_KEYS: Final = frozenset({"policy_version", "decision_ref", "withdrawn"})


def _figure(payload: Mapping[str, Any], key: str, low: int, high: int) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise StoragePolicyError(f"{key} must be a whole number from {low} to {high}")
    return value


def parse_storage_policy(payload: Mapping[str, Any]) -> StoragePolicy:
    """The published document as a policy, or `StoragePolicyError`. Exact keys, no defaults."""

    if set(payload) != _POLICY_KEYS:
        raise StoragePolicyError("a storage policy names exactly its nine fields")
    if payload.get("policy_version") != STORAGE_POLICY_VERSION:
        raise StoragePolicyError(f"policy_version must be {STORAGE_POLICY_VERSION}")
    if payload.get("decision_ref") != STORAGE_DECISION_REF:
        raise StoragePolicyError(f"decision_ref must be {STORAGE_DECISION_REF}")
    if payload.get("timezone") != SHOP_TIMEZONE_NAME:
        raise StoragePolicyError(f"the shop's days are counted in {SHOP_TIMEZONE_NAME}")
    policy = StoragePolicy(
        free_days=_figure(payload, "free_days_after_ready", 1, 365),
        fee_per_started_day_vnd=_figure(payload, "fee_per_started_day_vnd", 1, 1_000_000),
        fee_cap_percent=_figure(payload, "fee_cap_percent_of_quoted_total", 1, 100),
        disposal_from_day=_figure(payload, "disposal_from_day", 1, 3650),
        disposal_min_attempts=_figure(payload, "disposal_min_contact_attempts", 1, 20),
        disposal_min_attempt_days=_figure(payload, "disposal_min_contact_days", 1, 20),
    )
    if policy.disposal_from_day <= policy.free_days:
        raise StoragePolicyError("disposal starts after the free days end")
    if policy.disposal_min_attempt_days > policy.disposal_min_attempts:
        raise StoragePolicyError("attempts on N different days need at least N attempts")
    return policy


def validate_storage_document(payload: Mapping[str, Any]) -> None:
    """The typed validator: a full policy, or the owner's withdrawal of it (`DEC-036` reversal)."""

    if payload.get("withdrawn") is True:
        if (
            set(payload) != _WITHDRAWAL_KEYS
            or payload.get("policy_version") != STORAGE_POLICY_VERSION
            or payload.get("decision_ref") != STORAGE_DECISION_REF
        ):
            raise StoragePolicyError("a withdrawal names only the policy version and DEC-036")
        return
    parse_storage_policy(payload)


def withdrawal_document() -> dict[str, Any]:
    """Once in force, no fee is charged and no disposal is offered, as before publication."""

    return {
        "policy_version": STORAGE_POLICY_VERSION,
        "decision_ref": STORAGE_DECISION_REF,
        "withdrawn": True,
    }


def _vnd_text(amount: int) -> str:
    """`5000` -> `5.000 ₫`: the Vietnamese thousands separator, for the two sentences below."""

    return f"{amount:,}".replace(",", ".") + " ₫"


def receipt_line_vi(policy: StoragePolicy) -> str:
    """The one line the receipt prints once the policy is published (`DEC-036`: "a fee the customer
    was never told about is a fee the shop should not charge"). Built from the figures, so the
    receipt can never state a rule the server does not apply."""

    return (
        f"Lấy đồ trong {policy.free_days} ngày kể từ khi đồ xong; "
        f"từ ngày {policy.free_days + 1} phí lưu kho "
        f"{_vnd_text(policy.fee_per_started_day_vnd)}/ngày "
        f"(tối đa {policy.fee_cap_percent}% tiền giặt); "
        f"sau {policy.disposal_from_day} ngày tiệm có thể thanh lý."
    )


def disposal_rule_vi(policy: StoragePolicy) -> str:
    """The rule the owner's confirm sheet states verbatim before *Thanh lý*."""

    return (
        f"Đồ chờ lấy từ ngày thứ {policy.disposal_from_day} kể từ khi đồ xong, và tiệm đã liên hệ "
        f"khách ít nhất {policy.disposal_min_attempts} lần trong ít nhất "
        f"{policy.disposal_min_attempt_days} ngày khác nhau, thì chủ tiệm được thanh lý "
        "(thường là tặng người cần). Tiền khách đã trả giữ nguyên; tiền còn nợ được xoá. "
        "Đơn đóng lại và không mở lại được."
    )


# --- days -----------------------------------------------------------------------------------------


def shop_date(moment: datetime) -> date:
    """The shop-local calendar day of an instant. A naive time is refused, never assumed."""

    if moment.tzinfo is None:
        raise ValueError("a moment must carry its timezone")
    return moment.astimezone(SHOP_TIMEZONE).date()


def days_waiting(ready_at: datetime, as_of: datetime) -> int:
    """Shop-local calendar days from the ready day to `as_of`'s day; 0 on the ready day itself and
    for a clock that reads earlier than the ready time (never negative)."""

    return max(0, (shop_date(as_of) - shop_date(ready_at)).days)


def awaiting_pickup(
    *,
    commercial: CommercialOrderStatus,
    production: ProductionStatus,
    fulfillment_mode: FulfillmentMode,
    self_collection_recorded: bool,
) -> bool:
    """Is this order laundry the customer has to come back for, finished and on the shelf?

    Delivery orders are the shop's courier's to hand over (`MODES_EXPECTING_RETURN`), so they are
    not on the list and never accrue a storage fee. The list's SQL states the same four conditions
    (`AWAITING_PICKUP_SQL` in the db package); a test pins the two together.
    """

    return (
        commercial is CommercialOrderStatus.ACTIVE
        and production is ProductionStatus.READY_AT_STORE
        and not self_collection_recorded
        and fulfillment_mode not in MODES_EXPECTING_RETURN
    )


class StorageClock(StrEnum):
    """Whether the storage fee's day count runs for an order now (`DEC-036`, `DEC-047`)."""

    #: Waiting for pickup (`awaiting_pickup`): the days count.
    RUNNING = "RUNNING"
    #: Finished laundry put on hold (`DEC-047`): the count is frozen where the hold found it, and
    #: the fee accrued up to then is owed.
    PAUSED = "PAUSED"
    #: Anything else: nothing accrues (collected, delivery, rework, cancelled, not finished).
    STOPPED = "STOPPED"


def storage_clock(
    *,
    commercial: CommercialOrderStatus,
    production: ProductionStatus,
    resume_to: ProductionStatus | None,
    fulfillment_mode: FulfillmentMode,
    self_collection_recorded: bool,
) -> StorageClock:
    """`RUNNING` while waiting for pickup; `PAUSED` while that same laundry is on hold (`DEC-047`).

    A hold of finished laundry is production `ON_HOLD` resuming to `READY_AT_STORE`: the domain
    permits exactly one exit, back to the shelf, so nothing happens to the laundry while it is held
    and the order is still the customer's to collect once it is lifted.

    An `EXCEPTION` that interrupted `READY_AT_STORE` is the same pause until it is resolved (round 9
    review, P1). It may go straight back to the shelf with nothing washed, so it cannot be the
    `STOPPED` that let a payment taken meanwhile settle without the fee and a return to the shelf
    restart the free days -- the approver-only waiver, bypassed by any operator in two calls. Only a
    rewash (back into the wash) restarts the count, and that is the order's ready time clearing, not
    this clock.
    """

    if awaiting_pickup(
        commercial=commercial,
        production=production,
        fulfillment_mode=fulfillment_mode,
        self_collection_recorded=self_collection_recorded,
    ):
        return StorageClock.RUNNING
    if (
        commercial is CommercialOrderStatus.ACTIVE
        and production in SHELF_INTERRUPTIONS
        and resume_to is ProductionStatus.READY_AT_STORE
        and not self_collection_recorded
        and fulfillment_mode not in MODES_EXPECTING_RETURN
    ):
        return StorageClock.PAUSED
    return StorageClock.STOPPED


@dataclass(frozen=True, slots=True)
class StorageHold:
    """One hold of finished laundry (`DEC-047`): when it began, and when it was lifted (`None`
    while it lasts). Recorded by `order_storage_holds` (`0066`)."""

    held_at: datetime
    resumed_at: datetime | None


#: The production states that interrupt finished laundry without taking it off the shelf for
#: good: a hold, and an exception not yet resolved. Each pauses the storage fee while it lasts
#: (`DEC-047`).
SHELF_INTERRUPTIONS: Final = frozenset({ProductionStatus.ON_HOLD, ProductionStatus.EXCEPTION})


class HoldMove(StrEnum):
    """What one production move does to the storage fee's hold record (`DEC-047`)."""

    #: Finished laundry put on hold: a hold begins.
    START = "START"
    #: The hold of finished laundry lifted, back to the shelf: the hold ends.
    END = "END"


def hold_move(
    *, before: ProductionStatus, after: ProductionStatus, resume_to: ProductionStatus | None
) -> HoldMove | None:
    """`START` for a hold of finished laundry, `END` for its lifting, else `None`. Pure.

    A hold of laundry still being washed is not a pause of a fee -- nothing accrues then. An
    exception that interrupts finished laundry is a pause too (`SHELF_INTERRUPTIONS`), and any way
    out of an exception ends it: back to the shelf continues the count, and a rewash clears the
    ready time, so the ended record began before the next one and no longer counts.
    """

    if (
        after in SHELF_INTERRUPTIONS
        and before is ProductionStatus.READY_AT_STORE
        and resume_to is ProductionStatus.READY_AT_STORE
    ):
        return HoldMove.START
    if before is ProductionStatus.ON_HOLD and after is ProductionStatus.READY_AT_STORE:
        return HoldMove.END
    if before is ProductionStatus.EXCEPTION:
        return HoldMove.END
    return None


@dataclass(frozen=True, slots=True)
class WaitingClock:
    """How long one order's finished laundry has waited for its customer (`DEC-050`).

    The one clock every measure of the customer's lateness reads -- the storage fee's days
    (`DEC-047`), *days waiting* on the list and the order, disposal eligibility and the pickup
    reminders' day steps. A hold stops the clock and lifting it continues the count from where it
    stopped (`DEC-047`): the count is the shop-local days from the ready day to the instant the
    clock reads -- `as_of` less the time every hold lifted since lasted -- and it is frozen at the
    start of a hold still open (`paused`). It is the time held that is taken out, never a day per
    shop midnight a hold crossed (pre-production review 9: holding the bag from closing to opening
    every night took a whole day out per night, so the count, the fee, disposal and the reminders
    never moved; a hold within one shop date took nothing out, so held hours were charged).
    """

    #: The shop day the laundry was (last) ready: day 0.
    ready_on: date
    #: The days that count.
    days: int
    #: The shop days the lifted holds took out of the count: the calendar days to the clock's end
    #: less `days`, so `days + held_days` is always the calendar days since the ready day.
    held_days: int
    #: A hold since the laundry was ready is still open: `days` is frozen where it began.
    paused: bool
    #: Each lifted hold since the ready time as `(held_at, resumed_at)` instants, oldest first.
    lifted: tuple[tuple[datetime, datetime], ...] = ()

    def reaches(self, day: int) -> datetime | None:
        """The instant the count reaches `day`, the holds already lifted skipped over.

        `None` while paused: when the open hold will be lifted is not known, so no later instant
        is either. A hold not yet begun is not foreseen -- the instant moves if one comes.
        """

        if self.paused:
            return None
        if day < 0:
            raise ValueError("a day count is never negative")
        on = date.fromordinal(self.ready_on.toordinal() + day)
        candidate = datetime(on.year, on.month, on.day, tzinfo=SHOP_TIMEZONE)
        for held_at, resumed_at in self.lifted:
            if held_at < candidate:
                candidate += resumed_at - held_at
        return candidate

    def falls_on(self, day: int) -> date | None:
        """The shop day on which the count first reads `day` (from `reaches(day)` on that day).

        A hold that did not last whole days moves that instant off the shop's midnight, so the
        count can turn during a day; this is the day it turns. `None` while paused.
        """

        reached = self.reaches(day)
        return None if reached is None else shop_date(reached)


def waiting_clock(
    ready_at: datetime,
    as_of: datetime,
    *,
    holds: Sequence[StorageHold],
    paused: bool = False,
) -> WaitingClock:
    """`DEC-050`'s one clock: the days the laundry has waited at `as_of`, time on hold not counted.

    Shop-local days from the ready day to the instant the clock reads: `as_of` less the time each
    hold since the laundry was last ready lasted (up to `as_of`). While `paused` (a hold is open),
    the count is frozen at the start of the latest open hold. Holds before `ready_at` (a rewash
    since) do not count. Never negative. Pure.
    """

    since = sorted(
        (hold for hold in holds if hold.held_at >= ready_at), key=lambda hold: hold.held_at
    )
    end = as_of
    open_holds = [hold.held_at for hold in since if hold.resumed_at is None]
    frozen = paused and bool(open_holds)
    if frozen:
        end = min(as_of, open_holds[-1])
    counted = [
        (hold.held_at, max(hold.held_at, min(hold.resumed_at, end)))
        for hold in since
        if hold.resumed_at is not None and hold.held_at < end
    ]
    held_time = sum((resumed_at - held_at for held_at, resumed_at in counted), timedelta())
    calendar = days_waiting(ready_at, end)
    days = days_waiting(ready_at, end - held_time)
    return WaitingClock(
        ready_on=shop_date(ready_at),
        days=days,
        held_days=calendar - days,
        paused=frozen,
        lifted=tuple(counted),
    )


def counted_days(
    ready_at: datetime,
    as_of: datetime,
    *,
    holds: Sequence[StorageHold] = (),
    paused: bool = False,
) -> int:
    """The days the storage fee counts (`DEC-047`): `waiting_clock`'s days. Never negative."""

    return waiting_clock(ready_at, as_of, holds=holds, paused=paused).days


# --- the fee --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StorageFee:
    """The fee for one order at one instant, with every figure it came from (its trace)."""

    days_waiting: int
    chargeable_days: int
    fee_per_started_day_vnd: int
    uncapped_vnd: int
    cap_vnd: int
    amount_vnd: int

    @property
    def capped(self) -> bool:
        return self.uncapped_vnd > self.cap_vnd


def storage_fee(
    policy: StoragePolicy,
    *,
    ready_at: datetime,
    as_of: datetime,
    quoted_total_vnd: int,
    holds: Sequence[StorageHold] = (),
    paused: bool = False,
) -> StorageFee:
    """`DEC-036`: free through day `free_days`; then a fee per started day, capped.

    Day 20 -> 0; day 21 -> one day's fee; the cap is `quoted_total * percent // 100` (down).
    `DEC-047`: the days are `counted_days` -- frozen at a hold, less the holds already lifted.
    """

    if (
        not isinstance(quoted_total_vnd, int)
        or isinstance(quoted_total_vnd, bool)
        or not 0 <= quoted_total_vnd <= MAX_SETTLEMENT_VND
    ):
        raise ValueError("the quoted total must be a non-negative whole number of đồng")
    waited = counted_days(ready_at, as_of, holds=holds, paused=paused)
    chargeable = max(0, waited - policy.free_days)
    uncapped = chargeable * policy.fee_per_started_day_vnd
    cap = quoted_total_vnd * policy.fee_cap_percent // 100
    return StorageFee(
        days_waiting=waited,
        chargeable_days=chargeable,
        fee_per_started_day_vnd=policy.fee_per_started_day_vnd,
        uncapped_vnd=uncapped,
        cap_vnd=cap,
        amount_vnd=min(uncapped, cap),
    )


class StorageFeeStatus(StrEnum):
    """Where one order stands on the storage fee. Exactly one applies, checked in this order."""

    #: Paid in full: the fee is what was fixed when the settling payment was taken -- the amount
    #: the customer paid (`amount_vnd`, 0 when the order was paid before any fee accrued). A fee
    #: once paid stays on the order whatever the policy later says (`DEC-036` "Reversal").
    FIXED = "FIXED"
    #: `MONEY-LIFECYCLE-009` (M1): the order's payments already cover more of the fee than the fee
    #: computed now -- it was waived, the order was held, the laundry was rewashed and its free days
    #: restarted, the owner withdrew the policy, the order is no longer waiting. Money that moved is
    #: never un-owed by a later event (`fee_already_paid`): `amount_vnd` is the part already paid,
    #: and returning it is a refund, a separate and explicit act. Nothing is left to pay on the fee.
    ALREADY_PAID = "ALREADY_PAID"
    #: An `OPS_APPROVER` or the owner waived it; nothing accrues on this order any more.
    WAIVED = "WAIVED"
    #: The owner has not published the storage policy (or withdrew it): no fee.
    POLICY_UNPUBLISHED = "POLICY_UNPUBLISHED"
    #: Not laundry waiting for the customer (`awaiting_pickup`), or no ready time is recorded.
    NOT_WAITING = "NOT_WAITING"
    #: The quote presents no single total, so there is no cap to measure against: no fee.
    NO_SINGLE_TOTAL = "NO_SINGLE_TOTAL"
    #: Waiting, within the free days.
    FREE_PERIOD = "FREE_PERIOD"
    #: Waiting past the free days: `amount_vnd` is owed on top of the quoted total.
    ACCRUING = "ACCRUING"
    #: `DEC-047`: finished laundry on hold after its free days: `amount_vnd`, the fee accrued up to
    #: the hold, is owed; the days on hold do not add to it. It may be waived like `ACCRUING`.
    PAUSED = "PAUSED"


@dataclass(frozen=True, slots=True)
class OrderStorageFee:
    status: StorageFeeStatus
    #: What the order owes for storage now: the charge added to its list of charges when > 0.
    amount_vnd: int
    #: The computation, when one was made (`FREE_PERIOD`, `ACCRUING`, and `ALREADY_PAID` over
    #: either of those).
    fee: StorageFee | None = None
    #: `MONEY-LIFECYCLE-009`: the part of the storage fee the order's payments already cover
    #: (`fee_already_paid`) -- 0 when they do not reach past the quoted total, and 0 once the fee is
    #: `FIXED` (a settled order's fee is what its settlement says, all of it paid or charged).
    already_paid_vnd: int = 0


def fee_already_paid(*, paid_vnd: int, quoted_total_vnd: int | None) -> int:
    """How much of the storage fee the order's payments already cover (`MONEY-LIFECYCLE-009`).

    A payment is measured against every charge on the order, so money beyond the quoted total paid
    toward the storage fee: 103.000 d paid on a 100.000 d total is 3.000 d of the fee. That part
    stays owed-for whatever happens to the fee afterwards -- a waiver, a hold, a rewash, a withdrawn
    policy, a cancellation. The alternative is an order whose ledger holds more than it "owes":
    reads that cannot state it, a waiver that strands the order, and money a later event quietly
    turned into a debt of the shop's. Returning it is a refund, which is its own act.

    0 when the quote presents no single total (no payment can have been taken against one).
    """

    for value in (paid_vnd, quoted_total_vnd):
        if value is not None and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= MAX_SETTLEMENT_VND
        ):
            raise ValueError("amounts are non-negative whole numbers of đồng")
    if quoted_total_vnd is None:
        return 0
    return max(0, paid_vnd - quoted_total_vnd)


def order_storage_fee(
    policy: StoragePolicy | None,
    *,
    clock: StorageClock,
    ready_at: datetime | None,
    as_of: datetime,
    quoted_total_vnd: int | None,
    waived: bool,
    settled: bool,
    fixed_vnd: int | None,
    paid_vnd: int,
    holds: Sequence[StorageHold],
) -> OrderStorageFee:
    """The storage fee one order owes at `as_of`, from its stored facts.

    `settled` is whether a settlement row exists (the order was paid in full); `fixed_vnd` is the
    fee recorded beside it, or `None` when none was (the order was paid before any fee accrued, or
    the fee was waived, or no policy was in force). A settled order's fee never moves again.

    `paid_vnd` is the sum of the order's payment ledger. Before the fee is fixed it is never less
    than the part of it the ledger already covers (`fee_already_paid`, status `ALREADY_PAID`), so
    what the order owes is never below what it has been paid (`MONEY-LIFECYCLE-009`, M1).

    `clock` is `storage_clock`'s answer for the order; `holds` its holds of finished laundry
    (`DEC-047`): while `PAUSED`, the fee is what had accrued when the hold began (status `PAUSED`).
    """

    if fixed_vnd is not None:
        return OrderStorageFee(StorageFeeStatus.FIXED, fixed_vnd)
    if settled:
        return OrderStorageFee(StorageFeeStatus.FIXED, 0)
    owed_now = _unfixed_storage_fee(
        policy,
        clock=clock,
        ready_at=ready_at,
        as_of=as_of,
        quoted_total_vnd=quoted_total_vnd,
        waived=waived,
        holds=holds,
    )
    already = fee_already_paid(paid_vnd=paid_vnd, quoted_total_vnd=quoted_total_vnd)
    if already > owed_now.amount_vnd:
        return OrderStorageFee(
            StorageFeeStatus.ALREADY_PAID, already, owed_now.fee, already_paid_vnd=already
        )
    return OrderStorageFee(owed_now.status, owed_now.amount_vnd, owed_now.fee, already)


def _unfixed_storage_fee(
    policy: StoragePolicy | None,
    *,
    clock: StorageClock,
    ready_at: datetime | None,
    as_of: datetime,
    quoted_total_vnd: int | None,
    waived: bool,
    holds: Sequence[StorageHold],
) -> OrderStorageFee:
    """What the published policy charges an order whose fee is not fixed, before what was paid."""

    if waived:
        return OrderStorageFee(StorageFeeStatus.WAIVED, 0)
    if policy is None:
        return OrderStorageFee(StorageFeeStatus.POLICY_UNPUBLISHED, 0)
    paused = clock is StorageClock.PAUSED and any(
        hold.resumed_at is None and ready_at is not None and hold.held_at >= ready_at
        for hold in holds
    )
    if (clock is not StorageClock.RUNNING and not paused) or ready_at is None:
        return OrderStorageFee(StorageFeeStatus.NOT_WAITING, 0)
    if quoted_total_vnd is None:
        return OrderStorageFee(StorageFeeStatus.NO_SINGLE_TOTAL, 0)
    fee = storage_fee(
        policy,
        ready_at=ready_at,
        as_of=as_of,
        quoted_total_vnd=quoted_total_vnd,
        holds=holds,
        paused=paused,
    )
    if fee.amount_vnd == 0:
        return OrderStorageFee(StorageFeeStatus.FREE_PERIOD, 0, fee)
    status = StorageFeeStatus.PAUSED if paused else StorageFeeStatus.ACCRUING
    return OrderStorageFee(status, fee.amount_vnd, fee)


@dataclass(frozen=True, slots=True)
class WaiverEffect:
    """What *Miễn phí lưu kho* does to an order now (`MONEY-LIFECYCLE-009`, M1/A3).

    A waiver takes off the part of the fee that is not yet paid, and never lowers what is owed
    below what is paid: the part already paid is kept (`fee_already_paid`). When that leaves nothing
    owed, the waiver settles the order in the same transaction -- otherwise an order whose ledger
    covers everything it owes would read "partly paid" with nothing left to take, and could never
    be paid in full or handed over.
    """

    #: The order's quoted total, which a settlement the waiver writes is measured against.
    quoted_total_vnd: int
    #: What the waiver takes off: the part of the fee the payments do not cover.
    waived_vnd: int
    #: The part of the fee already paid, which stays owed-for.
    kept_vnd: int
    #: What the order owes once waived: its quoted total plus `kept_vnd`.
    owed_after_vnd: int
    #: What is still owed once waived.
    remaining_after_vnd: int

    @property
    def settles(self) -> bool:
        """Whether the waiver leaves nothing owed, and so settles the order with it."""

        return self.remaining_after_vnd == 0


def waiver_effect(
    fee: OrderStorageFee, *, quoted_total_vnd: int | None, paid_vnd: int
) -> WaiverEffect | None:
    """What waiving the fee would do now, or `None` when nothing unpaid is left to waive.

    Only an `ACCRUING` or `PAUSED` (`DEC-047`) fee is waived; `already_paid_vnd` is what
    `order_storage_fee` found the ledger already covers. Pure arithmetic on the three figures, each
    validated; no rounding.
    """

    if (
        fee.status not in {StorageFeeStatus.ACCRUING, StorageFeeStatus.PAUSED}
        or quoted_total_vnd is None
    ):
        return None
    kept = fee.already_paid_vnd
    if kept != fee_already_paid(paid_vnd=paid_vnd, quoted_total_vnd=quoted_total_vnd):
        raise ValueError("the fee was computed against a different ledger")
    waived = fee.amount_vnd - kept
    if waived <= 0:
        return None
    owed_after = quoted_total_vnd + kept
    return WaiverEffect(
        quoted_total_vnd=quoted_total_vnd,
        waived_vnd=waived,
        kept_vnd=kept,
        owed_after_vnd=owed_after,
        remaining_after_vnd=owed_after - paid_vnd,
    )


# --- disposal -------------------------------------------------------------------------------------


class DisposalRefusal(StrEnum):
    """Why *Thanh lý* is not legal for an order now. Every one that applies is reported."""

    STORAGE_POLICY_UNPUBLISHED = "STORAGE_POLICY_UNPUBLISHED"
    NOT_AWAITING_PICKUP = "NOT_AWAITING_PICKUP"
    #: Fewer than `disposal_from_day` days since the laundry was ready.
    DISPOSAL_TOO_EARLY = "DISPOSAL_TOO_EARLY"
    #: Fewer than `disposal_min_attempts` contact attempts since the laundry was ready.
    CONTACT_ATTEMPTS_TOO_FEW = "CONTACT_ATTEMPTS_TOO_FEW"
    #: The attempts fall on fewer than `disposal_min_attempt_days` different shop days.
    CONTACT_DAYS_TOO_FEW = "CONTACT_DAYS_TOO_FEW"


@dataclass(frozen=True, slots=True)
class DisposalVerdict:
    refusals: tuple[DisposalRefusal, ...]
    days_waiting: int | None
    #: The attempts that count: recorded at or after the laundry was (last) ready.
    attempts_counted: int
    attempt_days: int
    #: The first shop day disposal is allowed on by the day rule, when a policy and a ready time
    #: exist; the attempts rule still applies on that day.
    eligible_on: date | None

    @property
    def allowed(self) -> bool:
        return not self.refusals


def disposal_verdict(
    policy: StoragePolicy | None,
    *,
    awaiting: bool,
    ready_at: datetime | None,
    as_of: datetime,
    attempt_times: Iterable[datetime],
    holds: Sequence[StorageHold],
    paused: bool = False,
) -> DisposalVerdict:
    """`DEC-036`: may the owner dispose of this order's laundry now?

    Who may press it (the owner, with MFA) is the repository's check; this is the rule about the
    order. An attempt recorded before the laundry was last ready (before a rewash) does not count.
    `DEC-050`: the days are `waiting_clock`'s -- the days the shop held the laundry do not bring
    disposal closer, and `eligible_on` moves past every hold already lifted (`None` while one is
    open, `paused`).
    """

    counted = [moment for moment in attempt_times if ready_at is not None and moment >= ready_at]
    distinct_days = len({shop_date(moment) for moment in counted})
    refusals: list[DisposalRefusal] = []
    clock = None if ready_at is None else waiting_clock(ready_at, as_of, holds=holds, paused=paused)
    waited = None if clock is None else clock.days
    eligible_on = None
    if policy is None:
        refusals.append(DisposalRefusal.STORAGE_POLICY_UNPUBLISHED)
    if not awaiting or ready_at is None:
        refusals.append(DisposalRefusal.NOT_AWAITING_PICKUP)
    if policy is not None:
        if clock is not None:
            # The day the count turns to the disposal day. Disposal itself is decided by the count
            # at `as_of` above, so on that day it is allowed from the instant the count turns.
            eligible_on = clock.falls_on(policy.disposal_from_day)
        if waited is None or waited < policy.disposal_from_day:
            refusals.append(DisposalRefusal.DISPOSAL_TOO_EARLY)
        if len(counted) < policy.disposal_min_attempts:
            refusals.append(DisposalRefusal.CONTACT_ATTEMPTS_TOO_FEW)
        if distinct_days < policy.disposal_min_attempt_days:
            refusals.append(DisposalRefusal.CONTACT_DAYS_TOO_FEW)
    return DisposalVerdict(
        refusals=tuple(refusals),
        days_waiting=waited,
        attempts_counted=len(counted),
        attempt_days=distinct_days,
        eligible_on=eligible_on,
    )


@dataclass(frozen=True, slots=True)
class DisposalMoney:
    """What happens to the money when laundry is disposed of (`DEC-036`): money already paid is
    kept, money still owed is written off. `kept + written_off == owed`, always."""

    owed_vnd: int
    kept_vnd: int
    written_off_vnd: int


def disposal_money(*, owed_vnd: int, paid_vnd: int) -> DisposalMoney:
    for value in (owed_vnd, paid_vnd):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("amounts are non-negative whole numbers of đồng")
    if paid_vnd > owed_vnd:
        raise ValueError("the order's payments exceed what it owes")
    return DisposalMoney(owed_vnd=owed_vnd, kept_vnd=paid_vnd, written_off_vnd=owed_vnd - paid_vnd)


__all__ = [
    "NOTE_MAX_LENGTH",
    "PHONE_LIKE",
    "SHELF_INTERRUPTIONS",
    "STORAGE_DECISION_REF",
    "STORAGE_POLICY_CONFIG_TYPE",
    "STORAGE_POLICY_VERSION",
    "ContactChannel",
    "ContactOutcome",
    "DisposalMoney",
    "DisposalRefusal",
    "DisposalVerdict",
    "HoldMove",
    "NoteRefusal",
    "OrderStorageFee",
    "StorageClock",
    "StorageFee",
    "StorageFeeStatus",
    "StorageHold",
    "StoragePolicy",
    "StoragePolicyError",
    "WaitingClock",
    "WaiverEffect",
    "awaiting_pickup",
    "clean_note",
    "counted_days",
    "days_waiting",
    "disposal_money",
    "disposal_rule_vi",
    "disposal_verdict",
    "fee_already_paid",
    "hold_move",
    "order_storage_fee",
    "parse_storage_policy",
    "receipt_line_vi",
    "shop_date",
    "storage_clock",
    "storage_fee",
    "validate_storage_document",
    "waiting_clock",
    "waiver_effect",
    "withdrawal_document",
]
