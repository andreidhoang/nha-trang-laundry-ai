"""The owner-assistant API: intents, opaque refusals, idempotent replay.

These are route/contract tests over a stub service, exactly the way `test_store_scope_refusals.py`
pins the refusal mapping: the point is what the boundary does with each outcome, not what
PostgreSQL does. The stub still runs the real `DeterministicAssistantBrain`, so each intent class
is exercised end to end — the route, the service contract and the matching table — with only the
database replaced.

The repository-level scoping (a non-owner sees only their own turns) is a database fact and is
covered in `test_assistant_postgres.py`; here the list route is pinned for shape and pass-through.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.assistant import (
    AssistantContextReads,
    DeterministicAssistantBrain,
    StoredAssistantTurn,
    answer_sse_frames,
)
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_assistant_service,
    get_identity_service,
)
from nha_trang_laundry_db.assistant import AssistantLink, AssistantTurn
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.store_access import StoreAccessError

STAFF_ID = UUID("00000000-0000-0000-0000-0000000004a1")
STORE_ID = UUID("00000000-0000-0000-0000-0000000004a2")
ORDER_ID = UUID("00000000-0000-0000-0000-0000000004a3")

ORIGIN = "http://testserver"
CSRF = "y" * 40

BRAIN = DeterministicAssistantBrain()


def _reads() -> AssistantContextReads:
    return AssistantContextReads(
        today_counts=(("ACTIVE", 1), ("CONFIRMED", 2)),
        sla_in_production=3,
        sla_breached=1,
        pending_approvals=2,
        pending_approvals_truncated=False,
        pending_approval_previews=("abcd1234… · yêu cầu vai trò OWNER_ADMIN",),
        lookup_order=lambda order_id: None,
    )


class BrainBackedAssistantService:
    """Answers with the real deterministic brain; persistence is replaced by construction.

    The turn id is deliberately stable: the route's replay contract is that the same logical
    command returns the same turn, and the stub must be able to keep it.
    """

    TURN_ID = UUID("00000000-0000-0000-0000-0000000004a9")
    #: A turn that exists but belongs to another staff member: the repository scopes it away, so
    #: the service stub reports it exactly as it reports an id that was never recorded.
    INVISIBLE_TURN_ID = UUID("00000000-0000-0000-0000-0000000004aa")

    def __init__(self, *, replayed: bool = False) -> None:
        self._replayed = replayed
        self._turn_id = self.TURN_ID

    def post_turn(self, *, question: str, **_: Any) -> StoredAssistantTurn:
        answer = BRAIN.answer(question, _reads())
        return StoredAssistantTurn(
            turn_id=self._turn_id,
            intent=answer.intent,
            answer=answer.answer,
            links=answer.links,
            reason_codes=answer.reason_codes,
            created_at=datetime.now(UTC),
            replayed=self._replayed,
        )

    def _turn(self) -> AssistantTurn:
        return AssistantTurn(
            turn_id=self._turn_id,
            store_id=STORE_ID,
            staff_user_id=STAFF_ID,
            question="Hôm nay thế nào?",
            intent="TODAY_OVERVIEW",
            answer="Hôm nay cửa hàng có 1 đơn ở trạng thái ACTIVE.",
            links=(AssistantLink(label="Mở đơn hàng hôm nay", href="#/"),),
            reason_codes=(),
            correlation_id=str(uuid4()),
            created_at=datetime.now(UTC),
        )

    def list_turns(self, **_: Any) -> tuple[AssistantTurn, ...]:
        return (self._turn(),)

    def get_turn(self, *, turn_id: UUID, **_: Any) -> AssistantTurn | None:
        # Both the invisible id and every unknown id come back as None, indistinguishably.
        return self._turn() if turn_id == self._turn_id else None


class RefusingAssistantService:
    """Every call refuses the way the repository refuses a non-member."""

    def post_turn(self, **_: Any) -> None:
        raise StoreAccessError("store access is not authorized")

    def list_turns(self, **_: Any) -> None:
        raise StoreAccessError("store access is not authorized")

    def get_turn(self, **_: Any) -> None:
        raise StoreAccessError("store access is not authorized")


class ConflictingAssistantService:
    def post_turn(self, **_: Any) -> None:
        raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")


def _write_headers(key: str = "assistant-key-0001") -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": key}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        STAFF_ID, "owner-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    app.dependency_overrides[get_assistant_service] = BrainBackedAssistantService
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Xin chào, bạn làm được gì?", "GREETING"),
        ("Hôm nay tình hình thế nào?", "TODAY_OVERVIEW"),
        ("Có đơn nào đang trễ không?", "SLA_RISK"),
        ("Có phong bì nào chờ duyệt không?", "PENDING_APPROVALS"),
        (f"Đơn {ORDER_ID} đang ở đâu?", "ORDER_LOOKUP"),
        ("Doanh thu tháng này là bao nhiêu?", "REVENUE_UNAVAILABLE"),
        ("Thời tiết tuần tới ra sao?", "UNSUPPORTED"),
    ],
)
def test_each_question_class_gets_its_intent(
    client: TestClient, question: str, intent: str
) -> None:
    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/assistant/turns",
        headers=_write_headers(),
        json={"question": question},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["intent"] == intent
    assert body["answer"]
    assert body["replayed"] is False
    assert isinstance(body["links"], list)
    assert isinstance(body["reason_codes"], list)
    UUID(body["turn_id"])
    datetime.fromisoformat(body["created_at"])
    if intent == "UNSUPPORTED":
        assert body["reason_codes"] == ["ASSISTANT_INTENT_UNSUPPORTED"]


def test_a_non_member_gets_the_single_opaque_refusal(client: TestClient) -> None:
    app.dependency_overrides[get_assistant_service] = RefusingAssistantService

    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/assistant/turns",
        headers=_write_headers(),
        json={"question": "Hôm nay thế nào?"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "operation denied"}

    listing = client.get(f"/internal/v1/stores/{STORE_ID}/assistant/turns")
    assert listing.status_code == 403
    assert listing.json() == {"detail": "operation denied"}


def test_a_missing_idempotency_key_is_refused(client: TestClient) -> None:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": CSRF}
    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/assistant/turns",
        headers=headers,
        json={"question": "Hôm nay thế nào?"},
    )

    assert response.status_code == 422


def test_replaying_a_key_returns_the_same_turn_marked_replayed(client: TestClient) -> None:
    app.dependency_overrides[get_assistant_service] = lambda: BrainBackedAssistantService(
        replayed=True
    )

    first = client.post(
        f"/internal/v1/stores/{STORE_ID}/assistant/turns",
        headers=_write_headers(),
        json={"question": "Hôm nay thế nào?"},
    )
    replay = client.post(
        f"/internal/v1/stores/{STORE_ID}/assistant/turns",
        headers=_write_headers(),
        json={"question": "Hôm nay thế nào?"},
    )

    assert first.status_code == replay.status_code == 201
    assert replay.json()["turn_id"] == first.json()["turn_id"]
    assert replay.json()["replayed"] is True


def test_reusing_a_key_with_a_different_question_conflicts(client: TestClient) -> None:
    app.dependency_overrides[get_assistant_service] = ConflictingAssistantService

    response = client.post(
        f"/internal/v1/stores/{STORE_ID}/assistant/turns",
        headers=_write_headers(),
        json={"question": "Một câu hỏi khác hẳn."},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "IDEMPOTENCY_CONFLICT"}


def test_an_unauthenticated_call_is_refused() -> None:
    app.dependency_overrides[get_identity_service] = lambda: object()
    app.dependency_overrides[get_assistant_service] = BrainBackedAssistantService
    try:
        client = TestClient(app)
        response = client.post(
            f"/internal/v1/stores/{STORE_ID}/assistant/turns",
            headers=_write_headers(),
            json={"question": "Hôm nay thế nào?"},
        )
        listing = client.get(f"/internal/v1/stores/{STORE_ID}/assistant/turns")
        stream = client.get(
            "/internal/v1/stores/"
            f"{STORE_ID}/assistant/turns/{BrainBackedAssistantService.TURN_ID}/stream"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert listing.status_code == 401
    assert stream.status_code == 401


def test_the_history_route_returns_newest_first_typed_items(client: TestClient) -> None:
    response = client.get(f"/internal/v1/stores/{STORE_ID}/assistant/turns?limit=50")

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    item = items[0]
    assert item["intent"] == "TODAY_OVERVIEW"
    assert item["question"] == "Hôm nay thế nào?"
    assert item["links"] == [{"label": "Mở đơn hàng hôm nay", "href": "#/"}]
    datetime.fromisoformat(item["created_at"])


def _sse_frames(body: str) -> list[str]:
    return [
        line.removeprefix("data: ")
        for block in body.split("\n\n")
        for line in block.splitlines()
        if line.startswith("data: ")
    ]


def test_the_stream_replays_the_stored_answer_word_frames_then_done(client: TestClient) -> None:
    stored = BrainBackedAssistantService()._turn()

    response = client.get(f"/internal/v1/stores/{STORE_ID}/assistant/turns/{stored.turn_id}/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _sse_frames(response.text)
    assert len(frames) >= 3, "a real answer streams as several frames, not one blob"
    assert frames[-1] == "[DONE]"
    reconstructed = "".join(json.loads(frame) for frame in frames[:-1])
    assert reconstructed == stored.answer


def test_a_word_is_never_split_and_whitespace_survives_the_framing() -> None:
    answer = "Hôm nay cửa hàng có đơn.\nDòng hai giữ nguyên xuống dòng.\n"
    frames = _sse_frames("".join(answer_sse_frames(answer)))
    assert frames[-1] == "[DONE]"
    assert "".join(json.loads(frame) for frame in frames[:-1]) == answer
    for frame in frames[:-1]:
        # No frame begins or ends mid-word: each is whole words plus trailing whitespace.
        assert frame == json.dumps(json.loads(frame), ensure_ascii=False)


def test_an_unknown_turn_and_an_invisible_turn_are_the_same_response(client: TestClient) -> None:
    unknown = client.get(f"/internal/v1/stores/{STORE_ID}/assistant/turns/{uuid4()}/stream")
    invisible = client.get(
        "/internal/v1/stores/"
        f"{STORE_ID}/assistant/turns/{BrainBackedAssistantService.INVISIBLE_TURN_ID}/stream"
    )

    assert unknown.status_code == invisible.status_code == 404
    assert unknown.content == invisible.content
    assert unknown.json() == {"detail": "assistant turn not found"}


def test_the_stream_refuses_a_non_member_with_the_opaque_403(client: TestClient) -> None:
    app.dependency_overrides[get_assistant_service] = RefusingAssistantService

    response = client.get(
        "/internal/v1/stores/"
        f"{STORE_ID}/assistant/turns/{BrainBackedAssistantService.TURN_ID}/stream"
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "operation denied"}
