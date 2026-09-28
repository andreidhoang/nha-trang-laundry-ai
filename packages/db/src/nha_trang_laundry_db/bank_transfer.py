"""The shop's bank account, and the exact VietQR for what an order or an account month owes.

`VIETQR-001` (`DEC-041`). The account is a configuration version like the storage policy and the
account terms: an immutable, hashed document in `configuration_versions`, published by
`scripts/publish_bank_account.py` -- a script the owner runs after paying a 1.000 ₫ test QR from
their own phone, never a seed a process applies on boot. Only an active `OWNER_ADMIN` may publish
it, because it decides where every customer's transfer goes. Until it is published every QR refuses
`BANK_ACCOUNT_UNPUBLISHED` and the counter works as it did; publishing the withdrawal returns a shop
to that state.

**The amount is read, never stored.** An order's QR asks for the payment ledger's remaining balance
(`OrderView.remaining_vnd`, the domain's `payment_position` over the charges -- storage fee
included once accrued), read by the same order read the page uses, in the same request. An account
month's asks for what of that statement is still unpaid: everything charged to the account up to
the month's end less every payment the account has made, never below zero, by PostgreSQL --
payments settle the oldest money first (`allocate_oldest_first`), so that is exactly the part of
the statement no payment has covered (the reasoning `_STANDING_SQL` states for overdue money).

Nothing is written here except the owner's publication, and nothing here is personal data: the
account number and holder's name are the shop's own, printed on every QR it hands out.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain.accounts import (
    ACCOUNT_TIMEZONE,
    month_start_instant,
    next_month,
)
from nha_trang_laundry_domain.vietqr import (
    BANK_TRANSFER_ACCOUNT_CONFIG_TYPE,
    BankAccountError,
    BankTransferAccount,
    QrRefusal,
    account_month_qr_amount,
    account_month_transfer_code,
    order_qr_amount,
    order_transfer_code,
    parse_bank_account,
    same_account,
    validate_bank_account_document,
    vietqr_payload,
)

from nha_trang_laundry_db.accounts import ACCOUNT_READ_ROLES, AccountNotFoundError
from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    JsonObject,
    snapshot_hash,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.orders import OrderRepository
from nha_trang_laundry_db.query_version import QueryVersion, query_version
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership


class BankAccountAuthorizationError(PermissionError):
    """Only an active owner decides where the customers' money goes."""


@dataclass(frozen=True, slots=True)
class PublishedBankAccount:
    account: BankTransferAccount
    version_id: UUID
    version: int
    snapshot_hash: str


def publish_bank_account(
    connection: Any, *, actor_id: UUID, payload: JsonObject
) -> tuple[str, bool]:
    """Publish the account (or its withdrawal); return the digest and whether a version was made.

    Idempotent on the account in force: the same bank, account and names again -- whatever the new
    confirmation instant -- change nothing and return the digest in force.
    """

    validate_bank_account_document(payload)
    digest = snapshot_hash(payload)
    repository = ConfigurationRepository(
        {BANK_TRANSFER_ACCOUNT_CONFIG_TYPE: validate_bank_account_document}
    )
    with connection.transaction(), connection.cursor() as cursor:
        _require_active_owner(cursor, actor_id)
        in_force = ConfigurationRepository.latest_published(
            cursor, BANK_TRANSFER_ACCOUNT_CONFIG_TYPE
        )
        if in_force is not None:
            if in_force.snapshot_hash == digest:
                return digest, False
            current = read_published_bank_account(cursor)
            if (
                current is not None
                and payload.get("withdrawn") is not True
                and same_account(current.account, parse_bank_account(dict(payload)))
            ):
                return current.snapshot_hash, False
        cursor.execute(
            "SELECT coalesce(max(version), 0) FROM configuration_versions WHERE config_type = %s",
            (BANK_TRANSFER_ACCOUNT_CONFIG_TYPE,),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1
    config_id = repository.create_draft(
        connection,
        ConfigurationDraft(
            config_type=BANK_TRANSFER_ACCOUNT_CONFIG_TYPE,
            version=next_version,
            payload=payload,
            created_by=actor_id,
        ),
        correlation_id=uuid4(),
    )
    repository.publish(
        connection,
        config_id=config_id,
        version=next_version,
        snapshot_hash_value=digest,
        published_by=actor_id,
        correlation_id=uuid4(),
    )
    return digest, True


def read_published_bank_account(cursor: Any) -> PublishedBankAccount | None:
    """The account in force, or `None` -- which refuses every QR `BANK_ACCOUNT_UNPUBLISHED`.

    Re-hashed against the digest recorded at publication and re-parsed before use, as the other
    published documents are: a payload that no longer matches or no longer parses is not an account
    any customer's money should be sent to. A withdrawal in force is `None`.
    """

    published = ConfigurationRepository.latest_published(cursor, BANK_TRANSFER_ACCOUNT_CONFIG_TYPE)
    if published is None:
        return None
    payload = ConfigurationRepository.get_published(cursor, published.version_id)
    if payload is None or payload.get("withdrawn") is True:
        return None
    if not hmac.compare_digest(snapshot_hash(payload), published.snapshot_hash):
        return None
    try:
        account = parse_bank_account(dict(payload))
    except BankAccountError:
        return None
    return PublishedBankAccount(
        account=account,
        version_id=published.version_id,
        version=published.version,
        snapshot_hash=published.snapshot_hash,
    )


# --- the QR reads -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransferQr:
    """A QR the counter may show: the payload and what it asks for, or the refusal by name.

    Every field but `refusal`, `evaluated_at` and `transfer_code` is `None` when `refusal` is set.
    `transfer_code` is the order's (or month's) code whatever the refusal, so a paid order's page
    can still name the memo an earlier transfer carried.
    """

    refusal: QrRefusal | None
    transfer_code: str
    evaluated_at: datetime
    payload: str | None = None
    amount_vnd: int | None = None
    account: PublishedBankAccount | None = None


@dataclass(frozen=True, slots=True)
class AccountMonthQr:
    qr: TransferQr
    account_id: UUID
    month: date
    #: What of the months before this one is still unpaid, and so part of `qr.amount_vnd`.
    unpaid_before_month_vnd: int
    query_version: str


#: `account-month-unpaid-v1`. What of one statement month is still unpaid, and what of it was
#: charged before the month (the opening balance's unpaid part): everything charged up to the bound
#: less every payment the account has made, never below zero; payments settle the oldest first.
_ACCOUNT_MONTH_UNPAID_SQL: Final = """
    SELECT a.id,
           greatest(totals.charged_through - totals.paid, 0) AS unpaid_through_month_vnd,
           greatest(totals.charged_before - totals.paid, 0) AS unpaid_before_month_vnd
    FROM customer_accounts a
    CROSS JOIN LATERAL (
        SELECT
            (SELECT coalesce(sum(c.amount_vnd), 0) FROM customer_account_charges c
             WHERE c.account_id = a.id AND c.charged_at < %(upper)s) AS charged_through,
            (SELECT coalesce(sum(c.amount_vnd), 0) FROM customer_account_charges c
             WHERE c.account_id = a.id AND c.charged_at < %(lower)s) AS charged_before,
            (SELECT coalesce(sum(p.amount_vnd), 0) FROM customer_account_payments p
             WHERE p.account_id = a.id) AS paid
    ) AS totals
    WHERE a.customer_id = %(customer)s AND a.store_id = %(store)s
"""

ACCOUNT_MONTH_UNPAID_QUERY: Final[QueryVersion] = query_version(
    "account-month-unpaid-v1", _ACCOUNT_MONTH_UNPAID_SQL, ACCOUNT_TIMEZONE
)


class BankTransferRepository:
    """The reads behind the QR routes. The account is read on the same cursor as the money."""

    @staticmethod
    def order_qr(
        cursor: Any, *, order_id: UUID, principal: StaffPrincipal, now: datetime
    ) -> TransferQr:
        """The order's QR: the ledger's remaining balance, the order's transfer code, the account.

        The order read's rule decides who may ask (`OrderRepository.read_for_principal`: a reading
        role, and membership of the store the order row names -- `OrderNotVisibleError` otherwise,
        the same for a missing order and a stranger's).
        """

        view = OrderRepository.read_for_principal(cursor, order_id=order_id, principal=principal)
        code = order_transfer_code(
            order_id=view.order_id,
            ticket_number=view.ticket_number,
            ticket_issued_on=view.ticket_issued_on,
        )
        amount = order_qr_amount(
            commercial=view.commercial, balance=view.balance, remaining_vnd=view.remaining_vnd
        )
        return _qr(cursor, amount=amount, code=code, now=now)

    @staticmethod
    def account_month_qr(
        cursor: Any,
        *,
        store_id: UUID,
        customer_id: UUID,
        month: date,
        principal: StaffPrincipal,
        now: datetime,
    ) -> AccountMonthQr:
        """An account customer's month: what of that statement is still unpaid, with its code.

        The statement read's rule decides who may ask: a role that reads customers, with MFA, and
        membership of the store.
        """

        if not principal.roles & ACCOUNT_READ_ROLES or not principal.mfa_verified:
            raise StoreAccessError("this account read is not authorized for the role")
        require_store_membership(
            cursor, staff_user_id=principal.staff_user_id, store_id=store_id, error=StoreAccessError
        )
        lower, upper = month_start_instant(month), month_start_instant(next_month(month))
        cursor.execute(
            _ACCOUNT_MONTH_UNPAID_SQL,
            {"customer": customer_id, "store": store_id, "lower": lower, "upper": upper},
        )
        row = cursor.fetchone()
        if row is None:
            raise AccountNotFoundError("no account for this customer in this store")
        account_id = row[0] if isinstance(row[0], UUID) else UUID(str(row[0]))
        code = account_month_transfer_code(account_id=account_id, month=month)
        amount: int | QrRefusal = (
            QrRefusal.MONTH_NOT_STARTED if now < lower else account_month_qr_amount(int(row[1]))
        )
        return AccountMonthQr(
            qr=_qr(cursor, amount=amount, code=code, now=now),
            account_id=account_id,
            month=month,
            unpaid_before_month_vnd=int(row[2]),
            query_version=ACCOUNT_MONTH_UNPAID_QUERY.label,
        )


def _qr(cursor: Any, *, amount: int | QrRefusal, code: str, now: datetime) -> TransferQr:
    """Nothing owed is the truer answer than an unpublished account, so it is asked first."""

    if isinstance(amount, QrRefusal):
        return TransferQr(refusal=amount, transfer_code=code, evaluated_at=now)
    published = read_published_bank_account(cursor)
    if published is None:
        return TransferQr(
            refusal=QrRefusal.BANK_ACCOUNT_UNPUBLISHED, transfer_code=code, evaluated_at=now
        )
    payload = vietqr_payload(
        bank_bin=published.account.bank_bin,
        account_number=published.account.account_number,
        amount_vnd=amount,
        transfer_code=code,
    )
    return TransferQr(
        refusal=None,
        transfer_code=code,
        evaluated_at=now,
        payload=payload,
        amount_vnd=amount,
        account=published,
    )


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
        raise BankAccountAuthorizationError(
            "only an active OWNER_ADMIN may publish the shop's bank account"
        )


__all__ = [
    "ACCOUNT_MONTH_UNPAID_QUERY",
    "AccountMonthQr",
    "BankAccountAuthorizationError",
    "BankTransferRepository",
    "PublishedBankAccount",
    "TransferQr",
    "publish_bank_account",
    "read_published_bank_account",
]
