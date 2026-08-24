"""Database-backed application service for authenticated operational commands."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from nha_trang_laundry_contracts import AgentDeploymentStage
from nha_trang_laundry_contracts.channel_envelope import ReconciliationState
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
    StoredApproval,
)
from nha_trang_laundry_db.channel import (
    ChannelBindingError,
    ContactChannelBindingRepository,
)
from nha_trang_laundry_db.configurations import ConfigurationRepository, snapshot_hash
from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.incidents import (
    IncidentOpenCommand,
    IncidentRepository,
    IncidentSummary,
)
from nha_trang_laundry_db.intake import (
    CreateOrderRequestCommand,
    OrderRequestRepository,
    OrderRequestSummary,
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
from nha_trang_laundry_db.quotes import (
    QuoteRepository,
    QuoteRevisionCommand,
    QuoteStateError,
    QuoteSummary,
)
from nha_trang_laundry_db.settlement import (
    CollectedToday,
    SettlementCommand,
    SettlementRepository,
    StoredSettlement,
)
from nha_trang_laundry_db.shadow_console import (
    AuditEntry,
    DraftDecision,
    PendingDraft,
    ReviewedDraft,
    ShadowConsoleRepository,
    UnknownSend,
)
from nha_trang_laundry_db.store_access import (
    StoreAccessError,
    member_store_ids,
    require_store_membership,
)
from nha_trang_laundry_domain.catalog import (
    ActorRole,
    ApprovalAction,
    CommercialOrderStatus,
    FulfillmentMode,
    ServiceDefinition,
    Unit,
)
from nha_trang_laundry_domain.pricebook_import import PricebookImportError, published_price_rules
from nha_trang_laundry_domain.quote_composition import (
    PricebookProvenance,
    RequestedLine,
    UnresolvedQuote,
    compose_quote_revision,
)
from nha_trang_laundry_domain.quotes import ImmutableQuoteSnapshot

from nha_trang_laundry_api.auth import AuthSettings


class OperationsUnavailable(RuntimeError):
    """Raised when the internal operational database is not configured."""


class QuotePricingUnavailable(RuntimeError):
    """Raised when this deployment has no published pricebook to price against.

    Deliberately not a fallback. A running system with no published pricebook has no approved
    prices, and inventing one — from a file on disk, from a previous version, from a default — would
    produce a monetary artefact nobody authorized. Unknown means stop.
    """


@dataclass(frozen=True, slots=True)
class QuoteRevisionResult:
    """A committed quote revision, described by what the domain computed rather than re-derived."""

    quote_id: UUID
    revision: int
    row_version: int
    finality: str
    status: str
    snapshot_hash: str
    list_service_subtotal_vnd: int
    net_service_subtotal_vnd: int
    display_total_min_vnd: int | None
    display_total_max_vnd: int | None
    reason_codes: tuple[str, ...]
    required_approvals: tuple[str, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class UnresolvedQuoteResult:
    """Policy the engine could not resolve, carried verbatim. No row was written."""

    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoredSettlementResult:
    settlement_id: UUID
    order_id: UUID
    expected_total_vnd: int
    paid_amount_vnd: int
    settlement_shape: str
    balance_status: str
    self_collection_recorded: bool
    row_version: int
    replayed: bool


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
class StoredOrderRequestResult:
    """A committed intake draft, described by what the repository persisted."""

    order_request_id: UUID
    store_id: UUID
    contact_binding_id: UUID
    status: str
    row_version: int
    created_at: datetime
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

    # --- SHADOW-CONSOLE-001 -----------------------------------------------------------------
    #
    # Thin pass-through. Every authorization decision lives in ShadowConsoleRepository, because a
    # route is a place a check can be forgotten and the repository is the only path to the data.

    def shadow_pending_drafts(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int = 50
    ) -> tuple[PendingDraft, ...]:
        with self._connection_factory(self._database_url) as connection:
            return ShadowConsoleRepository().list_pending_drafts(
                connection, store_id=store_id, principal=principal, limit=limit
            )

    def shadow_reviewed_drafts(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        limit: int = 50,
        before: UUID | None = None,
    ) -> tuple[ReviewedDraft, ...]:
        with self._connection_factory(self._database_url) as connection:
            return ShadowConsoleRepository().list_reviewed_drafts(
                connection, store_id=store_id, principal=principal, limit=limit, before=before
            )

    def shadow_decide_draft(
        self,
        *,
        agent_run_id: UUID,
        decision: str,
        principal: StaffPrincipal,
        reason_code: str | None,
        edited_text: str | None,
    ) -> DraftDecision:
        with self._connection_factory(self._database_url) as connection:
            return ShadowConsoleRepository().decide_draft(
                connection,
                agent_run_id=agent_run_id,
                decision=decision,
                principal=principal,
                correlation_id=uuid4(),
                reason_code=reason_code,
                edited_text=edited_text,
            )

    def shadow_unknown_sends(
        self, *, principal: StaffPrincipal, limit: int = 50
    ) -> tuple[UnknownSend, ...]:
        with self._connection_factory(self._database_url) as connection:
            return ShadowConsoleRepository.list_unknown_sends(
                connection, principal=principal, limit=limit
            )

    def shadow_resolve_unknown_send(
        self,
        *,
        receipt_id: UUID,
        resolution: ReconciliationState,
        principal: StaffPrincipal,
        note: str | None,
    ) -> None:
        with self._connection_factory(self._database_url) as connection:
            ShadowConsoleRepository().resolve_unknown_send(
                connection,
                receipt_id=receipt_id,
                resolution=resolution,
                principal=principal,
                correlation_id=uuid4(),
                note=note,
            )

    def shadow_audit_timeline(
        self, *, store_id: UUID, aggregate_id: UUID, principal: StaffPrincipal
    ) -> tuple[AuditEntry, ...]:
        with self._connection_factory(self._database_url) as connection:
            return ShadowConsoleRepository().audit_timeline(
                connection, store_id=store_id, aggregate_id=aggregate_id, principal=principal
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

    def list_pending_approvals(
        self, *, principal: StaffPrincipal, limit: int
    ) -> tuple[StoredApproval, ...]:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._approvals.list_pending(cursor, principal=principal, limit=limit)

    # --- QUOTE-COMMAND-001 ------------------------------------------------------------------
    #
    # The first command path in this system that produces a monetary artefact. Every number below
    # arrives from `packages/domain`; this method's job is to establish who is asking, which
    # pricebook answers, and to persist exactly what came back.

    def create_quote(
        self,
        *,
        store_id: UUID,
        bound_order_request_id: UUID,
        lines: tuple[RequestedLine, ...],
        fulfillment_mode: FulfillmentMode,
        idempotency_key: str,
        principal: StaffPrincipal,
        verified_distance_m: int | None = None,
        planned_transport_weight_kg: str | None = None,
        approved_manual_fee_vnd: int | None = None,
        customer_acknowledged_manual_fee: bool = False,
        quote_id: UUID | None = None,
        expected_current_revision: int = 0,
        expected_row_version: int = 0,
    ) -> QuoteRevisionResult | UnresolvedQuoteResult:
        """Price through the deterministic engine and commit one immutable revision."""
        priced_at = datetime.now(UTC)
        target_id = quote_id or uuid4()
        revision = expected_current_revision + 1
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                # Membership first, and outside the idempotency wrapper: a staff member who has
                # lost access to this store must not be able to replay a key into a fresh write.
                require_store_membership(
                    cursor,
                    staff_user_id=principal.staff_user_id,
                    store_id=store_id,
                    error=StoreAccessError,
                )
                pricebook = self._published_pricebook(cursor)
            composition = compose_quote_revision(
                quote_id=target_id,
                revision=revision,
                rules=pricebook[0],
                requested=lines,
                pricebook=pricebook[1],
                priced_at=priced_at,
                fulfillment_mode=fulfillment_mode,
                verified_distance_m=verified_distance_m,
                planned_transport_weight_kg=planned_transport_weight_kg,
                approved_manual_fee_vnd=approved_manual_fee_vnd,
                customer_acknowledged_manual_fee=customer_acknowledged_manual_fee,
            )
            if isinstance(composition, UnresolvedQuote):
                # Nothing is written and no idempotency record is claimed: an unresolved quote is
                # not an outcome a caller should be able to replay into existence later.
                return UnresolvedQuoteResult(composition.reason_codes)
            snapshot = composition.snapshot

            def commit() -> dict[str, object]:
                QuoteRepository().create_revision(
                    connection,
                    QuoteRevisionCommand(
                        store_id=store_id,
                        bound_order_request_id=bound_order_request_id,
                        snapshot=snapshot,
                        expected_current_revision=expected_current_revision,
                        expected_row_version=expected_row_version,
                        created_by=principal.staff_user_id,
                        correlation_id=uuid4(),
                        occurred_at=priced_at,
                    ),
                )
                return _quote_mapping(snapshot, row_version=revision)

            try:
                result = self._idempotency.execute(
                    connection,
                    IdempotentCommand(
                        scope=f"staff-quote-create:{principal.staff_user_id}",
                        key=idempotency_key,
                        payload={
                            "store_id": str(store_id),
                            "bound_order_request_id": str(bound_order_request_id),
                            "quote_id": str(quote_id) if quote_id else None,
                            "expected_current_revision": expected_current_revision,
                            "expected_row_version": expected_row_version,
                            "lines": [
                                {
                                    "service_code": line.service_code,
                                    "quantity": line.quantity,
                                    "unit": line.unit.value,
                                    "quantity_basis": line.quantity_basis.value,
                                }
                                for line in lines
                            ],
                        },
                        occurred_at=priced_at,
                    ),
                    commit,
                )
            except psycopg.errors.UniqueViolation as error:
                # `quotes` is UNIQUE (store_id, bound_order_request_id): one order request has one
                # quote container, and further pricing is a new revision of it. Asking to open a
                # second container is a business conflict a caller can act on, so it must not
                # escape as a driver error and become a 500 — found live in the demo stack, where
                # the second run of the verifier priced the same request again.
                raise QuoteStateError(
                    "this order request already has a quote; add a revision instead"
                ) from error
        return _quote_revision_result(result.response, replayed=result.replayed)

    def _published_pricebook(self, cursor: Any) -> tuple[dict[str, Any], PricebookProvenance]:
        """Resolve the one pricebook this deployment prices against, or refuse.

        Three things have to hold before a price is computed, and each failure is a refusal rather
        than a degraded answer: a pricebook must be published, its stored payload must still hash to
        the digest recorded when it was published, and that payload must rebuild into rules without
        losing a field.
        """
        payload, published = self._resolve_published_pricebook(cursor)
        try:
            rules = published_price_rules(payload)
        except PricebookImportError as error:
            raise QuotePricingUnavailable("published pricebook is not usable") from error
        return rules, PricebookProvenance(
            version_id=published.version_id,
            version=published.version,
            snapshot_hash=f"JCS-SHA256-V1:{published.snapshot_hash}",
        )

    @staticmethod
    def _resolve_published_pricebook(cursor: Any) -> tuple[Any, Any]:
        """The published pricebook document and its publication record, digest-checked.

        The one integrity gate every pricebook read passes through — pricing and the console's
        service picker alike — so neither can silently read a payload nobody approved.
        """
        published = ConfigurationRepository.latest_published(cursor, "PRICEBOOK")
        if published is None:
            raise QuotePricingUnavailable("no published pricebook")
        payload = ConfigurationRepository.get_published(cursor, published.version_id)
        if payload is None:
            raise QuotePricingUnavailable("published pricebook payload is missing")
        if not hmac.compare_digest(snapshot_hash(payload), published.snapshot_hash):
            raise QuotePricingUnavailable("published pricebook payload does not match its digest")
        return payload, published

    def list_published_services(self) -> tuple[ServiceDefinition, ...]:
        """The published service catalog, for the quote form's picker.

        The form used to make an operator type a service code from memory. The published payload
        already carries the approved Vietnamese display names, so the picker offers them instead —
        through the same digest gate that prices, because a picker filled from an unverified
        payload would present a pricebook nobody approved.
        """
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            payload, _published = self._resolve_published_pricebook(cursor)
        services = payload.get("services") if isinstance(payload, dict) else None
        if not isinstance(services, list):
            raise QuotePricingUnavailable("published pricebook payload has no service catalog")
        try:
            return tuple(
                ServiceDefinition(
                    code=str(item["code"]),
                    display_name=str(item["display_name"]),
                    category=str(item["category"]),
                    unit=Unit(str(item["unit"])),
                )
                for item in services
            )
        except (KeyError, TypeError, ValueError) as error:
            raise QuotePricingUnavailable("published pricebook catalog is not usable") from error

    def collected_today(self, *, store_id: UUID, principal: StaffPrincipal) -> CollectedToday:
        """Today's counter takings for one store.

        A pass-through by design. The sum is computed by the database over an append-only ledger
        and the membership check runs on the same cursor; there is nothing for this layer to add
        that would not be a second opinion about money.
        """
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return SettlementRepository.collected_today(
                cursor, store_id=store_id, principal=principal
            )

    def list_quotes(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[QuoteSummary, ...]:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return QuoteRepository.list_for_store(
                cursor, store_id=store_id, principal=principal, limit=limit
            )

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
                        principal=principal,
                    )
                ),
            )
        return _stored_incident_result(result.response, replayed=result.replayed)

    # --- SETTLEMENT-001 ----------------------------------------------------------------------

    def record_settlement(
        self,
        *,
        order_id: UUID,
        paid_amount_vnd: int,
        collected_by_customer: bool,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredSettlementResult:
        """Attest that the customer paid the quoted total and collected their goods."""
        attested_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-settlement:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "order_id": str(order_id),
                        "paid_amount_vnd": paid_amount_vnd,
                        "collected_by_customer": collected_by_customer,
                    },
                    occurred_at=attested_at,
                ),
                lambda: _settlement_mapping(
                    SettlementRepository().record(
                        connection,
                        SettlementCommand(
                            order_id=order_id,
                            paid_amount_vnd=paid_amount_vnd,
                            collected_by_customer=collected_by_customer,
                            principal=principal,
                            correlation_id=uuid4(),
                            attested_at=attested_at,
                        ),
                    )
                ),
            )
        return _stored_settlement_result(result.response, replayed=result.replayed)

    # --- STORE-ASSIGNMENT-001 ---------------------------------------------------------------
    #
    # The first routes in this system that *grant* an authorization rather than check one, so the
    # failure mode is inverted: not a refused operator, but a granted one who should not have been.
    # Both wrap the repository, which does the owner check against the database and writes the
    # assignment, its domain event, its audit entry and its outbox event in one transaction.

    def assign_store(
        self,
        *,
        staff_user_id: UUID,
        store_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
    ) -> None:
        occurred_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-store-assign:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={"staff_user_id": str(staff_user_id), "store_id": str(store_id)},
                    occurred_at=occurred_at,
                ),
                lambda: self._assign_store_once(
                    connection,
                    staff_user_id=staff_user_id,
                    store_id=store_id,
                    principal=principal,
                    occurred_at=occurred_at,
                ),
            )

    def _assign_store_once(
        self,
        connection: Any,
        *,
        staff_user_id: UUID,
        store_id: UUID,
        principal: StaffPrincipal,
        occurred_at: datetime,
    ) -> dict[str, object]:
        ShadowConsoleRepository.assign_store(
            connection,
            staff_user_id=staff_user_id,
            store_id=store_id,
            principal=principal,
            correlation_id=uuid4(),
            now=occurred_at,
        )
        return {"staff_user_id": str(staff_user_id), "store_id": str(store_id)}

    def revoke_store(
        self,
        *,
        staff_user_id: UUID,
        store_id: UUID,
        principal: StaffPrincipal,
        idempotency_key: str,
    ) -> None:
        occurred_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-store-revoke:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={"staff_user_id": str(staff_user_id), "store_id": str(store_id)},
                    occurred_at=occurred_at,
                ),
                lambda: self._revoke_store_once(
                    connection,
                    staff_user_id=staff_user_id,
                    store_id=store_id,
                    principal=principal,
                    occurred_at=occurred_at,
                ),
            )

    def _revoke_store_once(
        self,
        connection: Any,
        *,
        staff_user_id: UUID,
        store_id: UUID,
        principal: StaffPrincipal,
        occurred_at: datetime,
    ) -> dict[str, object]:
        ShadowConsoleRepository.revoke_store(
            connection,
            staff_user_id=staff_user_id,
            store_id=store_id,
            principal=principal,
            correlation_id=uuid4(),
            now=occurred_at,
        )
        return {"staff_user_id": str(staff_user_id), "store_id": str(store_id)}

    def list_member_stores(self, *, principal: StaffPrincipal) -> tuple[UUID, ...]:
        """Return the stores this principal is assigned to, in a stable order.

        Every store-scoped route refuses a principal who is not an assigned member, and until this
        existed the console had no way to learn which stores those are — the runbook told staff to
        paste a UUID by hand. It reads `staff_store_assignments` directly and returns nothing else:
        there is no `stores` table yet, so a name would have to be invented, and an invented name
        next to a real identifier is worse than the identifier alone.
        """

        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return tuple(sorted(member_store_ids(cursor, staff_user_id=principal.staff_user_id)))

    def list_incidents(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[IncidentSummary, ...]:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._incidents.list_for_store(
                cursor, store_id=store_id, principal=principal, limit=limit
            )

    # --- INTAKE-UI-001 ------------------------------------------------------------------
    #
    # The staff counter path onto the same `order_requests` aggregate the agent tool path writes.
    # There is no intake business rule here on purpose: creation is `OrderRequestRepository.create`
    # with a STAFF actor, and the only staff-specific decision is what a counter intake may name —
    # a contact binding that already exists, and nothing else.

    def create_order_request(
        self,
        *,
        store_id: UUID,
        contact_binding_id: UUID,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredOrderRequestResult:
        """Open an intake draft bound to a contact the server already knows.

        Two checks run before the idempotency wrapper, as on `create_quote`: membership, because a
        staff member who has lost this store must not replay a key into a fresh write, and contact
        existence, because the domain's only source of contact bindings is the verified channel
        envelope and this path invents none. A counter intake has no channel conversation, so the
        conversation binding is a freshly minted opaque id — no table joins on it anywhere, and the
        agent bound-read path, which demands the full four-id tuple, can never match a request it
        did not create. Contact bindings are never hard-deleted, so the existence answer cannot
        change between the preflight and the commit.
        """
        created_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                require_store_membership(
                    cursor,
                    staff_user_id=principal.staff_user_id,
                    store_id=store_id,
                    error=StoreAccessError,
                )
                if not ContactChannelBindingRepository.binding_exists(
                    cursor, contact_binding_id=contact_binding_id
                ):
                    raise ChannelBindingError("contact binding is not available")

            def commit() -> dict[str, object]:
                stored = OrderRequestRepository().create(
                    connection,
                    CreateOrderRequestCommand(
                        store_id=store_id,
                        contact_binding_id=contact_binding_id,
                        conversation_binding_id=uuid4(),
                        actor_id=principal.staff_user_id,
                        correlation_id=uuid4(),
                        created_at=created_at,
                        actor_type="STAFF",
                    ),
                )
                return {
                    "order_request_id": str(stored.order_request_id),
                    "store_id": str(store_id),
                    "contact_binding_id": str(contact_binding_id),
                    "status": stored.status,
                    "row_version": stored.row_version,
                    "created_at": created_at.isoformat(),
                }

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-order-request-create:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "store_id": str(store_id),
                        "contact_binding_id": str(contact_binding_id),
                    },
                    occurred_at=created_at,
                ),
                commit,
            )
        return _stored_order_request_result(result.response, replayed=result.replayed)

    def list_order_requests(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[OrderRequestSummary, ...]:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return OrderRequestRepository.list_for_store(
                cursor, store_id=store_id, principal=principal, limit=limit
            )

    def get_order_request(
        self, *, store_id: UUID, order_request_id: UUID, principal: StaffPrincipal
    ) -> OrderRequestSummary | None:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return OrderRequestRepository.get_for_store(
                cursor,
                order_request_id=order_request_id,
                store_id=store_id,
                principal=principal,
            )

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


def _stored_order_request_result(
    value: dict[str, object], *, replayed: bool
) -> StoredOrderRequestResult:
    return StoredOrderRequestResult(
        order_request_id=UUID(str(value["order_request_id"])),
        store_id=UUID(str(value["store_id"])),
        contact_binding_id=UUID(str(value["contact_binding_id"])),
        status=str(value["status"]),
        row_version=int(str(value["row_version"])),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        replayed=replayed,
    )


def _settlement_mapping(value: StoredSettlement) -> dict[str, object]:
    return {
        "settlement_id": str(value.settlement_id),
        "order_id": str(value.order_id),
        "expected_total_vnd": value.expected_total_vnd,
        "paid_amount_vnd": value.paid_amount_vnd,
        "settlement_shape": value.settlement_shape,
        "balance_status": value.balance_status,
        "self_collection_recorded": value.self_collection_recorded,
        "row_version": value.row_version,
    }


def _stored_settlement_result(
    value: dict[str, object], *, replayed: bool
) -> StoredSettlementResult:
    return StoredSettlementResult(
        settlement_id=UUID(str(value["settlement_id"])),
        order_id=UUID(str(value["order_id"])),
        expected_total_vnd=int(str(value["expected_total_vnd"])),
        paid_amount_vnd=int(str(value["paid_amount_vnd"])),
        settlement_shape=str(value["settlement_shape"]),
        balance_status=str(value["balance_status"]),
        self_collection_recorded=bool(value["self_collection_recorded"]),
        row_version=int(str(value["row_version"])),
        replayed=replayed,
    )


def _quote_mapping(snapshot: ImmutableQuoteSnapshot, *, row_version: int) -> dict[str, object]:
    """Project a committed revision into the JSON the idempotency ledger replays.

    Everything here is read off the immutable snapshot the domain built. A replayed response has to
    be indistinguishable from the original, so nothing may be recomputed at read time.
    """
    data = snapshot.data
    totals = data.totals
    return {
        "quote_id": str(data.quote_id),
        "revision": data.revision,
        "row_version": row_version,
        "finality": data.finality.value,
        "status": data.status.value,
        "snapshot_hash": snapshot.document.snapshot_hash,
        "list_service_subtotal_vnd": totals.list_service_subtotal_max_vnd,
        "net_service_subtotal_vnd": totals.net_service_subtotal_max_vnd,
        "display_total_min_vnd": totals.display_total_min_vnd,
        "display_total_max_vnd": totals.display_total_max_vnd,
        "reason_codes": list(data.reason_codes),
        "required_approvals": list(data.required_approvals),
    }


def _quote_revision_result(value: dict[str, object], *, replayed: bool) -> QuoteRevisionResult:
    return QuoteRevisionResult(
        quote_id=UUID(str(value["quote_id"])),
        revision=int(str(value["revision"])),
        row_version=int(str(value["row_version"])),
        finality=str(value["finality"]),
        status=str(value["status"]),
        snapshot_hash=str(value["snapshot_hash"]),
        list_service_subtotal_vnd=int(str(value["list_service_subtotal_vnd"])),
        net_service_subtotal_vnd=int(str(value["net_service_subtotal_vnd"])),
        display_total_min_vnd=_optional_vnd(value["display_total_min_vnd"]),
        display_total_max_vnd=_optional_vnd(value["display_total_max_vnd"]),
        reason_codes=tuple(str(code) for code in _string_list(value["reason_codes"])),
        required_approvals=tuple(str(code) for code in _string_list(value["required_approvals"])),
        replayed=replayed,
    )


def _optional_vnd(value: object) -> int | None:
    return None if value is None else int(str(value))


def _string_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise OperationsUnavailable("stored quote response is malformed")
    return value


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
