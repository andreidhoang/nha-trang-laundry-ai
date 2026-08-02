"""Read-only, deterministic validation of backup policy and restored PostgreSQL state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from nha_trang_laundry_domain.canonical import canonical_document

from .migrations import discover_migrations


class RecoveryValidationError(ValueError):
    """Raised when recovery policy, evidence, or restored state fails closed."""


@dataclass(frozen=True, slots=True)
class BackupPolicy:
    target_rpo_seconds: int
    target_rto_seconds: int
    wal_archive_max_interval_seconds: int
    base_backup_max_interval_seconds: int
    daily_recovery_copy_retention_days: int


@dataclass(frozen=True, slots=True)
class RestoreDrillEvidence:
    incident_started_at: datetime
    source_latest_commit_at: datetime
    selected_recovery_point_at: datetime
    restore_completed_at: datetime
    quote_id: UUID
    quote_revision: int
    published_config_id: UUID
    correlation_id: UUID

    @property
    def achieved_rpo_seconds(self) -> int:
        return int((self.source_latest_commit_at - self.selected_recovery_point_at).total_seconds())

    @property
    def achieved_rto_seconds(self) -> int:
        return int((self.restore_completed_at - self.incident_started_at).total_seconds())


@dataclass(frozen=True, slots=True)
class RestoreIntegrityReport:
    migrations_verified: int
    quote_snapshot_verified: bool
    published_config_verified: bool
    audit_timeline_verified: bool
    duplicate_send_keys: int
    duplicate_provider_receipts: int
    unresolved_processing_items: int


def load_backup_policy(path: Path) -> BackupPolicy:
    document = _object(json.loads(path.read_text(encoding="utf-8")), "backup policy")
    required_true = (
        "continuous_wal_archiving",
        "off_host",
        "separate_failure_domain",
        "object_versioning",
    )
    if any(document.get(field) is not True for field in required_true):
        raise RecoveryValidationError("backup policy is not continuous and off-host")
    if document.get("encryption_at_rest") != "AES-256":
        raise RecoveryValidationError("backup at-rest encryption is insufficient")
    if document.get("encryption_in_transit") != "TLS1.2+":
        raise RecoveryValidationError("backup transport encryption is insufficient")
    for field in ("credential_reference", "encryption_key_reference"):
        value = document.get(field)
        if (
            not isinstance(value, str)
            or not value
            or any(character.isspace() for character in value)
        ):
            raise RecoveryValidationError("backup secret reference is invalid")
    policy = BackupPolicy(
        target_rpo_seconds=_positive_int(document, "target_rpo_seconds"),
        target_rto_seconds=_positive_int(document, "target_rto_seconds"),
        wal_archive_max_interval_seconds=_positive_int(
            document, "wal_archive_max_interval_seconds"
        ),
        base_backup_max_interval_seconds=_positive_int(
            document, "base_backup_max_interval_seconds"
        ),
        daily_recovery_copy_retention_days=_positive_int(
            document, "daily_recovery_copy_retention_days"
        ),
    )
    if (
        policy.target_rpo_seconds > 900
        or policy.target_rto_seconds > 14_400
        or policy.wal_archive_max_interval_seconds > 900
        or policy.base_backup_max_interval_seconds > 86_400
        or policy.daily_recovery_copy_retention_days < 35
    ):
        raise RecoveryValidationError("backup policy violates RPO, RTO, or retention requirements")
    return policy


def parse_restore_drill(document: dict[str, Any], policy: BackupPolicy) -> RestoreDrillEvidence:
    if document.get("schema_version") != 1 or document.get("environment") != "STAGING":
        raise RecoveryValidationError("restore evidence schema or environment is invalid")
    for field in (
        "encrypted_off_host_copy_verified",
        "object_evidence_verified",
        "reviewer_attested",
        "remediation_closed",
    ):
        if document.get(field) is not True:
            raise RecoveryValidationError(f"restore evidence did not pass {field}")
    primary = document.get("primary_failure_domain")
    recovery = document.get("recovery_failure_domain")
    if not isinstance(primary, str) or not isinstance(recovery, str) or primary == recovery:
        raise RecoveryValidationError("restore did not use a separate failure domain")
    evidence = RestoreDrillEvidence(
        incident_started_at=_timestamp(document, "incident_started_at"),
        source_latest_commit_at=_timestamp(document, "source_latest_commit_at"),
        selected_recovery_point_at=_timestamp(document, "selected_recovery_point_at"),
        restore_completed_at=_timestamp(document, "restore_completed_at"),
        quote_id=_uuid(document, "quote_id"),
        quote_revision=_positive_int(document, "quote_revision"),
        published_config_id=_uuid(document, "published_config_id"),
        correlation_id=_uuid(document, "correlation_id"),
    )
    if (
        evidence.achieved_rpo_seconds < 0
        or evidence.achieved_rpo_seconds > policy.target_rpo_seconds
        or evidence.achieved_rto_seconds < 0
        or evidence.achieved_rto_seconds > policy.target_rto_seconds
    ):
        raise RecoveryValidationError("restore drill missed the required RPO or RTO")
    return evidence


def validate_restored_database(
    connection: Any, evidence: RestoreDrillEvidence
) -> RestoreIntegrityReport:
    """Validate a restored database without mutation or provider delivery."""

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SELECT version, checksum_sha256 FROM schema_migrations ORDER BY version")
        stored_migrations = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
        expected_migrations = {item.version: item.checksum for item in discover_migrations()}
        if stored_migrations != expected_migrations:
            raise RecoveryValidationError("restored migration ledger is incomplete or changed")

        cursor.execute(
            """
            SELECT snapshot, snapshot_hash FROM quote_revisions
            WHERE quote_id = %s AND revision = %s
            """,
            (evidence.quote_id, evidence.quote_revision),
        )
        quote = cursor.fetchone()
        if quote is None or not isinstance(quote[0], dict):
            raise RecoveryValidationError("historical quote is unavailable")
        if canonical_document(quote[0]).snapshot_hash != str(quote[1]):
            raise RecoveryValidationError("historical quote hash does not reconstruct")

        cursor.execute(
            """
            SELECT 1 FROM configuration_versions
            WHERE id = %s AND lifecycle IN ('PUBLISHED','RETIRED')
            """,
            (evidence.published_config_id,),
        )
        published_config_verified = cursor.fetchone() == (1,)
        cursor.execute(
            """
            SELECT
                (SELECT count(*) FROM domain_events WHERE correlation_id = %s),
                (SELECT count(*) FROM audit_events WHERE correlation_id = %s)
            """,
            (evidence.correlation_id, evidence.correlation_id),
        )
        timeline = cursor.fetchone()
        audit_timeline_verified = (
            timeline is not None and int(timeline[0]) > 0 and int(timeline[1]) > 0
        )
        cursor.execute(
            """
            SELECT count(*) FROM (
                SELECT idempotency_key FROM outbox_events
                GROUP BY idempotency_key HAVING count(*) > 1
            ) AS duplicates
            """
        )
        duplicate_send_keys = int(cursor.fetchone()[0])
        cursor.execute(
            """
            SELECT count(*) FROM (
                SELECT provider_message_id FROM delivery_attempts
                WHERE provider_message_id IS NOT NULL
                GROUP BY provider_message_id HAVING count(*) > 1
            ) AS duplicates
            """
        )
        duplicate_provider_receipts = int(cursor.fetchone()[0])
        cursor.execute("SELECT count(*) FROM outbox_events WHERE status = 'PROCESSING'")
        unresolved_processing_items = int(cursor.fetchone()[0])
    if (
        not published_config_verified
        or not audit_timeline_verified
        or duplicate_send_keys
        or duplicate_provider_receipts
        or unresolved_processing_items
    ):
        raise RecoveryValidationError("restored rules, audit, or outbox reconciliation failed")
    return RestoreIntegrityReport(
        migrations_verified=len(stored_migrations),
        quote_snapshot_verified=True,
        published_config_verified=True,
        audit_timeline_verified=True,
        duplicate_send_keys=0,
        duplicate_provider_receipts=0,
        unresolved_processing_items=0,
    )


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RecoveryValidationError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _positive_int(document: dict[str, Any], field: str) -> int:
    value = document.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RecoveryValidationError(f"{field} must be a positive integer")
    return value


def _timestamp(document: dict[str, Any], field: str) -> datetime:
    value = document.get(field)
    if not isinstance(value, str):
        raise RecoveryValidationError(f"{field} must be an RFC3339 timestamp")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RecoveryValidationError(f"{field} must be an RFC3339 timestamp") from error
    if timestamp.tzinfo is None:
        raise RecoveryValidationError(f"{field} must include a timezone")
    return timestamp


def _uuid(document: dict[str, Any], field: str) -> UUID:
    value = document.get(field)
    try:
        return UUID(str(value))
    except ValueError as error:
        raise RecoveryValidationError(f"{field} must be a UUID") from error
