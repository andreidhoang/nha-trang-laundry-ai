"""Compile the sole public-agent OpenAPI registry into strict model boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError


class AgentToolOperation(StrEnum):
    """Operation IDs that are allowed to cross the public-agent boundary."""

    CATALOG_RESOLVE = "catalogResolve"
    ORDER_REQUEST_CREATE = "orderRequestCreate"
    ORDER_REQUEST_RECORD_CUSTOMER_FACTS = "orderRequestRecordCustomerFacts"
    QUOTE_ESTIMATE = "quoteEstimate"
    DELIVERY_EVALUATE = "deliveryEvaluate"
    CAPACITY_CHECK = "capacityCheck"
    MESSAGE_DRAFT_CREATE = "messageDraftCreate"
    PUBLIC_ORDER_STATUS_GET = "publicOrderStatusGet"
    INCIDENT_OPEN = "incidentOpen"
    APPROVAL_REQUEST_CREATE = "approvalRequestCreate"


class AgentToolSideEffect(StrEnum):
    """Normative side-effect class declared by ``x-agent-tool``."""

    NONE = "NONE"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"


OPENCLAW_TOOL_NAMES: dict[AgentToolOperation, str] = {
    AgentToolOperation.CATALOG_RESOLVE: "laundry_catalog_resolve",
    AgentToolOperation.ORDER_REQUEST_CREATE: "laundry_order_request_create",
    AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS: (
        "laundry_order_request_record_customer_facts"
    ),
    AgentToolOperation.QUOTE_ESTIMATE: "laundry_quote_estimate",
    AgentToolOperation.DELIVERY_EVALUATE: "laundry_delivery_evaluate",
    AgentToolOperation.CAPACITY_CHECK: "laundry_capacity_check",
    AgentToolOperation.MESSAGE_DRAFT_CREATE: "laundry_message_draft_create",
    AgentToolOperation.PUBLIC_ORDER_STATUS_GET: "laundry_public_order_status_get",
    AgentToolOperation.INCIDENT_OPEN: "laundry_incident_open",
    AgentToolOperation.APPROVAL_REQUEST_CREATE: "laundry_approval_request_create",
}

# None of these fields may be supplied by the model at any nesting level. Some
# concepts have similarly named customer-provided text fields, so this list is
# deliberately exact rather than substring based.
SERVER_OWNED_MODEL_FIELDS = frozenset(
    {
        "actor",
        "actor_id",
        "actor_role",
        "address_id",
        "approval_state",
        "capability",
        "contact_id",
        "conversation_id",
        "customer_id",
        "distance_measurement_id",
        "executable_capability",
        "expires_at",
        "obligations",
        "order_id",
        "order_request_id",
        "policy_version",
        "reason_codes",
        "required_approver_role",
        "stage",
        "store_id",
        "tenant_id",
        "ttl",
    }
)


class ToolRegistryError(ValueError):
    """The normative registry itself is unsafe or malformed."""


class ToolArgumentsInvalid(ValueError):
    """Model arguments did not match the operation's strict request schema."""

    def __init__(self, operation_id: str, errors: tuple[str, ...]) -> None:
        self.operation_id = operation_id
        self.errors = errors
        super().__init__(f"VALIDATION_ERROR: {operation_id}: {'; '.join(errors)}")


@dataclass(frozen=True, slots=True)
class OperationContract:
    operation: AgentToolOperation
    tool_name: str
    method: str
    path: str
    description: str
    side_effect: AgentToolSideEffect
    max_calls_per_run: int
    model_argument_schema: Mapping[str, Any]
    success_status: int
    success_response_schema: Mapping[str, Any]
    error_response_schema: Mapping[str, Any]
    path_parameters: tuple[str, ...]
    header_parameters: tuple[str, ...]

    def validate_model_arguments(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Reject unknown/server-owned fields and return a detached validated copy."""

        injected = sorted(_find_server_owned_fields(arguments))
        if injected:
            raise ToolArgumentsInvalid(
                self.operation.value,
                tuple(f"server-owned field is prohibited: {field}" for field in injected),
            )
        validator = Draft202012Validator(
            self.model_argument_schema,
            format_checker=FormatChecker(),
        )
        errors = tuple(
            _format_validation_error(error) for error in validator.iter_errors(arguments)
        )
        if errors:
            raise ToolArgumentsInvalid(self.operation.value, tuple(sorted(errors)))
        return deepcopy(dict(arguments))

    def validate_success_response(self, response: Mapping[str, Any]) -> dict[str, Any]:
        """Reject backend output that drifted from the customer-safe response contract."""

        validator = Draft202012Validator(
            self.success_response_schema,
            format_checker=FormatChecker(),
        )
        errors = tuple(_format_validation_error(error) for error in validator.iter_errors(response))
        if errors:
            raise ToolArgumentsInvalid(f"{self.operation.value}.response", tuple(sorted(errors)))
        return deepcopy(dict(response))

    def validate_error_response(self, response: Mapping[str, Any]) -> dict[str, Any]:
        """Reject malformed failures before their text can reach the model."""

        validator = Draft202012Validator(
            self.error_response_schema,
            format_checker=FormatChecker(),
        )
        errors = tuple(_format_validation_error(error) for error in validator.iter_errors(response))
        if errors:
            raise ToolArgumentsInvalid(
                f"{self.operation.value}.error_response", tuple(sorted(errors))
            )
        return deepcopy(dict(response))


@dataclass(frozen=True, slots=True)
class AgentToolRegistry:
    openapi_version: str
    contract_version: str
    operations: Mapping[AgentToolOperation, OperationContract]

    def get(self, operation: AgentToolOperation | str) -> OperationContract:
        try:
            operation_id = AgentToolOperation(operation)
            return self.operations[operation_id]
        except (KeyError, ValueError) as error:
            raise ToolRegistryError(f"Unknown public-agent operation: {operation}") from error

    @property
    def operation_ids(self) -> tuple[str, ...]:
        return tuple(operation.value for operation in self.operations)

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(contract.tool_name for contract in self.operations.values())


def load_agent_tool_registry(path: Path) -> AgentToolRegistry:
    """Load, resolve local refs, and fail closed on contract drift."""

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ToolRegistryError("Agent OpenAPI document must be a mapping")
    openapi_version = raw.get("openapi")
    info = raw.get("info")
    paths = raw.get("paths")
    if not isinstance(openapi_version, str) or not openapi_version.startswith("3.1."):
        raise ToolRegistryError("Agent tool contract must use OpenAPI 3.1")
    if not isinstance(info, dict) or not isinstance(info.get("version"), str):
        raise ToolRegistryError("Agent tool contract requires info.version")
    if not isinstance(paths, dict):
        raise ToolRegistryError("Agent tool contract requires paths")

    compiled: dict[AgentToolOperation, OperationContract] = {}
    for route, path_item in paths.items():
        if not isinstance(route, str) or not isinstance(path_item, dict):
            raise ToolRegistryError("Every OpenAPI path must be a mapping")
        inherited = _as_parameter_list(path_item.get("parameters", []))
        for method, raw_operation in path_item.items():
            if method.lower() not in {"get", "post"}:
                continue
            if not isinstance(raw_operation, dict):
                raise ToolRegistryError(f"Operation at {method.upper()} {route} must be a mapping")
            raw_operation_id = raw_operation.get("operationId")
            if not isinstance(raw_operation_id, str):
                raise ToolRegistryError(
                    f"Operation at {method.upper()} {route} requires a string operationId"
                )
            try:
                operation_id = AgentToolOperation(raw_operation_id)
            except ValueError as error:
                raise ToolRegistryError(
                    f"Unregistered operationId at {method.upper()} {route}: {raw_operation_id}"
                ) from error
            if operation_id in compiled:
                raise ToolRegistryError(f"Duplicate operationId: {operation_id.value}")
            parameters = inherited + _as_parameter_list(raw_operation.get("parameters", []))
            resolved_parameters = tuple(
                _resolve_local_refs(raw, parameter) for parameter in parameters
            )
            path_parameters = tuple(
                str(parameter["name"])
                for parameter in resolved_parameters
                if parameter.get("in") == "path"
            )
            header_parameters = tuple(
                str(parameter["name"])
                for parameter in resolved_parameters
                if parameter.get("in") == "header"
            )
            model_schema = _request_body_schema(raw, raw_operation)
            success_status, response_schema = _success_response_schema(raw, raw_operation)
            error_response_schema = _error_response_schema(raw, raw_operation)
            side_effect, max_calls_per_run = _agent_tool_limits(raw_operation, operation_id)
            try:
                Draft202012Validator.check_schema(model_schema)
                Draft202012Validator.check_schema(response_schema)
                Draft202012Validator.check_schema(error_response_schema)
            except SchemaError as error:
                raise ToolRegistryError(
                    f"Invalid request schema for {operation_id.value}: {error.message}"
                ) from error
            top_properties = model_schema.get("properties", {})
            if isinstance(top_properties, dict) and (
                set(path_parameters) | set(header_parameters)
            ).intersection(top_properties):
                raise ToolRegistryError(
                    f"{operation_id.value} exposes a server-derived path/header "
                    "parameter to the model"
                )
            compiled[operation_id] = OperationContract(
                operation=operation_id,
                tool_name=OPENCLAW_TOOL_NAMES[operation_id],
                method=method.upper(),
                path=route,
                description=str(
                    raw_operation.get("summary") or raw_operation.get("description") or ""
                ),
                side_effect=side_effect,
                max_calls_per_run=max_calls_per_run,
                model_argument_schema=model_schema,
                success_status=success_status,
                success_response_schema=response_schema,
                error_response_schema=error_response_schema,
                path_parameters=path_parameters,
                header_parameters=header_parameters,
            )

    expected = set(AgentToolOperation)
    if set(compiled) != expected:
        missing = sorted(operation.value for operation in expected.difference(compiled))
        extra = sorted(operation.value for operation in set(compiled).difference(expected))
        raise ToolRegistryError(f"Agent operation drift; missing={missing}, extra={extra}")
    if len(set(OPENCLAW_TOOL_NAMES.values())) != len(OPENCLAW_TOOL_NAMES):
        raise ToolRegistryError("OpenClaw public tool names must be unique")
    return AgentToolRegistry(
        openapi_version=openapi_version,
        contract_version=info["version"],
        operations=compiled,
    )


def _request_body_schema(
    document: Mapping[str, Any], operation: Mapping[str, Any]
) -> dict[str, Any]:
    request_body = operation.get("requestBody")
    if request_body is None:
        return {"type": "object", "additionalProperties": False, "properties": {}}
    resolved_body = _resolve_local_refs(document, request_body)
    if not isinstance(resolved_body, dict):
        raise ToolRegistryError("requestBody must resolve to a mapping")
    content = resolved_body.get("content")
    json_content = content.get("application/json") if isinstance(content, dict) else None
    schema = json_content.get("schema") if isinstance(json_content, dict) else None
    if not isinstance(schema, dict):
        raise ToolRegistryError("Every agent request body must define application/json schema")
    resolved = _resolve_local_refs(document, schema)
    if not isinstance(resolved, dict):
        raise ToolRegistryError("Request schema must resolve to a mapping")
    return resolved


def _success_response_schema(
    document: Mapping[str, Any], operation: Mapping[str, Any]
) -> tuple[int, dict[str, Any]]:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        raise ToolRegistryError("Every agent operation requires responses")
    success_keys = sorted(
        key for key in responses if isinstance(key, str) and len(key) == 3 and key.startswith("2")
    )
    if len(success_keys) != 1:
        raise ToolRegistryError("Every agent operation requires exactly one explicit 2xx response")
    status = int(success_keys[0])
    response = _resolve_local_refs(document, responses[success_keys[0]])
    if not isinstance(response, dict):
        raise ToolRegistryError("Success response must resolve to a mapping")
    content = response.get("content")
    json_content = content.get("application/json") if isinstance(content, dict) else None
    schema = json_content.get("schema") if isinstance(json_content, dict) else None
    if not isinstance(schema, dict):
        raise ToolRegistryError("Every agent success response requires an application/json schema")
    resolved = _resolve_local_refs(document, schema)
    if not isinstance(resolved, dict):
        raise ToolRegistryError("Success response schema must resolve to a mapping")
    return status, resolved


def _error_response_schema(
    document: Mapping[str, Any], operation: Mapping[str, Any]
) -> dict[str, Any]:
    responses = operation.get("responses")
    default = responses.get("default") if isinstance(responses, dict) else None
    if default is None:
        raise ToolRegistryError("Every agent operation requires a default error response")
    response = _resolve_local_refs(document, default)
    if not isinstance(response, dict):
        raise ToolRegistryError("Default error response must resolve to a mapping")
    content = response.get("content")
    json_content = content.get("application/json") if isinstance(content, dict) else None
    schema = json_content.get("schema") if isinstance(json_content, dict) else None
    if not isinstance(schema, dict):
        raise ToolRegistryError("Every default error requires an application/json schema")
    resolved = _resolve_local_refs(document, schema)
    if not isinstance(resolved, dict):
        raise ToolRegistryError("Default error schema must resolve to a mapping")
    return resolved


def _agent_tool_limits(
    operation: Mapping[str, Any], operation_id: AgentToolOperation
) -> tuple[AgentToolSideEffect, int]:
    extension = operation.get("x-agent-tool")
    if not isinstance(extension, dict):
        raise ToolRegistryError(f"{operation_id.value} requires x-agent-tool controls")
    raw_side_effect = extension.get("side_effect")
    if not isinstance(raw_side_effect, str):
        raise ToolRegistryError(f"{operation_id.value} has invalid side_effect")
    try:
        side_effect = AgentToolSideEffect(raw_side_effect)
    except ValueError as error:
        raise ToolRegistryError(f"{operation_id.value} has invalid side_effect") from error
    max_calls = extension.get("max_calls_per_run")
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) or not 1 <= max_calls <= 6:
        raise ToolRegistryError(f"{operation_id.value} has invalid max_calls_per_run")
    return side_effect, max_calls


def _resolve_local_refs(document: Mapping[str, Any], node: Any, stack: tuple[str, ...] = ()) -> Any:
    if isinstance(node, list):
        return [_resolve_local_refs(document, item, stack) for item in node]
    if not isinstance(node, dict):
        return deepcopy(node)
    if set(node) == {"$ref"}:
        reference = node["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ToolRegistryError(f"Only local OpenAPI refs are allowed: {reference}")
        if reference in stack:
            raise ToolRegistryError(f"Cyclic OpenAPI ref: {reference}")
        target: Any = document
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, Mapping) or part not in target:
                raise ToolRegistryError(f"Unresolved OpenAPI ref: {reference}")
            target = target[part]
        return _resolve_local_refs(document, target, (*stack, reference))
    return {key: _resolve_local_refs(document, value, stack) for key, value in node.items()}


def _as_parameter_list(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ToolRegistryError("OpenAPI parameters must be a list of mappings")
    return tuple(value)


def _find_server_owned_fields(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in SERVER_OWNED_MODEL_FIELDS:
                found.add(key)
            found.update(_find_server_owned_fields(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_find_server_owned_fields(child))
    return found


def _format_validation_error(error: ValidationError) -> str:
    location = ".".join(str(part) for part in error.absolute_path) or "$"
    return f"{location}: {error.message}"
