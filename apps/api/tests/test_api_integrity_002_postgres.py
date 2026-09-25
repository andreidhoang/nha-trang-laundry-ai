"""`API-INTEGRITY-002` at the HTTP boundary, against PostgreSQL.

Two findings from the API reviewer, each driven through the routes a console or a curious staff
member would actually call:

1. a manual send's recipient came from the request body rather than from the approved draft, and a
   `SEND_MESSAGE` envelope could be raised over any UUID with invented digests;
2. the unknown-send queue listed and resolved every store's receipts for anyone holding a role, and
   its reconcile route took no `Idempotency-Key`.
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
from nha_trang_laundry_contracts.channel_envelope import (
    ChannelDeliveryStatus,
    ChannelMessageKind,
    ChannelOutboundReceipt,
    ChannelProvider,
    ReconciliationState,
    SendAttempt,
    SendAttemptOutcome,
    SendAuthorization,
    SendAuthorizationSource,
)
from nha_trang_laundry_contracts.runtime_registry import ReleaseCapability
from nha_trang_laundry_db.channel import ChannelSendReceiptRepository
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.stores import StoreRepository

# See `test_ops_board_postgres.py`: the real draft fixture lives beside the repository tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from message_draft_test_data import (
    SeededDraft,
    current_binding,
    grant_service_basis,
    seed_message_draft,
)

ORIGIN = "http://testserver"
CSRF = "q" * 40
NOW = datetime.now(UTC)
FORGED = "JCS-SHA256-V1:" + "f" * 64


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
    return StaffPrincipal(uuid4(), f"integrity-{uuid4().hex}", frozenset({role}), mfa, uuid4())


class _Shop:
    """A store with an operator, an approver, a sender and one agent draft."""

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
        # DEC-033: a manual send needs a published policy and a basis; the customer wrote.
        grant_service_basis(connection, self.draft.contact_binding_id, received_at=NOW)

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

    def unknown_send(self, connection: Any) -> UUID:
        receipt = ChannelOutboundReceipt(
            receipt_id=uuid4(),
            outbox_id=uuid4(),
            idempotency_key=f"outbox-{uuid4().hex}",
            provider=ChannelProvider.TELEGRAM_SANDBOX,
            message_kind=ChannelMessageKind.INTAKE_RECEIPT,
            authorization=SendAuthorization(
                source=SendAuthorizationSource.CAPABILITY_AUTHORIZED,
                capability=ReleaseCapability.INTAKE_RECEIPT,
            ),
            attempt=SendAttempt(
                attempt_number=1, started_at=NOW, outcome=SendAttemptOutcome.TIMEOUT
            ),
            delivery_status=ChannelDeliveryStatus.FAILED,
            reconciliation_state=ReconciliationState.UNKNOWN_REQUIRES_HUMAN,
        )
        ChannelSendReceiptRepository().record_attempt(
            connection, receipt, store_id=self.store_id, correlation_id=uuid4()
        )
        return receipt.receipt_id


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


def _approval_body(shop: _Shop, resource_id: UUID, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "store_id": str(shop.store_id),
        "action": "SEND_MESSAGE",
        "resource_type": "MESSAGE_DRAFT",
        "resource_id": str(resource_id),
        "resource_version": 1,
        "snapshot_hash": FORGED,
        "rendered_hash": FORGED,
        "policy_version": "manual-send-policy-v1",
    }
    body.update(overrides)
    return body


def _approved(connection: Any, service: OperationsService, shop: _Shop) -> tuple[UUID, Any]:
    binding = current_binding(connection, shop.draft.agent_run_id)
    body = _approval_body(
        shop,
        shop.draft.agent_run_id,
        resource_version=binding.resource_version,
        snapshot_hash=binding.snapshot_hash,
        rendered_hash=binding.rendered_hash,
    )
    with _as(service, shop.operator) as client:
        created = client.post(
            "/internal/v1/approvals", json=body, headers=_headers(f"a-{uuid4().hex}")
        )
    assert created.status_code == 201, created.text
    approval_id = UUID(created.json()["approval_request_id"])
    with _as(service, shop.approver) as client:
        decided = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json={
                "decision": "APPROVED",
                "reason_code": "HUMAN_REVIEW_COMPLETE",
                "resource_version": binding.resource_version,
                "snapshot_hash": binding.snapshot_hash,
                "rendered_hash": binding.rendered_hash,
            },
            headers=_headers(f"d-{uuid4().hex}"),
        )
    assert decided.status_code == 200, decided.text
    return approval_id, binding


def _count(connection: Any, statement: str, *values: Any) -> int:
    with connection.cursor() as cursor:
        cursor.execute(statement, values)
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


# --- finding 1: the envelope binds real content ---------------------------------------------


def test_an_envelope_over_a_draft_that_does_not_exist_is_refused(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    with _as(service, shop.operator) as client:
        refused = client.post(
            "/internal/v1/approvals",
            json=_approval_body(shop, uuid4()),
            headers=_headers(f"a-{uuid4().hex}"),
        )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "the approval names a resource this store does not have"


def test_an_envelope_with_a_made_up_hash_over_a_real_draft_is_refused(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    binding = current_binding(connection, shop.draft.agent_run_id)
    with _as(service, shop.operator) as client:
        forged_rendering = client.post(
            "/internal/v1/approvals",
            json=_approval_body(shop, shop.draft.agent_run_id, snapshot_hash=binding.snapshot_hash),
            headers=_headers(f"a-{uuid4().hex}"),
        )
        forged_snapshot = client.post(
            "/internal/v1/approvals",
            json=_approval_body(shop, shop.draft.agent_run_id, rendered_hash=binding.rendered_hash),
            headers=_headers(f"a-{uuid4().hex}"),
        )
    for refused in (forged_rendering, forged_snapshot):
        assert refused.status_code == 409
        assert refused.json()["detail"] == (
            "the approval names a content digest this resource does not have"
        )
    assert (
        _count(
            connection,
            "SELECT count(*) FROM approval_requests WHERE resource_id = %s",
            shop.draft.agent_run_id,
        )
        == 0
    )


# --- finding 1: the recipient is the draft's -----------------------------------------------


def test_a_caller_chosen_recipient_is_refused_and_nothing_is_locked(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    approval_id, binding = _approved(connection, service, shop)
    body = {
        "observed_resource_version": binding.resource_version,
        "observed_snapshot_hash": binding.snapshot_hash,
        "observed_rendered_hash": binding.rendered_hash,
        "channel": "INTERNAL_TEST",
    }
    with _as(service, shop.sender) as client:
        # Even naming the draft's own recipient is refused: the field is not the caller's to send.
        for recipient in (uuid4(), shop.draft.contact_binding_id):
            refused = client.post(
                f"/internal/v1/approvals/{approval_id}/manual-send",
                json={**body, "recipient_binding_id": str(recipient)},
                headers=_headers(f"m-{uuid4().hex}"),
            )
            assert refused.status_code == 422, refused.text
    assert (
        _count(
            connection,
            "SELECT count(*) FROM manual_send_envelopes WHERE approval_request_id = %s",
            approval_id,
        )
        == 0
    )

    # Positive control: the same request without the field locks the draft's own recipient.
    with _as(service, shop.sender) as client:
        prepared = client.post(
            f"/internal/v1/approvals/{approval_id}/manual-send",
            json=body,
            headers=_headers(f"m-{uuid4().hex}"),
        )
    assert prepared.status_code == 201, prepared.text
    assert prepared.json()["recipient_binding_id"] == str(shop.draft.contact_binding_id)


# --- finding 2: the unknown-send queue is the store's --------------------------------------


def test_the_queue_is_listed_per_store_and_another_stores_receipt_is_invisible(
    connection: Any, service: OperationsService
) -> None:
    ours, theirs = _Shop(connection), _Shop(connection)
    mine, other = ours.unknown_send(connection), theirs.unknown_send(connection)

    with _as(service, ours.approver) as client:
        listed = client.get(f"/internal/v1/stores/{ours.store_id}/shadow/unknown-sends")
        foreign = client.get(f"/internal/v1/stores/{theirs.store_id}/shadow/unknown-sends")
        global_route = client.get("/internal/v1/shadow/unknown-sends")
    assert listed.status_code == 200
    assert [item["receipt_id"] for item in listed.json()] == [str(mine)]
    assert str(other) not in listed.text
    assert foreign.status_code == 403
    assert foreign.json() == {"detail": "shadow access denied"}
    # The unscoped route is gone rather than left beside the scoped one.
    assert global_route.status_code in {404, 405}


def test_listing_requires_mfa(connection: Any, service: OperationsService) -> None:
    shop = _Shop(connection)
    unverified = _staff(StaffRole.OPERATOR, mfa=False)
    shop.add(connection, unverified)
    shop.unknown_send(connection)

    with _as(service, unverified) as client:
        refused = client.get(f"/internal/v1/stores/{shop.store_id}/shadow/unknown-sends")
    assert refused.status_code == 403


def test_another_stores_receipt_cannot_be_resolved_and_the_refusal_is_opaque(
    connection: Any, service: OperationsService
) -> None:
    ours, theirs = _Shop(connection), _Shop(connection)
    receipt_id = ours.unknown_send(connection)
    body = {"resolution": "CONFIRMED_NOT_SENT"}

    with _as(service, theirs.approver) as client:
        foreign = client.post(
            f"/internal/v1/shadow/unknown-sends/{receipt_id}/reconcile",
            json=body,
            headers=_headers(f"r-{uuid4().hex}"),
        )
        missing = client.post(
            f"/internal/v1/shadow/unknown-sends/{uuid4()}/reconcile",
            json=body,
            headers=_headers(f"r-{uuid4().hex}"),
        )
    assert foreign.status_code == missing.status_code == 403
    assert foreign.json() == missing.json() == {"detail": "shadow access denied"}
    assert (
        _count(
            connection,
            "SELECT count(*) FROM channel_send_receipts WHERE receipt_id = %s "
            "AND reconciliation_state = 'UNKNOWN_REQUIRES_HUMAN'",
            receipt_id,
        )
        == 1
    )


def test_reconcile_requires_an_idempotency_key(connection: Any, service: OperationsService) -> None:
    shop = _Shop(connection)
    receipt_id = shop.unknown_send(connection)

    with _as(service, shop.approver) as client:
        refused = client.post(
            f"/internal/v1/shadow/unknown-sends/{receipt_id}/reconcile",
            json={"resolution": "CONFIRMED_SENT"},
            headers=_headers(),
        )
    assert refused.status_code == 422
    assert "Idempotency-Key" in refused.text


def test_a_replayed_reconcile_returns_the_first_answer_and_does_not_apply_twice(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    receipt_id = shop.unknown_send(connection)
    url = f"/internal/v1/shadow/unknown-sends/{receipt_id}/reconcile"
    key = f"r-{uuid4().hex}"
    body = {"resolution": "CONFIRMED_SENT", "note": "Đã thấy tin trên Zalo OA."}

    with _as(service, shop.approver) as client:
        first = client.post(url, json=body, headers=_headers(key))
        replay = client.post(url, json=body, headers=_headers(key))
        changed = client.post(url, json={"resolution": "CONFIRMED_NOT_SENT"}, headers=_headers(key))
        fresh_key = client.post(url, json=body, headers=_headers(f"r-{uuid4().hex}"))

    assert first.status_code == 204
    assert replay.status_code == 204
    assert changed.status_code == 409
    assert changed.json() == {"detail": "IDEMPOTENCY_CONFLICT"}
    # A new key is a new command, and the receipt is no longer awaiting one.
    assert fresh_key.status_code == 409
    for table in ("audit_events", "domain_events"):
        column = "action" if table == "audit_events" else "event_type"
        value = "CHANNEL_SEND_RECONCILE" if table == "audit_events" else "CHANNEL_SEND_RECONCILED"
        assert (
            _count(
                connection,
                f"SELECT count(*) FROM {table} WHERE aggregate_id = %s AND {column} = %s",
                receipt_id,
                value,
            )
            == 1
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT reconciliation_state, resolution_actor_id FROM channel_send_receipts "
            "WHERE receipt_id = %s",
            (receipt_id,),
        )
        assert cursor.fetchone() == ("CONFIRMED_SENT", str(shop.approver.staff_user_id))
