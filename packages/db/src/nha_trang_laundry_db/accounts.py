"""Account customers (công nợ): open, limit, charge, collect, statement, freeze. `PAYMENT-002`.

The B2B half of `DEC-035`. Every rule is the domain's (`nha_trang_laundry_domain.accounts` and
`.account_charge`); every figure is PostgreSQL's; this module holds the rows and their transactions.

**Who may do what** (store membership and MFA on every one):

* open an account, type or change its limit, stop or restart it, lift an overdue block -- the
  **owner** (`OWNER_ADMIN`) only: `DEC-035` names the owner as the one who marks an account and
  types its limit, and the lift is the owner's too;
* put an order on the account (*Giao đồ — ghi công nợ*) and take a payment against it (*Thu công
  nợ*) -- the counter's roles, the ones that take payments (`settlement.SETTLEMENT_ROLES`);
* read the account and its statements -- the roles that read the customer (`CUSTOMER_READ_ROLES`).

**Locks.** A command that touches an account locks the account row first, then each order it
moves, in the order it moves them, so two account commands never wait on each other the wrong way
round; the order-only commands (steps, counter payments) never lock an account. The decision is
re-made under those locks on facts read under them -- never on what the screen showed.

**One money path.** An order leaving on the account writes no payment (no money moved); a payment
against the account writes one `order_payments` row per order it reaches, oldest first, tied to the
account payment by an allocation row, and the allocation that covers an order's charge writes its
settlement and moves it to `PAID`. The takings and the report read `order_payments` as they always
did, so an account payment is money in on the day it was taken, by its method, exactly once.

**No personal data in any ledger row.** Events, audit rows and outbox rows carry ids, amounts,
methods and states. The owner's lift reason is kept on the lift row only (it is free text a person
wrote about a customer), and never in an event, an audit row or an outbox row.

Nothing here reads a clock: every instant is passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain.account_charge import (
    AccountChargeRefused,
    account_payment_refusal,
    evaluate_account_charge,
)
from nha_trang_laundry_domain.accounts import (
    ACCOUNT_DECISION,
    ACCOUNT_TIMEZONE,
    RECOMMENDED_STARTING_LIMIT_VND,
    AccountHandoverFacts,
    AccountRefusal,
    AccountRuleError,
    AccountStanding,
    AccountStatus,
    OpenCharge,
    account_handover_refusal,
    allocate_oldest_first,
    clean_lift_reason,
    latest_due_month,
    lift_until_instant,
    local_day,
    month_has_ended,
    month_label,
    month_start_instant,
    next_month,
    statement_due_on,
    statement_month,
    validate_limit,
)
from nha_trang_laundry_domain.catalog import (
    MODES_EXPECTING_RETURN,
    CommercialOrderStatus,
    FulfillmentMode,
    OrderBalanceStatus,
    ProductionStatus,
)
from nha_trang_laundry_domain.customers import CustomerKind
from nha_trang_laundry_domain.payments import (
    PaymentMethod,
    normalise_bank_ref,
    owed_charges,
    owed_total,
)
from nha_trang_laundry_domain.settlement import (
    QuotedTotal,
    SettlementNotSupported,
    SettlementShape,
    evaluate_settlement,
)

from nha_trang_laundry_db.account_terms import read_published_account_terms
from nha_trang_laundry_db.customers import CUSTOMER_READ_ROLES
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.query_version import QueryVersion, query_version
from nha_trang_laundry_db.settlement import SETTLEMENT_ROLES, collected_by_for_shape
from nha_trang_laundry_db.storage_fees import storage_fee_for_order
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: `DEC-035`: the owner marks an account, types its limit, stops it and lifts its block.
ACCOUNT_OWNER_ROLES: Final = frozenset({StaffRole.OWNER_ADMIN})
#: Putting an order on the account and taking a payment against it: the counter's money roles.
ACCOUNT_COUNTER_ROLES: Final = SETTLEMENT_ROLES
#: Reading the account and its statements: whoever reads the customer.
ACCOUNT_READ_ROLES: Final = CUSTOMER_READ_ROLES

#: How many open charges and recent payments the account card carries; each read says when it
#: stopped (`truncated`).
ACCOUNT_OPEN_CHARGES_LIMIT: Final = 50
ACCOUNT_RECENT_PAYMENTS_LIMIT: Final = 20
#: How many charge and payment lines one statement lists; the totals are over every row.
STATEMENT_LINES_LIMIT: Final = 200


class AccountNotFoundError(LookupError):
    """No account for this customer in this store -- or another store's: the same answer."""


class AccountCustomerNotFoundError(LookupError):
    """The customer is not in this store."""


class AccountOrderNotFoundError(LookupError):
    """The order does not exist -- or is another store's, which is the same answer."""


class AccountStateError(ValueError):
    """The row moved since the caller read it (`STALE_VERSION: …`)."""


# --- the figures, as versioned SQL -------------------------------------------------------------

#: Where an account stands, summed by PostgreSQL over its two ledgers: what was charged, what was
#: paid, what is outstanding, and what is overdue -- the money charged before `%(due_upper)s` (the
#: end of the latest month whose due date has passed) that every payment so far has not covered.
#: Payments settle the oldest money first (`allocate_oldest_first`), so "charged up to then, less
#: everything paid" is exactly what of that money is still unpaid.
_STANDING_SQL: Final = """
    SELECT a.id, a.customer_id, a.store_id, a.credit_limit_vnd, a.status,
           a.overdue_block_lifted_until, a.row_version, a.opened_at, a.terms_version_id,
           totals.charged - totals.paid AS outstanding_vnd,
           greatest(totals.charged_due - totals.paid, 0) AS overdue_vnd,
           totals.charged, totals.paid
    FROM customer_accounts a
    CROSS JOIN LATERAL (
        SELECT
            (SELECT coalesce(sum(c.amount_vnd), 0) FROM customer_account_charges c
             WHERE c.account_id = a.id) AS charged,
            (SELECT coalesce(sum(p.amount_vnd), 0) FROM customer_account_payments p
             WHERE p.account_id = a.id) AS paid,
            (SELECT coalesce(sum(c.amount_vnd), 0) FROM customer_account_charges c
             WHERE c.account_id = a.id AND c.charged_at < %(due_upper)s) AS charged_due
    ) AS totals
    WHERE a.customer_id = %(customer)s AND a.store_id = %(store)s
"""

#: One calendar month's statement, in the shop's time zone: the opening balance (everything charged
#: before the month less everything paid before it), the month's charges and payments with their
#: counts, and the closing balance. Every figure is PostgreSQL's.
_STATEMENT_SQL: Final = """
    WITH bounds AS (
        SELECT (%(month)s::date)::timestamp AT TIME ZONE %(zone)s AS lower_at,
               ((%(month)s::date + INTERVAL '1 month')::date)::timestamp AT TIME ZONE %(zone)s
                   AS upper_at
    ), charged AS (
        SELECT coalesce(sum(c.amount_vnd) FILTER (WHERE c.charged_at < b.lower_at), 0) AS before,
               coalesce(sum(c.amount_vnd) FILTER (
                   WHERE c.charged_at >= b.lower_at AND c.charged_at < b.upper_at), 0) AS during,
               count(c.id) FILTER (
                   WHERE c.charged_at >= b.lower_at AND c.charged_at < b.upper_at) AS entries
        FROM bounds b
        LEFT JOIN customer_account_charges c ON c.account_id = %(account)s
    ), paid AS (
        SELECT coalesce(sum(p.amount_vnd) FILTER (WHERE p.recorded_at < b.lower_at), 0) AS before,
               coalesce(sum(p.amount_vnd) FILTER (
                   WHERE p.recorded_at >= b.lower_at AND p.recorded_at < b.upper_at), 0) AS during,
               count(p.id) FILTER (
                   WHERE p.recorded_at >= b.lower_at AND p.recorded_at < b.upper_at) AS entries
        FROM bounds b
        LEFT JOIN customer_account_payments p ON p.account_id = %(account)s
    )
    SELECT charged.before - paid.before AS opening_vnd,
           charged.during AS charges_vnd, charged.entries AS charge_count,
           paid.during AS payments_vnd, paid.entries AS payment_count,
           charged.before - paid.before + charged.during - paid.during AS closing_vnd
    FROM charged, paid
"""

#: The published version of the statement rule, stored on every frozen statement and returned with
#: every statement read (invariant 18). The time zone is hashed with it: moving the month boundary
#: would change every figure while leaving the statement word for word identical.
STATEMENT_QUERY: Final[QueryVersion] = query_version(
    "account-statement-v1", _STATEMENT_SQL, _STANDING_SQL, ACCOUNT_TIMEZONE
)

#: `DAILY-SUMMARY-001`'s accounts line (round 7 wave 2 integration): every account of the store, as
#: of an instant, split into money **due** -- on a statement already closed (charged before the
#: current month) whose due date has not passed -- and money **overdue** -- charged before the end
#: of the latest month whose due date has passed (`_due_upper`, the bound `_STANDING_SQL` uses) --
#: each less every payment the account made before the instant. Payments settle the oldest money
#: first, so "billed less paid" and "past due less paid" are exactly what of each is still unpaid
#: (the same reasoning as `_STANDING_SQL`). Counts and sums only, by PostgreSQL; no customer.
_ACCOUNTS_DUE_SQL: Final = """
    WITH per_account AS (
        SELECT
            (SELECT coalesce(sum(p.amount_vnd), 0) FROM customer_account_payments p
             WHERE p.account_id = a.id AND p.recorded_at < %(as_of)s) AS paid,
            (SELECT coalesce(sum(c.amount_vnd), 0) FROM customer_account_charges c
             WHERE c.account_id = a.id AND c.charged_at < %(billed_upper)s) AS billed,
            (SELECT coalesce(sum(c.amount_vnd), 0) FROM customer_account_charges c
             WHERE c.account_id = a.id AND c.charged_at < %(due_upper)s) AS past_due
        FROM customer_accounts a
        WHERE a.store_id = %(store)s AND a.opened_at < %(as_of)s
    ), owed AS (
        SELECT greatest(past_due - paid, 0) AS overdue_vnd,
               greatest(billed - paid, 0) - greatest(past_due - paid, 0) AS due_vnd
        FROM per_account
    )
    SELECT count(*) FILTER (WHERE due_vnd > 0), coalesce(sum(due_vnd), 0),
           count(*) FILTER (WHERE overdue_vnd > 0), coalesce(sum(overdue_vnd), 0),
           count(*)
    FROM owed
"""

#: The published version of the accounts-due rule, carried among the evening summary's sources.
ACCOUNTS_DUE_QUERY: Final[QueryVersion] = query_version(
    "accounts-due-v1", _ACCOUNTS_DUE_SQL, _STANDING_SQL, ACCOUNT_TIMEZONE
)

#: The orders on the account with money still owed, oldest charge first -- the order a payment
#: reaches them in. `remaining_vnd` is the charge less its allocations, by PostgreSQL.
_OPEN_CHARGES_SQL: Final = """
    SELECT c.id, c.order_id, c.charged_at, c.amount_vnd,
           c.amount_vnd - coalesce(alloc.total, 0) AS remaining_vnd,
           t.ticket_number, t.issued_on, c.owed_vnd
    FROM customer_account_charges c
    JOIN orders o ON o.id = c.order_id AND o.store_id = c.store_id
    LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
    LEFT JOIN LATERAL (
        SELECT sum(a.amount_vnd) AS total FROM customer_account_allocations a
        WHERE a.charge_id = c.id
    ) AS alloc ON TRUE
    WHERE c.account_id = %(account)s AND c.amount_vnd > coalesce(alloc.total, 0)
    ORDER BY c.charged_at, c.id
"""

_ORDER_FOR_ACCOUNT_SQL: Final = """
    SELECT o.store_id, o.customer_id, o.commercial_status, o.production_status, o.balance_status,
           o.fulfillment_mode, o.self_collection_recorded, o.row_version,
           r.display_total_min_vnd, r.display_total_max_vnd,
           o.current_quote_id, o.current_quote_revision, o.current_quote_snapshot_hash
    FROM orders o
    JOIN quote_revisions r
      ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
    WHERE o.id = %s
"""


# --- read models --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatementFigures:
    """One month's statement: figures by PostgreSQL, due date by the domain (`DEC-035`)."""

    month: date
    opening_vnd: int
    charges_vnd: int
    charge_count: int
    payments_vnd: int
    payment_count: int
    closing_vnd: int
    due_on: date


@dataclass(frozen=True, slots=True)
class OpenChargeView:
    charge_id: UUID
    order_id: UUID
    charged_at: datetime
    amount_vnd: int
    remaining_vnd: int
    ticket_number: int | None
    ticket_issued_on: date | None


@dataclass(frozen=True, slots=True)
class AccountPaymentView:
    payment_id: UUID
    amount_vnd: int
    method: str
    bank_ref_last: str | None
    recorded_at: datetime
    order_count: int


@dataclass(frozen=True, slots=True)
class AccountView:
    """The account card: where it stands, this month's statement, what is owed and what was paid."""

    account_id: UUID
    customer_id: UUID
    store_id: UUID
    status: AccountStatus
    credit_limit_vnd: int | None
    outstanding_vnd: int
    available_vnd: int | None
    overdue_vnd: int
    #: The statement month the overdue money was charged up to, when anything is overdue.
    overdue_month: date | None
    overdue_due_on: date | None
    overdue_block_lifted_until: datetime | None
    block_lifted: bool
    #: What a new order meets now: `ACCOUNT_SUSPENDED`, `ACCOUNT_LIMIT_UNSET`, `ACCOUNT_OVERDUE`,
    #: or `None` (the limit is then checked per order).
    handover_refusal: AccountRefusal | None
    current_statement: StatementFigures
    open_charges: tuple[OpenChargeView, ...]
    open_charges_truncated: bool
    recent_payments: tuple[AccountPaymentView, ...]
    recent_payments_truncated: bool
    opened_at: datetime
    row_version: int
    query_version: str


@dataclass(frozen=True, slots=True)
class AccountsDue:
    """The store's accounts, counted: money due on a closed statement, and money past due."""

    as_of: datetime
    accounts: int
    due_accounts: int
    due_vnd: int
    overdue_accounts: int
    overdue_vnd: int


@dataclass(frozen=True, slots=True)
class AccountRead:
    """What the customer's page shows about credit: the account, or why there is none yet."""

    customer_id: UUID
    customer_kind: CustomerKind
    terms_published: bool
    recommended_limit_vnd: int
    #: Why "Mở công nợ" would be refused now (`ACCOUNT_TERMS_UNPUBLISHED`,
    #: `ACCOUNT_REQUIRES_BUSINESS`, `CUSTOMER_ERASED`, `ACCOUNT_ALREADY_OPEN`), or `None`.
    open_refusal: AccountRefusal | None
    account: AccountView | None


@dataclass(frozen=True, slots=True)
class StatementChargeLine:
    order_id: UUID
    charged_at: datetime
    amount_vnd: int
    owed_vnd: int
    ticket_number: int | None
    ticket_issued_on: date | None


@dataclass(frozen=True, slots=True)
class FrozenStatement:
    opening_vnd: int
    charges_vnd: int
    payments_vnd: int
    closing_vnd: int
    frozen_at: datetime
    query_version: str


@dataclass(frozen=True, slots=True)
class AccountStatement:
    account_id: UUID
    customer_id: UUID
    customer_name: str | None
    figures: StatementFigures
    month_ended: bool
    frozen: FrozenStatement | None
    charges: tuple[StatementChargeLine, ...]
    charges_truncated: bool
    payments: tuple[AccountPaymentView, ...]
    payments_truncated: bool
    query_version: str


@dataclass(frozen=True, slots=True)
class OrderAccountHandover:
    """What the order page offers for an account customer's order.

    `offered` is true exactly when *Giao đồ — ghi công nợ* would be accepted now, with
    `collected_by_customer` as given; `refusal` says why not, by name, when it is false.
    """

    order_id: UUID
    customer_id: UUID
    account_id: UUID
    offered: bool
    refusal: AccountRefusal | None
    collected_by_customer: bool
    order_remaining_vnd: int | None
    outstanding_vnd: int
    credit_limit_vnd: int | None
    outstanding_after_vnd: int | None
    order_row_version: int


@dataclass(frozen=True, slots=True)
class StoredAccountCharge:
    charge_id: UUID
    order_id: UUID
    account_id: UUID
    amount_vnd: int
    outstanding_after_vnd: int
    balance_status: str
    self_collection_recorded: bool
    order_row_version: int


@dataclass(frozen=True, slots=True)
class StoredAllocation:
    order_id: UUID
    amount_vnd: int
    order_payment_id: UUID
    settled: bool
    position: int


@dataclass(frozen=True, slots=True)
class StoredAccountPayment:
    payment_id: UUID
    account_id: UUID
    amount_vnd: int
    method: str
    bank_ref_last: str | None
    recorded_at: datetime
    allocations: tuple[StoredAllocation, ...]
    outstanding_after_vnd: int
    account_row_version: int


# --- commands -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenAccountCommand:
    store_id: UUID
    customer_id: UUID
    credit_limit_vnd: int | None
    principal: StaffPrincipal
    correlation_id: UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class UpdateAccountCommand:
    """The owner's change. Only the names in `provided` apply (`credit_limit_vnd`, `status`)."""

    store_id: UUID
    customer_id: UUID
    expected_row_version: int
    provided: frozenset[str]
    credit_limit_vnd: int | None
    status: AccountStatus | None
    principal: StaffPrincipal
    correlation_id: UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class LiftBlockCommand:
    store_id: UUID
    customer_id: UUID
    expected_row_version: int
    reason: str
    #: The last local day the block stays lifted.
    until: date
    principal: StaffPrincipal
    correlation_id: UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class AccountChargeCommand:
    order_id: UUID
    expected_row_version: int
    collected_by_customer: bool
    principal: StaffPrincipal
    correlation_id: UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class AccountPaymentCommand:
    store_id: UUID
    customer_id: UUID
    expected_row_version: int
    amount_vnd: int
    method: PaymentMethod
    transfer_seen: bool
    bank_ref_last: str | None
    principal: StaffPrincipal
    correlation_id: UUID
    at: datetime


# --- the repository -----------------------------------------------------------------------------


class AccountRepository:
    """The only path to the account tables, and the one writer of `ON_ACCOUNT`."""

    # --- authorisation -------------------------------------------------------------------------

    @staticmethod
    def _require(
        cursor: Any, principal: StaffPrincipal, store_id: UUID, roles: frozenset[StaffRole]
    ) -> None:
        if not principal.roles & roles or not principal.mfa_verified:
            raise StoreAccessError("this account action is not authorized for the role")
        require_store_membership(
            cursor, staff_user_id=principal.staff_user_id, store_id=store_id, error=StoreAccessError
        )

    @staticmethod
    def authorize_read(cursor: Any, *, store_id: UUID, principal: StaffPrincipal) -> None:
        AccountRepository._require(cursor, principal, store_id, ACCOUNT_READ_ROLES)

    @staticmethod
    def authorize_owner(cursor: Any, *, store_id: UUID, principal: StaffPrincipal) -> None:
        AccountRepository._require(cursor, principal, store_id, ACCOUNT_OWNER_ROLES)

    @staticmethod
    def authorize_counter(cursor: Any, *, store_id: UUID, principal: StaffPrincipal) -> None:
        AccountRepository._require(cursor, principal, store_id, ACCOUNT_COUNTER_ROLES)

    @staticmethod
    def order_store(cursor: Any, order_id: UUID) -> UUID:
        cursor.execute("SELECT store_id FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
        if row is None:
            raise AccountOrderNotFoundError("order not found")
        return _uuid(row[0])

    # --- reads ---------------------------------------------------------------------------------

    def read(
        self,
        cursor: Any,
        *,
        store_id: UUID,
        customer_id: UUID,
        principal: StaffPrincipal,
        now: datetime,
    ) -> AccountRead:
        """The customer's credit, as the customer's page shows it."""

        self.authorize_read(cursor, store_id=store_id, principal=principal)
        kind, erased = _customer(cursor, store_id=store_id, customer_id=customer_id)
        terms = read_published_account_terms(cursor)
        account = _account_view(cursor, store_id=store_id, customer_id=customer_id, now=now)
        refusal: AccountRefusal | None = None
        if account is not None:
            refusal = AccountRefusal.ACCOUNT_ALREADY_OPEN
        elif terms is None:
            refusal = AccountRefusal.ACCOUNT_TERMS_UNPUBLISHED
        elif erased:
            refusal = AccountRefusal.CUSTOMER_ERASED
        elif kind is not CustomerKind.BUSINESS:
            refusal = AccountRefusal.ACCOUNT_REQUIRES_BUSINESS
        return AccountRead(
            customer_id=customer_id,
            customer_kind=kind,
            terms_published=terms is not None,
            recommended_limit_vnd=RECOMMENDED_STARTING_LIMIT_VND,
            open_refusal=refusal,
            account=account,
        )

    def statement(
        self,
        cursor: Any,
        *,
        store_id: UUID,
        customer_id: UUID,
        month: date,
        principal: StaffPrincipal,
        now: datetime,
    ) -> AccountStatement:
        """One month's statement, live from the ledgers, with the frozen copy when there is one."""

        self.authorize_read(cursor, store_id=store_id, principal=principal)
        cursor.execute(
            """
            SELECT a.id, c.display_name FROM customer_accounts a
            JOIN customers c ON c.id = a.customer_id AND c.store_id = a.store_id
            WHERE a.customer_id = %s AND a.store_id = %s
            """,
            (customer_id, store_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise AccountNotFoundError("no account for this customer in this store")
        account_id, name = _uuid(row[0]), row[1]
        figures = _statement_figures(cursor, account_id=account_id, month=month)
        lower, upper = month_start_instant(month), month_start_instant(next_month(month))
        cursor.execute(
            """
            SELECT c.order_id, c.charged_at, c.amount_vnd, c.owed_vnd, t.ticket_number, t.issued_on
            FROM customer_account_charges c
            JOIN orders o ON o.id = c.order_id AND o.store_id = c.store_id
            LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
            WHERE c.account_id = %s AND c.charged_at >= %s AND c.charged_at < %s
            ORDER BY c.charged_at, c.id
            LIMIT %s
            """,
            (account_id, lower, upper, STATEMENT_LINES_LIMIT + 1),
        )
        charge_rows = cursor.fetchall()
        payments, payments_truncated = _payments(
            cursor,
            account_id=account_id,
            lower=lower,
            upper=upper,
            limit=STATEMENT_LINES_LIMIT,
            newest_first=False,
        )
        cursor.execute(
            """
            SELECT opening_vnd, charges_vnd, payments_vnd, closing_vnd, frozen_at, query_version
            FROM customer_account_statements WHERE account_id = %s AND period_month = %s
            """,
            (account_id, month),
        )
        frozen_row = cursor.fetchone()
        frozen = (
            None
            if frozen_row is None
            else FrozenStatement(
                opening_vnd=int(frozen_row[0]),
                charges_vnd=int(frozen_row[1]),
                payments_vnd=int(frozen_row[2]),
                closing_vnd=int(frozen_row[3]),
                frozen_at=frozen_row[4],
                query_version=str(frozen_row[5]),
            )
        )
        return AccountStatement(
            account_id=account_id,
            customer_id=customer_id,
            customer_name=None if name is None else str(name),
            figures=figures,
            month_ended=month_has_ended(month, now),
            frozen=frozen,
            charges=tuple(
                StatementChargeLine(
                    order_id=_uuid(item[0]),
                    charged_at=item[1],
                    amount_vnd=int(item[2]),
                    owed_vnd=int(item[3]),
                    ticket_number=None if item[4] is None else int(item[4]),
                    ticket_issued_on=item[5],
                )
                for item in charge_rows[:STATEMENT_LINES_LIMIT]
            ),
            charges_truncated=len(charge_rows) > STATEMENT_LINES_LIMIT,
            payments=payments,
            payments_truncated=payments_truncated,
            query_version=STATEMENT_QUERY.label,
        )

    def accounts_due(
        self, cursor: Any, *, store_id: UUID, principal: StaffPrincipal, day: date, as_of: datetime
    ) -> AccountsDue:
        """Where the store's accounts stand at `as_of`, judged on the shop day `day`.

        `day` decides which statements are closed (charged before `day`'s month) and which are past
        due (`latest_due_month(day)`, as the overdue block decides it); `as_of` bounds the payments
        counted. For a past day the caller passes that day's end: the ledgers are append-only, so
        the figures are what they were then. Counts and sums only -- no customer, no name -- under
        the account read's gate.
        """

        _aware(as_of)
        self.authorize_read(cursor, store_id=store_id, principal=principal)
        cursor.execute(
            _ACCOUNTS_DUE_SQL,
            {
                "store": store_id,
                "as_of": as_of,
                "billed_upper": month_start_instant(date(day.year, day.month, 1)),
                "due_upper": month_start_instant(next_month(latest_due_month(day))),
            },
        )
        row = cursor.fetchone()
        assert row is not None
        return AccountsDue(
            as_of=as_of,
            accounts=int(row[4]),
            due_accounts=int(row[0]),
            due_vnd=int(row[1]),
            overdue_accounts=int(row[2]),
            overdue_vnd=int(row[3]),
        )

    def order_handover(
        self, cursor: Any, *, order_id: UUID, principal: StaffPrincipal, now: datetime
    ) -> OrderAccountHandover | None:
        """For the order page: may this order leave on its customer's account now, and if not, why.

        `None` when the order's customer has no account (or the order has no customer record):
        there is nothing to offer and nothing to explain. The same decision the charge makes under
        its locks, asked here without them -- the charge asks again.
        """

        store_id = self.order_store(cursor, order_id)
        self.authorize_read(cursor, store_id=store_id, principal=principal)
        cursor.execute(_ORDER_FOR_ACCOUNT_SQL, (order_id,))
        order = cursor.fetchone()
        if order is None:  # pragma: no cover - the store read above found it
            raise AccountOrderNotFoundError("order not found")
        if order[1] is None:
            return None
        customer_id = _uuid(order[1])
        standing_row = _standing_row(cursor, store_id=store_id, customer_id=customer_id, now=now)
        if standing_row is None:
            return None
        standing = _standing(standing_row, now)
        paid = _ledger_sum(cursor, order_id)
        # UNCLAIMED-001 (`DEC-036`): laundry that waited past the free days owes its storage fee
        # too, so the limit is measured against everything the order owes -- the same charges the
        # order page and the charge itself read (round 7 wave 2 integration).
        storage = storage_fee_for_order(cursor, order_id=order_id, moment=now)
        charges = owed_charges(
            QuotedTotal(_optional_int(order[8]), _optional_int(order[9])),
            storage_fee_vnd=storage.fee.amount_vnd,
        )
        owed = None if charges is None else owed_total(charges)
        mode = FulfillmentMode(str(order[5]))
        collected = mode not in MODES_EXPECTING_RETURN
        refusal: AccountRefusal | None
        if read_published_account_terms(cursor) is None:
            refusal = AccountRefusal.ACCOUNT_TERMS_UNPUBLISHED
        else:
            outcome = evaluate_account_charge(
                commercial=CommercialOrderStatus(str(order[2])),
                production=ProductionStatus(str(order[3])),
                balance=OrderBalanceStatus(str(order[4])),
                fulfillment_mode=mode,
                self_collection_recorded=bool(order[6]),
                owed_vnd=owed,
                paid_vnd=paid,
                collected_by_customer=collected,
                standing=standing,
            )
            refusal = outcome.refusal if isinstance(outcome, AccountChargeRefused) else None
        remaining = None if owed is None or paid > owed else owed - paid
        return OrderAccountHandover(
            order_id=order_id,
            customer_id=customer_id,
            account_id=_uuid(standing_row[0]),
            offered=refusal is None,
            refusal=refusal,
            collected_by_customer=collected,
            order_remaining_vnd=remaining,
            outstanding_vnd=standing.outstanding_vnd,
            credit_limit_vnd=standing.credit_limit_vnd,
            outstanding_after_vnd=(
                None if remaining is None else standing.outstanding_vnd + remaining
            ),
            order_row_version=int(order[7]),
        )

    # --- the owner's commands ------------------------------------------------------------------

    def open(self, connection: Any, command: OpenAccountCommand) -> tuple[UUID, int]:
        """Open an account for a BUSINESS customer, with the owner's limit or none yet."""

        _aware(command.at)
        limit = validate_limit(command.credit_limit_vnd)
        account_id = uuid4()

        def mutation(cursor: Any) -> None:
            self.authorize_owner(cursor, store_id=command.store_id, principal=command.principal)
            terms = read_published_account_terms(cursor)
            if terms is None:
                raise AccountRuleError(AccountRefusal.ACCOUNT_TERMS_UNPUBLISHED)
            cursor.execute(
                "SELECT kind, erased_at FROM customers WHERE id = %s AND store_id = %s FOR UPDATE",
                (command.customer_id, command.store_id),
            )
            customer = cursor.fetchone()
            if customer is None:
                raise AccountCustomerNotFoundError("customer is not in this store")
            if customer[1] is not None:
                raise AccountRuleError(AccountRefusal.CUSTOMER_ERASED)
            if CustomerKind(str(customer[0])) is not CustomerKind.BUSINESS:
                raise AccountRuleError(AccountRefusal.ACCOUNT_REQUIRES_BUSINESS)
            cursor.execute(
                "SELECT 1 FROM customer_accounts WHERE customer_id = %s",
                (command.customer_id,),
            )
            if cursor.fetchone() is not None:
                raise AccountRuleError(AccountRefusal.ACCOUNT_ALREADY_OPEN)
            cursor.execute(
                """
                INSERT INTO customer_accounts (
                    id, customer_id, store_id, credit_limit_vnd, status,
                    overdue_block_lifted_until, terms_version_id, opened_by_staff_id, opened_at,
                    row_version, updated_at
                ) VALUES (%s, %s, %s, %s, 'ACTIVE', NULL, %s, %s, %s, 1, %s)
                """,
                (
                    account_id,
                    command.customer_id,
                    command.store_id,
                    limit,
                    terms.version_id,
                    command.principal.staff_user_id,
                    command.at,
                    command.at,
                ),
            )

        with connection.transaction():
            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="CUSTOMER_ACCOUNT",
                    aggregate_id=account_id,
                    aggregate_version=1,
                    event_type="CUSTOMER_ACCOUNT_OPENED",
                    event_payload={
                        "account_id": str(account_id),
                        "customer_id": str(command.customer_id),
                        "store_id": str(command.store_id),
                        "credit_limit_vnd": limit,
                        "status": AccountStatus.ACTIVE.value,
                        "decision": ACCOUNT_DECISION,
                    },
                    audit_action="CUSTOMER_ACCOUNT_OPEN",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "customer.account_opened.v1",
                            {
                                "account_id": str(account_id),
                                "customer_id": str(command.customer_id),
                            },
                            f"customer-account:{account_id}:opened",
                        ),
                    ),
                    occurred_at=command.at,
                ),
                mutation,
            )
        return account_id, 1

    def update(self, connection: Any, command: UpdateAccountCommand) -> int:
        """The owner types or changes the limit, or stops or restarts the account."""

        _aware(command.at)
        if not command.provided or not command.provided <= {"credit_limit_vnd", "status"}:
            raise AccountRuleError(AccountRefusal.NOTHING_TO_CHANGE)
        if "credit_limit_vnd" in command.provided:
            # A limit, once typed, is changed to another limit. To stop credit, the owner stops the
            # account; clearing the figure would read as "no limit" to somebody one day.
            if command.credit_limit_vnd is None:
                raise AccountRuleError(AccountRefusal.ACCOUNT_LIMIT_INVALID)
            validate_limit(command.credit_limit_vnd)
        if "status" in command.provided and command.status is None:
            raise AccountRuleError(AccountRefusal.NOTHING_TO_CHANGE)
        with connection.transaction():
            with connection.cursor() as cursor:
                self.authorize_owner(cursor, store_id=command.store_id, principal=command.principal)
                current = _lock_account(
                    cursor,
                    store_id=command.store_id,
                    customer_id=command.customer_id,
                    expected_row_version=command.expected_row_version,
                )
            changed: dict[str, object] = {}
            if (
                "credit_limit_vnd" in command.provided
                and command.credit_limit_vnd != current.credit_limit_vnd
            ):
                changed["credit_limit_vnd"] = command.credit_limit_vnd
            if "status" in command.provided and command.status is not current.status:
                assert command.status is not None
                changed["status"] = command.status.value
            if not changed:
                raise AccountRuleError(AccountRefusal.NOTHING_TO_CHANGE)
            version = command.expected_row_version + 1

            def mutation(cursor: Any) -> None:
                assignments = ", ".join(f"{name} = %({name})s" for name in changed)
                cursor.execute(
                    f"""
                    UPDATE customer_accounts
                    SET {assignments}, row_version = row_version + 1,
                        updated_at = greatest(updated_at, %(at)s)
                    WHERE id = %(id)s AND row_version = %(version)s
                    RETURNING row_version
                    """,
                    {
                        **changed,
                        "at": command.at,
                        "id": current.account_id,
                        "version": command.expected_row_version,
                    },
                )
                if cursor.fetchone() is None:  # pragma: no cover - the row is locked
                    raise AccountStateError("STALE_VERSION: account changed during the update")

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="CUSTOMER_ACCOUNT",
                    aggregate_id=current.account_id,
                    aggregate_version=version,
                    event_type="CUSTOMER_ACCOUNT_UPDATED",
                    event_payload={
                        "account_id": str(current.account_id),
                        "changed": sorted(changed),
                        **changed,
                        "decision": ACCOUNT_DECISION,
                    },
                    audit_action="CUSTOMER_ACCOUNT_UPDATE",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "customer.account_updated.v1",
                            {"account_id": str(current.account_id), "changed": sorted(changed)},
                            f"customer-account:{current.account_id}:version:{version}",
                        ),
                    ),
                    occurred_at=command.at,
                ),
                mutation,
            )
        return version

    def lift_block(self, connection: Any, command: LiftBlockCommand) -> tuple[UUID, datetime, int]:
        """The owner lifts the overdue block through a local day, with a reason; every lift stays"""

        _aware(command.at)
        reason = clean_lift_reason(command.reason)
        until = lift_until_instant(command.until, today=local_day(command.at))
        lift_id = uuid4()
        with connection.transaction():
            with connection.cursor() as cursor:
                self.authorize_owner(cursor, store_id=command.store_id, principal=command.principal)
                current = _lock_account(
                    cursor,
                    store_id=command.store_id,
                    customer_id=command.customer_id,
                    expected_row_version=command.expected_row_version,
                )
            version = command.expected_row_version + 1

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO customer_account_block_lifts (
                        id, account_id, store_id, reason, lifted_until, lifted_by_staff_id,
                        lifted_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        lift_id,
                        current.account_id,
                        command.store_id,
                        reason,
                        until,
                        command.principal.staff_user_id,
                        command.at,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE customer_accounts
                    SET overdue_block_lifted_until = %s, row_version = row_version + 1,
                        updated_at = greatest(updated_at, %s)
                    WHERE id = %s AND row_version = %s
                    """,
                    (until, command.at, current.account_id, command.expected_row_version),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="CUSTOMER_ACCOUNT",
                    aggregate_id=current.account_id,
                    aggregate_version=version,
                    event_type="CUSTOMER_ACCOUNT_BLOCK_LIFTED",
                    # The reason is on the lift row only: free text about a customer.
                    event_payload={
                        "account_id": str(current.account_id),
                        "lift_id": str(lift_id),
                        "lifted_until": until.isoformat(),
                        "decision": ACCOUNT_DECISION,
                    },
                    audit_action="CUSTOMER_ACCOUNT_LIFT_BLOCK",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "customer.account_block_lifted.v1",
                            {"account_id": str(current.account_id), "lift_id": str(lift_id)},
                            f"customer-account:{current.account_id}:lift:{lift_id}",
                        ),
                    ),
                    occurred_at=command.at,
                    audit_details={"reason_recorded": True},
                ),
                mutation,
            )
        return lift_id, until, version

    # --- the counter's commands ----------------------------------------------------------------

    def charge(self, connection: Any, command: AccountChargeCommand) -> StoredAccountCharge:
        """*Giao đồ — ghi công nợ*: the order's finished goods leave, what it owes goes on account.

        Under the account's row lock, then the order's: the domain decides again on what is read
        there (`evaluate_account_charge` → `settlement.goods_may_leave` with the account facts), and
        the charge row, the order's balance (`ON_ACCOUNT`), its collection flag, the account's
        version, the event, the audit row and the outbox row commit together or not at all.
        """

        _aware(command.at)
        charge_id = uuid4()
        with connection.transaction():
            with connection.cursor() as cursor:
                if (
                    not command.principal.roles & ACCOUNT_COUNTER_ROLES
                    or not command.principal.mfa_verified
                ):
                    raise StoreAccessError(
                        "putting an order on account requires an operations role"
                    )
                cursor.execute(
                    "SELECT store_id, customer_id FROM orders WHERE id = %s", (command.order_id,)
                )
                located = cursor.fetchone()
                if located is None:
                    raise AccountOrderNotFoundError("order not found")
                store_id = _uuid(located[0])
                self.authorize_counter(cursor, store_id=store_id, principal=command.principal)
                if located[1] is None:
                    raise AccountRuleError(AccountRefusal.NOT_AN_ACCOUNT_CUSTOMER)
                customer_id = _uuid(located[1])
                if read_published_account_terms(cursor) is None:
                    raise AccountRuleError(AccountRefusal.ACCOUNT_TERMS_UNPUBLISHED)
                # The account first, then the order: the lock order every account command keeps.
                cursor.execute(
                    "SELECT id FROM customer_accounts WHERE customer_id = %s AND store_id = %s "
                    "FOR UPDATE",
                    (customer_id, store_id),
                )
                if cursor.fetchone() is None:
                    raise AccountRuleError(AccountRefusal.NOT_AN_ACCOUNT_CUSTOMER)
                cursor.execute(_ORDER_FOR_ACCOUNT_SQL + " FOR UPDATE OF o", (command.order_id,))
                order = cursor.fetchone()
                if order is None:  # pragma: no cover - read above
                    raise AccountOrderNotFoundError("order not found")
                if int(order[7]) != command.expected_row_version:
                    raise AccountStateError(
                        "STALE_VERSION: order changed since it was read; read it again"
                    )
                standing_row = _standing_row(
                    cursor, store_id=store_id, customer_id=customer_id, now=command.at
                )
                assert standing_row is not None
                standing = _standing(standing_row, command.at)
                account_id = _uuid(standing_row[0])
                account_version = int(standing_row[6])
                paid = _ledger_sum(cursor, command.order_id)
                # UNCLAIMED-001 (`DEC-036`), round 7 wave 2 integration: the storage fee the order
                # owes at the moment it leaves, under the order lock held above. The goods leave
                # now, so the fee stops now: it is fixed with this charge (`0061`), and the account
                # is charged the quoted total plus the fee, less what was already paid.
                storage = storage_fee_for_order(
                    cursor, order_id=command.order_id, moment=command.at
                )
            storage_fee_vnd = storage.fee.amount_vnd
            charges = owed_charges(
                QuotedTotal(_optional_int(order[8]), _optional_int(order[9])),
                storage_fee_vnd=storage_fee_vnd,
            )
            outcome = evaluate_account_charge(
                commercial=CommercialOrderStatus(str(order[2])),
                production=ProductionStatus(str(order[3])),
                balance=OrderBalanceStatus(str(order[4])),
                fulfillment_mode=FulfillmentMode(str(order[5])),
                self_collection_recorded=bool(order[6]),
                owed_vnd=None if charges is None else owed_total(charges),
                paid_vnd=paid,
                collected_by_customer=command.collected_by_customer,
                standing=standing,
            )
            if isinstance(outcome, AccountChargeRefused):
                raise AccountRuleError(outcome.refusal)
            moved: list[tuple[int, bool]] = []

            def mutation(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO customer_account_charges (
                        id, account_id, customer_id, store_id, order_id, owed_vnd,
                        paid_before_vnd, amount_vnd, collected_by_customer, charged_by_staff_id,
                        charged_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        charge_id,
                        account_id,
                        customer_id,
                        store_id,
                        command.order_id,
                        outcome.owed_vnd,
                        outcome.paid_before_vnd,
                        outcome.amount_vnd,
                        outcome.collected_by_customer,
                        command.principal.staff_user_id,
                        command.at,
                    ),
                )
                if storage_fee_vnd > 0:
                    # The fee this charge includes, fixed now and never again (`0061` checks at
                    # commit that the charge owes exactly the quoted total plus this amount).
                    trace = storage.fee.fee
                    assert trace is not None and storage.published is not None
                    cursor.execute(
                        """
                        INSERT INTO order_storage_fees (
                            id, order_id, store_id, settlement_id, account_charge_id, amount_vnd,
                            days_waiting, chargeable_days, policy_version_id, fixed_by_staff_id,
                            fixed_at
                        ) VALUES (%s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            uuid4(),
                            command.order_id,
                            store_id,
                            charge_id,
                            storage_fee_vnd,
                            trace.days_waiting,
                            trace.chargeable_days,
                            storage.published.version_id,
                            command.principal.staff_user_id,
                            command.at,
                        ),
                    )
                cursor.execute(
                    """
                    UPDATE orders
                    SET balance_status = 'ON_ACCOUNT',
                        self_collection_recorded = self_collection_recorded OR %s,
                        row_version = row_version + 1
                    WHERE id = %s AND row_version = %s AND balance_status = %s
                    RETURNING row_version, self_collection_recorded
                    """,
                    (
                        outcome.collected_by_customer,
                        command.order_id,
                        command.expected_row_version,
                        str(order[4]),
                    ),
                )
                result = cursor.fetchone()
                if result is None:  # pragma: no cover - the row is locked
                    raise AccountStateError("STALE_VERSION: order changed during the charge")
                moved.append((int(result[0]), bool(result[1])))
                cursor.execute(
                    """
                    UPDATE customer_accounts
                    SET row_version = row_version + 1, updated_at = greatest(updated_at, %s)
                    WHERE id = %s AND row_version = %s
                    """,
                    (command.at, account_id, account_version),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    # On the order, so the charge is on the order's own audit timeline.
                    aggregate_type="ORDER_ACCOUNT_CHARGE",
                    aggregate_id=command.order_id,
                    aggregate_version=1,
                    event_type="ORDER_ACCOUNT_CHARGED",
                    event_payload={
                        "charge_id": str(charge_id),
                        "account_id": str(account_id),
                        "customer_id": str(customer_id),
                        "amount_vnd": outcome.amount_vnd,
                        "owed_vnd": outcome.owed_vnd,
                        "paid_before_vnd": outcome.paid_before_vnd,
                        "outstanding_after_vnd": outcome.outstanding_after_vnd,
                        "credit_limit_vnd": standing.credit_limit_vnd,
                        "collected_by_customer": outcome.collected_by_customer,
                        "balance_status": OrderBalanceStatus.ON_ACCOUNT.value,
                        "decision": ACCOUNT_DECISION,
                        # UNCLAIMED-001: present only when a storage fee is part of the charge.
                        **({"storage_fee_vnd": storage_fee_vnd} if storage_fee_vnd > 0 else {}),
                    },
                    audit_action="ORDER_ACCOUNT_CHARGE",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "order.account_charged.v1",
                            {
                                "order_id": str(command.order_id),
                                "account_id": str(account_id),
                                "charge_id": str(charge_id),
                            },
                            f"order:{command.order_id}:account-charge",
                        ),
                    ),
                    occurred_at=command.at,
                ),
                mutation,
            )
        version, collected = moved[0]
        return StoredAccountCharge(
            charge_id=charge_id,
            order_id=command.order_id,
            account_id=account_id,
            amount_vnd=outcome.amount_vnd,
            outstanding_after_vnd=outcome.outstanding_after_vnd,
            balance_status=OrderBalanceStatus.ON_ACCOUNT.value,
            self_collection_recorded=collected,
            order_row_version=version,
        )

    def record_payment(
        self, connection: Any, command: AccountPaymentCommand
    ) -> StoredAccountPayment:
        """*Thu công nợ*: one payment, allocated oldest first, each order's balance moved with it.

        Under the account's row lock, then each order's in turn. The account payment row, its
        allocation rows, one `order_payments` row per order reached (and, for each order it pays in
        full, the settlement row and the balance `PAID`), the events, audit rows and outbox rows all
        commit together. `0059` checks at commit that the allocations are exactly their payments and
        sum to the account payment.
        """

        _aware(command.at)
        bank_ref = normalise_bank_ref(command.bank_ref_last)
        payment_id = uuid4()
        with connection.transaction():
            with connection.cursor() as cursor:
                self.authorize_counter(
                    cursor, store_id=command.store_id, principal=command.principal
                )
                current = _lock_account(
                    cursor,
                    store_id=command.store_id,
                    customer_id=command.customer_id,
                    expected_row_version=command.expected_row_version,
                )
                standing_row = _standing_row(
                    cursor,
                    store_id=command.store_id,
                    customer_id=command.customer_id,
                    now=command.at,
                )
                assert standing_row is not None
                outstanding = int(standing_row[9])
                refusal = account_payment_refusal(
                    outstanding_vnd=outstanding,
                    amount_vnd=command.amount_vnd,
                    method=command.method,
                    transfer_seen=command.transfer_seen,
                    bank_ref_last=bank_ref,
                )
                if refusal is not None:
                    raise AccountPaymentRefused(refusal.value)
                cursor.execute(_OPEN_CHARGES_SQL, {"account": current.account_id})
                open_rows = cursor.fetchall()
            allocations = allocate_oldest_first(
                command.amount_vnd,
                tuple(OpenCharge(_uuid(row[0]), _uuid(row[1]), int(row[4])) for row in open_rows),
            )
            owed_by_charge = {_uuid(row[0]): int(row[7]) for row in open_rows}
            version = command.expected_row_version + 1

            def header(cursor: Any) -> None:
                cursor.execute(
                    """
                    INSERT INTO customer_account_payments (
                        id, account_id, customer_id, store_id, amount_vnd, method, bank_ref_last,
                        recorded_by_staff_id, recorded_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        payment_id,
                        current.account_id,
                        command.customer_id,
                        command.store_id,
                        command.amount_vnd,
                        command.method.value,
                        bank_ref,
                        command.principal.staff_user_id,
                        command.at,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE customer_accounts
                    SET row_version = row_version + 1, updated_at = greatest(updated_at, %s)
                    WHERE id = %s AND row_version = %s
                    """,
                    (command.at, current.account_id, command.expected_row_version),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="CUSTOMER_ACCOUNT",
                    aggregate_id=current.account_id,
                    aggregate_version=version,
                    event_type="CUSTOMER_ACCOUNT_PAYMENT_RECORDED",
                    event_payload={
                        "account_id": str(current.account_id),
                        "account_payment_id": str(payment_id),
                        "amount_vnd": command.amount_vnd,
                        "method": command.method.value,
                        "outstanding_after_vnd": outstanding - command.amount_vnd,
                        "allocations": [
                            {"order_id": str(item.order_id), "amount_vnd": item.amount_vnd}
                            for item in allocations
                        ],
                        "decision": ACCOUNT_DECISION,
                    },
                    audit_action="CUSTOMER_ACCOUNT_PAYMENT_RECORD",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "customer.account_payment_recorded.v1",
                            {
                                "account_id": str(current.account_id),
                                "account_payment_id": str(payment_id),
                            },
                            f"customer-account-payment:{payment_id}",
                        ),
                    ),
                    occurred_at=command.at,
                ),
                header,
            )
            stored: list[StoredAllocation] = []
            for position, item in enumerate(allocations, start=1):
                stored.append(
                    _allocate(
                        connection,
                        account_id=current.account_id,
                        payment_id=payment_id,
                        position=position,
                        charge_id=_uuid(item.charge_id),
                        order_id=_uuid(item.order_id),
                        amount_vnd=item.amount_vnd,
                        charge_owed_vnd=owed_by_charge[_uuid(item.charge_id)],
                        method=command.method,
                        bank_ref=bank_ref,
                        principal=command.principal,
                        correlation_id=command.correlation_id,
                        at=command.at,
                    )
                )
        return StoredAccountPayment(
            payment_id=payment_id,
            account_id=current.account_id,
            amount_vnd=command.amount_vnd,
            method=command.method.value,
            bank_ref_last=bank_ref,
            recorded_at=command.at,
            allocations=tuple(stored),
            outstanding_after_vnd=outstanding - command.amount_vnd,
            account_row_version=version,
        )


class AccountPaymentRefused(ValueError):
    """A payment against the account the counter's rules refuse (`PaymentRefusal` codes)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- month close ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FreezeOutcome:
    account_id: UUID
    month: date
    figures: StatementFigures
    #: False when the month was already frozen for the account: nothing was written.
    created: bool


def freeze_statements(
    connection: Any,
    *,
    actor_id: UUID,
    month: date,
    now: datetime,
    store_id: UUID | None = None,
) -> tuple[FreezeOutcome, ...]:
    """Freeze `month`'s statement for every account (or one store's), as the statement SQL reads it.

    The owner's month close (`scripts/close_account_statements.py`). Refused until the month has
    ended in the shop's time zone (`MONTH_NOT_ENDED`); an account already frozen for the month is
    left as it is. Each frozen statement commits with its event, audit and outbox rows.
    """

    _aware(now)
    if not month_has_ended(month, now):
        raise AccountRuleError(AccountRefusal.MONTH_NOT_ENDED)
    with connection.cursor() as cursor:
        _require_active_owner(cursor, actor_id)
        # Every account open by the month's end, or with a charge or a payment in or before it.
        end = month_start_instant(next_month(month))
        cursor.execute(
            """
            SELECT a.id, a.store_id FROM customer_accounts a
            WHERE (%(store)s::uuid IS NULL OR a.store_id = %(store)s::uuid)
              AND (
                  a.opened_at < %(end)s
                  OR EXISTS (SELECT 1 FROM customer_account_charges c
                             WHERE c.account_id = a.id AND c.charged_at < %(end)s)
                  OR EXISTS (SELECT 1 FROM customer_account_payments p
                             WHERE p.account_id = a.id AND p.recorded_at < %(end)s)
              )
            ORDER BY a.store_id, a.opened_at, a.id
            """,
            {"store": store_id, "end": end},
        )
        accounts = [(_uuid(row[0]), _uuid(row[1])) for row in cursor.fetchall()]
    outcomes: list[FreezeOutcome] = []
    for account_id, account_store in accounts:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM customer_accounts WHERE id = %s FOR UPDATE", (account_id,)
                )
                cursor.execute(
                    "SELECT 1 FROM customer_account_statements "
                    "WHERE account_id = %s AND period_month = %s",
                    (account_id, month),
                )
                exists = cursor.fetchone() is not None
                figures = _statement_figures(cursor, account_id=account_id, month=month)
            if exists:
                outcomes.append(FreezeOutcome(account_id, month, figures, False))
                continue
            statement_id = uuid4()

            def mutation(
                cursor: Any,
                statement_id: UUID = statement_id,
                account_id: UUID = account_id,
                account_store: UUID = account_store,
                figures: StatementFigures = figures,
            ) -> None:
                cursor.execute(
                    """
                    INSERT INTO customer_account_statements (
                        id, account_id, store_id, period_month, opening_vnd, charges_vnd,
                        payments_vnd, closing_vnd, charge_count, payment_count, due_on,
                        query_version, frozen_by_staff_id, frozen_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        statement_id,
                        account_id,
                        account_store,
                        month,
                        figures.opening_vnd,
                        figures.charges_vnd,
                        figures.payments_vnd,
                        figures.closing_vnd,
                        figures.charge_count,
                        figures.payment_count,
                        figures.due_on,
                        STATEMENT_QUERY.label,
                        actor_id,
                        now,
                    ),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="CUSTOMER_ACCOUNT_STATEMENT",
                    aggregate_id=statement_id,
                    aggregate_version=1,
                    event_type="CUSTOMER_ACCOUNT_STATEMENT_FROZEN",
                    event_payload={
                        "account_id": str(account_id),
                        "month": month_label(month),
                        "opening_vnd": figures.opening_vnd,
                        "charges_vnd": figures.charges_vnd,
                        "payments_vnd": figures.payments_vnd,
                        "closing_vnd": figures.closing_vnd,
                        "due_on": figures.due_on.isoformat(),
                        "query_version": STATEMENT_QUERY.label,
                    },
                    audit_action="CUSTOMER_ACCOUNT_STATEMENT_FREEZE",
                    actor_type="STAFF",
                    actor_id=actor_id,
                    correlation_id=uuid4(),
                    outbox_events=(
                        OutboxEvent(
                            "customer.account_statement_frozen.v1",
                            {"account_id": str(account_id), "month": month_label(month)},
                            f"customer-account:{account_id}:statement:{month_label(month)}",
                        ),
                    ),
                    occurred_at=now,
                ),
                mutation,
            )
            outcomes.append(FreezeOutcome(account_id, month, figures, True))
    return tuple(outcomes)


# --- helpers -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LockedAccount:
    account_id: UUID
    credit_limit_vnd: int | None
    status: AccountStatus


def _lock_account(
    cursor: Any, *, store_id: UUID, customer_id: UUID, expected_row_version: int
) -> _LockedAccount:
    cursor.execute(
        """
        SELECT id, credit_limit_vnd, status, row_version FROM customer_accounts
        WHERE customer_id = %s AND store_id = %s
        FOR UPDATE
        """,
        (customer_id, store_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise AccountNotFoundError("no account for this customer in this store")
    if int(row[3]) != expected_row_version:
        raise AccountStateError("STALE_VERSION: account changed since it was read; read it again")
    return _LockedAccount(_uuid(row[0]), _optional_int(row[1]), AccountStatus(str(row[2])))


def _due_upper(now: datetime) -> datetime:
    """The end of the latest statement month whose due date has passed, as an instant."""

    return month_start_instant(next_month(latest_due_month(local_day(now))))


def _standing_row(
    cursor: Any, *, store_id: UUID, customer_id: UUID, now: datetime
) -> tuple[Any, ...] | None:
    cursor.execute(
        _STANDING_SQL,
        {"customer": customer_id, "store": store_id, "due_upper": _due_upper(now)},
    )
    row = cursor.fetchone()
    return None if row is None else tuple(row)


def _standing(row: tuple[Any, ...], now: datetime) -> AccountStanding:
    return AccountStanding(
        status=AccountStatus(str(row[4])),
        credit_limit_vnd=_optional_int(row[3]),
        outstanding_vnd=int(row[9]),
        overdue_vnd=int(row[10]),
        overdue_block_lifted_until=row[5],
        as_of=now,
    )


def _account_view(
    cursor: Any, *, store_id: UUID, customer_id: UUID, now: datetime
) -> AccountView | None:
    row = _standing_row(cursor, store_id=store_id, customer_id=customer_id, now=now)
    if row is None:
        return None
    standing = _standing(row, now)
    account_id = _uuid(row[0])
    current_month = statement_month(now)
    figures = _statement_figures(cursor, account_id=account_id, month=current_month)
    cursor.execute(
        _OPEN_CHARGES_SQL + " LIMIT %(limit)s",
        {
            "account": account_id,
            "limit": ACCOUNT_OPEN_CHARGES_LIMIT + 1,
        },
    )
    open_rows = cursor.fetchall()
    payments, payments_truncated = _payments(
        cursor,
        account_id=account_id,
        lower=None,
        upper=None,
        limit=ACCOUNT_RECENT_PAYMENTS_LIMIT,
        newest_first=True,
    )
    overdue_month = latest_due_month(local_day(now)) if standing.overdue else None
    # What a new order meets now, before its own amount is known: the account-level refusals only.
    probe = account_handover_refusal(AccountHandoverFacts(standing=standing, order_remaining_vnd=0))
    return AccountView(
        account_id=account_id,
        customer_id=_uuid(row[1]),
        store_id=_uuid(row[2]),
        status=standing.status,
        credit_limit_vnd=standing.credit_limit_vnd,
        outstanding_vnd=standing.outstanding_vnd,
        available_vnd=standing.available_vnd,
        overdue_vnd=standing.overdue_vnd,
        overdue_month=overdue_month,
        overdue_due_on=None if overdue_month is None else statement_due_on(overdue_month),
        overdue_block_lifted_until=standing.overdue_block_lifted_until,
        block_lifted=standing.block_lifted,
        handover_refusal=None if probe is AccountRefusal.ACCOUNT_LIMIT_EXCEEDED else probe,
        current_statement=figures,
        open_charges=tuple(
            OpenChargeView(
                charge_id=_uuid(item[0]),
                order_id=_uuid(item[1]),
                charged_at=item[2],
                amount_vnd=int(item[3]),
                remaining_vnd=int(item[4]),
                ticket_number=None if item[5] is None else int(item[5]),
                ticket_issued_on=item[6],
            )
            for item in open_rows[:ACCOUNT_OPEN_CHARGES_LIMIT]
        ),
        open_charges_truncated=len(open_rows) > ACCOUNT_OPEN_CHARGES_LIMIT,
        recent_payments=payments,
        recent_payments_truncated=payments_truncated,
        opened_at=row[7],
        row_version=int(row[6]),
        query_version=STATEMENT_QUERY.label,
    )


def _statement_figures(cursor: Any, *, account_id: UUID, month: date) -> StatementFigures:
    cursor.execute(
        _STATEMENT_SQL, {"month": month, "zone": ACCOUNT_TIMEZONE, "account": account_id}
    )
    row = cursor.fetchone()
    assert row is not None
    return StatementFigures(
        month=month,
        opening_vnd=int(row[0]),
        charges_vnd=int(row[1]),
        charge_count=int(row[2]),
        payments_vnd=int(row[3]),
        payment_count=int(row[4]),
        closing_vnd=int(row[5]),
        due_on=statement_due_on(month),
    )


def _payments(
    cursor: Any,
    *,
    account_id: UUID,
    lower: datetime | None,
    upper: datetime | None,
    limit: int,
    newest_first: bool,
) -> tuple[tuple[AccountPaymentView, ...], bool]:
    direction = "DESC" if newest_first else "ASC"
    cursor.execute(
        f"""
        SELECT p.id, p.amount_vnd, p.method, p.bank_ref_last, p.recorded_at,
               (SELECT count(*) FROM customer_account_allocations a
                WHERE a.account_payment_id = p.id)
        FROM customer_account_payments p
        WHERE p.account_id = %(account)s
          AND (%(lower)s::timestamptz IS NULL OR p.recorded_at >= %(lower)s::timestamptz)
          AND (%(upper)s::timestamptz IS NULL OR p.recorded_at < %(upper)s::timestamptz)
        ORDER BY p.recorded_at {direction}, p.id {direction}
        LIMIT %(limit)s
        """,
        {"account": account_id, "lower": lower, "upper": upper, "limit": limit + 1},
    )
    rows = cursor.fetchall()
    return (
        tuple(
            AccountPaymentView(
                payment_id=_uuid(row[0]),
                amount_vnd=int(row[1]),
                method=str(row[2]),
                bank_ref_last=None if row[3] is None else str(row[3]),
                recorded_at=row[4],
                order_count=int(row[5]),
            )
            for row in rows[:limit]
        ),
        len(rows) > limit,
    )


def _allocate(
    connection: Any,
    *,
    account_id: UUID,
    payment_id: UUID,
    position: int,
    charge_id: UUID,
    order_id: UUID,
    amount_vnd: int,
    charge_owed_vnd: int,
    method: PaymentMethod,
    bank_ref: str | None,
    principal: StaffPrincipal,
    correlation_id: UUID,
    at: datetime,
) -> StoredAllocation:
    """One order's share of an account payment, written as the counter's payment path writes one.

    The order is locked, its ledger summed by PostgreSQL, and -- when this share covers the charge
    -- the settlement's shape decided by `evaluate_settlement` exactly as for any settling payment.
    """

    with connection.cursor() as cursor:
        cursor.execute(_ORDER_FOR_ACCOUNT_SQL + " FOR UPDATE OF o", (order_id,))
        order = cursor.fetchone()
        assert order is not None
        cursor.execute(
            "SELECT coalesce(sum(amount_vnd), 0), count(*) FROM order_payments WHERE order_id = %s",
            (order_id,),
        )
        ledger = cursor.fetchone()
        # UNCLAIMED-001: the storage fee fixed with this charge (`0061`), 0 when none. The charge
        # owes the quoted total plus it; the settlement's shape is decided over the quoted total.
        cursor.execute(
            "SELECT coalesce(sum(amount_vnd), 0) FROM order_storage_fees "
            "WHERE account_charge_id = %s",
            (charge_id,),
        )
        fee_row = cursor.fetchone()
    charge_fee_vnd = int(fee_row[0])
    paid_before, entries = int(ledger[0]), int(ledger[1])
    balance = OrderBalanceStatus(str(order[4]))
    if balance is not OrderBalanceStatus.ON_ACCOUNT:
        raise AccountStateError("STALE_VERSION: an order on the account is no longer on it")
    paid_after = paid_before + amount_vnd
    if paid_after > charge_owed_vnd:  # pragma: no cover - the allocation is bounded by the charge
        raise AccountStateError("an account payment would pay an order past what it owed")
    settles = paid_after == charge_owed_vnd
    settlement_id: UUID | None = None
    shape: SettlementShape | None = None
    quoted = QuotedTotal(_optional_int(order[8]), _optional_int(order[9]))
    if settles:
        settled = evaluate_settlement(
            quoted=quoted,
            tendered_vnd=charge_owed_vnd - charge_fee_vnd,
            collected_by_customer=False,
            fulfillment_mode=FulfillmentMode(str(order[5])),
            on_account=True,
        )
        if isinstance(settled, SettlementNotSupported):  # pragma: no cover - the charge checked it
            raise AccountStateError(f"the settling allocation is refused: {settled.reason_code}")
        settlement_id = uuid4()
        shape = settled.shape
    shape_value = None if shape is None else shape.value
    order_payment_id = uuid4()
    ordinal = entries + 1
    store_id = _uuid(order[0])

    def mutation(cursor: Any) -> None:
        if settlement_id is not None and shape is not None:
            cursor.execute(
                """
                INSERT INTO order_settlements (
                    id, order_id, store_id, settled_quote_id, settled_quote_revision,
                    settled_quote_snapshot_hash, expected_total_vnd, paid_amount_vnd,
                    settlement_shape, collected_by, attested_by_staff_id, attested_at, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    settlement_id,
                    order_id,
                    store_id,
                    _uuid(order[10]),
                    int(order[11]),
                    str(order[12]),
                    charge_owed_vnd,
                    paid_after,
                    shape_value,
                    collected_by_for_shape(shape),
                    principal.staff_user_id,
                    at,
                    at,
                ),
            )
        cursor.execute(
            """
            INSERT INTO order_payments (
                id, order_id, store_id, amount_vnd, method, bank_ref_last, legacy, settlement_id,
                recorded_by_staff_id, recorded_at, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s, %s)
            """,
            (
                order_payment_id,
                order_id,
                store_id,
                amount_vnd,
                method.value,
                bank_ref,
                settlement_id,
                principal.staff_user_id,
                at,
                at,
            ),
        )
        cursor.execute(
            """
            INSERT INTO customer_account_allocations (
                id, account_payment_id, account_id, charge_id, order_id, order_payment_id,
                amount_vnd, position
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid4(),
                payment_id,
                account_id,
                charge_id,
                order_id,
                order_payment_id,
                amount_vnd,
                position,
            ),
        )
        if settles:
            # The one update a completed order admits (`0059`): its balance, and its version.
            cursor.execute(
                """
                UPDATE orders SET balance_status = 'PAID', row_version = row_version + 1
                WHERE id = %s AND balance_status = 'ON_ACCOUNT'
                """,
                (order_id,),
            )

    outbox = [
        OutboxEvent(
            "order.payment_recorded.v1",
            {"order_id": str(order_id), "payment_id": str(order_payment_id)},
            f"order:{order_id}:payment:{ordinal}",
        )
    ]
    if settlement_id is not None:
        outbox.append(
            OutboxEvent(
                "order.settlement_recorded.v1",
                {"order_id": str(order_id), "settlement_id": str(settlement_id)},
                f"order:{order_id}:settlement",
            )
        )
    commit_material_change(
        connection,
        MaterialChange(
            aggregate_type="ORDER_PAYMENT",
            aggregate_id=order_id,
            aggregate_version=ordinal,
            event_type="ORDER_PAYMENT_RECORDED",
            event_payload={
                "payment_id": str(order_payment_id),
                "amount_vnd": amount_vnd,
                "method": method.value,
                "balance_status": (
                    OrderBalanceStatus.PAID if settles else OrderBalanceStatus.ON_ACCOUNT
                ).value,
                "paid_vnd": paid_after,
                "owed_vnd": charge_owed_vnd,
                "collected_by_customer": False,
                "settlement_id": None if settlement_id is None else str(settlement_id),
                "settlement_shape": shape_value,
                "account_payment_id": str(payment_id),
                "account_id": str(account_id),
            },
            audit_action="ORDER_PAYMENT_RECORD",
            actor_type="STAFF",
            actor_id=principal.staff_user_id,
            correlation_id=correlation_id,
            outbox_events=tuple(outbox),
            occurred_at=at,
        ),
        mutation,
    )
    return StoredAllocation(
        order_id=order_id,
        amount_vnd=amount_vnd,
        order_payment_id=order_payment_id,
        settled=settles,
        position=position,
    )


def _customer(cursor: Any, *, store_id: UUID, customer_id: UUID) -> tuple[CustomerKind, bool]:
    cursor.execute(
        "SELECT kind, erased_at FROM customers WHERE id = %s AND store_id = %s",
        (customer_id, store_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise AccountCustomerNotFoundError("customer is not in this store")
    return CustomerKind(str(row[0])), row[1] is not None


def _ledger_sum(cursor: Any, order_id: UUID) -> int:
    cursor.execute(
        "SELECT coalesce(sum(amount_vnd), 0) FROM order_payments WHERE order_id = %s", (order_id,)
    )
    row = cursor.fetchone()
    return int(row[0])


def _require_active_owner(cursor: Any, actor_id: UUID) -> None:
    cursor.execute(
        """
        SELECT 1
        FROM staff_users u
        JOIN staff_role_assignments r ON r.staff_user_id = u.id
        WHERE u.id = %s AND u.status = 'ACTIVE' AND r.role = %s AND r.revoked_at IS NULL
        """,
        (actor_id, StaffRole.OWNER_ADMIN.value),
    )
    if cursor.fetchone() is None:
        raise StoreAccessError("only an active OWNER_ADMIN may close a statement month")


def _aware(moment: datetime) -> None:
    if moment.tzinfo is None:
        raise ValueError("account instants must be timezone-aware")


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


__all__ = [
    "ACCOUNTS_DUE_QUERY",
    "ACCOUNT_COUNTER_ROLES",
    "ACCOUNT_OPEN_CHARGES_LIMIT",
    "ACCOUNT_OWNER_ROLES",
    "ACCOUNT_READ_ROLES",
    "ACCOUNT_RECENT_PAYMENTS_LIMIT",
    "STATEMENT_LINES_LIMIT",
    "STATEMENT_QUERY",
    "AccountChargeCommand",
    "AccountCustomerNotFoundError",
    "AccountNotFoundError",
    "AccountOrderNotFoundError",
    "AccountPaymentCommand",
    "AccountPaymentRefused",
    "AccountPaymentView",
    "AccountRead",
    "AccountRepository",
    "AccountStateError",
    "AccountStatement",
    "AccountView",
    "AccountsDue",
    "FreezeOutcome",
    "FrozenStatement",
    "LiftBlockCommand",
    "OpenAccountCommand",
    "OpenChargeView",
    "OrderAccountHandover",
    "StatementChargeLine",
    "StatementFigures",
    "StoredAccountCharge",
    "StoredAccountPayment",
    "StoredAllocation",
    "UpdateAccountCommand",
    "freeze_statements",
]
