"""The exact VietQR for what is owed (`VIETQR-001`, `DEC-041`): connection lifetimes and the matrix.

What is owed, which code names the order and whether a QR may be shown at all are decided by
`nha_trang_laundry_domain.vietqr` through `BankTransferRepository`, on the cursor that reads the
money. This module adds one thing of its own: the QR **matrix**, drawn here with `segno` (error
correction M, quiet zone 4) and handed to the console as rows of 0 and 1. The console only draws
squares; no SVG, HTML or image crosses the API, so nothing the browser renders was markup the
server wrote.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Final, Literal
from uuid import UUID

import segno
from nha_trang_laundry_db.bank_transfer import (
    AccountMonthQr,
    BankTransferRepository,
    TransferQr,
)
from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_domain.accounts import parse_month
from pydantic import BaseModel, ConfigDict

from nha_trang_laundry_api.auth import AuthSettings

#: The quiet zone the matrix carries on every side, in modules (ISO/IEC 18004 asks for 4).
QUIET_ZONE_MODULES: Final = 4
#: Error correction level M: about 15% of the symbol can be damaged -- a thumb on a phone screen,
#: a crease in a thermal receipt -- and still scan.
ERROR_CORRECTION: Final = "M"


class VietQrServiceUnavailable(RuntimeError):
    """No database configured: the routes answer 503 rather than guess."""


def qr_modules(payload: str) -> list[list[int]]:
    """The QR symbol for `payload` as rows of modules, 1 dark and 0 light, quiet zone included.

    `boost_error=False` keeps the level at M exactly: segno would otherwise raise it whenever the
    same version has room, and the level in the contract would then be a minimum, not a fact.
    """

    symbol = segno.make_qr(payload, error=ERROR_CORRECTION, boost_error=False)
    return [
        [1 if module else 0 for module in row]
        for row in symbol.matrix_iter(scale=1, border=QUIET_ZONE_MODULES)
    ]


class TransferQrResponse(BaseModel):
    """One of: a QR (`refusal` null, every field set), or the refusal by name with no QR.

    `refusal` is `BANK_ACCOUNT_UNPUBLISHED` until the owner publishes the shop's account;
    `NOTHING_OWED` when the order is paid, refunded or on the customer's account (or a credit
    covered the bill); `ORDER_NOT_ACTIVE` / `NO_PRESENTABLE_TOTAL` exactly when the payment route
    would refuse to record the money; `MONTH_NOT_STARTED` for an account month that has not begun;
    `AMOUNT_ABOVE_REMAINING` / `AMOUNT_INVALID` for an order QR asked for a typed part amount the
    payment route would refuse (COUNTER-UI-RACE-009).
    """

    model_config = ConfigDict(extra="forbid")

    refusal: str | None
    #: The memo the QR pre-fills and the order search resolves -- present whatever the refusal.
    transfer_code: str
    payload: str | None
    #: The symbol, row by row, 1 dark and 0 light, quiet zone included; square.
    modules: list[list[int]] | None
    amount_vnd: int | None
    #: Where the amount comes from: the payment ledger's remaining balance for an order, the
    #: statement's unpaid figure for an account month, or -- `PART_OF_BALANCE_DUE` -- the part
    #: payment the counter typed, checked against that remaining balance (at most all of it).
    amount_source: Literal["BALANCE_DUE", "PART_OF_BALANCE_DUE", "STATEMENT_UNPAID"]
    account_name: str | None
    bank_display_name: str | None
    #: Tier 3: the published account's configuration version and digest.
    bank_account_version: int | None
    bank_account_hash: str | None
    evaluated_at: datetime


class OrderVietQrResponse(TransferQrResponse):
    order_id: UUID


class AccountMonthVietQrResponse(TransferQrResponse):
    account_id: UUID
    customer_id: UUID
    month: str
    #: What of the months before this one is still unpaid, and so part of `amount_vnd`.
    unpaid_before_month_vnd: int
    query_version: str


def _fields(
    qr: TransferQr, source: Literal["BALANCE_DUE", "PART_OF_BALANCE_DUE", "STATEMENT_UNPAID"]
) -> dict[str, Any]:
    published = qr.account
    return {
        "refusal": None if qr.refusal is None else qr.refusal.value,
        "transfer_code": qr.transfer_code,
        "payload": qr.payload,
        "modules": None if qr.payload is None else qr_modules(qr.payload),
        "amount_vnd": qr.amount_vnd,
        "amount_source": source,
        "account_name": None if published is None else published.account.account_name,
        "bank_display_name": None if published is None else published.account.bank_display_name,
        "bank_account_version": None if published is None else published.version,
        "bank_account_hash": None if published is None else published.snapshot_hash,
        "evaluated_at": qr.evaluated_at,
    }


def order_vietqr_response(
    order_id: UUID, qr: TransferQr, *, part: bool = False
) -> OrderVietQrResponse:
    """`part`: the QR was asked for a typed part amount (`?amount_vnd=`), not the whole balance."""

    source: Literal["BALANCE_DUE", "PART_OF_BALANCE_DUE"] = (
        "PART_OF_BALANCE_DUE" if part else "BALANCE_DUE"
    )
    return OrderVietQrResponse(order_id=order_id, **_fields(qr, source))


def account_month_vietqr_response(
    customer_id: UUID, found: AccountMonthQr
) -> AccountMonthVietQrResponse:
    return AccountMonthVietQrResponse(
        account_id=found.account_id,
        customer_id=customer_id,
        month=f"{found.month.year:04d}-{found.month.month:02d}",
        unpaid_before_month_vnd=found.unpaid_before_month_vnd,
        query_version=found.query_version,
        **_fields(found.qr, "STATEMENT_UNPAID"),
    )


class VietQrService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise VietQrServiceUnavailable("vietqr database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory

    def order_qr(
        self, *, order_id: UUID, principal: StaffPrincipal, part_vnd: int | None = None
    ) -> TransferQr:
        """The balance and the account are read on one cursor, in one request."""

        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return BankTransferRepository.order_qr(
                cursor,
                order_id=order_id,
                principal=principal,
                now=datetime.now(UTC),
                part_vnd=part_vnd,
            )

    def account_month_qr(
        self, *, store_id: UUID, customer_id: UUID, month: str, principal: StaffPrincipal
    ) -> AccountMonthQr:
        first: date = parse_month(month)
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return BankTransferRepository.account_month_qr(
                cursor,
                store_id=store_id,
                customer_id=customer_id,
                month=first,
                principal=principal,
                now=datetime.now(UTC),
            )


__all__ = [
    "AccountMonthVietQrResponse",
    "OrderVietQrResponse",
    "VietQrService",
    "VietQrServiceUnavailable",
    "account_month_vietqr_response",
    "order_vietqr_response",
    "qr_modules",
]
