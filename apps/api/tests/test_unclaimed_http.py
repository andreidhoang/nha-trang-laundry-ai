"""`UNCLAIMED-001` (`DEC-036`) over HTTP against real PostgreSQL.

The five routes at wall time: before the owner publishes, the waiting list and the contact attempts
work, no fee is charged and nothing can be waived or disposed of; after, the fee is a charge on the
order read, the approver waives it, the cash at pickup includes it, and the owner disposes of
laundry nobody came back for. Plus the refusals each route owes: roles, `If-Match`, idempotency,
another shop's staff, and a note that looks like a phone number -- which is never echoed back.

Harness step (documented): the laundry's ready time is moved back with one SQL statement (`_age`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import app, get_operations_service, get_unclaimed_service
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_api.unclaimed import UnclaimedService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.storage_fees import publish_storage_policy
from nha_trang_laundry_db.unclaimed import ContactAttemptCommand, UnclaimedRepository
from nha_trang_laundry_domain.unclaimed import (
    ContactChannel,
    ContactOutcome,
    withdrawal_document,
)
from test_prepaid_dropoff_http import (
    CSRF,
    QUOTED_TOTAL,
    _as,
    _dropped_off,
    _member,
    _post,
    _store,
)

TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "storage-policy-dec-036.json"
DENIED = {"detail": "operation denied"}
VN = timedelta(hours=7)


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection(database_url: str) -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    settings = AuthSettings(database_url=database_url)
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    app.dependency_overrides[get_unclaimed_service] = lambda: UnclaimedService(settings)
    try:
        yield TestClient(app, cookies={"staff_session": "session-token", "staff_csrf": CSRF})
    finally:
        app.dependency_overrides.clear()


class Shop:
    def __init__(self, connection: Any) -> None:
        self.store_id, self.owner = _store(connection)
        self.approver = _member(connection, self.store_id, self.owner, StaffRole.OPS_APPROVER)
        self.operator = _member(connection, self.store_id, self.owner, StaffRole.OPERATOR)
        self.auditor = _member(connection, self.store_id, self.owner, StaffRole.AUDITOR)


@pytest.fixture
def shop(connection: psycopg.Connection[Any]) -> Generator[Shop, None, None]:
    """One store with each role, and no storage policy in force before or after the test."""

    made = Shop(connection)
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )
    yield made
    connection.rollback()
    publish_storage_policy(
        connection, actor_id=made.owner.staff_user_id, payload=withdrawal_document()
    )


def _publish(connection: Any, shop: Shop) -> None:
    publish_storage_policy(
        connection,
        actor_id=shop.owner.staff_user_id,
        payload=json.loads(TEMPLATE.read_text(encoding="utf-8")),
    )


def _ready(client: TestClient, connection: Any, shop: Shop) -> UUID:
    """A walk-in order washed and on the shelf, driven through the served step route."""

    order_id, version = _dropped_off(connection, shop.store_id, shop.operator)
    _as(shop.operator)
    for step in ("START_WASH", "QUALITY_CHECK", "MARK_READY"):
        moved = _post(
            client, f"/internal/v1/orders/{order_id}/steps", {"step": step}, if_match=version
        )
        assert moved.status_code == 200, moved.text
        version = int(moved.json()["row_version"])
    return order_id


def _age(connection: Any, order_id: UUID, days: int) -> None:
    """The documented harness step: the laundry was accepted and reported ready `days` days
    earlier. Both stamps move together, so the order stays one the SLA engine can read (ready is
    never before accepted); any promise, which is immutable once set, is left as it was."""

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE orders
            SET production_ready_at = production_ready_at - make_interval(days => %s),
                production_accepted_at = production_accepted_at - make_interval(days => %s),
                row_version = row_version + 1
            WHERE id = %s
            """,
            (days, days, order_id),
        )
    connection.commit()


def _read(client: TestClient, order_id: UUID) -> dict[str, Any]:
    response = client.get(f"/internal/v1/orders/{order_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _listed(client: TestClient, shop: Shop) -> dict[str, Any]:
    response = client.get(f"/internal/v1/stores/{shop.store_id}/orders/awaiting-pickup")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _row(listing: dict[str, Any], order_id: UUID) -> dict[str, Any]:
    return next(item for item in listing["orders"] if item["order_id"] == str(order_id))


def _attempt_at(connection: Any, order_id: UUID, staff: StaffPrincipal, at: datetime) -> None:
    """An attempt on an earlier day. The route records the server's now; a past day is recorded
    through the repository with its time held still, as the repository tests do."""

    UnclaimedRepository().record_contact_attempt(
        connection,
        ContactAttemptCommand(
            order_id=order_id,
            principal=staff,
            idempotency_key=f"attempt-{uuid4().hex}",
            correlation_id=uuid4(),
            channel=ContactChannel.CALL,
            outcome=ContactOutcome.NO_ANSWER,
            attempted_at=at,
        ),
    )
    connection.commit()


def test_before_publication_the_list_and_attempts_work_and_nothing_is_charged(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id = _ready(client, connection, shop)
    _age(connection, order_id, 40)

    listing = _listed(client, shop)
    assert listing["policy_published"] is False and listing["policy"] is None
    row = _row(listing, order_id)
    assert row["days_waiting"] == 40 and row["attempts_count"] == 0
    assert row["storage_fee"]["status"] == "POLICY_UNPUBLISHED"
    assert row["storage_fee"]["amount_vnd"] == 0 and row["remaining_vnd"] == QUOTED_TOTAL
    assert row["has_phone"] is False and row["ticket_number"] is not None

    attempt = _post(
        client,
        f"/internal/v1/orders/{order_id}/contact-attempts",
        {"channel": "ZALO", "outcome": "NO_ANSWER", "note": "đã nhắn, chưa xem"},
    )
    assert attempt.status_code == 201, attempt.text
    assert attempt.json()["ordinal"] == 1
    assert _row(_listed(client, shop), order_id)["attempts_count"] == 1

    read = _read(client, order_id)
    assert [c["kind"] for c in read["charges"]] == ["QUOTED_TOTAL"]
    _as(shop.approver)
    waived = _post(
        client,
        f"/internal/v1/orders/{order_id}/storage-fee-waiver",
        {"reason": "khách quen"},
        if_match=read["row_version"],
    )
    assert waived.status_code == 422
    assert waived.json()["detail"]["reason_code"] == "STORAGE_POLICY_UNPUBLISHED"
    _as(shop.owner)
    disposed = _post(
        client, f"/internal/v1/orders/{order_id}/disposal", if_match=read["row_version"]
    )
    assert disposed.status_code == 422
    assert "STORAGE_POLICY_UNPUBLISHED" in disposed.json()["detail"]["reason_codes"]


def test_the_fee_the_waiver_and_cash_at_pickup_including_the_fee(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id = _ready(client, connection, shop)
    _age(connection, order_id, 25)

    read = _read(client, order_id)
    assert [(c["kind"], c["amount_vnd"]) for c in read["charges"]] == [
        ("QUOTED_TOTAL", QUOTED_TOTAL),
        ("STORAGE_FEE", 25_000),
    ]
    assert (read["owed_vnd"], read["remaining_vnd"]) == (QUOTED_TOTAL + 25_000,) * 2
    listing = _listed(client, shop)
    assert listing["policy"]["receipt_line_vi"].startswith("Lấy đồ trong 20 ngày")
    assert _row(listing, order_id)["storage_fee"]["amount_vnd"] == 25_000
    storage = client.get(f"/internal/v1/orders/{order_id}/storage").json()
    assert storage["storage_fee"]["status"] == "ACCRUING"
    assert storage["storage_fee"]["chargeable_days"] == 5
    assert storage["disposal_verdict"]["allowed"] is False

    # Cash at pickup, the fee included, the customer takes the bag: one payment settles it.
    paid = _post(
        client,
        f"/internal/v1/orders/{order_id}/payments",
        {
            "amount_vnd": read["remaining_vnd"],
            "method": "TIEN_MAT",
            "transfer_seen": False,
            "collected_by_customer": True,
        },
        if_match=read["row_version"],
    )
    assert paid.status_code == 201, paid.text
    assert (paid.json()["balance_status"], paid.json()["remaining_vnd"]) == ("PAID", 0)
    after = _read(client, order_id)
    assert (after["owed_vnd"], after["paid_vnd"]) == (QUOTED_TOTAL + 25_000,) * 2
    assert client.get(f"/internal/v1/orders/{order_id}/storage").json()["storage_fee"] == {
        "status": "FIXED",
        "amount_vnd": 25_000,
        "chargeable_days": None,
        "fee_per_started_day_vnd": None,
        "cap_vnd": None,
        "capped": False,
        # MONEY-LIFECYCLE-009: the part of an unfixed fee a part payment covered; 0 once fixed.
        "already_paid_vnd": 0,
    }

    # A second order, waived by the approver instead.
    other = _ready(client, connection, shop)
    _age(connection, other, 30)
    version = _read(client, other)["row_version"]
    path = f"/internal/v1/orders/{other}/storage-fee-waiver"
    assert _post(client, path, {"reason": "khách quen"}, if_match=version).status_code == 403
    _as(shop.approver)
    assert _post(client, path, {"reason": "khách quen"}).status_code == 428
    assert _post(client, path, {"reason": "khách quen"}, if_match=version + 5).status_code == 409
    key = f"waive-{uuid4().hex}"
    waived = _post(client, path, {"reason": "khách quen"}, if_match=version, key=key)
    assert waived.status_code == 200, waived.text
    body = waived.json()
    assert (body["owed_vnd"], body["row_version"]) == (QUOTED_TOTAL, version + 1)
    replay = _post(client, path, {"reason": "khách quen"}, if_match=version, key=key)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    changed = _post(client, path, {"reason": "lý do khác"}, if_match=version, key=key)
    assert changed.status_code == 409
    again = _post(client, path, {"reason": "lần nữa"}, if_match=version + 1)
    assert again.json()["detail"]["reason_code"] == "STORAGE_FEE_ALREADY_WAIVED"
    waiver = client.get(f"/internal/v1/orders/{other}/storage").json()["waiver"]
    assert (waiver["waived_amount_vnd"], waiver["reason"]) == (50_000, "khách quen")


def test_a_note_that_looks_like_a_phone_is_refused_and_never_echoed(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id = _ready(client, connection, shop)
    path = f"/internal/v1/orders/{order_id}/contact-attempts"
    refused = _post(client, path, {"channel": "CALL", "outcome": "REACHED", "note": "0905 123 456"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["reason_code"] == "NOTE_LOOKS_LIKE_PHONE"
    assert "905" not in refused.text
    malformed = _post(
        client, path, {"channel": "PHONE", "outcome": "REACHED", "note": "0905123456"}
    )
    assert malformed.status_code == 422 and "0905123456" not in malformed.text
    waiver = _post(
        client,
        f"/internal/v1/orders/{order_id}/storage-fee-waiver",
        {"reason": 905123456},
        if_match=1,
    )
    assert waiver.status_code in {403, 422} and "905123456" not in waiver.text
    _as(shop.auditor)
    assert _post(client, path, {"channel": "CALL", "outcome": "REACHED"}).status_code == 403
    listing = _listed(client, shop)  # the auditor reads the list
    assert _row(listing, order_id)["phone"] is None and listing["phone_visible"] is False


def test_the_owner_disposes_after_sixty_days_and_three_attempts_on_two_days(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    _publish(connection, shop)
    order_id = _ready(client, connection, shop)
    _age(connection, order_id, 65)
    path = f"/internal/v1/orders/{order_id}/contact-attempts"
    for _ in range(2):
        attempt = _post(client, path, {"channel": "CALL", "outcome": "NO_ANSWER"})
        assert attempt.status_code == 201, attempt.text
    _attempt_at(connection, order_id, shop.operator, datetime.now(UTC) - timedelta(days=2))

    storage = client.get(f"/internal/v1/orders/{order_id}/storage").json()
    verdict = storage["disposal_verdict"]
    assert verdict["allowed"] is True and verdict["refusals"] == []
    assert (verdict["attempts_counted"], verdict["attempt_days"]) == (3, 2)
    assert (
        "Tiền khách đã trả giữ nguyên; tiền còn nợ được xoá."
        in (storage["policy"]["disposal_rule_vi"])
    )
    version = _read(client, order_id)["row_version"]
    dispose = f"/internal/v1/orders/{order_id}/disposal"
    for who in (shop.operator, shop.approver):
        _as(who)
        assert _post(client, dispose, if_match=version).json() == DENIED
    _as(shop.owner)
    assert _post(client, dispose).status_code == 428
    done = _post(client, dispose, if_match=version)
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["commercial"], body["balance"], body["paid_vnd"]) == ("CANCELLED", "UNPAID", 0)
    record = client.get(f"/internal/v1/orders/{order_id}/storage").json()["disposal"]
    owed = QUOTED_TOTAL + QUOTED_TOTAL // 2
    assert (record["owed_vnd"], record["kept_vnd"], record["written_off_vnd"]) == (owed, 0, owed)
    assert str(order_id) not in {item["order_id"] for item in _listed(client, shop)["orders"]}


def test_every_route_is_store_scoped(
    client: TestClient, connection: psycopg.Connection[Any], shop: Shop
) -> None:
    order_id = _ready(client, connection, shop)
    stranger = Shop(connection)
    _as(stranger.owner)
    assert (
        client.get(f"/internal/v1/stores/{shop.store_id}/orders/awaiting-pickup").json() == DENIED
    )
    assert client.get(f"/internal/v1/orders/{order_id}/storage").status_code == 404
    attempt = _post(
        client,
        f"/internal/v1/orders/{order_id}/contact-attempts",
        {"channel": "CALL", "outcome": "REACHED"},
    )
    assert attempt.json() == DENIED
    for route in ("storage-fee-waiver", "disposal"):
        body: dict[str, object] | None = {"reason": "x"} if route == "storage-fee-waiver" else None
        response = _post(client, f"/internal/v1/orders/{order_id}/{route}", body, if_match=5)
        assert response.json() == DENIED, route
