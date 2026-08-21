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
    # Vietnamese, because the whole answer is Vietnamese — "1 đơn ở trạng thái ACTIVE" is a
    # sentence half of which the person reading it cannot read.
    assert "2 đơn đã xác nhận" in accented.answer
    assert "1 đơn đang chạy" in accented.answer
    assert "ACTIVE" not in accented.answer
    assert accented.links[0].href == "#/"


def test_a_status_with_no_vietnamese_name_is_shown_raw_not_guessed() -> None:
    """A status the gloss has not been taught must look unfamiliar, not plausible.

    The failure this guards against is the quiet one: somebody adds a status to the domain, the
    assistant does not know its Vietnamese name, and rather than saying something the reader can
    see is unfamiliar it drops the status or renders an approximation. The raw token is the honest
    answer — it is unreadable in exactly the way that prompts a question.
    """

    def lookup(order_id: UUID) -> OrderFact:
        return OrderFact(order_id=order_id, commercial_status="AWAITING_ALIEN_INSPECTION")

    found = BRAIN.answer(f"Đơn {ORDER_ID} tới đâu rồi?", _reads(lookup_order=lookup))

    assert "AWAITING_ALIEN_INSPECTION" in found.answer


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
    assert "đang chạy" in found.answer and "ACTIVE" not in found.answer
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


def test_a_money_question_that_names_a_timeframe_is_still_refused() -> None:
    """The regression that motivated the precedence rule, in the owner's own phrasings.

    Every one of these came back `TODAY_OVERVIEW` against the running stack — an order count in
    answer to a question about money, with nothing on screen saying the money part had been
    dropped. The table is first-match-wins and the money rule sat last, so any money question that
    also named a timeframe matched a topic first. Not computing money is necessary and was never
    the whole promise; saying so is the rest of it.
    """
    for question in (
        "Doanh thu hôm nay bao nhiêu?",
        "Hôm nay thu được bao nhiêu tiền?",
        "Tình hình doanh thu thế nào?",
        "Doanh thu tuần này?",
    ):
        assert BRAIN.answer(question, _reads()).intent == "REVENUE_UNAVAILABLE", question


def test_ordinary_words_that_merely_contain_a_cue_are_not_money_questions() -> None:
    """Diacritic folding turns three money words into four extremely common ordinary ones.

    `lãi`→`lai` collides with `lại` (again), and `lỗ`→`lo` sits inside `lỗi` (error), `lọc`
    (filter) and `lớn` (big). Matched as substrings of the folded text, those cues refused a
    laundry question as if it had asked about revenue. The first case below is the sharpest: it is
    one of the four suggestion chips `screens/assistant.js` ships on the empty transcript, and that
    module documents each chip as mapping to a documented intent of this brain.
    """
    for question in (
        "Bạn trả lời được gì?",
        "Đơn này giặt lại được không?",
        "Máy giặt bị lỗi thì ghi vào đâu?",
        "Làm sao lọc đơn theo trạng thái?",
        "Đơn nào lớn nhất hôm qua?",
    ):
        assert BRAIN.answer(question, _reads()).intent != "REVENUE_UNAVAILABLE", question


def test_a_cue_is_a_whole_word_and_an_accent_can_be_the_whole_word() -> None:
    """`tre` lived inside `trên`, and `chao` inside `cháo`. Neither is a question about laundry."""
    assert BRAIN.answer("Đơn ở trên bảng có gì?", _reads()).intent == "UNSUPPORTED"
    assert BRAIN.answer("Trẻ em có giảm giá không?", _reads()).intent == "UNSUPPORTED"
    assert BRAIN.answer("Cháo lòng có giặt không?", _reads()).intent == "UNSUPPORTED"
    # And the words those collided with still match, accented and bare alike.
    assert BRAIN.answer("Đơn nào sắp trễ hẹn?", _reads()).intent == "SLA_RISK"
    assert BRAIN.answer("don nao sap tre hen", _reads()).intent == "SLA_RISK"
    assert BRAIN.answer("chao ban", _reads()).intent == "GREETING"


def test_every_suggestion_chip_the_console_ships_reaches_its_documented_intent() -> None:
    """`screens/assistant.js` SUGGESTIONS, verbatim, with the intent each one is offered for.

    The chips are the first thing an operator with an empty transcript can do, so a chip that
    lands on the wrong intent is the console's own demonstration of itself going wrong. Keeping
    them here means the list and the brain cannot drift apart silently.
    """
    assert BRAIN.answer("Hôm nay thế nào?", _reads()).intent == "TODAY_OVERVIEW"
    assert BRAIN.answer("Đơn nào sắp trễ hẹn?", _reads()).intent == "SLA_RISK"
    assert BRAIN.answer("Có gì chờ duyệt không?", _reads()).intent == "PENDING_APPROVALS"
    # "What can you answer?" is a question about capability, and the fallback is the answer that
    # lists them. It reached REVENUE_UNAVAILABLE for as long as `lo` was matched as a substring.
    answer = BRAIN.answer("Bạn trả lời được gì?", _reads())
    assert answer.intent == "UNSUPPORTED"
    for cue in ("hôm nay", "SLA", "phê duyệt", "UUID"):
        assert cue in answer.answer
