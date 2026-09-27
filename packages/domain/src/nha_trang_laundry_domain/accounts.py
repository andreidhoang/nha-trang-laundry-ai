"""Account customers (khách công nợ): the owner's limit, the monthly statement, the overdue block.

`PAYMENT-002`, the B2B half of `DEC-035` (2026-09-25). The owner-confirmed business fact is that B2B
credit is "có hỗ trợ sau phê duyệt"; `DEC-035` fixes the terms that were `CẦN CHỐT`:

* **Who.** Only a `BUSINESS` customer the **owner** marks as an account, with a **credit limit in
  VND the owner types**. Until a limit is typed the account is refused `ACCOUNT_LIMIT_UNSET`: no
  default is enforced. The recommended starting limit, 3.000.000 ₫, is a hint shown beside the field
  and nothing else -- `RECOMMENDED_STARTING_LIMIT_VND` is never read by a decision below.
* **Statement period.** The calendar month, in the shop's time zone (`Asia/Ho_Chi_Minh`).
* **Due date.** The 15th of the following month. Paying on the 15th is on time; from the 16th the
  month's statement is overdue while any of the money charged up to its end is still unpaid.
* **Unpaid orders.** The goods of an account customer may leave unpaid while the account's
  outstanding total plus this order's remaining amount stays within the limit -- equal to the limit
  is within it.
* **Overdue.** While a statement is overdue, new orders for the account are paid at the counter.
  The owner may lift the block until a date, with a reason; every lift is recorded.

The statement's figures are PostgreSQL's (opening, charges, payments, closing); what this module
decides is which month a moment belongs to, when a month is due, whether a statement is overdue on a
given day, whether an order may leave on the account, and how a payment is split across the unpaid
orders (oldest first). Pure: no clock, no database, no environment. The instant and the local day
are always parameters. Integer đồng only; nothing here divides or rounds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Final
from zoneinfo import ZoneInfo

#: The decision these rules implement.
ACCOUNT_DECISION: Final = "DEC-035"

#: The shop's day boundary, the one `settlement.BUSINESS_TIMEZONE` names.
ACCOUNT_TIMEZONE: Final = "Asia/Ho_Chi_Minh"

#: `DEC-035`: payment is due by the 15th of the month after the statement's month.
STATEMENT_DUE_DAY: Final = 15

#: `DEC-035`: "recommended starting limit (not enforced): 3.000.000 ₫". Shown as a hint beside the
#: owner's limit field. No decision in this module reads it.
RECOMMENDED_STARTING_LIMIT_VND: Final = 3_000_000

#: The largest amount a limit or a payment may be, the bound every money column in this schema uses.
MAX_ACCOUNT_VND: Final = 9_007_199_254_740_991

#: How far ahead the owner may lift an overdue block in one press, in days. A lift is a promise the
#: owner makes to one customer for a while, not a way to switch the rule off: one statement cycle is
#: the longest single lift, and the owner may lift again when it runs out. (Engineering bound, not a
#: business term; `DEC-035` says only that every lift is recorded. Reported with `PAYMENT-002`.)
MAX_LIFT_DAYS: Final = 31

#: The owner's reason for a lift, trimmed: at least this many characters, at most the second.
LIFT_REASON_MIN: Final = 3
LIFT_REASON_MAX: Final = 200

#: The configuration type the owner publishes the terms under, and the document's version tag.
ACCOUNT_TERMS_CONFIG_TYPE: Final = "ACCOUNT_TERMS"
ACCOUNT_TERMS_VERSION: Final = "account-terms-v1"


class AccountStatus(StrEnum):
    """Whether the account may take new orders unpaid. Payments are taken in either state."""

    ACTIVE = "ACTIVE"
    #: The owner stopped the account (`DEC-035`'s reversal, "unmark accounts"). What is owed stays
    #: owed and is still collected; nothing new leaves unpaid.
    SUSPENDED = "SUSPENDED"


class AccountRefusal(StrEnum):
    """Why an account command, or an order leaving on the account, is refused, by name."""

    #: The owner has not published the account terms (`scripts/publish_account_terms.py`).
    ACCOUNT_TERMS_UNPUBLISHED = "ACCOUNT_TERMS_UNPUBLISHED"
    #: Only a `BUSINESS` customer may have an account (`DEC-035`).
    ACCOUNT_REQUIRES_BUSINESS = "ACCOUNT_REQUIRES_BUSINESS"
    #: The customer's personal data was erased; no new account is opened for the record.
    CUSTOMER_ERASED = "CUSTOMER_ERASED"
    ACCOUNT_ALREADY_OPEN = "ACCOUNT_ALREADY_OPEN"
    #: The order's customer has no account, or the order has no customer record at all.
    NOT_AN_ACCOUNT_CUSTOMER = "NOT_AN_ACCOUNT_CUSTOMER"
    #: The owner stopped the account; new orders are paid at the counter.
    ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"
    #: The owner has not typed this account's limit. No default is enforced (`DEC-035`).
    ACCOUNT_LIMIT_UNSET = "ACCOUNT_LIMIT_UNSET"
    #: A limit that is not a whole number of đồng from 1 to the bound.
    ACCOUNT_LIMIT_INVALID = "ACCOUNT_LIMIT_INVALID"
    #: Outstanding plus this order's remaining amount would be above the limit.
    ACCOUNT_LIMIT_EXCEEDED = "ACCOUNT_LIMIT_EXCEEDED"
    #: A statement is past its due date with money still unpaid, and the owner has not lifted it.
    ACCOUNT_OVERDUE = "ACCOUNT_OVERDUE"
    #: The lift's end date is before today or more than `MAX_LIFT_DAYS` ahead.
    LIFT_EXPIRY_INVALID = "LIFT_EXPIRY_INVALID"
    #: A lift needs the owner's reason, 3 to 200 characters.
    LIFT_REASON_REQUIRED = "LIFT_REASON_REQUIRED"
    #: The change names nothing that differs from the account as it stands.
    NOTHING_TO_CHANGE = "NOTHING_TO_CHANGE"
    #: `YYYY-MM`, a real month.
    MONTH_INVALID = "MONTH_INVALID"
    #: A statement is frozen only once its month has ended in the shop's time zone.
    MONTH_NOT_ENDED = "MONTH_NOT_ENDED"
    # --- the order being charged ---------------------------------------------------------------
    #: Only a running order leaves on the account.
    ORDER_NOT_ACTIVE = "ORDER_NOT_ACTIVE"
    #: Nothing is owed on the order: it is paid, refunded, or already on the account.
    NOTHING_OWED = "NOTHING_OWED"
    #: The order's quote presents no single total (`DEC-001` / `DEC-003`).
    NO_PRESENTABLE_TOTAL = "NO_PRESENTABLE_TOTAL"
    #: The laundry is not finished, so it cannot leave.
    GOODS_NOT_READY_FOR_HANDOVER = "GOODS_NOT_READY_FOR_HANDOVER"
    #: A customer who collects at the counter takes the goods in the same press that charges the
    #: account: the charge is the handover. Charging without it would put an order on the account
    #: whose goods are still on the shelf.
    ACCOUNT_CHARGE_IS_THE_HANDOVER = "ACCOUNT_CHARGE_IS_THE_HANDOVER"
    #: "Collected at the counter" on an order that travels back by courier (`DEC-023`).
    COLLECTION_WAS_NOT_BY_THE_CUSTOMER = "COLLECTION_WAS_NOT_BY_THE_CUSTOMER"
    #: The order is already recorded as taken.
    ALREADY_COLLECTED = "ALREADY_COLLECTED"


class AccountRuleError(ValueError):
    """A refusal by name. `str()` is the code, so a caller never paraphrases it."""

    def __init__(self, code: AccountRefusal) -> None:
        super().__init__(code.value)
        self.code = code


# --- months and due dates ---------------------------------------------------------------------


def _zone() -> ZoneInfo:
    return ZoneInfo(ACCOUNT_TIMEZONE)


def local_day(moment: datetime) -> date:
    """The shop's calendar day an instant falls on."""

    if moment.tzinfo is None:
        raise ValueError("an account instant must be timezone-aware")
    return moment.astimezone(_zone()).date()


def statement_month(moment: datetime) -> date:
    """The first day of the calendar month, in the shop's time zone, an instant belongs to.

    23:59:59 local on the last day of a month is that month; one microsecond after local midnight
    is the next, whatever UTC says.
    """

    day = local_day(moment)
    return date(day.year, day.month, 1)


def next_month(month: date) -> date:
    """The first day of the month after `month` (which must be a first day)."""

    _require_first_day(month)
    return date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)


def previous_month(month: date) -> date:
    _require_first_day(month)
    return date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)


def statement_due_on(month: date) -> date:
    """`DEC-035`: the 15th of the month after the statement's month."""

    following = next_month(month)
    return date(following.year, following.month, STATEMENT_DUE_DAY)


def latest_due_month(today: date) -> date:
    """The latest statement month whose due date is strictly before `today`.

    On 15 October the September statement is still on time (due that day), so the latest month
    already past due is August; on 16 October it is September.
    """

    month = date(today.year, today.month, 1)
    candidate = previous_month(month)
    if statement_due_on(candidate) < today:
        return candidate
    return previous_month(candidate)


def month_start_instant(month: date) -> datetime:
    """Local midnight on the first day of `month`, as an aware instant."""

    _require_first_day(month)
    return datetime.combine(month, time(0, 0), tzinfo=_zone())


def parse_month(text: str) -> date:
    """`YYYY-MM` as its first day, or `MONTH_INVALID`."""

    match = re.fullmatch(r"(\d{4})-(\d{2})", text.strip())
    if match is None:
        raise AccountRuleError(AccountRefusal.MONTH_INVALID)
    year, number = int(match.group(1)), int(match.group(2))
    if not 2000 <= year <= 2999 or not 1 <= number <= 12:
        raise AccountRuleError(AccountRefusal.MONTH_INVALID)
    return date(year, number, 1)


def month_label(month: date) -> str:
    """`YYYY-MM`, the form the routes and the frozen rows name a month by."""

    _require_first_day(month)
    return f"{month.year:04d}-{month.month:02d}"


def month_has_ended(month: date, now: datetime) -> bool:
    """Whether the shop's clock has passed the end of `month` (local midnight of the next month)."""

    return now >= month_start_instant(next_month(month))


def _require_first_day(month: date) -> None:
    if month.day != 1:
        raise ValueError("a statement month is named by its first day")


# --- the limit and the owner's lift -----------------------------------------------------------


def validate_limit(value: object) -> int | None:
    """The owner's limit as stored: `None` (not typed yet) or a whole number of đồng ≥ 1."""

    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > MAX_ACCOUNT_VND
    ):
        raise AccountRuleError(AccountRefusal.ACCOUNT_LIMIT_INVALID)
    return value


def clean_lift_reason(text: str | None) -> str:
    """The owner's reason, trimmed with inner runs of space collapsed; refused when too short."""

    cleaned = " ".join((text or "").split())
    if not LIFT_REASON_MIN <= len(cleaned) <= LIFT_REASON_MAX:
        raise AccountRuleError(AccountRefusal.LIFT_REASON_REQUIRED)
    return cleaned


def lift_until_instant(until: date, *, today: date) -> datetime:
    """The instant a lift through the whole of local day `until` ends: the next local midnight.

    `until` is the last day the block stays lifted; it may be today and at most `MAX_LIFT_DAYS`
    ahead.
    """

    if until < today or until > today + timedelta(days=MAX_LIFT_DAYS):
        raise AccountRuleError(AccountRefusal.LIFT_EXPIRY_INVALID)
    return datetime.combine(until + timedelta(days=1), time(0, 0), tzinfo=_zone())


# --- where an account stands ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountStanding:
    """Every fact about an account an order-leaving decision reads. All stored or summed by SQL.

    `outstanding_vnd` is the account's charges less its payments. `overdue_vnd` is what is still
    unpaid of the money charged up to the end of `latest_due_month(today)`: the money charged in a
    month whose due date has passed, less every payment taken since (oldest first, so a payment
    always settles the oldest money). Both are PostgreSQL's sums; this class only reads them.
    """

    status: AccountStatus
    credit_limit_vnd: int | None
    outstanding_vnd: int
    overdue_vnd: int
    overdue_block_lifted_until: datetime | None
    #: When these facts were read; the lift is compared against it.
    as_of: datetime

    @property
    def overdue(self) -> bool:
        return self.overdue_vnd > 0

    @property
    def block_lifted(self) -> bool:
        """Whether the owner's lift is in force at `as_of`. It ends at its instant, exclusive."""

        until = self.overdue_block_lifted_until
        return until is not None and self.as_of < until

    @property
    def available_vnd(self) -> int | None:
        """How much more may be charged before the limit; `None` while the limit is unset."""

        if self.credit_limit_vnd is None:
            return None
        return max(self.credit_limit_vnd - self.outstanding_vnd, 0)


@dataclass(frozen=True, slots=True)
class AccountHandoverFacts:
    """`settlement.goods_may_leave`'s second input: the account, and what this order still owes."""

    standing: AccountStanding
    order_remaining_vnd: int


def account_handover_refusal(facts: AccountHandoverFacts) -> AccountRefusal | None:
    """Whether the account lets this order's goods leave unpaid now. `None` means it does.

    In order: a stopped account; no limit typed (no default is ever assumed); an overdue statement
    the owner has not lifted; and the limit itself -- outstanding plus this order's remaining amount
    must be at most the limit, so an order that takes the account exactly to its limit leaves.
    """

    standing = facts.standing
    if standing.status is AccountStatus.SUSPENDED:
        return AccountRefusal.ACCOUNT_SUSPENDED
    if standing.credit_limit_vnd is None:
        return AccountRefusal.ACCOUNT_LIMIT_UNSET
    if standing.overdue and not standing.block_lifted:
        return AccountRefusal.ACCOUNT_OVERDUE
    if not _whole(facts.order_remaining_vnd) or not _whole(standing.outstanding_vnd):
        raise ValueError("account amounts must be non-negative whole đồng")
    if standing.outstanding_vnd + facts.order_remaining_vnd > standing.credit_limit_vnd:
        return AccountRefusal.ACCOUNT_LIMIT_EXCEEDED
    return None


# --- a payment against the account ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenCharge:
    """One order on the account with money still owed on it, as SQL listed it (oldest first)."""

    charge_id: Any
    order_id: Any
    remaining_vnd: int


@dataclass(frozen=True, slots=True)
class Allocation:
    """How much of one account payment settles one order's charge."""

    charge_id: Any
    order_id: Any
    amount_vnd: int
    #: Whether this allocation pays the order's charge in full.
    settles: bool


def allocate_oldest_first(
    amount_vnd: int, open_charges: tuple[OpenCharge, ...]
) -> tuple[Allocation, ...]:
    """Split a payment across the unpaid orders, oldest first (`PAYMENT-002`).

    `open_charges` is in the order SQL listed it -- charged first, first -- and every entry owes at
    least 1 đồng. The payment fills each in turn; the last one reached may be paid in part. A
    payment larger than everything owed is the caller's refusal (`OVERPAYMENT_REFUSED`), asked
    before this; here it is a contradiction and raises.
    """

    if not _whole(amount_vnd) or amount_vnd < 1:
        raise ValueError("an account payment is at least 1 đồng")
    left = amount_vnd
    allocations: list[Allocation] = []
    for item in open_charges:
        if not _whole(item.remaining_vnd) or item.remaining_vnd < 1:
            raise ValueError("an open charge owes at least 1 đồng")
        if left == 0:
            break
        take = min(left, item.remaining_vnd)
        allocations.append(
            Allocation(item.charge_id, item.order_id, take, take == item.remaining_vnd)
        )
        left -= take
    if left:
        raise ValueError("the payment is larger than what the account owes")
    return tuple(allocations)


# --- the owner's published terms --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountTerms:
    """The account terms as the owner published them. Every value is `DEC-035`'s."""

    policy_version: str
    decision_ref: str
    statement_period: str
    due_day_of_next_month: int
    timezone: str
    overdue_blocks_unpaid_handover: bool
    recommended_starting_limit_vnd: int
    title_vi: str
    text_vi: str


class AccountTermsError(ValueError):
    """The document is not `DEC-035`'s terms."""


_TERMS_KEYS: Final = frozenset(
    {
        "policy_version",
        "decision_ref",
        "statement_period",
        "due_day_of_next_month",
        "timezone",
        "overdue_blocks_unpaid_handover",
        "recommended_starting_limit_vnd",
        "title_vi",
        "text_vi",
    }
)


def parse_account_terms(payload: dict[str, Any]) -> AccountTerms:
    """Validate the document against `DEC-035`. Only its terms are supported: a different due day,
    period or zone is a different decision, and this system implements exactly one."""

    if set(payload) != _TERMS_KEYS:
        raise AccountTermsError("the account terms name exactly the DEC-035 fields")
    expected = {
        "policy_version": ACCOUNT_TERMS_VERSION,
        "decision_ref": ACCOUNT_DECISION,
        "statement_period": "CALENDAR_MONTH",
        "due_day_of_next_month": STATEMENT_DUE_DAY,
        "timezone": ACCOUNT_TIMEZONE,
        "overdue_blocks_unpaid_handover": True,
        "recommended_starting_limit_vnd": RECOMMENDED_STARTING_LIMIT_VND,
    }
    for key, value in expected.items():
        if payload.get(key) != value or type(payload.get(key)) is not type(value):
            raise AccountTermsError(f"{key} must be {value!r} (DEC-035)")
    for key in ("title_vi", "text_vi"):
        text = payload.get(key)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise AccountTermsError(f"{key} is the owner's text, 1 to 2000 characters")
    text_vi = str(payload["text_vi"])
    for fact in ("15", "3.000.000"):
        if fact not in text_vi:
            raise AccountTermsError(f"the text must state {fact!r} as DEC-035 does")
    return AccountTerms(
        policy_version=ACCOUNT_TERMS_VERSION,
        decision_ref=ACCOUNT_DECISION,
        statement_period="CALENDAR_MONTH",
        due_day_of_next_month=STATEMENT_DUE_DAY,
        timezone=ACCOUNT_TIMEZONE,
        overdue_blocks_unpaid_handover=True,
        recommended_starting_limit_vnd=RECOMMENDED_STARTING_LIMIT_VND,
        title_vi=str(payload["title_vi"]).strip(),
        text_vi=text_vi.strip(),
    )


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_ACCOUNT_VND


__all__ = [
    "ACCOUNT_DECISION",
    "ACCOUNT_TERMS_CONFIG_TYPE",
    "ACCOUNT_TERMS_VERSION",
    "ACCOUNT_TIMEZONE",
    "LIFT_REASON_MAX",
    "LIFT_REASON_MIN",
    "MAX_ACCOUNT_VND",
    "MAX_LIFT_DAYS",
    "RECOMMENDED_STARTING_LIMIT_VND",
    "STATEMENT_DUE_DAY",
    "AccountHandoverFacts",
    "AccountRefusal",
    "AccountRuleError",
    "AccountStanding",
    "AccountStatus",
    "AccountTerms",
    "AccountTermsError",
    "Allocation",
    "OpenCharge",
    "account_handover_refusal",
    "allocate_oldest_first",
    "clean_lift_reason",
    "latest_due_month",
    "lift_until_instant",
    "local_day",
    "month_has_ended",
    "month_label",
    "month_start_instant",
    "next_month",
    "parse_account_terms",
    "parse_month",
    "previous_month",
    "statement_due_on",
    "statement_month",
    "validate_limit",
]
