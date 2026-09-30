"""`OPS-OBSERVABILITY-009` (review P3): the restore validator passes a real shop's ledger.

The first real drill (round 9) restored a database the API had written during a daily walk and
`scripts/validate_restore_drill.py` refused it: "restored rules, audit, or outbox reconciliation
failed". Not a restore defect -- the same query refuses the live database it was restored from.
`CONTIGUOUSLY_VERSIONED_AGGREGATES` listed `ORDER`, and the order's writers stopped writing
`n + 1` ORDER events when `PAYMENT-001` and the delivery legs arrived: recording a payment or a leg
bumps `orders.row_version` and writes its event under `ORDER_PAYMENT` / `DELIVERY_LEG`. So every
order that ever took a payment is a "gap", and no shop could pass a drill -- the failure the list's
own docstring warns about ("a check that cannot be passed is not strict, it is ignored").

`ORDER` leaves the contiguity list, as that docstring prescribes, and gets the check its writers do
promise: no ORDER event claims a version beyond its order row, and none exists without its row.
These tests build the ledger through the real repositories.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.configurations import ConfigurationDraft, ConfigurationRepository
from nha_trang_laundry_db.configurations import snapshot_hash as config_hash
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.recovery import (
    RecoveryValidationError,
    RestoreDrillEvidence,
    validate_restored_database,
)
from nha_trang_laundry_domain.order_steps import OrderStep
from test_order_payments import _pay, _received, _to_ready
from test_order_step_repository import TOTAL_VND, _read, _step


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _published_config(connection: Any) -> UUID:
    config_id = uuid4()
    config_type = f"DRILL_{uuid4().hex.upper()}"
    repository = ConfigurationRepository({config_type: lambda _candidate: None})
    payload: dict[str, object] = {"services": []}
    staff_id = UUID("00000000-0000-0000-0000-000000000011")
    repository.create_draft(
        connection,
        ConfigurationDraft(config_type, 1, payload, staff_id, config_id=config_id),
        correlation_id=uuid4(),
    )
    repository.publish(
        connection,
        config_id=config_id,
        version=1,
        snapshot_hash_value=config_hash(payload),
        published_by=staff_id,
        correlation_id=uuid4(),
    )
    connection.commit()
    return config_id


def _evidence(connection: Any, order_id: UUID, config_id: UUID) -> RestoreDrillEvidence:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_quote_id, current_quote_revision FROM orders WHERE id = %s",
            (order_id,),
        )
        quote_id, revision = cursor.fetchone()
        cursor.execute(
            """
            SELECT correlation_id FROM domain_events
             WHERE aggregate_type = 'ORDER' AND aggregate_id = %s AND aggregate_version = 1
            """,
            (order_id,),
        )
        (correlation_id,) = cursor.fetchone()
        cursor.execute("SELECT max(occurred_at) FROM domain_events")
        (latest,) = cursor.fetchone()
    return RestoreDrillEvidence(
        incident_started_at=latest + timedelta(minutes=1),
        source_latest_commit_at=latest,
        selected_recovery_point_at=latest,
        restore_completed_at=latest + timedelta(minutes=30),
        quote_id=quote_id,
        quote_revision=int(revision),
        published_config_id=config_id,
        correlation_id=correlation_id,
    )


def _order_versions(connection: Any, order_id: UUID) -> list[int]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT aggregate_version FROM domain_events
             WHERE aggregate_type = 'ORDER' AND aggregate_id = %s ORDER BY aggregate_version
            """,
            (order_id,),
        )
        return [int(row[0]) for row in cursor.fetchall()]


def test_an_order_that_took_a_payment_passes_the_drill(connection: Any) -> None:
    config_id = _published_config(connection)
    _, staff, order_id = _received(connection)
    _to_ready(connection, order_id, staff)
    _pay(connection, order_id, staff, TOTAL_VND, collected=True)
    connection.commit()
    # Goods leave after the payment: an ORDER event lands on the far side of the skipped version.
    view = _read(connection, order_id, staff)
    _step(connection, order_id, staff, view.row_version, OrderStep.HAND_OVER)
    connection.commit()

    versions = _order_versions(connection, order_id)
    # The shape the drill met: the payment bumped the order without an ORDER event.
    assert max(versions) == len(set(versions)) + 1, versions

    report = validate_restored_database(connection, _evidence(connection, order_id, config_id))
    assert report.audit_timeline_verified is True


def test_an_order_paid_by_a_deposit_then_the_rest_passes_the_drill(connection: Any) -> None:
    """Two payments, two skipped versions, and a transition in between."""

    config_id = _published_config(connection)
    _, staff, order_id = _received(connection)
    _pay(connection, order_id, staff, 50_000)
    connection.commit()
    _to_ready(connection, order_id, staff)
    _pay(connection, order_id, staff, TOTAL_VND - 50_000)
    connection.commit()

    versions = _order_versions(connection, order_id)
    assert max(versions) - len(set(versions)) >= 1, versions

    report = validate_restored_database(connection, _evidence(connection, order_id, config_id))
    assert report.audit_timeline_verified is True


def test_an_order_with_only_transitions_still_passes(connection: Any) -> None:
    config_id = _published_config(connection)
    _, staff, order_id = _received(connection)
    _to_ready(connection, order_id, staff)
    connection.commit()
    assert _order_versions(connection, order_id) == list(
        range(1, len(_order_versions(connection, order_id)) + 1)
    )
    validate_restored_database(connection, _evidence(connection, order_id, config_id))


def _raw_event(connection: Any, aggregate_type: str, aggregate_id: UUID, version: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO domain_events (
                id, aggregate_type, aggregate_id, aggregate_version, event_type, payload,
                correlation_id, occurred_at
            ) VALUES (%s, %s, %s, %s, 'DRILL_PROBE', '{}'::jsonb, %s, %s)
            """,
            (uuid4(), aggregate_type, aggregate_id, version, uuid4(), datetime.now(UTC)),
        )
    connection.commit()


def test_a_lost_event_in_a_contiguous_aggregate_is_still_caught(connection: Any) -> None:
    config_id = _published_config(connection)
    _, _staff, order_id = _received(connection)
    connection.commit()
    evidence = _evidence(connection, order_id, config_id)
    approval_id = uuid4()
    _raw_event(connection, "APPROVAL", approval_id, 1)
    _raw_event(connection, "APPROVAL", approval_id, 3)

    with pytest.raises(RecoveryValidationError):
        validate_restored_database(connection, evidence)


def test_an_order_event_ahead_of_its_row_is_caught(connection: Any) -> None:
    """What the ORDER check promises instead: the ledger never runs ahead of the row."""

    config_id = _published_config(connection)
    _, _staff, order_id = _received(connection)
    connection.commit()
    evidence = _evidence(connection, order_id, config_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT row_version FROM orders WHERE id = %s", (order_id,))
        (row_version,) = cursor.fetchone()
    _raw_event(connection, "ORDER", order_id, int(row_version) + 1)

    with pytest.raises(RecoveryValidationError):
        validate_restored_database(connection, evidence)


def test_an_order_event_without_its_row_is_caught(connection: Any) -> None:
    config_id = _published_config(connection)
    _, _staff, order_id = _received(connection)
    connection.commit()
    evidence = _evidence(connection, order_id, config_id)
    _raw_event(connection, "ORDER", uuid4(), 1)

    with pytest.raises(RecoveryValidationError):
        validate_restored_database(connection, evidence)
