"""AGENT-PIPELINE-001: a queued job runs end to end through the assembled runtime."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    ReleaseCapability,
)
from nha_trang_laundry_db.agent_runs import AgentRunEnqueueCommand, AgentRunRepository
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_worker.agent_runner import (
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentRuntimeInvocation,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
)
from nha_trang_laundry_worker.host import WorkerSettings, WorkerSupervisor
from nha_trang_laundry_worker.pipeline import (
    DISABLED_STATUS,
    STOPPED_STATUS,
    AgentPipeline,
    CapturingEvidenceSink,
    PipelineConfigurationError,
    build_agent_cycle,
    build_agent_pipeline,
)
from nha_trang_laundry_worker.responses_runtime import (
    CURRENT_TOOL_CONTRACT_HASH,
    ResponsesPriceTable,
    ResponsesRuntimeConfig,
    ResponsesTransportTimeout,
    ScriptedResponsesTransport,
)

NOW = datetime.now(UTC)
INSTRUCTIONS = "Bạn chỉ soạn bản nháp. Không tính tiền, không gửi, không quyết định chính sách."
REGISTRY_HASH = f"sha256:{'a' * 64}"
PROMPT_HASH = f"sha256:{'b' * 64}"


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _instructions_hash() -> str:
    from hashlib import sha256

    return f"sha256:{sha256(INSTRUCTIONS.encode('utf-8')).hexdigest()}"


def config(**overrides: Any) -> ResponsesRuntimeConfig:
    values: dict[str, Any] = {
        "runtime_id": "responses-pipeline",
        "model_id": "gpt-test",
        "immutable_model_release": "gpt-test-2026-08-01",
        "reasoning_effort": "low",
        "runtime_registry_hash": REGISTRY_HASH,
        "prompt_bundle_version": "prompt-v1",
        "prompt_bundle_hash": PROMPT_HASH,
        "prompt_instructions_hash": _instructions_hash(),
        "tool_contract_hash": CURRENT_TOOL_CONTRACT_HASH,
        "max_model_calls": 3,
        "max_input_tokens": 8000,
        "max_output_tokens": 1200,
        "max_turn_cost_usd": "0.02",
        "price_table": ResponsesPriceTable.assemble(
            price_table_version="price-v1",
            model_id="gpt-test",
            immutable_model_release="gpt-test-2026-08-01",
            effective_at=NOW - timedelta(days=1),
            input_cost_per_million_usd="1",
            cached_input_cost_per_million_usd="0.25",
            output_cost_per_million_usd="2",
        ),
    }
    values.update(overrides)
    return ResponsesRuntimeConfig(**values)


def draft_response(draft: str = "Đã ghi nhận, nhân viên sẽ xác nhận.") -> dict[str, Any]:
    envelope = {"disposition": "DRAFT_REQUIRES_HUMAN", "draft_text": draft, "reason_code": None}
    return {
        "status": "completed",
        "parallel_tool_calls": False,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(envelope)}],
            }
        ],
        "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 20},
    }


class RecordingToolTransport:
    def __init__(self) -> None:
        self.requests: list[AgentToolForwardRequest] = []

    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        self.requests.append(request)
        return AgentToolForwardResponse(status_code=200, headers={}, body={"ok": True})


def runner() -> AgentRunner:
    private = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    return AgentRunner(
        AgentRunnerTokenIssuer(
            issuer="https://control-plane.test",
            audience="agent-tool-facade",
            private_key=private,
        )
    )


def pipeline(
    *, script: list[Any] | None = None, sink: CapturingEvidenceSink | None = None
) -> AgentPipeline:
    return build_agent_pipeline(
        config=config(),
        provider_transport=ScriptedResponsesTransport(
            script if script is not None else [draft_response()]
        ),
        tool_transport=RecordingToolTransport(),
        runner=runner(),
        instructions=INSTRUCTIONS,
        input_text_for=lambda invocation: f"run {invocation.run_id}",
        evidence_sink=sink,
    )


def enqueue(connection: psycopg.Connection[Any]) -> AgentRunEnqueueCommand:
    command = AgentRunEnqueueCommand(
        agent_run_id=uuid4(),
        source_webhook_event_id=None,
        organization_id=uuid4(),
        store_id=uuid4(),
        channel="INTERNAL_TEST",
        conversation_binding_id=uuid4(),
        contact_binding_id=uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        deployment_stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        runtime_registry_version="1.0.0-eval",
        runtime_registry_hash=REGISTRY_HASH,
        prompt_bundle_version="prompt-v1",
        prompt_bundle_hash=PROMPT_HASH,
        tool_contract_hash=CURRENT_TOOL_CONTRACT_HASH,
        correlation_id=uuid4(),
        created_at=_before_pending(connection),
    )
    AgentRunRepository().enqueue(connection, command)
    return command


def _before_pending(connection: psycopg.Connection[Any]) -> datetime:
    with connection.cursor() as cursor:
        cursor.execute("SELECT now() - interval '1 minute'")
        row = cursor.fetchone()
    assert row is not None
    return cast(datetime, row[0])


def _row(cursor: Any) -> tuple[Any, ...]:
    row = cursor.fetchone()
    assert row is not None
    return cast(tuple[Any, ...], row)


# --- the pipeline exists and runs ----------------------------------------------------------


def test_a_queued_job_runs_to_draft_and_persists_redacted_runtime_evidence(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    enqueue(postgres_connection)
    assembled = pipeline()

    result = assembled.run_cycle(postgres_connection, lambda: True)

    assert result.status == "DRAFT_REQUIRES_HUMAN"
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, result_safe_summary FROM agent_runs WHERE id = %s",
            (result.agent_run_id,),
        )
        run_status, summary = _row(cursor)
    assert run_status == "DRAFT_REQUIRES_HUMAN"
    evidence = summary["runtime_evidence"]
    assert evidence["terminal_outcome"] == "DRAFT"
    assert evidence["provider_backed"] is False
    assert evidence["retry_count"] == 0
    assert evidence["bridge_revoked"] is True
    assert evidence["chain_of_thought_persisted"] is False
    assert evidence["provider_response_id_persisted"] is False
    assert Decimal(str(evidence["settled_cost_usd"])) >= 0


def test_no_prompt_draft_or_payload_reaches_the_persisted_evidence(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    secret_draft = "SENSITIVE-DRAFT-TEXT-DO-NOT-PERSIST"
    enqueue(postgres_connection)
    assembled = pipeline(script=[draft_response(secret_draft)])

    result = assembled.run_cycle(postgres_connection, lambda: True)

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT result_safe_summary FROM agent_runs WHERE id = %s", (result.agent_run_id,)
        )
        summary = json.dumps(_row(cursor)[0])
    assert secret_draft not in summary
    assert INSTRUCTIONS not in summary
    assert "input_text" not in summary
    assert "reasoning" not in summary


def test_the_context_packet_is_bound_to_the_run_being_executed(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """A packet is bound to its run; a different session yields a different session hash."""
    from nha_trang_laundry_worker.pipeline import RunScopedContextLoader

    loader = RunScopedContextLoader(
        config=config(), instructions=INSTRUCTIONS, input_text_for=lambda _: "x"
    )
    invocation = AgentRuntimeInvocation(
        run_id=uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        session_key="session-abc",
        bridge_token="token",
    )

    context = loader.load(invocation)

    assert context.run_id == invocation.run_id
    assert context.capability is invocation.capability
    other = AgentRuntimeInvocation(
        run_id=uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        session_key="session-other",
        bridge_token="token",
    )
    assert loader.load(other).session_key_hash != context.session_key_hash


# --- budgets and terminal codes ------------------------------------------------------------


def test_a_transport_timeout_lands_require_human_with_its_terminal_code(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    enqueue(postgres_connection)
    sink = CapturingEvidenceSink()
    assembled = pipeline(script=[ResponsesTransportTimeout("PROVIDER_TIMEOUT")], sink=sink)

    result = assembled.run_cycle(postgres_connection, lambda: True)

    # A provider timeout is not a crash: the runtime settles the reservation, revokes the bridge and
    # returns the deterministic handoff, so the run lands REQUIRE_HUMAN carrying its terminal code.
    assert result.status == "DRAFT_REQUIRES_HUMAN"
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT result_safe_summary FROM agent_runs WHERE id = %s", (result.agent_run_id,)
        )
        evidence = _row(cursor)[0]["runtime_evidence"]
    assert evidence["terminal_outcome"] == "REQUIRE_HUMAN"
    assert evidence["terminal_code"] == "PROVIDER_TIMEOUT"
    assert evidence["bridge_revoked"] is True
    assert evidence["retry_count"] == 0


def test_a_model_call_budget_of_zero_is_rejected_before_any_provider_call() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        config(max_model_calls=0)


# --- flags stay closed ----------------------------------------------------------------------


def test_with_the_queue_flag_false_no_job_is_claimed(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    command = enqueue(postgres_connection)
    cycle = build_agent_cycle(pipeline(), enabled=False)

    assert cycle(postgres_connection, lambda: True) == DISABLED_STATUS

    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT status FROM agent_runs WHERE id = %s", (command.agent_run_id,))
        assert _row(cursor)[0] == "PENDING"


def test_a_revoked_authority_claims_nothing(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    command = enqueue(postgres_connection)
    assembled = pipeline()

    assert assembled.run_cycle(postgres_connection, lambda: False).status == STOPPED_STATUS

    with postgres_connection.cursor() as cursor:
        cursor.execute("SELECT status FROM agent_runs WHERE id = %s", (command.agent_run_id,))
        assert _row(cursor)[0] == "PENDING"


def test_worker_settings_still_refuse_to_enable_the_agent_runtime() -> None:
    settings = WorkerSettings()

    assert settings.worker_agent_queue_enabled is False
    assert settings.feature_agent_runtime_enabled is False
    with pytest.raises(ValueError, match="unreleased external capabilities"):
        WorkerSettings(feature_agent_runtime_enabled=True)


def test_a_provider_backed_transport_cannot_be_assembled_by_this_item() -> None:
    class ProviderBackedTransport:
        provider_backed = True

        def create(self, request: Any, *, timeout_seconds: float) -> Any:  # pragma: no cover
            raise AssertionError("must never be called")

    with pytest.raises(PipelineConfigurationError, match="PROVIDER-TRANSPORT-001"):
        build_agent_pipeline(
            config=config(),
            provider_transport=ProviderBackedTransport(),
            tool_transport=RecordingToolTransport(),
            runner=runner(),
            instructions=INSTRUCTIONS,
            input_text_for=lambda _: "x",
        )


# --- the supervisor accepts the cycle --------------------------------------------------------


def test_the_supervisor_accepts_an_injected_cycle_and_reports_it(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The hole `AgentCycle` left is fillable; the supervisor stops reporting it unavailable."""
    settings = WorkerSettings(
        database_url=os.environ["DATABASE_URL"], worker_agent_queue_enabled=True
    )
    supervisor = WorkerSupervisor(settings, agent_cycle=build_agent_cycle(pipeline(), enabled=True))

    supervisor.run_cycle()

    snapshot = supervisor.snapshot()
    assert snapshot.last_error_code != "AGENT_RUNTIME_UNAVAILABLE"


def test_a_supervisor_without_a_cycle_still_fails_closed() -> None:
    settings = WorkerSettings(database_url="postgresql://unused", worker_agent_queue_enabled=True)
    supervisor = WorkerSupervisor(settings)

    supervisor.run_cycle()

    assert supervisor.snapshot().last_error_code == "AGENT_RUNTIME_UNAVAILABLE"


# --- one claim, exactly once -----------------------------------------------------------------


def test_two_pipelines_racing_the_queue_never_claim_the_same_job(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The lease is the authority: two workers may both find work, but never the same work."""
    database_url = os.environ["DATABASE_URL"]
    enqueue(postgres_connection)
    barrier = threading.Barrier(2)
    claimed: list[str | None] = []
    lock = threading.Lock()

    def claim() -> None:
        assembled = pipeline()
        barrier.wait(timeout=10)
        with psycopg.connect(database_url) as connection:
            result = assembled.run_cycle(connection, lambda: True)
        with lock:
            claimed.append(result.agent_run_id)

    workers = [threading.Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)

    assert len(claimed) == 2
    identified = [run_id for run_id in claimed if run_id is not None]
    assert len(set(identified)) == len(identified)


def test_evidence_from_a_failed_run_is_not_attributed_to_the_next_run(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    sink = CapturingEvidenceSink()
    enqueue(postgres_connection)
    assembled = pipeline(script=[ResponsesTransportTimeout("PROVIDER_TIMEOUT")], sink=sink)
    failed = assembled.run_cycle(postgres_connection, lambda: True)

    assert sink.take() is None

    enqueue(postgres_connection)
    follow_up = pipeline(sink=CapturingEvidenceSink())
    succeeded = follow_up.run_cycle(postgres_connection, lambda: True)

    assert failed.agent_run_id != succeeded.agent_run_id
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT result_safe_summary FROM agent_runs WHERE id = %s", (failed.agent_run_id,)
        )
        first_code = _row(cursor)[0]["runtime_evidence"]["terminal_code"]
        cursor.execute(
            "SELECT result_safe_summary FROM agent_runs WHERE id = %s", (succeeded.agent_run_id,)
        )
        second_code = _row(cursor)[0]["runtime_evidence"]["terminal_code"]
    # Each run carries its own terminal code; the sink never leaks one run's record into the next.
    assert first_code == "PROVIDER_TIMEOUT"
    assert second_code != "PROVIDER_TIMEOUT"


def test_the_pipeline_exposes_the_parts_it_composed() -> None:
    assembled = pipeline()

    assert isinstance(assembled, AgentPipeline)
    assert assembled.runtime.provider_backed is False
