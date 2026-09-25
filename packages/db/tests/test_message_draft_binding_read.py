"""`MESSAGE-DRAFT-BINDING-001`: reading what a `SEND_MESSAGE` envelope over a draft binds.

`API-INTEGRITY-002` made the server compute a draft's binding and exposed it nowhere, so no console
could raise the envelope and no approver could read the words being approved. The read is store
scoped: role and MFA, then membership of the named store, then a draft of another store answers
exactly as a missing one does.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from message_draft_test_data import DRAFT_TEXT, current_binding, seed_message_draft
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.message_drafts import (
    EDITED_TEXT_REVISION,
    read_message_draft_binding_for_store,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.store_access import StoreAccessError
from nha_trang_laundry_db.stores import StoreRepository


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url, autocommit=True) as established:
        apply_migrations(established)
        yield established


def _staff(role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
    return StaffPrincipal(uuid4(), f"binding-{uuid4().hex}", frozenset({role}), mfa)


def _store(connection: Any, *members: StaffPrincipal) -> UUID:
    store_id = uuid4()
    moment = datetime.now(UTC)
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
    )
    with connection.transaction(), connection.cursor() as cursor:
        for member in members:
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s) ON CONFLICT (id) DO NOTHING
                """,
                (member.staff_user_id, member.oidc_subject, moment),
            )
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (member.staff_user_id, store_id, member.staff_user_id, moment),
            )
    return store_id


def _read(connection: Any, principal: StaffPrincipal, store_id: UUID, draft_id: UUID) -> Any:
    with connection.transaction(), connection.cursor() as cursor:
        return read_message_draft_binding_for_store(
            cursor, store_id=store_id, agent_run_id=draft_id, principal=principal
        )


@pytest.mark.parametrize(
    "role", [StaffRole.OPS_APPROVER, StaffRole.OWNER_ADMIN, StaffRole.OPERATOR]
)
def test_a_member_reads_exactly_the_binding_the_server_compares_against(
    connection: Any, role: StaffRole
) -> None:
    reader = _staff(role)
    store_id = _store(connection, reader)
    draft = seed_message_draft(connection, store_id)

    read = _read(connection, reader, store_id, draft.agent_run_id)

    assert read == current_binding(connection, draft.agent_run_id)
    assert read.text == DRAFT_TEXT
    assert read.contact_binding_id == draft.contact_binding_id
    assert read.resource_version == 1


def test_a_reviewers_edit_is_what_is_read_and_it_moves_the_revision(connection: Any) -> None:
    approver = _staff(StaffRole.OPS_APPROVER)
    store_id = _store(connection, approver)
    draft = seed_message_draft(connection, store_id)
    before = _read(connection, approver, store_id, draft.agent_run_id)
    ShadowConsoleRepository().decide_draft(
        connection,
        agent_run_id=draft.agent_run_id,
        decision="EDIT",
        principal=approver,
        correlation_id=uuid4(),
        edited_text="Dạ, tiệm giao tận nơi trong hôm nay ạ.",
    )

    after = _read(connection, approver, store_id, draft.agent_run_id)

    assert after.text == "Dạ, tiệm giao tận nơi trong hôm nay ạ."
    assert after.resource_version == EDITED_TEXT_REVISION
    assert after.rendered_hash != before.rendered_hash
    assert after.snapshot_hash != before.snapshot_hash


def test_a_rejected_draft_has_nothing_to_read(connection: Any) -> None:
    approver = _staff(StaffRole.OPS_APPROVER)
    store_id = _store(connection, approver)
    draft = seed_message_draft(connection, store_id)
    ShadowConsoleRepository().decide_draft(
        connection,
        agent_run_id=draft.agent_run_id,
        decision="REJECT",
        principal=approver,
        correlation_id=uuid4(),
        reason_code="TONE_NOT_APPROPRIATE",
    )

    assert _read(connection, approver, store_id, draft.agent_run_id) is None


def test_another_stores_draft_through_ones_own_store_is_indistinguishable_from_none(
    connection: Any,
) -> None:
    ours, theirs = _staff(StaffRole.OPS_APPROVER), _staff(StaffRole.OPS_APPROVER)
    our_store, their_store = _store(connection, ours), _store(connection, theirs)
    their_draft = seed_message_draft(connection, their_store)

    assert _read(connection, ours, our_store, their_draft.agent_run_id) is None
    assert _read(connection, ours, our_store, uuid4()) is None


def test_another_store_and_an_unknown_store_are_one_refusal(connection: Any) -> None:
    ours, theirs = _staff(StaffRole.OPS_APPROVER), _staff(StaffRole.OPS_APPROVER)
    _store(connection, ours)
    their_store = _store(connection, theirs)
    their_draft = seed_message_draft(connection, their_store)

    refusals = []
    for store_id in (their_store, uuid4()):
        with pytest.raises(StoreAccessError) as refused:
            _read(connection, ours, store_id, their_draft.agent_run_id)
        refusals.append(str(refused.value))
    assert refusals[0] == refusals[1]


@pytest.mark.parametrize(
    ("role", "mfa"),
    [(StaffRole.AUDITOR, True), (StaffRole.OPERATOR, False)],
    ids=["auditor", "operator-without-mfa"],
)
def test_a_role_without_a_part_in_a_send_is_refused_even_as_a_member(
    connection: Any, role: StaffRole, mfa: bool
) -> None:
    principal = _staff(role, mfa=mfa)
    store_id = _store(connection, principal)
    draft = seed_message_draft(connection, store_id)

    with pytest.raises(StoreAccessError):
        _read(connection, principal, store_id, draft.agent_run_id)
