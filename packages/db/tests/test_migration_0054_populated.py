"""`0054` applied to a database that already holds a one-day export and its pending envelope.

`test_migration_populated.py` explains why this shape of test exists. `0054` adds a nullable
`export_requests.business_date_to` and a CHECK on the window's shape, and the question that matters
is what happens to what was written before a window could exist: a one-day export request whose
owner envelope was raised under the old code and not yet decided when the new code is deployed.

The answer this pins: nothing about it moves. The row keeps its NULL last day (the append-only
trigger forbids a backfill and none is attempted), its digests re-derive to exactly the pair the old
code stored, a different owner can still approve the envelope that was raised before the migration,
and the file releases once, cut on the same day, with the one-day query version.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
)
from nha_trang_laundry_db.exports import (
    BUSINESS_TIMEZONE,
    EXPORT_POLICY_VERSION,
    EXPORT_QUERY,
    ExportExecutionCommand,
    SanitizedExportRepository,
    _facts,
    _statement,
)
from nha_trang_laundry_db.migrations import MIGRATIONS_DIRECTORY, apply_migrations
from nha_trang_laundry_domain.approvals import APPROVAL_RESOURCE_TYPES
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import ApprovalAction
from psycopg import sql
from psycopg.conninfo import make_conninfo
from test_sanitized_export import _order_with_complaint, _Shop

MIGRATION_UNDER_TEST = "0054"


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def scratch_database() -> Generator[str, None, None]:
    configured = _database_url()
    maintenance = make_conninfo(configured, dbname="postgres")
    name = f"ntl_migration_{uuid4().hex[:12]}"
    with psycopg.connect(maintenance, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(configured, dbname=name)
    finally:
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def migrations_before(tmp_path: Path) -> Path:
    directory = tmp_path / "migrations"
    directory.mkdir()
    for path in sorted(MIGRATIONS_DIRECTORY.glob("*.sql")):
        if path.name[:4] < MIGRATION_UNDER_TEST:
            shutil.copy2(path, directory / path.name)
    return directory


def test_a_one_day_export_and_its_envelope_from_before_0054_still_release(
    scratch_database: str, migrations_before: Path
) -> None:
    with psycopg.connect(scratch_database) as connection:
        apply_migrations(connection, migrations_before)
        shop = _Shop(connection, datetime.now(UTC))
        order_id = _order_with_complaint(connection, shop)
        day: date = shop.now.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()

        # The request exactly as the pre-window `SanitizedExportRepository.request` wrote it: no
        # last day (the column does not exist yet), and the one-day digests -- which
        # `test_export_range.py` pins against the pre-window module's own output.
        facts = _facts("STORE_DAY_ORDERS_V1", shop.store_id, day, None)
        snapshot = canonical_document(facts).snapshot_hash
        rendered = canonical_document(_statement(facts)).snapshot_hash
        request_id = uuid4()
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO export_requests (
                    id, store_id, dataset, business_date, row_version,
                    snapshot_hash, rendered_hash, requested_by_staff_id, requested_at
                ) VALUES (%s, %s, 'STORE_DAY_ORDERS_V1', %s, 1, %s, %s, %s, %s)
                """,
                (
                    request_id,
                    shop.store_id,
                    day,
                    snapshot,
                    rendered,
                    shop.requester.staff_user_id,
                    shop.now,
                ),
            )
        # And its envelope, raised before the migration.
        envelope = ApprovalRepository().request(
            connection,
            ApprovalRequestCommand(
                ApprovalAction.EXPORT_SANITIZED_DATA,
                APPROVAL_RESOURCE_TYPES[ApprovalAction.EXPORT_SANITIZED_DATA],
                request_id,
                1,
                snapshot,
                rendered,
                EXPORT_POLICY_VERSION,
                shop.requester.staff_user_id,
                f"approval-{uuid4().hex}",
                uuid4(),
                shop.now,
                store_id=shop.store_id,
            ),
        )

        applied = apply_migrations(connection)
        assert MIGRATION_UNDER_TEST in applied

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT business_date, business_date_to FROM export_requests WHERE id = %s",
                (request_id,),
            )
            assert cursor.fetchone() == (day, None)

        # A different owner decides the pre-migration envelope under the new code...
        ApprovalRepository().decide(
            connection,
            ApprovalDecisionCommand(
                approval_request_id=envelope.approval_request_id,
                decision=ApprovalDecision.APPROVED,
                observed_resource_version=1,
                observed_snapshot_hash=snapshot,
                observed_rendered_hash=rendered,
                reason_code="OWNER_APPROVED_EXPORT",
                principal=shop.owner,
                correlation_id=uuid4(),
                decided_at=shop.now,
            ),
        )
        # ...and the file releases, once, cut on the same day by the same rule.
        produced = SanitizedExportRepository().execute(
            connection,
            ExportExecutionCommand(
                export_request_id=request_id,
                approval_request_id=envelope.approval_request_id,
                principal=shop.requester,
                correlation_id=uuid4(),
            ),
        )
        assert produced.row_count == 1
        assert str(order_id) in produced.content_csv
        assert produced.query_version == EXPORT_QUERY.label
        assert (produced.business_date, produced.business_date_to) == (day, day)
