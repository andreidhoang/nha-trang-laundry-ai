"""Durable encrypted inbox, replay defense, and synchronous opt-out projection."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.consent import OptOutDisposition, SuppressionState

from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

RAW_HASH_PATTERN = re.compile(r"^RAW-SHA256-V1:[0-9a-f]{64}$")


class InboxOutcome(StrEnum):
    CREATED = "CREATED"
    DUPLICATE = "DUPLICATE"


class InboxReplayConflictError(ValueError):
    """Raised after a conflicting provider event is durably security-audited."""


class InboxStateError(ValueError):
    """Raised when trusted adapter input cannot be safely persisted."""


@dataclass(frozen=True)
class EncryptedInboundPayload:
    ciphertext: bytes
    plaintext_hash: str

    @classmethod
    def from_ciphertext_and_plaintext(
        cls, *, ciphertext: bytes, authenticated_plaintext: bytes
    ) -> EncryptedInboundPayload:
        return cls(ciphertext, raw_payload_hash(authenticated_plaintext))


@dataclass(frozen=True)
class InboundWebhook:
    provider: str
    channel_account_id: str
    provider_event_id: str
    event_type: str
    channel: str
    payload: EncryptedInboundPayload
    opt_out_disposition: OptOutDisposition
    contact_binding_id: UUID | None
    opt_out_registry_version: str | None
    correlation_id: UUID
    received_at: datetime | None = None


@dataclass(frozen=True)
class InboxResult:
    webhook_event_id: UUID
    outcome: InboxOutcome
    processing_status: str


class InboxRepository:
    """Persist before dispatch and reject provider-ID payload substitution."""

    def record(self, connection: Any, command: InboundWebhook) -> InboxResult:
        _validate_command(command)
        received_at = command.received_at or datetime.now(UTC)
        replay_conflict = False
        result: InboxResult | None = None
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, payload_hash, processing_status
                FROM webhook_events
                WHERE provider = %s AND channel_account_id = %s AND provider_event_id = %s
                FOR SHARE
                """,
                (command.provider, command.channel_account_id, command.provider_event_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                existing_id = _uuid(existing[0])
                if hmac.compare_digest(str(existing[1]), command.payload.plaintext_hash):
                    result = InboxResult(existing_id, InboxOutcome.DUPLICATE, str(existing[2]))
                else:
                    _record_replay_conflict(
                        connection,
                        existing_id=existing_id,
                        expected_hash=str(existing[1]),
                        observed_hash=command.payload.plaintext_hash,
                        correlation_id=command.correlation_id,
                        detected_at=received_at,
                    )
                    replay_conflict = True
            else:
                event_id = uuid4()
                safety_blocked = command.opt_out_disposition is not OptOutDisposition.NONE
                processing_status = "SAFETY_BLOCKED" if safety_blocked else "DISPATCH_PENDING"
                consent_event_id = uuid4() if _creates_suppression(command) else None

                def mutation(change_cursor: Any) -> None:
                    change_cursor.execute(
                        """
                        INSERT INTO webhook_events (
                            id, provider, channel_account_id, provider_event_id, payload_hash,
                            encrypted_payload, event_type, contact_binding_id, channel,
                            opt_out_disposition, opt_out_registry_version, processing_status,
                            received_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            event_id,
                            command.provider,
                            command.channel_account_id,
                            command.provider_event_id,
                            command.payload.plaintext_hash,
                            command.payload.ciphertext,
                            command.event_type,
                            command.contact_binding_id,
                            command.channel,
                            command.opt_out_disposition.value,
                            command.opt_out_registry_version,
                            processing_status,
                            received_at,
                        ),
                    )
                    if consent_event_id is not None:
                        _insert_suppression(
                            change_cursor,
                            consent_event_id=consent_event_id,
                            webhook_event_id=event_id,
                            command=command,
                            occurred_at=received_at,
                        )

                outbox_type = (
                    "consent.changed.v1" if consent_event_id else "inbox.dispatch_requested.v1"
                )
                commit_material_change(
                    connection,
                    MaterialChange(
                        aggregate_type="WEBHOOK_EVENT",
                        aggregate_id=event_id,
                        aggregate_version=1,
                        event_type="INBOUND_WEBHOOK_RECORDED",
                        event_payload={
                            "provider": command.provider,
                            "provider_event_id": command.provider_event_id,
                            "payload_hash": command.payload.plaintext_hash,
                            "opt_out_disposition": command.opt_out_disposition.value,
                        },
                        audit_action="INBOUND_WEBHOOK_RECORD",
                        actor_type="PROVIDER",
                        actor_id=None,
                        correlation_id=command.correlation_id,
                        outbox_events=(
                            OutboxEvent(
                                outbox_type,
                                {
                                    "webhook_event_id": str(event_id),
                                    "processing_status": processing_status,
                                },
                                f"webhook:{command.provider}:{command.channel_account_id}:"
                                f"{command.provider_event_id}",
                            ),
                        ),
                        occurred_at=received_at,
                    ),
                    mutation,
                )
                result = InboxResult(event_id, InboxOutcome.CREATED, processing_status)
        if replay_conflict:
            raise InboxReplayConflictError("provider event ID was reused with a different payload")
        if result is None:
            raise InboxStateError("inbox record did not complete")
        return result

    @staticmethod
    def marketing_suppression(
        cursor: Any, *, contact_binding_id: UUID, channel: str
    ) -> SuppressionState:
        cursor.execute(
            """
            SELECT state
            FROM suppression_entries
            WHERE contact_binding_id = %s AND purpose = 'MARKETING' AND channel = %s
            """,
            (contact_binding_id, channel),
        )
        row = cursor.fetchone()
        if row is None:
            return SuppressionState.UNKNOWN_BLOCKED
        return SuppressionState(str(row[0]))


def raw_payload_hash(authenticated_plaintext: bytes) -> str:
    """Hash authenticated raw bytes without logging or persisting their plaintext."""
    return f"RAW-SHA256-V1:{sha256(authenticated_plaintext).hexdigest()}"


def _validate_command(command: InboundWebhook) -> None:
    texts = (
        command.provider,
        command.channel_account_id,
        command.provider_event_id,
        command.event_type,
        command.channel,
    )
    if any(not text.strip() for text in texts):
        raise InboxStateError("inbox identity fields are required")
    if not command.payload.ciphertext or not RAW_HASH_PATTERN.fullmatch(
        command.payload.plaintext_hash
    ):
        raise InboxStateError("encrypted inbox payload is invalid")
    if command.received_at is not None and command.received_at.tzinfo is None:
        raise InboxStateError("received_at must be timezone-aware")
    if _creates_suppression(command) and command.contact_binding_id is None:
        raise InboxStateError("opt-out requires a server-bound contact")
    if (
        command.opt_out_disposition
        in {OptOutDisposition.WITHDRAW, OptOutDisposition.PENDING_REVIEW_BLOCKED}
        and not (command.opt_out_registry_version or "").strip()
    ):
        raise InboxStateError("opt-out requires its published registry version")


def _creates_suppression(command: InboundWebhook) -> bool:
    return command.opt_out_disposition in {
        OptOutDisposition.WITHDRAW,
        OptOutDisposition.PENDING_REVIEW_BLOCKED,
    }


def _insert_suppression(
    cursor: Any,
    *,
    consent_event_id: UUID,
    webhook_event_id: UUID,
    command: InboundWebhook,
    occurred_at: datetime,
) -> None:
    if command.contact_binding_id is None:
        raise InboxStateError("suppression contact binding is missing")
    event_type = (
        "WITHDRAW"
        if command.opt_out_disposition is OptOutDisposition.WITHDRAW
        else "PENDING_REVIEW_BLOCK"
    )
    state = (
        SuppressionState.SUPPRESSED
        if command.opt_out_disposition is OptOutDisposition.WITHDRAW
        else SuppressionState.PENDING_REVIEW_BLOCKED
    )
    cursor.execute(
        """
        INSERT INTO consent_events (
            id, contact_binding_id, purpose, channel, event_type, registry_version,
            evidence_webhook_id, occurred_at
        ) VALUES (%s, %s, 'MARKETING', %s, %s, %s, %s, %s)
        """,
        (
            consent_event_id,
            command.contact_binding_id,
            command.channel,
            event_type,
            command.opt_out_registry_version,
            webhook_event_id,
            occurred_at,
        ),
    )
    cursor.execute(
        """
        INSERT INTO suppression_entries (
            contact_binding_id, purpose, channel, state, source_consent_event_id,
            row_version, updated_at
        ) VALUES (%s, 'MARKETING', %s, %s, %s, 1, %s)
        ON CONFLICT (contact_binding_id, purpose, channel) DO UPDATE
        SET state = CASE
                WHEN suppression_entries.state = 'SUPPRESSED' THEN 'SUPPRESSED'
                WHEN EXCLUDED.state = 'SUPPRESSED' THEN 'SUPPRESSED'
                ELSE 'PENDING_REVIEW_BLOCKED'
            END,
            source_consent_event_id = EXCLUDED.source_consent_event_id,
            row_version = suppression_entries.row_version + 1,
            updated_at = EXCLUDED.updated_at
        """,
        (
            command.contact_binding_id,
            command.channel,
            state.value,
            consent_event_id,
            occurred_at,
        ),
    )


def _record_replay_conflict(
    connection: Any,
    *,
    existing_id: UUID,
    expected_hash: str,
    observed_hash: str,
    correlation_id: UUID,
    detected_at: datetime,
) -> None:
    conflict_id = uuid4()

    def mutation(cursor: Any) -> None:
        cursor.execute(
            """
            INSERT INTO inbox_replay_conflicts (
                id, webhook_event_id, expected_payload_hash, observed_payload_hash, detected_at
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (conflict_id, existing_id, expected_hash, observed_hash, detected_at),
        )

    commit_material_change(
        connection,
        MaterialChange(
            aggregate_type="INBOX_REPLAY_CONFLICT",
            aggregate_id=conflict_id,
            aggregate_version=1,
            event_type="INBOX_REPLAY_CONFLICT_DETECTED",
            event_payload={
                "webhook_event_id": str(existing_id),
                "expected_payload_hash": expected_hash,
                "observed_payload_hash": observed_hash,
            },
            audit_action="INBOX_REPLAY_CONFLICT",
            actor_type="PROVIDER",
            actor_id=None,
            correlation_id=correlation_id,
            outbox_events=(
                OutboxEvent(
                    "security.inbox_replay_conflict.v1",
                    {"conflict_id": str(conflict_id), "webhook_event_id": str(existing_id)},
                    f"inbox-replay-conflict:{conflict_id}",
                ),
            ),
            occurred_at=detected_at,
        ),
        mutation,
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
