"""The deterministic assistant brain: matching is total, honest, and computes nothing.

The properties pinned here are the ones a wrong answer would violate quietly: Vietnamese typed
with or without diacritics must match the same intent, the table must be first-match-wins, an
order reference must be extracted from the raw question (never the normalized copy), and the two
refusal answers must never fabricate — no invented capability, and no digit anywhere in the
revenue refusal, because a number there would be an estimate.
"""

from __future__ import annotations

from uuid import UUID

from nha_trang_laundry_api.assistant import (
    AssistantContextReads,
    DeterministicAssistantBrain,
    OrderFact,
)

ORDER_ID = UUID("123e4567-e89b-42d3-a456-426614174000")
BRAIN = DeterministicAssistantBrain()


def _reads(**overrides: object) -> AssistantContextReads:
    base: dict[str, object] = {
        "today_counts": (("ACTIVE", 1), ("CONFIRMED", 2)),
        "sla_in_production": 3,
        "sla_breached": 1,
        "pending_approvals": 2,
        "pending_approvals_truncated": False,
        "pending_approval_previews": ("abcd1234… · yêu cầu vai trò OWNER_ADMIN",),
        "lookup_order": lambda order_id: None,
    }
    base.update(overrides)
    return AssistantContextReads(**base)  # type: ignore[arg-type]


def test_greeting_matches_with_and_without_diacritics() -> None:
    assert BRAIN.answer("Xin chào", _reads()).intent == "GREETING"
    assert BRAIN.answer("chao ban", _reads()).intent == "GREETING"
    assert "•" in BRAIN.answer("hello", _reads()).answer


def test_today_overview_is_diacritic_insensitive_and_names_counts() -> None:
    accented = BRAIN.answer("Hôm nay tình hình thế nào?", _reads())
    plain = BRAIN.answer("hom nay tinh hinh the nao?", _reads())

    assert accented.intent == plain.intent == "TODAY_OVERVIEW"
    assert "2 đơn ở trạng thái CONFIRMED" in accented.answer
    assert "1 đơn ở trạng thái ACTIVE" in accented.answer
    assert accented.links[0].href == "#/"


def test_the_intent_table_is_first_match_wins() -> None:
    # GREETING precedes TODAY_OVERVIEW, which precedes SLA_RISK, which precedes approvals.
    assert BRAIN.answer("Chào, hôm nay có đơn nào trễ không?", _reads()).intent == "GREETING"
    assert BRAIN.answer("Đơn nào đang trễ hay chờ duyệt?", _reads()).intent == "SLA_RISK"


def test_sla_risk_names_the_board_counts_and_the_rule_used() -> None:
    answer = BRAIN.answer("Có đơn nào sắp quá hạn không?", _reads())

    assert answer.intent == "SLA_RISK"
    assert "3 đơn đang sản xuất" in answer.answer
    assert "1 đơn đã quá mốc rủi ro nội bộ" in answer.answer
    assert "SLA_STANDARD_CLOTHES" in answer.answer
    assert answer.links[0].href == "#/exceptions"


def test_pending_approvals_names_count_preview_and_scope() -> None:
    answer = BRAIN.answer("Có gì đang chờ phê duyệt?", _reads())

    assert answer.intent == "PENDING_APPROVALS"
    assert "2 phong bì duyệt" in answer.answer
    assert "abcd1234" in answer.answer
    assert answer.links[0].href == "#/approvals"


def test_order_lookup_extracts_the_reference_from_the_raw_question() -> None:
    seen: list[UUID] = []

    def lookup(order_id: UUID) -> OrderFact | None:
        seen.append(order_id)
        return OrderFact(order_id=order_id, commercial_status="ACTIVE")

    found = BRAIN.answer(f"Đơn {ORDER_ID} tới đâu rồi?", _reads(lookup_order=lookup))

    assert found.intent == "ORDER_LOOKUP"
    assert seen == [ORDER_ID]
    assert "ACTIVE" in found.answer
    assert found.links[0].href == f"#/orders/{ORDER_ID}"

    missing = BRAIN.answer(f"đơn {ORDER_ID}", _reads())
    assert missing.intent == "ORDER_LOOKUP"
    assert "Không tìm thấy đơn" in missing.answer
    assert missing.links == ()


def test_the_revenue_refusal_contains_no_number_at_all() -> None:
    for question in (
        "Doanh thu tháng này thế nào?",
        "khách còn nợ bao nhiêu tiền",
        "tháng này lãi hay lỗ",
    ):
        answer = BRAIN.answer(question, _reads())
        assert answer.intent == "REVENUE_UNAVAILABLE"
        assert not any(character.isdigit() for character in answer.answer)
        assert answer.links[0].href == "#/gaps"


def test_the_fallback_names_capabilities_and_fabricates_nothing() -> None:
    answer = BRAIN.answer("ThờI tiết tuần tới ra sao?".replace("I", "i"), _reads())

    assert answer.intent == "UNSUPPORTED"
    assert answer.reason_codes == ("ASSISTANT_INTENT_UNSUPPORTED",)
    # Rendered from the same capability table the greeting uses: the cues are present.
    for cue in ("hôm nay", "SLA", "phê duyệt", "UUID"):
        assert cue in answer.answer
