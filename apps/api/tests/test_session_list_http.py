"""`SESSION-LIST-001` over HTTP, with real session cookies against real PostgreSQL.

Nothing here overrides `current_principal`: every request carries the cookie a browser would, and
the server authenticates it through `staff_sessions` exactly as it does in production. That is what
lets these tests prove the property a lost phone depends on -- after the owner signs one device out,
that device's very next request is refused while every other device of the same person keeps
working -- rather than asserting it about a principal the test invented.
"""

from __future__ import annotations

import os
import re
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings, StaffIdentityService
from nha_trang_laundry_api.main import app, get_identity_service
from nha_trang_laundry_db.identity import IdentityRepository, StaffRole
from nha_trang_laundry_db.migrations import apply_migrations

ORIGIN = "http://testserver"
CSRF = "csrf-token-with-at-least-thirty-two-bytes-of-entropy"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return url


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as established:
        apply_migrations(established)
        yield established


class _NoTokens:
    """The identity provider is not under test: sessions are minted straight into the table."""

    def verify(self, token: str) -> Any:  # pragma: no cover - never reached
        raise AssertionError("no identity token is exchanged in these tests")


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = AuthSettings(
        database_url=_database_url(),
        oidc_issuer="https://idp.invalid/realms/test",
        oidc_audience="staff-console",
        oidc_jwks_url="https://idp.invalid/realms/test/jwks",
        oidc_mfa_claim="acr",
        oidc_mfa_value="mfa",
    )
    app.dependency_overrides[get_identity_service] = lambda: StaffIdentityService(
        settings,
        verifier=_NoTokens(),  # type: ignore[arg-type]
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _person(connection: Any, *roles: StaffRole) -> tuple[UUID, str]:
    staff_id = uuid4()
    subject = f"session-http-{staff_id}"
    now = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, email, status, created_at)
            VALUES (%s, %s, 'Người thử', NULL, 'ACTIVE', %s)
            """,
            (staff_id, subject, now),
        )
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_id, role.value, now),
            )
    return staff_id, subject


def _device(connection: Any, subject: str) -> Any:
    """One signed-in browser: the cookie value `POST /internal/v1/auth/session` would set."""

    return IdentityRepository().create_session(
        connection, oidc_subject=subject, mfa_verified=True, correlation_id=uuid4()
    )


def _headers(device: Any) -> dict[str, str]:
    return {
        "Cookie": f"staff_session={device.value}; staff_csrf={CSRF}",
        "Origin": ORIGIN,
        "X-CSRF-Token": CSRF,
    }


def _get(client: TestClient, path: str, device: Any) -> Any:
    return client.get(path, headers=_headers(device))


def _revoke(client: TestClient, session_id: UUID, device: Any) -> Any:
    headers = {**_headers(device), "Idempotency-Key": f"revoke-{uuid4().hex}"}
    return client.post(f"/internal/v1/sessions/{session_id}/revoke", headers=headers)


def _secret(device: Any) -> str:
    return str(device.value).split(".", 1)[1]


def _assert_no_secret(response: Any, *devices: Any) -> None:
    """No secret half of any cookie, no `secret_hash` column, and no 64-hex digest at all."""

    text = response.text
    assert "secret" not in text and "hash" not in text
    assert re.search(r"[0-9a-f]{64}", text) is None
    for device in devices:
        assert _secret(device) not in text


# --- the session read names its session ------------------------------------------------------


def test_the_session_read_names_the_session_the_cookie_is(
    connection: Any, client: TestClient
) -> None:
    staff_id, subject = _person(connection, StaffRole.OPERATOR)
    device = _device(connection, subject)

    response = _get(client, "/internal/v1/session", device)

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == str(device.session_id)
    assert body["staff_user_id"] == str(staff_id)
    _assert_no_secret(response, device)


# --- GET /internal/v1/sessions -------------------------------------------------------------------


def test_a_person_lists_their_own_devices_with_this_one_marked(
    connection: Any, client: TestClient
) -> None:
    staff_id, subject = _person(connection, StaffRole.OPERATOR)
    phone = _device(connection, subject)
    tablet = _device(connection, subject)
    _, other_subject = _person(connection, StaffRole.OPERATOR)
    stranger = _device(connection, other_subject)

    response = _get(client, "/internal/v1/sessions", tablet)

    assert response.status_code == 200
    body = response.json()
    assert body["staff_user_id"] == str(staff_id)
    assert body["truncated"] is False
    marked = {entry["session_id"]: entry["current"] for entry in body["sessions"]}
    assert marked == {str(phone.session_id): False, str(tablet.session_id): True}
    assert set(body["sessions"][0]) == {
        "session_id",
        "issued_at",
        "last_seen_at",
        "idle_expires_at",
        "absolute_expires_at",
        "current",
    }
    _assert_no_secret(response, phone, tablet, stranger)


def test_the_own_list_is_bounded_and_discloses_the_cut(connection: Any, client: TestClient) -> None:
    _, subject = _person(connection, StaffRole.OPERATOR)
    devices = [_device(connection, subject) for _ in range(3)]

    cut = _get(client, "/internal/v1/sessions?limit=2", devices[0])
    refused = _get(client, "/internal/v1/sessions?limit=201", devices[0])

    assert cut.status_code == 200
    assert len(cut.json()["sessions"]) == 2
    assert cut.json()["truncated"] is True
    assert refused.status_code == 422


def test_the_session_list_needs_a_session(client: TestClient) -> None:
    assert client.get("/internal/v1/sessions").status_code == 401


# --- GET /internal/v1/staff/{id}/sessions --------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [
        StaffRole.OPERATOR,
        StaffRole.OPS_APPROVER,
        StaffRole.DRIVER,
        StaffRole.ACCOUNTANT,
        StaffRole.AUDITOR,
    ],
)
def test_nobody_but_the_owner_lists_another_persons_devices(
    connection: Any, client: TestClient, role: StaffRole
) -> None:
    owner_id, owner_subject = _person(connection, StaffRole.OWNER_ADMIN)
    _device(connection, owner_subject)
    _, subject = _person(connection, role)
    reader = _device(connection, subject)

    response = _get(client, f"/internal/v1/staff/{owner_id}/sessions", reader)

    assert response.status_code == 403
    assert "session" not in response.text


def test_the_owner_lists_a_persons_devices_and_none_is_theirs(
    connection: Any, client: TestClient
) -> None:
    _, owner_subject = _person(connection, StaffRole.OWNER_ADMIN)
    owner = _device(connection, owner_subject)
    lan_id, lan_subject = _person(connection, StaffRole.OPERATOR)
    lan_phone = _device(connection, lan_subject)
    lan_tablet = _device(connection, lan_subject)

    response = _get(client, f"/internal/v1/staff/{lan_id}/sessions", owner)

    assert response.status_code == 200
    body = response.json()
    assert body["staff_user_id"] == str(lan_id)
    assert {entry["session_id"] for entry in body["sessions"]} == {
        str(lan_phone.session_id),
        str(lan_tablet.session_id),
    }
    assert not any(entry["current"] for entry in body["sessions"])
    _assert_no_secret(response, owner, lan_phone, lan_tablet)


def test_an_owner_the_database_no_longer_knows_is_refused(
    connection: Any, client: TestClient
) -> None:
    """Roles are re-read from the database on every request, so a revoked owner role is refused at
    the route even though the session row survived; the repository's own owner re-read, behind
    it, is proved in `packages/db/tests/test_session_list.py`."""

    owner_id, owner_subject = _person(connection, StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER)
    owner = _device(connection, owner_subject)
    lan_id, lan_subject = _person(connection, StaffRole.OPERATOR)
    _device(connection, lan_subject)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE staff_role_assignments SET revoked_at = now() "
            "WHERE staff_user_id = %s AND role = 'OWNER_ADMIN'",
            (owner_id,),
        )

    response = _get(client, f"/internal/v1/staff/{lan_id}/sessions", owner)

    assert response.status_code == 403


def test_an_unknown_person_is_a_404(connection: Any, client: TestClient) -> None:
    _, owner_subject = _person(connection, StaffRole.OWNER_ADMIN)
    owner = _device(connection, owner_subject)

    response = _get(client, f"/internal/v1/staff/{uuid4()}/sessions", owner)

    assert response.status_code == 404
    assert response.json() == {"detail": "staff user unavailable"}


# --- revoking one device, with the existing route -----------------------------------------------


def test_signing_out_one_device_leaves_the_others_signed_in(
    connection: Any, client: TestClient
) -> None:
    """The lost-phone case for the owner: three devices, one signed out from another."""

    _, subject = _person(connection, StaffRole.OWNER_ADMIN)
    phone = _device(connection, subject)
    tablet = _device(connection, subject)
    laptop = _device(connection, subject)

    assert _revoke(client, phone.session_id, laptop).status_code == 204

    # The lost phone's next request is refused ...
    assert _get(client, "/internal/v1/session", phone).status_code == 401
    # ... and every other device of the same person carries on.
    for survivor in (tablet, laptop):
        assert _get(client, "/internal/v1/session", survivor).status_code == 200
    listed = _get(client, "/internal/v1/sessions", laptop).json()["sessions"]
    assert {entry["session_id"] for entry in listed} == {
        str(tablet.session_id),
        str(laptop.session_id),
    }


def test_the_owner_signs_out_one_of_a_persons_devices_from_their_list(
    connection: Any, client: TestClient
) -> None:
    _, owner_subject = _person(connection, StaffRole.OWNER_ADMIN)
    owner = _device(connection, owner_subject)
    lan_id, lan_subject = _person(connection, StaffRole.OPERATOR)
    lost = _device(connection, lan_subject)
    kept = _device(connection, lan_subject)

    assert _revoke(client, lost.session_id, owner).status_code == 204

    after = _get(client, f"/internal/v1/staff/{lan_id}/sessions", owner).json()["sessions"]
    assert [entry["session_id"] for entry in after] == [str(kept.session_id)]
    assert _get(client, "/internal/v1/session", lost).status_code == 401
    assert _get(client, "/internal/v1/session", kept).status_code == 200
    assert _get(client, "/internal/v1/session", owner).status_code == 200


def test_the_revoke_rules_are_unchanged(connection: Any, client: TestClient) -> None:
    """`SESSION-LIST-001` uses the revoke route as it was: this very session, or any session for
    OWNER_ADMIN. A non-owner may not sign out someone else, and -- the rule the console has to
    show rather than discover -- not even another device of their own: that is the owner's press.
    Signing out an already signed-out device is the route's own 404, as it always was."""

    _, owner_subject = _person(connection, StaffRole.OWNER_ADMIN)
    owner = _device(connection, owner_subject)
    owner_other = _device(connection, owner_subject)
    _, lan_subject = _person(connection, StaffRole.OPERATOR)
    lan = _device(connection, lan_subject)
    lan_other = _device(connection, lan_subject)

    assert _revoke(client, owner.session_id, lan).status_code == 403
    assert _revoke(client, lan_other.session_id, lan).status_code == 403
    assert _get(client, "/internal/v1/session", owner).status_code == 200
    assert _get(client, "/internal/v1/session", lan_other).status_code == 200

    assert _revoke(client, lan.session_id, lan).status_code == 204
    assert _get(client, "/internal/v1/session", lan).status_code == 401
    assert _get(client, "/internal/v1/session", lan_other).status_code == 200

    assert _revoke(client, owner_other.session_id, owner).status_code == 204
    assert _revoke(client, owner_other.session_id, owner).status_code == 404
