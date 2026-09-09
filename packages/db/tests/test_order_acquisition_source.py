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
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderStateError,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    FulfillmentMode,
    IntakeStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness
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


def test_the_ready_clock_clears_for_a_rewash_and_restamps_when_it_is_finished_again(
    connection: Any,
) -> None:
    """`0037`, corrected. A rewashed order is not a finished order.

    `DEC-024` makes READY_AT_STORE -> EXCEPTION -> IN_PROCESS legal on purpose: a stain found at
    quality check needs a rewash, and that is backward movement through the sequence. The first
    version of this column kept the earliest stamp forever, which froze the SLA board at MET for
    precisely the order most likely to be late -- the inverse of the defect the column was added to
    fix. It now names the LAST completion, and holds nothing while the work is being redone.
    """

    order_id, store_id = _order(connection, AcquisitionSource.WALK_IN)
    principal = _staff_for_store(connection, store_id)
    repository = OrderRepository()

    def move(**kwargs: Any) -> None:
        stored = _current(connection, order_id)
        repository.transition(
            connection,
            OrderTransitionCommand(
                order_id,
                stored,
                principal,
                f"ready-clock-{uuid4().hex}",
                uuid4(),
                **kwargs,
            ),
        )

    move(intake_target=IntakeStatus.RECEIVED_PENDING_INSPECTION)
    move(
        intake_target=IntakeStatus.ACCEPTED,
        intake_readiness=IntakeReadiness(
            custody_recorded=True,
            quantity_basis_approved=True,
            service_classified=True,
            exact_price_approved=True,
            customer_reconfirmation_satisfied=True,
            slot_approved=True,
        ),
        production_accepted_at=NOW,
    )
    for target in (
        ProductionStatus.QUEUED,
        ProductionStatus.IN_PROCESS,
        ProductionStatus.QUALITY_CHECK,
        ProductionStatus.READY_AT_STORE,
    ):
        move(production_target=target)
    first_ready = _ready_at(connection, order_id)
    assert first_ready is not None, "reaching READY_AT_STORE must stamp the clock"

    # A stain is found. The order goes to EXCEPTION and back to the machine.
    move(production_target=ProductionStatus.EXCEPTION)
    assert _ready_at(connection, order_id) is None, (
        "an order being rewashed is not finished, so the clock must not still name a completion"
    )

    move(production_target=ProductionStatus.IN_PROCESS)
    assert _ready_at(connection, order_id) is None

    move(production_target=ProductionStatus.QUALITY_CHECK)
    move(production_target=ProductionStatus.READY_AT_STORE)
    second_ready = _ready_at(connection, order_id)
    assert second_ready is not None
    assert second_ready > first_ready, "the second completion is the one that counts"

    # And releasing keeps it: the laundry left, nothing was redone.
    move(production_target=ProductionStatus.RELEASED)
    assert _ready_at(connection, order_id) == second_ready


def _current(connection: Any, order_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT row_version FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _ready_at(connection: Any, order_id: UUID) -> datetime | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT production_ready_at FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
    assert row is not None
    stamped = row[0]
    assert stamped is None or isinstance(stamped, datetime)
    return stamped


def _staff_for_store(connection: Any, store_id: UUID) -> StaffPrincipal:
    """The principal `_order` already created for this store, re-derived from its assignment."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT staff_user_id FROM staff_store_assignments WHERE store_id = %s LIMIT 1",
            (store_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    staff_id = row[0] if isinstance(row[0], UUID) else UUID(str(row[0]))
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({StaffRole.OWNER_ADMIN}), True)


def test_a_committed_newer_acceptance_refuses_the_create_and_writes_nothing(
    connection: Any,
) -> None:
    """The sequential case: a newer acceptance already committed when the create runs.

    This one passed before `FOR UPDATE OF q` too, and says so plainly. The guard reads
    `superseded` and refuses, and it does not need a lock to see an acceptance that committed
    earlier. What it pins is the refusal and, as much as the refusal, that nothing is written on the
    way out: no order row, and the agreement still OPEN for whoever prices it next.

    The interleaving the lock actually exists for -- an acceptance committing *between* the guard's
    read and the UPDATE -- cannot be arranged from outside the repository, because there is no hook
    inside its transaction to stop at. The test below demonstrates the mechanism instead.
    """

    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    store_id = uuid4()
    principal = _staff(connection, store_id)
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=principal
    )
    # Committed so the other connection can see the setup at all. The race being tested is between
    # the create and a *committed* acceptance, which is precisely the one READ COMMITTED lets
    # through: an uncommitted one is invisible to everybody and proves nothing.
    connection.commit()

    # A second connection stands in for the other tablet, and accepts a newer revision while the
    # create is mid-transaction. `accepted_quote` already left revision `revision` accepted, so a
    # later acceptance is what supersedes it.
    with psycopg.connect(database_url) as other, other.cursor() as cursor:
        cursor.execute(
            "SELECT max(final_revision) FROM quote_acceptances WHERE quote_id = %s", (quote_id,)
        )
        row = cursor.fetchone()
        assert row is not None
        latest = int(row[0])
        cursor.execute(
            """
            INSERT INTO quote_acceptances (
                id, store_id, quote_id, accepted_revision, accepted_snapshot_hash, final_revision,
                display_total_vnd, accepted_by, accepted_at, correlation_id, policy_version
            )
            SELECT %s, %s, %s, r.revision, r.snapshot_hash, %s, 0, %s, now(), %s, 'test'
            FROM quote_revisions r WHERE r.quote_id = %s AND r.revision = %s
            """,
            (
                uuid4(),
                store_id,
                quote_id,
                latest + 1,
                principal.staff_user_id,
                uuid4(),
                quote_id,
                revision,
            ),
        )
        other.commit()

    # The create now runs against a quote the customer has re-agreed. It must refuse rather than
    # bind the older revision.
    with pytest.raises(OrderStateError) as refusal:
        OrderRepository().create(
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
                AcquisitionSource.WALK_IN,
            ),
        )
    assert "newer price" in str(refusal.value)

    # And nothing was written: no order, and the agreement is still OPEN for whoever prices it next.
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM orders WHERE current_quote_id = %s", (quote_id,))
        count = cursor.fetchone()
        assert count is not None and count[0] == 0
        cursor.execute("SELECT lifecycle FROM quotes WHERE id = %s", (quote_id,))
        lifecycle = cursor.fetchone()
        assert lifecycle is not None and lifecycle[0] == "OPEN"


def test_the_create_guard_holds_a_lock_that_an_acceptance_must_wait_for(connection: Any) -> None:
    """The mechanism, since the interleaving itself cannot be staged from outside.

    `OrderRepository.create` reads its guard `FOR UPDATE OF q`, and the write half of accepting a
    price -- `QuoteRepository.create_revision` -- updates that same `quotes` row. So the two
    serialise on it: whichever arrives second waits, then sees the first's committed state
    and refuses for the right reason. Before the lock, the guard was a plain read whose
    `superseded` finding was never re-asserted at write time, and the mutation's only
    compare-and-swap is `lifecycle = 'OPEN'` -- which says nothing about whether the customer has
    agreed a newer price.

    This holds the guard's own SELECT open on one connection and shows the acceptance's UPDATE
    blocking on another, with a statement timeout standing in for "waits". A `QueryCanceled` here is
    the pass: it means the second writer could not proceed. Without the lock it returns instantly.
    """

    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")

    store_id = uuid4()
    principal = _staff(connection, store_id)
    quote_id, _revision, _quote, _contact = accepted_quote(
        connection, store_id=store_id, principal=principal
    )
    connection.commit()

    holder = psycopg.connect(database_url)
    try:
        with holder.cursor() as cursor:
            # The same lock the create guard takes, on the same row.
            cursor.execute("SELECT id FROM quotes WHERE id = %s FOR UPDATE", (quote_id,))
            assert cursor.fetchone() is not None

            with psycopg.connect(database_url) as accepter, accepter.cursor() as writer:
                writer.execute("SET statement_timeout = '750ms'")
                with pytest.raises(psycopg.errors.QueryCanceled):
                    # What `create_revision` does when a price is accepted.
                    writer.execute(
                        """
                        UPDATE quotes SET row_version = row_version + 1
                        WHERE id = %s AND lifecycle = 'OPEN'
                        """,
                        (quote_id,),
                    )
    finally:
        holder.rollback()
        holder.close()
