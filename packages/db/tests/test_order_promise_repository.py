"""`PROMISE-001` (`DEC-037`) against real PostgreSQL.

What only a database can prove: that before the owner publishes nothing refuses and no promise is
stored; that after, `RECEIVE` writes the promise in the step's own transaction (its row version,
event with the trace, audit and outbox rows) or refuses and writes nothing; that the first promise
cannot be changed by any path; that a *Hẹn lại* moves only what the customer was told, with its
reason in the ledger and nowhere else; and that the board and the report read the promise.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.order_promises import OrderPromiseRepository, PromiseChangeCommand
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderNotVisibleError,
    OrderPromiseRefused,
    OrderRepository,
    OrderStateError,
    OrderStepCommand,
    OrderStepRequiresHuman,
    OrderView,
)
from nha_trang_laundry_db.promise_policy import (
    TurnaroundPolicyAuthorizationError,
    publish_turnaround_policy,
    read_published_turnaround_policy,
    withdrawal_document,
)
from nha_trang_laundry_db.reports import ReportKey, ReportRepository
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode, Unit
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.pricebook_import import import_pricebook_csv
from nha_trang_laundry_domain.promise import PromiseChangeReason, PromiseChoice
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from nha_trang_laundry_domain.turnaround_source import build_turnaround_policy
from quote_test_data import FixtureLine, accepted_quote, ensure_store

VN = ZoneInfo("Asia/Ho_Chi_Minh")
TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
TET_2027 = [date(2027, 2, day) for day in range(5, 11)]

STANDARD = FixtureLine("line-0", "STANDARD_WASH_DRY", Unit.KG, "5", 25_000, 125_000)
BLANKET = FixtureLine("line-1", "BED_BLANKET", Unit.KG, "3", 30_000, 90_000)
PLUSH = FixtureLine("line-2", "OTHER_PLUSH", Unit.ANIMAL_PLUSH_ITEM, "1", None, 50_000)


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=VN)


#: Thứ Sáu 25/9/2026, 17:00 at the counter: DEC-037's own example.
ACCEPTED = at(2026, 9, 25, 17)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(connection: Any, store_id: UUID, role: StaffRole) -> StaffPrincipal:
    ensure_store(connection, store_id)
    staff_id = uuid4()
    now = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
            """,
            (staff_id, f"oidc-{staff_id}", now),
        )
        cursor.execute(
            """
            INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
            VALUES (%s, %s, %s, %s)
            """,
            (uuid4(), staff_id, role.value, now),
        )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (staff_id, store_id, staff_id, now),
        )
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), True, uuid4())


def _document(tet: list[date] | None = None) -> dict[str, object]:
    pricebook = (TEMPLATES / "services-pricebook.csv").read_bytes()
    book = import_pricebook_csv(pricebook)
    return build_turnaround_policy(
        sla_csv=(TEMPLATES / "service-sla.csv").read_text(encoding="utf-8"),
        calendar_csv=(TEMPLATES / "business-calendar-rules.csv").read_text(encoding="utf-8"),
        services=[(service.code, service.category) for service in book.services],
        tet_dates=TET_2027 if tet is None else tet,
        pricebook_sha256=hashlib.sha256(pricebook).hexdigest(),
    )


def _publish(connection: Any, owner: StaffPrincipal) -> None:
    digest, created = publish_turnaround_policy(
        connection, actor_id=owner.staff_user_id, payload=_document()
    )
    assert created and len(digest) == 64


def _order(
    connection: Any, store_id: UUID, staff: StaffPrincipal, lines: tuple[FixtureLine, ...]
) -> UUID:
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=store_id, principal=staff, lines=lines
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
                FulfillmentMode.SELF_DROP_SELF_COLLECT,
                staff,
                f"order-{uuid4().hex}",
                uuid4(),
                datetime.now(UTC),
                AcquisitionSource.WALK_IN,
            ),
        )
        .order_id
    )


def _view(connection: Any, order_id: UUID, staff: StaffPrincipal) -> OrderView:
    with connection.cursor() as cursor:
        return OrderRepository.read_for_principal(cursor, order_id=order_id, principal=staff)


def _receive(
    connection: Any,
    order_id: UUID,
    staff: StaffPrincipal,
    *,
    occurred_at: datetime = ACCEPTED,
    choice: PromiseChoice | None = None,
    custom_at: datetime | None = None,
    key: str | None = None,
) -> OrderView:
    version = _view(connection, order_id, staff).row_version
    return (
        OrderRepository()
        .execute_step(
            connection,
            OrderStepCommand(
                order_id=order_id,
                expected_row_version=version,
                principal=staff,
                idempotency_key=key or f"receive-{uuid4().hex}",
                correlation_id=uuid4(),
                step=OrderStep.RECEIVE,
                slot_approved=True,
                occurred_at=occurred_at,
                promise_choice=choice,
                custom_promise_at=custom_at,
            ),
        )
        .view
    )


def _step(
    connection: Any, order_id: UUID, staff: StaffPrincipal, step: OrderStep, when: datetime
) -> OrderView:
    version = _view(connection, order_id, staff).row_version
    return (
        OrderRepository()
        .execute_step(
            connection,
            OrderStepCommand(
                order_id=order_id,
                expected_row_version=version,
                principal=staff,
                idempotency_key=f"step-{uuid4().hex}",
                correlation_id=uuid4(),
                step=step,
                occurred_at=when,
            ),
        )
        .view
    )


def _finish(connection: Any, order_id: UUID, staff: StaffPrincipal, ready: datetime) -> None:
    _step(connection, order_id, staff, OrderStep.START_WASH, ready - timedelta(hours=2))
    _step(connection, order_id, staff, OrderStep.QUALITY_CHECK, ready - timedelta(minutes=10))
    _step(connection, order_id, staff, OrderStep.MARK_READY, ready)


def _change(
    connection: Any,
    order_id: UUID,
    staff: StaffPrincipal,
    new_at: datetime,
    *,
    reason: PromiseChangeReason = PromiseChangeReason.MACHINE_ISSUE,
    note: str | None = None,
    key: str | None = None,
    version: int | None = None,
    now: datetime = ACCEPTED + timedelta(hours=1),
) -> OrderView:
    return (
        OrderPromiseRepository()
        .change(
            connection,
            PromiseChangeCommand(
                order_id=order_id,
                expected_row_version=version or _view(connection, order_id, staff).row_version,
                principal=staff,
                idempotency_key=key or f"promise-{uuid4().hex}",
                correlation_id=uuid4(),
                new_promise_at=new_at,
                reason=reason,
                note=note,
                occurred_at=now,
            ),
        )
        .view
    )


def _ledger(connection: Any, order_id: UUID) -> tuple[int, int, int]:
    with connection.cursor() as cursor:
        counts = []
        for table in ("domain_events", "audit_events", "outbox_events"):
            cursor.execute(f"SELECT count(*) FROM {table} WHERE aggregate_id = %s", (order_id,))
            row = cursor.fetchone()
            assert row is not None
            counts.append(int(row[0]))
    return counts[0], counts[1], counts[2]


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> tuple[UUID, StaffPrincipal, StaffPrincipal]:
    store_id = uuid4()
    owner = _staff(connection, store_id, StaffRole.OWNER_ADMIN)
    operator = _staff(connection, store_id, StaffRole.OPERATOR)
    return store_id, owner, operator


# --- before publication: no promise, nothing refuses ------------------------------------------


def test_before_publication_receive_works_and_stores_no_promise(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, _owner, operator = shop
    order_id = _order(connection, store_id, operator, (STANDARD, PLUSH))
    view = _receive(connection, order_id, operator)
    assert view.commercial.value == "ACTIVE"
    assert (view.promised_ready_at, view.current_promise_at, view.promise_basis) == (
        None,
        None,
        None,
    )
    with connection.cursor() as cursor:
        read = OrderPromiseRepository.read(
            cursor, order_id=order_id, principal=operator, now=ACCEPTED
        )
    assert read.policy_published is False and read.options is None and read.state is None


def test_before_publication_a_promise_choice_is_refused_not_dropped(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, _owner, operator = shop
    order_id = _order(connection, store_id, operator, (BLANKET,))
    before = _view(connection, order_id, operator).row_version
    with pytest.raises(OrderPromiseRefused) as refused:
        _receive(connection, order_id, operator, choice=PromiseChoice.H24)
    assert refused.value.code == "TURNAROUND_POLICY_UNPUBLISHED"
    assert _view(connection, order_id, operator).row_version == before


def test_before_publication_hen_lai_is_refused(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, _owner, operator = shop
    order_id = _order(connection, store_id, operator, (STANDARD,))
    _receive(connection, order_id, operator)
    with pytest.raises(OrderPromiseRefused) as refused:
        _change(connection, order_id, operator, at(2026, 9, 26, 15))
    assert refused.value.code == "TURNAROUND_POLICY_UNPUBLISHED"


# --- publication ------------------------------------------------------------------------------


def test_only_an_active_owner_publishes(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    _store, owner, operator = shop
    with pytest.raises(TurnaroundPolicyAuthorizationError):
        publish_turnaround_policy(connection, actor_id=operator.staff_user_id, payload=_document())
    with connection.cursor() as cursor:
        assert read_published_turnaround_policy(cursor) is None
    _publish(connection, owner)
    again = publish_turnaround_policy(connection, actor_id=owner.staff_user_id, payload=_document())
    assert again[1] is False  # the same document in force changes nothing
    with connection.cursor() as cursor:
        published = read_published_turnaround_policy(cursor)
    assert published is not None and published.version == 1
    assert published.policy.tet_years == frozenset({2027})


def test_a_withdrawal_returns_the_shop_to_no_promise(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    publish_turnaround_policy(
        connection, actor_id=owner.staff_user_id, payload=withdrawal_document()
    )
    order_id = _order(connection, store_id, operator, (PLUSH,))
    view = _receive(connection, order_id, operator)
    assert view.promised_ready_at is None


# --- RECEIVE after publication -----------------------------------------------------------------


def test_receive_at_17_00_promises_13_00_the_next_open_day(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD,))
    before = _view(connection, order_id, operator).row_version
    view = _receive(connection, order_id, operator)

    assert view.promised_ready_at == at(2026, 9, 26, 13)
    assert view.current_promise_at == view.promised_ready_at
    assert (view.promise_basis, view.promise_rule_id) == ("RULE", "SLA_STANDARD_CLOTHES")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT e.aggregate_version, e.payload, a.action, a.details, o.payload
            FROM domain_events e
            JOIN audit_events a
              ON a.aggregate_id = e.aggregate_id AND a.correlation_id = e.correlation_id
             AND a.occurred_at = e.occurred_at
            JOIN outbox_events o
              ON o.aggregate_id = e.aggregate_id AND o.event_type = 'order.promise_set.v1'
            WHERE e.aggregate_id = %s AND e.event_type = 'ORDER_PROMISE_SET'
            """,
            (order_id,),
        )
        rows = cursor.fetchall()
    assert len(rows) == 1
    version, event, action, details, outbox = rows[0]
    # The promise is the step's last write, at the step's last row version.
    assert version == view.row_version and view.row_version > before
    assert action == "ORDER_PROMISE_SET"
    assert details == {
        "basis": "RULE",
        "rule_id": "SLA_STANDARD_CLOTHES",
        "event_type": "ORDER_PROMISE_SET",
    }
    assert event["trace"]["lines"][0]["hours"] == 8
    # The instant production accepted the goods: the accepting transition's own stamp.
    assert event["trace"]["accepted_at"].startswith("2026-09-25T10:00:00.0000")
    assert outbox["promised_ready_at"] == view.promised_ready_at.isoformat()


def test_a_blanket_is_promised_48_calendar_hours_unless_the_staff_says_24(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    silent = _receive(
        connection, _order(connection, store_id, operator, (STANDARD, BLANKET)), operator
    )
    assert silent.promised_ready_at == at(2026, 9, 27, 17)  # Friday 17:00 + 48 calendar hours
    assert (silent.promise_basis, silent.promise_rule_id) == ("H48", "SLA_BLANKETS_SHEETS")
    chosen = _receive(
        connection,
        _order(connection, store_id, operator, (BLANKET,)),
        operator,
        choice=PromiseChoice.H24,
    )
    assert chosen.promised_ready_at == at(2026, 9, 26, 17)
    assert chosen.promise_basis == "H24"


def test_a_special_item_refuses_receive_until_a_person_sets_the_time(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD, PLUSH))
    before = _view(connection, order_id, operator)
    ledger = _ledger(connection, order_id)

    with pytest.raises(OrderStepRequiresHuman) as refused:
        _receive(connection, order_id, operator)
    assert refused.value.reason_codes == ("PROMISE_REQUIRED", "HUMAN_ETA_REQUIRED")
    # Nothing at all: not the intake, not the commercial moves, not the promise.
    after = _view(connection, order_id, operator)
    assert (after.row_version, after.commercial, after.intake) == (
        before.row_version,
        before.commercial,
        before.intake,
    )
    assert _ledger(connection, order_id) == ledger

    with connection.cursor() as cursor:
        read = OrderPromiseRepository.read(
            cursor, order_id=order_id, principal=operator, now=ACCEPTED
        )
    assert read.options is not None and read.options.requirement.value == "CUSTOM"
    assert read.options.human_service_codes == ("OTHER_PLUSH",)

    view = _receive(
        connection,
        order_id,
        operator,
        choice=PromiseChoice.CUSTOM,
        custom_at=at(2026, 9, 28, 10),
    )
    assert view.promised_ready_at == at(2026, 9, 28, 10)
    assert (view.promise_basis, view.promise_rule_id) == ("CUSTOM", "STAFF_SET")


@pytest.mark.parametrize(
    ("lines", "choice", "custom_at", "code"),
    [
        ((BLANKET,), PromiseChoice.EXPRESS_2H, None, "PROMISE_CHOICE_NOT_APPLICABLE"),
        ((STANDARD,), PromiseChoice.H24, None, "PROMISE_CHOICE_NOT_APPLICABLE"),
        ((PLUSH,), PromiseChoice.CUSTOM, at(2026, 9, 26, 21), "PROMISE_OUTSIDE_OPENING_HOURS"),
        ((PLUSH,), PromiseChoice.CUSTOM, at(2026, 9, 25, 16), "PROMISE_NOT_AFTER_ACCEPTANCE"),
    ],
)
def test_a_choice_the_rules_refuse_writes_nothing(
    connection: psycopg.Connection[Any],
    shop: tuple[UUID, StaffPrincipal, StaffPrincipal],
    lines: tuple[FixtureLine, ...],
    choice: PromiseChoice,
    custom_at: datetime | None,
    code: str,
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, lines)
    ledger = _ledger(connection, order_id)
    with pytest.raises(OrderPromiseRefused) as refused:
        _receive(connection, order_id, operator, choice=choice, custom_at=custom_at)
    assert refused.value.code == code
    assert _ledger(connection, order_id) == ledger


def test_express_is_two_opening_hours(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    view = _receive(
        connection,
        _order(connection, store_id, operator, (STANDARD,)),
        operator,
        occurred_at=at(2026, 9, 25, 19),
        choice=PromiseChoice.EXPRESS_2H,
    )
    assert view.promised_ready_at == at(2026, 9, 26, 9)


def test_receive_replays_with_its_promise_and_a_changed_choice_is_a_conflict(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (BLANKET,))
    version = _view(connection, order_id, operator).row_version

    def send(choice: PromiseChoice) -> Any:
        return OrderRepository().execute_step(
            connection,
            OrderStepCommand(
                order_id=order_id,
                expected_row_version=version,
                principal=operator,
                idempotency_key="receive-once",
                correlation_id=uuid4(),
                step=OrderStep.RECEIVE,
                slot_approved=True,
                occurred_at=ACCEPTED,
                promise_choice=choice,
            ),
        )

    first = send(PromiseChoice.H24)
    again = send(PromiseChoice.H24)
    assert again.replayed and again.view == first.view
    assert again.view.promised_ready_at == at(2026, 9, 26, 17)
    with pytest.raises(IdempotencyConflictError):
        send(PromiseChoice.H48)


def test_a_promise_choice_is_taken_only_by_receive(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD,))
    view = _receive(connection, order_id, operator)
    with pytest.raises(OrderStateError, match="taken only by RECEIVE"):
        OrderRepository().execute_step(
            connection,
            OrderStepCommand(
                order_id=order_id,
                expected_row_version=view.row_version,
                principal=operator,
                idempotency_key="wash",
                correlation_id=uuid4(),
                step=OrderStep.START_WASH,
                promise_choice=PromiseChoice.H24,
            ),
        )


# --- the first promise is immutable -----------------------------------------------------------


def test_no_path_changes_the_first_promise(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD,))
    _receive(connection, order_id, operator)
    for statement in (
        "UPDATE orders SET promised_ready_at = promised_ready_at + interval '1 hour', "
        "row_version = row_version + 1 WHERE id = %s",
        "UPDATE orders SET promise_basis = 'CUSTOM', row_version = row_version + 1 WHERE id = %s",
        # Moving what the customer was told without a ledger row.
        "UPDATE orders SET current_promise_at = current_promise_at + interval '1 hour', "
        "row_version = row_version + 1 WHERE id = %s",
    ):
        with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
            connection.execute(statement, (order_id,))


# --- Hẹn lại ----------------------------------------------------------------------------------


def test_hen_lai_moves_what_the_customer_was_told_and_keeps_the_first(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD,))
    received = _receive(connection, order_id, operator)
    moved = _change(
        connection,
        order_id,
        operator,
        at(2026, 9, 26, 17),
        reason=PromiseChangeReason.OTHER,
        note="Khách gọi số 0905123456 xin lấy muộn",
    )
    assert moved.promised_ready_at == at(2026, 9, 26, 13)
    assert moved.current_promise_at == at(2026, 9, 26, 17)
    assert moved.row_version == received.row_version + 1

    with connection.cursor() as cursor:
        read = OrderPromiseRepository.read(
            cursor, order_id=order_id, principal=operator, now=at(2026, 9, 26, 16)
        )
        cursor.execute(
            "SELECT payload::text FROM domain_events WHERE aggregate_id = %s "
            "AND event_type = 'ORDER_PROMISE_CHANGED'",
            (order_id,),
        )
        event = cursor.fetchone()
        cursor.execute(
            "SELECT details::text FROM audit_events WHERE aggregate_id = %s "
            "AND action = 'ORDER_PROMISE_CHANGE'",
            (order_id,),
        )
        audit = cursor.fetchone()
        cursor.execute(
            "SELECT payload::text FROM outbox_events WHERE aggregate_id = %s "
            "AND event_type = 'order.promise_changed.v1'",
            (order_id,),
        )
        outbox = cursor.fetchone()
    assert read.state is not None and read.state.value == "DUE_SOON"
    assert [(item.reason_code, item.note) for item in read.changes] == [
        ("OTHER", "Khách gọi số 0905123456 xin lấy muộn")
    ]
    # The note -- free text, here with a phone number in it -- stays in its own table.
    for payload in (event, audit, outbox):
        assert payload is not None
        assert "0905123456" not in payload[0] and "Khách" not in payload[0]
    assert event is not None and '"reason_code": "OTHER"' in event[0]


def test_hen_lai_replays_and_refuses_a_stale_version(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD,))
    received = _receive(connection, order_id, operator)
    first = _change(connection, order_id, operator, at(2026, 9, 26, 15), key="k1")
    again = _change(
        connection, order_id, operator, at(2026, 9, 26, 15), key="k1", version=received.row_version
    )
    assert again == first
    with pytest.raises(OrderStateError, match="STALE_VERSION"):
        _change(connection, order_id, operator, at(2026, 9, 26, 16), version=received.row_version)


@pytest.mark.parametrize(
    ("new_at", "reason", "note", "code"),
    [
        (at(2026, 9, 26, 21), PromiseChangeReason.WORKLOAD, None, "PROMISE_OUTSIDE_OPENING_HOURS"),
        (
            at(2026, 9, 25, 17, 30),
            PromiseChangeReason.WORKLOAD,
            None,
            "PROMISE_NOT_AFTER_ACCEPTANCE",
        ),
        (at(2026, 9, 26, 13), PromiseChangeReason.WORKLOAD, None, "PROMISE_UNCHANGED"),
        (at(2026, 9, 26, 15), PromiseChangeReason.OTHER, None, "PROMISE_NOTE_REQUIRED"),
        (at(2026, 9, 26, 15), PromiseChangeReason.OTHER, "x" * 121, "PROMISE_NOTE_TOO_LONG"),
    ],
)
def test_hen_lai_refusals_write_nothing(
    connection: psycopg.Connection[Any],
    shop: tuple[UUID, StaffPrincipal, StaffPrincipal],
    new_at: datetime,
    reason: PromiseChangeReason,
    note: str | None,
    code: str,
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    order_id = _order(connection, store_id, operator, (STANDARD,))
    _receive(connection, order_id, operator)
    ledger = _ledger(connection, order_id)
    with pytest.raises(OrderPromiseRefused) as refused:
        _change(connection, order_id, operator, new_at, reason=reason, note=note)
    assert refused.value.code == code
    assert _ledger(connection, order_id) == ledger


def test_hen_lai_needs_a_promise_and_unfinished_laundry(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    unpromised = _order(connection, store_id, operator, (STANDARD,))
    _receive(connection, unpromised, operator)
    _publish(connection, owner)
    with pytest.raises(OrderPromiseRefused) as not_set:
        _change(connection, unpromised, operator, at(2026, 9, 26, 15))
    assert not_set.value.code == "PROMISE_NOT_SET"

    order_id = _order(connection, store_id, operator, (STANDARD,))
    _receive(connection, order_id, operator)
    _finish(connection, order_id, operator, at(2026, 9, 26, 11))
    with pytest.raises(OrderPromiseRefused) as done:
        _change(connection, order_id, operator, at(2026, 9, 26, 15))
    assert done.value.code == "PROMISE_ORDER_DONE"


def test_the_promise_read_is_store_scoped(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, _owner, operator = shop
    stranger = _staff(connection, uuid4(), StaffRole.OPERATOR)
    order_id = _order(connection, store_id, operator, (STANDARD,))
    with pytest.raises(OrderNotVisibleError), connection.cursor() as cursor:
        OrderPromiseRepository.read(cursor, order_id=order_id, principal=stranger, now=ACCEPTED)


# --- the board and the report read the promise ------------------------------------------------


def test_the_board_ranks_by_the_order_promise_and_says_which_rule(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    # Accepted first, before publication: the stated rule's mark is 17:00 + 8 h = 01:00 on 26/9.
    unpromised = _order(connection, store_id, operator, (STANDARD,))
    _receive(connection, unpromised, operator)
    _publish(connection, owner)
    # Same instant, promised 27/9 17:00 (48 calendar hours): due later, so ranked below.
    blanket = _order(connection, store_id, operator, (BLANKET,))
    _receive(connection, blanket, operator)
    # Express, promised 19:00 on 25/9: due soonest.
    express = _order(connection, store_id, operator, (STANDARD,))
    _receive(connection, express, operator, choice=PromiseChoice.EXPRESS_2H)

    board = ShadowConsoleRepository().sla_risk_board(
        connection,
        store_id=store_id,
        principal=operator,
        policy=STANDARD_WASH_SLA,
        now=at(2026, 9, 25, 20),
    )
    assert [row.order_id for row in board] == [express, unpromised, blanket]
    assert [row.rule_source for row in board] == ["ORDER_PROMISE", "STATED_RULE", "ORDER_PROMISE"]
    assert [row.promise_rule_id for row in board] == ["EXPRESS_2H", None, "SLA_BLANKETS_SHEETS"]
    assert board[0].sla_outcome == "BREACHED" and board[0].breach_microseconds == 3_600_000_000
    assert board[2].internal_risk_due_at == at(2026, 9, 27, 17)
    assert board[2].remaining_microseconds is not None and board[2].remaining_microseconds > 0

    # The keyset is the ranking: paging one row at a time returns the same list.
    paged: list[UUID] = []
    after: tuple[datetime, UUID] | None = None
    while True:
        page = ShadowConsoleRepository().sla_risk_board(
            connection,
            store_id=store_id,
            principal=operator,
            policy=STANDARD_WASH_SLA,
            now=at(2026, 9, 25, 20),
            limit=1,
            after=after,
        )
        if not page:
            break
        paged.append(page[0].order_id)
        assert page[0].due_at is not None
        after = (page[0].due_at, page[0].order_id)
    assert paged == [row.order_id for row in board]


def test_on_time_counts_against_the_first_promise_and_is_complete_when_every_order_had_one(
    connection: psycopg.Connection[Any], shop: tuple[UUID, StaffPrincipal, StaffPrincipal]
) -> None:
    store_id, owner, operator = shop
    _publish(connection, owner)
    on_time = _order(connection, store_id, operator, (STANDARD,))
    _receive(connection, on_time, operator)
    _finish(connection, on_time, operator, at(2026, 9, 26, 12))
    # Re-promised to 18:00 and ready at 16:00: on time for the customer, late against the first
    # promise -- and the figure counts the first.
    repromised = _order(connection, store_id, operator, (STANDARD,))
    _receive(connection, repromised, operator)
    _change(connection, repromised, operator, at(2026, 9, 26, 18))
    _finish(connection, repromised, operator, at(2026, 9, 26, 16))

    def on_time_figure() -> Any:
        with connection.cursor() as cursor:
            report = ReportRepository.store_report(
                cursor,
                store_id=store_id,
                principal=owner,
                policy=STANDARD_WASH_SLA,
                from_date=date(2026, 9, 26),
                to_date=date(2026, 9, 26),
                as_of=at(2026, 9, 30, 9),
            )
        return next(f for f in report.summary.figures if f.key is ReportKey.ON_TIME_INTERNAL)

    figure = on_time_figure()
    assert (figure.numerator, figure.denominator) == (1, 2)
    assert figure.data_quality.value == "COMPLETE" and figure.rule_assumed == 0

    # An order taken while the policy was withdrawn is judged by the stated rule, and says so.
    publish_turnaround_policy(
        connection, actor_id=owner.staff_user_id, payload=withdrawal_document()
    )
    unpromised = _order(connection, store_id, operator, (STANDARD,))
    _receive(connection, unpromised, operator, occurred_at=at(2026, 9, 26, 9))
    _finish(connection, unpromised, operator, at(2026, 9, 26, 14))
    figure = on_time_figure()
    assert (figure.numerator, figure.denominator) == (2, 3)
    assert figure.data_quality.value == "RULE_ASSUMED" and figure.rule_assumed == 1
