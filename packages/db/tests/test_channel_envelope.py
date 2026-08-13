"""CHANNEL-ENVELOPE-001: server-owned binding, database-level dedupe, one receipt per attempt."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import psycopg
import pytest
from jsonschema import Draft202012Validator
from nha_trang_laundry_contracts.channel_envelope import (
    ChannelAuthentication,
    ChannelAuthenticationScheme,
    ChannelContent,
    ChannelContentType,
    ChannelDeliveryStatus,
    ChannelInboundEnvelope,
    ChannelMessageKind,
    ChannelOutboundReceipt,
    ChannelProvider,
    ContactVerificationState,
    DedupeKeySource,
    IngressSuppression,
    IngressSuppressionDecision,
    ReconciliationResolution,
    ReconciliationState,
    SendAttempt,
    SendAttemptOutcome,
    SendAuthorization,
    SendAuthorizationSource,
)
from nha_trang_laundry_db.channel import (
    ChannelReceiptError,
    ChannelSendReceiptRepository,
    ContactChannelBindingRepository,
)
from nha_trang_laundry_db.inbox import (
    EncryptedInboundPayload,
    InboundWebhook,
    InboxOutcome,
    InboxRepository,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.consent import OptOutDisposition
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime.now(UTC)


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _envelope(**overrides: Any) -> ChannelInboundEnvelope:
    payload: dict[str, Any] = {
        "envelope_id": uuid4(),
        "provider": ChannelProvider.TELEGRAM_SANDBOX,
        "provider_update_id": "update-1",
        "dedupe_key_source": DedupeKeySource.PROVIDER_ASSIGNED,
        "received_at": NOW,
        "authentication": ChannelAuthentication(
            scheme=ChannelAuthenticationScheme.SECRET_HEADER, verified=True, skew_seconds=1
        ),
        "contact_binding": {
            "provider_user_ref": "tg:1",
            "contact_id": uuid4(),
            "verification_state": ContactVerificationState.UNVERIFIED,
        },
        "content": ChannelContent(content_type=ChannelContentType.TEXT, text="giặt 5 ký bao nhiêu"),
        "suppression": IngressSuppression(ingress_decision=IngressSuppressionDecision.ALLOW),
    }
    payload.update(overrides)
    return ChannelInboundEnvelope(**payload)


def _receipt(**overrides: Any) -> ChannelOutboundReceipt:
    payload: dict[str, Any] = {
        "receipt_id": uuid4(),
        "outbox_id": uuid4(),
        "idempotency_key": "outbox-attempt-0001",
        "provider": ChannelProvider.TELEGRAM_SANDBOX,
        "message_kind": ChannelMessageKind.LIST_PRICE_INFO,
        "authorization": SendAuthorization(
            source=SendAuthorizationSource.HUMAN_APPROVAL, approval_ref=uuid4()
        ),
        "attempt": SendAttempt(
            attempt_number=1,
            started_at=NOW,
            completed_at=NOW + timedelta(seconds=1),
            outcome=SendAttemptOutcome.ACCEPTED,
            provider_message_ref="pm-1",
        ),
        "delivery_status": ChannelDeliveryStatus.PROVIDER_ACCEPTED,
        "reconciliation_state": ReconciliationState.NOT_REQUIRED,
    }
    payload.update(overrides)
    return ChannelOutboundReceipt(**payload)


def _row(cursor: Any) -> tuple[Any, ...]:
    row = cursor.fetchone()
    assert row is not None
    return cast(tuple[Any, ...], row)


def _schema(name: str) -> Draft202012Validator:
    with (ROOT / "specs/contracts" / name).open(encoding="utf-8") as handle:
        return Draft202012Validator(json.load(handle))


# --- schema conformance -------------------------------------------------------------------


def test_envelope_model_output_satisfies_the_normative_json_schema() -> None:
    document = _envelope().canonical_document()

    _schema("channel-inbound-envelope-v1.schema.json").validate(document)

    assert document["contact_binding"]["resolved_by"] == "SERVER_BINDING_TABLE"


def test_receipt_model_output_satisfies_the_normative_json_schema() -> None:
    document = _receipt().canonical_document()

    _schema("channel-outbound-receipt-v1.schema.json").validate(document)


def test_envelope_rejects_an_unverified_request() -> None:
    with pytest.raises(ValidationError):
        _envelope(
            authentication={
                "scheme": ChannelAuthenticationScheme.SECRET_HEADER,
                "verified": False,
                "skew_seconds": 0,
            }
        )


def test_envelope_rejects_a_provider_supplied_binding_resolution() -> None:
    with pytest.raises(ValidationError):
        _envelope(
            contact_binding={
                "provider_user_ref": "tg:1",
                "contact_id": uuid4(),
                "verification_state": ContactVerificationState.VERIFIED,
                "resolved_by": "PROVIDER_PAYLOAD",
            }
        )


def test_envelope_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        _envelope(raw_provider_payload={"text": "leaked"})


def test_unsupported_content_is_normalized_rather_than_dropped() -> None:
    envelope = _envelope(
        content=ChannelContent(content_type=ChannelContentType.UNSUPPORTED, text=None)
    )

    assert envelope.content.content_type is ChannelContentType.UNSUPPORTED
    _schema("channel-inbound-envelope-v1.schema.json").validate(envelope.canonical_document())


# --- the unknown-outcome rule -------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome", [SendAttemptOutcome.TIMEOUT, SendAttemptOutcome.TRANSPORT_ERROR]
)
def test_an_ambiguous_outcome_can_never_be_recorded_as_settled(
    outcome: SendAttemptOutcome,
) -> None:
    with pytest.raises(ValidationError, match="ambiguous"):
        _receipt(
            attempt=SendAttempt(attempt_number=1, started_at=NOW, outcome=outcome),
            delivery_status=ChannelDeliveryStatus.FAILED,
            reconciliation_state=ReconciliationState.NOT_REQUIRED,
        )


def test_leaving_unknown_requires_a_resolution_record() -> None:
    with pytest.raises(ValidationError, match="resolution"):
        _receipt(reconciliation_state=ReconciliationState.CONFIRMED_SENT)


def test_a_human_resolution_names_its_actor() -> None:
    with pytest.raises(ValidationError, match="actor_id"):
        ReconciliationResolution(resolved_by="HUMAN_DECISION", resolved_at=NOW)


def test_provider_confirmation_resolves_an_unknown_outcome() -> None:
    receipt = _receipt(
        attempt=SendAttempt(attempt_number=2, started_at=NOW, outcome=SendAttemptOutcome.TIMEOUT),
        delivery_status=ChannelDeliveryStatus.PROVIDER_ACCEPTED,
        reconciliation_state=ReconciliationState.CONFIRMED_SENT,
        resolution=ReconciliationResolution(
            resolved_by="PROVIDER_CONFIRMATION", resolved_at=NOW + timedelta(minutes=5)
        ),
    )

    _schema("channel-outbound-receipt-v1.schema.json").validate(receipt.canonical_document())


def test_authorization_must_carry_its_own_evidence() -> None:
    with pytest.raises(ValidationError, match="approval_ref"):
        SendAuthorization(source=SendAuthorizationSource.HUMAN_APPROVAL)
    with pytest.raises(ValidationError, match="capability"):
        SendAuthorization(source=SendAuthorizationSource.CAPABILITY_AUTHORIZED)


# --- server-owned contact binding ---------------------------------------------------------


def test_unknown_provider_identity_creates_an_unverified_binding(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = ContactChannelBindingRepository()

    resolved = repository.resolve_or_create(
        postgres_connection,
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        provider_user_ref=f"tg:{uuid4()}",
        correlation_id=uuid4(),
    )

    assert resolved.created is True
    assert resolved.binding.verification_state is ContactVerificationState.UNVERIFIED
    assert resolved.binding.resolved_by == "SERVER_BINDING_TABLE"


def test_binding_resolution_is_stable_and_audited(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = ContactChannelBindingRepository()
    reference = f"tg:{uuid4()}"
    correlation_id = uuid4()

    first = repository.resolve_or_create(
        postgres_connection,
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        provider_user_ref=reference,
        correlation_id=correlation_id,
    )
    second = repository.resolve_or_create(
        postgres_connection,
        provider=ChannelProvider.TELEGRAM_SANDBOX,
        provider_user_ref=reference,
        correlation_id=correlation_id,
    )

    assert first.binding.contact_id == second.binding.contact_id
    assert second.created is False
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM audit_events WHERE aggregate_id = %s AND action = %s",
            (first.binding.contact_id, "CONTACT_CHANNEL_BINDING_CREATE"),
        )
        assert _row(cursor)[0] == 1


def test_verified_state_requires_its_evidence_at_the_database(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    with (
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        cursor.execute(
            """
                INSERT INTO contact_channel_bindings (
                    provider, provider_user_ref, contact_binding_id, verification_state,
                    row_version, created_at, updated_at
                ) VALUES ('ZALO_OA', %s, %s, 'VERIFIED', 1, %s, %s)
                """,
            (f"za:{uuid4()}", uuid4(), NOW, NOW),
        )


# --- one receipt per attempt --------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "delivery_status", "reconciliation_state"),
    [
        (
            SendAttemptOutcome.ACCEPTED,
            ChannelDeliveryStatus.PROVIDER_ACCEPTED,
            ReconciliationState.NOT_REQUIRED,
        ),
        (
            SendAttemptOutcome.REJECTED,
            ChannelDeliveryStatus.FAILED,
            ReconciliationState.NOT_REQUIRED,
        ),
        (SendAttemptOutcome.TIMEOUT, ChannelDeliveryStatus.FAILED, ReconciliationState.UNKNOWN),
        (
            SendAttemptOutcome.TRANSPORT_ERROR,
            ChannelDeliveryStatus.FAILED,
            ReconciliationState.UNKNOWN_REQUIRES_HUMAN,
        ),
    ],
)
def test_every_attempt_outcome_writes_exactly_one_receipt(
    postgres_connection: psycopg.Connection[Any],
    outcome: SendAttemptOutcome,
    delivery_status: ChannelDeliveryStatus,
    reconciliation_state: ReconciliationState,
) -> None:
    repository = ChannelSendReceiptRepository()
    receipt = _receipt(
        attempt=SendAttempt(attempt_number=1, started_at=NOW, outcome=outcome),
        delivery_status=delivery_status,
        reconciliation_state=reconciliation_state,
    )

    repository.record_attempt(postgres_connection, receipt, correlation_id=uuid4())

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM channel_send_receipts WHERE outbox_id = %s",
            (receipt.outbox_id,),
        )
        assert _row(cursor)[0] == 1


def test_an_unknown_outcome_is_surfaced_for_human_reconciliation(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = ChannelSendReceiptRepository()
    receipt = _receipt(
        attempt=SendAttempt(attempt_number=1, started_at=NOW, outcome=SendAttemptOutcome.TIMEOUT),
        delivery_status=ChannelDeliveryStatus.FAILED,
        reconciliation_state=ReconciliationState.UNKNOWN_REQUIRES_HUMAN,
    )

    repository.record_attempt(postgres_connection, receipt, correlation_id=uuid4())

    assert receipt.receipt_id in repository.unresolved_unknown_outcomes(postgres_connection)
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            "SELECT payload FROM outbox_events WHERE idempotency_key = %s",
            (f"channel-receipt:{receipt.outbox_id}:1",),
        )
        payload = _row(cursor)[0]
        assert payload["requires_human"] is True


def test_recording_the_same_attempt_twice_loses_at_the_database(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = ChannelSendReceiptRepository()
    receipt = _receipt()

    repository.record_attempt(postgres_connection, receipt, correlation_id=uuid4())

    duplicate = _receipt(
        receipt_id=uuid4(), outbox_id=receipt.outbox_id, idempotency_key=receipt.idempotency_key
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.record_attempt(postgres_connection, duplicate, correlation_id=uuid4())


def test_two_workers_racing_one_attempt_record_it_exactly_once(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    database_url = os.environ["DATABASE_URL"]
    receipt = _receipt()
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def record() -> None:
        try:
            barrier.wait(timeout=10)
            with psycopg.connect(database_url) as connection:
                ChannelSendReceiptRepository().record_attempt(
                    connection,
                    _receipt(
                        receipt_id=uuid4(),
                        outbox_id=receipt.outbox_id,
                        idempotency_key=receipt.idempotency_key,
                    ),
                    correlation_id=uuid4(),
                )
        except BaseException as error:  # the loser's error is the assertion
            failures.append(error)

    workers = [threading.Thread(target=record) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert len(failures) == 1
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM channel_send_receipts WHERE outbox_id = %s",
            (receipt.outbox_id,),
        )
        assert _row(cursor)[0] == 1


def test_receipt_repository_refuses_an_unreconcilable_outcome(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = ChannelSendReceiptRepository()
    receipt = _receipt(
        attempt=SendAttempt(attempt_number=1, started_at=NOW, outcome=SendAttemptOutcome.ACCEPTED),
        delivery_status=ChannelDeliveryStatus.PROVIDER_ACCEPTED,
        reconciliation_state=ReconciliationState.UNKNOWN_REQUIRES_HUMAN,
    )

    with pytest.raises(ChannelReceiptError, match="accepted send"):
        repository.record_attempt(postgres_connection, receipt, correlation_id=uuid4())


def test_receipt_write_is_atomic_with_its_event_and_audit_rows(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = ChannelSendReceiptRepository()
    receipt = _receipt()

    repository.record_attempt(postgres_connection, receipt, correlation_id=uuid4())

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT count(*) FROM channel_send_receipts WHERE outbox_id = %(outbox)s),
                (SELECT count(*) FROM domain_events
                 WHERE aggregate_type = 'CHANNEL_SEND' AND aggregate_id = %(outbox)s),
                (SELECT count(*) FROM audit_events
                 WHERE aggregate_type = 'CHANNEL_SEND' AND aggregate_id = %(outbox)s),
                (SELECT count(*) FROM outbox_events
                 WHERE aggregate_type = 'CHANNEL_SEND' AND aggregate_id = %(outbox)s)
            """,
            {"outbox": receipt.outbox_id},
        )
        assert _row(cursor) == (1, 1, 1, 1)


def test_a_receipt_row_cannot_be_hard_deleted(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    repository = ChannelSendReceiptRepository()
    receipt = _receipt()
    repository.record_attempt(postgres_connection, receipt, correlation_id=uuid4())

    with (
        postgres_connection.transaction(),
        postgres_connection.cursor() as cursor,
        pytest.raises(psycopg.errors.RaiseException),
    ):
        cursor.execute(
            "DELETE FROM channel_send_receipts WHERE receipt_id = %s", (receipt.receipt_id,)
        )


# --- ingress dedupe proven, not asserted ---------------------------------------------------


def test_two_workers_racing_a_replayed_update_lose_at_the_database_constraint(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The dedupe key is a database constraint, so a race cannot produce two inbox rows.

    An application-level check would leave a window between the SELECT and the INSERT. This test
    forces both writers through that window with a barrier and asserts the loser is rejected by
    PostgreSQL rather than by Python.
    """
    database_url = os.environ["DATABASE_URL"]
    provider_event_id = f"event-{uuid4().hex}"
    channel_account_id = f"account-{uuid4().hex}"
    barrier = threading.Barrier(2)
    outcomes: list[InboxOutcome] = []
    failures: list[BaseException] = []

    def record() -> None:
        command = InboundWebhook(
            provider="TEST_PROVIDER",
            channel_account_id=channel_account_id,
            provider_event_id=provider_event_id,
            event_type="MESSAGE",
            channel="TEST_CHANNEL",
            payload=EncryptedInboundPayload.from_ciphertext_and_plaintext(
                ciphertext=b"sealed", authenticated_plaintext=b'{"message":"xin chao"}'
            ),
            opt_out_disposition=OptOutDisposition.NONE,
            contact_binding_id=uuid4(),
            opt_out_registry_version="opt-out-v1",
            correlation_id=uuid4(),
            received_at=NOW,
        )
        try:
            barrier.wait(timeout=10)
            with psycopg.connect(database_url) as connection:
                outcomes.append(InboxRepository().record(connection, command).outcome)
        except BaseException as error:  # the loser's error is the assertion
            failures.append(error)

    workers = [threading.Thread(target=record) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert len(outcomes) + len(failures) == 2
    # Exactly one writer may create the row. The loser either observes DUPLICATE because the row
    # lock serialized it, or is rejected by the unique constraint; both are the database deciding.
    assert [outcome for outcome in outcomes if outcome is InboxOutcome.CREATED] == [
        InboxOutcome.CREATED
    ]
    assert all(isinstance(error, psycopg.errors.UniqueViolation) for error in failures)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM webhook_events
            WHERE provider = %s AND channel_account_id = %s AND provider_event_id = %s
            """,
            ("TEST_PROVIDER", channel_account_id, provider_event_id),
        )
        assert _row(cursor)[0] == 1
