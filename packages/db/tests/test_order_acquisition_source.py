"""`ACQUISITION-ATTRIBUTION-001`: an order says where the customer came from, once.

The claim under test is not "the repository writes a column". It is that the value cannot be
changed after the order exists, **and that the refusal comes from the database rather than from
Python**, because the reason it must be immutable is not a coding convention: the only source of
truth for how a customer found the shop walked out of the shop, and nobody can be asked again in
November how they arrived in September. A guard that lives in one repository method is a guard a
second writer can miss.

So every test here goes through a real database and one of them writes raw SQL on purpose,
bypassing the repository entirely, to prove the constraint is not merely being observed by the code
that happens to run today.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode
from quote_test_data import accepted_quote

NOW = datetime(2026, 9, 8, 3, tzinfo=UTC)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(connection: Any, store_id: UUID) -> StaffPrincipal:
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )
    staff_id = uuid4()
    assigner = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_id, assigner):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (identifier, f"oidc-{identifier}", NOW),
            )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            ON CONFLICT DO NOTHING
            """,
            (staff_id, store_id, assigner, NOW),
        )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({StaffRole.OWNER_ADMIN}), True)


def _order(connection: Any, source: AcquisitionSource) -> tuple[UUID, UUID]:
    store_id = uuid4()
    principal = _staff(connection, store_id)
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=principal
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
            principal,
            f"order-create-{uuid4().hex}",
            uuid4(),
            NOW,
            source,
        ),
    )
    return stored.order_id, store_id


def _stored_source(connection: Any, order_id: UUID) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT acquisition_source FROM orders WHERE id = %s", (order_id,))
        return str(cursor.fetchone()[0])


@pytest.mark.parametrize("source", list(AcquisitionSource))
def test_every_source_the_enum_offers_can_actually_be_recorded(
    connection: Any, source: AcquisitionSource
) -> None:
    """A member the console offers but the CHECK constraint rejects is a 500 at the counter."""

    order_id, _ = _order(connection, source)

    assert _stored_source(connection, order_id) == source.value


def test_the_database_refuses_to_change_it_even_by_raw_sql(connection: Any) -> None:
    """The guard is in `enforce_order_projection_update`, not in the repository."""

    order_id, _ = _order(connection, AcquisitionSource.LEAFLET_QR)

    with (
        pytest.raises(psycopg.errors.RaiseException, match="invalid order projection update"),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            UPDATE orders
            SET acquisition_source = 'GOOGLE_MAPS', row_version = row_version + 1
            WHERE id = %s
            """,
            (order_id,),
        )

    assert _stored_source(connection, order_id) == "LEAFLET_QR"


def test_an_insert_that_omits_the_source_fails_rather_than_recording_ignorance(
    connection: Any,
) -> None:
    """`0036` drops the default it needed for the backfill.

    With the default left in place, any writer that forgot the column would have recorded
    `UNKNOWN` -- which reads identically to "the counter did not ask" and is not the same fact at
    all. This is the test that would have caught leaving it.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_default, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'orders' AND column_name = 'acquisition_source'
            """
        )
        column_default, is_nullable = cursor.fetchone()

    assert column_default is None
    assert is_nullable == "NO"


def test_a_replay_that_changes_the_source_is_a_conflict_not_a_replay(connection: Any) -> None:
    """The source is inside the hashed idempotency payload.

    Re-sending an order-create with the same key but a different source is a different intent, and
    a repository that answered it with the first order's identifier would silently discard the
    correction while telling the counter it had succeeded.
    """

    store_id = uuid4()
    principal = _staff(connection, store_id)
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=principal
    )
    key = f"order-create-{uuid4().hex}"

    def command(source: AcquisitionSource) -> CreateOrderCommand:
        return CreateOrderCommand(
            store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            FulfillmentMode.SELF_DROP_SELF_COLLECT,
            principal,
            key,
            uuid4(),
            NOW,
            source,
        )

    first = OrderRepository().create(connection, command(AcquisitionSource.ZALO))
    replayed = OrderRepository().create(connection, command(AcquisitionSource.ZALO))
    assert replayed.order_id == first.order_id
    assert replayed.replayed is True

    with pytest.raises(IdempotencyConflictError):
        OrderRepository().create(connection, command(AcquisitionSource.WALK_IN))
