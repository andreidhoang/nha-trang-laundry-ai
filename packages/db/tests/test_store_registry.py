"""`STORE-REGISTRY-001`: the identifier every authorization decision turns on now names a row.

Sixteen migrations referenced `store_id` and none created a table for it, so an assignment granted
membership of a value nobody had validated and `require_store_membership` compared one unchecked
value against another. These tests pin the two consequences of fixing it: a typo can no longer
become a store, and a deployment has a way to create its first one.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.stores import StoreRepository, StoreStateError


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _create(connection: Any, store_id: UUID, name: str = "Cửa hàng thử nghiệm") -> Any:
    return StoreRepository.create(
        connection,
        store_id=store_id,
        name=name,
        created_by=None,
        correlation_id=uuid4(),
    )


def test_a_membership_cannot_name_a_store_that_does_not_exist(connection: Any) -> None:
    """The whole point of the table: a mistyped identifier is refused by the database.

    Before this, assigning a staff member to a UUID nobody had ever issued succeeded. That member
    was then a member of nothing -- shown no orders, refused every write, with no error anywhere
    saying why, because every check compared their assignment against the same invented value.
    """

    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, now(), 1)
            """,
            (uuid4(), uuid4(), uuid4()),
        )


def test_creating_a_store_twice_is_a_no_op_rather_than_a_conflict(connection: Any) -> None:
    """Deploy day runs from a runbook, and a runbook step gets run twice."""

    store_id = uuid4()
    first = _create(connection, store_id, "Giặt Là Sạch Cộng")
    second = _create(connection, store_id, "a different name entirely")

    assert first.created is True
    assert second.created is False
    # The second call does not rename the shop. A name is a fact somebody stated; a re-run of a
    # deployment step is not somebody restating it.
    assert second.name == "Giặt Là Sạch Cộng"


def test_a_store_must_be_called_something(connection: Any) -> None:
    with pytest.raises(StoreStateError, match="a store name is required"):
        _create(connection, uuid4(), "   ")


def test_the_creation_is_audited_like_any_other_material_change(connection: Any) -> None:
    """A store is where money is taken, so its creation is a record, not a side effect."""

    store_id = uuid4()
    _create(connection, store_id)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT event_type FROM domain_events WHERE aggregate_id = %s",
            (store_id,),
        )
        events = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            "SELECT action, actor_type FROM audit_events WHERE aggregate_id = %s",
            (store_id,),
        )
        audit = cursor.fetchall()

    assert events == ["STORE_CREATED"]
    # No staff member existed when the genesis store was created, and the audit record says so
    # rather than naming somebody who did not act.
    assert audit == [("STORE_CREATE", "SYSTEM")]
