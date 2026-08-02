"""Database-backed application service for authenticated operational commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from nha_trang_laundry_contracts import AgentDeploymentStage
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
    StoredApproval,
)
from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.incidents import (
    IncidentOpenCommand,
    IncidentRepository,
    IncidentSummary,
)
from nha_trang_laundry_db.manual_sends import (
    ManualSendAttestationCommand,
    ManualSendPrepareCommand,
    ManualSendRepository,
    StoredManualSend,
)
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderTransitionCommand,
    StoredOrder,
)
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteSummary
from nha_trang_laundry_domain.catalog import (
    ActorRole,
    ApprovalAction,
    CommercialOrderStatus,
    FulfillmentMode,
)

from nha_trang_laundry_api.auth import AuthSettings


class OperationsUnavailable(RuntimeError):
    """Raised when the internal operational database is not configured."""


@dataclass(frozen=True, slots=True)
class StoredManualSendResult:
    value: StoredManualSend
    row_version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class StoredIncidentResult:
    incident_id: UUID
    status: str
    fault_decided: bool
    remedy_decided: bool
    replayed: bool


@dataclass(frozen=True, slots=True)
class QueueRecoverySummary:
    pending_internal: int
    processing_internal: int
    expired_internal: int
    dead_internal: int
    pending_agent: int
    processing_agent: int
    expired_agent: int
    failed_agent: int


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
        self._manual_sends = ManualSendRepository()
        self._incidents = IncidentRepository()
        self._idempotency = IdempotencyRepository()

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
        reason_code: str,
        note: str | None,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredApproval:
        with self._connection_factory(self._database_url) as connection:
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-approval-decision:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "approval_id": str(approval_id),
                        "decision": decision.value,
                        "resource_version": resource_version,
                        "snapshot_hash": snapshot_hash,
                        "rendered_hash": rendered_hash,
                        "reason_code": reason_code,
                        "note": note,
                    },
                ),
                lambda: _approval_mapping(
                    self._approvals.decide(
                        connection,
                        ApprovalDecisionCommand(
                            approval_id,
                            decision,
                            resource_version,
                            snapshot_hash,
                            rendered_hash,
                            reason_code,
                            principal,
                            uuid4(),
                            note=note,
                        ),
                        return_expired=True,
                    )
                ),
            )
        stored = _stored_approval(result.response, replayed=result.replayed)
        if stored.status == "EXPIRED":
            raise ApprovalStateError("approval expired")
        return stored

    def list_pending_approvals(self, *, limit: int) -> tuple[StoredApproval, ...]:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._approvals.list_pending(cursor, limit=limit)

    def list_quotes(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[QuoteSummary, ...]:
        del principal
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return QuoteRepository.list_for_store(cursor, store_id=store_id, limit=limit)

    def prepare_manual_send(
        self,
        *,
        approval_request_id: UUID,
        observed_resource_version: int,
        observed_snapshot_hash: str,
        observed_rendered_hash: str,
        recipient_binding_id: UUID,
        channel: str,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredManualSendResult:
        with self._connection_factory(self._database_url) as connection:
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-manual-prepare:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "approval_request_id": str(approval_request_id),
                        "observed_resource_version": observed_resource_version,
                        "observed_snapshot_hash": observed_snapshot_hash,
                        "observed_rendered_hash": observed_rendered_hash,
                        "recipient_binding_id": str(recipient_binding_id),
                        "channel": channel,
                    },
                ),
                lambda: _manual_send_mapping(
                    self._manual_sends.prepare(
                        connection,
                        ManualSendPrepareCommand(
                            approval_request_id=approval_request_id,
                            observed_resource_version=observed_resource_version,
                            observed_snapshot_hash=observed_snapshot_hash,
                            observed_rendered_hash=observed_rendered_hash,
                            recipient_binding_id=recipient_binding_id,
                            channel=channel,
                            purpose="TRANSACTIONAL",
                            deployment_stage=AgentDeploymentStage.SHADOW,
                            principal=principal,
                            correlation_id=uuid4(),
                        ),
                    ),
                    row_version=1,
                ),
            )
        return _stored_manual_send_result(result.response, replayed=result.replayed)

    def attest_manual_send(
        self,
        *,
        manual_send_envelope_id: UUID,
        observed_resource_version: int,
        exact_rendered_hash: str,
        expected_envelope_row_version: int,
        sent_at: datetime,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredManualSendResult:
        with self._connection_factory(self._database_url) as connection:
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-manual-attest:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "manual_send_envelope_id": str(manual_send_envelope_id),
                        "observed_resource_version": observed_resource_version,
                        "exact_rendered_hash": exact_rendered_hash,
                        "expected_envelope_row_version": expected_envelope_row_version,
                        "sent_at": sent_at.isoformat(),
                    },
                ),
                lambda: _manual_send_mapping(
                    self._manual_sends.attest(
                        connection,
                        ManualSendAttestationCommand(
                            manual_send_envelope_id=manual_send_envelope_id,
                            observed_resource_version=observed_resource_version,
                            exact_rendered_hash=exact_rendered_hash,
                            principal=principal,
                            correlation_id=uuid4(),
                            sent_at=sent_at,
                            expected_envelope_row_version=expected_envelope_row_version,
                        ),
                    ),
                    row_version=2,
                ),
            )
        return _stored_manual_send_result(result.response, replayed=result.replayed)

    def open_incident(
        self,
        *,
        store_id: UUID,
        order_id: UUID,
        contact_scope_hash: str,
        evidence_summary_hash: str,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredIncidentResult:
        opened_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-incident-open:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "store_id": str(store_id),
                        "order_id": str(order_id),
                        "contact_scope_hash": contact_scope_hash,
                        "evidence_summary_hash": evidence_summary_hash,
                    },
                    occurred_at=opened_at,
                ),
                lambda: _incident_mapping(
                    self._incidents.open(
                        connection,
                        IncidentOpenCommand(
                            store_id=store_id,
                            order_id=order_id,
                            affected_message_id=None,
                            affected_policy_version=None,
                            contact_scope_hash=contact_scope_hash,
                            category="SERVICE_QUALITY",
                            evidence_summary_hash=evidence_summary_hash,
                            actor_id=principal.staff_user_id,
                            correlation_id=uuid4(),
                            opened_at=opened_at,
                            actor_type="STAFF",
                        ),
                    )
                ),
            )
        return _stored_incident_result(result.response, replayed=result.replayed)

    def list_incidents(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[IncidentSummary, ...]:
        del principal
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._incidents.list_for_store(cursor, store_id=store_id, limit=limit)

    def queue_recovery_summary(self, *, principal: StaffPrincipal) -> QueueRecoverySummary:
        del principal
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                SELECT
                  count(*) FILTER (WHERE status = 'PENDING'),
                  count(*) FILTER (WHERE status = 'PROCESSING'),
                  count(*) FILTER (
                    WHERE status = 'PROCESSING' AND lease_expires_at < CURRENT_TIMESTAMP
                  ),
                  count(*) FILTER (WHERE status = 'DEAD')
                FROM outbox_events
                """
            )
            outbox = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                  count(*) FILTER (WHERE status = 'PENDING'),
                  count(*) FILTER (WHERE status = 'PROCESSING'),
                  count(*) FILTER (
                    WHERE status = 'PROCESSING' AND lease_expires_at < CURRENT_TIMESTAMP
                  ),
                  count(*) FILTER (WHERE status = 'FAILED')
                FROM agent_runs
                """
            )
            agent = cursor.fetchone()
        if outbox is None or agent is None:
            raise OperationsUnavailable("queue recovery summary is unavailable")
        return QueueRecoverySummary(*(int(value) for value in (*outbox, *agent)))


def _manual_send_mapping(value: StoredManualSend, *, row_version: int) -> dict[str, object]:
    return {
        "manual_send_envelope_id": str(value.manual_send_envelope_id),
        "approval_request_id": str(value.approval_request_id),
        "status": value.status,
        "recipient_binding_id": str(value.recipient_binding_id),
        "rendered_hash": value.rendered_hash,
        "row_version": row_version,
    }


def _stored_manual_send_result(
    value: dict[str, object], *, replayed: bool
) -> StoredManualSendResult:
    stored = StoredManualSend(
        UUID(str(value["manual_send_envelope_id"])),
        UUID(str(value["approval_request_id"])),
        str(value["status"]),
        UUID(str(value["recipient_binding_id"])),
        str(value["rendered_hash"]),
    )
    return StoredManualSendResult(stored, int(str(value["row_version"])), replayed)


def _incident_mapping(value: Any) -> dict[str, object]:
    return {
        "incident_id": str(value.incident_id),
        "status": value.status,
        "fault_decided": value.fault_decided,
        "remedy_decided": value.remedy_decided,
    }


def _stored_incident_result(value: dict[str, object], *, replayed: bool) -> StoredIncidentResult:
    return StoredIncidentResult(
        UUID(str(value["incident_id"])),
        str(value["status"]),
        bool(value["fault_decided"]),
        bool(value["remedy_decided"]),
        replayed,
    )


def _approval_mapping(value: StoredApproval) -> dict[str, object]:
    return {
        "approval_request_id": str(value.approval_request_id),
        "status": value.status,
        "envelope_hash": value.envelope_hash,
        "required_role": value.required_role.value,
        "expires_at": value.expires_at.isoformat(),
    }


def _stored_approval(value: dict[str, object], *, replayed: bool) -> StoredApproval:
    return StoredApproval(
        UUID(str(value["approval_request_id"])),
        str(value["status"]),
        str(value["envelope_hash"]),
        ActorRole(str(value["required_role"])),
        datetime.fromisoformat(str(value["expires_at"])),
        replayed,
    )
