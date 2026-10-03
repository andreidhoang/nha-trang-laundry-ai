"""Invoice requests (*Khách cần hóa đơn*): what a request is, what it may say, where it may go.

`EINVOICE-REQUEST-001`, `DEC-040` (2026-09-28). A Vietnamese e-invoice is issued only through a
licensed provider under the shop's own tax code and signature, and which invoice type, rate and date
apply is the owner's and the accountant's to settle. So this module decides nothing about an
invoice. It decides:

* **what the buyer's details may be** -- a unit name (required, 1-200), a tax code (optional: 10
  digits, 10 digits + ``-`` + 3 digits, or 12 digits), an address (required whenever a tax code is
  given, 1-300), an invoice email (optional, a simple shape) and a buyer name (optional, 1-120).
  Every text is trimmed and its inner whitespace collapsed; none may hold a control character, begin
  with what a spreadsheet would execute (``= + - @``), or look like a phone number (`PHONE_LIKE`):
  the list is downloaded for a bookkeeper, and no phone value may reach an export;
* **what the bookkeeper reads back** -- the invoice's symbol (*ký hiệu*, 1-12 of ``0-9A-Z``), its
  number (1-8 digits) and its date, which may not be after the shop's today;
* **how a request closes** -- ``REQUESTED`` -> ``ISSUED`` | ``CANCELLED`` (with a reason, and a
  note when the reason is ``OTHER``); both are terminal.

No tax is split, no rate is named and nothing is called a *hóa đơn* that the software prints: the
amounts a request carries are the ones the order and the account charges already publish. An open
request reads them at the moment it is read; an issued one carries the figure fixed when it was
issued (`INVOICE-TRUTH-009`, `0068`), and what happened to its orders afterwards is said beside it
as a flag (`issued_flags`) rather than folded into that figure. Pure: no clock, no database, no
environment.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final
from uuid import UUID

from nha_trang_laundry_domain.unclaimed import PHONE_LIKE

#: The decision these rules implement.
INVOICE_DECISION: Final = "DEC-040"

#: `DEC-040`: the tax code's three shapes.
TAX_CODE_PATTERN: Final = re.compile(r"(?:[0-9]{10}(?:-[0-9]{3})?|[0-9]{12})")
#: The invoice's symbol (*ký hiệu*) as the provider prints it, and its number.
INVOICE_SYMBOL_PATTERN: Final = re.compile(r"[0-9A-Z]{1,12}")
INVOICE_NUMBER_PATTERN: Final = re.compile(r"[0-9]{1,8}")
#: A simple email shape: one ``@``, a dot in the domain, no space. Not a deliverability check.
EMAIL_PATTERN: Final = re.compile(r"[^@\s=+\-][^@\s]*@[^@\s]+\.[^@\s]+")
#: What a spreadsheet would execute at the start of a cell.
FORMULA_START: Final = ("=", "+", "-", "@")
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")

UNIT_NAME_MAX: Final = 200
ADDRESS_MAX: Final = 300
EMAIL_MAX: Final = 254
BUYER_NAME_MAX: Final = 120
CANCEL_NOTE_MAX: Final = 200


class InvoiceSubjectKind(StrEnum):
    """What a request is for."""

    ORDER = "ORDER"
    #: An account customer's calendar month (`DEC-035`'s statement period).
    ACCOUNT_MONTH = "ACCOUNT_MONTH"


class InvoiceRequestStatus(StrEnum):
    REQUESTED = "REQUESTED"
    #: The bookkeeper issued it in the provider's portal; staff recorded symbol, number, date.
    ISSUED = "ISSUED"
    CANCELLED = "CANCELLED"


class InvoiceCancelReason(StrEnum):
    CUSTOMER_WITHDREW = "CUSTOMER_WITHDREW"
    DUPLICATE = "DUPLICATE"
    WRONG_DETAILS = "WRONG_DETAILS"
    #: Needs a note saying what.
    OTHER = "OTHER"


class InvoiceRefusal(StrEnum):
    """Why a request, or a change to one, is refused, by name."""

    #: The owner has not published the customer privacy notice (`DEC-034`): a buyer's name or email
    #: can identify a person, so nothing is captured until then.
    PRIVACY_NOTICE_UNPUBLISHED = "PRIVACY_NOTICE_UNPUBLISHED"
    #: The order is cancelled or not in this store; the account month is not the store's, is in the
    #: future, or has nothing charged in it.
    INVOICE_SUBJECT_UNAVAILABLE = "INVOICE_SUBJECT_UNAVAILABLE"
    #: A live request (REQUESTED or ISSUED) already covers this subject -- or covers it through the
    #: other kind: an order that an account month's live request covers (the month's open request,
    #: or the orders its issued invoice listed), or an account month every one of whose charged
    #: orders has its own request (`INVOICE-TRUTH-009`: one with some left covers the rest).
    INVOICE_REQUEST_EXISTS = "INVOICE_REQUEST_EXISTS"
    INVOICE_TAX_CODE_SHAPE = "INVOICE_TAX_CODE_SHAPE"
    #: A tax code was given without an address.
    INVOICE_ADDRESS_REQUIRED = "INVOICE_ADDRESS_REQUIRED"
    #: A buyer text is missing (the unit name), too long, or holds a control character, a leading
    #: formula character or an email that is not one. `field` says which.
    INVOICE_BUYER_FIELD_INVALID = "INVOICE_BUYER_FIELD_INVALID"
    #: A buyer text or a note holds something that looks like a phone number.
    INVOICE_FIELD_LOOKS_LIKE_PHONE = "INVOICE_FIELD_LOOKS_LIKE_PHONE"
    #: The request is already ISSUED or CANCELLED.
    INVOICE_REQUEST_CLOSED = "INVOICE_REQUEST_CLOSED"
    #: The symbol, the number or the date the bookkeeper read back is not one (or the date is after
    #: the shop's today). `field` says which.
    INVOICE_ISSUED_DETAILS_INVALID = "INVOICE_ISSUED_DETAILS_INVALID"
    #: Another request of this store already records this symbol and number.
    INVOICE_NUMBER_TAKEN = "INVOICE_NUMBER_TAKEN"
    #: *Ghi số hóa đơn* named orders the request does not cover now (`orders_on_invoice`): one
    #: that took a request of its own since the sheet was read, one that is not this request's, a
    #: repeated one, or none. Nothing is fixed; the owner reads the request again.
    INVOICE_AMOUNT_MOVED = "INVOICE_AMOUNT_MOVED"
    #: The total typed off the invoice is not what the orders it lists cost the customer now
    #: (`orders_on_invoice`): a typing slip, an order ticked that the invoice does not list, or the
    #: order's figure moved since the invoice was made. Nothing is fixed. `field` is
    #: ``invoice_total_vnd``.
    INVOICE_TOTAL_MISMATCH = "INVOICE_TOTAL_MISMATCH"
    #: A cancellation for ``OTHER`` says what, in 1-200 characters.
    INVOICE_CANCEL_NOTE_REQUIRED = "INVOICE_CANCEL_NOTE_REQUIRED"
    #: Round 9b (J6d): an order request whose quote presents no single total (a range) cannot be
    #: recorded as issued -- there is no figure the shop charges to fix it at. It can be cancelled,
    #: or wait until the price is closed.
    INVOICE_TOTAL_UNKNOWN = "INVOICE_TOTAL_UNKNOWN"


class InvoiceRuleError(ValueError):
    """A refusal by name. `str()` is the code; `field` names the input it is about, if one."""

    def __init__(self, code: InvoiceRefusal, *, field: str | None = None) -> None:
        super().__init__(code.value)
        self.code = code
        self.field = field


@dataclass(frozen=True, slots=True)
class BuyerDetails:
    """The buyer as written on a request (and on an account customer's profile)."""

    unit_name: str
    tax_code: str | None
    address: str | None
    email: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class IssuedDetails:
    symbol: str
    number: str
    issued_on: date


def _clean(text: str | None) -> str | None:
    """Trimmed, inner whitespace collapsed; `None` when nothing is left."""

    if text is None:
        return None
    return " ".join(text.split()) or None


def _checked_text(value: str | None, *, field: str, maximum: int) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    if len(cleaned) > maximum or _CONTROL.search(cleaned) or cleaned.startswith(FORMULA_START):
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID, field=field)
    if PHONE_LIKE.search(cleaned):
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_FIELD_LOOKS_LIKE_PHONE, field=field)
    return cleaned


def clean_tax_code(value: str | None) -> str | None:
    """The tax code as stored, or `None` when none was given. Spaces around it are dropped; inside
    it nothing is repaired -- a code typed with a space in it is refused, not guessed at."""

    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if TAX_CODE_PATTERN.fullmatch(stripped) is None:
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_TAX_CODE_SHAPE, field="buyer_tax_code")
    return stripped


def clean_buyer(
    *,
    unit_name: str | None,
    tax_code: str | None,
    address: str | None,
    email: str | None,
    name: str | None,
) -> BuyerDetails:
    """The buyer's details as stored, or the first refusal, in the order the sheet shows them."""

    cleaned_unit = _checked_text(unit_name, field="buyer_unit_name", maximum=UNIT_NAME_MAX)
    if cleaned_unit is None:
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID, field="buyer_unit_name")
    cleaned_tax = clean_tax_code(tax_code)
    cleaned_address = _checked_text(address, field="buyer_address", maximum=ADDRESS_MAX)
    if cleaned_tax is not None and cleaned_address is None:
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_ADDRESS_REQUIRED, field="buyer_address")
    cleaned_email = _clean(email)
    if cleaned_email is not None:
        if (
            len(cleaned_email) > EMAIL_MAX
            or _CONTROL.search(cleaned_email)
            or EMAIL_PATTERN.fullmatch(cleaned_email) is None
        ):
            raise InvoiceRuleError(InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID, field="buyer_email")
        if PHONE_LIKE.search(cleaned_email):
            raise InvoiceRuleError(
                InvoiceRefusal.INVOICE_FIELD_LOOKS_LIKE_PHONE, field="buyer_email"
            )
    cleaned_name = _checked_text(name, field="buyer_name", maximum=BUYER_NAME_MAX)
    return BuyerDetails(
        unit_name=cleaned_unit,
        tax_code=cleaned_tax,
        address=cleaned_address,
        email=cleaned_email,
        name=cleaned_name,
    )


def clean_issued(
    *, symbol: str | None, number: str | None, issued_on: date | None, today: date
) -> IssuedDetails:
    """What the bookkeeper read back from the provider's portal, or the first refusal.

    The symbol is upper-cased (the provider prints it so; a counter may type it lower-case); the
    number keeps its leading zeros, as printed. `today` is the shop's local day, passed in.
    """

    cleaned_symbol = (symbol or "").strip().upper()
    if INVOICE_SYMBOL_PATTERN.fullmatch(cleaned_symbol) is None:
        raise InvoiceRuleError(
            InvoiceRefusal.INVOICE_ISSUED_DETAILS_INVALID, field="invoice_symbol"
        )
    cleaned_number = (number or "").strip()
    if INVOICE_NUMBER_PATTERN.fullmatch(cleaned_number) is None:
        raise InvoiceRuleError(
            InvoiceRefusal.INVOICE_ISSUED_DETAILS_INVALID, field="invoice_number"
        )
    if issued_on is None or issued_on > today:
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_ISSUED_DETAILS_INVALID, field="invoice_date")
    return IssuedDetails(symbol=cleaned_symbol, number=cleaned_number, issued_on=issued_on)


def clean_cancel_note(reason: InvoiceCancelReason, note: str | None) -> str | None:
    """The cancellation's note as stored: required for ``OTHER``, optional otherwise."""

    cleaned = _clean(note)
    if cleaned is None:
        if reason is InvoiceCancelReason.OTHER:
            raise InvoiceRuleError(InvoiceRefusal.INVOICE_CANCEL_NOTE_REQUIRED, field="note")
        return None
    if len(cleaned) > CANCEL_NOTE_MAX or _CONTROL.search(cleaned):
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_CANCEL_NOTE_REQUIRED, field="note")
    if PHONE_LIKE.search(cleaned):
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_FIELD_LOOKS_LIKE_PHONE, field="note")
    return cleaned


def request_code(number: int) -> str:
    """The code the counter and the bookkeeper quote: ``YC-0007``. Never reused within a store."""

    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ValueError("a request number is a positive integer")
    return f"YC-{number:04d}"


def require_open(status: InvoiceRequestStatus) -> None:
    """Only a REQUESTED request may be recorded as issued or cancelled."""

    if status is not InvoiceRequestStatus.REQUESTED:
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_REQUEST_CLOSED)


class InvoiceFlag(StrEnum):
    """Something that happened to an issued request's orders after the invoice was issued.

    `INVOICE-TRUTH-009` (review M6). An issued invoice is a document the bookkeeper already gave the
    customer; its figure never moves. When the order moves afterwards, the request says so, by name,
    and the bookkeeper decides what the provider's portal needs (an adjusting invoice is theirs).
    """

    #: A covered order's money went back to the customer after the invoice was issued.
    REFUNDED_AFTER_ISSUE = "REFUNDED_AFTER_ISSUE"
    #: A covered order was cancelled after the invoice was issued, with no money going back.
    CANCELLED_AFTER_ISSUE = "CANCELLED_AFTER_ISSUE"
    #: What the covered orders cost, read now, is not the figure on the invoice (a storage fee
    #: accrued or was settled after it, or the invoice predates the figure's fixing).
    AMOUNT_CHANGED_AFTER_ISSUE = "AMOUNT_CHANGED_AFTER_ISSUE"
    #: An account month: orders charged to the month that its invoice does not list and that no
    #: request covers now -- charged after the issue, or left off it because each had a request of
    #: its own then, cancelled since. The flag says only that (review round 9: it once said "charged
    #: after", untrue of the second). Each can be requested on its own.
    MONTH_ORDERS_NOT_ON_INVOICE = "MONTH_ORDERS_NOT_ON_INVOICE"
    #: Round 9b (J6a): the invoice was already issued at the provider for a figure the shop's
    #: orders no longer cost when it was recorded; it was recorded at the invoice's printed figure,
    #: as the owner confirmed, and the bookkeeper is told.
    PRINTED_TOTAL_DIFFERS = "PRINTED_TOTAL_DIFFERS"
    #: Round 9b (J6b): an order this request covers is also covered by another live request --
    #: data from before `INVOICE-TRUTH-009` (the `0068` backfill fixed an account month with every
    #: order charged to it, including ones with an invoice request of their own). Said on both, so
    #: the bookkeeper never invoices the order twice; nothing is changed.
    ON_ANOTHER_REQUEST = "ON_ANOTHER_REQUEST"


@dataclass(frozen=True, slots=True)
class CoveredOrderState:
    """One covered order's state that an invoice cares about: cancelled, and money given back."""

    cancelled: bool
    refunded: bool


def issued_flags(
    *,
    fixed_total_vnd: int | None,
    live_total_vnd: int | None,
    at_issue: Mapping[UUID, CoveredOrderState],
    now: Mapping[UUID, CoveredOrderState],
    uninvoiced_month_orders: int,
    printed_total_vnd: int | None = None,
    other_live_requests: int = 0,
) -> tuple[InvoiceFlag, ...]:
    """What happened to an issued request's orders since it was issued, in a fixed order.

    `at_issue` is each covered order's state recorded with the snapshot; `now` the same orders read
    now (an order missing from `now` is read as unchanged). A refund implies the cancellation it
    came with, so an order refunded after the issue is flagged once, as refunded.
    `printed_total_vnd` is the invoice's printed figure when it was recorded at one that differs
    from what its orders cost (J6a); `other_live_requests` how many other live requests cover one
    of its orders (J6b, `request_overlap_flags` for an open request).
    """

    if uninvoiced_month_orders < 0:
        raise ValueError("a count of charges is never negative")
    refunded = cancelled = False
    for order_id, before in at_issue.items():
        after = now.get(order_id, before)
        if after.refunded and not before.refunded:
            refunded = True
        elif after.cancelled and not before.cancelled:
            cancelled = True
    flags: list[InvoiceFlag] = []
    if refunded:
        flags.append(InvoiceFlag.REFUNDED_AFTER_ISSUE)
    if cancelled:
        flags.append(InvoiceFlag.CANCELLED_AFTER_ISSUE)
    if printed_total_vnd is not None:
        flags.append(InvoiceFlag.PRINTED_TOTAL_DIFFERS)
    if fixed_total_vnd != live_total_vnd:
        flags.append(InvoiceFlag.AMOUNT_CHANGED_AFTER_ISSUE)
    if uninvoiced_month_orders:
        flags.append(InvoiceFlag.MONTH_ORDERS_NOT_ON_INVOICE)
    flags.extend(request_overlap_flags(other_live_requests))
    return tuple(flags)


def request_overlap_flags(other_live_requests: int) -> tuple[InvoiceFlag, ...]:
    """J6b: `ON_ANOTHER_REQUEST` when another live request covers one of this request's orders."""

    if other_live_requests < 0:
        raise ValueError("a count of requests is never negative")
    return (InvoiceFlag.ON_ANOTHER_REQUEST,) if other_live_requests else ()


@dataclass(frozen=True, slots=True)
class OnInvoice:
    """What *Ghi số hóa đơn* fixes: the orders the invoice lists, and -- only when the owner
    confirmed an invoice printed for a figure the orders no longer cost -- that printed figure."""

    order_ids: tuple[UUID, ...]
    #: The invoice's printed total when it differs from what the orders cost (`J6a`), else None.
    printed_total_vnd: int | None = None


def orders_on_invoice(
    *,
    kind: InvoiceSubjectKind,
    invoice_total_vnd: int | None,
    invoice_order_ids: Sequence[UUID],
    request_total_vnd: int | None,
    lines: Sequence[tuple[UUID, int | None]],
    record_printed: bool = False,
) -> OnInvoice:
    """Which of an open request's orders the issued invoice lists, from what *Ghi số hóa đơn* sent.

    Review round 9 (M5, M6): an issued request is fixed at what the INVOICE says -- the total the
    owner types off it and the orders it lists -- never at what the sheet happened to read when it
    was opened. `lines` are the request's orders now, read under the store's lock, each with what it
    cost the customer; `request_total_vnd` is the request's figure now.

    * The orders sent are a non-empty set of the request's orders now, each once; anything else is
      `INVOICE_AMOUNT_MOVED` (the sheet is stale or the input is not this request's).
    * An account month may be fixed at some of its orders -- the invoice was made before others
      went on the month. The rest stay on no invoice: the issued month says so
      (`MONTH_ORDERS_NOT_ON_INVOICE`) and each can be requested on its own.
    * An order whose quote presents no single total has no figure to fix: `INVOICE_TOTAL_UNKNOWN`
      (round 9b, J6d), whatever was typed.
    * The total is exactly what the orders sent cost: the order's figure for an order, the sum of
      the lines sent for a month. Anything else is `INVOICE_TOTAL_MISMATCH` on
      ``invoice_total_vnd`` -- a typing slip is the common cause, so the press is refused first.
    * Round 9b (J6a): the invoice is a document the provider already issued, so when the owner
      confirms the typed total is what it prints (`record_printed`), it is recorded at that
      printed figure (`OnInvoice.printed_total_vnd`) beside what the orders cost, and flagged --
      never refused into a dead end. No figure of the shop's is adjusted.

    Returns the orders sent, in the request's order, and the printed figure when it differs.
    """

    sent = list(invoice_order_ids)
    covered = [order_id for order_id, _amount in lines]
    if not sent or len(set(sent)) != len(sent) or not set(sent) <= set(covered):
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_AMOUNT_MOVED)
    chosen = tuple(order_id for order_id in covered if order_id in set(sent))
    if kind is InvoiceSubjectKind.ORDER:
        total = request_total_vnd
        if total is None:
            raise InvoiceRuleError(InvoiceRefusal.INVOICE_TOTAL_UNKNOWN)
    else:
        amounts = [amount for order_id, amount in lines if order_id in set(chosen)]
        if any(amount is None for amount in amounts):
            raise ValueError("an account month's line always has an amount")
        total = sum(amount for amount in amounts if amount is not None)
    if invoice_total_vnd == total:
        return OnInvoice(chosen)
    if not record_printed or invoice_total_vnd is None:
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_TOTAL_MISMATCH, field="invoice_total_vnd")
    if (
        not isinstance(invoice_total_vnd, int)
        or isinstance(invoice_total_vnd, bool)
        or invoice_total_vnd < 0
    ):
        raise InvoiceRuleError(InvoiceRefusal.INVOICE_TOTAL_MISMATCH, field="invoice_total_vnd")
    return OnInvoice(chosen, printed_total_vnd=invoice_total_vnd)


#: The unit words the bookkeeper's list prints beside a quantity.
UNIT_VI: Final = {
    "KG": "kg",
    "ITEM": "cái",
    "PAIR": "đôi",
    "SET": "bộ",
    "ANIMAL_PLUSH_ITEM": "con",
    "CASE": "vỏ",
    "M2": "m²",
}


__all__ = [
    "ADDRESS_MAX",
    "BUYER_NAME_MAX",
    "CANCEL_NOTE_MAX",
    "EMAIL_MAX",
    "INVOICE_DECISION",
    "UNIT_NAME_MAX",
    "UNIT_VI",
    "BuyerDetails",
    "CoveredOrderState",
    "InvoiceCancelReason",
    "InvoiceFlag",
    "InvoiceRefusal",
    "InvoiceRequestStatus",
    "InvoiceRuleError",
    "InvoiceSubjectKind",
    "IssuedDetails",
    "OnInvoice",
    "clean_buyer",
    "clean_cancel_note",
    "clean_issued",
    "clean_tax_code",
    "issued_flags",
    "orders_on_invoice",
    "request_code",
    "request_overlap_flags",
    "require_open",
]
