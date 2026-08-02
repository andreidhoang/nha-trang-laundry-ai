"""Staff-only API entry point. Public customer endpoints are intentionally absent."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.staticfiles import StaticFiles
from nha_trang_laundry_db.approvals import (
    ApprovalAuthorizationError,
    ApprovalDecision,
    ApprovalStateError,
    StoredApproval,
)
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import IdentityStateError, StaffPrincipal, StaffRole
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
    correlation_scope,
)
from pydantic import BaseModel, ConfigDict, Field

from nha_trang_laundry_api.auth import (
    AuthenticationError,
    AuthenticationUnavailable,
    AuthSettings,
    StaffIdentityService,
)
from nha_trang_laundry_api.operations import OperationsService, OperationsUnavailable

app = FastAPI(
    title="Nha Trang Laundry AI Control Plane",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
_LOGGER = SafeStructuredLogger()
WEB_DIRECTORY = Path(__file__).resolve().parents[3] / "web"


@app.middleware("http")
async def correlation_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    context = CorrelationContext.from_http_header(request.headers.get(CORRELATION_HEADER))
    with correlation_scope(context):
        response = await call_next(request)
    response.headers[CORRELATION_HEADER] = context.header_value
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
    resource_version: int = Field(ge=1)
    snapshot_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")
    rendered_hash: str = Field(pattern=r"^JCS-SHA256-V1:[0-9a-f]{64}$")


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
    session_token: Annotated[str | None, Cookie(alias="staff_session")] = None,
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> StaffPrincipal:
    if not session_token or service is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="staff session required")
    try:
        return service.current_principal(session_token)
    except IdentityStateError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid staff session") from error


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Return only process health; dependency health is added with real infrastructure."""
    return {"status": "ok"}


@app.post("/internal/v1/auth/session", response_model=SessionResponse, include_in_schema=False)
def exchange_identity_token(
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> SessionResponse:
    if service is None or authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="identity token required")
    try:
        token, expires_at = service.exchange(authorization.removeprefix("Bearer "))
        principal = service.current_principal(token)
    except (AuthenticationError, IdentityStateError) as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="identity exchange rejected"
        ) from error
    response.set_cookie(
        key=AuthSettings().staff_session_cookie_name,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        expires=expires_at,
        path="/",
    )
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
    response: Response,
    session_token: Annotated[str | None, Cookie(alias="staff_session")] = None,
    principal: Annotated[StaffPrincipal | None, Depends(current_principal)] = None,
    service: Annotated[StaffIdentityService | None, Depends(get_identity_service)] = None,
) -> None:
    if session_token is not None and principal is not None and service is not None:
        service.logout(session_token, principal)
    response.delete_cookie(AuthSettings().staff_session_cookie_name, path="/")


def _session_response(principal: StaffPrincipal) -> SessionResponse:
    return SessionResponse(
        staff_user_id=str(principal.staff_user_id),
        roles=sorted(role.value for role in principal.roles),
        mfa_verified=principal.mfa_verified,
    )


def require_owner(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
) -> StaffPrincipal:
    if StaffRole.OWNER_ADMIN not in principal.roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="owner role required")
    return principal


def require_operations_staff(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
) -> StaffPrincipal:
    allowed = {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
    if not principal.roles & allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="operations role required")
    return principal


def require_approval_staff(
    principal: Annotated[StaffPrincipal, Depends(current_principal)],
) -> StaffPrincipal:
    allowed = {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER}
    if not principal.roles & allowed or not principal.mfa_verified:
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
            principal=principal,
        )
    except (ApprovalAuthorizationError, ApprovalStateError) as error:
        _raise_operations_error(error)
    return _approval_response(stored)


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
    if isinstance(error, (OrderAuthorizationError, ApprovalAuthorizationError)):
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


if WEB_DIRECTORY.is_dir():
    app.mount("/staff", StaticFiles(directory=WEB_DIRECTORY, html=True), name="staff-pwa")
