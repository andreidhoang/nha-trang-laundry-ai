from __future__ import annotations

import os
import threading
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.outbox import INTERNAL_EVENT_TYPES, ClaimedOutboxEvent, OutboxRepository
from nha_trang_laundry_domain.catalog import ActorRole
from nha_trang_laundry_observability import Telemetry, current_trace_id
from nha_trang_laundry_worker import InternalOutboxWorker
from nha_trang_laundry_worker.host import WorkerSettings, WorkerSupervisor
from nha_trang_laundry_worker.main import create_app
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError


@pytest.fixture
def database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(value) as connection:
        apply_migrations(connection)
    return value


@pytest.fixture
def postgres_connection(database_url: str) -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(database_url) as connection:
        yield connection


def test_liveness_is_non_authoritative_and_missing_database_fails_readiness() -> None:
    settings = WorkerSettings(database_url=None, worker_poll_interval_seconds=0.05)
    supervisor = WorkerSupervisor(settings)

    with TestClient(create_app(settings, supervisor)) as client:
        live = client.get("/livez")
        ready = client.get("/readyz")

    assert live.status_code == 200
    assert live.json() == {
        "status": "alive",
        "automation": "disabled",
        "provider_send_available": False,
    }
    assert ready.status_code == 503
    assert ready.json()["error_code"] == "DATABASE_NOT_CONFIGURED"
    assert supervisor.snapshot().running is False


def test_supervisor_processes_one_fixed_internal_event_and_stops_cleanly(
    database_url: str,
    postgres_connection: psycopg.Connection[Any],
) -> None:
    handled = threading.Event()
    event_id = _insert_internal_event(postgres_connection)
    worker = InternalOutboxWorker({"order.state_transitioned.v1": lambda _: handled.set()})
    settings = WorkerSettings(
        database_url=database_url,
        worker_poll_interval_seconds=5,
        worker_recovery_interval_seconds=60,
        worker_internal_outbox_enabled=True,
    )
    supervisor = WorkerSupervisor(settings, internal_worker=worker)

    with TestClient(create_app(settings, supervisor)) as client:
        assert client.get("/readyz").status_code == 200
        assert handled.wait(timeout=2)
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json()["provider_send_available"] is False
        assert response.json()["automatic_sends_enabled"] is False

    assert supervisor.snapshot().running is False
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, claim_token, lease_expires_at FROM outbox_events WHERE id = %s",
            (event_id,),
        )
        assert cursor.fetchone() == ("SENT", None, None)


def test_enabled_mode_without_fixed_registry_is_not_ready(database_url: str) -> None:
    settings = WorkerSettings(
        database_url=database_url,
        worker_poll_interval_seconds=0.05,
        worker_internal_outbox_enabled=True,
    )
    supervisor = WorkerSupervisor(settings)

    with TestClient(create_app(settings, supervisor)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["error_code"] == "INTERNAL_HANDLER_REGISTRY_UNAVAILABLE"


def test_agent_queue_without_fixed_cycle_is_not_ready(database_url: str) -> None:
    settings = WorkerSettings(
        database_url=database_url,
        worker_poll_interval_seconds=0.05,
        worker_agent_queue_enabled=True,
    )
    supervisor = WorkerSupervisor(settings)

    with TestClient(create_app(settings, supervisor)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["error_code"] == "AGENT_RUNTIME_UNAVAILABLE"
    assert response.json()["agent_provider_runtime_enabled"] is False


def test_database_outage_fails_readiness_without_changing_liveness() -> None:
    def unavailable(_: str) -> Any:
        raise psycopg.OperationalError("synthetic database outage")

    settings = WorkerSettings(
        database_url="postgresql://synthetic.invalid/local",
        worker_poll_interval_seconds=0.05,
    )
    supervisor = WorkerSupervisor(settings, connection_factory=unavailable)

    with TestClient(create_app(settings, supervisor)) as client:
        assert client.get("/livez").status_code == 200
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["error_code"] == "DATABASE_UNAVAILABLE"


def test_unknown_internal_handler_is_dead_lettered_without_dynamic_loading(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    event_id = _insert_internal_event(postgres_connection)

    result = InternalOutboxWorker({}).run_once(postgres_connection)

    assert result.event_id == str(event_id)
    assert result.status == "DEAD"
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT event.status, dead.last_error_class, dead.replay_eligible
            FROM outbox_events AS event
            JOIN dead_letter_events AS dead ON dead.outbox_event_id = event.id
            WHERE event.id = %s
            """,
            (event_id,),
        )
        assert cursor.fetchone() == ("DEAD", "HANDLER_NOT_CONFIGURED", False)


def test_shutdown_deadline_revokes_inflight_handler_claim(
    database_url: str,
    postgres_connection: psycopg.Connection[Any],
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_handler(_: Any) -> None:
        entered.set()
        assert release.wait(timeout=2)

    event_id = _insert_internal_event(postgres_connection)
    settings = WorkerSettings(
        database_url=database_url,
        worker_poll_interval_seconds=5,
        worker_recovery_interval_seconds=60,
        worker_internal_outbox_enabled=True,
    )
    supervisor = WorkerSupervisor(
        settings,
        internal_worker=InternalOutboxWorker({"order.state_transitioned.v1": blocking_handler}),
    )
    supervisor.start()
    assert entered.wait(timeout=2)

    assert supervisor.stop(timeout_seconds=0.01) is False
    assert supervisor.snapshot().authority_revoked is True
    release.set()
    assert supervisor.stop(timeout_seconds=2) is True

    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT status, claim_token FROM outbox_events WHERE id = %s", (event_id,))
        status_row = cursor.fetchone()
    assert status_row is not None
    assert status_row[0] == "PROCESSING"
    assert status_row[1] is not None

    repository = OutboxRepository()
    recovery_time = datetime.now(UTC) + timedelta(minutes=1)
    recovered: UUID | None = None
    for _ in range(100):
        recovered = repository.recover_expired_internal(
            postgres_connection,
            worker_role=ActorRole.OUTBOX_WORKER,
            now=recovery_time,
        )
        if recovered in (None, event_id):
            break
    assert recovered == event_id
    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT status FROM outbox_events WHERE id = %s", (event_id,))
        assert cursor.fetchone() == ("DEAD",)


@pytest.mark.parametrize(
    "field",
    (
        "feature_public_channels_enabled",
        "feature_automated_sends_enabled",
        "feature_agent_runtime_enabled",
    ),
)
def test_worker_settings_reject_release_capability_flags(field: str) -> None:
    with pytest.raises(ValidationError, match="unreleased external capabilities"):
        if field == "feature_public_channels_enabled":
            WorkerSettings(feature_public_channels_enabled=True)
        elif field == "feature_automated_sends_enabled":
            WorkerSettings(feature_automated_sends_enabled=True)
        else:
            WorkerSettings(feature_agent_runtime_enabled=True)


def test_internal_worker_continues_durable_w3c_parent_without_business_identifiers() -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Telemetry(
        meter_provider=MeterProvider(),
        tracer_provider=tracer_provider,
        instrumentation_name="test.worker.propagation",
    )
    event_id = UUID("00000000-0000-0000-0000-000000000903")
    claim_token = UUID("00000000-0000-0000-0000-000000000904")
    event = ClaimedOutboxEvent(
        event_id=event_id,
        claim_token=claim_token,
        aggregate_type="TEST",
        aggregate_id=UUID("00000000-0000-0000-0000-000000000905"),
        event_type="order.state_transitioned.v1",
        payload={},
        idempotency_key="synthetic:trace",
        correlation_id=UUID("00000000-0000-0000-0000-000000000906"),
        attempt_count=1,
        lease_expires_at=datetime(2026, 8, 2, 1, tzinfo=UTC),
        traceparent="00-00000000000000000000000000000901-0000000000000902-01",
        tracestate=None,
    )

    class TraceRepository(OutboxRepository):
        def claim_next_internal(
            self,
            connection: Any,
            *,
            worker_role: ActorRole,
            now: datetime | None = None,
        ) -> ClaimedOutboxEvent | None:
            del connection, worker_role, now
            return event

        def complete_internal(
            self,
            connection: Any,
            *,
            event_id: UUID,
            claim_token: UUID,
            worker_role: ActorRole,
            completed_at: datetime | None = None,
        ) -> None:
            del connection, event_id, claim_token, worker_role, completed_at

    observed_trace_ids: list[str | None] = []
    worker = InternalOutboxWorker(
        {event.event_type: lambda _: observed_trace_ids.append(current_trace_id())},
        repository=TraceRepository(),
        telemetry=telemetry,
    )

    assert worker.run_once(object()).status == "COMPLETED"
    assert observed_trace_ids == ["00000000000000000000000000000901"]
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].parent is not None and spans[0].parent.span_id == 0x902
    assert spans[0].attributes is not None
    assert "traceparent" not in spans[0].attributes


def _insert_internal_event(
    connection: psycopg.Connection[Any],
    *,
    occurred_at: datetime | None = None,
) -> UUID:
    event_id = uuid4()
    timestamp = occurred_at or _before_pending_internal_event(connection)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                correlation_id, occurred_at, available_at
            ) VALUES (
                %s, 'TEST', %s, 'order.state_transitioned.v1', '{}'::jsonb,
                %s, %s, %s, %s
            )
            """,
            (
                event_id,
                uuid4(),
                f"worker-host:{event_id}",
                uuid4(),
                timestamp,
                timestamp,
            ),
        )
    return event_id


def _before_pending_internal_event(connection: psycopg.Connection[Any]) -> datetime:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT min(available_at)
            FROM outbox_events
            WHERE status = 'PENDING'
              AND event_type = ANY(%s)
            """,
            (list(sorted(INTERNAL_EVENT_TYPES)),),
        )
        row = cursor.fetchone()
    earliest = None if row is None else row[0]
    if not isinstance(earliest, datetime):
        return datetime(1970, 1, 1, tzinfo=UTC)
    return earliest - timedelta(days=1)
