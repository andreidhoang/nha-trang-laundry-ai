"""Give a synthetic principal a real shop to act in.

Every synthetic generator minted principals with `uuid4()` and no `staff_store_assignments` row,
which was invisible while the approval path checked only roles. Once an approval names its store
and the repository requires membership (`0034`, 2026-08-30), a principal with no assignment is
refused -- correctly, by the same rule that stops one store approving another's action.

So the fixtures seed what production requires instead of the check being relaxed to fit them. This
is the same correction the order fixtures needed when the order guard started reading the chain from
quote to customer: a fixture that could not exist in production tests a path no customer can reach.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.message_drafts import MessageDraftBinding, read_message_draft_binding
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository

#: The words of every synthetic message draft. Synthetic, customer-free, and fixed so a digest is
#: reproducible run to run.
SYNTHETIC_DRAFT_TEXT = "Dạ, đồ của anh/chị đã giặt xong, mời anh/chị ghé tiệm nhận ạ."


def seed_store_membership(
    connection: Any,
    *,
    principals: tuple[StaffPrincipal | UUID, ...],
    occurred_at: datetime,
    store_id: UUID | None = None,
) -> UUID:
    """Create the staff rows and assignments these principals need, and return their store.

    Idempotent on staff id, because generators reuse a principal across several commands within one
    fixture and a second insert must not be an error.
    """
    store = store_id or uuid4()
    # And the shop itself has to exist. `STORE-REGISTRY-001` gave `store_id` a table and a foreign
    # key, so a bare UUID is now refused by PostgreSQL rather than only by nobody. Same correction
    # as the paragraph above, one layer down: the fixture creates what the deploy-day runbook
    # creates.
    StoreRepository.create(
        connection,
        store_id=store,
        name="Cửa hàng tổng hợp",
        created_by=None,
        correlation_id=uuid4(),
        occurred_at=occurred_at,
    )
    assigner = uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên tổng hợp', 'ACTIVE', %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (assigner, f"synthetic-assigner-{assigner}", occurred_at),
        )
        for member in principals:
            # Generators hold either a full principal or a bare actor id, so both are accepted.
            staff_id = member if isinstance(member, UUID) else member.staff_user_id
            subject = (
                f"synthetic-actor-{staff_id}" if isinstance(member, UUID) else member.oidc_subject
            )
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên tổng hợp', 'ACTIVE', %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (staff_id, subject, occurred_at),
            )
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                ON CONFLICT DO NOTHING
                """,
                (staff_id, store, assigner, occurred_at),
            )
    return store


def seed_message_draft(
    connection: Any,
    *,
    store_id: UUID,
    conversation_binding_id: UUID,
    contact_binding_id: UUID,
    occurred_at: datetime,
    text: str = SYNTHETIC_DRAFT_TEXT,
) -> tuple[UUID, MessageDraftBinding]:
    """An agent run and the draft it produced, and the binding the server computes for it.

    `API-INTEGRITY-002` made `MESSAGE_DRAFT` a resolvable resource: a `SEND_MESSAGE` envelope must
    name a draft that exists in its store, at the revision and digests the server derives from the
    stored text. The manual-send generators used to raise envelopes over `uuid4()` with the digests
    their fixtures declare, which is now refused as content that does not exist -- so, as for the
    store above, the fixture seeds what production requires instead of the check being relaxed.
    """
    agent_run_id = uuid4()
    with connection.cursor() as cursor:
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
                contact_binding_id,
                f"sha256:{'a' * 64}",
                f"sha256:{'b' * 64}",
                f"sha256:{'c' * 64}",
                occurred_at,
            ),
        )
    ShadowConsoleRepository.record_draft(
        connection,
        agent_run_id=agent_run_id,
        store_id=store_id,
        conversation_binding_id=conversation_binding_id,
        contact_binding_id=contact_binding_id,
        draft_text=text,
        terminal_outcome="DRAFT",
        terminal_code="DRAFT_REQUIRES_HUMAN",
        tool_call_count=1,
        correlation_id=uuid4(),
        now=occurred_at,
    )
    return agent_run_id, current_message_binding(connection, agent_run_id)


def current_message_binding(connection: Any, agent_run_id: UUID) -> MessageDraftBinding:
    """The draft's binding as it stands now; a draft with nothing sendable is a fixture error."""
    with connection.transaction(), connection.cursor() as cursor:
        binding = read_message_draft_binding(cursor, agent_run_id)
    if binding is None:
        raise ValueError("synthetic message draft has no sendable content")
    return binding


__all__ = [
    "SYNTHETIC_DRAFT_TEXT",
    "current_message_binding",
    "seed_message_draft",
    "seed_store_membership",
]
