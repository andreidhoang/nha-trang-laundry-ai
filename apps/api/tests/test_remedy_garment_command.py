"""`REMEDY-GARMENT-001` through `OperationsService`: the garment is part of what a key commits to.

`propose_remedy` is idempotent on `(staff member, key)`, and the payload it hashes is what makes a
request *this* request. A claim for shirt #2 and a claim for shirt #3 are different claims -- each
spends a different garment's staff limit -- so reusing a key with another garment must conflict
rather than replay the first. A request that names no garment must hash exactly as it did before
the field existed, or a retry of a request made before the upgrade would conflict with itself.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.keyed_digest import request_digest
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import Unit
from nha_trang_laundry_domain.remedies import RemedyKind

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from quote_test_data import FixtureLine
from test_remedies import _shop

SHIRTS = FixtureLine("line-0", "DC_SHIRT", Unit.ITEM, "3", 50_000, 150_000)
DRESS = FixtureLine("line-1", "DC_EVENING_DRESS", Unit.ITEM, "1", 120_000, 120_000)


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    """Autocommit: `OperationsService` opens its own connection and sees only committed rows."""

    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def service() -> OperationsService:
    return OperationsService(AuthSettings(database_url=_database_url()))


def _propose(service: OperationsService, shop: tuple[Any, ...], key: str, **fields: Any) -> Any:
    store_id, staff, _, incident_id = shop
    arguments: dict[str, Any] = {
        "store_id": store_id,
        "incident_id": incident_id,
        "kind": RemedyKind.DAMAGE_COMPENSATION,
        "store_fault_attested": True,
        "order_line_id": "line-0",
        "amount_vnd": 40_000,
        "attested_late_by_minutes": None,
        "idempotency_key": key,
        "principal": staff,
    }
    arguments.update(fields)
    return service.propose_remedy(**arguments)


def test_the_same_key_for_the_same_garment_replays_and_for_another_conflicts(
    connection: psycopg.Connection[Any], service: OperationsService
) -> None:
    shop = _shop(connection, lines=(SHIRTS,))
    key = f"garment-{uuid4().hex}"
    first = _propose(service, shop, key, garment_index=2)
    assert (first.garment_index, first.status, first.replayed) == (2, "STAFF_AUTHORIZED", False)
    again = _propose(service, shop, key, garment_index=2)
    assert again.replayed is True
    assert (again.proposal_id, again.garment_index) == (first.proposal_id, 2)
    with pytest.raises(IdempotencyConflictError):
        _propose(service, shop, key, garment_index=3)


def test_a_request_naming_no_garment_hashes_as_it_did_before_the_field_existed(
    connection: psycopg.Connection[Any], service: OperationsService
) -> None:
    """The stored request hash of an unnamed claim is the hash of the pre-addendum payload."""

    shop = _shop(connection, lines=(DRESS,))
    store_id, staff, _, incident_id = shop
    key = f"garment-{uuid4().hex}"
    proposal = _propose(service, shop, key, order_line_id="line-1")
    assert proposal.garment_index == 1
    before = {
        "store_id": str(store_id),
        "incident_id": str(incident_id),
        "kind": "DAMAGE_COMPENSATION",
        "store_fault_attested": True,
        "order_line_id": "line-1",
        "amount_vnd": 40_000,
        "attested_late_by_minutes": None,
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT request_hash FROM command_idempotency_records
            WHERE scope = %s AND idempotency_key = %s
            """,
            (f"staff-remedy-propose:{staff.staff_user_id}", key),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == request_digest(canonical_document(before).canonical_json)
