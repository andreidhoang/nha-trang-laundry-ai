"""Trợ lý AI — the internal owner-assistant, deterministic by construction.

The brain answers questions about the business in Vietnamese from reads the server already
governs. It never calls a model, never calculates money, policy, SLA or order state, and never
estimates: what the governed data does not say, the answer says it does not know.

The seam for a live model is `AssistantBrain`, a protocol with one method. A provider-backed
implementation enters only after capability authorization plus a credential — and holding the
credential alone authorizes nothing, per `docs/runbooks/provider-credentials.md`. Until those
gates pass, `DeterministicAssistantBrain` is the only implementation and matching is pure string
work over a fixed intent table: no model call exists anywhere on this path.

Two properties are pinned by unit tests:

* **Matching never rewrites the question.** A normalized copy (lowercased, Vietnamese diacritics
  stripped) decides the intent; the stored question is the verbatim text the person typed.
* **The intent table is first-match-wins and total.** Every question gets exactly one intent, and
  the fallback names what the assistant can do rather than guessing an answer.

Streaming (`answer_sse_frames`) is transport pacing of an already-durable deterministic answer,
not token generation. The turn is looked up after it was persisted, and the stored `answer` string
is replayed verbatim in word-sized SSE frames so the console can render it progressively. No model
is called, no content is produced or altered at stream time, and nothing new is persisted — the
frames are a reading of a row that already exists.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import psycopg
from nha_trang_laundry_db.approvals import ApprovalRepository
from nha_trang_laundry_db.assistant import (
    AssistantLink,
    AssistantTurn,
    AssistantTurnRepository,
    RecordTurnCommand,
    find_order_status,
    today_status_counts,
)
from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from nha_trang_laundry_observability import redact_text

from nha_trang_laundry_api.auth import AuthSettings

#: The one SLA rule the risk answer evaluates with. Choosing a policy per order is an open
#: business decision (the read model stays unrouted for exactly that reason), so this intent names
#: the rule it used rather than pretending to know each order's policy.
SLA_POLICY = STANDARD_WASH_SLA

#: Approvals page size for the count answer. A full page is a floor, not a total, and says so.
APPROVALS_PAGE = 100

#: SSE pacing for the turn stream. Frames carry whole words only — a Vietnamese word is never
#: split — and the inter-frame pause stays inside a 25-40ms band, compressed toward the floor for
#: long answers so even the longest one finishes in about four seconds.
STREAM_WORDS_PER_FRAME = 3
STREAM_MIN_FRAME_SECONDS = 0.025
STREAM_MAX_FRAME_SECONDS = 0.040
STREAM_BUDGET_SECONDS = 4.0


class AssistantUnavailable(RuntimeError):
    """Raised when the assistant's database is not configured."""


@dataclass(frozen=True, slots=True)
class OrderFact:
    """The one fact an order lookup may state: the status the server recorded."""

    order_id: UUID
    commercial_status: str


@dataclass(frozen=True, slots=True)
class AssistantAnswer:
    intent: str
    answer: str
    links: tuple[AssistantLink, ...]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssistantContextReads:
    """Everything the brain may look at, fetched by the service from governed repositories.

    `lookup_order` is a function rather than a prefetched row because whether a lookup is needed
    depends on the question, and prefetching every order to find out would read data nobody asked
    about.
    """

    today_counts: tuple[tuple[str, int], ...]
    sla_in_production: int
    sla_breached: int
    pending_approvals: int
    pending_approvals_truncated: bool
    pending_approval_previews: tuple[str, ...]
    lookup_order: Callable[[UUID], OrderFact | None]


class AssistantBrain(Protocol):
    """The live-model seam. One question plus governed reads in; one answer out."""

    def answer(self, question: str, context_reads: AssistantContextReads) -> AssistantAnswer: ...


@dataclass(frozen=True, slots=True)
class StoredAssistantTurn:
    """A persisted turn in exactly the shape the route returns, plus the replay marker."""

    turn_id: UUID
    intent: str
    answer: str
    links: tuple[AssistantLink, ...]
    reason_codes: tuple[str, ...]
    created_at: datetime
    replayed: bool


#: What the assistant can answer, in the owner's language. GREETING and the UNSUPPORTED fallback
#: both render from this table, so the two never drift apart.
CAPABILITY_TABLE: tuple[tuple[str, str], ...] = (
    ("hôm nay / tình hình", "số đơn của cửa hàng hôm nay, theo từng trạng thái (chỉ đếm)"),
    ("trễ / SLA / quá hạn", "số đơn đang sản xuất đã quá mốc rủi ro nội bộ"),
    ("phê duyệt / chờ duyệt", "số phong bì duyệt đang chờ, kèm vài mục gần nhất"),
    ("mã đơn đầy đủ (UUID)", "trạng thái hiện tại của một đơn trong cửa hàng"),
)

_LINK_TODAY = AssistantLink(label="Mở đơn hàng hôm nay", href="#/")
_LINK_EXCEPTIONS = AssistantLink(label="Mở màn hình ngoại lệ", href="#/exceptions")
_LINK_APPROVALS = AssistantLink(label="Mở hàng chờ duyệt", href="#/approvals")
_LINK_GAPS = AssistantLink(label="Xem năng lực chưa hỗ trợ", href="#/gaps")

#: An order carries no short public code; the identifier the console shows is the only reference
#: a person can quote back.
_ORDER_REFERENCE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _normalize(question: str) -> str:
    """Lowercase and strip Vietnamese diacritics from a COPY, for matching only.

    `đ`/`Đ` survive NFD decomposition (they carry no combining mark), so they are folded to `d`
    explicitly. The stored question is never this value.
    """
    decomposed = unicodedata.normalize("NFD", question.casefold())
    stripped = "".join(
        character for character in decomposed if unicodedata.category(character) != "Mn"
    )
    return " ".join(stripped.replace("đ", "d").split())


def _capability_lines() -> str:
    return "\n".join(f"• {cue} — {description}" for cue, description in CAPABILITY_TABLE)


class DeterministicAssistantBrain:
    """First-match-wins over a fixed intent table. No model, no arithmetic, no guessing."""

    def answer(self, question: str, context_reads: AssistantContextReads) -> AssistantAnswer:
        normalized = _normalize(question)
        if any(cue in normalized for cue in ("xin chao", "chao", "hello")):
            return _greeting()
        if any(cue in normalized for cue in ("hom nay", "tinh hinh", "don hom nay")):
            return _today_overview(context_reads)
        if any(cue in normalized for cue in ("tre", "sla", "qua han", "nguy co")):
            return _sla_risk(context_reads)
        if any(cue in normalized for cue in ("phe duyet", "cho duyet", "duyet")):
            return _pending_approvals(context_reads)
        reference = _ORDER_REFERENCE.search(question)
        if reference is not None:
            return _order_lookup(UUID(reference.group(0)), context_reads)
        if any(cue in normalized for cue in ("doanh thu", "tien", "thu nhap", "lai", "lo")):
            return _revenue_unavailable()
        return AssistantAnswer(
            intent="UNSUPPORTED",
            answer=(
                "Tôi chưa trả lời được câu hỏi này. Tôi chỉ trả lời được những nhóm sau:\n"
                + _capability_lines()
                + "\nCâu hỏi về doanh thu hay số tiền chưa có câu trả lời — hệ thống chưa kết "
                "nối số liệu đó và tôi không bao giờ đoán."
            ),
            links=(_LINK_TODAY, _LINK_APPROVALS, _LINK_GAPS),
            reason_codes=("ASSISTANT_INTENT_UNSUPPORTED",),
        )


def _greeting() -> AssistantAnswer:
    return AssistantAnswer(
        intent="GREETING",
        answer=(
            "Chào bạn. Tôi là trợ lý nội bộ: mọi câu trả lời chỉ đến từ dữ liệu vận hành hệ "
            "thống đang quản lý, và điều gì tôi không biết tôi sẽ nói là không biết. Tôi trả lời "
            "được các nhóm sau:\n" + _capability_lines()
        ),
        links=(_LINK_TODAY, _LINK_APPROVALS, _LINK_EXCEPTIONS),
        reason_codes=(),
    )


def _today_overview(context_reads: AssistantContextReads) -> AssistantAnswer:
    counts = dict(context_reads.today_counts)
    if not counts:
        text = "Hôm nay cửa hàng này chưa có đơn nào (theo giờ Asia/Ho_Chi_Minh)."
    else:
        breakdown = ", ".join(
            f"{count} đơn ở trạng thái {status}" for status, count in context_reads.today_counts
        )
        text = (
            f"Hôm nay (theo giờ Asia/Ho_Chi_Minh) cửa hàng có {breakdown}. "
            f"Tổng cộng {sum(counts.values())} đơn. Đây là số đếm theo trạng thái máy chủ ghi "
            "nhận, không phải chỉ số hiệu suất."
        )
    return AssistantAnswer(
        intent="TODAY_OVERVIEW",
        answer=text,
        links=(_LINK_TODAY,),
        reason_codes=(),
    )


def _sla_risk(context_reads: AssistantContextReads) -> AssistantAnswer:
    if context_reads.sla_in_production == 0:
        text = "Hiện không có đơn nào đang sản xuất, nên không có đơn nào có nguy cơ trễ."
    else:
        text = (
            f"Có {context_reads.sla_in_production} đơn đang sản xuất, trong đó "
            f"{context_reads.sla_breached} đơn đã quá mốc rủi ro nội bộ (SLA_BREACHED). Mốc này "
            f"tính theo quy tắc {SLA_POLICY.policy_id} — {SLA_POLICY.target_max_hours} giờ kể từ "
            "khi nhận sản xuất; quy tắc SLA riêng của từng đơn là quyết định kinh doanh chưa được "
            "chốt, nên con số này dùng đúng một quy tắc đã nêu."
        )
    return AssistantAnswer(
        intent="SLA_RISK",
        answer=text,
        links=(_LINK_EXCEPTIONS,),
        reason_codes=(),
    )


def _pending_approvals(context_reads: AssistantContextReads) -> AssistantAnswer:
    if context_reads.pending_approvals == 0:
        text = "Không có phong bì duyệt nào đang chờ (REQUESTED) trên các cửa hàng bạn được gán."
    else:
        total = f"{context_reads.pending_approvals}"
        if context_reads.pending_approvals_truncated:
            total += " (đã đầy một trang, có thể còn nữa)"
        text = (
            f"Có {total} phong bì duyệt đang chờ (REQUESTED) trên các cửa hàng bạn được gán — "
            "danh sách này không riêng cửa hàng đang chọn."
        )
        if context_reads.pending_approval_previews:
            text += " Vài mục sắp hết hạn nhất:\n" + "\n".join(
                f"• {preview}" for preview in context_reads.pending_approval_previews
            )
    return AssistantAnswer(
        intent="PENDING_APPROVALS",
        answer=text,
        links=(_LINK_APPROVALS,),
        reason_codes=(),
    )


def _order_lookup(order_id: UUID, context_reads: AssistantContextReads) -> AssistantAnswer:
    found = context_reads.lookup_order(order_id)
    if found is None:
        return AssistantAnswer(
            intent="ORDER_LOOKUP",
            answer=(
                f"Không tìm thấy đơn {order_id} trong cửa hàng này. Có thể mã không đúng, hoặc "
                "đơn thuộc cửa hàng khác — tôi chỉ nhìn được cửa hàng bạn chọn."
            ),
            links=(),
            reason_codes=(),
        )
    return AssistantAnswer(
        intent="ORDER_LOOKUP",
        answer=f"Đơn {found.order_id} đang ở trạng thái {found.commercial_status}.",
        links=(AssistantLink(label="Mở đơn hàng", href=f"#/orders/{found.order_id}"),),
        reason_codes=(),
    )


def _revenue_unavailable() -> AssistantAnswer:
    return AssistantAnswer(
        intent="REVENUE_UNAVAILABLE",
        answer=(
            "Hệ thống chưa kết nối số liệu doanh thu — việc đó cần một quyết định chính sách của "
            "chủ, và chưa có quyết định nào được phê chuẩn. Tôi không bao giờ ước lượng hay đoán "
            "một con số tiền."
        ),
        links=(_LINK_GAPS,),
        reason_codes=(),
    )


def _word_chunks(answer: str) -> list[str]:
    """Split the stored answer into frames of a few whole words, losslessly.

    Each token is a word plus its trailing whitespace, so concatenating every chunk reproduces the
    stored string exactly — the stream adds nothing and drops nothing.
    """
    tokens = re.findall(r"\S+(?:\s+|$)", answer)
    if not tokens:
        return []
    max_frames = int(STREAM_BUDGET_SECONDS // STREAM_MIN_FRAME_SECONDS)
    per_frame = max(STREAM_WORDS_PER_FRAME, -(-len(tokens) // max_frames))
    return [
        "".join(tokens[index : index + per_frame]) for index in range(0, len(tokens), per_frame)
    ]


def answer_sse_frames(answer: str) -> Iterator[str]:
    """Pace an already-persisted answer as SSE frames, ending with `data: [DONE]`.

    Each frame's payload is a JSON string, so newlines inside the answer survive the framing; the
    client concatenates the decoded strings and gets the stored answer back verbatim.
    """
    chunks = _word_chunks(answer)
    delay = min(
        STREAM_MAX_FRAME_SECONDS,
        max(STREAM_MIN_FRAME_SECONDS, STREAM_BUDGET_SECONDS / max(len(chunks), 1)),
    )
    for index, chunk in enumerate(chunks):
        if index:
            time.sleep(delay)
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


class AssistantService:
    """Own connection lifetimes while repositories own transactional semantics."""

    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = psycopg.connect,
        brain: AssistantBrain | None = None,
    ) -> None:
        if not settings.database_url:
            raise AssistantUnavailable("assistant database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._brain: AssistantBrain = brain or DeterministicAssistantBrain()
        self._turns = AssistantTurnRepository()
        self._approvals = ApprovalRepository()
        self._idempotency = IdempotencyRepository()

    def post_turn(
        self,
        *,
        principal: StaffPrincipal,
        store_id: UUID,
        question: str,
        idempotency_key: str,
        correlation_id: UUID | None = None,
    ) -> StoredAssistantTurn:
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                # Membership first, outside the idempotency wrapper: a staff member who has lost
                # access to this store must not replay a key into a fresh write.
                require_store_membership(
                    cursor,
                    staff_user_id=principal.staff_user_id,
                    store_id=store_id,
                    error=StoreAccessError,
                )
            context_reads = self._build_reads(connection, principal=principal, store_id=store_id)
            answer = self._brain.answer(question, context_reads)
            turn_id = uuid4()
            # Tier-2 conversation-memory discipline: only the redacted text may reach the table.
            # Matching ran on the verbatim question; what is stored — and what a replay returns —
            # is the same text with credentials and personal contact details substituted out.
            redacted_question = redact_text(question)
            redacted_answer = redact_text(answer.answer)

            def commit() -> dict[str, object]:
                turn = self._turns.record_turn(
                    connection,
                    RecordTurnCommand(
                        turn_id=turn_id,
                        store_id=store_id,
                        principal=principal,
                        question=redacted_question,
                        intent=answer.intent,
                        answer=redacted_answer,
                        links=answer.links,
                        reason_codes=answer.reason_codes,
                        correlation_id=correlation_id or uuid4(),
                        idempotency_key=f"assistant-turn:{turn_id}",
                    ),
                )
                return _turn_mapping(turn)

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"assistant-turn:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={"store_id": str(store_id), "question": question},
                ),
                commit,
            )
        return _stored_turn(result.response, replayed=result.replayed)

    def list_turns(
        self, *, principal: StaffPrincipal, store_id: UUID, limit: int
    ) -> tuple[AssistantTurn, ...]:
        with self._connection_factory(self._database_url) as connection:
            return self._turns.list_recent(
                connection, store_id=store_id, principal=principal, limit=limit
            )

    def get_turn(
        self, *, principal: StaffPrincipal, store_id: UUID, turn_id: UUID
    ) -> AssistantTurn | None:
        """One persisted turn for streaming; `None` for both unknown and invisible ids."""
        with self._connection_factory(self._database_url) as connection:
            return self._turns.get_scoped(
                connection, store_id=store_id, principal=principal, turn_id=turn_id
            )

    def _build_reads(
        self, connection: Any, *, principal: StaffPrincipal, store_id: UUID
    ) -> AssistantContextReads:
        with connection.cursor() as cursor:
            counts = today_status_counts(cursor, store_id=store_id, principal=principal)
            approvals = self._approvals.list_pending(
                cursor, principal=principal, limit=APPROVALS_PAGE
            )
        board = ShadowConsoleRepository().sla_risk_board(
            connection, store_id=store_id, principal=principal, policy=SLA_POLICY
        )

        def lookup_order(order_id: UUID) -> OrderFact | None:
            with connection.cursor() as cursor:
                found = find_order_status(
                    cursor, store_id=store_id, principal=principal, order_id=order_id
                )
            return (
                None if found is None else OrderFact(order_id=found[0], commercial_status=found[1])
            )

        return AssistantContextReads(
            today_counts=counts,
            sla_in_production=len(board),
            sla_breached=sum(1 for risk in board if "SLA_BREACHED" in risk.reason_codes),
            pending_approvals=len(approvals),
            pending_approvals_truncated=len(approvals) >= APPROVALS_PAGE,
            pending_approval_previews=tuple(
                f"{str(item.approval_request_id)[:8]}… · yêu cầu vai trò {item.required_role.value}"
                for item in approvals[:5]
            ),
            lookup_order=lookup_order,
        )


def _turn_mapping(turn: AssistantTurn) -> dict[str, object]:
    return {
        "turn_id": str(turn.turn_id),
        "intent": turn.intent,
        "answer": turn.answer,
        "links": [{"label": link.label, "href": link.href} for link in turn.links],
        "reason_codes": list(turn.reason_codes),
        "created_at": turn.created_at.isoformat(),
    }


def _stored_turn(response: Mapping[str, object], *, replayed: bool) -> StoredAssistantTurn:
    """Rebuild the wire shape from the idempotent response, which may be a replay's copy."""
    links = response["links"]
    reason_codes = response["reason_codes"]
    if not isinstance(links, list) or not isinstance(reason_codes, list):
        raise ValueError("stored assistant turn response is invalid")
    return StoredAssistantTurn(
        turn_id=UUID(str(response["turn_id"])),
        intent=str(response["intent"]),
        answer=str(response["answer"]),
        links=tuple(
            AssistantLink(label=str(item["label"]), href=str(item["href"]))
            for item in links
            if isinstance(item, dict)
        ),
        reason_codes=tuple(str(code) for code in reason_codes),
        created_at=datetime.fromisoformat(str(response["created_at"])),
        replayed=replayed,
    )
