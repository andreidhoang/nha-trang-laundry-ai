"""`CONSENT-TRANSACTIONAL-001` (`DEC-033`) at the HTTP boundary, against PostgreSQL.

The refusals a console renders -- SUPPRESSED, PENDING_REVIEW, MESSAGING_POLICY_UNPUBLISHED,
NO_SERVICE_BASIS -- as the structured 422 bodies the routes send, and the two new store-scoped
routes: a contact's service-messaging state, and the release.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import app, current_principal, get_operations_service
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.consent import OptOutDisposition

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "db" / "tests"))

from message_draft_test_data import (
    SeededDraft,
    current_binding,
    publish_test_messaging_policy,
    record_customer_message,
    seed_message_draft,
)

ORIGIN = "http://testserver"
CSRF = "q" * 40
CHANNEL = "INTERNAL_TEST"


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return database_url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


@pytest.fixture
def service() -> OperationsService:
    return OperationsService(AuthSettings(database_url=_database_url()))


def _staff(role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
    return StaffPrincipal(uuid4(), f"consent-http-{uuid4().hex}", frozenset({role}), mfa, uuid4())


class _Shop:
    def __init__(self, connection: Any) -> None:
        self.operator = _staff(StaffRole.OPERATOR)
        self.approver = _staff(StaffRole.OPS_APPROVER)
        self.owner = _staff(StaffRole.OWNER_ADMIN)
        self.store_id = uuid4()
        StoreRepository.create(
            connection, store_id=self.store_id, name="Cửa hàng", created_by=None,
            correlation_id=uuid4(),
        )  # fmt: skip
        for member in (self.operator, self.approver, self.owner):
            self.add(connection, member)
        self.draft: SeededDraft = seed_message_draft(connection, self.store_id)
        self.contact = self.draft.contact_binding_id

    def add(self, connection: Any, member: StaffPrincipal, store_id: UUID | None = None) -> None:
        now = datetime.now(UTC)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s) ON CONFLICT (id) DO NOTHING
                """,
                (member.staff_user_id, member.oidc_subject, now),
            )
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1)
                """,
                (member.staff_user_id, store_id or self.store_id, member.staff_user_id, now),
            )

    @property
    def state_url(self) -> str:
        return (
            f"/internal/v1/stores/{self.store_id}/contacts/{self.contact}/service-messaging"
            f"?channel={CHANNEL}"
        )

    @property
    def release_url(self) -> str:
        return (
            f"/internal/v1/stores/{self.store_id}/contacts/{self.contact}/service-messaging/release"
        )


@contextmanager
def _as(service: OperationsService, principal: StaffPrincipal) -> Iterator[TestClient]:
    app.dependency_overrides[current_principal] = lambda: principal
    app.dependency_overrides[get_operations_service] = lambda: service
    try:
        with TestClient(app, cookies={"staff_session": "t", "staff_csrf": CSRF}) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": CSRF}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _raise(client: TestClient, connection: Any, shop: _Shop) -> Any:
    binding = current_binding(connection, shop.draft.agent_run_id)
    return client.post(
        "/internal/v1/approvals",
        json={
            "store_id": str(shop.store_id),
            "action": "SEND_MESSAGE",
            "resource_type": "MESSAGE_DRAFT",
            "resource_id": str(shop.draft.agent_run_id),
            "resource_version": binding.resource_version,
            "snapshot_hash": binding.snapshot_hash,
            "rendered_hash": binding.rendered_hash,
            "policy_version": "manual-send-policy-v1",
        },
        headers=_headers(f"a-{uuid4().hex}"),
    )


def _approved(connection: Any, service: OperationsService, shop: _Shop) -> tuple[UUID, Any]:
    binding = current_binding(connection, shop.draft.agent_run_id)
    with _as(service, shop.operator) as client:
        created = _raise(client, connection, shop)
    assert created.status_code == 201, created.text
    approval_id = UUID(created.json()["approval_request_id"])
    with _as(service, shop.approver) as client:
        decided = client.post(
            f"/internal/v1/approvals/{approval_id}/decisions",
            json={
                "decision": "APPROVED",
                "reason_code": "HUMAN_REVIEW_COMPLETE",
                "resource_version": binding.resource_version,
                "snapshot_hash": binding.snapshot_hash,
                "rendered_hash": binding.rendered_hash,
            },
            headers=_headers(f"d-{uuid4().hex}"),
        )
    assert decided.status_code == 200, decided.text
    return approval_id, binding


def _prepare(service: OperationsService, shop: _Shop, approval_id: UUID, binding: Any) -> Any:
    with _as(service, shop.operator) as client:
        return client.post(
            f"/internal/v1/approvals/{approval_id}/manual-send",
            json={
                "observed_resource_version": binding.resource_version,
                "observed_snapshot_hash": binding.snapshot_hash,
                "observed_rendered_hash": binding.rendered_hash,
                "channel": CHANNEL,
            },
            headers=_headers(f"m-{uuid4().hex}"),
        )


def _envelopes(connection: Any) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM manual_send_envelopes")
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _assert_refusal(response: Any, shop: _Shop, outcome: str, reason: str) -> None:
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["outcome"] == outcome
    assert detail["reason_code"] == reason
    assert detail["purpose"] == "TRANSACTIONAL"
    assert detail["contact_binding_id"] == str(shop.contact)
    assert detail["store_id"] == str(shop.store_id)
    assert detail["channel"] == CHANNEL
    assert detail["decision"] == "DEC-033"
    assert shop.draft.text not in response.text


# --- the refusals the console renders ----------------------------------------------------------


def test_prepare_refuses_an_unpublished_policy_and_locks_nothing(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    record_customer_message(connection, shop.contact, received_at=datetime.now(UTC))
    approval_id, binding = _approved(connection, service, shop)

    refused = _prepare(service, shop, approval_id, binding)

    _assert_refusal(refused, shop, "REQUIRE_HUMAN", "MESSAGING_POLICY_UNPUBLISHED")
    assert _envelopes(connection) == 0


def test_prepare_refuses_without_a_service_basis(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    approval_id, binding = _approved(connection, service, shop)

    _assert_refusal(
        _prepare(service, shop, approval_id, binding), shop, "REQUIRE_HUMAN", "NO_SERVICE_BASIS"
    )
    assert _envelopes(connection) == 0


def test_prepare_refuses_a_stop_that_arrived_after_the_envelope_was_raised(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    record_customer_message(connection, shop.contact, received_at=datetime.now(UTC))
    approval_id, binding = _approved(connection, service, shop)
    record_customer_message(
        connection,
        shop.contact,
        received_at=datetime.now(UTC),
        disposition=OptOutDisposition.WITHDRAW,
    )

    _assert_refusal(_prepare(service, shop, approval_id, binding), shop, "SUPPRESSED", "SUPPRESSED")
    assert _envelopes(connection) == 0


def test_prepare_refuses_an_opt_out_awaiting_review(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    approval_id, binding = _approved(connection, service, shop)
    record_customer_message(
        connection,
        shop.contact,
        received_at=datetime.now(UTC),
        disposition=OptOutDisposition.PENDING_REVIEW_BLOCKED,
    )

    _assert_refusal(
        _prepare(service, shop, approval_id, binding), shop, "REQUIRE_HUMAN", "PENDING_REVIEW"
    )


def test_attest_refuses_a_stop_that_arrived_after_prepare(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    record_customer_message(connection, shop.contact, received_at=datetime.now(UTC))
    approval_id, binding = _approved(connection, service, shop)
    prepared = _prepare(service, shop, approval_id, binding)
    assert prepared.status_code == 201, prepared.text
    record_customer_message(
        connection,
        shop.contact,
        received_at=datetime.now(UTC),
        disposition=OptOutDisposition.WITHDRAW,
    )

    with _as(service, shop.operator) as client:
        refused = client.post(
            f"/internal/v1/manual-sends/{prepared.json()['manual_send_envelope_id']}/attest",
            json={
                "observed_resource_version": binding.resource_version,
                "exact_rendered_hash": binding.rendered_hash,
                "sent_at": datetime.now(UTC).isoformat(),
            },
            headers={**_headers(f"t-{uuid4().hex}"), "If-Match": '"1"'},
        )
    _assert_refusal(refused, shop, "SUPPRESSED", "SUPPRESSED")


def test_raising_a_send_envelope_for_a_suppressed_contact_is_refused(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    record_customer_message(
        connection,
        shop.contact,
        received_at=datetime.now(UTC),
        disposition=OptOutDisposition.WITHDRAW,
    )
    with _as(service, shop.operator) as client:
        refused = _raise(client, connection, shop)
    _assert_refusal(refused, shop, "SUPPRESSED", "SUPPRESSED")


# --- the read ----------------------------------------------------------------------------------


def test_the_read_reports_the_state_the_guard_and_the_evidence_a_release_may_cite(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    now = datetime.now(UTC)
    record_customer_message(
        connection,
        shop.contact,
        received_at=now - timedelta(hours=2),
        disposition=OptOutDisposition.WITHDRAW,
    )
    later = record_customer_message(connection, shop.contact, received_at=now - timedelta(hours=1))

    with _as(service, shop.operator) as client:
        read = client.get(shop.state_url)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["transactional_state"] == "SUPPRESSED"
    assert body["marketing_state"] == "SUPPRESSED"
    assert body["releasable"] is True
    assert body["egress"] == {
        "decision": "SUPPRESSED",
        "reason_code": "SUPPRESSED",
        "basis": None,
        "policy_version": 1,
        "suppression_state": "SUPPRESSED",
    }
    assert [item["webhook_event_id"] for item in body["release_evidence"]] == [str(later)]


@pytest.mark.parametrize("who", ["another_store", "unknown_store", "auditor", "no_mfa"])
def test_the_read_is_store_scoped(connection: Any, service: OperationsService, who: str) -> None:
    shop = _Shop(connection)
    principal = _staff(StaffRole.AUDITOR if who == "auditor" else StaffRole.OPERATOR)
    url = shop.state_url
    if who == "another_store":
        other = _Shop(connection)
        shop.add(connection, principal, store_id=other.store_id)
    elif who == "unknown_store":
        shop.add(connection, principal)
        url = url.replace(str(shop.store_id), str(uuid4()))
    elif who == "no_mfa":
        principal = _staff(StaffRole.OPERATOR, mfa=False)
        shop.add(connection, principal)
    else:
        shop.add(connection, principal)

    with _as(service, principal) as client:
        refused = client.get(url)
    assert refused.status_code == 403, refused.text


def test_a_contact_the_store_never_dealt_with_is_404(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    with _as(service, shop.operator) as client:
        missing = client.get(shop.state_url.replace(str(shop.contact), str(uuid4())))
    assert missing.status_code == 404


# --- the release -------------------------------------------------------------------------------


def _suppressed_with_evidence(connection: Any, shop: _Shop) -> UUID:
    now = datetime.now(UTC)
    record_customer_message(
        connection,
        shop.contact,
        received_at=now - timedelta(hours=2),
        disposition=OptOutDisposition.WITHDRAW,
    )
    return record_customer_message(connection, shop.contact, received_at=now - timedelta(hours=1))


def test_an_owner_releases_on_the_customers_later_message_and_a_retry_replays(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    evidence = _suppressed_with_evidence(connection, shop)
    body = {"channel": CHANNEL, "evidence_webhook_event_id": str(evidence)}
    key = f"r-{uuid4().hex}"

    with _as(service, shop.owner) as client:
        released = client.post(shop.release_url, json=body, headers=_headers(key))
        replayed = client.post(shop.release_url, json=body, headers=_headers(key))
        conflicting = client.post(
            shop.release_url,
            json={**body, "evidence_webhook_event_id": str(uuid4())},
            headers=_headers(key),
        )
        after = client.get(shop.state_url).json()

    assert released.status_code == 201, released.text
    assert released.json()["state"] == "CLEAR"
    assert released.json()["previous_state"] == "SUPPRESSED"
    assert released.json()["replayed"] is False
    assert replayed.status_code == 201
    assert replayed.json()["replayed"] is True
    assert (
        replayed.json()["release_consent_event_id"] == released.json()["release_consent_event_id"]
    )
    assert conflicting.status_code == 409
    assert after["transactional_state"] == "CLEAR"
    assert after["marketing_state"] == "SUPPRESSED"
    assert after["egress"]["decision"] == "ALLOW"
    assert after["egress"]["basis"] == "CUSTOMER_INITIATED"


@pytest.mark.parametrize(
    "who", ["operator", "approver_without_mfa", "owner_of_another_store", "unknown_store"]
)
def test_a_release_is_refused_to_anyone_but_an_owner_or_approver_of_this_store(
    connection: Any, service: OperationsService, who: str
) -> None:
    shop = _Shop(connection)
    evidence = _suppressed_with_evidence(connection, shop)
    url = shop.release_url
    principal = {
        "operator": shop.operator,
        "approver_without_mfa": StaffPrincipal(
            shop.approver.staff_user_id,
            shop.approver.oidc_subject,
            shop.approver.roles,
            False,
            uuid4(),
        ),
        "owner_of_another_store": _staff(StaffRole.OWNER_ADMIN),
        "unknown_store": shop.owner,
    }[who]
    if who == "owner_of_another_store":
        shop.add(connection, principal, store_id=_Shop(connection).store_id)
    if who == "unknown_store":
        url = url.replace(str(shop.store_id), str(uuid4()))

    with _as(service, principal) as client:
        refused = client.post(
            url,
            json={"channel": CHANNEL, "evidence_webhook_event_id": str(evidence)},
            headers=_headers(f"r-{uuid4().hex}"),
        )
        state = client.get(shop.state_url) if who == "unknown_store" else None
    assert refused.status_code == 403, refused.text
    if state is not None:
        assert state.json()["transactional_state"] == "SUPPRESSED"


def test_a_release_on_evidence_the_server_cannot_verify_is_refused_with_its_reason(
    connection: Any, service: OperationsService
) -> None:
    shop = _Shop(connection)
    _suppressed_with_evidence(connection, shop)
    stranger = record_customer_message(connection, uuid4(), received_at=datetime.now(UTC))

    with _as(service, shop.approver) as client:
        refused = client.post(
            shop.release_url,
            json={"channel": CHANNEL, "evidence_webhook_event_id": str(stranger)},
            headers=_headers(f"r-{uuid4().hex}"),
        )
        still = client.get(shop.state_url).json()
    assert refused.status_code == 422
    assert refused.json()["detail"] == {
        "reason_code": "RELEASE_EVIDENCE_INVALID",
        "decision": "DEC-033",
    }
    assert still["transactional_state"] == "SUPPRESSED"


def test_nothing_to_release_is_said_so(connection: Any, service: OperationsService) -> None:
    shop = _Shop(connection)
    message = record_customer_message(connection, shop.contact, received_at=datetime.now(UTC))
    with _as(service, shop.owner) as client:
        refused = client.post(
            shop.release_url,
            json={"channel": CHANNEL, "evidence_webhook_event_id": str(message)},
            headers=_headers(f"r-{uuid4().hex}"),
        )
    assert refused.status_code == 422
    assert refused.json()["detail"]["reason_code"] == "NOTHING_TO_RELEASE"
