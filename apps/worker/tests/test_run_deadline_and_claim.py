"""AGENT-SHADOW-DEFECTS-001 F2: a run can always record how it ended, and nothing late can write.

Before this item the run deadline equalled the lease expiry (`claim_next` leases for 20 s
and `_job_from_claim` took `min(started + 20 s, lease)`), so a run that timed out reached `fail()`
at the exact instant its claim went stale: the failure raised from inside the `except`, escaped
`run_once`, and the real code (`MODEL_TIMEOUT`) was lost. A run finishing near the deadline
committed its draft and then failed `complete_draft`. The context loader granted the provider a
fresh 20 s regardless of the job, a late ledger failure was labelled `INVALID_PROVIDER_OUTPUT`, and
the persisted evidence did not say which run it belonged to.

The fake repository below enforces the same predicate as `agent_runs._assert_claim`
(`lease_expires_at >= now`) against the wall clock, so these tests fail for the reason the reviewer
found, not for a reason of their own.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    AgentToolOperation,
    ReleaseCapability,
)
from nha_trang_laundry_db.agent_runs import (
    AgentRunEnqueueCommand,
    AgentRunRepository,
    AgentRunStateError,
    ClaimedAgentRun,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_worker.agent_runner import (
    AgentRunJob,
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentRuntimeInvocation,
    AgentToolBridgeResult,
    AgentToolBridgeSession,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
)
from nha_trang_laundry_worker.durable_agent_worker import (
    RECORDING_MARGIN,
    DurableAgentRunWorker,
    _DatabaseToolCallObserver,
)
from nha_trang_laundry_worker.pipeline import (
    CapturingEvidenceSink,
    RunScopedContextLoader,
    build_agent_pipeline,
)
from nha_trang_laundry_worker.responses_runtime import (
    CURRENT_TOOL_CONTRACT_HASH,
    BoundedResponsesRuntime,
    ResponsesPriceTable,
    ResponsesRuntimeConfig,
    ResponsesRuntimeContext,
    ResponsesTransportTimeout,
    ScriptedResponsesTransport,
)

INSTRUCTIONS = "Chỉ soạn bản nháp."
REGISTRY_HASH = "sha256:" + "a" * 64
PROMPT_HASH = "sha256:" + "b" * 64


def _config() -> ResponsesRuntimeConfig:
    now = datetime.now(UTC)
    return ResponsesRuntimeConfig(
        runtime_id="responses-deadline-test",
        model_id="gpt-test",
        immutable_model_release="gpt-test-2026-08-01",
        reasoning_effort="low",
        runtime_registry_hash=REGISTRY_HASH,
        prompt_bundle_version="prompt-v1",
        prompt_bundle_hash=PROMPT_HASH,
        prompt_instructions_hash="sha256:" + sha256(INSTRUCTIONS.encode()).hexdigest(),
        tool_contract_hash=CURRENT_TOOL_CONTRACT_HASH,
        max_model_calls=3,
        max_input_tokens=8000,
        max_output_tokens=1200,
        max_turn_cost_usd="0.02",
        price_table=ResponsesPriceTable.assemble(
            price_table_version="price-v1",
            model_id="gpt-test",
            immutable_model_release="gpt-test-2026-08-01",
            effective_at=now - timedelta(days=1),
            input_cost_per_million_usd="1",
            cached_input_cost_per_million_usd="0.25",
            output_cost_per_million_usd="2",
        ),
    )


def _issuer() -> AgentRunnerTokenIssuer:
    key = (
        Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    return AgentRunnerTokenIssuer(
        issuer="https://control-plane.test", audience="agent-tool-facade", private_key=key
    )


def _final(draft: str = "Dạ, nhân viên sẽ xác nhận.") -> dict[str, Any]:
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
        "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5},
    }


def _tool_call() -> dict[str, Any]:
    return {
        "status": "completed",
        "parallel_tool_calls": False,
        "output": [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "catalogResolve",
                "arguments": json.dumps(
                    {"query": "giặt chăn", "locale": "vi-VN", "known_attributes": None}
                ),
            }
        ],
        "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5},
    }


class _CatalogTransport:
    def __init__(self) -> None:
        self.requests: list[AgentToolForwardRequest] = []

    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        self.requests.append(request)
        return AgentToolForwardResponse(
            status_code=200,
            headers={},
            body={
                "ok": True,
                "trace_id": "tr_12345678",
                "decision": {
                    "outcome": "REQUIRE_HUMAN",
                    "reason_codes": ["SHADOW_MODE_ALL_SENDS"],
                    "obligations": [],
                    "policy_version": "policy-eval-only-v1",
                    "snapshot_hash": f"sha256:{'a' * 64}",
                },
                "data": {"candidates": []},
            },
        )


class _LeaseEnforcingRepository:
    """Same predicate as `_assert_claim`, against the wall clock, not a frozen transaction."""

    def __init__(self, *, lease: timedelta) -> None:
        self._lease = lease
        self.events: list[tuple[str, str]] = []
        self.claimed: ClaimedAgentRun | None = None

    def claim_next(self, connection: Any, *, worker_role: Any, now: datetime) -> ClaimedAgentRun:
        del connection, worker_role
        config = _config()
        self.claimed = ClaimedAgentRun(
            agent_run_id=uuid4(),
            claim_token=uuid4(),
            organization_id=uuid4(),
            store_id=uuid4(),
            channel="INTERNAL_TEST",
            conversation_binding_id=uuid4(),
            contact_binding_id=uuid4(),
            capability=ReleaseCapability.INTERNAL_SHADOW,
            deployment_stage=AgentDeploymentStage.SHADOW,
            data_classification=AgentDataClassification.SYNTHETIC,
            runtime_registry_version="1.0.0-eval",
            runtime_registry_hash=config.runtime_registry_hash,
            prompt_bundle_version=config.prompt_bundle_version,
            prompt_bundle_hash=config.prompt_bundle_hash,
            tool_contract_hash=config.tool_contract_hash,
            order_request_id=None,
            public_code=None,
            bound_row_version=0,
            attempt_count=1,
            lease_expires_at=now + self._lease,
        )
        return self.claimed

    def _assert_claim(self, what: str) -> None:
        assert self.claimed is not None
        if self.claimed.lease_expires_at < datetime.now(UTC):
            self.events.append((what, "STALE_CLAIM"))
            raise AgentRunStateError("agent run claim is stale or unavailable")
        self.events.append((what, "OK"))

    def record_tool_call(self, connection: Any, entry: Any) -> None:
        del connection, entry
        self._assert_claim("record_tool_call")

    def complete_draft(self, connection: Any, **values: Any) -> None:
        del connection, values
        self._assert_claim("complete_draft")

    def fail(self, connection: Any, **values: Any) -> None:
        del connection
        self._assert_claim("fail:" + str(values["failure_code"]))


class _NullConnection:
    """No transaction is needed by the fake repository; the worker may still open one."""

    def transaction(self) -> Any:
        return _NullContext()


class _NullContext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


class _HangingRuntime:
    """A runtime that ignores every deadline; only the runner's own guard can stop it."""

    provider_backed = False

    def __init__(self) -> None:
        self.release = threading.Event()

    def invoke(self, invocation: AgentRuntimeInvocation, bridge: AgentToolBridgeSession) -> Any:
        del invocation, bridge
        self.release.wait(timeout=30)
        raise AssertionError("released only at teardown")


def test_a_timed_out_run_records_model_timeout_before_its_lease_expires() -> None:
    lease = RECORDING_MARGIN + timedelta(seconds=2)
    repository = _LeaseEnforcingRepository(lease=lease)
    worker = DurableAgentRunWorker(AgentRunner(_issuer()), repository=repository)  # type: ignore[arg-type]
    runtime = _HangingRuntime()
    try:
        result = worker.run_once(
            _NullConnection(),
            runtime=runtime,
            transport=_CatalogTransport(),
            correlation_id=uuid4(),
        )
    finally:
        runtime.release.set()

    assert result.status == "FAILED"
    # Recorded under a live claim, with its real code -- not lost to a stale-claim exception.
    assert repository.events == [("fail:MODEL_TIMEOUT", "OK")]


def test_a_lost_claim_is_a_named_terminal_status_not_an_escaping_exception() -> None:
    class _AlreadyRecovered(_LeaseEnforcingRepository):
        def fail(self, connection: Any, **values: Any) -> None:
            del connection
            self.events.append(("fail:" + str(values["failure_code"]), "STALE_CLAIM"))
            raise AgentRunStateError("agent run failure claim is stale")

    class _Refusing:
        provider_backed = False

        def invoke(self, invocation: Any, bridge: Any) -> Any:
            del invocation, bridge
            raise RuntimeError("boom")

    repository = _AlreadyRecovered(lease=timedelta(seconds=20))
    worker = DurableAgentRunWorker(AgentRunner(_issuer()), repository=repository)  # type: ignore[arg-type]

    result = worker.run_once(
        _NullConnection(),
        runtime=_Refusing(),
        transport=_CatalogTransport(),
        correlation_id=uuid4(),
    )

    assert result.status == "CLAIM_LOST"
    assert repository.claimed is not None
    assert result.agent_run_id == str(repository.claimed.agent_run_id)


def test_the_job_deadline_leaves_the_recording_margin_before_the_lease() -> None:
    seen: list[AgentRunJob] = []

    class _SpyRunner(AgentRunner):
        def execute(self, *, job: AgentRunJob, **values: Any) -> Any:
            seen.append(job)
            raise AssertionError("stop here")

    repository = _LeaseEnforcingRepository(lease=timedelta(seconds=20))
    worker = DurableAgentRunWorker(_SpyRunner(_issuer()), repository=repository)  # type: ignore[arg-type]
    started = datetime.now(UTC)
    worker.run_once(
        _NullConnection(),
        runtime=_HangingRuntime(),
        transport=_CatalogTransport(),
        correlation_id=uuid4(),
        now=started,
    )

    assert repository.claimed is not None
    assert seen[0].deadline_at == repository.claimed.lease_expires_at - RECORDING_MARGIN
    assert seen[0].deadline_at < repository.claimed.lease_expires_at


def test_the_provider_is_never_granted_more_time_than_the_job_has() -> None:
    granted: list[float] = []

    class _SlowProvider:
        provider_backed = False

        def create(self, request: Any, *, timeout_seconds: float) -> Any:
            del request
            granted.append(timeout_seconds)
            raise ResponsesTransportTimeout("PROVIDER_TIMEOUT")

    lease = RECORDING_MARGIN + timedelta(seconds=2)
    repository = _LeaseEnforcingRepository(lease=lease)
    assembled = build_agent_pipeline(
        config=_config(),
        provider_transport=_SlowProvider(),
        tool_transport=_CatalogTransport(),
        runner=AgentRunner(_issuer()),
        instructions=INSTRUCTIONS,
        input_text_for=lambda _: "khách hỏi",
        repository=repository,  # type: ignore[arg-type]
        draft_recorder=lambda *_: None,
    )

    result = assembled.run_cycle(_NullConnection(), lambda: True)

    assert result.status == "REQUIRE_HUMAN"
    # Two seconds of job time, not a fresh twenty: the runtime honours the job deadline.
    assert len(granted) == 1
    assert 0 < granted[0] <= 2.0
    assert repository.events == [("complete_draft", "OK")]


def test_the_context_loader_never_outlives_the_invocation_deadline() -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=3)
    loader = RunScopedContextLoader(
        config=_config(), instructions=INSTRUCTIONS, input_text_for=lambda _: "x"
    )
    context = loader.load(
        AgentRuntimeInvocation(
            run_id=uuid4(),
            capability=ReleaseCapability.INTERNAL_SHADOW,
            session_key="session",
            bridge_token="t" * 32,
            deadline_at=deadline,
        )
    )

    assert context.deadline_at < deadline


def _bridge(*, observer: Any = None) -> tuple[AgentToolBridgeSession, AgentRuntimeInvocation]:
    started = datetime.now(UTC)
    job = AgentRunJob(
        run_id=uuid4(),
        organization_id=uuid4(),
        store_id=uuid4(),
        channel="INTERNAL_TEST",
        conversation_binding_id=uuid4(),
        contact_binding_id=uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        started_at=started,
        deadline_at=started + timedelta(seconds=15),
    )
    bridge = AgentToolBridgeSession(
        job=job,
        bridge_token="b" * 32,
        issuer=_issuer(),
        transport=_CatalogTransport(),
        observer=observer,
    )
    invocation = AgentRuntimeInvocation(
        run_id=job.run_id,
        capability=job.capability,
        session_key=bridge.session_key,
        bridge_token="b" * 32,
        deadline_at=job.deadline_at,
    )
    return bridge, invocation


def _runtime(
    script: list[Any], sink: CapturingEvidenceSink
) -> tuple[BoundedResponsesRuntime, ScriptedResponsesTransport]:
    config = _config()
    provider = ScriptedResponsesTransport(script)
    runtime = BoundedResponsesRuntime(
        config=config,
        context_loader=RunScopedContextLoader(
            config=config, instructions=INSTRUCTIONS, input_text_for=lambda _: "khách hỏi"
        ),
        transport=provider,
        evidence_sink=sink,
    )
    return runtime, provider


def test_a_context_that_outlives_the_run_is_rejected_before_the_provider() -> None:
    class _GenerousLoader:
        """Builds a packet with a fresh twenty seconds, as the loader used to."""

        def load(self, invocation: AgentRuntimeInvocation) -> ResponsesRuntimeContext:
            return ResponsesRuntimeContext.assemble(
                run_id=invocation.run_id,
                capability=invocation.capability,
                session_key=invocation.session_key,
                config=_config(),
                deadline_at=datetime.now(UTC) + timedelta(seconds=20),
                instructions=INSTRUCTIONS,
                input_text="khách hỏi",
            )

    provider = ScriptedResponsesTransport([_final()])
    runtime = BoundedResponsesRuntime(
        config=_config(),
        context_loader=_GenerousLoader(),
        transport=provider,
        evidence_sink=CapturingEvidenceSink(),
    )
    bridge, invocation = _bridge()

    output = runtime.invoke(invocation, bridge)

    assert provider.requests == []
    assert output.terminal_code == "CONTEXT_DEADLINE_EXCEEDS_RUN"


def test_a_facade_result_arriving_after_revocation_never_reaches_model_or_ledger() -> None:
    recorded: list[Any] = []

    class _Ledger:
        def record(self, **values: Any) -> None:
            recorded.append(values)

    holder: dict[str, AgentToolBridgeSession] = {}

    class _RevokedInFlight(_CatalogTransport):
        def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
            response = super().send(request)
            holder["bridge"].close()  # the runner's deadline fires while the call is in flight
            return response

    started = datetime.now(UTC)
    job = AgentRunJob(
        run_id=uuid4(),
        organization_id=uuid4(),
        store_id=uuid4(),
        channel="INTERNAL_TEST",
        conversation_binding_id=uuid4(),
        contact_binding_id=uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        started_at=started,
        deadline_at=started + timedelta(seconds=15),
    )
    bridge = AgentToolBridgeSession(
        job=job,
        bridge_token="b" * 32,
        issuer=_issuer(),
        transport=_RevokedInFlight(),
        observer=_Ledger(),
    )
    holder["bridge"] = bridge
    invocation = AgentRuntimeInvocation(
        run_id=job.run_id,
        capability=job.capability,
        session_key=bridge.session_key,
        bridge_token="b" * 32,
        deadline_at=job.deadline_at,
    )
    sink = CapturingEvidenceSink()
    runtime, provider = _runtime([_tool_call(), _final()], sink)

    output = runtime.invoke(invocation, bridge)

    assert recorded == []
    assert len(provider.requests) == 1
    assert output.disposition == "REQUIRE_HUMAN"
    assert output.terminal_code == "TOOL_UNAVAILABLE"


def test_a_closed_run_ledger_refuses_a_late_write() -> None:
    repository = _LeaseEnforcingRepository(lease=timedelta(seconds=20))
    claimed = repository.claim_next(None, worker_role=None, now=datetime.now(UTC))
    observer = _DatabaseToolCallObserver(_NullConnection(), repository, claimed, uuid4())  # type: ignore[arg-type]
    observer.close()  # the worker has begun recording the run's outcome

    with pytest.raises(AgentRunStateError, match="no longer live"):
        observer.record(
            sequence_number=1,
            operation=AgentToolOperation.CATALOG_RESOLVE,
            arguments={"query": "x"},
            result=AgentToolBridgeResult(
                status_code=200, body={"ok": True}, trace_id=None, response_headers={}
            ),
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
    assert repository.events == []


def test_a_revoked_run_makes_no_further_provider_call() -> None:
    sink = CapturingEvidenceSink()
    runtime, provider = _runtime([_final()], sink)
    bridge, invocation = _bridge()
    bridge.close()  # what the runner does at the deadline, before a late thread resumes

    output = runtime.invoke(invocation, bridge)

    assert provider.requests == []
    assert output.disposition == "REQUIRE_HUMAN"
    assert output.terminal_code == "RUN_REVOKED"


def test_a_tool_ledger_failure_is_named_and_stops_the_run() -> None:
    class _StaleLedger:
        def record(self, **values: Any) -> None:
            del values
            raise AgentRunStateError("agent run claim is stale or unavailable")

    sink = CapturingEvidenceSink()
    runtime, provider = _runtime([_tool_call(), _final()], sink)
    bridge, invocation = _bridge(observer=_StaleLedger())

    output = runtime.invoke(invocation, bridge)

    assert output.terminal_code == "TOOL_LEDGER_UNAVAILABLE"
    assert output.disposition == "REQUIRE_HUMAN"
    # The failed ledger write ends the run: no second model call follows it.
    assert len(provider.requests) == 1


def test_evidence_is_attributed_only_to_the_run_that_produced_it() -> None:
    sink = CapturingEvidenceSink()
    runtime, _ = _runtime([_final()], sink)
    bridge, invocation = _bridge()
    runtime.invoke(invocation, bridge)

    assert sink.take(uuid4()) is None
    evidence = sink.take(invocation.run_id)
    assert evidence is not None and evidence.run_id == invocation.run_id
    assert sink.take(invocation.run_id) is None


# --- PostgreSQL: the draft is recorded under the claim, in the claim's transaction ------------


@pytest.fixture
def database_url() -> Generator[str, None, None]:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(url) as connection:
        apply_migrations(connection)
    yield url


def _enqueue(connection: psycopg.Connection[Any]) -> AgentRunEnqueueCommand:
    store_id = uuid4()
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
    )
    config = _config()
    command = AgentRunEnqueueCommand(
        agent_run_id=uuid4(),
        source_webhook_event_id=None,
        organization_id=uuid4(),
        store_id=store_id,
        channel="INTERNAL_TEST",
        conversation_binding_id=uuid4(),
        contact_binding_id=uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        deployment_stage=AgentDeploymentStage.SHADOW,
        data_classification=AgentDataClassification.SYNTHETIC,
        runtime_registry_version="1.0.0-eval",
        runtime_registry_hash=config.runtime_registry_hash,
        prompt_bundle_version=config.prompt_bundle_version,
        prompt_bundle_hash=config.prompt_bundle_hash,
        tool_contract_hash=config.tool_contract_hash,
        correlation_id=uuid4(),
        created_at=datetime(1970, 1, 1, tzinfo=UTC),
    )
    AgentRunRepository().enqueue(connection, command)
    return command


def test_a_draft_is_never_committed_for_a_run_whose_claim_was_lost(database_url: str) -> None:
    """Another worker recovers the run mid-flight; the late finisher writes nothing at all."""
    with psycopg.connect(database_url, autocommit=True) as connection:
        command = _enqueue(connection)

        def recover_elsewhere(_: Any) -> str:
            # What `recover_expired` does to a lapsed claim, from a different connection.
            with psycopg.connect(database_url, autocommit=True) as other:
                other.execute(
                    """
                    UPDATE agent_runs SET status = 'FAILED', claim_token = NULL,
                        claimed_at = NULL, lease_expires_at = NULL,
                        failure_code = 'LEASE_EXPIRED', completed_at = now()
                    WHERE id = %s AND status = 'PROCESSING'
                    """,
                    (command.agent_run_id,),
                )
            return "khách hỏi"

        assembled = build_agent_pipeline(
            config=_config(),
            provider_transport=ScriptedResponsesTransport([_final()]),
            tool_transport=_CatalogTransport(),
            runner=AgentRunner(_issuer()),
            instructions=INSTRUCTIONS,
            input_text_for=recover_elsewhere,
        )
        result = assembled.run_cycle(connection, lambda: True)

        assert result.status == "CLAIM_LOST"
        assert result.agent_run_id == str(command.agent_run_id)
        row = connection.execute(
            "SELECT count(*) FROM agent_drafts WHERE agent_run_id = %s", (command.agent_run_id,)
        ).fetchone()
        status = connection.execute(
            "SELECT status, failure_code FROM agent_runs WHERE id = %s", (command.agent_run_id,)
        ).fetchone()
    assert row == (0,)
    assert status == ("FAILED", "LEASE_EXPIRED")


def test_persisted_runtime_evidence_names_its_run(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        command = _enqueue(connection)
        assembled = build_agent_pipeline(
            config=_config(),
            provider_transport=ScriptedResponsesTransport([_final()]),
            tool_transport=_CatalogTransport(),
            runner=AgentRunner(_issuer()),
            instructions=INSTRUCTIONS,
            input_text_for=lambda _: "khách hỏi",
        )
        result = assembled.run_cycle(connection, lambda: True)
        row = connection.execute(
            "SELECT result_safe_summary FROM agent_runs WHERE id = %s", (command.agent_run_id,)
        ).fetchone()

    assert result.status == "DRAFT_REQUIRES_HUMAN"
    assert row is not None
    assert UUID(row[0]["runtime_evidence"]["run_id"]) == command.agent_run_id


def test_a_hanging_runtime_thread_is_not_left_calling_the_provider() -> None:
    """After the runner's guard fires the late thread observes revocation and stops."""
    calls: list[float] = []
    entered = threading.Event()

    class _BlockOnceProvider:
        provider_backed = False

        def create(self, request: Any, *, timeout_seconds: float) -> Any:
            del request
            calls.append(timeout_seconds)
            entered.set()
            # Ignores its own timeout the way a stuck transport would, then returns a tool call.
            time.sleep(timeout_seconds + 1.5)
            return _tool_call()

    tools = _CatalogTransport()
    lease = RECORDING_MARGIN + timedelta(seconds=1)
    repository = _LeaseEnforcingRepository(lease=lease)
    assembled = build_agent_pipeline(
        config=_config(),
        provider_transport=_BlockOnceProvider(),
        tool_transport=tools,
        runner=AgentRunner(_issuer()),
        instructions=INSTRUCTIONS,
        input_text_for=lambda _: "khách hỏi",
        repository=repository,  # type: ignore[arg-type]
        draft_recorder=lambda *_: None,
    )

    result = assembled.run_cycle(_NullConnection(), lambda: True)
    assert entered.is_set()
    assert result.status == "FAILED"
    assert repository.events == [("fail:MODEL_TIMEOUT", "OK")]
    time.sleep(2.5)  # let the stuck thread come back
    assert len(calls) == 1
    assert tools.requests == []
    assert repository.events == [("fail:MODEL_TIMEOUT", "OK")]
