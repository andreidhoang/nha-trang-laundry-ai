"""AGENT-SHADOW-DEFECTS-001 F3: a run executes the release it was queued for, or nothing.

`agent_runs` records the runtime registry, prompt bundle and tool contract a run was queued for.
Before this item `_job_from_claim` dropped all of them, the runtime's configuration hashes were
labels compared with nothing, and the persisted evidence omitted the prompt and registry digests:
a run queued for prompt X ran under prompt Y and nothing anywhere said so.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
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
from nha_trang_laundry_db.agent_runs import (
    AgentRunEnqueueCommand,
    AgentRunPolicyFacts,
    AgentRunRepository,
    ClaimedAgentRun,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_worker.agent_run_policy import AgentRunPolicyGate
from nha_trang_laundry_worker.agent_runner import (
    AgentRunJob,
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentRunRejected,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
    ExecutionPins,
    SyntheticScriptedRuntime,
    registry_execution_pins,
)
from nha_trang_laundry_worker.pipeline import (
    PipelineConfigurationError,
    build_agent_pipeline,
    load_pinned_prompt,
)
from nha_trang_laundry_worker.responses_runtime import (
    ResponsesPriceTable,
    ResponsesRuntimeConfig,
    ScriptedResponsesTransport,
)

PINNED = load_pinned_prompt()
OTHER_HASH = "sha256:" + "3" * 64


def _config(**overrides: Any) -> ResponsesRuntimeConfig:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "runtime_id": "responses-pin-test",
        "model_id": "gpt-test",
        "immutable_model_release": "gpt-test-2026-08-01",
        "reasoning_effort": "low",
        "runtime_registry_version": PINNED.pins.runtime_registry_version,
        "runtime_registry_hash": PINNED.pins.runtime_registry_hash,
        "prompt_bundle_version": PINNED.pins.prompt_bundle_version,
        "prompt_bundle_hash": PINNED.pins.prompt_bundle_hash,
        "prompt_instructions_hash": PINNED.instructions_hash,
        "tool_contract_hash": PINNED.pins.tool_contract_hash,
        "max_model_calls": 3,
        "max_input_tokens": 8000,
        "max_output_tokens": 1200,
        "max_turn_cost_usd": "0.02",
        "price_table": ResponsesPriceTable.assemble(
            price_table_version="price-v1",
            model_id="gpt-test",
            immutable_model_release="gpt-test-2026-08-01",
            effective_at=now - timedelta(days=1),
            input_cost_per_million_usd="1",
            cached_input_cost_per_million_usd="0.25",
            output_cost_per_million_usd="2",
        ),
    }
    values.update(overrides)
    return ResponsesRuntimeConfig(**values)


def _synthetic_run_gate() -> AgentRunPolicyGate:
    """The real policy point, fed what a synthetic run with no consent record reads as.

    These tests use a fake repository and no database; the gate still decides (F5).
    """
    return AgentRunPolicyGate(
        facts_reader=lambda _connection, claimed: AgentRunPolicyFacts(
            data_classification=claimed.data_classification,
            suppression_states=(),
            has_source_event=False,
            source_contact_binding_id=None,
            gate=None,
        )
    )


def _runner() -> AgentRunner:
    key = (
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
            issuer="https://control-plane.test", audience="agent-tool-facade", private_key=key
        )
    )


def _final() -> dict[str, Any]:
    envelope = {"disposition": "DRAFT_REQUIRES_HUMAN", "draft_text": "Dạ.", "reason_code": None}
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


class _NoTools:
    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        raise AssertionError(f"no tool call expected: {request.path}")


class _NullConnection:
    def transaction(self) -> Any:
        return _Null()


class _Null:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


class _Repository:
    """Hands out one claim with the given pins and records what the worker wrote."""

    def __init__(self, pins: ExecutionPins) -> None:
        self._pins = pins
        self.failed: list[str] = []
        self.completed: list[dict[str, Any]] = []

    def claim_next(self, connection: Any, *, worker_role: Any, now: datetime) -> ClaimedAgentRun:
        del connection, worker_role
        return ClaimedAgentRun(
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
            runtime_registry_version=self._pins.runtime_registry_version,
            runtime_registry_hash=self._pins.runtime_registry_hash,
            prompt_bundle_version=self._pins.prompt_bundle_version,
            prompt_bundle_hash=self._pins.prompt_bundle_hash,
            tool_contract_hash=self._pins.tool_contract_hash,
            order_request_id=None,
            public_code=None,
            bound_row_version=0,
            attempt_count=1,
            lease_expires_at=now + timedelta(seconds=20),
        )

    def record_tool_call(self, connection: Any, entry: Any) -> None:
        raise AssertionError("no tool call expected")

    def complete_draft(self, connection: Any, **values: Any) -> None:
        self.completed.append(values["safe_summary"])

    def fail(self, connection: Any, **values: Any) -> None:
        self.failed.append(str(values["failure_code"]))


@pytest.mark.parametrize(
    "queued",
    [
        replace(PINNED.pins, runtime_registry_version="0.9.0-old"),
        replace(PINNED.pins, runtime_registry_hash=OTHER_HASH),
        replace(PINNED.pins, prompt_bundle_version="prompt-v0"),
        replace(PINNED.pins, prompt_bundle_hash=OTHER_HASH),
        replace(PINNED.pins, tool_contract_hash=OTHER_HASH),
    ],
    ids=["registry-version", "registry-hash", "prompt-version", "prompt-hash", "tool-hash"],
)
def test_a_run_queued_for_another_release_fails_before_any_model_call(
    queued: ExecutionPins,
) -> None:
    provider = ScriptedResponsesTransport([_final()])
    repository = _Repository(queued)
    assembled = build_agent_pipeline(
        config=_config(),
        provider_transport=provider,
        tool_transport=_NoTools(),
        runner=_runner(),
        instructions=PINNED.instructions,
        input_text_for=lambda _: "khách hỏi",
        repository=repository,  # type: ignore[arg-type]
        draft_recorder=lambda *_: None,
        policy_gate=_synthetic_run_gate(),
    )

    result = assembled.run_cycle(_NullConnection(), lambda: True)

    assert result.status == "FAILED"
    assert repository.failed == ["RUNTIME_PIN_MISMATCH"]
    assert repository.completed == []
    assert provider.requests == []


def test_a_run_queued_for_the_executing_release_proceeds_and_records_what_ran() -> None:
    provider = ScriptedResponsesTransport([_final()])
    repository = _Repository(PINNED.pins)
    assembled = build_agent_pipeline(
        config=_config(),
        provider_transport=provider,
        tool_transport=_NoTools(),
        runner=_runner(),
        instructions=PINNED.instructions,
        input_text_for=lambda _: "khách hỏi",
        repository=repository,  # type: ignore[arg-type]
        draft_recorder=lambda *_: None,
        policy_gate=_synthetic_run_gate(),
    )

    result = assembled.run_cycle(_NullConnection(), lambda: True)

    assert result.status == "DRAFT_REQUIRES_HUMAN"
    evidence = repository.completed[0]["runtime_evidence"]
    assert evidence["runtime_registry_version"] == PINNED.pins.runtime_registry_version
    assert evidence["runtime_registry_hash"] == PINNED.pins.runtime_registry_hash
    assert evidence["prompt_bundle_version"] == PINNED.pins.prompt_bundle_version
    assert evidence["prompt_bundle_hash"] == PINNED.pins.prompt_bundle_hash
    assert evidence["prompt_instructions_hash"] == PINNED.instructions_hash
    assert evidence["tool_contract_hash"] == PINNED.pins.tool_contract_hash
    assert evidence["model_id"] == "gpt-test"
    assert evidence["price_table_version"] == "price-v1"
    assert str(evidence["price_table_hash"]).startswith("sha256:")
    # Digests and versions only: the instruction text itself never reaches the summary.
    assert PINNED.instructions not in json.dumps(repository.completed[0])


@pytest.mark.parametrize(
    ("overrides", "instructions", "named"),
    [
        ({"runtime_registry_version": "0.9.0-old"}, None, "runtime_registry_version"),
        ({"runtime_registry_hash": OTHER_HASH}, None, "runtime_registry_hash"),
        ({"prompt_bundle_version": "prompt-v0"}, None, "prompt_bundle_version"),
        ({"prompt_bundle_hash": OTHER_HASH}, None, "prompt_bundle_hash"),
        ({"tool_contract_hash": OTHER_HASH}, None, "tool_contract_hash"),
        ({"prompt_instructions_hash": OTHER_HASH}, None, "prompt_instructions_hash"),
        ({}, "Một prompt khác, chưa được ghim.", "instructions"),
    ],
)
def test_the_pipeline_refuses_a_configuration_that_is_not_the_pinned_release(
    overrides: dict[str, Any], instructions: str | None, named: str
) -> None:
    with pytest.raises(PipelineConfigurationError, match=named):
        build_agent_pipeline(
            config=_config(**overrides),
            provider_transport=ScriptedResponsesTransport([]),
            tool_transport=_NoTools(),
            runner=_runner(),
            instructions=instructions if instructions is not None else PINNED.instructions,
            input_text_for=lambda _: "x",
        )


def _job(pins: ExecutionPins | None) -> AgentRunJob:
    started = datetime.now(UTC)
    return AgentRunJob(
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
        pins=pins,
    )


def test_a_job_without_queued_pins_is_refused() -> None:
    runtime = SyntheticScriptedRuntime(draft_text="x", execution_pins=PINNED.pins)

    with pytest.raises(AgentRunRejected, match="RUNTIME_PIN_MISSING"):
        _runner().execute(job=_job(None), runtime=runtime, transport=_NoTools())


def test_a_runtime_that_states_no_executed_release_is_refused() -> None:
    class _Unpinned:
        provider_backed = False

        def invoke(self, invocation: Any, bridge: Any) -> Any:
            raise AssertionError("must not be invoked")

    with pytest.raises(AgentRunRejected, match="RUNTIME_PIN_MISSING"):
        _runner().execute(job=_job(PINNED.pins), runtime=_Unpinned(), transport=_NoTools())  # type: ignore[arg-type]


def test_registry_pins_are_the_registry_file_and_its_declared_bundle() -> None:
    """Hashed here independently of the loader, so the loader cannot vouch for itself."""
    root = Path(__file__).resolve().parents[3]

    def digest(relative: str) -> str:
        return "sha256:" + sha256((root / relative).read_bytes()).hexdigest()

    pins = registry_execution_pins()

    assert pins == PINNED.pins
    assert pins.runtime_registry_version == "1.0.0-eval"
    assert pins.runtime_registry_hash == digest("runtime/model-registry-v1.yaml")
    assert pins.prompt_bundle_hash == digest("runtime/prompts/manifest-v1.yaml")
    assert pins.tool_contract_hash == digest("specs/contracts/agent-tools-v1.openapi.yaml")
    assert PINNED.instructions_hash == digest("runtime/prompts/public-concierge.vi-VN.md")
    assert PINNED.instructions == (root / "runtime/prompts/public-concierge.vi-VN.md").read_text(
        encoding="utf-8"
    )


# --- PostgreSQL: the pins the queue recorded are the pins the runner compares ---------------


@pytest.fixture
def database_url() -> Generator[str, None, None]:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(url) as connection:
        apply_migrations(connection)
    yield url


def _enqueue(connection: psycopg.Connection[Any], pins: ExecutionPins) -> AgentRunEnqueueCommand:
    store_id = uuid4()
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
    )
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
        runtime_registry_version=pins.runtime_registry_version,
        runtime_registry_hash=pins.runtime_registry_hash,
        prompt_bundle_version=pins.prompt_bundle_version,
        prompt_bundle_hash=pins.prompt_bundle_hash,
        tool_contract_hash=pins.tool_contract_hash,
        correlation_id=uuid4(),
        created_at=datetime(1970, 1, 1, tzinfo=UTC),
    )
    AgentRunRepository().enqueue(connection, command)
    return command


def test_a_durably_queued_run_pinned_to_another_prompt_never_reaches_the_provider(
    database_url: str,
) -> None:
    provider = ScriptedResponsesTransport([_final()])
    with psycopg.connect(database_url, autocommit=True) as connection:
        command = _enqueue(connection, replace(PINNED.pins, prompt_bundle_hash=OTHER_HASH))
        assembled = build_agent_pipeline(
            config=_config(),
            provider_transport=provider,
            tool_transport=_NoTools(),
            runner=_runner(),
            instructions=PINNED.instructions,
            input_text_for=lambda _: "khách hỏi",
        )
        result = assembled.run_cycle(connection, lambda: True)
        row = connection.execute(
            "SELECT status, failure_code FROM agent_runs WHERE id = %s", (command.agent_run_id,)
        ).fetchone()
        drafts = connection.execute(
            "SELECT count(*) FROM agent_drafts WHERE agent_run_id = %s", (command.agent_run_id,)
        ).fetchone()

    assert result.status == "FAILED"
    assert row == ("FAILED", "RUNTIME_PIN_MISMATCH")
    assert drafts == (0,)
    assert provider.requests == []
