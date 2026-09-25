"""AGENT-SHADOW-DEFECTS-001 F5: a run's data classification is derived, never declared.

Until 0050 `agent_runs.data_classification` was whatever the enqueuer wrote, checked only against
the two allowed spellings, and the runner, the Tool Facade's verifier and the facade's synthetic
policy all trusted it. These tests write every real-customer signal the schema holds and prove the
database -- not the enqueuer -- decides.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import fields
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_contracts import AgentDeploymentStage, ReleaseCapability
from nha_trang_laundry_contracts.channel_envelope import ChannelProvider
from nha_trang_laundry_db.agent_runs import AgentRunEnqueueCommand, AgentRunRepository
from nha_trang_laundry_db.channel import ContactChannelBindingRepository
from nha_trang_laundry_db.inbox import (
    EncryptedInboundPayload,
    InboundWebhook,
    InboxRepository,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.consent import OptOutDisposition

SHA = "sha256:" + "a" * 64


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url, autocommit=True) as connection:
        apply_migrations(connection)
        yield connection


def _command(
    connection: psycopg.Connection[Any],
    *,
    contact_binding_id: UUID | None = None,
    source_webhook_event_id: UUID | None = None,
    public_code: str | None = None,
) -> AgentRunEnqueueCommand:
    store_id = uuid4()
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
    )
    return AgentRunEnqueueCommand(
        agent_run_id=uuid4(),
        source_webhook_event_id=source_webhook_event_id,
        organization_id=uuid4(),
        store_id=store_id,
        channel="INTERNAL_TEST",
        conversation_binding_id=uuid4(),
        contact_binding_id=contact_binding_id or uuid4(),
        capability=ReleaseCapability.INTERNAL_SHADOW,
        deployment_stage=AgentDeploymentStage.SHADOW,
        runtime_registry_version="1.0.0-eval",
        runtime_registry_hash=SHA,
        prompt_bundle_version="1.0.0-eval",
        prompt_bundle_hash=SHA,
        tool_contract_hash=SHA,
        correlation_id=uuid4(),
        public_code=public_code,
    )


def _inbound(connection: psycopg.Connection[Any], contact_binding_id: UUID | None) -> UUID:
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
                    ciphertext=b"sealed", authenticated_plaintext=b'{"text":"chao"}'
                ),
                opt_out_disposition=OptOutDisposition.NONE,
                contact_binding_id=contact_binding_id,
                opt_out_registry_version=None,
                correlation_id=uuid4(),
                received_at=datetime.now(UTC),
            ),
        )
        .webhook_event_id
    )


def _classification(connection: psycopg.Connection[Any], run_id: UUID) -> str:
    row = connection.execute(
        "SELECT data_classification FROM agent_runs WHERE id = %s", (run_id,)
    ).fetchone()
    assert row is not None
    return str(row[0])


def _enqueue(connection: psycopg.Connection[Any], command: AgentRunEnqueueCommand) -> str:
    AgentRunRepository().enqueue(connection, command)
    return _classification(connection, command.agent_run_id)


def test_the_enqueuer_cannot_state_a_classification() -> None:
    assert "data_classification" not in {field.name for field in fields(AgentRunEnqueueCommand)}


def test_a_run_with_no_real_customer_signal_is_synthetic(
    connection: psycopg.Connection[Any],
) -> None:
    assert _enqueue(connection, _command(connection)) == "SYNTHETIC"


def test_a_run_sourced_from_an_inbound_provider_event_is_real_customer(
    connection: psycopg.Connection[Any],
) -> None:
    event_id = _inbound(connection, None)

    assert _enqueue(connection, _command(connection, source_webhook_event_id=event_id)) == (
        "REAL_CUSTOMER"
    )


def test_a_run_for_a_contact_with_a_provider_binding_is_real_customer(
    connection: psycopg.Connection[Any],
) -> None:
    resolved = ContactChannelBindingRepository().resolve_or_create(
        connection,
        provider=ChannelProvider.ZALO_OA,
        provider_user_ref=f"zalo-{uuid4().hex}",
        correlation_id=uuid4(),
    )

    command = _command(connection, contact_binding_id=resolved.binding.contact_id)
    assert _enqueue(connection, command) == "REAL_CUSTOMER"


def test_a_run_for_a_contact_that_has_written_in_is_real_customer(
    connection: psycopg.Connection[Any],
) -> None:
    contact = uuid4()
    _inbound(connection, contact)

    assert _enqueue(connection, _command(connection, contact_binding_id=contact)) == (
        "REAL_CUSTOMER"
    )


def test_a_run_bound_to_a_public_order_locator_is_real_customer(
    connection: psycopg.Connection[Any],
) -> None:
    command = _command(connection, public_code="PUBLICCODE-" + uuid4().hex[:12])

    assert _enqueue(connection, command) == "REAL_CUSTOMER"


def test_a_raw_insert_that_declares_synthetic_is_overwritten(
    connection: psycopg.Connection[Any],
) -> None:
    """Even an INSERT naming the column cannot choose: the trigger derives, it does not compare."""
    event_id = _inbound(connection, None)
    command = _command(connection, source_webhook_event_id=event_id)
    connection.execute(
        """
        INSERT INTO agent_runs (
            id, source_webhook_event_id, organization_id, store_id, channel,
            conversation_binding_id, contact_binding_id, capability, deployment_stage,
            data_classification, runtime_registry_version, runtime_registry_hash,
            prompt_bundle_version, prompt_bundle_hash, tool_contract_hash, status, created_at
        ) VALUES (%s, %s, %s, %s, 'INTERNAL_TEST', %s, %s, 'INTERNAL_SHADOW', 'SHADOW',
                  'SYNTHETIC', '1.0.0-eval', %s, '1.0.0-eval', %s, %s, 'PENDING', now())
        """,
        (
            command.agent_run_id,
            event_id,
            command.organization_id,
            command.store_id,
            command.conversation_binding_id,
            command.contact_binding_id,
            SHA,
            SHA,
            SHA,
        ),
    )

    assert _classification(connection, command.agent_run_id) == "REAL_CUSTOMER"


def test_the_enqueue_event_records_the_derived_classification(
    connection: psycopg.Connection[Any],
) -> None:
    contact = uuid4()
    _inbound(connection, contact)
    command = _command(connection, contact_binding_id=contact)
    AgentRunRepository().enqueue(connection, command)

    row = connection.execute(
        """
        SELECT payload FROM domain_events
        WHERE aggregate_id = %s AND event_type = 'AGENT_RUN_ENQUEUED'
        """,
        (command.agent_run_id,),
    ).fetchone()
    assert row is not None
    assert row[0]["data_classification"] == "REAL_CUSTOMER"
