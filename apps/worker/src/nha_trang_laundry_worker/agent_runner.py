"""Fail-closed Agent Runner and in-memory bridge for the isolated public cell.

The model only supplies a registered operation and strict arguments.  This module owns
the contact binding, path identifiers, version, short-lived facade JWT, budgets, and
the non-delivery Shadow result.  It deliberately has no channel/provider send client.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import jwt
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    AgentRunnerClaims,
    AgentToolOperation,
    AgentToolSideEffect,
    ReleaseCapability,
    ToolArgumentsInvalid,
    VerifiedReleaseAuthorization,
    load_agent_tool_registry,
    load_public_runtime_registry,
    operation_is_authorized,
)
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ROOT = Path(__file__).resolve().parents[4]
TOOL_REGISTRY = load_agent_tool_registry(ROOT / "specs/contracts/agent-tools-v1.openapi.yaml")

IdempotencyKey = Annotated[str, StringConstraints(min_length=16, max_length=128)]


class AgentRunnerError(RuntimeError):
    """Base class for a controlled agent-run refusal."""


class AgentRunRejected(AgentRunnerError):
    """The server-owned job is unsafe for the current candidate runtime."""


class AgentRuntimeTimeout(AgentRunRejected):
    """The runtime crossed its hard deadline; the durable job needs human recovery."""


class AgentToolBridgeRejected(AgentRunnerError):
    """A public-cell tool attempt failed closed before or at the facade boundary."""


class ProviderRuntimeBlocked(AgentRunnerError):
    """A provider-backed runtime cannot be invoked until release gates are satisfied."""


@dataclass(frozen=True, slots=True)
class AgentIntentBudget:
    """Server-selected budget for one declared capability/intention."""

    max_tool_calls: int
    max_tool_mutations: int


INTENT_BUDGETS: Mapping[ReleaseCapability, AgentIntentBudget] = {
    ReleaseCapability.INTERNAL_SHADOW: AgentIntentBudget(6, 1),
    ReleaseCapability.PUBLIC_FAQ: AgentIntentBudget(2, 0),
    ReleaseCapability.LIST_PRICE_INFO: AgentIntentBudget(2, 0),
    ReleaseCapability.INTAKE_QUESTION: AgentIntentBudget(4, 1),
    ReleaseCapability.INTAKE_FACT_CAPTURE: AgentIntentBudget(4, 1),
    ReleaseCapability.INTAKE_RECEIPT: AgentIntentBudget(4, 1),
    ReleaseCapability.INCIDENT_RECEIPT: AgentIntentBudget(3, 1),
    ReleaseCapability.ORDER_STATUS: AgentIntentBudget(2, 0),
    ReleaseCapability.SLA_GUIDANCE: AgentIntentBudget(4, 1),
    ReleaseCapability.QUOTE_ESTIMATE: AgentIntentBudget(4, 1),
    ReleaseCapability.BOOKING: AgentIntentBudget(4, 1),
    ReleaseCapability.DELIVERY_ADVISORY: AgentIntentBudget(4, 1),
    ReleaseCapability.MARKETING_FOLLOWUP: AgentIntentBudget(0, 0),
}


@dataclass(frozen=True, slots=True)
class AgentRunJob:
    """Contact-bound input constructed by the control plane, never by model arguments."""

    run_id: UUID
    organization_id: UUID
    store_id: UUID
    channel: str
    conversation_binding_id: UUID
    contact_binding_id: UUID
    capability: ReleaseCapability
    stage: AgentDeploymentStage
    data_classification: AgentDataClassification
    started_at: datetime
    deadline_at: datetime
    order_request_id: UUID | None = None
    public_code: str | None = None
    row_version: int = 0
    binding_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.started_at.tzinfo is None or self.deadline_at.tzinfo is None:
            raise ValueError("agent run timestamps must be timezone-aware")
        duration = self.deadline_at - self.started_at
        if duration <= timedelta() or duration > timedelta(seconds=20):
            raise ValueError("agent run deadline must be between 1 and 20 seconds")
        if (
            not self.channel
            or len(self.channel) > 50
            or not self.channel.replace("_", "").isalnum()
        ):
            raise ValueError("agent run channel must be a bounded uppercase identifier")
        if self.channel != self.channel.upper():
            raise ValueError("agent run channel must be uppercase")
        if self.row_version < 0:
            raise ValueError("agent run row version must be non-negative")
        if self.public_code is not None and not 16 <= len(self.public_code) <= 64:
            raise ValueError("public code must be between 16 and 64 characters")

    @property
    def budget(self) -> AgentIntentBudget:
        return INTENT_BUDGETS[self.capability]

    def session_key(self) -> str:
        request_id = str(self.order_request_id) if self.order_request_id is not None else "-"
        public_code = self.public_code if self.public_code is not None else "-"
        return f"laundry-public:{self.binding_id}:{request_id}:{self.row_version}:{public_code}"


@dataclass(frozen=True, slots=True)
class AgentToolForwardRequest:
    """The only private request shape emitted from the bridge to the Tool Facade."""

    method: Literal["GET", "POST"]
    path: str
    headers: Mapping[str, str]
    body: Mapping[str, Any]
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class AgentToolForwardResponse:
    status_code: int
    headers: Mapping[str, str]
    body: Mapping[str, Any]


class AgentToolTransport(Protocol):
    """Private-only, fixed-tool transport. It has no generic URL parameter."""

    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse: ...


class AgentToolCallObserver(Protocol):
    """Receives safe, server-owned tool metadata after a validated facade response."""

    def record(
        self,
        *,
        sequence_number: int,
        operation: AgentToolOperation,
        arguments: Mapping[str, Any],
        result: AgentToolBridgeResult,
        started_at: datetime,
        completed_at: datetime,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentToolBridgeResult:
    status_code: int
    body: Mapping[str, Any]
    trace_id: str | None
    response_headers: Mapping[str, str]


class AgentRunnerTokenIssuer:
    """Mint an Ed25519 runner bearer for exactly one bound facade invocation window."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        private_key: str,
        lifetime_seconds: int = 20,
    ) -> None:
        if not issuer or not audience or not private_key:
            raise ValueError("runner token issuer, audience, and private key are required")
        if not 1 <= lifetime_seconds <= 60:
            raise ValueError("runner bearer lifetime must be between 1 and 60 seconds")
        self._issuer = issuer
        self._audience = audience
        self._private_key = private_key
        self._lifetime_seconds = lifetime_seconds

    def issue(
        self,
        job: AgentRunJob,
        *,
        now: datetime,
        order_request_id: UUID | None,
        public_code: str | None,
    ) -> str:
        expires_at = min(now + timedelta(seconds=self._lifetime_seconds), job.deadline_at)
        iat = int(now.timestamp())
        exp = int(expires_at.timestamp())
        if exp <= iat:
            raise AgentToolBridgeRejected("TOOL_UNAVAILABLE: runner bearer lifetime exhausted")
        claims = AgentRunnerClaims(
            iss=self._issuer,
            aud=self._audience,
            sub="AGENT_RUNNER",
            iat=iat,
            exp=exp,
            jti=uuid4(),
            run_id=job.run_id,
            organization_id=job.organization_id,
            store_id=job.store_id,
            channel=job.channel,
            conversation_binding_id=job.conversation_binding_id,
            contact_binding_id=job.contact_binding_id,
            capabilities=(job.capability,),
            stage=job.stage,
            data_classification=job.data_classification,
            order_request_id=order_request_id,
            public_code=public_code,
        )
        return jwt.encode(claims.model_dump(mode="json"), self._private_key, algorithm="EdDSA")


class AgentToolBridgeSession:
    """Ephemeral, contact-bound bridge state shared only with one public-cell executor."""

    def __init__(
        self,
        *,
        job: AgentRunJob,
        bridge_token: str,
        issuer: AgentRunnerTokenIssuer,
        transport: AgentToolTransport,
        observer: AgentToolCallObserver | None = None,
    ) -> None:
        if len(bridge_token) < 32:
            raise ValueError("bridge token must have at least 32 characters")
        self._job = job
        self._bridge_token = bridge_token
        self._issuer = issuer
        self._transport = transport
        self._observer = observer
        self._order_request_id = job.order_request_id
        self._public_code = job.public_code
        self._row_version = job.row_version
        self._tool_call_count = 0
        self._mutation_count = 0
        self._operation_counts: Counter[AgentToolOperation] = Counter()
        self._idempotent_results: dict[str, tuple[str, AgentToolBridgeResult]] = {}
        self._closed = False

    @property
    def bridge_token(self) -> str:
        """Secret for the trusted executor only; never include it in model-visible context."""

        return self._bridge_token

    @property
    def session_key(self) -> str:
        request_id = str(self._order_request_id) if self._order_request_id is not None else "-"
        public_code = self._public_code if self._public_code is not None else "-"
        return (
            f"laundry-public:{self._job.binding_id}:{request_id}:{self._row_version}:{public_code}"
        )

    @property
    def tool_call_count(self) -> int:
        return self._tool_call_count

    @property
    def binding_id(self) -> UUID:
        return self._job.binding_id

    def expected_path_parameters(self, operation: AgentToolOperation) -> dict[str, str]:
        contract = TOOL_REGISTRY.get(operation)
        result: dict[str, str] = {}
        for parameter in contract.path_parameters:
            if parameter == "order_request_id":
                if self._order_request_id is None:
                    raise AgentToolBridgeRejected("MISSING_REQUIRED_FACT: no bound order request")
                result[parameter] = str(self._order_request_id)
            elif parameter == "public_code":
                if self._public_code is None:
                    raise AgentToolBridgeRejected(
                        "MISSING_REQUIRED_FACT: no bound public order code"
                    )
                result[parameter] = self._public_code
            else:
                raise AgentToolBridgeRejected("TOOL_UNAVAILABLE: unsupported bound path parameter")
        return result

    def invoke(
        self,
        operation: AgentToolOperation,
        arguments: Mapping[str, Any],
        *,
        bridge_token: str,
        binding_id: UUID,
        route_path_parameters: Mapping[str, str],
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> AgentToolBridgeResult:
        """Authenticate a loopback tool call and forward only the server-derived route."""

        current_time = now or datetime.now(UTC)
        if self._closed:
            raise AgentToolBridgeRejected("TOOL_UNAVAILABLE: agent bridge is closed")
        self._verify_executor(bridge_token, binding_id, current_time)
        contract = TOOL_REGISTRY.get(operation)
        claims = self._claims_for_authorization(current_time)
        if not operation_is_authorized(claims, operation):
            raise AgentToolBridgeRejected("POLICY_DENIED: capability does not authorize operation")
        expected_path = self.expected_path_parameters(operation)
        if dict(route_path_parameters) != expected_path:
            raise AgentToolBridgeRejected("POLICY_DENIED: cross-bound path is rejected")
        try:
            validated = contract.validate_model_arguments(arguments)
        except ToolArgumentsInvalid as error:
            raise AgentToolBridgeRejected(str(error)) from error
        logical_key, fingerprint = self._logical_key_and_fingerprint(
            operation, validated, idempotency_key
        )
        if logical_key is not None:
            existing = self._idempotent_results.get(logical_key)
            if existing is not None:
                if not hmac.compare_digest(existing[0], fingerprint):
                    raise AgentToolBridgeRejected("IDEMPOTENCY_CONFLICT: tool call payload changed")
                return existing[1]
        self._consume_budget(operation)
        started_at = current_time
        request = self._build_request(
            contract, validated, expected_path, idempotency_key, current_time
        )
        response = self._transport.send(request)
        result = self._validated_result(contract, response)
        self._update_bound_state(operation, result, response.headers)
        if self._observer is not None:
            self._observer.record(
                sequence_number=self._tool_call_count,
                operation=operation,
                arguments=validated,
                result=result,
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )
        if logical_key is not None:
            self._idempotent_results[logical_key] = (fingerprint, result)
        return result

    def close(self) -> None:
        """Irrevocably revoke this in-memory bridge after timeout or controlled failure."""

        self._closed = True

    def _verify_executor(self, bridge_token: str, binding_id: UUID, now: datetime) -> None:
        if now > self._job.deadline_at:
            raise AgentToolBridgeRejected("TOOL_UNAVAILABLE: agent run deadline exceeded")
        if binding_id != self._job.binding_id:
            raise AgentToolBridgeRejected("POLICY_DENIED: wrong run binding")
        if not hmac.compare_digest(bridge_token, self._bridge_token):
            raise AgentToolBridgeRejected("POLICY_DENIED: bridge credential rejected")

    def _claims_for_authorization(self, now: datetime) -> AgentRunnerClaims:
        iat = int(now.timestamp())
        exp = min(iat + 20, int(self._job.deadline_at.timestamp()))
        if exp <= iat:
            raise AgentToolBridgeRejected("TOOL_UNAVAILABLE: agent run deadline exceeded")
        return AgentRunnerClaims(
            iss="agent-runner-local",
            aud="agent-tool-facade",
            sub="AGENT_RUNNER",
            iat=iat,
            exp=exp,
            jti=uuid4(),
            run_id=self._job.run_id,
            organization_id=self._job.organization_id,
            store_id=self._job.store_id,
            channel=self._job.channel,
            conversation_binding_id=self._job.conversation_binding_id,
            contact_binding_id=self._job.contact_binding_id,
            capabilities=(self._job.capability,),
            stage=self._job.stage,
            data_classification=self._job.data_classification,
            order_request_id=self._order_request_id,
            public_code=self._public_code,
        )

    def _logical_key_and_fingerprint(
        self,
        operation: AgentToolOperation,
        arguments: Mapping[str, Any],
        idempotency_key: str | None,
    ) -> tuple[str | None, str]:
        contract = TOOL_REGISTRY.get(operation)
        requires_idempotency = "Idempotency-Key" in contract.header_parameters
        if requires_idempotency:
            if idempotency_key is None or not 16 <= len(idempotency_key) <= 128:
                raise AgentToolBridgeRejected("VALIDATION_ERROR: idempotency key is required")
            if not all(character.isalnum() or character in "._:-" for character in idempotency_key):
                raise AgentToolBridgeRejected("VALIDATION_ERROR: idempotency key is invalid")
        elif idempotency_key is not None:
            raise AgentToolBridgeRejected(
                "VALIDATION_ERROR: idempotency is not registered for operation"
            )
        stable_payload = json.dumps(
            arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        fingerprint = sha256(f"{operation.value}\0{stable_payload}".encode()).hexdigest()
        if idempotency_key is None:
            return None, fingerprint
        return f"{operation.value}:{idempotency_key}", fingerprint

    def _consume_budget(self, operation: AgentToolOperation) -> None:
        contract = TOOL_REGISTRY.get(operation)
        if self._tool_call_count >= self._job.budget.max_tool_calls:
            raise AgentToolBridgeRejected("REQUIRE_HUMAN: tool-call budget exhausted")
        if self._operation_counts[operation] >= contract.max_calls_per_run:
            raise AgentToolBridgeRejected("REQUIRE_HUMAN: operation call budget exhausted")
        is_mutation = contract.side_effect is AgentToolSideEffect.REVERSIBLE_WRITE
        if is_mutation and self._mutation_count >= self._job.budget.max_tool_mutations:
            raise AgentToolBridgeRejected("REQUIRE_HUMAN: tool mutation budget exhausted")
        self._tool_call_count += 1
        self._operation_counts[operation] += 1
        if is_mutation:
            self._mutation_count += 1

    def _build_request(
        self,
        contract: Any,
        arguments: Mapping[str, Any],
        path_parameters: Mapping[str, str],
        idempotency_key: str | None,
        now: datetime,
    ) -> AgentToolForwardRequest:
        path = contract.path
        for name, value in path_parameters.items():
            path = path.replace("{" + name + "}", quote(value, safe=""))
        if "{" in path:
            raise AgentToolBridgeRejected("TOOL_UNAVAILABLE: unresolved server path binding")
        bearer = self._issuer.issue(
            self._job,
            now=now,
            order_request_id=self._order_request_id,
            public_code=self._public_code,
        )
        headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}
        if "Idempotency-Key" in contract.header_parameters:
            if idempotency_key is None:
                raise AgentToolBridgeRejected("VALIDATION_ERROR: idempotency key is required")
            digest = sha256(
                f"{self._job.run_id}\0{contract.operation.value}\0{idempotency_key}".encode()
            ).hexdigest()
            headers["Idempotency-Key"] = f"agt-{digest[:48]}"
        if "If-Match" in contract.header_parameters:
            if self._row_version < 1:
                raise AgentToolBridgeRejected(
                    "STALE_VERSION: bound aggregate has no mutable version"
                )
            headers["If-Match"] = f'"{self._row_version}"'
        return AgentToolForwardRequest(
            method=contract.method,
            path=path,
            headers=headers,
            body=arguments,
            timeout_seconds=3,
        )

    def _validated_result(
        self, contract: Any, response: AgentToolForwardResponse
    ) -> AgentToolBridgeResult:
        body = response.body
        try:
            if response.status_code == contract.success_status:
                validated = contract.validate_success_response(body)
            elif 400 <= response.status_code <= 599:
                validated = contract.validate_error_response(body)
            else:
                raise AgentToolBridgeRejected("TOOL_UNAVAILABLE: facade returned invalid status")
        except ToolArgumentsInvalid as error:
            raise AgentToolBridgeRejected("TOOL_UNAVAILABLE: invalid facade response") from error
        trace_id = validated.get("trace_id")
        return AgentToolBridgeResult(
            status_code=response.status_code,
            body=validated,
            trace_id=trace_id if isinstance(trace_id, str) else None,
            response_headers=dict(response.headers),
        )

    def _update_bound_state(
        self,
        operation: AgentToolOperation,
        result: AgentToolBridgeResult,
        headers: Mapping[str, str],
    ) -> None:
        if not 200 <= result.status_code < 300:
            return
        data = result.body.get("data")
        if not isinstance(data, Mapping):
            return
        if operation is AgentToolOperation.ORDER_REQUEST_CREATE:
            raw_request_id = data.get("order_request_id")
            if not isinstance(raw_request_id, str):
                raise AgentToolBridgeRejected(
                    "TOOL_UNAVAILABLE: create response missing bound request"
                )
            try:
                self._order_request_id = UUID(raw_request_id)
            except ValueError as error:
                raise AgentToolBridgeRejected(
                    "TOOL_UNAVAILABLE: invalid created request"
                ) from error
        raw_version = data.get("row_version")
        if isinstance(raw_version, int) and not isinstance(raw_version, bool) and raw_version >= 1:
            self._row_version = raw_version
            return
        etag = headers.get("ETag") or headers.get("etag")
        if etag is not None and len(etag) >= 3 and etag.startswith('"') and etag.endswith('"'):
            value = etag[1:-1]
            if value.isdigit() and int(value) >= 1:
                self._row_version = int(value)


class AgentRuntimeOutput(BaseModel):
    """Only a customer-visible draft and bounded usage may leave the public runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    model_calls: int = Field(ge=0, le=3)
    input_tokens: int = Field(default=0, ge=0, le=8000)
    output_tokens: int = Field(default=0, ge=0, le=1200)


@dataclass(frozen=True, slots=True)
class AgentRuntimeInvocation:
    run_id: UUID
    capability: ReleaseCapability
    session_key: str
    bridge_token: str


class ConstrainedAgentRuntime(Protocol):
    provider_backed: bool

    def invoke(
        self, invocation: AgentRuntimeInvocation, bridge: AgentToolBridgeSession
    ) -> AgentRuntimeOutput: ...


@dataclass(frozen=True, slots=True)
class ScriptedToolCall:
    operation: AgentToolOperation
    arguments: Mapping[str, Any]
    idempotency_key: IdempotencyKey | None = None


class SyntheticScriptedRuntime:
    """Deterministic synthetic runtime for integration/eval paths; never a provider client."""

    provider_backed = False

    def __init__(self, *, draft_text: str, tool_calls: Sequence[ScriptedToolCall] = ()) -> None:
        self._draft_text = draft_text
        self._tool_calls = tuple(tool_calls)

    def invoke(
        self, invocation: AgentRuntimeInvocation, bridge: AgentToolBridgeSession
    ) -> AgentRuntimeOutput:
        for call in self._tool_calls:
            bridge.invoke(
                call.operation,
                call.arguments,
                bridge_token=invocation.bridge_token,
                binding_id=UUID(invocation.session_key.split(":")[1]),
                route_path_parameters=bridge.expected_path_parameters(call.operation),
                idempotency_key=call.idempotency_key,
            )
        return AgentRuntimeOutput(draft_text=self._draft_text, model_calls=0)


class DisabledOpenClawProviderRuntime:
    """Explicit provider path retained as a release-gated refusal, never an implicit fallback."""

    provider_backed = True

    def __init__(
        self, runtime_registry_path: Path = ROOT / "runtime/model-registry-v1.yaml"
    ) -> None:
        self._registry = load_public_runtime_registry(runtime_registry_path)

    def invoke(
        self, invocation: AgentRuntimeInvocation, bridge: AgentToolBridgeSession
    ) -> AgentRuntimeOutput:
        del invocation, bridge
        blockers = self._registry.release_blockers()
        if blockers:
            raise ProviderRuntimeBlocked(";".join(blockers))
        raise ProviderRuntimeBlocked("OPENCLAW_EXECUTOR_NOT_CONFIGURED")


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    run_id: UUID
    status: Literal["DRAFT_REQUIRES_HUMAN", "REQUIRE_HUMAN"]
    draft_text: str
    tool_call_count: int
    automatic_send_authorized: Literal[False] = False


class AgentRunner:
    """Control-plane coordinator for one bounded draft-only agent run."""

    def __init__(
        self,
        issuer: AgentRunnerTokenIssuer,
        *,
        release_authorization: VerifiedReleaseAuthorization | None = None,
        deployed_commit_sha: str | None = None,
    ) -> None:
        if (release_authorization is None) != (deployed_commit_sha is None):
            raise ValueError(
                "release authorization and deployed commit SHA must be configured together"
            )
        self._issuer = issuer
        self._registry = load_public_runtime_registry(ROOT / "runtime/model-registry-v1.yaml")
        self._release_authorization = release_authorization
        self._deployed_commit_sha = deployed_commit_sha

    def execute(
        self,
        *,
        job: AgentRunJob,
        runtime: ConstrainedAgentRuntime,
        transport: AgentToolTransport,
        bridge_token: str | None = None,
        observer: AgentToolCallObserver | None = None,
        now: datetime | None = None,
    ) -> AgentRunResult:
        current_time = now or datetime.now(UTC)
        self._validate_job(job, current_time, runtime)
        token = bridge_token or secrets.token_urlsafe(32)
        bridge = AgentToolBridgeSession(
            job=job,
            bridge_token=token,
            issuer=self._issuer,
            transport=transport,
            observer=observer,
        )
        invocation = AgentRuntimeInvocation(
            run_id=job.run_id,
            capability=job.capability,
            session_key=bridge.session_key,
            bridge_token=token,
        )
        output = self._invoke_with_deadline(runtime, invocation, bridge, job.deadline_at)
        if output.model_calls > self._registry.limits.max_model_calls:
            raise AgentRunRejected("REQUIRE_HUMAN: model-call budget exhausted")
        return AgentRunResult(
            run_id=job.run_id,
            status="DRAFT_REQUIRES_HUMAN",
            draft_text=output.draft_text,
            tool_call_count=bridge.tool_call_count,
        )

    def _invoke_with_deadline(
        self,
        runtime: ConstrainedAgentRuntime,
        invocation: AgentRuntimeInvocation,
        bridge: AgentToolBridgeSession,
        deadline_at: datetime,
    ) -> AgentRuntimeOutput:
        """Bound a synchronous runtime without letting a late call retain tool authority.

        The production executor must set its own transport timeout as well.  This control-plane
        guard returns at the declared deadline and revokes the bridge first, so a late or stuck
        in-process runtime cannot issue a post-deadline tool call.
        """

        result: list[AgentRuntimeOutput] = []
        errors: list[BaseException] = []

        def invoke() -> None:
            try:
                result.append(runtime.invoke(invocation, bridge))
            except BaseException as error:  # Propagate controlled runtime errors unchanged.
                errors.append(error)

        remaining_seconds = (deadline_at - datetime.now(UTC)).total_seconds()
        if remaining_seconds <= 0:
            bridge.close()
            raise AgentRuntimeTimeout("MODEL_TIMEOUT: agent run deadline exceeded")
        execution = threading.Thread(target=invoke, daemon=True, name="agent-runtime-invocation")
        execution.start()
        execution.join(timeout=remaining_seconds)
        if execution.is_alive():
            bridge.close()
            raise AgentRuntimeTimeout("MODEL_TIMEOUT: agent runtime deadline exceeded")
        if errors:
            error = errors[0]
            if isinstance(error, AgentRunnerError):
                raise error
            raise AgentRunRejected("TOOL_UNAVAILABLE: agent runtime invocation failed") from error
        if len(result) != 1:
            raise AgentRunRejected("TOOL_UNAVAILABLE: agent runtime returned no result")
        return result[0]

    def _validate_job(
        self, job: AgentRunJob, now: datetime, runtime: ConstrainedAgentRuntime
    ) -> None:
        if now > job.deadline_at:
            raise AgentRunRejected("TOOL_UNAVAILABLE: agent run deadline exceeded")
        if (
            job.data_classification is AgentDataClassification.REAL_CUSTOMER
            and not self._registry.activation.real_customer_model_calls_enabled
        ):
            raise AgentRunRejected("POLICY_DENIED: real-customer processing is disabled")
        if job.stage is not AgentDeploymentStage.SHADOW:
            raise AgentRunRejected("POLICY_DENIED: non-shadow agent stages are disabled")
        if runtime.provider_backed:
            if self._registry.release_blockers():
                raise AgentRunRejected(
                    "POLICY_DENIED: provider runtime release gates are incomplete"
                )
            authorization = self._release_authorization
            commit_sha = self._deployed_commit_sha
            if (
                authorization is None
                or commit_sha is None
                or not authorization.authorizes(
                    commit_sha=commit_sha,
                    stage=job.stage.value,
                    capability=job.capability,
                    now=now,
                )
            ):
                raise AgentRunRejected(
                    "POLICY_DENIED: signed release manifest authorization is missing or invalid"
                )


__all__ = [
    "INTENT_BUDGETS",
    "AgentIntentBudget",
    "AgentRunJob",
    "AgentRunRejected",
    "AgentRunResult",
    "AgentRunner",
    "AgentRunnerError",
    "AgentRunnerTokenIssuer",
    "AgentRuntimeInvocation",
    "AgentRuntimeOutput",
    "AgentRuntimeTimeout",
    "AgentToolBridgeRejected",
    "AgentToolBridgeResult",
    "AgentToolBridgeSession",
    "AgentToolCallObserver",
    "AgentToolForwardRequest",
    "AgentToolForwardResponse",
    "AgentToolTransport",
    "ConstrainedAgentRuntime",
    "DisabledOpenClawProviderRuntime",
    "ProviderRuntimeBlocked",
    "ScriptedToolCall",
    "SyntheticScriptedRuntime",
]
