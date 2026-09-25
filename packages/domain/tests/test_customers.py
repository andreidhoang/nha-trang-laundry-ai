"""`CUSTOMER-001` (`DEC-034`): phone normalisation, search classification, retention, the notice."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nha_trang_laundry_domain.customers import (
    CUSTOMER_RETENTION_MONTHS,
    CustomerRefusal,
    CustomerRuleError,
    MarketingChange,
    PhoneKind,
    PrivacyNoticeError,
    QueryMode,
    classify_query,
    clean_display_name,
    clean_note,
    fold_text,
    marketing_change,
    normalize_phone,
    parse_privacy_notice,
    retention_cutoff,
    retention_due,
)

# --- phone normalisation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "e164", "national", "kind"),
    [
        ("0905123456", "+84905123456", "0905123456", PhoneKind.MOBILE),
        ("0905 123 456", "+84905123456", "0905123456", PhoneKind.MOBILE),
        ("0905.123.456", "+84905123456", "0905123456", PhoneKind.MOBILE),
        ("0905-123-456", "+84905123456", "0905123456", PhoneKind.MOBILE),
        ("+84905123456", "+84905123456", "0905123456", PhoneKind.MOBILE),
        ("+84 905 123 456", "+84905123456", "0905123456", PhoneKind.MOBILE),
        ("84905123456", "+84905123456", "0905123456", PhoneKind.MOBILE),
        ("  0382 318 492 ", "+84382318492", "0382318492", PhoneKind.MOBILE),
        ("0562123456", "+84562123456", "0562123456", PhoneKind.MOBILE),
        ("0701234567", "+84701234567", "0701234567", PhoneKind.MOBILE),
        ("0812345678", "+84812345678", "0812345678", PhoneKind.MOBILE),
        # Landlines keep their area code: Khánh Hòa 0258 + 7 digits, Hà Nội 024 + 8 digits.
        ("0258 3812 345", "+842583812345", "02583812345", PhoneKind.LANDLINE),
        ("024 3825 1234", "+842438251234", "02438251234", PhoneKind.LANDLINE),
        ("+84 258 3812345", "+842583812345", "02583812345", PhoneKind.LANDLINE),
    ],
)
def test_accepted_forms_normalise_to_one_number(
    raw: str, e164: str, national: str, kind: PhoneKind
) -> None:
    phone = normalize_phone(raw)
    assert (phone.e164, phone.national, phone.kind) == (e164, national, kind)
    assert phone.last4 == national[-4:]


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "090512345",  # nine digits
        "09051234567",  # eleven digits for a mobile prefix
        "01651234567",  # retired 11-digit 01x mobile
        "+84 0905 123 456",  # trunk zero kept after the country code
        "+1 415 555 0100",  # not Vietnamese
        "0605123456",  # 06x is not a mobile prefix
        "0105123456",
        "0258381234",  # landline one digit short
        "(0258) 3812345",  # parentheses are not a tolerated separator
        "0905/123/456",
        "0905 123 45a",
        "\uff11\uff12\uff13",  # full-width digits
        "0\uff1905123456",
        "1900 1234",
        "905123456",  # no prefix at all
    ],
)
def test_anything_else_is_refused_phone_invalid(raw: str) -> None:
    with pytest.raises(CustomerRuleError) as caught:
        normalize_phone(raw)
    assert caught.value.code is CustomerRefusal.PHONE_INVALID
    assert str(caught.value) == "PHONE_INVALID"


@given(
    first=st.sampled_from("35789"),
    rest=st.text(alphabet="0123456789", min_size=8, max_size=8),
    form=st.sampled_from(["0{s}", "+84{s}", "84{s}", "+84 {s}"]),
)
def test_every_written_form_of_a_mobile_is_the_same_number(
    first: str, rest: str, form: str
) -> None:
    subscriber = first + rest
    phone = normalize_phone(form.format(s=subscriber))
    assert phone.e164 == f"+84{subscriber}"
    assert normalize_phone(phone.national) == phone
    assert normalize_phone(phone.e164) == phone


# --- search classification ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "mode", "value"),
    [
        ("", QueryMode.EMPTY, ""),
        ("  ", QueryMode.EMPTY, ""),
        ("3456", QueryMode.LAST4, "3456"),
        (" 84 92 ", QueryMode.LAST4, "8492"),
        ("0905 123 456", QueryMode.PHONE, "+84905123456"),
        ("+84905123456", QueryMode.PHONE, "+84905123456"),
        ("09051", QueryMode.PHONE_INCOMPLETE, ""),
        ("090512345", QueryMode.PHONE_INCOMPLETE, ""),
        ("0605123456", QueryMode.PHONE_INVALID, ""),
        ("123", QueryMode.PHONE_INCOMPLETE, ""),
        ("Chị Lan", QueryMode.NAME, "chi lan"),
        ("  ĐỨC   Anh ", QueryMode.NAME, "duc anh"),
        ("L", QueryMode.TOO_SHORT, ""),
    ],
)
def test_a_query_asks_for_exactly_one_search(raw: str, mode: QueryMode, value: str) -> None:
    query = classify_query(raw)
    assert (query.mode, query.value) == (mode, value)


def test_digits_are_never_searched_as_a_name() -> None:
    for raw in ("12", "12345", "0905", "090512345678"):
        assert classify_query(raw).mode is not QueryMode.NAME


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Chị Lan", "chi lan"),
        ("CHỊ LAN", "chị lan"),
        ("Nguyễn Văn Đức", "nguyen van duc"),
        ("Homestay  Biển Xanh", "homestay bien xanh"),
        ("Ánh", "anh"),
    ],
)
def test_name_folding_is_case_and_diacritic_insensitive(left: str, right: str) -> None:
    assert fold_text(left) == fold_text(right)


def test_name_and_note_bounds() -> None:
    assert clean_display_name("  chị   Lan ") == "chị Lan"
    assert clean_display_name("   ") is None
    with pytest.raises(CustomerRuleError):
        clean_display_name("x" * 81)
    assert clean_note("x" * 200) == "x" * 200
    with pytest.raises(CustomerRuleError) as caught:
        clean_note("x" * 201)
    assert caught.value.code is CustomerRefusal.NOTE_TOO_LONG


def test_marketing_consent_changes_only_when_asked_to() -> None:
    assert marketing_change(active=False, requested=None) is None
    assert marketing_change(active=False, requested=False) is None
    assert marketing_change(active=True, requested=True) is None
    assert marketing_change(active=False, requested=True) is MarketingChange.GIVEN
    assert marketing_change(active=True, requested=False) is MarketingChange.WITHDRAWN


# --- retention ------------------------------------------------------------------------------------


def test_retention_is_24_calendar_months_and_due_at_the_exact_instant() -> None:
    as_of = datetime(2026, 9, 25, 3, 0, tzinfo=UTC)
    cutoff = retention_cutoff(as_of)
    assert cutoff == datetime(2024, 9, 25, 3, 0, tzinfo=UTC)
    assert retention_due(last_activity_at=cutoff, as_of=as_of)
    assert not retention_due(last_activity_at=cutoff + timedelta(microseconds=1), as_of=as_of)
    assert retention_due(last_activity_at=cutoff - timedelta(days=400), as_of=as_of)


@pytest.mark.parametrize(
    ("as_of", "cutoff"),
    [
        # A shorter month clamps to its last day rather than rolling into the next month.
        (datetime(2026, 3, 31, tzinfo=UTC), datetime(2024, 3, 31, tzinfo=UTC)),
        (datetime(2028, 2, 29, tzinfo=UTC), datetime(2026, 2, 28, tzinfo=UTC)),
        (datetime(2026, 2, 28, tzinfo=UTC), datetime(2024, 2, 28, tzinfo=UTC)),
        (datetime(2026, 1, 15, 12, tzinfo=UTC), datetime(2024, 1, 15, 12, tzinfo=UTC)),
    ],
)
def test_retention_cutoff_calendar_edges(as_of: datetime, cutoff: datetime) -> None:
    assert retention_cutoff(as_of) == cutoff


@given(st.datetimes(min_value=datetime(2027, 1, 1), max_value=datetime(2100, 1, 1)))
def test_the_cutoff_is_never_less_than_two_years_minus_three_days(naive: datetime) -> None:
    as_of = naive.replace(tzinfo=UTC)
    gap = as_of - retention_cutoff(as_of)
    assert timedelta(days=CUSTOMER_RETENTION_MONTHS * 30) < gap <= timedelta(days=732)


def test_retention_refuses_a_naive_instant() -> None:
    with pytest.raises(ValueError):
        retention_cutoff(datetime(2026, 9, 25))


# --- the notice -----------------------------------------------------------------------------------


def _notice(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": "customer-privacy-notice-v1",
        "decision_ref": "DEC-034",
        "notice_version": "V1",
        "legal_entity": "CÔNG TY TNHH A & T CARE",
        "hotline_e164": "+84382318492",
        "retention_months": 24,
        "consent_sentence_vi": "Tiệm xin lưu số điện thoại của anh/chị để báo khi đồ xong.",
        "service_consent_label_vi": "Khách đã nghe và đồng ý",
        "marketing_consent_label_vi": "Khách muốn nhận tin ưu đãi",
        "title_vi": "Thông tin khách hàng",
        "text_vi": (
            "CÔNG TY TNHH A & T CARE giữ số điện thoại của anh/chị. Gọi 0382 318 492 để xoá. "
            "Không có đơn trong 24 tháng thì tiệm tự xoá."
        ),
        "source": "docs/POLICY_CUSTOMER_PRIVACY_NOTICE_V1.md",
    }
    document.update(overrides)
    return document


def test_a_complete_notice_parses() -> None:
    notice = parse_privacy_notice(_notice())
    assert notice.retention_months == 24
    assert notice.hotline.national == "0382318492"


@pytest.mark.parametrize(
    "overrides",
    [
        {"decision_ref": "DEC-015"},
        {"retention_months": 36},
        {"retention_months": True},
        {"hotline_e164": "0382318492"},
        {"legal_entity": "CÔNG TY KHÁC"},
        {"text_vi": "CÔNG TY TNHH A & T CARE giữ thông tin trong 24 tháng."},
        {"text_vi": "CÔNG TY TNHH A & T CARE, 0382 318 492."},
        {"consent_sentence_vi": "  "},
        {"extra": "x"},
    ],
)
def test_an_incomplete_notice_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(PrivacyNoticeError):
        parse_privacy_notice(_notice(**overrides))
