"""`CREDIT-PICK-001` and `CONTACT-PICK-001` over HTTP against real PostgreSQL.

The counter's Nhận đồ flow had two values left that a person typed: a remedy credit's code, and a
channel customer's contact binding. Two reads replace them, and each is proved here the way the
`READ-PATHS-001` reads were:

* **content** -- the rows are the ones the database holds for this store, no more (another store's,
  a spent credit, a binding nothing in this store names) and no less;
* **scoping** -- another store's rows never appear, and a member of another store, a wrong role, a
  session without MFA and a store id that does not exist all receive the *same* opaque 403;
* **bounds** -- the limit is enforced by the server and a cut list says `truncated`;
* **what is not disclosed** -- no contact field on a credit, no provider handle or message text on
  a contact.

And the one property neither read may weaken: `POST …/order-requests` still refuses a binding the
server never recorded, whatever a list says.
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
from nha_trang_laundry_contracts.channel_envelope import ChannelProvider
from nha_trang_laundry_db.channel import ContactChannelBindingRepository
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.remedy_reads import STORE_CREDITS_SQL
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.remedies import RemedyKind

# The remedy fixtures walk a real order to RELEASED, settle it and open an incident through every
# repository; reached by path for the reason `test_ops_board_postgres.py` gives.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

import quote_test_data
from message_draft_test_data import record_customer_message
from test_remedies import NOW, _execute, _incident, _propose, _released_order, _settle, _shop

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


def _person(connection: Any, role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
    staff_user_id = uuid4()
    now = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên thử nghiệm', 'ACTIVE', %s)
            """,
            (staff_user_id, f"oidc-{staff_user_id}", now),
        )
        cursor.execute(
            """
            INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
            VALUES (%s, %s, %s, %s)
            """,
            (uuid4(), staff_user_id, role.value, now),
        )
    return StaffPrincipal(staff_user_id, f"oidc-{staff_user_id}", frozenset({role}), mfa, uuid4())


def _member(connection: Any, store_id: UUID, role: StaffRole) -> StaffPrincipal:
    owner = _person(connection, StaffRole.OWNER_ADMIN)
    member = _person(connection, role)
    for person in (owner, member):
        ShadowConsoleRepository.assign_store(
            connection,
            staff_user_id=person.staff_user_id,
            store_id=store_id,
            principal=owner,
            correlation_id=uuid4(),
        )
    return member


def _store(connection: Any) -> UUID:
    store_id = uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )
    return store_id


def _binding(connection: Any, handle: str | None = None) -> tuple[UUID, str]:
    """A channel customer, recorded through the channel envelope's own resolve path."""

    reference = handle or f"synthetic-handle-{uuid4().hex[:12]}"
    resolved = ContactChannelBindingRepository().resolve_or_create(
        connection,
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        provider_user_ref=reference,
        correlation_id=uuid4(),
    )
    return resolved.binding.contact_id, reference


def _refusals(client: TestClient, path: str, principals: list[StaffPrincipal]) -> list[Any]:
    answers = []
    for principal in principals:
        _as(principal)
        answers.append(client.get(path))
    return answers


# --- CREDIT-PICK-001 ------------------------------------------------------------------------------


def _credit(
    connection: Any,
    store_id: UUID,
    staff: StaffPrincipal,
    *,
    amount_vnd: int,
    executed_at: datetime,
    order_incident: tuple[UUID, UUID] | None = None,
) -> tuple[UUID, UUID]:
    """One executed damage credit in `store_id`, issued at `executed_at`: (credit id, order id)."""

    if order_incident is None:
        order_id, _ = _released_order(connection, store_id, staff)
        _settle(connection, order_id, staff, collected=True)
        incident_id = _incident(connection, store_id, order_id, staff)
    else:
        order_id, incident_id = order_incident
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=amount_vnd,
    )
    credit_id = _execute(connection, proposal.proposal_id, staff, executed_at).credit_id
    assert credit_id is not None
    return credit_id, order_id


def _ticket(connection: Any, order_id: UUID) -> tuple[int, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.ticket_number, t.issued_on FROM orders o
            JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
            WHERE o.id = %s
            """,
            (order_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0]), row[1]


def _spend(connection: Any, credit_id: UUID) -> None:
    """The one update `0042` admits: the credit spent on a revision that exists."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE remedy_credits c
            SET redeemed_at = now(), redeemed_quote_id = o.current_quote_id,
                redeemed_quote_revision = o.current_quote_revision,
                row_version = c.row_version + 1
            FROM orders o
            WHERE c.id = %s AND o.id = c.issued_from_order_id
            """,
            (credit_id,),
        )


def test_the_counter_lists_the_stores_unused_credits_newest_first_with_their_ticket(
    connection: Any, client: TestClient
) -> None:
    store_id, staff, first_order, first_incident = _shop(connection)
    oldest, _ = _credit(
        connection,
        store_id,
        staff,
        amount_vnd=30_000,
        executed_at=NOW,
        order_incident=(first_order, first_incident),
    )
    middle, middle_order = _credit(
        connection, store_id, staff, amount_vnd=45_000, executed_at=NOW + timedelta(minutes=1)
    )
    newest, newest_order = _credit(
        connection, store_id, staff, amount_vnd=60_000, executed_at=NOW + timedelta(minutes=2)
    )
    # Another shop's unused credit is never this shop's to list.
    other_store, other_staff, other_order, other_incident = _shop(connection)
    foreign, _ = _credit(
        connection,
        other_store,
        other_staff,
        amount_vnd=70_000,
        executed_at=NOW,
        order_incident=(other_order, other_incident),
    )

    _as(staff)
    response = client.get(f"/internal/v1/stores/{store_id}/remedy-credits")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["store_id"] == str(store_id)
    assert body["state"] == "UNUSED"
    assert body["ticket"] is None
    assert body["limit"] == 50
    assert body["truncated"] is False
    listed = [row["credit_id"] for row in body["credits"]]
    assert listed == [str(newest), str(middle), str(oldest)]
    assert str(foreign) not in listed

    row = body["credits"][0]
    number, issued_on = _ticket(connection, newest_order)
    assert row == {
        "credit_id": str(newest),
        "kind": "DAMAGE_COMPENSATION",
        "amount_vnd": 60_000,
        "issued_at": row["issued_at"],
        "issued_from_order_id": str(newest_order),
        "ticket_number": number,
        "ticket_issued_on": issued_on.isoformat(),
        "policy_version_id": row["policy_version_id"],
    }
    assert isinstance(row["amount_vnd"], int)
    assert datetime.fromisoformat(row["issued_at"]) == NOW + timedelta(minutes=2)
    # No contact field of any kind (`DEC-015`): the list is of credits, not of customers.
    assert not any("contact" in key or "bearer" in key for key in row)

    # A spent credit leaves the list; the others stay, in the same order.
    _spend(connection, middle)
    after = client.get(f"/internal/v1/stores/{store_id}/remedy-credits").json()
    assert [row["credit_id"] for row in after["credits"]] == [str(newest), str(oldest)]

    # The ticket filter narrows to credits issued from orders tracked by that number.
    middle_number, _ = _ticket(connection, middle_order)
    newest_number, _ = _ticket(connection, newest_order)
    assert middle_number != newest_number
    narrowed = client.get(f"/internal/v1/stores/{store_id}/remedy-credits?ticket={newest_number}")
    assert narrowed.status_code == 200
    assert narrowed.json()["ticket"] == newest_number
    assert [row["credit_id"] for row in narrowed.json()["credits"]] == [str(newest)]
    # The spent credit's ticket finds nothing: filtering never resurrects a spent credit.
    spent = client.get(f"/internal/v1/stores/{store_id}/remedy-credits?ticket={middle_number}")
    assert spent.json()["credits"] == []


def test_a_credit_issued_to_a_channel_customer_is_listed_with_no_ticket(
    connection: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_id, staff, _, _ = _shop(connection)
    binding, _ = _binding(connection)
    # The same real order path, bound to a channel binding instead of a counter ticket.
    monkeypatch.setattr(quote_test_data, "counter_ticket", lambda *_args, **_kwargs: binding)
    credit_id, order_id = _credit(connection, store_id, staff, amount_vnd=25_000, executed_at=NOW)

    _as(staff)
    rows = client.get(f"/internal/v1/stores/{store_id}/remedy-credits").json()["credits"]
    row = next(row for row in rows if row["credit_id"] == str(credit_id))
    assert row["issued_from_order_id"] == str(order_id)
    assert row["ticket_number"] is None and row["ticket_issued_on"] is None
    assert str(binding) not in str(rows)


def test_the_credit_list_is_bounded_by_the_server_and_says_when_it_is_cut(
    connection: Any, client: TestClient
) -> None:
    store_id, staff, first_order, first_incident = _shop(connection)
    for minute in range(3):
        _credit(
            connection,
            store_id,
            staff,
            amount_vnd=10_000,
            executed_at=NOW + timedelta(minutes=minute),
            order_incident=(first_order, first_incident) if minute == 0 else None,
        )
    _as(staff)
    path = f"/internal/v1/stores/{store_id}/remedy-credits"

    cut = client.get(f"{path}?limit=2").json()
    assert (len(cut["credits"]), cut["truncated"], cut["limit"]) == (2, True, 2)
    whole = client.get(f"{path}?limit=3").json()
    assert (len(whole["credits"]), whole["truncated"]) == (3, False)
    assert client.get(f"{path}?limit=200").status_code == 200

    for query in ("limit=0", "limit=201", "state=REDEEMED", "ticket=0", "ticket=abc"):
        assert client.get(f"{path}?{query}").status_code == 422, query


def test_the_open_credit_page_is_read_from_the_partial_index(connection: Any) -> None:
    """`remedy_credits_open_idx` exists for this read; a rewrite that stops using it is a defect."""

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            "EXPLAIN " + STORE_CREDITS_SQL, {"store": uuid4(), "ticket": None, "limit": 51}
        )
        plan = "\n".join(str(row[0]) for row in cursor.fetchall())
    assert "remedy_credits_open_idx" in plan, plan


def test_every_counter_role_and_the_auditor_read_credits_and_nobody_else_does(
    connection: Any, client: TestClient
) -> None:
    store_id, staff, order_id, incident_id = _shop(connection)
    credit_id, _ = _credit(
        connection,
        store_id,
        staff,
        amount_vnd=20_000,
        executed_at=NOW,
        order_incident=(order_id, incident_id),
    )
    path = f"/internal/v1/stores/{store_id}/remedy-credits"

    readers = [
        _member(connection, store_id, role)
        for role in (
            StaffRole.OWNER_ADMIN,
            StaffRole.OPS_APPROVER,
            StaffRole.OPERATOR,
            StaffRole.AUDITOR,
        )
    ]
    for reader in readers:
        _as(reader)
        answer = client.get(path)
        assert answer.status_code == 200, (reader.roles, answer.text)
        assert [row["credit_id"] for row in answer.json()["credits"]] == [str(credit_id)]

    driver = _member(connection, store_id, StaffRole.DRIVER)
    accountant = _member(connection, store_id, StaffRole.ACCOUNTANT)
    outsider = _member(connection, _store(connection), StaffRole.OPERATOR)
    no_mfa = StaffPrincipal(
        staff.staff_user_id, staff.oidc_subject, staff.roles, False, staff.session_id
    )
    answers = _refusals(client, path, [driver, accountant, outsider, no_mfa])
    _as(staff)
    answers.append(client.get(f"/internal/v1/stores/{uuid4()}/remedy-credits"))
    assert [answer.status_code for answer in answers] == [403] * 5
    assert all(answer.json() == DENIED for answer in answers)


# --- CONTACT-PICK-001 -----------------------------------------------------------------------------


def _intake(store_id: UUID, member: StaffPrincipal, binding: UUID) -> UUID:
    """The staff intake the hand-off makes, through the same service the route calls."""

    service = OperationsService(AuthSettings(database_url=_database_url()))
    stored = service.create_order_request(
        store_id=store_id,
        contact_binding_id=binding,
        idempotency_key=f"intake-{uuid4().hex}",
        principal=member,
    )
    return stored.order_request_id


def _stamp(connection: Any, order_request_id: UUID, at: datetime) -> None:
    """Pin an intake's creation instant, so newest-first is a fact the test states."""

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE order_requests SET created_at = %s WHERE id = %s", (at, order_request_id)
        )


def test_the_counter_sees_the_channel_customers_this_store_served_and_no_one_else(
    connection: Any, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_id, staff, _, _ = _shop(connection)
    other_store, other_staff, _, _ = _shop(connection)

    waiting, waiting_handle = _binding(connection)
    ordered, _ = _binding(connection)
    elsewhere, _ = _binding(connection)
    silent, _ = _binding(connection)
    # A customer who wrote to the shop but for whom this store has started nothing: not listed.
    record_customer_message(connection, silent, received_at=NOW)

    waiting_request = _intake(store_id, staff, waiting)
    _stamp(connection, waiting_request, NOW + timedelta(minutes=5))
    _intake(other_store, other_staff, elsewhere)

    # A real order for `ordered`, through the same repositories as every other order fixture.
    monkeypatch.setattr(quote_test_data, "counter_ticket", lambda *_args, **_kwargs: ordered)
    order_id, _ = _released_order(connection, store_id, staff)
    monkeypatch.undo()
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE order_requests SET created_at = %s WHERE contact_binding_id = %s",
            (NOW - timedelta(days=1), ordered),
        )

    _as(staff)
    response = client.get(f"/internal/v1/stores/{store_id}/contacts/recent")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["store_id"] == str(store_id)
    assert body["truncated"] is False
    listed = [row["contact_binding_id"] for row in body["contacts"]]
    # Walk-in tickets (the `_shop` orders) are not channel customers; `elsewhere` was served by
    # another store only; `silent` has no order or intake here. None is listed.
    assert listed == [str(waiting), str(ordered)]

    first, second = body["contacts"]
    assert first == {
        "contact_binding_id": str(waiting),
        "channels": ["TELEGRAM_SANDBOX"],
        "last_activity_at": first["last_activity_at"],
        "latest_order": None,
        "open_order_count": 0,
        "waiting_order_request_id": str(waiting_request),
    }
    assert datetime.fromisoformat(first["last_activity_at"]) == NOW + timedelta(minutes=5)

    board = client.get(f"/internal/v1/orders/{order_id}").json()
    assert second["latest_order"] == {
        "order_id": str(order_id),
        "created_at": second["latest_order"]["created_at"],
        "commercial": board["commercial"],
        "intake": board["intake"],
        "production": board["production"],
        "balance": board["balance"],
        "payable_total_vnd": board["payable_total_vnd"],
    }
    assert isinstance(second["latest_order"]["payable_total_vnd"], int)
    assert second["open_order_count"] == 1
    # Its one intake became that order, so there is nothing to resume.
    assert second["waiting_order_request_id"] is None

    # No provider handle, no message text: nothing but the opaque binding and this store's facts.
    assert waiting_handle not in response.text
    assert "provider_user_ref" not in response.text

    # The other store sees its own customer and none of this one's.
    _as(other_staff)
    theirs = client.get(f"/internal/v1/stores/{other_store}/contacts/recent").json()
    assert [row["contact_binding_id"] for row in theirs["contacts"]] == [str(elsewhere)]


def test_the_hand_off_creates_the_intake_and_the_server_still_refuses_an_unknown_binding(
    connection: Any, client: TestClient
) -> None:
    store_id = _store(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    binding, _ = _binding(connection)
    _as(operator)
    path = f"/internal/v1/stores/{store_id}/contacts/recent"
    assert client.get(path).json()["contacts"] == []

    created = client.post(
        f"/internal/v1/stores/{store_id}/order-requests",
        json={"contact_binding_id": str(binding)},
        headers={"Idempotency-Key": f"hand-off-{uuid4().hex}"},
    )
    assert created.status_code == 201, created.text
    rows = client.get(path).json()["contacts"]
    assert [row["contact_binding_id"] for row in rows] == [str(binding)]
    assert rows[0]["waiting_order_request_id"] == created.json()["order_request_id"]

    # A list is not an authority: a binding nobody recorded is refused exactly as before.
    unknown = client.post(
        f"/internal/v1/stores/{store_id}/order-requests",
        json={"contact_binding_id": str(uuid4())},
        headers={"Idempotency-Key": f"hand-off-{uuid4().hex}"},
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"] == {
        "outcome": "REQUIRE_HUMAN",
        "reason_codes": ["CONTACT_BINDING_UNKNOWN"],
    }
    assert len(client.get(path).json()["contacts"]) == 1


def test_the_contact_list_is_bounded_and_refused_to_everyone_but_the_counter(
    connection: Any, client: TestClient
) -> None:
    store_id = _store(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    for minute in range(3):
        binding, _ = _binding(connection)
        _stamp(connection, _intake(store_id, operator, binding), NOW + timedelta(minutes=minute))
    path = f"/internal/v1/stores/{store_id}/contacts/recent"

    for role in (StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR):
        _as(_member(connection, store_id, role))
        assert len(client.get(path).json()["contacts"]) == 3

    _as(operator)
    cut = client.get(f"{path}?limit=2").json()
    assert (len(cut["contacts"]), cut["truncated"], cut["limit"]) == (2, True, 2)
    assert client.get(f"{path}?limit=3").json()["truncated"] is False
    assert client.get(f"{path}").json()["limit"] == 20
    for query in ("limit=0", "limit=101"):
        assert client.get(f"{path}?{query}").status_code == 422, query

    auditor = _member(connection, store_id, StaffRole.AUDITOR)
    driver = _member(connection, store_id, StaffRole.DRIVER)
    accountant = _member(connection, store_id, StaffRole.ACCOUNTANT)
    outsider = _member(connection, _store(connection), StaffRole.OPERATOR)
    no_mfa = StaffPrincipal(
        operator.staff_user_id, operator.oidc_subject, operator.roles, False, operator.session_id
    )
    answers = _refusals(client, path, [auditor, driver, accountant, outsider, no_mfa])
    _as(operator)
    answers.append(client.get(f"/internal/v1/stores/{uuid4()}/contacts/recent"))
    assert [answer.status_code for answer in answers] == [403] * 6
    assert all(answer.json() == DENIED for answer in answers)
