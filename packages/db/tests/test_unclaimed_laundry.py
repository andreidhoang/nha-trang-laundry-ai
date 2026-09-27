"""`UNCLAIMED-001` (`DEC-036`) against real PostgreSQL.

What only the database can prove: that before the owner publishes, the waiting list and the contact
attempts work and nothing is charged or disposed of; that after, the storage fee is a charge on the
order computed at read and fixed by the settling payment, in one transaction with its settlement;
that a waiver is recorded and moves the order's version; that disposal closes the order with money
paid kept and money owed written off, and that `0056`'s ledger rules and `0060`'s checks agree with
it; and that no phone number, note or reason reaches an event, audit or outbox payload.

Harness step (documented): the laundry's ready time is moved back with one SQL statement
(`_age`), exactly as the real-API conformance scenario does -- nothing else can make an order
twenty-five days old inside a test.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.customers import CustomerRepository
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderNotVisibleError,
    OrderRepository,
    OrderStateError,
)
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository, StoredPayment
from nha_trang_laundry_db.privacy_notice import publish_privacy_notice
from nha_trang_laundry_db.settlement import (
    SettlementCommand,
    SettlementRepository,
    SettlementStateError,
)
from nha_trang_laundry_db.storage_fees import (
    StoragePolicyAuthorizationError,
    publish_storage_policy,
    read_published_storage_policy,
)
from nha_trang_laundry_db.unclaimed import (
    AWAITING_PICKUP_SQL,
    ContactAttemptCommand,
    DisposalCommand,
    UnclaimedAuthorizationError,
    UnclaimedRefused,
    UnclaimedRepository,
    WaiverCommand,
)
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    OrderBalanceStatus,
)
from nha_trang_laundry_domain.customers import CustomerKind
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import ChargeKind, PaymentMethod
from nha_trang_laundry_domain.unclaimed import (
    ContactChannel,
    ContactOutcome,
    StorageFeeStatus,
    withdrawal_document,
)
from quote_test_data import accepted_quote, ensure_store
from test_customer_notice import notice_payload
from test_order_step_repository import TOTAL_VND, _read, _step

CASH = PaymentMethod.TIEN_MAT
VN = timedelta(hours=7)
TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "storage-policy-dec-036.json"


def policy_payload() -> dict[str, Any]:
    """The document the owner publishes: `DEC-036`'s figures, from the repository's template."""

    loaded: dict[str, Any] = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(connection: Any, store_id: UUID, role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
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
    return StaffPrincipal(staff_id, f"oidc-{staff_id}", frozenset({role}), mfa, uuid4())


class Shop:
    def __init__(self, connection: Any) -> None:
        self.store_id = uuid4()
        self.owner = _staff(connection, self.store_id, StaffRole.OWNER_ADMIN)
        self.approver = _staff(connection, self.store_id, StaffRole.OPS_APPROVER)
        self.operator = _staff(connection, self.store_id, StaffRole.OPERATOR)
        self.auditor = _staff(connection, self.store_id, StaffRole.AUDITOR)


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Generator[Shop, None, None]:
    """A store with one of each role, starting -- and ending -- with no storage policy in force.

    The policy is one per deployment, so a test that leaves it published would charge storage fees
    in every later test that ages an order. The withdrawal on the way out is the owner's reversal.
    """

    made = Shop(connection)
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )
    yield made
    connection.rollback()
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )


def _publish(connection: Any, shop: Shop) -> None:
    publish_storage_policy(connection, actor_id=shop.owner.staff_user_id, payload=policy_payload())


def _order(
    connection: Any,
    shop: Shop,
    *,
    mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
    customer_id: UUID | None = None,
) -> UUID:
    quote_id, revision, quote, contact_id = accepted_quote(
        connection,
        store_id=shop.store_id,
        principal=shop.operator,
        fulfillment_mode=mode,
        customer_id=customer_id,
    )
    return (
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


def _ready(connection: Any, shop: Shop, **kwargs: Any) -> UUID:
    order_id = _order(connection, shop, **kwargs)
    view = _step(connection, order_id, shop.operator, 1, OrderStep.RECEIVE, slot_approved=True).view
    for step in (OrderStep.START_WASH, OrderStep.QUALITY_CHECK, OrderStep.MARK_READY):
        view = _step(connection, order_id, shop.operator, view.row_version, step).view
    connection.commit()
    return order_id


def _age(connection: Any, order_id: UUID, days: int) -> None:
    """The documented harness step: the laundry was accepted and reported ready `days` days
    earlier. Both stamps move together, so the order stays one the SLA engine can read (ready is
    never before accepted); any promise, which is immutable once set, is left as it was."""

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE orders
            SET production_ready_at = production_ready_at - make_interval(days => %s),
                production_accepted_at = production_accepted_at - make_interval(days => %s),
                row_version = row_version + 1
            WHERE id = %s
            """,
            (days, days, order_id),
        )
    connection.commit()


def _ready_at(connection: Any, order_id: UUID) -> datetime:
    value: datetime = _rows(
        connection, "SELECT production_ready_at FROM orders WHERE id = %s", order_id
    )[0][0]
    return value


def _rows(connection: Any, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [tuple(row) for row in cursor.fetchall()]


def _pay(
    connection: Any,
    order_id: UUID,
    staff: StaffPrincipal,
    amount: int,
    *,
    collected: bool = False,
    at: datetime | None = None,
) -> StoredPayment:
    return PaymentRepository().record(
        connection,
        PaymentCommand(
            order_id=order_id,
            expected_row_version=_read(connection, order_id, staff).row_version,
            amount_vnd=amount,
            method=CASH,
            transfer_seen=False,
            bank_ref_last=None,
            collected_by_customer=collected,
            principal=staff,
            correlation_id=uuid4(),
            recorded_at=at,
        ),
    )


def _attempt(
    connection: Any,
    order_id: UUID,
    staff: StaffPrincipal,
    *,
    at: datetime | None = None,
    note: str | None = None,
    key: str | None = None,
    outcome: ContactOutcome = ContactOutcome.NO_ANSWER,
) -> Any:
    stored = UnclaimedRepository().record_contact_attempt(
        connection,
        ContactAttemptCommand(
            order_id=order_id,
            principal=staff,
            idempotency_key=key or f"attempt-{uuid4().hex}",
            correlation_id=uuid4(),
            channel=ContactChannel.CALL,
            outcome=outcome,
            note=note,
            attempted_at=at,
        ),
    )
    connection.commit()
    return stored


def _waive(connection: Any, order_id: UUID, staff: StaffPrincipal, reason: str) -> Any:
    return UnclaimedRepository().waive_storage_fee(
        connection,
        WaiverCommand(
            order_id=order_id,
            expected_row_version=_read(connection, order_id, staff).row_version,
            principal=staff,
            idempotency_key=f"waive-{uuid4().hex}",
            correlation_id=uuid4(),
            reason=reason,
        ),
    )


def _dispose(connection: Any, order_id: UUID, staff: StaffPrincipal) -> Any:
    return UnclaimedRepository().dispose(
        connection,
        DisposalCommand(
            order_id=order_id,
            expected_row_version=_read(connection, order_id, staff).row_version,
            principal=staff,
            idempotency_key=f"dispose-{uuid4().hex}",
            correlation_id=uuid4(),
        ),
    )


def _list(connection: Any, shop: Shop, staff: StaffPrincipal | None = None) -> Any:
    with connection.cursor() as cursor:
        return UnclaimedRepository.list_awaiting_pickup(
            cursor,
            store_id=shop.store_id,
            principal=staff or shop.operator,
            as_of=datetime.now(UTC),
        )


def _storage(connection: Any, order_id: UUID, staff: StaffPrincipal) -> Any:
    with connection.cursor() as cursor:
        return UnclaimedRepository.read_order_storage(
            cursor, order_id=order_id, principal=staff, as_of=datetime.now(UTC)
        )


def _payloads(connection: Any, order_id: UUID) -> list[str]:
    texts: list[str] = []
    for table, column in (
        ("domain_events", "payload"),
        ("audit_events", "details"),
        ("outbox_events", "payload"),
        ("command_idempotency_records", "response"),
    ):
        key = "scope LIKE %s" if table == "command_idempotency_records" else "aggregate_id = %s"
        value: object = (
            f"order:{order_id}:%" if table == "command_idempotency_records" else order_id
        )
        texts += [
            str(text)
            for (text,) in _rows(
                connection, f"SELECT {column}::text FROM {table} WHERE {key}", value
            )
        ]
    return texts


def _shop_day(days_ago: int, hour: int) -> datetime:
    """`hour`:00 shop time, `days_ago` shop days before today."""

    today = (datetime.now(UTC) + VN).date()
    local = datetime(today.year, today.month, today.day, hour) - timedelta(days=days_ago)
    return (local - VN).replace(tzinfo=UTC)


# --- before publication ------------------------------------------------------------------------


def test_before_publication_the_list_and_attempts_work_and_nothing_is_charged(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    with connection.cursor() as cursor:
        assert read_published_storage_policy(cursor) is None
    order_id = _ready(connection, shop)
    _age(connection, order_id, 45)

    listed = _list(connection, shop)
    assert listed.policy is None
    row = next(item for item in listed.orders if item.order_id == order_id)
    assert row.days_waiting == 45
    assert (row.fee.status, row.fee.amount_vnd) == (StorageFeeStatus.POLICY_UNPUBLISHED, 0)
    assert row.remaining_vnd == TOTAL_VND

    stored = _attempt(connection, order_id, shop.operator, note="hẹn chiều mai qua")
    assert stored.ordinal == 1
    assert _list(connection, shop).orders[0].attempts_count == 1

    view = _read(connection, order_id, shop.operator)
    assert [(c.kind, c.amount_vnd) for c in view.charges] == [(ChargeKind.QUOTED_TOTAL, TOTAL_VND)]
    with pytest.raises(UnclaimedRefused) as refused:
        _waive(connection, order_id, shop.approver, "khách quen")
    assert refused.value.code == "STORAGE_POLICY_UNPUBLISHED"
    connection.rollback()
    with pytest.raises(UnclaimedRefused) as refused:
        _dispose(connection, order_id, shop.owner)
    assert refused.value.code == "STORAGE_POLICY_UNPUBLISHED"
    connection.rollback()
    verdict = _storage(connection, order_id, shop.operator).disposal_verdict
    assert "STORAGE_POLICY_UNPUBLISHED" in {refusal.value for refusal in verdict.refusals}


def test_only_the_owner_publishes_the_policy(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    with pytest.raises(StoragePolicyAuthorizationError):
        publish_storage_policy(
            connection, actor_id=shop.approver.staff_user_id, payload=policy_payload()
        )
    connection.rollback()
    _publish(connection, shop)
    _digest, created = publish_storage_policy(
        connection, actor_id=shop.owner.staff_user_id, payload=policy_payload()
    )
    assert created is False  # the document already in force changes nothing
    with connection.cursor() as cursor:
        published = read_published_storage_policy(cursor)
    assert published is not None and published.policy.free_days == 20


# --- the fee -----------------------------------------------------------------------------------


def test_the_fee_is_a_charge_computed_at_read_and_fixed_by_the_settling_payment(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id = _ready(connection, shop)
    _age(connection, order_id, 20)
    assert _read(connection, order_id, shop.operator).owed_vnd == TOTAL_VND  # day 20: free
    _age(connection, order_id, 5)  # day 25: five started days past the free twenty

    view = _read(connection, order_id, shop.operator)
    assert [(c.kind, c.amount_vnd) for c in view.charges] == [
        (ChargeKind.QUOTED_TOTAL, TOTAL_VND),
        (ChargeKind.STORAGE_FEE, 25_000),
    ]
    assert (view.owed_vnd, view.remaining_vnd) == (TOTAL_VND + 25_000, TOTAL_VND + 25_000)
    row = next(item for item in _list(connection, shop).orders if item.order_id == order_id)
    assert (row.days_waiting, row.fee.amount_vnd, row.remaining_vnd) == (25, 25_000, 135_000)

    # The exact-total route takes only the quoted total, so it refuses and says where to go.
    with pytest.raises(SettlementStateError) as refused:
        SettlementRepository().record(
            connection,
            SettlementCommand(
                order_id=order_id,
                paid_amount_vnd=TOTAL_VND,
                collected_by_customer=True,
                principal=shop.operator,
                correlation_id=uuid4(),
            ),
        )
    assert refused.value.reason_code == "STORAGE_FEE_OWED"
    connection.rollback()

    # The quoted total alone leaves the fee owed: the goods do not leave.
    part = _pay(connection, order_id, shop.operator, TOTAL_VND)
    connection.commit()
    assert (part.balance_status, part.remaining_vnd) == ("PARTIALLY_PAID", 25_000)
    # Cash at pickup, the fee included: paid in full, collected, fixed.
    rest = _pay(connection, order_id, shop.operator, 25_000, collected=True)
    connection.commit()
    assert (rest.balance_status, rest.owed_vnd, rest.remaining_vnd) == ("PAID", 135_000, 0)
    assert _rows(
        connection,
        "SELECT expected_total_vnd, paid_amount_vnd FROM order_settlements WHERE order_id = %s",
        order_id,
    ) == [(135_000, 135_000)]
    assert _rows(
        connection,
        "SELECT amount_vnd, days_waiting, chargeable_days FROM order_storage_fees "
        "WHERE order_id = %s",
        order_id,
    ) == [(25_000, 25, 5)]

    # What the customer paid is what was owed then: ten more days change nothing, and neither
    # does the owner withdrawing the policy.
    _age(connection, order_id, 10)
    publish_storage_policy(
        connection, actor_id=shop.owner.staff_user_id, payload=withdrawal_document()
    )
    view = _read(connection, order_id, shop.operator)
    assert (view.owed_vnd, view.paid_vnd, view.remaining_vnd) == (135_000, 135_000, 0)
    done = _step(connection, order_id, shop.operator, view.row_version, OrderStep.HAND_OVER).view
    connection.commit()
    assert done.commercial is CommercialOrderStatus.COMPLETED
    payment_events = _rows(
        connection,
        "SELECT payload ->> 'storage_fee_vnd', payload ->> 'storage_fee_fixed' FROM domain_events "
        "WHERE aggregate_id = %s AND event_type = 'ORDER_PAYMENT_RECORDED' ORDER BY occurred_at",
        order_id,
    )
    assert payment_events == [("25000", None), ("25000", "true")]


def test_the_fee_is_capped_at_half_the_quoted_total(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id = _ready(connection, shop)
    _age(connection, order_id, 59)
    view = _read(connection, order_id, shop.operator)
    assert view.owed_vnd == TOTAL_VND + TOTAL_VND // 2


def test_a_delivery_order_is_not_on_the_list_and_owes_no_storage(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id = _ready(connection, shop, mode=FulfillmentMode.PICKUP_AND_RETURN)
    _age(connection, order_id, 40)
    assert order_id not in {item.order_id for item in _list(connection, shop).orders}
    assert _read(connection, order_id, shop.operator).owed_vnd == TOTAL_VND
    with pytest.raises(UnclaimedRefused, match="NOT_AWAITING_PICKUP"):
        _attempt(connection, order_id, shop.operator)
    connection.rollback()


def test_the_list_states_the_domain_rule(connection: psycopg.Connection[Any], shop: Shop) -> None:
    waiting = _ready(connection, shop)
    pickup_only = _ready(connection, shop, mode=FulfillmentMode.PICKUP_ONLY)
    in_machine = _order(connection, shop)
    view = _step(
        connection, in_machine, shop.operator, 1, OrderStep.RECEIVE, slot_approved=True
    ).view
    _step(connection, in_machine, shop.operator, view.row_version, OrderStep.START_WASH)
    delivered = _ready(connection, shop, mode=FulfillmentMode.RETURN_ONLY)
    connection.commit()
    listed = {item.order_id for item in _list(connection, shop).orders}
    assert {waiting, pickup_only} <= listed
    assert not {in_machine, delivered} & listed
    assert (
        "READY_AT_STORE" in AWAITING_PICKUP_SQL
        and "self_collection_recorded" in AWAITING_PICKUP_SQL
    )


# --- the waiver --------------------------------------------------------------------------------


def test_an_approver_waives_the_fee_with_a_reason_and_it_is_recorded(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id = _ready(connection, shop)
    _age(connection, order_id, 30)
    before = _read(connection, order_id, shop.operator)
    assert before.owed_vnd == TOTAL_VND + 50_000

    with pytest.raises(UnclaimedAuthorizationError):
        _waive(connection, order_id, shop.operator, "khách quen")
    connection.rollback()
    with pytest.raises(UnclaimedRefused, match="NOTE_LOOKS_LIKE_PHONE"):
        _waive(connection, order_id, shop.approver, "khách quen 0905 123 456")
    connection.rollback()

    result = _waive(connection, order_id, shop.approver, "khách quen, chị Lan")
    connection.commit()
    assert result.view.row_version == before.row_version + 1
    assert (result.view.owed_vnd, result.view.remaining_vnd) == (TOTAL_VND, TOTAL_VND)
    assert _rows(
        connection,
        "SELECT waived_amount_vnd, days_waiting, reason FROM storage_fee_waivers "
        "WHERE order_id = %s",
        order_id,
    ) == [(50_000, 30, "khách quen, chị Lan")]
    assert all("chị Lan" not in text for text in _payloads(connection, order_id))
    storage = _storage(connection, order_id, shop.auditor)
    assert storage.fee.status is StorageFeeStatus.WAIVED and storage.waiver is not None
    with pytest.raises(UnclaimedRefused, match="STORAGE_FEE_ALREADY_WAIVED"):
        _waive(connection, order_id, shop.owner, "lần nữa")
    connection.rollback()
    # Ten more days: still nothing. Paying the quoted total settles it, with no fee row.
    _age(connection, order_id, 10)
    paid = _pay(connection, order_id, shop.operator, TOTAL_VND, collected=True)
    connection.commit()
    assert (paid.balance_status, paid.owed_vnd) == ("PAID", TOTAL_VND)
    assert _rows(
        connection, "SELECT count(*) FROM order_storage_fees WHERE order_id = %s", order_id
    ) == [(0,)]


def test_nothing_to_waive_in_the_free_days(connection: psycopg.Connection[Any], shop: Shop) -> None:
    _publish(connection, shop)
    order_id = _ready(connection, shop)
    _age(connection, order_id, 3)
    with pytest.raises(UnclaimedRefused, match="NO_STORAGE_FEE_OWED"):
        _waive(connection, order_id, shop.owner, "khách quen")
    connection.rollback()


# --- contact attempts --------------------------------------------------------------------------


def test_a_contact_attempt_is_append_only_idempotent_and_keeps_its_note_to_itself(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id = _ready(connection, shop)
    key = f"attempt-{uuid4().hex}"
    first = _attempt(connection, order_id, shop.operator, note="máy bận, gọi lại", key=key)
    again = _attempt(connection, order_id, shop.operator, note="máy bận, gọi lại", key=key)
    assert again.replayed and again.attempt_id == first.attempt_id
    with pytest.raises(IdempotencyConflictError):
        _attempt(connection, order_id, shop.operator, note="khác", key=key)
    connection.rollback()
    with pytest.raises(UnclaimedRefused, match="NOTE_LOOKS_LIKE_PHONE"):
        _attempt(connection, order_id, shop.operator, note="gọi số 0905123456")
    connection.rollback()
    with pytest.raises(UnclaimedAuthorizationError):
        _attempt(connection, order_id, shop.auditor)
    connection.rollback()
    assert _rows(
        connection, "SELECT count(*) FROM order_contact_attempts WHERE order_id = %s", order_id
    ) == [(1,)]
    assert all("máy bận" not in text for text in _payloads(connection, order_id))
    read = _storage(connection, order_id, shop.auditor)
    assert [(a.channel, a.outcome, a.note) for a in read.attempts] == [
        ("CALL", "NO_ANSWER", "máy bận, gọi lại")
    ]
    with (
        pytest.raises(psycopg.errors.RaiseException),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE order_contact_attempts SET outcome = 'REACHED' WHERE order_id = %s",
            (order_id,),
        )
    connection.rollback()
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO order_contact_attempts (
                id, order_id, store_id, channel, outcome, note, attempted_by_staff_id,
                attempted_at, created_at
            ) VALUES (%s, %s, %s, 'CALL', 'REACHED', '0905.123.456', %s, now(), now())
            """,
            (uuid4(), order_id, shop.store_id, shop.operator.staff_user_id),
        )
    connection.rollback()


# --- disposal ----------------------------------------------------------------------------------


def test_the_owner_disposes_after_sixty_days_and_three_attempts_on_two_days(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id = _ready(connection, shop)
    deposit = _pay(connection, order_id, shop.operator, 50_000)
    connection.commit()
    assert deposit.balance_status == "PARTIALLY_PAID"
    _age(connection, order_id, 65)
    # Three attempts, all today: one shop day is not enough.
    for hour in (9, 10, 11):
        _attempt(connection, order_id, shop.operator, at=_shop_day(0, hour))
    with pytest.raises(UnclaimedRefused) as refused:
        _dispose(connection, order_id, shop.owner)
    assert refused.value.reason_codes == ("CONTACT_DAYS_TOO_FEW",)
    connection.rollback()
    _attempt(connection, order_id, shop.operator, at=_shop_day(3, 15))

    # Only the owner, only with MFA.
    for who in (
        shop.operator,
        shop.approver,
        _staff(connection, shop.store_id, StaffRole.OWNER_ADMIN, mfa=False),
    ):
        with pytest.raises(UnclaimedAuthorizationError):
            _dispose(connection, order_id, who)
        connection.rollback()
    # The ordinary cancellation cannot say UNCLAIMED_DISPOSED.
    view = _read(connection, order_id, shop.owner)
    with pytest.raises(OrderStateError, match="DEC-036"):
        _step(
            connection,
            order_id,
            shop.owner,
            view.row_version,
            OrderStep.CANCEL,
            custody_resolution=CustodyResolution.UNCLAIMED_DISPOSED,
        )
    connection.rollback()

    owed = TOTAL_VND + TOTAL_VND // 2  # 65 days: the fee is at its cap
    result = _dispose(connection, order_id, shop.owner)
    connection.commit()
    assert result.view.commercial is CommercialOrderStatus.CANCELLED
    # Money already paid is kept: the balance is what the ledger holds, and nothing went back.
    assert result.view.balance is OrderBalanceStatus.PARTIALLY_PAID
    assert result.view.paid_vnd == 50_000
    assert _rows(
        connection, "SELECT count(*) FROM order_refunds WHERE order_id = %s", order_id
    ) == [(0,)]
    assert _rows(
        connection,
        "SELECT days_waiting, attempts_counted, attempt_days, owed_vnd, storage_fee_vnd, kept_vnd, "
        "written_off_vnd FROM order_disposals WHERE order_id = %s",
        order_id,
    ) == [(65, 4, 2, owed, TOTAL_VND // 2, 50_000, owed - 50_000)]
    transitions = _rows(
        connection,
        "SELECT payload ->> 'target', payload ->> 'custody_resolution' FROM domain_events "
        "WHERE aggregate_id = %s AND event_type = 'ORDER_STATE_TRANSITIONED' "
        "ORDER BY aggregate_version DESC LIMIT 2",
        order_id,
    )
    assert transitions == [("CANCELLED", "UNCLAIMED_DISPOSED"), ("CANCELLATION_REVIEW", None)]
    assert _rows(
        connection,
        "SELECT count(*) FROM outbox_events WHERE aggregate_id = %s "
        "AND event_type = 'order.unclaimed_disposed.v1'",
        order_id,
    ) == [(1,)]
    storage = _storage(connection, order_id, shop.auditor)
    assert storage.disposal is not None and storage.disposal.written_off_vnd == owed - 50_000
    assert order_id not in {item.order_id for item in _list(connection, shop).orders}


def test_disposal_of_an_unpaid_and_of_a_prepaid_order(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    unpaid = _ready(connection, shop)
    prepaid = _ready(connection, shop)
    _pay(connection, prepaid, shop.operator, TOTAL_VND)
    connection.commit()
    for order_id in (unpaid, prepaid):
        _age(connection, order_id, 60)
        for days_ago in (1, 1, 0):
            _attempt(connection, order_id, shop.operator, at=_shop_day(days_ago, 10))
    too_early = _ready(connection, shop)
    _age(connection, too_early, 59)
    for days_ago in (1, 1, 0):
        _attempt(connection, too_early, shop.operator, at=_shop_day(days_ago, 10))
    with pytest.raises(UnclaimedRefused) as refused:
        _dispose(connection, too_early, shop.owner)
    assert refused.value.reason_codes == ("DISPOSAL_TOO_EARLY",)
    connection.rollback()

    first = _dispose(connection, unpaid, shop.owner)
    connection.commit()
    assert first.view.balance is OrderBalanceStatus.UNPAID
    second = _dispose(connection, prepaid, shop.owner)
    connection.commit()
    # Paid before any fee: the settlement fixed a zero fee, so nothing is written off.
    assert second.view.balance is OrderBalanceStatus.PAID
    assert _rows(
        connection,
        "SELECT order_id = %s, owed_vnd, kept_vnd, written_off_vnd FROM order_disposals "
        "WHERE order_id IN (%s, %s) ORDER BY order_id = %s",
        unpaid,
        unpaid,
        prepaid,
        unpaid,
    ) == [
        (False, TOTAL_VND, TOTAL_VND, 0),
        (True, TOTAL_VND + TOTAL_VND // 2, 0, TOTAL_VND + TOTAL_VND // 2),
    ]


def test_the_ledgers_refuse_a_disposal_that_does_not_agree(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """`0056` and `0060` from the database's side: whoever writes, the rows must agree."""

    _publish(connection, shop)
    order_id = _ready(connection, shop)
    _pay(connection, order_id, shop.operator, 20_000)
    connection.commit()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM configuration_versions WHERE config_type = 'STORAGE_POLICY' "
            "ORDER BY version DESC LIMIT 1"
        )
        found = cursor.fetchone()
        assert found is not None
        policy_id = found[0]

    def insert_disposal(cursor: Any, kept: int) -> None:
        cursor.execute(
            """
            INSERT INTO order_disposals (
                id, order_id, store_id, days_waiting, attempts_counted, attempt_days, owed_vnd,
                storage_fee_vnd, kept_vnd, written_off_vnd, policy_version_id,
                disposed_by_staff_id, disposed_at
            ) VALUES (%s, %s, %s, 61, 3, 2, %s, 0, %s, %s, %s, %s, now())
            """,
            (
                uuid4(),
                order_id,
                shop.store_id,
                TOTAL_VND,
                kept,
                TOTAL_VND - kept,
                policy_id,
                shop.owner.staff_user_id,
            ),
        )

    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as raw:
        # A disposal beside an order that did not close cannot commit.
        with (
            pytest.raises(psycopg.errors.RaiseException, match="closes its order"),
            raw.transaction(),
            raw.cursor() as cursor,
        ):
            insert_disposal(cursor, 20_000)
        # A partly paid order cannot close without a refund unless its disposal is recorded.
        with (
            pytest.raises(psycopg.errors.RaiseException, match="without refunding"),
            raw.transaction(),
            raw.cursor() as cursor,
        ):
            for target in ("CANCELLATION_REVIEW", "CANCELLED"):
                cursor.execute(
                    "UPDATE orders SET commercial_status = %s, row_version = row_version + 1 "
                    "WHERE id = %s",
                    (target, order_id),
                )
        # With it, the close commits only when the disposal keeps exactly what was paid.
        with (
            pytest.raises(psycopg.errors.RaiseException, match="keeps exactly"),
            raw.transaction(),
            raw.cursor() as cursor,
        ):
            insert_disposal(cursor, 0)
            for target in ("CANCELLATION_REVIEW", "CANCELLED"):
                cursor.execute(
                    "UPDATE orders SET commercial_status = %s, row_version = row_version + 1 "
                    "WHERE id = %s",
                    (target, order_id),
                )
        # A disposal whose money does not add up is refused outright.
        with (
            pytest.raises(psycopg.errors.CheckViolation),
            raw.transaction(),
            raw.cursor() as cursor,
        ):
            cursor.execute(
                """
                INSERT INTO order_disposals (
                    id, order_id, store_id, days_waiting, attempts_counted, attempt_days,
                    owed_vnd, storage_fee_vnd, kept_vnd, written_off_vnd, policy_version_id,
                    disposed_by_staff_id, disposed_at
                ) VALUES (%s, %s, %s, 61, 3, 2, 100, 0, 20, 70, %s, %s, now())
                """,
                (uuid4(), order_id, shop.store_id, policy_id, shop.owner.staff_user_id),
            )
        # A storage fee row that does not match its settlement cannot commit either.
        with pytest.raises(psycopg.errors.Error), raw.transaction(), raw.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO order_storage_fees (
                        id, order_id, store_id, settlement_id, amount_vnd, days_waiting,
                        chargeable_days, policy_version_id, fixed_by_staff_id, fixed_at
                    ) VALUES (%s, %s, %s, %s, 5000, 21, 1, %s, %s, now())
                    """,
                (
                    uuid4(),
                    order_id,
                    shop.store_id,
                    uuid4(),
                    policy_id,
                    shop.owner.staff_user_id,
                ),
            )
    assert _read(connection, order_id, shop.operator).commercial is CommercialOrderStatus.ACTIVE


# --- privacy -----------------------------------------------------------------------------------


def test_the_list_shows_the_phone_to_the_counter_and_the_last_four_to_the_auditor(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    publish_privacy_notice(connection, actor_id=shop.owner.staff_user_id, payload=notice_payload())
    phone = "09" + str(uuid4().int)[:8]
    customer_id = CustomerRepository().create(
        connection,
        store_id=shop.store_id,
        principal=shop.operator,
        phone=phone,
        display_name="chị Lan",
        delivery_address=None,
        note=None,
        kind=CustomerKind.RETAIL,
        service_consent=True,
        marketing_consent=False,
        at=datetime.now(UTC),
        correlation_id=uuid4(),
    )
    connection.commit()
    order_id = _ready(connection, shop, customer_id=customer_id)
    walk_in = _ready(connection, shop)
    _attempt(connection, order_id, shop.operator, outcome=ContactOutcome.PROMISED_TO_COME)

    counter = {item.order_id: item for item in _list(connection, shop).orders}
    assert counter[order_id].phone == phone and counter[order_id].customer_name == "chị Lan"
    assert counter[order_id].has_phone and not counter[walk_in].has_phone
    assert counter[walk_in].phone is None and counter[walk_in].ticket_number is not None
    audited = {item.order_id: item for item in _list(connection, shop, shop.auditor).orders}
    assert audited[order_id].phone is None
    assert audited[order_id].phone_last4 == phone[-4:]
    assert all(phone not in text for text in _payloads(connection, order_id))


def test_a_stranger_store_cannot_read_or_write(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id = _ready(connection, shop)
    other = Shop(connection)
    with pytest.raises(UnclaimedAuthorizationError):
        _list(connection, shop, other.operator)
    connection.rollback()
    with pytest.raises(UnclaimedAuthorizationError):
        _attempt(connection, order_id, other.operator)
    connection.rollback()
    with pytest.raises(OrderNotVisibleError):
        _storage(connection, order_id, other.operator)
    connection.rollback()
    assert _ready_at(connection, order_id) is not None
