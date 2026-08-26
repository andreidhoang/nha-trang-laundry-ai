"""A walk-in customer can be taken in, and nothing about them is stored.

`DEC-013`, resolved 2026-08-26. The tests that matter here are as much about what is absent as what
is present: the table has no column for a name, a phone number or an address, and an order can no
longer name a customer reference nobody issued.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.counter_tickets import CounterTicketRepository
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.store_access import StoreAccessError

NOW = datetime(2026, 8, 26, 3, tzinfo=UTC)


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _member(connection: Any, store_id: UUID, role: StaffRole) -> StaffPrincipal:
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
            INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at, assigned_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (uuid4(), staff_id, role.value, NOW, staff_id),
        )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (staff_id, store_id, staff_id, NOW),
        )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), True, uuid4())


def test_the_ticket_table_holds_nothing_that_identifies_a_person(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The design is the absence. Asserted against the schema so a future column has to argue."""

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'counter_tickets'"
        )
        columns = {str(row[0]) for row in cursor.fetchall()}
    assert columns == {
        "id",
        "store_id",
        "issued_on",
        "ticket_number",
        "issued_by",
        "issued_at",
        "correlation_id",
    }
    # `issued_by` is the staff member who handed it over, not the customer.
    for personal in ("name", "phone", "email", "address", "customer_name", "contact"):
        assert not any(personal in column for column in columns)


def test_numbers_restart_each_day_and_never_repeat_within_one(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Two customers must never share a number, and a counter starts at one each morning."""

    store_id = uuid4()
    staff = _member(postgres_connection, store_id, StaffRole.OPERATOR)
    repository = CounterTicketRepository()
    today = [
        repository.issue(
            postgres_connection,
            store_id=store_id,
            principal=staff,
            correlation_id=uuid4(),
            issued_at=NOW,
        )
        for _ in range(3)
    ]
    tomorrow = repository.issue(
        postgres_connection,
        store_id=store_id,
        principal=staff,
        correlation_id=uuid4(),
        issued_at=NOW.replace(day=27),
    )
    assert [ticket.ticket_number for ticket in today] == [1, 2, 3]
    assert tomorrow.ticket_number == 1
    assert len({ticket.ticket_id for ticket in today}) == 3


def test_two_stores_do_not_share_a_counter(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Numbering is per store: one shop's queue is not the other's."""

    first, second = uuid4(), uuid4()
    repository = CounterTicketRepository()
    one = repository.issue(
        postgres_connection,
        store_id=first,
        principal=_member(postgres_connection, first, StaffRole.OPERATOR),
        correlation_id=uuid4(),
        issued_at=NOW,
    )
    other = repository.issue(
        postgres_connection,
        store_id=second,
        principal=_member(postgres_connection, second, StaffRole.OPERATOR),
        correlation_id=uuid4(),
        issued_at=NOW,
    )
    assert one.ticket_number == other.ticket_number == 1


def test_a_non_member_cannot_issue_a_ticket_for_a_store(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The same store boundary every other counter command enforces."""

    store_id = uuid4()
    outsider = _member(postgres_connection, uuid4(), StaffRole.OPERATOR)
    with pytest.raises(StoreAccessError):
        CounterTicketRepository().issue(
            postgres_connection,
            store_id=store_id,
            principal=outsider,
            correlation_id=uuid4(),
            issued_at=NOW,
        )


def test_a_read_only_role_cannot_issue_a_ticket(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Issuing a ticket is counter work, not a read."""

    store_id = uuid4()
    auditor = _member(postgres_connection, store_id, StaffRole.AUDITOR)
    with pytest.raises(StoreAccessError):
        CounterTicketRepository().issue(
            postgres_connection,
            store_id=store_id,
            principal=auditor,
            correlation_id=uuid4(),
            issued_at=NOW,
        )
