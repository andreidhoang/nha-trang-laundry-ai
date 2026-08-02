"""Constrained worker orchestration; provider send capability is intentionally absent."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from nha_trang_laundry_db.outbox import ClaimedOutboxEvent, OutboxRepository
from nha_trang_laundry_domain.catalog import ActorRole
from nha_trang_laundry_observability import Telemetry
from opentelemetry import metrics, trace

from .agent_runner import (
    AgentRunJob,
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentRunRejected,
    AgentRunResult,
    AgentToolBridgeRejected,
    AgentToolBridgeSession,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
    DisabledOpenClawProviderRuntime,
    ProviderRuntimeBlocked,
    ScriptedToolCall,
    SyntheticScriptedRuntime,
)
from .bridge_api import AgentBridgeSessionStore, create_agent_bridge_app
from .durable_agent_worker import DurableAgentRunWorker, DurableAgentRunWorkerResult

InternalEventHandler = Callable[[ClaimedOutboxEvent], None]
AuthorityCheck = Callable[[], bool]


@dataclass(frozen=True)
class WorkerRunResult:
    event_id: str | None
    status: str


class InternalOutboxWorker:
    """Run one exact allowlisted internal event; this class has no channel/provider client."""

    def __init__(
        self,
        handlers: Mapping[str, InternalEventHandler],
        repository: OutboxRepository | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self._handlers = dict(handlers)
        self._repository = repository or OutboxRepository()
        self._telemetry = telemetry or Telemetry(
            meter_provider=metrics.get_meter_provider(),
            tracer_provider=trace.get_tracer_provider(),
            instrumentation_name="nha_trang_laundry.worker.outbox",
        )

    def run_once(
        self,
        connection: Any,
        *,
        authority_valid: AuthorityCheck | None = None,
    ) -> WorkerRunResult:
        authority = authority_valid or (lambda: True)
        if not authority():
            return WorkerRunResult(None, "STOPPED")
        event = self._repository.claim_next_internal(
            connection, worker_role=ActorRole.OUTBOX_WORKER
        )
        if event is None:
            return WorkerRunResult(None, "IDLE")
        carrier = {
            key: value
            for key, value in (("traceparent", event.traceparent), ("tracestate", event.tracestate))
            if value is not None
        }
        with self._telemetry.start_span(
            "outbox_process",
            carrier=carrier,
            attributes={"component": "worker", "operation": "outbox_process", "queue": "internal"},
        ):
            return self._process_claimed_event(connection, event, authority)

    def _process_claimed_event(
        self,
        connection: Any,
        event: ClaimedOutboxEvent,
        authority: AuthorityCheck,
    ) -> WorkerRunResult:
        handler = self._handlers.get(event.event_type)
        if handler is None:
            if not authority():
                return WorkerRunResult(str(event.event_id), "ABANDONED")
            self._repository.retry_or_dead_letter_internal(
                connection,
                event_id=event.event_id,
                claim_token=event.claim_token,
                worker_role=ActorRole.OUTBOX_WORKER,
                error_class="HANDLER_NOT_CONFIGURED",
                retryable=False,
            )
            self._telemetry.record(
                "dlq_items_total",
                1,
                {"component": "worker", "queue": "internal", "outcome": "failed"},
            )
            return WorkerRunResult(str(event.event_id), "DEAD")
        try:
            handler(event)
        except Exception:
            if not authority():
                return WorkerRunResult(str(event.event_id), "ABANDONED")
            self._repository.retry_or_dead_letter_internal(
                connection,
                event_id=event.event_id,
                claim_token=event.claim_token,
                worker_role=ActorRole.OUTBOX_WORKER,
                error_class="INTERNAL_HANDLER_TRANSIENT_FAILURE",
                retryable=True,
            )
            self._telemetry.record(
                "queue_retries_total",
                1,
                {
                    "component": "worker",
                    "queue": "internal",
                    "outcome": "held",
                    "retryable": "true",
                },
            )
            return WorkerRunResult(str(event.event_id), "RETRY_SCHEDULED")
        if not authority():
            return WorkerRunResult(str(event.event_id), "ABANDONED")
        self._repository.complete_internal(
            connection,
            event_id=event.event_id,
            claim_token=event.claim_token,
            worker_role=ActorRole.OUTBOX_WORKER,
        )
        return WorkerRunResult(str(event.event_id), "COMPLETED")


__all__ = [
    "AgentBridgeSessionStore",
    "AgentRunJob",
    "AgentRunRejected",
    "AgentRunResult",
    "AgentRunner",
    "AgentRunnerTokenIssuer",
    "AgentToolBridgeRejected",
    "AgentToolBridgeSession",
    "AgentToolForwardRequest",
    "AgentToolForwardResponse",
    "AuthorityCheck",
    "DisabledOpenClawProviderRuntime",
    "DurableAgentRunWorker",
    "DurableAgentRunWorkerResult",
    "InternalEventHandler",
    "InternalOutboxWorker",
    "ProviderRuntimeBlocked",
    "ScriptedToolCall",
    "SyntheticScriptedRuntime",
    "WorkerRunResult",
    "create_agent_bridge_app",
]
