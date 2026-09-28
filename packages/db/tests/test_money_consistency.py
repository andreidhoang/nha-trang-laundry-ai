"""Round 7 wave 2 integration: the money rules of `PAYMENT-002`, `UNCLAIMED-001` and `0061`.

Three slices changed what an order owes and how it may close, each on its own branch:

* `PAYMENT-002` (`0059`) gave `ON_ACCOUNT` its meaning in `0056`'s ledger rule
  (`enforce_order_payment_ledger`): an order on account has its charge, no settlement, and money
  still owed; the account pays it through allocations that are ordinary ledger rows;
* `UNCLAIMED-001` (`0060`) widened `0056`'s refund rule (`enforce_order_refund_consistency`) so a
  paid or partly paid order closes without a refund only beside its disposal, and bound a storage
  fee to the settlement that fixed it;
* `0061` (this integration) admits the order that is both: a business customer's laundry left past
  the free days and then taken away on the account -- the fee is fixed by the account charge.

What only the database can prove, each shape committed through the repositories and each
disagreeing write refused by the database itself:

* the final trigger functions carry every slice's rule (the migration that ran last replaced a
  function without dropping another slice's clause);
* an account order paid through allocation commits; paying it around the account, marking it paid
  without a settlement, or charging it a sum that is not its total are refused;
* a disposed partly paid order commits keeping what was paid; a partly paid order closing without a
  refund or a disposal, and a disposal keeping anything but the ledger's sum, are refused;
* a fee-bearing settled order commits with its fee bound to its settlement; a fee its settlement
  does not include, and a fee bound to two things, are refused;
* an account order that waited: the limit is measured against the quoted total plus the fee, the
  charge carries the fee (fixed then, so it stops accruing), the order is not on the waiting list,
  the fee cannot be waived afterwards, and the account's payment settles the total plus the fee.

Harness step (documented, as `test_unclaimed_laundry._age`): the laundry's accepted and ready times
are moved back with one SQL statement -- nothing else can make an order twenty-five days old here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.accounts import AccountRepository
from nha_trang_laundry_db.migrations import apply_migrations, discover_migrations
from nha_trang_laundry_db.payments import PaymentCommand, PaymentRepository, StoredPayment
from nha_trang_laundry_db.settlement import (
    SettlementCommand,
    SettlementRepository,
    SettlementStateError,
)
from nha_trang_laundry_db.storage_fees import publish_storage_policy, storage_fee_for_order
from nha_trang_laundry_db.unclaimed import (
    ContactAttemptCommand,
    DisposalCommand,
    UnclaimedRefused,
    UnclaimedRepository,
    WaiverCommand,
)
from nha_trang_laundry_domain.accounts import AccountRefusal, AccountRuleError
from nha_trang_laundry_domain.catalog import CommercialOrderStatus, OrderBalanceStatus
from nha_trang_laundry_domain.customers import CustomerKind
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import ChargeKind, PaymentMethod
from nha_trang_laundry_domain.unclaimed import ContactChannel, ContactOutcome, withdrawal_document
from test_customer_accounts import Shop
from test_order_step_repository import TOTAL_VND, _read, _step

ROOT = Path(__file__).resolve().parents[3]
POLICY = json.loads((ROOT / "templates/storage-policy-dec-036.json").read_text(encoding="utf-8"))
VN = timedelta(hours=7)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Iterator[Shop]:
    """A store with its owner and counter, the notice and the account terms published, and the
    storage policy published for the test -- and withdrawn after it, because a configuration version
    is one per database and a policy left in force would charge fees in every later test."""

    made = Shop(connection)
    publish_storage_policy(connection, actor_id=made.owner.staff_user_id, payload=POLICY)
    try:
        yield made
    finally:
        publish_storage_policy(
            connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
        )


# --- helpers ----------------------------------------------------------------------------------


def _rows(connection: Any, query: str, *params: object) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return [tuple(row) for row in cursor.fetchall()]


def _age(connection: Any, order_id: UUID, days: int) -> None:
    """The documented harness step: accepted and ready `days` days earlier, both stamps together."""

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


def _counter_pay(
    connection: Any, shop: Shop, order_id: UUID, amount: int, *, collected: bool = False
) -> StoredPayment:
    return PaymentRepository().record(
        connection,
        PaymentCommand(
            order_id=order_id,
            expected_row_version=_read(connection, order_id, shop.counter).row_version,
            amount_vnd=amount,
            method=PaymentMethod.TIEN_MAT,
            transfer_seen=False,
            bank_ref_last=None,
            collected_by_customer=collected,
            principal=shop.counter,
            correlation_id=uuid4(),
        ),
    )


def _shop_day(days_ago: int, hour: int) -> datetime:
    today = (datetime.now(UTC) + VN).date()
    local = datetime(today.year, today.month, today.day, hour) - timedelta(days=days_ago)
    return (local - VN).replace(tzinfo=UTC)


def _attempt(connection: Any, shop: Shop, order_id: UUID, at: datetime) -> None:
    UnclaimedRepository().record_contact_attempt(
        connection,
        ContactAttemptCommand(
            order_id=order_id,
            principal=shop.counter,
            idempotency_key=f"attempt-{uuid4().hex}",
            correlation_id=uuid4(),
            channel=ContactChannel.CALL,
            outcome=ContactOutcome.NO_ANSWER,
            attempted_at=at,
        ),
    )


def _policy_id(connection: Any) -> UUID:
    [(found,)] = _rows(
        connection,
        "SELECT id FROM configuration_versions WHERE config_type = 'STORAGE_POLICY' "
        "ORDER BY version DESC LIMIT 1",
    )
    return UUID(str(found))


def _refused(connection: Any, match: str, statements: list[tuple[str, tuple[Any, ...]]]) -> None:
    """Every statement in one transaction, refused by the database at a statement or at commit."""

    with (
        pytest.raises(psycopg.errors.RaiseException, match=match),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        for query, params in statements:
            cursor.execute(query, params)


_INSERT_FEE = """
    INSERT INTO order_storage_fees (
        id, order_id, store_id, settlement_id, account_charge_id, amount_vnd, days_waiting,
        chargeable_days, policy_version_id, fixed_by_staff_id, fixed_at
    ) VALUES (%s, %s, %s, %s, %s, %s, 25, 5, %s, %s, now())
"""


# --- the final functions -----------------------------------------------------------------------


def test_the_final_functions_carry_every_slices_rule(connection: psycopg.Connection[Any]) -> None:
    """`0059` and `0060` replaced different `0056` functions; `0061` replaced three more. Whatever
    ran last defines each, so each must still say what every slice needs it to say."""

    migrations = discover_migrations()
    versions = [m.version for m in migrations]
    assert versions[versions.index("0059") : versions.index("0061") + 1] == ["0059", "0060", "0061"]
    # "Whatever ran last" is still 0061 while nothing after it replaces a function.
    later = migrations[versions.index("0061") + 1 :]
    assert not [m.version for m in later if "FUNCTION" in m.path.read_text(encoding="utf-8")]

    def body(name: str) -> str:
        [(text,)] = _rows(
            connection,
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE p.proname = %s AND n.nspname = current_schema()",
            name,
        )
        return str(text)

    ledger = body("enforce_order_payment_ledger")
    # `0056`'s four balances, and `0059`'s ON_ACCOUNT.
    for clause in ("'UNPAID'", "'PARTIALLY_PAID'", "'PAID'", "'REFUNDED'", "'ON_ACCOUNT'"):
        assert clause in ledger, clause
    assert "customer_account_charges" in ledger
    refund = body("enforce_order_refund_consistency")
    # `0056`'s widening to part payments, and `0060`'s disposal exception.
    assert "'PARTIALLY_PAID'" in refund and "order_disposals" in refund
    fee = body("enforce_storage_fee_settlement")
    assert "order_settlements" in fee and "customer_account_charges" in fee
    charge = body("enforce_account_charge")
    assert "only an active account with a limit" in charge
    assert "order_storage_fees" in body("enforce_account_charge_owes_total_and_fee")
    assert "order_storage_fees" in body("enforce_waiver_before_settlement")


# --- an account order, paid through allocation ---------------------------------------------------


def test_an_account_order_paid_through_allocation_commits_and_money_around_it_is_refused(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    hotel = shop.customer()
    shop.open(hotel, 1_000_000)
    paid_off = shop.ready_order(hotel)
    shop.charge(paid_off)
    view = _read(connection, paid_off, shop.counter)
    _step(connection, paid_off, shop.counter, view.row_version, OrderStep.HAND_OVER)
    assert _read(connection, paid_off, shop.counter).balance is OrderBalanceStatus.ON_ACCOUNT
    shop.pay(hotel, TOTAL_VND)

    view = _read(connection, paid_off, shop.counter)
    assert (view.commercial, view.balance) == (
        CommercialOrderStatus.COMPLETED,
        OrderBalanceStatus.PAID,
    )
    assert (view.owed_vnd, view.paid_vnd, view.remaining_vnd) == (TOTAL_VND, TOTAL_VND, 0)
    assert _rows(
        connection,
        "SELECT settlement_shape, expected_total_vnd, paid_amount_vnd FROM order_settlements "
        "WHERE order_id = %s",
        paid_off,
    ) == [("EXACT_PAYMENT_ON_ACCOUNT", TOTAL_VND, TOTAL_VND)]

    still_owed = shop.ready_order(hotel)
    shop.charge(still_owed)
    loose = shop.ready_order(hotel)
    # Marked paid with no settlement behind it.
    _refused(
        connection,
        "leaves it only when the account pays it in full",
        [
            (
                "UPDATE orders SET balance_status = 'PAID', row_version = row_version + 1 "
                "WHERE id = %s",
                (still_owed,),
            )
        ],
    )
    # Paid at the counter, around the account.
    _refused(
        connection,
        "paid through its account",
        [
            (
                """
                INSERT INTO order_payments (id, order_id, store_id, amount_vnd, method, legacy,
                    recorded_by_staff_id, recorded_at, created_at)
                VALUES (%s, %s, %s, 1000, 'TIEN_MAT', FALSE, %s, now(), now())
                """,
                (uuid4(), still_owed, shop.store_id, shop.counter.staff_user_id),
            )
        ],
    )
    # Charged a sum that is not its total (and no fee was fixed with the charge).
    [(account_id,)] = _rows(
        connection, "SELECT id FROM customer_accounts WHERE customer_id = %s", hotel
    )
    _refused(
        connection,
        "owes the quoted total plus the storage fee",
        [
            (
                """
                INSERT INTO customer_account_charges (
                    id, account_id, customer_id, store_id, order_id, owed_vnd, paid_before_vnd,
                    amount_vnd, collected_by_customer, charged_by_staff_id, charged_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 0, %s, TRUE, %s, now())
                """,
                (
                    uuid4(),
                    account_id,
                    hotel,
                    shop.store_id,
                    loose,
                    TOTAL_VND + 5_000,
                    TOTAL_VND + 5_000,
                    shop.counter.staff_user_id,
                ),
            ),
            (
                "UPDATE orders SET balance_status = 'ON_ACCOUNT', row_version = row_version + 1 "
                "WHERE id = %s",
                (loose,),
            ),
        ],
    )
    assert _read(connection, loose, shop.counter).balance is OrderBalanceStatus.UNPAID


# --- a disposed partly paid order ----------------------------------------------------------------


def test_a_disposed_partly_paid_order_commits_and_a_close_without_it_is_refused(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    retail = shop.customer(CustomerKind.RETAIL)
    order_id = shop.ready_order(retail)
    _counter_pay(connection, shop, order_id, 20_000)
    _age(connection, order_id, 65)
    for days_ago in (2, 1, 0):
        _attempt(connection, shop, order_id, _shop_day(days_ago, 10))
    owed = _read(connection, order_id, shop.counter).owed_vnd
    assert owed > TOTAL_VND  # 65 days: the fee, capped, is part of what is owed

    UnclaimedRepository().dispose(
        connection,
        DisposalCommand(
            order_id=order_id,
            expected_row_version=_read(connection, order_id, shop.counter).row_version,
            principal=shop.owner,
            idempotency_key=f"dispose-{uuid4().hex}",
            correlation_id=uuid4(),
        ),
    )
    view = _read(connection, order_id, shop.counter)
    assert (view.commercial, view.balance) == (
        CommercialOrderStatus.CANCELLED,
        OrderBalanceStatus.PARTIALLY_PAID,
    )
    assert _rows(
        connection,
        "SELECT owed_vnd, kept_vnd, written_off_vnd FROM order_disposals WHERE order_id = %s",
        order_id,
    ) == [(owed, 20_000, owed - 20_000)]
    assert _rows(
        connection, "SELECT count(*) FROM order_refunds WHERE order_id = %s", order_id
    ) == [(0,)]

    other = shop.ready_order(retail)
    _counter_pay(connection, shop, other, 20_000)
    close = [
        (
            "UPDATE orders SET commercial_status = %s, row_version = row_version + 1 WHERE id = %s",
            (target, other),
        )
        for target in ("CANCELLATION_REVIEW", "CANCELLED")
    ]
    # Closed with the money kept but no disposal recorded: a cancellation that owes a refund.
    _refused(connection, "without refunding", close)
    # A disposal that keeps anything but what the ledger holds cannot commit beside the close.
    disposal = (
        """
        INSERT INTO order_disposals (
            id, order_id, store_id, days_waiting, attempts_counted, attempt_days, owed_vnd,
            storage_fee_vnd, kept_vnd, written_off_vnd, policy_version_id, disposed_by_staff_id,
            disposed_at
        ) VALUES (%s, %s, %s, 61, 3, 2, %s, 0, 0, %s, %s, %s, now())
        """,
        (
            uuid4(),
            other,
            shop.store_id,
            TOTAL_VND,
            TOTAL_VND,
            _policy_id(connection),
            shop.owner.staff_user_id,
        ),
    )
    _refused(connection, "keeps exactly", [disposal, *close])
    assert _read(connection, other, shop.counter).commercial is CommercialOrderStatus.ACTIVE


# --- a fee-bearing settled order -----------------------------------------------------------------


def test_a_fee_bearing_settled_order_commits_and_a_fee_outside_its_settlement_is_refused(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    retail = shop.customer(CustomerKind.RETAIL)
    order_id = shop.ready_order(retail)
    _age(connection, order_id, 25)
    view = _read(connection, order_id, shop.counter)
    fee = view.owed_vnd - TOTAL_VND
    assert fee > 0
    assert [charge.kind for charge in view.charges] == [
        ChargeKind.QUOTED_TOTAL,
        ChargeKind.STORAGE_FEE,
    ]
    # The exact-total route cannot take a fee-bearing order; the payment route can.
    with pytest.raises(SettlementStateError) as refused:
        SettlementRepository().record(
            connection,
            SettlementCommand(
                order_id=order_id,
                paid_amount_vnd=TOTAL_VND,
                collected_by_customer=True,
                principal=shop.counter,
                correlation_id=uuid4(),
            ),
        )
    assert refused.value.reason_code == "STORAGE_FEE_OWED"
    paid = _counter_pay(connection, shop, order_id, TOTAL_VND + fee, collected=True)
    assert (paid.balance_status, paid.remaining_vnd) == ("PAID", 0)
    [(settlement_id, expected)] = _rows(
        connection,
        "SELECT id, expected_total_vnd FROM order_settlements WHERE order_id = %s",
        order_id,
    )
    assert expected == TOTAL_VND + fee
    assert _rows(
        connection,
        "SELECT settlement_id, account_charge_id, amount_vnd FROM order_storage_fees "
        "WHERE order_id = %s",
        order_id,
    ) == [(settlement_id, None, fee)]

    # A fee on an order settled at its quoted total alone: the settlement does not include it.
    plain = shop.ready_order(retail)
    _counter_pay(connection, shop, plain, TOTAL_VND, collected=True)
    [(plain_settlement,)] = _rows(
        connection, "SELECT id FROM order_settlements WHERE order_id = %s", plain
    )
    params = (
        uuid4(),
        plain,
        shop.store_id,
        plain_settlement,
        None,
        5_000,
        _policy_id(connection),
        shop.owner.staff_user_id,
    )
    _refused(
        connection, "inside a settlement of the quoted total plus the fee", [(_INSERT_FEE, params)]
    )
    # A fee bound to both a settlement and an account charge is not a fee the schema admits.
    with pytest.raises(psycopg.errors.Error), connection.transaction(), connection.cursor() as cur:
        cur.execute(_INSERT_FEE, (*params[:4], uuid4(), *params[5:]))


# --- an account order that waited past the free days ---------------------------------------------


def test_an_account_order_that_waited_is_charged_its_fee_and_the_limit_counts_it(
    connection: psycopg.Connection[Any], shop: Shop
) -> None:
    hotel = shop.customer()
    order_id = shop.ready_order(hotel)
    _age(connection, order_id, 25)
    view = _read(connection, order_id, shop.counter)
    fee = view.owed_vnd - TOTAL_VND
    assert fee > 0
    # A limit the quoted total fits but the total plus the fee does not.
    shop.open(hotel, TOTAL_VND + fee - 1)

    def offer() -> Any:
        with connection.cursor() as cursor:
            return AccountRepository().order_handover(
                cursor, order_id=order_id, principal=shop.counter, now=datetime.now(UTC)
            )

    refused_offer = offer()
    assert (refused_offer.offered, refused_offer.refusal) == (
        False,
        AccountRefusal.ACCOUNT_LIMIT_EXCEEDED,
    )
    assert refused_offer.order_remaining_vnd == TOTAL_VND + fee
    with pytest.raises(AccountRuleError) as caught:
        shop.charge(order_id)
    assert caught.value.code is AccountRefusal.ACCOUNT_LIMIT_EXCEEDED

    # Exactly the total plus the fee: it leaves, with the fee on the account, fixed there.
    shop.set_limit(hotel, TOTAL_VND + fee)
    assert offer().offered is True
    stored = shop.charge(order_id)
    assert stored.amount_vnd == TOTAL_VND + fee
    [(charge_id, owed, amount)] = _rows(
        connection,
        "SELECT id, owed_vnd, amount_vnd FROM customer_account_charges WHERE order_id = %s",
        order_id,
    )
    assert (owed, amount) == (TOTAL_VND + fee, TOTAL_VND + fee)
    assert _rows(
        connection,
        "SELECT settlement_id, account_charge_id, amount_vnd FROM order_storage_fees "
        "WHERE order_id = %s",
        order_id,
    ) == [(None, charge_id, fee)]
    view = _read(connection, order_id, shop.counter)
    assert view.balance is OrderBalanceStatus.ON_ACCOUNT
    assert (view.owed_vnd, view.paid_vnd, view.remaining_vnd) == (
        TOTAL_VND + fee,
        0,
        TOTAL_VND + fee,
    )

    # It has left the shop: not on the waiting list, and the fee no longer accrues.
    with connection.cursor() as cursor:
        listed = UnclaimedRepository.list_awaiting_pickup(
            cursor, store_id=shop.store_id, principal=shop.owner, as_of=datetime.now(UTC)
        )
        later = storage_fee_for_order(
            cursor, order_id=order_id, moment=datetime.now(UTC) + timedelta(days=10)
        )
    assert order_id not in {row.order_id for row in listed.orders}
    assert later.fee.amount_vnd == fee
    # A fee fixed by the charge is not waived afterwards -- by the route or around it.
    with pytest.raises(UnclaimedRefused) as nothing:
        UnclaimedRepository().waive_storage_fee(
            connection,
            WaiverCommand(
                order_id=order_id,
                expected_row_version=view.row_version,
                principal=shop.owner,
                idempotency_key=f"waive-{uuid4().hex}",
                correlation_id=uuid4(),
                reason="Khách quen",
            ),
        )
    assert nothing.value.code == "NO_STORAGE_FEE_OWED"
    _refused(
        connection,
        "waived only before the order is paid in full",
        [
            (
                """
                INSERT INTO storage_fee_waivers (
                    id, order_id, store_id, waived_amount_vnd, days_waiting, reason,
                    policy_version_id, waived_by_staff_id, waived_at
                ) VALUES (%s, %s, %s, %s, 25, 'Khách quen', %s, %s, now())
                """,
                (
                    uuid4(),
                    order_id,
                    shop.store_id,
                    fee,
                    _policy_id(connection),
                    shop.owner.staff_user_id,
                ),
            )
        ],
    )

    # The account pays it off: settled at the quoted total plus the fee, through the account.
    shop.pay(hotel, TOTAL_VND + fee)
    view = _read(connection, order_id, shop.counter)
    assert view.balance is OrderBalanceStatus.PAID
    assert (view.owed_vnd, view.paid_vnd, view.remaining_vnd) == (
        TOTAL_VND + fee,
        TOTAL_VND + fee,
        0,
    )
    assert _rows(
        connection,
        "SELECT settlement_shape, expected_total_vnd, paid_amount_vnd FROM order_settlements "
        "WHERE order_id = %s",
        order_id,
    ) == [("EXACT_PAYMENT_ON_ACCOUNT", TOTAL_VND + fee, TOTAL_VND + fee)]
