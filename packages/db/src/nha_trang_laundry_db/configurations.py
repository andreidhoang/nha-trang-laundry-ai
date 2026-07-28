"""Versioned, immutable configuration publication primitives."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import rfc8785

from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

JsonObject = Mapping[str, Any]
ConfigValidator = Callable[[JsonObject], None]

CONFIG_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,62}$")
VOLATILE_HASH_FIELDS = frozenset({"trace_id", "request_received_at", "server_generated_at"})


class ConfigurationValidationError(ValueError):
    """Raised when a configuration is not typed, canonical, or publishable."""


class ConfigurationStateError(ValueError):
    """Raised when a configuration lifecycle transition is not allowed."""


@dataclass(frozen=True)
class ConfigurationDraft:
    """Validated draft input; the caller supplies a typed validator for its config type."""

    config_type: str
    version: int
    payload: JsonObject
    created_by: UUID
    config_id: UUID | None = None
    occurred_at: datetime | None = None


def snapshot_hash(payload: JsonObject) -> str:
    """Return the SHA-256 of RFC 8785 canonical JSON without contract-defined volatile fields."""
    try:
        canonical_payload = _without_volatile_fields(payload)
        return sha256(rfc8785.dumps(canonical_payload)).hexdigest()
    except (TypeError, ValueError) as error:
        raise ConfigurationValidationError("configuration payload is not canonical JSON") from error


def _without_volatile_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _without_volatile_fields(item)
            for key, item in value.items()
            if isinstance(key, str) and key not in VOLATILE_HASH_FIELDS
        }
    if isinstance(value, list):
        return [_without_volatile_fields(item) for item in value]
    return value


class ConfigurationRepository:
    """Persist validated configuration versions and expose published versions only."""

    def __init__(self, validators: Mapping[str, ConfigValidator]) -> None:
        self._validators = dict(validators)

    def create_draft(
        self,
        connection: Any,
        draft: ConfigurationDraft,
        *,
        correlation_id: UUID,
    ) -> UUID:
        self._validate_draft(draft)
        config_id = draft.config_id or uuid4()
        occurred_at = draft.occurred_at or datetime.now(UTC)
        content_hash = snapshot_hash(draft.payload)

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CONFIGURATION_VERSION",
                aggregate_id=config_id,
                aggregate_version=draft.version,
                event_type="CONFIGURATION_DRAFT_CREATED",
                event_payload={
                    "config_type": draft.config_type,
                    "version": draft.version,
                    "snapshot_hash": content_hash,
                },
                audit_action="CONFIGURATION_DRAFT_CREATE",
                actor_type="STAFF",
                actor_id=draft.created_by,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "configuration.draft_created.v1",
                        {"config_id": str(config_id), "snapshot_hash": content_hash},
                        f"configuration:{config_id}:draft-created",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            lambda cursor: cursor.execute(
                """
                INSERT INTO configuration_versions (
                    id, config_type, version, lifecycle, payload, snapshot_hash, created_by,
                    created_at
                ) VALUES (%s, %s, %s, 'DRAFT', %s::jsonb, %s, %s, %s)
                """,
                (
                    config_id,
                    draft.config_type,
                    draft.version,
                    rfc8785.dumps(_without_volatile_fields(draft.payload)).decode("utf-8"),
                    content_hash,
                    draft.created_by,
                    occurred_at,
                ),
            ),
        )
        return config_id

    def publish(
        self,
        connection: Any,
        *,
        config_id: UUID,
        version: int,
        snapshot_hash_value: str,
        published_by: UUID,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> None:
        if version < 1 or not re.fullmatch(r"[0-9a-f]{64}", snapshot_hash_value):
            raise ConfigurationValidationError("invalid configuration version or snapshot hash")
        published_at = occurred_at or datetime.now(UTC)

        def publish_mutation(cursor: Any) -> None:
            cursor.execute(
                """
                UPDATE configuration_versions
                SET lifecycle = 'PUBLISHED', published_by = %s, published_at = %s
                WHERE id = %s AND version = %s AND lifecycle = 'DRAFT' AND snapshot_hash = %s
                RETURNING config_type
                """,
                (published_by, published_at, config_id, version, snapshot_hash_value),
            )
            if cursor.fetchone() is None:
                raise ConfigurationStateError(
                    "configuration draft is missing, stale, or already published"
                )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CONFIGURATION_VERSION",
                aggregate_id=config_id,
                aggregate_version=version,
                event_type="CONFIGURATION_PUBLISHED",
                event_payload={"version": version, "snapshot_hash": snapshot_hash_value},
                audit_action="CONFIGURATION_PUBLISH",
                actor_type="STAFF",
                actor_id=published_by,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "configuration.published.v1",
                        {"config_id": str(config_id), "snapshot_hash": snapshot_hash_value},
                        f"configuration:{config_id}:published:{snapshot_hash_value}",
                    ),
                ),
                occurred_at=published_at,
            ),
            publish_mutation,
        )

    @staticmethod
    def get_published(cursor: Any, config_id: UUID) -> JsonObject | None:
        """Read a specific immutable published payload; drafts are intentionally invisible."""
        cursor.execute(
            """
            SELECT payload FROM configuration_versions
            WHERE id = %s AND lifecycle = 'PUBLISHED'
            """,
            (config_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        payload = row[0]
        if not isinstance(payload, Mapping):
            raise ConfigurationStateError("stored configuration payload is not an object")
        return payload

    def _validate_draft(self, draft: ConfigurationDraft) -> None:
        if not CONFIG_TYPE_PATTERN.fullmatch(draft.config_type) or draft.version < 1:
            raise ConfigurationValidationError("invalid configuration type or version")
        validator = self._validators.get(draft.config_type)
        if validator is None:
            raise ConfigurationValidationError(
                "configuration type has no registered typed validator"
            )
        validator(draft.payload)
        snapshot_hash(draft.payload)
