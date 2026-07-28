"""Loopback-only fixed-route HTTP adapter for one authenticated public-cell run."""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from nha_trang_laundry_contracts import AgentToolOperation

from .agent_runner import TOOL_REGISTRY as _TOOL_REGISTRY
from .agent_runner import AgentToolBridgeRejected, AgentToolBridgeResult, AgentToolBridgeSession


class AgentBridgeSessionStore:
    """Holds at most one executor-local bridge session for a single constrained run."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._session: AgentToolBridgeSession | None = None

    def install(self, session: AgentToolBridgeSession) -> None:
        with self._lock:
            if self._session is not None:
                raise RuntimeError("active public-cell bridge session already exists")
            self._session = session

    def clear(self, binding_id: UUID) -> None:
        with self._lock:
            if self._session is not None and self._session.binding_id == binding_id:
                self._session = None

    def get(self, binding_id: UUID) -> AgentToolBridgeSession:
        with self._lock:
            if self._session is None:
                raise AgentToolBridgeRejected("TOOL_UNAVAILABLE: no active bridge session")
            if self._session.binding_id != binding_id:
                raise AgentToolBridgeRejected("POLICY_DENIED: wrong run binding")
            return self._session


def create_agent_bridge_app(store: AgentBridgeSessionStore) -> FastAPI:
    """Build exactly the OpenAPI tool paths; no generic tool or outbound-send route exists."""

    app = FastAPI(
        title="Nha Trang Laundry Agent Runner Bridge",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    router = APIRouter(include_in_schema=False)
    for operation, contract in _TOOL_REGISTRY.operations.items():
        router.add_api_route(
            contract.path,
            _fixed_endpoint(store, operation),
            methods=[contract.method],
            status_code=contract.success_status,
            name=f"bridge-{operation.value}",
        )
    app.include_router(router)
    return app


def _fixed_endpoint(store: AgentBridgeSessionStore, operation: AgentToolOperation) -> Any:
    async def endpoint(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        binding_header: Annotated[str | None, Header(alias="X-Agent-Run-Binding")] = None,
    ) -> JSONResponse:
        bridge_token = _bearer_token(authorization)
        binding_id = _binding_id(binding_header)
        contract = _TOOL_REGISTRY.get(operation)
        try:
            if contract.method == "GET":
                arguments: Mapping[str, Any] = {}
            else:
                raw = await request.json()
                if not isinstance(raw, dict):
                    raise AgentToolBridgeRejected("VALIDATION_ERROR: JSON object required")
                arguments = raw
            result = store.get(binding_id).invoke(
                operation,
                arguments,
                bridge_token=bridge_token,
                binding_id=binding_id,
                route_path_parameters={
                    key: str(value) for key, value in request.path_params.items()
                },
                idempotency_key=request.headers.get("Idempotency-Key"),
            )
            return _result_response(result)
        except AgentToolBridgeRejected as error:
            return _error_response(error)

    return endpoint


def _bearer_token(authorization: str | None) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bridge bearer required")
    token = authorization.removeprefix("Bearer ").strip()
    if len(token) < 32:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bridge bearer rejected")
    return token


def _binding_id(value: str | None) -> UUID:
    if value is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "agent run binding required")
    try:
        return UUID(value)
    except ValueError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "agent run binding rejected") from error


def _result_response(result: AgentToolBridgeResult) -> JSONResponse:
    response = JSONResponse(status_code=result.status_code, content=dict(result.body))
    etag = result.response_headers.get("ETag") or result.response_headers.get("etag")
    if etag is not None:
        response.headers["ETag"] = etag
    return response


def _error_response(error: AgentToolBridgeRejected) -> JSONResponse:
    raw = str(error)
    code, _, message = raw.partition(":")
    normalized_code = {
        "REQUIRE_HUMAN": "HUMAN_APPROVAL_REQUIRED",
        "MISSING_REQUIRED_FACT": "MISSING_REQUIRED_FACT",
        "STALE_VERSION": "STALE_VERSION",
        "IDEMPOTENCY_CONFLICT": "IDEMPOTENCY_CONFLICT",
        "VALIDATION_ERROR": "VALIDATION_ERROR",
        "POLICY_DENIED": "POLICY_DENIED",
        "TOOL_UNAVAILABLE": "TOOL_UNAVAILABLE",
    }.get(code, "TOOL_UNAVAILABLE")
    status_code = {
        "VALIDATION_ERROR": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "POLICY_DENIED": status.HTTP_403_FORBIDDEN,
        "STALE_VERSION": status.HTTP_409_CONFLICT,
        "IDEMPOTENCY_CONFLICT": status.HTTP_409_CONFLICT,
        "HUMAN_APPROVAL_REQUIRED": status.HTTP_409_CONFLICT,
    }.get(normalized_code, status.HTTP_503_SERVICE_UNAVAILABLE)
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "trace_id": "tr_bridge_unavailable",
            "error": {
                "code": normalized_code,
                "message": (message or raw)[:300],
                "reason_codes": [normalized_code],
                "field_errors": [],
            },
        },
    )


__all__ = ["AgentBridgeSessionStore", "create_agent_bridge_app"]
