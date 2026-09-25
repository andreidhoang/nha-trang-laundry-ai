"""`PAYMENT-002` (`DEC-035`, B2B half) against real PostgreSQL: account customers (công nợ).

What only the database can prove, each against the rows it holds:

* **refused until the owner publishes the terms**, on a database where nothing was published;
* **only the owner opens an account, only for a BUSINESS customer**, and the customer stays one;
* **a limit never typed refuses** (`ACCOUNT_LIMIT_UNSET`) and writes nothing;
* **two orders leave unpaid within the limit, a third is refused over it** -- each re-decided
  under the account's and the order's row locks, the balance `ON_ACCOUNT`, no payment row written;
* **a payment reaches the oldest orders first**: one `order_payments` row per order, the order it
  pays off settled (`EXACT_PAYMENT_ON_ACCOUNT`) and `PAID` -- a completed order included -- and the
  next paid in part;
* **takings and the report still equal the payment ledger**, with the account's money in it once;
* **the statement's totals equal the charges and payments** it covers, month by month, frozen by the
  owner's month close;
* **the overdue block** from the 16th, and the owner's lift, recorded with its reason;
* **the schema refuses** a charged order paid around its account, and any other edit of a closed
  order; no phone value in any ledger row.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from nha_trang_laundry_db.account_terms import (
    AccountTermsAuthorizationError,
    publish_account_terms,
    read_published_account_terms,
)
from nha_trang_laundry_db.accounts import (
    STATEMENT_QUERY,
    AccountChargeCommand,
    AccountNotFoundError,
    AccountPaymentCommand,
    AccountPaymentRefused,
    AccountRepository,
    AccountStateError,
    LiftBlockCommand,
    OpenAccountCommand,
    UpdateAccountCommand,
    freeze_statements,
)
from nha_trang_laundry_db.customers import CustomerChanges, CustomerRepository
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import CreateOrderCommand, OrderRepository
from nha_trang_laundry_db.privacy_notice import publish_privacy_notice
from nha_trang_laundry_db.reports import ReportKey, ReportRepository
from nha_trang_laundry_db.settlement import SettlementRepository
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_domain.accounts import AccountRefusal, AccountRuleError, AccountStatus
from nha_trang_laundry_domain.catalog import AcquisitionSource, FulfillmentMode
from nha_trang_laundry_domain.customers import CustomerKind, CustomerRefusal, CustomerRuleError
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.payments import PaymentMethod
from nha_trang_laundry_domain.sla import STANDARD_WASH_SLA
from psycopg import sql
from psycopg.conninfo import make_conninfo
from quote_test_data import accepted_quote
from test_customer_notice import notice_payload
from test_customer_records import _join, _mobile, _person, _store
from test_order_step_repository import TOTAL_VND, _read, _step

HCM = ZoneInfo("Asia/Ho_Chi_Minh")
ROOT = Path(__file__).resolve().parents[3]
TERMS = json.loads((ROOT / "templates/account-terms-dec-035.json").read_text(encoding="utf-8"))


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
def scratch_url() -> Iterator[str]:
    """A database of its own, migrated from empty, where nobody has published anything."""

    configured = _database_url()
    maintenance = make_conninfo(configured, dbname="postgres")
    name = f"ntl_account_{uuid4().hex[:12]}"
    with psycopg.connect(maintenance, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(configured, dbname=name)
    finally:
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


# --- fixtures -------------------------------------------------------------------------------------


class Shop:
    """One store, its owner and its counter, with the notice and the terms published."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.store_id = _store(connection)
        self.owner = _join(connection, self.store_id, _person(connection, StaffRole.OWNER_ADMIN))
        self.counter = _join(connection, self.store_id, _person(connection, StaffRole.OPERATOR))
        publish_privacy_notice(
            connection, actor_id=self.owner.staff_user_id, payload=notice_payload()
        )
        publish_account_terms(connection, actor_id=self.owner.staff_user_id, payload=TERMS)

    def customer(self, kind: CustomerKind = CustomerKind.BUSINESS) -> UUID:
        national, _ = _mobile()
        return CustomerRepository().create(
            self.connection,
            store_id=self.store_id,
            principal=self.counter,
            phone=national,
            display_name="Homestay Biển Xanh",
            delivery_address=None,
            note=None,
            kind=kind,
            service_consent=True,
            marketing_consent=False,
            at=datetime.now(UTC),
            correlation_id=uuid4(),
        )

    def open(
        self, customer_id: UUID, limit: int | None = None, *, at: datetime | None = None
    ) -> None:
        AccountRepository().open(
            self.connection,
            OpenAccountCommand(
                store_id=self.store_id,
                customer_id=customer_id,
                credit_limit_vnd=limit,
                principal=self.owner,
                correlation_id=uuid4(),
                at=at or datetime.now(UTC),
            ),
        )

    def account(self, customer_id: UUID, now: datetime | None = None) -> Any:
        with self.connection.cursor() as cursor:
            return AccountRepository().read(
                cursor,
                store_id=self.store_id,
                customer_id=customer_id,
                principal=self.owner,
                now=now or datetime.now(UTC),
            )

    def set_limit(self, customer_id: UUID, limit: int) -> None:
        AccountRepository().update(
            self.connection,
            UpdateAccountCommand(
                store_id=self.store_id,
                customer_id=customer_id,
                expected_row_version=self.account(customer_id).account.row_version,
                provided=frozenset({"credit_limit_vnd"}),
                credit_limit_vnd=limit,
                status=None,
                principal=self.owner,
                correlation_id=uuid4(),
                at=datetime.now(UTC),
            ),
        )

    def ready_order(
        self,
        customer_id: UUID,
        mode: FulfillmentMode = FulfillmentMode.SELF_DROP_SELF_COLLECT,
    ) -> UUID:
        quote_id, revision, quote, contact_id = accepted_quote(
            self.connection,
            store_id=self.store_id,
            principal=self.counter,
            customer_id=customer_id,
            fulfillment_mode=mode,
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
                    mode,
                    self.counter,
                    f"order-{uuid4().hex}",
                    uuid4(),
                    datetime.now(UTC),
                    AcquisitionSource.WALK_IN,
                ),
            )
            .order_id
        )
        view = _step(
            self.connection, order_id, self.counter, 1, OrderStep.RECEIVE, slot_approved=True
        ).view
        for step in (OrderStep.START_WASH, OrderStep.QUALITY_CHECK, OrderStep.MARK_READY):
            view = _step(self.connection, order_id, self.counter, view.row_version, step).view
        return order_id

    def charge(self, order_id: UUID, *, at: datetime | None = None, collected: bool = True) -> Any:
        return AccountRepository().charge(
            self.connection,
            AccountChargeCommand(
                order_id=order_id,
                expected_row_version=_read(self.connection, order_id, self.counter).row_version,
                collected_by_customer=collected,
                principal=self.counter,
                correlation_id=uuid4(),
                at=at or datetime.now(UTC),
            ),
        )

    def pay(
        self,
        customer_id: UUID,
        amount: int,
        *,
        at: datetime | None = None,
        method: PaymentMethod = PaymentMethod.TIEN_MAT,
        seen: bool = False,
    ) -> Any:
        moment = at or datetime.now(UTC)
        return AccountRepository().record_payment(
            self.connection,
            AccountPaymentCommand(
                store_id=self.store_id,
                customer_id=customer_id,
                expected_row_version=self.account(customer_id, moment).account.row_version,
                amount_vnd=amount,
                method=method,
                transfer_seen=seen,
                bank_ref_last=None,
                principal=self.counter,
                correlation_id=uuid4(),
                at=moment,
            ),
        )


def _rows(connection: Any, query: str, *params: object) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return [tuple(row) for row in cursor.fetchall()]


def _one(connection: Any, query: str, *params: object) -> Any:
    [(value,)] = _rows(connection, query, *params)
    return value


def _local(day: date, hour: int = 10) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=HCM)


def _refusal(caught: pytest.ExceptionInfo[AccountRuleError]) -> AccountRefusal:
    return caught.value.code


# --- publication ----------------------------------------------------------------------------------


def test_no_account_is_opened_until_the_owner_publishes_the_terms(scratch_url: str) -> None:
    with psycopg.connect(scratch_url, autocommit=True) as connection:
        apply_migrations(connection)
        store_id = _store(connection)
        owner = _join(connection, store_id, _person(connection, StaffRole.OWNER_ADMIN))
        counter = _join(connection, store_id, _person(connection, StaffRole.OPERATOR))
        publish_privacy_notice(connection, actor_id=owner.staff_user_id, payload=notice_payload())
        national, _ = _mobile()
        customer_id = CustomerRepository().create(
            connection,
            store_id=store_id,
            principal=counter,
            phone=national,
            display_name="Khách sạn Hải Âu",
            delivery_address=None,
            note=None,
            kind=CustomerKind.BUSINESS,
            service_consent=True,
            marketing_consent=False,
            at=datetime.now(UTC),
            correlation_id=uuid4(),
        )
        command = OpenAccountCommand(
            store_id=store_id,
            customer_id=customer_id,
            credit_limit_vnd=3_000_000,
            principal=owner,
            correlation_id=uuid4(),
            at=datetime.now(UTC),
        )
        with connection.cursor() as cursor:
            assert read_published_account_terms(cursor) is None
            read = AccountRepository().read(
                cursor,
                store_id=store_id,
                customer_id=customer_id,
                principal=owner,
                now=datetime.now(UTC),
            )
        assert read.terms_published is False
        assert read.open_refusal is AccountRefusal.ACCOUNT_TERMS_UNPUBLISHED
        with pytest.raises(AccountRuleError) as caught:
            AccountRepository().open(connection, command)
        assert _refusal(caught) is AccountRefusal.ACCOUNT_TERMS_UNPUBLISHED
        assert _one(connection, "SELECT count(*) FROM customer_accounts") == 0
        assert (
            _one(
                connection,
                "SELECT count(*) FROM domain_events WHERE aggregate_type = 'CUSTOMER_ACCOUNT'",
            )
            == 0
        )

        # Only an active owner publishes; anyone else is refused and nothing is written.
        with pytest.raises(AccountTermsAuthorizationError):
            publish_account_terms(connection, actor_id=counter.staff_user_id, payload=TERMS)
        digest, created = publish_account_terms(
            connection, actor_id=owner.staff_user_id, payload=TERMS
        )
        assert created
        assert publish_account_terms(connection, actor_id=owner.staff_user_id, payload=TERMS) == (
            digest,
            False,
        )
        AccountRepository().open(connection, command)
        assert _one(connection, "SELECT count(*) FROM customer_accounts") == 1


# --- opening --------------------------------------------------------------------------------------


def test_only_the_owner_opens_an_account_and_only_for_a_business(connection: Any) -> None:
    shop = Shop(connection)
    business = shop.customer()
    retail = shop.customer(CustomerKind.RETAIL)
    for who in (
        shop.counter,
        _join(connection, shop.store_id, _person(connection, StaffRole.OPS_APPROVER)),
    ):
        with pytest.raises(StoreAccessError):
            AccountRepository().open(
                connection,
                OpenAccountCommand(
                    store_id=shop.store_id,
                    customer_id=business,
                    credit_limit_vnd=None,
                    principal=who,
                    correlation_id=uuid4(),
                    at=datetime.now(UTC),
                ),
            )
    # An owner without MFA, and an owner of another store, are refused too.
    no_mfa = StaffPrincipal(
        shop.owner.staff_user_id, shop.owner.oidc_subject, shop.owner.roles, False, uuid4()
    )
    elsewhere = _person(connection, StaffRole.OWNER_ADMIN)
    for who in (no_mfa, elsewhere):
        with pytest.raises(StoreAccessError):
            AccountRepository().open(
                connection,
                OpenAccountCommand(
                    store_id=shop.store_id,
                    customer_id=business,
                    credit_limit_vnd=None,
                    principal=who,
                    correlation_id=uuid4(),
                    at=datetime.now(UTC),
                ),
            )
    with pytest.raises(AccountRuleError) as caught:
        shop.open(retail)
    assert _refusal(caught) is AccountRefusal.ACCOUNT_REQUIRES_BUSINESS
    assert shop.account(retail).open_refusal is AccountRefusal.ACCOUNT_REQUIRES_BUSINESS

    shop.open(business)
    read = shop.account(business)
    assert read.account is not None
    assert read.account.credit_limit_vnd is None
    assert read.account.handover_refusal is AccountRefusal.ACCOUNT_LIMIT_UNSET
    assert read.recommended_limit_vnd == 3_000_000
    with pytest.raises(AccountRuleError) as caught:
        shop.open(business, 3_000_000)
    assert _refusal(caught) is AccountRefusal.ACCOUNT_ALREADY_OPEN

    # The customer stays a business customer, refused by name and by the schema.
    with connection.cursor() as cursor:
        profile = CustomerRepository().profile(
            cursor, store_id=shop.store_id, customer_id=business, principal=shop.counter
        )
    with pytest.raises(CustomerRuleError) as named:
        CustomerRepository().update(
            connection,
            store_id=shop.store_id,
            customer_id=business,
            principal=shop.counter,
            expected_row_version=profile.row_version,
            changes=CustomerChanges(provided=frozenset({"kind"}), kind=CustomerKind.RETAIL),
            at=datetime.now(UTC),
            correlation_id=uuid4(),
        )
    assert named.value.code is CustomerRefusal.CUSTOMER_HAS_ACCOUNT
    with pytest.raises(psycopg.errors.RaiseException):
        connection.execute(
            "UPDATE customers SET kind = 'RETAIL', row_version = row_version + 1 WHERE id = %s",
            (business,),
        )


# --- leaving on the account -----------------------------------------------------------------------


def test_a_limit_never_typed_refuses_and_writes_nothing(connection: Any) -> None:
    shop = Shop(connection)
    homestay = shop.customer()
    shop.open(homestay)
    order_id = shop.ready_order(homestay)
    before = _read(connection, order_id, shop.counter)
    with pytest.raises(AccountRuleError) as caught:
        shop.charge(order_id)
    assert _refusal(caught) is AccountRefusal.ACCOUNT_LIMIT_UNSET
    after = _read(connection, order_id, shop.counter)
    assert (after.balance.value, after.row_version) == ("UNPAID", before.row_version)
    assert (
        _one(
            connection,
            "SELECT count(*) FROM customer_account_charges WHERE order_id = %s",
            order_id,
        )
        == 0
    )
    with connection.cursor() as cursor:
        offer = AccountRepository().order_handover(
            cursor, order_id=order_id, principal=shop.counter, now=datetime.now(UTC)
        )
    assert offer is not None and offer.offered is False
    assert offer.refusal is AccountRefusal.ACCOUNT_LIMIT_UNSET


def test_two_orders_leave_within_the_limit_and_a_third_is_refused(connection: Any) -> None:
    shop = Shop(connection)
    homestay = shop.customer()
    shop.open(homestay)
    shop.set_limit(homestay, 2 * TOTAL_VND + 10_000)
    first, second, third = (shop.ready_order(homestay) for _ in range(3))
    with connection.cursor() as cursor:
        before = SettlementRepository.collected_today(
            cursor, store_id=shop.store_id, principal=shop.counter
        )

    charged = shop.charge(first)
    assert (charged.amount_vnd, charged.outstanding_after_vnd) == (TOTAL_VND, TOTAL_VND)
    assert charged.balance_status == "ON_ACCOUNT" and charged.self_collection_recorded is True
    view = _read(connection, first, shop.counter)
    # Money owed, not money collected: nothing paid, all of it remaining.
    assert (view.balance.value, view.paid_vnd, view.remaining_vnd) == ("ON_ACCOUNT", 0, TOTAL_VND)
    assert [step.step for step in view.next_steps if step.primary] == [OrderStep.HAND_OVER]
    assert OrderStep.TAKE_PAYMENT not in {step.step for step in view.next_steps}
    # Handing over completes it, unpaid, on the account.
    done = _step(connection, first, shop.counter, view.row_version, OrderStep.HAND_OVER).view
    assert (done.commercial.value, done.balance.value) == ("COMPLETED", "ON_ACCOUNT")

    shop.charge(second)
    with pytest.raises(AccountRuleError) as caught:
        shop.charge(third)
    assert _refusal(caught) is AccountRefusal.ACCOUNT_LIMIT_EXCEEDED
    assert _read(connection, third, shop.counter).balance.value == "UNPAID"

    account = shop.account(homestay).account
    assert (account.outstanding_vnd, account.available_vnd) == (2 * TOTAL_VND, 10_000)
    assert [item.order_id for item in account.open_charges] == [first, second]
    # Nothing moved in the drawer: no payment row for either, and today's takings are unchanged.
    assert (
        _one(
            connection,
            "SELECT count(*) FROM order_payments WHERE order_id = ANY(%s)",
            [first, second],
        )
        == 0
    )
    with connection.cursor() as cursor:
        after = SettlementRepository.collected_today(
            cursor, store_id=shop.store_id, principal=shop.counter
        )
    assert after.collected_vnd == before.collected_vnd

    # Each charge committed with its event, audit row and outbox row, on the order's timeline.
    for order_id in (first, second):
        assert _rows(
            connection,
            "SELECT (SELECT count(*) FROM domain_events WHERE aggregate_id = %s "
            "AND event_type = 'ORDER_ACCOUNT_CHARGED'), (SELECT count(*) FROM audit_events "
            "WHERE aggregate_id = %s AND action = 'ORDER_ACCOUNT_CHARGE'), (SELECT count(*) "
            "FROM outbox_events WHERE aggregate_id = %s "
            "AND event_type = 'order.account_charged.v1')",
            order_id,
            order_id,
            order_id,
        ) == [(1, 1, 1)]


def test_a_stale_screen_is_refused(connection: Any) -> None:
    shop = Shop(connection)
    homestay = shop.customer()
    shop.open(homestay, 1_000_000)
    order_id = shop.ready_order(homestay)
    version = _read(connection, order_id, shop.counter).row_version
    with pytest.raises(AccountStateError):
        AccountRepository().charge(
            connection,
            AccountChargeCommand(
                order_id=order_id,
                expected_row_version=version - 1,
                collected_by_customer=True,
                principal=shop.counter,
                correlation_id=uuid4(),
                at=datetime.now(UTC),
            ),
        )
    shop.charge(order_id)
    stale = shop.account(homestay).account.row_version - 1
    with pytest.raises(AccountStateError):
        AccountRepository().record_payment(
            connection,
            AccountPaymentCommand(
                store_id=shop.store_id,
                customer_id=homestay,
                expected_row_version=stale,
                amount_vnd=1,
                method=PaymentMethod.TIEN_MAT,
                transfer_seen=False,
                bank_ref_last=None,
                principal=shop.counter,
                correlation_id=uuid4(),
                at=datetime.now(UTC),
            ),
        )


def test_a_delivery_order_goes_on_account_before_the_courier_takes_it(connection: Any) -> None:
    shop = Shop(connection)
    hotel = shop.customer()
    shop.open(hotel, 1_000_000)
    order_id = shop.ready_order(hotel, FulfillmentMode.PICKUP_AND_RETURN)
    with pytest.raises(AccountRuleError) as caught:
        shop.charge(order_id, collected=True)
    assert _refusal(caught) is AccountRefusal.COLLECTION_WAS_NOT_BY_THE_CUSTOMER
    charged = shop.charge(order_id, collected=False)
    assert (charged.balance_status, charged.self_collection_recorded) == ("ON_ACCOUNT", False)
    view = _read(connection, order_id, shop.counter)
    assert OrderStep.RELEASE in {step.step for step in view.next_steps}


# --- paying the account ---------------------------------------------------------------------------


def test_a_payment_reaches_the_oldest_orders_first_and_moves_their_balances(
    connection: Any,
) -> None:
    shop = Shop(connection)
    homestay = shop.customer()
    shop.open(homestay, 1_000_000)
    first, second, third = (shop.ready_order(homestay) for _ in range(3))
    for order_id in (first, second, third):
        shop.charge(order_id)
    view = _read(connection, first, shop.counter)
    _step(connection, first, shop.counter, view.row_version, OrderStep.HAND_OVER)
    assert _read(connection, first, shop.counter).commercial.value == "COMPLETED"

    with pytest.raises(AccountPaymentRefused) as refused:
        shop.pay(homestay, 3 * TOTAL_VND + 1)
    assert refused.value.code == "OVERPAYMENT_REFUSED"
    with pytest.raises(AccountPaymentRefused) as refused:
        shop.pay(homestay, 10_000, method=PaymentMethod.CHUYEN_KHOAN)
    assert refused.value.code == "TRANSFER_NOT_SEEN"

    paid = shop.pay(homestay, TOTAL_VND + 40_000)
    assert [(item.order_id, item.amount_vnd, item.settled) for item in paid.allocations] == [
        (first, TOTAL_VND, True),
        (second, 40_000, False),
    ]
    assert paid.outstanding_after_vnd == 2 * TOTAL_VND - 40_000

    # The completed order is now PAID, through the account's settlement shape; nothing else moved.
    settled = _read(connection, first, shop.counter)
    assert (settled.commercial.value, settled.balance.value) == ("COMPLETED", "PAID")
    assert (settled.paid_vnd, settled.remaining_vnd) == (TOTAL_VND, 0)
    assert settled.settlement_shape == "EXACT_PAYMENT_ON_ACCOUNT"
    assert (
        _one(
            connection,
            "SELECT collected_by || ':' || paid_amount_vnd FROM order_settlements "
            "WHERE order_id = %s",
            first,
        )
        == f"ACCOUNT_HANDOVER:{TOTAL_VND}"
    )
    part = _read(connection, second, shop.counter)
    assert (part.balance.value, part.paid_vnd, part.remaining_vnd) == (
        "ON_ACCOUNT",
        40_000,
        TOTAL_VND - 40_000,
    )
    assert _read(connection, third, shop.counter).paid_vnd == 0

    # One order payment per order reached, tied to the account payment by the allocation ledger.
    assert _rows(
        connection,
        "SELECT a.position, a.order_id, a.amount_vnd, p.amount_vnd, p.method "
        "FROM customer_account_allocations a JOIN order_payments p ON p.id = a.order_payment_id "
        "WHERE a.account_payment_id = %s ORDER BY a.position",
        paid.payment_id,
    ) == [
        (1, first, TOTAL_VND, TOTAL_VND, "TIEN_MAT"),
        (2, second, 40_000, 40_000, "TIEN_MAT"),
    ]
    # The rest pays off the second, then the third; the account owes nothing.
    rest = shop.pay(homestay, 2 * TOTAL_VND - 40_000, method=PaymentMethod.CHUYEN_KHOAN, seen=True)
    assert [(item.order_id, item.settled) for item in rest.allocations] == [
        (second, True),
        (third, True),
    ]
    assert shop.account(homestay).account.outstanding_vnd == 0
    with pytest.raises(AccountPaymentRefused) as refused:
        shop.pay(homestay, 1)
    assert refused.value.code == "NOTHING_OWED"


def test_takings_and_the_report_still_equal_the_payment_ledger(connection: Any) -> None:
    shop = Shop(connection)
    homestay = shop.customer()
    shop.open(homestay, 1_000_000)
    orders = [shop.ready_order(homestay) for _ in range(2)]
    for order_id in orders:
        shop.charge(order_id)
    shop.pay(homestay, 150_000)
    shop.pay(homestay, 20_000, method=PaymentMethod.CHUYEN_KHOAN, seen=True)

    now = datetime.now(UTC)
    today = now.astimezone(HCM).date()
    with connection.cursor() as cursor:
        takings = SettlementRepository.collected_today(
            cursor, store_id=shop.store_id, principal=shop.counter, as_of=now
        )
        report = ReportRepository.store_report(
            cursor,
            store_id=shop.store_id,
            principal=shop.owner,
            policy=STANDARD_WASH_SLA,
            from_date=today,
            to_date=today,
            as_of=now,
        )
    ledger = _rows(
        connection,
        "SELECT coalesce(sum(amount_vnd) FILTER (WHERE method = 'TIEN_MAT'), 0), "
        "coalesce(sum(amount_vnd) FILTER (WHERE method = 'CHUYEN_KHOAN'), 0) "
        "FROM order_payments WHERE store_id = %s",
        shop.store_id,
    )
    account_payments = _one(
        connection,
        "SELECT sum(amount_vnd) FROM customer_account_payments WHERE store_id = %s",
        shop.store_id,
    )
    assert ledger == [(150_000, 20_000)]
    assert account_payments == 170_000
    assert (takings.cash_vnd, takings.transfer_vnd, takings.collected_vnd) == (
        150_000,
        20_000,
        170_000,
    )
    figures = {figure.key: figure for figure in report.summary.figures}
    money = figures[ReportKey.MONEY_COLLECTED]
    assert money.numerator == 170_000
    # Counted as ledger rows: the 150.000 ₫ reached two orders, so it is two cash payments.
    assert money.by_kind == (("TIEN_MAT", 2, 150_000), ("CHUYEN_KHOAN", 1, 20_000))
    # The two charges (220.000 ₫ owed on the account) appear nowhere in the money figures.
    assert (
        _one(
            connection,
            "SELECT sum(amount_vnd) FROM customer_account_charges WHERE store_id = %s",
            shop.store_id,
        )
        == 2 * TOTAL_VND
    )


# --- the statement, the overdue block and the lift -----------------------------------------------


def test_statements_total_the_charges_and_payments_month_by_month(connection: Any) -> None:
    shop = Shop(connection)
    hotel = shop.customer()
    shop.open(hotel, 1_000_000, at=_local(date(2026, 8, 1), 9))
    august = [shop.ready_order(hotel) for _ in range(2)]
    shop.charge(august[0], at=_local(date(2026, 8, 3)))
    # 23:59:59 on 31 August, local, is August.
    shop.charge(august[1], at=datetime(2026, 8, 31, 23, 59, 59, tzinfo=HCM))
    september = shop.ready_order(hotel)
    shop.charge(september, at=datetime(2026, 9, 1, 0, 0, tzinfo=HCM))
    shop.pay(hotel, 150_000, at=_local(date(2026, 9, 10)))

    def statement(month: date) -> Any:
        with connection.cursor() as cursor:
            return AccountRepository().statement(
                cursor,
                store_id=shop.store_id,
                customer_id=hotel,
                month=month,
                principal=shop.counter,
                now=_local(date(2026, 9, 12)),
            )

    aug = statement(date(2026, 8, 1))
    assert (
        aug.figures.opening_vnd,
        aug.figures.charges_vnd,
        aug.figures.charge_count,
        aug.figures.payments_vnd,
        aug.figures.closing_vnd,
        aug.figures.due_on,
    ) == (0, 2 * TOTAL_VND, 2, 0, 2 * TOTAL_VND, date(2026, 9, 15))
    assert [line.order_id for line in aug.charges] == august
    assert aug.month_ended is True and aug.frozen is None
    sep = statement(date(2026, 9, 1))
    assert (
        sep.figures.opening_vnd,
        sep.figures.charges_vnd,
        sep.figures.payments_vnd,
        sep.figures.payment_count,
        sep.figures.closing_vnd,
        sep.figures.due_on,
    ) == (2 * TOTAL_VND, TOTAL_VND, 150_000, 1, 3 * TOTAL_VND - 150_000, date(2026, 10, 15))
    assert sep.month_ended is False
    # The totals are the rows: the charges and payments of each month, summed independently.
    assert aug.figures.charges_vnd == _one(
        connection,
        "SELECT sum(amount_vnd) FROM customer_account_charges WHERE customer_id = %s "
        "AND charged_at < '2026-09-01 00:00+07'",
        hotel,
    )
    assert sep.figures.payments_vnd == _one(
        connection,
        "SELECT sum(amount_vnd) FROM customer_account_payments WHERE customer_id = %s",
        hotel,
    )

    # The owner's month close freezes August as the SQL reads it -- once.
    with pytest.raises(AccountRuleError) as caught:
        freeze_statements(
            connection,
            actor_id=shop.owner.staff_user_id,
            month=date(2026, 9, 1),
            now=_local(date(2026, 9, 30), 23),
            store_id=shop.store_id,
        )
    assert _refusal(caught) is AccountRefusal.MONTH_NOT_ENDED
    with pytest.raises(StoreAccessError):
        freeze_statements(
            connection,
            actor_id=shop.counter.staff_user_id,
            month=date(2026, 8, 1),
            now=_local(date(2026, 9, 1)),
            store_id=shop.store_id,
        )
    frozen = freeze_statements(
        connection,
        actor_id=shop.owner.staff_user_id,
        month=date(2026, 8, 1),
        now=_local(date(2026, 9, 1)),
        store_id=shop.store_id,
    )
    assert [(item.created, item.figures.closing_vnd) for item in frozen] == [(True, 2 * TOTAL_VND)]
    again = freeze_statements(
        connection,
        actor_id=shop.owner.staff_user_id,
        month=date(2026, 8, 1),
        now=_local(date(2026, 9, 2)),
        store_id=shop.store_id,
    )
    assert [item.created for item in again] == [False]
    copy = statement(date(2026, 8, 1)).frozen
    assert copy is not None
    assert (copy.opening_vnd, copy.charges_vnd, copy.payments_vnd, copy.closing_vnd) == (
        0,
        2 * TOTAL_VND,
        0,
        2 * TOTAL_VND,
    )
    assert copy.query_version == STATEMENT_QUERY.label
    with pytest.raises(psycopg.errors.RaiseException):
        connection.execute(
            "UPDATE customer_account_statements SET closing_vnd = 0 WHERE account_id = %s",
            (shop.account(hotel).account.account_id,),
        )


def test_an_overdue_statement_blocks_new_orders_until_paid_or_lifted(connection: Any) -> None:
    shop = Shop(connection)
    spa = shop.customer()
    shop.open(spa, 1_000_000, at=_local(date(2026, 8, 1), 9))
    august = shop.ready_order(spa)
    shop.charge(august, at=_local(date(2026, 8, 20)))
    fresh = shop.ready_order(spa)

    def offer(now: datetime) -> Any:
        with connection.cursor() as cursor:
            return AccountRepository().order_handover(
                cursor, order_id=fresh, principal=shop.counter, now=now
            )

    # On the 15th the August statement is due today, not overdue.
    assert offer(_local(date(2026, 9, 15), 20)).offered is True
    sixteenth = _local(date(2026, 9, 16), 9)
    blocked = offer(sixteenth)
    assert (blocked.offered, blocked.refusal) == (False, AccountRefusal.ACCOUNT_OVERDUE)
    account = shop.account(spa, sixteenth).account
    assert account.overdue_vnd == TOTAL_VND
    assert (account.overdue_month, account.overdue_due_on) == (date(2026, 8, 1), date(2026, 9, 15))
    assert account.handover_refusal is AccountRefusal.ACCOUNT_OVERDUE
    with pytest.raises(AccountRuleError) as caught:
        shop.charge(fresh, at=sixteenth)
    assert _refusal(caught) is AccountRefusal.ACCOUNT_OVERDUE

    # Only the owner lifts it, with a reason and an end; the lift is recorded, reason on its row.
    lift = LiftBlockCommand(
        store_id=shop.store_id,
        customer_id=spa,
        expected_row_version=account.row_version,
        reason="Khách hẹn chuyển khoản ngày 20",
        until=date(2026, 9, 18),
        principal=shop.counter,
        correlation_id=uuid4(),
        at=sixteenth,
    )
    with pytest.raises(StoreAccessError):
        AccountRepository().lift_block(connection, lift)
    lift_id, until, _ = AccountRepository().lift_block(
        connection, replace(lift, principal=shop.owner)
    )
    assert until == datetime(2026, 9, 19, tzinfo=HCM)
    assert (
        _one(connection, "SELECT reason FROM customer_account_block_lifts WHERE id = %s", lift_id)
        == "Khách hẹn chuyển khoản ngày 20"
    )
    assert "Khách hẹn" not in json.dumps(
        _rows(
            connection,
            "SELECT payload FROM domain_events WHERE aggregate_type = 'CUSTOMER_ACCOUNT' "
            "UNION ALL SELECT details FROM audit_events WHERE aggregate_type = 'CUSTOMER_ACCOUNT' "
            "UNION ALL SELECT payload FROM outbox_events WHERE aggregate_type = 'CUSTOMER_ACCOUNT'",
        ),
        ensure_ascii=False,
    )
    assert offer(sixteenth).offered is True
    shop.charge(fresh, at=sixteenth)
    # After the lift ends the block is back, until August is paid.
    later = shop.ready_order(spa)
    after_lift = _local(date(2026, 9, 19), 8)
    with pytest.raises(AccountRuleError) as caught:
        shop.charge(later, at=after_lift)
    assert _refusal(caught) is AccountRefusal.ACCOUNT_OVERDUE
    shop.pay(spa, TOTAL_VND, at=after_lift)
    assert shop.account(spa, after_lift).account.overdue_vnd == 0
    shop.charge(later, at=after_lift)


# --- the schema's own refusals --------------------------------------------------------------------


def test_the_schema_refuses_money_around_the_account(connection: Any) -> None:
    shop = Shop(connection)
    homestay = shop.customer()
    shop.open(homestay, 1_000_000)
    order_id = shop.ready_order(homestay)
    loose = shop.ready_order(homestay)
    shop.charge(order_id)
    view = _read(connection, order_id, shop.counter)
    _step(connection, order_id, shop.counter, view.row_version, OrderStep.HAND_OVER)

    # A completed order admits no edit but its account payoff.
    with pytest.raises(psycopg.errors.RaiseException):
        connection.execute(
            "UPDATE orders SET incident_open = TRUE, row_version = row_version + 1 WHERE id = %s",
            (order_id,),
        )
    # ON_ACCOUNT with no charge row behind it.
    with pytest.raises(psycopg.errors.RaiseException):
        connection.execute(
            "UPDATE orders SET balance_status = 'ON_ACCOUNT', row_version = row_version + 1 "
            "WHERE id = %s",
            (loose,),
        )
    # A counter payment around the account on a charged order.
    with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
        connection.execute(
            """
            INSERT INTO order_payments (id, order_id, store_id, amount_vnd, method, legacy,
                recorded_by_staff_id, recorded_at, created_at)
            VALUES (%s, %s, %s, 1000, 'TIEN_MAT', FALSE, %s, now(), now())
            """,
            (uuid4(), order_id, shop.store_id, shop.counter.staff_user_id),
        )
    # And the ledgers are append-only.
    with pytest.raises(psycopg.errors.RaiseException):
        connection.execute("DELETE FROM customer_account_charges WHERE order_id = %s", (order_id,))


def test_reads_are_store_scoped_and_role_bound(connection: Any) -> None:
    shop = Shop(connection)
    homestay = shop.customer()
    shop.open(homestay, 1_000_000)
    other = _store(connection)
    stranger = _join(connection, other, _person(connection, StaffRole.OWNER_ADMIN))
    with connection.cursor() as cursor, pytest.raises(StoreAccessError):
        AccountRepository().read(
            cursor,
            store_id=shop.store_id,
            customer_id=homestay,
            principal=stranger,
            now=datetime.now(UTC),
        )
    with connection.cursor() as cursor, pytest.raises(AccountNotFoundError):
        # A member of another store asks through their own store: no such account there.
        AccountRepository().statement(
            cursor,
            store_id=other,
            customer_id=homestay,
            month=date(2026, 9, 1),
            principal=stranger,
            now=datetime.now(UTC),
        )


def test_the_owner_changes_the_limit_and_stops_the_account(connection: Any) -> None:
    shop = Shop(connection)
    homestay = shop.customer()
    shop.open(homestay, 500_000)
    account = shop.account(homestay).account
    with pytest.raises(AccountRuleError) as caught:
        AccountRepository().update(
            connection,
            UpdateAccountCommand(
                store_id=shop.store_id,
                customer_id=homestay,
                expected_row_version=account.row_version,
                provided=frozenset({"credit_limit_vnd"}),
                credit_limit_vnd=500_000,
                status=None,
                principal=shop.owner,
                correlation_id=uuid4(),
                at=datetime.now(UTC),
            ),
        )
    assert _refusal(caught) is AccountRefusal.NOTHING_TO_CHANGE
    version = AccountRepository().update(
        connection,
        UpdateAccountCommand(
            store_id=shop.store_id,
            customer_id=homestay,
            expected_row_version=account.row_version,
            provided=frozenset({"status"}),
            credit_limit_vnd=None,
            status=AccountStatus.SUSPENDED,
            principal=shop.owner,
            correlation_id=uuid4(),
            at=datetime.now(UTC),
        ),
    )
    assert version == account.row_version + 1
    order_id = shop.ready_order(homestay)
    with pytest.raises(AccountRuleError) as caught:
        shop.charge(order_id)
    assert _refusal(caught) is AccountRefusal.ACCOUNT_SUSPENDED


def test_no_phone_value_in_any_account_ledger_row(connection: Any) -> None:
    shop = Shop(connection)
    national, e164 = _mobile()
    customer_id = CustomerRepository().create(
        connection,
        store_id=shop.store_id,
        principal=shop.counter,
        phone=national,
        display_name="Homestay Hoa Sứ",
        delivery_address="12 Trần Phú",
        note=None,
        kind=CustomerKind.BUSINESS,
        service_consent=True,
        marketing_consent=False,
        at=datetime.now(UTC),
        correlation_id=uuid4(),
    )
    shop.open(customer_id, 1_000_000)
    order_id = shop.ready_order(customer_id)
    shop.charge(order_id)
    shop.pay(customer_id, 50_000)
    written = json.dumps(
        _rows(
            connection,
            "SELECT payload::text FROM domain_events WHERE occurred_at > now() - interval '1 hour' "
            "UNION ALL SELECT details::text FROM audit_events "
            "WHERE occurred_at > now() - interval '1 hour' "
            "UNION ALL SELECT payload::text FROM outbox_events "
            "WHERE occurred_at > now() - interval '1 hour'",
        ),
        ensure_ascii=False,
    )
    for value in (national, e164, national[1:], "Hoa Sứ", "Trần Phú"):
        assert value not in written
