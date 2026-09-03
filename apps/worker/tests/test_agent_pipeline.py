"""AGENT-PIPELINE-001: a queued job runs end to end through the assembled runtime.

The final section (TOOL-BACKEND-001) runs the same pipeline against the real Tool Facade backed
by `DomainAgentToolBackend`, through an in-process loopback transport that drives the actual
ASGI app — authentication, the policy point, bound-path, header and schema gates all execute.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Generator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from nha_trang_laundry_agent_tools.auth import AgentAuthSettings, AgentRunnerTokenVerifier
from nha_trang_laundry_agent_tools.backend import DomainAgentToolBackend
from nha_trang_laundry_agent_tools.facade import (
    AgentFacadeService,
    get_agent_facade_service,
    get_agent_verifier,
)
from nha_trang_laundry_agent_tools.main import app as facade_app
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    ReleaseCapability,
)
from nha_trang_laundry_db.agent_runs import AgentRunEnqueueCommand, AgentRunRepository
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.pricebook import publish_pricebook
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_worker.agent_runner import (
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentRuntimeInvocation,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
    AgentToolTransport,
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

ROOT = Path(__file__).resolve().parents[3]
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


def _ensure_store(connection: Any, store_id: UUID) -> None:
    """`STORE-REGISTRY-001`: `store_id` is a foreign key, so the shop exists first.

    The deploy-day runbook runs `scripts/bootstrap_store.py` first, and this is the fixture standing
    in for that step rather than an INSERT that skips it.
    """

    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )


def enqueue(
    connection: psycopg.Connection[Any],
    *,
    store_id: UUID | None = None,
    contact_binding_id: UUID | None = None,
    conversation_binding_id: UUID | None = None,
    order_request_id: UUID | None = None,
    bound_row_version: int = 0,
) -> AgentRunEnqueueCommand:
    run_store_id = store_id or uuid4()
    _ensure_store(connection, run_store_id)
    command = AgentRunEnqueueCommand(
        agent_run_id=uuid4(),
        source_webhook_event_id=None,
        organization_id=uuid4(),
        store_id=run_store_id,
        channel="INTERNAL_TEST",
        conversation_binding_id=conversation_binding_id or uuid4(),
        contact_binding_id=contact_binding_id or uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        deployment_stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        runtime_registry_version="1.0.0-eval",
        runtime_registry_hash=REGISTRY_HASH,
        prompt_bundle_version="prompt-v1",
        prompt_bundle_hash=PROMPT_HASH,
        tool_contract_hash=CURRENT_TOOL_CONTRACT_HASH,
        correlation_id=uuid4(),
        order_request_id=order_request_id,
        bound_row_version=bound_row_version,
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
        # A worker still running here holds its own connection, and its locks block the next
        # test's reset with no clue where they came from. TEST-ISOLATION-001.
        assert not worker.is_alive(), "a concurrency worker outlived its join"

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


def test_the_full_shadow_loop_reaches_a_human_review_queue(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Queue to draft to attributed approval to audit chain, with no manual step in between.

    This is the loop `SHADOW-001` needs: without the draft reaching a review queue there is nothing
    for a human to approve, and the fourteen-day pilot has nowhere to happen.
    """
    from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
    from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository

    # The queue starts empty, so the run claimed below is unambiguously the one enqueued here and
    # the timeline assertions describe it rather than a neighbour. Asserting that rather than
    # draining for it is the point of `TEST-ISOLATION-001`.
    assert pipeline().run_cycle(postgres_connection, lambda: True).status == "IDLE"

    command = enqueue(postgres_connection)
    result = pipeline().run_cycle(postgres_connection, lambda: True)
    assert result.agent_run_id == str(command.agent_run_id)

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT store_id, draft_text FROM agent_drafts WHERE agent_run_id = %s",
            (result.agent_run_id,),
        )
        store_id, draft_text = _row(cursor)
    assert draft_text  # the agent's proposal is now reviewable rather than only counted

    owner_id = uuid4()
    approver_id = uuid4()
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        for staff_id, role in ((owner_id, "OWNER_ADMIN"), (approver_id, "OPS_APPROVER")):
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                """,
                (staff_id, f"oidc-{staff_id}", NOW),
            )
            # The role row is written as well as carried on the principal: STORE-ASSIGNMENT-001
            # made assign_store check the database rather than trust the session object, so an
            # OWNER_ADMIN that existed only in memory was claiming a grant nobody had made.
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_id, role, NOW),
            )
    owner = StaffPrincipal(
        staff_user_id=owner_id,
        oidc_subject=f"oidc-{owner_id}",
        roles=frozenset({StaffRole.OWNER_ADMIN}),
        mfa_verified=True,
        session_id=uuid4(),
    )
    approver = StaffPrincipal(
        staff_user_id=approver_id,
        oidc_subject=f"oidc-{approver_id}",
        roles=frozenset({StaffRole.OPS_APPROVER}),
        mfa_verified=True,
        session_id=uuid4(),
    )
    repository = ShadowConsoleRepository()
    repository.assign_store(
        postgres_connection,
        staff_user_id=approver_id,
        store_id=store_id,
        principal=owner,
        correlation_id=uuid4(),
    )

    pending = repository.list_pending_drafts(
        postgres_connection, store_id=store_id, principal=approver
    )
    assert UUID(str(result.agent_run_id)) in {item.agent_run_id for item in pending}

    repository.decide_draft(
        postgres_connection,
        agent_run_id=UUID(str(result.agent_run_id)),
        decision="APPROVE",
        principal=approver,
        correlation_id=uuid4(),
    )

    timeline = repository.audit_timeline(
        postgres_connection,
        store_id=store_id,
        aggregate_id=UUID(str(result.agent_run_id)),
        principal=approver,
    )
    # The timeline is the whole chain for the run, not only the review: the run's own lifecycle
    # events appear alongside the draft and its approval, which is what an auditor needs.
    actions = [entry.action for entry in timeline]
    assert "AGENT_RUN_COMPLETE_DRAFT" in actions
    assert actions.index("AGENT_DRAFT_RECORD") < actions.index("AGENT_DRAFT_APPROVE")
    assert actions[-1] == "AGENT_DRAFT_APPROVE"
    assert timeline[-1].actor_id == approver_id
    assert (
        repository.list_pending_drafts(postgres_connection, store_id=store_id, principal=approver)
        == ()
    )


# --- TOOL-BACKEND-001: the same pipeline against the real domain backend ---------------------
#
# Every other transport in this file is a test double. `LoopbackFacadeTransport` is not a double
# of the facade: it drives the facade's real ASGI app, so the runner's minted bearer is verified,
# the policy point, bound-path, header and schema gates all execute, and only then does
# `DomainAgentToolBackend` dispatch to the domain. The dependency overrides swap wiring, never
# gates — the same override mechanism test_facade.py uses.

FREE_TEXT_MARKER = "CUSTOMER-FREE-TEXT-7f3a9c1e"
CHAIN_OF_THOUGHT_MARKER = "ENCRYPTED-REASONING-b28d4f06"


class LoopbackFacadeTransport(AgentToolTransport):
    """Forward the bridge's request through the real facade app, in process."""

    def __init__(self, client: TestClient) -> None:
        self._client = client
        self.requests: list[AgentToolForwardRequest] = []

    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        self.requests.append(request)
        headers = dict(request.headers)
        if request.method == "GET":
            response = self._client.get(request.path, headers=headers)
        else:
            response = self._client.post(request.path, headers=headers, json=dict(request.body))
        return AgentToolForwardResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.json(),
        )


def _domain_wired_runner_and_transport(
    database_url: str,
) -> tuple[AgentRunner, LoopbackFacadeTransport]:
    """Wire the facade to the domain backend with a fresh runner key pair.

    The runner mints real Ed25519 bearers; the facade verifies them with the matching public
    key. Nothing here bypasses a gate — the only substitutions are the two dependency-injection
    points the facade itself declares.
    """
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    runner = AgentRunner(
        AgentRunnerTokenIssuer(
            issuer="https://control-plane.test",
            audience="agent-tool-facade",
            private_key=private_pem,
        )
    )
    verifier = AgentRunnerTokenVerifier(
        AgentAuthSettings(
            agent_runner_jwt_issuer="https://control-plane.test",
            agent_runner_jwt_audience="agent-tool-facade",
            agent_runner_jwt_public_key=public_pem,
        )
    )
    facade_app.dependency_overrides[get_agent_verifier] = lambda: verifier
    facade_app.dependency_overrides[get_agent_facade_service] = lambda: AgentFacadeService(
        DomainAgentToolBackend(database_url=database_url)
    )
    return runner, LoopbackFacadeTransport(TestClient(facade_app))


def _backend_pipeline(
    script: list[Any], runner: AgentRunner, transport: LoopbackFacadeTransport
) -> AgentPipeline:
    return build_agent_pipeline(
        config=config(),
        provider_transport=ScriptedResponsesTransport(script),
        tool_transport=transport,
        runner=runner,
        instructions=INSTRUCTIONS,
        input_text_for=lambda invocation: f"run {invocation.run_id}",
    )


def _tool_call_response(
    call_id: str,
    operation: str,
    arguments: Mapping[str, Any],
    *,
    with_reasoning: bool = False,
) -> dict[str, Any]:
    output: list[dict[str, Any]] = []
    if with_reasoning:
        # Encrypted reasoning stays in process memory; the marker proves it never persists.
        output.append(
            {
                "type": "reasoning",
                "id": f"rs-{call_id}",
                "encrypted_content": CHAIN_OF_THOUGHT_MARKER,
            }
        )
    output.append(
        {
            "type": "function_call",
            "call_id": call_id,
            "name": operation,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        }
    )
    return {
        "status": "completed",
        "parallel_tool_calls": False,
        "output": output,
        "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 20},
    }


def _publish_pricebook(database_url: str) -> None:
    # The facade backend reads through its own connection, so the publication must be committed
    # — the harness fixture connection is deliberately not autocommit, and an uncommitted
    # publish would be invisible to the code under test (the API suite documents this trap).
    with psycopg.connect(database_url, autocommit=True) as connection:
        publish_pricebook(
            connection,
            actor_id=uuid4(),
            source=(ROOT / "templates/services-pricebook.csv").read_bytes(),
        )


def _tool_call_rows(
    connection: psycopg.Connection[Any], agent_run_id: str
) -> list[tuple[Any, ...]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT operation_id, request_fingerprint, result_status_code, result_code,
                   safe_summary
            FROM agent_tool_calls WHERE agent_run_id = %s ORDER BY sequence_number
            """,
            (agent_run_id,),
        )
        return [tuple(row) for row in cursor.fetchall()]


def test_a_full_pipeline_run_with_the_domain_backend_executes_real_tools(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Queue to draft, with the tool calls answered by the domain rather than a double.

    Run 1 resolves the catalog and creates the intake draft (one mutation — the Shadow budget
    allows exactly one). Run 2 is enqueued already bound to the request run 1 created, and its
    advisory operations travel the bound-path gate with the run's own claims.
    """
    database_url = os.environ["DATABASE_URL"]
    _publish_pricebook(database_url)
    store_id, contact_id, conversation_id = uuid4(), uuid4(), uuid4()
    _ensure_store(postgres_connection, store_id)
    runner, transport = _domain_wired_runner_and_transport(database_url)
    try:
        first = enqueue(
            postgres_connection,
            store_id=store_id,
            contact_binding_id=contact_id,
            conversation_binding_id=conversation_id,
        )
        first_result = _backend_pipeline(
            [
                _tool_call_response(
                    "call-catalog-1",
                    "catalogResolve",
                    {
                        "query": f"giặt chăn {FREE_TEXT_MARKER}",
                        "locale": "vi-VN",
                        "known_attributes": None,
                    },
                ),
                _tool_call_response(
                    "call-create-1",
                    "orderRequestCreate",
                    {
                        "customer_intent": f"Giặt chăn, {FREE_TEXT_MARKER}",
                        "locale": "vi-VN",
                        "source_provider_message_ids": ["msg-1"],
                    },
                ),
                draft_response(),
            ],
            runner,
            transport,
        ).run_cycle(postgres_connection, lambda: True)

        assert first_result.status == "DRAFT_REQUIRES_HUMAN"
        assert first_result.agent_run_id == str(first.agent_run_id)
        first_calls = _tool_call_rows(postgres_connection, first_result.agent_run_id or "")
        assert [row[0] for row in first_calls] == ["catalogResolve", "orderRequestCreate"]
        assert [row[2] for row in first_calls] == [200, 201]

        with postgres_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, store_id, contact_binding_id, conversation_binding_id, status,
                       row_version
                FROM order_requests
                """
            )
            created_row = _row(cursor)
        assert created_row[1:] == (store_id, contact_id, conversation_id, "DRAFT", 1)
        created_request_id = UUID(str(created_row[0]))

        # Run 2 starts bound to the request run 1 created; both advisories are read-only, so
        # the Shadow mutation budget is untouched while the bound-path gate is exercised.
        second = enqueue(
            postgres_connection,
            store_id=store_id,
            contact_binding_id=contact_id,
            conversation_binding_id=conversation_id,
            order_request_id=created_request_id,
            bound_row_version=1,
        )
        second_result = _backend_pipeline(
            [
                _tool_call_response(
                    "call-delivery-1",
                    "deliveryEvaluate",
                    {
                        "fulfillment_mode": "PICKUP_AND_RETURN",
                        "planned_transport_weight_kg": "5",
                    },
                ),
                _tool_call_response(
                    "call-capacity-1", "capacityCheck", {"requested_ready_at": None}
                ),
                draft_response("Phí giao nhận cần nhân viên xác nhận trước khi báo khách."),
            ],
            runner,
            transport,
        ).run_cycle(postgres_connection, lambda: True)

        assert second_result.status == "DRAFT_REQUIRES_HUMAN"
        assert second_result.agent_run_id == str(second.agent_run_id)
        second_calls = _tool_call_rows(postgres_connection, second_result.agent_run_id or "")
        assert [row[0] for row in second_calls] == ["deliveryEvaluate", "capacityCheck"]
        # The delivery engine's unresolved-distance outcome reaches the ledger verbatim.
        assert [row[3] for row in second_calls] == ["REQUIRE_HUMAN", "REQUIRE_HUMAN"]
        with postgres_connection.cursor() as cursor:
            cursor.execute(
                "SELECT draft_text FROM agent_drafts WHERE agent_run_id = %s",
                (second_result.agent_run_id,),
            )
            assert "nhân viên xác nhận" in str(_row(cursor)[0])
    finally:
        facade_app.dependency_overrides.clear()


def test_a_backend_backed_run_records_only_redacted_tool_ledger_and_pure_artifacts(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The ledger keeps fingerprints and bounded summaries; nothing else may persist.

    Markers ride in the model-visible tool arguments and in the provider's encrypted reasoning.
    After the run, every table the run could have touched is scanned for them: the tool ledger,
    the run summary, the persisted draft, the domain rows the backend wrote, and the
    event/audit/outbox/idempotency ledgers.
    """
    database_url = os.environ["DATABASE_URL"]
    _publish_pricebook(database_url)
    runner, transport = _domain_wired_runner_and_transport(database_url)
    try:
        command = enqueue(postgres_connection)
        result = _backend_pipeline(
            [
                _tool_call_response(
                    "call-catalog-1",
                    "catalogResolve",
                    {
                        "query": f"giặt chăn {FREE_TEXT_MARKER}",
                        "locale": "vi-VN",
                        "known_attributes": None,
                    },
                    with_reasoning=True,
                ),
                _tool_call_response(
                    "call-create-1",
                    "orderRequestCreate",
                    {
                        "customer_intent": f"Giặt chăn, {FREE_TEXT_MARKER}",
                        "locale": "vi-VN",
                        "source_provider_message_ids": ["msg-1"],
                    },
                    with_reasoning=True,
                ),
                draft_response(),
            ],
            runner,
            transport,
        ).run_cycle(postgres_connection, lambda: True)
    finally:
        facade_app.dependency_overrides.clear()

    assert result.status == "DRAFT_REQUIRES_HUMAN"
    assert result.agent_run_id == str(command.agent_run_id)
    rows = _tool_call_rows(postgres_connection, result.agent_run_id or "")
    assert [row[0] for row in rows] == ["catalogResolve", "orderRequestCreate"]
    for _operation, fingerprint, status_code, result_code, safe_summary in rows:
        # The arguments live only inside a one-way fingerprint; the summary is exactly the
        # three bounded fields `_DatabaseToolCallObserver` is allowed to write.
        assert str(fingerprint).startswith("sha256:")
        assert set(safe_summary) == {"result_code", "status_code", "top_level_keys"}
        assert safe_summary["result_code"] == result_code
        assert safe_summary["status_code"] == status_code
        assert set(safe_summary["top_level_keys"]) <= {
            "ok",
            "trace_id",
            "decision",
            "data",
            "error",
        }

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT result_safe_summary FROM agent_runs WHERE id = %s",
            (result.agent_run_id,),
        )
        summary = _row(cursor)[0]
    evidence = summary["runtime_evidence"]
    assert evidence["chain_of_thought_persisted"] is False
    assert evidence["provider_response_id_persisted"] is False
    assert evidence["tool_call_count"] == 2

    prohibited = (
        FREE_TEXT_MARKER,
        CHAIN_OF_THOUGHT_MARKER,
        "call-catalog-1",  # a provider-issued call id is not server evidence either
        "call-create-1",
    )
    with postgres_connection.cursor() as cursor:
        for table in (
            "agent_runs",
            "agent_tool_calls",
            "agent_drafts",
            "order_requests",
            "domain_events",
            "audit_events",
            "outbox_events",
            "command_idempotency_records",
        ):
            cursor.execute(f"SELECT row_to_json(t)::text FROM {table} AS t")
            for stored in cursor.fetchall():
                for marker in prohibited:
                    assert marker not in str(stored[0]), f"{table} persisted {marker}"
