from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
)
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.manual_sends import ManualSendStateError
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.catalog import ApprovalAction

HASH_A = "JCS-SHA256-V1:" + "a" * 64
HASH_B = "JCS-SHA256-V1:" + "b" * 64


def _principal(role: StaffRole) -> StaffPrincipal:
    return StaffPrincipal(
        uuid4(),
        f"staff-operations-{role.value.casefold()}-{uuid4().hex}",
        frozenset({role}),
        True,
    )


def test_real_service_manual_send_is_idempotent_version_bound_and_atomic() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    requester = _principal(StaffRole.OPS_APPROVER)
    owner = _principal(StaffRole.OWNER_ADMIN)
    sender = _principal(StaffRole.OPERATOR)
    recipient_id = uuid4()
    requested_at = datetime.now(UTC) - timedelta(seconds=2)

    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        approval = ApprovalRepository().request(
            connection,
            ApprovalRequestCommand(
                ApprovalAction.SEND_MESSAGE,
                "MESSAGE_DRAFT",
                uuid4(),
                1,
                HASH_A,
                HASH_B,
                "staff-operations-policy-v1",
                requester.staff_user_id,
                f"staff-operations-approval-{uuid4().hex}",
                uuid4(),
                requested_at,
            ),
        )
        ApprovalRepository().decide(
            connection,
            ApprovalDecisionCommand(
                approval.approval_request_id,
                ApprovalDecision.APPROVED,
                1,
                HASH_A,
                HASH_B,
                "HUMAN_REVIEW_COMPLETE",
                owner,
                uuid4(),
                requested_at + timedelta(seconds=1),
            ),
        )

    service = OperationsService(AuthSettings(database_url=database_url))
    prepared = service.prepare_manual_send(
        approval_request_id=approval.approval_request_id,
        observed_resource_version=1,
        observed_snapshot_hash=HASH_A,
        observed_rendered_hash=HASH_B,
        recipient_binding_id=recipient_id,
        channel="INTERNAL_TEST",
        idempotency_key="staff-operations-manual-prepare",
        principal=sender,
    )
    replayed_prepare = service.prepare_manual_send(
        approval_request_id=approval.approval_request_id,
        observed_resource_version=1,
        observed_snapshot_hash=HASH_A,
        observed_rendered_hash=HASH_B,
        recipient_binding_id=recipient_id,
        channel="INTERNAL_TEST",
        idempotency_key="staff-operations-manual-prepare",
        principal=sender,
    )
    assert replayed_prepare.value.manual_send_envelope_id == prepared.value.manual_send_envelope_id
    assert replayed_prepare.replayed is True
    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        service.prepare_manual_send(
            approval_request_id=approval.approval_request_id,
            observed_resource_version=1,
            observed_snapshot_hash=HASH_A,
            observed_rendered_hash=HASH_B,
            recipient_binding_id=uuid4(),
            channel="INTERNAL_TEST",
            idempotency_key="staff-operations-manual-prepare",
            principal=sender,
        )
    with pytest.raises(ManualSendStateError, match="version is stale"):
        service.attest_manual_send(
            manual_send_envelope_id=prepared.value.manual_send_envelope_id,
            observed_resource_version=1,
            exact_rendered_hash=HASH_B,
            expected_envelope_row_version=2,
            sent_at=datetime.now(UTC) - timedelta(seconds=1),
            idempotency_key="staff-operations-manual-attest-stale",
            principal=sender,
        )
    sent_at = datetime.now(UTC) - timedelta(seconds=1)
    attested = service.attest_manual_send(
        manual_send_envelope_id=prepared.value.manual_send_envelope_id,
        observed_resource_version=1,
        exact_rendered_hash=HASH_B,
        expected_envelope_row_version=1,
        sent_at=sent_at,
        idempotency_key="staff-operations-manual-attest",
        principal=sender,
    )
    replayed_attestation = service.attest_manual_send(
        manual_send_envelope_id=prepared.value.manual_send_envelope_id,
        observed_resource_version=1,
        exact_rendered_hash=HASH_B,
        expected_envelope_row_version=1,
        sent_at=sent_at,
        idempotency_key="staff-operations-manual-attest",
        principal=sender,
    )

    assert attested.value.status == "MANUAL_SEND_RECORDED"
    assert attested.row_version == 2
    assert replayed_attestation.replayed is True
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM manual_send_attestations WHERE approval_request_id = %s",
            (approval.approval_request_id,),
        )
        assert cursor.fetchone() == (1,)
        cursor.execute(
            "SELECT count(*) FROM approval_executions WHERE approval_request_id = %s",
            (approval.approval_request_id,),
        )
        assert cursor.fetchone() == (0,)
        cursor.execute(
            """
            SELECT count(*) FROM domain_events
            WHERE aggregate_id = %s AND event_type = 'MANUAL_SEND_RECORDED'
            """,
            (prepared.value.manual_send_envelope_id,),
        )
        assert cursor.fetchone() == (1,)
