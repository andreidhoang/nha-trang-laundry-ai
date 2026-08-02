"""Validate externally produced restore evidence and the restored database, without mutation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg
from nha_trang_laundry_db.recovery import (
    load_backup_policy,
    parse_restore_drill,
    validate_restored_database,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--database-url-file", required=True, type=Path)
    arguments = parser.parse_args()
    if not arguments.evidence.is_file() or arguments.evidence.is_symlink():
        raise RuntimeError("restore evidence must be a regular restricted file")
    if not arguments.database_url_file.is_file() or arguments.database_url_file.is_symlink():
        raise RuntimeError("restored database URL must be a regular secret file")
    document = json.loads(arguments.evidence.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("restore evidence root must be an object")
    policy = load_backup_policy(ROOT / "deploy/backup/backup-policy-v1.json")
    evidence = parse_restore_drill(document, policy)
    database_url = arguments.database_url_file.read_text(encoding="utf-8").strip()
    if not database_url or "\n" in database_url or "\r" in database_url:
        raise RuntimeError("restored database URL secret is invalid")
    with psycopg.connect(database_url) as connection:
        report = validate_restored_database(connection, evidence)
    print(
        "Restore drill verified: "
        f"RPO={evidence.achieved_rpo_seconds}s RTO={evidence.achieved_rto_seconds}s "
        f"migrations={report.migrations_verified}; no release authority was granted."
    )


if __name__ == "__main__":
    main()
