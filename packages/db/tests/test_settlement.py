"""`SETTLEMENT-001`: an order can finish, for the one case that is not a policy question.

Measured on 2026-08-14 and provable from three files: no order this system creates could ever reach
`COMPLETED`. `balance_status` was hardcoded `'UNPAID'` at insert, the two fulfilment flags took
their `DEFAULT FALSE`, and the only `UPDATE orders` statement touched none of the three. The guard
was correct and unsatisfiable.

The headline test walks an order from creation to `COMPLETED` through every intermediate transition,
because that is the claim. The rest are refusals, which is where the risk actually is: settlement is
where scope creep into the business owner's decisions would hurt most.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderStateError,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.settlement import (
    CollectedToday,
    SettlementAuthorizationError,
    SettlementCommand,
    SettlementRepository,
    SettlementStateError,
)
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    FulfillmentMode,
    IntakeStatus,
    ProductionStatus,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness
from nha_trang_laundry_domain.quotes import ImmutableQuoteSnapshot, build_quote_snapshot
from quote_test_data import accepted_quote, make_quote_snapshot

NOW = datetime(2026, 8, 1, 3, tzinfo=UTC)
#: Every intake blocker cleared. Intake acceptance is gated on six separate facts; this test is
#: about settlement, so they are all satisfied rather than exercised.
READY = IntakeReadiness(True, True, True, True, True, True)
#: `make_quote_snapshot` bills 100,000 of service and adds a 10,000 delivery fee.
QUOTED_TOTAL = 110_000


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(connection: Any, store_id: UUID | None, role: StaffRole) -> StaffPrincipal:
    staff_id, assigner = uuid4(), uuid4()
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
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), True, uuid4())


def _approved_quote(
    quote_id: UUID, approval_id: UUID, *, delivery_fee: bool = True
) -> ImmutableQuoteSnapshot:
    estimate = make_quote_snapshot(quote_id, 1)
    data = replace(
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
    return build_quote_snapshot(data)


def _order(connection: Any, store_id: UUID, staff: StaffPrincipal) -> UUID:
    # Priced, then accepted, then ordered -- the shape production produces since QUOTE-ACCEPT-001.
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff
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
            staff,
            f"order-{uuid4().hex}",
            uuid4(),
            NOW,
        ),
    )
    return stored.order_id


def _advance(
    connection: Any, order_id: UUID, staff: StaffPrincipal, version: int, **target: Any
) -> Any:
    return OrderRepository().transition(
        connection,
        OrderTransitionCommand(
            order_id, version, staff, f"step-{uuid4().hex}", uuid4(), occurred_at=NOW, **target
        ),
    )


def _ready_active_order(connection: Any, store_id: UUID, staff: StaffPrincipal) -> tuple[UUID, int]:
    """Drive an order to ACTIVE with production RELEASED, through every real transition."""
    order_id = _order(connection, store_id, staff)
    version = 1
    for step in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "production_accepted_at": NOW,
            "intake_readiness": READY,
        },
        {"commercial_target": CommercialOrderStatus.STORE_CONFIRMATION_PENDING},
        {"commercial_target": CommercialOrderStatus.CONFIRMED},
        {"commercial_target": CommercialOrderStatus.ACTIVE},
        {"production_target": ProductionStatus.QUEUED},
        {"production_target": ProductionStatus.IN_PROCESS},
        {"production_target": ProductionStatus.QUALITY_CHECK},
        {"production_target": ProductionStatus.READY_AT_STORE},
        {"production_target": ProductionStatus.RELEASED},
    ):
        stored = _advance(connection, order_id, staff, version, **step)
        version = stored.row_version
    return order_id, version


def _settle(
    connection: Any,
    order_id: UUID,
    staff: StaffPrincipal,
    *,
    amount: int = QUOTED_TOTAL,
    collected: bool = True,
    attested_at: datetime = NOW,
) -> Any:
    return SettlementRepository().record(
        connection,
        SettlementCommand(
            order_id=order_id,
            paid_amount_vnd=amount,
            collected_by_customer=collected,
            principal=staff,
            correlation_id=uuid4(),
            attested_at=attested_at,
        ),
    )


def _order_row(connection: Any, order_id: UUID) -> tuple[str, str, bool, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT commercial_status, balance_status, self_collection_recorded, row_version
            FROM orders WHERE id = %s
            """,
            (order_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return str(row[0]), str(row[1]), bool(row[2]), int(row[3])


# --- order_reaches_completed_through_normal_path -----------------------------------------------


def test_an_order_reaches_completed_after_settlement(
    connection: psycopg.Connection[Any],
) -> None:
    """The claim of this item, end to end and through every intermediate transition."""
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order_id, version = _ready_active_order(connection, store_id, staff)

    assert _order_row(connection, order_id)[:3] == ("ACTIVE", "UNPAID", False)

    settlement = _settle(connection, order_id, staff)
    assert settlement.expected_total_vnd == QUOTED_TOTAL
    assert settlement.paid_amount_vnd == QUOTED_TOTAL
    assert settlement.settlement_shape == "EXACT_PAYMENT_SELF_COLLECTION"

    commercial, balance, collected, version = _order_row(connection, order_id)
    assert (commercial, balance, collected) == ("ACTIVE", "PAID", True)

    completed = _advance(
        connection,
        order_id,
        staff,
        version,
        commercial_target=CommercialOrderStatus.COMPLETED,
    )
    assert completed.commercial is CommercialOrderStatus.COMPLETED
    with connection.cursor() as cursor:
        cursor.execute("SELECT closed_at FROM orders WHERE id = %s", (order_id,))
        closed = cursor.fetchone()
    assert closed is not None and closed[0] is not None


def test_the_completion_guard_was_not_widened(connection: psycopg.Connection[Any]) -> None:
    """Settlement makes the guard satisfiable; it must not make it weaker.

    An order that is paid and collected but whose production never reached RELEASED is still
    refused. If this test ever needs the guard relaxed, the test is wrong.
    """
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order_id = _order(connection, store_id, staff)
    version = 1
    for step in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "production_accepted_at": NOW,
            "intake_readiness": READY,
        },
        {"commercial_target": CommercialOrderStatus.STORE_CONFIRMATION_PENDING},
        {"commercial_target": CommercialOrderStatus.CONFIRMED},
        {"commercial_target": CommercialOrderStatus.ACTIVE},
        {"production_target": ProductionStatus.QUEUED},
    ):
        version = _advance(connection, order_id, staff, version, **step).row_version

    _settle(connection, order_id, staff)
    _, balance, collected, version = _order_row(connection, order_id)
    assert (balance, collected) == ("PAID", True)

    with pytest.raises(OrderStateError, match="production is not released"):
        _advance(
            connection,
            order_id,
            staff,
            version,
            commercial_target=CommercialOrderStatus.COMPLETED,
        )


# --- every_other_settlement_shape_not_supported ------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "reason"),
    [
        (QUOTED_TOTAL - 1, "AMOUNT_IS_NOT_THE_EXACT_TOTAL"),
        (QUOTED_TOTAL + 1, "AMOUNT_IS_NOT_THE_EXACT_TOTAL"),
        (0, "AMOUNT_IS_NOT_THE_EXACT_TOTAL"),
        (QUOTED_TOTAL // 2, "AMOUNT_IS_NOT_THE_EXACT_TOTAL"),
    ],
)
def test_an_amount_other_than_the_quoted_total_is_refused(
    connection: psycopg.Connection[Any], amount: int, reason: str
) -> None:
    """Under, over, deposit, half: all DEC-010, none rounded and none partially recorded."""
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order_id, _ = _ready_active_order(connection, store_id, staff)

    with pytest.raises(SettlementStateError) as raised:
        _settle(connection, order_id, staff, amount=amount)
    assert raised.value.reason_code == reason
    assert raised.value.decision == "DEC-010"

    assert _order_row(connection, order_id)[1] == "UNPAID"
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM order_settlements WHERE order_id = %s", (order_id,))
        written = cursor.fetchone()
    assert written is not None and int(written[0]) == 0


def test_goods_that_did_not_leave_with_the_customer_are_refused(
    connection: psycopg.Connection[Any],
) -> None:
    """The other branch of the guard is a delivery leg, and delivery is DEC-003 and unbuilt."""
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order_id, _ = _ready_active_order(connection, store_id, staff)

    with pytest.raises(SettlementStateError) as raised:
        _settle(connection, order_id, staff, collected=False)
    assert raised.value.reason_code == "COLLECTION_WAS_NOT_BY_THE_CUSTOMER"
    assert raised.value.decision == "DEC-003"
    assert _order_row(connection, order_id)[1] == "UNPAID"


def test_an_order_that_is_not_active_cannot_be_settled(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order_id = _order(connection, store_id, staff)

    with pytest.raises(SettlementStateError) as raised:
        _settle(connection, order_id, staff)
    assert raised.value.reason_code == "ORDER_NOT_ACTIVE"


# --- settlement_record_append_only_and_idempotent ----------------------------------------------


def test_settling_twice_is_refused_and_leaves_one_record(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order_id, _ = _ready_active_order(connection, store_id, staff)

    _settle(connection, order_id, staff)
    with pytest.raises(SettlementStateError) as raised:
        _settle(connection, order_id, staff)
    assert raised.value.reason_code == "ALREADY_SETTLED"

    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM order_settlements WHERE order_id = %s", (order_id,))
        rows = cursor.fetchone()
        cursor.execute(
            """
            SELECT count(*) FROM audit_events
            WHERE aggregate_type = 'ORDER_SETTLEMENT' AND aggregate_id = %s
            """,
            (order_id,),
        )
        audits = cursor.fetchone()
    assert rows is not None and int(rows[0]) == 1
    assert audits is not None and int(audits[0]) == 1


def test_a_settlement_record_cannot_be_updated_or_deleted(
    connection: psycopg.Connection[Any],
) -> None:
    """A settlement recorded in error is an incident, not an edit."""
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order_id, _ = _ready_active_order(connection, store_id, staff)
    _settle(connection, order_id, staff)

    for statement in (
        "UPDATE order_settlements SET paid_amount_vnd = 1 WHERE order_id = %s",
        "DELETE FROM order_settlements WHERE order_id = %s",
    ):
        with (
            pytest.raises(psycopg.errors.RaiseException),
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, (order_id,))


def test_the_ledger_records_the_settlement_atomically(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order_id, _ = _ready_active_order(connection, store_id, staff)
    _settle(connection, order_id, staff)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT event_type FROM domain_events
            WHERE aggregate_type = 'ORDER_SETTLEMENT' AND aggregate_id = %s
            """,
            (order_id,),
        )
        assert cursor.fetchall() == [("ORDER_SETTLEMENT_RECORDED",)]
        cursor.execute(
            """
            SELECT action, actor_id FROM audit_events
            WHERE aggregate_type = 'ORDER_SETTLEMENT' AND aggregate_id = %s
            """,
            (order_id,),
        )
        assert cursor.fetchall() == [("ORDER_SETTLEMENT_RECORD", staff.staff_user_id)]
        cursor.execute(
            """
            SELECT event_type FROM outbox_events
            WHERE aggregate_type = 'ORDER_SETTLEMENT' AND aggregate_id = %s
            """,
            (order_id,),
        )
        assert cursor.fetchall() == [("order.settlement_recorded.v1",)]


def test_the_settlement_pins_the_quote_it_was_checked_against(
    connection: psycopg.Connection[Any],
) -> None:
    """Re-pricing later must not change what the customer was asked for at the counter."""
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    order_id, _ = _ready_active_order(connection, store_id, staff)
    _settle(connection, order_id, staff)

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT s.settled_quote_snapshot_hash, o.current_quote_snapshot_hash
            FROM order_settlements s JOIN orders o ON o.id = s.order_id
            WHERE s.order_id = %s
            """,
            (order_id,),
        )
        row = cursor.fetchone()
    assert row is not None and row[0] == row[1]


# --- store scoping and RBAC --------------------------------------------------------------------


def test_a_staff_member_from_another_store_cannot_settle(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    outsider = _staff(connection, uuid4(), StaffRole.OPERATOR)
    order_id, _ = _ready_active_order(connection, store_id, staff)

    with pytest.raises(SettlementAuthorizationError):
        _settle(connection, order_id, outsider)
    assert _order_row(connection, order_id)[1] == "UNPAID"


def test_a_read_only_role_cannot_settle(connection: psycopg.Connection[Any]) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    auditor = _staff(connection, store_id, StaffRole.AUDITOR)
    order_id, _ = _ready_active_order(connection, store_id, staff)

    with pytest.raises(SettlementAuthorizationError):
        _settle(connection, order_id, auditor)
    assert _order_row(connection, order_id)[1] == "UNPAID"


# --- collected_today: the one money figure the console reads -----------------------------------
#
# The owner's morning has a money question in it. The risk in answering it is not arithmetic — the
# database sums a BIGINT column — but scope: a number labelled "today" that quietly includes
# yesterday, or one store's counter that quietly includes another's, is worse than no number at
# all, because it looks checkable and is not. These four tests are that scope.


def test_todays_takings_count_today_and_only_today(
    connection: psycopg.Connection[Any],
) -> None:
    """The day boundary is real: an older settlement is outside the window, not merely older."""
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    now = datetime.now(UTC)

    for _ in range(2):
        order_id, _version = _ready_active_order(connection, store_id, staff)
        _settle(connection, order_id, staff, attested_at=now)
    # `NOW` is 2026-08-01, which is not today by any clock this test can run on.
    stale_order, _stale_version = _ready_active_order(connection, store_id, staff)
    _settle(connection, stale_order, staff, attested_at=NOW)

    with connection.cursor() as cursor:
        collected = SettlementRepository.collected_today(cursor, store_id=store_id, principal=staff)
    assert collected.settlement_count == 2
    assert collected.collected_vnd == 2 * QUOTED_TOTAL


def test_a_store_with_nothing_settled_today_reads_zero_not_null(
    connection: psycopg.Connection[Any],
) -> None:
    """`coalesce` earns its place: "chưa thu đồng nào" is an answer, and null is not renderable."""
    store_id = uuid4()
    staff = _staff(connection, store_id, StaffRole.OPERATOR)
    with connection.cursor() as cursor:
        collected = SettlementRepository.collected_today(cursor, store_id=store_id, principal=staff)
    assert collected == CollectedToday(collected_vnd=0, settlement_count=0)


def test_takings_never_cross_a_store_boundary(connection: psycopg.Connection[Any]) -> None:
    """Assigned to both stores is not permission to see them added together."""
    first, second = uuid4(), uuid4()
    staff = _staff(connection, first, StaffRole.OPERATOR)
    # Assigned to the second store as well — by somebody who exists, since the assignment carries a
    # real foreign key to the staff member who granted it.
    assigner = _staff(connection, second, StaffRole.OWNER_ADMIN)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (staff.staff_user_id, second, assigner.staff_user_id, NOW),
        )
    order_id, _version = _ready_active_order(connection, first, staff)
    _settle(connection, order_id, staff, attested_at=datetime.now(UTC))

    with connection.cursor() as cursor:
        assert SettlementRepository.collected_today(
            cursor, store_id=second, principal=staff
        ) == CollectedToday(collected_vnd=0, settlement_count=0)
        busy = SettlementRepository.collected_today(cursor, store_id=first, principal=staff)
    assert busy.collected_vnd == QUOTED_TOTAL


def test_a_non_member_is_refused_the_takings(connection: psycopg.Connection[Any]) -> None:
    """Money is the last read that should leak a store's activity to somebody not assigned to it."""
    store_id = uuid4()
    member = _staff(connection, store_id, StaffRole.OPERATOR)
    outsider = _staff(connection, None, StaffRole.OWNER_ADMIN)
    order_id, _version = _ready_active_order(connection, store_id, member)
    _settle(connection, order_id, member, attested_at=datetime.now(UTC))

    with (
        connection.cursor() as cursor,
        pytest.raises((SettlementAuthorizationError, StoreAccessError)),
    ):
        SettlementRepository.collected_today(cursor, store_id=store_id, principal=outsider)
