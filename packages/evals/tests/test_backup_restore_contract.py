from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from nha_trang_laundry_db.recovery import (
    RecoveryValidationError,
    load_backup_policy,
    parse_restore_drill,
)

ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = ROOT / "deploy/backup/backup-policy-v1.json"


def _valid_evidence() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "environment": "STAGING",
        "primary_failure_domain": "primary-host-a",
        "recovery_failure_domain": "recovery-host-b",
        "encrypted_off_host_copy_verified": True,
        "object_evidence_verified": True,
        "reviewer_attested": True,
        "remediation_closed": True,
        "incident_started_at": "2026-08-02T01:00:00Z",
        "source_latest_commit_at": "2026-08-02T01:10:00Z",
        "selected_recovery_point_at": "2026-08-02T01:00:00Z",
        "restore_completed_at": "2026-08-02T03:00:00Z",
        "quote_id": "00000000-0000-0000-0000-000000000901",
        "quote_revision": 1,
        "published_config_id": "00000000-0000-0000-0000-000000000902",
        "correlation_id": "00000000-0000-0000-0000-000000000903",
    }


def test_backup_policy_requires_continuous_encrypted_off_host_recovery() -> None:
    policy = load_backup_policy(POLICY_PATH)
    raw = POLICY_PATH.read_text(encoding="utf-8")

    assert policy.target_rpo_seconds == 900
    assert policy.target_rto_seconds == 14_400
    assert policy.wal_archive_max_interval_seconds <= policy.target_rpo_seconds
    assert policy.base_backup_max_interval_seconds <= 86_400
    assert policy.daily_recovery_copy_retention_days >= 35
    assert "postgresql://" not in raw and "secret" not in json.dumps(json.loads(raw)).casefold()


def test_restore_drill_computes_rpo_and_rto_in_deterministic_code() -> None:
    policy = load_backup_policy(POLICY_PATH)

    evidence = parse_restore_drill(_valid_evidence(), policy)

    assert evidence.achieved_rpo_seconds == 600
    assert evidence.achieved_rto_seconds == 7200


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selected_recovery_point_at", "2026-08-02T00:54:59Z"),
        ("restore_completed_at", "2026-08-02T05:00:01Z"),
        ("recovery_failure_domain", "primary-host-a"),
        ("encrypted_off_host_copy_verified", False),
        ("object_evidence_verified", False),
        ("reviewer_attested", False),
    ],
)
def test_restore_drill_fails_closed_without_real_recovery_evidence(
    field: str, value: object
) -> None:
    document = deepcopy(_valid_evidence())
    document[field] = value

    with pytest.raises(RecoveryValidationError):
        parse_restore_drill(document, load_backup_policy(POLICY_PATH))


def test_restore_validator_is_read_only_and_checks_duplicate_delivery() -> None:
    recovery = (ROOT / "packages/db/src/nha_trang_laundry_db/recovery.py").read_text(
        encoding="utf-8"
    )
    validator = (ROOT / "scripts/validate_restore_drill.py").read_text(encoding="utf-8")

    assert "SET TRANSACTION READ ONLY" in recovery
    assert "schema_migrations" in recovery
    assert "canonical_document" in recovery
    assert "delivery_attempts" in recovery and "provider_message_id" in recovery
    assert "outbox_events WHERE status = 'PROCESSING'" in recovery
    assert "INSERT " not in recovery and "UPDATE " not in recovery and "DELETE " not in recovery
    assert "no release authority was granted" in validator
