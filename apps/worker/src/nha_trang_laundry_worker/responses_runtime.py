"""Bounded, fail-closed adapter for the OpenAI Responses protocol.

This module deliberately stops at an injectable transport boundary.  It has no API key,
network client, provider built-in tool, channel client, or send capability.  The model can
request only an exact operation from the normative Agent Tool registry; the existing
``AgentToolBridgeSession`` remains the sole authority for identity, policy, paths, budgets,
idempotency, and business mutations.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from threading import Lock
from typing import Annotated, Any, Literal, Protocol, Self
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from nha_trang_laundry_contracts import (
    CAPABILITY_OPERATIONS,
    AgentToolOperation,
    ReleaseCapability,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from .agent_runner import (
    ROOT,
    TOOL_REGISTRY,
    AgentRuntimeInvocation,
    AgentRuntimeOutput,
    AgentToolBridgeRejected,
    AgentToolBridgeSession,
)

Sha256Pin = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
BoundedIdentifier = Annotated[
    str, StringConstraints(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:/-]+$")
]
CallId = Annotated[
    str, StringConstraints(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
]
MoneyString = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,12})?$")]

DETERMINISTIC_HANDOFF_TEXT = "Nhân viên sẽ tiếp tục hỗ trợ yêu cầu này."
CURRENT_TOOL_CONTRACT_HASH = (
    "sha256:"
    + sha256((ROOT / "specs/contracts/agent-tools-v1.openapi.yaml").read_bytes()).hexdigest()
)
FINAL_OUTPUT_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["disposition", "draft_text", "reason_code"],
    "properties": {
        "disposition": {
            "type": "string",
            "enum": ["DRAFT_REQUIRES_HUMAN", "REQUIRE_HUMAN"],
        },
        "draft_text": {"type": ["string", "null"], "minLength": 1, "maxLength": 4000},
        "reason_code": {
            "type": ["string", "null"],
            "pattern": r"^[A-Z][A-Z0-9_]{2,63}$",
        },
    },
}


class ResponsesRuntimeFailure(RuntimeError):
    """Base class for controlled Responses adapter failures."""


class ResponsesContextRejected(ResponsesRuntimeFailure):
    """The immutable context packet or one of its pins did not match the invocation."""


class ResponsesBudgetExhausted(ResponsesRuntimeFailure):
    """A model, token, cost, or deadline budget was exhausted before more authority was used."""


class ResponsesProviderFailure(ResponsesRuntimeFailure):
    """Base class for a normalized provider transport failure."""


class ResponsesConnectionFailure(ResponsesProviderFailure):
    """The transport proves that the request was not accepted by the provider."""


class ResponsesOutcomeAmbiguous(ResponsesProviderFailure):
    """The provider may have processed the request, but no trustworthy response is available."""


class ResponsesRequestCancelled(ResponsesProviderFailure):
    """The in-flight request was cancelled and is conservatively treated as ambiguous."""


class ResponsesTransportTimeout(ResponsesProviderFailure):
    """The provider transport timed out with an ambiguous billing outcome."""


class ResponsesEvidencePersistenceError(ResponsesRuntimeFailure):
    """Safe terminal evidence could not be persisted."""


class ResponsesPriceTable(BaseModel):
    """Versioned USD prices for exactly one immutable provider model release."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    price_table_version: BoundedIdentifier
    provider: Literal["openai"] = "openai"
    model_id: BoundedIdentifier
    immutable_model_release: BoundedIdentifier
    currency: Literal["USD"] = "USD"
    effective_at: datetime
    input_cost_per_million_usd: MoneyString
    cached_input_cost_per_million_usd: MoneyString
    output_cost_per_million_usd: MoneyString
    price_table_hash: Sha256Pin

    @model_validator(mode="after")
    def price_table_is_consistent(self) -> Self:
        if self.effective_at.tzinfo is None:
            raise ValueError("price-table effective time must be timezone-aware")
        if Decimal(self.cached_input_cost_per_million_usd) > Decimal(
            self.input_cost_per_million_usd
        ):
            raise ValueError("cached-input price cannot exceed the registered input price")
        if not _constant_text_equal(self.price_table_hash, self.expected_hash()):
            raise ValueError("price-table hash does not match its contents")
        return self

    def expected_hash(self) -> str:
        return _sha256_json(self.model_dump(mode="json", exclude={"price_table_hash"}))

    @classmethod
    def assemble(
        cls,
        *,
        price_table_version: str,
        model_id: str,
        immutable_model_release: str,
        effective_at: datetime,
        input_cost_per_million_usd: str,
        cached_input_cost_per_million_usd: str,
        output_cost_per_million_usd: str,
    ) -> Self:
        values: dict[str, Any] = {
            "schema_version": 1,
            "price_table_version": price_table_version,
            "provider": "openai",
            "model_id": model_id,
            "immutable_model_release": immutable_model_release,
            "currency": "USD",
            "effective_at": effective_at,
            "input_cost_per_million_usd": input_cost_per_million_usd,
            "cached_input_cost_per_million_usd": cached_input_cost_per_million_usd,
            "output_cost_per_million_usd": output_cost_per_million_usd,
        }
        unhashed = cls.model_construct(
            **values,
            price_table_hash="sha256:" + ("0" * 64),
        )
        return cls(**values, price_table_hash=unhashed.expected_hash())


class ResponsesRuntimeConfig(BaseModel):
    """Immutable, explicitly priced configuration for one exact Responses model release."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_id: BoundedIdentifier
    provider_route: Literal["/v1/responses"] = "/v1/responses"
    model_id: BoundedIdentifier
    immutable_model_release: BoundedIdentifier
    reasoning_effort: Literal["low", "medium", "high"]
    runtime_registry_hash: Sha256Pin
    prompt_bundle_version: BoundedIdentifier
    prompt_bundle_hash: Sha256Pin
    prompt_instructions_hash: Sha256Pin
    tool_contract_hash: Sha256Pin
    max_model_calls: int = Field(ge=1, le=3)
    max_input_tokens: int = Field(ge=1, le=8000)
    max_output_tokens: int = Field(ge=1, le=1200)
    max_turn_cost_usd: MoneyString
    price_table: ResponsesPriceTable

    @model_validator(mode="after")
    def price_table_matches_runtime(self) -> Self:
        if self.price_table.model_id != self.model_id:
            raise ValueError("price table model ID does not match the runtime model")
        if self.price_table.immutable_model_release != self.immutable_model_release:
            raise ValueError("price table release does not match the runtime model release")
        return self


class ResponsesRuntimeContext(BaseModel):
    """Server-assembled packet; its business bindings never become model arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: UUID
    capability: ReleaseCapability
    session_key_hash: Sha256Pin
    runtime_id: BoundedIdentifier
    model_id: BoundedIdentifier
    immutable_model_release: BoundedIdentifier
    runtime_registry_hash: Sha256Pin
    prompt_bundle_version: BoundedIdentifier
    prompt_bundle_hash: Sha256Pin
    prompt_instructions_hash: Sha256Pin
    tool_contract_hash: Sha256Pin
    deadline_at: datetime
    instructions: Annotated[str, StringConstraints(min_length=1, max_length=16000)]
    input_text: Annotated[str, StringConstraints(min_length=1, max_length=24000)]
    context_packet_hash: Sha256Pin

    @model_validator(mode="after")
    def packet_is_self_consistent(self) -> Self:
        if self.deadline_at.tzinfo is None:
            raise ValueError("Responses context deadline must be timezone-aware")
        if not _constant_text_equal(self.context_packet_hash, self.expected_hash()):
            raise ValueError("Responses context packet hash does not match its contents")
        return self

    def expected_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"context_packet_hash"})
        return _sha256_json(payload)

    @classmethod
    def assemble(
        cls,
        *,
        run_id: UUID,
        capability: ReleaseCapability,
        session_key: str,
        config: ResponsesRuntimeConfig,
        deadline_at: datetime,
        instructions: str,
        input_text: str,
    ) -> Self:
        values: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "capability": capability,
            "session_key_hash": _sha256_text(session_key),
            "runtime_id": config.runtime_id,
            "model_id": config.model_id,
            "immutable_model_release": config.immutable_model_release,
            "runtime_registry_hash": config.runtime_registry_hash,
            "prompt_bundle_version": config.prompt_bundle_version,
            "prompt_bundle_hash": config.prompt_bundle_hash,
            "prompt_instructions_hash": config.prompt_instructions_hash,
            "tool_contract_hash": config.tool_contract_hash,
            "deadline_at": deadline_at,
            "instructions": instructions,
            "input_text": input_text,
        }
        unhashed = cls.model_construct(
            **values,
            context_packet_hash="sha256:" + ("0" * 64),
        )
        return cls(**values, context_packet_hash=unhashed.expected_hash())


class ResponsesContextLoader(Protocol):
    """Loads one exact, preassembled context packet for a claimed run."""

    def load(self, invocation: AgentRuntimeInvocation) -> ResponsesRuntimeContext: ...


class BoundResponsesContextLoader:
    """Small deterministic context source for a bounded worker batch or test fixture."""

    def __init__(self, contexts: Sequence[ResponsesRuntimeContext]) -> None:
        self._contexts = {context.run_id: context for context in contexts}
        if len(self._contexts) != len(contexts):
            raise ValueError("Responses contexts must have unique run IDs")

    def load(self, invocation: AgentRuntimeInvocation) -> ResponsesRuntimeContext:
        try:
            return self._contexts[invocation.run_id]
        except KeyError as error:
            raise ResponsesContextRejected("CONTEXT_NOT_FOUND") from error


class ResponsesInputText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["input_text"] = "input_text"
    text: Annotated[str, StringConstraints(min_length=1, max_length=24000)]


class ResponsesUserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["message"] = "message"
    role: Literal["user"] = "user"
    content: tuple[ResponsesInputText, ...] = Field(min_length=1, max_length=1)


class ResponsesFunctionCallInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["function_call"] = "function_call"
    call_id: CallId
    name: BoundedIdentifier
    arguments: str = Field(min_length=2, max_length=16000)


class ResponsesFunctionCallOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["function_call_output"] = "function_call_output"
    call_id: CallId
    output: str = Field(min_length=2, max_length=24000)


class ResponsesReasoningInput(BaseModel):
    """Opaque ephemeral reasoning item required only for stateless tool continuation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["reasoning"] = "reasoning"
    id: BoundedIdentifier
    encrypted_content: str | None = Field(default=None, max_length=64000)


ResponsesInputItem = (
    ResponsesUserMessage
    | ResponsesFunctionCallInput
    | ResponsesFunctionCallOutput
    | ResponsesReasoningInput
)


class ResponsesFunctionTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["function"] = "function"
    name: BoundedIdentifier
    description: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    parameters: Mapping[str, Any]
    strict: Literal[True] = True


class ResponsesReasoningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    effort: Literal["low", "medium", "high"]


class ResponsesJsonSchemaFormat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["json_schema"] = "json_schema"
    name: Literal["bounded_agent_outcome"] = "bounded_agent_outcome"
    strict: Literal[True] = True
    schema_: Mapping[str, Any] = Field(alias="schema")


class ResponsesTextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: ResponsesJsonSchemaFormat


class ResponsesRequest(BaseModel):
    """Exact provider request; there is no generic URL, built-in tool, or stored response state."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    model: BoundedIdentifier
    store: Literal[False] = False
    parallel_tool_calls: Literal[False] = False
    include: tuple[Literal["reasoning.encrypted_content"], ...] = ("reasoning.encrypted_content",)
    instructions: Annotated[str, StringConstraints(min_length=1, max_length=16000)]
    input: tuple[ResponsesInputItem, ...] = Field(min_length=1, max_length=32)
    tools: tuple[ResponsesFunctionTool, ...] = Field(max_length=10)
    tool_choice: Literal["auto"] = "auto"
    reasoning: ResponsesReasoningConfig
    text: ResponsesTextConfig
    max_output_tokens: int = Field(ge=1, le=1200)


class ResponsesUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def cached_tokens_are_part_of_input(self) -> Self:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input tokens cannot exceed total input tokens")
        return self


class ResponsesFunctionCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["function_call"] = "function_call"
    call_id: CallId
    name: BoundedIdentifier
    arguments: str = Field(min_length=1, max_length=16000)


class ResponsesOutputText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["output_text"] = "output_text"
    text: Annotated[str, StringConstraints(min_length=1, max_length=8000)]


class ResponsesAssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: tuple[ResponsesOutputText, ...] = Field(min_length=1, max_length=1)


class ResponsesReasoningItem(BaseModel):
    """Normalized transport item; contents stay in memory and never enter evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["reasoning"] = "reasoning"
    id: BoundedIdentifier
    encrypted_content: str | None = Field(default=None, max_length=64000)


ResponsesOutputItem = ResponsesFunctionCall | ResponsesAssistantMessage | ResponsesReasoningItem


class ResponsesProviderResponse(BaseModel):
    """Narrow normalized response returned by the provider transport implementation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["completed"] = "completed"
    parallel_tool_calls: Literal[False] = False
    output: tuple[ResponsesOutputItem, ...] = Field(min_length=1, max_length=8)
    usage: ResponsesUsage


class ResponsesProviderTransport(Protocol):
    """Provider boundary.  A real implementation remains release-gated by ``AgentRunner``."""

    provider_backed: bool

    def create(
        self, request: ResponsesRequest, *, timeout_seconds: float
    ) -> ResponsesProviderResponse | Mapping[str, Any]: ...


class ScriptedResponsesTransport:
    """Deterministic, no-network transport used by tests and local synthetic evaluation."""

    provider_backed = False

    def __init__(
        self,
        script: Sequence[ResponsesProviderResponse | Mapping[str, Any] | ResponsesProviderFailure],
    ) -> None:
        self._script = list(script)
        self._lock = Lock()
        self.requests: list[ResponsesRequest] = []
        self.timeouts: list[float] = []

    def create(
        self, request: ResponsesRequest, *, timeout_seconds: float
    ) -> ResponsesProviderResponse | Mapping[str, Any]:
        with self._lock:
            self.requests.append(request)
            self.timeouts.append(timeout_seconds)
            if not self._script:
                raise ResponsesConnectionFailure("SCRIPT_EXHAUSTED")
            outcome = self._script.pop(0)
        if isinstance(outcome, ResponsesProviderFailure):
            raise outcome
        return outcome


class ResponsesFinalEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: Literal["DRAFT_REQUIRES_HUMAN", "REQUIRE_HUMAN"]
    draft_text: Annotated[str, StringConstraints(min_length=1, max_length=4000)] | None
    reason_code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")] | None

    @model_validator(mode="after")
    def disposition_is_consistent(self) -> Self:
        if self.disposition == "DRAFT_REQUIRES_HUMAN":
            if self.draft_text is None or self.reason_code is not None:
                raise ValueError("a draft requires text and no handoff reason")
        elif self.draft_text is not None or self.reason_code is None:
            raise ValueError("a deterministic handoff requires only a reason code")
        return self


class ResponsesRuntimeEvidence(BaseModel):
    """Redacted terminal evidence.  Raw prompts, arguments, results, IDs and CoT are absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: UUID
    terminal_outcome: Literal["DRAFT", "REQUIRE_HUMAN"]
    terminal_code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
    runtime_id: BoundedIdentifier
    model_id: BoundedIdentifier
    immutable_model_release: BoundedIdentifier
    runtime_registry_hash: Sha256Pin
    prompt_bundle_version: BoundedIdentifier
    prompt_bundle_hash: Sha256Pin
    prompt_instructions_hash: Sha256Pin
    tool_contract_hash: Sha256Pin
    price_table_version: BoundedIdentifier
    price_table_hash: Sha256Pin
    context_packet_hash: Sha256Pin
    provider_backed: bool
    model_attempt_count: int = Field(ge=0, le=3)
    tool_call_count: int = Field(ge=0, le=6)
    input_tokens: int = Field(ge=0, le=8000)
    cached_input_tokens: int = Field(ge=0, le=8000)
    output_tokens: int = Field(ge=0, le=1200)
    reservation_count: int = Field(ge=0, le=3)
    reserved_cost_usd: MoneyString
    settled_cost_usd: MoneyString
    released_cost_usd: MoneyString
    retry_count: Literal[0] = 0
    bridge_revoked: Literal[True] = True
    chain_of_thought_persisted: Literal[False] = False
    provider_response_id_persisted: Literal[False] = False


class ResponsesEvidenceSink(Protocol):
    """Durable implementations must atomically persist one safe terminal record per run."""

    def persist(self, evidence: ResponsesRuntimeEvidence) -> None: ...


class RecordingResponsesEvidenceSink:
    """Thread-safe deterministic evidence sink for local tests; it stores only redacted models."""

    def __init__(self) -> None:
        self.records: list[ResponsesRuntimeEvidence] = []
        self._run_ids: set[UUID] = set()
        self._lock = Lock()

    def persist(self, evidence: ResponsesRuntimeEvidence) -> None:
        with self._lock:
            if evidence.run_id in self._run_ids:
                raise ResponsesEvidencePersistenceError("DUPLICATE_TERMINAL_EVIDENCE")
            self._run_ids.add(evidence.run_id)
            self.records.append(evidence)


@dataclass(frozen=True, slots=True)
class _Reservation:
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class _TurnBudget:
    def __init__(self, config: ResponsesRuntimeConfig, capability: ReleaseCapability) -> None:
        self._config = config
        self._input_rate = Decimal(config.price_table.input_cost_per_million_usd)
        self._cached_input_rate = Decimal(config.price_table.cached_input_cost_per_million_usd)
        self._output_rate = Decimal(config.price_table.output_cost_per_million_usd)
        self._hard_cost = Decimal(config.max_turn_cost_usd)
        self._model_limit = min(config.max_model_calls, _intent_model_limit(capability))
        self.model_attempts = 0
        self.input_tokens = 0
        self.cached_input_tokens = 0
        self.output_tokens = 0
        self.reservation_count = 0
        self.reserved_cost = Decimal(0)
        self.settled_cost = Decimal(0)
        self.released_cost = Decimal(0)

    def reserve(self) -> _Reservation:
        if self.model_attempts >= self._model_limit:
            raise ResponsesBudgetExhausted("MODEL_CALL_BUDGET_EXHAUSTED")
        remaining_input = self._config.max_input_tokens - self.input_tokens
        remaining_output = self._config.max_output_tokens - self.output_tokens
        if remaining_input <= 0 or remaining_output <= 0:
            raise ResponsesBudgetExhausted("TOKEN_BUDGET_EXHAUSTED")
        reservation_cost = _token_cost(
            remaining_input,
            0,
            remaining_output,
            self._input_rate,
            self._cached_input_rate,
            self._output_rate,
        )
        if self.settled_cost + reservation_cost > self._hard_cost:
            raise ResponsesBudgetExhausted("COST_BUDGET_EXHAUSTED")
        self.model_attempts += 1
        self.reservation_count += 1
        self.reserved_cost += reservation_cost
        return _Reservation(remaining_input, remaining_output, reservation_cost)

    def release(self, reservation: _Reservation) -> None:
        self.released_cost += reservation.cost_usd

    def settle_ambiguous(self, reservation: _Reservation) -> None:
        self.settled_cost += reservation.cost_usd

    def settle_usage(self, reservation: _Reservation, usage: ResponsesUsage) -> None:
        if (
            usage.input_tokens > reservation.input_tokens
            or usage.output_tokens > reservation.output_tokens
        ):
            self.settle_ambiguous(reservation)
            raise ResponsesBudgetExhausted("PROVIDER_USAGE_EXCEEDED_RESERVATION")
        actual = _token_cost(
            usage.input_tokens,
            usage.cached_input_tokens,
            usage.output_tokens,
            self._input_rate,
            self._cached_input_rate,
            self._output_rate,
        )
        self.input_tokens += usage.input_tokens
        self.cached_input_tokens += usage.cached_input_tokens
        self.output_tokens += usage.output_tokens
        self.settled_cost += actual
        self.released_cost += reservation.cost_usd - actual


class BoundedResponsesRuntime:
    """Finite-state Responses runtime with one serial, bridge-mediated tool call per turn."""

    def __init__(
        self,
        *,
        config: ResponsesRuntimeConfig,
        context_loader: ResponsesContextLoader,
        transport: ResponsesProviderTransport,
        evidence_sink: ResponsesEvidenceSink,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._context_loader = context_loader
        self._transport = transport
        self._evidence_sink = evidence_sink
        self._now = now or (lambda: datetime.now(UTC))
        self.provider_backed = transport.provider_backed

    def invoke(
        self, invocation: AgentRuntimeInvocation, bridge: AgentToolBridgeSession
    ) -> AgentRuntimeOutput:
        budget = _TurnBudget(self._config, invocation.capability)
        context_hash = _sha256_json({"run_id": str(invocation.run_id), "invalid": True})
        terminal_code = "UNEXPECTED_RUNTIME_FAILURE"
        outcome: Literal["DRAFT", "REQUIRE_HUMAN"] = "REQUIRE_HUMAN"
        draft_text = DETERMINISTIC_HANDOFF_TEXT
        seen_call_ids: set[str] = set()
        try:
            context = self._context_loader.load(invocation)
            context_hash = context.context_packet_hash
            self._validate_context(invocation, context)
            input_items: list[ResponsesInputItem] = [
                ResponsesUserMessage(content=(ResponsesInputText(text=context.input_text),))
            ]
            tools = self._tools_for(invocation.capability)
            while True:
                self._require_time(context.deadline_at)
                reservation = budget.reserve()
                try:
                    request = self._request(context, input_items, tools, budget)
                except Exception:
                    budget.release(reservation)
                    raise
                response = self._call_provider(request, reservation, budget, context.deadline_at)
                function_calls = [
                    item for item in response.output if isinstance(item, ResponsesFunctionCall)
                ]
                messages = [
                    item for item in response.output if isinstance(item, ResponsesAssistantMessage)
                ]
                reasoning = [
                    item for item in response.output if isinstance(item, ResponsesReasoningItem)
                ]
                if function_calls:
                    if len(function_calls) != 1 or messages:
                        raise ResponsesRuntimeFailure("MULTIPLE_OR_MIXED_OUTPUT_ITEMS")
                    call = function_calls[0]
                    if call.call_id in seen_call_ids:
                        raise ResponsesRuntimeFailure("DUPLICATE_TOOL_CALL_ID")
                    seen_call_ids.add(call.call_id)
                    result = self._invoke_tool(invocation, bridge, call, context.deadline_at)
                    input_items.extend(
                        ResponsesReasoningInput(
                            id=item.id,
                            encrypted_content=item.encrypted_content,
                        )
                        for item in reasoning
                    )
                    input_items.append(
                        ResponsesFunctionCallInput(
                            call_id=call.call_id,
                            name=call.name,
                            arguments=call.arguments,
                        )
                    )
                    input_items.append(
                        ResponsesFunctionCallOutput(
                            call_id=call.call_id,
                            output=json.dumps(
                                {"status_code": result.status_code, "body": result.body},
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        )
                    )
                    self._require_time(context.deadline_at)
                    continue
                if len(messages) != 1:
                    raise ResponsesRuntimeFailure("UNSUPPORTED_OUTPUT_ITEM")
                envelope = _parse_final(messages[0].content[0].text)
                if envelope.disposition == "DRAFT_REQUIRES_HUMAN":
                    if envelope.draft_text is None:  # Narrowed by model validation.
                        raise ResponsesRuntimeFailure("INVALID_FINAL_OUTPUT")
                    draft_text = envelope.draft_text
                    terminal_code = "VALIDATED_DRAFT"
                    outcome = "DRAFT"
                else:
                    terminal_code = envelope.reason_code or "MODEL_REQUESTED_HANDOFF"
                break
        except ResponsesConnectionFailure:
            terminal_code = "PROVIDER_CONNECTION_FAILURE"
        except ResponsesOutcomeAmbiguous:
            terminal_code = "PROVIDER_OUTCOME_AMBIGUOUS"
        except ResponsesRequestCancelled:
            terminal_code = "PROVIDER_REQUEST_CANCELLED"
        except ResponsesTransportTimeout:
            terminal_code = "PROVIDER_TIMEOUT"
        except ResponsesContextRejected as error:
            terminal_code = _controlled_code(error, "CONTEXT_REJECTED")
        except ResponsesBudgetExhausted as error:
            terminal_code = _controlled_code(error, "BUDGET_EXHAUSTED")
        except AgentToolBridgeRejected as error:
            terminal_code = _bridge_code(error)
        except (
            ResponsesRuntimeFailure,
            ValidationError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as error:
            terminal_code = _controlled_code(error, "INVALID_PROVIDER_OUTPUT")
        finally:
            bridge.close()
            evidence = ResponsesRuntimeEvidence(
                run_id=invocation.run_id,
                terminal_outcome=outcome,
                terminal_code=terminal_code,
                runtime_id=self._config.runtime_id,
                model_id=self._config.model_id,
                immutable_model_release=self._config.immutable_model_release,
                runtime_registry_hash=self._config.runtime_registry_hash,
                prompt_bundle_version=self._config.prompt_bundle_version,
                prompt_bundle_hash=self._config.prompt_bundle_hash,
                prompt_instructions_hash=self._config.prompt_instructions_hash,
                tool_contract_hash=self._config.tool_contract_hash,
                price_table_version=self._config.price_table.price_table_version,
                price_table_hash=self._config.price_table.price_table_hash,
                context_packet_hash=context_hash,
                provider_backed=self.provider_backed,
                model_attempt_count=budget.model_attempts,
                tool_call_count=bridge.tool_call_count,
                input_tokens=budget.input_tokens,
                cached_input_tokens=budget.cached_input_tokens,
                output_tokens=budget.output_tokens,
                reservation_count=budget.reservation_count,
                reserved_cost_usd=_money(budget.reserved_cost),
                settled_cost_usd=_money(budget.settled_cost),
                released_cost_usd=_money(budget.released_cost),
            )
            try:
                self._evidence_sink.persist(evidence)
            except ResponsesEvidencePersistenceError:
                raise
            except Exception as error:
                raise ResponsesEvidencePersistenceError(
                    "TERMINAL_EVIDENCE_PERSIST_FAILED"
                ) from error
        return AgentRuntimeOutput(
            draft_text=draft_text,
            model_calls=budget.model_attempts,
            input_tokens=budget.input_tokens,
            output_tokens=budget.output_tokens,
        )

    def _validate_context(
        self, invocation: AgentRuntimeInvocation, context: ResponsesRuntimeContext
    ) -> None:
        expected: tuple[tuple[str, object, object], ...] = (
            ("run", context.run_id, invocation.run_id),
            ("capability", context.capability, invocation.capability),
            ("session", context.session_key_hash, _sha256_text(invocation.session_key)),
            ("runtime", context.runtime_id, self._config.runtime_id),
            ("model", context.model_id, self._config.model_id),
            (
                "model_release",
                context.immutable_model_release,
                self._config.immutable_model_release,
            ),
            (
                "runtime_registry",
                context.runtime_registry_hash,
                self._config.runtime_registry_hash,
            ),
            (
                "prompt_version",
                context.prompt_bundle_version,
                self._config.prompt_bundle_version,
            ),
            ("prompt", context.prompt_bundle_hash, self._config.prompt_bundle_hash),
            (
                "prompt_instructions",
                context.prompt_instructions_hash,
                self._config.prompt_instructions_hash,
            ),
            ("tools", context.tool_contract_hash, self._config.tool_contract_hash),
        )
        mismatch = next(
            (
                name
                for name, actual, registered in expected
                if not _constant_text_equal(str(actual), str(registered))
            ),
            None,
        )
        if mismatch is not None:
            raise ResponsesContextRejected(f"CONTEXT_{mismatch.upper()}_MISMATCH")
        if not _constant_text_equal(
            _sha256_text(context.instructions), self._config.prompt_instructions_hash
        ):
            raise ResponsesContextRejected("CONTEXT_PROMPT_CONTENT_MISMATCH")
        if not _constant_text_equal(self._config.tool_contract_hash, CURRENT_TOOL_CONTRACT_HASH):
            raise ResponsesContextRejected("CONTEXT_TOOL_CONTRACT_CONTENT_MISMATCH")
        if self._config.price_table.effective_at > context.deadline_at:
            raise ResponsesContextRejected("PRICE_TABLE_NOT_EFFECTIVE")

    def _request(
        self,
        context: ResponsesRuntimeContext,
        input_items: Sequence[ResponsesInputItem],
        tools: tuple[ResponsesFunctionTool, ...],
        budget: _TurnBudget,
    ) -> ResponsesRequest:
        remaining_output = self._config.max_output_tokens - budget.output_tokens
        if remaining_output <= 0:
            raise ResponsesBudgetExhausted("OUTPUT_TOKEN_BUDGET_EXHAUSTED")
        return ResponsesRequest(
            model=self._config.model_id,
            instructions=context.instructions,
            input=tuple(input_items),
            tools=tools,
            reasoning=ResponsesReasoningConfig(effort=self._config.reasoning_effort),
            text=ResponsesTextConfig(
                format=ResponsesJsonSchemaFormat(schema=deepcopy(FINAL_OUTPUT_SCHEMA))
            ),
            max_output_tokens=remaining_output,
        )

    def _call_provider(
        self,
        request: ResponsesRequest,
        reservation: _Reservation,
        budget: _TurnBudget,
        deadline_at: datetime,
    ) -> ResponsesProviderResponse:
        remaining = self._remaining_seconds(deadline_at)
        if remaining <= 0:
            budget.release(reservation)
            raise ResponsesBudgetExhausted("DEADLINE_EXHAUSTED_BEFORE_PROVIDER")
        try:
            raw = self._transport.create(request, timeout_seconds=remaining)
        except ResponsesConnectionFailure:
            budget.release(reservation)
            raise
        except (ResponsesOutcomeAmbiguous, ResponsesRequestCancelled, ResponsesTransportTimeout):
            budget.settle_ambiguous(reservation)
            raise
        except Exception as error:
            budget.settle_ambiguous(reservation)
            raise ResponsesOutcomeAmbiguous("UNCLASSIFIED_PROVIDER_FAILURE") from error
        try:
            response = ResponsesProviderResponse.model_validate(raw)
        except ValidationError as error:
            budget.settle_ambiguous(reservation)
            raise ResponsesRuntimeFailure("MALFORMED_PROVIDER_RESPONSE") from error
        budget.settle_usage(reservation, response.usage)
        self._require_time(deadline_at)
        return response

    def _invoke_tool(
        self,
        invocation: AgentRuntimeInvocation,
        bridge: AgentToolBridgeSession,
        call: ResponsesFunctionCall,
        deadline_at: datetime,
    ) -> Any:
        self._require_time(deadline_at)
        try:
            operation = AgentToolOperation(call.name)
        except ValueError as error:
            raise ResponsesRuntimeFailure("UNKNOWN_TOOL") from error
        if operation not in CAPABILITY_OPERATIONS[invocation.capability]:
            raise ResponsesRuntimeFailure("UNAUTHORIZED_TOOL")
        try:
            raw_arguments = json.loads(call.arguments)
        except json.JSONDecodeError as error:
            raise ResponsesRuntimeFailure("MALFORMED_TOOL_ARGUMENTS_JSON") from error
        if not isinstance(raw_arguments, dict):
            raise ResponsesRuntimeFailure("TOOL_ARGUMENTS_NOT_OBJECT")
        contract = TOOL_REGISTRY.get(operation)
        provider_validator = Draft202012Validator(
            _provider_strict_schema(contract.model_argument_schema),
            format_checker=FormatChecker(),
        )
        if any(provider_validator.iter_errors(raw_arguments)):
            raise AgentToolBridgeRejected("VALIDATION_ERROR: invalid provider tool arguments")
        arguments = _drop_null_object_fields(raw_arguments)
        idempotency_key = None
        if "Idempotency-Key" in contract.header_parameters:
            digest = sha256(
                f"{invocation.run_id}\0{operation.value}\0{call.call_id}".encode()
            ).hexdigest()
            idempotency_key = f"rsp-{digest[:48]}"
        result = bridge.invoke(
            operation,
            arguments,
            bridge_token=invocation.bridge_token,
            binding_id=bridge.binding_id,
            route_path_parameters=bridge.expected_path_parameters(operation),
            idempotency_key=idempotency_key,
            now=self._now(),
        )
        self._require_time(deadline_at)
        return result

    def _tools_for(self, capability: ReleaseCapability) -> tuple[ResponsesFunctionTool, ...]:
        return tuple(
            ResponsesFunctionTool(
                name=operation.value,
                description=TOOL_REGISTRY.get(operation).description,
                parameters=_provider_strict_schema(
                    TOOL_REGISTRY.get(operation).model_argument_schema
                ),
            )
            for operation in sorted(CAPABILITY_OPERATIONS[capability], key=lambda item: item.value)
        )

    def _require_time(self, deadline_at: datetime) -> None:
        if self._remaining_seconds(deadline_at) <= 0:
            raise ResponsesBudgetExhausted("DEADLINE_EXHAUSTED")

    def _remaining_seconds(self, deadline_at: datetime) -> float:
        now = self._now()
        if now.tzinfo is None:
            raise ResponsesContextRejected("CLOCK_MUST_BE_TIMEZONE_AWARE")
        return (deadline_at - now).total_seconds()


def _parse_final(text: str) -> ResponsesFinalEnvelope:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise ResponsesRuntimeFailure("MALFORMED_FINAL_JSON") from error
    if not isinstance(raw, dict):
        raise ResponsesRuntimeFailure("FINAL_OUTPUT_NOT_OBJECT")
    try:
        return ResponsesFinalEnvelope.model_validate(raw)
    except ValidationError as error:
        raise ResponsesRuntimeFailure("INVALID_FINAL_OUTPUT") from error


def _provider_strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Compile a strict provider hint; the original OpenAPI remains authoritative.

    OpenAI strict function schemas require every object property to be required and
    ``additionalProperties`` to be false.  Optional OpenAPI properties are made nullable,
    then explicit nulls are removed before the original bridge validation.  Unsupported
    cross-field ``allOf`` hints are omitted here only; the bridge validates them exactly.
    """

    raw = deepcopy(dict(schema))
    if "oneOf" in raw and raw.get("type") is None:
        variants = raw.get("oneOf")
        if isinstance(variants, list) and all(_is_object_schema(item) for item in variants):
            raw = _merge_root_object_union(raw, variants)
    raw.pop("allOf", None)
    if raw.get("type") == "object" and "properties" in raw:
        raw.pop("oneOf", None)
    compiled = _strict_node(raw)
    if not isinstance(compiled, dict):
        raise ResponsesRuntimeFailure("INVALID_REGISTERED_TOOL_SCHEMA")
    return compiled


def _strict_node(raw: Any) -> Any:
    if isinstance(raw, list):
        return [_strict_node(item) for item in raw]
    if not isinstance(raw, dict):
        return deepcopy(raw)
    result = {key: _strict_node(value) for key, value in raw.items() if key != "allOf"}
    if "oneOf" in result:
        result["anyOf"] = result.pop("oneOf")
    properties = result.get("properties")
    if result.get("type") == "object" or isinstance(properties, dict):
        props = properties if isinstance(properties, dict) else {}
        originally_required = set(result.get("required", []))
        strict_properties: dict[str, Any] = {}
        for name, value in props.items():
            compiled = _strict_node(value)
            if name not in originally_required:
                compiled = _nullable_schema(compiled)
            strict_properties[name] = compiled
        result["type"] = "object"
        result["properties"] = strict_properties
        result["required"] = list(strict_properties)
        result["additionalProperties"] = False
    return result


def _merge_root_object_union(raw: Mapping[str, Any], variants: Sequence[Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    presence: dict[str, int] = {}
    required_count: dict[str, int] = {}
    for variant in variants:
        if not isinstance(variant, Mapping):
            continue
        variant_properties = variant.get("properties", {})
        required = set(variant.get("required", []))
        if not isinstance(variant_properties, Mapping):
            continue
        for name, value in variant_properties.items():
            presence[name] = presence.get(name, 0) + 1
            if name in required:
                required_count[name] = required_count.get(name, 0) + 1
            if name not in properties:
                properties[name] = deepcopy(value)
            elif properties[name] != value:
                properties[name] = {"anyOf": [properties[name], deepcopy(value)]}
    result = {
        key: deepcopy(value)
        for key, value in raw.items()
        if key not in {"oneOf", "type", "properties", "required", "additionalProperties"}
    }
    result.update(
        {
            "type": "object",
            "properties": properties,
            "required": [
                name
                for name in properties
                if presence.get(name) == len(variants) and required_count.get(name) == len(variants)
            ],
            "additionalProperties": False,
        }
    )
    return result


def _nullable_schema(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return {"anyOf": [deepcopy(schema), {"type": "null"}]}
    raw_type = schema.get("type")
    if raw_type == "null" or (isinstance(raw_type, list) and "null" in raw_type):
        return schema
    if isinstance(raw_type, str):
        result = deepcopy(schema)
        result["type"] = [raw_type, "null"]
        return result
    return {"anyOf": [deepcopy(schema), {"type": "null"}]}


def _is_object_schema(value: Any) -> bool:
    return isinstance(value, Mapping) and (
        value.get("type") == "object" or isinstance(value.get("properties"), Mapping)
    )


def _drop_null_object_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_null_object_fields(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, list):
        return [_drop_null_object_fields(item) for item in value]
    return value


def _intent_model_limit(capability: ReleaseCapability) -> int:
    if capability in {
        ReleaseCapability.PUBLIC_FAQ,
        ReleaseCapability.LIST_PRICE_INFO,
        ReleaseCapability.ORDER_STATUS,
    }:
        return 1
    if capability is ReleaseCapability.INTERNAL_SHADOW:
        return 3
    return 2


def _token_cost(
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    input_rate: Decimal,
    cached_input_rate: Decimal,
    output_rate: Decimal,
) -> Decimal:
    million = Decimal(1_000_000)
    uncached_input_tokens = input_tokens - cached_input_tokens
    if uncached_input_tokens < 0:
        raise ValueError("cached input token count exceeds total input token count")
    return (
        Decimal(uncached_input_tokens) * input_rate
        + Decimal(cached_input_tokens) * cached_input_rate
        + Decimal(output_tokens) * output_rate
    ) / million


def _money(value: Decimal) -> str:
    rendered = format(value.quantize(Decimal("0.000000000001")), "f").rstrip("0").rstrip(".")
    return rendered or "0"


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def _sha256_text(value: str) -> str:
    return f"sha256:{sha256(value.encode()).hexdigest()}"


def _constant_text_equal(left: str, right: str) -> bool:
    from hmac import compare_digest

    return compare_digest(left.encode(), right.encode())


def _controlled_code(error: BaseException, fallback: str) -> str:
    message = str(error).strip()
    if (
        message
        and len(message) <= 64
        and all(
            character.isupper() or character.isdigit() or character == "_" for character in message
        )
    ):
        return message
    return fallback


def _bridge_code(error: AgentToolBridgeRejected) -> str:
    message = str(error)
    prefix = message.split(":", 1)[0].strip()
    normalized = "".join(character if character.isalnum() else "_" for character in prefix.upper())
    return normalized[:64] if len(normalized) >= 3 else "TOOL_BRIDGE_REJECTED"


__all__ = [
    "CURRENT_TOOL_CONTRACT_HASH",
    "DETERMINISTIC_HANDOFF_TEXT",
    "BoundResponsesContextLoader",
    "BoundedResponsesRuntime",
    "RecordingResponsesEvidenceSink",
    "ResponsesConnectionFailure",
    "ResponsesContextLoader",
    "ResponsesContextRejected",
    "ResponsesEvidencePersistenceError",
    "ResponsesEvidenceSink",
    "ResponsesFunctionCall",
    "ResponsesOutcomeAmbiguous",
    "ResponsesPriceTable",
    "ResponsesProviderResponse",
    "ResponsesProviderTransport",
    "ResponsesRequest",
    "ResponsesRequestCancelled",
    "ResponsesRuntimeConfig",
    "ResponsesRuntimeContext",
    "ResponsesRuntimeEvidence",
    "ResponsesRuntimeFailure",
    "ResponsesTransportTimeout",
    "ResponsesUsage",
    "ScriptedResponsesTransport",
]
