"""AGENT-SHADOW-DEFECTS-001 F5: the policy point decides every run before any model call.

Suppression (invariant #10), the server-derived classification, the kill switches and the release
manifest were each checked nowhere on the agent-run path. These tests drive the assembled pipeline
against PostgreSQL and assert on what never happened -- no provider request, no tool call, no
draft -- as well as on the code the run was failed with.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    ReleaseCapability,
)
from nha_trang_laundry_contracts.channel_envelope import ChannelProvider
from nha_trang_laundry_db.agent_runs import AgentRunEnqueueCommand, AgentRunRepository
from nha_trang_laundry_db.channel import ContactChannelBindingRepository
from nha_trang_laundry_db.inbox import EncryptedInboundPayload, InboundWebhook, InboxRepository
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.consent import OptOutDisposition
from nha_trang_laundry_policy import SuppressionState
from nha_trang_laundry_worker.agent_run_policy import fold_suppression
from nha_trang_laundry_worker.agent_runner import (
    AgentRunner,
    AgentRunnerTokenIssuer,
    AgentToolForwardRequest,
    AgentToolForwardResponse,
)
from nha_trang_laundry_worker.durable_agent_worker import DurableAgentRunWorker
from nha_trang_laundry_worker.pipeline import build_agent_pipeline, load_pinned_prompt
from nha_trang_laundry_worker.responses_runtime import (
    ResponsesPriceTable,
    ResponsesRuntimeConfig,
    ScriptedResponsesTransport,
)

PINNED = load_pinned_prompt()


def _config() -> ResponsesRuntimeConfig:
    now = datetime.now(UTC)
    return ResponsesRuntimeConfig(
        runtime_id="responses-policy-test",
        model_id="gpt-test",
        immutable_model_release="gpt-test-2026-08-01",
        reasoning_effort="low",
        runtime_registry_version=PINNED.pins.runtime_registry_version,
        runtime_registry_hash=PINNED.pins.runtime_registry_hash,
        prompt_bundle_version=PINNED.pins.prompt_bundle_version,
        prompt_bundle_hash=PINNED.pins.prompt_bundle_hash,
        prompt_instructions_hash=PINNED.instructions_hash,
        tool_contract_hash=PINNED.pins.tool_contract_hash,
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


class _Tools:
    def __init__(self) -> None:
        self.requests: list[AgentToolForwardRequest] = []

    def send(self, request: AgentToolForwardRequest) -> AgentToolForwardResponse:
        self.requests.append(request)
        raise AssertionError("no tool call may happen here")


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url, autocommit=True) as connection:
        apply_migrations(connection)
        yield connection


def _enqueue(
    connection: psycopg.Connection[Any],
    *,
    contact_binding_id: UUID | None = None,
    source_webhook_event_id: UUID | None = None,
) -> AgentRunEnqueueCommand:
    store_id = uuid4()
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
    )
    command = AgentRunEnqueueCommand(
        agent_run_id=uuid4(),
        source_webhook_event_id=source_webhook_event_id,
        organization_id=uuid4(),
        store_id=store_id,
        channel="TELEGRAM",
        conversation_binding_id=uuid4(),
        contact_binding_id=contact_binding_id or uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        deployment_stage=AgentDeploymentStage.SHADOW,
        runtime_registry_version=PINNED.pins.runtime_registry_version,
        runtime_registry_hash=PINNED.pins.runtime_registry_hash,
        prompt_bundle_version=PINNED.pins.prompt_bundle_version,
        prompt_bundle_hash=PINNED.pins.prompt_bundle_hash,
        tool_contract_hash=PINNED.pins.tool_contract_hash,
        correlation_id=uuid4(),
        created_at=datetime(1970, 1, 1, tzinfo=UTC),
    )
    AgentRunRepository().enqueue(connection, command)
    return command


def _inbound(
    connection: psycopg.Connection[Any],
    contact_binding_id: UUID,
    disposition: OptOutDisposition = OptOutDisposition.NONE,
) -> UUID:
    return (
        InboxRepository()
        .record(
            connection,
            InboundWebhook(
                provider="TEST_PROVIDER",
                channel_account_id=f"account-{uuid4().hex}",
                provider_event_id=f"event-{uuid4().hex}",
                event_type="MESSAGE",
                channel="TELEGRAM",
                payload=EncryptedInboundPayload.from_ciphertext_and_plaintext(
                    ciphertext=b"sealed", authenticated_plaintext=b'{"text":"dung"}'
                ),
                opt_out_disposition=disposition,
                contact_binding_id=contact_binding_id,
                opt_out_registry_version=(
                    None if disposition is OptOutDisposition.NONE else "opt-out-v1"
                ),
                correlation_id=uuid4(),
                received_at=datetime.now(UTC),
            ),
        )
        .webhook_event_id
    )


def _run(
    connection: psycopg.Connection[Any], provider: ScriptedResponsesTransport, tools: _Tools
) -> str:
    assembled = build_agent_pipeline(
        config=_config(),
        provider_transport=provider,
        tool_transport=tools,
        runner=_runner(),
        instructions=PINNED.instructions,
        input_text_for=lambda _: "khách hỏi",
    )
    return assembled.run_cycle(connection, lambda: True).status


def _outcome(connection: psycopg.Connection[Any], run_id: UUID) -> tuple[Any, ...]:
    row = connection.execute(
        """
        SELECT run.status, run.failure_code, run.data_classification,
               (SELECT count(*) FROM agent_drafts WHERE agent_run_id = run.id)
        FROM agent_runs AS run WHERE run.id = %s
        """,
        (run_id,),
    ).fetchone()
    assert row is not None
    return tuple(row)


def test_a_contact_who_said_stop_never_reaches_a_model(connection: psycopg.Connection[Any]) -> None:
    contact = uuid4()
    _inbound(connection, contact, OptOutDisposition.WITHDRAW)
    command = _enqueue(connection, contact_binding_id=contact)
    provider, tools = ScriptedResponsesTransport([_final()]), _Tools()

    assert _run(connection, provider, tools) == "FAILED"

    assert _outcome(connection, command.agent_run_id) == (
        "FAILED",
        "SUPPRESSED",
        "REAL_CUSTOMER",
        0,
    )
    assert provider.requests == []
    assert tools.requests == []


def test_a_real_customer_run_is_denied_before_any_model_call(
    connection: psycopg.Connection[Any],
) -> None:
    resolved = ContactChannelBindingRepository().resolve_or_create(
        connection,
        provider=ChannelProvider.ZALO_OA,
        provider_user_ref=f"zalo-{uuid4().hex}",
        correlation_id=uuid4(),
    )
    command = _enqueue(connection, contact_binding_id=resolved.binding.contact_id)
    provider, tools = ScriptedResponsesTransport([_final()]), _Tools()

    assert _run(connection, provider, tools) == "FAILED"

    status, code, classification, drafts = _outcome(connection, command.agent_run_id)
    assert (status, classification, drafts) == ("FAILED", "REAL_CUSTOMER", 0)
    # No kill-switch row exists for the capability: the policy is unavailable, so it is denied.
    assert code == "POLICY_STORE_UNAVAILABLE"
    assert provider.requests == []


def test_kill_switches_alone_never_open_a_real_customer_run(
    connection: psycopg.Connection[Any],
) -> None:
    """Every switch on, unexpired -- still denied: no gate is verified and no release is signed."""
    connection.execute(
        """
        INSERT INTO automation_execution_gates (
            capability, global_automation_enabled, agent_processing_enabled,
            agent_outbound_enabled, channel_ingress_enabled, capability_enabled,
            stage_policy_allows, pdp_allows, version, expires_at, updated_at
        ) VALUES ('INTERNAL_SHADOW', TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, TRUE, 1,
                  now() + interval '1 hour', now())
        """
    )
    contact = uuid4()
    event_id = _inbound(connection, contact)
    command = _enqueue(connection, contact_binding_id=contact, source_webhook_event_id=event_id)
    provider, tools = ScriptedResponsesTransport([_final()]), _Tools()

    assert _run(connection, provider, tools) == "FAILED"

    status, code, classification, _ = _outcome(connection, command.agent_run_id)
    assert (status, classification) == ("FAILED", "REAL_CUSTOMER")
    assert code in {
        "STAGE_MISMATCH",
        "STAGE_GATE_UNVERIFIED",
        "RELEASE_NOT_AUTHORIZED",
        "RELEASE_METADATA_MISMATCH",
        "SUPPRESSION_UNKNOWN",
    }
    assert provider.requests == []


def test_a_provider_backed_runtime_is_denied_by_policy_even_for_synthetic_data(
    connection: psycopg.Connection[Any],
) -> None:
    class _ProviderBacked:
        provider_backed = True
        execution_pins = PINNED.pins

        def __init__(self) -> None:
            self.invoked = False

        def invoke(self, invocation: Any, bridge: Any) -> Any:
            self.invoked = True
            raise AssertionError("must not be invoked")

    command = _enqueue(connection)
    runtime = _ProviderBacked()
    worker = DurableAgentRunWorker(_runner())

    result = worker.run_once(
        connection, runtime=runtime, transport=_Tools(), correlation_id=uuid4()
    )

    assert result.status == "FAILED"
    assert _outcome(connection, command.agent_run_id)[:3] == (
        "FAILED",
        "POLICY_STORE_UNAVAILABLE",
        "SYNTHETIC",
    )
    assert runtime.invoked is False


def test_an_admitted_synthetic_run_records_the_policy_that_admitted_it(
    connection: psycopg.Connection[Any],
) -> None:
    command = _enqueue(connection)
    provider = ScriptedResponsesTransport([_final()])

    assert _run(connection, provider, _Tools()) == "DRAFT_REQUIRES_HUMAN"

    row = connection.execute(
        "SELECT result_safe_summary FROM agent_runs WHERE id = %s", (command.agent_run_id,)
    ).fetchone()
    assert row is not None
    assert row[0]["policy_version"] == "synthetic-internal-v1"
    assert row[0]["policy_reason_codes"] == ["SYNTHETIC_INTERNAL_ONLY"]
    assert len(provider.requests) == 1


def test_an_unreadable_policy_store_is_a_denial_not_a_default() -> None:
    from nha_trang_laundry_db.agent_runs import ClaimedAgentRun
    from nha_trang_laundry_worker.agent_run_policy import AgentRunPolicyGate

    def unreadable(connection: Any, claimed: ClaimedAgentRun) -> Any:
        raise psycopg.OperationalError("connection lost")

    claimed = ClaimedAgentRun(
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
        runtime_registry_hash=PINNED.pins.runtime_registry_hash,
        prompt_bundle_version="1.0.0-eval",
        prompt_bundle_hash=PINNED.pins.prompt_bundle_hash,
        tool_contract_hash=PINNED.pins.tool_contract_hash,
        order_request_id=None,
        public_code=None,
        bound_row_version=0,
        attempt_count=1,
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=20),
    )

    decision = AgentRunPolicyGate(facts_reader=unreadable).decide(
        None, claimed, provider_backed=False, now=datetime.now(UTC)
    )

    assert decision.allowed is False
    assert [reason.value for reason in decision.reason_codes] == ["POLICY_STORE_UNAVAILABLE"]


@pytest.mark.parametrize(
    ("states", "classification", "expected"),
    [
        (("CLEAR", "SUPPRESSED"), AgentDataClassification.SYNTHETIC, SuppressionState.SUPPRESSED),
        (("PENDING_REVIEW_BLOCKED",), AgentDataClassification.SYNTHETIC, SuppressionState.UNKNOWN),
        (("UNKNOWN_BLOCKED",), AgentDataClassification.SYNTHETIC, SuppressionState.UNKNOWN),
        (("SOMETHING_NEW",), AgentDataClassification.SYNTHETIC, SuppressionState.UNKNOWN),
        (("CLEAR",), AgentDataClassification.REAL_CUSTOMER, SuppressionState.CLEAR),
        ((), AgentDataClassification.REAL_CUSTOMER, SuppressionState.UNKNOWN),
        ((), AgentDataClassification.SYNTHETIC, SuppressionState.NOT_APPLICABLE),
    ],
)
def test_suppression_folds_to_its_most_restrictive_state(
    states: tuple[str, ...],
    classification: AgentDataClassification,
    expected: SuppressionState,
) -> None:
    assert fold_suppression(states, classification) is expected
