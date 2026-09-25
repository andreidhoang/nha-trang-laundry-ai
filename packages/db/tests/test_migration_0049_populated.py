"""`0049` applied to a database that already holds receipts and envelopes, not an empty one.

`test_migration_populated.py` explains why this shape of test exists: a migration that backfills
applies cleanly to the empty database every other test uses and can still fail, or silently
mis-attribute, on the first real one. `0049` gives `channel_send_receipts` a store, so the question
that matters is what happens to receipts written before a receipt had one:

* a receipt authorised by a real approval takes that approval's store;
* a receipt whose store cannot be derived is **not** given one. It stays null, which every
  store-scoped read excludes and every resolution refuses -- fail closed, never visible to all --
  unless the operator names the store with `ntl.legacy_receipt_store` before migrating;
* the new rules bind new rows only: legacy rows survive, and a new row without a store is refused.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from message_draft_test_data import current_binding, seed_message_draft
from nha_trang_laundry_contracts.channel_envelope import ReconciliationState
from nha_trang_laundry_db.approvals import ApprovalRepository, ApprovalRequestCommand
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import MIGRATIONS_DIRECTORY, apply_migrations
from nha_trang_laundry_db.shadow_console import ShadowAuthorizationError, ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import ApprovalAction
from psycopg import sql
from psycopg.conninfo import make_conninfo

MIGRATION_UNDER_TEST = "0049"
NOW = datetime(2026, 9, 25, 3, tzinfo=UTC)


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def scratch_database() -> Generator[str, None, None]:
    configured = _database_url()
    maintenance = make_conninfo(configured, dbname="postgres")
    name = f"ntl_migration_{uuid4().hex[:12]}"
    with psycopg.connect(maintenance, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(configured, dbname=name)
    finally:
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def migrations_before(tmp_path: Path) -> Path:
    directory = tmp_path / "migrations"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIRECTORY.glob("*.sql")):
        if path.name[:4] < MIGRATION_UNDER_TEST:
            shutil.copy2(path, directory / path.name)
    return directory


def _staff(role: StaffRole) -> StaffPrincipal:
    return StaffPrincipal(uuid4(), f"legacy-{uuid4().hex}", frozenset({role}), True)


def _store_with(connection: Any, *members: StaffPrincipal) -> UUID:
    store_id = uuid4()
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
    )
    with connection.transaction(), connection.cursor() as cursor:
        for member in members:
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
                """,
                (member.staff_user_id, member.oidc_subject, NOW),
            )
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (member.staff_user_id, store_id, member.staff_user_id, NOW),
            )
    return store_id


def _legacy_receipt(connection: Any, *, approval_ref: UUID | None) -> UUID:
    """A receipt in the pre-`0049` shape: no store column exists to write."""
    receipt_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO channel_send_receipts (
                receipt_id, outbox_id, idempotency_key, provider, message_kind,
                authorization_source, approval_ref, capability, egress_suppression_check,
                attempt_number, attempt_started_at, attempt_outcome, delivery_status,
                reconciliation_state, recorded_at
            ) VALUES (%s, %s, %s, 'TELEGRAM_SANDBOX', 'INTAKE_RECEIPT', %s, %s, %s,
                      'PASSED_IN_SEND_TRANSACTION', 1, %s, 'TIMEOUT', 'FAILED',
                      'UNKNOWN_REQUIRES_HUMAN', %s)
            """,
            (
                receipt_id,
                uuid4(),
                f"outbox-{uuid4().hex}",
                "HUMAN_APPROVAL" if approval_ref else "CAPABILITY_AUTHORIZED",
                approval_ref,
                None if approval_ref else "INTAKE_RECEIPT",
                NOW,
                NOW,
            ),
        )
    return receipt_id


def _legacy_envelope(connection: Any, approval_id: UUID, resource_id: UUID) -> UUID:
    """An envelope from before the recipient was derived: its recipient is nobody's."""
    envelope_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO manual_send_envelopes (
                id, approval_request_id, resource_id, resource_version, snapshot_hash,
                rendered_hash, recipient_binding_id, channel, purpose, status, row_version,
                prepared_by, prepared_at, updated_at
            ) VALUES (%s, %s, %s, 1, %s, %s, %s, 'INTERNAL_TEST', 'TRANSACTIONAL',
                      'APPROVED_FOR_MANUAL_SEND', 1, %s, %s, %s)
            """,
            (
                envelope_id,
                approval_id,
                resource_id,
                "JCS-SHA256-V1:" + "a" * 64,
                "JCS-SHA256-V1:" + "b" * 64,
                uuid4(),
                uuid4(),
                NOW,
                NOW,
            ),
        )
    return envelope_id


class _Legacy:
    """One store's worth of pre-`0049` rows: one derivable receipt, two that are not."""

    def __init__(self, connection: Any) -> None:
        self.requester = _staff(StaffRole.OPERATOR)
        self.approver = _staff(StaffRole.OPS_APPROVER)
        self.store_id = _store_with(connection, self.requester, self.approver)
        draft = seed_message_draft(connection, self.store_id)
        binding = current_binding(connection, draft.agent_run_id)
        self.approval_id = (
            ApprovalRepository()
            .request(
                connection,
                ApprovalRequestCommand(
                    ApprovalAction.SEND_MESSAGE,
                    "MESSAGE_DRAFT",
                    draft.agent_run_id,
                    binding.resource_version,
                    binding.snapshot_hash,
                    binding.rendered_hash,
                    "manual-send-policy-v1",
                    self.requester.staff_user_id,
                    f"legacy-{uuid4().hex}",
                    uuid4(),
                    store_id=self.store_id,
                ),
            )
            .approval_request_id
        )
        self.derivable = _legacy_receipt(connection, approval_ref=self.approval_id)
        self.dangling = _legacy_receipt(connection, approval_ref=uuid4())
        self.capability = _legacy_receipt(connection, approval_ref=None)
        self.envelope = _legacy_envelope(connection, self.approval_id, draft.agent_run_id)


def _stores(connection: Any, *receipts: UUID) -> list[Any]:
    with connection.cursor() as cursor:
        found = []
        for receipt_id in receipts:
            cursor.execute(
                "SELECT store_id FROM channel_send_receipts WHERE receipt_id = %s", (receipt_id,)
            )
            row = cursor.fetchone()
            assert row is not None
            found.append(row[0])
    return found


def test_a_populated_ledger_is_backfilled_where_derivable_and_fails_closed_elsewhere(
    scratch_database: str, migrations_before: Path
) -> None:
    with psycopg.connect(scratch_database) as connection:
        assert MIGRATION_UNDER_TEST not in apply_migrations(connection, migrations_before)
        legacy = _Legacy(connection)

        assert MIGRATION_UNDER_TEST in apply_migrations(connection)

        assert _stores(connection, legacy.derivable, legacy.dangling, legacy.capability) == [
            legacy.store_id,
            None,
            None,
        ]
        # The store's own queue shows the one receipt it can be shown, and nothing else.
        listed = ShadowConsoleRepository.list_unknown_sends(
            connection, store_id=legacy.store_id, principal=legacy.approver
        )
        assert [item.receipt_id for item in listed] == [legacy.derivable]
        # An unattributed receipt is refused to everyone, never resolved by whoever asks first.
        for orphan in (legacy.dangling, legacy.capability):
            with pytest.raises(ShadowAuthorizationError):
                ShadowConsoleRepository().resolve_unknown_send(
                    connection,
                    receipt_id=orphan,
                    resolution=ReconciliationState.CONFIRMED_NOT_SENT,
                    principal=legacy.approver,
                    correlation_id=uuid4(),
                )
        # The legacy envelope with an unapproved recipient survives; the rule binds new rows.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM manual_send_envelopes WHERE id = %s", (legacy.envelope,)
            )
            assert cursor.fetchone() == (1,)
        # And the derivable receipt resolves normally for its own store's approver.
        ShadowConsoleRepository().resolve_unknown_send(
            connection,
            receipt_id=legacy.derivable,
            resolution=ReconciliationState.CONFIRMED_SENT,
            principal=legacy.approver,
            correlation_id=uuid4(),
        )


def test_an_operator_can_name_the_store_for_receipts_nothing_can_attribute(
    scratch_database: str, migrations_before: Path
) -> None:
    with psycopg.connect(scratch_database) as connection:
        apply_migrations(connection, migrations_before)
        legacy = _Legacy(connection)
        connection.execute(
            sql.SQL("SET ntl.legacy_receipt_store = {}").format(sql.Literal(str(legacy.store_id)))
        )
        connection.commit()

        apply_migrations(connection)

        assert (
            _stores(connection, legacy.derivable, legacy.dangling, legacy.capability)
            == [legacy.store_id] * 3
        )
