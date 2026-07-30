from pathlib import Path

import pytest
from nha_trang_laundry_db.migrations import discover_migrations


def test_discovers_forward_only_transaction_foundation() -> None:
    migrations = discover_migrations()

    assert [(migration.version, migration.name) for migration in migrations] == [
        ("0001", "transaction_foundation"),
        ("0002", "configuration_publication"),
        ("0003", "staff_identity"),
        ("0004", "staff_session_idle_timeout"),
        ("0005", "quote_snapshots"),
        ("0006", "quote_snapshot_constraints"),
        ("0007", "operations_control"),
        ("0008", "operations_constraints"),
        ("0009", "agent_run_ledger"),
        ("0010", "agent_run_binding"),
        ("0011", "manual_send_integrity"),
        ("0012", "automation_execution_gates"),
        ("0013", "quote_acknowledgment_evidence"),
        ("0014", "customer_incidents"),
        ("0015", "order_request_drafts"),
    ]
    assert all(len(migration.checksum) == 64 for migration in migrations)


def test_rejects_invalid_migration_filename(tmp_path: Path) -> None:
    (tmp_path / "not_a_migration.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid migration filename"):
        discover_migrations(tmp_path)
