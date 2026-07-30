"""Synthetic STOP/outbox race using durable ingress and the final delivery gate."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from nha_trang_laundry_db.inbox import EncryptedInboundPayload, InboundWebhook, InboxRepository
from nha_trang_laundry_db.marketing_delivery import MarketingDeliveryRepository
from nha_trang_laundry_domain.catalog import ActorRole
from nha_trang_laundry_domain.consent import (
    OptOutDisposition,
    OptOutRegistry,
    SuppressionState,
    evaluate_opt_out,
)

from .fixtures import SyntheticFixtureBundle


@dataclass(frozen=True, slots=True)
class SyntheticStopOutboxRacePreflight:
    suppression_persisted_before_model_invocation: bool
    final_send_authorization_denied: bool
    provider_attempted: bool
    trace_id: str


@dataclass(frozen=True, slots=True)
class SyntheticAmbiguousOptOutPreflight:
    marketing_blocked_immediately: bool
    human_consent_review_opened: bool
    trace_id: str


def execute_stop_outbox_race_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticStopOutboxRacePreflight:
    payload = fixture.payload
    fault = payload.get("fault_injection")
    if not isinstance(fault, Mapping) or (
        fault.get("suppression_commits_after_claim_before_provider_attempt") is not True
    ):
        raise ValueError("STOP-race fixture does not select the required interleaving")
    timestamp = _clock(payload)
    contact_id = uuid4()
    outbox_event_id = uuid4()
    correlation_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                correlation_id, occurred_at, status, recipient_binding_id, purpose
            ) VALUES (
                %s, 'MESSAGE', %s, 'message.send_requested.v1', %s::jsonb, %s,
                %s, %s, 'PROCESSING', %s, 'MARKETING'
            )
            """,
            (
                outbox_event_id,
                outbox_event_id,
                '{"channel":"INTERNAL_TEST"}',
                f"synthetic-stop-race:{outbox_event_id}",
                correlation_id,
                timestamp,
                contact_id,
            ),
        )
    inbox_result = InboxRepository().record(
        connection,
        InboundWebhook(
            provider="SYNTHETIC_PROVIDER",
            channel_account_id=f"synthetic-{uuid4().hex}",
            provider_event_id="synthetic-stop-outbox-race-001",
            event_type="MESSAGE",
            channel="INTERNAL_TEST",
            payload=EncryptedInboundPayload.from_ciphertext_and_plaintext(
                ciphertext=b"synthetic-sealed-stop",
                authenticated_plaintext=b"STOP",
            ),
            opt_out_disposition=OptOutDisposition.WITHDRAW,
            contact_binding_id=contact_id,
            opt_out_registry_version="synthetic-opt-out-v1",
            correlation_id=correlation_id,
            received_at=timestamp,
        ),
    )
    with connection.cursor() as cursor:
        suppression = InboxRepository.marketing_suppression(
            cursor, contact_binding_id=contact_id, channel="INTERNAL_TEST"
        )
    reason = MarketingDeliveryRepository().hold_if_not_authorized(
        connection,
        outbox_event_id=outbox_event_id,
        worker_role=ActorRole.OUTBOX_WORKER,
        actor_id=uuid4(),
        correlation_id=correlation_id,
        now=timestamp,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM delivery_attempts WHERE outbox_event_id = %s",
            (outbox_event_id,),
        )
        provider_attempts = int(cursor.fetchone()[0])
    return SyntheticStopOutboxRacePreflight(
        suppression_persisted_before_model_invocation=(
            inbox_result.processing_status == "SAFETY_BLOCKED"
            and suppression is SuppressionState.SUPPRESSED
        ),
        final_send_authorization_denied=reason == "SUPPRESSED_CONTACT",
        provider_attempted=provider_attempts > 0,
        trace_id="synthetic-stop-outbox-race-001",
    )


def execute_ambiguous_opt_out_preflight(
    connection: Any, fixture: SyntheticFixtureBundle
) -> SyntheticAmbiguousOptOutPreflight:
    timestamp = _clock(fixture.payload)
    contact_id = uuid4()
    disposition = evaluate_opt_out(
        "đừng nhắn quảng cáo kiểu này nữa nhé",
        registry=OptOutRegistry(
            version="synthetic-opt-out-v1",
            exact_commands=("STOP", "DỪNG"),
            ambiguous_patterns=(r"đừng\s+nhắn\s+quảng\s+cáo\s+kiểu\s+này\s+nữa\s+nhé",),
            published_at=timestamp - timedelta(days=1),
            expires_at=timestamp + timedelta(days=1),
        ),
        now=timestamp,
    )
    result = InboxRepository().record(
        connection,
        InboundWebhook(
            provider="SYNTHETIC_PROVIDER",
            channel_account_id=f"synthetic-{uuid4().hex}",
            provider_event_id="synthetic-ambiguous-optout-001",
            event_type="MESSAGE",
            channel="INTERNAL_TEST",
            payload=EncryptedInboundPayload.from_ciphertext_and_plaintext(
                ciphertext=b"synthetic-sealed-ambiguous-optout",
                authenticated_plaintext=b"synthetic-ambiguous-optout",
            ),
            opt_out_disposition=disposition,
            contact_binding_id=contact_id,
            opt_out_registry_version="synthetic-opt-out-v1",
            correlation_id=uuid4(),
            received_at=timestamp,
        ),
    )
    with connection.cursor() as cursor:
        suppression = InboxRepository.marketing_suppression(
            cursor, contact_binding_id=contact_id, channel="INTERNAL_TEST"
        )
        cursor.execute(
            """
            SELECT count(*) FROM consent_events
            WHERE contact_binding_id = %s AND event_type = 'PENDING_REVIEW_BLOCK'
            """,
            (contact_id,),
        )
        review_events = int(cursor.fetchone()[0])
    return SyntheticAmbiguousOptOutPreflight(
        marketing_blocked_immediately=(
            result.processing_status == "SAFETY_BLOCKED"
            and suppression is SuppressionState.PENDING_REVIEW_BLOCKED
        ),
        human_consent_review_opened=review_events == 1,
        trace_id="synthetic-ambiguous-optout-001",
    )


def _clock(payload: Mapping[str, Any]) -> datetime:
    value = payload.get("clock")
    if not isinstance(value, str):
        raise ValueError("STOP-race fixture clock is invalid")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("STOP-race fixture clock is invalid")
    return timestamp.astimezone(UTC)


__all__ = [
    "SyntheticAmbiguousOptOutPreflight",
    "SyntheticStopOutboxRacePreflight",
    "execute_ambiguous_opt_out_preflight",
    "execute_stop_outbox_race_preflight",
]
