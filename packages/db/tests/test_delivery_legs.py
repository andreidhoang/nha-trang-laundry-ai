"""A delivery order can be closed, and only by the leg that means the customer has their laundry.

`DEC-023`, resolved 2026-08-26. Before this, `required_delivery_legs_succeeded` was read in three
places, defaulted FALSE and was written nowhere, so no delivery order could ever complete.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegError,
    DeliveryLegKind,
    DeliveryLegOutcome,
    DeliveryLegRepository,
    RecordDeliveryLegCommand,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository, OrderTransitionCommand
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness
from quote_test_data import accepted_quote

# Inside the fixture quote's validity window: `quote_test_data` prices at 2026-08-01 and the
# order guard refuses a quote that expired before the customer accepted it.
NOW = datetime(2026, 8, 1, 3, tzinfo=UTC)
READY = IntakeReadiness(True, True, True, True, True, True)


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


def _row_version(connection: Any, order_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT row_version FROM orders WHERE id = %s", (order_id,))
        return int(cursor.fetchone()[0])


def _active_order(
    connection: Any, store_id: UUID, staff: StaffPrincipal, mode: FulfillmentMode
) -> UUID:
    """An order taken, accepted and made ACTIVE, which is where a delivery can be recorded."""

    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, fulfillment_mode=mode
    )
    stored = OrderRepository().create(
        connection,
        CreateOrderCommand(
            store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            mode,
            staff,
            f"order-{uuid4().hex}",
            uuid4(),
            NOW,
        ),
    )
    version = stored.row_version
    for keywords in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "intake_readiness": READY,
            "production_accepted_at": NOW,
        },
        {"commercial_target": CommercialOrderStatus.STORE_CONFIRMATION_PENDING},
        {"commercial_target": CommercialOrderStatus.CONFIRMED},
        {"commercial_target": CommercialOrderStatus.ACTIVE},
    ):
        version = (
            OrderRepository()
            .transition(
                connection,
                OrderTransitionCommand(
                    stored.order_id, version, staff, f"move-{uuid4().hex}", uuid4(), **keywords
                ),
            )
            .row_version
        )
    return stored.order_id


def test_only_a_succeeded_return_leg_lets_the_order_be_completed(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """A failed attempt is a row and nothing more; the retry is what finishes the job."""

    store_id = uuid4()
    staff = _member(postgres_connection, store_id, StaffRole.OPERATOR)
    order_id = _active_order(
        postgres_connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN
    )
    repository = DeliveryLegRepository()

    failed = repository.record(
        postgres_connection,
        RecordDeliveryLegCommand(
            order_id, DeliveryLegKind.RETURN, DeliveryLegOutcome.FAILED, staff, uuid4(), NOW
        ),
    )
    assert failed.completes_fulfillment is False

    pickup = repository.record(
        postgres_connection,
        RecordDeliveryLegCommand(
            order_id, DeliveryLegKind.PICKUP, DeliveryLegOutcome.SUCCEEDED, staff, uuid4(), NOW
        ),
    )
    # Collecting the laundry is not delivering it.
    assert pickup.completes_fulfillment is False

    succeeded = repository.record(
        postgres_connection,
        RecordDeliveryLegCommand(
            order_id, DeliveryLegKind.RETURN, DeliveryLegOutcome.SUCCEEDED, staff, uuid4(), NOW
        ),
    )
    assert succeeded.completes_fulfillment is True

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT required_delivery_legs_succeeded FROM orders WHERE id = %s", (order_id,)
        )
        flag = cursor.fetchone()
        assert flag is not None and flag[0] is True
        cursor.execute("SELECT count(*) FROM delivery_legs WHERE order_id = %s", (order_id,))
        counted = cursor.fetchone()
        assert counted is not None and counted[0] == 3


def test_a_walk_in_order_cannot_have_a_delivery_recorded(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The customer brought it and takes it home. There is no delivery to attest."""

    store_id = uuid4()
    staff = _member(postgres_connection, store_id, StaffRole.OPERATOR)
    order_id = _active_order(
        postgres_connection, store_id, staff, FulfillmentMode.SELF_DROP_SELF_COLLECT
    )
    with pytest.raises(DeliveryLegError):
        DeliveryLegRepository().record(
            postgres_connection,
            RecordDeliveryLegCommand(
                order_id, DeliveryLegKind.RETURN, DeliveryLegOutcome.SUCCEEDED, staff, uuid4(), NOW
            ),
        )


def test_the_same_return_cannot_succeed_twice(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """One delivery, one success. A second would be a second handover that did not happen.

    It must refuse as a typed domain error, not as a raw `psycopg.errors.UniqueViolation`. It did
    the latter until 2026-08-29, and the route turned it into HTTP 500 -- so an operator recording
    a delivery twice saw a crash instead of being told it was already recorded.
    """

    store_id = uuid4()
    staff = _member(postgres_connection, store_id, StaffRole.OPERATOR)
    order_id = _active_order(
        postgres_connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN
    )
    repository = DeliveryLegRepository()
    repository.record(
        postgres_connection,
        RecordDeliveryLegCommand(
            order_id, DeliveryLegKind.RETURN, DeliveryLegOutcome.SUCCEEDED, staff, uuid4(), NOW
        ),
    )
    with pytest.raises(DeliveryLegError):
        repository.record(
            postgres_connection,
            RecordDeliveryLegCommand(
                order_id, DeliveryLegKind.RETURN, DeliveryLegOutcome.SUCCEEDED, staff, uuid4(), NOW
            ),
        )


def test_a_non_member_cannot_record_a_delivery(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _member(postgres_connection, store_id, StaffRole.OPERATOR)
    order_id = _active_order(
        postgres_connection, store_id, staff, FulfillmentMode.PICKUP_AND_RETURN
    )
    outsider = _member(postgres_connection, uuid4(), StaffRole.OPERATOR)
    with pytest.raises(StoreAccessError):
        DeliveryLegRepository().record(
            postgres_connection,
            RecordDeliveryLegCommand(
                order_id,
                DeliveryLegKind.RETURN,
                DeliveryLegOutcome.SUCCEEDED,
                outsider,
                uuid4(),
                NOW,
            ),
        )


def test_a_pickup_leg_is_refused_on_an_order_the_customer_brings_in(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """COUNTER-DEFECTS-001: the mirror of the return-leg rule, which did not exist.

    `RETURN_ONLY` means the customer carries the laundry to the shop and the shop delivers it back.
    There is no collection to record. The mode check ran on `RETURN` legs only, so a `PICKUP` leg
    was written as a durable append-only row with its outbox event -- a collection nobody made,
    against an order that closes on a `RETURN` leg and was therefore not advanced by it either.
    """

    store_id = uuid4()
    staff = _member(postgres_connection, store_id, StaffRole.OPERATOR)
    order_id = _active_order(postgres_connection, store_id, staff, FulfillmentMode.RETURN_ONLY)
    repository = DeliveryLegRepository()

    with pytest.raises(DeliveryLegError, match="no pickup leg"):
        repository.record(
            postgres_connection,
            RecordDeliveryLegCommand(
                order_id, DeliveryLegKind.PICKUP, DeliveryLegOutcome.SUCCEEDED, staff, uuid4(), NOW
            ),
        )

    # The leg the mode does expect is still accepted; a rule that refuses both closes the shop.
    returned = repository.record(
        postgres_connection,
        RecordDeliveryLegCommand(
            order_id, DeliveryLegKind.RETURN, DeliveryLegOutcome.SUCCEEDED, staff, uuid4(), NOW
        ),
    )
    assert returned.completes_fulfillment is True
