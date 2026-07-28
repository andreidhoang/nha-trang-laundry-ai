from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.approvals import (
    ApprovalAuthorizationError,
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalExecutionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
)
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.inbox import (
    EncryptedInboundPayload,
    InboundWebhook,
    InboxOutcome,
    InboxReplayConflictError,
    InboxRepository,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderAuthorizationError,
    OrderRepository,
    OrderStateError,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.quotes import QuoteRepository, QuoteRevisionCommand
from nha_trang_laundry_domain.catalog import (
    ActorRole,
    ApprovalAction,
    CommercialOrderStatus,
    FulfillmentMode,
    QuantityBasis,
    QuoteFinality,
    QuoteRevisionStatus,
)
from nha_trang_laundry_domain.consent import OptOutDisposition, SuppressionState
from nha_trang_laundry_domain.quotes import ImmutableQuoteSnapshot, build_quote_snapshot
from quote_test_data import PRICED_AT, make_quote_snapshot

HASH_A = "JCS-SHA256-V1:" + "a" * 64
HASH_B = "JCS-SHA256-V1:" + "b" * 64
NOW = datetime(2026, 8, 1, 3, tzinfo=UTC)


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def principal(role: StaffRole, *, mfa: bool = True, user_id: UUID | None = None) -> StaffPrincipal:
    return StaffPrincipal(
        user_id or uuid4(),
        f"test-{role.value.casefold()}-{uuid4().hex}",
        frozenset({role}),
        mfa,
    )


def test_inbox_replay_and_stop_suppression_are_durable_before_dispatch(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = InboxRepository()
    provider_event_id = f"event-{uuid4().hex}"
    contact_id = uuid4()
    original = InboundWebhook(
        provider="TEST_PROVIDER",
        channel_account_id=f"account-{uuid4().hex}",
        provider_event_id=provider_event_id,
        event_type="MESSAGE",
        channel="TEST_CHANNEL",
        payload=EncryptedInboundPayload.from_ciphertext_and_plaintext(
            ciphertext=b"sealed-original", authenticated_plaintext=b'{"message":"hello"}'
        ),
        opt_out_disposition=OptOutDisposition.NONE,
        contact_binding_id=contact_id,
        opt_out_registry_version="opt-out-v1",
        correlation_id=uuid4(),
        received_at=NOW,
    )

    first = repository.record(postgres_connection, original)
    duplicate = repository.record(
        postgres_connection,
        replace(
            original,
            payload=EncryptedInboundPayload.from_ciphertext_and_plaintext(
                ciphertext=b"sealed-retry", authenticated_plaintext=b'{"message":"hello"}'
            ),
            correlation_id=uuid4(),
        ),
    )
    assert first.outcome is InboxOutcome.CREATED
    assert duplicate.outcome is InboxOutcome.DUPLICATE
    assert duplicate.webhook_event_id == first.webhook_event_id

    with pytest.raises(InboxReplayConflictError, match="different payload"):
        repository.record(
            postgres_connection,
            replace(
                original,
                payload=EncryptedInboundPayload.from_ciphertext_and_plaintext(
                    ciphertext=b"sealed-substitution",
                    authenticated_plaintext=b'{"message":"substituted"}',
                ),
                correlation_id=uuid4(),
            ),
        )

    stop = repository.record(
        postgres_connection,
        replace(
            original,
            provider_event_id=f"stop-{uuid4().hex}",
            payload=EncryptedInboundPayload.from_ciphertext_and_plaintext(
                ciphertext=b"sealed-stop", authenticated_plaintext="DỪNG".encode()
            ),
            opt_out_disposition=OptOutDisposition.WITHDRAW,
            correlation_id=uuid4(),
        ),
    )
    assert stop.processing_status == "SAFETY_BLOCKED"
    with postgres_connection.cursor() as cursor:
        assert (
            repository.marketing_suppression(
                cursor, contact_binding_id=contact_id, channel="TEST_CHANNEL"
            )
            is SuppressionState.SUPPRESSED
        )
        cursor.execute(
            "SELECT encrypted_payload FROM webhook_events WHERE id = %s", (first.webhook_event_id,)
        )
        encrypted = cursor.fetchone()
        cursor.execute(
            "SELECT count(*) FROM inbox_replay_conflicts WHERE webhook_event_id = %s",
            (first.webhook_event_id,),
        )
        conflict_count = cursor.fetchone()
    assert encrypted == (b"sealed-original",)
    assert conflict_count == (1,)


def test_approval_is_server_derived_hash_bound_authorized_and_one_time(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = ApprovalRepository()
    requester = principal(StaffRole.OPS_APPROVER)
    owner = principal(StaffRole.OWNER_ADMIN)
    resource_id = uuid4()
    request = ApprovalRequestCommand(
        ApprovalAction.SEND_MESSAGE,
        "MESSAGE_DRAFT",
        resource_id,
        3,
        HASH_A,
        HASH_B,
        "approval-policy-v1",
        requester.staff_user_id,
        f"approval-{uuid4().hex}",
        uuid4(),
        NOW,
    )
    created = repository.request(postgres_connection, request)
    replayed = repository.request(postgres_connection, replace(request, correlation_id=uuid4()))
    assert replayed.approval_request_id == created.approval_request_id
    assert replayed.envelope_hash == created.envelope_hash
    assert replayed.replayed is True

    decision = ApprovalDecisionCommand(
        created.approval_request_id,
        ApprovalDecision.APPROVED,
        3,
        HASH_A,
        HASH_B,
        owner,
        uuid4(),
        NOW + timedelta(minutes=1),
    )
    with pytest.raises(ApprovalAuthorizationError, match="own action"):
        repository.decide(
            postgres_connection,
            replace(decision, principal=requester, correlation_id=uuid4()),
        )
    with pytest.raises(ApprovalAuthorizationError, match="MFA"):
        repository.decide(
            postgres_connection,
            replace(
                decision,
                principal=principal(StaffRole.OWNER_ADMIN, mfa=False),
                correlation_id=uuid4(),
            ),
        )
    with pytest.raises(ApprovalStateError, match="hash is stale"):
        repository.decide(
            postgres_connection,
            replace(decision, observed_rendered_hash=HASH_A, correlation_id=uuid4()),
        )

    approved = repository.decide(postgres_connection, decision)
    assert approved.status == "APPROVED"
    execution = ApprovalExecutionCommand(
        created.approval_request_id,
        ActorRole.OUTBOX_WORKER,
        3,
        HASH_A,
        HASH_B,
        "approval-policy-v1",
        uuid4(),
        NOW + timedelta(minutes=2),
    )
    with pytest.raises(ApprovalAuthorizationError, match="OUTBOX_WORKER"):
        repository.claim_execution(
            postgres_connection,
            replace(execution, worker_role=ActorRole.AGENT_RUNNER, correlation_id=uuid4()),
        )
    assert repository.claim_execution(postgres_connection, execution).status == "EXECUTING"
    with pytest.raises(ApprovalStateError, match="not executable"):
        repository.claim_execution(postgres_connection, replace(execution, correlation_id=uuid4()))


def test_expired_approval_is_durably_expired_not_silently_extended(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = ApprovalRepository()
    requester = principal(StaffRole.OPERATOR)
    owner = principal(StaffRole.OWNER_ADMIN)
    request = ApprovalRequestCommand(
        ApprovalAction.CONFIRM_SLOT,
        "SLOT_PROPOSAL",
        uuid4(),
        1,
        HASH_A,
        HASH_B,
        "approval-policy-v1",
        requester.staff_user_id,
        f"approval-{uuid4().hex}",
        uuid4(),
        NOW,
    )
    created = repository.request(postgres_connection, request)
    with pytest.raises(ApprovalStateError, match="expired"):
        repository.decide(
            postgres_connection,
            ApprovalDecisionCommand(
                created.approval_request_id,
                ApprovalDecision.APPROVED,
                1,
                HASH_A,
                HASH_B,
                owner,
                uuid4(),
                NOW + timedelta(minutes=16),
            ),
        )
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM approval_request_states WHERE approval_request_id = %s",
            (created.approval_request_id,),
        )
        assert cursor.fetchone() == ("EXPIRED",)


def test_order_creation_transition_replay_authorization_and_atomic_audit(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    owner = principal(StaffRole.OWNER_ADMIN)
    driver = principal(StaffRole.DRIVER, mfa=False)
    store_id = uuid4()
    quote_id = uuid4()
    quote = _approved_final_quote(quote_id)
    QuoteRepository().create_revision(
        postgres_connection,
        QuoteRevisionCommand(
            store_id,
            uuid4(),
            quote,
            0,
            0,
            owner.staff_user_id,
            uuid4(),
            NOW,
        ),
    )
    repository = OrderRepository()
    create = CreateOrderCommand(
        store_id,
        uuid4(),
        quote_id,
        1,
        quote.document.snapshot_hash,
        FulfillmentMode.PICKUP_AND_RETURN,
        owner,
        f"order-{uuid4().hex}",
        uuid4(),
        PRICED_AT + timedelta(hours=1),
    )
    first = repository.create(postgres_connection, create)
    replay = repository.create(postgres_connection, replace(create, correlation_id=uuid4()))
    assert replay.order_id == first.order_id
    assert replay.replayed is True
    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        repository.create(
            postgres_connection,
            replace(
                create,
                fulfillment_mode=FulfillmentMode.SELF_DROP_SELF_COLLECT,
                correlation_id=uuid4(),
            ),
        )

    transition = OrderTransitionCommand(
        first.order_id,
        1,
        owner,
        f"transition-{uuid4().hex}",
        uuid4(),
        commercial_target=CommercialOrderStatus.STORE_CONFIRMATION_PENDING,
        occurred_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(OrderAuthorizationError, match="not authorized"):
        repository.transition(
            postgres_connection,
            replace(transition, principal=driver, correlation_id=uuid4()),
        )
    moved = repository.transition(postgres_connection, transition)
    replayed = repository.transition(
        postgres_connection, replace(transition, correlation_id=uuid4())
    )
    assert moved.row_version == 2
    assert replayed.replayed is True
    with pytest.raises(OrderStateError, match="STALE_VERSION"):
        repository.transition(
            postgres_connection,
            replace(
                transition,
                idempotency_key=f"stale-{uuid4().hex}",
                correlation_id=uuid4(),
            ),
        )

    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION test_reject_operations_audit() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'injected operations audit failure';
            END;
            $$
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER test_operations_audit_failure
            BEFORE INSERT ON audit_events
            FOR EACH ROW EXECUTE FUNCTION test_reject_operations_audit()
            """
        )
    try:
        with pytest.raises(psycopg.Error, match="injected operations audit failure"):
            repository.transition(
                postgres_connection,
                OrderTransitionCommand(
                    first.order_id,
                    2,
                    owner,
                    f"audit-failure-{uuid4().hex}",
                    uuid4(),
                    commercial_target=CommercialOrderStatus.CONFIRMED,
                    occurred_at=NOW + timedelta(minutes=2),
                ),
            )
        with postgres_connection.cursor() as cursor:
            cursor.execute(
                "SELECT commercial_status, row_version FROM orders WHERE id = %s",
                (first.order_id,),
            )
            assert cursor.fetchone() == ("STORE_CONFIRMATION_PENDING", 2)
    finally:
        with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER IF EXISTS test_operations_audit_failure ON audit_events")
            cursor.execute("DROP FUNCTION IF EXISTS test_reject_operations_audit()")


def _approved_final_quote(quote_id: UUID) -> ImmutableQuoteSnapshot:
    estimate = make_quote_snapshot(quote_id, 1)
    lines = tuple(
        replace(line, quantity_basis=QuantityBasis.STAFF_MEASUREMENT)
        for line in estimate.data.lines
    )
    return build_quote_snapshot(
        replace(
            estimate.data,
            finality=QuoteFinality.APPROVED_EXACT,
            status=QuoteRevisionStatus.ACCEPTED_FINAL,
            lines=lines,
            required_approvals=(),
            approval_id=uuid4(),
        )
    )
