"""`PAYMENT-001` (`DEC-035`) against real PostgreSQL: the payment ledger and what it moves.

What only the database can prove: that a deposit, a part payment and the rest each commit with
their event, audit and outbox rows or not at all; that the payment settling the order writes the
same settlement row the exact-total route writes; that goods cannot leave while a deposit is all
that is paid; that the takings split by method equal the ledger; that a deposit goes back through
the refund path; and that `0056` refuses a balance and a ledger that disagree, whoever writes them.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import OrderStateError
from nha_trang_laundry_db.payments import (
    PaymentCommand,
    PaymentRepository,
    PaymentStateError,
    StoredPayment,
)
from nha_trang_laundry_db.settlement import (
    CollectionCommand,
    SettlementAuthorizationError,
    SettlementCommand,
    SettlementRepository,
    SettlementStateError,
)
from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import PaymentMethod
from test_order_step_repository import (
    TOTAL_VND,
    _order,
    _primary,
    _read,
    _staff,
    _step,
)

CASH = PaymentMethod.TIEN_MAT
TRANSFER = PaymentMethod.CHUYEN_KHOAN


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def autocommit() -> Generator[psycopg.Connection[Any], None, None]:
    """A second connection whose `transaction()` blocks are real transactions, so a deferred check
    runs at the end of the block rather than when the test's connection is closed."""

    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url, autocommit=True) as established:
        yield established


def _pay(
    connection: Any,
    order_id: UUID,
    staff: Any,
    amount: int,
    *,
    method: PaymentMethod = CASH,
    seen: bool = False,
    ref: str | None = None,
    collected: bool = False,
    version: int | None = None,
    at: datetime | None = None,
) -> StoredPayment:
    if version is None:
        version = _read(connection, order_id, staff).row_version
    return PaymentRepository().record(
        connection,
        PaymentCommand(
            order_id=order_id,
            expected_row_version=version,
            amount_vnd=amount,
            method=method,
            transfer_seen=seen,
            bank_ref_last=ref,
            collected_by_customer=collected,
            principal=staff,
            correlation_id=uuid4(),
            recorded_at=at,
        ),
    )


def _received(
    connection: Any, mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT
) -> tuple[UUID, Any, UUID]:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff, mode)
    _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=True)
    connection.commit()
    return store_id, staff, order_id


def _to_ready(connection: Any, order_id: UUID, staff: Any) -> Any:
    view = _read(connection, order_id, staff)
    for step in (OrderStep.START_WASH, OrderStep.QUALITY_CHECK, OrderStep.MARK_READY):
        view = _step(connection, order_id, staff, view.row_version, step).view
    connection.commit()
    return view


def _rows(connection: Any, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [tuple(row) for row in cursor.fetchall()]


def _commit_raw(autocommit: Any, sql: str, params: tuple[object, ...]) -> None:
    """One statement in its own real transaction, so a deferred check runs at its commit."""

    with autocommit.transaction(), autocommit.cursor() as cursor:
        cursor.execute(sql, params)


def _payment_ledger(connection: Any, order_id: UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in ("domain_events", "audit_events", "outbox_events"):
        counts[table] = _rows(
            connection,
            f"SELECT count(*) FROM {table} WHERE aggregate_id = %s AND aggregate_type = "
            "'ORDER_PAYMENT'",
            order_id,
        )[0][0]
    counts["payments"] = _rows(
        connection, "SELECT count(*) FROM order_payments WHERE order_id = %s", order_id
    )[0][0]
    return counts


# --- the counter's sequence ----------------------------------------------------------------------


def test_a_transfer_deposit_at_drop_off_then_cash_at_pickup_then_hand_over(
    connection: psycopg.Connection[Any],
) -> None:
    _store, staff, order_id = _received(connection)

    deposit = _pay(connection, order_id, staff, 50_000, method=TRANSFER, seen=True, ref=" ft 26 ")
    connection.commit()
    assert (deposit.balance_status, deposit.paid_vnd, deposit.remaining_vnd) == (
        "PARTIALLY_PAID",
        50_000,
        TOTAL_VND - 50_000,
    )
    assert deposit.bank_ref_last == "FT26" and deposit.settlement_id is None
    assert _payment_ledger(connection, order_id) == {
        "domain_events": 1,
        "audit_events": 1,
        "outbox_events": 1,
        "payments": 1,
    }
    # The reference tail is kept on the ledger row only: no event, audit or outbox payload carries
    # it, nor anything about the customer.
    for table, column in (
        ("domain_events", "payload"),
        ("audit_events", "details"),
        ("outbox_events", "payload"),
    ):
        payloads = _rows(
            connection,
            f"SELECT {column}::text FROM {table} WHERE aggregate_id = %s AND aggregate_type = "
            "'ORDER_PAYMENT'",
            order_id,
        )
        assert payloads and all("FT26" not in text for (text,) in payloads), table
    view = _read(connection, order_id, staff)
    assert (view.owed_vnd, view.paid_vnd, view.remaining_vnd) == (TOTAL_VND, 50_000, 60_000)
    assert [(p.amount_vnd, p.method, p.bank_ref_last, p.legacy) for p in view.payments] == [
        (50_000, "CHUYEN_KHOAN", "FT26", False)
    ]
    assert [(c.kind.value, c.amount_vnd) for c in view.charges] == [("QUOTED_TOTAL", TOTAL_VND)]
    assert view.payment_may_hand_over is False  # the laundry is not finished yet

    view = _to_ready(connection, order_id, staff)
    # Goods leave only when paid: the rest is the next thing, and nothing hands over before it.
    assert _primary(view) is OrderStep.TAKE_PAYMENT
    listed = {item.step for item in view.next_steps}
    assert not listed & {OrderStep.RELEASE, OrderStep.HAND_OVER, OrderStep.COLLECT}
    assert view.payment_may_hand_over is True

    rest = _pay(connection, order_id, staff, 60_000, collected=True, version=view.row_version)
    connection.commit()
    assert (rest.balance_status, rest.remaining_vnd, rest.self_collection_recorded) == (
        "PAID",
        0,
        True,
    )
    assert rest.settlement_shape == "EXACT_PAYMENT_SELF_COLLECTION"
    settlement = _rows(
        connection,
        "SELECT expected_total_vnd, paid_amount_vnd, collected_by FROM order_settlements "
        "WHERE order_id = %s",
        order_id,
    )
    assert settlement == [(TOTAL_VND, TOTAL_VND, "CUSTOMER")]
    # The settling payment names the settlement it completed; the deposit does not.
    assert _rows(
        connection,
        "SELECT amount_vnd, method, settlement_id IS NOT NULL FROM order_payments "
        "WHERE order_id = %s ORDER BY recorded_at, id",
        order_id,
    ) == [(50_000, "CHUYEN_KHOAN", False), (60_000, "TIEN_MAT", True)]
    outbox = _rows(
        connection,
        "SELECT event_type FROM outbox_events WHERE aggregate_id = %s AND aggregate_type = "
        "'ORDER_PAYMENT' ORDER BY event_type",
        order_id,
    )
    assert outbox == [
        ("order.payment_recorded.v1",),
        ("order.payment_recorded.v1",),
        ("order.settlement_recorded.v1",),
    ]

    view = _read(connection, order_id, staff)
    assert _primary(view) is OrderStep.HAND_OVER
    done = _step(connection, order_id, staff, view.row_version, OrderStep.HAND_OVER).view
    connection.commit()
    assert done.commercial is CommercialOrderStatus.COMPLETED
    assert (done.paid_vnd, done.remaining_vnd) == (TOTAL_VND, 0)


def test_two_part_payments_and_the_rest_settle_as_a_prepaid_order_that_collects_later(
    connection: psycopg.Connection[Any],
) -> None:
    _store, staff, order_id = _received(connection)
    _pay(connection, order_id, staff, 30_000)
    _pay(connection, order_id, staff, 40_000, method=TRANSFER, seen=True)
    rest = _pay(connection, order_id, staff, 40_000)
    connection.commit()
    assert (rest.balance_status, rest.paid_vnd, rest.self_collection_recorded) == (
        "PAID",
        TOTAL_VND,
        False,
    )
    assert rest.settlement_shape == "EXACT_PAYMENT_PREPAID_SELF_COLLECTION"
    versions = _rows(
        connection,
        "SELECT aggregate_version FROM domain_events WHERE aggregate_id = %s AND "
        "aggregate_type = 'ORDER_PAYMENT' ORDER BY aggregate_version",
        order_id,
    )
    assert versions == [(1,), (2,), (3,)]
    view = _to_ready(connection, order_id, staff)
    assert _primary(view) is OrderStep.COLLECT
    SettlementRepository().record_collection(
        connection, CollectionCommand(order_id, view.row_version, staff, uuid4())
    )
    connection.commit()
    assert _primary(_read(connection, order_id, staff)) is OrderStep.HAND_OVER


@pytest.mark.parametrize("mode", [FulfillmentMode.PICKUP_AND_RETURN, FulfillmentMode.RETURN_ONLY])
def test_a_delivery_order_takes_the_rest_before_the_courier_leaves(
    connection: psycopg.Connection[Any], mode: FulfillmentMode
) -> None:
    _store, staff, order_id = _received(connection, mode)
    _pay(connection, order_id, staff, 50_000)
    connection.commit()
    view = _to_ready(connection, order_id, staff)
    assert _primary(view) is OrderStep.TAKE_PAYMENT
    assert view.payment_may_hand_over is False
    with pytest.raises(PaymentStateError) as refused:
        _pay(connection, order_id, staff, 60_000, collected=True, version=view.row_version)
    assert refused.value.reason_code == "COLLECTION_WAS_NOT_BY_THE_CUSTOMER"
    connection.rollback()
    rest = _pay(connection, order_id, staff, 60_000, version=view.row_version)
    connection.commit()
    assert rest.settlement_shape == "EXACT_PAYMENT_PREPAID_DELIVERY"
    assert _primary(_read(connection, order_id, staff)) is OrderStep.RELEASE


# --- refusals: nothing is written ----------------------------------------------------------------


def test_overpayment_is_refused_and_writes_nothing(connection: psycopg.Connection[Any]) -> None:
    _store, staff, order_id = _received(connection)
    _pay(connection, order_id, staff, 50_000)
    connection.commit()
    before = _payment_ledger(connection, order_id)
    with pytest.raises(PaymentStateError) as refused:
        _pay(connection, order_id, staff, TOTAL_VND - 50_000 + 1)
    connection.rollback()
    assert refused.value.reason_code == "OVERPAYMENT_REFUSED"
    assert _payment_ledger(connection, order_id) == before
    assert _read(connection, order_id, staff).balance is OrderBalanceStatus.PARTIALLY_PAID


def test_pickup_is_refused_while_only_a_deposit_is_paid(
    connection: psycopg.Connection[Any],
) -> None:
    _store, staff, order_id = _received(connection)
    _pay(connection, order_id, staff, 50_000)
    connection.commit()
    view = _to_ready(connection, order_id, staff)

    with pytest.raises(SettlementStateError) as collect:
        SettlementRepository().record_collection(
            connection, CollectionCommand(order_id, view.row_version, staff, uuid4())
        )
    connection.rollback()
    assert collect.value.reason_code == "COLLECTION_REQUIRES_PAYMENT"

    with pytest.raises(PaymentStateError) as partial:
        _pay(connection, order_id, staff, 10_000, collected=True, version=view.row_version)
    connection.rollback()
    assert partial.value.reason_code == "HANDOVER_REQUIRES_FULL_PAYMENT"

    for step in (OrderStep.RELEASE, OrderStep.HAND_OVER):
        with pytest.raises(OrderStateError):
            _step(connection, order_id, staff, view.row_version, step)
        connection.rollback()
    after = _read(connection, order_id, staff)
    assert (after.balance, after.production, after.self_collection_recorded) == (
        OrderBalanceStatus.PARTIALLY_PAID,
        ProductionStatus.READY_AT_STORE,
        False,
    )


def test_a_transfer_not_seen_and_a_bad_reference_are_refused(
    connection: psycopg.Connection[Any],
) -> None:
    _store, staff, order_id = _received(connection)
    for kwargs, code in (
        ({"method": TRANSFER, "seen": False}, "TRANSFER_NOT_SEEN"),
        ({"method": TRANSFER, "seen": True, "ref": "FT-26"}, "BANK_REF_INVALID"),
        ({"method": CASH, "ref": "FT26"}, "BANK_REF_INVALID"),
    ):
        with pytest.raises(PaymentStateError) as refused:
            _pay(connection, order_id, staff, 10_000, **kwargs)
        connection.rollback()
        assert refused.value.reason_code == code
    assert _payment_ledger(connection, order_id)["payments"] == 0


def test_a_stale_if_match_is_refused(connection: psycopg.Connection[Any]) -> None:
    _store, staff, order_id = _received(connection)
    version = _read(connection, order_id, staff).row_version
    _pay(connection, order_id, staff, 10_000, version=version)
    connection.commit()
    with pytest.raises(PaymentStateError) as refused:
        _pay(connection, order_id, staff, 10_000, version=version)
    connection.rollback()
    assert refused.value.reason_code == "STALE_VERSION"


def test_a_non_member_and_a_session_without_mfa_are_refused(
    connection: psycopg.Connection[Any],
) -> None:
    _store, staff, order_id = _received(connection)
    outsider = _staff(connection, uuid4())
    connection.commit()
    version = _read(connection, order_id, staff).row_version
    with pytest.raises(SettlementAuthorizationError):
        _pay(connection, order_id, outsider, 10_000, version=version)
    connection.rollback()
    from dataclasses import replace

    with pytest.raises(SettlementAuthorizationError):
        _pay(connection, order_id, replace(staff, mfa_verified=False), 10_000, version=version)
    connection.rollback()
    assert _payment_ledger(connection, order_id)["payments"] == 0


def test_an_order_not_yet_received_takes_no_money(connection: psycopg.Connection[Any]) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    connection.commit()
    with pytest.raises(PaymentStateError) as refused:
        _pay(connection, order_id, staff, 10_000)
    connection.rollback()
    assert refused.value.reason_code == "ORDER_NOT_ACTIVE"


# --- the exact-total route keeps working, as one payment ----------------------------------------


def test_the_exact_total_settlement_route_records_one_legacy_cash_payment(
    connection: psycopg.Connection[Any],
) -> None:
    _store, staff, order_id = _received(connection)
    _to_ready(connection, order_id, staff)
    SettlementRepository().record(
        connection, SettlementCommand(order_id, TOTAL_VND, True, staff, uuid4())
    )
    connection.commit()
    assert _rows(
        connection,
        "SELECT amount_vnd, method, legacy, settlement_id IS NOT NULL FROM order_payments "
        "WHERE order_id = %s",
        order_id,
    ) == [(TOTAL_VND, "TIEN_MAT", True, True)]
    view = _read(connection, order_id, staff)
    assert (view.paid_vnd, view.remaining_vnd) == (TOTAL_VND, 0)
    assert view.payments[0].legacy is True


def test_the_exact_total_route_refuses_an_order_that_already_has_a_deposit(
    connection: psycopg.Connection[Any],
) -> None:
    _store, staff, order_id = _received(connection)
    _pay(connection, order_id, staff, 10_000)
    connection.commit()
    with pytest.raises(SettlementStateError) as refused:
        SettlementRepository().record(
            connection, SettlementCommand(order_id, TOTAL_VND, False, staff, uuid4())
        )
    connection.rollback()
    assert refused.value.reason_code == "ORDER_PARTLY_PAID"


# --- takings by method ---------------------------------------------------------------------------


def test_the_takings_by_method_equal_the_payments(connection: psycopg.Connection[Any]) -> None:
    store_id, staff, first = _received(connection)
    second = _order(connection, store_id, staff)
    _step(connection, second, staff, 1, OrderStep.RECEIVE, slot_approved=True)
    connection.commit()
    now = datetime.now(UTC)
    _pay(connection, first, staff, 50_000, method=TRANSFER, seen=True, at=now)
    _pay(connection, first, staff, 60_000, at=now)
    _pay(connection, second, staff, 20_000, at=now)
    _pay(connection, second, staff, 5_000, method=TRANSFER, seen=True, at=now)
    connection.commit()
    with connection.cursor() as cursor:
        takings = SettlementRepository.collected_today(
            cursor, store_id=store_id, principal=staff, as_of=now
        )
    assert (takings.collected_vnd, takings.payment_count) == (135_000, 4)
    assert (takings.cash_vnd, takings.cash_count) == (80_000, 2)
    assert (takings.transfer_vnd, takings.transfer_count) == (55_000, 2)
    # One order was paid in full today; the other only partly.
    assert takings.settlement_count == 1
    ledger = _rows(
        connection,
        "SELECT method, sum(amount_vnd) FROM order_payments WHERE store_id = %s GROUP BY method "
        "ORDER BY method",
        store_id,
    )
    assert ledger == [("CHUYEN_KHOAN", 55_000), ("TIEN_MAT", 80_000)]
    # Yesterday's drawer is untouched by today's payments.
    with connection.cursor() as cursor:
        yesterday = SettlementRepository.collected_today(
            cursor, store_id=store_id, principal=staff, as_of=now - timedelta(days=1)
        )
    assert (yesterday.collected_vnd, yesterday.payment_count) == (0, 0)


# --- a deposit goes back through the refund path -------------------------------------------------


def test_a_cancellation_after_a_deposit_refunds_exactly_the_deposit(
    connection: psycopg.Connection[Any],
) -> None:
    store_id, staff, order_id = _received(connection)
    _pay(connection, order_id, staff, 30_000, method=TRANSFER, seen=True)
    connection.commit()
    view = _read(connection, order_id, staff)
    cancel = next(item for item in view.next_steps if item.step is OrderStep.CANCEL)
    assert CustodyResolution.RETURNED_UNWASHED_REFUNDED in cancel.custody_resolutions
    done = _step(
        connection,
        order_id,
        staff,
        view.row_version,
        OrderStep.CANCEL,
        custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
    ).view
    connection.commit()
    assert (done.commercial, done.balance) == (
        CommercialOrderStatus.CANCELLED,
        OrderBalanceStatus.REFUNDED,
    )
    assert _rows(
        connection,
        "SELECT refunded_amount_vnd, settlement_id FROM order_refunds WHERE order_id = %s",
        order_id,
    ) == [(30_000, None)]
    with connection.cursor() as cursor:
        takings = SettlementRepository.collected_today(cursor, store_id=store_id, principal=staff)
    assert (takings.collected_vnd, takings.refunded_vnd, takings.net_vnd) == (30_000, 30_000, 0)


# --- 0056: the balance and the ledger cannot disagree, whoever writes them ----------------------


def test_the_database_refuses_a_payment_the_balance_does_not_reflect(
    connection: psycopg.Connection[Any], autocommit: psycopg.Connection[Any]
) -> None:
    store_id, staff, order_id = _received(connection)
    with pytest.raises(psycopg.errors.RaiseException, match="cannot read as unpaid"):
        _commit_raw(
            autocommit,
            """
            INSERT INTO order_payments (
                id, order_id, store_id, amount_vnd, method, bank_ref_last, legacy,
                settlement_id, recorded_by_staff_id, recorded_at, created_at
            ) VALUES (%s, %s, %s, 10000, 'TIEN_MAT', NULL, FALSE, NULL, %s, now(), now())
            """,
            (uuid4(), order_id, store_id, staff.staff_user_id),
        )


def test_the_database_refuses_a_balance_the_ledger_does_not_support(
    connection: psycopg.Connection[Any], autocommit: psycopg.Connection[Any]
) -> None:
    _store, _staff_member, order_id = _received(connection)
    for balance, message in (
        ("PARTIALLY_PAID", "partly paid order has payments"),
        ("PAID", "must sum to its settled amount"),
    ):
        with pytest.raises(psycopg.errors.RaiseException, match=message):
            _commit_raw(
                autocommit,
                "UPDATE orders SET balance_status = %s, row_version = row_version + 1 "
                "WHERE id = %s",
                (balance, order_id),
            )


def test_the_ledger_is_append_only_and_refuses_a_foreign_store(
    connection: psycopg.Connection[Any], autocommit: psycopg.Connection[Any]
) -> None:
    store_id, staff, order_id = _received(connection)
    stored = _pay(connection, order_id, staff, 10_000)
    connection.commit()
    for statement in (
        "UPDATE order_payments SET amount_vnd = 1 WHERE id = %s",
        "DELETE FROM order_payments WHERE id = %s",
    ):
        with pytest.raises(psycopg.errors.RaiseException):
            _commit_raw(autocommit, statement, (stored.payment_id,))
    with pytest.raises(psycopg.errors.RaiseException, match="order's store"):
        _commit_raw(
            autocommit,
            """
            INSERT INTO order_payments (
                id, order_id, store_id, amount_vnd, method, bank_ref_last, legacy,
                settlement_id, recorded_by_staff_id, recorded_at, created_at
            ) VALUES (%s, %s, %s, 1000, 'TIEN_MAT', NULL, FALSE, NULL, %s, now(), now())
            """,
            (uuid4(), order_id, uuid4(), staff.staff_user_id),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        _commit_raw(
            autocommit,
            """
            INSERT INTO order_payments (
                id, order_id, store_id, amount_vnd, method, bank_ref_last, legacy,
                settlement_id, recorded_by_staff_id, recorded_at, created_at
            ) VALUES (%s, %s, %s, 1000, 'THE', NULL, FALSE, NULL, %s, now(), now())
            """,
            (uuid4(), order_id, store_id, staff.staff_user_id),
        )


def test_the_order_read_bounds_its_payment_list(connection: psycopg.Connection[Any]) -> None:
    from nha_trang_laundry_db.payments import PAYMENT_READ_LIMIT

    _store, staff, order_id = _received(connection)
    for _ in range(PAYMENT_READ_LIMIT + 2):
        _pay(connection, order_id, staff, 1)
    connection.commit()
    view = _read(connection, order_id, staff)
    assert len(view.payments) == PAYMENT_READ_LIMIT and view.payments_truncated
    # The sum is over the whole ledger, not the rows shown.
    assert view.paid_vnd == PAYMENT_READ_LIMIT + 2
