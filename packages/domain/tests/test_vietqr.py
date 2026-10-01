"""`VIETQR-001` (`DEC-041`): the NAPAS VietQR payload, its CRC, the parser and the transfer code."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.catalog import CommercialOrderStatus, OrderBalanceStatus
from nha_trang_laundry_domain.settlement import MAX_SETTLEMENT_VND
from nha_trang_laundry_domain.vietqr import (
    AccountMonthTransferCode,
    BankAccountError,
    OrderIdTransferCode,
    QrRefusal,
    TicketTransferCode,
    VietQrError,
    account_month_qr_amount,
    account_month_transfer_code,
    bank_account_document,
    bank_account_withdrawal_document,
    crc16_ccitt_false,
    crc_hex,
    napas_payload,
    order_qr_amount,
    order_transfer_code,
    parse_bank_account,
    parse_payload,
    parse_transfer_code,
    same_account,
    static_vietqr_payload,
    validate_bank_account_document,
    vietqr_payload,
)

# The spec's golden vectors: real VietQR strings (BIN 970416, account 257678859).
STATIC = (
    "00020101021138530010A0000007270123000697041601092576788590208QRIBFTTA53037045802VN6304AE9F"
)
DYNAMIC_10000 = (
    "00020101021238530010A0000007270123000697041601092576788590208QRIBFTTA53037045405100005802VN"
    "62150811Chuyen tien630453E6"
)
DYNAMIC_1000 = (
    "00020101021238530010A0000007270123000697041601092576788590208QRIBFTTA5303704540410005802VN"
    "62150811Chuyen tien6304BBB8"
)
BIN, ACCOUNT = "970416", "257678859"
ORDER = UUID("a1b2c3d4-0000-4000-8000-000000000001")


# --- CRC and the golden vectors ------------------------------------------------------------------


def test_crc16_ccitt_false_check_value() -> None:
    assert crc16_ccitt_false(b"123456789") == 0x29B1
    assert crc_hex("123456789") == "29B1"


def test_the_static_golden_vector_reproduces_byte_for_byte() -> None:
    assert static_vietqr_payload(bank_bin=BIN, account_number=ACCOUNT) == STATIC


@pytest.mark.parametrize(("amount", "expected"), [(10_000, DYNAMIC_10000), (1_000, DYNAMIC_1000)])
def test_the_dynamic_golden_vectors_reproduce_byte_for_byte(amount: int, expected: str) -> None:
    built = napas_payload(
        bank_bin=BIN, account_number=ACCOUNT, amount_vnd=amount, purpose="Chuyen tien"
    )
    assert built == expected


@pytest.mark.parametrize("vector", [STATIC, DYNAMIC_10000, DYNAMIC_1000])
def test_the_golden_vectors_parse_and_their_crc_checks(vector: str) -> None:
    parsed = parse_payload(vector)
    assert (parsed.bank_bin, parsed.account_number) == (BIN, ACCOUNT)
    assert parsed.crc == vector[-4:]
    assert crc_hex(vector[:-4]) == vector[-4:]


def test_the_shop_payload_puts_the_elements_in_the_spec_order() -> None:
    payload = vietqr_payload(
        bank_bin=BIN, account_number=ACCOUNT, amount_vnd=147_500, transfer_code="NTL2809012"
    )
    assert payload == (
        "000201"
        "010212"
        "38530010A0000007270123000697041601092576788590208QRIBFTTA"
        "5303704"
        "5406147500"
        "5802VN"
        "62140810NTL2809012"
        "6304" + crc_hex(payload[:-4])
    )
    parsed = parse_payload(payload)
    assert parsed.dynamic is True
    assert parsed.amount_vnd == 147_500
    assert parsed.purpose == "NTL2809012"


# --- round trip ----------------------------------------------------------------------------------


@given(
    bank_bin=st.from_regex(r"\A[0-9]{6}\Z"),
    account=st.from_regex(r"\A[A-Za-z0-9]{6,19}\Z"),
    amount=st.integers(min_value=1, max_value=10**12),
    code=st.from_regex(r"\A[A-Z0-9]{1,25}\Z"),
)
def test_every_payload_parses_back_to_what_built_it(
    bank_bin: str, account: str, amount: int, code: str
) -> None:
    payload = vietqr_payload(
        bank_bin=bank_bin, account_number=account, amount_vnd=amount, transfer_code=code
    )
    parsed = parse_payload(payload)
    assert (parsed.bank_bin, parsed.account_number, parsed.amount_vnd, parsed.purpose) == (
        bank_bin,
        account,
        amount,
        code,
    )
    assert parsed.dynamic is True


def test_a_changed_character_fails_the_crc() -> None:
    tampered = DYNAMIC_10000.replace("10000", "90000")
    with pytest.raises(VietQrError, match="CRC"):
        parse_payload(tampered)


# --- refusals ------------------------------------------------------------------------------------


@pytest.mark.parametrize("bank_bin", ["97041", "9704166", "97041A", "", " 970416"])
def test_a_bin_that_is_not_six_digits_is_refused(bank_bin: str) -> None:
    with pytest.raises(VietQrError, match="BIN"):
        vietqr_payload(bank_bin=bank_bin, account_number=ACCOUNT, amount_vnd=1, transfer_code="A")


@pytest.mark.parametrize("account", ["12345", "12345678901234567890", "1234-5678", "12 345678", ""])
def test_an_account_that_is_not_6_to_19_alphanumerics_is_refused(account: str) -> None:
    with pytest.raises(VietQrError, match="account"):
        vietqr_payload(bank_bin=BIN, account_number=account, amount_vnd=1, transfer_code="A")


@pytest.mark.parametrize("amount", [0, -1, 1.0, 1000.5, True, "1000", None])
def test_an_amount_at_or_below_zero_or_not_an_integer_is_refused(amount: object) -> None:
    with pytest.raises(VietQrError, match="amount"):
        vietqr_payload(
            bank_bin=BIN,
            account_number=ACCOUNT,
            amount_vnd=amount,  # type: ignore[arg-type]
            transfer_code="NTL2809012",
        )


@pytest.mark.parametrize(
    "code", ["", "ntl2809012", "NTL 2809012", "NTL-2809012", "Chuyen tien", "A" * 26, "NTLĐ"]
)
def test_a_code_outside_the_pattern_is_refused(code: str) -> None:
    with pytest.raises(VietQrError, match="transfer code"):
        vietqr_payload(bank_bin=BIN, account_number=ACCOUNT, amount_vnd=1, transfer_code=code)


def test_an_amount_without_a_memo_is_refused() -> None:
    with pytest.raises(VietQrError):
        napas_payload(bank_bin=BIN, account_number=ACCOUNT, amount_vnd=1000, purpose=None)


# --- the transfer code ---------------------------------------------------------------------------


def test_a_ticket_order_is_named_by_its_day_and_three_digit_number() -> None:
    code = order_transfer_code(order_id=ORDER, ticket_number=12, ticket_issued_on=date(2026, 9, 28))
    assert code == "NTL2809012"
    assert order_transfer_code(
        order_id=ORDER, ticket_number=7, ticket_issued_on=date(2026, 1, 3)
    ) == ("NTL0301007")
    assert order_transfer_code(
        order_id=ORDER, ticket_number=999, ticket_issued_on=date(2026, 12, 31)
    ) == ("NTL3112999")


def test_an_order_without_a_ticket_is_named_by_its_id() -> None:
    assert (
        order_transfer_code(order_id=ORDER, ticket_number=None, ticket_issued_on=None)
        == "NTLA1B2C3D4"
    )


def test_a_ticket_three_digits_cannot_hold_falls_back_to_the_id() -> None:
    assert (
        order_transfer_code(order_id=ORDER, ticket_number=1000, ticket_issued_on=date(2026, 9, 28))
        == "NTLA1B2C3D4"
    )


def test_an_account_month_is_named_by_account_prefix_and_mmyy() -> None:
    account = UUID("0f1e2d3c-4b5a-4000-8000-000000000009")
    assert account_month_transfer_code(account_id=account, month=date(2026, 9, 1)) == (
        "NTLCN0F1E2D0926"
    )


@given(
    ticket=st.integers(min_value=1, max_value=999),
    day=st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31)),
    order=st.uuids(),
)
def test_every_ticket_code_parses_back_to_its_day_and_number(
    ticket: int, day: date, order: UUID
) -> None:
    code = order_transfer_code(order_id=order, ticket_number=ticket, ticket_issued_on=day)
    assert parse_transfer_code(code) == TicketTransferCode(
        day=day.day, month=day.month, ticket_number=ticket
    )
    assert vietqr_payload(bank_bin=BIN, account_number=ACCOUNT, amount_vnd=1, transfer_code=code)


@given(order=st.uuids())
def test_every_id_code_parses_back_to_the_id_prefix(order: UUID) -> None:
    code = order_transfer_code(order_id=order, ticket_number=None, ticket_issued_on=None)
    assert parse_transfer_code(code) == OrderIdTransferCode(prefix=str(order)[:8])


def test_search_reads_a_code_whatever_the_case_and_spaces() -> None:
    assert parse_transfer_code(" ntl 2809 012 ") == TicketTransferCode(28, 9, 12)
    assert parse_transfer_code("ntla1b2c3d4") == OrderIdTransferCode("a1b2c3d4")
    assert parse_transfer_code("ntlcn0f1e2d0926") == AccountMonthTransferCode("0f1e2d", 9, 26)


@pytest.mark.parametrize(
    "text", ["", "NTL", "2809012", "NTL3213012", "NTL0013012", "NTL2809000", "NTL28090123X", "XYZ"]
)
def test_a_code_that_names_nothing_parses_to_none(text: str) -> None:
    assert parse_transfer_code(text) is None


# --- when an order has a QR ----------------------------------------------------------------------

ACTIVE = CommercialOrderStatus.ACTIVE


def test_the_qr_asks_for_the_ledgers_remaining_balance() -> None:
    assert (
        order_qr_amount(commercial=ACTIVE, balance=OrderBalanceStatus.UNPAID, remaining_vnd=147_500)
        == 147_500
    )
    assert (
        order_qr_amount(
            commercial=ACTIVE, balance=OrderBalanceStatus.PARTIALLY_PAID, remaining_vnd=97_500
        )
        == 97_500
    )


@pytest.mark.parametrize(
    "balance",
    [OrderBalanceStatus.PAID, OrderBalanceStatus.ON_ACCOUNT, OrderBalanceStatus.REFUNDED],
)
def test_a_paid_refunded_or_account_order_owes_nothing(balance: OrderBalanceStatus) -> None:
    assert (
        order_qr_amount(commercial=ACTIVE, balance=balance, remaining_vnd=0)
        is QrRefusal.NOTHING_OWED
    )


def test_a_bill_a_credit_covered_owes_nothing() -> None:
    assert (
        order_qr_amount(commercial=ACTIVE, balance=OrderBalanceStatus.UNPAID, remaining_vnd=0)
        is QrRefusal.NOTHING_OWED
    )


def test_an_order_that_is_not_running_has_no_qr() -> None:
    assert (
        order_qr_amount(
            commercial=CommercialOrderStatus.CANCELLED,
            balance=OrderBalanceStatus.UNPAID,
            remaining_vnd=50_000,
        )
        is QrRefusal.ORDER_NOT_ACTIVE
    )


def test_an_order_with_no_single_total_has_no_qr() -> None:
    assert (
        order_qr_amount(commercial=ACTIVE, balance=OrderBalanceStatus.UNPAID, remaining_vnd=None)
        is QrRefusal.NO_PRESENTABLE_TOTAL
    )


def test_an_account_month_asks_for_its_unpaid_figure() -> None:
    assert account_month_qr_amount(1_250_000) == 1_250_000
    assert account_month_qr_amount(0) is QrRefusal.NOTHING_OWED
    with pytest.raises(ValueError):
        account_month_qr_amount(-1)


# --- the published account -----------------------------------------------------------------------


def _document(**changes: str) -> dict[str, str]:
    fields = {
        "bank_bin": BIN,
        "account_number": ACCOUNT,
        "account_name": "NGUYEN VAN A",
        "bank_display_name": "VietinBank",
        "test_transfer_confirmed_at": "2026-09-28T09:15:00+07:00",
    }
    fields.update(changes)
    return bank_account_document(**fields)


def test_a_bank_account_document_parses() -> None:
    account = parse_bank_account(_document())
    assert (account.bank_bin, account.account_number, account.account_name) == (
        BIN,
        ACCOUNT,
        "NGUYEN VAN A",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"bank_bin": "97041"},
        {"account_number": "123"},
        {"account_name": "Nguyễn Văn A"},
        {"account_name": "nguyen van a"},
        {"account_name": ""},
        {"bank_display_name": ""},
        {"bank_display_name": " Vietcombank"},
        {"test_transfer_confirmed_at": ""},
        {"test_transfer_confirmed_at": "yesterday"},
        {"test_transfer_confirmed_at": "2026-09-28T09:15:00"},
    ],
)
def test_a_document_this_code_would_not_print_a_qr_for_is_refused(changes: dict[str, str]) -> None:
    with pytest.raises(BankAccountError):
        _document(**changes)


def test_an_extra_or_missing_key_is_refused() -> None:
    document: dict[str, object] = dict(_document())
    document["note"] = "x"
    with pytest.raises(BankAccountError):
        parse_bank_account(document)
    del document["note"]
    del document["bank_display_name"]
    with pytest.raises(BankAccountError):
        parse_bank_account(document)


def test_the_withdrawal_validates_and_is_not_an_account() -> None:
    withdrawal = bank_account_withdrawal_document()
    validate_bank_account_document(withdrawal)
    with pytest.raises(BankAccountError):
        parse_bank_account(withdrawal)
    with pytest.raises(BankAccountError):
        validate_bank_account_document({**withdrawal, "bank_bin": BIN})


def test_the_same_account_confirmed_again_is_the_same_account() -> None:
    first = parse_bank_account(_document())
    again = parse_bank_account(_document(test_transfer_confirmed_at="2026-10-01T08:00:00+07:00"))
    moved = parse_bank_account(_document(account_number="0123456789"))
    assert same_account(first, again)
    assert not same_account(first, moved)


# --- COUNTER-UI-RACE-009 (C3): a QR for a typed part payment ------------------------------------
#
# The counter types a deposit ("Khách trả một phần (đặt cọc)"); the QR must ask for exactly that
# amount or for nothing. The matrix: every part against every order state, and every part that
# the payment route would refuse.


@pytest.mark.parametrize(
    ("balance", "remaining", "part", "expected"),
    [
        # A part within what remains is asked for exactly -- never the whole balance.
        (OrderBalanceStatus.UNPAID, 200_000, 50_000, 50_000),
        (OrderBalanceStatus.UNPAID, 200_000, 1, 1),
        (OrderBalanceStatus.UNPAID, 200_000, 199_999, 199_999),
        (OrderBalanceStatus.PARTIALLY_PAID, 150_000, 50_000, 50_000),
        # All of what remains, typed by hand, is still that amount.
        (OrderBalanceStatus.UNPAID, 200_000, 200_000, 200_000),
        (OrderBalanceStatus.PARTIALLY_PAID, 150_000, 150_000, 150_000),
        # More than remains is refused by name, never clamped to the balance.
        (OrderBalanceStatus.UNPAID, 200_000, 200_001, QrRefusal.AMOUNT_ABOVE_REMAINING),
        (OrderBalanceStatus.PARTIALLY_PAID, 150_000, 200_000, QrRefusal.AMOUNT_ABOVE_REMAINING),
        # Not a whole number of đồng from 1 to the ceiling.
        (OrderBalanceStatus.UNPAID, 200_000, 0, QrRefusal.AMOUNT_INVALID),
        (OrderBalanceStatus.UNPAID, 200_000, -50_000, QrRefusal.AMOUNT_INVALID),
        (OrderBalanceStatus.UNPAID, 200_000, True, QrRefusal.AMOUNT_INVALID),
        (OrderBalanceStatus.UNPAID, 200_000, 50_000.0, QrRefusal.AMOUNT_INVALID),
        (OrderBalanceStatus.UNPAID, 200_000, 10_000_000_000, QrRefusal.AMOUNT_ABOVE_REMAINING),
        (OrderBalanceStatus.UNPAID, 200_000, MAX_SETTLEMENT_VND + 1, QrRefusal.AMOUNT_INVALID),
        # The order's own refusals come first, whatever the part.
        (OrderBalanceStatus.PAID, 0, 50_000, QrRefusal.NOTHING_OWED),
        (OrderBalanceStatus.ON_ACCOUNT, 0, 50_000, QrRefusal.NOTHING_OWED),
        (OrderBalanceStatus.REFUNDED, 0, 50_000, QrRefusal.NOTHING_OWED),
        (OrderBalanceStatus.UNPAID, 0, 50_000, QrRefusal.NOTHING_OWED),
        (OrderBalanceStatus.UNPAID, None, 50_000, QrRefusal.NO_PRESENTABLE_TOTAL),
    ],
)
def test_a_typed_part_is_asked_for_exactly_or_refused_by_name(
    balance: OrderBalanceStatus, remaining: int | None, part: Any, expected: int | QrRefusal
) -> None:
    got = order_qr_amount(
        commercial=ACTIVE, balance=balance, remaining_vnd=remaining, part_vnd=part
    )
    assert got == expected and type(got) is type(expected)


@pytest.mark.parametrize("part", [None, 50_000])
def test_a_part_on_an_order_that_is_not_running_has_no_qr(part: int | None) -> None:
    assert (
        order_qr_amount(
            commercial=CommercialOrderStatus.CANCELLED,
            balance=OrderBalanceStatus.UNPAID,
            remaining_vnd=200_000,
            part_vnd=part,
        )
        is QrRefusal.ORDER_NOT_ACTIVE
    )


def test_a_part_qr_payload_carries_the_typed_amount() -> None:
    amount = order_qr_amount(
        commercial=ACTIVE, balance=OrderBalanceStatus.UNPAID, remaining_vnd=200_000, part_vnd=50_000
    )
    assert amount == 50_000
    payload = vietqr_payload(
        bank_bin=BIN, account_number=ACCOUNT, amount_vnd=50_000, transfer_code="NTL2809012"
    )
    assert parse_payload(payload).amount_vnd == 50_000
