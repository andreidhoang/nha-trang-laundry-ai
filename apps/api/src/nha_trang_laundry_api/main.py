"""Staff-only API entry point. Public customer endpoints are intentionally absent."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from time import perf_counter
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.staticfiles import StaticFiles
from nha_trang_laundry_db.approvals import (
    ApprovalAuthorizationError,
    ApprovalDecision,
    ApprovalStateError,
    StoredApproval,
)
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import IdentityStateError, StaffPrincipal, StaffRole
from nha_trang_laundry_db.manual_sends import (
    ManualSendAuthorizationError,
    ManualSendStateError,
)
from nha_trang_laundry_db.orders import (
    OrderAuthorizationError,
    OrderStateError,
    StoredOrder,
)
from nha_trang_laundry_domain.approvals import ApprovalEnvelopeError
from nha_trang_laundry_domain.catalog import (
    ApprovalAction,
    CommercialOrderStatus,
    FulfillmentMode,
)
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
    StoredIncidentResult,
    StoredManualSendResult,
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


class QuoteSummaryResponse(BaseModel):
    quote_id: UUID
    revision: int
    row_version: int
    finality: str
    status: str
    snapshot_hash: str
    display_total_min_vnd: int
    display_total_max_vnd: int
    valid_until: datetime


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


def require_owner(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
) -> StaffPrincipal:
    if StaffRole.OWNER_ADMIN not in principal.roles:
        _record_authorization_denial("OWNER_ROLE_REQUIRED")
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner role required")
    return principal


def require_operations_staff(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
) -> StaffPrincipal:
    allowed = {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
    if not principal.roles & allowed or not principal.mfa_verified:
        _record_authorization_denial("OPERATIONS_ROLE_OR_MFA_REQUIRED")
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="operations role with MFA required")
    return principal


def require_approval_staff(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
) -> StaffPrincipal:
    allowed = {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER}
    if not principal.roles & allowed or not principal.mfa_verified:
        _record_authorization_denial("APPROVAL_ROLE_OR_MFA_REQUIRED")
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="approval role with MFA required")
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
    except ValueError as error:
        _raise_operations_error(error)
    return _order_response(stored)


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
    del principal
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="operations unavailable")
    try:
        return [_approval_response(item) for item in service.list_pending_approvals(limit=limit)]
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
    except ValueError as error:
        _raise_operations_error(error)


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
    except (ValueError, IdempotencyConflictError) as error:
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
    except ValueError as error:
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
        (OrderAuthorizationError, ApprovalAuthorizationError, ManualSendAuthorizationError),
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


if WEB_DIRECTORY.is_dir():
    app.mount("/staff", StaticFiles(directory=WEB_DIRECTORY, html=True), name="staff-pwa")
