"""`CUSTOMER-001`: the privacy notice the owner publishes is the drafted document, verbatim.

`scripts/publish_privacy_notice.py` builds the payload from
`docs/POLICY_CUSTOMER_PRIVACY_NOTICE_V1.md` and the owner-confirmed lines of
`BUSINESS_TRUTH_INTAKE.md`. These tests pin that the document says what `DEC-034` requires a
notice to say, and that a business fact the owner has not confirmed is never published.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nha_trang_laundry_domain.customers import (
    PrivacyNoticeError,
    notice_payload_from_documents,
    parse_privacy_notice,
)

ROOT = Path(__file__).resolve().parents[3]
NOTICE = ROOT / "docs" / "POLICY_CUSTOMER_PRIVACY_NOTICE_V1.md"
BUSINESS_TRUTH = ROOT / "BUSINESS_TRUTH_INTAKE.md"


def notice_payload() -> dict[str, object]:
    """The payload the publisher would publish today. Shared by the repository and API tests."""

    return notice_payload_from_documents(
        notice_markdown=NOTICE.read_text(encoding="utf-8"),
        business_truth=BUSINESS_TRUTH.read_text(encoding="utf-8"),
        source="docs/POLICY_CUSTOMER_PRIVACY_NOTICE_V1.md",
    )


def test_the_drafted_notice_says_who_what_why_how_long_and_how_to_delete() -> None:
    notice = parse_privacy_notice(notice_payload())
    assert notice.legal_entity == "CÔNG TY TNHH A & T CARE"
    assert notice.hotline.e164 == "+84382318492"
    text = notice.text_vi
    for fact in (
        "CÔNG TY TNHH A & T CARE",  # who keeps it
        "0382 318 492",  # how to reach them
        "Tiệm lưu gì",  # what is kept
        "Để làm gì",  # why
        "24 tháng",  # how long
        "yêu cầu xoá",  # how to ask for deletion
        "tiệm vẫn nhận đồ",  # declining costs the customer nothing
    ):
        assert fact in text, fact
    # The owner's notes are not part of what the customer is told.
    assert "Ghi chú cho chủ tiệm" not in text
    assert "Nghị định" not in text
    # One screen: short enough to read at the counter.
    assert len(text.split()) < 330


def test_the_consent_sentence_and_the_two_ticks_are_separate() -> None:
    notice = parse_privacy_notice(notice_payload())
    assert notice.consent_sentence_vi.endswith("không ạ?")
    assert "ưu đãi" not in notice.consent_sentence_vi
    assert "ưu đãi" in notice.marketing_consent_label_vi
    assert "không bắt buộc" in notice.marketing_consent_label_vi


def test_an_unconfirmed_business_fact_is_never_published() -> None:
    truth = BUSINESS_TRUTH.read_text(encoding="utf-8").replace(
        "CÔNG TY TNHH A & T CARE — ĐÃ XÁC NHẬN", "CÔNG TY TNHH A & T CARE — CẦN XÁC NHẬN"
    )
    with pytest.raises(PrivacyNoticeError, match="does not confirm"):
        notice_payload_from_documents(
            notice_markdown=NOTICE.read_text(encoding="utf-8"),
            business_truth=truth,
            source="x",
        )
