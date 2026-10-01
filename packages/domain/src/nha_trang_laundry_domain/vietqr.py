"""An exact VietQR for what is owed: the payload, its checksum, and the transfer code. `VIETQR-001`.

`DEC-041` (2026-09-28). Customers in Vietnam pay by scanning a QR code; when they type the amount
and the memo themselves the memo is empty or wrong and the amount is off, and the counter cannot
tell which order a transfer was for. This module builds the NAPAS VietQR (EMVCo merchant-presented
mode) payload for the shop's published account, the amount the payment ledger says is still owed,
and a transfer code that names the order -- with no provider call.

**The payload.** Each field is its id, a two-digit length and its value, in this order:

======  ===========================================================================================
``00``  ``01`` (payload format)
``01``  ``12`` when an amount is present (dynamic), ``11`` for the static account QR
``38``  ``00`` = ``A000000727`` (NAPAS), ``01`` = (``00`` = bank BIN, ``01`` = account),
        ``02`` = ``QRIBFTTA`` (transfer to an account)
``53``  ``704`` (VND)
``54``  the amount: whole đồng, digits only, above zero (dynamic only)
``58``  ``VN``
``62``  ``08`` = the transfer code (the memo the customer's bank app pre-fills; dynamic only)
``63``  CRC-16/CCITT-FALSE over everything before it *including* ``6304``, four upper-case hex
======  ===========================================================================================

**The transfer code** (`order_transfer_code`, `account_month_transfer_code`):

* ``NTL`` + ``ddmm`` of the ticket's business day + the ticket number in three digits, for example
  ``NTL2809012`` -- what the counter already says out loud;
* else ``NTL`` + the first 8 hex digits of the order id, upper-cased (an order that came by message
  and has no ticket; also a ticket above 999, which three digits cannot hold -- a two-person shop
  never issues a thousand tickets in a day, but a code must never be ambiguous if one did);
* an account customer's month: ``NTLCN`` + the first 6 hex digits of the account id + ``mmyy``.

The two order forms never collide: after ``NTL`` the ticket form is exactly seven decimal digits
and the id form exactly eight hex digits, and ``N`` is not a hex digit, so ``NTLCN`` is neither.

Pure: no clock, no database, no environment. Integer đồng only; nothing here adds, rounds or
divides money -- the amount is the ledger's, passed in, and only written out as digits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final
from uuid import UUID

from nha_trang_laundry_domain.catalog import CommercialOrderStatus, OrderBalanceStatus
from nha_trang_laundry_domain.settlement import MAX_SETTLEMENT_VND

#: The configuration type the owner's bank account is published under, and its document version.
BANK_TRANSFER_ACCOUNT_CONFIG_TYPE: Final = "BANK_TRANSFER_ACCOUNT"
BANK_TRANSFER_ACCOUNT_VERSION: Final = "bank-transfer-account-v1"
BANK_TRANSFER_DECISION_REF: Final = "DEC-041"

#: NAPAS's globally unique identifier in the merchant account information template (`38.00`).
NAPAS_GUID: Final = "A000000727"
#: `38.02`: the service is a fast transfer to a bank account (as opposed to a card, `QRIBFTTC`).
SERVICE_TO_ACCOUNT: Final = "QRIBFTTA"
CURRENCY_VND: Final = "704"
COUNTRY_VN: Final = "VN"

#: The static QR (no amount, no memo) and the dynamic one (an amount is present).
POINT_OF_INITIATION_STATIC: Final = "11"
POINT_OF_INITIATION_DYNAMIC: Final = "12"

#: A NAPAS bank identification number: six digits.
BANK_BIN_PATTERN: Final = re.compile(r"^[0-9]{6}$")
#: An account number (or the alias a bank issues in its place): 6 to 19 letters or digits.
ACCOUNT_NUMBER_PATTERN: Final = re.compile(r"^[A-Za-z0-9]{6,19}$")
#: What this shop ever puts in the memo: upper-case letters and digits, 1 to 25 of them. Banks strip
#: or mangle anything else (accents, punctuation), and a code that arrives changed finds nothing.
TRANSFER_CODE_PATTERN: Final = re.compile(r"^[A-Z0-9]{1,25}$")
#: The account holder's name as banks print it: upper-case ASCII, no accents.
ACCOUNT_NAME_PATTERN: Final = re.compile(r"^[A-Z0-9](?:[A-Z0-9 .,&()/'-]{0,48}[A-Z0-9.)])?$")

TRANSFER_CODE_PREFIX: Final = "NTL"
ACCOUNT_MONTH_PREFIX: Final = "NTLCN"
#: The highest ticket number the ticket form holds (three digits).
TICKET_FORM_MAX: Final = 999

_TICKET_FORM = re.compile(r"^NTL(?P<dd>[0-9]{2})(?P<mm>[0-9]{2})(?P<ticket>[0-9]{3})$")
_ID_FORM = re.compile(r"^NTL(?P<prefix>[0-9A-F]{8})$")
_ACCOUNT_MONTH_FORM = re.compile(r"^NTLCN(?P<prefix>[0-9A-F]{6})(?P<mm>[0-9]{2})(?P<yy>[0-9]{2})$")


class VietQrError(ValueError):
    """An input the payload refuses: a BIN, account, amount or code the standard cannot carry."""


# --- CRC-16/CCITT-FALSE -------------------------------------------------------------------------


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: polynomial ``0x1021``, initial ``0xFFFF``, no reflection, no final XOR.

    The check value for ``b"123456789"`` is ``0x29B1``.
    """

    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def crc_hex(text: str) -> str:
    """The checksum as the payload carries it: four upper-case hex digits over the ASCII text."""

    return f"{crc16_ccitt_false(text.encode('ascii')):04X}"


# --- the payload --------------------------------------------------------------------------------


def _field(tag: str, value: str) -> str:
    if len(value) > 99:
        raise VietQrError(f"field {tag} is longer than 99 characters")
    return f"{tag}{len(value):02d}{value}"


def _require_account(bank_bin: str, account_number: str) -> None:
    if not isinstance(bank_bin, str) or not BANK_BIN_PATTERN.fullmatch(bank_bin):
        raise VietQrError("a bank BIN is six digits")
    if not isinstance(account_number, str) or not ACCOUNT_NUMBER_PATTERN.fullmatch(account_number):
        raise VietQrError("an account number is 6 to 19 letters or digits")


def _require_amount(amount_vnd: object) -> int:
    if (
        not isinstance(amount_vnd, int)
        or isinstance(amount_vnd, bool)
        or not 1 <= amount_vnd <= MAX_SETTLEMENT_VND
    ):
        raise VietQrError("the amount is a whole number of đồng above zero")
    return amount_vnd


def _account_template(bank_bin: str, account_number: str) -> str:
    beneficiary = _field("00", bank_bin) + _field("01", account_number)
    return _field("00", NAPAS_GUID) + _field("01", beneficiary) + _field("02", SERVICE_TO_ACCOUNT)


def _with_crc(body: str) -> str:
    unsigned = body + "6304"
    return unsigned + crc_hex(unsigned)


def napas_payload(
    *, bank_bin: str, account_number: str, amount_vnd: int | None, purpose: str | None
) -> str:
    """The NAPAS VietQR payload in its general form, as the standard's own examples use it.

    ``amount_vnd`` and ``purpose`` are both present (the dynamic QR) or both absent (the static
    account QR). ``purpose`` here is any printable ASCII of 1 to 25 characters -- the standard's
    examples say ``Chuyen tien`` -- which is what lets the reference vectors be reproduced. The
    shop's own QR is `vietqr_payload`, which accepts only a transfer code.
    """

    _require_account(bank_bin, account_number)
    if (amount_vnd is None) != (purpose is None):
        raise VietQrError("an amount and a memo come together, or neither does")
    parts = [_field("00", "01")]
    if amount_vnd is None or purpose is None:
        parts.append(_field("01", POINT_OF_INITIATION_STATIC))
        parts.append(_field("38", _account_template(bank_bin, account_number)))
        parts.append(_field("53", CURRENCY_VND))
        parts.append(_field("58", COUNTRY_VN))
        return _with_crc("".join(parts))
    amount = _require_amount(amount_vnd)
    if (
        not isinstance(purpose, str)
        or not 1 <= len(purpose) <= 25
        or not all(0x20 <= ord(character) <= 0x7E for character in purpose)
    ):
        raise VietQrError("a memo is 1 to 25 printable ASCII characters")
    parts.append(_field("01", POINT_OF_INITIATION_DYNAMIC))
    parts.append(_field("38", _account_template(bank_bin, account_number)))
    parts.append(_field("53", CURRENCY_VND))
    parts.append(_field("54", str(amount)))
    parts.append(_field("58", COUNTRY_VN))
    parts.append(_field("62", _field("08", purpose)))
    return _with_crc("".join(parts))


def vietqr_payload(
    *, bank_bin: str, account_number: str, amount_vnd: int, transfer_code: str
) -> str:
    """The shop's QR: the published account, the amount still owed, and the order's transfer code.

    Refuses (`VietQrError`) a BIN that is not six digits, an account that is not 6 to 19 letters or
    digits, an amount that is not a whole number of đồng above zero, and a code outside
    ``^[A-Z0-9]{1,25}$``.
    """

    if not isinstance(transfer_code, str) or not TRANSFER_CODE_PATTERN.fullmatch(transfer_code):
        raise VietQrError("a transfer code is 1 to 25 upper-case letters or digits")
    return napas_payload(
        bank_bin=bank_bin,
        account_number=account_number,
        amount_vnd=_require_amount(amount_vnd),
        purpose=transfer_code,
    )


def static_vietqr_payload(*, bank_bin: str, account_number: str) -> str:
    """The account alone, no amount and no memo (point of initiation ``11``)."""

    return napas_payload(
        bank_bin=bank_bin, account_number=account_number, amount_vnd=None, purpose=None
    )


# --- the parser ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedVietQr:
    """What a VietQR payload says, read back field by field."""

    dynamic: bool
    bank_bin: str
    account_number: str
    amount_vnd: int | None
    purpose: str | None
    crc: str


def _fields(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    position = 0
    while position < len(text):
        if position + 4 > len(text):
            raise VietQrError("a field is cut short")
        tag = text[position : position + 2]
        size = text[position + 2 : position + 4]
        if not tag.isdigit() or not size.isdigit():
            raise VietQrError("a field id and length are digits")
        end = position + 4 + int(size)
        if end > len(text):
            raise VietQrError(f"field {tag} runs past the end")
        if tag in found:
            raise VietQrError(f"field {tag} appears twice")
        found[tag] = text[position + 4 : end]
        position = end
    return found


def parse_payload(text: str) -> ParsedVietQr:
    """Read a VietQR payload back, checking its CRC; `VietQrError` for anything malformed.

    The inverse of `napas_payload`: every payload it builds parses to the values it was built from.
    """

    if not isinstance(text, str) or not text.isascii() or len(text) < 8:
        raise VietQrError("a payload is ASCII text")
    if text[-8:-4] != "6304":
        raise VietQrError("a payload ends with its CRC field")
    if crc_hex(text[:-4]) != text[-4:]:
        raise VietQrError("the CRC does not match")
    fields = _fields(text[:-8])
    if fields.get("00") != "01":
        raise VietQrError("payload format indicator must be 01")
    initiation = fields.get("01")
    if initiation not in {POINT_OF_INITIATION_STATIC, POINT_OF_INITIATION_DYNAMIC}:
        raise VietQrError("point of initiation must be 11 or 12")
    template = _fields(fields.get("38", ""))
    if template.get("00") != NAPAS_GUID or template.get("02") != SERVICE_TO_ACCOUNT:
        raise VietQrError("not a NAPAS transfer to an account")
    beneficiary = _fields(template.get("01", ""))
    bank_bin, account = beneficiary.get("00", ""), beneficiary.get("01", "")
    _require_account(bank_bin, account)
    if fields.get("53") != CURRENCY_VND or fields.get("58") != COUNTRY_VN:
        raise VietQrError("currency must be 704 and country VN")
    amount_text = fields.get("54")
    amount: int | None = None
    if amount_text is not None:
        if not amount_text.isdigit() or amount_text.startswith("0"):
            raise VietQrError("the amount is digits only")
        amount = _require_amount(int(amount_text))
    purpose = _fields(fields["62"]).get("08") if "62" in fields else None
    dynamic = initiation == POINT_OF_INITIATION_DYNAMIC
    if dynamic != (amount is not None):
        raise VietQrError("a dynamic QR carries an amount and a static one does not")
    return ParsedVietQr(
        dynamic=dynamic,
        bank_bin=bank_bin,
        account_number=account,
        amount_vnd=amount,
        purpose=purpose,
        crc=text[-4:],
    )


# --- the transfer code --------------------------------------------------------------------------


def order_transfer_code(
    *, order_id: UUID, ticket_number: int | None, ticket_issued_on: date | None
) -> str:
    """``NTL`` + ``ddmm`` + three-digit ticket, or ``NTL`` + the order id's first 8 hex digits.

    Ticket 12 issued on 28/09 is ``NTL2809012``; an order with no ticket (or ticket 1000 and
    above) is ``NTL`` + the first 8 hex digits of its id, upper-cased.
    """

    if (
        isinstance(ticket_number, int)
        and not isinstance(ticket_number, bool)
        and 1 <= ticket_number <= TICKET_FORM_MAX
        and isinstance(ticket_issued_on, date)
    ):
        return (
            f"{TRANSFER_CODE_PREFIX}{ticket_issued_on.day:02d}{ticket_issued_on.month:02d}"
            f"{ticket_number:03d}"
        )
    return f"{TRANSFER_CODE_PREFIX}{order_id.hex[:8].upper()}"


def account_month_transfer_code(*, account_id: UUID, month: date) -> str:
    """``NTLCN`` + the account id's first 6 hex digits + ``mmyy`` of the statement month."""

    return (
        f"{ACCOUNT_MONTH_PREFIX}{account_id.hex[:6].upper()}{month.month:02d}{month.year % 100:02d}"
    )


@dataclass(frozen=True, slots=True)
class TicketTransferCode:
    """``NTLddmmNNN``: ticket ``ticket_number`` issued on ``day``/``month`` of any year."""

    day: int
    month: int
    ticket_number: int


@dataclass(frozen=True, slots=True)
class OrderIdTransferCode:
    """``NTL`` + 8 hex digits: the order whose id starts with ``prefix`` (lower-case, as stored)."""

    prefix: str


@dataclass(frozen=True, slots=True)
class AccountMonthTransferCode:
    """``NTLCN`` + 6 hex + ``mmyy``: an account customer's month, not an order."""

    account_prefix: str
    month: int
    year_two_digits: int


TransferCode = TicketTransferCode | OrderIdTransferCode | AccountMonthTransferCode


def normalise_transfer_code(text: str) -> str:
    """As staff type it or a bank app shows it: spaces removed, upper-cased."""

    return "".join(str(text).split()).upper()


def parse_transfer_code(text: str) -> TransferCode | None:
    """Which order (or account month) a transfer code names, or `None` when it names nothing.

    Case-insensitive and blind to spaces. A ticket code with a day or month that no calendar has
    (``NTL3213…``) or ticket ``000`` names nothing.
    """

    code = normalise_transfer_code(text)
    ticket = _TICKET_FORM.fullmatch(code)
    if ticket is not None:
        day, month, number = int(ticket["dd"]), int(ticket["mm"]), int(ticket["ticket"])
        if not 1 <= month <= 12 or not 1 <= day <= _days_in_month(month) or number < 1:
            return None
        return TicketTransferCode(day=day, month=month, ticket_number=number)
    by_id = _ID_FORM.fullmatch(code)
    if by_id is not None:
        return OrderIdTransferCode(prefix=by_id["prefix"].lower())
    account = _ACCOUNT_MONTH_FORM.fullmatch(code)
    if account is not None and 1 <= int(account["mm"]) <= 12:
        return AccountMonthTransferCode(
            account_prefix=account["prefix"].lower(),
            month=int(account["mm"]),
            year_two_digits=int(account["yy"]),
        )
    return None


def _days_in_month(month: int) -> int:
    # 29 for February: a leap-day ticket exists in some years, and the search says no match
    # rather than refusing the code in the others.
    return 29 if month == 2 else 30 if month in {4, 6, 9, 11} else 31


# --- when an order has a QR ---------------------------------------------------------------------


class QrRefusal(StrEnum):
    """Why there is no QR for an order or an account month, by name."""

    #: The owner has not published the shop's bank account (`scripts/publish_bank_account.py`).
    BANK_ACCOUNT_UNPUBLISHED = "BANK_ACCOUNT_UNPUBLISHED"
    #: Nothing is owed: paid, refunded, on the customer's account, or a bill a credit covered.
    NOTHING_OWED = "NOTHING_OWED"
    #: Only a running order takes money at the counter (`payments.evaluate_payment`).
    ORDER_NOT_ACTIVE = "ORDER_NOT_ACTIVE"
    #: The quote presents no single total, so there is no amount to put in a QR.
    NO_PRESENTABLE_TOTAL = "NO_PRESENTABLE_TOTAL"
    #: An account month that has not begun has no statement to pay yet.
    MONTH_NOT_STARTED = "MONTH_NOT_STARTED"
    #: COUNTER-UI-RACE-009 (C3): a part payment typed at the counter ("đặt cọc") that is more than
    #: is still owed. The payment route refuses that amount (`OVERPAYMENT_REFUSED`), so no QR may
    #: ask the customer to send it.
    AMOUNT_ABOVE_REMAINING = "AMOUNT_ABOVE_REMAINING"
    #: A part amount that is not a whole number of đồng from 1 to the settlement ceiling.
    AMOUNT_INVALID = "AMOUNT_INVALID"


#: The balances on which money is still owed at the counter (`payments.PAYABLE_BALANCES`).
_OWING_BALANCES: Final = frozenset({OrderBalanceStatus.UNPAID, OrderBalanceStatus.PARTIALLY_PAID})


def order_qr_amount(
    *,
    commercial: CommercialOrderStatus,
    balance: OrderBalanceStatus,
    remaining_vnd: int | None,
    part_vnd: int | None = None,
) -> int | QrRefusal:
    """The amount an order's QR asks for -- the ledger's remaining balance -- or why there is none.

    Exactly the payments the counter's payment route would record: a QR for an amount that route
    would refuse would send the customer's money somewhere the shop cannot book it.
    ``remaining_vnd`` is `payments.payment_position`'s, read in the same request.

    ``part_vnd`` is a part payment the counter typed ("Khách trả một phần (đặt cọc)", `DEC-035`):
    the QR then asks for exactly that, provided the payment route would accept it -- a whole number
    of đồng from 1 up to what remains. Anything else is refused by name, never clamped: a QR for
    an amount other than the one typed is how a customer pays 200.000 ₫ against a 50.000 ₫ deposit.
    The order's own refusals come first, as for the full amount.
    """

    if balance not in _OWING_BALANCES:
        return QrRefusal.NOTHING_OWED
    if commercial is not CommercialOrderStatus.ACTIVE:
        return QrRefusal.ORDER_NOT_ACTIVE
    if remaining_vnd is None:
        return QrRefusal.NO_PRESENTABLE_TOTAL
    if remaining_vnd == 0:
        return QrRefusal.NOTHING_OWED
    if part_vnd is None:
        return _require_amount(remaining_vnd)
    if (
        not isinstance(part_vnd, int)
        or isinstance(part_vnd, bool)
        or not 1 <= part_vnd <= MAX_SETTLEMENT_VND
    ):
        return QrRefusal.AMOUNT_INVALID
    if part_vnd > remaining_vnd:
        return QrRefusal.AMOUNT_ABOVE_REMAINING
    return part_vnd


def account_month_qr_amount(unpaid_vnd: int) -> int | QrRefusal:
    """The account month's QR asks for what of that statement is still unpaid, or nothing."""

    if isinstance(unpaid_vnd, bool) or not isinstance(unpaid_vnd, int) or unpaid_vnd < 0:
        raise ValueError("an unpaid figure is a non-negative whole number of đồng")
    if unpaid_vnd == 0:
        return QrRefusal.NOTHING_OWED
    return _require_amount(unpaid_vnd)


# --- the published account ----------------------------------------------------------------------


class BankAccountError(ValueError):
    """The document is not a bank account this code will print a QR for."""


@dataclass(frozen=True, slots=True)
class BankTransferAccount:
    """The owner's published account: where every QR sends the customer's money."""

    bank_bin: str
    account_number: str
    #: As the bank prints it -- upper-case ASCII -- so the customer sees the same name in their app.
    account_name: str
    #: The bank's short name for the counter ("Vietcombank").
    bank_display_name: str
    #: When the owner confirmed the 1.000 ₫ test transfer arrived (ISO 8601 with an offset).
    test_transfer_confirmed_at: str


_ACCOUNT_KEYS: Final = frozenset(
    {
        "policy_version",
        "decision_ref",
        "bank_bin",
        "account_number",
        "account_name",
        "bank_display_name",
        "test_transfer_confirmed_at",
    }
)
_WITHDRAWAL_KEYS: Final = frozenset({"policy_version", "decision_ref", "withdrawn"})
_CONFIRMED_AT: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$"
)


def bank_account_document(
    *,
    bank_bin: str,
    account_number: str,
    account_name: str,
    bank_display_name: str,
    test_transfer_confirmed_at: str,
) -> dict[str, str]:
    """The document `scripts/publish_bank_account.py` publishes. Validated before it is returned."""

    document = {
        "policy_version": BANK_TRANSFER_ACCOUNT_VERSION,
        "decision_ref": BANK_TRANSFER_DECISION_REF,
        "bank_bin": bank_bin,
        "account_number": account_number,
        "account_name": account_name,
        "bank_display_name": bank_display_name,
        "test_transfer_confirmed_at": test_transfer_confirmed_at,
    }
    parse_bank_account(document)
    return document


def parse_bank_account(payload: object) -> BankTransferAccount:
    """The published document as an account, or `BankAccountError`. Exact keys, no defaults."""

    if not isinstance(payload, dict) or set(payload) != _ACCOUNT_KEYS:
        raise BankAccountError("a bank account document names exactly its seven fields")
    if payload.get("policy_version") != BANK_TRANSFER_ACCOUNT_VERSION:
        raise BankAccountError(f"policy_version must be {BANK_TRANSFER_ACCOUNT_VERSION}")
    if payload.get("decision_ref") != BANK_TRANSFER_DECISION_REF:
        raise BankAccountError(f"decision_ref must be {BANK_TRANSFER_DECISION_REF}")
    bank_bin, account = payload.get("bank_bin"), payload.get("account_number")
    try:
        _require_account(str(bank_bin) if isinstance(bank_bin, str) else "", str(account or ""))
    except VietQrError as error:
        raise BankAccountError(str(error)) from error
    name = payload.get("account_name")
    if not isinstance(name, str) or not ACCOUNT_NAME_PATTERN.fullmatch(name):
        raise BankAccountError("the account name is upper-case ASCII, as the bank prints it")
    display = payload.get("bank_display_name")
    if (
        not isinstance(display, str)
        or display != display.strip()
        or not 1 <= len(display) <= 40
        or any(not character.isprintable() for character in display)
    ):
        raise BankAccountError("the bank's display name is 1 to 40 printable characters")
    confirmed = payload.get("test_transfer_confirmed_at")
    if not isinstance(confirmed, str) or not _CONFIRMED_AT.fullmatch(confirmed):
        raise BankAccountError(
            "test_transfer_confirmed_at is when the owner saw the 1.000 ₫ test arrive (ISO 8601)"
        )
    return BankTransferAccount(
        bank_bin=str(bank_bin),
        account_number=str(account),
        account_name=name,
        bank_display_name=display,
        test_transfer_confirmed_at=confirmed,
    )


def validate_bank_account_document(payload: object) -> None:
    """The typed validator: a full account, or the owner's withdrawal of it (`DEC-041` reversal)."""

    if isinstance(payload, dict) and payload.get("withdrawn") is True:
        if (
            set(payload) != _WITHDRAWAL_KEYS
            or payload.get("policy_version") != BANK_TRANSFER_ACCOUNT_VERSION
            or payload.get("decision_ref") != BANK_TRANSFER_DECISION_REF
        ):
            raise BankAccountError("a withdrawal names only the document version and DEC-041")
        return
    parse_bank_account(payload)


def bank_account_withdrawal_document() -> dict[str, object]:
    """Once in force, no QR is shown, as before publication."""

    return {
        "policy_version": BANK_TRANSFER_ACCOUNT_VERSION,
        "decision_ref": BANK_TRANSFER_DECISION_REF,
        "withdrawn": True,
    }


def same_account(left: BankTransferAccount, right: BankTransferAccount) -> bool:
    """Whether two documents send money to the same place under the same printed names -- the
    confirmation instant aside, so publishing the account already in force again changes nothing."""

    return (
        left.bank_bin,
        left.account_number,
        left.account_name,
        left.bank_display_name,
    ) == (right.bank_bin, right.account_number, right.account_name, right.bank_display_name)


#: The test QR the owner scans and pays from their own phone before publishing (`DEC-041`).
PREVIEW_AMOUNT_VND: Final = 1_000
PREVIEW_TRANSFER_CODE: Final = "NTLTEST"


__all__ = [
    "ACCOUNT_MONTH_PREFIX",
    "BANK_TRANSFER_ACCOUNT_CONFIG_TYPE",
    "BANK_TRANSFER_ACCOUNT_VERSION",
    "BANK_TRANSFER_DECISION_REF",
    "PREVIEW_AMOUNT_VND",
    "PREVIEW_TRANSFER_CODE",
    "TRANSFER_CODE_PATTERN",
    "AccountMonthTransferCode",
    "BankAccountError",
    "BankTransferAccount",
    "OrderIdTransferCode",
    "ParsedVietQr",
    "QrRefusal",
    "TicketTransferCode",
    "TransferCode",
    "VietQrError",
    "account_month_qr_amount",
    "account_month_transfer_code",
    "bank_account_document",
    "bank_account_withdrawal_document",
    "crc16_ccitt_false",
    "crc_hex",
    "napas_payload",
    "normalise_transfer_code",
    "order_qr_amount",
    "order_transfer_code",
    "parse_bank_account",
    "parse_payload",
    "parse_transfer_code",
    "same_account",
    "static_vietqr_payload",
    "validate_bank_account_document",
    "vietqr_payload",
]
