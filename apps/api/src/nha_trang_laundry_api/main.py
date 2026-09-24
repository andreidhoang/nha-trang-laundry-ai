"""Staff-only API entry point. Public customer endpoints are intentionally absent."""

from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from time import perf_counter
from typing import Annotated, Literal, NoReturn
from urllib.parse import urlencode
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.staticfiles import StaticFiles
from nha_trang_laundry_contracts.channel_envelope import ReconciliationState
from nha_trang_laundry_db.approvals import (
    ApprovalAuthorizationError,
    ApprovalDecision,
    ApprovalStateError,
    StoredApproval,
)
from nha_trang_laundry_db.assistant import AssistantAuthorizationError
from nha_trang_laundry_db.channel import ChannelBindingError
from nha_trang_laundry_db.counter_tickets import CounterTicketError
from nha_trang_laundry_db.delivery_legs import (
    DeliveryLegError,
    DeliveryLegKind,
    DeliveryLegOutcome,
)
from nha_trang_laundry_db.exports import ExportAuthorizationError, ExportStateError
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import (
    IdentityStateError,
    StaffPrincipal,
    StaffRole,
    StaffSubjectTakenError,
)
from nha_trang_laundry_db.intake import OrderRequestSummary
from nha_trang_laundry_db.manual_sends import (
    ManualSendAuthorizationError,
    ManualSendStateError,
)
from nha_trang_laundry_db.orders import (
    OrderAuthorizationError,
    OrderNotVisibleError,
    OrderStateError,
    OrderView,
    StoredOrder,
)
from nha_trang_laundry_db.quotes import QuoteIntegrityError, QuoteStateError
from nha_trang_laundry_db.range_prices import RangePriceProposalIntegrityError
from nha_trang_laundry_db.remedies import RemedyAuthorizationError, RemedyStateError
from nha_trang_laundry_db.settlement import (
    BUSINESS_TIMEZONE,
    COLLECTED_TODAY_QUERY,
    SettlementAuthorizationError,
    SettlementStateError,
)
from nha_trang_laundry_db.shadow_console import (
    SLA_BOARD_DEFAULT_LIMIT,
    ShadowAuthorizationError,
    ShadowStateError,
)
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_domain.approvals import ApprovalEnvelopeError
from nha_trang_laundry_domain.canonical import MAX_CANONICAL_INT
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    ApprovalAction,
    CommercialOrderStatus,
    CustodyResolution,
    FulfillmentMode,
    IntakeStatus,
    ProductionStatus,
    QuantityBasis,
    Unit,
)
from nha_trang_laundry_domain.quote_composition import RequestedLine
from nha_trang_laundry_domain.range_prices import RangePriceChoice
from nha_trang_laundry_domain.remedies import RemedyKind
from nha_trang_laundry_observability import (
    CORRELATION_HEADER,
    CorrelationContext,
    SafeStructuredLogger,
    Telemetry,
    configure_structured_logging,
    correlation_scope,
    current_correlation,
)
from opentelemetry import metrics, trace
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, StreamingResponse

from nha_trang_laundry_api.assistant import (
    SLA_POLICY,
    AssistantService,
    AssistantUnavailable,
    answer_sse_frames,
    sla_policy_notice_vi,
)
from nha_trang_laundry_api.auth import (
    AuthenticationAttemptLimiter,
    AuthenticationError,
    AuthenticationUnavailable,
    AuthSettings,
    StaffIdentityService,
)
from nha_trang_laundry_api.operations import (
    OperationsService,
    OperationsUnavailable,
    QueueRecoverySummary,
    QuotePricingUnavailable,
    QuotePromotionView,
    StoredIncidentResult,
    StoredManualSendResult,
    UnresolvedQuoteResult,
)
from nha_trang_laundry_api.ops_board import OpsBoardService, OpsBoardUnavailable
from nha_trang_laundry_api.security import BrowserSecurityMiddleware, RequestSizeLimitMiddleware

# SHOP-OBSERVABILITY-001. Before this call, every `_LOGGER.record(...)` below was a no-op in the
# container: uvicorn's default LOGGING_CONFIG leaves this logger at WARNING with no handler
# anywhere, and a logger below its level does not raise -- so `emit` returned True and the line went
# nowhere. It is configured at import, before the first request can be served, because the browser
# security boundary logs rejections during startup traffic too.
configure_structured_logging()

_AUTH_SETTINGS = AuthSettings()
app = FastAPI(
    title="Nha Trang Laundry AI Control Plane",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    RequestSizeLimitMiddleware,
    max_bytes=_AUTH_SETTINGS.api_max_request_bytes,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(_AUTH_SETTINGS.trusted_hosts()))
app.add_middleware(BrowserSecurityMiddleware, settings=_AUTH_SETTINGS)
_LOGGER = SafeStructuredLogger()
_TELEMETRY = Telemetry(
    meter_provider=metrics.get_meter_provider(),
    tracer_provider=trace.get_tracer_provider(),
    instrumentation_name="nha_trang_laundry.api",
)
_AUTH_LIMITER = AuthenticationAttemptLimiter(
    limit=_AUTH_SETTINGS.auth_attempt_limit,
    window_seconds=_AUTH_SETTINGS.auth_attempt_window_seconds,
)
WEB_DIRECTORY = Path(__file__).resolve().parents[3] / "web"


@app.middleware("http")
async def correlation_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    context = CorrelationContext.from_http_header(request.headers.get(CORRELATION_HEADER))
    operation = _http_operation(request.url.path)
    started_at = perf_counter()
    with (
        correlation_scope(context),
        _TELEMETRY.start_span(
            "http_request",
            carrier=dict(request.headers),
            attributes={"component": "api", "operation": operation},
        ),
    ):
        try:
            response = await call_next(request)
        except Exception:
            _record_http_metrics(operation, 500, started_at)
            raise
        trace_headers: dict[str, str] = {}
        _TELEMETRY.inject_current(trace_headers)
        for name, value in trace_headers.items():
            response.headers[name] = value
    response.headers[CORRELATION_HEADER] = context.header_value
    _record_http_metrics(operation, response.status_code, started_at)
    _LOGGER.record(
        component="api",
        name="http.request.completed",
        outcome="completed",
        correlation=context,
        fields={
            "method": request.method,
            "route": request.url.path,
            "status_code": response.status_code,
        },
    )
    return response


def _http_operation(path: str) -> str:
    if path == "/healthz":
        return "healthz"
    if path.startswith("/internal/v1/stores/") and "/quotes" in path:
        return "quote_compute"
    if path.startswith("/staff"):
        return "staff_shell"
    if path.startswith("/internal/v1/auth"):
        return "staff_auth"
    if path.startswith("/internal/"):
        return "internal_api"
    return "not_found"


def _record_http_metrics(operation: str, status_code: int, started_at: float) -> None:
    status_class = f"{status_code // 100}xx"
    outcome = "completed" if status_code < 400 else "denied" if status_code < 500 else "failed"
    attributes = {
        "component": "api",
        "operation": operation,
        "outcome": outcome,
        "status_class": status_class,
    }
    _TELEMETRY.record("api_requests_total", 1, attributes)
    _TELEMETRY.record("api_latency_ms", (perf_counter() - started_at) * 1000, attributes)


class SessionResponse(BaseModel):
    staff_user_id: str
    roles: list[str]
    mfa_verified: bool


class RoleAssignmentRequest(BaseModel):
    role: StaffRole


class StaffCreateRequest(BaseModel):
    oidc_subject: str
    display_name: str
    email: str | None = None


class StaffCreateResponse(BaseModel):
    staff_user_id: str


class StrictRequest(BaseModel):
    """Every request body: no unknown keys, and no coercion into an integer or a boolean.

    Integer and boolean fields on subclasses are `StrictInt` / `StrictBool`. Pydantic's lax mode
    turns `true` into 1 and "120000" into 120000 before the domain sees the value, which bypasses
    the domain's own `not isinstance(x, bool)` guards -- `paid_amount_vnd: true` was 1 ₫ and
    `verified_distance_m: true` was 1 m, inside the free-delivery zone. Not `strict=True` on the
    whole model: that would also refuse the ISO strings JSON must carry UUIDs and datetimes as.
    `test_request_strictness.py` enumerates every request model, so a new `int` field fails there.
    """

    model_config = ConfigDict(extra="forbid")


class OrderCreateRequest(StrictRequest):
    bound_contact_id: UUID
    quote_id: UUID
    quote_revision: StrictInt = Field(ge=1)
    quote_snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    fulfillment_mode: FulfillmentMode
    customer_final_quote_accepted_at: datetime
    #: `ACQUISITION-ATTRIBUTION-001`. Required, with no default. `UNKNOWN` is a value a caller
    #: sends deliberately, never one the server supplies on their behalf -- an omitted field means
    #: the client is out of date, and that is a different thing from a customer who was not asked.
    acquisition_source: AcquisitionSource


class CommercialTransitionRequest(StrictRequest):
    target: CommercialOrderStatus
    #: `DEC-024`. Required only to cancel an order whose work has begun, and the refusal says so.
    #: Supplying it is the approval: a named staff member states what happened to the laundry and
    #: the money, and the order records it. There is deliberately no separate "approved" boolean --
    #: a second field nobody fills is how the original defect started.
    custody_resolution: CustodyResolution | None = None


class IntakeTransitionRequest(StrictRequest):
    """Intake carries one attested fact; the rest the server reads for itself."""

    target: IntakeStatus
    #: Whether a human has confirmed the shop has capacity for this order. The system never decides
    #: this: `evaluate_delivery` returns REQUIRE_HUMAN for every slot because Shadow stage has no
    #: auto-confirmable capacity, so it is the operator's word or nothing.
    slot_approved: StrictBool = False


class ProductionTransitionRequest(StrictRequest):
    target: ProductionStatus


class ApprovalRequest(StrictRequest):
    #: The shop this approval belongs to. Required since migration `0034`: an approval used to
    #: carry no store at all, so `decide` could not check membership and a member of any store
    #: could approve any other store's action.
    store_id: UUID
    action: ApprovalAction
    resource_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,127}$")
    resource_id: UUID
    resource_version: StrictInt = Field(ge=1, le=MAX_CANONICAL_INT)
    snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    rendered_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1, max_length=200)


class ApprovalDecisionRequest(StrictRequest):
    decision: ApprovalDecision
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,99}$")
    note: str | None = Field(default=None, min_length=1, max_length=500)
    resource_version: StrictInt = Field(ge=1, le=MAX_CANONICAL_INT)
    snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    rendered_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")


class ManualSendPrepareRequest(StrictRequest):
    observed_resource_version: StrictInt = Field(ge=1, le=MAX_CANONICAL_INT)
    observed_snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    observed_rendered_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    recipient_binding_id: UUID
    channel: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,49}$")


class ManualSendAttestationRequest(StrictRequest):
    observed_resource_version: StrictInt = Field(ge=1, le=MAX_CANONICAL_INT)
    exact_rendered_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    sent_at: datetime


class IncidentOpenRequest(StrictRequest):
    """`DEC-028`. What the customer said, and which order it is about. Nothing else.

    The two `sha256:` fields this model used to require were agent-pipeline concepts the counter
    inherited, and nothing in the system produced either, so the form could not be completed by
    anybody. They are now derived by the server. `StrictRequest` forbids unknown fields, so a client
    that tries to supply one is refused rather than quietly ignored.
    """

    order_id: UUID
    evidence_summary: str = Field(min_length=1, max_length=2000)


class RemedyProposalRequest(StrictRequest):
    """What a staff member proposes. `REMEDY-001`, `DEC-004`.

    **There is no ceiling field and there must never be.** The 5x damage cap comes from the order's
    own priced line and the 10% late-delivery credit from its settled total; a client that could
    state either would be authorising its own bound. The same applies to the window: it is measured
    from a recorded handover, not from a date a form supplies.

    `amount_vnd` is an integer of dong and is legal for exactly one kind. `DAMAGE_COMPENSATION` is
    the only remedy where a person chooses the figure -- a rewash moves no money, a late-delivery
    credit is computed, and loss has no figure at all -- so supplying it anywhere else is refused
    with `REMEDY_AMOUNT_NOT_APPLICABLE` rather than ignored.
    """

    kind: RemedyKind
    #: The staff finding `DEC-004` rests every remedy on. Required, with no default: a remedy is
    #: authorised by somebody deciding the store was at fault, and a default would decide it for
    #: them in whichever direction the default pointed.
    store_fault_attested: StrictBool
    order_line_id: str | None = Field(default=None, min_length=1, max_length=64)
    amount_vnd: StrictInt | None = Field(default=None, ge=0, le=MAX_CANONICAL_INT)
    #: How late the delivery was, attested by the staff member who handled it. The shop records no
    #: promised arrival time, so this cannot be derived; that a return leg happened at all is
    #: checked against the record, and this is refused unless it clears the published threshold.
    attested_late_by_minutes: StrictInt | None = Field(default=None, ge=0, le=MAX_CANONICAL_INT)


class RemedyCreditRedemptionRequest(StrictRequest):
    """Which credit to spend, and the caller's evidence that it read the quote it is spending on."""

    credit_id: UUID
    expected_current_revision: StrictInt = Field(ge=1)
    expected_snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")


class RemedyProposalResponse(BaseModel):
    """The recorded proposal, with the figures the server computed for it.

    `outcome` is `REQUIRE_HUMAN` and `reason_code` is `LOSS_POLICY_UNRESOLVED` for a loss, on a 201
    rather than an error: the complaint was recorded, and for a loss the record *is* the outcome.
    Every other field is then null, because `DEC-004` gives loss no figure of any kind.
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
    #: What the server computed the bound to be. Null where the kind has none.
    ceiling_vnd: int | None
    window_opened_at: str | None
    window_closes_at: str | None
    #: The `APPROVE_REMEDY` envelope, present exactly when the amount is above the staff ceiling.
    approval_id: UUID | None
    reason_code: str | None
    replayed: bool


class RemedyExecutionResponse(BaseModel):
    proposal_id: UUID
    incident_id: UUID
    order_id: UUID
    kind: str
    status: str
    event_type: str
    credit_id: UUID | None
    amount_vnd: int | None
    replayed: bool


class RemedyOptionsResponse(BaseModel):
    """What the form must show before a staff member types anything.

    `policy_published` false means every remedy fails closed (invariant 11) and the console must say
    that the owner has not published the figures -- not render an empty form. A null
    `late_delivery_credit_vnd` means no delivery this system recorded could have been late, and is
    rendered as unavailable rather than as 0.
    """

    incident_id: UUID
    order_id: UUID
    policy_published: bool
    staff_approval_ceiling_vnd: int | None
    goods_returned_at: str | None
    rewash_window_closes_at: str | None
    rewash_window_open: bool
    defect_window_closes_at: str | None
    defect_window_open: bool
    damage_line_ceilings_vnd: dict[str, int] | None
    late_delivery_credit_vnd: int | None
    late_delivery_threshold_minutes: int | None
    loss_reason_code: str


class RemedyCreditRedemptionResponse(BaseModel):
    credit_id: UUID
    quote_id: UUID
    revision: int
    snapshot_hash: str
    credit_vnd: int
    net_service_subtotal_vnd: int
    display_total_vnd: int | None
    replayed: bool


class MemberStore(BaseModel):
    """One store the caller belongs to. `name` is null for a store minted before the registry."""

    store_id: UUID
    name: str | None


class MemberStoresResponse(BaseModel):
    #: Kept as the primitive every existing caller reads; `stores` carries the same identifiers in
    #: the same order, with the name beside each so a person can tell two shops apart.
    store_ids: list[UUID]
    stores: list[MemberStore]


class OrderResponse(BaseModel):
    order_id: UUID
    store_id: UUID
    commercial: CommercialOrderStatus
    intake: str
    production: str
    balance: str
    row_version: int
    replayed: bool


class OrderViewResponse(OrderResponse):
    """An order as the counter reads it: the command result's fields, plus what pickup needs.

    Returned by the board and by the read by id -- never by a command, whose reply is the stored
    idempotent result and carries the eight fields above only. Every addition is a stored fact:

    * `payable_total_vnd` -- the bound quote revision's total, from the same columns the settlement
      checks a payment against, so the number shown is the number the counter can take. Null when
      that revision presents no single total, never zero. It does not change once paid; `balance`
      says whether it has been.
    * `ticket_number` / `ticket_issued_on` -- the walk-in ticket the order is tracked by
      (`DEC-013`), null when the customer reference is a channel binding instead.
    * `quote_id` / `quote_revision` -- the accepted revision the order is bound to.
    """

    fulfillment_mode: str
    created_at: datetime
    quote_id: UUID
    quote_revision: int
    payable_total_vnd: int | None
    ticket_number: int | None
    ticket_issued_on: date | None


class ApprovalResponse(BaseModel):
    approval_request_id: UUID
    status: str
    envelope_hash: str
    required_role: str
    expires_at: str
    replayed: bool
    # The binding an approver has to hand back. `ApprovalDecisionRequest` requires
    # `resource_version`, `snapshot_hash` and `rendered_hash`, and `ManualSendPrepareRequest`
    # requires the same three as `observed_*`. Until these were projected neither could be built
    # from anything a client could read, so the queue rendered decision controls that could never
    # be pressed and the manual-send envelope could never be opened.
    #
    # Null on the request and decision paths, which answer about a state change. The queue read
    # populates them, and the queue read is what the console acts from.
    resource_type: str | None = None
    resource_id: UUID | None = None
    resource_version: int | None = None
    snapshot_hash: str | None = None
    rendered_hash: str | None = None
    # Which action this envelope authorises. Null on the same two paths the binding is null on.
    # Returned because the resource type is not specific enough to decide whether the console can
    # show an approver what they are approving: `SET_RANGE_PRICE` and `PRESENT_QUOTE` are both
    # `QUOTE_REVISION`, and only one of them is about a number the quote screen does not render.
    action: str | None = None


class ManualSendResponse(BaseModel):
    manual_send_envelope_id: UUID
    approval_request_id: UUID
    status: str
    recipient_binding_id: UUID
    rendered_hash: str
    row_version: int
    replayed: bool


class SettlementRequest(StrictRequest):
    # An integer of đồng. VND has no minor unit, and a float would introduce a representation the
    # currency does not have on the one field that decides whether a customer paid.
    paid_amount_vnd: StrictInt = Field(ge=0, le=MAX_CANONICAL_INT)
    # Explicit rather than defaulted. "The customer took their goods" is the fact being attested,
    # and a default true would let a staff member attest to it by not mentioning it.
    collected_by_customer: StrictBool


class SettlementResponse(BaseModel):
    settlement_id: UUID
    order_id: UUID
    expected_total_vnd: int
    paid_amount_vnd: int
    settlement_shape: str
    balance_status: str
    self_collection_recorded: bool
    row_version: int
    replayed: bool


class QuoteLineRequest(StrictRequest):
    service_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,62}$")
    # A string, and deliberately not a float or a Decimal. Pydantic coercion would rewrite what the
    # customer or the scale actually said — "4" becomes 4.0 becomes "4.0" — and DEC-001 (weight
    # precision and rounding) is open, so the quantity travels verbatim and the domain engine is the
    # only thing allowed to have an opinion about its form.
    quantity: str = Field(min_length=1, max_length=16)
    unit: Unit
    quantity_basis: QuantityBasis


class QuoteCreateRequest(StrictRequest):
    bound_order_request_id: UUID
    lines: list[QuoteLineRequest] = Field(min_length=1, max_length=20)
    # Required, with no default. Whether the shop carries this laundry decides whether a delivery
    # fee exists, and a default would be the server deciding a fact about the customer's order.
    # `evaluate_delivery` answers the rest: 0 for self-collect, the owner-confirmed zone table
    # under 6km, and a staff-negotiated fee above it (DEC-003).
    fulfillment_mode: FulfillmentMode
    # Delivery facts. Absent is not zero -- an absent distance makes the fee REQUIRE_HUMAN, which
    # is why the quote then carries no total rather than a total that understates the price.
    verified_distance_m: StrictInt | None = Field(default=None, ge=0, le=100_000)
    planned_transport_weight_kg: str | None = Field(default=None, max_length=32)
    approved_manual_fee_vnd: StrictInt | None = Field(default=None, ge=0)
    customer_acknowledged_manual_fee: StrictBool = False
    # Absent means "open a new quote". Present means "add a revision to this one", and then
    # expected_current_revision plus If-Match carry the compare-and-swap: a correction is always a
    # new revision, never an update to an existing one.
    quote_id: UUID | None = None
    expected_current_revision: StrictInt = Field(default=0, ge=0)
    # Off unless asked for. A range-priced service is refused by default -- which is what every
    # caller before `RANGE-PRICE-001` relied on -- and storing the band instead is a deliberate
    # act: it produces a revision with a minimum and a maximum and no single total, which a caller
    # expecting a price must not receive by accident.
    present_range_as_band: StrictBool = False


class QuotePromotionResponse(BaseModel):
    """What the shop's promotion programme did to this revision, frozen at pricing time.

    Null in place of the whole object means no programme was evaluated, and `reason_codes` on the
    revision says which case: `PROMOTION_NOT_PUBLISHED` (nobody has published one) or
    `PROMOTION_PENDING_BAND_CLOSE` (this is a band, and the discount waits for the amount).

    `interval_end_at_exclusive` is the exclusive end of the programme window, so the last day it
    covered is the day before. It is on the response so a console can say *when* an expired
    programme ended instead of rendering an unexplained zero -- the same rule that forbids drawing a
    null total as `0`.
    """

    policy_code: str
    configuration_version: int
    status: str
    discount_amount_vnd: int
    rate_bps: list[int]
    interval_start_at: str
    interval_end_at_exclusive: str
    inside_interval: bool
    eligibility_resolved: bool
    reason_codes: list[str]


class QuoteRevisionResponse(BaseModel):
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
    reason_codes: list[str]
    required_approvals: list[str]
    replayed: bool
    promotion: QuotePromotionResponse | None = None


def _promotion_response(view: QuotePromotionView | None) -> QuotePromotionResponse | None:
    """Carry the service's frozen promotion through unchanged. No arithmetic in the route layer."""

    if view is None:
        return None
    return QuotePromotionResponse(
        policy_code=view.policy_code,
        configuration_version=view.configuration_version,
        status=view.status,
        discount_amount_vnd=view.discount_amount_vnd,
        rate_bps=list(view.rate_bps),
        interval_start_at=view.interval_start_at,
        interval_end_at_exclusive=view.interval_end_at_exclusive,
        inside_interval=view.inside_interval,
        eligibility_resolved=view.eligibility_resolved,
        reason_codes=list(view.reason_codes),
    )


class RangePriceChoiceRequest(StrictRequest):
    """One amount a staff member chose for one range-priced line.

    An integer of dong, like every other money field on this surface. A float would introduce a
    representation the currency does not have, on a field that decides what a customer pays.

    There is no band here, and there must never be: the interval comes from the stored revision the
    server itself read. A client that could state its own bounds would be authorising its own price.
    """

    service_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,62}$")
    amount_vnd: StrictInt = Field(ge=0, le=MAX_CANONICAL_INT)


class RangePriceRequest(StrictRequest):
    """The amounts, and the caller's evidence that it is pricing the revision it was shown."""

    expected_current_revision: StrictInt = Field(ge=1)
    expected_snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    choices: list[RangePriceChoiceRequest] = Field(min_length=1, max_length=20)


class RangePriceProposalResponse(BaseModel):
    """The raised envelope plus the exact binding its approver must hand back."""

    approval_request_id: UUID
    status: str
    envelope_hash: str
    required_role: str
    expires_at: str
    resource_version: int
    snapshot_hash: str
    rendered_hash: str
    replayed: bool


class ProposedRangePriceLineResponse(BaseModel):
    """One line of a proposal: the bound the owner published, and the number inside it.

    All three are integers of dong and all three are server-held. The band is returned beside the
    amount rather than left to the client to fetch, because the pair is the disclosure: an amount
    without the interval it was checked against is as unreadable as the interval alone was.
    """

    service_code: str
    band_minimum_vnd: int
    band_maximum_vnd: int
    proposed_amount_vnd: int


class RangePriceProposalContentResponse(BaseModel):
    """What a `SET_RANGE_PRICE` envelope is actually asking its approver to authorise.

    `RANGE-APPROVAL-VISIBILITY-001`. The approvals queue returns a digest, and until this route
    existed the digest was all an approver could see: the console linked through to the quote,
    which renders the published band, so the owner read *80.000 ₫ - 240.000 ₫* and approved a
    number they had never been shown.

    This is a display read and nothing else. Application still re-derives the digest from the
    amounts the caller holds and refuses unless it equals the approved one; that check does not
    consult these rows. What the read does do before answering is re-derive the digest from the
    stored rows and require the envelope's own copy, so a stored amount that does not belong to
    this envelope is withheld rather than shown.
    """

    approval_request_id: UUID
    quote_id: UUID
    revision: int
    pricebook_version: int
    #: The envelope's `rendered_hash`, returned so a client can show that the amounts below and the
    #: digest it is about to hand back are the same content. It is not a substitute for the
    #: server's own checks, and no client-side comparison of it authorises anything.
    rendered_hash: str
    proposed_at: datetime
    lines: list[ProposedRangePriceLineResponse]


class QuoteLineResponse(BaseModel):
    line_id: str
    service_code: str
    quantity: str
    unit: str
    #: "EXACT" or "RANGE". A RANGE line has the two bounds and no amount, because there is no
    #: amount until a person chooses one.
    price_kind: str
    net_amount_vnd: int | None
    band_minimum_vnd: int | None
    band_maximum_vnd: int | None


class QuoteRevisionDetailResponse(BaseModel):
    quote_id: UUID
    revision: int
    row_version: int
    finality: str
    status: str
    snapshot_hash: str
    display_total_min_vnd: int | None
    display_total_max_vnd: int | None
    valid_until: datetime | None
    reason_codes: list[str]
    lines: list[QuoteLineResponse]
    #: When the customer agreed the price that produced this revision, from the `DEC-021`
    #: attestation. Null for a revision no acceptance produced.
    customer_accepted_at: datetime | None = None


class QuoteSummaryResponse(BaseModel):
    quote_id: UUID
    revision: int
    row_version: int
    finality: str
    status: str
    snapshot_hash: str
    # Null when the delivery fee is unresolved, which the schema requires and the console shows as
    # "chưa có tổng" rather than as a number. See QuoteSummary for how this was found.
    display_total_min_vnd: int | None
    display_total_max_vnd: int | None
    valid_until: datetime | None


class IncidentResponse(BaseModel):
    incident_id: UUID
    status: str
    fault_decided: bool
    remedy_decided: bool
    replayed: bool = False


class IncidentSummaryResponse(BaseModel):
    incident_id: UUID
    store_id: UUID
    order_id: UUID | None
    category: str
    status: str
    fault_decided: bool
    remedy_decided: bool
    opened_at: datetime
    #: Null for an incident the agent path opened, which stores no summary, and for one whose
    #: evidence has been disposed of under `INCIDENT_EVIDENCE`. The incident itself never
    #: disappears; only its description does, and only on the published schedule.
    evidence_summary: str | None = None


class QueueRecoveryResponse(BaseModel):
    pending_internal: int
    processing_internal: int
    expired_internal: int
    dead_internal: int
    pending_agent: int
    processing_agent: int
    expired_agent: int
    failed_agent: int
    replay_available: bool = False


def get_identity_service() -> StaffIdentityService:
    """Build the identity service per request, which is also the JWKS-outage policy.

    A new `StaffIdentityService` means a new `IdentityPlatformVerifier` means a new `PyJWKClient`,
    whose key cache is per instance and is therefore discarded before it can ever be reused. Every
    `POST /internal/v1/auth/session` fetches the signing keys live, and if the issuer is down the
    exchange is a 401.

    `SHOP-IDENTITY-001` recorded that as the decision rather than leaving it as an accident, because
    with Keycloak self-hosted on the same host (`DEC-011`) staff sign-in now has a hard local
    dependency and somebody will ask what happens when it stops.

    **Fail closed, no key cache.** A cache window is a window in which a rotated or revoked signing
    key still mints sessions, and the cost avoided is one HTTP GET over a loopback bridge per
    sign-in, already bounded to five attempts per 300s per source by `AuthenticationAttemptLimiter`.

    **The blast radius is new sign-ins only.** `current_principal` never touches JWKS: sessions are
    opaque rows in `staff_sessions`, so staff already signed in keep working for the eight-hour idle
    and twenty-four-hour absolute lifetimes. For a shop whose staff sign in once a shift, an issuer
    outage is an inconvenience at the start of a shift rather than a stop.
    """

    try:
        return StaffIdentityService(AuthSettings())
    except AuthenticationUnavailable as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="staff identity unavailable"
        ) from error


def get_operations_service() -> OperationsService:
    try:
        return OperationsService(AuthSettings())
    except OperationsUnavailable as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable"
        ) from error


def current_principal(
    request: Request,
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> StaffPrincipal:
    session_token = request.cookies.get(_AUTH_SETTINGS.staff_session_cookie_name)
    if not session_token or service is None:
        _record_auth_outcome("session_missing", _authentication_source_key(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="staff session required")
    try:
        return service.current_principal(session_token)
    except IdentityStateError as error:
        _record_auth_outcome("session_rejected", _authentication_source_key(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid staff session") from error


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Return only process health; dependency health is added with real infrastructure."""
    return {"status": "ok"}


@app.post("/internal/v1/auth/session", response_model=SessionResponse, include_in_schema=False)
def exchange_identity_token(
    request: Request,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> SessionResponse:
    source_key = _authentication_source_key(request)
    limit = _AUTH_LIMITER.acquire(source_key)
    if not limit.allowed:
        _record_auth_outcome("throttled", source_key)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="identity exchange temporarily unavailable",
            headers={"Retry-After": str(limit.retry_after_seconds)},
        )
    if service is None or authorization is None or not authorization.startswith("Bearer "):
        _record_auth_outcome("rejected", source_key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="identity token required")
    try:
        token, expires_at = service.exchange(authorization.removeprefix("Bearer "))
        principal = service.current_principal(token)
    except (AuthenticationError, IdentityStateError) as error:
        _record_auth_outcome("rejected", source_key)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="identity exchange rejected"
        ) from error
    _AUTH_LIMITER.reset(source_key)
    csrf_token = token_urlsafe(32)
    response.set_cookie(
        key=_AUTH_SETTINGS.staff_session_cookie_name,
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        expires=expires_at,
        path="/",
    )
    response.set_cookie(
        key=_AUTH_SETTINGS.staff_csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        secure=True,
        samesite="strict",
        expires=expires_at,
        path="/",
    )
    response.headers["X-CSRF-Token"] = csrf_token
    _record_auth_outcome("accepted", source_key)
    return _session_response(principal)


@app.get("/internal/v1/session", response_model=SessionResponse, include_in_schema=False)
def get_session(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
) -> SessionResponse:
    return _session_response(principal)


@app.post("/internal/v1/auth/logout", include_in_schema=False)
def logout(
    request: Request,
    response: Response,
    principal: Annotated[StaffPrincipal | None, Depends(current_principal)] = None,
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> dict[str, str | None]:
    """End the application session, and tell the console where to end the issuer's.

    Ending only this session left every Keycloak cookie in place -- `AUTH_SESSION_ID`,
    `KEYCLOAK_IDENTITY`, `KEYCLOAK_SESSION`, measured after a sign-out -- so the next person to
    press "sign in" on a shop tablet was handed a code without presenting anything at all. That is
    the enabling half of the silent-SSO defect; `acr_values` on the authorization request is the
    other half, and both are needed: one stops a session being reused, the other stops it existing.

    The URL is built here rather than in the console because the issuer is deployment
    configuration, and hard-coding `/idp/realms/...` into `apps/web` would couple the console to
    one topology. A deployment with no issuer configured -- the demo stack -- gets null and the
    console skips the navigation.
    """

    session_token = request.cookies.get(_AUTH_SETTINGS.staff_session_cookie_name)
    if session_token is not None and principal is not None and service is not None:
        # Signing out twice is not a failure. `revoke_session` raises when the session is already
        # gone, and that escaped as a 500.
        #
        # Measured: a *sequential* second sign-out answers 401, because `current_principal` rejects
        # the revoked session before this body runs. The 500 needs two requests genuinely in
        # flight together -- the same person on two tablets, or a retry racing its own original --
        # where both pass authentication and only one wins the revoke. Rare, and worth fixing for
        # what it did rather than how often: the exception fired before the two `delete_cookie`
        # calls below, so the loser kept its cookies and was shown an error for a session that had
        # in fact ended. The goal state is "not signed in", and it is already reached.
        with suppress(IdentityStateError):
            service.logout(session_token, principal)
    # Outside the guard on purpose: whatever happened above, this browser stops holding a session.
    response.delete_cookie(_AUTH_SETTINGS.staff_session_cookie_name, path="/")
    response.delete_cookie(_AUTH_SETTINGS.staff_csrf_cookie_name, path="/")
    return {"end_session_url": _end_session_url()}


def _end_session_url() -> str | None:
    """The issuer's end-session endpoint, but only when a browser here could actually reach it.

    Two things this must not do, both measured against the running demo stack before they were
    fixed. It must not name an issuer the browser cannot resolve: R1 mounts Keycloak same-origin
    behind the proxy, which `connect-src 'self'` forces, but the demo's issuer is
    `https://demo-idp.local` on an internal network -- so the console offered a sign-out that
    navigated to a dead host. Same-origin is therefore the condition, not an incidental property.

    And it must not build the redirect from `request.base_url`. Behind Caddy, uvicorn sees the
    proxied request as `http`, so the parameter came out
    `post_logout_redirect_uri=http%3A%2F%2F...` -- which would not match the `https` URI registered
    in the realm, and Keycloak answers an error page rather than redirecting. The configured origin
    is the authority for what this deployment is called; the request is not.
    """

    issuer = _AUTH_SETTINGS.oidc_issuer
    client_id = _AUTH_SETTINGS.oidc_audience
    if not issuer or not client_id:
        return None

    origins = [item.strip() for item in _AUTH_SETTINGS.staff_allowed_origins.split(",")]
    origin = next((item for item in origins if item and issuer.startswith(f"{item}/")), None)
    if origin is None:
        return None

    # `client_id` rather than `id_token_hint`: the console never keeps the ID token. `callback.js`
    # holds it in one `const` for one fetch and lets it go, which is the property that keeps a
    # bearer token out of storage, and it is worth more than the marginally stronger hint.
    parameters = urlencode(
        {"client_id": client_id, "post_logout_redirect_uri": f"{origin}/signin/"}
    )
    return f"{issuer.rstrip('/')}/protocol/openid-connect/logout?{parameters}"


def _session_response(principal: StaffPrincipal) -> SessionResponse:
    return SessionResponse(
        staff_user_id=str(principal.staff_user_id),
        roles=sorted(role.value for role in principal.roles),
        mfa_verified=principal.mfa_verified,
    )


def _authentication_source_key(request: Request) -> str:
    source = request.client.host if request.client is not None else "unknown"
    return sha256(source.encode("utf-8")).hexdigest()


def _record_auth_outcome(outcome: str, source_key: str) -> None:
    correlation = current_correlation() or CorrelationContext.new()
    _LOGGER.record(
        component="api",
        name="auth.identity_exchange",
        outcome=outcome,
        correlation=correlation,
        fields={"source_hash": source_key},
    )


def _record_authorization_denial(reason_code: str) -> None:
    _LOGGER.record(
        component="api",
        name="auth.authorization",
        outcome="rejected",
        correlation=current_correlation() or CorrelationContext.new(),
        fields={"reason_code": reason_code},
    )


# One refusal body for every authorization failure, whatever caused it.
#
# QUOTE-COMMAND-001 required a membership refusal to be indistinguishable from a role refusal, and
# found that it was not: the role gates named the missing role while `require_store_membership`
# produced "operation denied". Both are 403, but a caller comparing bodies could tell "your role is
# wrong" from "you are not in this store" — and the second is a fact about our data, not about them.
# The specific reason is still recorded, in the structured log, where the operator can read it and
# the caller cannot.
#: Every write route takes this header, and it is constrained here rather than in nineteen places.
#:
#: An empty one used to reach the repository, where `_required_text` raised a bare `ValueError` that
#: no route's except tuple names -- so `Idempotency-Key: ` answered HTTP 500, measured. A blank
#: header is not exotic: it is a template variable that did not interpolate, or a proxy that
#: stripped the value. 500 tells the client to retry a write, which is the one thing an idempotency
#: key exists to make safe.
#:
#: FastAPI answers a violation with 422 and names the header, before any handler runs.
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=300, pattern=r"\S")
]

AUTHORIZATION_DENIED = "operation denied"


@app.exception_handler(StoreAccessError)
def _store_access_denied(_request: Request, error: StoreAccessError) -> JSONResponse:
    """A membership refusal is 403 everywhere, whether or not the route remembered to catch it.

    Per-route `except` tuples cannot carry this invariant, and trying made a test that looked
    stronger than it was. `require_store_membership` is called from deep inside the repositories --
    `orders.py`, `approvals.py`, `assistant.py`, `delivery_legs.py`, `intake.py` and more -- so the
    set of routes that can raise it is not the set whose own body mentions it, and enumerating that
    set by scanning `operations.py` covered six of at least nine. It had already shipped twice as a
    500: on `create_order`, and on `record_settlement`, which is the route that moves money. A 500
    tells the client to retry.

    So it is answered once, here, for the whole surface. The route-level catches stay -- they
    produce the same status and read where the refusal is expected -- but nothing depends on a
    future route remembering to add one.
    """

    del _request, error
    _record_authorization_denial("STORE_MEMBERSHIP_REQUIRED")
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN, content={"detail": AUTHORIZATION_DENIED}
    )


def require_owner(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
) -> StaffPrincipal:
    if StaffRole.OWNER_ADMIN not in principal.roles:
        _record_authorization_denial("OWNER_ROLE_REQUIRED")
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=AUTHORIZATION_DENIED)
    return principal


def require_operations_staff(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
) -> StaffPrincipal:
    allowed = {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
    if not principal.roles & allowed or not principal.mfa_verified:
        _record_authorization_denial("OPERATIONS_ROLE_OR_MFA_REQUIRED")
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=AUTHORIZATION_DENIED)
    return principal


def require_approval_staff(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
) -> StaffPrincipal:
    allowed = {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER}
    if not principal.roles & allowed or not principal.mfa_verified:
        _record_authorization_denial("APPROVAL_ROLE_OR_MFA_REQUIRED")
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=AUTHORIZATION_DENIED)
    return principal


@app.post(
    "/internal/v1/staff",
    response_model=StaffCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_staff(
    request: StaffCreateRequest,
    principal: Annotated[StaffPrincipal, Depends(require_owner)],
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> StaffCreateResponse:
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="staff identity unavailable"
        )
    try:
        staff_id = service.create_staff(
            oidc_subject=request.oidc_subject,
            display_name=request.display_name,
            email=request.email,
            actor_id=principal.staff_user_id,
        )
    # Ordered: the taken-subject case is a subclass-free sibling of IdentityStateError and answers
    # a different question. "cannot be created" tells an owner nothing; "already exists" tells them
    # to look for the person instead of pressing the button again.
    except StaffSubjectTakenError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="this OIDC subject already belongs to a staff user",
        ) from error
    except IdentityStateError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="staff user cannot be created"
        ) from error
    return StaffCreateResponse(staff_user_id=str(staff_id))


@app.post("/internal/v1/staff/{staff_user_id}/roles", status_code=status.HTTP_204_NO_CONTENT)
def assign_staff_role(
    staff_user_id: UUID,
    request: RoleAssignmentRequest,
    principal: Annotated[StaffPrincipal, Depends(require_owner)],
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> None:
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="staff identity unavailable"
        )
    try:
        service.assign_role(staff_user_id, request.role, principal.staff_user_id)
    except IdentityStateError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="staff user unavailable") from error


@app.post(
    "/internal/v1/staff/{staff_user_id}/stores/{store_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def assign_staff_store(
    staff_user_id: UUID,
    store_id: UUID,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_owner)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> None:
    """Grant a staff member access to one store.

    The first route here that hands out an authorization rather than checking one. `require_owner`
    is a convenience that fails fast; the repository re-checks against the database, because a
    session minted while its holder was an owner keeps claiming so after the role is revoked.

    `OWNER_ADMIN` is not implicitly a member of every store, and this route does not change that.
    An owner who wants to see a store assigns themselves, and that assignment is audited like any
    other — including who granted it.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        service.assign_store(
            staff_user_id=staff_user_id,
            store_id=store_id,
            principal=principal,
            idempotency_key=idempotency_key,
        )
    except (ShadowAuthorizationError, ShadowStateError, IdempotencyConflictError) as error:
        _raise_shadow_error(error)


@app.delete(
    "/internal/v1/staff/{staff_user_id}/stores/{store_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_staff_store(
    staff_user_id: UUID,
    store_id: UUID,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_owner)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> None:
    """End a staff member's access to one store.

    A grant with no revoke leaves disabling the whole account as the only way to remove access,
    which is blunter than the situation usually needs and loses the person's history. The
    assignment row is kept and marked revoked, so who granted it, who ended it and when all survive.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        service.revoke_store(
            staff_user_id=staff_user_id,
            store_id=store_id,
            principal=principal,
            idempotency_key=idempotency_key,
        )
    except (ShadowAuthorizationError, ShadowStateError, IdempotencyConflictError) as error:
        _raise_shadow_error(error)


@app.post("/internal/v1/staff/{staff_user_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
def disable_staff(
    staff_user_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_owner)],
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> None:
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="staff identity unavailable"
        )
    try:
        service.disable_staff(staff_user_id, principal.staff_user_id)
    except IdentityStateError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="staff user unavailable") from error


@app.post("/internal/v1/sessions/{session_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> None:
    if principal.session_id != session_id and StaffRole.OWNER_ADMIN not in principal.roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="session revoke denied")
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="staff identity unavailable"
        )
    try:
        service.revoke_session(session_id, principal.staff_user_id)
    except IdentityStateError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="session unavailable") from error


@app.post(
    "/internal/v1/stores/{store_id}/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_order(
    store_id: UUID,
    request: OrderCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> OrderResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        stored = service.create_order(
            store_id=store_id,
            bound_contact_id=request.bound_contact_id,
            quote_id=request.quote_id,
            quote_revision=request.quote_revision,
            quote_snapshot_hash=request.quote_snapshot_hash,
            fulfillment_mode=request.fulfillment_mode,
            accepted_at=request.customer_final_quote_accepted_at,
            acquisition_source=request.acquisition_source,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    # `OrderRepository.create` refuses a non-member with `OrderAuthorizationError`, which descends
    # from `PermissionError` and therefore from `OSError` — not from `ValueError`. Catching only
    # `ValueError` here turned the single most common condition in the system (a staff user who
    # holds the right role but has no `staff_store_assignments` row yet) into a 500, and a 500 is
    # what tells a client to retry. `_raise_operations_error` was always written to map this to 403.
    except (OrderAuthorizationError, ValueError) as error:
        _raise_operations_error(error)
    return _order_response(stored)


@app.get("/internal/v1/stores", response_model=MemberStoresResponse)
def list_member_stores(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> MemberStoresResponse:
    """List the stores the caller is assigned to.

    Membership, not role, is what every store-scoped route enforces, and there was no way to ask
    what one's own membership is. An empty list is a legitimate and common answer — it is the state
    of every staff user the API can create, because assigning a store has no route — so the client
    must render it as "you have no store yet, ask an owner", never as an error.
    """

    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    assigned = service.list_member_stores(principal=principal)
    return MemberStoresResponse(
        store_ids=[store_id for store_id, _name in assigned],
        stores=[MemberStore(store_id=store_id, name=name) for store_id, name in assigned],
    )


@app.get("/internal/v1/stores/{store_id}/orders", response_model=list[OrderViewResponse])
def list_orders(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
    limit: int = 100,
    open_only: Annotated[bool, Query(alias="open")] = False,
    ticket: Annotated[int | None, Query(ge=1, le=100_000)] = None,
    ticket_date: date | None = None,
) -> list[OrderViewResponse]:
    """The store's orders, newest first.

    `open=true` keeps every order not yet completed or cancelled, whatever its age -- the board's
    newest hundred is three days of trade, and laundry is collected later than that. `ticket=17`
    finds the order a walk-in ticket tracks (`DEC-013`); numbers restart daily, so it means today's
    17 on the shop's business day unless `ticket_date` names the day on the slip.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    if ticket_date is not None and ticket is None:
        # A date alone is not a lookup; answering the whole board would look like one.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="ticket_date needs a ticket number"
        )
    try:
        return [
            _order_view_response(item)
            for item in service.list_orders(
                store_id=store_id,
                principal=principal,
                limit=limit,
                open_only=open_only,
                ticket_number=ticket,
                ticket_date=ticket_date,
            )
        ]
    except (OrderAuthorizationError, ValueError) as error:
        _raise_operations_error(error)


@app.get("/internal/v1/orders/{order_id}", response_model=OrderViewResponse)
def read_order(
    order_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> OrderViewResponse:
    """One order, by id, for a member of the store the order belongs to.

    The only way to reach an order that has left the board's newest page, and the only read that
    hands back a `row_version` for one -- which every transition needs as `If-Match`. The store is
    the row's, never the request's. A missing order and another store's order are the same 404, so
    the route cannot be used to learn which ids exist where; a role that may not read orders at all
    is the usual opaque 403.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        return _order_view_response(service.read_order(order_id=order_id, principal=principal))
    except OrderNotVisibleError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="order unavailable") from error
    except OrderAuthorizationError as error:
        _raise_operations_error(error)


@app.post("/internal/v1/orders/{order_id}/transition", response_model=OrderResponse)
def transition_order(
    order_id: UUID,
    request: CommercialTransitionRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> OrderResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    expected = _parse_if_match(if_match)
    try:
        stored = service.transition_commercial(
            order_id=order_id,
            target=request.target,
            expected_row_version=expected,
            idempotency_key=idempotency_key,
            principal=principal,
            custody_resolution=request.custody_resolution,
        )
    except (OrderStateError, OrderAuthorizationError, IdempotencyConflictError) as error:
        _raise_operations_error(error)
    return _order_response(stored)


@app.post("/internal/v1/orders/{order_id}/intake-transition", response_model=OrderResponse)
def transition_order_intake(
    order_id: UUID,
    request: IntakeTransitionRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> OrderResponse:
    """Move the order's intake: the laundry arrived, was inspected, and was accepted for work.

    Until 2026-08-29 this route did not exist, and neither did the production one. A verification
    pass drove the whole lifecycle over HTTP and found every order stopping dead at CONFIRMED --
    `transition_commercial` refuses ACTIVE while intake is not ACCEPTED, and nothing could move
    intake. The domain and the repository had always supported it; the product had no way to ask.

    `slot_approved` is the only readiness fact the caller supplies. The other five are read from the
    order and its bound quote by the service, because a client asserting "an exact price was
    approved" would be a client asserting server state.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    expected = _parse_if_match(if_match)
    try:
        stored = service.transition_intake(
            order_id=order_id,
            target=request.target,
            expected_row_version=expected,
            idempotency_key=idempotency_key,
            principal=principal,
            slot_approved=request.slot_approved,
        )
    except (OrderStateError, OrderAuthorizationError, IdempotencyConflictError) as error:
        _raise_operations_error(error)
    return _order_response(stored)


@app.post("/internal/v1/orders/{order_id}/production-transition", response_model=OrderResponse)
def transition_order_production(
    order_id: UUID,
    request: ProductionTransitionRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> OrderResponse:
    """Move the order through washing: queued, in process, checked, ready, released.

    The screen offers every target and the server refuses the illegal ones, exactly as the
    commercial transition does. Copying the sequence into the browser would make a second rulebook
    nobody keeps in step with the first.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    expected = _parse_if_match(if_match)
    try:
        stored = service.transition_production(
            order_id=order_id,
            target=request.target,
            expected_row_version=expected,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (OrderStateError, OrderAuthorizationError, IdempotencyConflictError) as error:
        _raise_operations_error(error)
    return _order_response(stored)


@app.post(
    "/internal/v1/orders/{order_id}/settlement",
    response_model=SettlementResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_settlement(
    order_id: UUID,
    request: SettlementRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> SettlementResponse:
    """Attest that the customer paid the quoted total and collected their goods.

    The staff member records what they witnessed at the counter; the amount is checked against the
    immutable quote revision the order is bound to. Nothing here computes or adjusts money.

    Only one settlement shape exists. Anything else — a part payment, a deposit, an overpayment,
    credit terms, or goods that left by a delivery leg — is refused with the reason and the open
    decision that owns it, because those are the business owner's to make and `DEC-010` holds them.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        stored = service.record_settlement(
            order_id=order_id,
            paid_amount_vnd=request.paid_amount_vnd,
            collected_by_customer=request.collected_by_customer,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    # `StoreAccessError` is a `PermissionError`, not a `ValueError`, and this route is the one that
    # moves money. `record_settlement` runs `_require_order_store_membership` before the idempotency
    # claim, so a staff member holding the settlement role but not assigned to the order's store
    # raised it here and it escaped as a 500 -- verified by execution on 2026-09-09. A 500 tells the
    # client to retry, which is the worst possible advice on a payment. Eight sibling routes already
    # catch it; this one did not.
    except (SettlementAuthorizationError, StoreAccessError) as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=AUTHORIZATION_DENIED) from error
    except SettlementStateError as error:
        # The reason and its decision travel intact. "Not supported" with no way to learn which
        # question is unanswered would send a staff member to find a workaround.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "outcome": "NOT_SUPPORTED",
                "reason_code": error.reason_code,
                "decision": error.decision,
            },
        ) from error
    except IdempotencyConflictError as error:
        _raise_operations_error(error)
    return SettlementResponse(
        settlement_id=stored.settlement_id,
        order_id=stored.order_id,
        expected_total_vnd=stored.expected_total_vnd,
        paid_amount_vnd=stored.paid_amount_vnd,
        settlement_shape=stored.settlement_shape,
        balance_status=stored.balance_status,
        self_collection_recorded=stored.self_collection_recorded,
        row_version=stored.row_version,
        replayed=stored.replayed,
    )


@app.post(
    "/internal/v1/approvals",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_approval(
    request: ApprovalRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> ApprovalResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        stored = service.request_approval(
            store_id=request.store_id,
            action=request.action,
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            resource_version=request.resource_version,
            snapshot_hash=request.snapshot_hash,
            rendered_hash=request.rendered_hash,
            policy_version=request.policy_version,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (
        ApprovalEnvelopeError,
        ApprovalStateError,
        ApprovalAuthorizationError,
        IdempotencyConflictError,
    ) as error:
        _raise_operations_error(error)
    return _approval_response(stored)


@app.get("/internal/v1/approvals", response_model=list[ApprovalResponse])
def list_pending_approvals(
    principal: Annotated[StaffPrincipal, Depends(require_approval_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
    limit: int = 100,
) -> list[ApprovalResponse]:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        return [
            _approval_response(item)
            for item in service.list_pending_approvals(principal=principal, limit=limit)
        ]
    except ValueError as error:
        _raise_operations_error(error)


@app.post("/internal/v1/approvals/{approval_id}/decisions", response_model=ApprovalResponse)
def decide_approval(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_approval_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> ApprovalResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        stored = service.decide_approval(
            approval_id=approval_id,
            decision=request.decision,
            resource_version=request.resource_version,
            snapshot_hash=request.snapshot_hash,
            rendered_hash=request.rendered_hash,
            reason_code=request.reason_code,
            note=request.note,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (ApprovalAuthorizationError, ApprovalStateError, IdempotencyConflictError) as error:
        _raise_operations_error(error)
    return _approval_response(stored)


@app.get(
    "/internal/v1/approvals/{approval_id}/range-price-proposal",
    response_model=RangePriceProposalContentResponse,
)
def read_range_price_proposal(
    approval_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_approval_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> RangePriceProposalContentResponse:
    """The amounts behind one `SET_RANGE_PRICE` envelope, for the person being asked to sign it.

    `RANGE-APPROVAL-VISIBILITY-001`. Keyed by the approval rather than by the store, for the same
    reason `POST /internal/v1/remedy-proposals/{id}/execution` is: the store comes from the stored
    row, never from a caller who could name one. Membership against that store is required inside
    `RangePriceProposalRepository.read`.

    404 covers three different truths on purpose -- no such approval, an approval of some other
    kind, and an envelope whose amounts were never recorded. A caller learns nothing about another
    shop's approvals from the difference, and the third case fails closed exactly as the first two
    do: the console cannot show a number, so it offers no approve control.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        record = service.read_range_price_proposal(approval_id=approval_id, principal=principal)
    except RangePriceProposalIntegrityError as error:
        # Before `ValueError`, which it is a subclass of. A 422 with the code rather than the 409
        # with a message string the generic mapper would produce: this is a refusal the console has
        # to render as a refusal -- the amounts are withheld and the approve control stays shut --
        # and a console that has to parse a sentence to know that is a console that will not.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"outcome": "REQUIRE_HUMAN", "reason_codes": [str(error)]},
        ) from error
    except (StoreAccessError, ValueError) as error:
        _raise_operations_error(error)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="no proposed range price for this approval"
        )
    return RangePriceProposalContentResponse(
        approval_request_id=record.approval_id,
        quote_id=record.quote_id,
        revision=record.revision,
        pricebook_version=record.pricebook_version,
        rendered_hash=record.rendered_hash,
        proposed_at=record.proposed_at,
        lines=[
            ProposedRangePriceLineResponse(
                service_code=line.service_code,
                band_minimum_vnd=line.band_minimum_vnd,
                band_maximum_vnd=line.band_maximum_vnd,
                proposed_amount_vnd=line.proposed_amount_vnd,
            )
            for line in record.lines
        ],
    )


@app.post(
    "/internal/v1/stores/{store_id}/quotes",
    response_model=QuoteRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_quote(
    store_id: UUID,
    request: QuoteCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> QuoteRevisionResponse:
    """Price a garment through the deterministic engine and commit an immutable revision.

    There is no arithmetic in this function, and there must never be. It reads what the caller
    asked for, hands it to `OperationsService.create_quote`, and translates the domain's answer into
    a status code. Every number in the response was computed inside `packages/domain`.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    if request.quote_id is None and request.expected_current_revision != 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="a new quote starts at revision 0")
    try:
        result = service.create_quote(
            store_id=store_id,
            bound_order_request_id=request.bound_order_request_id,
            lines=tuple(
                RequestedLine(
                    service_code=line.service_code,
                    quantity=line.quantity,
                    unit=line.unit,
                    quantity_basis=line.quantity_basis,
                )
                for line in request.lines
            ),
            fulfillment_mode=request.fulfillment_mode,
            verified_distance_m=request.verified_distance_m,
            planned_transport_weight_kg=request.planned_transport_weight_kg,
            approved_manual_fee_vnd=request.approved_manual_fee_vnd,
            customer_acknowledged_manual_fee=request.customer_acknowledged_manual_fee,
            idempotency_key=idempotency_key,
            principal=principal,
            quote_id=request.quote_id,
            expected_current_revision=request.expected_current_revision,
            expected_row_version=0 if request.quote_id is None else _parse_if_match(if_match),
            present_range_as_band=request.present_range_as_band,
        )
    except QuotePricingUnavailable as error:
        # No approved price list means no price. This is a refusal, not an outage of convenience.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="pricebook unavailable"
        ) from error
    except (StoreAccessError, QuoteStateError, QuoteIntegrityError, ValueError) as error:
        _raise_operations_error(error)
    if isinstance(result, UnresolvedQuoteResult):
        # Unresolved policy travels intact: the reason codes are the engine's, and no price, no
        # default and no partial row was produced alongside them.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"outcome": "REQUIRE_HUMAN", "reason_codes": list(result.reason_codes)},
        )
    return QuoteRevisionResponse(
        quote_id=result.quote_id,
        revision=result.revision,
        row_version=result.row_version,
        finality=result.finality,
        status=result.status,
        snapshot_hash=result.snapshot_hash,
        list_service_subtotal_vnd=result.list_service_subtotal_vnd,
        net_service_subtotal_vnd=result.net_service_subtotal_vnd,
        display_total_min_vnd=result.display_total_min_vnd,
        display_total_max_vnd=result.display_total_max_vnd,
        reason_codes=list(result.reason_codes),
        required_approvals=list(result.required_approvals),
        promotion=_promotion_response(result.promotion),
        replayed=result.replayed,
    )


class CounterTicketResponse(BaseModel):
    """What the counter hands over. There is no customer field, and that is deliberate."""

    ticket_id: UUID
    ticket_number: int
    issued_on: date


@app.post(
    "/internal/v1/stores/{store_id}/counter-tickets",
    response_model=CounterTicketResponse,
    status_code=status.HTTP_201_CREATED,
)
def issue_counter_ticket(
    store_id: UUID,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> CounterTicketResponse:
    """Issue the number a walk-in customer's order is tracked by.

    `DEC-013`, resolved 2026-08-26. No request body: nothing about the person is collected, so
    there is nothing to send. `ticket_id` is what an order carries as its customer reference; it
    identifies an order's customer, not a customer.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        issued = service.issue_counter_ticket(
            store_id=store_id, idempotency_key=idempotency_key, principal=principal
        )
    except (StoreAccessError, CounterTicketError, IdempotencyConflictError) as error:
        _raise_operations_error(error)
    return CounterTicketResponse(
        ticket_id=issued.ticket_id,
        ticket_number=issued.ticket_number,
        issued_on=issued.issued_on,
    )


class DeliveryLegRequest(StrictRequest):
    """One delivery attempt. There is no amount field: the customer already paid at the counter."""

    leg_kind: DeliveryLegKind
    outcome: DeliveryLegOutcome


class DeliveryLegResponse(BaseModel):
    leg_id: UUID
    order_id: UUID
    leg_kind: str
    outcome: str
    completes_fulfillment: bool


@app.post(
    "/internal/v1/orders/{order_id}/delivery-legs",
    response_model=DeliveryLegResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_delivery_leg(
    order_id: UUID,
    request: DeliveryLegRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> DeliveryLegResponse:
    """Record that laundry went out, and whether the customer received it.

    `DEC-023`, resolved 2026-08-26. A failed attempt is recorded and charges nothing; a retry is a
    new leg. Only a succeeded return leg lets the order be completed, and the order transition still
    enforces that separately.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        stored = service.record_delivery_leg(
            order_id=order_id,
            leg_kind=request.leg_kind,
            outcome=request.outcome,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (StoreAccessError, DeliveryLegError, IdempotencyConflictError) as error:
        _raise_operations_error(error)
    return DeliveryLegResponse(
        leg_id=stored.leg_id,
        order_id=stored.order_id,
        leg_kind=stored.leg_kind,
        outcome=stored.outcome,
        completes_fulfillment=stored.completes_fulfillment,
    )


class QuoteAcceptRequest(StrictRequest):
    """The revision the customer agreed to, named exactly.

    Both fields are the operator's evidence that the price on their screen is the price the customer
    heard. The server refuses if either has moved, because an attestation naming the wrong revision
    would record a customer agreeing to something they never saw.
    """

    expected_current_revision: StrictInt = Field(ge=1)
    expected_snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")


@app.post(
    "/internal/v1/stores/{store_id}/quotes/{quote_id}/acceptance",
    response_model=QuoteRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def accept_quote(
    store_id: UUID,
    quote_id: UUID,
    request: QuoteAcceptRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> QuoteRevisionResponse:
    """Record that a named staff member witnessed the customer accept this exact price.

    `DEC-021`, resolved 2026-08-25. This function decides nothing: it carries an attestation to the
    service, which writes it to `quote_acceptances` and derives the accepted revision from the
    priced one. No price is recomputed on this path -- the accepted revision carries the same lines,
    totals and traces as the revision the customer was read.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        result = service.accept_quote(
            store_id=store_id,
            quote_id=quote_id,
            expected_current_revision=request.expected_current_revision,
            expected_snapshot_hash=request.expected_snapshot_hash,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (StoreAccessError, QuoteStateError, QuoteIntegrityError, ValueError) as error:
        _raise_operations_error(error)
    if isinstance(result, UnresolvedQuoteResult):
        # The engine refused to derive an accepted revision. Its reason codes travel intact so the
        # console can name the missing fact while the customer is still standing there -- most often
        # that the quantity was the customer's estimate rather than a weighing.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"outcome": "REQUIRE_HUMAN", "reason_codes": list(result.reason_codes)},
        )
    return QuoteRevisionResponse(
        quote_id=result.quote_id,
        revision=result.revision,
        row_version=result.row_version,
        finality=result.finality,
        status=result.status,
        snapshot_hash=result.snapshot_hash,
        list_service_subtotal_vnd=result.list_service_subtotal_vnd,
        net_service_subtotal_vnd=result.net_service_subtotal_vnd,
        display_total_min_vnd=result.display_total_min_vnd,
        display_total_max_vnd=result.display_total_max_vnd,
        reason_codes=list(result.reason_codes),
        required_approvals=list(result.required_approvals),
        promotion=_promotion_response(result.promotion),
        replayed=result.replayed,
    )


# --- RANGE-PRICE-001: closing a published price band ------------------------------------------
#
# Two routes, and the second cannot be reached without the existing approval-decision route in
# between. Neither does arithmetic on money: the first hands the amounts to the domain and persists
# an envelope, the second hands them to the domain again and persists what comes back.


@app.post(
    "/internal/v1/stores/{store_id}/quotes/{quote_id}/range-prices",
    response_model=RangePriceProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
def propose_range_prices(
    store_id: UUID,
    quote_id: UUID,
    request: RangePriceRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> RangePriceProposalResponse:
    """Propose exact amounts inside the bands this quote revision published, for approval.

    The published band is the bound and the server owns it: this function forwards amounts and
    nothing else. An amount outside the band is refused here, before any approval row exists, and
    the refusal carries the domain's own code so the console can name it in Vietnamese.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        result = service.propose_range_prices(
            store_id=store_id,
            quote_id=quote_id,
            expected_current_revision=request.expected_current_revision,
            expected_snapshot_hash=request.expected_snapshot_hash,
            choices=tuple(
                RangePriceChoice(service_code=choice.service_code, amount_vnd=choice.amount_vnd)
                for choice in request.choices
            ),
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (
        ApprovalEnvelopeError,
        ApprovalStateError,
        ApprovalAuthorizationError,
        IdempotencyConflictError,
        StoreAccessError,
        QuoteStateError,
        QuoteIntegrityError,
        ValueError,
    ) as error:
        _raise_operations_error(error)
    if isinstance(result, UnresolvedQuoteResult):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"outcome": "REQUIRE_HUMAN", "reason_codes": list(result.reason_codes)},
        )
    return RangePriceProposalResponse(
        approval_request_id=result.approval.approval_request_id,
        status=result.approval.status,
        envelope_hash=result.approval.envelope_hash,
        required_role=result.approval.required_role.value,
        expires_at=result.approval.expires_at.isoformat(),
        resource_version=result.resource_version,
        snapshot_hash=result.snapshot_hash,
        rendered_hash=result.rendered_hash,
        replayed=result.approval.replayed,
    )


@app.post(
    "/internal/v1/stores/{store_id}/quotes/{quote_id}/range-prices/{approval_id}",
    response_model=QuoteRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def apply_range_prices(
    store_id: UUID,
    quote_id: UUID,
    approval_id: UUID,
    request: RangePriceRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> QuoteRevisionResponse:
    """Write the approved amounts into a new revision derived from the band the customer saw.

    The amounts are sent again because the envelope stores a digest, not the content. The server
    re-derives the digest and refuses unless it is the one the approver bound, which is invariant 8
    and is also why editing a line invalidates the approval: an edit is a new revision.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        result = service.apply_range_prices(
            store_id=store_id,
            quote_id=quote_id,
            approval_id=approval_id,
            expected_current_revision=request.expected_current_revision,
            expected_snapshot_hash=request.expected_snapshot_hash,
            choices=tuple(
                RangePriceChoice(service_code=choice.service_code, amount_vnd=choice.amount_vnd)
                for choice in request.choices
            ),
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (
        ApprovalStateError,
        ApprovalAuthorizationError,
        IdempotencyConflictError,
        StoreAccessError,
        QuoteStateError,
        QuoteIntegrityError,
        ValueError,
    ) as error:
        _raise_operations_error(error)
    if isinstance(result, UnresolvedQuoteResult):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"outcome": "REQUIRE_HUMAN", "reason_codes": list(result.reason_codes)},
        )
    return QuoteRevisionResponse(
        quote_id=result.quote_id,
        revision=result.revision,
        row_version=result.row_version,
        finality=result.finality,
        status=result.status,
        snapshot_hash=result.snapshot_hash,
        list_service_subtotal_vnd=result.list_service_subtotal_vnd,
        net_service_subtotal_vnd=result.net_service_subtotal_vnd,
        display_total_min_vnd=result.display_total_min_vnd,
        display_total_max_vnd=result.display_total_max_vnd,
        reason_codes=list(result.reason_codes),
        required_approvals=list(result.required_approvals),
        promotion=_promotion_response(result.promotion),
        replayed=result.replayed,
    )


@app.get(
    "/internal/v1/stores/{store_id}/quotes/{quote_id}",
    response_model=QuoteRevisionDetailResponse,
)
def read_quote(
    store_id: UUID,
    quote_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
    revision: int | None = None,
) -> QuoteRevisionDetailResponse:
    """One revision with its lines, so a console can draw the band it is asking staff to price in.

    The list read returns totals, which is enough to show a quote and not enough to close one.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        view = service.read_quote(
            store_id=store_id, quote_id=quote_id, principal=principal, revision=revision
        )
    except (StoreAccessError, QuoteStateError, QuoteIntegrityError, ValueError) as error:
        _raise_operations_error(error)
    return QuoteRevisionDetailResponse(
        quote_id=view.quote_id,
        revision=view.revision,
        row_version=view.row_version,
        finality=view.finality,
        status=view.status,
        snapshot_hash=view.snapshot_hash,
        display_total_min_vnd=view.display_total_min_vnd,
        display_total_max_vnd=view.display_total_max_vnd,
        valid_until=view.valid_until,
        reason_codes=list(view.reason_codes),
        lines=[
            QuoteLineResponse(
                line_id=line.line_id,
                service_code=line.service_code,
                quantity=line.quantity,
                unit=line.unit,
                price_kind=line.price_kind,
                net_amount_vnd=line.net_amount_vnd,
                band_minimum_vnd=line.band_minimum_vnd,
                band_maximum_vnd=line.band_maximum_vnd,
            )
            for line in view.lines
        ],
        customer_accepted_at=view.customer_accepted_at,
    )


@app.get("/internal/v1/stores/{store_id}/quotes", response_model=list[QuoteSummaryResponse])
def list_quotes(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
    limit: int = 100,
) -> list[QuoteSummaryResponse]:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        return [
            QuoteSummaryResponse.model_validate(item, from_attributes=True)
            for item in service.list_quotes(store_id=store_id, principal=principal, limit=limit)
        ]
    # `StoreAccessError` is a `PermissionError`; see the note on `create_order`. The write path for
    # quotes already catches it (`main.py` `create_quote`), so before this line a staff member could
    # be told 403 when creating a quote and 500 when listing the same store's quotes.
    except (StoreAccessError, ValueError) as error:
        _raise_operations_error(error)


# --- Today's counter takings: the one money read the console makes ----------------------------
#
# The owner's morning has a money question in it, and until now the console had no honest answer:
# the assistant refuses revenue questions because no revenue read existed. This is not that read,
# and it deliberately does not make the assistant able to answer one. It is a narrower fact with a
# ledger behind it — the sum of today's attested settlements — and it is named for what it is.
#
# Why this is safe to show when "doanh thu" is not: `order_settlements` is append-only, one row per
# order, and the database itself constrains `paid_amount_vnd = expected_total_vnd`, so every row is
# a customer who paid the quoted total in full at the counter. Summing it involves no policy, no
# proration and no model. Revenue would require answering what to do about work in progress,
# delivery collections, partial refunds and B2B accounts — all of which are DEC-010, and none of
# which this route pretends to have settled.
#
# The one refund that does exist travels beside it (`collected-today-v2`): a paid order cancelled
# under a DEC-024 resolution that charges the customer nothing hands the whole settled amount back,
# and an append-only `order_refunds` row records it. With only the takings on the card, the card
# read above the drawer by every refund.


class CollectedTodayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: `collected-today-v2` (`DEC-024`). Every amount is non-negative integer VND (invariant 2),
    #: and every one is computed by the database: `collected_vnd` is today's settlements, gross,
    #: exactly as in v1; `refunded_vnd` is today's refunds, gross; `net_vnd` is the magnitude of
    #: the drawer's movement and `net_direction` says which way. The console does no arithmetic.
    collected_vnd: int = Field(ge=0)
    settlement_count: int = Field(ge=0)
    refunded_vnd: int = Field(ge=0)
    refund_count: int = Field(ge=0)
    net_vnd: int = Field(ge=0)
    net_direction: Literal["IN", "OUT"]
    business_timezone: str
    #: `OPS-BOARD-001`, invariant 18: the identifier of the rule that produced the figure travels
    #: with the figure. This is the only money the console shows, so it is the one where "which
    #: rule was this" most needs to survive onto a printout.
    query_version: str


@app.get(
    "/internal/v1/stores/{store_id}/settlements/today",
    response_model=CollectedTodayResponse,
)
def collected_today(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> CollectedTodayResponse:
    """Money attested as collected at this store's counter, on today's local business day."""
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        collected = service.collected_today(store_id=store_id, principal=principal)
    except SettlementAuthorizationError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=AUTHORIZATION_DENIED) from error
    except (StoreAccessError, ValueError) as error:
        _raise_operations_error(error)
    # The window is published with the figure. A total whose day boundary the reader has to guess
    # is a number they cannot check, and this one is settled by the counter's clock, not by UTC.
    return CollectedTodayResponse(
        collected_vnd=collected.collected_vnd,
        settlement_count=collected.settlement_count,
        refunded_vnd=collected.refunded_vnd,
        refund_count=collected.refund_count,
        net_vnd=collected.net_vnd,
        net_direction=collected.net_direction,
        business_timezone=BUSINESS_TIMEZONE,
        query_version=COLLECTED_TODAY_QUERY.label,
    )


# --- The published service catalog: what the quote form's picker offers -----------------------
#
# The quote form used to make an operator type a service code from memory — 43 uppercase tokens.
# The published pricebook already carries the approved Vietnamese display names, so this read hands
# them to the picker. It is deployment-global (the pricebook is not store data), role-gated like
# every pricing surface, and digest-gated by the same check that prices.


class PricebookServiceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    display_name: str
    category: str
    unit: str


@app.get(
    "/internal/v1/pricebook/services",
    response_model=list[PricebookServiceResponse],
)
def list_pricebook_services(
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> list[PricebookServiceResponse]:
    """Names for the picker, in published order. No store scope: the catalog is not store data."""
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        services = service.list_published_services()
    except QuotePricingUnavailable as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="pricebook unavailable"
        ) from error
    return [
        PricebookServiceResponse(
            code=item.code,
            display_name=item.display_name,
            category=item.category,
            unit=item.unit.value,
        )
        for item in services
    ]


# --- INTAKE-UI-001: staff counter intake onto the order-request aggregate ----------------------
#
# The Báo giá screen used to demand a pasted order-request UUID. These three routes are the staff
# half of the intake aggregate the agent tool path already writes: create a draft bound to an
# existing contact, list a store's drafts, fetch one for the quote prefill. The role gate is the
# quote gate, because pricing one of these is the very next thing the same person does.


class OrderRequestCreateRequest(StrictRequest):
    # The one field a counter intake may name. The binding must already exist — the domain's only
    # source of contact bindings is the verified channel envelope, and this surface creates none.
    # There is deliberately no free-text field: the aggregate has no column for customer words,
    # and an intake request body is not a place to smuggle them.
    contact_binding_id: UUID


class OrderRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_request_id: UUID
    store_id: UUID
    contact_binding_id: UUID
    status: str
    row_version: int
    created_at: datetime
    replayed: bool


class OrderRequestSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_request_id: UUID
    contact_binding_id: UUID
    status: str
    row_version: int
    created_at: datetime


@app.post(
    "/internal/v1/stores/{store_id}/order-requests",
    response_model=OrderRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_order_request(
    store_id: UUID,
    request: OrderRequestCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> OrderRequestResponse:
    """Record that a customer is at the counter, bound to a contact the server already knows.

    The handler holds no intake rule: membership and contact existence are checked in the service,
    the write is `OrderRequestRepository.create` auditing as STAFF, and the idempotency ledger
    decides what a repeated key means. A contact binding nobody recorded fails closed — a human
    establishes one through a channel first; this route never invents one.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        stored = service.create_order_request(
            store_id=store_id,
            contact_binding_id=request.contact_binding_id,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except ChannelBindingError as error:
        # Same shape as the quote engine's refusal: the domain cannot proceed and a person must,
        # with the reason code intact rather than paraphrased.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"outcome": "REQUIRE_HUMAN", "reason_codes": ["CONTACT_BINDING_UNKNOWN"]},
        ) from error
    except (StoreAccessError, ValueError, IdempotencyConflictError) as error:
        _raise_operations_error(error)
    return OrderRequestResponse(
        order_request_id=stored.order_request_id,
        store_id=stored.store_id,
        contact_binding_id=stored.contact_binding_id,
        status=stored.status,
        row_version=stored.row_version,
        created_at=stored.created_at,
        replayed=stored.replayed,
    )


@app.get(
    "/internal/v1/stores/{store_id}/order-requests",
    response_model=list[OrderRequestSummaryResponse],
)
def list_order_requests(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
    limit: int = 100,
) -> list[OrderRequestSummaryResponse]:
    """Newest first, capped at 100 server-side. No customer text exists on this aggregate."""
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        return [
            _order_request_summary_response(item)
            for item in service.list_order_requests(
                store_id=store_id, principal=principal, limit=limit
            )
        ]
    except (StoreAccessError, ValueError) as error:
        _raise_operations_error(error)


@app.get(
    "/internal/v1/stores/{store_id}/order-requests/{order_request_id}",
    response_model=OrderRequestSummaryResponse,
)
def get_order_request(
    store_id: UUID,
    order_request_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> OrderRequestSummaryResponse:
    """One intake draft for the quote prefill. Another store's id is this store's 404."""
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        stored = service.get_order_request(
            store_id=store_id, order_request_id=order_request_id, principal=principal
        )
    except (StoreAccessError, ValueError) as error:
        _raise_operations_error(error)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="order request not found")
    return _order_request_summary_response(stored)


def _order_request_summary_response(item: OrderRequestSummary) -> OrderRequestSummaryResponse:
    return OrderRequestSummaryResponse(
        order_request_id=item.order_request_id,
        contact_binding_id=item.contact_binding_id,
        status=item.status,
        row_version=item.row_version,
        created_at=item.created_at,
    )


@app.post(
    "/internal/v1/approvals/{approval_id}/manual-send",
    response_model=ManualSendResponse,
    status_code=status.HTTP_201_CREATED,
)
def prepare_manual_send(
    approval_id: UUID,
    request: ManualSendPrepareRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> ManualSendResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        result = service.prepare_manual_send(
            approval_request_id=approval_id,
            observed_resource_version=request.observed_resource_version,
            observed_snapshot_hash=request.observed_snapshot_hash,
            observed_rendered_hash=request.observed_rendered_hash,
            recipient_binding_id=request.recipient_binding_id,
            channel=request.channel,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (ManualSendAuthorizationError, ManualSendStateError, IdempotencyConflictError) as error:
        _raise_operations_error(error)
    return _manual_send_response(result)


@app.post(
    "/internal/v1/manual-sends/{manual_send_id}/attest",
    response_model=ManualSendResponse,
)
def attest_manual_send(
    manual_send_id: UUID,
    request: ManualSendAttestationRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> ManualSendResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        result = service.attest_manual_send(
            manual_send_envelope_id=manual_send_id,
            observed_resource_version=request.observed_resource_version,
            exact_rendered_hash=request.exact_rendered_hash,
            expected_envelope_row_version=_parse_if_match(if_match),
            sent_at=request.sent_at,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (ManualSendAuthorizationError, ManualSendStateError, IdempotencyConflictError) as error:
        _raise_operations_error(error)
    return _manual_send_response(result)


@app.post(
    "/internal/v1/stores/{store_id}/incidents",
    response_model=IncidentResponse,
    status_code=status.HTTP_201_CREATED,
)
def open_incident(
    store_id: UUID,
    request: IncidentOpenRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> IncidentResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        return _incident_response(
            service.open_incident(
                store_id=store_id,
                order_id=request.order_id,
                evidence_summary=request.evidence_summary,
                idempotency_key=idempotency_key,
                principal=principal,
            )
        )
    except (StoreAccessError, ValueError, IdempotencyConflictError) as error:
        _raise_operations_error(error)


@app.get(
    "/internal/v1/stores/{store_id}/incidents",
    response_model=list[IncidentSummaryResponse],
)
def list_incidents(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
    limit: int = 100,
) -> list[IncidentSummaryResponse]:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        return [
            IncidentSummaryResponse.model_validate(item, from_attributes=True)
            for item in service.list_incidents(store_id=store_id, principal=principal, limit=limit)
        ]
    except (StoreAccessError, ValueError) as error:
        _raise_operations_error(error)


# --- REMEDY-001: DEC-004 expressed as configuration, and a surface to act on it ----------------
#
# Four routes and no arithmetic on money anywhere in them. Each forwards to the service, which
# forwards to the repository, which hands the decision to `domain.remedies` and persists the answer.
# A refusal comes back as 422 carrying the domain's own reason code plus the figure that would have
# allowed it, so the console can tell staff *why* in Vietnamese rather than only that.


@app.get(
    "/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-options",
    response_model=RemedyOptionsResponse,
)
def remedy_options(
    store_id: UUID,
    incident_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> RemedyOptionsResponse:
    """The ceilings, the windows and the owner threshold, before anybody fills a form in.

    The packet's requirement in one read: staff must never discover that the owner is required after
    typing an amount. Nothing is written and nothing is reserved by asking.
    """

    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        options = service.remedy_options(
            store_id=store_id, incident_id=incident_id, principal=principal
        )
    except (RemedyAuthorizationError, RemedyStateError, StoreAccessError, ValueError) as error:
        _raise_remedy_error(error)
    return RemedyOptionsResponse(
        incident_id=options.incident_id,
        order_id=options.order_id,
        policy_published=options.policy_published,
        staff_approval_ceiling_vnd=options.staff_approval_ceiling_vnd,
        goods_returned_at=_isoformat_or_none(options.goods_returned_at),
        rewash_window_closes_at=_isoformat_or_none(options.rewash_window_closes_at),
        rewash_window_open=options.rewash_window_open,
        defect_window_closes_at=_isoformat_or_none(options.defect_window_closes_at),
        defect_window_open=options.defect_window_open,
        damage_line_ceilings_vnd=(
            None
            if options.damage_line_ceilings_vnd is None
            else dict(options.damage_line_ceilings_vnd)
        ),
        late_delivery_credit_vnd=options.late_delivery_credit_vnd,
        late_delivery_threshold_minutes=options.late_delivery_threshold_minutes,
        loss_reason_code=options.loss_reason_code,
    )


@app.post(
    "/internal/v1/stores/{store_id}/incidents/{incident_id}/remedy-proposals",
    response_model=RemedyProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
def propose_remedy(
    store_id: UUID,
    incident_id: UUID,
    request: RemedyProposalRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> RemedyProposalResponse:
    """Propose a remedy against one incident, checked against the published `DEC-004` figures.

    A loss is a 201 whose `outcome` is `REQUIRE_HUMAN`: the complaint is recorded, which is the
    outcome the owner has decided for it, and no figure is computed anywhere.
    """

    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        result = service.propose_remedy(
            store_id=store_id,
            incident_id=incident_id,
            kind=request.kind,
            store_fault_attested=request.store_fault_attested,
            order_line_id=request.order_line_id,
            amount_vnd=request.amount_vnd,
            attested_late_by_minutes=request.attested_late_by_minutes,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (
        RemedyAuthorizationError,
        RemedyStateError,
        ApprovalEnvelopeError,
        ApprovalStateError,
        ApprovalAuthorizationError,
        IdempotencyConflictError,
        StoreAccessError,
        ValueError,
    ) as error:
        _raise_remedy_error(error)
    return RemedyProposalResponse.model_validate(result, from_attributes=True)


@app.post(
    "/internal/v1/remedy-proposals/{proposal_id}/execution",
    response_model=RemedyExecutionResponse,
    status_code=status.HTTP_201_CREATED,
)
def execute_remedy(
    proposal_id: UUID,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> RemedyExecutionResponse:
    """Carry out an authorised remedy: issue the credit, or command the rewash.

    No body. Everything this needs was decided when the proposal was recorded and is immutable on
    its row -- accepting an amount here would let the figure move between the owner approving it and
    the shop paying it, which is precisely what invariant 8 binds the rendered hash to prevent.
    """

    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        result = service.execute_remedy(
            proposal_id=proposal_id, idempotency_key=idempotency_key, principal=principal
        )
    except (
        RemedyAuthorizationError,
        RemedyStateError,
        IdempotencyConflictError,
        StoreAccessError,
        ValueError,
    ) as error:
        _raise_remedy_error(error)
    return RemedyExecutionResponse.model_validate(result, from_attributes=True)


@app.post(
    "/internal/v1/stores/{store_id}/quotes/{quote_id}/remedy-credits",
    response_model=RemedyCreditRedemptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def redeem_remedy_credit(
    store_id: UUID,
    quote_id: UUID,
    request: RemedyCreditRedemptionRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> RemedyCreditRedemptionResponse:
    """Spend one credit on the next bill, producing a revision that carries its discount.

    The settlement ledger is untouched. A credit changes what a total *is* before the customer is
    told it; `DEC-010` still keeps that path accepting only the exact quoted total, in full.
    """

    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        result = service.redeem_remedy_credit(
            store_id=store_id,
            quote_id=quote_id,
            credit_id=request.credit_id,
            expected_current_revision=request.expected_current_revision,
            expected_snapshot_hash=request.expected_snapshot_hash,
            idempotency_key=idempotency_key,
            principal=principal,
        )
    except (
        RemedyAuthorizationError,
        RemedyStateError,
        QuoteStateError,
        QuoteIntegrityError,
        IdempotencyConflictError,
        StoreAccessError,
        ValueError,
    ) as error:
        _raise_remedy_error(error)
    return RemedyCreditRedemptionResponse.model_validate(result, from_attributes=True)


@app.get("/internal/v1/queue-recovery", response_model=QueueRecoveryResponse)
def queue_recovery(
    principal: Annotated[StaffPrincipal, Depends(require_approval_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> QueueRecoveryResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    summary = service.queue_recovery_summary(principal=principal)
    return _queue_recovery_response(summary)


def _parse_if_match(value: str | None) -> int:
    """Parse a row version, bounded at both ends.

    Python integers have no width, so `int("9007199254740992")` parsed happily and travelled into
    the idempotency payload, where JCS canonicalisation follows IEEE-754, has no form for it, and
    raises -- answering a bad row version with HTTP 500 on all three order transition routes. The
    number was never valid; the only defect was where it was refused.
    """
    if value is None:
        raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED, detail="If-Match is required")
    normalized = value.strip().strip('"')
    try:
        parsed = int(normalized)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="If-Match is invalid") from error
    if not 1 <= parsed <= MAX_CANONICAL_INT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="If-Match is invalid")
    return parsed


def _raise_operations_error(error: Exception) -> NoReturn:
    if isinstance(
        error,
        (
            OrderAuthorizationError,
            ApprovalAuthorizationError,
            ManualSendAuthorizationError,
            # QUOTE-COMMAND-001 found this missing. `require_store_membership` raises
            # StoreAccessError by default, and without this arm it fell through to the 409 below —
            # so a caller could tell "you are not a member of this store" apart from "your role
            # cannot do this", which is exactly the distinction the membership check exists to
            # hide. Both are now the same opaque 403, as on the Shadow surfaces.
            StoreAccessError,
        ),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="operation denied") from error
    if isinstance(error, ApprovalEnvelopeError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    if isinstance(error, IdempotencyConflictError):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="IDEMPOTENCY_CONFLICT") from error
    raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error


def _raise_remedy_error(error: Exception) -> NoReturn:
    """Map a remedy refusal onto a status and a body the console can render in Vietnamese.

    422 rather than 409, and structured rather than a string. A refusal here is a policy answer with
    a number attached -- "quá 5 lần phí giặt của món đó, tối đa 500.000 đ", "quá 7 ngày kể từ khi
    nhận đồ" -- and a message the console has to parse would make that copy a regex. Authorization
    failures keep the same opaque 403 as every other surface, so a caller still cannot tell "not a
    member of this store" from "your role cannot do this".
    """

    if isinstance(error, (RemedyAuthorizationError, StoreAccessError)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="operation denied") from error
    if isinstance(error, RemedyStateError):
        detail: dict[str, object] = {"reason_code": error.reason_code}
        if error.authority is not None:
            detail["authority"] = error.authority
        if error.ceiling_vnd is not None:
            detail["ceiling_vnd"] = error.ceiling_vnd
        if error.window_closes_at is not None:
            detail["window_closes_at"] = error.window_closes_at.isoformat()
        if error.threshold_minutes is not None:
            detail["threshold_minutes"] = error.threshold_minutes
        if error.committed_vnd is not None:
            # What earlier proposals already committed against the same item. The ceiling alone
            # would tell staff "at most 500.000 d" about a garment that already has 300.000 d on it.
            detail["committed_vnd"] = error.committed_vnd
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail) from error
    _raise_operations_error(error)


def _isoformat_or_none(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _order_response(stored: StoredOrder) -> OrderResponse:
    return OrderResponse(
        order_id=stored.order_id,
        store_id=stored.store_id,
        commercial=stored.commercial,
        intake=stored.intake.value,
        production=stored.production.value,
        balance=stored.balance.value,
        row_version=stored.row_version,
        replayed=stored.replayed,
    )


def _order_view_response(view: OrderView) -> OrderViewResponse:
    return OrderViewResponse(
        order_id=view.order_id,
        store_id=view.store_id,
        commercial=view.commercial,
        intake=view.intake.value,
        production=view.production.value,
        balance=view.balance.value,
        row_version=view.row_version,
        # A read, so nothing was replayed. Kept so a list item and a command result share a shape.
        replayed=False,
        fulfillment_mode=view.fulfillment_mode.value,
        created_at=view.created_at,
        quote_id=view.quote_id,
        quote_revision=view.quote_revision,
        payable_total_vnd=view.payable_total_vnd,
        ticket_number=view.ticket_number,
        ticket_issued_on=view.ticket_issued_on,
    )


def _approval_response(stored: StoredApproval) -> ApprovalResponse:
    return ApprovalResponse(
        approval_request_id=stored.approval_request_id,
        status=stored.status,
        envelope_hash=stored.envelope_hash,
        required_role=stored.required_role.value,
        expires_at=stored.expires_at.isoformat(),
        replayed=stored.replayed,
        resource_type=stored.resource_type,
        resource_id=stored.resource_id,
        resource_version=stored.resource_version,
        snapshot_hash=stored.snapshot_hash,
        rendered_hash=stored.rendered_hash,
        action=stored.action,
    )


def _manual_send_response(stored: StoredManualSendResult) -> ManualSendResponse:
    return ManualSendResponse(
        manual_send_envelope_id=stored.value.manual_send_envelope_id,
        approval_request_id=stored.value.approval_request_id,
        status=stored.value.status,
        recipient_binding_id=stored.value.recipient_binding_id,
        rendered_hash=stored.value.rendered_hash,
        row_version=stored.row_version,
        replayed=stored.replayed,
    )


def _incident_response(stored: StoredIncidentResult) -> IncidentResponse:
    return IncidentResponse(
        incident_id=stored.incident_id,
        status=stored.status,
        fault_decided=stored.fault_decided,
        remedy_decided=stored.remedy_decided,
        replayed=stored.replayed,
    )


def _queue_recovery_response(summary: QueueRecoverySummary) -> QueueRecoveryResponse:
    return QueueRecoveryResponse(
        pending_internal=summary.pending_internal,
        processing_internal=summary.processing_internal,
        expired_internal=summary.expired_internal,
        dead_internal=summary.dead_internal,
        pending_agent=summary.pending_agent,
        processing_agent=summary.processing_agent,
        expired_agent=summary.expired_agent,
        failed_agent=summary.failed_agent,
    )


# --- SHADOW-CONSOLE-001: the surfaces the Shadow pilot needs ---------------------------------


class PendingDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_run_id: UUID
    conversation_binding_id: UUID
    draft_text: str
    terminal_outcome: str
    terminal_code: str
    tool_call_count: int
    produced_at: datetime


class ReviewedDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: UUID
    agent_run_id: UUID
    decision: str
    reason_code: str | None
    edited_text: str | None
    decided_by_staff_id: UUID
    decided_at: datetime
    draft_text: str
    terminal_outcome: str
    terminal_code: str
    produced_at: datetime


class DraftDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "EDIT", "REJECT"]
    reason_code: str | None = Field(default=None, max_length=64)
    edited_text: str | None = Field(default=None, max_length=4000)


class DraftDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: UUID
    agent_run_id: UUID
    decision: str
    decided_by_staff_id: UUID
    decided_at: datetime


class UnknownSendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: UUID
    outbox_id: UUID
    provider: str
    message_kind: str
    attempt_number: int
    reconciliation_state: str
    recorded_at: datetime


class ReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal["CONFIRMED_SENT", "CONFIRMED_NOT_SENT"]
    note: str | None = Field(default=None, max_length=500)


class AuditEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime
    action: str
    actor_type: str
    actor_id: UUID | None
    aggregate_type: str
    aggregate_id: UUID


@app.get(
    "/internal/v1/stores/{store_id}/shadow/reviews",
    response_model=list[ReviewedDraftResponse],
)
def list_shadow_reviews(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
    limit: int = 50,
    before: UUID | None = None,
) -> list[ReviewedDraftResponse]:
    """The decisions already made about agent drafts, newest first, with the draft each was about.

    The pending queue is undecided-only, so a draft leaves it the instant somebody rules on it.
    That is right for a queue and wrong for a record: approve, edit and reject are captured so the
    agent can be graded on them, and without this read nothing in the console looked at them again.

    Visibility is the store's rather than the reader's — a review is a decision made on the shop's
    behalf, so every Shadow read role sees all of them, `AUDITOR` included. `before` pages
    backwards from the oldest review the caller holds and is refused if it names one they may not
    read, rather than answering with the newest page.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        reviews = service.shadow_reviewed_drafts(
            store_id=store_id, principal=principal, limit=limit, before=before
        )
    except (ShadowAuthorizationError, ShadowStateError, ValueError) as error:
        _raise_shadow_error(error)
    return [
        ReviewedDraftResponse(
            review_id=item.review_id,
            agent_run_id=item.agent_run_id,
            decision=item.decision,
            reason_code=item.reason_code,
            edited_text=item.edited_text,
            decided_by_staff_id=item.decided_by_staff_id,
            decided_at=item.decided_at,
            draft_text=item.draft_text,
            terminal_outcome=item.terminal_outcome,
            terminal_code=item.terminal_code,
            produced_at=item.produced_at,
        )
        for item in reviews
    ]


@app.get("/internal/v1/stores/{store_id}/shadow/drafts", response_model=list[PendingDraftResponse])
def list_shadow_drafts(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
    limit: int = 50,
) -> list[PendingDraftResponse]:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        drafts = service.shadow_pending_drafts(store_id=store_id, principal=principal, limit=limit)
    except (ShadowAuthorizationError, ShadowStateError, ValueError) as error:
        _raise_shadow_error(error)
    return [
        PendingDraftResponse(
            agent_run_id=item.agent_run_id,
            conversation_binding_id=item.conversation_binding_id,
            draft_text=item.draft_text,
            terminal_outcome=item.terminal_outcome,
            terminal_code=item.terminal_code,
            tool_call_count=item.tool_call_count,
            produced_at=item.produced_at,
        )
        for item in drafts
    ]


@app.post(
    "/internal/v1/shadow/drafts/{agent_run_id}/decision", response_model=DraftDecisionResponse
)
def decide_shadow_draft(
    agent_run_id: UUID,
    request: DraftDecisionRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> DraftDecisionResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        decided = service.shadow_decide_draft(
            agent_run_id=agent_run_id,
            decision=request.decision,
            idempotency_key=idempotency_key,
            principal=principal,
            reason_code=request.reason_code,
            edited_text=request.edited_text,
        )
    except (ShadowAuthorizationError, ShadowStateError, ValueError) as error:
        _raise_shadow_error(error)
    except IdempotencyConflictError as error:
        _raise_operations_error(error)
    return DraftDecisionResponse(
        review_id=decided.review_id,
        agent_run_id=decided.agent_run_id,
        decision=decided.decision,
        decided_by_staff_id=decided.decided_by_staff_id,
        decided_at=decided.decided_at,
    )


@app.get("/internal/v1/shadow/unknown-sends", response_model=list[UnknownSendResponse])
def list_unknown_sends(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
    limit: int = 50,
) -> list[UnknownSendResponse]:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        items = service.shadow_unknown_sends(principal=principal, limit=limit)
    except (ShadowAuthorizationError, ShadowStateError, ValueError) as error:
        _raise_shadow_error(error)
    return [
        UnknownSendResponse(
            receipt_id=item.receipt_id,
            outbox_id=item.outbox_id,
            provider=item.provider,
            message_kind=item.message_kind,
            attempt_number=item.attempt_number,
            reconciliation_state=item.reconciliation_state,
            recorded_at=item.recorded_at,
        )
        for item in items
    ]


@app.post(
    "/internal/v1/shadow/unknown-sends/{receipt_id}/reconcile",
    status_code=status.HTTP_204_NO_CONTENT,
)
def reconcile_unknown_send(
    receipt_id: UUID,
    request: ReconcileRequest,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> None:
    """Only a human leaves UNKNOWN. There is no automatic caller for this route."""
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        service.shadow_resolve_unknown_send(
            receipt_id=receipt_id,
            resolution=ReconciliationState(request.resolution),
            principal=principal,
            note=request.note,
        )
    except (ShadowAuthorizationError, ShadowStateError, ValueError) as error:
        _raise_shadow_error(error)


@app.get(
    "/internal/v1/stores/{store_id}/shadow/audit/{aggregate_id}",
    response_model=list[AuditEntryResponse],
)
def shadow_audit_timeline(
    store_id: UUID,
    aggregate_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> list[AuditEntryResponse]:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        entries = service.shadow_audit_timeline(
            store_id=store_id, aggregate_id=aggregate_id, principal=principal
        )
    except (ShadowAuthorizationError, ShadowStateError, ValueError) as error:
        _raise_shadow_error(error)
    return [
        AuditEntryResponse(
            occurred_at=entry.occurred_at,
            action=entry.action,
            actor_type=entry.actor_type,
            actor_id=entry.actor_id,
            aggregate_type=entry.aggregate_type,
            aggregate_id=entry.aggregate_id,
        )
        for entry in entries
    ]


def _raise_shadow_error(error: Exception) -> NoReturn:
    if isinstance(error, ShadowAuthorizationError):
        # Same response for an unauthorized role and an unassigned store: probing identifiers
        # teaches a caller nothing about which stores exist.
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="shadow access denied") from error
    raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error


# --- ASSISTANT-001: the internal owner-assistant ---------------------------------------------


class AssistantTurnRequest(StrictRequest):
    question: str = Field(min_length=1, max_length=4000)


class AssistantLinkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    href: str


class AssistantTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: UUID
    intent: str
    #: Null once `ASSISTANT_TRANSCRIPT` has disposed of this turn's text at 180 days. The turn, its
    #: intent and its reason codes are ledger and are kept; only the words go.
    answer: str | None
    links: list[AssistantLinkResponse]
    reason_codes: list[str]
    created_at: datetime
    replayed: bool


class AssistantHistoryItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: UUID
    question: str | None
    intent: str
    answer: str | None
    links: list[AssistantLinkResponse]
    reason_codes: list[str]
    created_at: datetime


def get_assistant_service() -> AssistantService:
    try:
        return AssistantService(AuthSettings())
    except AssistantUnavailable as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="assistant unavailable"
        ) from error


@app.post(
    "/internal/v1/stores/{store_id}/assistant/turns",
    response_model=AssistantTurnResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_assistant_turn(
    store_id: UUID,
    request: AssistantTurnRequest,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[AssistantService | None, Depends(get_assistant_service)] = None,
) -> AssistantTurnResponse:
    """Ask the deterministic assistant one question and record the answered turn.

    The role gate is the operations gate; the repository re-checks store membership, and both
    failures surface as the one opaque 403 every other store refusal produces. The brain never
    calls a model and never computes money — it answers from governed reads or says it cannot.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="assistant unavailable")
    try:
        stored = service.post_turn(
            principal=principal,
            store_id=store_id,
            question=request.question,
            idempotency_key=idempotency_key,
            correlation_id=(current_correlation() or CorrelationContext.new()).correlation_id,
        )
    except (AssistantAuthorizationError, StoreAccessError, ValueError) as error:
        _raise_assistant_error(error)
    return AssistantTurnResponse(
        turn_id=stored.turn_id,
        intent=stored.intent,
        answer=stored.answer,
        links=[AssistantLinkResponse(label=link.label, href=link.href) for link in stored.links],
        reason_codes=list(stored.reason_codes),
        created_at=stored.created_at,
        replayed=stored.replayed,
    )


@app.get(
    "/internal/v1/stores/{store_id}/assistant/turns",
    response_model=list[AssistantHistoryItemResponse],
)
def list_assistant_turns(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[AssistantService | None, Depends(get_assistant_service)] = None,
    limit: int = 50,
    before: UUID | None = None,
) -> list[AssistantHistoryItemResponse]:
    """Newest first. An owner sees the store's turns; anyone else sees only their own.

    `before` is the oldest turn the caller already holds and pages backwards from it. The cursor is
    a turn id rather than an encoded offset: the caller already has one, and resolving it runs the
    same visibility rule as the page itself, so a cursor naming a turn this caller may not read is
    refused with the one opaque refusal every other assistant scope failure produces — never
    silently answered with the newest page, which to a reader paging backwards is indistinguishable
    from reaching the end of the history.

    A page carrying exactly `limit` items means there may be more; the console says so rather than
    ending the transcript without comment.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="assistant unavailable")
    try:
        turns = service.list_turns(
            principal=principal, store_id=store_id, limit=limit, before=before
        )
    except (AssistantAuthorizationError, StoreAccessError, ValueError) as error:
        _raise_assistant_error(error)
    return [
        AssistantHistoryItemResponse(
            turn_id=turn.turn_id,
            question=turn.question,
            intent=turn.intent,
            answer=turn.answer,
            links=[AssistantLinkResponse(label=link.label, href=link.href) for link in turn.links],
            reason_codes=list(turn.reason_codes),
            created_at=turn.created_at,
        )
        for turn in turns
    ]


@app.get("/internal/v1/stores/{store_id}/assistant/turns/{turn_id}/stream")
def stream_assistant_turn(
    store_id: UUID,
    turn_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[AssistantService | None, Depends(get_assistant_service)] = None,
) -> StreamingResponse:
    """Replay one already-persisted turn's answer as server-sent events.

    This is transport pacing of a durable, deterministic answer, not generation: the turn row is
    read back through the same scoping as the history list, and its stored answer is chunked into
    word-sized frames. A turn the caller may not see is indistinguishable from one that does not
    exist — both are the same 404.
    """
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="assistant unavailable")
    try:
        turn = service.get_turn(principal=principal, store_id=store_id, turn_id=turn_id)
    except (AssistantAuthorizationError, StoreAccessError, ValueError) as error:
        _raise_assistant_error(error)
    if turn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="assistant turn not found")
    if turn.answer is None:
        # The turn exists and the caller may see it; its words were disposed of under the published
        # schedule. A 404 would say it never happened and a stream of nothing would look like a
        # stalled connection, so this is its own refusal, phrased as the fact it is.
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="assistant turn text was disposed of under ASSISTANT_TRANSCRIPT",
        )
    return StreamingResponse(
        answer_sse_frames(turn.answer),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _raise_assistant_error(error: Exception) -> NoReturn:
    if isinstance(error, (AssistantAuthorizationError, StoreAccessError)):
        # One refusal body for a wrong role and an unassigned store, as on every other surface.
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=AUTHORIZATION_DENIED) from error
    if isinstance(error, IdempotencyConflictError):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="IDEMPOTENCY_CONFLICT") from error
    raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error


# --- OPS-BOARD-001: the shop's day, as versioned deterministic queries ------------------------
#
# Three reads and two commands, and not one of them decides anything. The SLA board is
# `ShadowConsoleRepository.sla_risk_board` -- the query that already existed, with migration
# `0037`'s clock fix inside it -- given a list surface so staff can see which order, how long is
# left and what to do first instead of the two counts `#/assistant` answers with. The day summary
# is `today_status_counts`, which had a query and no route. The export is the one genuinely absent
# capability, and it is an owner-approved, audited act rather than a button.
#
# Every response carries `query_version`: invariant 18 says a figure is produced by a versioned
# deterministic query, and a figure whose version does not travel with it cannot be traced to the
# rule that produced it once it is on a printout. No figure here is computed in this layer.


class SlaRiskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: UUID
    commercial_status: str
    production_status: str
    production_accepted_at: datetime
    production_ready_at: datetime | None
    internal_risk_due_at: datetime | None
    sla_outcome: str
    overall_outcome: str
    reason_codes: list[str]
    #: Durations, in microseconds, produced by the domain engine. Non-negative, and which side of
    #: the internal mark an order is on is carried by which one is non-zero -- never by a sign.
    #: `None` where the policy sets no mark at all, which is not the same as zero time left.
    elapsed_microseconds: int | None
    remaining_microseconds: int | None
    breach_microseconds: int


class SlaBoardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SlaRiskResponse]
    query_version: str
    policy_id: str
    policy_type: str
    policy_target_max_hours: int | None
    #: The sentence `#/assistant` already says, shared rather than copied. It names the rule that
    #: produced these numbers and states that per-order SLA policy is an unresolved business
    #: decision -- so a board cannot be read as the shop having promised a customer anything.
    policy_notice_vi: str
    evaluated_at: datetime
    next_accepted_at: datetime | None
    next_order_id: UUID | None


class DaySummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: (commercial_status, count) in the server's order. A mapping would let a client iterate it in
    #: whatever order its JSON parser felt like, and the query's ORDER BY is part of the answer.
    counts: list[tuple[str, int]]
    total_orders: int
    query_version: str
    business_timezone: str


class ExportRequestBody(StrictRequest):
    #: Named by the requester, with no default. A day nobody chose is a day nobody is accountable
    #: for having exported, and "unknown means stop" applies to a date exactly as it applies to a
    #: price.
    business_date: date


class ExportRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_request_id: UUID
    store_id: UUID
    dataset: str
    business_date: date
    #: What the caller takes to `POST /internal/v1/approvals` to raise the envelope. Handed back
    #: rather than recomputed by the client: the digests are what the owner approves, and a client
    #: that derived its own would be approving a document the server never stored.
    resource_type: str
    resource_version: int
    snapshot_hash: str
    rendered_hash: str
    policy_version: str
    requested_at: datetime
    columns: list[str]
    excludes: list[str]
    query_version: str
    #: Which event puts an order on the named day, and the Vietnamese sentence the owner approves.
    #: Both come from the server because both are hashed into `rendered_hash`: a console composing
    #: its own wording would be describing a document nobody signed. `day_boundary` is on the wire
    #: because this system cuts the shop's day two ways -- an export by `orders.created_at`, the
    #: counter's takings by `order_settlements.attested_at` -- and both surfaces say *tiền đã thu*.
    day_boundary: str
    statement_vi: str
    replayed: bool


class ExportRequestContentResponse(BaseModel):
    """What one `EXPORT_SANITIZED_DATA` envelope authorises, for the owner being asked to sign it.

    The same shape and the same purpose as `RangePriceProposalContentResponse`: an approver must be
    able to read what they are releasing before the approve control is reachable. `#/approvals`
    could not put an export in front of anybody -- `EXPORT_REQUEST` was absent from the console's
    viewable-resource table -- so the envelope `#/exports` raises could never be decided, and a
    capability that a staff member can request and no owner can release is not a capability.

    `requested_by_you` is the one field that is about the reader rather than the content. It is the
    separation-of-duty answer computed where the console can act on it: the account that defined
    this export cannot approve it, and learning that from a disabled control with a sentence beside
    it is better than learning it from a 403 after pressing. It discloses nothing about anyone else
    -- it is true only of the caller, and is `false` for every other account whatever raised it.
    """

    model_config = ConfigDict(extra="forbid")

    approval_request_id: UUID
    export_request_id: UUID
    dataset: str
    business_date: date
    business_timezone: str
    day_boundary: str
    columns: list[str]
    excludes: list[str]
    query_version: str
    statement_vi: str
    #: The envelope's own digest, so the console can refuse to show content that belongs to a
    #: different rendering than the queue row the decision will be built from.
    rendered_hash: str
    requested_at: datetime
    requested_by_you: bool


class ExportExecutionBody(StrictRequest):
    approval_id: UUID


class ExportExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_id: UUID
    export_request_id: UUID
    store_id: UUID
    approval_request_id: UUID
    business_date: date
    row_count: int
    content_hash: str
    query_version: str
    content_csv: str
    produced_at: datetime


def get_ops_board_service() -> OpsBoardService:
    try:
        return OpsBoardService(AuthSettings())
    except OpsBoardUnavailable as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations board unavailable"
        ) from error


@app.get("/internal/v1/stores/{store_id}/sla-board", response_model=SlaBoardResponse)
def sla_board(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
    service: Annotated[OpsBoardService | None, Depends(get_ops_board_service)] = None,
    limit: int = SLA_BOARD_DEFAULT_LIMIT,
    after_accepted_at: datetime | None = None,
    after_order_id: UUID | None = None,
) -> SlaBoardResponse:
    """In-production orders against the one stated internal mark, oldest accepted first.

    The route gate is the session alone and the real gate is the repository, exactly as on the two
    Shadow list routes: `SHADOW_READ_ROLES` plus an explicit `staff_store_assignments` row. Writing
    a second role set here would be a second opinion about who may read a store, and the one in the
    repository is the one that cannot be forgotten.

    Ordering is the query's, not this layer's, and it is acceptance order rather than urgency
    order: an order whose clock stopped at `production_ready_at` has frozen time remaining and can
    sort above one about to breach, so `sla_risk_board` declines to claim a ranking its index can
    serve. Each row carries its own outcome and its own remaining and breach figures for that
    reason. Re-sorting the page here would rank one page against itself and break the keyset.
    """
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations board unavailable"
        )
    try:
        page = service.sla_board(
            store_id=store_id,
            principal=principal,
            policy=SLA_POLICY,
            limit=limit,
            after_accepted_at=after_accepted_at,
            after_order_id=after_order_id,
        )
    except (ShadowAuthorizationError, ShadowStateError, ValueError) as error:
        _raise_shadow_error(error)
    return SlaBoardResponse(
        items=[
            SlaRiskResponse(
                order_id=item.order_id,
                commercial_status=item.commercial_status,
                production_status=item.production_status,
                production_accepted_at=item.production_accepted_at,
                production_ready_at=item.production_ready_at,
                internal_risk_due_at=item.internal_risk_due_at,
                sla_outcome=item.sla_outcome,
                overall_outcome=item.overall_outcome,
                reason_codes=list(item.reason_codes),
                elapsed_microseconds=item.elapsed_microseconds,
                remaining_microseconds=item.remaining_microseconds,
                breach_microseconds=item.breach_microseconds,
            )
            for item in page.items
        ],
        query_version=page.query_version,
        policy_id=page.policy_id,
        policy_type=page.policy_type,
        policy_target_max_hours=page.policy_target_max_hours,
        policy_notice_vi=sla_policy_notice_vi(SLA_POLICY),
        evaluated_at=page.evaluated_at,
        next_accepted_at=page.next_accepted_at,
        next_order_id=page.next_order_id,
    )


@app.get("/internal/v1/stores/{store_id}/day-summary", response_model=DaySummaryResponse)
def day_summary(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OpsBoardService | None, Depends(get_ops_board_service)] = None,
) -> DaySummaryResponse:
    """The store's own day, counted by commercial status. No money is served here.

    The day's takings keep their own route and their own `DEC-014` gate
    (`GET /internal/v1/stores/{store_id}/settlements/today`). Folding that figure in here would put
    it behind a second gate, and a role gate with two doors is a role gate that gets widened by
    whichever door somebody edits next.

    `total_orders` is summed by the server for the same reason every other figure is: a console that
    added the rows would be a second, unreviewed opinion about the day's volume.
    """
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations board unavailable"
        )
    try:
        summary = service.day_summary(store_id=store_id, principal=principal)
    except (AssistantAuthorizationError, StoreAccessError, ValueError) as error:
        _raise_assistant_error(error)
    return DaySummaryResponse(
        counts=[(status_name, count) for status_name, count in summary.counts],
        total_orders=sum(count for _, count in summary.counts),
        query_version=summary.query_version,
        business_timezone=summary.business_timezone,
    )


@app.post(
    "/internal/v1/stores/{store_id}/exports",
    response_model=ExportRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_export(
    store_id: UUID,
    request: ExportRequestBody,
    idempotency_key: IdempotencyKey,
    principal: Annotated[StaffPrincipal, Depends(require_approval_staff)],
    service: Annotated[OpsBoardService | None, Depends(get_ops_board_service)] = None,
) -> ExportRequestResponse:
    """Record what somebody wants exported and hand back the binding an approval envelope needs.

    Nothing leaves the system on this call and no approval is raised on it. The digests returned are
    what `POST /internal/v1/approvals` binds, and `_require_resolvable_resource` now resolves an
    `EXPORT_REQUEST` against the row this wrote -- so an envelope naming an export that does not
    exist, or one belonging to another shop, is refused before anybody can approve it.
    """
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations board unavailable"
        )
    try:
        stored = service.request_export(
            store_id=store_id,
            principal=principal,
            business_date=request.business_date,
            idempotency_key=idempotency_key,
        )
    except ExportAuthorizationError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=AUTHORIZATION_DENIED) from error
    except IdempotencyConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="IDEMPOTENCY_CONFLICT") from error
    except (StoreAccessError, ExportStateError, ValueError) as error:
        _raise_export_error(error)
    return ExportRequestResponse(
        export_request_id=stored.export_request_id,
        store_id=stored.store_id,
        dataset=stored.dataset,
        business_date=stored.business_date,
        resource_type=stored.resource_type,
        resource_version=stored.resource_version,
        snapshot_hash=stored.snapshot_hash,
        rendered_hash=stored.rendered_hash,
        policy_version=stored.policy_version,
        requested_at=stored.requested_at,
        columns=list(stored.columns),
        excludes=list(stored.excludes),
        query_version=stored.query_version,
        day_boundary=stored.day_boundary,
        statement_vi=stored.statement_vi,
        replayed=stored.replayed,
    )


@app.get(
    "/internal/v1/approvals/{approval_id}/export-request",
    response_model=ExportRequestContentResponse,
)
def read_export_request_for_approval(
    approval_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(require_approval_staff)],
    service: Annotated[OpsBoardService | None, Depends(get_ops_board_service)] = None,
) -> ExportRequestContentResponse:
    """What one `EXPORT_SANITIZED_DATA` envelope releases, for the person being asked to sign it.

    The export half of `RANGE-APPROVAL-VISIBILITY-001`. `#/approvals` had no way to render an
    `EXPORT_REQUEST`, so the envelope `#/exports` raises could never be decided from the approvals
    queue: a staff member could ask for an export that no owner was able to release. Adding the
    resource type to the console's viewable table alone would have fixed the dead end by letting an
    owner approve a digest and a UUID, which is the blind approval that item existed to remove.
    This route is what makes it not blind -- the business date, the exact columns, the stated
    exclusions and the day boundary, re-derived from the column list in force right now.

    Keyed by the approval rather than by the store, exactly as the range-price read is: the store
    comes off the stored request row and membership is required against it, so an approval from
    another shop is refused with the same opaque 403 as any other non-membership.

    404 covers "no such approval" and "this approval is not an export" alike. A caller learns
    nothing from the difference, and both fail closed in the direction the console needs: no
    content, therefore no approve control.
    """
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations board unavailable"
        )
    try:
        record = service.read_export_for_approval(approval_id=approval_id, principal=principal)
    except (ExportAuthorizationError, StoreAccessError, ExportStateError, ValueError) as error:
        _raise_export_error(error)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no export request for this approval")
    return ExportRequestContentResponse(
        approval_request_id=record.approval_request_id,
        export_request_id=record.export_request_id,
        dataset=record.dataset,
        business_date=record.business_date,
        business_timezone=record.business_timezone,
        day_boundary=record.day_boundary,
        columns=list(record.columns),
        excludes=list(record.excludes),
        query_version=record.query_version,
        statement_vi=record.statement_vi,
        rendered_hash=record.rendered_hash,
        requested_at=record.requested_at,
        requested_by_you=record.requested_by_you,
    )


@app.post(
    "/internal/v1/stores/{store_id}/exports/{export_request_id}/execution",
    response_model=ExportExecutionResponse,
)
def execute_export(
    store_id: UUID,
    export_request_id: UUID,
    request: ExportExecutionBody,
    principal: Annotated[StaffPrincipal, Depends(require_approval_staff)],
    service: Annotated[OpsBoardService | None, Depends(get_ops_board_service)] = None,
) -> ExportExecutionResponse:
    """Release the file once, against an approval that names this exact request.

    **This route deliberately does not honour `Idempotency-Key`, and that is the safer choice
    rather than an omission.** `IdempotencyRepository` persists a command's response verbatim into
    `command_idempotency_records.response`, where `protect_idempotency_record` forbids anyone to
    delete it -- so replaying an export through it would store the exported bytes inside the system
    for ever, which is precisely the escaped, ungoverned second copy the export's own sanitisation
    rule exists to prevent. The one-time property comes from `data_exports.export_request_id UNIQUE`
    instead: an approved request releases one file, and a second attempt is refused by name.

    `store_id` is in the path for the console's sake and is not trusted: the repository reads the
    store off the locked `export_requests` row and checks membership against that. The path value
    is passed down as an assertion only -- a URL naming a different shop is refused before the file
    is produced, so a mislabelled request cannot burn the request's one release.
    """
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations board unavailable"
        )
    try:
        produced = service.execute_export(
            store_id=store_id,
            export_request_id=export_request_id,
            approval_request_id=request.approval_id,
            principal=principal,
        )
    except ExportAuthorizationError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=AUTHORIZATION_DENIED) from error
    except (StoreAccessError, ExportStateError, ValueError) as error:
        _raise_export_error(error)
    return ExportExecutionResponse(
        export_id=produced.export_id,
        export_request_id=produced.export_request_id,
        store_id=produced.store_id,
        approval_request_id=produced.approval_request_id,
        business_date=produced.business_date,
        row_count=produced.row_count,
        content_hash=produced.content_hash,
        query_version=produced.query_version,
        content_csv=produced.content_csv,
        produced_at=produced.produced_at,
    )


#: Export refusals that a person can resolve by deciding something, rather than by retyping.
#:
#: These three are the owner\'s approval being absent, expired, or bound to a different document.
#: All three are answered `REQUIRE_HUMAN`, which is `core/errors.js`\'s classification for "somebody
#: has to decide this" -- not `NOT_SUPPORTED`, which means the shop has never decided the case, and
#: not `INVALID`, which would send a staff member back to retype a date that was never wrong.
#: `EXPORT_APPROVAL_SELF_DECIDED` joined them when separation of duty was bound to the person who
#: defined the export rather than to whoever raised the envelope. It is the same kind of answer as
#: the other three -- a different owner has to decide this -- and never a retype.
_EXPORT_REQUIRES_HUMAN = frozenset(
    {
        "EXPORT_APPROVAL_REQUIRED",
        "EXPORT_APPROVAL_EXPIRED",
        "EXPORT_APPROVAL_NOT_BOUND",
        "EXPORT_APPROVAL_SELF_DECIDED",
    }
)


def _raise_export_error(error: Exception) -> NoReturn:
    """One refusal shape for the export surface, keeping the reason code the caller can act on."""
    if isinstance(error, (ExportAuthorizationError, StoreAccessError)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=AUTHORIZATION_DENIED) from error
    if isinstance(error, ExportStateError):
        if error.reason_code in _EXPORT_REQUIRES_HUMAN:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"outcome": "REQUIRE_HUMAN", "reason_code": error.reason_code},
            ) from error
        # The rest are state, not policy: this request has already released its one file, or there
        # is no such request. 409 with the code as the detail, which is the shape `classify` turns
        # into a conflict the screen glosses from `REASON_NOTE`.
        raise HTTPException(status.HTTP_409_CONFLICT, detail=error.reason_code) from error
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


if WEB_DIRECTORY.is_dir():
    app.mount("/staff", StaticFiles(directory=WEB_DIRECTORY, html=True), name="staff-pwa")
