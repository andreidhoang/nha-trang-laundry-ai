"""`READ-PATHS-001` over HTTP against real PostgreSQL: four reads the console admitted it lacked.

Each route is proved four ways -- served to the role that needs it, refused to a wrong role, refused
to a member of another store, refused for a store id that does not exist -- and the three refusals
are required to be *the same* response, because a caller who can tell them apart has learned which
stores exist. Then content: the rows the route returns are the ones the database holds, no more
(another store's, another order's) and no less (a pre-`DEC-031` loss with no figure, a revoked
assignment inside its window).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
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
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository
from nha_trang_laundry_db.remedy_reads import RemedyReadRepository
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.staff_directory import (
    RECENT_REVOCATION_WINDOW,
    StaffDirectoryRepository,
)
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode
from nha_trang_laundry_domain.remedies import RemedyKind, RemedyStatus

# The remedy fixtures walk a real order to RELEASED, settle it and open an incident through every
# repository; reached by path for the reason `test_ops_board_postgres.py` gives.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from quote_test_data import accepted_quote
from test_remedies import _execute, _propose, _shop

DENIED = {"detail": "operation denied"}


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


def _person(
    connection: Any,
    *,
    roles: frozenset[StaffRole],
    name: str = "Nhân viên thử nghiệm",
    email: str | None = None,
    mfa: bool = True,
) -> StaffPrincipal:
    """A staff user whose roles are in the database, as the owner check reads them."""

    staff_user_id = uuid4()
    now = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, email, status, created_at)
            VALUES (%s, %s, %s, %s, 'ACTIVE', %s)
            """,
            (staff_user_id, f"oidc-{staff_user_id}", name, email, now),
        )
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_user_id, role.value, now),
            )
    return StaffPrincipal(staff_user_id, f"oidc-{staff_user_id}", roles, mfa, uuid4())


def _store(connection: Any, name: str = "Cửa hàng thử nghiệm") -> tuple[UUID, StaffPrincipal]:
    store_id = uuid4()
    StoreRepository.create(
        connection, store_id=store_id, name=name, created_by=None, correlation_id=uuid4()
    )
    owner = _person(connection, roles=frozenset({StaffRole.OWNER_ADMIN}), name="Chủ tiệm")
    _assign(connection, owner, store_id, owner)
    return store_id, owner


def _assign(connection: Any, staff: StaffPrincipal, store_id: UUID, owner: StaffPrincipal) -> None:
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=staff.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )


def _member(
    connection: Any, store_id: UUID, owner: StaffPrincipal, role: StaffRole, **kwargs: Any
) -> StaffPrincipal:
    member = _person(connection, roles=frozenset({role}), **kwargs)
    _assign(connection, member, store_id, owner)
    return member


# --- 1. The staff directory ----------------------------------------------------------------------


def test_the_owner_reads_who_works_in_the_store_with_roles_and_status(
    connection: Any, client: TestClient
) -> None:
    store_id, owner = _store(connection)
    lan = _member(
        connection,
        store_id,
        owner,
        StaffRole.OPERATOR,
        name="Nguyễn Thị Lan",
        email="lan@example.invalid",
    )
    # Two roles, granted in the reverse of `StaffRole` order: the read lists them in that order.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
            VALUES (%s, %s, 'OWNER_ADMIN', now())
            """,
            (uuid4(), lan.staff_user_id),
        )
    departed = _member(connection, store_id, owner, StaffRole.DRIVER, name="Trần Văn Bình")
    ShadowConsoleRepository.revoke_store(
        connection,
        staff_user_id=departed.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )
    disabled = _member(connection, store_id, owner, StaffRole.OPERATOR, name="Lê Thị Cúc")
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE staff_users SET status = 'DISABLED', disabled_at = now(), "
            "authorization_version = authorization_version + 1 WHERE id = %s",
            (disabled.staff_user_id,),
        )
    # Someone who works only in another shop must not appear here.
    other_store, other_owner = _store(connection, "Cửa hàng khác")
    stranger = _member(connection, other_store, other_owner, StaffRole.OPERATOR, name="Người lạ")

    _as(owner)
    response = client.get(f"/internal/v1/stores/{store_id}/staff")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["store_id"] == str(store_id)
    assert body["recent_revocation_days"] == RECENT_REVOCATION_WINDOW.days == 30
    assert body["truncated"] is False
    by_id = {entry["staff_user_id"]: entry for entry in body["staff"]}
    assert set(by_id) == {
        str(owner.staff_user_id),
        str(lan.staff_user_id),
        str(departed.staff_user_id),
        str(disabled.staff_user_id),
    }
    assert str(stranger.staff_user_id) not in by_id

    lan_entry = by_id[str(lan.staff_user_id)]
    assert lan_entry["display_name"] == "Nguyễn Thị Lan"
    assert lan_entry["status"] == "ACTIVE"
    assert lan_entry["roles"] == ["OWNER_ADMIN", "OPERATOR"]
    assert lan_entry["assignment_revoked_at"] is None
    assert by_id[str(disabled.staff_user_id)]["status"] == "DISABLED"
    assert by_id[str(departed.staff_user_id)]["assignment_revoked_at"] is not None
    # Live assignments first, the revoked one last.
    assert body["staff"][-1]["staff_user_id"] == str(departed.staff_user_id)

    # No email and no OIDC subject: the owner was never handed either through the API.
    assert set(lan_entry) == {
        "staff_user_id",
        "display_name",
        "status",
        "roles",
        "assigned_at",
        "assignment_revoked_at",
    }
    assert "lan@example.invalid" not in response.text
    assert f"oidc-{lan.staff_user_id}" not in response.text


def test_the_staff_directory_refuses_everyone_else_with_one_indistinguishable_answer(
    connection: Any, client: TestClient
) -> None:
    store_id, owner = _store(connection)
    operator = _member(connection, store_id, owner, StaffRole.OPERATOR)
    approver = _member(connection, store_id, owner, StaffRole.OPS_APPROVER)
    other_store, other_owner = _store(connection, "Cửa hàng khác")
    # An owner without MFA cannot hold a session in production (`SENSITIVE_MFA_ROLES`); the read
    # still refuses one rather than relying on that.
    no_mfa = _member(connection, store_id, owner, StaffRole.OWNER_ADMIN, mfa=False)
    # A session that still claims OWNER_ADMIN after the role was revoked in the database.
    stale = _member(connection, store_id, owner, StaffRole.OWNER_ADMIN)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE staff_role_assignments SET revoked_at = now() WHERE staff_user_id = %s",
            (stale.staff_user_id,),
        )

    answers = []
    for principal in (operator, approver, other_owner, no_mfa, stale):
        _as(principal)
        answers.append(client.get(f"/internal/v1/stores/{store_id}/staff"))
    _as(owner)
    answers.append(client.get(f"/internal/v1/stores/{uuid4()}/staff"))
    _as(other_owner)
    assert client.get(f"/internal/v1/stores/{other_store}/staff").status_code == 200

    assert [answer.status_code for answer in answers] == [403] * 6
    assert all(answer.json() == DENIED for answer in answers)


def test_a_revoked_assignment_stays_on_the_directory_for_thirty_days_and_then_leaves(
    connection: Any,
) -> None:
    """The window is measured from the `now` the caller passes, to the microsecond."""

    store_id, owner = _store(connection)
    departed = _member(connection, store_id, owner, StaffRole.OPERATOR, name="Người đã nghỉ")
    revoked_at = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    ShadowConsoleRepository.revoke_store(
        connection,
        staff_user_id=departed.staff_user_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
        now=revoked_at,
    )

    def listed(now: datetime) -> bool:
        with connection.cursor() as cursor:
            directory = StaffDirectoryRepository.list_for_store(
                cursor, store_id=store_id, principal=owner, now=now
            )
        return departed.staff_user_id in {entry.staff_user_id for entry in directory.entries}

    edge = revoked_at + RECENT_REVOCATION_WINDOW
    assert listed(edge) is True
    assert listed(edge + timedelta(microseconds=1)) is False
    with connection.cursor() as cursor, pytest.raises(StoreAccessError):
        StaffDirectoryRepository.list_for_store(
            cursor,
            store_id=store_id,
            principal=_member(connection, store_id, owner, StaffRole.AUDITOR),
            now=edge,
        )


# --- 2 and 3. Remedy credits of an order, remedy proposals of an incident -------------------------


def _counter_members(
    connection: Any, store_id: UUID
) -> tuple[StaffPrincipal, StaffPrincipal, StaffPrincipal]:
    """A driver and an auditor of the store, and an operator of a different store."""

    owner = _person(connection, roles=frozenset({StaffRole.OWNER_ADMIN}))
    driver = _member(connection, store_id, owner, StaffRole.DRIVER)
    auditor = _member(connection, store_id, owner, StaffRole.AUDITOR)
    elsewhere, other_owner = _store(connection, "Cửa hàng khác")
    outsider = _member(connection, elsewhere, other_owner, StaffRole.OPERATOR)
    return driver, auditor, outsider


def test_the_counter_finds_the_credits_an_order_issued_and_whether_each_is_spent(
    connection: Any, client: TestClient
) -> None:
    store_id, staff, order_id, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=80_000,
    )
    credit_id = _execute(connection, proposal.proposal_id, staff).credit_id
    assert credit_id is not None

    _as(staff)
    path = f"/internal/v1/stores/{store_id}/orders/{order_id}/remedy-credits"
    response = client.get(path)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["order_id"] == str(order_id)
    assert body["store_id"] == str(store_id)
    assert body["truncated"] is False
    assert len(body["credits"]) == 1
    credit = body["credits"][0]
    assert credit["credit_id"] == str(credit_id)
    assert credit["remedy_proposal_id"] == str(proposal.proposal_id)
    assert credit["incident_id"] == str(incident_id)
    assert credit["kind"] == "DAMAGE_COMPENSATION"
    # Integer đồng, exactly the figure the proposal recorded.
    assert credit["amount_vnd"] == 80_000
    assert isinstance(credit["amount_vnd"], int)
    assert credit["status"] == "UNUSED"
    assert credit["redeemed_at"] is None
    assert credit["redeemed_quote_id"] is None
    # There is no expiry column, so the read carries no expiry field rather than an invented one.
    assert not any("expir" in key for key in credit)

    # Spent: the only update the credit table admits, pointed at a revision that exists.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE remedy_credits c
            SET redeemed_at = now(), redeemed_quote_id = o.current_quote_id,
                redeemed_quote_revision = o.current_quote_revision,
                row_version = c.row_version + 1
            FROM orders o
            WHERE c.id = %s AND o.id = c.issued_from_order_id
            RETURNING o.current_quote_id, o.current_quote_revision
            """,
            (credit_id,),
        )
        spent_on = cursor.fetchone()
    assert spent_on is not None
    spent = client.get(path).json()["credits"][0]
    assert spent["status"] == "REDEEMED"
    assert spent["redeemed_quote_id"] == str(spent_on[0])
    assert spent["redeemed_quote_revision"] == int(spent_on[1])

    # An order of the same store with no remedy is an empty list, not a 404.
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff
    )
    quiet = OrderRepository().create(
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
            datetime.now(UTC),
            AcquisitionSource.WALK_IN,
        ),
    )
    empty = client.get(f"/internal/v1/stores/{store_id}/orders/{quiet.order_id}/remedy-credits")
    assert empty.status_code == 200
    assert empty.json()["credits"] == []


def test_order_credits_are_refused_to_other_roles_other_stores_and_unknown_stores(
    connection: Any, client: TestClient
) -> None:
    store_id, staff, order_id, _ = _shop(connection)
    driver, auditor, outsider = _counter_members(connection, store_id)
    path = f"/internal/v1/stores/{store_id}/orders/{order_id}/remedy-credits"

    answers = []
    for principal in (driver, auditor, outsider):
        _as(principal)
        answers.append(client.get(path))
    _as(staff)
    answers.append(client.get(f"/internal/v1/stores/{uuid4()}/orders/{order_id}/remedy-credits"))
    no_mfa = StaffPrincipal(
        staff.staff_user_id, staff.oidc_subject, staff.roles, False, staff.session_id
    )
    _as(no_mfa)
    answers.append(client.get(path))

    assert [answer.status_code for answer in answers] == [403] * 5
    assert all(answer.json() == DENIED for answer in answers)

    # A member naming another store's order, or an order that does not exist, reads the same 404.
    other_store, _, other_order, _ = _shop(connection)
    _as(staff)
    foreign = client.get(f"/internal/v1/stores/{store_id}/orders/{other_order}/remedy-credits")
    missing = client.get(f"/internal/v1/stores/{store_id}/orders/{uuid4()}/remedy-credits")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": "order unavailable"}
    assert other_store != store_id


def _legacy_loss(connection: Any, seed_proposal_id: UUID) -> UUID:
    """A `POLICY_UNRESOLVED` loss as written before `DEC-031`: no amount, no line, no envelope."""

    legacy = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO remedy_proposals (
                id, store_id, incident_id, order_id, kind, status, policy_version_id,
                policy_version, store_fault_attested, proposal_hash, proposed_by, proposed_at,
                correlation_id
            )
            SELECT %s, store_id, incident_id, order_id, 'LOST_ITEM', 'POLICY_UNRESOLVED',
                   policy_version_id, policy_version, TRUE, proposal_hash, proposed_by,
                   proposed_at + interval '1 second', %s
            FROM remedy_proposals WHERE id = %s
            """,
            (legacy, uuid4(), seed_proposal_id),
        )
    return legacy


def test_every_proposal_on_an_incident_is_listed_including_a_figureless_pre_dec_031_loss(
    connection: Any, client: TestClient
) -> None:
    store_id, staff, order_id, incident_id = _shop(connection)
    # Inside the 100.000 d staff limit for the line, so staff may authorise and carry it out.
    paid = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=40_000,
    )
    # The line's total now passes the staff limit, so this one waits for the owner.
    owner_bound = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=150_000,
        proposed_at=_proposed_at(connection, paid.proposal_id) + timedelta(seconds=2),
    )
    assert owner_bound.status is RemedyStatus.OWNER_APPROVAL_REQUIRED
    assert owner_bound.approval_id is not None
    legacy = _legacy_loss(connection, owner_bound.proposal_id)
    credit_id = _execute(connection, paid.proposal_id, staff).credit_id
    # Another incident's proposal, in another store, must not leak in.
    other_store, other_staff, _, other_incident = _shop(connection)
    _propose(
        connection,
        other_store,
        other_incident,
        other_staff,
        kind=RemedyKind.FREE_REWASH,
        store_fault_attested=True,
    )

    _as(staff)
    response = client.get(
        f"/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-proposals"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["incident_id"] == str(incident_id)
    assert body["order_id"] == str(order_id)
    assert body["truncated"] is False
    rows = body["proposals"]
    assert [row["proposal_id"] for row in rows] == [
        str(paid.proposal_id),
        str(owner_bound.proposal_id),
        str(legacy),
    ]

    executed, first, loss = rows
    assert first["kind"] == "DAMAGE_COMPENSATION"
    assert first["status"] == "OWNER_APPROVAL_REQUIRED"
    assert first["amount_vnd"] == 150_000
    assert first["ceiling_vnd"] == 500_000
    assert first["approval_id"] == str(owner_bound.approval_id)
    assert first["approval_status"] == "REQUESTED"
    assert first["approval_expires_at"] is not None
    assert first["proposed_by"] == str(staff.staff_user_id)
    assert first["proposed_by_name"] == "Nhân viên"
    assert first["credit_id"] is None

    assert loss["kind"] == "LOST_ITEM"
    assert loss["status"] == "POLICY_UNRESOLVED"
    assert loss["amount_vnd"] is None
    assert loss["ceiling_vnd"] is None
    assert loss["approval_id"] is None
    assert loss["approval_status"] is None
    assert loss["approval_lapsed"] is None

    assert executed["status"] == "EXECUTED"
    assert executed["amount_vnd"] == 40_000
    assert executed["executed_at"] is not None
    assert executed["credit_id"] == str(credit_id)


def _proposed_at(connection: Any, proposal_id: UUID) -> datetime:
    with connection.cursor() as cursor:
        cursor.execute("SELECT proposed_at FROM remedy_proposals WHERE id = %s", (proposal_id,))
        row = cursor.fetchone()
    assert row is not None
    value = row[0]
    assert isinstance(value, datetime)
    return value


def test_an_owner_envelope_lapses_at_its_expiry_instant_and_not_a_microsecond_before(
    connection: Any,
) -> None:
    store_id, staff, _, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=150_000,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT expires_at FROM approval_requests WHERE id = %s", (proposal.approval_id,)
        )
        row = cursor.fetchone()
    assert row is not None
    expires_at = row[0]

    def lapsed(now: datetime) -> bool | None:
        with connection.cursor() as cursor:
            read = RemedyReadRepository.list_incident_proposals(
                cursor, store_id=store_id, incident_id=incident_id, principal=staff, now=now
            )
        return read.proposals[0].approval_lapsed

    assert lapsed(expires_at - timedelta(microseconds=1)) is False
    assert lapsed(expires_at) is True


def test_incident_proposals_are_refused_to_other_roles_other_stores_and_unknown_stores(
    connection: Any, client: TestClient
) -> None:
    store_id, staff, _, incident_id = _shop(connection)
    driver, auditor, outsider = _counter_members(connection, store_id)
    path = f"/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-proposals"

    answers = []
    for principal in (driver, auditor, outsider):
        _as(principal)
        answers.append(client.get(path))
    _as(staff)
    answers.append(
        client.get(f"/internal/v1/stores/{uuid4()}/incidents/{incident_id}/remedy-proposals")
    )

    assert [answer.status_code for answer in answers] == [403] * 4
    assert all(answer.json() == DENIED for answer in answers)

    _, _, _, other_incident = _shop(connection)
    foreign = client.get(
        f"/internal/v1/stores/{store_id}/incidents/{other_incident}/remedy-proposals"
    )
    missing = client.get(f"/internal/v1/stores/{store_id}/incidents/{uuid4()}/remedy-proposals")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": "incident unavailable"}
    # An incident with no proposal yet is an empty list, not a refusal.
    assert client.get(path).json()["proposals"] == []


# --- 4. The acquisition source, read back ---------------------------------------------------------


def test_the_recorded_acquisition_source_is_on_the_order_read_and_the_board(
    connection: Any, client: TestClient
) -> None:
    store_id, owner = _store(connection)
    operator = _member(connection, store_id, owner, StaffRole.OPERATOR)
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=operator
    )
    order = OrderRepository().create(
        connection,
        CreateOrderCommand(
            store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            FulfillmentMode.SELF_DROP_SELF_COLLECT,
            operator,
            f"order-{uuid4().hex}",
            uuid4(),
            datetime.now(UTC),
            AcquisitionSource.GOOGLE_MAPS,
        ),
    )

    _as(operator)
    read = client.get(f"/internal/v1/orders/{order.order_id}")
    board = client.get(f"/internal/v1/stores/{store_id}/orders")

    assert read.status_code == 200, read.text
    assert read.json()["acquisition_source"] == "GOOGLE_MAPS"
    assert board.status_code == 200
    assert [item["acquisition_source"] for item in board.json()] == ["GOOGLE_MAPS"]

    # The same read's refusals are unchanged: another store's member gets the missing-order 404.
    elsewhere, other_owner = _store(connection, "Cửa hàng khác")
    _as(_member(connection, elsewhere, other_owner, StaffRole.OPERATOR))
    assert client.get(f"/internal/v1/orders/{order.order_id}").status_code == 404
    _as(_member(connection, store_id, owner, StaffRole.DRIVER))
    assert client.get(f"/internal/v1/orders/{order.order_id}").status_code == 403
