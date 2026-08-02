"""Recoverable worker process supervision with fail-closed execution modes."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import psycopg
from nha_trang_laundry_db.agent_runs import AgentRunRepository
from nha_trang_laundry_db.outbox import OutboxRepository
from nha_trang_laundry_domain.catalog import ActorRole
from nha_trang_laundry_observability import EventSeverity, SafeStructuredLogger
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import AuthorityCheck, InternalOutboxWorker

AgentCycle = Callable[[Any, AuthorityCheck], str]
ConnectionFactory = Callable[[str], Any]


class WorkerSettings(BaseSettings):
    """Environment-only worker settings; execution remains disabled by default."""

    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
        secrets_dir="/run/secrets" if Path("/run/secrets").is_dir() else None,
    )

    database_url: str | None = None
    worker_poll_interval_seconds: float = Field(default=1.0, ge=0.01, le=60.0)
    worker_recovery_interval_seconds: float = Field(default=30.0, ge=1.0, le=3600.0)
    worker_internal_outbox_enabled: bool = False
    worker_agent_queue_enabled: bool = False
    feature_public_channels_enabled: bool = False
    feature_automated_sends_enabled: bool = False
    feature_agent_runtime_enabled: bool = False

    @model_validator(mode="after")
    def prohibit_release_capabilities(self) -> WorkerSettings:
        if (
            self.feature_public_channels_enabled
            or self.feature_automated_sends_enabled
            or self.feature_agent_runtime_enabled
        ):
            raise ValueError("worker host cannot enable unreleased external capabilities")
        return self


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    running: bool
    ready: bool
    last_cycle_at: datetime | None
    last_database_success_at: datetime | None
    last_error_code: str | None
    internal_outbox_status: str
    agent_status: str
    recovered_outbox_count: int
    recovered_agent_count: int
    authority_revoked: bool


class WorkerSupervisor:
    """Own one bounded polling thread; PostgreSQL remains the claim and lease authority."""

    def __init__(
        self,
        settings: WorkerSettings,
        *,
        internal_worker: InternalOutboxWorker | None = None,
        agent_cycle: AgentCycle | None = None,
        connection_factory: ConnectionFactory = psycopg.connect,
        logger: SafeStructuredLogger | None = None,
    ) -> None:
        self.settings = settings
        self._internal_worker = internal_worker
        self._agent_cycle = agent_cycle
        self._connection_factory = connection_factory
        self._logger = logger or SafeStructuredLogger()
        self._shutdown_requested = threading.Event()
        self._authority_revoked = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_cycle_at: datetime | None = None
        self._last_database_success_at: datetime | None = None
        self._last_error_code: str | None = None
        self._internal_status = "DISABLED"
        self._agent_status = "DISABLED"
        self._recovered_outbox_count = 0
        self._recovered_agent_count = 0
        self._last_recovery_monotonic: float | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._shutdown_requested.clear()
            self._authority_revoked.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="laundry-worker-supervisor",
            )
            self._thread.start()

    def stop(self, timeout_seconds: float = 10.0) -> bool:
        if timeout_seconds < 0:
            raise ValueError("shutdown timeout must be non-negative")
        self._shutdown_requested.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_seconds)
        stopped = thread is None or not thread.is_alive()
        self._authority_revoked.set()
        if not stopped:
            self._record_error("SHUTDOWN_TIMEOUT")
        return stopped

    def run_cycle(self) -> None:
        """Execute at most one item per enabled queue plus bounded expired-claim recovery."""

        timestamp = datetime.now(UTC)
        if not self._authority_valid():
            self._record_stopped_cycle(timestamp)
            return
        database_url = (self.settings.database_url or "").strip()
        if not database_url:
            self._record_error("DATABASE_NOT_CONFIGURED", cycle_at=timestamp)
            return
        if self.settings.worker_internal_outbox_enabled and self._internal_worker is None:
            self._record_error("INTERNAL_HANDLER_REGISTRY_UNAVAILABLE", cycle_at=timestamp)
            return
        if self.settings.worker_agent_queue_enabled and self._agent_cycle is None:
            self._record_error("AGENT_RUNTIME_UNAVAILABLE", cycle_at=timestamp)
            return
        try:
            with self._connection_factory(database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    if cursor.fetchone() != (1,):
                        raise RuntimeError("database readiness probe returned an invalid result")
                if not self._authority_valid():
                    self._record_stopped_cycle(timestamp)
                    return
                self._recover_expired_if_due(connection)
                internal_status = "DISABLED"
                if self.settings.worker_internal_outbox_enabled:
                    if self._internal_worker is None:
                        raise RuntimeError("internal worker registry disappeared")
                    internal_status = self._internal_worker.run_once(
                        connection, authority_valid=self._authority_valid
                    ).status
                agent_status = "DISABLED"
                if self.settings.worker_agent_queue_enabled:
                    if self._agent_cycle is None:
                        raise RuntimeError("agent cycle disappeared")
                    if self._authority_valid():
                        agent_status = self._agent_cycle(connection, self._authority_valid)
                    else:
                        agent_status = "STOPPED"
            with self._lock:
                self._last_cycle_at = timestamp
                self._last_database_success_at = timestamp
                self._last_error_code = None
                self._internal_status = internal_status
                self._agent_status = agent_status
            self._logger.record(
                component="worker",
                name="worker.cycle.completed",
                outcome="completed",
                fields={
                    "internal_outbox_status": internal_status,
                    "agent_status": agent_status,
                },
            )
        except Exception as error:
            error_code = (
                "DATABASE_UNAVAILABLE" if isinstance(error, psycopg.Error) else "CYCLE_FAILED"
            )
            self._record_error(error_code, cycle_at=timestamp)

    def readiness(self) -> WorkerSnapshot:
        """Probe dependencies without claiming work or implying automation authority."""

        database_url = (self.settings.database_url or "").strip()
        configuration_error = self._configuration_error()
        if not database_url or configuration_error is not None:
            error_code = configuration_error or "DATABASE_NOT_CONFIGURED"
            self._record_error(error_code)
            return self.snapshot(ready=False)
        try:
            with (
                self._connection_factory(database_url) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT 1")
                ready = cursor.fetchone() == (1,)
        except Exception:
            self._record_error("DATABASE_UNAVAILABLE")
            return self.snapshot(ready=False)
        if not ready:
            self._record_error("DATABASE_UNAVAILABLE")
            return self.snapshot(ready=False)
        with self._lock:
            self._last_database_success_at = datetime.now(UTC)
            self._last_error_code = None
        return self.snapshot(ready=True)

    def snapshot(self, *, ready: bool | None = None) -> WorkerSnapshot:
        with self._lock:
            thread = self._thread
            running = thread is not None and thread.is_alive()
            computed_ready = (
                running
                and self._last_database_success_at is not None
                and self._last_error_code is None
                and self._configuration_error() is None
            )
            return WorkerSnapshot(
                running=running,
                ready=computed_ready if ready is None else ready and running,
                last_cycle_at=self._last_cycle_at,
                last_database_success_at=self._last_database_success_at,
                last_error_code=self._last_error_code,
                internal_outbox_status=self._internal_status,
                agent_status=self._agent_status,
                recovered_outbox_count=self._recovered_outbox_count,
                recovered_agent_count=self._recovered_agent_count,
                authority_revoked=self._authority_revoked.is_set(),
            )

    def _run(self) -> None:
        while not self._shutdown_requested.is_set():
            self.run_cycle()
            self._shutdown_requested.wait(self.settings.worker_poll_interval_seconds)

    def _recover_expired_if_due(self, connection: Any) -> None:
        current = monotonic()
        last = self._last_recovery_monotonic
        if last is not None and current - last < self.settings.worker_recovery_interval_seconds:
            return
        self._last_recovery_monotonic = current
        outbox_id = OutboxRepository().recover_expired_internal(
            connection, worker_role=ActorRole.OUTBOX_WORKER
        )
        agent_id = AgentRunRepository().recover_expired(
            connection, worker_role=ActorRole.AGENT_RUNNER
        )
        with self._lock:
            if outbox_id is not None:
                self._recovered_outbox_count += 1
            if agent_id is not None:
                self._recovered_agent_count += 1
        if outbox_id is not None or agent_id is not None:
            self._logger.record(
                component="worker",
                name="worker.claim.recovered",
                outcome="requires_human",
                severity=EventSeverity.WARNING,
                fields={
                    "outbox_recovered": outbox_id is not None,
                    "agent_recovered": agent_id is not None,
                },
            )

    def _configuration_error(self) -> str | None:
        if self.settings.worker_internal_outbox_enabled and self._internal_worker is None:
            return "INTERNAL_HANDLER_REGISTRY_UNAVAILABLE"
        if self.settings.worker_agent_queue_enabled and self._agent_cycle is None:
            return "AGENT_RUNTIME_UNAVAILABLE"
        return None

    def _authority_valid(self) -> bool:
        return not self._authority_revoked.is_set()

    def _record_stopped_cycle(self, cycle_at: datetime) -> None:
        with self._lock:
            self._last_cycle_at = cycle_at
            self._internal_status = "STOPPED"
            self._agent_status = "STOPPED"

    def _record_error(self, code: str, *, cycle_at: datetime | None = None) -> None:
        with self._lock:
            if cycle_at is not None:
                self._last_cycle_at = cycle_at
            self._last_error_code = code
        self._logger.record(
            component="worker",
            name="worker.cycle.completed",
            outcome="failed",
            severity=EventSeverity.ERROR,
            fields={"error_code": code, "incident_ref": str(uuid4())},
        )


__all__ = ["WorkerSettings", "WorkerSnapshot", "WorkerSupervisor"]
