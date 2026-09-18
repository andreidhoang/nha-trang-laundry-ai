"""INCIDENT-INTAKE-001 / DEC-028: a complaint taken at the counter, and what happens to it later.

Until `DEC-028` this path could not be exercised at all. The route required `contact_scope_hash` and
`evidence_summary_hash` and nothing in the system produced either, so a staff member facing a
customer whose shirt came back stained had two required `sha256:` fields and nowhere to get them.
These tests are the first that open an incident the way the counter does.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.incidents import (
    IncidentRepository,
    StaffIncidentOpenCommand,
    contact_scope_digest,
    evidence_summary_digest,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository
from nha_trang_laundry_db.retention import (
    PurgeOutcome,
    RetentionClass,
    RetentionDisposition,
    RetentionRepository,
)
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode
from quote_test_data import PRICED_AT, accepted_quote

NOW = datetime(2026, 9, 18, 3, tzinfo=UTC)
SUMMARY = "Áo sơ mi trắng bị ố vàng ở cổ, khách giao hôm 12/9."


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _row(cursor: Any) -> tuple[Any, ...]:
    row = cursor.fetchone()
    assert row is not None
    return cast(tuple[Any, ...], row)


def _owner(connection: Any, store_id: UUID) -> StaffPrincipal:
    """An OWNER_ADMIN who is actually a member of the store, which every path here requires."""

    staff_user_id, assigner_id = uuid4(), uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Giặt Là Sạch Cộng",
        created_by=None,
        correlation_id=uuid4(),
    )
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_user_id, assigner_id):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Chủ tiệm', 'ACTIVE', %s)
                """,
                (identifier, f"oidc-{identifier}", NOW),
            )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (staff_user_id, store_id, assigner_id, NOW),
        )
    return StaffPrincipal(
        staff_user_id,
        f"oidc-{staff_user_id}",
        frozenset({StaffRole.OWNER_ADMIN}),
        True,
    )


def _order(connection: Any, *, store_id: UUID, principal: StaffPrincipal) -> tuple[UUID, UUID]:
    """One real order, priced and accepted the way production produces one. Returns (order, ticket).

    Built through `accepted_quote` rather than by inserting a row, because `orders.bound_contact_id`
    must name a counter ticket this store issued -- and that binding is exactly what `DEC-028`
    derives the incident's contact scope from. A fixture that invented the binding would be testing
    a chain no customer could walk.
    """

    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=principal
    )
    created = OrderRepository().create(
        connection,
        CreateOrderCommand(
            store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            FulfillmentMode.SELF_DROP_SELF_COLLECT,
            principal,
            f"order-{uuid4().hex}",
            uuid4(),
            PRICED_AT + timedelta(hours=1),
            AcquisitionSource.WALK_IN,
        ),
    )
    return created.order_id, contact_id


def _open(
    connection: Any,
    *,
    store_id: UUID,
    order_id: UUID,
    principal: StaffPrincipal,
    summary: str = SUMMARY,
    opened_at: datetime | None = None,
) -> UUID:
    return (
        IncidentRepository()
        .open_from_counter(
            connection,
            StaffIncidentOpenCommand(
                store_id=store_id,
                order_id=order_id,
                evidence_summary=summary,
                actor_id=principal.staff_user_id,
                correlation_id=uuid4(),
                opened_at=opened_at or NOW,
            ),
            principal=principal,
        )
        .incident_id
    )


def test_a_staff_member_can_record_a_complaint_against_an_order(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The week-one event the console could not handle: a customer complains at the counter."""

    store_id = uuid4()
    owner = _owner(postgres_connection, store_id)
    order_id, contact_id = _order(postgres_connection, store_id=store_id, principal=owner)

    incident_id = _open(postgres_connection, store_id=store_id, order_id=order_id, principal=owner)

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.contact_scope_hash, i.evidence_summary_hash, i.category, i.status, e.summary
              FROM customer_incidents i
              JOIN customer_incident_evidence e ON e.incident_id = i.id
             WHERE i.id = %s
            """,
            (incident_id,),
        )
        scope, evidence, category, status, summary = _row(cursor)
        listed = IncidentRepository.list_for_store(
            cursor, store_id=store_id, principal=owner, limit=10
        )

    # Derived from the order's binding, not from anything the caller said.
    assert scope == contact_scope_digest(contact_id)
    assert evidence == evidence_summary_digest(SUMMARY)
    assert (category, status) == ("SERVICE_QUALITY", "OPEN")
    assert summary == SUMMARY
    # And it reads back on the screen the staff member will look at next.
    assert [item.incident_id for item in listed] == [incident_id]
    assert listed[0].evidence_summary == SUMMARY


def test_the_summary_never_reaches_a_ledger_that_outlives_its_schedule(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """`DEC-018`'s shape, avoided rather than discovered later.

    The summary is personal data on a 365-day schedule. A copy in the domain event, the audit row or
    the outbox row would sit in a table `reject_ledger_mutation` never lets anyone delete, and the
    purge would then be a false statement -- exactly what held `ASSISTANT_TRANSCRIPT` back.
    """

    store_id = uuid4()
    owner = _owner(postgres_connection, store_id)
    order_id, _ = _order(postgres_connection, store_id=store_id, principal=owner)

    incident_id = _open(postgres_connection, store_id=store_id, order_id=order_id, principal=owner)

    with postgres_connection.cursor() as cursor:
        for table, column in (
            ("domain_events", "payload"),
            ("audit_events", "details"),
            ("outbox_events", "payload"),
        ):
            cursor.execute(
                f"SELECT count(*) FROM {table} WHERE aggregate_id = %s AND {column}::text ILIKE %s",
                (incident_id, "%ố vàng%"),
            )
            assert _row(cursor) == (0,), f"{table}.{column} carries the complaint text"


def test_two_orders_of_one_walk_in_ticket_share_a_scope_and_two_tickets_do_not(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """`DEC-013` decided the ticket IS the walk-in customer; `DEC-028` applies that, not a new rule.

    The consequence is worth asserting rather than assuming: two complaints from the same person on
    two different visits hash differently, because the shop issued two tickets and deliberately
    stores no name, phone or address that could link them. That is the privacy posture DEC-013
    chose, visible here as a grouping limit.
    """

    store_id = uuid4()
    owner = _owner(postgres_connection, store_id)
    first_order, first_ticket = _order(postgres_connection, store_id=store_id, principal=owner)
    second_order, second_ticket = _order(postgres_connection, store_id=store_id, principal=owner)

    first = _open(postgres_connection, store_id=store_id, order_id=first_order, principal=owner)
    second = _open(postgres_connection, store_id=store_id, order_id=second_order, principal=owner)

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, contact_scope_hash FROM customer_incidents WHERE id IN (%s, %s)",
            (first, second),
        )
        scopes = {row[0]: row[1] for row in cursor.fetchall()}

    assert first_ticket != second_ticket
    assert scopes[first] != scopes[second]
    assert scopes[first] == contact_scope_digest(first_ticket)
    assert scopes[second] == contact_scope_digest(second_ticket)


def test_an_incident_in_another_store_is_refused_before_anything_is_written(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    owner = _owner(postgres_connection, store_id)
    order_id, _ = _order(postgres_connection, store_id=store_id, principal=owner)
    other_store = uuid4()
    _owner(postgres_connection, other_store)

    with pytest.raises(StoreAccessError):
        _open(postgres_connection, store_id=other_store, order_id=order_id, principal=owner)

    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM customer_incidents")
        assert _row(cursor) == (0,)


def test_an_order_from_another_store_cannot_be_complained_about(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Membership alone is not enough: the order must belong to the store that is naming it."""

    store_id, other_store = uuid4(), uuid4()
    owner = _owner(postgres_connection, store_id)
    other_owner = _owner(postgres_connection, other_store)
    foreign_order, _ = _order(postgres_connection, store_id=other_store, principal=other_owner)

    with pytest.raises(ValueError, match="order binding"):
        _open(postgres_connection, store_id=store_id, order_id=foreign_order, principal=owner)


def test_the_summary_is_normalized_before_it_is_hashed_and_stored(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Vietnamese arrives from a browser in either normalization, depending on the input method.

    Two spellings of the same sentence must not produce two different incidents with two different
    hashes, and the stored text must be the text the hash commits to -- which is why the
    normalization happens before both, not between them.
    """

    store_id = uuid4()
    owner = _owner(postgres_connection, store_id)
    order_id, _ = _order(postgres_connection, store_id=store_id, principal=owner)
    decomposed = unicodedata.normalize("NFD", SUMMARY)
    assert decomposed != SUMMARY

    incident_id = _open(
        postgres_connection,
        store_id=store_id,
        order_id=order_id,
        principal=owner,
        summary=decomposed,
    )

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.evidence_summary_hash, e.summary
              FROM customer_incidents i
              JOIN customer_incident_evidence e ON e.incident_id = i.id
             WHERE i.id = %s
            """,
            (incident_id,),
        )
        stored_hash, stored = _row(cursor)

    assert stored == SUMMARY
    assert stored_hash == evidence_summary_digest(SUMMARY)


@pytest.mark.parametrize("summary", ["", "   ", "\n\t "])
def test_an_empty_complaint_is_refused(
    postgres_connection: psycopg.Connection[Any], summary: str
) -> None:
    """An incident with no description is a tally mark, which is the answer DEC-028 rejected."""

    store_id = uuid4()
    owner = _owner(postgres_connection, store_id)
    order_id, _ = _order(postgres_connection, store_id=store_id, principal=owner)

    with pytest.raises(ValueError, match="summary"):
        _open(
            postgres_connection,
            store_id=store_id,
            order_id=order_id,
            principal=owner,
            summary=summary,
        )


def test_a_recorded_complaint_cannot_be_rewritten(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Evidence that can be edited after the fact is not evidence of anything.

    Two guards, both added by `0039`: the summary itself is immutable while it exists, and the
    incident's binding, evidence hash and opening time are immutable for as long as the row does.
    The lifecycle columns stay writable, because an incident is meant to move.
    """

    store_id = uuid4()
    owner = _owner(postgres_connection, store_id)
    order_id, _ = _order(postgres_connection, store_id=store_id, principal=owner)
    incident_id = _open(postgres_connection, store_id=store_id, order_id=order_id, principal=owner)

    for statement, parameters in (
        (
            "UPDATE customer_incident_evidence SET summary = %s WHERE incident_id = %s",
            ("Không có gì cả.", incident_id),
        ),
        (
            "UPDATE customer_incidents SET evidence_summary_hash = %s WHERE id = %s",
            (f"sha256:{'0' * 64}", incident_id),
        ),
        (
            "UPDATE customer_incidents SET opened_at = %s WHERE id = %s",
            (NOW - timedelta(days=400), incident_id),
        ),
    ):
        with (
            pytest.raises(psycopg.errors.RaiseException),
            postgres_connection.transaction(),
            postgres_connection.cursor() as cursor,
        ):
            cursor.execute(statement, parameters)

    # The lifecycle still moves, which is the whole reason the guard is per-column.
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            "UPDATE customer_incidents SET status = 'CLOSED', fault_decided = TRUE WHERE id = %s",
            (incident_id,),
        )
        assert cursor.rowcount == 1


def _enable_incident_purge(connection: Any, owner: StaffPrincipal) -> RetentionRepository:
    repository = RetentionRepository()
    repository.publish_configuration(
        connection,
        class_name=RetentionClass.INCIDENT_EVIDENCE,
        disposition=RetentionDisposition.PURGE,
        principal=owner,
        correlation_id=uuid4(),
        enabled=True,
        retention_days=365,
        decision_ref="DEC-008",
    )
    for hold_id in repository.active_hold_ids(
        connection, class_name=RetentionClass.INCIDENT_EVIDENCE
    ):
        repository.release_legal_hold(
            connection, hold_id=hold_id, principal=owner, correlation_id=uuid4()
        )
    return repository


def test_a_closed_complaint_loses_its_description_and_keeps_its_record(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The point of putting the summary in a side table, exercised end to end.

    `DEC-008` schedules `INCIDENT_EVIDENCE` at 365 days. After the purge the shop can still see that
    a complaint was made, about which order, when, what was decided and that its description was
    disposed of under a named schedule. That is an account, not a gap -- and it is the reason
    storing the text was the right answer rather than the risky one.
    """

    store_id = uuid4()
    owner = _owner(postgres_connection, store_id)
    order_id, _ = _order(postgres_connection, store_id=store_id, principal=owner)
    incident_id = _open(
        postgres_connection,
        store_id=store_id,
        order_id=order_id,
        principal=owner,
        opened_at=datetime.now(UTC) - timedelta(days=400),
    )
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            "UPDATE customer_incidents SET status = 'CLOSED' WHERE id = %s", (incident_id,)
        )
    repository = _enable_incident_purge(postgres_connection, owner)

    run = repository.run_purge(
        postgres_connection, class_name=RetentionClass.INCIDENT_EVIDENCE, correlation_id=uuid4()
    )
    record = repository.disposal_record(
        postgres_connection, subject_table="customer_incidents", subject_key=incident_id
    )

    assert (run.outcome, run.affected_row_count) == (PurgeOutcome.COMPLETED, 1)
    assert record is not None and record.decision_ref == "DEC-008"
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT evidence_summary_hash, status FROM customer_incidents WHERE id = %s",
            (incident_id,),
        )
        stored_hash, status = _row(cursor)
        listed = IncidentRepository.list_for_store(
            cursor, store_id=store_id, principal=owner, limit=10
        )

    # The complaint is gone; the fact that there was one, and about what, is not.
    assert stored_hash == evidence_summary_digest(SUMMARY)
    assert status == "CLOSED"
    assert [item.incident_id for item in listed] == [incident_id]
    assert listed[0].evidence_summary is None


def test_an_unfinished_complaint_holds_its_own_evidence(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """`DEC-018` generalised, and reported rather than passed over in silence.

    Deleting the description of a complaint whose fault and remedy nobody has decided destroys the
    only account of what was actually wrong, and `DEC-004` governs remedies that can be owed later.
    So an incident holds its evidence until it is CLOSED, and the run says so instead of calling
    itself COMPLETED over a silent exemption.
    """

    store_id = uuid4()
    owner = _owner(postgres_connection, store_id)
    order_id, _ = _order(postgres_connection, store_id=store_id, principal=owner)
    incident_id = _open(
        postgres_connection,
        store_id=store_id,
        order_id=order_id,
        principal=owner,
        opened_at=datetime.now(UTC) - timedelta(days=400),
    )
    repository = _enable_incident_purge(postgres_connection, owner)

    run = repository.run_purge(
        postgres_connection, class_name=RetentionClass.INCIDENT_EVIDENCE, correlation_id=uuid4()
    )

    assert run.outcome is PurgeOutcome.COMPLETED_WITH_EXEMPTIONS
    assert (run.affected_row_count, run.exempt_row_count) == (0, 1)
    assert run.detail is not None and "CLOSED" in run.detail
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM customer_incident_evidence WHERE incident_id = %s",
            (incident_id,),
        )
        assert _row(cursor) == (1,)
