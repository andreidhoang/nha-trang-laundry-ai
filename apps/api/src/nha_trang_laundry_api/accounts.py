"""The API's side of `PAYMENT-002` (`DEC-035`, B2B half): connections, the clock and idempotency.

The rules are the domain's (`nha_trang_laundry_domain.accounts`, `.account_charge`); the rows and
their transactions are `nha_trang_laundry_db.accounts`'. This module owns what neither may: the
server's clock (every instant is read here once per request and passed in), connection lifetimes,
and the idempotency ledger's shape.

The idempotency ledger is append-only and outlives an erasure, so the stored result of every
command here is ids, versions, amounts and codes -- never a name. The owner's lift reason is part
of the request the ledger hashes (a changed reason under the same key is a conflict), and is not
in the stored result.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.accounts import (
    AccountChargeCommand,
    AccountPaymentCommand,
    AccountRead,
    AccountRepository,
    AccountStatement,
    LiftBlockCommand,
    OpenAccountCommand,
    OrderAccountHandover,
    UpdateAccountCommand,
)
from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_domain.accounts import AccountStatus, parse_month
from nha_trang_laundry_domain.payments import PaymentMethod

from nha_trang_laundry_api.auth import AuthSettings


class AccountsUnavailable(RuntimeError):
    """No database configured: the routes answer 503 rather than guess."""


@dataclass(frozen=True, slots=True)
class AccountCommandResult:
    account_id: UUID
    row_version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class VersionResult:
    row_version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class AllocationResult:
    order_id: UUID
    amount_vnd: int
    settled: bool
    position: int


@dataclass(frozen=True, slots=True)
class AccountPaymentResult:
    payment_id: UUID
    account_id: UUID
    amount_vnd: int
    method: str
    bank_ref_last: str | None
    recorded_at: datetime
    allocations: tuple[AllocationResult, ...]
    outstanding_after_vnd: int
    row_version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class AccountChargeResult:
    charge_id: UUID
    order_id: UUID
    account_id: UUID
    amount_vnd: int
    outstanding_after_vnd: int
    balance_status: str
    self_collection_recorded: bool
    row_version: int
    replayed: bool


class AccountService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise AccountsUnavailable("account database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._repository = AccountRepository()
        self._idempotency = IdempotencyRepository()

    # --- reads -------------------------------------------------------------------------------

    def read(self, *, store_id: UUID, customer_id: UUID, principal: StaffPrincipal) -> AccountRead:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._repository.read(
                cursor,
                store_id=store_id,
                customer_id=customer_id,
                principal=principal,
                now=datetime.now(UTC),
            )

    def statement(
        self, *, store_id: UUID, customer_id: UUID, month: str, principal: StaffPrincipal
    ) -> AccountStatement:
        first = parse_month(month)
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._repository.statement(
                cursor,
                store_id=store_id,
                customer_id=customer_id,
                month=first,
                principal=principal,
                now=datetime.now(UTC),
            )

    def order_handover(
        self, *, order_id: UUID, principal: StaffPrincipal
    ) -> OrderAccountHandover | None:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._repository.order_handover(
                cursor, order_id=order_id, principal=principal, now=datetime.now(UTC)
            )

    # --- the owner's commands ----------------------------------------------------------------

    def open(
        self,
        *,
        store_id: UUID,
        customer_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        credit_limit_vnd: int | None,
    ) -> AccountCommandResult:
        at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                AccountRepository.authorize_owner(cursor, store_id=store_id, principal=principal)

            def commit() -> dict[str, object]:
                account_id, version = self._repository.open(
                    connection,
                    OpenAccountCommand(
                        store_id=store_id,
                        customer_id=customer_id,
                        credit_limit_vnd=credit_limit_vnd,
                        principal=principal,
                        correlation_id=uuid4(),
                        at=at,
                    ),
                )
                return {"account_id": str(account_id), "row_version": version}

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-account-open:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "store_id": str(store_id),
                        "customer_id": str(customer_id),
                        "credit_limit_vnd": credit_limit_vnd,
                    },
                    occurred_at=at,
                ),
                commit,
            )
        return _command_result(result.response, replayed=result.replayed)

    def update(
        self,
        *,
        store_id: UUID,
        customer_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        expected_row_version: int,
        provided: frozenset[str],
        credit_limit_vnd: int | None,
        status: AccountStatus | None,
    ) -> VersionResult:
        at = datetime.now(UTC)
        payload: dict[str, object] = {
            "store_id": str(store_id),
            "customer_id": str(customer_id),
            "expected_row_version": expected_row_version,
        }
        if "credit_limit_vnd" in provided:
            payload["credit_limit_vnd"] = credit_limit_vnd
        if "status" in provided:
            payload["status"] = None if status is None else AccountStatus(status).value
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                AccountRepository.authorize_owner(cursor, store_id=store_id, principal=principal)

            def commit() -> dict[str, object]:
                version = self._repository.update(
                    connection,
                    UpdateAccountCommand(
                        store_id=store_id,
                        customer_id=customer_id,
                        expected_row_version=expected_row_version,
                        provided=provided,
                        credit_limit_vnd=credit_limit_vnd,
                        status=status,
                        principal=principal,
                        correlation_id=uuid4(),
                        at=at,
                    ),
                )
                return {"row_version": version}

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-account-update:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload=payload,
                    occurred_at=at,
                ),
                commit,
            )
        return VersionResult(
            row_version=int(str(result.response["row_version"])), replayed=result.replayed
        )

    def lift_block(
        self,
        *,
        store_id: UUID,
        customer_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        expected_row_version: int,
        reason: str,
        until: date,
    ) -> VersionResult:
        at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                AccountRepository.authorize_owner(cursor, store_id=store_id, principal=principal)

            def commit() -> dict[str, object]:
                lift_id, _until, version = self._repository.lift_block(
                    connection,
                    LiftBlockCommand(
                        store_id=store_id,
                        customer_id=customer_id,
                        expected_row_version=expected_row_version,
                        reason=reason,
                        until=until,
                        principal=principal,
                        correlation_id=uuid4(),
                        at=at,
                    ),
                )
                return {"lift_id": str(lift_id), "row_version": version}

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-account-lift:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "store_id": str(store_id),
                        "customer_id": str(customer_id),
                        "expected_row_version": expected_row_version,
                        "reason": reason,
                        "until": until.isoformat(),
                    },
                    occurred_at=at,
                ),
                commit,
            )
        return VersionResult(
            row_version=int(str(result.response["row_version"])), replayed=result.replayed
        )

    # --- the counter's commands --------------------------------------------------------------

    def charge(
        self,
        *,
        order_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        expected_row_version: int,
        collected_by_customer: bool,
    ) -> AccountChargeResult:
        at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                # Before the idempotency claim, as the payment route does: a replay must not run
                # for a staff member who has lost the order's store.
                store_id = AccountRepository.order_store(cursor, order_id)
                AccountRepository.authorize_counter(cursor, store_id=store_id, principal=principal)

            def commit() -> dict[str, object]:
                stored = self._repository.charge(
                    connection,
                    AccountChargeCommand(
                        order_id=order_id,
                        expected_row_version=expected_row_version,
                        collected_by_customer=collected_by_customer,
                        principal=principal,
                        correlation_id=uuid4(),
                        at=at,
                    ),
                )
                return {
                    "charge_id": str(stored.charge_id),
                    "order_id": str(stored.order_id),
                    "account_id": str(stored.account_id),
                    "amount_vnd": stored.amount_vnd,
                    "outstanding_after_vnd": stored.outstanding_after_vnd,
                    "balance_status": stored.balance_status,
                    "self_collection_recorded": stored.self_collection_recorded,
                    "row_version": stored.order_row_version,
                }

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-account-charge:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "order_id": str(order_id),
                        "expected_row_version": expected_row_version,
                        "collected_by_customer": collected_by_customer,
                    },
                    occurred_at=at,
                ),
                commit,
            )
        value = result.response
        return AccountChargeResult(
            charge_id=UUID(str(value["charge_id"])),
            order_id=UUID(str(value["order_id"])),
            account_id=UUID(str(value["account_id"])),
            amount_vnd=int(str(value["amount_vnd"])),
            outstanding_after_vnd=int(str(value["outstanding_after_vnd"])),
            balance_status=str(value["balance_status"]),
            self_collection_recorded=value["self_collection_recorded"] is True,
            row_version=int(str(value["row_version"])),
            replayed=result.replayed,
        )

    def record_payment(
        self,
        *,
        store_id: UUID,
        customer_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        expected_row_version: int,
        amount_vnd: int,
        method: PaymentMethod,
        transfer_seen: bool,
        bank_ref_last: str | None,
    ) -> AccountPaymentResult:
        at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                AccountRepository.authorize_counter(cursor, store_id=store_id, principal=principal)

            def commit() -> dict[str, object]:
                stored = self._repository.record_payment(
                    connection,
                    AccountPaymentCommand(
                        store_id=store_id,
                        customer_id=customer_id,
                        expected_row_version=expected_row_version,
                        amount_vnd=amount_vnd,
                        method=method,
                        transfer_seen=transfer_seen,
                        bank_ref_last=bank_ref_last,
                        principal=principal,
                        correlation_id=uuid4(),
                        at=at,
                    ),
                )
                return {
                    "payment_id": str(stored.payment_id),
                    "account_id": str(stored.account_id),
                    "amount_vnd": stored.amount_vnd,
                    "method": stored.method,
                    "bank_ref_last": stored.bank_ref_last,
                    "recorded_at": stored.recorded_at.isoformat(),
                    "allocations": [
                        {
                            "order_id": str(item.order_id),
                            "amount_vnd": item.amount_vnd,
                            "settled": item.settled,
                            "position": item.position,
                        }
                        for item in stored.allocations
                    ],
                    "outstanding_after_vnd": stored.outstanding_after_vnd,
                    "row_version": stored.account_row_version,
                }

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-account-payment:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "store_id": str(store_id),
                        "customer_id": str(customer_id),
                        "expected_row_version": expected_row_version,
                        "amount_vnd": amount_vnd,
                        "method": method.value,
                        "transfer_seen": transfer_seen,
                        "bank_ref_last": bank_ref_last,
                    },
                    occurred_at=at,
                ),
                commit,
            )
        value = result.response
        allocations = value["allocations"]
        assert isinstance(allocations, list)
        return AccountPaymentResult(
            payment_id=UUID(str(value["payment_id"])),
            account_id=UUID(str(value["account_id"])),
            amount_vnd=int(str(value["amount_vnd"])),
            method=str(value["method"]),
            bank_ref_last=None if value["bank_ref_last"] is None else str(value["bank_ref_last"]),
            recorded_at=datetime.fromisoformat(str(value["recorded_at"])),
            allocations=tuple(
                AllocationResult(
                    order_id=UUID(str(item["order_id"])),
                    amount_vnd=int(str(item["amount_vnd"])),
                    settled=item["settled"] is True,
                    position=int(str(item["position"])),
                )
                for item in allocations
            ),
            outstanding_after_vnd=int(str(value["outstanding_after_vnd"])),
            row_version=int(str(value["row_version"])),
            replayed=result.replayed,
        )


def _command_result(value: dict[str, object], *, replayed: bool) -> AccountCommandResult:
    return AccountCommandResult(
        account_id=UUID(str(value["account_id"])),
        row_version=int(str(value["row_version"])),
        replayed=replayed,
    )


__all__ = [
    "AccountChargeResult",
    "AccountCommandResult",
    "AccountPaymentResult",
    "AccountService",
    "AccountsUnavailable",
    "AllocationResult",
    "VersionResult",
]
