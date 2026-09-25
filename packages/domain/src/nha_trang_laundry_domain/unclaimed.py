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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
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
    policy: StoragePolicy, *, ready_at: datetime, as_of: datetime, quoted_total_vnd: int
) -> StorageFee:
    """`DEC-036`: free through day `free_days`; then a fee per started day, capped.

    Day 20 -> 0; day 21 -> one day's fee; the cap is `quoted_total * percent // 100` (down).
    """

    if (
        not isinstance(quoted_total_vnd, int)
        or isinstance(quoted_total_vnd, bool)
        or not 0 <= quoted_total_vnd <= MAX_SETTLEMENT_VND
    ):
        raise ValueError("the quoted total must be a non-negative whole number of đồng")
    waited = days_waiting(ready_at, as_of)
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


@dataclass(frozen=True, slots=True)
class OrderStorageFee:
    status: StorageFeeStatus
    #: What the order owes for storage now: the charge added to its list of charges when > 0.
    amount_vnd: int
    #: The computation, when one was made (`FREE_PERIOD`, `ACCRUING`).
    fee: StorageFee | None = None


def order_storage_fee(
    policy: StoragePolicy | None,
    *,
    awaiting: bool,
    ready_at: datetime | None,
    as_of: datetime,
    quoted_total_vnd: int | None,
    waived: bool,
    settled: bool,
    fixed_vnd: int | None,
) -> OrderStorageFee:
    """The storage fee one order owes at `as_of`, from its stored facts.

    `settled` is whether a settlement row exists (the order was paid in full); `fixed_vnd` is the
    fee recorded beside it, or `None` when none was (the order was paid before any fee accrued, or
    the fee was waived, or no policy was in force). A settled order's fee never moves again.
    """

    if fixed_vnd is not None:
        return OrderStorageFee(StorageFeeStatus.FIXED, fixed_vnd)
    if settled:
        return OrderStorageFee(StorageFeeStatus.FIXED, 0)
    if waived:
        return OrderStorageFee(StorageFeeStatus.WAIVED, 0)
    if policy is None:
        return OrderStorageFee(StorageFeeStatus.POLICY_UNPUBLISHED, 0)
    if not awaiting or ready_at is None:
        return OrderStorageFee(StorageFeeStatus.NOT_WAITING, 0)
    if quoted_total_vnd is None:
        return OrderStorageFee(StorageFeeStatus.NO_SINGLE_TOTAL, 0)
    fee = storage_fee(policy, ready_at=ready_at, as_of=as_of, quoted_total_vnd=quoted_total_vnd)
    status = StorageFeeStatus.ACCRUING if fee.amount_vnd > 0 else StorageFeeStatus.FREE_PERIOD
    return OrderStorageFee(status, fee.amount_vnd, fee)


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
) -> DisposalVerdict:
    """`DEC-036`: may the owner dispose of this order's laundry now?

    Who may press it (the owner, with MFA) is the repository's check; this is the rule about the
    order. An attempt recorded before the laundry was last ready (before a rewash) does not count.
    """

    counted = [moment for moment in attempt_times if ready_at is not None and moment >= ready_at]
    distinct_days = len({shop_date(moment) for moment in counted})
    refusals: list[DisposalRefusal] = []
    waited = None if ready_at is None else days_waiting(ready_at, as_of)
    eligible_on = None
    if policy is None:
        refusals.append(DisposalRefusal.STORAGE_POLICY_UNPUBLISHED)
    if not awaiting or ready_at is None:
        refusals.append(DisposalRefusal.NOT_AWAITING_PICKUP)
    if policy is not None:
        if ready_at is not None:
            eligible_on = date.fromordinal(
                shop_date(ready_at).toordinal() + policy.disposal_from_day
            )
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
    "STORAGE_DECISION_REF",
    "STORAGE_POLICY_CONFIG_TYPE",
    "STORAGE_POLICY_VERSION",
    "ContactChannel",
    "ContactOutcome",
    "DisposalMoney",
    "DisposalRefusal",
    "DisposalVerdict",
    "NoteRefusal",
    "OrderStorageFee",
    "StorageFee",
    "StorageFeeStatus",
    "StoragePolicy",
    "StoragePolicyError",
    "awaiting_pickup",
    "clean_note",
    "days_waiting",
    "disposal_money",
    "disposal_rule_vi",
    "disposal_verdict",
    "order_storage_fee",
    "parse_storage_policy",
    "receipt_line_vi",
    "shop_date",
    "storage_fee",
    "validate_storage_document",
    "withdrawal_document",
]
