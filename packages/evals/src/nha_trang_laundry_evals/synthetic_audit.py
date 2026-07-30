"""PostgreSQL audit-write fault injection for the agent tool ledger."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

import psycopg
import rfc8785
from nha_trang_laundry_contracts import (
    AgentDataClassification,
    AgentDeploymentStage,
    AgentToolOperation,
    ReleaseCapability,
)
from nha_trang_laundry_db.agent_runs import (
    AgentRunEnqueueCommand,
    AgentRunRepository,
    AgentToolCallLedgerEntry,
    request_fingerprint,
)
from nha_trang_laundry_domain.catalog import ActorRole

from .fixtures import SyntheticFixtureBundle


@dataclass(frozen=True, slots=True)
class SyntheticAuditFailurePreflight:
    tool_trace: tuple[Mapping[str, object], ...]
    business_mutation_rolled_back: bool
    domain_event_rolled_back: bool
    required_outbox_event_rolled_back: bool
    trace_id: str


def execute_audit_write_failure_preflight(
    connection: Any,
    fixture: SyntheticFixtureBundle,
) -> SyntheticAuditFailurePreflight:
    """Reject the audit insert and prove the surrounding material change is absent."""

    fault = fixture.payload.get("fault_injection")
    if not isinstance(fault, Mapping) or fault.get("reject_audit_action") != "AGENT_TOOL_CALL":
        raise ValueError("audit-failure fixture does not select the agent tool audit action")
    trace_id = _source_event_id(fixture.payload)
    timestamp = fixture_clock(fixture.payload)
    repository = AgentRunRepository()
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
        runtime_registry_hash=f"sha256:{'a' * 64}",
        prompt_bundle_version="1.0.0-eval",
        prompt_bundle_hash=f"sha256:{'b' * 64}",
        tool_contract_hash=f"sha256:{'c' * 64}",
        correlation_id=uuid4(),
        created_at=timestamp,
    )
    repository.enqueue(connection, command)
    claimed = repository.claim_next(connection, worker_role=ActorRole.AGENT_RUNNER, now=timestamp)
    if claimed is None:
        raise ValueError("synthetic agent run was not claimable")
    entry = AgentToolCallLedgerEntry(
        agent_run_id=claimed.agent_run_id,
        claim_token=claimed.claim_token,
        sequence_number=1,
        operation=AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS,
        request_fingerprint=request_fingerprint({"fact_count": 1}),
        result_status_code=200,
        result_code="REQUIRE_HUMAN",
        trace_id=trace_id,
        safe_summary={"result_code": "REQUIRE_HUMAN"},
        started_at=timestamp,
        completed_at=timestamp,
        correlation_id=command.correlation_id,
    )
    trigger_name = f"synthetic_agent_audit_failure_{uuid4().hex}"
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE FUNCTION pg_temp.reject_agent_tool_audit() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'injected agent audit failure';
            END;
            $$
            """
        )
        cursor.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE INSERT ON audit_events
            FOR EACH ROW WHEN (NEW.action = 'AGENT_TOOL_CALL')
            EXECUTE FUNCTION pg_temp.reject_agent_tool_audit()
            """
        )
    try:
        try:
            repository.record_tool_call(connection, entry)
        except psycopg.Error as error:
            if "injected agent audit failure" not in str(error):
                raise
        else:
            raise ValueError("audit failure injection did not reject the material change")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM agent_tool_calls WHERE agent_run_id = %s",
                (entry.agent_run_id,),
            )
            tool_count = int(cursor.fetchone()[0])
            cursor.execute(
                """
                SELECT count(*) FROM domain_events
                WHERE aggregate_type = 'AGENT_RUN' AND aggregate_id = %s
                  AND event_type = 'AGENT_TOOL_CALL_RECORDED'
                """,
                (entry.agent_run_id,),
            )
            event_count = int(cursor.fetchone()[0])
            cursor.execute(
                """
                SELECT count(*) FROM outbox_events
                WHERE aggregate_type = 'AGENT_RUN' AND aggregate_id = %s
                  AND event_type = 'agent.tool_called.v1'
                """,
                (entry.agent_run_id,),
            )
            outbox_count = int(cursor.fetchone()[0])
    finally:
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON audit_events")

    arguments = {"facts": [{"fact_type": "CUSTOMER_NOTE", "value": "synthetic"}]}
    return SyntheticAuditFailurePreflight(
        tool_trace=(
            {
                "sequence": 1,
                "operation_id": AgentToolOperation.ORDER_REQUEST_RECORD_CUSTOMER_FACTS.value,
                "argument_field_names": ["facts"],
                "arguments_sha256": f"sha256:{sha256(rfc8785.dumps(arguments)).hexdigest()}",
                "status_code": 500,
                "trace_id": trace_id,
            },
        ),
        business_mutation_rolled_back=tool_count == 0,
        domain_event_rolled_back=event_count == 0,
        required_outbox_event_rolled_back=outbox_count == 0,
        trace_id=trace_id,
    )


def fixture_clock(payload: Mapping[str, Any]) -> datetime:
    value = payload.get("clock")
    if not isinstance(value, str):
        raise ValueError("audit-failure fixture clock is invalid")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("audit-failure fixture clock is invalid")
    return timestamp.astimezone(UTC)


def _source_event_id(payload: Mapping[str, Any]) -> str:
    events = payload.get("provider_events")
    if not isinstance(events, list) or len(events) != 1 or not isinstance(events[0], Mapping):
        raise ValueError("audit-failure fixture must contain one synthetic event")
    value = events[0].get("event_id")
    if not isinstance(value, str) or not value:
        raise ValueError("audit-failure fixture event id is invalid")
    return value


__all__ = [
    "SyntheticAuditFailurePreflight",
    "execute_audit_write_failure_preflight",
    "fixture_clock",
]
