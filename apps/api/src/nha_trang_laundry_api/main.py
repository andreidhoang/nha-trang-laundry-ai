"""Staff-only API entry point. Public customer endpoints are intentionally absent."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from time import perf_counter
from typing import Annotated, Literal, NoReturn
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
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
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import IdentityStateError, StaffPrincipal, StaffRole
from nha_trang_laundry_db.intake import OrderRequestSummary
from nha_trang_laundry_db.manual_sends import (
    ManualSendAuthorizationError,
    ManualSendStateError,
)
from nha_trang_laundry_db.orders import (
    OrderAuthorizationError,
    OrderStateError,
    StoredOrder,
)
from nha_trang_laundry_db.quotes import QuoteIntegrityError, QuoteStateError
from nha_trang_laundry_db.settlement import (
    BUSINESS_TIMEZONE,
    SettlementAuthorizationError,
    SettlementStateError,
)
from nha_trang_laundry_db.shadow_console import ShadowAuthorizationError, ShadowStateError
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_domain.approvals import ApprovalEnvelopeError
from nha_trang_laundry_domain.catalog import (
    ApprovalAction,
    CommercialOrderStatus,
    FulfillmentMode,
    QuantityBasis,
    Unit,
)
from nha_trang_laundry_domain.quote_composition import RequestedLine
from nha_trang_laundry_observability import (
    CORRELATION_HEADER,
    CorrelationContext,
    SafeStructuredLogger,
    Telemetry,
    correlation_scope,
    current_correlation,
)
from opentelemetry import metrics, trace
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import StreamingResponse

from nha_trang_laundry_api.assistant import (
    AssistantService,
    AssistantUnavailable,
    answer_sse_frames,
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
    StoredIncidentResult,
    StoredManualSendResult,
    UnresolvedQuoteResult,
)
from nha_trang_laundry_api.security import BrowserSecurityMiddleware, RequestSizeLimitMiddleware

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
    model_config = ConfigDict(extra="forbid")


class OrderCreateRequest(StrictRequest):
    bound_contact_id: UUID
    quote_id: UUID
    quote_revision: int = Field(ge=1)
    quote_snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    fulfillment_mode: FulfillmentMode
    customer_final_quote_accepted_at: datetime


class CommercialTransitionRequest(StrictRequest):
    target: CommercialOrderStatus


class ApprovalRequest(StrictRequest):
    action: ApprovalAction
    resource_type: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,127}$")
    resource_id: UUID
    resource_version: int = Field(ge=1)
    snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    rendered_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1, max_length=200)


class ApprovalDecisionRequest(StrictRequest):
    decision: ApprovalDecision
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,99}$")
    note: str | None = Field(default=None, min_length=1, max_length=500)
    resource_version: int = Field(ge=1)
    snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    rendered_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")


class ManualSendPrepareRequest(StrictRequest):
    observed_resource_version: int = Field(ge=1)
    observed_snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    observed_rendered_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    recipient_binding_id: UUID
    channel: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,49}$")


class ManualSendAttestationRequest(StrictRequest):
    observed_resource_version: int = Field(ge=1)
    exact_rendered_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    sent_at: datetime


class IncidentOpenRequest(StrictRequest):
    order_id: UUID
    contact_scope_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evidence_summary_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class MemberStoresResponse(BaseModel):
    store_ids: list[UUID]


class OrderResponse(BaseModel):
    order_id: UUID
    store_id: UUID
    commercial: CommercialOrderStatus
    intake: str
    production: str
    balance: str
    row_version: int
    replayed: bool


class ApprovalResponse(BaseModel):
    approval_request_id: UUID
    status: str
    envelope_hash: str
    required_role: str
    expires_at: str
    replayed: bool


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
    paid_amount_vnd: int = Field(ge=0)
    # Explicit rather than defaulted. "The customer took their goods" is the fact being attested,
    # and a default true would let a staff member attest to it by not mentioning it.
    collected_by_customer: bool


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
    verified_distance_m: int | None = Field(default=None, ge=0, le=100_000)
    planned_transport_weight_kg: str | None = Field(default=None, max_length=32)
    approved_manual_fee_vnd: int | None = Field(default=None, ge=0)
    customer_acknowledged_manual_fee: bool = False
    # Absent means "open a new quote". Present means "add a revision to this one", and then
    # expected_current_revision plus If-Match carry the compare-and-swap: a correction is always a
    # new revision, never an update to an existing one.
    quote_id: UUID | None = None
    expected_current_revision: int = Field(default=0, ge=0)


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


@app.post(
    "/internal/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False
)
def logout(
    request: Request,
    response: Response,
    principal: Annotated[StaffPrincipal | None, Depends(current_principal)] = None,
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> None:
    session_token = request.cookies.get(_AUTH_SETTINGS.staff_session_cookie_name)
    if session_token is not None and principal is not None and service is not None:
        service.logout(session_token, principal)
    response.delete_cookie(_AUTH_SETTINGS.staff_session_cookie_name, path="/")
    response.delete_cookie(_AUTH_SETTINGS.staff_csrf_cookie_name, path="/")


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
AUTHORIZATION_DENIED = "operation denied"


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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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
    return MemberStoresResponse(store_ids=list(service.list_member_stores(principal=principal)))


@app.get("/internal/v1/stores/{store_id}/orders", response_model=list[OrderResponse])
def list_orders(
    store_id: UUID,
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
    limit: int = 100,
) -> list[OrderResponse]:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        return [
            _order_response(item)
            for item in service.list_orders(store_id=store_id, principal=principal, limit=limit)
        ]
    except (OrderAuthorizationError, ValueError) as error:
        _raise_operations_error(error)


@app.post("/internal/v1/orders/{order_id}/transition", response_model=OrderResponse)
def transition_order(
    order_id: UUID,
    request: CommercialTransitionRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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
    except SettlementAuthorizationError as error:
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> ApprovalResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        stored = service.request_approval(
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
    except (ApprovalEnvelopeError, ApprovalStateError, IdempotencyConflictError) as error:
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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


@app.post(
    "/internal/v1/stores/{store_id}/quotes",
    response_model=QuoteRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_quote(
    store_id: UUID,
    request: QuoteCreateRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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
        replayed=result.replayed,
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
# delivery collections, refunds and B2B accounts — all of which are DEC-010, and none of which this
# route pretends to have settled.


class CollectedTodayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collected_vnd: int
    settlement_count: int
    business_timezone: str


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
        business_timezone=BUSINESS_TIMEZONE,
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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
                contact_scope_hash=request.contact_scope_hash,
                evidence_summary_hash=request.evidence_summary_hash,
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
    if value is None:
        raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED, detail="If-Match is required")
    normalized = value.strip().strip('"')
    try:
        parsed = int(normalized)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="If-Match is invalid") from error
    if parsed < 1:
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


def _approval_response(stored: StoredApproval) -> ApprovalResponse:
    return ApprovalResponse(
        approval_request_id=stored.approval_request_id,
        status=stored.status,
        envelope_hash=stored.envelope_hash,
        required_role=stored.required_role.value,
        expires_at=stored.expires_at.isoformat(),
        replayed=stored.replayed,
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
    principal: Annotated[StaffPrincipal, Depends(require_operations_staff)],
    service: Annotated[OperationsService | None, Depends(get_operations_service)] = None,
) -> DraftDecisionResponse:
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        decided = service.shadow_decide_draft(
            agent_run_id=agent_run_id,
            decision=request.decision,
            principal=principal,
            reason_code=request.reason_code,
            edited_text=request.edited_text,
        )
    except (ShadowAuthorizationError, ShadowStateError, ValueError) as error:
        _raise_shadow_error(error)
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
    answer: str
    links: list[AssistantLinkResponse]
    reason_codes: list[str]
    created_at: datetime
    replayed: bool


class AssistantHistoryItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: UUID
    question: str
    intent: str
    answer: str
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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
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


if WEB_DIRECTORY.is_dir():
    app.mount("/staff", StaticFiles(directory=WEB_DIRECTORY, html=True), name="staff-pwa")
