"""`STORE-SCOPING-002`: the order transition route was a cross-store write.

`OrderRepository.create` and `list_for_store` require store membership. `transition` did not, and
it is the one that changes state. Any principal holding an operations role with MFA could confirm,
cancel or complete an order in a store they have no assignment to, by supplying its identifier.

These tests use a real database because the claim is about the repository, not about exception
mapping: a stub that refuses would prove only that the stub refuses.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderAuthorizationError,
    OrderRepository,
    OrderStateError,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    CustodyResolution,
    IntakeStatus,
    FulfillmentMode,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness
from nha_trang_laundry_domain.quote_composition import QUOTE_VALIDITY
from nha_trang_laundry_domain.quotes import ImmutableQuoteSnapshot, build_quote_snapshot
from quote_test_data import PRICED_AT, accepted_quote, make_quote_snapshot

# The moment the customer agreed the price, as the caller reports it. Since COUNTER-DEFECTS-001
# this is an attested fact recorded on the order and no longer decides whether the quote has
# expired -- the server's own clock does that -- so a fixed date here is honest rather than load
# bearing.
NOW = datetime(2026, 8, 1, 3, tzinfo=UTC)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _ensure_store(connection: Any, store_id: UUID) -> None:
    """`STORE-REGISTRY-001`: `store_id` is a foreign key, so the shop exists before anyone joins it.

    The deploy-day runbook runs `scripts/bootstrap_store.py` before assigning anyone, and this is
    the fixture standing in for that step rather than an INSERT that skips it.
    """

    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )


def _staff(connection: Any, store_id: UUID | None) -> StaffPrincipal:
    if store_id is not None:
        _ensure_store(connection, store_id)
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
        if store_id is not None:
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


def _approved_final_quote(quote_id: UUID, approval_id: UUID) -> ImmutableQuoteSnapshot:
    estimate = make_quote_snapshot(quote_id, 1)
    return build_quote_snapshot(
        replace(
            estimate.data,
            finality=QuoteFinality.APPROVED_EXACT,
            status=QuoteRevisionStatus.ACCEPTED_FINAL,
            lines=tuple(
                replace(line, quantity_basis=QuantityBasis.STAFF_MEASUREMENT)
                for line in estimate.data.lines
            ),
            required_approvals=(),
            approval_id=approval_id,
        )
    )


def _order_in_store(connection: Any, store_id: UUID, owner: StaffPrincipal) -> UUID:
    # Priced, then accepted, then ordered -- the shape production produces since QUOTE-ACCEPT-001.
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=owner
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
            owner,
            f"order-create-{uuid4().hex}",
            uuid4(),
            NOW,
        ),
    )
    return stored.order_id


def _transition(connection: Any, order_id: UUID, principal: StaffPrincipal, *, key: str) -> Any:
    return OrderRepository().transition(
        connection,
        OrderTransitionCommand(
            order_id,
            1,
            principal,
            key,
            uuid4(),
            commercial_target=CommercialOrderStatus.STORE_CONFIRMATION_PENDING,
            occurred_at=NOW,
        ),
    )


def test_a_member_of_another_store_cannot_transition_this_order(
    connection: psycopg.Connection[Any],
) -> None:
    """The cross-store write this item exists to close."""
    store_a, store_b = uuid4(), uuid4()
    resident = _staff(connection, store_a)
    outsider = _staff(connection, store_b)
    order_id = _order_in_store(connection, store_a, resident)

    with pytest.raises(OrderAuthorizationError):
        _transition(connection, order_id, outsider, key=f"outsider-{uuid4().hex}")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT commercial_status, row_version FROM orders WHERE id = %s", (order_id,)
        )
        assert cursor.fetchone() == ("REQUESTED", 1), "a refused transition must change nothing"


def test_a_staff_member_with_no_assignment_at_all_is_refused(
    connection: psycopg.Connection[Any],
) -> None:
    store_a = uuid4()
    resident = _staff(connection, store_a)
    unassigned = _staff(connection, None)
    order_id = _order_in_store(connection, store_a, resident)

    with pytest.raises(OrderAuthorizationError):
        _transition(connection, order_id, unassigned, key=f"unassigned-{uuid4().hex}")


def test_a_member_of_the_owning_store_still_succeeds(
    connection: psycopg.Connection[Any],
) -> None:
    """The fix must not be a blanket denial, which is the easy way to pass the test above."""
    store_a = uuid4()
    resident = _staff(connection, store_a)
    order_id = _order_in_store(connection, store_a, resident)

    stored = _transition(connection, order_id, resident, key=f"resident-{uuid4().hex}")
    assert stored.commercial is CommercialOrderStatus.STORE_CONFIRMATION_PENDING
    assert stored.row_version == 2


def test_a_refused_transition_leaves_no_idempotency_claim(
    connection: psycopg.Connection[Any],
) -> None:
    """A refusal must not poison the key, or a later legitimate attempt replays the refusal.

    The membership check runs before `IdempotencyRepository.execute` is reached, so the ledger never
    sees the attempt. Asserted rather than assumed, because the ordering is the whole property.
    """
    store_a, store_b = uuid4(), uuid4()
    resident = _staff(connection, store_a)
    outsider = _staff(connection, store_b)
    order_id = _order_in_store(connection, store_a, resident)
    shared_key = f"contested-{uuid4().hex}"

    with pytest.raises(OrderAuthorizationError):
        _transition(connection, order_id, outsider, key=shared_key)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM command_idempotency_records WHERE idempotency_key = %s",
            (shared_key,),
        )
        claimed = cursor.fetchone()
        assert claimed is not None and int(claimed[0]) == 0

    # And the key still works for someone entitled to use it.
    stored = _transition(connection, order_id, resident, key=shared_key)
    assert stored.commercial is CommercialOrderStatus.STORE_CONFIRMATION_PENDING


def test_membership_is_read_from_the_database_not_from_the_principal(
    connection: psycopg.Connection[Any],
) -> None:
    """The authority is the assignment row, not the `StaffPrincipal` the route hands down.

    `transition` locks the order `FOR UPDATE` and resolves membership on the same cursor while that
    lock is held, so the answer is whatever the database says at that moment. Demonstrated in the
    direction that is actually available: the same in-memory principal is refused before its
    assignment row exists and accepted afterwards, with nothing about the object changing between
    the two calls.

    The reverse direction — proving a *revoked* assignment is refused — cannot be written today,
    and finding that out is worth recording. `staff_store_assignments` has no `revoked_at` column
    and carries `staff_store_assignments_no_hard_delete`, so once a staff member is assigned to a
    store there is no supported way to un-assign them: not a route, not a repository method, not
    even a direct DELETE. Revocation needs a schema change and a command of its own, which is
    outside `STORE-SCOPING-002`; it is recorded in that item's evidence rather than silently worked
    around here.
    """
    store_a = uuid4()
    resident = _staff(connection, store_a)
    order_id = _order_in_store(connection, store_a, resident)
    latecomer = _staff(connection, None)

    with pytest.raises(OrderAuthorizationError):
        _transition(connection, order_id, latecomer, key=f"latecomer-{uuid4().hex}")

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (latecomer.staff_user_id, store_a, resident.staff_user_id, NOW),
        )

    stored = _transition(connection, order_id, latecomer, key=f"latecomer-{uuid4().hex}")
    assert stored.commercial is CommercialOrderStatus.STORE_CONFIRMATION_PENDING


def test_deriving_intake_readiness_hides_another_stores_order(
    connection: psycopg.Connection[Any],
) -> None:
    """A stranger's order and an order that does not exist must fail identically.

    `POST /orders/{id}/intake-transition` derives five readiness facts before
    `OrderRepository.transition` authorizes anything, because the transition needs the readiness to
    decide. That ordering is what makes this necessary: the write is refused either way, but an
    unscoped derivation answered "order is missing" for an identifier nobody issued and something
    else for one another store holds. That difference is an existence oracle, and `store_access`
    states the opposite property as its reason for existing -- the failures are "deliberately
    indistinguishable to the caller, so probing identifiers teaches nobody which stores exist".

    Written against the real database on purpose. The predicate that closes this is a SQL `EXISTS`
    over `staff_store_assignments`; a stub cursor would prove only that the stub returns nothing.
    """
    owner_store = uuid4()
    owner = _staff(connection, owner_store)
    order_id = _order_in_store(connection, owner_store, owner)
    outsider = _staff(connection, uuid4())

    with connection.cursor() as cursor:
        with pytest.raises(OrderStateError) as stranger:
            OperationsService._derive_intake_readiness(
                cursor,
                order_id=order_id,
                staff_user_id=outsider.staff_user_id,
                slot_approved=True,
            )
        with pytest.raises(OrderStateError) as nobodys:
            OperationsService._derive_intake_readiness(
                cursor,
                order_id=uuid4(),
                staff_user_id=outsider.staff_user_id,
                slot_approved=True,
            )

    assert str(stranger.value) == str(nobodys.value)


def test_deriving_intake_readiness_still_works_for_a_member(
    connection: psycopg.Connection[Any],
) -> None:
    """The scoping predicate must not blind the people the route is for.

    A guard that refuses everyone passes the test above and breaks the counter, so the positive
    case is pinned next to the negative one.
    """
    store_id = uuid4()
    owner = _staff(connection, store_id)
    order_id = _order_in_store(connection, store_id, owner)

    with connection.cursor() as cursor:
        readiness = OperationsService._derive_intake_readiness(
            cursor, order_id=order_id, staff_user_id=owner.staff_user_id, slot_approved=True
        )

    # The order was created against an accepted APPROVED_EXACT quote priced from the pricebook off
    # a staff measurement, so every fact the server derives is true and only capacity was attested.
    assert readiness.exact_price_approved
    assert readiness.customer_reconfirmation_satisfied
    assert readiness.quantity_basis_approved
    assert readiness.slot_approved


def test_an_agreement_authorises_exactly_one_order(
    connection: psycopg.Connection[Any],
) -> None:
    """One chốt, one order. Found by adversarial verification on 2026-08-29.

    `quote_acceptances` is UNIQUE on `(quote_id, accepted_revision)` so "who chốt this" has one
    answer. Converting that answer into an order was not single-shot: three POSTs with three fresh
    idempotency keys produced three orders against one attestation, each independently settleable,
    turning 100.000đ agreed once into 300.000đ recorded as collected. The idempotency scope keys on
    the caller's header, so a double-submit or a retry with a regenerated key is a new order.

    `CONVERTED` has been in the `quotes.lifecycle` CHECK since migration `0005` and nothing ever
    wrote it. Spending the agreement is what that value was for.
    """
    store_id = uuid4()
    owner = _staff(connection, store_id)
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=owner
    )

    def create() -> Any:
        return OrderRepository().create(
            connection,
            CreateOrderCommand(
                store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                owner,
                f"order-{uuid4().hex}",
                uuid4(),
                NOW,
            ),
        )

    first = create()
    assert first.order_id is not None

    with pytest.raises(OrderStateError, match="already been converted"):
        create()

    with connection.cursor() as cursor:
        cursor.execute("SELECT lifecycle FROM quotes WHERE id = %s", (quote_id,))
        lifecycle = cursor.fetchone()
        assert lifecycle is not None and str(lifecycle[0]) == "CONVERTED"
        cursor.execute(
            "SELECT count(*) FROM orders"
            " WHERE current_quote_id = %s AND current_quote_revision = %s",
            (quote_id, revision),
        )
        counted = cursor.fetchone()
        assert counted is not None and counted[0] == 1


def test_an_order_cannot_cite_a_price_the_customer_has_moved_on_from(
    connection: psycopg.Connection[Any],
) -> None:
    """The regression this repository introduced on 2026-08-29 and closed on 2026-08-30.

    A quote may be re-priced after chốt -- "thêm cái áo này nữa" -- and accepted again. The guard
    asked only that *some* acceptance named the revision, never that it was still the current one,
    so an order could be created against the superseded agreement. It then settled at the old price
    while the system refused the price the customer had just agreed to, with the order, the
    settlement and the immutable snapshot all agreeing with each other on the stale number.

    It was reachable only because revision-scoping the acceptance outbox key removed a UNIQUE
    collision that had been accidentally preventing a second acceptance. The 500 that fixed was
    real; the accident it removed was load-bearing and nothing replaced it.
    """
    store_id = uuid4()
    owner = _staff(connection, store_id)
    quote_id, superseded_revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=owner
    )

    # The customer adds a shirt: a later acceptance on the same quote.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO quote_acceptances (
                id, store_id, quote_id, accepted_revision, accepted_snapshot_hash,
                final_revision, display_total_vnd, accepted_by, accepted_at, correlation_id,
                policy_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'quote-acceptance-v1')
            """,
            (
                uuid4(),
                store_id,
                quote_id,
                # The later agreement names the revision the fixture accepted and produces the
                # next one, which is the shape a real reprice-and-re-chốt leaves behind. The FK is
                # on (quote_id, accepted_revision), so this must cite a revision that exists.
                superseded_revision,
                quote.document.snapshot_hash,
                superseded_revision + 1,
                50_000,
                owner.staff_user_id,
                NOW,
                uuid4(),
            ),
        )

    with pytest.raises(OrderStateError, match="newer price"):
        OrderRepository().create(
            connection,
            CreateOrderCommand(
                store_id,
                contact_id,
                quote_id,
                superseded_revision,
                quote.document.snapshot_hash,
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                owner,
                f"order-{uuid4().hex}",
                uuid4(),
                NOW,
            ),
        )


def test_an_expired_quote_is_refused_whatever_the_caller_claims(connection: Any) -> None:
    """COUNTER-DEFECTS-001: expiry is the server's decision, not a field in the request body.

    `OrderRepository.create` compared `valid_until` against
    `command.customer_final_quote_accepted_at`, which arrives in the request body and is validated
    only for timezone-awareness. A quote the shop had already withdrawn was therefore orderable by
    naming an earlier acceptance time, and the shop was held to the withdrawn price.
    """

    store_id = uuid4()
    owner = _staff(connection, store_id)
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=owner
    )
    command = CreateOrderCommand(
        store_id,
        contact_id,
        quote_id,
        revision,
        quote.document.snapshot_hash,
        FulfillmentMode.SELF_DROP_SELF_COLLECT,
        owner,
        f"order-create-{uuid4().hex}",
        uuid4(),
        # The caller claims the customer agreed well inside the window. Before this fix that claim
        # was the whole test the guard ran.
        PRICED_AT,
    )

    with pytest.raises(OrderStateError, match="missing, stale, or expired"):
        OrderRepository().create(
            connection, command, evaluated_at=PRICED_AT + QUOTE_VALIDITY + timedelta(seconds=1)
        )

    # And the same quote, read a minute after it was priced, is still orderable -- a guard that
    # refuses everyone would pass this test and close the shop.
    stored = OrderRepository().create(
        connection, command, evaluated_at=PRICED_AT + timedelta(minutes=1)
    )
    assert stored.commercial is CommercialOrderStatus.REQUESTED


def test_an_order_request_reaches_a_terminal_status_with_its_order(connection: Any) -> None:
    """COUNTER-DEFECTS-001: `order_requests.status` was written by nothing.

    Every row was born `DRAFT` and stayed `DRAFT` for its whole life, so `SUBMITTED` and
    `CANCELLED` were unreachable CHECK values. A request whose order had been created -- or
    cancelled -- sat in the intake list indistinguishable from a customer still standing at the
    counter.
    """

    store_id = uuid4()
    owner = _staff(connection, store_id)
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=owner
    )

    def request_status() -> str:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.status FROM order_requests r
                JOIN quotes q ON q.bound_order_request_id = r.id
                WHERE q.id = %s
                """,
                (quote_id,),
            )
            row = cursor.fetchone()
        assert row is not None
        return str(row[0])

    stored = OrderRepository().create(
        connection,
        CreateOrderCommand(
            store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            FulfillmentMode.SELF_DROP_SELF_COLLECT,
            owner,
            f"order-create-{uuid4().hex}",
            uuid4(),
            NOW,
        ),
    )
    assert request_status() == "SUBMITTED"

    OrderRepository().transition(
        connection,
        OrderTransitionCommand(
            stored.order_id,
            stored.row_version,
            owner,
            f"cancel-{uuid4().hex}",
            uuid4(),
            commercial_target=CommercialOrderStatus.CANCELLED,
        ),
    )
    assert request_status() == "CANCELLED"


def test_what_happened_to_the_laundry_is_part_of_the_command_not_decoration(
    connection: Any,
) -> None:
    """`custody_resolution` decides both the approval and what the ledger records.

    It was left out of the hashed idempotency payload, so two cancellations differing only in
    their resolution produced the same digest: the second returned the first's stored response
    with HTTP 200, and the ledger kept the first resolution while the staff member who pressed the
    button had recorded a different one. Nothing reported a conflict.
    """

    store_id, repository = uuid4(), OrderRepository()
    _ensure_store(connection, store_id)
    owner = _staff(connection, store_id)
    order_id = _order_in_store(connection, store_id, owner)

    def move(version: int, **fields: Any) -> Any:
        return repository.transition(
            connection,
            OrderTransitionCommand(
                order_id,
                version,
                owner,
                f"move-{uuid4().hex}",
                uuid4(),
                occurred_at=NOW + timedelta(minutes=version),
                **fields,
            ),
        )

    move(1, commercial_target=CommercialOrderStatus.STORE_CONFIRMATION_PENDING)
    move(2, commercial_target=CommercialOrderStatus.CONFIRMED)
    # Handoff is recorded before acceptance -- the shop takes custody here, not at ACCEPTED.
    move(3, intake_target=IntakeStatus.RECEIVED_PENDING_INSPECTION)
    move(
        4,
        intake_target=IntakeStatus.ACCEPTED,
        intake_readiness=IntakeReadiness(True, True, True, True, True, True),
        production_accepted_at=NOW + timedelta(minutes=4),
    )
    move(5, commercial_target=CommercialOrderStatus.ACTIVE)
    move(6, commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW)

    # Production never started, so "returned unwashed and refunded" is true of this order.
    cancel = OrderTransitionCommand(
        order_id,
        7,
        owner,
        f"cancel-{uuid4().hex}",
        uuid4(),
        commercial_target=CommercialOrderStatus.CANCELLED,
        custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
        occurred_at=NOW + timedelta(minutes=7),
    )
    cancelled = repository.transition(connection, cancel)
    assert cancelled.commercial is CommercialOrderStatus.CANCELLED

    # The same command replayed is still one cancellation.
    assert repository.transition(connection, replace(cancel, correlation_id=uuid4())).replayed

    # The same key claiming a different thing happened to the customer's goods is a conflict, not
    # a replay. Before the fix this returned the stored response and reported success.
    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        repository.transition(
            connection,
            replace(
                cancel,
                custody_resolution=CustodyResolution.NOT_RECEIVED,
                correlation_id=uuid4(),
            ),
        )
