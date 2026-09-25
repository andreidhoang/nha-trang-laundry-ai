"""`ORDER-STEPS-001` / `READ-ENRICH-001` against real PostgreSQL.

What these prove is about what gets written and read, so they cannot be proved with a stub: that a
composite step writes exactly the rows the per-axis routes write, one transition at a time; that a
step refused or failing anywhere leaves nothing behind; that `If-Match` and idempotency behave as on
the per-axis routes; and that the order, quote, intake, incident and SLA reads carry the new fields
from the rows the database holds.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegKind,
    DeliveryLegOutcome,
    DeliveryLegRepository,
    RecordDeliveryLegCommand,
)
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.incidents import IncidentRepository, StaffIncidentOpenCommand
from nha_trang_laundry_db.intake import OrderRequestRepository
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderAuthorizationError,
    OrderRepository,
    OrderStateError,
    OrderStepCommand,
    OrderStepRequiresHuman,
    OrderStepResult,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.quotes import QuoteRepository
from nha_trang_laundry_db.settlement import SettlementCommand, SettlementRepository
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    IntakeStatus,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from quote_test_data import accepted_quote, ensure_store

TOTAL_VND = 110_000  # the fixture's 100.000 service line plus its 10.000 delivery adjustment


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(connection: Any, store_id: UUID | None) -> StaffPrincipal:
    now = datetime.now(UTC)
    if store_id is not None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM stores WHERE id = %s", (store_id,))
            exists = cursor.fetchone() is not None
        if not exists:
            ensure_store(connection, store_id)
    staff_id, assigner = uuid4(), uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        for identifier in (staff_id, assigner):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                """,
                (identifier, f"oidc-{identifier}", now),
            )
        if store_id is not None:
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (staff_id, store_id, assigner, now),
            )
    return StaffPrincipal(
        staff_id, f"oidc-{staff_id}", frozenset({StaffRole.OPERATOR}), True, uuid4()
    )


def _order(
    connection: Any,
    store_id: UUID,
    staff: StaffPrincipal,
    mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
) -> UUID:
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, fulfillment_mode=mode
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
                mode,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                datetime.now(UTC),
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )


def _step(
    connection: Any,
    order_id: UUID,
    staff: StaffPrincipal,
    version: int,
    step: OrderStep,
    *,
    key: str | None = None,
    slot_approved: bool = False,
    custody_resolution: CustodyResolution | None = None,
) -> OrderStepResult:
    return OrderRepository().execute_step(
        connection,
        OrderStepCommand(
            order_id=order_id,
            expected_row_version=version,
            principal=staff,
            idempotency_key=key or f"step-{uuid4().hex}",
            correlation_id=uuid4(),
            step=step,
            slot_approved=slot_approved,
            custody_resolution=custody_resolution,
        ),
    )


def _one(cursor: Any) -> tuple[Any, ...]:
    row = cursor.fetchone()
    assert row is not None
    return tuple(row)


def _read(connection: Any, order_id: UUID, staff: StaffPrincipal) -> Any:
    with connection.cursor() as cursor:
        return OrderRepository.read_for_principal(cursor, order_id=order_id, principal=staff)


def _primary(view: Any) -> OrderStep | None:
    return next((item.step for item in view.next_steps if item.primary), None)


def _ledger(connection: Any, order_id: UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for table in ("domain_events", "audit_events", "outbox_events"):
            cursor.execute(
                f"SELECT count(*) FROM {table} WHERE aggregate_id = %s",
                (order_id,),
            )
            counts[table] = int(cursor.fetchone()[0])
        cursor.execute(
            "SELECT count(*) FROM command_idempotency_records WHERE scope = %s",
            (f"order:{order_id}:step",),
        )
        counts["idempotency"] = int(cursor.fetchone()[0])
    return counts


def _order_row(connection: Any, order_id: UUID) -> tuple[Any, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT commercial_status, intake_status, production_status, balance_status,
                   row_version, production_accepted_at
            FROM orders WHERE id = %s
            """,
            (order_id,),
        )
        return tuple(cursor.fetchone())


def _trail(connection: Any, order_id: UUID) -> dict[str, list[Any]]:
    """The order's ledger rows with the order id abstracted away, for shape comparison."""

    marker = str(order_id)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT aggregate_type, aggregate_version, event_type, payload
            FROM domain_events WHERE aggregate_id = %s AND event_type <> 'ORDER_REQUESTED'
            ORDER BY aggregate_version
            """,
            (order_id,),
        )
        events = [tuple(row) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT aggregate_type, action, actor_type, details
            FROM audit_events
            WHERE aggregate_id = %s AND action = 'ORDER_STATE_TRANSITION'
            ORDER BY occurred_at, id
            """,
            (order_id,),
        )
        audits = [tuple(row) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT event_type, payload, replace(idempotency_key, %s, 'ORDER')
            FROM outbox_events
            WHERE aggregate_id = %s AND event_type = 'order.state_transitioned.v1'
            ORDER BY occurred_at, id
            """,
            (marker, order_id),
        )
        outbox = [(row[0], {**row[1], "order_id": "ORDER"}, row[2]) for row in cursor.fetchall()]
    return {"events": events, "audits": audits, "outbox": outbox}


# --- the audit trail is the per-axis audit trail ------------------------------------------------


def test_receive_writes_exactly_the_rows_the_per_axis_routes_write(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    by_hand, by_step = _order(connection, store_id, staff), _order(connection, store_id, staff)

    repository = OrderRepository()
    version = 1
    for target in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {"commercial_target": CommercialOrderStatus.STORE_CONFIRMATION_PENDING},
        {"commercial_target": CommercialOrderStatus.CONFIRMED},
        {"intake_target": IntakeStatus.ACCEPTED},
        {"commercial_target": CommercialOrderStatus.ACTIVE},
    ):
        extra: dict[str, Any] = {}
        if target.get("intake_target") is IntakeStatus.ACCEPTED:
            with connection.cursor() as cursor:
                extra["intake_readiness"] = OrderRepository.intake_readiness_for(
                    cursor, order_id=by_hand, staff_user_id=staff.staff_user_id, slot_approved=True
                )
            extra["production_accepted_at"] = datetime.now(UTC)
        version = repository.transition(
            connection,
            OrderTransitionCommand(
                by_hand, version, staff, f"hand-{uuid4().hex}", uuid4(), **target, **extra
            ),
        ).row_version

    result = _step(connection, by_step, staff, 1, OrderStep.RECEIVE, slot_approved=True)

    assert result.view.row_version == version == 6
    assert _order_row(connection, by_step)[:5] == _order_row(connection, by_hand)[:5]
    assert _trail(connection, by_step) == _trail(connection, by_hand)
    assert len(_trail(connection, by_step)["audits"]) == 5
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(DISTINCT correlation_id), count(*), count(DISTINCT occurred_at)
            FROM audit_events WHERE aggregate_id = %s AND action = 'ORDER_STATE_TRANSITION'
            """,
            (by_step,),
        )
        correlations, rows, instants = _one(cursor)
    # One command, one correlation id; five rows, each at its own instant in plan order.
    assert (correlations, rows, instants) == (1, 5, 5)


# --- all or nothing ------------------------------------------------------------------------------


def test_a_receive_without_the_slot_attestation_writes_nothing(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    before, row = _ledger(connection, order_id), _order_row(connection, order_id)

    with pytest.raises(OrderStepRequiresHuman) as refused:
        _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=False)

    assert refused.value.reason_codes == ("SLOT_APPROVAL_REQUIRED",)
    assert _ledger(connection, order_id) == before
    assert _order_row(connection, order_id) == row


def test_a_failure_in_the_middle_of_a_step_rolls_back_every_earlier_transition(
    connection: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan passes, the third write fails: the first two must not survive."""

    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    before, row = _ledger(connection, order_id), _order_row(connection, order_id)

    original = OrderRepository._apply_locked_transition
    calls: list[int] = []

    def failing_third(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 3:
            raise OrderStateError("INVALID_STATE_TRANSITION: injected mid-step failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(OrderRepository, "_apply_locked_transition", failing_third)
    with pytest.raises(OrderStateError, match="injected"):
        _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=True)

    assert len(calls) == 3
    assert _ledger(connection, order_id) == before
    assert _order_row(connection, order_id) == row
    # And the key was not consumed: the retry runs, rather than replaying a failure.
    monkeypatch.setattr(OrderRepository, "_apply_locked_transition", original)
    assert _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=True)


def test_a_step_the_state_does_not_allow_is_refused_before_anything_is_written(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    before = _ledger(connection, order_id)

    with pytest.raises(OrderStateError, match=r"^INVALID_STATE_TRANSITION"):
        _step(connection, order_id, staff, 1, OrderStep.MARK_READY)
    assert _ledger(connection, order_id) == before


# --- If-Match and idempotency --------------------------------------------------------------------


def test_a_stale_row_version_is_refused_as_stale(connection: psycopg.Connection[Any]) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)

    with pytest.raises(OrderStateError, match=r"^STALE_VERSION"):
        _step(connection, order_id, staff, 2, OrderStep.RECEIVE, slot_approved=True)


def test_a_replay_returns_the_first_answer_and_a_changed_payload_is_a_conflict(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    key = f"step-{uuid4().hex}"

    first = _step(connection, order_id, staff, 1, OrderStep.RECEIVE, key=key, slot_approved=True)
    after_first = _ledger(connection, order_id)
    again = _step(connection, order_id, staff, 1, OrderStep.RECEIVE, key=key, slot_approved=True)

    assert not first.replayed and again.replayed
    assert again.view == first.view
    assert _ledger(connection, order_id) == after_first
    with pytest.raises(IdempotencyConflictError):
        _step(connection, order_id, staff, 1, OrderStep.START_WASH, key=key)


def test_a_non_member_is_refused_and_learns_nothing(connection: psycopg.Connection[Any]) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    outsider = _staff(connection, uuid4())
    before = _ledger(connection, order_id)

    with pytest.raises(OrderAuthorizationError):
        _step(connection, order_id, outsider, 1, OrderStep.RECEIVE, slot_approved=True)
    assert _ledger(connection, order_id) == before


# --- lifecycles ----------------------------------------------------------------------------------


def test_a_walk_in_paid_at_pickup_from_creation_to_completed(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    view = _read(connection, order_id, staff)
    assert _primary(view) is OrderStep.RECEIVE

    for step, expected_next in (
        (OrderStep.RECEIVE, OrderStep.START_WASH),
        (OrderStep.START_WASH, OrderStep.QUALITY_CHECK),
        (OrderStep.QUALITY_CHECK, OrderStep.MARK_READY),
        (OrderStep.MARK_READY, OrderStep.SETTLE),
    ):
        view = _step(connection, order_id, staff, view.row_version, step, slot_approved=True).view
        assert _primary(view) is expected_next, (step, view.next_steps)

    SettlementRepository().record(
        connection, SettlementCommand(order_id, TOTAL_VND, True, staff, uuid4())
    )
    view = _read(connection, order_id, staff)
    assert view.settlement_shape == "EXACT_PAYMENT_SELF_COLLECTION"
    assert _primary(view) is OrderStep.HAND_OVER

    done = _step(connection, order_id, staff, view.row_version, OrderStep.HAND_OVER).view
    assert done.commercial is CommercialOrderStatus.COMPLETED
    assert done.production is ProductionStatus.RELEASED
    assert done.next_steps == ()


def test_a_walk_in_prepaid_at_drop_off_collects_then_hands_over(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    view = _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=True).view
    assert OrderStep.PREPAY in [item.step for item in view.next_steps]
    SettlementRepository().record(
        connection, SettlementCommand(order_id, TOTAL_VND, False, staff, uuid4())
    )
    view = _read(connection, order_id, staff)
    for step in (OrderStep.START_WASH, OrderStep.QUALITY_CHECK, OrderStep.MARK_READY):
        view = _step(connection, order_id, staff, view.row_version, step).view
    assert view.settlement_shape == "EXACT_PAYMENT_PREPAID_SELF_COLLECTION"
    assert _primary(view) is OrderStep.COLLECT
    from nha_trang_laundry_db.settlement import CollectionCommand

    SettlementRepository().record_collection(
        connection, CollectionCommand(order_id, view.row_version, staff, uuid4())
    )
    view = _read(connection, order_id, staff)
    assert _primary(view) is OrderStep.HAND_OVER
    done = _step(connection, order_id, staff, view.row_version, OrderStep.HAND_OVER).view
    assert done.commercial is CommercialOrderStatus.COMPLETED


@pytest.mark.parametrize("mode", [FulfillmentMode.PICKUP_AND_RETURN, FulfillmentMode.RETURN_ONLY])
def test_a_delivery_order_pays_releases_delivers_and_completes(
    connection: psycopg.Connection[Any], mode: FulfillmentMode
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff, mode)
    view = _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=True).view
    listed = [item.step for item in view.next_steps]
    assert (OrderStep.DELIVERY_PICKUP in listed) is (mode is FulfillmentMode.PICKUP_AND_RETURN)
    legs = DeliveryLegRepository()
    if mode is FulfillmentMode.PICKUP_AND_RETURN:
        legs.record(
            connection,
            RecordDeliveryLegCommand(
                order_id, DeliveryLegKind.PICKUP, DeliveryLegOutcome.SUCCEEDED, staff, uuid4()
            ),
        )
        view = _read(connection, order_id, staff)
        assert OrderStep.DELIVERY_PICKUP not in [item.step for item in view.next_steps]
    for step in (OrderStep.START_WASH, OrderStep.QUALITY_CHECK, OrderStep.MARK_READY):
        view = _step(connection, order_id, staff, view.row_version, step).view
    assert _primary(view) is OrderStep.PREPAY
    SettlementRepository().record(
        connection, SettlementCommand(order_id, TOTAL_VND, False, staff, uuid4())
    )
    view = _read(connection, order_id, staff)
    assert view.settlement_shape == "EXACT_PAYMENT_PREPAID_DELIVERY"
    assert _primary(view) is OrderStep.RELEASE
    view = _step(connection, order_id, staff, view.row_version, OrderStep.RELEASE).view
    assert _primary(view) is OrderStep.DELIVERY_RETURN
    legs.record(
        connection,
        RecordDeliveryLegCommand(
            order_id, DeliveryLegKind.RETURN, DeliveryLegOutcome.FAILED, staff, uuid4()
        ),
    )
    legs.record(
        connection,
        RecordDeliveryLegCommand(
            order_id, DeliveryLegKind.RETURN, DeliveryLegOutcome.SUCCEEDED, staff, uuid4()
        ),
    )
    view = _read(connection, order_id, staff)
    assert view.required_delivery_legs_succeeded
    kinds = [(leg.leg_kind, leg.outcome) for leg in view.delivery_legs]
    expected = [("RETURN", "FAILED"), ("RETURN", "SUCCEEDED")]
    if mode is FulfillmentMode.PICKUP_AND_RETURN:
        expected.insert(0, ("PICKUP", "SUCCEEDED"))
    assert kinds == expected
    assert _primary(view) is OrderStep.COMPLETE
    done = _step(connection, order_id, staff, view.row_version, OrderStep.COMPLETE).view
    assert done.commercial is CommercialOrderStatus.COMPLETED


def test_cancel_before_work_is_direct_and_after_work_goes_through_review(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    fresh = _order(connection, store_id, staff)
    cancelled = _step(connection, fresh, staff, 1, OrderStep.CANCEL).view
    assert cancelled.commercial is CommercialOrderStatus.CANCELLED
    assert cancelled.row_version == 2

    received = _order(connection, store_id, staff)
    view = _step(connection, received, staff, 1, OrderStep.RECEIVE, slot_approved=True).view
    cancel = next(item for item in view.next_steps if item.step is OrderStep.CANCEL)
    assert cancel.requires == ("custody_resolution",)
    with pytest.raises(OrderStateError, match=r"^HUMAN_APPROVAL_REQUIRED"):
        _step(connection, received, staff, view.row_version, OrderStep.CANCEL)
    done = _step(
        connection,
        received,
        staff,
        view.row_version,
        OrderStep.CANCEL,
        custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
    ).view
    assert done.commercial is CommercialOrderStatus.CANCELLED
    assert done.balance is OrderBalanceStatus.UNPAID
    assert done.row_version == view.row_version + 2


def test_hold_and_resume(connection: psycopg.Connection[Any]) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    view = _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=True).view
    view = _step(connection, order_id, staff, view.row_version, OrderStep.START_WASH).view
    held = _step(connection, order_id, staff, view.row_version, OrderStep.HOLD).view
    assert held.production is ProductionStatus.ON_HOLD
    assert _primary(held) is OrderStep.RESUME
    resumed = _step(connection, order_id, staff, held.row_version, OrderStep.RESUME).view
    assert resumed.production is ProductionStatus.IN_PROCESS


# --- READ-ENRICH-001 -----------------------------------------------------------------------------


def test_quote_reads_carry_the_request_the_customer_and_the_priced_mode(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    quote_id, revision, _quote, contact_id = accepted_quote(
        connection,
        store_id=store_id,
        principal=staff,
        fulfillment_mode=FulfillmentMode.PICKUP_ONLY,
    )
    with connection.cursor() as cursor:
        listed = QuoteRepository.list_for_store(
            cursor, store_id=store_id, principal=staff, limit=10
        )
        cursor.execute("SELECT bound_order_request_id FROM quotes WHERE id = %s", (quote_id,))
        request_id = _one(cursor)[0]
        binding = QuoteRepository.binding_for_revision(
            cursor, store_id=store_id, quote_id=quote_id, revision=revision
        )
        elsewhere = QuoteRepository.binding_for_revision(
            cursor, store_id=uuid4(), quote_id=quote_id, revision=revision
        )
    (item,) = [entry for entry in listed if entry.quote_id == quote_id]
    assert (item.order_request_id, item.contact_binding_id, item.fulfillment_mode) == (
        request_id,
        contact_id,
        "PICKUP_ONLY",
    )
    assert binding is not None
    assert (binding.order_request_id, binding.contact_binding_id, binding.fulfillment_mode) == (
        request_id,
        contact_id,
        "PICKUP_ONLY",
    )
    assert elsewhere is None


def test_order_requests_carry_their_ticket_and_the_order_they_became(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    _unconverted = accepted_quote(connection, store_id=store_id, principal=staff)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT q.bound_order_request_id, t.ticket_number, t.issued_on
            FROM orders o JOIN quotes q ON q.id = o.current_quote_id
            JOIN counter_tickets t ON t.id = o.bound_contact_id
            WHERE o.id = %s
            """,
            (order_id,),
        )
        request_id, number, issued_on = _one(cursor)
        listed = OrderRequestRepository.list_for_store(
            cursor, store_id=store_id, principal=staff, limit=100
        )
        one = OrderRequestRepository.get_for_store(
            cursor, order_request_id=request_id, store_id=store_id, principal=staff
        )
    converted = [item for item in listed if item.order_id is not None]
    open_ones = [item for item in listed if item.order_id is None]
    assert [item.order_request_id for item in converted] == [request_id]
    assert len(open_ones) == 1 and open_ones[0].ticket_number is not None
    assert one is not None
    assert (one.order_id, one.ticket_number, one.ticket_issued_on) == (
        order_id,
        number,
        issued_on,
    )


def test_order_read_carries_settlement_shape_and_no_legs_for_a_walk_in(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    view = _read(connection, order_id, staff)
    assert (view.delivery_legs, view.required_delivery_legs_succeeded, view.settlement_shape) == (
        (),
        False,
        None,
    )
    with connection.cursor() as cursor:
        (listed,) = [
            item
            for item in OrderRepository.list_for_store(cursor, store_id=store_id, principal=staff)
            if item.order_id == order_id
        ]
    assert listed.next_steps == view.next_steps


def test_incidents_of_one_order_and_one_incident_by_id_carry_the_ticket(
    connection: psycopg.Connection[Any],
) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id, other_order = _order(connection, store_id, staff), _order(connection, store_id, staff)
    incidents = IncidentRepository()
    opened = []
    for index, target in enumerate((order_id, order_id, other_order)):
        opened.append(
            incidents.open_from_counter(
                connection,
                StaffIncidentOpenCommand(
                    store_id=store_id,
                    order_id=target,
                    evidence_summary=f"Áo bị ố lần {index}",
                    actor_id=staff.staff_user_id,
                    correlation_id=uuid4(),
                    opened_at=datetime.now(UTC) + timedelta(seconds=index),
                ),
                principal=staff,
            ).incident_id
        )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.ticket_number FROM orders o
            JOIN counter_tickets t ON t.id = o.bound_contact_id WHERE o.id = %s
            """,
            (order_id,),
        )
        number = _one(cursor)[0]
        mine = IncidentRepository.list_for_order(
            cursor, store_id=store_id, order_id=order_id, principal=staff
        )
        one = IncidentRepository.read_for_store(
            cursor, store_id=store_id, incident_id=opened[0], principal=staff
        )
        store_list = IncidentRepository.list_for_store(
            cursor, store_id=store_id, principal=staff, limit=10
        )
        elsewhere = IncidentRepository.list_for_order(
            cursor, store_id=store_id, order_id=uuid4(), principal=staff
        )
    assert [item.incident_id for item in mine] == [opened[1], opened[0]]  # newest first
    assert all(item.ticket_number == number for item in mine)
    assert one is not None and one.ticket_number == number and one.order_id == order_id
    assert {item.incident_id for item in store_list} == set(opened)
    assert all(item.ticket_number is not None for item in store_list)
    assert elsewhere == ()

    outsider = _staff(connection, uuid4())
    with connection.cursor() as cursor, pytest.raises(StoreAccessError):
        IncidentRepository.list_for_order(
            cursor, store_id=store_id, order_id=order_id, principal=outsider
        )
    with connection.cursor() as cursor, pytest.raises(StoreAccessError):
        IncidentRepository.read_for_store(
            cursor, store_id=store_id, incident_id=opened[0], principal=outsider
        )


def test_the_sla_board_carries_each_orders_ticket(connection: psycopg.Connection[Any]) -> None:
    store_id = uuid4()
    staff = _staff(connection, store_id)
    order_id = _order(connection, store_id, staff)
    _step(connection, order_id, staff, 1, OrderStep.RECEIVE, slot_approved=True)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT t.ticket_number, t.issued_on FROM orders o
            JOIN counter_tickets t ON t.id = o.bound_contact_id WHERE o.id = %s
            """,
            (order_id,),
        )
        number, issued_on = _one(cursor)
    board = ShadowConsoleRepository().sla_risk_board(
        connection, store_id=store_id, principal=staff, policy=STANDARD_WASH_SLA
    )
    (item,) = [entry for entry in board if entry.order_id == order_id]
    assert (item.ticket_number, item.ticket_issued_on) == (number, issued_on)
