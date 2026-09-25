"""Database-backed application service for authenticated operational commands."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from nha_trang_laundry_contracts import AgentDeploymentStage
from nha_trang_laundry_contracts.channel_envelope import ReconciliationState
from nha_trang_laundry_db.approvals import (
    COUNTER_ATTESTED_CAPABILITY,
    ApprovalAttestationCommand,
    ApprovalBinding,
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
    StoredApproval,
    read_approval_binding,
)
from nha_trang_laundry_db.channel import (
    ChannelBindingError,
    ContactChannelBindingRepository,
)
from nha_trang_laundry_db.configurations import ConfigurationRepository, snapshot_hash
from nha_trang_laundry_db.connection import application_connect
from nha_trang_laundry_db.counter_tickets import (
    CounterTicketRepository,
    IssuedTicket,
    ticket_business_date,
)
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegKind,
    DeliveryLegOutcome,
    DeliveryLegRepository,
    RecordDeliveryLegCommand,
    StoredDeliveryLeg,
)
from nha_trang_laundry_db.idempotency import IdempotencyRepository, IdempotentCommand
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.incidents import (
    IncidentRepository,
    IncidentSummary,
    StaffIncidentOpenCommand,
    evidence_summary_digest,
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
from nha_trang_laundry_db.message_drafts import (
    MessageDraftBinding,
    MessageDraftSendProgress,
    read_message_draft_send_state_for_store,
)
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderStepCommand,
    OrderStepResult,
    OrderTransitionCommand,
    OrderView,
    StoredOrder,
    TicketReference,
)
from nha_trang_laundry_db.promotions import read_published_promotion_program
from nha_trang_laundry_db.quotes import (
    QuoteAcceptanceCommand,
    QuoteAcceptanceRepository,
    QuoteRepository,
    QuoteRevisionCommand,
    QuoteStateError,
    QuoteSummary,
)
from nha_trang_laundry_db.range_prices import (
    ProposedRangePriceLine,
    RangePriceProposalRecord,
    RangePriceProposalRepository,
    RangePriceReviewEntry,
    RecordRangePriceProposalCommand,
)
from nha_trang_laundry_db.remedies import (
    RemedyCreditRedemptionCommand,
    RemedyCreditRepository,
    RemedyExecutionCommand,
    RemedyOptions,
    RemedyProposalCommand,
    RemedyProposalRepository,
    ReservedRemedyCredits,
    read_reserved_remedy_credits,
)
from nha_trang_laundry_db.remedy_reads import (
    IncidentRemedyProposals,
    OrderRemedyCredits,
    RemedyApprovalBinding,
    RemedyReadRepository,
)
from nha_trang_laundry_db.settlement import (
    CollectedToday,
    CollectionCommand,
    SettlementCommand,
    SettlementRepository,
    StoredCollection,
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
from nha_trang_laundry_db.staff_directory import StaffDirectory, StaffDirectoryRepository
from nha_trang_laundry_db.store_access import (
    StoreAccessError,
    member_store_ids,
    require_store_membership,
)
from nha_trang_laundry_db.transactional_consent import (
    ServiceMessagingState,
    TransactionalReleaseCommand,
    read_service_messaging_state,
    release_transactional_suppression,
)
from nha_trang_laundry_domain.approvals import APPROVAL_POLICIES, APPROVAL_RESOURCE_TYPES
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    ActorRole,
    ApprovalAction,
    CommercialOrderStatus,
    CustodyResolution,
    ErrorCode,
    FulfillmentMode,
    IntakeStatus,
    ProductionStatus,
    ServiceDefinition,
    Unit,
)
from nha_trang_laundry_domain.order_steps import OrderStep
from nha_trang_laundry_domain.orders import IntakeReadiness
from nha_trang_laundry_domain.pricebook_import import PricebookImportError, published_price_rules
from nha_trang_laundry_domain.quote_composition import (
    PricebookProvenance,
    RequestedLine,
    UnresolvedQuote,
    accept_quote_revision,
    close_range_prices,
    compose_quote_revision,
    frozen_promotion,
    stored_price_bands,
)
from nha_trang_laundry_domain.quotes import (
    ExactLineAmounts,
    ImmutableQuoteSnapshot,
    parse_quote_revision,
)
from nha_trang_laundry_domain.range_prices import (
    RANGE_PRICE_POLICY_VERSION,
    RangePriceAttestation,
    RangePriceChoice,
    RangePriceRefused,
    range_price_rendered_document,
    resolve_range_prices,
)
from nha_trang_laundry_domain.remedies import RemedyKind

from nha_trang_laundry_api.auth import AuthSettings

# The rule an acceptance is stamped with. `DEC-021` is the policy; the version moves when the
# rule changes, so an attestation always names the rule in force when it was signed.
QUOTE_ACCEPTANCE_POLICY_VERSION = "quote-acceptance-dec-021-v1"
# The reason code on the `approval_decisions` row a staff member's own range-price attestation
# writes (`DEC-029`). Distinct from any reason an owner would give, so the owner reviewing the
# record can tell at a glance which prices were chosen at the counter without a second person.
RANGE_PRICE_COUNTER_ATTESTED = "RANGE_PRICE_COUNTER_ATTESTED"
# "Nhân viên đang trực quầy được chốt giá" -- the owner's words. OWNER_ADMIN is included because
# a supervisor is never locked out of what their staff may do.
QUOTE_ACCEPTANCE_ROLES = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
)
# Who may look at what an approval envelope is asking for. The same two roles `main.py`'s
# `require_approval_staff` admits, restated here rather than imported because every other command
# in this module re-checks its own roles: a service method that trusts a route dependency is a
# service method that is safe only while it is called from that one route.
APPROVAL_DECISION_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER})


class OperationsUnavailable(RuntimeError):
    """Raised when the internal operational database is not configured."""


class QuotePricingUnavailable(RuntimeError):
    """Raised when this deployment has no published pricebook to price against.

    Deliberately not a fallback. A running system with no published pricebook has no approved
    prices, and inventing one — from a file on disk, from a previous version, from a default — would
    produce a monetary artefact nobody authorized. Unknown means stop.
    """


@dataclass(frozen=True, slots=True)
class QuotePromotionView:
    """The promotion frozen onto a committed revision, as a console has to render it.

    `None` in place of this whole object means no programme was evaluated against the revision, and
    the revision's own reason codes say which of the two cases that is -- `PROMOTION_NOT_PUBLISHED`
    or `PROMOTION_PENDING_BAND_CLOSE`. It is deliberately not an object full of zeroes: "no
    promotion was assessed" and "a promotion was assessed and came to nothing" are different
    sentences to say to a customer, and collapsing them is the failure this item exists to fix.

    `interval_end_at_exclusive` is here so that a console can render *when* an expired programme
    ended rather than showing a silent zero. It is the exclusive bound, so the last day the
    programme covered is the day before it.
    """

    policy_code: str
    configuration_version: int
    status: str
    discount_amount_vnd: int
    rate_bps: tuple[int, ...]
    interval_start_at: str
    interval_end_at_exclusive: str
    inside_interval: bool
    eligibility_resolved: bool
    reason_codes: tuple[str, ...]


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
    promotion: QuotePromotionView | None = None


@dataclass(frozen=True, slots=True)
class UnresolvedQuoteResult:
    """Policy the engine could not resolve, carried verbatim. No row was written."""

    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RangePriceProposalResult:
    """A raised `SET_RANGE_PRICE` envelope and the exact binding an approver must hand back.

    The three fields beside the approval are what `ApprovalDecisionRequest` requires as
    `resource_version`, `snapshot_hash` and `rendered_hash`. The request path of
    `ApprovalRepository` does not project them -- `list_pending` does, and a queue read is not what
    the person who just proposed a price is looking at -- so they are returned here rather than
    leaving the console to reconstruct a digest it must not be computing in the first place.
    """

    approval: StoredApproval
    resource_version: int
    snapshot_hash: str
    rendered_hash: str


@dataclass(frozen=True, slots=True)
class QuoteLineView:
    """One line as a console must render it: what it is, and what it costs or may cost.

    `net_amount_vnd` and the band are mutually exclusive and never both absent. An exact line has
    the amount; an open band has the two bounds and no amount, because there is no amount -- and a
    read model that filled one in from the other would be inventing the number this whole item
    exists to keep a person responsible for.
    """

    line_id: str
    service_code: str
    quantity: str
    unit: str
    price_kind: str
    net_amount_vnd: int | None
    band_minimum_vnd: int | None
    band_maximum_vnd: int | None


@dataclass(frozen=True, slots=True)
class QuoteRevisionView:
    """One stored revision with its lines. Read-only; every number is off the immutable snapshot."""

    quote_id: UUID
    revision: int
    row_version: int
    finality: str
    status: str
    snapshot_hash: str
    display_total_min_vnd: int | None
    display_total_max_vnd: int | None
    valid_until: datetime | None
    reason_codes: tuple[str, ...]
    lines: tuple[QuoteLineView, ...]
    #: When the customer agreed the price that produced this revision (`DEC-021`), read from the
    #: attestation. Null for a revision no acceptance produced.
    customer_accepted_at: datetime | None = None
    #: `READ-ENRICH-001`: the intake request the quote is bound to, its customer reference, and the
    #: fulfilment mode this revision was priced under -- what an order create needs, read rather
    #: than pasted. See `QuoteBinding`.
    order_request_id: UUID | None = None
    contact_binding_id: UUID | None = None
    fulfillment_mode: str | None = None


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
class StoredCollectionResult:
    """A recorded pickup (`DEC-032`), as the idempotency ledger stored and replays it."""

    collection_id: UUID
    order_id: UUID
    settlement_id: UUID
    collected_by_staff_id: UUID
    collected_at: datetime
    self_collection_recorded: bool
    row_version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class StoredManualSendResult:
    value: StoredManualSend
    row_version: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class StoredTransactionalReleaseResult:
    """A recorded TRANSACTIONAL release (`DEC-033`), as the idempotency ledger stored it."""

    release_consent_event_id: UUID
    contact_binding_id: UUID
    channel: str
    purpose: str
    previous_state: str
    state: str
    evidence_webhook_event_id: UUID
    released_at: datetime
    replayed: bool


@dataclass(frozen=True, slots=True)
class StoredIncidentResult:
    incident_id: UUID
    status: str
    fault_decided: bool
    remedy_decided: bool
    replayed: bool


@dataclass(frozen=True, slots=True)
class StoredRemedyProposalResult:
    """A recorded remedy proposal and what the server decided about it. `REMEDY-001`.

    Before `DEC-031` a loss came back `REQUIRE_HUMAN` with `reason_code = LOSS_POLICY_UNRESOLVED`.
    A loss is now a proposal like damage that always waits for the owner, so `outcome` is `ALLOW`,
    `status` is `OWNER_APPROVAL_REQUIRED`, and `owner_reasons` says why (`LOSS_CLAIM`).
    `reason_code` stays on the contract and is `None`.
    """

    proposal_id: UUID
    incident_id: UUID
    order_id: UUID
    kind: str
    status: str
    outcome: str
    proposal_hash: str
    policy_version: int
    amount_vnd: int | None
    ceiling_vnd: int | None
    window_opened_at: str | None
    window_closes_at: str | None
    approval_id: UUID | None
    reason_code: str | None
    replayed: bool
    #: `OwnerReason` values; empty when staff may authorise. A replay of a proposal recorded before
    #: this field existed reads as empty, which is only ever shown, never decided on.
    owner_reasons: tuple[str, ...] = ()
    #: `REMEDY-GARMENT-001`: the garment recorded. A replay of a proposal recorded before the field
    #: existed reads as `None`, which is what those rows hold.
    garment_index: int | None = None


@dataclass(frozen=True, slots=True)
class StoredRemedyExecutionResult:
    proposal_id: UUID
    incident_id: UUID
    order_id: UUID
    kind: str
    status: str
    #: `REWASH_COMMANDED` or `CREDIT_EXECUTED`, the domain-event names the incident eval queries.
    event_type: str
    credit_id: UUID | None
    amount_vnd: int | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class StoredCreditRedemptionResult:
    credit_id: UUID
    quote_id: UUID
    revision: int
    snapshot_hash: str
    credit_vnd: int
    net_service_subtotal_vnd: int
    display_total_vnd: int | None
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


def _require_order_store_membership(
    connection: Any, order_id: UUID, principal: StaffPrincipal
) -> None:
    """Prove membership of an order's store before any idempotency replay can answer for it.

    A missing order returns quietly; the write path refuses it with the same opaque message a
    non-member gets, so this cannot become an oracle for which order ids exist.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT store_id FROM orders WHERE id = %s", (order_id,))
        row = cursor.fetchone()
        if row is None:
            return
        store_id = row[0] if isinstance(row[0], UUID) else UUID(str(row[0]))
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )


class _AcceptanceUnresolved(Exception):
    """The acceptance engine refused, raised so the idempotency claim rolls back with it.

    An unresolved acceptance writes nothing, so it must not consume the caller's key: the operator
    weighs the laundry the customer had estimated, presses chốt again with the same key, and that
    retry has to reach the engine rather than replay a refusal.
    """

    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        super().__init__("acceptance is unresolved")
        self.reason_codes = reason_codes


class OperationsService:
    """Own connection lifetimes while repositories own transactional semantics."""

    def __init__(
        self,
        settings: AuthSettings,
        connection_factory: Callable[[str], Any] = application_connect,
    ) -> None:
        if not settings.database_url:
            raise OperationsUnavailable("operations database is not configured")
        self._database_url = settings.database_url
        self._connection_factory = connection_factory
        self._orders = OrderRepository()
        self._approvals = ApprovalRepository()
        self._manual_sends = ManualSendRepository()
        self._incidents = IncidentRepository()
        self._remedies = RemedyProposalRepository()
        self._remedy_credits = RemedyCreditRepository()
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
        acquisition_source: AcquisitionSource,
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
                    acquisition_source,
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
        custody_resolution: CustodyResolution | None = None,
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
                    custody_resolution=custody_resolution,
                ),
            )

    def transition_intake(
        self,
        *,
        order_id: UUID,
        target: IntakeStatus,
        expected_row_version: int,
        idempotency_key: str,
        principal: StaffPrincipal,
        slot_approved: bool = False,
    ) -> StoredOrder:
        """Move intake, deriving the readiness the server already knows.

        `IntakeReadiness` carries six facts and five of them are things this system holds: whether
        custody was recorded, whether the quantity was weighed rather than estimated, whether the
        services came from the published pricebook, whether an exact price was approved, and whether
        the customer reconfirmed it. Letting a client assert those would let a client assert server
        state -- a caller could claim an exact price was approved for a quote that is still an
        estimate, and the order would accept it.

        So they are read, not received. The one fact left is `slot_approved`: capacity is a human
        judgement this system deliberately never makes -- `evaluate_delivery` returns
        `slot_outcome=REQUIRE_HUMAN` for every order because Shadow stage has no auto-confirmable
        capacity -- so the operator attests it and only it.
        """

        with self._connection_factory(self._database_url) as connection:
            readiness: IntakeReadiness | None = None
            accepted_at: datetime | None = None
            if target is IntakeStatus.ACCEPTED:
                with connection.cursor() as cursor:
                    readiness = self._derive_intake_readiness(
                        cursor,
                        order_id=order_id,
                        staff_user_id=principal.staff_user_id,
                        slot_approved=slot_approved,
                    )
                accepted_at = datetime.now(UTC)
            return self._orders.transition(
                connection,
                OrderTransitionCommand(
                    order_id,
                    expected_row_version,
                    principal,
                    idempotency_key,
                    uuid4(),
                    intake_target=target,
                    intake_readiness=readiness,
                    production_accepted_at=accepted_at,
                ),
            )

    def transition_production(
        self,
        *,
        order_id: UUID,
        target: ProductionStatus,
        expected_row_version: int,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredOrder:
        """Move production. The sequence and its legality are the domain's, not this method's."""

        with self._connection_factory(self._database_url) as connection:
            return self._orders.transition(
                connection,
                OrderTransitionCommand(
                    order_id,
                    expected_row_version,
                    principal,
                    idempotency_key,
                    uuid4(),
                    production_target=target,
                ),
            )

    @staticmethod
    def _derive_intake_readiness(
        cursor: Any, *, order_id: UUID, staff_user_id: UUID, slot_approved: bool
    ) -> IntakeReadiness:
        """Read the five facts the system holds, and take the sixth from the operator.

        Scoped to the caller's own stores. `OrderRepository.transition` is what actually authorizes
        the write, and it refuses a non-member -- but it runs after this, so without the membership
        predicate below a non-member probing `order_id` would get "order is missing" for an
        identifier that does not exist and a different refusal for one that does. That is an
        existence oracle across stores, and `store_access` promises the opposite in as many words:
        the two failures are "deliberately indistinguishable to the caller, so probing identifiers
        teaches nobody which stores exist". Filtering here rather than raising a second error keeps
        that promise -- a non-member and a stranger's identifier produce the same empty row.
        """

        # ORDER-STEPS-001: one derivation. The five facts are read by the same columns and decided
        # by the same `derive_intake_readiness` the `RECEIVE` step and `next_steps` use, so the
        # per-axis route and the composite step cannot disagree about whether an order may be
        # accepted. The membership filter lives in that statement, unchanged.
        return OrderRepository.intake_readiness_for(
            cursor, order_id=order_id, staff_user_id=staff_user_id, slot_approved=slot_approved
        )

    def execute_order_step(
        self,
        *,
        order_id: UUID,
        step: OrderStep,
        expected_row_version: int,
        idempotency_key: str,
        principal: StaffPrincipal,
        slot_approved: bool = False,
        custody_resolution: CustodyResolution | None = None,
    ) -> OrderStepResult:
        """`ORDER-STEPS-001`: one named business step, as its domain transitions, all or nothing.

        A pass-through: the plan, the readiness facts, the authorization and the atomicity are all
        `OrderRepository.execute_step`'s. `slot_approved` is the operator's attestation and the only
        readiness fact a caller supplies, exactly as on the per-axis intake route.
        """

        with self._connection_factory(self._database_url) as connection:
            return self._orders.execute_step(
                connection,
                OrderStepCommand(
                    order_id=order_id,
                    expected_row_version=expected_row_version,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    correlation_id=uuid4(),
                    step=step,
                    slot_approved=slot_approved,
                    custody_resolution=custody_resolution,
                ),
            )

    def list_orders(
        self,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        limit: int,
        open_only: bool = False,
        ticket_number: int | None = None,
        ticket_date: date | None = None,
    ) -> tuple[OrderView, ...]:
        """The board, optionally narrowed to open orders or to one walk-in ticket.

        A ticket number with no date means today's, on the server's clock and the counter's own
        business day -- the rule the number was issued under (`ticket_business_date`). "Phiếu số
        17" at the counter means today's 17 unless the slip says otherwise, and a browser's clock
        or timezone is not the authority on which day it is in the shop.
        """

        ticket: TicketReference | None = None
        if ticket_number is not None:
            ticket = TicketReference(
                number=ticket_number,
                issued_on=ticket_date or ticket_business_date(datetime.now(UTC)),
            )
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._orders.list_for_store(
                cursor,
                store_id=store_id,
                principal=principal,
                limit=limit,
                open_only=open_only,
                ticket=ticket,
            )

    def read_order(self, *, order_id: UUID, principal: StaffPrincipal) -> OrderView:
        """One order by id. The store, and so the membership check, come from the row."""

        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._orders.read_for_principal(cursor, order_id=order_id, principal=principal)

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
        idempotency_key: str,
        principal: StaffPrincipal,
        reason_code: str | None,
        edited_text: str | None,
    ) -> DraftDecision:
        """Record a reviewer's verdict on an agent draft, once.

        `agent_draft_reviews` is UNIQUE on `agent_run_id` -- one verdict per draft, which is
        right -- and the route declared no `Idempotency-Key` parameter, so FastAPI dropped the
        console has always sent. A resent decision therefore reached the INSERT a second time and
        the UNIQUE violation escaped as HTTP 500. The reviewer, having pressed a button and been
        told the server broke, has no way to know their verdict was in fact recorded.
        """

        decided_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:

            def commit() -> dict[str, object]:
                stored = ShadowConsoleRepository().decide_draft(
                    connection,
                    agent_run_id=agent_run_id,
                    decision=decision,
                    principal=principal,
                    correlation_id=uuid4(),
                    reason_code=reason_code,
                    edited_text=edited_text,
                )
                return {
                    "review_id": str(stored.review_id),
                    "agent_run_id": str(stored.agent_run_id),
                    "decision": stored.decision,
                    "reason_code": stored.reason_code,
                    "edited_text": stored.edited_text,
                    "decided_by_staff_id": str(stored.decided_by_staff_id),
                    "decided_at": stored.decided_at.isoformat(),
                }

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-shadow-decision:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "agent_run_id": str(agent_run_id),
                        "decision": decision,
                        "reason_code": reason_code,
                        "edited_text": edited_text,
                    },
                    occurred_at=decided_at,
                ),
                commit,
            )
        value = result.response
        return DraftDecision(
            review_id=UUID(str(value["review_id"])),
            agent_run_id=UUID(str(value["agent_run_id"])),
            decision=str(value["decision"]),
            reason_code=None if value["reason_code"] is None else str(value["reason_code"]),
            edited_text=None if value["edited_text"] is None else str(value["edited_text"]),
            decided_by_staff_id=UUID(str(value["decided_by_staff_id"])),
            decided_at=datetime.fromisoformat(str(value["decided_at"])),
        )

    def shadow_unknown_sends(
        self, *, store_id: UUID, principal: StaffPrincipal, limit: int = 50
    ) -> tuple[UnknownSend, ...]:
        with self._connection_factory(self._database_url) as connection:
            return ShadowConsoleRepository.list_unknown_sends(
                connection, store_id=store_id, principal=principal, limit=limit
            )

    def shadow_resolve_unknown_send(
        self,
        *,
        receipt_id: UUID,
        resolution: ReconciliationState,
        idempotency_key: str,
        principal: StaffPrincipal,
        note: str | None,
    ) -> bool:
        """Record a person's reading of an unknown send, once. True when this call was a replay.

        `API-INTEGRITY-002`. The route took no `Idempotency-Key`, so a resent resolution -- a
        double tap, a retry after a dropped response -- reached the repository a second time and
        was answered 409 "not awaiting human reconciliation", telling the person who had just
        succeeded that they had failed. Now the same key and body replay the first answer without
        touching the receipt again; the same key with a different body is `IDEMPOTENCY_CONFLICT`.

        A refused resolution (wrong store, wrong role, already settled) raises inside the wrapper,
        which rolls the key's claim back with it, so a refusal never burns a key.
        """

        resolved_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:

            def commit() -> dict[str, object]:
                ShadowConsoleRepository().resolve_unknown_send(
                    connection,
                    receipt_id=receipt_id,
                    resolution=resolution,
                    principal=principal,
                    correlation_id=uuid4(),
                    note=note,
                    now=resolved_at,
                )
                return {
                    "receipt_id": str(receipt_id),
                    "reconciliation_state": resolution.value,
                    "resolved_at": resolved_at.isoformat(),
                }

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-shadow-reconcile:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "receipt_id": str(receipt_id),
                        "resolution": resolution.value,
                        "note": note,
                    },
                    occurred_at=resolved_at,
                ),
                commit,
            )
        return result.replayed

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
        store_id: UUID,
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
                    store_id=store_id,
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

    def read_message_draft_binding(
        self, *, store_id: UUID, agent_run_id: UUID, principal: StaffPrincipal
    ) -> tuple[MessageDraftBinding, MessageDraftSendProgress | None] | None:
        """One draft's server-computed `SEND_MESSAGE` binding (`MESSAGE-DRAFT-BINDING-001`), and
        how far the latest send over it has gone (`MANUAL-SEND-RESUME`).

        A pure read, in one transaction. Role, MFA and membership are checked inside
        `read_message_draft_binding_for_store`, on the cursor that then reads the draft and the
        send progress.
        """

        with (
            self._connection_factory(self._database_url) as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            return read_message_draft_send_state_for_store(
                cursor, store_id=store_id, agent_run_id=agent_run_id, principal=principal
            )

    def list_pending_approvals(
        self, *, principal: StaffPrincipal, limit: int
    ) -> tuple[StoredApproval, ...]:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._approvals.list_pending(cursor, principal=principal, limit=limit)

    # --- FULFILMENT-001 (DEC-023) -----------------------------------------------------------

    def record_delivery_leg(
        self,
        *,
        order_id: UUID,
        leg_kind: DeliveryLegKind,
        outcome: DeliveryLegOutcome,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredDeliveryLeg:
        """Record that the courier took laundry out, and whether it reached the customer.

        `DEC-023`, resolved 2026-08-26. No amount crosses this method: the customer paid the exact
        total at the counter before the laundry left, so a leg attests arrival and nothing else.

        Idempotent since 2026-08-31, for two reasons that met here. The route took no key, so the
        console's `request()` threw before sending and "Ghi nhận chuyến giao" never reached the
        server. And `delivery_legs` is an append-only ledger where a retry is a *new* row by
        design -- correct for a second real attempt, wrong for a resent request, which wrote a
        second attempt nobody made. The key separates the two: same key is the same attempt.
        """

        recorded_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            # Before the idempotency lookup: a replay short-circuits to the stored response, so a
            # check that lives only inside the executor is skipped for a revoked member.
            _require_order_store_membership(connection, order_id, principal)

            def commit() -> dict[str, object]:
                leg = DeliveryLegRepository().record(
                    connection,
                    RecordDeliveryLegCommand(
                        order_id=order_id,
                        leg_kind=leg_kind,
                        outcome=outcome,
                        principal=principal,
                        correlation_id=uuid4(),
                        recorded_at=recorded_at,
                    ),
                )
                return {
                    "leg_id": str(leg.leg_id),
                    "order_id": str(leg.order_id),
                    "leg_kind": leg.leg_kind,
                    "outcome": leg.outcome,
                    "completes_fulfillment": leg.completes_fulfillment,
                }

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-delivery-leg:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "order_id": str(order_id),
                        "leg_kind": leg_kind.value,
                        "outcome": outcome.value,
                    },
                    occurred_at=recorded_at,
                ),
                commit,
            )
        value = result.response
        return StoredDeliveryLeg(
            leg_id=UUID(str(value["leg_id"])),
            order_id=UUID(str(value["order_id"])),
            leg_kind=str(value["leg_kind"]),
            outcome=str(value["outcome"]),
            completes_fulfillment=bool(value["completes_fulfillment"]),
        )

    # --- COUNTER-TICKET-001 (DEC-013) -------------------------------------------------------

    def issue_counter_ticket(
        self, *, store_id: UUID, idempotency_key: str, principal: StaffPrincipal
    ) -> IssuedTicket:
        """Hand a walk-in customer the number their order is known by.

        `DEC-013`, resolved 2026-08-26: a walk-in is identified by a counter-issued number and
        nothing about the person is stored. There is no request body for the same reason -- there is
        nothing to send. A field here would be the privacy policy the decision declined to write.

        Idempotent since 2026-08-31. The route took no key and the console's `request()` refuses to
        issue any mutating call without one, so "Phát phiếu" threw in the browser and never reached
        the server -- the walk-in path was dead at the button. Honouring the key rather than merely
        accepting it also means a double-click at the counter hands over one number, not two.
        """

        issued_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                # Outside the wrapper: a replay would otherwise hand a revoked member a ticket
                # number for a store they no longer belong to.
                require_store_membership(
                    cursor,
                    staff_user_id=principal.staff_user_id,
                    store_id=store_id,
                    error=StoreAccessError,
                )

            def commit() -> dict[str, object]:
                ticket = CounterTicketRepository().issue(
                    connection,
                    store_id=store_id,
                    principal=principal,
                    correlation_id=uuid4(),
                )
                return {
                    "ticket_id": str(ticket.ticket_id),
                    "ticket_number": ticket.ticket_number,
                    "issued_on": ticket.issued_on.isoformat(),
                }

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-counter-ticket:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={"store_id": str(store_id)},
                    occurred_at=issued_at,
                ),
                commit,
            )
        value = result.response
        return IssuedTicket(
            ticket_id=UUID(str(value["ticket_id"])),
            ticket_number=int(str(value["ticket_number"])),
            issued_on=date.fromisoformat(str(value["issued_on"])),
        )

    # --- QUOTE-ACCEPT-001 (DEC-021) ---------------------------------------------------------
    #
    # The staff attestation that a customer agreed to an exact price, ratified by the owner on
    # 2026-08-25: a named staff member on duty confirms it, one person suffices, and the control is
    # attribution plus immutability plus owner review rather than a second signature.
    #
    # The attestation is written to `quote_acceptances`, not to an approval envelope. An envelope is
    # two parties and `approvals.py:542` refuses to let one person be both; forcing an attestation
    # through it would have meant relaxing a control that protects sends, cancellations and every
    # financial action. `SETTLEMENT-001` records money received the same way, and the owner's own
    # resolution names it as the precedent.

    def accept_quote(
        self,
        *,
        store_id: UUID,
        quote_id: UUID,
        expected_current_revision: int,
        expected_snapshot_hash: str,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> QuoteRevisionResult | UnresolvedQuoteResult:
        """Record the attestation and derive the accepted revision from the priced one."""

        if not principal.roles & QUOTE_ACCEPTANCE_ROLES or not principal.mfa_verified:
            raise StoreAccessError("accepting a quote requires an operations role with MFA")
        accepted_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                # Outside the idempotency wrapper, as on `create_quote`: a staff member who has
                # lost this store must not replay a held key into a fresh write.
                require_store_membership(
                    cursor,
                    staff_user_id=principal.staff_user_id,
                    store_id=store_id,
                    error=StoreAccessError,
                )

            def commit() -> dict[str, object]:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT current_revision, row_version, bound_order_request_id
                        FROM quotes WHERE id = %s AND store_id = %s AND lifecycle = 'OPEN'
                        """,
                        (quote_id, store_id),
                    )
                    container = cursor.fetchone()
                    if container is None:
                        raise QuoteStateError("quote is missing, closed, or not in this store")
                    current_revision = int(container[0])
                    row_version = int(container[1])
                    bound_order_request_id = container[2]
                    if current_revision != expected_current_revision:
                        # Someone repriced between the operator reading the screen and pressing the
                        # button. Refusing is the point: an attestation must name the revision the
                        # customer actually agreed to, not whichever one is newest.
                        raise QuoteStateError(
                            "quote moved since it was read; read it to the customer again"
                        )
                    stored = QuoteRepository.get_revision(cursor, quote_id, current_revision)
                    # Read inside the same transaction as the revision, so the programme the
                    # re-verification runs against is the one published at this instant rather than
                    # one that could have moved between two connections.
                    promotion = read_published_promotion_program(cursor)
                if stored is None:
                    raise QuoteStateError("quote revision is missing")
                if not hmac.compare_digest(stored.document.snapshot_hash, expected_snapshot_hash):
                    raise QuoteStateError("quote content changed since it was read")

                priced = parse_quote_revision(json.loads(stored.document.canonical_json))
                # `FR-QTE-010`: an expired quote may not be accepted until it is repriced. The
                # guard existed only at order creation, which is one step too late -- the customer
                # has already been read yesterday's price and has already said yes, and the shop
                # discovers the problem while writing the order. Refusing here is refusing at the
                # moment the price is quoted aloud, which is the only moment a reprice is free.
                #
                # `valid_until` is read from the immutable snapshot rather than the row, because the
                # snapshot is what the customer was quoted from (`P-03`), and against the server's
                # clock rather than any caller-supplied time, for the reason `COUNTER-DEFECTS-001`
                # recorded: a client that decides whether its own request is late decides nothing.
                valid_until = priced.data.valid_until
                if valid_until is not None and accepted_at >= valid_until:
                    raise QuoteStateError(
                        "QUOTE_EXPIRED: this price is out of date and cannot be accepted; "
                        "price the bag again before the customer agrees"
                    )
                composition = accept_quote_revision(
                    priced=priced,
                    revision=current_revision + 1,
                    promotion=promotion,
                    # The server's clock, the same one the expiry check above uses and for the same
                    # reason: a client that decides when its own acceptance happened decides whether
                    # a promotion covers it.
                    accepted_at=accepted_at,
                )
                if isinstance(composition, UnresolvedQuote):
                    # Refused before anything is written. The reason codes name the missing
                    # fact -- most often a quantity the customer estimated, never weighed.
                    # Raised rather than returned so the idempotency claim rolls back with it: the
                    # operator fixes the missing fact and retries, and the same key must still work.
                    raise _AcceptanceUnresolved(composition.reason_codes)

                total = priced.data.totals.display_total_min_vnd
                if total is None:
                    raise _AcceptanceUnresolved(("QUOTE_DELIVERY_FEE_UNRESOLVED",))
                correlation_id = uuid4()
                QuoteAcceptanceRepository().record(
                    connection,
                    QuoteAcceptanceCommand(
                        store_id=store_id,
                        quote_id=quote_id,
                        accepted_revision=current_revision,
                        accepted_snapshot_hash=stored.document.snapshot_hash,
                        final_revision=current_revision + 1,
                        display_total_vnd=total,
                        accepted_by=principal.staff_user_id,
                        correlation_id=correlation_id,
                        policy_version=QUOTE_ACCEPTANCE_POLICY_VERSION,
                        accepted_at=accepted_at,
                    ),
                )
                QuoteRepository().create_revision(
                    connection,
                    QuoteRevisionCommand(
                        store_id=store_id,
                        bound_order_request_id=bound_order_request_id,
                        snapshot=composition.snapshot,
                        expected_current_revision=current_revision,
                        expected_row_version=row_version,
                        created_by=principal.staff_user_id,
                        correlation_id=correlation_id,
                        occurred_at=accepted_at,
                    ),
                )
                # The same projection `create_quote` and `apply_range_prices` use. It used to be
                # spelled out again here, which meant a field added to one of the three reached two
                # of them -- and `PROMO-WIRING-001` adds one.
                return _quote_mapping(composition.snapshot, row_version=row_version + 1)

            try:
                result = self._idempotency.execute(
                    connection,
                    IdempotentCommand(
                        scope=f"staff-quote-accept:{principal.staff_user_id}",
                        key=idempotency_key,
                        payload={
                            "store_id": str(store_id),
                            "quote_id": str(quote_id),
                            "expected_current_revision": expected_current_revision,
                            "expected_snapshot_hash": expected_snapshot_hash,
                        },
                        occurred_at=accepted_at,
                    ),
                    commit,
                )
            except _AcceptanceUnresolved as unresolved:
                return UnresolvedQuoteResult(unresolved.reason_codes)
            except psycopg.errors.UniqueViolation as error:
                # `quote_acceptances` is UNIQUE on both (quote_id, accepted_revision) and
                # (quote_id, final_revision). The INSERT guards the first with ON CONFLICT DO
                # NOTHING and nothing guarded the second, so two operators pressing "chốt" at the
                # same instant produced one 201 and one HTTP 500. Losing a race is a conflict the
                # loser can act on, not a crash.
                raise QuoteStateError("this quote revision has already been accepted") from error
            return _quote_revision_result(result.response, replayed=result.replayed)

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
        present_range_as_band: bool = False,
    ) -> QuoteRevisionResult | UnresolvedQuoteResult:
        """Price through the deterministic engine and commit one immutable revision.

        `present_range_as_band` is the only way a revision containing an open band is written, and
        it is off unless a caller asks: every existing caller wants an exact price, and a band
        arriving where one was expected would be a total that is not a total.
        """
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
                # `None` when the owner has published no programme, which is a state this system
                # supports rather than an error: the quote composes at list price and says
                # `PROMOTION_NOT_PUBLISHED`. Invariant 11 -- there is no constant to fall back on.
                promotion = read_published_promotion_program(cursor)
                # A re-price carries the remedy credits the revision it replaces had reserved, and
                # releases any another order has spent since. Before the credit-lifecycle fix this
                # composed from the lines alone and the credit vanished from the bill; now
                # `QuoteRepository.create_revision` would refuse a revision that dropped one.
                reserved = (
                    read_reserved_remedy_credits(
                        cursor,
                        store_id=store_id,
                        quote_id=quote_id,
                        revision=expected_current_revision,
                    )
                    if quote_id is not None and expected_current_revision > 0
                    else ReservedRemedyCredits()
                )
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
                present_range_as_band=present_range_as_band,
                promotion=promotion,
                remedy_credits=reserved.credits,
                spent_remedy_credit_ids=reserved.spent_ids,
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
                            # Every delivery fact belongs here because every one of them moves the
                            # display total. Omitting them made the ledger hash a request that was
                            # not the request: correcting a delivery fee and resending the same key
                            # replayed the stale total, the acceptance derived it, the order bound
                            # it, and the shop collected less than the price it had quoted -- while
                            # the *correct* amount was refused as not the exact total. A changed
                            # quantity already conflicted properly; the asymmetry was the bug.
                            "fulfillment_mode": fulfillment_mode.value,
                            "verified_distance_m": verified_distance_m,
                            "planned_transport_weight_kg": planned_transport_weight_kg,
                            "approved_manual_fee_vnd": approved_manual_fee_vnd,
                            "customer_acknowledged_manual_fee": customer_acknowledged_manual_fee,
                            # Part of the request for the same reason the delivery facts are: it
                            # decides whether the stored revision is a band or a refusal, so the
                            # same key asking for the other one must conflict rather than replay.
                            "present_range_as_band": present_range_as_band,
                            # The programme in force moves the display total exactly as a delivery
                            # fee does. Leaving it out would let the same key replay a price
                            # computed under a programme that has since been replaced, which is the
                            # bug the delivery facts were added to this payload to fix.
                            "promotion_version_id": (
                                None if promotion is None else str(promotion.version_id)
                            ),
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

    # --- RANGE-PRICE-001 ---------------------------------------------------------------------
    #
    # Twenty of the forty-four published services carry a band rather than a rate, and until this
    # item none of them could be quoted. Closing one is two commands, in this order, and the order
    # is not an implementation detail:
    #
    #   1. `create_quote(present_range_as_band=True)` stores the band the customer was shown.
    #   2. `propose_range_prices` validates the staff member's amounts against *that stored
    #      revision* and raises a `SET_RANGE_PRICE` envelope bound to its digest -- and, since
    #      `DEC-029` (2026-09-25), records the chooser's own counter attestation of it in the same
    #      transaction, so nobody else has to be at the counter.
    #   3. (Before `DEC-029`, and again if it is reversed: a second person decides the envelope on
    #      the existing `/internal/v1/approvals/{id}/decisions` route.)
    #   4. `apply_range_prices` writes the amounts into a new revision.
    #
    # The band revision has to exist first because an approval must bind something real. The
    # alternative -- raising an envelope against a revision that does not exist yet, with a
    # fabricated `snapshot_hash` -- is exactly what migration `0029` was written to stop.
    #
    # Neither method below does arithmetic on money. They establish who is asking, which stored
    # revision answers, and whether the envelope binds this exact content; every number comes back
    # from `packages/domain`.

    def propose_range_prices(
        self,
        *,
        store_id: UUID,
        quote_id: UUID,
        expected_current_revision: int,
        expected_snapshot_hash: str,
        choices: tuple[RangePriceChoice, ...],
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> RangePriceProposalResult | UnresolvedQuoteResult:
        """Check the amounts against the stored band, then raise the envelope for them.

        Nothing is priced here and no revision is written. A refusal therefore costs nothing: an
        out-of-band amount is refused before any approval exists, which is what makes "nothing is
        persisted" true rather than merely tidy.
        """

        if not principal.roles & QUOTE_ACCEPTANCE_ROLES or not principal.mfa_verified:
            raise StoreAccessError("proposing a price inside a band requires an operations role")
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                require_store_membership(
                    cursor,
                    staff_user_id=principal.staff_user_id,
                    store_id=store_id,
                    error=StoreAccessError,
                )
                priced = self._bound_band_revision(
                    cursor,
                    store_id=store_id,
                    quote_id=quote_id,
                    expected_current_revision=expected_current_revision,
                    expected_snapshot_hash=expected_snapshot_hash,
                )
            proposal = _range_price_attestation(priced, choices)
            if proposal is None:
                raise QuoteStateError("this quote revision carries no price band")
            refusal = _band_refusal(priced, proposal)
            if refusal is not None:
                return UnresolvedQuoteResult((refusal,))
            bands = stored_price_bands(priced)
            if bands is None:
                # `_band_refusal` has already read the same bands and returned a refusal code for
                # an unreadable revision, so this cannot be reached. It is not an assertion for
                # the type checker's benefit alone: reaching it would mean the two reads disagreed,
                # and proposing amounts against bands nobody could name is the failure this whole
                # item is about.
                raise QuoteStateError("this quote revision carries no price band")
            rendered_hash = range_price_rendered_document(proposal).snapshot_hash
            approval = self._approvals.request(
                connection,
                ApprovalRequestCommand(
                    ApprovalAction.SET_RANGE_PRICE,
                    # `APPROVAL_RESOURCE_TYPES[SET_RANGE_PRICE]`, not a literal chosen here:
                    # `build_approval_envelope` refuses any other value for this action.
                    APPROVAL_RESOURCE_TYPES[ApprovalAction.SET_RANGE_PRICE],
                    quote_id,
                    priced.data.revision,
                    priced.document.snapshot_hash,
                    rendered_hash,
                    RANGE_PRICE_POLICY_VERSION,
                    principal.staff_user_id,
                    idempotency_key,
                    uuid4(),
                    store_id=store_id,
                ),
            )
            # `RANGE-APPROVAL-VISIBILITY-001`. The envelope carries a digest; the owner has to read
            # a number. These rows are that number, kept beside the band it was checked against so
            # the approvals surface can put both in front of the approver before offering them an
            # approve control. Nothing below is consulted when the approval is applied -- that path
            # still re-derives the digest from the amounts the caller holds.
            #
            # Written second because `range_price_proposals.approval_id` is a foreign key, and
            # skipped on a replay because the envelope is idempotent and its amounts are already
            # stored under this same approval id.
            if not approval.replayed:
                RangePriceProposalRepository.record(
                    connection,
                    RecordRangePriceProposalCommand(
                        approval_id=approval.approval_request_id,
                        store_id=store_id,
                        quote_id=quote_id,
                        revision=priced.data.revision,
                        pricebook_version_id=proposal.pricebook_version_id,
                        pricebook_version=proposal.pricebook_version,
                        rendered_hash=rendered_hash,
                        proposed_by=principal.staff_user_id,
                        correlation_id=uuid4(),
                        lines=tuple(
                            ProposedRangePriceLine(
                                service_code=choice.service_code,
                                # The bound is read from the stored revision, never from the
                                # request: what is displayed to the approver has to be the interval
                                # the amount was actually checked against.
                                band_minimum_vnd=bands[choice.service_code].minimum_vnd,
                                band_maximum_vnd=bands[choice.service_code].maximum_vnd,
                                proposed_amount_vnd=choice.amount_vnd,
                            )
                            for choice in choices
                        ),
                    ),
                )
                # `DEC-029` (2026-09-25): choosing inside the band is the chooser's own counter
                # attestation, recorded in this same transaction as the envelope and the amounts --
                # so there is never an envelope for these amounts that nobody has attested, and
                # never an attestation with no amounts behind it (invariant 5). Keyed on the policy
                # table rather than assumed, so the one-line reversal the ruling names puts the
                # envelope back in front of the owner and this block simply stops running.
                if (
                    APPROVAL_POLICIES[ApprovalAction.SET_RANGE_PRICE].execution_capability
                    == COUNTER_ATTESTED_CAPABILITY
                ):
                    approval = self._approvals.attest(
                        connection,
                        ApprovalAttestationCommand(
                            approval_request_id=approval.approval_request_id,
                            observed_resource_version=priced.data.revision,
                            observed_snapshot_hash=priced.document.snapshot_hash,
                            observed_rendered_hash=rendered_hash,
                            reason_code=RANGE_PRICE_COUNTER_ATTESTED,
                            principal=principal,
                            correlation_id=uuid4(),
                        ),
                    )
            else:
                # A replay answers with the envelope's state now, not the state stored when it was
                # first raised: the stored request reply says REQUESTED, and on the counter path
                # that stopped being true in the same transaction that wrote it.
                with connection.cursor() as cursor:
                    current = read_approval_binding(cursor, approval.approval_request_id)
                if current is not None:
                    approval = replace(approval, status=current.status)
            return RangePriceProposalResult(
                approval=approval,
                resource_version=priced.data.revision,
                snapshot_hash=priced.document.snapshot_hash,
                rendered_hash=rendered_hash,
            )

    def read_range_price_proposal(
        self, *, approval_id: UUID, principal: StaffPrincipal
    ) -> RangePriceProposalRecord | None:
        """The amounts one `SET_RANGE_PRICE` envelope asks the owner to authorise.

        A pure read. It decides nothing and reserves nothing: it exists so that the person holding
        the only second-party control over a staff-chosen price can see that price. Membership is
        required against the store recorded on the proposal row itself, inside the repository.
        """

        if not principal.roles & APPROVAL_DECISION_ROLES or not principal.mfa_verified:
            raise StoreAccessError("reading an approval's proposed amounts requires an approver")
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return RangePriceProposalRepository.read(
                cursor, approval_id=approval_id, principal=principal
            )

    def list_range_price_reviews(
        self,
        *,
        store_id: UUID,
        business_date: date,
        principal: StaffPrincipal,
        limit: int = 100,
    ) -> tuple[RangePriceReviewEntry, ...]:
        """The owner's review of prices chosen inside a band on one business day (`DEC-029`).

        Approvers only, with MFA -- the same rule as the single read. The staff member who chose a
        price is not the person reviewing it: an OPERATOR is refused here even for their own store.
        """

        if not principal.roles & APPROVAL_DECISION_ROLES or not principal.mfa_verified:
            raise StoreAccessError("reviewing chosen range prices requires an approver")
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return RangePriceProposalRepository.list_for_store_day(
                cursor,
                store_id=store_id,
                business_date=business_date,
                principal=principal,
                limit=limit,
            )

    def apply_range_prices(
        self,
        *,
        store_id: UUID,
        quote_id: UUID,
        approval_id: UUID,
        expected_current_revision: int,
        expected_snapshot_hash: str,
        choices: tuple[RangePriceChoice, ...],
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> QuoteRevisionResult | UnresolvedQuoteResult:
        """Write the approved amounts into a new revision derived from the band revision.

        The amounts are supplied again rather than read from the envelope, because the envelope
        stores a digest and not the content -- `approvals.py` says so plainly and calls comparing an
        unstored rendering "theatre". Re-deriving the digest from the amounts in hand and demanding
        it equal the one that was attested (by the chooser since `DEC-029`) is the check that is
        not theatre: it proves the caller
        holds the same content, and invariant 8 asks for nothing weaker and nothing more.

        Editing a line invalidates the approval by construction rather than by a rule written here.
        An edit is a new revision, the container moves past the one the envelope named, and every
        comparison below then fails.
        """

        if not principal.roles & QUOTE_ACCEPTANCE_ROLES or not principal.mfa_verified:
            raise StoreAccessError("closing a price band requires an operations role")
        applied_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            with connection.cursor() as cursor:
                require_store_membership(
                    cursor,
                    staff_user_id=principal.staff_user_id,
                    store_id=store_id,
                    error=StoreAccessError,
                )
                binding = read_approval_binding(cursor, approval_id)
                container = QuoteRepository.find_container_by_id(cursor, store_id, quote_id)
                priced = self._bound_band_revision(
                    cursor,
                    store_id=store_id,
                    quote_id=quote_id,
                    expected_current_revision=expected_current_revision,
                    expected_snapshot_hash=expected_snapshot_hash,
                )
                promotion = read_published_promotion_program(cursor)
            if container is None:
                raise QuoteStateError("quote is missing, closed, or not in this store")
            attestation = _range_price_attestation(priced, choices, approval_id=approval_id)
            if attestation is None:
                raise QuoteStateError("this quote revision carries no price band")
            _require_range_price_approval(
                binding,
                store_id=store_id,
                quote_id=quote_id,
                priced=priced,
                rendered_hash=range_price_rendered_document(attestation).snapshot_hash,
                at=applied_at,
            )
            composition = close_range_prices(
                priced=priced,
                revision=priced.data.revision + 1,
                attestation=attestation,
                promotion=promotion,
                # A promotion applies to the amount the staff member just chose, evaluated at the
                # moment they chose it. See `close_range_prices` for why the band is the wrong base.
                closed_at=applied_at,
            )
            if isinstance(composition, UnresolvedQuote):
                # Refused before anything is written, exactly as `create_quote` refuses: an
                # unresolved price is not an outcome a caller should be able to replay into
                # existence later, so no idempotency record is claimed either.
                return UnresolvedQuoteResult(composition.reason_codes)
            snapshot = composition.snapshot

            def commit() -> dict[str, object]:
                QuoteRepository().create_revision(
                    connection,
                    QuoteRevisionCommand(
                        store_id=store_id,
                        bound_order_request_id=container.bound_order_request_id,
                        snapshot=snapshot,
                        expected_current_revision=priced.data.revision,
                        expected_row_version=container.row_version,
                        created_by=principal.staff_user_id,
                        correlation_id=uuid4(),
                        occurred_at=applied_at,
                    ),
                )
                return _quote_mapping(snapshot, row_version=container.row_version + 1)

            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-range-price:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "store_id": str(store_id),
                        "quote_id": str(quote_id),
                        "approval_id": str(approval_id),
                        "expected_current_revision": expected_current_revision,
                        "expected_snapshot_hash": expected_snapshot_hash,
                        # The amounts are part of what makes this request this request: the same
                        # key with different numbers must conflict rather than replay the old ones.
                        "choices": [
                            {"service_code": choice.service_code, "amount_vnd": choice.amount_vnd}
                            for choice in sorted(choices, key=lambda item: item.service_code)
                        ],
                    },
                    occurred_at=applied_at,
                ),
                commit,
            )
            return _quote_revision_result(result.response, replayed=result.replayed)

    def read_quote(
        self,
        *,
        store_id: UUID,
        quote_id: UUID,
        principal: StaffPrincipal,
        revision: int | None = None,
    ) -> QuoteRevisionView:
        """One revision with its lines, so a console can render a band and what was chosen in it.

        The list read returns totals only, which is enough to show a quote and not enough to close
        one: closing a band needs the bound per line, and the bound lives on the line.
        """

        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as (cursor),
        ):
            require_store_membership(
                cursor,
                staff_user_id=principal.staff_user_id,
                store_id=store_id,
                error=StoreAccessError,
            )
            container = QuoteRepository.find_container_by_id(cursor, store_id, quote_id)
            if container is None:
                # No lifecycle condition, unlike the command paths: a quote that became an order is
                # exactly when somebody asks what was charged for it.
                raise QuoteStateError("quote is missing or not in this store")
            target = container.current_revision if revision is None else revision
            stored = QuoteRepository.get_revision(cursor, quote_id, target)
            accepted_at = QuoteAcceptanceRepository.accepted_at_for_final_revision(
                cursor, store_id=store_id, quote_id=quote_id, final_revision=target
            )
            binding = QuoteRepository.binding_for_revision(
                cursor, store_id=store_id, quote_id=quote_id, revision=target
            )
        if stored is None:
            raise QuoteStateError("quote revision is missing")
        priced = parse_quote_revision(json.loads(stored.document.canonical_json))
        view = _quote_revision_view(
            priced,
            snapshot_hash=stored.document.snapshot_hash,
            row_version=container.row_version,
            customer_accepted_at=accepted_at,
        )
        if binding is None:
            return view
        return replace(
            view,
            order_request_id=binding.order_request_id,
            contact_binding_id=binding.contact_binding_id,
            fulfillment_mode=binding.fulfillment_mode,
        )

    def _bound_band_revision(
        self,
        cursor: Any,
        *,
        store_id: UUID,
        quote_id: UUID,
        expected_current_revision: int,
        expected_snapshot_hash: str,
    ) -> ImmutableQuoteSnapshot:
        """The current revision of this store's quote, proven to be the one the caller read.

        Both compare-and-swap checks, for the reason `accept_quote` gives: an amount chosen against
        a revision that has since moved was chosen against a price nobody is offering any more.
        """

        container = QuoteRepository.find_container_by_id(cursor, store_id, quote_id)
        if container is None or container.lifecycle != "OPEN":
            raise QuoteStateError("quote is missing, closed, or not in this store")
        if container.current_revision != expected_current_revision:
            raise QuoteStateError("quote moved since it was read; read the band again")
        stored = QuoteRepository.get_revision(cursor, quote_id, container.current_revision)
        if stored is None:
            raise QuoteStateError("quote revision is missing")
        if not hmac.compare_digest(stored.document.snapshot_hash, expected_snapshot_hash):
            raise QuoteStateError("quote content changed since it was read")
        return parse_quote_revision(json.loads(stored.document.canonical_json))

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

    def read_service_messaging_state(
        self,
        *,
        store_id: UUID,
        contact_binding_id: UUID,
        channel: str,
        principal: StaffPrincipal,
    ) -> ServiceMessagingState:
        """A contact's TRANSACTIONAL state and the messages a release may cite (`DEC-033`).

        A read: it takes no advisory lock and decides nothing. The egress answer it carries is
        what the guard would say now, and the send asks the guard again under the lock.
        """

        with self._connection_factory(self._database_url) as connection:
            return read_service_messaging_state(
                connection,
                store_id=store_id,
                contact_binding_id=contact_binding_id,
                channel=channel,
                principal=principal,
                at=datetime.now(UTC),
            )

    def release_transactional_suppression(
        self,
        *,
        store_id: UUID,
        contact_binding_id: UUID,
        channel: str,
        evidence_webhook_event_id: UUID,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredTransactionalReleaseResult:
        with self._connection_factory(self._database_url) as connection:
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-consent-release:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "store_id": str(store_id),
                        "contact_binding_id": str(contact_binding_id),
                        "channel": channel,
                        "evidence_webhook_event_id": str(evidence_webhook_event_id),
                    },
                ),
                lambda: _transactional_release_mapping(
                    release_transactional_suppression(
                        connection,
                        TransactionalReleaseCommand(
                            store_id=store_id,
                            contact_binding_id=contact_binding_id,
                            channel=channel,
                            evidence_webhook_event_id=evidence_webhook_event_id,
                            principal=principal,
                            correlation_id=uuid4(),
                        ),
                    )
                ),
            )
        value = result.response
        return StoredTransactionalReleaseResult(
            release_consent_event_id=UUID(str(value["release_consent_event_id"])),
            contact_binding_id=UUID(str(value["contact_binding_id"])),
            channel=str(value["channel"]),
            purpose=str(value["purpose"]),
            previous_state=str(value["previous_state"]),
            state=str(value["state"]),
            evidence_webhook_event_id=UUID(str(value["evidence_webhook_event_id"])),
            released_at=datetime.fromisoformat(str(value["released_at"])),
            replayed=result.replayed,
        )

    def open_incident(
        self,
        *,
        store_id: UUID,
        order_id: UUID,
        evidence_summary: str,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredIncidentResult:
        """Take a complaint at the counter. `DEC-028`: both digests are the server's to compute.

        The caller sends what the customer said and the order it is about. It does not send a
        contact scope, because a staff member naming a scope is a staff member choosing whose
        incident this is, and invariant 9 puts that outside client control.
        """

        opened_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-incident-open:{principal.staff_user_id}",
                    key=idempotency_key,
                    # The digest, not the text. `request_hash` is an unsalted commitment stored in a
                    # row `protect_idempotency_record` never lets anyone delete, so putting the
                    # summary itself here would place a permanent commitment to a customer's
                    # complaint beside a 365-day schedule -- the DEC-018 shape. Two calls with the
                    # same summary still produce the same payload, so idempotency is unchanged.
                    payload={
                        "store_id": str(store_id),
                        "order_id": str(order_id),
                        "evidence_summary_hash": evidence_summary_digest(evidence_summary),
                    },
                    occurred_at=opened_at,
                ),
                lambda: _incident_mapping(
                    self._incidents.open_from_counter(
                        connection,
                        StaffIncidentOpenCommand(
                            store_id=store_id,
                            order_id=order_id,
                            evidence_summary=evidence_summary,
                            actor_id=principal.staff_user_id,
                            correlation_id=uuid4(),
                            opened_at=opened_at,
                        ),
                        principal=principal,
                    )
                ),
            )
        return _stored_incident_result(result.response, replayed=result.replayed)

    # --- REMEDY-001 --------------------------------------------------------------------------
    #
    # Four methods and no arithmetic. Every ceiling, window and rate is decided by
    # `nha_trang_laundry_domain.remedies` from a published `REMEDY_POLICY` version; this layer
    # resolves a connection, forwards, and returns what the repository gives back.

    def remedy_options(
        self, *, store_id: UUID, incident_id: UUID, principal: StaffPrincipal
    ) -> RemedyOptions:
        """What the server would allow for this incident, before anybody types anything.

        The form has to show the kind, the computed ceiling, the window and whether the owner will
        be needed *first*. Staff discovering after filling a form in that the owner is required is
        the failure this read exists to prevent, and it is a read: nothing is written and nothing is
        reserved by asking.
        """

        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._remedies.options(
                cursor, store_id=store_id, incident_id=incident_id, principal=principal
            )

    def propose_remedy(
        self,
        *,
        store_id: UUID,
        incident_id: UUID,
        kind: RemedyKind,
        store_fault_attested: bool,
        order_line_id: str | None,
        amount_vnd: int | None,
        attested_late_by_minutes: int | None,
        idempotency_key: str,
        principal: StaffPrincipal,
        garment_index: int | None = None,
    ) -> StoredRemedyProposalResult:
        """Record what a staff member proposed, after the server checked it against `DEC-004`."""

        proposed_at = datetime.now(UTC)
        # The same key with a different garment is a different claim, so the garment is part of the
        # payload. Only when named: a request that names none hashes exactly as it did before
        # `REMEDY-GARMENT-001`, so a retry of a request made then still replays instead of
        # conflicting with itself.
        garment_payload: dict[str, object] = (
            {} if garment_index is None else {"garment_index": garment_index}
        )
        with self._connection_factory(self._database_url) as connection:
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-remedy-propose:{principal.staff_user_id}",
                    key=idempotency_key,
                    # The amount and the line are part of what makes this request this request: the
                    # same key with a different figure must conflict rather than replay the old one.
                    payload={
                        "store_id": str(store_id),
                        "incident_id": str(incident_id),
                        "kind": kind.value,
                        "store_fault_attested": store_fault_attested,
                        "order_line_id": order_line_id,
                        "amount_vnd": amount_vnd,
                        "attested_late_by_minutes": attested_late_by_minutes,
                        **garment_payload,
                    },
                    occurred_at=proposed_at,
                ),
                lambda: _remedy_proposal_mapping(
                    self._remedies.propose(
                        connection,
                        RemedyProposalCommand(
                            store_id=store_id,
                            incident_id=incident_id,
                            kind=kind,
                            store_fault_attested=store_fault_attested,
                            principal=principal,
                            correlation_id=uuid4(),
                            order_line_id=order_line_id,
                            amount_vnd=amount_vnd,
                            attested_late_by_minutes=attested_late_by_minutes,
                            proposed_at=proposed_at,
                            garment_index=garment_index,
                        ),
                    )
                ),
            )
        return _stored_remedy_proposal_result(result.response, replayed=result.replayed)

    def execute_remedy(
        self, *, proposal_id: UUID, idempotency_key: str, principal: StaffPrincipal
    ) -> StoredRemedyExecutionResult:
        """Carry out an authorised remedy: issue the credit, or command the rewash."""

        executed_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-remedy-execute:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={"proposal_id": str(proposal_id)},
                    occurred_at=executed_at,
                ),
                lambda: _remedy_execution_mapping(
                    self._remedies.execute(
                        connection,
                        RemedyExecutionCommand(
                            proposal_id=proposal_id,
                            principal=principal,
                            correlation_id=uuid4(),
                            executed_at=executed_at,
                        ),
                    )
                ),
            )
        return _stored_remedy_execution_result(result.response, replayed=result.replayed)

    def redeem_remedy_credit(
        self,
        *,
        store_id: UUID,
        quote_id: UUID,
        credit_id: UUID,
        expected_current_revision: int,
        expected_snapshot_hash: str,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredCreditRedemptionResult:
        """Spend one credit against the next bill, as a revision that carries its discount."""

        redeemed_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-remedy-redeem:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "store_id": str(store_id),
                        "quote_id": str(quote_id),
                        "credit_id": str(credit_id),
                        "expected_current_revision": expected_current_revision,
                        "expected_snapshot_hash": expected_snapshot_hash,
                    },
                    occurred_at=redeemed_at,
                ),
                lambda: _credit_redemption_mapping(
                    self._remedy_credits.redeem(
                        connection,
                        RemedyCreditRedemptionCommand(
                            store_id=store_id,
                            quote_id=quote_id,
                            credit_id=credit_id,
                            expected_current_revision=expected_current_revision,
                            expected_snapshot_hash=expected_snapshot_hash,
                            principal=principal,
                            correlation_id=uuid4(),
                            redeemed_at=redeemed_at,
                        ),
                    )
                ),
            )
        return _stored_credit_redemption_result(result.response, replayed=result.replayed)

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
            # Before the idempotency lookup, for the same reason as the delivery-leg path: a
            # replay answers without running anything inside the executor, and this route moves
            # money.
            _require_order_store_membership(connection, order_id, principal)
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

    # --- PREPAID-DROPOFF-001 (DEC-032) -------------------------------------------------------

    def record_collection(
        self,
        *,
        order_id: UUID,
        expected_row_version: int,
        idempotency_key: str,
        principal: StaffPrincipal,
    ) -> StoredCollectionResult:
        """Record that the walk-in customer who paid at drop-off has taken their laundry.

        Idempotent on the caller's key, with `expected_row_version` in the payload: the same key
        and the same `If-Match` replay the recorded pickup, and the same key with a different one
        is a conflict rather than a replay of something the caller did not ask for.
        """
        collected_at = datetime.now(UTC)
        with self._connection_factory(self._database_url) as connection:
            # Before the idempotency lookup, as on settlement: a replay answers without running
            # anything inside the executor, and a revoked member must not replay a held key.
            _require_order_store_membership(connection, order_id, principal)
            result = self._idempotency.execute(
                connection,
                IdempotentCommand(
                    scope=f"staff-collection:{principal.staff_user_id}",
                    key=idempotency_key,
                    payload={
                        "order_id": str(order_id),
                        "expected_row_version": expected_row_version,
                    },
                    occurred_at=collected_at,
                ),
                lambda: _collection_mapping(
                    SettlementRepository().record_collection(
                        connection,
                        CollectionCommand(
                            order_id=order_id,
                            expected_row_version=expected_row_version,
                            principal=principal,
                            correlation_id=uuid4(),
                            collected_at=collected_at,
                        ),
                    )
                ),
            )
        return _stored_collection_result(result.response, replayed=result.replayed)

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

    def list_member_stores(
        self, *, principal: StaffPrincipal
    ) -> tuple[tuple[UUID, str | None], ...]:
        """Return the stores this principal is assigned to, in a stable order, with their names.

        Every store-scoped route refuses a principal who is not an assigned member, and until this
        existed the console had no way to learn which stores those are — the runbook told staff to
        paste a UUID by hand.

        It used to return identifiers alone, because when it was written there was no `stores` table
        and a name would have had to be invented. `STORE-REGISTRY-001` created that table and every
        store now has the name the people who work there use, so the identifier alone is no longer
        the honest maximum — it is just less than what the database knows. An owner with two shops
        was choosing between `11111111…5555` and `5442b740…aaa7` in the app bar, and picking the
        wrong one files a real order against the wrong shop.

        The name is left nullable rather than defaulted: a `store_id` that predates the registry has
        no row to name it, and `null` is the accurate answer there. The console shows the identifier
        in that case, which is exactly what it did for every store before.
        """

        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            assigned = sorted(member_store_ids(cursor, staff_user_id=principal.staff_user_id))
            if not assigned:
                return ()
            cursor.execute(
                "SELECT id, name FROM stores WHERE id = ANY(%s)",
                (list(assigned),),
            )
            names = {row[0]: row[1] for row in cursor.fetchall()}
            return tuple((store_id, names.get(store_id)) for store_id in assigned)

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

    def list_order_incidents(
        self, *, store_id: UUID, order_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[IncidentSummary, ...]:
        """`READ-ENRICH-001`: one order's incidents. Membership is the repository's."""

        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._incidents.list_for_order(
                cursor, store_id=store_id, order_id=order_id, principal=principal, limit=limit
            )

    def read_incident(
        self, *, store_id: UUID, incident_id: UUID, principal: StaffPrincipal
    ) -> IncidentSummary | None:
        """`READ-ENRICH-001`: one incident of this store, or `None`."""

        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return self._incidents.read_for_store(
                cursor, store_id=store_id, incident_id=incident_id, principal=principal
            )

    # --- READ-PATHS-001 -----------------------------------------------------------------
    #
    # Three reads the console's own gap register admitted were missing. Pass-throughs: the role,
    # the membership and the store predicate are all decided in the repositories, and the one
    # instant any of them compares against is taken here, once, and handed down.

    def list_store_staff(self, *, store_id: UUID, principal: StaffPrincipal) -> StaffDirectory:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return StaffDirectoryRepository.list_for_store(
                cursor, store_id=store_id, principal=principal, now=datetime.now(UTC)
            )

    def list_order_remedy_credits(
        self, *, store_id: UUID, order_id: UUID, principal: StaffPrincipal
    ) -> OrderRemedyCredits:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return RemedyReadRepository.list_order_credits(
                cursor, store_id=store_id, order_id=order_id, principal=principal
            )

    def list_incident_remedy_proposals(
        self, *, store_id: UUID, incident_id: UUID, principal: StaffPrincipal
    ) -> IncidentRemedyProposals:
        with (
            self._connection_factory(self._database_url) as connection,
            connection.cursor() as cursor,
        ):
            return RemedyReadRepository.list_incident_proposals(
                cursor,
                store_id=store_id,
                incident_id=incident_id,
                principal=principal,
                now=datetime.now(UTC),
            )

    def read_remedy_approval_binding(
        self, *, store_id: UUID, proposal_id: UUID, principal: StaffPrincipal
    ) -> RemedyApprovalBinding:
        """One remedy proposal's owner envelope, for the owner deciding it.

        `REMEDY-OWNER-DECIDE-001`. A pure read, on one transaction so the proposal, its envelope
        and the binding resolved from them are read together. Role, MFA and membership are checked
        in the repository on the cursor that then reads.
        """

        with (
            self._connection_factory(self._database_url) as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            return RemedyReadRepository.read_approval_binding(
                cursor,
                store_id=store_id,
                proposal_id=proposal_id,
                principal=principal,
                now=datetime.now(UTC),
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
        existence, because this path invents no customer reference of its own. A counter intake has
        no channel conversation, so the conversation binding is a freshly minted opaque id — no
        table joins on it anywhere, and the agent bound-read path, which demands the full four-id
        tuple, can never match a request it did not create.

        **A walk-in reference is a counter ticket, and until 2026-08-29 this route refused one.**
        The check read `contact_channel_bindings` alone, so the console's own walk-in flow broke at
        its second step: `orderRequests.js` calls `POST /counter-tickets`, puts the returned
        `ticket_id` into the contact field exactly as its docstring says, and the submit that
        follows came back `CONTACT_BINDING_UNKNOWN`. `DEC-013` ratified the walk-in path and
        `COUNTER-TICKET-001` built it, but its evidence was measured "through the real service and
        repository path" — and `OrderRepository.create`, which that measurement went through,
        already accepts either source. Only this route, which nothing had driven over HTTP, did not.
        So the stranger at the counter — the shop's most common customer — could not be served by
        the product at all.

        Checked against both sources rather than one foreign key, which is the shape
        `OrderRepository.create` established: `DEC-015` declines to unify a ticket and a channel
        binding behind a party layer, because unifying them is the customer-record layer that
        decision says not to build. The ticket check is store-scoped; the binding check is not,
        because a channel binding is not a store's to own.

        Neither source is ever hard-deleted, so the existence answer cannot change between the
        preflight and the commit.
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
                known = ContactChannelBindingRepository.binding_exists(
                    cursor, contact_binding_id=contact_binding_id
                ) or CounterTicketRepository.ticket_exists(
                    cursor, ticket_id=contact_binding_id, store_id=store_id
                )
                if not known:
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


def _transactional_release_mapping(value: Any) -> dict[str, object]:
    return {
        "release_consent_event_id": str(value.release_consent_event_id),
        "contact_binding_id": str(value.contact_binding_id),
        "channel": value.channel,
        "purpose": value.purpose,
        "previous_state": value.previous_state,
        "state": value.state,
        "evidence_webhook_event_id": str(value.evidence_webhook_event_id),
        "released_at": value.released_at.isoformat(),
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


def _remedy_proposal_mapping(value: Any) -> dict[str, object]:
    """Flatten a stored proposal for the idempotency record, which holds JSON and not objects."""

    return {
        "proposal_id": str(value.proposal_id),
        "incident_id": str(value.incident_id),
        "order_id": str(value.order_id),
        "kind": value.kind.value,
        "status": value.status.value,
        "outcome": value.outcome.value,
        "proposal_hash": value.proposal_hash,
        "policy_version": value.policy_version,
        "amount_vnd": value.amount_vnd,
        "ceiling_vnd": value.ceiling_vnd,
        "window_opened_at": _isoformat(value.window_opened_at),
        "window_closes_at": _isoformat(value.window_closes_at),
        "approval_id": None if value.approval_id is None else str(value.approval_id),
        "reason_code": value.reason_code,
        "owner_reasons": list(value.owner_reasons),
        "garment_index": value.garment_index,
    }


def _stored_remedy_proposal_result(
    value: dict[str, object], *, replayed: bool
) -> StoredRemedyProposalResult:
    return StoredRemedyProposalResult(
        proposal_id=UUID(str(value["proposal_id"])),
        incident_id=UUID(str(value["incident_id"])),
        order_id=UUID(str(value["order_id"])),
        kind=str(value["kind"]),
        status=str(value["status"]),
        outcome=str(value["outcome"]),
        proposal_hash=str(value["proposal_hash"]),
        policy_version=int(str(value["policy_version"])),
        amount_vnd=_optional_vnd(value["amount_vnd"]),
        ceiling_vnd=_optional_vnd(value["ceiling_vnd"]),
        window_opened_at=_optional_text(value["window_opened_at"]),
        window_closes_at=_optional_text(value["window_closes_at"]),
        approval_id=(None if value["approval_id"] is None else UUID(str(value["approval_id"]))),
        reason_code=_optional_text(value["reason_code"]),
        replayed=replayed,
        owner_reasons=_text_tuple(value.get("owner_reasons")),
        garment_index=_optional_position(value.get("garment_index")),
    )


def _optional_position(value: object) -> int | None:
    """A stored garment position, or `None` for a record written before it existed."""

    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


def _text_tuple(value: object) -> tuple[str, ...]:
    """A stored list of codes, or nothing for a record written before the list existed."""

    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _remedy_execution_mapping(value: Any) -> dict[str, object]:
    return {
        "proposal_id": str(value.proposal_id),
        "incident_id": str(value.incident_id),
        "order_id": str(value.order_id),
        "kind": value.kind.value,
        "status": value.status.value,
        "event_type": value.event_type,
        "credit_id": None if value.credit_id is None else str(value.credit_id),
        "amount_vnd": value.amount_vnd,
    }


def _stored_remedy_execution_result(
    value: dict[str, object], *, replayed: bool
) -> StoredRemedyExecutionResult:
    return StoredRemedyExecutionResult(
        proposal_id=UUID(str(value["proposal_id"])),
        incident_id=UUID(str(value["incident_id"])),
        order_id=UUID(str(value["order_id"])),
        kind=str(value["kind"]),
        status=str(value["status"]),
        event_type=str(value["event_type"]),
        credit_id=None if value["credit_id"] is None else UUID(str(value["credit_id"])),
        amount_vnd=_optional_vnd(value["amount_vnd"]),
        replayed=replayed,
    )


def _credit_redemption_mapping(value: Any) -> dict[str, object]:
    return {
        "credit_id": str(value.credit_id),
        "quote_id": str(value.quote_id),
        "revision": value.revision,
        "snapshot_hash": value.snapshot_hash,
        "credit_vnd": value.credit_vnd,
        "net_service_subtotal_vnd": value.net_service_subtotal_vnd,
        "display_total_vnd": value.display_total_vnd,
    }


def _stored_credit_redemption_result(
    value: dict[str, object], *, replayed: bool
) -> StoredCreditRedemptionResult:
    return StoredCreditRedemptionResult(
        credit_id=UUID(str(value["credit_id"])),
        quote_id=UUID(str(value["quote_id"])),
        revision=int(str(value["revision"])),
        snapshot_hash=str(value["snapshot_hash"]),
        credit_vnd=int(str(value["credit_vnd"])),
        net_service_subtotal_vnd=int(str(value["net_service_subtotal_vnd"])),
        display_total_vnd=_optional_vnd(value["display_total_vnd"]),
        replayed=replayed,
    )


def _isoformat(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


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


def _collection_mapping(value: StoredCollection) -> dict[str, object]:
    return {
        "collection_id": str(value.collection_id),
        "order_id": str(value.order_id),
        "settlement_id": str(value.settlement_id),
        "collected_by_staff_id": str(value.collected_by_staff_id),
        "collected_at": value.collected_at.isoformat(),
        "self_collection_recorded": value.self_collection_recorded,
        "row_version": value.row_version,
    }


def _stored_collection_result(
    value: dict[str, object], *, replayed: bool
) -> StoredCollectionResult:
    return StoredCollectionResult(
        collection_id=UUID(str(value["collection_id"])),
        order_id=UUID(str(value["order_id"])),
        settlement_id=UUID(str(value["settlement_id"])),
        collected_by_staff_id=UUID(str(value["collected_by_staff_id"])),
        collected_at=datetime.fromisoformat(str(value["collected_at"])),
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
        "promotion": _promotion_mapping(snapshot),
    }


def _promotion_mapping(snapshot: ImmutableQuoteSnapshot) -> dict[str, object] | None:
    """Read the frozen promotion off the revision. Nothing here is recomputed.

    `frozen_promotion` parses the revision's own `PROMOTION` calculation trace, which is canonical
    bytes inside an immutable snapshot. Re-deriving any of it at read time would mean the console
    could show a number the customer was never told -- the same failure the freeze exists to
    prevent, arriving through the back door.
    """

    frozen = frozen_promotion(snapshot)
    if frozen is None:
        return None
    return {
        "policy_code": frozen.policy_code,
        "configuration_version": frozen.configuration_version,
        "status": frozen.status,
        "discount_amount_vnd": frozen.discount_amount_vnd,
        "rate_bps": list(frozen.rate_bps),
        "interval_start_at": frozen.interval_start_at,
        "interval_end_at_exclusive": frozen.interval_end_at_exclusive,
        "inside_interval": frozen.candidate_inside_interval,
        "eligibility_resolved": frozen.eligibility_resolved,
        "reason_codes": list(frozen.reason_codes),
    }


def _range_price_attestation(
    priced: ImmutableQuoteSnapshot,
    choices: tuple[RangePriceChoice, ...],
    *,
    approval_id: UUID | None = None,
) -> RangePriceAttestation | None:
    """Assemble the attestation from the stored revision plus the amounts a person chose.

    Every field except the amounts is server-derived: the quote, the revision, and the pricebook
    version the revision was priced against. A caller supplies numbers and nothing else, so it
    cannot state which band its numbers should be checked against -- which is the difference between
    a bound and a suggestion.

    `None` when the stored revision names no pricebook, which `build_quote_snapshot` makes
    impossible and this refuses anyway rather than reading `[0]` off an empty sequence.
    """

    pricebook = next(
        (item for item in priced.data.configuration_snapshots if item.config_type == "PRICEBOOK"),
        None,
    )
    if pricebook is None:
        return None
    return RangePriceAttestation(
        quote_id=priced.data.quote_id,
        revision=priced.data.revision,
        pricebook_version_id=pricebook.version_id,
        pricebook_version=pricebook.version,
        choices=choices,
        approval_id=approval_id,
    )


def _band_refusal(priced: ImmutableQuoteSnapshot, attestation: RangePriceAttestation) -> str | None:
    """The domain's verdict on a set of amounts, before any envelope is raised.

    `apply_range_prices` checks the same thing again through `close_range_prices`, and that is not
    duplication worth removing: this call is what keeps an out-of-band amount from ever producing an
    approval row, and that one is what keeps a stale or tampered one from producing a price.
    """

    bands = stored_price_bands(priced)
    if bands is None:
        return ErrorCode.VALIDATION_ERROR.value
    outcome = resolve_range_prices(
        bands=bands,
        pricebook_version_id=attestation.pricebook_version_id,
        pricebook_version=attestation.pricebook_version,
        attestation=attestation,
    )
    if isinstance(outcome, RangePriceRefused):
        return outcome.reason_code
    if set(outcome.amounts) != set(bands):
        return ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value
    return None


def _require_range_price_approval(
    binding: ApprovalBinding | None,
    *,
    store_id: UUID,
    quote_id: UUID,
    priced: ImmutableQuoteSnapshot,
    rendered_hash: str,
    at: datetime,
) -> None:
    """Prove the envelope is an approved `SET_RANGE_PRICE` for exactly this content. Invariant 8.

    One message for every failure, deliberately, and for the same reason
    `_require_resolvable_resource` gives: separate strings would tell a member of any store whether
    a UUID is a real approval in somebody else's shop.

    Expiry is enforced here as well as at the decision, because a money envelope that outlives its
    own TTL is exactly what a TTL is for. Under `_OWNER_FINANCIAL` that meant the owner's approval
    and the staff member's application had to land inside one ten-minute window, which is the cost
    `docs/DECISION_REQUEST_RANGE_PRICE_AUTHORITY_2026-09.md` put to the owner. `DEC-029` answered
    it: the envelope is the counter attestation's thirty minutes, attested and applied by the same
    person seconds apart. The check itself is unchanged and still refuses a late application.
    """

    if (
        binding is None
        or binding.store_id != store_id
        or binding.action is not ApprovalAction.SET_RANGE_PRICE
        or binding.resource_type != APPROVAL_RESOURCE_TYPES[ApprovalAction.SET_RANGE_PRICE]
        or binding.resource_id != quote_id
        or binding.resource_version != priced.data.revision
        or binding.status != ApprovalDecision.APPROVED.value
        or at >= binding.expires_at
        or not hmac.compare_digest(binding.snapshot_hash, priced.document.snapshot_hash)
        or not hmac.compare_digest(binding.rendered_hash, rendered_hash)
    ):
        raise QuoteStateError(
            "no approved price is bound to this exact revision; propose the amounts again"
        )


def _quote_revision_view(
    priced: ImmutableQuoteSnapshot,
    *,
    snapshot_hash: str,
    row_version: int,
    customer_accepted_at: datetime | None = None,
) -> QuoteRevisionView:
    """Project a stored revision for reading. Nothing is computed; every field is read off it."""

    data = priced.data
    return QuoteRevisionView(
        quote_id=data.quote_id,
        revision=data.revision,
        row_version=row_version,
        finality=data.finality.value,
        status=data.status.value,
        snapshot_hash=snapshot_hash,
        display_total_min_vnd=data.totals.display_total_min_vnd,
        display_total_max_vnd=data.totals.display_total_max_vnd,
        valid_until=data.valid_until,
        reason_codes=data.reason_codes,
        lines=tuple(
            QuoteLineView(
                line_id=line.line_id,
                service_code=line.service_code,
                quantity=line.quantity,
                unit=line.unit.value,
                price_kind=line.amounts.kind,
                net_amount_vnd=(
                    line.amounts.net_amount_vnd
                    if isinstance(line.amounts, ExactLineAmounts)
                    else None
                ),
                band_minimum_vnd=(
                    None
                    if isinstance(line.amounts, ExactLineAmounts)
                    else line.amounts.net_amount_min_vnd
                ),
                band_maximum_vnd=(
                    None
                    if isinstance(line.amounts, ExactLineAmounts)
                    else line.amounts.net_amount_max_vnd
                ),
            )
            for line in data.lines
        ),
        customer_accepted_at=customer_accepted_at,
    )


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
        # `.get`, not `[...]`: a response stored in the idempotency ledger before
        # `PROMO-WIRING-001` has no promotion key, and a replay of it must still return rather than
        # raising. Absent reads as "no promotion was frozen on that revision", which is what was
        # true when it was written.
        promotion=_promotion_view(value.get("promotion")),
    )


def _promotion_view(value: object) -> QuotePromotionView | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise OperationsUnavailable("stored quote response is malformed")
    return QuotePromotionView(
        policy_code=str(value["policy_code"]),
        configuration_version=int(str(value["configuration_version"])),
        status=str(value["status"]),
        discount_amount_vnd=int(str(value["discount_amount_vnd"])),
        rate_bps=tuple(int(str(rate)) for rate in _string_list(value["rate_bps"])),
        interval_start_at=str(value["interval_start_at"]),
        interval_end_at_exclusive=str(value["interval_end_at_exclusive"]),
        inside_interval=bool(value["inside_interval"]),
        eligibility_resolved=bool(value["eligibility_resolved"]),
        reason_codes=tuple(str(code) for code in _string_list(value["reason_codes"])),
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
