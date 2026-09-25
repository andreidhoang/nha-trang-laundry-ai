"""A real agent draft to hang a `SEND_MESSAGE` envelope on.

`API-INTEGRITY-002` made `MESSAGE_DRAFT` a resolvable resource: an envelope must name a draft that
exists, in the store it names, with the digests the server computes from that draft's stored text.
Fixtures that used to raise an envelope over `uuid4()` with `"a" * 64` digests were exercising the
defect, so they now seed the draft production would have and ask the server for its binding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.inbox import EncryptedInboundPayload, InboundWebhook, InboxRepository
from nha_trang_laundry_db.message_drafts import MessageDraftBinding, read_message_draft_binding
from nha_trang_laundry_db.service_messaging import publish_messaging_policy
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_domain.consent import OptOutDisposition

DRAFT_TEXT = "Dạ, đồ của anh/chị đã giặt xong, mời anh/chị ghé tiệm nhận ạ."


@dataclass(frozen=True, slots=True)
class SeededDraft:
    agent_run_id: UUID
    store_id: UUID
    conversation_binding_id: UUID
    contact_binding_id: UUID
    text: str


def seed_message_draft(
    connection: Any,
    store_id: UUID,
    *,
    text: str = DRAFT_TEXT,
    contact_binding_id: UUID | None = None,
) -> SeededDraft:
    """An agent run and the draft it produced, in a store that must already exist."""

    agent_run_id, conversation_binding_id = uuid4(), uuid4()
    contact = contact_binding_id or uuid4()
    moment = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO agent_runs (
                id, organization_id, store_id, channel, conversation_binding_id,
                contact_binding_id, capability, deployment_stage, data_classification,
                runtime_registry_version, runtime_registry_hash, prompt_bundle_version,
                prompt_bundle_hash, tool_contract_hash, status, created_at
            ) VALUES (
                %s, %s, %s, 'INTERNAL_TEST', %s, %s, 'INTERNAL_SHADOW', 'SHADOW', 'SYNTHETIC',
                '1.0.0-eval', %s, '1.0.0-eval', %s, %s, 'PENDING', %s
            )
            """,
            (
                agent_run_id,
                uuid4(),
                store_id,
                conversation_binding_id,
                contact,
                f"sha256:{'a' * 64}",
                f"sha256:{'b' * 64}",
                f"sha256:{'c' * 64}",
                moment,
            ),
        )
    ShadowConsoleRepository.record_draft(
        connection,
        agent_run_id=agent_run_id,
        store_id=store_id,
        conversation_binding_id=conversation_binding_id,
        contact_binding_id=contact,
        draft_text=text,
        terminal_outcome="DRAFT",
        terminal_code="DRAFT_REQUIRES_HUMAN",
        tool_call_count=1,
        correlation_id=uuid4(),
        now=moment,
    )
    return SeededDraft(agent_run_id, store_id, conversation_binding_id, contact, text)


def current_binding(connection: Any, agent_run_id: UUID) -> MessageDraftBinding:
    """The binding the server computes for the draft as it stands now."""

    # In a transaction block, so a bare read never leaves one open on a non-autocommit connection
    # and silently turns the caller's later writes into uncommitted savepoints.
    with connection.transaction(), connection.cursor() as cursor:
        binding = read_message_draft_binding(cursor, agent_run_id)
    assert binding is not None, "the seeded draft has no sendable content"
    return binding


#: The document the owner publishes (`DEC-033`), read from where the publish script reads it, so
#: the tests exercise the shipped template rather than a copy of it.
MESSAGING_POLICY_TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "templates"
    / "transactional-messaging-policy-dec-033.json"
)


def publish_test_messaging_policy(
    connection: Any, *, payload: dict[str, Any] | None = None
) -> UUID:
    """Publish the transactional messaging policy as an owner would, and return that owner's id.

    `CONSENT-TRANSACTIONAL-001`: with no published policy every service send is refused, so a test
    of anything downstream of the manual-send guard publishes it first -- through the same function
    `scripts/publish_messaging_policy.py` calls, by an active `OWNER_ADMIN`, as production requires.
    """

    owner = uuid4()
    moment = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Chủ tiệm', 'ACTIVE', %s)
            """,
            (owner, f"policy-owner-{owner}", moment),
        )
        cursor.execute(
            """
            INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
            VALUES (%s, %s, 'OWNER_ADMIN', %s)
            """,
            (uuid4(), owner, moment),
        )
    document = payload or json.loads(MESSAGING_POLICY_TEMPLATE.read_text(encoding="utf-8"))
    publish_messaging_policy(connection, actor_id=owner, payload=document)
    return owner


def record_customer_message(
    connection: Any,
    contact_binding_id: UUID,
    *,
    received_at: datetime,
    channel: str = "INTERNAL_TEST",
    disposition: OptOutDisposition = OptOutDisposition.NONE,
) -> UUID:
    """An inbound message from the contact, through the ingress path production uses.

    `NONE` is a customer writing to the shop -- the `CUSTOMER_INITIATED` basis and release evidence.
    `WITHDRAW` is a STOP, which suppresses both purposes on that channel.
    """

    result = InboxRepository().record(
        connection,
        InboundWebhook(
            provider="TEST_PROVIDER",
            channel_account_id=f"account-{uuid4().hex}",
            provider_event_id=f"event-{uuid4().hex}",
            event_type="MESSAGE",
            channel=channel,
            payload=EncryptedInboundPayload.from_ciphertext_and_plaintext(
                ciphertext=b"sealed", authenticated_plaintext=f"msg-{uuid4().hex}".encode()
            ),
            opt_out_disposition=disposition,
            contact_binding_id=contact_binding_id,
            opt_out_registry_version=(
                None if disposition is OptOutDisposition.NONE else "opt-out-v1"
            ),
            correlation_id=uuid4(),
            received_at=received_at,
        ),
    )
    return result.webhook_event_id


def grant_service_basis(
    connection: Any, contact_binding_id: UUID, *, received_at: datetime
) -> UUID:
    """Publish the policy and give the contact a `CUSTOMER_INITIATED` basis at `received_at`."""

    publish_test_messaging_policy(connection)
    return record_customer_message(connection, contact_binding_id, received_at=received_at)
