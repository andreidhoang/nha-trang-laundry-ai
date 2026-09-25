"""`API-INTEGRITY-002`, finding 2: an unknown send belongs to a shop, and only that shop sees it.

`channel_send_receipts` had no store column (`0020`), so the unknown-send queue listed every
store's receipts to any Shadow reader and let any approver of any store resolve them. `0049` gives a
receipt its store, derived from the approval that authorised the send where there is one, and the
queue now reads and resolves by membership of that store, exactly like the draft queue beside it.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from message_draft_test_data import current_binding, seed_message_draft
from nha_trang_laundry_contracts.channel_envelope import (
    ChannelDeliveryStatus,
    ChannelMessageKind,
    ChannelOutboundReceipt,
    ChannelProvider,
    ReconciliationState,
    SendAttempt,
    SendAttemptOutcome,
    SendAuthorization,
    SendAuthorizationSource,
)
from nha_trang_laundry_contracts.runtime_registry import ReleaseCapability
from nha_trang_laundry_db.approvals import ApprovalRepository, ApprovalRequestCommand
from nha_trang_laundry_db.channel import ChannelReceiptError, ChannelSendReceiptRepository
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.shadow_console import (
    ShadowAuthorizationError,
    ShadowConsoleRepository,
    ShadowStateError,
)
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.catalog import ApprovalAction

NOW = datetime.now(UTC)


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _staff(role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
    return StaffPrincipal(
        uuid4(), f"receipt-{role.value.casefold()}-{uuid4().hex}", frozenset({role}), mfa
    )


def _store(connection: Any, *members: StaffPrincipal) -> UUID:
    store_id, assigner = uuid4(), uuid4()
    StoreRepository.create(
        connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
    )
    with connection.transaction(), connection.cursor() as cursor:
        for identifier, subject in [(assigner, f"oidc-{assigner}")] + [
            (m.staff_user_id, m.oidc_subject) for m in members
        ]:
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s) ON CONFLICT (id) DO NOTHING
                """,
                (identifier, subject, NOW),
            )
        for member in members:
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (member.staff_user_id, store_id, assigner, NOW),
            )
    return store_id


def _approval(connection: Any, store_id: UUID, requester: StaffPrincipal) -> UUID:
    """A real SEND_MESSAGE approval of this store, which is what a HUMAN_APPROVAL send spends."""
    draft = seed_message_draft(connection, store_id)
    binding = current_binding(connection, draft.agent_run_id)
    return (
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
                requester.staff_user_id,
                f"receipt-approval-{uuid4().hex}",
                uuid4(),
                store_id=store_id,
            ),
        )
        .approval_request_id
    )


def _receipt(approval_ref: UUID | None) -> ChannelOutboundReceipt:
    authorization = (
        SendAuthorization(source=SendAuthorizationSource.HUMAN_APPROVAL, approval_ref=approval_ref)
        if approval_ref is not None
        else SendAuthorization(
            source=SendAuthorizationSource.CAPABILITY_AUTHORIZED,
            capability=ReleaseCapability.INTAKE_RECEIPT,
        )
    )
    return ChannelOutboundReceipt(
        receipt_id=uuid4(),
        outbox_id=uuid4(),
        idempotency_key=f"outbox-{uuid4().hex}",
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        message_kind=ChannelMessageKind.INTAKE_RECEIPT,
        authorization=authorization,
        attempt=SendAttempt(attempt_number=1, started_at=NOW, outcome=SendAttemptOutcome.TIMEOUT),
        delivery_status=ChannelDeliveryStatus.FAILED,
        reconciliation_state=ReconciliationState.UNKNOWN_REQUIRES_HUMAN,
    )


class _Shop:
    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self.operator = _staff(StaffRole.OPERATOR)
        self.approver = _staff(StaffRole.OPS_APPROVER)
        self.auditor = _staff(StaffRole.AUDITOR)
        self.store_id = _store(connection, self.operator, self.approver, self.auditor)

    def unknown_send(self, connection: psycopg.Connection[Any], *, approved: bool = True) -> UUID:
        approval_ref = _approval(connection, self.store_id, self.operator) if approved else None
        receipt = _receipt(approval_ref)
        ChannelSendReceiptRepository().record_attempt(
            connection, receipt, store_id=self.store_id, correlation_id=uuid4()
        )
        return receipt.receipt_id


def _listed(connection: Any, shop: _Shop, principal: StaffPrincipal) -> list[UUID]:
    return [
        item.receipt_id
        for item in ShadowConsoleRepository.list_unknown_sends(
            connection, store_id=shop.store_id, principal=principal
        )
    ]


def _resolve(connection: Any, receipt_id: UUID, principal: StaffPrincipal) -> None:
    ShadowConsoleRepository().resolve_unknown_send(
        connection,
        receipt_id=receipt_id,
        resolution=ReconciliationState.CONFIRMED_NOT_SENT,
        principal=principal,
        correlation_id=uuid4(),
    )


def _state(connection: Any, receipt_id: UUID) -> tuple[Any, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT store_id, reconciliation_state FROM channel_send_receipts "
            "WHERE receipt_id = %s",
            (receipt_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return tuple(row)


# --- a receipt carries its store ------------------------------------------------------------


def test_a_receipt_is_recorded_with_its_store_for_both_authorization_sources(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(postgres_connection)
    approved = shop.unknown_send(postgres_connection, approved=True)
    capability = shop.unknown_send(postgres_connection, approved=False)

    assert _state(postgres_connection, approved)[0] == shop.store_id
    assert _state(postgres_connection, capability)[0] == shop.store_id


def test_a_receipt_naming_another_stores_approval_is_refused(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    ours, theirs = _Shop(postgres_connection), _Shop(postgres_connection)
    their_approval = _approval(postgres_connection, theirs.store_id, theirs.operator)

    with pytest.raises(ChannelReceiptError, match="approval"):
        ChannelSendReceiptRepository().record_attempt(
            postgres_connection,
            _receipt(their_approval),
            store_id=ours.store_id,
            correlation_id=uuid4(),
        )


def test_a_receipt_naming_an_approval_that_does_not_exist_is_refused(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(postgres_connection)

    with pytest.raises(ChannelReceiptError, match="approval"):
        ChannelSendReceiptRepository().record_attempt(
            postgres_connection, _receipt(uuid4()), store_id=shop.store_id, correlation_id=uuid4()
        )


def test_the_database_refuses_a_new_receipt_with_no_store(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            INSERT INTO channel_send_receipts (
                receipt_id, outbox_id, idempotency_key, provider, message_kind,
                authorization_source, capability, egress_suppression_check, attempt_number,
                attempt_started_at, attempt_outcome, delivery_status, reconciliation_state,
                recorded_at
            ) VALUES (%s, %s, %s, 'TELEGRAM_SANDBOX', 'INTAKE_RECEIPT',
                      'CAPABILITY_AUTHORIZED', 'INTAKE_RECEIPT', 'PASSED_IN_SEND_TRANSACTION', 1,
                      %s, 'TIMEOUT', 'FAILED', 'UNKNOWN_REQUIRES_HUMAN', %s)
            """,
            (uuid4(), uuid4(), f"outbox-{uuid4().hex}", NOW, NOW),
        )


# --- listing is the shop's ------------------------------------------------------------------


def test_the_queue_lists_this_stores_unknown_sends_and_not_another_stores(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    ours, theirs = _Shop(postgres_connection), _Shop(postgres_connection)
    mine = ours.unknown_send(postgres_connection)
    other = theirs.unknown_send(postgres_connection)

    assert _listed(postgres_connection, ours, ours.approver) == [mine]
    assert _listed(postgres_connection, theirs, theirs.approver) == [other]
    # An auditor reads its own shop's queue; it never resolves.
    assert _listed(postgres_connection, ours, ours.auditor) == [mine]


def test_a_member_of_another_store_cannot_list_this_stores_queue(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    ours, theirs = _Shop(postgres_connection), _Shop(postgres_connection)
    ours.unknown_send(postgres_connection)

    with pytest.raises(ShadowAuthorizationError):
        _listed(postgres_connection, ours, theirs.approver)


def test_listing_requires_mfa_even_for_a_member(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(postgres_connection)
    unverified = _staff(StaffRole.OPERATOR, mfa=False)
    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s)
            """,
            (unverified.staff_user_id, unverified.oidc_subject, NOW),
        )
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (unverified.staff_user_id, shop.store_id, unverified.staff_user_id, NOW),
        )
    shop.unknown_send(postgres_connection)

    with pytest.raises(ShadowAuthorizationError):
        _listed(postgres_connection, shop, unverified)


# --- resolving is the shop's ----------------------------------------------------------------


def test_an_approver_of_another_store_cannot_resolve_and_learns_nothing(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    ours, theirs = _Shop(postgres_connection), _Shop(postgres_connection)
    receipt_id = ours.unknown_send(postgres_connection)

    with pytest.raises(ShadowAuthorizationError) as foreign:
        _resolve(postgres_connection, receipt_id, theirs.approver)
    with pytest.raises(ShadowAuthorizationError) as missing:
        _resolve(postgres_connection, uuid4(), theirs.approver)

    # Another shop's receipt and a receipt that does not exist are the same refusal.
    assert str(foreign.value) == str(missing.value)
    assert _state(postgres_connection, receipt_id) == (ours.store_id, "UNKNOWN_REQUIRES_HUMAN")
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM audit_events WHERE aggregate_id = %s "
            "AND action = 'CHANNEL_SEND_RECONCILE'",
            (receipt_id,),
        )
        assert cursor.fetchone() == (0,)


def test_an_approver_of_this_store_resolves_its_receipt(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Positive control, and it leaves the queue."""
    shop = _Shop(postgres_connection)
    receipt_id = shop.unknown_send(postgres_connection)

    _resolve(postgres_connection, receipt_id, shop.approver)

    assert _state(postgres_connection, receipt_id) == (shop.store_id, "CONFIRMED_NOT_SENT")
    assert _listed(postgres_connection, shop, shop.approver) == []
    with pytest.raises(ShadowStateError, match="awaiting human reconciliation"):
        _resolve(postgres_connection, receipt_id, shop.approver)


def test_an_operator_of_this_store_still_cannot_resolve(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(postgres_connection)
    receipt_id = shop.unknown_send(postgres_connection)

    with pytest.raises(ShadowAuthorizationError):
        _resolve(postgres_connection, receipt_id, shop.operator)
    assert _state(postgres_connection, receipt_id)[1] == "UNKNOWN_REQUIRES_HUMAN"
