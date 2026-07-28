"""Database-backed application service for authenticated operational commands."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    StoredApproval,
)
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderTransitionCommand,
    StoredOrder,
)
from nha_trang_laundry_domain.catalog import (
    ApprovalAction,
    CommercialOrderStatus,
    FulfillmentMode,
)

from nha_trang_laundry_api.auth import AuthSettings


class OperationsUnavailable(RuntimeError):
    """Raised when the internal operational database is not configured."""


class OperationsService:
    """Own connection lifetimes while repositories own transactional semantics."""

    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = psycopg.connect,
    ) -> None:
        if not settings.database_url:
            raise OperationsUnavailable("operations database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._orders = OrderRepository()
        self._approvals = ApprovalRepository()

    def create_order(
        self,
        *,
        store_id: UUID,
        bound_contact_id: UUID,
        quote_id: UUID,
        quote_revision: int,
        quote_snapshot_hash: str,
        fulfillment_mode: FulfillmentMode,
        accepted_at: datetime,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredOrder:
        with self._connection_factory(self._database_url) as connection:
            return self._orders.create(
                connection,
                CreateOrderCommand(
                    store_id,
                    bound_contact_id,
                    quote_id,
                    quote_revision,
                    quote_snapshot_hash,
                    fulfillment_mode,
                    principal,
                    idempotency_key,
                    uuid4(),
                    accepted_at,
                ),
            )

    def transition_commercial(
        self,
        *,
        order_id: UUID,
        target: CommercialOrderStatus,
        expected_row_version: int,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredOrder:
        with self._connection_factory(self._database_url) as connection:
            return self._orders.transition(
                connection,
                OrderTransitionCommand(
                    order_id,
                    expected_row_version,
                    principal,
                    idempotency_key,
                    uuid4(),
                    commercial_target=target,
                ),
            )

    def list_orders(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[StoredOrder, ...]:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._orders.list_for_store(
                cursor, store_id=store_id, principal=principal, limit=limit
            )

    def request_approval(
        self,
        *,
        action: ApprovalAction,
        resource_type: str,
        resource_id: UUID,
        resource_version: int,
        snapshot_hash: str,
        rendered_hash: str,
        policy_version: str,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredApproval:
        with self._connection_factory(self._database_url) as connection:
            return self._approvals.request(
                connection,
                ApprovalRequestCommand(
                    action,
                    resource_type,
                    resource_id,
                    resource_version,
                    snapshot_hash,
                    rendered_hash,
                    policy_version,
                    principal.staff_user_id,
                    idempotency_key,
                    uuid4(),
                ),
            )

    def decide_approval(
        self,
        *,
        approval_id: UUID,
        decision: ApprovalDecision,
        resource_version: int,
        snapshot_hash: str,
        rendered_hash: str,
        principal: StaffPrincipal,
    ) -> StoredApproval:
        with self._connection_factory(self._database_url) as connection:
            return self._approvals.decide(
                connection,
                ApprovalDecisionCommand(
                    approval_id,
                    decision,
                    resource_version,
                    snapshot_hash,
                    rendered_hash,
                    principal,
                    uuid4(),
                ),
            )

    def list_pending_approvals(self, *, limit: int) -> tuple[StoredApproval, ...]:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._approvals.list_pending(cursor, limit=limit)
