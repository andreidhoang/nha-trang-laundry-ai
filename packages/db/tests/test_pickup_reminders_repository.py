"""`PICKUP-REMIND-001` (`DEC-043`, `0065`) against real PostgreSQL.

What only the database can prove: that the due list is `UNCLAIMED-001`'s waiting population with the
newest undone step, oldest ready first; that the text is given only after the egress guard allows
it -- refused before the owner publishes the messaging policy, for a customer who wrote STOP (on any
channel), and for a ticket with nobody to message; that an attempt naming a step is legal only for
the step due and `MESSAGE_SENT` only when the text would be given; that the column and the CHECKs
of `0065` hold; that reminder attempts count toward disposal; and that no phone number reaches the
text, an event, an audit or an outbox payload.

Harness step (documented, as `test_unclaimed_laundry`): the laundry's ready time is moved back with
one SQL statement (`_age`) -- nothing else can make an order fourteen days old inside a test.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from message_draft_test_data import publish_test_messaging_policy, record_customer_message
from nha_trang_laundry_db.customers import CustomerRepository
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pickup_reminders import PickupReminderRepository
from nha_trang_laundry_db.privacy_notice import publish_privacy_notice
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_db.unclaimed import (
    ContactAttemptCommand,
    ReminderRefused,
    UnclaimedAuthorizationError,
    UnclaimedRepository,
)
from nha_trang_laundry_domain.consent import OptOutDisposition
from nha_trang_laundry_domain.customers import CustomerKind
from nha_trang_laundry_domain.pickup_reminders import ReminderStep
from nha_trang_laundry_domain.unclaimed import (
    ContactChannel,
    ContactOutcome,
    withdrawal_document,
)
from test_customer_notice import notice_payload
from test_unclaimed_laundry import Shop, _age, _payloads, _publish, _ready, _rows, _storage


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Generator[Shop, None, None]:
    """A store with one of each role and no storage policy in force before or after the test."""

    made = Shop(connection)
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )
    yield made
    connection.rollback()
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )


def _customer(connection: Any, shop: Shop, name: str = "chị Lan") -> tuple[UUID, str]:
    publish_privacy_notice(connection, actor_id=shop.owner.staff_user_id, payload=notice_payload())
    phone = "09" + str(uuid4().int)[:8]
    customer_id = CustomerRepository().create(
        connection,
        store_id=shop.store_id,
        principal=shop.operator,
        phone=phone,
        display_name=name,
        delivery_address=None,
        note=None,
        kind=CustomerKind.RETAIL,
        service_consent=True,
        marketing_consent=False,
        at=datetime.now(UTC),
        correlation_id=uuid4(),
    )
    connection.commit()
    return customer_id, phone


def _due(connection: Any, shop: Shop, staff: Any = None) -> Any:
    with connection.cursor() as cursor:
        found = PickupReminderRepository.list_due(
            cursor,
            store_id=shop.store_id,
            principal=staff or shop.operator,
            as_of=datetime.now(UTC),
        )
    connection.rollback()
    return found


def _message(connection: Any, order_id: UUID, step: ReminderStep, staff: Any) -> Any:
    try:
        return PickupReminderRepository.read_message(
            connection, order_id=order_id, step=step, principal=staff, as_of=datetime.now(UTC)
        )
    finally:
        connection.rollback()


def _remind(
    connection: Any,
    order_id: UUID,
    staff: Any,
    step: ReminderStep | None,
    *,
    channel: ContactChannel = ContactChannel.ZALO,
    outcome: ContactOutcome = ContactOutcome.MESSAGE_SENT,
    key: str | None = None,
) -> Any:
    stored = UnclaimedRepository().record_contact_attempt(
        connection,
        ContactAttemptCommand(
            order_id=order_id,
            principal=staff,
            idempotency_key=key or f"remind-{uuid4().hex}",
            correlation_id=uuid4(),
            channel=channel,
            outcome=outcome,
            reminder_step=step,
        ),
    )
    connection.commit()
    return stored


def _bound_contact(connection: Any, order_id: UUID) -> UUID:
    value: UUID = _rows(connection, "SELECT bound_contact_id FROM orders WHERE id = %s", order_id)[
        0
    ][0]
    return value


def _attempts(connection: Any, order_id: UUID) -> int:
    count: int = _rows(
        connection, "SELECT count(*) FROM order_contact_attempts WHERE order_id = %s", order_id
    )[0][0]
    return count


# --- the due list ---------------------------------------------------------------------------------


def test_a_ready_ticket_is_due_counted_unreachable_and_leaves_once_reminded(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id = _ready(connection, shop)
    found = _due(connection, shop)
    [row] = [item for item in found.orders if item.order_id == order_id]
    assert (row.step, row.days_waiting, row.reachable.value) == (ReminderStep.READY, 0, "NONE")
    assert row.message_refusal == "NO_CONTACT" and row.zalo_url is None and row.phone is None
    assert (found.total_count, found.unreachable_count, found.truncated) == (1, 1, False)
    assert found.storage_policy_published is False and found.messaging_policy_published is False
    with pytest.raises(ReminderRefused, match="NO_CONTACT"):
        _message(connection, order_id, ReminderStep.READY, shop.operator)
    # A call about the reminder -- the shop has no number, the customer rang in -- does it.
    _remind(
        connection,
        order_id,
        shop.operator,
        ReminderStep.READY,
        channel=ContactChannel.CALL,
        outcome=ContactOutcome.REACHED,
    )
    assert _due(connection, shop).orders == ()


def test_aged_orders_show_the_newest_step_and_the_fee_day_only_with_the_policy(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    ages = {3: ReminderStep.DAY_3, 8: ReminderStep.DAY_7, 14: ReminderStep.DAY_14}
    orders = {}
    for days in (20, 14, 8, 3):
        orders[days] = _ready(connection, shop)
        _age(connection, orders[days], days)
    fresh = _ready(connection, shop)
    found = _due(connection, shop)
    # Oldest ready first; the fee day is not a step until the owner publishes the storage policy.
    assert [(item.order_id, item.step, item.days_waiting) for item in found.orders] == [
        (orders[20], ReminderStep.DAY_14, 20),
        *[(orders[days], ages[days], days) for days in (14, 8, 3)],
        (fresh, ReminderStep.READY, 0),
    ]
    _publish(connection, shop)
    found = {item.order_id: item.step for item in _due(connection, shop).orders}
    assert found[orders[20]] is ReminderStep.BEFORE_FEE
    _age(connection, orders[20], 1)
    # Day 21: the fee has started and Đồ chờ lấy takes over.
    assert orders[20] not in {item.order_id for item in _due(connection, shop).orders}


def test_an_undone_step_is_superseded_and_a_rewash_starts_the_schedule_over(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id = _ready(connection, shop)
    _remind(
        connection,
        order_id,
        shop.operator,
        ReminderStep.READY,
        channel=ContactChannel.CALL,
        outcome=ContactOutcome.NO_ANSWER,
    )
    assert _due(connection, shop).orders == ()
    _age(connection, order_id, 7)
    # DAY_3 was never done; on day 7 only DAY_7 shows.
    [row] = _due(connection, shop).orders
    assert row.step is ReminderStep.DAY_7
    with pytest.raises(ReminderRefused, match="REMINDER_STEP_NOT_DUE"):
        _remind(
            connection,
            order_id,
            shop.operator,
            ReminderStep.DAY_3,
            channel=ContactChannel.CALL,
            outcome=ContactOutcome.NO_ANSWER,
        )
    connection.rollback()
    # Attempts before the laundry was (last) ready do not count as done: the ready time moves
    # forward past them, as a rewash's MARK_READY does.
    _remind(
        connection,
        order_id,
        shop.operator,
        ReminderStep.DAY_7,
        channel=ContactChannel.CALL,
        outcome=ContactOutcome.NO_ANSWER,
    )
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE orders SET production_ready_at = now() + interval '1 second', "
            "row_version = row_version + 1 WHERE id = %s",
            (order_id,),
        )
    connection.commit()
    [again] = _due(connection, shop).orders
    assert again.step is ReminderStep.READY


def test_the_list_gives_the_number_and_zalo_link_to_the_counter_only(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    customer_id, phone = _customer(connection, shop)
    order_id = _ready(connection, shop, customer_id=customer_id)
    [row] = _due(connection, shop).orders
    assert row.order_id == order_id and row.reachable.value == "PHONE"
    assert row.customer_name == "chị Lan"
    assert row.phone == phone and row.zalo_url == f"https://zalo.me/{phone}"
    [audited] = _due(connection, shop, shop.auditor).orders
    assert audited.phone is None and audited.zalo_url is None
    assert audited.phone_last4 == phone[-4:]
    with pytest.raises(UnclaimedAuthorizationError):
        _message(connection, order_id, ReminderStep.READY, shop.auditor)
    stranger = Shop(connection)
    with pytest.raises(UnclaimedAuthorizationError):
        _due(connection, shop, stranger.operator)


def test_the_summary_counts_the_reminders_it_can_send_and_matches_the_list(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    """`SUMMARY-ATTENTION-001`: `count_due` is the list's population, minus the unreachable."""

    customer_id, _ = _customer(connection, shop)
    reachable = _ready(connection, shop, customer_id=customer_id)
    _ready(connection, shop)  # a ticket only: due, but nobody can be reminded
    listed = _due(connection, shop)
    with connection.cursor() as cursor:
        count, truncated = PickupReminderRepository.count_due(
            cursor, store_id=shop.store_id, principal=shop.owner, as_of=datetime.now(UTC)
        )
    connection.rollback()
    assert (
        (count, truncated) == (listed.total_count - listed.unreachable_count, False) == (1, False)
    )
    # A call is a reminder too, and needs no messaging policy.
    _remind(
        connection,
        reachable,
        shop.operator,
        ReminderStep.READY,
        channel=ContactChannel.CALL,
        outcome=ContactOutcome.REACHED,
    )
    with connection.cursor() as cursor:
        assert PickupReminderRepository.count_due(
            cursor, store_id=shop.store_id, principal=shop.owner, as_of=datetime.now(UTC)
        ) == (0, False)
        stranger = Shop(connection)
        with pytest.raises(UnclaimedAuthorizationError):
            PickupReminderRepository.count_due(
                cursor,
                store_id=shop.store_id,
                principal=stranger.operator,
                as_of=datetime.now(UTC),
            )
    connection.rollback()


def test_a_chat_order_is_reachable_by_chat(connection: psycopg.Connection[Any], shop: Shop) -> None:
    order_id = _ready(connection, shop)
    # Fixture: the order's reference is a chat binding, as an order that came in on a channel is.
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO contact_channel_bindings (
                provider, provider_user_ref, contact_binding_id, verification_state, row_version,
                created_at, updated_at
            ) VALUES ('ZALO_OA', %s, %s, 'UNVERIFIED', 1, now(), now())
            """,
            (f"user-{uuid4().hex}", _bound_contact(connection, order_id)),
        )
    connection.commit()
    [row] = _due(connection, shop).orders
    assert row.reachable.value == "CHAT" and row.zalo_url is None
    assert row.message_refusal == "MESSAGING_POLICY_UNPUBLISHED"
    assert _due(connection, shop).unreachable_count == 0


# --- the text behind the guard --------------------------------------------------------------------


def test_the_text_is_refused_until_the_owner_publishes_then_given_without_name_or_phone(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    customer_id, phone = _customer(connection, shop, name="anh Tuấn")
    order_id = _ready(connection, shop, customer_id=customer_id)
    [row] = _due(connection, shop).orders
    assert row.message_refusal == "MESSAGING_POLICY_UNPUBLISHED"
    with pytest.raises(ReminderRefused, match="MESSAGING_POLICY_UNPUBLISHED"):
        _message(connection, order_id, ReminderStep.READY, shop.operator)
    with pytest.raises(ReminderRefused, match="MESSAGING_POLICY_UNPUBLISHED"):
        _remind(connection, order_id, shop.operator, ReminderStep.READY)
    connection.rollback()
    assert _attempts(connection, order_id) == 0

    publish_test_messaging_policy(connection)
    connection.commit()
    assert _due(connection, shop).orders[0].message_refusal is None
    message = _message(connection, order_id, ReminderStep.READY, shop.operator)
    ticket = _rows(
        connection,
        "SELECT t.ticket_number FROM orders o JOIN counter_tickets t ON t.id = o.bound_contact_id "
        "WHERE o.id = %s",
        order_id,
    )[0][0]
    assert message.template == "pickup-reminder-v2" and message.basis == "OPEN_ORDER"
    assert message.text.startswith("Cửa hàng thử nghiệm xin báo: đồ giặt phiếu số " + str(ticket))
    assert "Số tiền còn lại:" in message.text
    assert phone not in message.text and "Tuấn" not in message.text
    with pytest.raises(ReminderRefused, match="REMINDER_STEP_NOT_DUE"):
        _message(connection, order_id, ReminderStep.DAY_3, shop.operator)


def test_before_fee_quotes_the_published_figures(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    customer_id, _ = _customer(connection, shop)
    order_id = _ready(connection, shop, customer_id=customer_id)
    _age(connection, order_id, 20)
    _publish(connection, shop)
    publish_test_messaging_policy(connection)
    connection.commit()
    message = _message(connection, order_id, ReminderStep.BEFORE_FEE, shop.operator)
    ready_on = (
        _rows(connection, "SELECT production_ready_at FROM orders WHERE id = %s", order_id)[0][0]
        + timedelta(hours=7)
    ).date()
    starts = ready_on + timedelta(days=21)
    assert (
        f"Từ ngày {starts.day:02d}/{starts.month:02d}/{starts.year} tiệm tính phí lưu kho "
        "5.000 ₫/ngày (tối đa 50% tiền giặt)."
    ) in message.text


def test_a_stop_on_any_channel_refuses_the_text_and_the_message(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    customer_id, _ = _customer(connection, shop)
    order_id = _ready(connection, shop, customer_id=customer_id)
    publish_test_messaging_policy(connection)
    connection.commit()
    record_customer_message(
        connection,
        _bound_contact(connection, order_id),
        received_at=datetime.now(UTC) - timedelta(minutes=1),
        channel="SOME_OTHER_CHANNEL",
        disposition=OptOutDisposition.WITHDRAW,
    )
    connection.commit()
    [row] = _due(connection, shop).orders
    assert row.message_refusal == "SUPPRESSED"
    with pytest.raises(ReminderRefused, match="SUPPRESSED"):
        _message(connection, order_id, ReminderStep.READY, shop.operator)
    with pytest.raises(ReminderRefused, match="SUPPRESSED"):
        _remind(connection, order_id, shop.operator, ReminderStep.READY, channel=ContactChannel.SMS)
    connection.rollback()
    assert _attempts(connection, order_id) == 0
    # A call stays available.
    _remind(
        connection,
        order_id,
        shop.operator,
        ReminderStep.READY,
        channel=ContactChannel.CALL,
        outcome=ContactOutcome.REACHED,
    )
    assert _attempts(connection, order_id) == 1


# --- recording ------------------------------------------------------------------------------------


def test_message_sent_is_recorded_with_its_step_and_the_guards_reason(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    customer_id, phone = _customer(connection, shop)
    order_id = _ready(connection, shop, customer_id=customer_id)
    publish_test_messaging_policy(connection)
    connection.commit()
    with pytest.raises(ReminderRefused, match="REMINDER_STEP_REQUIRED"):
        _remind(connection, order_id, shop.operator, None)
    connection.rollback()
    with pytest.raises(ReminderRefused, match="MESSAGE_SENT_CHANNEL_INVALID"):
        _remind(
            connection, order_id, shop.operator, ReminderStep.READY, channel=ContactChannel.CALL
        )
    connection.rollback()
    key = f"remind-{uuid4().hex}"
    first = _remind(connection, order_id, shop.operator, ReminderStep.READY, key=key)
    again = _remind(connection, order_id, shop.operator, ReminderStep.READY, key=key)
    assert again.replayed and again.attempt_id == first.attempt_id
    assert first.reminder_step == "READY" and first.outcome == "MESSAGE_SENT"
    with pytest.raises(IdempotencyConflictError):
        _remind(
            connection,
            order_id,
            shop.operator,
            ReminderStep.READY,
            key=key,
            channel=ContactChannel.SMS,
        )
    connection.rollback()
    assert _rows(
        connection,
        "SELECT channel, outcome, reminder_step FROM order_contact_attempts WHERE order_id = %s",
        order_id,
    ) == [("ZALO", "MESSAGE_SENT", "READY")]
    [(event,)] = _rows(
        connection,
        "SELECT payload FROM domain_events WHERE aggregate_id = %s "
        "AND event_type = 'ORDER_CONTACT_ATTEMPT_RECORDED'",
        order_id,
    )
    assert event["reminder_step"] == "READY"
    [(audit,)] = _rows(
        connection,
        "SELECT details FROM audit_events WHERE aggregate_id = %s "
        "AND action = 'ORDER_CONTACT_ATTEMPT_RECORD'",
        order_id,
    )
    assert audit["egress"]["basis"] == "OPEN_ORDER" and audit["egress"]["refusal"] is None
    assert all(phone not in text for text in _payloads(connection, order_id))
    read = _storage(connection, order_id, shop.operator)
    assert [(a.outcome, a.reminder_step) for a in read.attempts] == [("MESSAGE_SENT", "READY")]
    assert _due(connection, shop).orders == ()


def test_reminder_attempts_count_toward_disposal(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id = _ready(connection, shop)
    _remind(
        connection,
        order_id,
        shop.operator,
        ReminderStep.READY,
        channel=ContactChannel.CALL,
        outcome=ContactOutcome.NO_ANSWER,
    )
    UnclaimedRepository().record_contact_attempt(
        connection,
        ContactAttemptCommand(
            order_id=order_id,
            principal=shop.operator,
            idempotency_key=f"plain-{uuid4().hex}",
            correlation_id=uuid4(),
            channel=ContactChannel.CALL,
            outcome=ContactOutcome.NO_ANSWER,
        ),
    )
    connection.commit()
    _publish(connection, shop)
    verdict = _storage(connection, order_id, shop.owner).disposal_verdict
    assert verdict.attempts_counted == 2


def test_0065_holds_the_shape_in_the_database(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id = _ready(connection, shop)
    staff = shop.operator.staff_user_id
    for channel, outcome, step in (
        ("ZALO", "MESSAGE_SENT", None),
        ("CALL", "MESSAGE_SENT", "READY"),
        ("ZALO", "NO_ANSWER", "DAY_5"),
        ("ZALO", "SEEN", None),
    ):
        with (
            pytest.raises(psycopg.errors.CheckViolation),
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                INSERT INTO order_contact_attempts (
                    id, order_id, store_id, channel, outcome, note, attempted_by_staff_id,
                    attempted_at, created_at, reminder_step
                ) VALUES (%s, %s, %s, %s, %s, NULL, %s, now(), now(), %s)
                """,
                (uuid4(), order_id, shop.store_id, channel, outcome, staff, step),
            )
        connection.rollback()
    # A row written as before `0065` -- no step -- is still a row.
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO order_contact_attempts (
                id, order_id, store_id, channel, outcome, note, attempted_by_staff_id,
                attempted_at, created_at
            ) VALUES (%s, %s, %s, 'CALL', 'REACHED', NULL, %s, now(), now())
            """,
            (uuid4(), order_id, shop.store_id, staff),
        )
    connection.commit()
    assert _rows(
        connection, "SELECT reminder_step FROM order_contact_attempts WHERE order_id = %s", order_id
    ) == [(None,)]


def test_the_due_list_order_is_written_as_the_server_orders_it() -> None:
    """Round-9b J residual (verifier, P2): since `DEC-050` the due list is ordered by
    `WAITING_ORDER_SQL` -- the most counted days first, held days not counted -- so an order that
    became ready earlier but was held is listed below a later-ready one with more counted days. The
    workflow doc, the screen's header comment and this module's docstring still said "oldest ready
    first"."""

    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    places = {
        "docs/CORE_BUSINESS_WORKFLOWS_V1.md": "most counted days first",
        "apps/web/src/screens/reminders.js": "most counted days first",
        "packages/db/src/nha_trang_laundry_db/pickup_reminders.py": "most counted days first",
        "docs/REMAINING_GAPS_SPEC_V1.md": "most counted days first",
    }
    for relative, phrase in places.items():
        text = " ".join((root / relative).read_text(encoding="utf-8").split())
        assert "oldest ready first" not in text, relative
        assert phrase in text and "DEC-050" in text, relative
