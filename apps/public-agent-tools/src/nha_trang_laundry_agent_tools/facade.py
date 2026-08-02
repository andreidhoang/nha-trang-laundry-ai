"""Fixed-route private Agent Tool Facade generated from the normative OpenAPI registry."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Protocol
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from nha_trang_laundry_contracts import (
    AgentRunnerClaims,
    AgentToolOperation,
    ToolArgumentsInvalid,
    load_agent_tool_registry,
)
from nha_trang_laundry_observability import current_correlation

from nha_trang_laundry_agent_tools.auth import (
    AgentAuthenticationError,
    AgentAuthenticationUnavailable,
    AgentAuthorizationError,
    AgentAuthSettings,
    AgentRunnerTokenVerifier,
    authorize_operation,
)

ROOT = Path(__file__).resolve().parents[4]
TOOL_REGISTRY = load_agent_tool_registry(ROOT / "specs/contracts/agent-tools-v1.openapi.yaml")
router = APIRouter(include_in_schema=False)


class AgentToolUnavailable(RuntimeError):
    """No production domain adapter is registered for a valid tool invocation."""


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    operation: AgentToolOperation
    arguments: Mapping[str, Any]
    path_parameters: Mapping[str, str]
    idempotency_key: str | None
    if_match: str | None
    claims: AgentRunnerClaims
    trace_id: str


class AgentToolBackend(Protocol):
    def invoke(self, call: AgentToolCall) -> Mapping[str, Any]: ...


class UnavailableAgentToolBackend:
    def invoke(self, call: AgentToolCall) -> Mapping[str, Any]:
        del call
        raise AgentToolUnavailable("deterministic Tool Facade adapter is unavailable")


class AgentFacadeService:
    def __init__(self, backend: AgentToolBackend) -> None:
        self._backend = backend

    def invoke(
        self,
        *,
        operation: AgentToolOperation,
        arguments: Mapping[str, Any],
        path_parameters: Mapping[str, str],
        idempotency_key: str | None,
        if_match: str | None,
        claims: AgentRunnerClaims,
        trace_id: str,
    ) -> tuple[int, dict[str, Any]]:
        contract = TOOL_REGISTRY.get(operation)
        authorize_operation(claims, operation)
        _authorize_bound_path(claims, path_parameters)
        _validate_required_headers(contract.header_parameters, idempotency_key, if_match)
        validated = contract.validate_model_arguments(arguments)
        response = self._backend.invoke(
            AgentToolCall(
                operation,
                validated,
                dict(path_parameters),
                idempotency_key,
                if_match,
                claims,
                trace_id,
            )
        )
        try:
            validated_response = contract.validate_success_response(response)
        except ToolArgumentsInvalid as error:
            raise AgentToolUnavailable(
                "deterministic adapter returned an invalid response"
            ) from error
        return contract.success_status, validated_response


def get_agent_verifier() -> AgentRunnerTokenVerifier:
    try:
        return AgentRunnerTokenVerifier(AgentAuthSettings())
    except AgentAuthenticationUnavailable as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="agent authentication unavailable"
        ) from error


def current_agent_claims(
    authorization: Annotated[str | None, Header()] = None,
    verifier: Annotated[AgentRunnerTokenVerifier | None, Depends(get_agent_verifier)] = None,
) -> AgentRunnerClaims:
    if verifier is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="agent authentication unavailable"
        )
    try:
        return verifier.verify(authorization)
    except AgentAuthenticationError as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="runner bearer rejected"
        ) from error
    except AgentAuthorizationError as error:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="agent processing disabled"
        ) from error


def get_agent_facade_service() -> AgentFacadeService:
    return AgentFacadeService(UnavailableAgentToolBackend())


def _trace_id() -> str:
    context = current_correlation()
    return context.trace_id if context is not None else f"tr_{uuid4().hex}"


def _error_response(
    *, trace_id: str, code: str, message: str, status_code: int, path: str = "$"
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "trace_id": trace_id,
            "error": {
                "code": code,
                "message": message[:300],
                "reason_codes": [code],
                "field_errors": [] if path == "$" else [{"path": path[:200], "code": code}],
            },
        },
    )


async def _invoke_fixed_route(
    operation: AgentToolOperation,
    request: Request,
    claims: AgentRunnerClaims,
    service: AgentFacadeService,
) -> JSONResponse:
    trace_id = _trace_id()
    contract = TOOL_REGISTRY.get(operation)
    try:
        if contract.method == "GET":
            arguments: Mapping[str, Any] = {}
        else:
            raw = await request.json()
            if not isinstance(raw, dict):
                raise ToolArgumentsInvalid(operation.value, ("$: JSON object required",))
            arguments = raw
        status_code, payload = service.invoke(
            operation=operation,
            arguments=arguments,
            path_parameters={key: str(value) for key, value in request.path_params.items()},
            idempotency_key=request.headers.get("Idempotency-Key"),
            if_match=request.headers.get("If-Match"),
            claims=claims,
            trace_id=trace_id,
        )
        response = JSONResponse(status_code=status_code, content=payload)
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("row_version"), int):
            response.headers["ETag"] = f'"{data["row_version"]}"'
        return response
    except ToolArgumentsInvalid as error:
        path = error.errors[0].split(":", 1)[0] if error.errors else "$"
        return _error_response(
            trace_id=trace_id,
            code="VALIDATION_ERROR",
            message="Tool arguments violate the registered contract.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            path=path,
        )
    except AgentAuthorizationError:
        return _error_response(
            trace_id=trace_id,
            code="POLICY_DENIED",
            message="The bound runner context does not authorize this operation.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    except AgentToolUnavailable:
        return _error_response(
            trace_id=trace_id,
            code="TOOL_UNAVAILABLE",
            message="The deterministic tool adapter is unavailable; hand off to staff.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def _authorize_bound_path(claims: AgentRunnerClaims, path_parameters: Mapping[str, str]) -> None:
    raw_request_id = path_parameters.get("order_request_id")
    if raw_request_id is not None:
        try:
            request_id = UUID(raw_request_id)
        except ValueError as error:
            raise AgentAuthorizationError("invalid bound order request") from error
        if claims.order_request_id is None or request_id != claims.order_request_id:
            raise AgentAuthorizationError("cross-request access denied")
    public_code = path_parameters.get("public_code")
    if public_code is not None and (
        claims.public_code is None or not hmac.compare_digest(public_code, claims.public_code)
    ):
        raise AgentAuthorizationError("public order locator is not bound to this run")


def _validate_required_headers(
    required: tuple[str, ...], idempotency_key: str | None, if_match: str | None
) -> None:
    if "Idempotency-Key" in required and (
        idempotency_key is None
        or not 16 <= len(idempotency_key) <= 128
        or not all(character.isalnum() or character in "._:-" for character in idempotency_key)
    ):
        raise ToolArgumentsInvalid("header", ("Idempotency-Key: invalid or missing",))
    if "If-Match" in required and (
        if_match is None
        or len(if_match) < 3
        or not if_match.startswith('"')
        or not if_match.endswith('"')
        or not if_match[1:-1].isdigit()
    ):
        raise ToolArgumentsInvalid("header", ("If-Match: invalid or missing",))


def _register_routes() -> None:
    for operation, contract in TOOL_REGISTRY.operations.items():
        router.add_api_route(
            contract.path,
            _fixed_endpoint(operation),
            methods=[contract.method],
            status_code=contract.success_status,
            name=operation.value,
        )


def _fixed_endpoint(operation: AgentToolOperation) -> Any:
    async def endpoint(
        request: Request,
        claims: Annotated[AgentRunnerClaims, Depends(current_agent_claims)],
        service: Annotated[AgentFacadeService, Depends(get_agent_facade_service)],
    ) -> JSONResponse:
        return await _invoke_fixed_route(operation, request, claims, service)

    return endpoint


_register_routes()
