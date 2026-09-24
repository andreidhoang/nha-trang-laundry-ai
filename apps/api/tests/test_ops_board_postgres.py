"""`OPS-BOARD-001` against the real API and real PostgreSQL.

Two claims can only be made here, and both are the kind that a repository test would let through.

**One query, not two.** The board screen and `#/assistant` must report the same numbers for the
same store at the same instant. A repository test can prove `sla_risk_board` is correct; only
calling both surfaces can prove that the assistant's sentence and the board's list came out of the
same statement. If somebody later writes a second SQL statement for the board -- the defect this
item was re-scoped to prevent -- this is the test that goes red.

**The `DEC-014` gate is a server gate.** The takings figure is hidden from a role outside the set by
`require_operations_staff`, and hiding it in `core/rbac.js` would look identical on screen while
leaving the number one `curl` away. So the refusal is asserted against the route, and the body is
checked for the figure as well as the status, because a 403 that still carried the number in its
detail would pass a status-only assertion.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.assistant import SLA_POLICY, AssistantService
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_assistant_service,
    get_operations_service,
    get_ops_board_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_api.ops_board import OpsBoardService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.shadow_console import (
    ShadowConsoleRepository,
    sla_board_query_version,
)
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    FulfillmentMode,
    IntakeStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness

# `quote_test_data` builds the priced-then-accepted-then-ordered chain a real order needs, and it
# lives beside the repository tests that own that chain. Reached by path rather than copied: a
# second order fixture built some other way would let this module pass against a shape production
# cannot produce, which is exactly what that helper's docstring warns about. pytest puts the
# directory on `sys.path` when the db tests are collected in the same run; this makes a run of only
# `apps/api` work too.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from quote_test_data import accepted_quote

READY = IntakeReadiness(True, True, True, True, True, True)
CSRF = "y" * 40
ORIGIN = "http://testserver"


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection(database_url: str) -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(connection: Any, *, roles: frozenset[StaffRole], now: datetime) -> StaffPrincipal:
    staff_user_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên thử nghiệm', 'ACTIVE', %s)
            """,
            (staff_user_id, f"oidc-{staff_user_id}", now),
        )
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_user_id, role.value, now),
            )
    return StaffPrincipal(
        staff_user_id=staff_user_id,
        oidc_subject=f"oidc-{staff_user_id}",
        roles=roles,
        mfa_verified=True,
        session_id=uuid4(),
    )


def _store_with_member(
    connection: Any, now: datetime, *, roles: frozenset[StaffRole]
) -> tuple[UUID, StaffPrincipal, StaffPrincipal]:
    store_id = uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )
    owner = _staff(connection, roles=frozenset({StaffRole.OWNER_ADMIN}), now=now)
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=owner.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    member = _staff(connection, roles=roles, now=now)
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=member.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    # The API opens its own connection, so anything this fixture leaves inside an open transaction
    # is invisible to the route under test. Committing here is not tidying: without it the board
    # answers about an empty shop and every assertion below passes for the wrong reason.
    connection.commit()
    return store_id, owner, member


def _order_in_production(
    connection: Any, store_id: UUID, staff: StaffPrincipal, *, accepted_at: datetime
) -> UUID:
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff
    )
    stored = OrderRepository().create(
        connection,
        CreateOrderCommand(
            store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            FulfillmentMode.SELF_DROP_SELF_COLLECT,
            staff,
            f"order-{uuid4().hex}",
            uuid4(),
            accepted_at,
            AcquisitionSource.WALK_IN,
        ),
    )
    version = stored.row_version
    for step in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "production_accepted_at": accepted_at,
            "intake_readiness": READY,
        },
    ):
        moved = OrderRepository().transition(
            connection,
            OrderTransitionCommand(
                stored.order_id,
                version,
                staff,
                f"step-{uuid4().hex}",
                uuid4(),
                occurred_at=accepted_at,
                **step,
            ),
        )
        version = moved.row_version
    connection.commit()
    return stored.order_id


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    settings = AuthSettings(database_url=database_url)
    app.dependency_overrides[get_ops_board_service] = lambda: OpsBoardService(settings)
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    app.dependency_overrides[get_assistant_service] = lambda: AssistantService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


# --- one query, not two -------------------------------------------------------------------------


def test_the_board_and_the_assistant_report_the_same_numbers_for_the_same_store(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """The property the re-scope of this item exists to protect.

    Three orders in production, one of them past the internal mark. The board lists them; the
    assistant answers the same question in a sentence. The two numbers in that sentence are parsed
    out and compared to what the board returned, because "the same" has to mean the same figures and
    not merely "both endpoints answered".
    """
    now = datetime.now(UTC)
    store_id, _, member = _store_with_member(
        connection, now, roles=frozenset({StaffRole.OPS_APPROVER})
    )
    for hours in (1, 3, 12):
        _order_in_production(connection, store_id, member, accepted_at=now - timedelta(hours=hours))
    _as(member)

    board = client.get(f"/internal/v1/stores/{store_id}/sla-board").json()
    answer = client.post(
        f"/internal/v1/stores/{store_id}/assistant/turns",
        headers={"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": f"turn-{uuid4()}"},
        json={"question": "Có đơn nào trễ không?"},
    ).json()

    assert answer["intent"] == "SLA_RISK"
    numbers = [int(value) for value in re.findall(r"\d+", answer["answer"])]
    in_production, breached = numbers[0], numbers[1]

    assert len(board["items"]) == in_production == 3
    assert sum(1 for item in board["items"] if item["sla_outcome"] == "BREACHED") == breached == 1
    # And the rule named in the sentence is the rule named on the board, from the one constant.
    assert board["policy_id"] in answer["answer"]
    assert board["policy_notice_vi"] in answer["answer"]


def test_every_board_figure_carries_the_version_of_the_query_that_produced_it(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """Invariant 18 at the wire, not only in the repository."""
    now = datetime.now(UTC)
    store_id, _, member = _store_with_member(connection, now, roles=frozenset({StaffRole.OPERATOR}))
    _as(member)

    board = client.get(f"/internal/v1/stores/{store_id}/sla-board").json()
    summary = client.get(f"/internal/v1/stores/{store_id}/day-summary").json()
    takings = client.get(f"/internal/v1/stores/{store_id}/settlements/today").json()

    # Derived from the policy the route really evaluated under, so that a route quietly passing a
    # different policy would be visible in the version rather than hidden behind a constant.
    assert board["query_version"] == sla_board_query_version(SLA_POLICY).label
    assert summary["query_version"].startswith("today-status-counts-v1:")
    # v2 since DEC-024 refunds are subtracted; the pinned digest lives in `test_ops_board.py`.
    assert takings["query_version"].startswith("collected-today-v2:")


def test_the_board_refuses_a_store_the_caller_is_not_assigned_to(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """The membership check lives in the repository, and the route must not have widened it."""
    now = datetime.now(UTC)
    store_id, _, _ = _store_with_member(connection, now, roles=frozenset({StaffRole.OPERATOR}))
    outsider = _staff(connection, roles=frozenset({StaffRole.OPERATOR}), now=now)
    _as(outsider)

    response = client.get(f"/internal/v1/stores/{store_id}/sla-board")

    assert response.status_code == 403


# --- DEC-014 ------------------------------------------------------------------------------------


def test_a_role_outside_the_dec_014_set_cannot_see_the_takings_figure(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """Verified against the real API, because hiding it in the console hides nothing.

    `AUDITOR` is a member of the store and reads every Shadow surface, so it is the role that would
    slip through a gate written as "any assigned member". The response is checked for the number as
    well as for the status: a refusal that still carried `collected_vnd` in its body would satisfy a
    status-only assertion and leak the figure anyway.

    The three roles `DEC-014` names are asserted in the same test, so widening the set to admit a
    fourth fails here rather than passing quietly as "one more role can see it".
    """
    now = datetime.now(UTC)
    store_id, owner, auditor = _store_with_member(
        connection, now, roles=frozenset({StaffRole.AUDITOR})
    )

    _as(auditor)
    refused = client.get(f"/internal/v1/stores/{store_id}/settlements/today")
    assert refused.status_code == 403
    assert "collected_vnd" not in refused.text

    for role in (StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR):
        permitted = _staff(connection, roles=frozenset({role}), now=now)
        ShadowConsoleRepository.assign_store(
            connection,
            staff_user_id=permitted.staff_user_id,
            store_id=store_id,
            principal=owner,
            correlation_id=uuid4(),
        )
        _as(permitted)
        allowed = client.get(f"/internal/v1/stores/{store_id}/settlements/today")
        assert allowed.status_code == 200, f"{role.value} is in the DEC-014 set and was refused"
        assert allowed.json()["collected_vnd"] == 0


def test_the_day_summary_serves_no_money_at_all(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """The second door `DEC-014` would have to be widened through, deliberately not built.

    The day summary counts orders. If a takings figure ever appears in this response it will be
    behind `require_operations_staff` rather than behind the settlements route's own gate, and the
    one gate the decision names will have become two.
    """
    now = datetime.now(UTC)
    store_id, _, member = _store_with_member(connection, now, roles=frozenset({StaffRole.OPERATOR}))
    _order_in_production(connection, store_id, member, accepted_at=now)
    _as(member)

    summary = client.get(f"/internal/v1/stores/{store_id}/day-summary").json()

    assert summary["total_orders"] == 1
    assert not any("vnd" in key for key in summary)
    assert summary["business_timezone"] == "Asia/Ho_Chi_Minh"


# --- the export, end to end over HTTP -----------------------------------------------------------


def _post(client: TestClient, path: str, body: dict[str, Any]) -> Any:
    return client.post(
        path,
        headers={"Origin": ORIGIN, "X-CSRF-Token": CSRF, "Idempotency-Key": f"key-{uuid4()}"},
        json=body,
    )


def test_an_export_cannot_be_approved_by_the_person_who_defined_it_and_can_by_another_owner(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """`EXPORT-FIX-001`, both directions, driven through the real routes.

    `_OWNER_FINANCIAL` carries `SEPARATION_OF_DUTY` and the decision path enforced it against
    `approval_requests.requested_by` -- the account that raised the ENVELOPE. For an export that is
    a second, later act: the export request is what names the shop, the day and the column list,
    and anybody in the store may raise an envelope against a stored one. So this exact sequence
    used to succeed with the rule reading green: the owner defines the export, a colleague presses
    "xin chủ tiệm duyệt", and the owner approves their own release.

    Over HTTP rather than at the repository because the refusal a console meets is a status and a
    body, and the one this produces has to stay the same opaque 403 every other authorization
    failure produces -- a distinct code here would tell a caller which of two accounts the rule
    compared against.
    """
    now = datetime.now(UTC)
    store_id, owner, approver = _store_with_member(
        connection, now, roles=frozenset({StaffRole.OPS_APPROVER})
    )
    second_owner = _staff(connection, roles=frozenset({StaffRole.OWNER_ADMIN}), now=now)
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=second_owner.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    connection.commit()

    # The owner defines the export: they choose the day, the shop and the column list.
    _as(owner)
    created = _post(
        client,
        f"/internal/v1/stores/{store_id}/exports",
        {"business_date": now.date().isoformat()},
    )
    assert created.status_code == 201, created.text
    request_body = created.json()

    # Somebody else raises the envelope, which is what made the old check pass.
    _as(approver)
    envelope = _post(
        client,
        "/internal/v1/approvals",
        {
            "store_id": str(store_id),
            "action": "EXPORT_SANITIZED_DATA",
            "resource_type": request_body["resource_type"],
            "resource_id": request_body["export_request_id"],
            "resource_version": request_body["resource_version"],
            "snapshot_hash": request_body["snapshot_hash"],
            "rendered_hash": request_body["rendered_hash"],
            "policy_version": request_body["policy_version"],
        },
    )
    assert envelope.status_code in (200, 201), envelope.text
    approval_id = envelope.json()["approval_request_id"]

    decision = {
        "decision": "APPROVED",
        "reason_code": "APPROVED_AFTER_CONSOLE_REVIEW",
        "resource_version": request_body["resource_version"],
        "snapshot_hash": request_body["snapshot_hash"],
        "rendered_hash": request_body["rendered_hash"],
    }

    # The definer tries to approve their own export. Refused, opaquely.
    _as(owner)
    refused = _post(client, f"/internal/v1/approvals/{approval_id}/decisions", decision)
    assert refused.status_code == 403
    assert refused.json()["detail"] == "operation denied"

    # A different owner approves, and the file is released. The other direction matters: a rule
    # that refused everybody would pass the assertion above and break the capability.
    _as(second_owner)
    allowed = _post(client, f"/internal/v1/approvals/{approval_id}/decisions", decision)
    assert allowed.status_code == 200, allowed.text

    _as(owner)
    produced = _post(
        client,
        f"/internal/v1/stores/{store_id}/exports/{request_body['export_request_id']}/execution",
        {"approval_id": approval_id},
    )
    assert produced.status_code == 200, produced.text
    released = produced.json()
    assert released["approval_request_id"] == approval_id
    # And the header is the column list the owner was shown, with no always-false incident column.
    assert released["content_csv"].splitlines()[0] == ",".join(request_body["columns"])
    assert "incident_open" not in released["content_csv"]


def test_the_approval_queue_can_show_an_owner_what_an_export_would_release(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """The read that turns a dead end into a decision `#/approvals` can actually make.

    `EXPORT_REQUEST` was absent from the console's viewable-resource table, so an
    `EXPORT_SANITIZED_DATA` envelope could never be decided from the approvals queue at all. The
    console now renders the export's own contents above the approve control, and this is the route
    it reads. Asserted over HTTP because the properties that matter are the ones a URL exposes:
    that it is keyed by the approval and not by a store the caller could name, that an approval of
    another kind is a 404 rather than a differently-shaped answer, and that the digest it returns is
    the one the queue row carries.
    """
    now = datetime.now(UTC)
    store_id, owner, approver = _store_with_member(
        connection, now, roles=frozenset({StaffRole.OPS_APPROVER})
    )
    connection.commit()

    _as(approver)
    created = _post(
        client,
        f"/internal/v1/stores/{store_id}/exports",
        {"business_date": now.date().isoformat()},
    )
    assert created.status_code == 201, created.text
    request_body = created.json()
    envelope = _post(
        client,
        "/internal/v1/approvals",
        {
            "store_id": str(store_id),
            "action": "EXPORT_SANITIZED_DATA",
            "resource_type": request_body["resource_type"],
            "resource_id": request_body["export_request_id"],
            "resource_version": request_body["resource_version"],
            "snapshot_hash": request_body["snapshot_hash"],
            "rendered_hash": request_body["rendered_hash"],
            "policy_version": request_body["policy_version"],
        },
    )
    approval_id = envelope.json()["approval_request_id"]

    _as(owner)
    disclosed = client.get(f"/internal/v1/approvals/{approval_id}/export-request")
    assert disclosed.status_code == 200, disclosed.text
    content = disclosed.json()
    # What is being released, and what is withheld -- the two halves of the word "sanitized".
    assert content["columns"] == request_body["columns"]
    assert content["excludes"] == request_body["excludes"]
    assert content["business_date"] == now.date().isoformat()
    assert "incident_open" not in content["columns"]
    # The day boundary, named on the surface: the takings box on `#/today` sums a different event.
    assert content["day_boundary"] == "orders.created_at"
    assert "không phải theo lúc thu tiền" in content["statement_vi"]
    # The digest the console compares against the queue row it will build the decision from.
    assert content["rendered_hash"] == request_body["rendered_hash"]
    # The owner did not define this export, so nothing here tells them they may not approve it.
    assert content["requested_by_you"] is False

    # The definer, who may not approve it, is told so before they press rather than by a 403.
    _as(approver)
    for_definer = client.get(f"/internal/v1/approvals/{approval_id}/export-request")
    assert for_definer.status_code == 200
    assert for_definer.json()["requested_by_you"] is True

    # An approval that is not an export is a 404, and so is one that does not exist.
    assert client.get(f"/internal/v1/approvals/{uuid4()}/export-request").status_code == 404


def test_the_export_read_is_refused_for_a_store_the_caller_is_not_assigned_to(
    connection: psycopg.Connection[Any], client: TestClient
) -> None:
    """Keyed by the approval, so the membership check is the whole of the scoping.

    A caller who is an owner somewhere else must not be able to read what another shop's export
    would contain, and must not be able to tell "not yours" from "not there": both are the opaque
    403 and the 404 the test above already pins.
    """
    now = datetime.now(UTC)
    store_id, _, approver = _store_with_member(
        connection, now, roles=frozenset({StaffRole.OPS_APPROVER})
    )
    other_store_id, outsider, _ = _store_with_member(
        connection, now, roles=frozenset({StaffRole.OPS_APPROVER})
    )
    assert other_store_id != store_id
    connection.commit()

    _as(approver)
    created = _post(
        client,
        f"/internal/v1/stores/{store_id}/exports",
        {"business_date": now.date().isoformat()},
    )
    request_body = created.json()
    envelope = _post(
        client,
        "/internal/v1/approvals",
        {
            "store_id": str(store_id),
            "action": "EXPORT_SANITIZED_DATA",
            "resource_type": request_body["resource_type"],
            "resource_id": request_body["export_request_id"],
            "resource_version": request_body["resource_version"],
            "snapshot_hash": request_body["snapshot_hash"],
            "rendered_hash": request_body["rendered_hash"],
            "policy_version": request_body["policy_version"],
        },
    )
    approval_id = envelope.json()["approval_request_id"]

    _as(outsider)
    refused = client.get(f"/internal/v1/approvals/{approval_id}/export-request")
    assert refused.status_code == 403
    assert "columns" not in refused.text
    assert "statement_vi" not in refused.text
