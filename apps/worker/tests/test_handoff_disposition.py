"""AGENT-SHADOW-DEFECTS-001 F1: a deterministic handoff is never filed as a model draft.

`BoundedResponsesRuntime` concludes every provider failure, budget exhaustion, policy denial and
model-requested handoff by returning `DETERMINISTIC_HANDOFF_TEXT` rather than raising. Before this
item `AgentRunner.execute` then labelled every returned output `DRAFT_REQUIRES_HUMAN`, the pipeline
mapped that to `terminal_outcome = 'DRAFT'`, and a staff reviewer was shown the fallback sentence as
if a model had written it -- with no `HUMAN_APPROVAL_REQUIRED` badge, and counted as model output in
every Shadow metric built on `agent_drafts`.

These tests drive the real runtime through the real runner, so the disposition they assert is the
one the runtime concluded, not one a fixture asserted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    ReleaseCapability,
)
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_worker import pipeline as pipeline_module
from nha_trang_laundry_worker.agent_runner import (
    AgentRunJob,
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentRunResult,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
)
from nha_trang_laundry_worker.pipeline import (
    CapturingEvidenceSink,
    RunScopedContextLoader,
    load_pinned_prompt,
)
from nha_trang_laundry_worker.responses_runtime import (
    CURRENT_TOOL_CONTRACT_HASH,
    DETERMINISTIC_HANDOFF_TEXT,
    BoundedResponsesRuntime,
    ResponsesConnectionFailure,
    ResponsesOutcomeAmbiguous,
    ResponsesPriceTable,
    ResponsesRuntimeConfig,
    ResponsesTransportTimeout,
    ScriptedResponsesTransport,
)

# AGENT-SHADOW-DEFECTS-001 F3: the pipeline only assembles the release the runtime registry pins,
# so these tests run the real pinned prompt text and hashes rather than invented labels.
PINNED = load_pinned_prompt()
INSTRUCTIONS = PINNED.instructions
REGISTRY_HASH = PINNED.pins.runtime_registry_hash
PROMPT_HASH = PINNED.pins.prompt_bundle_hash


def _config() -> ResponsesRuntimeConfig:
    now = datetime.now(UTC)
    return ResponsesRuntimeConfig(
        runtime_id="responses-handoff-test",
        model_id="gpt-test",
        immutable_model_release="gpt-test-2026-08-01",
        reasoning_effort="low",
        runtime_registry_version=PINNED.pins.runtime_registry_version,
        runtime_registry_hash=REGISTRY_HASH,
        prompt_bundle_version=PINNED.pins.prompt_bundle_version,
        prompt_bundle_hash=PROMPT_HASH,
        prompt_instructions_hash=PINNED.instructions_hash,
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


class _NoToolTransport:
    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        raise AssertionError(f"no tool call is expected here: {request.path}")


def _final(envelope: dict[str, Any]) -> dict[str, Any]:
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


def _execute(script: list[Any]) -> tuple[AgentRunResult, CapturingEvidenceSink, AgentRunJob]:
    config = _config()
    sink = CapturingEvidenceSink()
    runtime = BoundedResponsesRuntime(
        config=config,
        context_loader=RunScopedContextLoader(
            config=config, instructions=INSTRUCTIONS, input_text_for=lambda _: "khách hỏi giá"
        ),
        transport=ScriptedResponsesTransport(script),
        evidence_sink=sink,
    )
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
        pins=runtime.execution_pins,
    )
    result = _runner().execute(job=job, runtime=runtime, transport=_NoToolTransport())
    return result, sink, job


@pytest.mark.parametrize(
    ("script", "terminal_code"),
    [
        ([ResponsesConnectionFailure("down")], "PROVIDER_CONNECTION_FAILURE"),
        ([ResponsesTransportTimeout("slow")], "PROVIDER_TIMEOUT"),
        ([ResponsesOutcomeAmbiguous("unknown")], "PROVIDER_OUTCOME_AMBIGUOUS"),
        (
            [_final({"disposition": "REQUIRE_HUMAN", "draft_text": None, "reason_code": "X_Y_Z"})],
            "X_Y_Z",
        ),
        ([_final({"disposition": "SEND_NOW", "draft_text": "x", "reason_code": None})], None),
    ],
)
def test_every_runtime_handoff_is_reported_as_require_human_with_its_real_code(
    script: list[Any], terminal_code: str | None
) -> None:
    result, sink, job = _execute(script)

    assert result.status == "REQUIRE_HUMAN"
    assert result.draft_text == DETERMINISTIC_HANDOFF_TEXT
    evidence = sink.take(job.run_id)
    assert evidence is not None
    assert evidence.terminal_outcome == "REQUIRE_HUMAN"
    # The run result carries the runtime's own terminal code, not the run status restated.
    assert result.terminal_code == evidence.terminal_code
    if terminal_code is not None:
        assert result.terminal_code == terminal_code


def test_only_a_validated_model_draft_is_reported_as_a_draft() -> None:
    result, sink, job = _execute(
        [_final({"disposition": "DRAFT_REQUIRES_HUMAN", "draft_text": "Dạ.", "reason_code": None})]
    )

    assert result.status == "DRAFT_REQUIRES_HUMAN"
    assert result.terminal_code == "VALIDATED_DRAFT"
    assert result.draft_text == "Dạ."
    evidence = sink.take(job.run_id)
    assert evidence is not None and evidence.terminal_outcome == "DRAFT"


def test_a_handoff_is_filed_for_review_as_require_human_with_the_runtime_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review row a reviewer sees says HUMAN_APPROVAL_REQUIRED and why, never DRAFT."""
    result, _, job = _execute([ResponsesConnectionFailure("down")])
    captured: dict[str, Any] = {}

    class _Cursor:
        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, *_: object) -> None:
            return None

        def fetchone(self) -> None:
            return None

    class _Connection:
        def cursor(self) -> _Cursor:
            return _Cursor()

    monkeypatch.setattr(
        ShadowConsoleRepository,
        "record_draft",
        staticmethod(lambda connection, **values: captured.update(values)),
    )
    claimed = SimpleNamespace(
        agent_run_id=job.run_id,
        store_id=job.store_id,
        conversation_binding_id=job.conversation_binding_id,
        contact_binding_id=job.contact_binding_id,
    )
    pipeline_module._record_draft_for_review(
        _Connection(), claimed, result, uuid4(), datetime.now(UTC)
    )

    assert captured["terminal_outcome"] == "REQUIRE_HUMAN"
    assert captured["terminal_code"] == "PROVIDER_CONNECTION_FAILURE"
    assert captured["draft_text"] == DETERMINISTIC_HANDOFF_TEXT
