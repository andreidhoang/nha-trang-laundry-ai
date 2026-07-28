"""Constrained worker orchestration; provider send capability is intentionally absent."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from nha_trang_laundry_db.outbox import ClaimedOutboxEvent, OutboxRepository
from nha_trang_laundry_domain.catalog import ActorRole

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
    ) -> None:
        self._handlers = dict(handlers)
        self._repository = repository or OutboxRepository()

    def run_once(self, connection: Any) -> WorkerRunResult:
        event = self._repository.claim_next_internal(
            connection, worker_role=ActorRole.OUTBOX_WORKER
        )
        if event is None:
            return WorkerRunResult(None, "IDLE")
        handler = self._handlers.get(event.event_type)
        if handler is None:
            self._repository.retry_or_dead_letter_internal(
                connection,
                event_id=event.event_id,
                worker_role=ActorRole.OUTBOX_WORKER,
                error_class="HANDLER_NOT_CONFIGURED",
                retryable=False,
            )
            return WorkerRunResult(str(event.event_id), "DEAD")
        try:
            handler(event)
        except Exception:
            self._repository.retry_or_dead_letter_internal(
                connection,
                event_id=event.event_id,
                worker_role=ActorRole.OUTBOX_WORKER,
                error_class="INTERNAL_HANDLER_TRANSIENT_FAILURE",
                retryable=True,
            )
            return WorkerRunResult(str(event.event_id), "RETRY_SCHEDULED")
        self._repository.complete_internal(
            connection, event_id=event.event_id, worker_role=ActorRole.OUTBOX_WORKER
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
