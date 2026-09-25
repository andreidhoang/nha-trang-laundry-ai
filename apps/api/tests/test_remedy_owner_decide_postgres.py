"""`REMEDY-OWNER-DECIDE-001` over HTTP against real PostgreSQL: an owner remedy, end to end.

Since `DEC-031` every loss claim, every compensation on a refunded order and anything above the
staff limit waits on an `APPROVE_REMEDY` envelope only the owner may decide. Before this item the
approvals card could show the owner nothing about one, so none could be decided from the console,
and the remedies screen could execute only a proposal its own browser session had sent -- so an
approval given hours later could not be paid from the console either.

Three things are proved here. The owner's read serves exactly the binding the decision is checked
against, to the owner and nobody else, and says so when the proposal no longer matches its
envelope. The incident's proposal list says, per row, what the counter may do next. And the whole
path runs on nothing but server reads: an operator proposes a 50.000 d loss, the owner reads and
approves it, and the operator pays it from the list -- once, with a replay and a refusal after.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator
from datetime import datetime, timedelta
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
from nha_trang_laundry_db.remedy_reads import RemedyReadRepository
from nha_trang_laundry_domain.remedies import RemedyKind

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_remedies import DAMAGE_CEILING, NOW, _approve, _propose, _shop, _staff

DENIED = {"detail": "operation denied"}
MISSING = {"detail": "remedy approval unavailable"}
#: The published `DEC-004` staff limit, spelled out so a change to it has to be argued for here.
STAFF_LIMIT = 100_000


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    """Autocommit: `OperationsService` opens its own connection and sees only committed rows."""

    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = AuthSettings(database_url=_database_url())
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _binding_path(store_id: UUID, proposal_id: UUID) -> str:
    return f"/internal/v1/stores/{store_id}/remedy-proposals/{proposal_id}/approval-binding"


def _envelope(connection: Any, approval_id: UUID) -> dict[str, object]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT resource_id, resource_version, snapshot_hash, rendered_hash, expires_at
            FROM approval_requests WHERE id = %s
            """,
            (approval_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return {
        "resource_id": str(row[0]),
        "resource_version": int(row[1]),
        "snapshot_hash": str(row[2]),
        "rendered_hash": str(row[3]),
        "expires_at": row[4],
    }


def _loss(connection: Any, amount_vnd: int = 50_000) -> tuple[UUID, StaffPrincipal, Any, UUID]:
    store_id, operator, _, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        operator,
        kind=RemedyKind.LOST_ITEM,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=amount_vnd,
    )
    assert proposal.approval_id is not None
    return store_id, operator, proposal, incident_id


# --- 1. The owner's read ------------------------------------------------------------------------


def test_the_owner_reads_exactly_the_binding_the_decision_checks(
    connection: Any, client: TestClient
) -> None:
    store_id, operator, proposal, incident_id = _loss(connection)
    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    envelope = _envelope(connection, proposal.approval_id)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.ticket_number, t.issued_on FROM orders o
            JOIN counter_tickets t ON t.id = o.bound_contact_id
            WHERE o.id = %s
            """,
            (proposal.order_id,),
        )
        ticket = cursor.fetchone()
    assert ticket is not None

    _as(owner)
    response = client.get(_binding_path(store_id, proposal.proposal_id))

    assert response.status_code == 200, response.text
    body = response.json()
    # The binding, value for value -- the three the decision is re-checked against.
    assert body["action"] == "APPROVE_REMEDY"
    assert body["resource_type"] == "REMEDY_PROPOSAL"
    assert body["resource_id"] == envelope["resource_id"] == str(proposal.proposal_id)
    assert body["resource_version"] == envelope["resource_version"] == 1
    assert body["snapshot_hash"] == envelope["snapshot_hash"]
    assert body["rendered_hash"] == envelope["rendered_hash"] == proposal.proposal_hash
    assert body["envelope_matches"] is True
    assert body["approval_id"] == str(proposal.approval_id)
    assert body["approval_status"] == "REQUESTED"
    assert body["approval_lapsed"] is False
    assert body["next_step"] == "AWAIT_OWNER"
    # What the owner reads before deciding: stored figures, none computed here.
    assert body["store_id"] == str(store_id)
    assert body["kind"] == "LOST_ITEM"
    assert body["status"] == "OWNER_APPROVAL_REQUIRED"
    assert body["amount_vnd"] == 50_000
    assert body["ceiling_vnd"] == DAMAGE_CEILING
    assert body["staff_approval_ceiling_vnd"] == STAFF_LIMIT
    # Why the owner: the domain's own reason, as recorded with the proposal. 50.000 d is inside
    # the staff limit, so "above the limit" would be a guess and a wrong one.
    assert body["owner_reasons"] == ["LOSS_CLAIM"]
    assert body["order_id"] == str(proposal.order_id)
    assert body["incident_id"] == str(incident_id)
    assert body["ticket_number"] == ticket[0]
    assert body["ticket_issued_on"] == ticket[1].isoformat()
    assert body["order_line_id"] == "line-1"
    assert body["service_code"] == "TEST_SERVICE"
    assert body["garment_index"] == proposal.garment_index
    assert body["incident_summary"] == "Áo dài bị phai màu sau khi giặt"
    assert body["proposed_by"] == str(operator.staff_user_id)
    assert body["proposed_by_name"] == "Nhân viên"
    assert body["executed_at"] is None
    assert body["credit_id"] is None


def test_above_the_staff_limit_is_the_reason_given_for_a_large_damage_claim(
    connection: Any, client: TestClient
) -> None:
    store_id, operator, _, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        operator,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=150_000,
    )
    _as(_staff(connection, store_id, StaffRole.OWNER_ADMIN))
    body = client.get(_binding_path(store_id, proposal.proposal_id)).json()
    assert body["kind"] == "DAMAGE_COMPENSATION"
    assert body["owner_reasons"] == ["ABOVE_STAFF_LIMIT"]
    assert (body["amount_vnd"], body["staff_approval_ceiling_vnd"]) == (150_000, STAFF_LIMIT)


def test_the_read_is_refused_to_everyone_who_may_not_decide_it(
    connection: Any, client: TestClient
) -> None:
    store_id, operator, proposal, _ = _loss(connection)
    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    approver = _staff(connection, store_id, StaffRole.OPS_APPROVER)
    auditor = _staff(connection, store_id, StaffRole.AUDITOR)
    other_store, _, _, _ = _shop(connection)
    outsider = _staff(connection, other_store, StaffRole.OWNER_ADMIN)
    no_mfa = StaffPrincipal(
        owner.staff_user_id, owner.oidc_subject, owner.roles, False, owner.session_id
    )
    path = _binding_path(store_id, proposal.proposal_id)

    answers = []
    # The proposer's own role, an approver who may not decide an owner action, an auditor, the
    # owner without MFA, and the owner of another shop.
    for principal in (operator, approver, auditor, no_mfa, outsider):
        _as(principal)
        answers.append(client.get(path))
    # A store id that does not exist reads exactly as one the caller is not in.
    _as(owner)
    answers.append(client.get(_binding_path(uuid4(), proposal.proposal_id)))

    assert [answer.status_code for answer in answers] == [403] * 6
    assert all(answer.json() == DENIED for answer in answers)
    # The same owner, rightly placed, is served: the refusals above are about the caller.
    assert client.get(path).status_code == 200


def test_another_stores_proposal_reads_as_missing(connection: Any, client: TestClient) -> None:
    """A member naming another shop's proposal learns nothing: it answers as one never made."""

    store_id, _, _, _ = _loss(connection)
    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    _, _, foreign, _ = _loss(connection)

    _as(owner)
    answers = [
        client.get(_binding_path(store_id, foreign.proposal_id)),
        client.get(_binding_path(store_id, uuid4())),
    ]
    assert [answer.status_code for answer in answers] == [404, 404]
    assert all(answer.json() == MISSING for answer in answers)


def test_a_staff_authorised_proposal_has_no_envelope_to_read(
    connection: Any, client: TestClient
) -> None:
    store_id, operator, _, incident_id = _shop(connection)
    staff_authorised = _propose(
        connection,
        store_id,
        incident_id,
        operator,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=40_000,
    )
    assert staff_authorised.approval_id is None
    _as(_staff(connection, store_id, StaffRole.OWNER_ADMIN))
    response = client.get(_binding_path(store_id, staff_authorised.proposal_id))
    assert response.status_code == 404
    assert response.json() == MISSING


def test_a_proposal_whose_bound_content_moved_reads_as_stale_and_cannot_be_approved(
    connection: Any, client: TestClient
) -> None:
    """The order's quote digest is the envelope's `snapshot_hash`. Moved underneath the envelope,
    the read must say so -- the console then withholds the figures and offers only a refusal --
    and approving with the envelope's own values must be refused by the server."""

    store_id, _, proposal, _ = _loss(connection)
    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    envelope = _envelope(connection, proposal.approval_id)
    moved = "JCS-SHA256-V1:" + "f" * 64
    # No path in this repository re-prices an order after its remedy was proposed, so the change
    # is made directly, inside one transaction that re-arms the order's projection guard before
    # it commits.
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("ALTER TABLE orders DISABLE TRIGGER order_projection_guard")
        cursor.execute(
            "UPDATE orders SET current_quote_snapshot_hash = %s WHERE id = %s",
            (moved, proposal.order_id),
        )
        cursor.execute("ALTER TABLE orders ENABLE TRIGGER order_projection_guard")

    _as(owner)
    body = client.get(_binding_path(store_id, proposal.proposal_id)).json()
    assert body["envelope_matches"] is False
    assert body["snapshot_hash"] == moved != envelope["snapshot_hash"]
    assert body["rendered_hash"] == envelope["rendered_hash"]

    decided = client.post(
        f"/internal/v1/approvals/{proposal.approval_id}/decisions",
        json={
            "decision": "APPROVED",
            "reason_code": "APPROVED_AFTER_CONSOLE_REVIEW",
            "resource_version": envelope["resource_version"],
            "snapshot_hash": envelope["snapshot_hash"],
            "rendered_hash": envelope["rendered_hash"],
        },
        headers={"Idempotency-Key": f"decide-{uuid4()}"},
    )
    assert decided.status_code == 409, decided.text
    # A refusal authorises nothing, so it is still allowed on a stale envelope.
    refused = client.post(
        f"/internal/v1/approvals/{proposal.approval_id}/decisions",
        json={
            "decision": "REJECTED",
            "reason_code": "REJECTED_AFTER_CONSOLE_REVIEW",
            "resource_version": envelope["resource_version"],
            "snapshot_hash": envelope["snapshot_hash"],
            "rendered_hash": envelope["rendered_hash"],
        },
        headers={"Idempotency-Key": f"decide-{uuid4()}"},
    )
    assert refused.status_code == 200, refused.text
    assert refused.json()["status"] == "REJECTED"
    after = client.get(_binding_path(store_id, proposal.proposal_id)).json()
    assert (after["approval_status"], after["next_step"]) == ("REJECTED", "PROPOSE_AGAIN")


# --- 2. The list says what may happen next ------------------------------------------------------


def test_the_proposal_list_says_what_the_counter_may_do_with_each_row(
    connection: Any, client: TestClient
) -> None:
    store_id, operator, _, incident_id = _shop(connection)
    staff_authorised = _propose(
        connection,
        store_id,
        incident_id,
        operator,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=40_000,
    )
    waiting = _propose(
        connection,
        store_id,
        incident_id,
        operator,
        kind=RemedyKind.LOST_ITEM,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=20_000,
    )
    approved = _propose(
        connection,
        store_id,
        incident_id,
        operator,
        kind=RemedyKind.LOST_ITEM,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=10_000,
    )
    _approve(connection, store_id, approved.approval_id, NOW + timedelta(minutes=1))

    _as(operator)
    body = client.get(
        f"/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-proposals"
    ).json()
    steps = {row["proposal_id"]: row["next_step"] for row in body["proposals"]}
    assert steps == {
        str(staff_authorised.proposal_id): "EXECUTE",
        str(waiting.proposal_id): "AWAIT_OWNER",
        str(approved.proposal_id): "EXECUTE",
    }

    # At the envelope's expiry instant -- and not a microsecond before -- neither owner envelope
    # can pay any more, decided or not: `_require_remedy_approval` refuses an approved envelope
    # past its expiry too. The staff-authorised proposal has no envelope and is unaffected.
    expires_at = _envelope(connection, approved.approval_id)["expires_at"]
    assert expires_at == _envelope(connection, waiting.approval_id)["expires_at"]
    assert isinstance(expires_at, datetime)

    def steps_at(now: datetime) -> dict[str, str]:
        with connection.cursor() as cursor:
            read = RemedyReadRepository.list_incident_proposals(
                cursor, store_id=store_id, incident_id=incident_id, principal=operator, now=now
            )
        return {str(row.proposal_id): row.next_step for row in read.proposals}

    assert steps_at(expires_at - timedelta(microseconds=1)) == steps
    assert steps_at(expires_at) == {
        str(staff_authorised.proposal_id): "EXECUTE",
        str(waiting.proposal_id): "PROPOSE_AGAIN",
        str(approved.proposal_id): "PROPOSE_AGAIN",
    }


# --- 3. End to end, on nothing but server reads -------------------------------------------------


def test_an_owner_approved_loss_is_paid_from_the_list_exactly_once(
    connection: Any, client: TestClient
) -> None:
    store_id, operator, _, incident_id = _shop(connection)
    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)

    # The operator proposes a 50.000 d loss through the route.
    _as(operator)
    proposed = client.post(
        f"/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-proposals",
        json={
            "kind": "LOST_ITEM",
            "store_fault_attested": True,
            "order_line_id": "line-1",
            "amount_vnd": 50_000,
        },
        headers={"Idempotency-Key": f"propose-{uuid4()}"},
    )
    assert proposed.status_code == 201, proposed.text
    proposal = proposed.json()
    assert proposal["status"] == "OWNER_APPROVAL_REQUIRED"
    assert proposal["owner_reasons"] == ["LOSS_CLAIM"]
    approval_id = proposal["approval_id"]

    # Before the owner decides, the counter cannot pay it.
    early = client.post(
        f"/internal/v1/remedy-proposals/{proposal['proposal_id']}/execution",
        headers={"Idempotency-Key": f"execute-{uuid4()}"},
    )
    assert early.status_code == 422
    assert early.json()["detail"]["reason_code"] == "REMEDY_APPROVAL_REQUIRED"

    # The owner finds it in the queue, REQUESTED, carrying its own store.
    _as(owner)
    queue = client.get("/internal/v1/approvals").json()
    [row] = [item for item in queue if item["approval_request_id"] == approval_id]
    assert row["status"] == "REQUESTED"
    assert row["resource_type"] == "REMEDY_PROPOSAL"
    assert row["action"] == "APPROVE_REMEDY"
    assert row["store_id"] == str(store_id)
    assert row["resource_id"] == proposal["proposal_id"]

    # The owner reads the binding through the envelope's store and approves with it.
    read = client.get(_binding_path(UUID(row["store_id"]), UUID(row["resource_id"]))).json()
    assert read["envelope_matches"] is True
    assert (read["resource_version"], read["snapshot_hash"], read["rendered_hash"]) == (
        row["resource_version"],
        row["snapshot_hash"],
        row["rendered_hash"],
    )
    decided = client.post(
        f"/internal/v1/approvals/{approval_id}/decisions",
        json={
            "decision": "APPROVED",
            "reason_code": "APPROVED_AFTER_CONSOLE_REVIEW",
            "resource_version": read["resource_version"],
            "snapshot_hash": read["snapshot_hash"],
            "rendered_hash": read["rendered_hash"],
        },
        headers={"Idempotency-Key": f"decide-{uuid4()}"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "APPROVED"

    # Later, with no session state: the operator reads the incident's list and pays from it.
    _as(operator)
    listed = client.get(
        f"/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-proposals"
    ).json()
    [entry] = listed["proposals"]
    assert entry["approval_status"] == "APPROVED"
    assert entry["next_step"] == "EXECUTE"
    key = f"execute-{uuid4()}"
    executed = client.post(
        f"/internal/v1/remedy-proposals/{entry['proposal_id']}/execution",
        headers={"Idempotency-Key": key},
    )
    assert executed.status_code == 201, executed.text
    paid = executed.json()
    assert paid["status"] == "EXECUTED"
    assert paid["event_type"] == "CREDIT_EXECUTED"
    assert paid["amount_vnd"] == 50_000
    assert paid["replayed"] is False
    credit_id = paid["credit_id"]
    assert credit_id is not None

    # A lost answer pressed again replays; a fresh press is refused. Nothing is paid twice.
    replay = client.post(
        f"/internal/v1/remedy-proposals/{entry['proposal_id']}/execution",
        headers={"Idempotency-Key": key},
    )
    assert replay.status_code == 201
    assert replay.json()["replayed"] is True
    assert replay.json()["credit_id"] == credit_id
    again = client.post(
        f"/internal/v1/remedy-proposals/{entry['proposal_id']}/execution",
        headers={"Idempotency-Key": f"execute-{uuid4()}"},
    )
    assert again.status_code == 422
    assert again.json()["detail"]["reason_code"] == "REMEDY_ALREADY_EXECUTED"

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, amount_vnd FROM remedy_credits WHERE remedy_proposal_id = %s",
            (entry["proposal_id"],),
        )
        credits = cursor.fetchall()
        cursor.execute(
            """
            SELECT count(*) FROM domain_events
            WHERE aggregate_type = 'REMEDY_PROPOSAL' AND aggregate_id = %s
              AND event_type = 'CREDIT_EXECUTED'
            """,
            (entry["proposal_id"],),
        )
        events = cursor.fetchone()
    assert [(str(row[0]), int(row[1])) for row in credits] == [(credit_id, 50_000)]
    assert events is not None and events[0] == 1

    after = client.get(
        f"/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-proposals"
    ).json()["proposals"][0]
    assert (after["status"], after["next_step"], after["credit_id"]) == (
        "EXECUTED",
        "NONE",
        credit_id,
    )
    # And the proposer could never have approved it themselves.
    assert operator.staff_user_id != owner.staff_user_id


def test_the_proposer_cannot_approve_their_own_remedy(connection: Any, client: TestClient) -> None:
    """Maker-checker, unchanged: an owner who proposed a loss still cannot approve it."""

    store_id, _, _, incident_id = _shop(connection)
    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        owner,
        kind=RemedyKind.LOST_ITEM,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=30_000,
    )
    _as(owner)
    read = client.get(_binding_path(store_id, proposal.proposal_id)).json()
    decided = client.post(
        f"/internal/v1/approvals/{proposal.approval_id}/decisions",
        json={
            "decision": "APPROVED",
            "reason_code": "APPROVED_AFTER_CONSOLE_REVIEW",
            "resource_version": read["resource_version"],
            "snapshot_hash": read["snapshot_hash"],
            "rendered_hash": read["rendered_hash"],
        },
        headers={"Idempotency-Key": f"decide-{uuid4()}"},
    )
    assert decided.status_code == 403
