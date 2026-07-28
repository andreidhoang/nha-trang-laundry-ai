from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_contracts import AgentDeploymentStage
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalExecutionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.manual_sends import (
    ManualSendAttestationCommand,
    ManualSendAuthorizationError,
    ManualSendPrepareCommand,
    ManualSendRepository,
    ManualSendStateError,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.catalog import ActorRole, ApprovalAction

NOW = datetime(2026, 8, 1, 3, tzinfo=UTC)
HASH_A = "JCS-SHA256-V1:" + "a" * 64
HASH_B = "JCS-SHA256-V1:" + "b" * 64


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _principal(role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
    return StaffPrincipal(
        uuid4(), f"manual-send-{role.value.casefold()}-{uuid4().hex}", frozenset({role}), mfa
    )


def _approved_message(connection: psycopg.Connection[Any]) -> tuple[UUID, UUID]:
    approvals = ApprovalRepository()
    requester = _principal(StaffRole.OPS_APPROVER)
    owner = _principal(StaffRole.OWNER_ADMIN)
    created = approvals.request(
        connection,
        ApprovalRequestCommand(
            ApprovalAction.SEND_MESSAGE,
            "MESSAGE_DRAFT",
            uuid4(),
            1,
            HASH_A,
            HASH_B,
            "manual-send-policy-v1",
            requester.staff_user_id,
            f"manual-send-approval-{uuid4().hex}",
            uuid4(),
            NOW,
        ),
    )
    approvals.decide(
        connection,
        ApprovalDecisionCommand(
            created.approval_request_id,
            ApprovalDecision.APPROVED,
            1,
            HASH_A,
            HASH_B,
            owner,
            uuid4(),
            NOW + timedelta(seconds=1),
        ),
    )
    return created.approval_request_id, uuid4()


def test_manual_attestation_consumes_exact_approval_and_blocks_worker_execution(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    approval_id, recipient_id = _approved_message(postgres_connection)
    manual = ManualSendRepository()
    sender = _principal(StaffRole.OPERATOR)
    prepared = manual.prepare(
        postgres_connection,
        ManualSendPrepareCommand(
            approval_id,
            1,
            HASH_A,
            HASH_B,
            recipient_id,
            "INTERNAL_TEST",
            "TRANSACTIONAL",
            AgentDeploymentStage.SHADOW,
            sender,
            uuid4(),
            NOW + timedelta(seconds=2),
        ),
    )
    recorded = manual.attest(
        postgres_connection,
        ManualSendAttestationCommand(
            prepared.manual_send_envelope_id,
            1,
            HASH_B,
            sender,
            uuid4(),
            NOW + timedelta(seconds=3),
            NOW + timedelta(seconds=4),
        ),
    )

    assert recorded.status == "MANUAL_SEND_RECORDED"
    with pytest.raises(ApprovalStateError, match="reserved for manual send"):
        ApprovalRepository().claim_execution(
            postgres_connection,
            ApprovalExecutionCommand(
                approval_id,
                ActorRole.OUTBOX_WORKER,
                1,
                HASH_A,
                HASH_B,
                "manual-send-policy-v1",
                uuid4(),
                NOW + timedelta(seconds=5),
            ),
        )
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM manual_send_envelopes WHERE id = %s",
            (prepared.manual_send_envelope_id,),
        )
        state = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM manual_send_attestations WHERE manual_send_envelope_id = %s",
            (prepared.manual_send_envelope_id,),
        )
        attestations = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM approval_executions WHERE approval_request_id = %s",
            (approval_id,),
        )
        executions = cursor.fetchone()
    assert state == ("MANUAL_SEND_RECORDED",)
    assert attestations == (1,)
    assert executions == (0,)


def test_manual_send_fails_closed_for_marketing_stale_content_and_missing_mfa(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    approval_id, recipient_id = _approved_message(postgres_connection)
    manual = ManualSendRepository()
    sender = _principal(StaffRole.OPERATOR)
    base = ManualSendPrepareCommand(
        approval_id,
        1,
        HASH_A,
        HASH_B,
        recipient_id,
        "INTERNAL_TEST",
        "TRANSACTIONAL",
        AgentDeploymentStage.SHADOW,
        sender,
        uuid4(),
        NOW + timedelta(seconds=2),
    )
    with pytest.raises(ManualSendStateError, match="marketing"):
        manual.prepare(postgres_connection, replace(base, purpose="MARKETING"))
    with pytest.raises(ManualSendStateError, match="binding is stale"):
        manual.prepare(
            postgres_connection,
            replace(base, observed_rendered_hash=HASH_A),
        )
    with pytest.raises(ManualSendAuthorizationError, match="MFA"):
        manual.prepare(
            postgres_connection,
            replace(base, principal=_principal(StaffRole.OPERATOR, mfa=False)),
        )
    with pytest.raises(ManualSendStateError, match="shadow stage"):
        manual.prepare(
            postgres_connection,
            replace(base, deployment_stage=AgentDeploymentStage.ASSISTED),
        )
    with pytest.raises(ManualSendStateError, match="not configured"):
        manual.prepare(postgres_connection, replace(base, channel="UNCONFIGURED"))
