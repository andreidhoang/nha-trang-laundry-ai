"""A real agent draft to hang a `SEND_MESSAGE` envelope on.

`API-INTEGRITY-002` made `MESSAGE_DRAFT` a resolvable resource: an envelope must name a draft that
exists, in the store it names, with the digests the server computes from that draft's stored text.
Fixtures that used to raise an envelope over `uuid4()` with `"a" * 64` digests were exercising the
defect, so they now seed the draft production would have and ask the server for its binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.message_drafts import MessageDraftBinding, read_message_draft_binding
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository

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
