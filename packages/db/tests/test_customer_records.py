"""`CUSTOMER-001` (`DEC-034`): customer records against real PostgreSQL.

What is proved here, each against the rows the database actually holds:

* **refused until the owner publishes the notice** -- on a database where nothing was published;
* **the phone at rest** -- sealed, digested, cut to its last four digits; never in clear;
* **uniqueness by digest per store** -- every written form of one number is one record per store;
* **search by each mode** -- full phone, last four digits, diacritic-insensitive name; newest
  activity first; bounded; erased and other stores' records never listed;
* **masking by role** -- an auditor reads neither the number nor the address;
* **If-Match and consent events** on a correction;
* **erasure keeps orders**, and an erased record is final;
* **the retention job** -- 24 months with no order, open orders protect, the owner runs it;
* **no phone value in any ledger row** -- every domain event, audit row, outbox row and
  idempotency record written along the whole life of a customer is scanned for the number in each
  of its written forms, and for the name, the address and the note.
"""

from __future__ import annotations

import os
import random
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.counter_tickets import CounterTicketRepository
from nha_trang_laundry_db.customers import (
    CustomerChanges,
    CustomerNotFoundError,
    CustomerPhoneExistsError,
    CustomerRepository,
    CustomerStateError,
    run_customer_retention,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.intake import CreateOrderRequestCommand, OrderRequestRepository
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository
from nha_trang_laundry_db.personal_data import open_phone
from nha_trang_laundry_db.privacy_notice import (
    PrivacyNoticeAuthorizationError,
    publish_privacy_notice,
    read_published_privacy_notice,
)
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode
from nha_trang_laundry_domain.customers import (
    CustomerKind,
    CustomerRefusal,
    CustomerRuleError,
    ErasureReason,
    LinkKind,
    QueryMode,
)
from psycopg import sql
from psycopg.conninfo import make_conninfo
from quote_test_data import accepted_quote
from test_customer_notice import notice_payload

NOW = datetime.now(UTC).replace(microsecond=0)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def scratch_url() -> Iterator[str]:
    """A database of its own, migrated from empty, where nobody has published anything."""

    configured = _database_url()
    maintenance = make_conninfo(configured, dbname="postgres")
    name = f"ntl_customer_{uuid4().hex[:12]}"
    with psycopg.connect(maintenance, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(configured, dbname=name)
    finally:
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


# --- fixtures -------------------------------------------------------------------------------------


def _person(connection: Any, role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
    staff_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
            """,
            (staff_id, f"oidc-{staff_id}", NOW),
        )
        cursor.execute(
            """
            INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
            VALUES (%s, %s, %s, %s)
            """,
            (uuid4(), staff_id, role.value, NOW),
        )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), mfa, uuid4())


def _join(connection: Any, store_id: UUID, person: StaffPrincipal) -> StaffPrincipal:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (person.staff_user_id, store_id, person.staff_user_id, NOW),
        )
    return person


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


def _staff(connection: Any, store_id: UUID, role: StaffRole = StaffRole.OPERATOR) -> StaffPrincipal:
    return _join(connection, store_id, _person(connection, role))


def _published(connection: Any) -> StaffPrincipal:
    owner = _person(connection, StaffRole.OWNER_ADMIN)
    publish_privacy_notice(connection, actor_id=owner.staff_user_id, payload=notice_payload())
    return owner


def _mobile() -> tuple[str, str]:
    """A fresh mobile number (national, e164) no other test uses."""

    subscriber = "9" + "".join(random.choice("0123456789") for _ in range(8))
    return f"0{subscriber}", f"+84{subscriber}"


def _create(
    connection: Any,
    store_id: UUID,
    staff: StaffPrincipal,
    phone: str,
    *,
    name: str | None = "chị Lan",
    note: str | None = None,
    address: str | None = None,
    marketing: bool = False,
    at: datetime = NOW,
) -> UUID:
    return CustomerRepository().create(
        connection,
        store_id=store_id,
        principal=staff,
        phone=phone,
        display_name=name,
        delivery_address=address,
        note=note,
        kind=CustomerKind.RETAIL,
        service_consent=True,
        marketing_consent=marketing,
        at=at,
        correlation_id=uuid4(),
    )


def _order_for(connection: Any, store_id: UUID, staff: StaffPrincipal, customer_id: UUID) -> UUID:
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, customer_id=customer_id
    )
    return (
        OrderRepository()
        .create(
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
                NOW,
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )


def _row(connection: Any, customer_id: UUID) -> tuple[Any, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT phone_ciphertext, phone_digest, phone_last4, display_name, name_folded,
                   delivery_address, note, erased_at, erasure_reason, row_version, store_id
            FROM customers WHERE id = %s
            """,
            (customer_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return tuple(row)


# --- publication ----------------------------------------------------------------------------------


def test_a_record_is_refused_until_the_owner_publishes_the_notice(scratch_url: str) -> None:
    national, _ = _mobile()
    with psycopg.connect(scratch_url, autocommit=True) as connection:
        apply_migrations(connection)
        store_id = _store(connection)
        staff = _staff(connection, store_id)
        with connection.cursor() as cursor:
            assert read_published_privacy_notice(cursor) is None
        with pytest.raises(CustomerRuleError) as caught:
            _create(connection, store_id, staff, national)
        assert caught.value.code is CustomerRefusal.PRIVACY_NOTICE_UNPUBLISHED
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM customers")
            assert cursor.fetchone() == (0,)
            cursor.execute("SELECT count(*) FROM domain_events WHERE aggregate_type = 'CUSTOMER'")
            assert cursor.fetchone() == (0,)

        # Only an active owner publishes; anyone else is refused and nothing is written.
        with pytest.raises(PrivacyNoticeAuthorizationError):
            publish_privacy_notice(
                connection, actor_id=staff.staff_user_id, payload=notice_payload()
            )
        owner = _person(connection, StaffRole.OWNER_ADMIN)
        digest, created = publish_privacy_notice(
            connection, actor_id=owner.staff_user_id, payload=notice_payload()
        )
        assert created
        assert publish_privacy_notice(
            connection, actor_id=owner.staff_user_id, payload=notice_payload()
        ) == (digest, False)

        customer_id = _create(connection, store_id, staff, national)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.service_consent_notice_version, v.config_type, c.service_consent_by
                FROM customers c JOIN configuration_versions v ON v.id = c.service_consent_notice_id
                WHERE c.id = %s
                """,
                (customer_id,),
            )
            assert cursor.fetchone() == (1, "CUSTOMER_PRIVACY_NOTICE", staff.staff_user_id)


def test_the_consent_attestation_is_required(connection: Any) -> None:
    _published(connection)
    store_id = _store(connection)
    staff = _staff(connection, store_id)
    with pytest.raises(CustomerRuleError) as caught:
        CustomerRepository().create(
            connection,
            store_id=store_id,
            principal=staff,
            phone=_mobile()[0],
            display_name=None,
            delivery_address=None,
            note=None,
            kind=CustomerKind.RETAIL,
            service_consent=False,
            marketing_consent=False,
            at=NOW,
            correlation_id=uuid4(),
        )
    assert caught.value.code is CustomerRefusal.SERVICE_CONSENT_REQUIRED


# --- the number at rest ---------------------------------------------------------------------------


def test_the_phone_is_sealed_digested_and_cut_to_four_digits(connection: Any) -> None:
    _published(connection)
    store_id = _store(connection)
    staff = _staff(connection, store_id)
    national, e164 = _mobile()
    customer_id = _create(
        connection, store_id, staff, f"{national[:4]} {national[4:7]} {national[7:]}"
    )
    sealed, digest, last4, *_ = _row(connection, customer_id)
    assert national.encode() not in bytes(sealed) and e164[3:].encode() not in bytes(sealed)
    assert digest.startswith("PHONE-HMAC-V1:") and national[1:] not in digest
    assert last4 == national[-4:]
    assert open_phone(bytes(sealed), customer_id=customer_id, store_id=store_id) == e164
    # Bound to its row: the same bytes on another customer's row do not open.
    with pytest.raises(ValueError):
        open_phone(bytes(sealed), customer_id=uuid4(), store_id=store_id)


def test_one_number_is_one_record_per_store_in_every_written_form(connection: Any) -> None:
    _published(connection)
    store_id, other_store = _store(connection), _store(connection)
    staff = _staff(connection, store_id)
    national, e164 = _mobile()
    first = _create(connection, store_id, staff, national)
    for spelling in (e164, e164[1:], f"{national[:4]}.{national[4:7]}.{national[7:]}"):
        with pytest.raises(CustomerPhoneExistsError) as caught:
            _create(connection, store_id, staff, spelling)
        assert caught.value.customer_id == first
    # Another shop keeps its own list.
    assert _create(connection, other_store, _staff(connection, other_store), national) != first


def test_an_invalid_phone_is_refused_by_name(connection: Any) -> None:
    _published(connection)
    store_id = _store(connection)
    with pytest.raises(CustomerRuleError) as caught:
        _create(connection, store_id, _staff(connection, store_id), "0605 123 456")
    assert caught.value.code is CustomerRefusal.PHONE_INVALID


# --- search ---------------------------------------------------------------------------------------


def test_search_by_full_phone_last_four_digits_and_name(connection: Any) -> None:
    _published(connection)
    store_id, other_store = _store(connection), _store(connection)
    staff = _staff(connection, store_id)
    repository = CustomerRepository()
    national, e164 = _mobile()
    # Two customers sharing the last four digits; the newer activity comes first.
    twin = national[:-5] + ("1" if national[-5] != "1" else "2") + national[-4:]
    older = _create(connection, store_id, staff, twin, name="Anh Tuấn", at=NOW - timedelta(days=2))
    lan = _create(connection, store_id, staff, national, name="Chị Lan Nguyễn")
    _create(connection, other_store, _staff(connection, other_store), _mobile()[0], name="Chị Lan")

    with connection.cursor() as cursor:
        full = repository.search(cursor, store_id=store_id, principal=staff, query=e164)
        assert full.mode is QueryMode.PHONE
        assert [item.customer_id for item in full.customers] == [lan]
        assert full.customers[0].phone == national

        last4 = repository.search(cursor, store_id=store_id, principal=staff, query=national[-4:])
        assert last4.mode is QueryMode.LAST4
        assert [item.customer_id for item in last4.customers] == [lan, older]

        by_name = repository.search(
            cursor, store_id=store_id, principal=staff, query="chi LAN nguyen"
        )
        assert by_name.mode is QueryMode.NAME
        assert [item.customer_id for item in by_name.customers] == [lan]
        folded = repository.search(cursor, store_id=store_id, principal=staff, query="tuan")
        assert [item.customer_id for item in folded.customers] == [older]

        # A LIKE wildcard typed as a name is a character, not a pattern.
        assert not repository.search(
            cursor, store_id=store_id, principal=staff, query="%%"
        ).customers

        incomplete = repository.search(cursor, store_id=store_id, principal=staff, query="09051")
        assert incomplete.mode is QueryMode.PHONE_INCOMPLETE and not incomplete.customers

        newest = repository.search(cursor, store_id=store_id, principal=staff, query="", limit=1)
        assert newest.mode is QueryMode.EMPTY
        assert [item.customer_id for item in newest.customers] == [lan]
        assert newest.truncated


def test_an_auditor_reads_masked_and_an_outsider_reads_nothing(connection: Any) -> None:
    _published(connection)
    store_id, other_store = _store(connection), _store(connection)
    staff = _staff(connection, store_id)
    national, _ = _mobile()
    customer_id = _create(connection, store_id, staff, national, address="12 Trần Phú")
    auditor = _staff(connection, store_id, StaffRole.AUDITOR)
    repository = CustomerRepository()
    with connection.cursor() as cursor:
        found = repository.search(cursor, store_id=store_id, principal=auditor, query=national)
        assert [item.phone for item in found.customers] == [None]
        assert found.customers[0].phone_last4 == national[-4:]
        profile = repository.profile(
            cursor, store_id=store_id, customer_id=customer_id, principal=auditor
        )
        assert (profile.phone, profile.delivery_address, profile.phone_visible) == (
            None,
            None,
            False,
        )
        full = repository.profile(
            cursor, store_id=store_id, customer_id=customer_id, principal=staff
        )
        assert (full.phone, full.delivery_address) == (national, "12 Trần Phú")

        outsider = _staff(connection, other_store)
        with pytest.raises(StoreAccessError):
            repository.search(cursor, store_id=store_id, principal=outsider, query=national)
        # Asked through the outsider's own store, the record does not exist.
        with pytest.raises(CustomerNotFoundError):
            repository.profile(
                cursor, store_id=other_store, customer_id=customer_id, principal=outsider
            )
        driver = _staff(connection, store_id, StaffRole.DRIVER)
        with pytest.raises(StoreAccessError):
            repository.search(cursor, store_id=store_id, principal=driver, query=national)
        no_mfa = StaffPrincipal(
            staff.staff_user_id, staff.oidc_subject, staff.roles, False, staff.session_id
        )
        with pytest.raises(StoreAccessError):
            repository.search(cursor, store_id=store_id, principal=no_mfa, query=national)


# --- correction -----------------------------------------------------------------------------------


def test_a_correction_needs_the_current_version_and_consent_is_an_event(connection: Any) -> None:
    _published(connection)
    store_id = _store(connection)
    staff = _staff(connection, store_id)
    customer_id = _create(connection, store_id, staff, _mobile()[0])
    repository = CustomerRepository()

    def change(version: int, **fields: Any) -> int:
        return repository.update(
            connection,
            store_id=store_id,
            customer_id=customer_id,
            principal=staff,
            expected_row_version=version,
            changes=CustomerChanges(provided=frozenset(fields), **fields),
            at=NOW,
            correlation_id=uuid4(),
        )

    assert change(1, note="Giặt riêng đồ trắng") == 2
    with pytest.raises(CustomerStateError, match="STALE_VERSION"):
        change(1, note="Không dùng nước xả")
    assert change(2, marketing_consent=True) == 3
    assert change(3, marketing_consent=False) == 4
    with pytest.raises(CustomerRuleError) as caught:
        change(4, marketing_consent=False)
    assert caught.value.code is CustomerRefusal.NOTHING_TO_CHANGE
    new_national, _ = _mobile()
    assert change(4, phone=new_national, display_name=None) == 5

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT aggregate_version, event_type, payload FROM domain_events
            WHERE aggregate_type = 'CUSTOMER' AND aggregate_id = %s
            ORDER BY aggregate_version
            """,
            (customer_id,),
        )
        events = cursor.fetchall()
    assert [(row[0], row[1]) for row in events] == [
        (1, "CUSTOMER_RECORDED"),
        (2, "CUSTOMER_DETAILS_CORRECTED"),
        (3, "CUSTOMER_CONSENT_CHANGED"),
        (4, "CUSTOMER_CONSENT_CHANGED"),
        (5, "CUSTOMER_DETAILS_CORRECTED"),
    ]
    assert events[2][2]["marketing_consent"] == "GIVEN"
    assert events[3][2]["marketing_consent"] == "WITHDRAWN"
    assert events[4][2]["changed_fields"] == ["display_name", "phone"]
    profile_row = _row(connection, customer_id)
    assert profile_row[2] == new_national[-4:] and profile_row[3] is None


# --- intake, order, link, credits -----------------------------------------------------------------


def test_an_intake_for_a_customer_moves_their_activity_and_the_order_inherits_them(
    connection: Any,
) -> None:
    _published(connection)
    store_id = _store(connection)
    staff = _staff(connection, store_id)
    customer_id = _create(connection, store_id, staff, _mobile()[0], at=NOW - timedelta(days=30))
    ticket = CounterTicketRepository().issue(
        connection, store_id=store_id, principal=staff, correlation_id=uuid4()
    )
    request = OrderRequestRepository().create(
        connection,
        CreateOrderRequestCommand(
            store_id=store_id,
            contact_binding_id=ticket.ticket_id,
            conversation_binding_id=uuid4(),
            actor_id=staff.staff_user_id,
            correlation_id=uuid4(),
            created_at=NOW,
            actor_type="STAFF",
            customer_id=customer_id,
        ),
    )
    with connection.cursor() as cursor:
        summary = OrderRequestRepository.get_for_store(
            cursor, order_request_id=request.order_request_id, store_id=store_id, principal=staff
        )
        assert summary is not None
        assert (summary.customer_id, summary.customer_name) == (customer_id, "chị Lan")
        cursor.execute(
            "SELECT last_activity_at, row_version FROM customers WHERE id = %s", (customer_id,)
        )
        # The touch moves activity and nothing else, so an open edit is not made stale by it.
        assert cursor.fetchone() == (NOW, 1)

    order_id = _order_for(connection, store_id, staff, customer_id)
    with connection.cursor() as cursor:
        view = OrderRepository.read_for_principal(cursor, order_id=order_id, principal=staff)
    assert (view.customer_id, view.customer_name, view.customer_has_phone) == (
        customer_id,
        "chị Lan",
        True,
    )
    with pytest.raises(psycopg.errors.RaiseException), connection.cursor() as cursor:
        cursor.execute("UPDATE orders SET customer_id = NULL WHERE id = %s", (order_id,))

    # Another store's customer is refused as not found, and nothing is written.
    other_store = _store(connection)
    other_ticket = CounterTicketRepository().issue(
        connection,
        store_id=other_store,
        principal=_staff(connection, other_store),
        correlation_id=uuid4(),
    )
    with pytest.raises(CustomerNotFoundError):
        OrderRequestRepository().create(
            connection,
            CreateOrderRequestCommand(
                store_id=other_store,
                contact_binding_id=other_ticket.ticket_id,
                conversation_binding_id=uuid4(),
                actor_id=staff.staff_user_id,
                correlation_id=uuid4(),
                created_at=NOW,
                actor_type="STAFF",
                customer_id=customer_id,
            ),
        )


def test_a_linked_ticket_brings_its_orders_and_credits_to_the_customer(connection: Any) -> None:
    # A walk-in order from before the record existed, with a damage credit issued on it.
    from nha_trang_laundry_domain.remedies import RemedyKind
    from test_remedies import _execute, _propose, _shop

    store_id, remedy_staff, order_id, incident_id = _shop(connection)
    proposal = _propose(
        connection,
        store_id,
        incident_id,
        remedy_staff,
        kind=RemedyKind.DAMAGE_COMPENSATION,
        store_fault_attested=True,
        order_line_id="line-1",
        amount_vnd=50_000,
    )
    credit_id = _execute(connection, proposal.proposal_id, remedy_staff).credit_id
    _published(connection)
    staff = _staff(connection, store_id)
    customer_id = _create(connection, store_id, staff, _mobile()[0])
    with connection.cursor() as cursor:
        cursor.execute("SELECT bound_contact_id FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
    assert row is not None
    ticket_id = row[0]
    repository = CustomerRepository()

    def link(kind: LinkKind, ref: UUID, target: UUID = customer_id) -> UUID:
        return repository.link(
            connection,
            store_id=store_id,
            customer_id=target,
            principal=staff,
            link_kind=kind,
            ref_id=ref,
            at=NOW,
            correlation_id=uuid4(),
        )

    with connection.cursor() as cursor:
        before = repository.detail(
            cursor, store_id=store_id, customer_id=customer_id, principal=staff
        )
    assert not before.recent_orders and not before.credits
    link(LinkKind.COUNTER_TICKET, ticket_id)
    with connection.cursor() as cursor:
        after = repository.detail(
            cursor, store_id=store_id, customer_id=customer_id, principal=staff
        )
    assert [item.order_id for item in after.recent_orders] == [order_id]
    assert [item.credit_id for item in after.credits] == [credit_id]
    assert after.credits[0].amount_vnd == 50_000
    assert [(item.link_kind, item.ref_id) for item in after.links] == [
        (LinkKind.COUNTER_TICKET, ticket_id)
    ]
    # One customer per reference, and only references this store has.
    second = _create(connection, store_id, staff, _mobile()[0], name=None)
    with pytest.raises(CustomerRuleError) as caught:
        link(LinkKind.COUNTER_TICKET, ticket_id, second)
    assert caught.value.code is CustomerRefusal.LINK_EXISTS
    for kind in LinkKind:
        with pytest.raises(CustomerRuleError) as caught:
            link(kind, uuid4())
        assert caught.value.code is CustomerRefusal.LINK_REFERENCE_UNKNOWN


# --- erasure --------------------------------------------------------------------------------------


def test_erasure_keeps_the_orders_and_is_final(connection: Any) -> None:
    _published(connection)
    store_id = _store(connection)
    staff = _staff(connection, store_id)
    approver = _staff(connection, store_id, StaffRole.OPS_APPROVER)
    national, _ = _mobile()
    customer_id = _create(
        connection, store_id, staff, national, note="Giặt riêng", address="12 Trần Phú"
    )
    order_id = _order_for(connection, store_id, staff, customer_id)
    repository = CustomerRepository()

    def erase(principal: StaffPrincipal, version: int) -> int:
        return repository.erase(
            connection,
            store_id=store_id,
            customer_id=customer_id,
            principal=principal,
            expected_row_version=version,
            reason=ErasureReason.CUSTOMER_REQUEST,
            at=NOW,
            correlation_id=uuid4(),
        )

    with pytest.raises(StoreAccessError):
        erase(staff, 1)  # an operator may not erase
    assert erase(approver, 1) == 2

    row = _row(connection, customer_id)
    assert row[:7] == (None,) * 7
    assert row[7] is not None and row[8] == "CUSTOMER_REQUEST"
    with connection.cursor() as cursor:
        cursor.execute("SELECT customer_id FROM orders WHERE id = %s", (order_id,))
        assert cursor.fetchone() == (customer_id,)
        view = OrderRepository.read_for_principal(cursor, order_id=order_id, principal=staff)
        assert (view.customer_id, view.customer_name, view.customer_has_phone) == (
            customer_id,
            None,
            False,
        )
        detail = repository.detail(
            cursor, store_id=store_id, customer_id=customer_id, principal=staff
        )
        assert detail.profile.erased_at is not None and detail.profile.phone is None
        assert [item.order_id for item in detail.recent_orders] == [order_id]
        assert not repository.search(
            cursor, store_id=store_id, principal=staff, query=national
        ).customers

    with pytest.raises(CustomerRuleError) as caught:
        erase(approver, 2)
    assert caught.value.code is CustomerRefusal.CUSTOMER_ERASED
    erased_update = "UPDATE customers SET note = 'x', row_version = 3 WHERE id = %s"
    with (
        pytest.raises(psycopg.errors.RaiseException, match="CUSTOMER_ERASED"),
        connection.cursor() as cursor,
    ):
        cursor.execute(erased_update, (customer_id,))
    with pytest.raises(psycopg.errors.RaiseException), connection.cursor() as cursor:
        cursor.execute("DELETE FROM customers WHERE id = %s", (customer_id,))
    # The number may be recorded again after the person asked to be forgotten.
    assert _create(connection, store_id, staff, national) != customer_id


def test_a_half_erased_row_cannot_be_stored(connection: Any) -> None:
    _published(connection)
    store_id = _store(connection)
    staff = _staff(connection, store_id)
    customer_id = _create(connection, store_id, staff, _mobile()[0])
    with pytest.raises(psycopg.errors.CheckViolation), connection.cursor() as cursor:
        cursor.execute(
            """
                UPDATE customers SET erased_at = now(), erased_by = %s,
                    erasure_reason = 'CUSTOMER_REQUEST', row_version = 2
                WHERE id = %s
                """,
            (staff.staff_user_id, customer_id),
        )


# --- retention ------------------------------------------------------------------------------------


def test_retention_erases_what_had_no_order_for_24_months_and_nothing_else(
    connection: Any,
) -> None:
    owner = _published(connection)
    store_id = _store(connection)
    staff = _staff(connection, store_id)
    as_of = NOW + timedelta(days=3650)  # far enough that no other test's record is younger
    long_ago = as_of - timedelta(days=740)
    stale = _create(connection, store_id, staff, _mobile()[0], at=long_ago)
    recent = _create(connection, store_id, staff, _mobile()[0], at=as_of - timedelta(days=30))
    # Old record, but its order is still open: never erased while the laundry is in the shop.
    protected = _create(connection, store_id, staff, _mobile()[0], at=long_ago)
    _order_for(connection, store_id, staff, protected)

    with pytest.raises(StoreAccessError):
        run_customer_retention(connection, actor_id=staff.staff_user_id, as_of=as_of)
    dry = run_customer_retention(
        connection, actor_id=owner.staff_user_id, as_of=as_of, dry_run=True
    )
    assert stale in dry.erased and recent not in dry.erased and protected not in dry.erased
    assert _row(connection, stale)[7] is None  # a dry run writes nothing

    outcome = run_customer_retention(connection, actor_id=owner.staff_user_id, as_of=as_of)
    assert stale in outcome.erased
    assert recent not in outcome.erased and protected not in outcome.erased
    assert _row(connection, stale)[8] == "RETENTION"
    assert _row(connection, recent)[7] is None and _row(connection, protected)[7] is None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT actor_id, details ->> 'reason', details ->> 'trigger' FROM audit_events
            WHERE aggregate_type = 'CUSTOMER' AND aggregate_id = %s AND action = 'CUSTOMER_ERASE'
            """,
            (stale,),
        )
        assert cursor.fetchone() == (owner.staff_user_id, "RETENTION", "RETENTION_JOB")


# --- no phone value in any ledger row -------------------------------------------------------------


def test_no_phone_name_address_or_note_reaches_any_ledger_row(connection: Any) -> None:
    """Scan every row the whole life of one customer wrote, in every table that keeps history."""

    _published(connection)
    store_id = _store(connection)
    staff = _staff(connection, store_id)
    approver = _staff(connection, store_id, StaffRole.OPS_APPROVER)
    national, e164 = _mobile()
    second_national, second_e164 = _mobile()
    name = f"Khách Riêng Tư {uuid4().hex[:6]}"
    note = f"Ghi chú riêng {uuid4().hex[:6]}"
    address = f"Hẻm {uuid4().hex[:6]} Trần Phú"
    repository = CustomerRepository()
    customer_id = _create(
        connection, store_id, staff, national, name=name, note=note, address=address, marketing=True
    )
    repository.update(
        connection,
        store_id=store_id,
        customer_id=customer_id,
        principal=staff,
        expected_row_version=1,
        changes=CustomerChanges(
            provided=frozenset({"phone", "marketing_consent"}),
            phone=second_e164,
            marketing_consent=False,
        ),
        at=NOW,
        correlation_id=uuid4(),
    )
    order_id = _order_for(connection, store_id, staff, customer_id)
    ticket = CounterTicketRepository().issue(
        connection, store_id=store_id, principal=staff, correlation_id=uuid4()
    )
    repository.link(
        connection,
        store_id=store_id,
        customer_id=customer_id,
        principal=staff,
        link_kind=LinkKind.COUNTER_TICKET,
        ref_id=ticket.ticket_id,
        at=NOW,
        correlation_id=uuid4(),
    )
    repository.erase(
        connection,
        store_id=store_id,
        customer_id=customer_id,
        principal=approver,
        expected_row_version=2,
        reason=ErasureReason.CUSTOMER_REQUEST,
        at=NOW,
        correlation_id=uuid4(),
    )

    secrets = {
        national,
        e164,
        national[1:],  # the subscriber digits, as any formatter would print them
        second_national,
        second_e164,
        second_national[1:],
        f"{national[:4]} {national[4:7]} {national[7:]}",
        name,
        note,
        address,
    }
    scanned = 0
    with connection.cursor() as cursor:
        for table, column in (
            ("domain_events", "payload"),
            ("audit_events", "details"),
            ("outbox_events", "payload"),
            ("command_idempotency_records", "response"),
        ):
            cursor.execute(f"SELECT {column}::text FROM {table}")
            rows = [str(row[0]) for row in cursor.fetchall()]
            scanned += len(rows)
            for text in rows:
                for secret in secrets:
                    assert secret not in text, f"{table}.{column} holds a personal value"
        # And the customer's own history is there, keyed by id, so the scan was over real rows.
        cursor.execute(
            """
            SELECT count(*) FROM domain_events
            WHERE aggregate_id = %s OR payload ->> 'customer_id' = %s
            """,
            (customer_id, str(customer_id)),
        )
        row = cursor.fetchone()
    assert row is not None and row[0] >= 4
    assert scanned > 0
    assert order_id
