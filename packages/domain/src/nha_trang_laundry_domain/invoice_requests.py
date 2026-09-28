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
amounts a request carries are the ones the order and the account statement already publish, read at
the moment the request is read. Pure: no clock, no database, no environment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

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
    #: other kind: an order charged to an account month that has a request, or an account month one
    #: of whose orders has its own.
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
    #: A cancellation for ``OTHER`` says what, in 1-200 characters.
    INVOICE_CANCEL_NOTE_REQUIRED = "INVOICE_CANCEL_NOTE_REQUIRED"


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
    "InvoiceCancelReason",
    "InvoiceRefusal",
    "InvoiceRequestStatus",
    "InvoiceRuleError",
    "InvoiceSubjectKind",
    "IssuedDetails",
    "clean_buyer",
    "clean_cancel_note",
    "clean_issued",
    "clean_tax_code",
    "request_code",
    "require_open",
]
