"""`VIETQR-001` (`DEC-041`) against real PostgreSQL: the published account and the QR reads.

What only the database can prove: that no QR exists until the owner publishes the account and none
after the withdrawal; that only the owner publishes, and the same account again changes nothing;
that an order's QR asks for exactly the payment ledger's remaining balance (after a part payment,
with an accrued storage fee) and nothing once paid; that the order search finds an order by its
transfer code within its own store only; and that an account month's QR asks for what of that
statement no payment has covered.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from nha_trang_laundry_db.bank_transfer import (
    ACCOUNT_MONTH_UNPAID_QUERY,
    BankAccountAuthorizationError,
    BankTransferRepository,
    TransferQr,
    publish_bank_account,
    read_published_bank_account,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderNotVisibleError, OrderRepository
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import PaymentMethod
from nha_trang_laundry_domain.unclaimed import withdrawal_document
from nha_trang_laundry_domain.vietqr import (
    OrderIdTransferCode,
    QrRefusal,
    TicketTransferCode,
    bank_account_document,
    bank_account_withdrawal_document,
    order_transfer_code,
    parse_payload,
    parse_transfer_code,
)
from quote_test_data import accepted_quote
from test_customer_accounts import Shop as AccountShop
from test_order_step_repository import TOTAL_VND, _read, _step
from test_unclaimed_laundry import _staff, policy_payload

HCM = ZoneInfo("Asia/Ho_Chi_Minh")
BIN, ACCOUNT = "970416", "257678859"


def _account(**changes: str) -> dict[str, str]:
    fields = {
        "bank_bin": BIN,
        "account_number": ACCOUNT,
        "account_name": "TIEM GIAT NHA TRANG",
        "bank_display_name": "ACB",
        "test_transfer_confirmed_at": "2026-09-28T09:15:00+07:00",
    }
    fields.update(changes)
    return bank_account_document(**fields)


@pytest.fixture
def connection() -> Iterator[psycopg.Connection[Any]]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url, autocommit=True) as established:
        apply_migrations(established)
        yield established


class Shop:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.store_id = uuid4()
        self.owner = _staff(connection, self.store_id, StaffRole.OWNER_ADMIN)
        self.operator = _staff(connection, self.store_id, StaffRole.OPERATOR)

    def withdraw(self) -> None:
        publish_bank_account(
            self.connection,
            actor_id=self.owner.staff_user_id,
            payload=bank_account_withdrawal_document(),
        )

    def publish(self, **changes: str) -> tuple[str, bool]:
        return publish_bank_account(
            self.connection, actor_id=self.owner.staff_user_id, payload=_account(**changes)
        )

    def order(self, *, ready: bool = False) -> UUID:
        quote_id, revision, quote, contact_id = accepted_quote(
            self.connection, store_id=self.store_id, principal=self.operator
        )
        order_id = (
            OrderRepository()
            .create(
                self.connection,
                CreateOrderCommand(
                    self.store_id,
                    contact_id,
                    quote_id,
                    revision,
                    quote.document.snapshot_hash,
                    FulfillmentMode.SELF_DROP_SELF_COLLECT,
                    self.operator,
                    f"order-{uuid4().hex}",
                    uuid4(),
                    datetime.now(UTC),
                    AcquisitionSource.WALK_IN,
                ),
            )
            .order_id
        )
        view = _step(
            self.connection, order_id, self.operator, 1, OrderStep.RECEIVE, slot_approved=True
        ).view
        if ready:
            for step in (OrderStep.START_WASH, OrderStep.QUALITY_CHECK, OrderStep.MARK_READY):
                view = _step(self.connection, order_id, self.operator, view.row_version, step).view
        return order_id

    def pay(self, order_id: UUID, amount: int) -> None:
        PaymentRepository().record(
            self.connection,
            PaymentCommand(
                order_id=order_id,
                expected_row_version=_read(self.connection, order_id, self.operator).row_version,
                amount_vnd=amount,
                method=PaymentMethod.CHUYEN_KHOAN,
                transfer_seen=True,
                bank_ref_last=None,
                collected_by_customer=False,
                principal=self.operator,
                correlation_id=uuid4(),
                recorded_at=None,
            ),
        )

    def qr(
        self, order_id: UUID, principal: StaffPrincipal | None = None, *, part: int | None = None
    ) -> TransferQr:
        with self.connection.cursor() as cursor:
            return BankTransferRepository.order_qr(
                cursor,
                order_id=order_id,
                principal=principal or self.operator,
                now=datetime.now(UTC),
                **({} if part is None else {"part_vnd": part}),
            )

    def search(self, code: str, principal: StaffPrincipal | None = None) -> list[UUID]:
        transfer = parse_transfer_code(code)
        assert isinstance(transfer, TicketTransferCode | OrderIdTransferCode)
        with self.connection.cursor() as cursor:
            found = OrderRepository.list_for_store(
                cursor,
                store_id=self.store_id,
                principal=principal or self.operator,
                limit=20,
                transfer=transfer,
            )
        return [item.order_id for item in found]


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Iterator[Shop]:
    """A store starting -- and ending -- with no bank account in force: the account is one per
    deployment, so a test that left it published would hand every later test a QR."""

    made = Shop(connection)
    made.withdraw()
    yield made
    made.withdraw()


def test_no_qr_until_the_owner_publishes_the_account(shop: Shop) -> None:
    order_id = shop.order()
    view = _read(shop.connection, order_id, shop.operator)
    assert read_published_bank_account(shop.connection.cursor()) is None

    qr = shop.qr(order_id)

    assert qr.refusal is QrRefusal.BANK_ACCOUNT_UNPUBLISHED
    assert (qr.payload, qr.amount_vnd, qr.account) == (None, None, None)
    # The code is the ticket's day and number, whatever the refusal.
    assert view.ticket_number is not None and view.ticket_issued_on is not None
    assert qr.transfer_code == (
        f"NTL{view.ticket_issued_on:%d%m}{view.ticket_number:03d}"
        if view.ticket_number <= 999
        else f"NTL{order_id.hex[:8].upper()}"
    )


def test_the_qr_asks_for_the_ledgers_remaining_balance_and_nothing_once_paid(shop: Shop) -> None:
    order_id = shop.order()
    digest, created = shop.publish()
    assert created is True

    first = shop.qr(order_id)
    assert first.refusal is None and first.amount_vnd == TOTAL_VND
    assert first.account is not None and first.account.snapshot_hash == digest
    assert first.payload is not None
    parsed = parse_payload(first.payload)
    assert (parsed.bank_bin, parsed.account_number, parsed.amount_vnd, parsed.purpose) == (
        BIN,
        ACCOUNT,
        TOTAL_VND,
        first.transfer_code,
    )

    shop.pay(order_id, 50_000)
    after_deposit = shop.qr(order_id)
    remaining = _read(shop.connection, order_id, shop.operator).remaining_vnd
    assert after_deposit.amount_vnd == remaining == TOTAL_VND - 50_000
    assert after_deposit.payload is not None
    assert parse_payload(after_deposit.payload).amount_vnd == TOTAL_VND - 50_000

    shop.pay(order_id, TOTAL_VND - 50_000)
    paid = shop.qr(order_id)
    assert paid.refusal is QrRefusal.NOTHING_OWED
    assert paid.payload is None and paid.transfer_code == first.transfer_code


def test_a_typed_part_is_asked_for_exactly_against_the_ledger_read_now(shop: Shop) -> None:
    """COUNTER-UI-RACE-009 (C3): a deposit typed at the counter is the QR's amount, checked against
    what the ledger says remains in this read -- before and after a part payment -- and refused by
    name above it; the order's own refusals still come first."""

    order_id = shop.order()
    shop.publish()

    deposit = shop.qr(order_id, part=50_000)
    assert deposit.refusal is None and deposit.amount_vnd == 50_000
    assert deposit.payload is not None
    assert parse_payload(deposit.payload).amount_vnd == 50_000
    assert deposit.transfer_code == shop.qr(order_id).transfer_code
    whole = shop.qr(order_id, part=TOTAL_VND)
    assert whole.refusal is None and whole.amount_vnd == TOTAL_VND
    above = shop.qr(order_id, part=TOTAL_VND + 1)
    assert above.refusal is QrRefusal.AMOUNT_ABOVE_REMAINING
    assert (above.payload, above.amount_vnd, above.account) == (None, None, None)

    shop.pay(order_id, 50_000)
    remaining = _read(shop.connection, order_id, shop.operator).remaining_vnd
    assert remaining == TOTAL_VND - 50_000
    assert shop.qr(order_id, part=remaining).amount_vnd == remaining
    assert shop.qr(order_id, part=remaining + 1).refusal is QrRefusal.AMOUNT_ABOVE_REMAINING
    assert shop.qr(order_id, part=20_000).amount_vnd == 20_000

    shop.pay(order_id, remaining)
    assert shop.qr(order_id, part=20_000).refusal is QrRefusal.NOTHING_OWED


def test_a_typed_part_above_what_remains_is_refused_before_the_account_is_asked(
    shop: Shop,
) -> None:
    order_id = shop.order()
    assert shop.qr(order_id, part=TOTAL_VND + 1).refusal is QrRefusal.AMOUNT_ABOVE_REMAINING
    assert shop.qr(order_id, part=50_000).refusal is QrRefusal.BANK_ACCOUNT_UNPUBLISHED


def test_the_qr_includes_an_accrued_storage_fee(shop: Shop) -> None:
    order_id = shop.order(ready=True)
    shop.publish()
    publish_storage_policy(
        shop.connection, actor_id=shop.owner.staff_user_id, payload=policy_payload()
    )
    try:
        # The documented harness step: the laundry was reported ready 25 shop days ago.
        shop.connection.execute(
            """
            UPDATE orders
            SET production_ready_at = production_ready_at - interval '25 days',
                production_accepted_at = production_accepted_at - interval '25 days',
                row_version = row_version + 1
            WHERE id = %s
            """,
            (order_id,),
        )
        view = _read(shop.connection, order_id, shop.operator)
        fee = sum(item.amount_vnd for item in view.charges if item.kind.value == "STORAGE_FEE")
        assert fee > 0
        qr = shop.qr(order_id)
        assert qr.amount_vnd == view.remaining_vnd == TOTAL_VND + fee
    finally:
        publish_storage_policy(
            shop.connection, actor_id=shop.owner.staff_user_id, payload=withdrawal_document()
        )


def test_only_the_owner_publishes_and_the_same_account_again_changes_nothing(shop: Shop) -> None:
    with pytest.raises(BankAccountAuthorizationError):
        publish_bank_account(
            shop.connection, actor_id=shop.operator.staff_user_id, payload=_account()
        )
    assert read_published_bank_account(shop.connection.cursor()) is None

    digest, created = shop.publish()
    assert created is True
    again, created_again = shop.publish(test_transfer_confirmed_at="2026-10-01T08:00:00+07:00")
    assert (again, created_again) == (digest, False)
    published = read_published_bank_account(shop.connection.cursor())
    assert published is not None and published.account.account_name == "TIEM GIAT NHA TRANG"

    moved, created_moved = shop.publish(account_number="0123456789")
    assert created_moved is True and moved != digest

    shop.withdraw()
    assert read_published_bank_account(shop.connection.cursor()) is None
    _digest, republished = shop.publish()
    assert republished is True


def test_an_order_qr_is_the_order_reads_to_give(shop: Shop, connection: Any) -> None:
    order_id = shop.order()
    shop.publish()
    stranger = _staff(connection, uuid4(), StaffRole.OPERATOR)
    with pytest.raises(OrderNotVisibleError):
        shop.qr(order_id, stranger)
    with pytest.raises(OrderNotVisibleError):
        shop.qr(uuid4())


def test_the_order_search_finds_an_order_by_its_transfer_code_in_its_own_store(
    shop: Shop, connection: Any
) -> None:
    order_id = shop.order()
    view = _read(connection, order_id, shop.operator)
    code = order_transfer_code(
        order_id=order_id,
        ticket_number=view.ticket_number,
        ticket_issued_on=view.ticket_issued_on,
    )
    assert order_id in shop.search(code)
    assert order_id in shop.search(code.lower())
    assert shop.search(f"NTL{order_id.hex[:8]}") == [order_id]

    other = Shop(connection)
    other_order = other.order()
    other_view = _read(connection, other_order, other.operator)
    other_code = order_transfer_code(
        order_id=other_order,
        ticket_number=other_view.ticket_number,
        ticket_issued_on=other_view.ticket_issued_on,
    )
    assert other_order not in shop.search(other_code)
    assert shop.search(f"NTL{other_order.hex[:8]}") == []
    assert other.search(f"NTL{other_order.hex[:8]}") == [other_order]


def _local(day: date, hour: int = 10) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=HCM)


def test_an_account_month_qr_asks_for_what_of_that_statement_is_unpaid(connection: Any) -> None:
    accounts = AccountShop(connection)
    owner = accounts.owner
    publish_bank_account(
        connection, actor_id=owner.staff_user_id, payload=bank_account_withdrawal_document()
    )
    hotel = accounts.customer()
    accounts.open(hotel, 1_000_000)
    august = [accounts.ready_order(hotel) for _ in range(2)]
    accounts.charge(august[0], at=_local(date(2026, 8, 3)))
    accounts.charge(august[1], at=_local(date(2026, 8, 20)))
    september = accounts.ready_order(hotel)
    accounts.charge(september, at=_local(date(2026, 9, 2)))
    accounts.pay(hotel, 150_000, at=_local(date(2026, 9, 10)))
    now = _local(date(2026, 9, 12))

    def month_qr(month: date) -> Any:
        with connection.cursor() as cursor:
            return BankTransferRepository.account_month_qr(
                cursor,
                store_id=accounts.store_id,
                customer_id=hotel,
                month=month,
                principal=accounts.counter,
                now=now,
            )

    try:
        unpublished = month_qr(date(2026, 8, 1))
        assert unpublished.qr.refusal is QrRefusal.BANK_ACCOUNT_UNPUBLISHED
        publish_bank_account(connection, actor_id=owner.staff_user_id, payload=_account())

        aug = month_qr(date(2026, 8, 1))
        account_id = accounts.account(hotel).account.account_id
        assert aug.account_id == account_id
        assert aug.qr.transfer_code == f"NTLCN{account_id.hex[:6].upper()}0826"
        assert aug.qr.amount_vnd == 2 * TOTAL_VND - 150_000
        assert aug.unpaid_before_month_vnd == 0
        assert aug.qr.payload is not None
        assert parse_payload(aug.qr.payload).purpose == aug.qr.transfer_code

        sep = month_qr(date(2026, 9, 1))
        assert sep.qr.amount_vnd == 3 * TOTAL_VND - 150_000
        assert sep.unpaid_before_month_vnd == 2 * TOTAL_VND - 150_000
        assert sep.query_version == ACCOUNT_MONTH_UNPAID_QUERY.label
        # Pinned: editing the rule fails here until the identifier is changed with it.
        assert ACCOUNT_MONTH_UNPAID_QUERY.label == "account-month-unpaid-v1:307f10a0804b7a1e"

        assert month_qr(date(2026, 10, 1)).qr.refusal is QrRefusal.MONTH_NOT_STARTED
        assert month_qr(date(2026, 7, 1)).qr.refusal is QrRefusal.NOTHING_OWED

        accounts.pay(hotel, 2 * TOTAL_VND - 150_000, at=_local(date(2026, 9, 11)))
        assert month_qr(date(2026, 8, 1)).qr.refusal is QrRefusal.NOTHING_OWED
        assert month_qr(date(2026, 9, 1)).qr.amount_vnd == TOTAL_VND
    finally:
        publish_bank_account(
            connection, actor_id=owner.staff_user_id, payload=bank_account_withdrawal_document()
        )
