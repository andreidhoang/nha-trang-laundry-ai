"""`CUSTOMER-001` service: connection lifetimes and idempotency around `CustomerRepository`.

The rules live in `nha_trang_laundry_domain.customers`; the rows and their transactions in
`nha_trang_laundry_db.customers`. This module owns two things the repository cannot:

* **the idempotency ledger's shape.** `command_idempotency_records` is append-only and outlives
  every erasure (`protect_idempotency_record`), so nothing personal may be stored in it. The request
  document it hashes carries the phone's keyed digest instead of the number, and the stored result
  is the customer's id and row version only. The routes answer with the record read *now*, so a
  replay after an erasure shows the erasure rather than resurrecting a name from the ledger.
* **the access log.** A search by phone puts the number in the query string, and uvicorn's access
  log prints request lines verbatim. `install_access_log_redaction` removes the query string from
  every access-log line for a customer path before it is formatted. The application's own
  structured log already records the route path only (`correlation_middleware`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.customers import (
    CustomerChanges,
    CustomerDetail,
    CustomerProfile,
    CustomerRepository,
    CustomerSearch,
)
from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.personal_data import phone_digest
from nha_trang_laundry_db.privacy_notice import (
    PublishedPrivacyNotice,
    read_published_privacy_notice,
)
from nha_trang_laundry_domain.customers import (
    CustomerKind,
    CustomerRefusal,
    CustomerRuleError,
    ErasureReason,
    LinkKind,
    normalize_phone,
)

from nha_trang_laundry_api.auth import AuthSettings

#: The path fragment every customer route carries; the access-log filter keys on it.
CUSTOMER_PATH_MARKER: Final = "/customers"


class CustomersUnavailable(RuntimeError):
    """No database configured: the routes answer 503 rather than guess."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    customer_id: UUID
    row_version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class LinkResult:
    link_id: UUID
    customer_id: UUID
    replayed: bool


class CustomerService:
    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise CustomersUnavailable("customer records database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._repository = CustomerRepository()
        self._idempotency = IdempotencyRepository()

    # --- reads -------------------------------------------------------------------------------

    def notice(self, *, store_id: UUID, principal: StaffPrincipal) -> PublishedPrivacyNotice | None:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            CustomerRepository.authorize_read(cursor, store_id=store_id, principal=principal)
            return read_published_privacy_notice(cursor)

    def search(
        self, *, store_id: UUID, principal: StaffPrincipal, query: str, limit: int
    ) -> CustomerSearch:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._repository.search(
                cursor, store_id=store_id, principal=principal, query=query, limit=limit
            )

    def profile(
        self, *, store_id: UUID, customer_id: UUID, principal: StaffPrincipal
    ) -> CustomerProfile:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._repository.profile(
                cursor, store_id=store_id, customer_id=customer_id, principal=principal
            )

    def detail(
        self, *, store_id: UUID, customer_id: UUID, principal: StaffPrincipal
    ) -> CustomerDetail:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._repository.detail(
                cursor, store_id=store_id, customer_id=customer_id, principal=principal
            )

    # --- writes ------------------------------------------------------------------------------

    def create(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        phone: str,
        display_name: str | None,
        delivery_address: str | None,
        note: str | None,
        kind: CustomerKind,
        service_consent: bool,
        marketing_consent: bool,
    ) -> CommandResult:
        """Record a customer. Refusals, in order: role/store, notice, consent, phone, fields."""

        at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                # Before the idempotency claim, as `create_order_request` does: a staff member who
                # lost the store must not replay a key into a write, and an unpublished notice is
                # refused whatever was typed -- the refusal does not depend on the input.
                CustomerRepository.authorize_write(cursor, store_id=store_id, principal=principal)
                if read_published_privacy_notice(cursor) is None:
                    raise CustomerRuleError(CustomerRefusal.PRIVACY_NOTICE_UNPUBLISHED)
            if service_consent is not True:
                raise CustomerRuleError(CustomerRefusal.SERVICE_CONSENT_REQUIRED)
            digest = phone_digest(normalize_phone(phone).e164)

            def commit() -> dict[str, object]:
                customer_id = self._repository.create(
                    connection,
                    store_id=store_id,
                    principal=principal,
                    phone=phone,
                    display_name=display_name,
                    delivery_address=delivery_address,
                    note=note,
                    kind=kind,
                    service_consent=service_consent,
                    marketing_consent=marketing_consent,
                    at=at,
                    correlation_id=uuid4(),
                )
                return {"customer_id": str(customer_id), "row_version": 1}

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-customer-create:{principal.staff_user_id}",
                    key=idempotency_key,
                    # The keyed digest stands for the number: equal numbers conflict or replay
                    # exactly as equal numbers would, and the ledger never holds one.
                    payload={
                        "store_id": str(store_id),
                        "phone_digest": digest,
                        "display_name": display_name,
                        "delivery_address": delivery_address,
                        "note": note,
                        "kind": CustomerKind(kind).value,
                        "service_consent": service_consent,
                        "marketing_consent": marketing_consent,
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
        changes: CustomerChanges,
    ) -> CommandResult:
        at = datetime.now(UTC)
        payload: dict[str, object] = {
            "store_id": str(store_id),
            "customer_id": str(customer_id),
            "expected_row_version": expected_row_version,
        }
        for name in sorted(changes.provided):
            value = getattr(changes, name)
            if name == "phone":
                value = None if value is None else phone_digest(normalize_phone(value).e164)
                name = "phone_digest"
            elif name == "kind" and value is not None:
                value = CustomerKind(value).value
            payload[name] = value
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                CustomerRepository.authorize_write(cursor, store_id=store_id, principal=principal)

            def commit() -> dict[str, object]:
                version = self._repository.update(
                    connection,
                    store_id=store_id,
                    customer_id=customer_id,
                    principal=principal,
                    expected_row_version=expected_row_version,
                    changes=changes,
                    at=at,
                    correlation_id=uuid4(),
                )
                return {"customer_id": str(customer_id), "row_version": version}

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-customer-update:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload=payload,
                    occurred_at=at,
                ),
                commit,
            )
        return _command_result(result.response, replayed=result.replayed)

    def erase(
        self,
        *,
        store_id: UUID,
        customer_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        expected_row_version: int,
        reason: ErasureReason,
    ) -> CommandResult:
        at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                CustomerRepository.authorize_erase(cursor, store_id=store_id, principal=principal)

            def commit() -> dict[str, object]:
                version = self._repository.erase(
                    connection,
                    store_id=store_id,
                    customer_id=customer_id,
                    principal=principal,
                    expected_row_version=expected_row_version,
                    reason=reason,
                    at=at,
                    correlation_id=uuid4(),
                )
                return {"customer_id": str(customer_id), "row_version": version}

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-customer-erase:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "store_id": str(store_id),
                        "customer_id": str(customer_id),
                        "expected_row_version": expected_row_version,
                        "reason": ErasureReason(reason).value,
                    },
                    occurred_at=at,
                ),
                commit,
            )
        return _command_result(result.response, replayed=result.replayed)

    def link(
        self,
        *,
        store_id: UUID,
        customer_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
        link_kind: LinkKind,
        ref_id: UUID,
    ) -> LinkResult:
        at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                CustomerRepository.authorize_write(cursor, store_id=store_id, principal=principal)

            def commit() -> dict[str, object]:
                link_id = self._repository.link(
                    connection,
                    store_id=store_id,
                    customer_id=customer_id,
                    principal=principal,
                    link_kind=link_kind,
                    ref_id=ref_id,
                    at=at,
                    correlation_id=uuid4(),
                )
                return {"link_id": str(link_id), "customer_id": str(customer_id)}

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-customer-link:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "store_id": str(store_id),
                        "customer_id": str(customer_id),
                        "link_kind": LinkKind(link_kind).value,
                        "ref_id": str(ref_id),
                    },
                    occurred_at=at,
                ),
                commit,
            )
        response = result.response
        return LinkResult(
            link_id=UUID(str(response["link_id"])),
            customer_id=UUID(str(response["customer_id"])),
            replayed=result.replayed,
        )


def _command_result(value: dict[str, object], *, replayed: bool) -> CommandResult:
    return CommandResult(
        customer_id=UUID(str(value["customer_id"])),
        row_version=int(str(value["row_version"])),
        replayed=replayed,
    )


# --- the access log ------------------------------------------------------------------------------


class CustomerQueryRedaction(logging.Filter):
    """Drop the query string from an access-log line for a customer path, before formatting.

    uvicorn logs `'%s - "%s %s HTTP/%s" %d'` with the path-and-query as the third argument. The
    filter rewrites that argument only; it never drops a record, so the request is still logged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if isinstance(arguments, tuple) and len(arguments) >= 3:
            target = arguments[2]
            if isinstance(target, str) and CUSTOMER_PATH_MARKER in target and "?" in target:
                path = target.split("?", 1)[0]
                record.args = (*arguments[:2], f"{path}?[query redacted]", *arguments[3:])
        return True


_ACCESS_LOGGER: Final = "uvicorn.access"


def install_access_log_redaction() -> None:
    """Attach the filter once to uvicorn's access logger (idempotent across reloads)."""

    logger = logging.getLogger(_ACCESS_LOGGER)
    if not any(isinstance(item, CustomerQueryRedaction) for item in logger.filters):
        logger.addFilter(CustomerQueryRedaction())


__all__ = [
    "CUSTOMER_PATH_MARKER",
    "CommandResult",
    "CustomerQueryRedaction",
    "CustomerService",
    "CustomersUnavailable",
    "LinkResult",
    "install_access_log_redaction",
]
