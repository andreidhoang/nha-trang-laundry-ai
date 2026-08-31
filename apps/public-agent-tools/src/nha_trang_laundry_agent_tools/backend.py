"""Domain-backed deterministic adapter for the fixed Agent Tool Facade.

`TOOL-BACKEND-001`. This module is the boundary between a model and the business, and it is
deliberately thin: it computes nothing. Every price, fee, capacity and policy value below is
produced by a `packages/domain` engine or carried by a `packages/db` repository, and reaches the
response verbatim — including the engines' `REQUIRE_HUMAN` and unresolved outcomes. Where no
honest backing exists (message drafts, public order status, incident intake for an
order-request-bound tool), the operation returns typed unavailability rather than a fabricated
success.

Assembling this backend is not enabling it. `facade.get_agent_facade_service()` keeps returning
the unavailable backend; a deployment opts in by overriding that dependency with the result of
`build_domain_backend`, exactly as `AGENT-PIPELINE-001` established for the worker pipeline.
"""

from __future__ import annotations

import hmac
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

import psycopg
from nha_trang_laundry_contracts import AgentToolOperation
from nha_trang_laundry_db.approvals import ApprovalRepository, ApprovalRequestCommand
from nha_trang_laundry_db.configurations import ConfigurationRepository, snapshot_hash
from nha_trang_laundry_db.idempotency import (
    IdempotencyConflictError,
    IdempotencyRepository,
    IdempotentCommand,
)
from nha_trang_laundry_db.intake import (
    CreateOrderRequestCommand,
    OrderRequestBindingError,
    OrderRequestRepository,
    OrderRequestStateError,
    RecordCustomerFactsCommand,
    StoredOrderRequest,
)
from nha_trang_laundry_db.quotes import (
    QuoteRepository,
    QuoteRevisionCommand,
    QuoteStateError,
)
from nha_trang_laundry_domain.approvals import (
    APPROVAL_POLICIES,
    APPROVAL_RESOURCE_TYPES,
    ApprovalEnvelopeError,
)
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.capacity import evaluate_r1_capacity
from nha_trang_laundry_domain.catalog import (
    ActorRole,
    ApprovalAction,
    CatalogError,
    FulfillmentMode,
    PolicyOutcome,
    QuantityBasis,
    ServiceDefinition,
    ServiceRegistry,
    Unit,
    standard_wash_aliases,
)
from nha_trang_laundry_domain.delivery import (
    DeliveryError,
    DeliveryResult,
    evaluate_delivery,
)
from nha_trang_laundry_domain.pricebook_import import (
    PricebookImportError,
    published_price_rules,
)
from nha_trang_laundry_domain.pricing import PriceRule
from nha_trang_laundry_domain.quote_composition import (
    PricebookProvenance,
    RequestedLine,
    UnresolvedQuote,
    compose_quote_revision,
)
from nha_trang_laundry_domain.quotes import ImmutableQuoteSnapshot
from nha_trang_laundry_policy import PolicyDecision, PolicyDecisionPoint

from nha_trang_laundry_agent_tools.auth import AgentAuthorizationError
from nha_trang_laundry_agent_tools.facade import (
    AgentToolBackend,
    AgentToolCall,
    AgentToolRefusal,
    AgentToolUnavailable,
    UnavailableAgentToolBackend,
)

_DIGEST = re.compile(r"[0-9a-f]{64}")
# The tool contract spells digests `sha256:<hex>`; the canonical snapshot machinery spells the
# same SHA-256 digest `JCS-SHA256-V1:<hex>`. The conversion re-labels one real digest; it never
# computes a new one.
_JCS_PREFIX = "JCS-SHA256-V1:"
_SERVICE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,62}$")
# A substring shorter than this matches nearly every service code and proves nothing.
_MIN_SUBSTRING_QUERY = 3

# Quote revisions are keyed (quote_id, revision) — the store has no surrogate revision UUID. The
# contract requires one, so it is derived the same way the domain derives service-version
# identity (quote_composition._service_version_id): deterministically, from the real key.
QUOTE_REVISION_ID_NAMESPACE: UUID = UUID("3f6c2f7a-5e1d-4c8b-9a0e-2d4f6a8c0e1f")

# Actions whose resource the backend can verify against a real store. Everything else fails
# closed: SLOT_PROPOSAL, DELIVERY_FEE_PROPOSAL and MESSAGE_DRAFT have no tables to verify
# against, and ACCEPT_ORDER binds ORDER_REQUEST here while the domain envelope binds ORDER —
# honouring it would require inventing an order that does not exist.
_VERIFIABLE_APPROVAL_ACTIONS = frozenset(
    {
        ApprovalAction.PRESENT_QUOTE,
        ApprovalAction.SET_RANGE_PRICE,
        ApprovalAction.APPLY_PROMOTION,
    }
)

_ToolHandler = Callable[[AgentToolCall], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class _PublishedPricebook:
    rules: dict[str, PriceRule]
    provenance: PricebookProvenance
    payload: Mapping[str, Any]


class DomainAgentToolBackend:
    """Dispatch each validated tool call to the domain engines and repositories."""

    def __init__(
        self,
        *,
        database_url: str,
        connection_factory: Callable[[str], Any] = psycopg.connect,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not database_url:
            raise AgentToolUnavailable("domain backend requires an explicit database URL")
        self._database_url = database_url
        self._connection_factory = connection_factory
        self._now = now or (lambda: datetime.now(UTC))
        self._idempotency = IdempotencyRepository()
        self._dispatch: Mapping[AgentToolOperation, _ToolHandler] = {
            AgentToolOperation.CATALOG_RESOLVE: self._catalog_resolve,
            AgentToolOperation.ORDER_REQUEST_CREATE: self._order_request_create,
            AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS: self._record_customer_facts,
            AgentToolOperation.QUOTE_ESTIMATE: self._quote_estimate,
            AgentToolOperation.DELIVERY_EVALUATE: self._delivery_evaluate,
            AgentToolOperation.CAPACITY_CHECK: self._capacity_check,
            AgentToolOperation.MESSAGE_DRAFT_CREATE: self._message_draft_create,
            AgentToolOperation.PUBLIC_ORDER_STATUS_GET: self._public_order_status_get,
            AgentToolOperation.INCIDENT_OPEN: self._incident_open,
            AgentToolOperation.APPROVAL_REQUEST_CREATE: self._approval_request_create,
        }

    def invoke(self, call: AgentToolCall) -> Mapping[str, Any]:
        handler = self._dispatch.get(call.operation)
        if handler is None:  # The registry guarantees coverage; refuse rather than improvise.
            raise AgentToolUnavailable(f"no deterministic adapter for {call.operation.value}")
        return handler(call)

    # --- shared pieces -----------------------------------------------------

    def _connect(self) -> Any:
        return self._connection_factory(self._database_url)

    def _policy_decision(self, call: AgentToolCall) -> PolicyDecision:
        """The real policy evaluation governing this boundary, not an invented version string."""
        decision = PolicyDecisionPoint().evaluate_synthetic_tool(call.claims, call.operation)
        if decision.policy_version is None:
            raise AgentToolUnavailable("the policy point returned no policy version")
        return decision

    def _policy_version(self, call: AgentToolCall) -> str:
        version = self._policy_decision(call).policy_version
        if version is None:  # `_policy_decision` raises first; this narrows for the type checker.
            raise AgentToolUnavailable("the policy point returned no policy version")
        return version

    def _published_pricebook(self, cursor: Any) -> _PublishedPricebook:
        """Resolve the one published pricebook, or refuse — mirrors OperationsService."""
        published = ConfigurationRepository.latest_published(cursor, "PRICEBOOK")
        if published is None:
            raise AgentToolUnavailable("no published pricebook")
        payload = ConfigurationRepository.get_published(cursor, published.version_id)
        if payload is None:
            raise AgentToolUnavailable("published pricebook payload is missing")
        if not hmac.compare_digest(snapshot_hash(payload), published.snapshot_hash):
            raise AgentToolUnavailable("published pricebook payload does not match its digest")
        try:
            rules = published_price_rules(payload)
        except PricebookImportError as error:
            raise AgentToolUnavailable("published pricebook is not usable") from error
        return _PublishedPricebook(
            rules,
            PricebookProvenance(
                version_id=published.version_id,
                version=published.version,
                snapshot_hash=f"{_JCS_PREFIX}{published.snapshot_hash}",
            ),
            payload,
        )

    @staticmethod
    def _bound_request(cursor: Any, call: AgentToolCall) -> StoredOrderRequest:
        # The facade's bound-path gate already proved path == claims; this read proves the
        # stored aggregate carries the same store/contact/conversation binding. A mismatch is
        # indistinguishable from an authorization refusal on purpose.
        bound = OrderRequestRepository.get_bound(
            cursor,
            order_request_id=UUID(str(call.path_parameters["order_request_id"])),
            store_id=call.claims.store_id,
            contact_binding_id=call.claims.contact_binding_id,
            conversation_binding_id=call.claims.conversation_binding_id,
        )
        if bound is None:
            raise AgentAuthorizationError("bound order request is unavailable")
        return bound

    @staticmethod
    def _expected_row_version(call: AgentToolCall) -> int:
        if call.if_match is None:
            raise AgentToolUnavailable("If-Match gate did not run")
        return int(call.if_match[1:-1])

    @staticmethod
    def _envelope(
        call: AgentToolCall,
        decision: PolicyDecision,
        *,
        outcome: str,
        reason_codes: list[str],
        obligations: list[str],
        decision_snapshot_hash: str,
        data: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "trace_id": call.trace_id,
            "decision": {
                "outcome": outcome,
                "reason_codes": reason_codes,
                "obligations": obligations,
                "policy_version": decision.policy_version,
                "snapshot_hash": decision_snapshot_hash,
            },
            "data": dict(data),
        }

    def _pdp_envelope(
        self, call: AgentToolCall, *, decision_snapshot_hash: str, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Envelope carrying the policy point's own verdict for this call, verbatim."""
        decision = self._policy_decision(call)
        return self._envelope(
            call,
            decision,
            outcome=decision.outcome.value,
            reason_codes=[reason.value for reason in decision.reason_codes],
            obligations=[],
            decision_snapshot_hash=decision_snapshot_hash,
            data=data,
        )

    # --- Catalog -----------------------------------------------------------

    def _catalog_resolve(self, call: AgentToolCall) -> Mapping[str, Any]:
        """Exact code/alias resolution, then bounded exact-substring matching — never fuzzy."""
        with self._connect() as connection, connection.cursor() as cursor:
            pricebook = self._published_pricebook(cursor)
        try:
            services = tuple(
                ServiceDefinition(
                    code=str(item["code"]),
                    display_name=str(item["display_name"]),
                    category=str(item["category"]),
                    unit=Unit(str(item["unit"])),
                )
                for item in pricebook.payload["services"]
            )
            registry = ServiceRegistry(services, standard_wash_aliases())
        except (KeyError, TypeError, CatalogError) as error:
            raise AgentToolUnavailable("published catalog is not usable") from error

        unit_filter = _known_unit(call.arguments)
        query = unicodedata.normalize("NFC", str(call.arguments["query"]))
        normalized = re.sub(r"[\s\-]+", "_", query.strip().upper())

        candidates: list[dict[str, Any]] = []
        if _SERVICE_CODE.fullmatch(normalized):
            try:
                service = registry.resolve(normalized, unit_filter)
            except CatalogError:
                service = None
            if service is not None:
                candidates.append(
                    _candidate(service, pricebook.provenance, "HIGH", clarification=None)
                )
        if not candidates and len(normalized) >= _MIN_SUBSTRING_QUERY:
            matches = [
                service
                for service in sorted(services, key=lambda item: item.code)
                if normalized in service.code
                and (unit_filter is None or service.unit is unit_filter)
            ]
            if len(matches) == 1:
                candidates.append(
                    _candidate(matches[0], pricebook.provenance, "MEDIUM", clarification=None)
                )
            else:
                # Multiple exact-substring hits are ambiguity, not a ranked preference: every
                # candidate is LOW and carries the contract's ambiguity marker.
                candidates.extend(
                    _candidate(service, pricebook.provenance, "LOW", "AMBIGUOUS_SERVICE")
                    for service in matches[:5]
                )

        return self._pdp_envelope(
            call,
            # The real digest of the published catalog the candidates came from.
            decision_snapshot_hash=_contract_sha256(pricebook.provenance.snapshot_hash),
            data={"candidates": candidates},
        )

    # --- Intake ------------------------------------------------------------

    def _order_request_create(self, call: AgentToolCall) -> Mapping[str, Any]:
        # `customer_intent` and `source_provider_message_ids` are deliberately not persisted:
        # the aggregate has no free-text columns, and no read model exists that could verify a
        # provider message id against the bound conversation. The draft binds only what the
        # server already knows: store, contact and conversation from the verified claims.
        claims = call.claims
        created_at = self._now()

        def commit() -> dict[str, object]:
            stored = OrderRequestRepository().create(
                connection,
                CreateOrderRequestCommand(
                    store_id=claims.store_id,
                    contact_binding_id=claims.contact_binding_id,
                    conversation_binding_id=claims.conversation_binding_id,
                    actor_id=claims.run_id,
                    correlation_id=claims.jti,
                    created_at=created_at,
                ),
            )
            return {
                "order_request_id": str(stored.order_request_id),
                "row_version": stored.row_version,
                "status": stored.status,
            }

        with self._connect() as connection:
            try:
                result = self._idempotency.execute(
                    connection,
                    IdempotentCommand(
                        scope=f"agent-order-request-create:{claims.run_id}",
                        key=_required_key(call.idempotency_key),
                        payload={
                            "store_id": str(claims.store_id),
                            "contact_binding_id": str(claims.contact_binding_id),
                            "conversation_binding_id": str(claims.conversation_binding_id),
                            "run_id": str(claims.run_id),
                        },
                        occurred_at=created_at,
                    ),
                    commit,
                )
            except IdempotencyConflictError as error:
                raise _idempotency_refusal() from error
        data = result.response
        return self._pdp_envelope(
            call,
            decision_snapshot_hash=_contract_sha256(canonical_document(data).snapshot_hash),
            data=data,
        )

    def _record_customer_facts(self, call: AgentToolCall) -> Mapping[str, Any]:
        claims = call.claims
        facts = call.arguments["facts"]
        # Only the allowlisted fact TYPES cross into the store. `service_text` and
        # `address_text` stay in the channel conversation; no aggregate column or ledger
        # payload may become a second home for them. The idempotency payload likewise carries
        # types, never text.
        fact_types = tuple(sorted({str(fact["fact_type"]) for fact in facts}))
        recorded_at = self._now()

        def commit() -> dict[str, object]:
            stored = OrderRequestRepository().record_customer_facts(
                connection,
                RecordCustomerFactsCommand(
                    order_request_id=UUID(str(call.path_parameters["order_request_id"])),
                    store_id=claims.store_id,
                    contact_binding_id=claims.contact_binding_id,
                    conversation_binding_id=claims.conversation_binding_id,
                    expected_row_version=self._expected_row_version(call),
                    fact_types=tuple(str(fact["fact_type"]) for fact in facts),
                    actor_id=claims.run_id,
                    correlation_id=claims.jti,
                    occurred_at=recorded_at,
                ),
            )
            return {
                "order_request_id": str(stored.order_request_id),
                "row_version": stored.row_version,
                "accepted_fact_count": len(facts),
                "unresolved_fact_types": [],
            }

        with self._connect() as connection:
            try:
                result = self._idempotency.execute(
                    connection,
                    IdempotentCommand(
                        scope=(f"agent-record-facts:{call.path_parameters['order_request_id']}"),
                        key=_required_key(call.idempotency_key),
                        payload={
                            "order_request_id": str(call.path_parameters["order_request_id"]),
                            "expected_row_version": self._expected_row_version(call),
                            "fact_types": list(fact_types),
                            "fact_count": len(facts),
                        },
                        occurred_at=recorded_at,
                    ),
                    commit,
                )
            except OrderRequestBindingError as error:
                raise AgentAuthorizationError("bound order request is unavailable") from error
            except OrderRequestStateError as error:
                raise _stale_refusal() from error
            except IdempotencyConflictError as error:
                raise _idempotency_refusal() from error
        data = result.response
        return self._pdp_envelope(
            call,
            decision_snapshot_hash=_contract_sha256(canonical_document(data).snapshot_hash),
            data=data,
        )

    # --- Quote -------------------------------------------------------------

    def _quote_estimate(self, call: AgentToolCall) -> Mapping[str, Any]:
        """Mirror of OperationsService.create_quote for the agent boundary."""
        claims = call.claims
        lines = tuple(
            RequestedLine(
                service_code=str(line["service_code"]),
                quantity=str(line["quantity"]),
                unit=Unit(str(line["unit"])),
                quantity_basis=QuantityBasis(str(line["quantity_basis"])),
            )
            for line in call.arguments["lines"]
        )
        priced_at = self._now()
        fulfillment = call.arguments["fulfillment"]

        with self._connect() as connection:
            with connection.cursor() as cursor:
                bound = self._bound_request(cursor, call)
                if bound.row_version != self._expected_row_version(call):
                    raise _stale_refusal()
                pricebook = self._published_pricebook(cursor)
                container = QuoteRepository.find_container(
                    cursor,
                    store_id=claims.store_id,
                    bound_order_request_id=bound.order_request_id,
                )
            quote_id = container.quote_id if container is not None else uuid4()
            expected_revision = container.current_revision if container is not None else 0
            expected_row_version = container.row_version if container is not None else 0
            composition = compose_quote_revision(
                quote_id=quote_id,
                revision=expected_revision + 1,
                rules=pricebook.rules,
                requested=lines,
                pricebook=pricebook.provenance,
                priced_at=priced_at,
                # The tool contract already requires `fulfillment.mode`; it simply was not being
                # passed on, so every agent estimate carried DELIVERY_FEE_UNRESOLVED even for a
                # customer collecting in person. `verified_distance_m` stays None for the same
                # reason `_delivery_evaluate` gives: no distance-evidence store exists, so a
                # delivery mode still resolves to REQUIRE_HUMAN rather than an invented distance.
                fulfillment_mode=FulfillmentMode(str(fulfillment["mode"])),
                planned_transport_weight_kg=(
                    None
                    if fulfillment.get("planned_transport_weight_kg") is None
                    else str(fulfillment["planned_transport_weight_kg"])
                ),
            )
            if isinstance(composition, UnresolvedQuote):
                # The engine's own reason codes, nothing persisted, no idempotency key
                # consumed — the caller may correct the input and retry with the same key.
                raise AgentToolRefusal(
                    code=composition.reason_codes[0],
                    message=(
                        "The pricing engine could not resolve an exact estimate; "
                        "staff must price this request."
                    ),
                    status_code=422,
                    reason_codes=composition.reason_codes,
                )
            snapshot = composition.snapshot

            def commit() -> dict[str, object]:
                QuoteRepository().create_revision(
                    connection,
                    QuoteRevisionCommand(
                        store_id=claims.store_id,
                        bound_order_request_id=bound.order_request_id,
                        snapshot=snapshot,
                        expected_current_revision=expected_revision,
                        expected_row_version=expected_row_version,
                        created_by=claims.run_id,
                        correlation_id=claims.jti,
                        occurred_at=priced_at,
                        actor_type=ActorRole.AGENT_RUNNER.value,
                    ),
                )
                return _quote_estimate_mapping(snapshot, pricebook.provenance)

            fulfillment = call.arguments["fulfillment"]
            fulfillment_payload: dict[str, object] = {"mode": str(fulfillment["mode"])}
            if fulfillment.get("planned_transport_weight_kg") is not None:
                fulfillment_payload["planned_transport_weight_kg"] = str(
                    fulfillment["planned_transport_weight_kg"]
                )
            try:
                result = self._idempotency.execute(
                    connection,
                    IdempotentCommand(
                        scope=f"agent-quote-estimate:{bound.order_request_id}",
                        key=_required_key(call.idempotency_key),
                        payload={
                            "store_id": str(claims.store_id),
                            "bound_order_request_id": str(bound.order_request_id),
                            # Mirror of create_quote: a fresh container id is generated inside
                            # the one-time executor, so the replay hash must not contain one.
                            "quote_id": str(quote_id) if container is not None else None,
                            "expected_current_revision": expected_revision,
                            "expected_row_version": expected_row_version,
                            "lines": [
                                {
                                    "service_code": line.service_code,
                                    "quantity": line.quantity,
                                    "unit": line.unit.value,
                                    "quantity_basis": line.quantity_basis.value,
                                }
                                for line in lines
                            ],
                            "fulfillment": fulfillment_payload,
                        },
                        occurred_at=priced_at,
                    ),
                    commit,
                )
            except IdempotencyConflictError as error:
                raise _idempotency_refusal() from error
            except QuoteStateError as error:
                raise _stale_refusal() from error
            except psycopg.errors.UniqueViolation as error:
                # A concurrent run created the container first; retrying re-prices as the
                # next revision of that container.
                raise _stale_refusal() from error
        data = result.response
        reason_codes = data["reason_codes"]
        if not isinstance(reason_codes, list):
            raise AgentToolUnavailable("stored quote estimate response is malformed")
        return self._envelope(
            call,
            self._policy_decision(call),
            # The composed revision carries status REVIEW_REQUIRED: a human reviews every
            # estimate before it may be presented. The engine's reason codes say why.
            outcome="REQUIRE_HUMAN",
            reason_codes=[str(code) for code in reason_codes],
            obligations=[],
            decision_snapshot_hash=str(data["snapshot_hash"]),
            data={key: value for key, value in data.items() if key != "reason_codes"},
        )

    # --- Delivery ----------------------------------------------------------

    def _delivery_evaluate(self, call: AgentToolCall) -> Mapping[str, Any]:
        # `verified_distance_m=None` is the only honest input: no distance-evidence store
        # exists, so the engine's DELIVERY_DISTANCE_UNVERIFIED / DELIVERY_FEE_REQUIRES_HUMAN
        # outcome for delivery modes comes through verbatim rather than being pre-empted by
        # an invented distance.
        try:
            result = evaluate_delivery(
                FulfillmentMode(str(call.arguments["fulfillment_mode"])),
                verified_distance_m=None,
                planned_transport_weight_kg=call.arguments.get("planned_transport_weight_kg"),
            )
        except DeliveryError as error:
            raise AgentToolRefusal(
                code=error.code.value,
                message="The delivery engine rejected these facts; staff must evaluate them.",
                status_code=422,
            ) from error
        data = _delivery_mapping(result)
        return self._envelope(
            call,
            self._policy_decision(call),
            outcome=result.overall_outcome.value,
            reason_codes=[reason.value for reason in result.reason_codes],
            obligations=[],
            decision_snapshot_hash=_contract_sha256(canonical_document(result.trace).snapshot_hash),
            data=data,
        )

    # --- Capacity ----------------------------------------------------------

    def _capacity_check(self, call: AgentToolCall) -> Mapping[str, Any]:
        requested_raw = call.arguments.get("requested_ready_at")
        try:
            requested = (
                datetime.fromisoformat(str(requested_raw)) if requested_raw is not None else None
            )
            result = evaluate_r1_capacity(requested_ready_at=requested)
        except ValueError as error:
            raise AgentToolRefusal(
                code="VALIDATION_ERROR",
                message="The requested ready time is not a timezone-aware timestamp.",
                status_code=422,
            ) from error
        _policy = self._policy_decision(call)
        return self._envelope(
            call,
            _policy,
            outcome=result.policy_outcome.value,
            reason_codes=[],
            # The engine's own requirement: no slot is ever confirmed by this advisory.
            obligations=[result.required_approval],
            decision_snapshot_hash=_contract_sha256(canonical_document(result).snapshot_hash),
            data={
                "advisory": result.advisory.value,
                "advisory_ready_window_start": None,
                "advisory_ready_window_end": None,
                "slot_confirmed": result.slot_confirmed,
                "required_approval": result.required_approval,
            },
        )

    # --- Operations with no honest backing: typed unavailability -----------

    def _message_draft_create(self, call: AgentToolCall) -> Mapping[str, Any]:
        del call
        # No message_drafts table, no published template store and no fact_ref builder exist.
        # The 11-field response (content hash, template version, >= 1 verifiable fact_refs)
        # cannot be produced without fabricating every one of them.
        raise AgentToolUnavailable(
            "message draft creation has no backing store: no message_drafts table, "
            "no published template store, no fact_ref builder"
        )

    def _public_order_status_get(self, call: AgentToolCall) -> Mapping[str, Any]:
        del call
        # `public_code` exists only on `agent_runs` (migration 0010), not on orders, and there
        # is no public status read model. A lookup here would have to invent the join.
        raise AgentToolUnavailable(
            "public order status has no read model: public_code is not bound to orders"
        )

    def _incident_open(self, call: AgentToolCall) -> Mapping[str, Any]:
        del call
        # IncidentRepository.open requires a real order_id in the orders store (or an affected
        # automated message) and a category in {SERVICE_QUALITY, AUTOMATED_MESSAGE_ERROR}.
        # This tool is bound to an order_request, no order_request -> order linkage exists to
        # read, and the tool's 7-value allegation enum has no honest mapping onto the 2 stored
        # categories (LOSS/DAMAGE/DELAY/DELIVERY/BILLING/OTHER would be fabricated into
        # SERVICE_QUALITY). No order id is invented here.
        raise AgentToolUnavailable(
            "incident intake cannot bind a real order or map the allegation categories "
            "without inventing data; hand off to staff"
        )

    # --- Approval ----------------------------------------------------------

    def _approval_request_create(self, call: AgentToolCall) -> Mapping[str, Any]:
        claims = call.claims
        try:
            action = ApprovalAction(str(call.arguments["action"]))
        except ValueError as error:
            # Unmapped actions fail closed; the tool enum is a subset of the domain enum, so
            # this is defensive rather than reachable through a validated call.
            raise AgentToolUnavailable(
                "approval action is not mapped to a domain action"
            ) from error
        if action not in _VERIFIABLE_APPROVAL_ACTIONS:
            raise AgentToolUnavailable(
                f"approval action {action.value} has no verifiable resource store at this "
                "boundary; only QUOTE_REVISION actions can be bound honestly"
            )
        resource_id = UUID(str(call.arguments["resource_id"]))
        resource_version = int(str(call.arguments["resource_version"]))
        snapshot_hash_arg = str(call.arguments["snapshot_hash"])
        rendered_hash_arg = str(call.arguments["rendered_hash"])

        with self._connect() as connection:
            with connection.cursor() as cursor:
                bound = self._bound_request(cursor, call)
                container = QuoteRepository.find_container(
                    cursor,
                    store_id=claims.store_id,
                    bound_order_request_id=bound.order_request_id,
                )
                # A resource that is not a current child of the bound request is refused
                # exactly like an authorization failure: the difference must not be an oracle.
                if container is None or resource_id != _quote_revision_resource_id(
                    container.quote_id, resource_version
                ):
                    raise AgentAuthorizationError("approval resource is not bound to this run")
                if resource_version != container.current_revision:
                    raise _stale_refusal()
                stored = QuoteRepository.get_revision(cursor, container.quote_id, resource_version)
                if stored is None:
                    raise AgentAuthorizationError("approval resource is not bound to this run")
            if not hmac.compare_digest(stored.document.snapshot_hash, _jcs_hash(snapshot_hash_arg)):
                raise _stale_refusal()
            policy = APPROVAL_POLICIES[action]
            if APPROVAL_RESOURCE_TYPES[action] != str(call.arguments["resource_type"]):
                raise AgentToolUnavailable("approval resource type does not match its action")
            try:
                stored_approval = ApprovalRepository().request(
                    connection,
                    ApprovalRequestCommand(
                        action=action,
                        resource_type=str(call.arguments["resource_type"]),
                        # The envelope binds the real aggregate key, not the derived
                        # presentation id the model addressed it by.
                        resource_id=container.quote_id,
                        resource_version=resource_version,
                        snapshot_hash=_jcs_hash(snapshot_hash_arg),
                        # The rendered hash binds whatever content the run intends to present;
                        # it is re-verified against the real rendered content at decision and
                        # execution time (approvals.py _require_exact_binding), never here.
                        rendered_hash=_jcs_hash(rendered_hash_arg),
                        policy_version=self._policy_version(call),
                        requested_by=claims.run_id,
                        idempotency_key=_required_key(call.idempotency_key),
                        correlation_id=claims.jti,
                        requested_at=self._now(),
                        actor_type=ActorRole.AGENT_RUNNER.value,
                        store_id=claims.store_id,
                    ),
                )
            except IdempotencyConflictError as error:
                raise _idempotency_refusal() from error
            except ApprovalEnvelopeError as error:
                raise AgentToolRefusal(
                    code="VALIDATION_ERROR",
                    message="The approval binding is not valid for this resource.",
                    status_code=422,
                ) from error
        _policy = self._policy_decision(call)
        return self._envelope(
            call,
            _policy,
            # The request exists; the action itself still requires the human the policy names.
            outcome="REQUIRE_HUMAN",
            reason_codes=list(policy.reason_codes),
            obligations=list(policy.obligations),
            decision_snapshot_hash=_contract_sha256(stored_approval.envelope_hash),
            data={
                "approval_request_id": str(stored_approval.approval_request_id),
                # The store's REQUESTED is the contract's PENDING; nothing else maps.
                "status": "PENDING",
                "reason_codes": list(policy.reason_codes),
                "required_role": stored_approval.required_role.value,
                "expires_at": stored_approval.expires_at.isoformat(),
                "obligations": list(policy.obligations),
                "execution_capability": policy.execution_capability,
            },
        )


def build_domain_backend(
    *,
    database_url: str | None = None,
    connection_factory: Callable[[str], Any] = psycopg.connect,
    now: Callable[[], datetime] | None = None,
) -> AgentToolBackend:
    """Assemble the domain backend under explicit configuration, or the unavailable one.

    This function creates no new authority: the facade's default dependency remains
    `UnavailableAgentToolBackend`, and selecting this backend is a deployment's explicit
    dependency override, not a side effect of importing the module.
    """
    if not database_url:
        return UnavailableAgentToolBackend()
    return DomainAgentToolBackend(
        database_url=database_url,
        connection_factory=connection_factory,
        now=now,
    )


def _quote_estimate_mapping(
    snapshot: ImmutableQuoteSnapshot, pricebook: PricebookProvenance
) -> dict[str, object]:
    """Project the engine's immutable revision onto the contract's QuoteEstimateData.

    Every value is read off the snapshot the domain built; nullable delivery fee and display
    total stay null because the engine left them unresolved, and the reason codes are the
    engine's own. `promotion_status` is REQUIRES_HUMAN because the snapshot carries
    PROMOTION_NOT_EVALUATED (DEC-002 is open) and the contract has no NOT_EVALUATED value —
    failing closed is the only honest member. `assumptions` stays empty: no engine produced
    assumption codes, and this module does not invent them.
    """
    data = snapshot.data
    totals = data.totals
    return {
        "quote_id": str(data.quote_id),
        "quote_revision_id": str(_quote_revision_resource_id(data.quote_id, data.revision)),
        "revision": data.revision,
        "finality": data.finality.value,
        "pricebook_version": str(pricebook.version),
        "promotion_version": None,
        "list_service_subtotal_vnd": totals.list_service_subtotal_max_vnd,
        "discount_amount_vnd": totals.discount_amount_max_vnd,
        "net_service_subtotal_vnd": totals.net_service_subtotal_max_vnd,
        "delivery_fee_vnd": totals.delivery_fee_vnd,
        "display_total_vnd": totals.display_total_max_vnd,
        "tax_treatment": "UNVERIFIED",
        "promotion_status": "REQUIRES_HUMAN",
        "promotion_eligibility_event": None,
        "promotion_eligibility_at": None,
        "vehicle_recommendation": None,
        "required_approvals": list(data.required_approvals),
        "assumptions": [],
        "snapshot_hash": _contract_sha256(snapshot.document.snapshot_hash),
        # Carried for the decision envelope; stripped from `data` before responding.
        "reason_codes": list(data.reason_codes),
    }


def _delivery_mapping(result: DeliveryResult) -> dict[str, object]:
    """Mechanical enum mapping from the engine's result onto the contract's shape."""
    if not result.delivery_job_required:
        distance_status = "NOT_REQUIRED"
        fee_status = "NOT_REQUIRED"
        vehicle = "NOT_REQUIRED"
    else:
        # verified_distance_m is always None at this boundary; the engine can therefore only
        # answer UNVERIFIED, and the fee can only be unresolved.
        distance_status = "UNVERIFIED"
        fee_status = "RESOLVED" if result.fee_outcome is PolicyOutcome.ALLOW else "REQUIRES_HUMAN"
        vehicle = (
            result.vehicle_recommendation.value
            if result.vehicle_recommendation is not None
            else "STAFF_PLAN_REQUIRED"
        )
    return {
        "distance_status": distance_status,
        "distance_km": None,
        "fee_status": fee_status,
        "delivery_fee_vnd": result.delivery_fee_vnd,
        "vehicle_recommendation": vehicle,
        "requires_human": result.overall_outcome is PolicyOutcome.REQUIRE_HUMAN,
    }


def _candidate(
    service: ServiceDefinition,
    pricebook: PricebookProvenance,
    confidence: str,
    clarification: str | None,
) -> dict[str, Any]:
    return {
        "service_code": service.code,
        # The immutable version id of the published document this definition came from.
        "service_version": str(pricebook.version_id),
        "display_name": service.display_name,
        "compatible_units": [service.unit.value],
        "confidence_band": confidence,
        "clarification_key": clarification,
    }


def _quote_revision_resource_id(quote_id: UUID, revision: int) -> UUID:
    return uuid5(uuid5(QUOTE_REVISION_ID_NAMESPACE, str(quote_id)), str(revision))


def _known_unit(arguments: Mapping[str, Any]) -> Unit | None:
    attributes = arguments.get("known_attributes")
    if not isinstance(attributes, Mapping) or attributes.get("unit") is None:
        return None
    return Unit(str(attributes["unit"]))


def _contract_sha256(canonical_hash: str) -> str:
    digest = (
        canonical_hash.removeprefix(_JCS_PREFIX)
        if canonical_hash.startswith(_JCS_PREFIX)
        else canonical_hash
    )
    if not _DIGEST.fullmatch(digest):
        raise AgentToolUnavailable("engine snapshot hash is not a SHA-256 digest")
    return f"sha256:{digest}"


def _jcs_hash(contract_hash: str) -> str:
    if not contract_hash.startswith("sha256:"):
        raise AgentToolRefusal(
            code="VALIDATION_ERROR",
            message="Snapshot and rendered hashes must be sha256 digests.",
            status_code=422,
        )
    return f"{_JCS_PREFIX}{contract_hash.removeprefix('sha256:')}"


def _required_key(idempotency_key: str | None) -> str:
    # The facade's header gate runs first; this is the fail-closed backstop for direct calls.
    if idempotency_key is None:
        raise AgentToolUnavailable("idempotency key gate did not run")
    return idempotency_key


def _stale_refusal() -> AgentToolRefusal:
    return AgentToolRefusal(
        code="STALE_VERSION",
        message="The bound resource changed; reload and retry with its current version.",
        status_code=409,
    )


def _idempotency_refusal() -> AgentToolRefusal:
    return AgentToolRefusal(
        code="IDEMPOTENCY_CONFLICT",
        message="This idempotency key was already used with different arguments.",
        status_code=409,
    )


__all__ = [
    "QUOTE_REVISION_ID_NAMESPACE",
    "DomainAgentToolBackend",
    "build_domain_backend",
]
