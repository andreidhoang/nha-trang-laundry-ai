"""`CUSTOMER-001` (`DEC-034`) over HTTP against real PostgreSQL.

* **refusal before publication** -- on a database where the owner has published nothing, creating a
  customer answers 422 `PRIVACY_NOTICE_UNPUBLISHED` and the notice read says `published: false`;
* **roles and masking** -- the operations roles read the number, an auditor reads it masked, and a
  driver, a session without MFA, a member of another store all receive the same opaque 403;
* **If-Match** on a correction and an erasure; only an owner or approver erases;
* **idempotent create** -- a replay answers the same record, a changed body under the same key is a
  conflict, a second key for the same number names the existing record;
* **cross-store 404** -- another store's customer, asked through one's own store, does not exist;
* **no phone in an error** -- a malformed body answers without echoing what was sent, and no phone
  reaches the idempotency ledger;
* **the counter's intake** -- `POST …/order-requests` with `customer_id` issues the ticket and opens
  the intake in one press, and the order page reads the customer's name.
"""

from __future__ import annotations

import logging
import os
import random
import sys
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.customers import CustomerQueryRedaction, CustomerService
from nha_trang_laundry_api.main import (
    app,
    current_principal,
    get_customer_service,
    get_operations_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.privacy_notice import publish_privacy_notice
from nha_trang_laundry_db.stores import StoreRepository
from psycopg import sql
from psycopg.conninfo import make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from test_customer_notice import notice_payload

DENIED = {"detail": "operation denied"}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


def _use(url: str) -> TestClient:
    settings = AuthSettings(database_url=url)
    app.dependency_overrides[get_operations_service] = lambda: OperationsService(settings)
    app.dependency_overrides[get_customer_service] = lambda: CustomerService(settings)
    return TestClient(app)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def client() -> Iterator[TestClient]:
    try:
        yield _use(_database_url())
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def scratch() -> Iterator[tuple[str, TestClient]]:
    configured = _database_url()
    maintenance = make_conninfo(configured, dbname="postgres")
    name = f"ntl_customer_api_{uuid4().hex[:12]}"
    with psycopg.connect(maintenance, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(configured, dbname=name)
    try:
        with psycopg.connect(url, autocommit=True) as established:
            apply_migrations(established)
        yield url, _use(url)
    finally:
        app.dependency_overrides.clear()
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


def _as(principal: StaffPrincipal) -> None:
    app.dependency_overrides[current_principal] = lambda: principal


def _person(connection: Any, role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
    staff_user_id = uuid4()
    now = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên thử nghiệm', 'ACTIVE', %s)
            """,
            (staff_user_id, f"oidc-{staff_user_id}", now),
        )
        cursor.execute(
            """
            INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
            VALUES (%s, %s, %s, %s)
            """,
            (uuid4(), staff_user_id, role.value, now),
        )
    return StaffPrincipal(staff_user_id, f"oidc-{staff_user_id}", frozenset({role}), mfa, uuid4())


def _member(
    connection: Any, store_id: UUID, role: StaffRole, *, mfa: bool = True
) -> StaffPrincipal:
    person = _person(connection, role, mfa=mfa)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_store_assignments (
                staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
            ) VALUES (%s, %s, %s, %s, 1)
            """,
            (person.staff_user_id, store_id, person.staff_user_id, datetime.now(UTC)),
        )
    return person


def _store(connection: Any) -> UUID:
    store_id = uuid4()
    StoreRepository.create(
        connection,
        store_id=store_id,
        name="Cửa hàng thử nghiệm",
        created_by=None,
        correlation_id=uuid4(),
    )
    return store_id


def _publish(connection: Any) -> None:
    owner = _person(connection, StaffRole.OWNER_ADMIN)
    publish_privacy_notice(connection, actor_id=owner.staff_user_id, payload=notice_payload())


def _mobile() -> str:
    return "09" + "".join(random.choice("0123456789") for _ in range(8))


def _body(phone: str, **extra: Any) -> dict[str, Any]:
    return {"phone": phone, "display_name": "chị Lan", "service_consent": True, **extra}


def _create(
    client: TestClient, store_id: UUID, body: dict[str, Any], key: str | None = None
) -> Any:
    return client.post(
        f"/internal/v1/stores/{store_id}/customers",
        json=body,
        headers={"Idempotency-Key": key or f"customer-{uuid4().hex}"},
    )


# --- publication ----------------------------------------------------------------------------------


def test_creating_a_customer_is_refused_until_the_notice_is_published(
    scratch: tuple[str, TestClient],
) -> None:
    url, client = scratch
    with psycopg.connect(url, autocommit=True) as connection:
        store_id = _store(connection)
        staff = _member(connection, store_id, StaffRole.OPERATOR)
        _as(staff)
        notice = client.get(f"/internal/v1/stores/{store_id}/customer-privacy-notice")
        assert notice.status_code == 200 and notice.json() == {
            "published": False,
            "version": None,
            "notice_version": None,
            "title": None,
            "text": None,
            "consent_sentence": None,
            "service_consent_label": None,
            "marketing_consent_label": None,
            "retention_months": None,
            "legal_entity": None,
        }
        # Refused whatever was typed -- even an invalid number is answered with the notice first.
        for phone in (_mobile(), "12"):
            refused = _create(client, store_id, _body(phone))
            assert refused.status_code == 422
            assert refused.json() == {
                "detail": {
                    "outcome": "NOT_SUPPORTED",
                    "reason_code": "PRIVACY_NOTICE_UNPUBLISHED",
                    "decision": "DEC-034",
                }
            }
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM customers")
            assert cursor.fetchone() == (0,)
            cursor.execute("SELECT count(*) FROM command_idempotency_records")
            assert cursor.fetchone() == (0,)

        _publish(connection)
        published = client.get(f"/internal/v1/stores/{store_id}/customer-privacy-notice").json()
        assert published["published"] is True and published["retention_months"] == 24
        assert published["legal_entity"] == "CÔNG TY TNHH A & T CARE"
        assert _create(client, store_id, _body(_mobile())).status_code == 201


# --- roles and masking ----------------------------------------------------------------------------


def test_roles_and_masking(connection: Any, client: TestClient) -> None:
    _publish(connection)
    store_id, other_store = _store(connection), _store(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    phone = _mobile()
    _as(operator)
    created = _create(client, store_id, _body(phone, delivery_address="12 Trần Phú"))
    assert created.status_code == 201, created.text
    customer = created.json()["customer"]
    customer_id = customer["customer_id"]
    assert (customer["phone"], customer["phone_visible"], customer["row_version"]) == (
        phone,
        True,
        1,
    )

    for role in (StaffRole.OPS_APPROVER, StaffRole.OWNER_ADMIN):
        _as(_member(connection, store_id, role))
        found = client.get(f"/internal/v1/stores/{store_id}/customers", params={"q": phone[-4:]})
        assert found.status_code == 200
        assert [item["phone"] for item in found.json()["customers"]] == [phone]

    auditor = _member(connection, store_id, StaffRole.AUDITOR)
    _as(auditor)
    masked = client.get(f"/internal/v1/stores/{store_id}/customers", params={"q": phone})
    assert masked.json()["mode"] == "PHONE"
    assert [(item["phone"], item["phone_last4"]) for item in masked.json()["customers"]] == [
        (None, phone[-4:])
    ]
    detail = client.get(f"/internal/v1/stores/{store_id}/customers/{customer_id}").json()
    assert (detail["customer"]["phone"], detail["customer"]["delivery_address"]) == (None, None)
    assert phone not in client.get(f"/internal/v1/stores/{store_id}/customers/{customer_id}").text
    # An auditor reads; an auditor does not write.
    assert _create(client, store_id, _body(_mobile())).status_code == 403

    refused = [
        _member(connection, store_id, StaffRole.DRIVER),
        _member(connection, store_id, StaffRole.ACCOUNTANT),
        _member(connection, store_id, StaffRole.OPERATOR, mfa=False),
        _member(connection, other_store, StaffRole.OPERATOR),
    ]
    for principal in refused:
        _as(principal)
        for response in (
            client.get(f"/internal/v1/stores/{store_id}/customers", params={"q": phone}),
            client.get(f"/internal/v1/stores/{store_id}/customers/{customer_id}"),
            _create(client, store_id, _body(_mobile())),
        ):
            assert (response.status_code, response.json()) == (403, DENIED)

    # Asked through one's own store, another store's customer does not exist.
    _as(_member(connection, other_store, StaffRole.OPERATOR))
    assert (
        client.get(f"/internal/v1/stores/{other_store}/customers/{customer_id}").status_code == 404
    )


# --- idempotency and duplicates -------------------------------------------------------------------


def test_create_is_idempotent_and_a_duplicate_names_the_record(
    connection: Any, client: TestClient
) -> None:
    _publish(connection)
    store_id = _store(connection)
    _as(_member(connection, store_id, StaffRole.OPERATOR))
    phone = _mobile()
    key = f"customer-{uuid4().hex}"
    first = _create(client, store_id, _body(phone), key)
    replay = _create(client, store_id, _body(phone), key)
    assert first.status_code == 201 and replay.status_code == 201
    assert replay.json()["replayed"] is True
    assert replay.json()["customer"]["customer_id"] == first.json()["customer"]["customer_id"]

    changed = _create(client, store_id, _body(phone, display_name="anh Tuấn"), key)
    assert (changed.status_code, changed.json()) == (409, {"detail": "IDEMPOTENCY_CONFLICT"})

    spaced = f"+84 {phone[1:4]} {phone[4:7]} {phone[7:]}"
    duplicate = _create(client, store_id, _body(spaced))
    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "detail": {
            "reason_code": "CUSTOMER_PHONE_EXISTS",
            "decision": "DEC-034",
            "customer_id": first.json()["customer"]["customer_id"],
        }
    }
    invalid = _create(client, store_id, _body("0605 123 456"))
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["reason_code"] == "PHONE_INVALID"
    unattested = _create(client, store_id, _body(_mobile(), service_consent=False))
    assert unattested.json()["detail"]["reason_code"] == "SERVICE_CONSENT_REQUIRED"

    # The ledger keeps ids and a keyed digest of the request: never the number.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT response::text, request_hash FROM command_idempotency_records "
            "WHERE scope LIKE 'staff-customer-create:%%'"
        )
        for response, request_hash in cursor.fetchall():
            assert phone[1:] not in response and "chị Lan" not in response
            assert phone[1:] not in request_hash


def test_a_malformed_body_is_refused_without_echoing_it(
    connection: Any, client: TestClient
) -> None:
    _publish(connection)
    store_id = _store(connection)
    _as(_member(connection, store_id, StaffRole.OPERATOR))
    phone = _mobile()
    for body in (
        {"phone": phone},  # service_consent missing: FastAPI would echo the whole body
        {"phone": phone, "service_consent": "yes"},
        {"phone": phone, "service_consent": True, "surname": phone},
        {"phone": phone * 5, "service_consent": True},
    ):
        response = _create(client, store_id, body)
        assert response.status_code == 422
        assert phone not in response.text, response.text
        assert all("input" not in item for item in response.json()["detail"])
    too_long = client.get(f"/internal/v1/stores/{store_id}/customers", params={"q": phone * 9})
    assert too_long.status_code == 422 and phone not in too_long.text


# --- If-Match, erasure ----------------------------------------------------------------------------


def test_corrections_and_erasure_need_the_current_version(
    connection: Any, client: TestClient
) -> None:
    _publish(connection)
    store_id = _store(connection)
    operator = _member(connection, store_id, StaffRole.OPERATOR)
    _as(operator)
    customer_id = _create(client, store_id, _body(_mobile())).json()["customer"]["customer_id"]
    path = f"/internal/v1/stores/{store_id}/customers/{customer_id}"

    def patch(body: dict[str, Any], version: str | None) -> Any:
        headers = {"Idempotency-Key": f"patch-{uuid4().hex}"}
        if version is not None:
            headers["If-Match"] = version
        return client.patch(path, json=body, headers=headers)

    assert patch({"note": "Giặt riêng đồ trắng"}, None).status_code == 428
    corrected = patch({"note": "Giặt riêng đồ trắng", "marketing_consent": True}, "1")
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["customer"]["row_version"] == 2
    assert corrected.json()["customer"]["marketing_consent"] is True
    stale = patch({"note": "khác"}, "1")
    assert stale.status_code == 409 and stale.json()["detail"].startswith("STALE_VERSION")

    def erase(version: str) -> Any:
        return client.post(
            f"{path}/erase",
            json={"reason": "CUSTOMER_REQUEST"},
            headers={"Idempotency-Key": f"erase-{uuid4().hex}", "If-Match": version},
        )

    assert (erase("2").status_code, erase("2").json()) == (403, DENIED)
    _as(_member(connection, store_id, StaffRole.OPS_APPROVER))
    assert erase("1").status_code == 409
    erased = erase("2")
    assert erased.status_code == 200, erased.text
    body = erased.json()["customer"]
    assert body["erased_at"] and body["display_name"] is None and body["phone"] is None
    assert body["erasure_reason"] == "CUSTOMER_REQUEST"
    again = erase("3")
    assert again.status_code == 422 and again.json()["detail"]["reason_code"] == "CUSTOMER_ERASED"
    assert patch({"note": "x"}, "3").json()["detail"]["reason_code"] == "CUSTOMER_ERASED"


# --- the counter's intake and the order page ------------------------------------------------------


def test_an_intake_for_a_customer_issues_the_ticket_in_one_press(
    connection: Any, client: TestClient
) -> None:
    _publish(connection)
    store_id = _store(connection)
    _as(_member(connection, store_id, StaffRole.OPERATOR))
    customer_id = _create(client, store_id, _body(_mobile())).json()["customer"]["customer_id"]
    key = f"intake-{uuid4().hex}"

    def open_intake(body: dict[str, Any], idempotency: str | None = None) -> Any:
        return client.post(
            f"/internal/v1/stores/{store_id}/order-requests",
            json=body,
            headers={"Idempotency-Key": idempotency or f"intake-{uuid4().hex}"},
        )

    opened = open_intake({"customer_id": customer_id}, key)
    assert opened.status_code == 201, opened.text
    intake = opened.json()
    assert intake["customer_id"] == customer_id and intake["ticket_number"] >= 1
    replay = open_intake({"customer_id": customer_id}, key).json()
    assert (replay["replayed"], replay["order_request_id"], replay["ticket_number"]) == (
        True,
        intake["order_request_id"],
        intake["ticket_number"],
    )
    summary = client.get(
        f"/internal/v1/stores/{store_id}/order-requests/{intake['order_request_id']}"
    ).json()
    assert (summary["customer_id"], summary["customer_name"], summary["ticket_number"]) == (
        customer_id,
        "chị Lan",
        intake["ticket_number"],
    )
    # Exactly one reference; an unknown record is refused like an unknown binding.
    both = open_intake({"customer_id": customer_id, "contact_binding_id": str(uuid4())})
    assert both.status_code == 422
    unknown = open_intake({"customer_id": str(uuid4())})
    assert unknown.status_code == 422
    assert unknown.json()["detail"] == {
        "outcome": "REQUIRE_HUMAN",
        "reason_codes": ["CUSTOMER_UNKNOWN"],
    }


# --- the access log -------------------------------------------------------------------------------


def test_the_access_log_never_prints_a_customer_query() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:5000",
            "GET",
            "/internal/v1/stores/abc/customers?q=0905%20123%20456&limit=20",
            "1.1",
            200,
        ),
        None,
    )
    assert CustomerQueryRedaction().filter(record) is True
    line = record.getMessage()
    assert "0905" not in line and "?[query redacted]" in line
    other = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/internal/v1/stores/abc/orders?open=true", "1.1", 200),
        None,
    )
    CustomerQueryRedaction().filter(other)
    assert "open=true" in other.getMessage()
    assert any(
        isinstance(item, CustomerQueryRedaction)
        for item in logging.getLogger("uvicorn.access").filters
    )
