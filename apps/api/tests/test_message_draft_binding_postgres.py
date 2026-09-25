"""`MESSAGE-DRAFT-BINDING-001` at the HTTP boundary, against PostgreSQL.

`API-INTEGRITY-002` made the server compute what a `SEND_MESSAGE` envelope binds and exposed it
nowhere: an envelope could not be raised from the console, and an approver deciding one could not
read the message. `GET /internal/v1/stores/{store_id}/message-drafts/{agent_run_id}/binding` is that
read. These tests drive it the way the console does -- read, raise from what was read, list, decide
from what was listed, lock the manual send -- and prove the refusals are the opaque ones.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import app, current_principal, get_operations_service
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository

# See `test_ops_board_postgres.py`: the real draft fixture lives beside the repository tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from message_draft_test_data import DRAFT_TEXT, SeededDraft, current_binding, seed_message_draft

ORIGIN = "http://testserver"
CSRF = "q" * 40
NOW = datetime.now(UTC)
EDITED = "Dạ, tiệm mở cửa đến 21 giờ, anh/chị ghé nhận giúp em ạ."


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def service() -> OperationsService:
    return OperationsService(AuthSettings(database_url=_database_url()))


def _staff(role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
    return StaffPrincipal(uuid4(), f"binding-{uuid4().hex}", frozenset({role}), mfa, uuid4())


class _Shop:
    """A store with an operator who raises, an approver who decides, a sender, and one draft."""

    def __init__(self, connection: Any) -> None:
        self.operator = _staff(StaffRole.OPERATOR)
        self.approver = _staff(StaffRole.OPS_APPROVER)
        self.sender = _staff(StaffRole.OPERATOR)
        self.store_id = uuid4()
        StoreRepository.create(
            connection,
            store_id=self.store_id,
            name="Cửa hàng",
            created_by=None,
            correlation_id=uuid4(),
        )
        for member in (self.operator, self.approver, self.sender):
            self.add(connection, member)
        self.draft: SeededDraft = seed_message_draft(connection, self.store_id)

    def add(self, connection: Any, member: StaffPrincipal) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s) ON CONFLICT (id) DO NOTHING
                """,
                (member.staff_user_id, member.oidc_subject, NOW),
            )
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (member.staff_user_id, self.store_id, member.staff_user_id, NOW),
            )

    @property
    def url(self) -> str:
        return binding_url(self.store_id, self.draft.agent_run_id)

    def review(self, connection: Any, decision: str, **values: Any) -> None:
        ShadowConsoleRepository().decide_draft(
            connection,
            agent_run_id=self.draft.agent_run_id,
            decision=decision,
            principal=self.approver,
            correlation_id=uuid4(),
            **values,
        )


def binding_url(store_id: UUID, agent_run_id: UUID) -> str:
    return f"/internal/v1/stores/{store_id}/message-drafts/{agent_run_id}/binding"


@contextmanager
def _as(service: OperationsService, principal: StaffPrincipal) -> Iterator[TestClient]:
    app.dependency_overrides[current_principal] = lambda: principal
    app.dependency_overrides[get_operations_service] = lambda: service
    try:
        with TestClient(app, cookies={"staff_session": "t", "staff_csrf": CSRF}) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": CSRF}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _raise_from(read: dict[str, Any]) -> dict[str, Any]:
    """The `ApprovalRequest` the console builds: every value copied from the binding read."""
    return {
        key: read[key]
        for key in (
            "store_id",
            "action",
            "resource_type",
            "resource_id",
            "resource_version",
            "snapshot_hash",
            "rendered_hash",
            "policy_version",
        )
    }


def _decision(item: dict[str, Any], decision: str) -> dict[str, Any]:
    return {
        "decision": decision,
        "reason_code": "HUMAN_REVIEW_COMPLETE",
        "resource_version": item["resource_version"],
        "snapshot_hash": item["snapshot_hash"],
        "rendered_hash": item["rendered_hash"],
    }


def _raise_envelope(service: OperationsService, shop: _Shop, raiser: StaffPrincipal) -> UUID:
    with _as(service, raiser) as client:
        read = client.get(shop.url)
        assert read.status_code == 200, read.text
        created = client.post(
            "/internal/v1/approvals",
            json=_raise_from(read.json()),
            headers=_headers(f"a-{uuid4().hex}"),
        )
    assert created.status_code == 201, created.text
    return UUID(created.json()["approval_request_id"])


def _queued(client: TestClient, approval_id: UUID) -> dict[str, Any]:
    listed = client.get("/internal/v1/approvals")
    assert listed.status_code == 200, listed.text
    matches: list[dict[str, Any]] = [
        i for i in listed.json() if i["approval_request_id"] == str(approval_id)
    ]
    assert len(matches) == 1, listed.text
    return matches[0]


# --- the read ---------------------------------------------------------------------------------


def test_the_approver_reads_the_exact_words_and_the_server_computed_digests(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    expected = current_binding(connection, shop.draft.agent_run_id)

    with _as(service, shop.approver) as client:
        read = client.get(shop.url)

    assert read.status_code == 200, read.text
    assert read.json() == {
        "store_id": str(shop.store_id),
        "action": "SEND_MESSAGE",
        "resource_type": "MESSAGE_DRAFT",
        "resource_id": str(shop.draft.agent_run_id),
        "resource_version": 1,
        "text": DRAFT_TEXT,
        # The opaque contact binding, as `ManualSendResponse` already returns it. Nothing else
        # about the customer exists on a draft to disclose.
        "recipient_binding_id": str(shop.draft.contact_binding_id),
        "snapshot_hash": expected.snapshot_hash,
        "rendered_hash": expected.rendered_hash,
        "policy_version": "manual-send-policy-v1",
    }


def test_the_operator_who_raises_the_envelope_may_read_it_too(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    with _as(service, shop.operator) as client:
        read = client.get(shop.url)
    assert read.status_code == 200, read.text
    assert read.json()["text"] == DRAFT_TEXT


def test_another_store_and_an_unknown_store_are_one_opaque_403(
    connection: Any, service: OperationsService
) -> None:
    ours, theirs = _Shop(connection), _Shop(connection)

    with _as(service, ours.approver) as client:
        foreign = client.get(theirs.url)
        unknown = client.get(binding_url(uuid4(), theirs.draft.agent_run_id))
        unknown_own_draft = client.get(binding_url(uuid4(), ours.draft.agent_run_id))

    for refused in (foreign, unknown, unknown_own_draft):
        assert refused.status_code == 403
        assert refused.json() == {"detail": "operation denied"}
    assert DRAFT_TEXT not in foreign.text


def test_a_foreign_a_missing_and_a_rejected_draft_are_one_404(
    connection: Any, service: OperationsService
) -> None:
    ours, theirs = _Shop(connection), _Shop(connection)
    rejected = seed_message_draft(connection, ours.store_id)
    ShadowConsoleRepository().decide_draft(
        connection,
        agent_run_id=rejected.agent_run_id,
        decision="REJECT",
        principal=ours.approver,
        correlation_id=uuid4(),
        reason_code="TONE_NOT_APPROPRIATE",
    )

    with _as(service, ours.approver) as client:
        answers = [
            client.get(binding_url(ours.store_id, draft_id))
            for draft_id in (theirs.draft.agent_run_id, uuid4(), rejected.agent_run_id)
        ]

    for answer in answers:
        assert answer.status_code == 404
        assert answer.json() == {"detail": "no sendable message draft"}


@pytest.mark.parametrize(
    ("role", "mfa"),
    [(StaffRole.AUDITOR, True), (StaffRole.OPERATOR, False), (StaffRole.DRIVER, True)],
    ids=["auditor", "operator-without-mfa", "driver"],
)
def test_a_member_without_a_part_in_a_send_is_refused(
    connection: Any, service: OperationsService, role: StaffRole, mfa: bool
) -> None:
    shop = _Shop(connection)
    member = _staff(role, mfa=mfa)
    shop.add(connection, member)

    with _as(service, member) as client:
        refused = client.get(shop.url)

    assert refused.status_code == 403
    assert DRAFT_TEXT not in refused.text


# --- the read is what the envelope binds -------------------------------------------------------


def test_an_envelope_raised_from_the_read_is_listed_decided_and_locked_with_the_same_digests(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    approval_id = _raise_envelope(service, shop, shop.operator)

    with _as(service, shop.approver) as client:
        read = client.get(shop.url).json()
        item = _queued(client, approval_id)
        # The queue carries the envelope's own store, which is what the console reads through.
        assert item["store_id"] == str(shop.store_id)
        assert item["action"] == "SEND_MESSAGE"
        assert item["resource_type"] == "MESSAGE_DRAFT"
        assert item["resource_id"] == read["resource_id"]
        assert item["resource_version"] == read["resource_version"]
        assert item["snapshot_hash"] == read["snapshot_hash"]
        assert item["rendered_hash"] == read["rendered_hash"]
        decided = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json=_decision(item, "APPROVED"),
            headers=_headers(f"d-{uuid4().hex}"),
        )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "APPROVED"

    # Still a human manual send, and still to the draft's own recipient: nothing here sends.
    with _as(service, shop.sender) as client:
        prepared = client.post(
            f"/internal/v1/approvals/{approval_id}/manual-send",
            json={
                "observed_resource_version": read["resource_version"],
                "observed_snapshot_hash": read["snapshot_hash"],
                "observed_rendered_hash": read["rendered_hash"],
                "channel": "INTERNAL_TEST",
            },
            headers=_headers(f"m-{uuid4().hex}"),
        )
    assert prepared.status_code == 201, prepared.text
    assert prepared.json()["recipient_binding_id"] == read["recipient_binding_id"]
    assert prepared.json()["rendered_hash"] == read["rendered_hash"]


def test_the_person_who_raised_the_envelope_cannot_approve_it(
    connection: Any, service: OperationsService
) -> None:
    """Maker-checker is unchanged: an approver who raises a send is not its approver."""
    shop = _Shop(connection)
    approval_id = _raise_envelope(service, shop, shop.approver)

    with _as(service, shop.approver) as client:
        item = _queued(client, approval_id)
        refused = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json=_decision(item, "APPROVED"),
            headers=_headers(f"d-{uuid4().hex}"),
        )
    assert refused.status_code == 403
    assert refused.json() == {"detail": "operation denied"}


def test_a_draft_edited_after_the_envelope_was_raised_reads_as_changed_and_is_refused(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    approval_id = _raise_envelope(service, shop, shop.operator)
    shop.review(connection, "EDIT", edited_text=EDITED)

    with _as(service, shop.approver) as client:
        item = _queued(client, approval_id)
        read = client.get(shop.url).json()
        # What the console compares: the draft now says something the envelope does not bind.
        assert read["text"] == EDITED
        assert read["resource_version"] == 2
        assert read["resource_version"] != item["resource_version"]
        assert read["rendered_hash"] != item["rendered_hash"]
        assert read["snapshot_hash"] != item["snapshot_hash"]

        approved = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json=_decision(item, "APPROVED"),
            headers=_headers(f"d-{uuid4().hex}"),
        )
        assert approved.status_code == 409
        assert approved.json()["detail"].startswith("RESOURCE_CHANGED_SINCE_REQUEST:")

        refused = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json=_decision(item, "REJECTED"),
            headers=_headers(f"d-{uuid4().hex}"),
        )
    assert refused.status_code == 200, refused.text
    assert refused.json()["status"] == "REJECTED"


def test_a_draft_rejected_after_the_envelope_was_raised_is_gone_and_cannot_be_approved(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    approval_id = _raise_envelope(service, shop, shop.operator)
    shop.review(connection, "REJECT", reason_code="TONE_NOT_APPROPRIATE")

    with _as(service, shop.approver) as client:
        item = _queued(client, approval_id)
        gone = client.get(shop.url)
        approved = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json=_decision(item, "APPROVED"),
            headers=_headers(f"d-{uuid4().hex}"),
        )
    assert gone.status_code == 404
    assert approved.status_code == 409
    assert approved.json()["detail"].startswith("RESOURCE_CHANGED_SINCE_REQUEST:")
