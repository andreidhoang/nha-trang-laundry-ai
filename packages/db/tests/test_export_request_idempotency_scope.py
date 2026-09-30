"""`PLATFORM-SECURITY-009` P9: one person's idempotency key can never replay another's export.

The export request's idempotency scope was the store (`export-request:{store}`), while every other
staff command in the repository is scoped to the staff member who sent it. Two members of one shop
sending the same key -- two tabs on a shared counter PC reuse a key generator, a script replays a
logged header -- meant the second person was handed the FIRST person's export request back as a
replay: their own request was never recorded, and the row they went on to raise an approval for
names someone else as the definer. That definer is exactly what separation of duty is measured
against (`approvals._RESOURCE_DEFINERS` reads `export_requests.requested_by_staff_id`).

The matrix: {same person, another person} x {same window, a different window}.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from nha_trang_laundry_db.exports import (
    ExportDataset,
    ExportRequestCommand,
    SanitizedExportRepository,
)
from nha_trang_laundry_db.idempotency import IdempotencyConflictError
from nha_trang_laundry_db.identity import StaffPrincipal
from test_sanitized_export import _local_date, _Shop, connection  # fixture

__all__ = ["connection"]


def _request(
    connection: Any, shop: _Shop, principal: StaffPrincipal, key: str, *, days_back: int = 0
) -> Any:
    return SanitizedExportRepository().request(
        connection,
        ExportRequestCommand(
            store_id=shop.store_id,
            dataset=ExportDataset.STORE_DAY_ORDERS_V1,
            business_date=_local_date(shop.now) - timedelta(days=days_back),
            principal=principal,
            correlation_id=uuid4(),
            idempotency_key=key,
            requested_at=shop.now,
        ),
    )


def _requested_by(connection: Any, export_request_id: Any) -> Any:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT requested_by_staff_id FROM export_requests WHERE id = %s",
            (export_request_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return row[0]


def test_the_same_person_resending_the_same_request_gets_their_own_request_back(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection, datetime.now(UTC))
    key = f"export-{uuid4().hex}"
    first = _request(connection, shop, shop.requester, key)
    again = _request(connection, shop, shop.requester, key)
    assert (first.replayed, again.replayed) == (False, True)
    assert again.export_request_id == first.export_request_id


def test_the_same_person_reusing_a_key_for_a_different_window_is_a_conflict(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection, datetime.now(UTC))
    key = f"export-{uuid4().hex}"
    _request(connection, shop, shop.requester, key)
    with pytest.raises(IdempotencyConflictError):
        _request(connection, shop, shop.requester, key, days_back=1)


def test_another_person_with_the_same_key_and_window_records_their_own_request(
    connection: psycopg.Connection[Any],
) -> None:
    """The case the review named: the second person must not receive the first person's row."""

    shop = _Shop(connection, datetime.now(UTC))
    key = f"export-{uuid4().hex}"
    mine = _request(connection, shop, shop.requester, key)
    theirs = _request(connection, shop, shop.owner, key)

    assert theirs.replayed is False
    assert theirs.export_request_id != mine.export_request_id
    assert _requested_by(connection, mine.export_request_id) == shop.requester.staff_user_id
    assert _requested_by(connection, theirs.export_request_id) == shop.owner.staff_user_id
    # And each person's own resend still replays their own row.
    assert _request(connection, shop, shop.owner, key).export_request_id == (
        theirs.export_request_id
    )
    assert _request(connection, shop, shop.requester, key).export_request_id == (
        mine.export_request_id
    )


def test_another_person_with_the_same_key_and_a_different_window_is_not_a_conflict(
    connection: psycopg.Connection[Any],
) -> None:
    """Their key space is their own: a colleague's earlier use of a key cannot refuse them."""

    shop = _Shop(connection, datetime.now(UTC))
    key = f"export-{uuid4().hex}"
    _request(connection, shop, shop.requester, key)
    theirs = _request(connection, shop, shop.owner, key, days_back=1)
    assert theirs.replayed is False
    assert _requested_by(connection, theirs.export_request_id) == shop.owner.staff_user_id
