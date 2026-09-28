"""`EINVOICE-REQUEST-001` (`DEC-040`): the pure rules of an invoice request.

The buyer's details as the counter types them, what the bookkeeper reads back, and how a request
closes. Nothing here names a tax, a rate or an amount.
"""

from __future__ import annotations

from datetime import date

import pytest
from nha_trang_laundry_domain.invoice_requests import (
    InvoiceCancelReason,
    InvoiceRefusal,
    InvoiceRequestStatus,
    InvoiceRuleError,
    clean_buyer,
    clean_cancel_note,
    clean_issued,
    clean_tax_code,
    request_code,
    require_open,
)

TODAY = date(2026, 9, 28)


def _buyer(**overrides: str | None) -> dict[str, str | None]:
    base: dict[str, str | None] = {
        "unit_name": "Công ty TNHH Biển Xanh",
        "tax_code": None,
        "address": None,
        "email": None,
        "name": None,
    }
    base.update(overrides)
    return base


def _refusal(**overrides: str | None) -> tuple[InvoiceRefusal, str | None]:
    with pytest.raises(InvoiceRuleError) as caught:
        clean_buyer(**_buyer(**overrides))
    return caught.value.code, caught.value.field


@pytest.mark.parametrize("code", ["4201234567", "4201234567-001", "001234567890", " 4201234567 "])
def test_the_three_tax_code_shapes(code: str) -> None:
    assert clean_tax_code(code) == code.strip()


@pytest.mark.parametrize(
    "code",
    ["420123456", "42012345678", "4201234567-01", "4201 234567", "42O1234567", "1234567890123"],
)
def test_anything_else_is_not_a_tax_code(code: str) -> None:
    with pytest.raises(InvoiceRuleError) as caught:
        clean_tax_code(code)
    assert caught.value.code is InvoiceRefusal.INVOICE_TAX_CODE_SHAPE


def test_a_blank_tax_code_is_none() -> None:
    assert clean_tax_code("   ") is None and clean_tax_code(None) is None


def test_the_buyer_is_trimmed_and_collapsed() -> None:
    buyer = clean_buyer(
        unit_name="  Công ty   TNHH  Biển Xanh ",
        tax_code="4201234567",
        address=" 12  Trần Phú ",
        email=" ketoan@bienxanh.vn ",
        name="  ",
    )
    assert buyer.unit_name == "Công ty TNHH Biển Xanh"
    assert buyer.address == "12 Trần Phú"
    assert buyer.email == "ketoan@bienxanh.vn"
    assert buyer.name is None


def test_the_unit_name_is_required_and_bounded() -> None:
    assert _refusal(unit_name=None) == (
        InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID,
        "buyer_unit_name",
    )
    assert _refusal(unit_name="x" * 201)[0] is InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID
    assert clean_buyer(**_buyer(unit_name="x" * 200)).unit_name == "x" * 200


def test_a_tax_code_needs_an_address() -> None:
    assert _refusal(tax_code="4201234567") == (
        InvoiceRefusal.INVOICE_ADDRESS_REQUIRED,
        "buyer_address",
    )
    assert _refusal(tax_code="4201234567", address="x" * 301)[0] is (
        InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID
    )


@pytest.mark.parametrize("email", ["khong-phai-email", "a@b", "a b@c.vn", "@c.vn", "-a@c.vn"])
def test_an_email_has_a_simple_shape(email: str) -> None:
    assert _refusal(email=email) == (InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID, "buyer_email")


@pytest.mark.parametrize("text", ["=1+1", "+cmd", "-2", "@SUM(A1)"])
def test_nothing_a_spreadsheet_would_execute(text: str) -> None:
    assert _refusal(unit_name=text)[0] is InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID
    assert _refusal(name=text)[0] is InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID


@pytest.mark.parametrize(
    ("field", "text"),
    [
        ("unit_name", "Công ty 0905123456"),
        ("address", "12 Trần Phú, ĐT 0905 123 456"),
        ("name", "anh An +84905123456"),
        ("email", "0905123456@gmail.com"),
    ],
)
def test_no_buyer_field_holds_a_phone_number(field: str, text: str) -> None:
    code, where = _refusal(**{field: text})
    assert code is InvoiceRefusal.INVOICE_FIELD_LOOKS_LIKE_PHONE
    assert where == f"buyer_{field}"


def test_a_control_character_is_refused() -> None:
    assert _refusal(name="An\x00")[0] is InvoiceRefusal.INVOICE_BUYER_FIELD_INVALID


def test_the_issued_details_are_read_back_as_printed() -> None:
    issued = clean_issued(symbol=" 1c26tyy ", number="00000123", issued_on=TODAY, today=TODAY)
    assert (issued.symbol, issued.number, issued.issued_on) == ("1C26TYY", "00000123", TODAY)


@pytest.mark.parametrize(
    ("symbol", "number", "issued_on", "field"),
    [
        ("", "1", TODAY, "invoice_symbol"),
        ("1C26-TYY", "1", TODAY, "invoice_symbol"),
        ("ABCDEFGHIJKLM", "1", TODAY, "invoice_symbol"),
        ("1C26TYY", "", TODAY, "invoice_number"),
        ("1C26TYY", "123456789", TODAY, "invoice_number"),
        ("1C26TYY", "12a", TODAY, "invoice_number"),
        ("1C26TYY", "1", None, "invoice_date"),
        ("1C26TYY", "1", date(2026, 9, 29), "invoice_date"),
    ],
)
def test_issued_details_that_are_not_ones(
    symbol: str, number: str, issued_on: date | None, field: str
) -> None:
    with pytest.raises(InvoiceRuleError) as caught:
        clean_issued(symbol=symbol, number=number, issued_on=issued_on, today=TODAY)
    assert caught.value.code is InvoiceRefusal.INVOICE_ISSUED_DETAILS_INVALID
    assert caught.value.field == field


def test_a_cancellation_for_other_says_what() -> None:
    with pytest.raises(InvoiceRuleError) as caught:
        clean_cancel_note(InvoiceCancelReason.OTHER, "  ")
    assert caught.value.code is InvoiceRefusal.INVOICE_CANCEL_NOTE_REQUIRED
    assert clean_cancel_note(InvoiceCancelReason.OTHER, " khách  đổi ý ") == "khách đổi ý"
    assert clean_cancel_note(InvoiceCancelReason.DUPLICATE, None) is None
    with pytest.raises(InvoiceRuleError) as caught:
        clean_cancel_note(InvoiceCancelReason.DUPLICATE, "gọi 0905123456")
    assert caught.value.code is InvoiceRefusal.INVOICE_FIELD_LOOKS_LIKE_PHONE


def test_only_an_open_request_closes() -> None:
    require_open(InvoiceRequestStatus.REQUESTED)
    for status in (InvoiceRequestStatus.ISSUED, InvoiceRequestStatus.CANCELLED):
        with pytest.raises(InvoiceRuleError) as caught:
            require_open(status)
        assert caught.value.code is InvoiceRefusal.INVOICE_REQUEST_CLOSED


def test_the_request_code() -> None:
    assert request_code(7) == "YC-0007"
    assert request_code(12345) == "YC-12345"
    for bad in (0, -1, True):
        with pytest.raises(ValueError):
            request_code(bad)
