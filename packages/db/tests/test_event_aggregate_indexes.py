"""`OPS-OBSERVABILITY-009` (review P7): the order history reads the ledgers through an index.

`AUDIT_TIMELINE_SQL` filters `audit_events` on `aggregate_id` alone and joins each transition to its
`domain_events` row by `aggregate_id`, `correlation_id` and `occurred_at`. Before `0070` the only
index on either table led with `aggregate_type`, so both were sequential scans that grew with every
write the shop ever made. These tests fill both tables with thousands of rows inside a transaction
that is rolled back, `ANALYZE` them, and `EXPLAIN` the exact statement the repository runs.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.shadow_console import AUDIT_TIMELINE_SQL

AGGREGATES = 1_000
ROWS_PER_AGGREGATE = 5


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def filled() -> Iterator[tuple[psycopg.Connection[Any], UUID]]:
    """Thousands of ledger rows, visible only inside a transaction that is always rolled back."""

    with psycopg.connect(_database_url(), autocommit=True) as connection:
        apply_migrations(connection)
        with connection.transaction(force_rollback=True), connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMP TABLE probe_aggregates ON COMMIT DROP AS
                SELECT gen_random_uuid() AS aggregate_id, gen_random_uuid() AS correlation_id,
                       now() - make_interval(secs => n) AS at
                  FROM generate_series(1, %(aggregates)s) AS n
                """,
                {"aggregates": AGGREGATES},
            )
            cursor.execute(
                """
                INSERT INTO audit_events (
                    id, aggregate_type, aggregate_id, action, actor_type, actor_id,
                    correlation_id, details, occurred_at
                )
                SELECT gen_random_uuid(), 'ORDER', p.aggregate_id,
                       CASE WHEN v %% 2 = 0 THEN 'ORDER_STATE_TRANSITION' ELSE 'ORDER_CREATED' END,
                       'STAFF', NULL, p.correlation_id, '{}'::jsonb,
                       p.at + make_interval(secs => v)
                  FROM probe_aggregates p, generate_series(1, %(rows)s) AS v
                """,
                {"rows": ROWS_PER_AGGREGATE},
            )
            cursor.execute(
                """
                INSERT INTO domain_events (
                    id, aggregate_type, aggregate_id, aggregate_version, event_type, payload,
                    correlation_id, occurred_at
                )
                SELECT gen_random_uuid(), 'ORDER', p.aggregate_id, v, 'ORDER_STATE_TRANSITIONED',
                       jsonb_build_object('dimension', 'PRODUCTION', 'target', 'WASHING'),
                       p.correlation_id, p.at + make_interval(secs => v)
                  FROM probe_aggregates p, generate_series(1, %(rows)s) AS v
                """,
                {"rows": ROWS_PER_AGGREGATE},
            )
            cursor.execute("ANALYZE audit_events")
            cursor.execute("ANALYZE domain_events")
            cursor.execute("SELECT aggregate_id FROM probe_aggregates LIMIT 1")
            row = cursor.fetchone()
            assert row is not None
            yield connection, UUID(str(row[0]))


def _plan(connection: Any, aggregate_id: UUID) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            "EXPLAIN (ANALYZE, FORMAT JSON) " + AUDIT_TIMELINE_SQL,
            {"aggregate": aggregate_id, "store": uuid4(), "limit": 100},
        )
        row = cursor.fetchone()
    assert row is not None
    document = row[0] if not isinstance(row[0], str) else json.loads(row[0])
    return dict(document[0]["Plan"])


def _nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", ()):
        yield from _nodes(child)


def _scans(plan: dict[str, Any], relation: str) -> list[tuple[str, str | None]]:
    """Each scan of `relation` and the index it used (a bitmap heap scan's is on its child)."""

    scans: list[tuple[str, str | None]] = []
    for node in _nodes(plan):
        if node.get("Relation Name") != relation:
            continue
        index = node.get("Index Name")
        if index is None and node["Node Type"] == "Bitmap Heap Scan":
            names = {child.get("Index Name") for child in _nodes(node)} - {None}
            index = names.pop() if len(names) == 1 else None
        scans.append((str(node["Node Type"]), index))
    return scans


def test_both_indexes_exist_after_migrating() -> None:
    with psycopg.connect(_database_url(), autocommit=True) as connection:
        apply_migrations(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexname, indexdef FROM pg_indexes
                 WHERE indexname IN ('audit_events_aggregate_id_idx',
                                     'domain_events_aggregate_id_idx')
                 ORDER BY indexname
                """
            )
            rows = cursor.fetchall()
    assert [name for name, _ in rows] == [
        "audit_events_aggregate_id_idx",
        "domain_events_aggregate_id_idx",
    ]
    definitions: dict[str, str] = {str(name): str(definition) for name, definition in rows}
    assert "(aggregate_id, occurred_at, id)" in definitions["audit_events_aggregate_id_idx"]
    assert (
        "(aggregate_id, correlation_id, occurred_at)"
        in definitions["domain_events_aggregate_id_idx"]
    )


def test_the_timeline_reads_audit_events_by_aggregate_id_index(
    filled: tuple[psycopg.Connection[Any], UUID],
) -> None:
    connection, aggregate_id = filled
    scans = _scans(_plan(connection, aggregate_id), "audit_events")
    assert scans, "the plan does not read audit_events at all"
    assert all(index == "audit_events_aggregate_id_idx" for _, index in scans), scans
    assert not any(node == "Seq Scan" for node, _ in scans), scans


def test_the_transition_lateral_reads_domain_events_by_aggregate_id_index(
    filled: tuple[psycopg.Connection[Any], UUID],
) -> None:
    connection, aggregate_id = filled
    scans = _scans(_plan(connection, aggregate_id), "domain_events")
    assert scans, "the plan does not read domain_events at all"
    assert all(index == "domain_events_aggregate_id_idx" for _, index in scans), scans
    assert not any(node == "Seq Scan" for node, _ in scans), scans


def test_an_aggregate_with_no_rows_is_still_an_index_probe(
    filled: tuple[psycopg.Connection[Any], UUID],
) -> None:
    connection, _ = filled
    scans = _scans(_plan(connection, uuid4()), "audit_events")
    assert scans and not any(node == "Seq Scan" for node, _ in scans), scans
