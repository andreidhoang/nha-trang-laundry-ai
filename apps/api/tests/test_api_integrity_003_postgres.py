"""`API-INTEGRITY-003` at the HTTP boundary, against PostgreSQL.

The staging review (`docs/STAGING_READINESS_2026-09.md`) recorded two follow-ups as not done:

1. **A timed-out statement answered 500.** The bounds exist (`OPS-HARDENING-002`); when one fired
   the counter was told "Máy chủ gặp lỗi. Đừng thử lại". It now answers 503 with `Retry-After` and
   `DATABASE_BUSY`. The claim that makes "thử lại" honest is atomicity, so it is measured here
   rather than asserted: a decision is made to stall on its *last* write -- the outbox row, after
   the state change, the decision row, the domain event and the audit row are already written in
   the same transaction -- until the bound fires. Afterwards none of those rows exists, and the
   same request under the same `Idempotency-Key` records the decision exactly once.
2. **Decision-time approval checks compared against the hash the client echoes.** An approver who
   submits the stored binding exactly -- read from the queue, the way the console does it -- is
   now refused when the quote behind the envelope has been re-priced since it was raised.
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
from nha_trang_laundry_api.main import (
    DATABASE_BUSY_RETRY_AFTER_SECONDS,
    app,
    current_principal,
    get_operations_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteRevisionCommand
from nha_trang_laundry_db.stores import StoreRepository

# The real draft and quote fixtures live beside the repository tests, as for API-INTEGRITY-002.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from message_draft_test_data import SeededDraft, current_binding, seed_message_draft
from quote_test_data import PRICED_AT, make_quote_snapshot

ORIGIN = "http://testserver"
CSRF = "r" * 40
NOW = datetime.now(UTC)
RENDERED = "JCS-SHA256-V1:" + "e" * 64


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


def _staff(role: StaffRole) -> StaffPrincipal:
    return StaffPrincipal(uuid4(), f"integrity-3-{uuid4().hex}", frozenset({role}), True, uuid4())


class _Shop:
    """A store with an operator who raises envelopes, an approver who decides them, and a draft."""

    def __init__(self, connection: Any) -> None:
        self.operator = _staff(StaffRole.OPERATOR)
        self.approver = _staff(StaffRole.OPS_APPROVER)
        self.store_id = uuid4()
        StoreRepository.create(
            connection,
            store_id=self.store_id,
            name="Cửa hàng",
            created_by=None,
            correlation_id=uuid4(),
        )
        with connection.cursor() as cursor:
            for member in (self.operator, self.approver):
                cursor.execute(
                    """
                    INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                    VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
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
        self.draft: SeededDraft = seed_message_draft(connection, self.store_id)


@contextmanager
def _as(service: OperationsService, principal: StaffPrincipal) -> Iterator[TestClient]:
    app.dependency_overrides[current_principal] = lambda: principal
    app.dependency_overrides[get_operations_service] = lambda: service
    try:
        with TestClient(
            app,
            cookies={"staff_session": "t", "staff_csrf": CSRF},
            raise_server_exceptions=False,
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _headers(key: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": key}


def _raise(service: OperationsService, shop: _Shop, body: dict[str, Any]) -> UUID:
    with _as(service, shop.operator) as client:
        created = client.post(
            "/internal/v1/approvals",
            json={"store_id": str(shop.store_id), **body},
            headers=_headers(f"raise-{uuid4().hex}"),
        )
    assert created.status_code == 201, created.text
    return UUID(created.json()["approval_request_id"])


def _queued(service: OperationsService, shop: _Shop, approval_id: UUID) -> dict[str, Any]:
    """The envelope as the approvals queue hands it to the console -- the binding to echo back."""
    with _as(service, shop.approver) as client:
        listed = client.get("/internal/v1/approvals")
    assert listed.status_code == 200, listed.text
    items: list[dict[str, Any]] = [
        row for row in listed.json() if row["approval_request_id"] == str(approval_id)
    ]
    assert len(items) == 1, items
    return items[0]


def _decision(item: dict[str, Any], decision: str = "APPROVED") -> dict[str, Any]:
    return {
        "decision": decision,
        "reason_code": "HUMAN_REVIEW_COMPLETE",
        "resource_version": item["resource_version"],
        "snapshot_hash": item["snapshot_hash"],
        "rendered_hash": item["rendered_hash"],
    }


def _count(connection: Any, statement: str, *values: Any) -> int:
    with connection.cursor() as cursor:
        cursor.execute(statement, values)
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _decision_footprint(
    connection: Any, approval_id: UUID, approver: StaffPrincipal, key: str
) -> dict[str, Any]:
    """Every row a decision writes, counted, plus the state it leaves the envelope in."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM approval_request_states WHERE approval_request_id = %s",
            (approval_id,),
        )
        state = cursor.fetchone()
    return {
        "status": None if state is None else state[0],
        "decisions": _count(
            connection,
            "SELECT count(*) FROM approval_decisions WHERE approval_request_id = %s",
            approval_id,
        ),
        "events": _count(
            connection,
            "SELECT count(*) FROM domain_events WHERE aggregate_id = %s "
            "AND event_type = 'APPROVAL_DECIDED'",
            approval_id,
        ),
        "audit": _count(
            connection,
            "SELECT count(*) FROM audit_events WHERE aggregate_id = %s "
            "AND action = 'APPROVAL_DECIDE'",
            approval_id,
        ),
        "outbox": _count(
            connection,
            "SELECT count(*) FROM outbox_events WHERE idempotency_key = %s",
            f"approval:{approval_id}:decision",
        ),
        "idempotency": _count(
            connection,
            "SELECT count(*) FROM command_idempotency_records "
            "WHERE scope = %s AND idempotency_key = %s",
            f"staff-approval-decision:{approver.staff_user_id}",
            key,
        ),
    }


# --- 1. A timed-out statement: 503, nothing half-written, and a safe same-key retry --------------


@pytest.mark.parametrize(
    "bounds",
    [
        {"DATABASE_LOCK_TIMEOUT_MS": "300"},
        # Statement bound first: a lock wait is a statement too, and this one outruns 300 ms.
        {"DATABASE_STATEMENT_TIMEOUT_MS": "300", "DATABASE_LOCK_TIMEOUT_MS": "10000"},
    ],
    ids=["lock_timeout", "statement_timeout"],
)
def test_a_decision_that_times_out_answers_503_leaves_nothing_and_retries_once(
    connection: Any,
    service: OperationsService,
    monkeypatch: pytest.MonkeyPatch,
    bounds: dict[str, str],
) -> None:
    shop = _Shop(connection)
    binding = current_binding(connection, shop.draft.agent_run_id)
    approval_id = _raise(
        service,
        shop,
        {
            "action": "SEND_MESSAGE",
            "resource_type": "MESSAGE_DRAFT",
            "resource_id": str(shop.draft.agent_run_id),
            "resource_version": binding.resource_version,
            "snapshot_hash": binding.snapshot_hash,
            "rendered_hash": binding.rendered_hash,
            "policy_version": "manual-send-policy-v1",
        },
    )
    item = _queued(service, shop, approval_id)
    key = f"decide-{uuid4().hex}"
    for name, value in bounds.items():
        monkeypatch.setenv(name, value)

    # `SHARE` lets every read through and stops every insert into the outbox -- the decision's last
    # write, so the state change, the decision row, the event and the audit row all precede it.
    with psycopg.connect(_database_url()) as blocker:
        blocker.execute("LOCK TABLE outbox_events IN SHARE MODE")
        try:
            with _as(service, shop.approver) as client:
                timed_out = client.post(
                    f"/internal/v1/approvals/{approval_id}/decisions",
                    json=_decision(item),
                    headers=_headers(key),
                )
        finally:
            blocker.rollback()

    assert timed_out.status_code == 503, timed_out.text
    assert timed_out.json() == {"detail": {"reason_code": "DATABASE_BUSY"}}
    assert timed_out.headers["Retry-After"] == str(DATABASE_BUSY_RETRY_AFTER_SECONDS)
    # Invariant 5: the transaction rolled back whole. Not one of the rows it had already written
    # survived, and neither did the idempotency claim -- so the retry below runs, not replays.
    assert _decision_footprint(connection, approval_id, shop.approver, key) == {
        "status": "REQUESTED",
        "decisions": 0,
        "events": 0,
        "audit": 0,
        "outbox": 0,
        "idempotency": 0,
    }

    with _as(service, shop.approver) as client:
        retried = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json=_decision(item),
            headers=_headers(key),
        )
        replayed = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json=_decision(item),
            headers=_headers(key),
        )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "APPROVED"
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["replayed"] is True
    assert _decision_footprint(connection, approval_id, shop.approver, key) == {
        "status": "APPROVED",
        "decisions": 1,
        "events": 1,
        "audit": 1,
        "outbox": 1,
        "idempotency": 1,
    }


# --- 2. The decision re-resolves the resource; the echoed hashes are not enough ------------------


def test_approving_a_quote_re_priced_after_the_envelope_is_refused_over_http(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    quote_id, order_request_id = uuid4(), uuid4()
    first = make_quote_snapshot(quote_id, 1, 100_000)
    repository = QuoteRepository()
    repository.create_revision(
        connection,
        QuoteRevisionCommand(
            shop.store_id,
            order_request_id,
            first,
            0,
            0,
            shop.operator.staff_user_id,
            uuid4(),
            PRICED_AT,
        ),
    )
    approval_id = _raise(
        service,
        shop,
        {
            "action": "PRESENT_QUOTE",
            "resource_type": "QUOTE_REVISION",
            "resource_id": str(quote_id),
            "resource_version": 1,
            "snapshot_hash": first.document.snapshot_hash,
            "rendered_hash": RENDERED,
            "policy_version": "quote-presentation-v1",
        },
    )
    item = _queued(service, shop, approval_id)
    assert item["snapshot_hash"] == first.document.snapshot_hash

    # Re-priced at the counter: revision 2 at a different amount, and the quote now offers it. The
    # envelope, and the binding the queue hands the approver, still name revision 1 -- correctly:
    # that is what was raised.
    repository.create_revision(
        connection,
        QuoteRevisionCommand(
            shop.store_id,
            order_request_id,
            make_quote_snapshot(quote_id, 2, 150_000),
            1,
            1,
            shop.operator.staff_user_id,
            uuid4(),
            PRICED_AT,
        ),
    )

    key = f"decide-{uuid4().hex}"
    with _as(service, shop.approver) as client:
        refused = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json=_decision(item),
            headers=_headers(key),
        )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"].startswith("RESOURCE_CHANGED_SINCE_REQUEST:")
    assert _decision_footprint(connection, approval_id, shop.approver, key) == {
        "status": "REQUESTED",
        "decisions": 0,
        "events": 0,
        "audit": 0,
        "outbox": 0,
        "idempotency": 0,
    }

    # Rejecting it is still possible: a rejection authorises nothing, and the owner must be able to
    # clear an envelope whose quote has moved on rather than wait for it to expire.
    with _as(service, shop.approver) as client:
        rejected = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json=_decision(item, "REJECTED"),
            headers=_headers(f"reject-{uuid4().hex}"),
        )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"
