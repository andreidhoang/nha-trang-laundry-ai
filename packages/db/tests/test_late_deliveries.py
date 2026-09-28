"""`LATE-CREDIT-002` (`DEC-042`) against real PostgreSQL.

What only the database can prove: that a delivery order receives its promise at *Nhận đồ* under the
published turnaround policy; that the list measures lateness from the stored ledgers (first promise,
customer-requested *Hẹn lại*, the succeeded RETURN leg) against the published threshold; that
*Lỗi của tiệm* writes the incident, the `LATE_DELIVERY_CREDIT` proposal at the server's minutes and
the decision in one transaction or not at all; that *Không phải lỗi tiệm* writes only the decision;
that each is idempotent and refused a second time; and that no note reaches a ledger payload.

Harness step (documented): the laundry's arrival is recorded with the delivery leg's own
`recorded_at` a few hours after the promise the policy computed -- the leg command takes the
instant as a parameter, so no SQL rewrites any stored time here.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

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
from nha_trang_laundry_db.late_deliveries import (
    LateDeliveryAuthorizationError,
    LateDeliveryDecideCommand,
    LateDeliveryRefused,
    LateDeliveryRepository,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.order_promises import OrderPromiseRepository, PromiseChangeCommand
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.promise_policy import publish_turnaround_policy
from nha_trang_laundry_db.remedies import (
    RemedyExecutionCommand,
    RemedyProposalRepository,
    RemedyStateError,
    publish_remedy_policy,
)
from nha_trang_laundry_db.reports import ReportRepository
from nha_trang_laundry_db.settlement import SettlementCommand, SettlementRepository
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
)
from nha_trang_laundry_domain.late_delivery import LateDeliveryDecision, NotStoreFaultReason
from nha_trang_laundry_domain.promise import PromiseChangeReason
from nha_trang_laundry_domain.remedies import RemedyKind
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from quote_test_data import accepted_quote
from test_order_promise_repository import (
    ACCEPTED,
    STANDARD,
    _document,
    _finish,
    _receive,
    _staff,
    _view,
)
from test_remedies import POLICY as REMEDY_POLICY
from test_remedies import _incident, _propose


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


VN_ZONE = ZoneInfo("Asia/Ho_Chi_Minh")


class Shop:
    def __init__(self, conn: Any) -> None:
        self.store_id = uuid4()
        self.owner = _staff(conn, self.store_id, StaffRole.OWNER_ADMIN)
        self.operator = _staff(conn, self.store_id, StaffRole.OPERATOR)
        self.auditor = _staff(conn, self.store_id, StaffRole.AUDITOR)


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Shop:
    made = Shop(connection)
    digest, created = publish_turnaround_policy(
        connection, actor_id=made.owner.staff_user_id, payload=_document()
    )
    assert created and digest
    return made


def _publish_remedies(connection: Any, shop: Shop, **overrides: Any) -> None:
    publish_remedy_policy(
        connection, actor_id=shop.owner.staff_user_id, payload={**REMEDY_POLICY, **overrides}
    )


def _delivery_order(
    connection: Any,
    shop: Shop,
    *,
    mode: FulfillmentMode = FulfillmentMode.PICKUP_AND_RETURN,
    settle: bool = True,
    move: PromiseChangeReason | None = None,
) -> tuple[UUID, datetime]:
    """A delivery order received under the published policy, washed, ready and paid.

    `move` records one *Hẹn lại* for that reason, one day later than the first promise, an hour
    after *Nhận đồ* -- before the laundry is ready, the only time the order route allows it.
    """

    quote_id, revision, quote, contact_id = accepted_quote(
        connection,
        store_id=shop.store_id,
        principal=shop.operator,
        fulfillment_mode=mode,
        lines=(STANDARD,),
    )
    order_id = (
        OrderRepository()
        .create(
            connection,
            CreateOrderCommand(
                shop.store_id,
                contact_id,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                mode,
                shop.operator,
                f"order-{uuid4().hex}",
                uuid4(),
                datetime.now(UTC),
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )
    view = _receive(connection, order_id, shop.operator)
    promise = view.promised_ready_at
    assert promise is not None, "a delivery order must receive its promise at Nhận đồ"
    if move is not None:
        OrderPromiseRepository().change(
            connection,
            PromiseChangeCommand(
                order_id=order_id,
                expected_row_version=_view(connection, order_id, shop.operator).row_version,
                principal=shop.operator,
                idempotency_key=f"promise-{uuid4().hex}",
                correlation_id=uuid4(),
                new_promise_at=promise + timedelta(days=1),
                reason=move,
                occurred_at=ACCEPTED + timedelta(hours=1),
            ),
        )
    _finish(connection, order_id, shop.operator, promise - timedelta(hours=1))
    if settle:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.display_total_min_vnd FROM orders o
                JOIN quote_revisions r
                  ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
                WHERE o.id = %s
                """,
                (order_id,),
            )
            total = int(cursor.fetchone()[0])
        SettlementRepository().record(
            connection,
            SettlementCommand(
                order_id=order_id,
                paid_amount_vnd=total,
                collected_by_customer=False,
                principal=shop.operator,
                correlation_id=uuid4(),
                attested_at=promise - timedelta(minutes=30),
            ),
        )
    return order_id, promise


def _leg(connection: Any, shop: Shop, order_id: UUID, at: datetime, *, ok: bool = True) -> None:
    DeliveryLegRepository().record(
        connection,
        RecordDeliveryLegCommand(
            order_id=order_id,
            leg_kind=DeliveryLegKind.RETURN,
            outcome=DeliveryLegOutcome.SUCCEEDED if ok else DeliveryLegOutcome.FAILED,
            principal=shop.operator,
            correlation_id=uuid4(),
            recorded_at=at,
        ),
    )


def _list(connection: Any, shop: Shop, principal: StaffPrincipal | None = None) -> Any:
    with connection.cursor() as cursor:
        return LateDeliveryRepository.list_late(
            cursor,
            store_id=shop.store_id,
            principal=principal or shop.operator,
            as_of=datetime.now(UTC),
        )


def _decide(
    connection: Any,
    shop: Shop,
    order_id: UUID,
    decision: LateDeliveryDecision,
    *,
    reason: NotStoreFaultReason | None = None,
    note: str | None = None,
    key: str | None = None,
    principal: StaffPrincipal | None = None,
) -> Any:
    return LateDeliveryRepository().decide(
        connection,
        LateDeliveryDecideCommand(
            store_id=shop.store_id,
            order_id=order_id,
            decision=decision,
            principal=principal or shop.operator,
            idempotency_key=key or f"late-{uuid4().hex}",
            correlation_id=uuid4(),
            reason=reason,
            note=note,
        ),
    )


def _count(connection: Any, sql: str, *params: object) -> int:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def test_a_delivery_order_receives_its_promise_at_nhan_do(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """The precondition `DEC-042` rests on: delivery orders are promised like walk-ins."""

    for mode in (FulfillmentMode.PICKUP_AND_RETURN, FulfillmentMode.RETURN_ONLY):
        order_id, promise = _delivery_order(connection, shop, mode=mode, settle=False)
        view = _view(connection, order_id, shop.operator)
        assert view.promised_ready_at == view.current_promise_at == promise
        assert promise > ACCEPTED


def test_before_the_remedy_policy_is_published_nothing_is_late_and_deciding_refuses(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=3))
    found = _list(connection, shop)
    assert found.policy_published is False and found.orders == ()
    with pytest.raises(LateDeliveryRefused) as refused:
        _decide(connection, shop, order_id, LateDeliveryDecision.STORE_FAULT_CREDITED)
    assert refused.value.code == "REMEDY_POLICY_UNPUBLISHED"


def test_the_list_measures_from_the_ledgers_against_the_published_threshold(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish_remedies(connection, shop)
    on_time, promise_a = _delivery_order(connection, shop)
    _leg(connection, shop, on_time, promise_a + timedelta(minutes=120, seconds=59))
    late, promise_b = _delivery_order(connection, shop)
    _leg(connection, shop, late, promise_b - timedelta(hours=1), ok=False)
    _leg(connection, shop, late, promise_b + timedelta(hours=3, minutes=10))
    not_delivered, _ = _delivery_order(connection, shop)

    found = _list(connection, shop)
    assert found.policy_published and found.threshold_minutes == 120
    assert [row.order_id for row in found.orders] == [late]
    row = found.orders[0]
    assert row.late_by_minutes == 190
    assert row.deadline_at == promise_b and row.deadline_basis == "FIRST_PROMISE"
    assert row.failed_attempts_before_deadline == (promise_b - timedelta(hours=1),)
    assert row.settled_total_vnd is not None
    # The domain's 10% of the settled total, as `RemedyOptions` probes it -- never computed here.
    assert row.credit_vnd is not None and row.credit_vnd > 0 and not row.refunded
    assert not_delivered not in {item.order_id for item in found.orders}


def test_the_summary_counts_what_the_list_shows_and_a_decision_takes_it_off(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """`SUMMARY-ATTENTION-001`: `count_undecided` is the list's population, counted."""

    def counted() -> Any:
        with connection.cursor() as cursor:
            return LateDeliveryRepository.count_undecided(
                cursor, store_id=shop.store_id, principal=shop.operator
            )

    _publish_remedies(connection, shop)
    first, promise_a = _delivery_order(connection, shop)
    _leg(connection, shop, first, promise_a + timedelta(hours=3))
    second, promise_b = _delivery_order(connection, shop)
    _leg(connection, shop, second, promise_b + timedelta(hours=4))
    on_time, promise_c = _delivery_order(connection, shop)
    _leg(connection, shop, on_time, promise_c + timedelta(minutes=90))

    assert counted() == (len(_list(connection, shop).orders), False) == (2, False)
    _decide(
        connection,
        shop,
        first,
        LateDeliveryDecision.NOT_STORE_FAULT,
        reason=NotStoreFaultReason.CUSTOMER_ABSENT,
    )
    assert counted() == (1, False)
    with pytest.raises(LateDeliveryAuthorizationError), connection.cursor() as cursor:
        LateDeliveryRepository.count_undecided(
            cursor, store_id=shop.store_id, principal=Shop(connection).operator
        )


def test_a_customer_requested_hen_lai_moves_the_deadline_and_a_shop_one_does_not(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish_remedies(connection, shop)
    asked, promise_a = _delivery_order(connection, shop, move=PromiseChangeReason.CUSTOMER_REQUEST)
    shop_moved, promise_b = _delivery_order(connection, shop, move=PromiseChangeReason.WORKLOAD)
    # Both arrive an hour after the time the customer was last told: a day and an hour after the
    # first promise.
    _leg(connection, shop, asked, promise_a + timedelta(days=1, hours=1))
    _leg(connection, shop, shop_moved, promise_b + timedelta(days=1, hours=1))
    found = {row.order_id: row for row in _list(connection, shop).orders}
    # The customer asked for a day later: 60 minutes late, under the threshold.
    assert asked not in found
    # The shop moved it for its own reasons: still measured from the first promise.
    assert found[shop_moved].late_by_minutes == 25 * 60
    assert found[shop_moved].deadline_basis == "FIRST_PROMISE"


def test_store_fault_writes_incident_proposal_and_decision_in_one_transaction(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish_remedies(connection, shop)
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=3, minutes=10))
    preview = _list(connection, shop).orders[0]
    stored = _decide(connection, shop, order_id, LateDeliveryDecision.STORE_FAULT_CREDITED)
    assert stored.decision == "STORE_FAULT_CREDITED" and not stored.replayed
    assert stored.late_by_minutes == 190
    assert stored.proposal_status == "STAFF_AUTHORIZED"
    assert stored.amount_vnd == preview.credit_vnd
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.kind, p.attested_late_by_minutes, p.store_fault_attested, p.status,
                   i.category, i.order_id, d.late_by_minutes
            FROM late_delivery_decisions d
            JOIN remedy_proposals p ON p.id = d.remedy_proposal_id
            JOIN customer_incidents i ON i.id = d.incident_id
            WHERE d.order_id = %s
            """,
            (order_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == "LATE_DELIVERY_CREDIT" and row[1] == 190 and row[2] is True
    assert row[4] == "SERVICE_QUALITY" and row[5] == order_id and row[6] == 190
    # The ledger row the decision writes, beside the incident's and the proposal's own.
    assert (
        _count(
            connection,
            "SELECT count(*) FROM domain_events WHERE event_type = 'LATE_DELIVERY_DECIDED' "
            "AND payload->>'order_id' = %s",
            str(order_id),
        )
        == 1
    )
    after = _list(connection, shop)
    assert after.orders == ()
    assert [item.order_id for item in after.follow_up] == [order_id]
    assert after.follow_up[0].next_step == "EXECUTE"


def test_above_the_staff_limit_the_credit_waits_for_the_owner(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    # A staff limit below the credit: the proposal must raise the owner's envelope, unchanged.
    _publish_remedies(connection, shop, staff_approval_ceiling_vnd=1_000)
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=3))
    row = _list(connection, shop).orders[0]
    assert row.credit_requires_owner is True
    stored = _decide(connection, shop, order_id, LateDeliveryDecision.STORE_FAULT_CREDITED)
    assert stored.proposal_status == "OWNER_APPROVAL_REQUIRED"
    assert stored.approval_id is not None and stored.owner_reasons == ("ABOVE_STAFF_LIMIT",)
    follow = _list(connection, shop).follow_up
    assert [(item.order_id, item.next_step) for item in follow] == [(order_id, "AWAIT_OWNER")]


def test_a_refused_proposal_rolls_the_incident_back_too(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """Nothing settled: the domain refuses the credit, and no incident or decision survives."""

    _publish_remedies(connection, shop)
    order_id, promise = _delivery_order(connection, shop, settle=False)
    _leg(connection, shop, order_id, promise + timedelta(hours=3))
    listed = _list(connection, shop).orders[0]
    assert listed.credit_vnd is None and listed.credit_refusal == "REMEDY_ORDER_NOT_SETTLED"
    with pytest.raises(RemedyStateError) as refused:
        _decide(connection, shop, order_id, LateDeliveryDecision.STORE_FAULT_CREDITED)
    assert refused.value.reason_code == "REMEDY_ORDER_NOT_SETTLED"
    assert (
        _count(connection, "SELECT count(*) FROM customer_incidents WHERE order_id = %s", order_id)
        == 0
    )
    assert _count(connection, "SELECT count(*) FROM late_delivery_decisions") == 0
    assert (
        _count(
            connection,
            "SELECT count(*) FROM command_idempotency_records "
            "WHERE scope LIKE 'late-delivery-decide:%%'",
        )
        == 0
    )


def test_not_store_fault_writes_only_the_decision_and_keeps_the_note_out_of_the_ledgers(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish_remedies(connection, shop)
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=5))
    note = "Khách dặn để ở quán cà phê gần nhà"
    stored = _decide(
        connection,
        shop,
        order_id,
        LateDeliveryDecision.NOT_STORE_FAULT,
        reason=NotStoreFaultReason.OTHER,
        note=note,
    )
    assert stored.decision == "NOT_STORE_FAULT" and stored.reason_code == "OTHER"
    assert stored.proposal_id is None and stored.incident_id is None
    assert (
        _count(connection, "SELECT count(*) FROM customer_incidents WHERE order_id = %s", order_id)
        == 0
    )
    assert _count(connection, "SELECT count(*) FROM remedy_proposals") == 0
    assert _list(connection, shop).orders == ()
    with connection.cursor() as cursor:
        for table in ("domain_events", "audit_events", "outbox_events"):
            cursor.execute(
                f"SELECT payload::text FROM {table}"
                if table != "audit_events"
                else "SELECT details::text FROM audit_events"
            )
            assert all(note not in str(row[0]) for row in cursor.fetchall())
        cursor.execute("SELECT note FROM late_delivery_decisions WHERE order_id = %s", (order_id,))
        assert cursor.fetchone() == (note,)


def test_reasons_are_checked_before_anything_is_read(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish_remedies(connection, shop)
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=5))
    cases: list[tuple[dict[str, Any], str]] = [
        ({"decision": LateDeliveryDecision.NOT_STORE_FAULT}, "LATE_DELIVERY_REASON_REQUIRED"),
        (
            {"decision": LateDeliveryDecision.NOT_STORE_FAULT, "reason": NotStoreFaultReason.OTHER},
            "NOTE_REQUIRED",
        ),
        (
            {
                "decision": LateDeliveryDecision.NOT_STORE_FAULT,
                "reason": NotStoreFaultReason.OTHER,
                "note": "gọi 0905 123 456 trước",
            },
            "NOTE_LOOKS_LIKE_PHONE",
        ),
        (
            {
                "decision": LateDeliveryDecision.STORE_FAULT_CREDITED,
                "reason": NotStoreFaultReason.CUSTOMER_ABSENT,
            },
            "LATE_DELIVERY_REASON_NOT_APPLICABLE",
        ),
    ]
    for fields, code in cases:
        decision = fields.pop("decision")
        with pytest.raises(LateDeliveryRefused) as refused:
            _decide(connection, shop, order_id, decision, **fields)
        assert refused.value.code == code
    assert _count(connection, "SELECT count(*) FROM late_delivery_decisions") == 0


def test_not_late_already_decided_and_not_measurable_are_refused_by_name(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish_remedies(connection, shop)
    on_time, promise = _delivery_order(connection, shop)
    _leg(connection, shop, on_time, promise + timedelta(minutes=90))
    with pytest.raises(LateDeliveryRefused) as refused:
        _decide(
            connection,
            shop,
            on_time,
            LateDeliveryDecision.NOT_STORE_FAULT,
            reason=NotStoreFaultReason.CUSTOMER_ABSENT,
        )
    assert refused.value.code == "NOT_LATE" and refused.value.threshold_minutes == 120

    undelivered, _ = _delivery_order(connection, shop)
    with pytest.raises(LateDeliveryRefused) as refused:
        _decide(connection, shop, undelivered, LateDeliveryDecision.STORE_FAULT_CREDITED)
    assert refused.value.code == "LATE_DELIVERY_NOT_MEASURABLE"

    late, promise_late = _delivery_order(connection, shop)
    _leg(connection, shop, late, promise_late + timedelta(hours=4))
    _decide(
        connection,
        shop,
        late,
        LateDeliveryDecision.NOT_STORE_FAULT,
        reason=NotStoreFaultReason.CUSTOMER_WRONG_ADDRESS,
    )
    with pytest.raises(LateDeliveryRefused) as refused:
        _decide(connection, shop, late, LateDeliveryDecision.STORE_FAULT_CREDITED)
    assert refused.value.code == "ALREADY_DECIDED"


def test_the_same_key_replays_and_a_changed_payload_conflicts(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish_remedies(connection, shop)
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=3))
    first = _decide(connection, shop, order_id, LateDeliveryDecision.STORE_FAULT_CREDITED, key="k1")
    again = _decide(connection, shop, order_id, LateDeliveryDecision.STORE_FAULT_CREDITED, key="k1")
    assert again.replayed and again.decision_id == first.decision_id
    assert again.proposal_id == first.proposal_id
    with pytest.raises(IdempotencyConflictError):
        _decide(
            connection,
            shop,
            order_id,
            LateDeliveryDecision.NOT_STORE_FAULT,
            reason=NotStoreFaultReason.CUSTOMER_ABSENT,
            key="k1",
        )
    assert _count(connection, "SELECT count(*) FROM remedy_proposals") == 1


def test_one_credit_per_order_even_through_the_manual_path(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """A credit already proposed by hand makes the one-tap credit a remedy refusal, as it is."""

    _publish_remedies(connection, shop)
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=3))
    incident = _incident(connection, shop.store_id, order_id, shop.operator)
    _propose(
        connection,
        shop.store_id,
        incident,
        shop.operator,
        kind=RemedyKind.LATE_DELIVERY_CREDIT,
        store_fault_attested=True,
        attested_late_by_minutes=180,
    )
    row = _list(connection, shop).orders[0]
    assert row.credit_vnd is None
    assert row.credit_refusal == "REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED"
    with pytest.raises(RemedyStateError) as refused:
        _decide(connection, shop, order_id, LateDeliveryDecision.STORE_FAULT_CREDITED)
    assert refused.value.reason_code == "REMEDY_LATE_DELIVERY_CREDIT_ALREADY_PROPOSED"


def test_only_operations_roles_with_mfa_in_the_store(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish_remedies(connection, shop)
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=3))
    with pytest.raises(LateDeliveryAuthorizationError):
        _list(connection, shop, shop.auditor)
    stranger = _staff(connection, uuid4(), StaffRole.OPERATOR)
    with pytest.raises(LateDeliveryAuthorizationError):
        _list(connection, shop, stranger)
    with pytest.raises(LateDeliveryAuthorizationError):
        _decide(
            connection,
            shop,
            order_id,
            LateDeliveryDecision.STORE_FAULT_CREDITED,
            principal=stranger,
        )
    no_mfa = StaffPrincipal(
        shop.operator.staff_user_id,
        shop.operator.oidc_subject,
        shop.operator.roles,
        False,
        uuid4(),
    )
    with pytest.raises(LateDeliveryAuthorizationError):
        _decide(
            connection, shop, order_id, LateDeliveryDecision.STORE_FAULT_CREDITED, principal=no_mfa
        )


def test_the_decision_table_is_append_only_and_bound_to_its_proposal(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish_remedies(connection, shop)
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=3))
    _decide(connection, shop, order_id, LateDeliveryDecision.STORE_FAULT_CREDITED)
    with pytest.raises(psycopg.Error), connection.transaction(), connection.cursor() as cursor:
        cursor.execute("UPDATE late_delivery_decisions SET late_by_minutes = 500")
    with pytest.raises(psycopg.Error), connection.transaction(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM late_delivery_decisions")
    # A decision whose minutes disagree with its proposal's attested minutes is refused by the
    # trigger, whatever path writes it.
    other, promise_other = _delivery_order(connection, shop)
    _leg(connection, shop, other, promise_other + timedelta(hours=3))
    incident = _incident(connection, shop.store_id, other, shop.operator)
    manual = _propose(
        connection,
        shop.store_id,
        incident,
        shop.operator,
        kind=RemedyKind.LATE_DELIVERY_CREDIT,
        store_fault_attested=True,
        attested_late_by_minutes=999,
    )
    with pytest.raises(psycopg.Error), connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO late_delivery_decisions (
                id, order_id, store_id, decision, late_by_minutes, deadline_at, deadline_basis,
                delivered_at, incident_id, remedy_proposal_id, decided_by, decided_at
            ) VALUES (%s, %s, %s, 'STORE_FAULT_CREDITED', 180, %s, 'FIRST_PROMISE', %s, %s, %s,
                      %s, now())
            """,
            (
                uuid4(),
                other,
                shop.store_id,
                promise_other,
                promise_other + timedelta(hours=3),
                incident,
                manual.proposal_id,
                shop.operator.staff_user_id,
            ),
        )


def _report(connection: Any, shop: Shop, day: date) -> Any:
    with connection.cursor() as cursor:
        return ReportRepository.store_report(
            cursor,
            store_id=shop.store_id,
            principal=shop.owner,
            policy=STANDARD_WASH_SLA,
            from_date=day,
            to_date=day,
            as_of=datetime.now(UTC),
        )


def test_the_report_counts_late_credited_not_the_shops_fault_and_undecided(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """`report-v4`: the window's deliveries measured late, by what was decided about them."""

    orders = [_delivery_order(connection, shop) for _ in range(4)]
    arrivals = [
        orders[0][1] + timedelta(minutes=60),  # on time enough: not late
        orders[1][1] + timedelta(hours=3),  # late -> the shop's fault, credited
        orders[2][1] + timedelta(hours=3),  # late -> not the shop's fault
        orders[3][1] + timedelta(hours=3),  # late -> undecided
    ]
    for (order_id, _), at in zip(orders, arrivals, strict=True):
        _leg(connection, shop, order_id, at)
    days = {at.astimezone(VN_ZONE).date() for at in arrivals}
    assert len(days) == 1, "the fixture's arrivals share one shop day"
    day = days.pop()

    before = _report(connection, shop, day).late_deliveries
    assert before.status == "UNAVAILABLE" and before.reason == "REMEDY_POLICY_UNPUBLISHED"
    assert before.late is None and before.undecided is None

    _publish_remedies(connection, shop)
    credited = _decide(connection, shop, orders[1][0], LateDeliveryDecision.STORE_FAULT_CREDITED)
    RemedyProposalRepository().execute(
        connection,
        RemedyExecutionCommand(
            proposal_id=credited.proposal_id,
            principal=shop.operator,
            correlation_id=uuid4(),
            executed_at=datetime.now(UTC),
        ),
    )
    _decide(
        connection,
        shop,
        orders[2][0],
        LateDeliveryDecision.NOT_STORE_FAULT,
        reason=NotStoreFaultReason.CUSTOMER_ABSENT,
    )
    figures = _report(connection, shop, day).late_deliveries
    assert figures.status == "COMPLETE" and figures.threshold_minutes == 120
    assert (figures.measured, figures.late) == (4, 3)
    assert (figures.store_fault, figures.credited) == (1, 1)
    assert figures.credited_vnd == credited.amount_vnd
    assert (figures.not_store_fault, figures.undecided) == (1, 1)


def test_remedy_options_and_the_list_show_the_same_credit(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """The refactor kept `RemedyOptions` on the same probe: one figure, two screens."""

    _publish_remedies(connection, shop)
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=3))
    listed = _list(connection, shop).orders[0]
    incident = _incident(connection, shop.store_id, order_id, shop.operator)
    with connection.cursor() as cursor:
        options = RemedyProposalRepository().options(
            cursor, store_id=shop.store_id, incident_id=incident, principal=shop.operator
        )
    assert listed.credit_vnd is not None
    assert options.late_delivery_credit_vnd == listed.credit_vnd


def test_a_refunded_bill_shows_no_credit(connection: psycopg.Connection[Any], shop: Shop) -> None:
    """`DEC-004`: no credit on a refunded bill (cancelled `SHOP_FAULT_NO_CHARGE`, refunded)."""

    _publish_remedies(connection, shop)
    order_id, promise = _delivery_order(connection, shop)
    _leg(connection, shop, order_id, promise + timedelta(hours=3))
    version = _view(connection, order_id, shop.operator).row_version
    for target in (
        {"commercial_target": CommercialOrderStatus.CANCELLATION_REVIEW},
        {
            "commercial_target": CommercialOrderStatus.CANCELLED,
            "custody_resolution": CustodyResolution.SHOP_FAULT_NO_CHARGE,
        },
    ):
        version = (
            OrderRepository()
            .transition(
                connection,
                OrderTransitionCommand(
                    order_id,
                    version,
                    shop.operator,
                    f"step-{uuid4().hex}",
                    uuid4(),
                    occurred_at=datetime.now(UTC),
                    **target,
                ),
            )
            .row_version
        )
    row = _list(connection, shop).orders[0]
    assert row.refunded is True and row.credit_vnd is None
    assert row.credit_refusal == "ORDER_REFUNDED"


def test_the_summary_count_reads_the_lists_population_without_a_personal_column() -> None:
    """`count_undecided` runs its own statement so the evening summary never selects a name or a
    ticket; its predicates must stay the list's exactly, or the count and the list could differ."""

    from nha_trang_laundry_db.late_deliveries import LATE_CANDIDATES_SQL, LATE_COUNT_SQL

    def predicates(statement: str) -> str:
        return statement[statement.index("WHERE") :]

    assert predicates(LATE_COUNT_SQL) == predicates(LATE_CANDIDATES_SQL)
    for personal in ("customers", "display_name", "counter_tickets", "ticket_number"):
        assert personal not in LATE_COUNT_SQL
