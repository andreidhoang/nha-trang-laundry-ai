from datetime import timedelta
from uuid import uuid4

import pytest
from nha_trang_laundry_db.identity import (
    IdentityRepository,
    IdentityStateError,
    _require_owner_survives_disable,
)


class UnusedConnection:
    def transaction(self) -> None:
        raise AssertionError("invalid input must fail before database access")


class OwnerSurvivalCursor:
    def __init__(self, responses: list[tuple[object, ...] | None]) -> None:
        self._responses = iter(responses)

    def execute(self, query: str, params: object | None = None) -> None:
        del query, params

    def fetchone(self) -> tuple[object, ...] | None:
        return next(self._responses)


@pytest.mark.parametrize(
    ("idle", "absolute"),
    [
        (timedelta(), timedelta(hours=1)),
        (timedelta(hours=2), timedelta(hours=1)),
        (timedelta(microseconds=1), timedelta(hours=1)),
    ],
)
def test_invalid_session_lifetimes_fail_before_database_access(
    idle: timedelta, absolute: timedelta
) -> None:
    with pytest.raises(IdentityStateError, match=r"session .*lifetime"):
        IdentityRepository().create_session(
            UnusedConnection(),
            oidc_subject="test-staff-subject",
            mfa_verified=True,
            correlation_id=uuid4(),
            idle_ttl=idle,
            absolute_ttl=absolute,
        )


@pytest.mark.parametrize("token", ["", "not-a-session", "not-a-uuid.secret"])
def test_invalid_session_token_fails_before_database_access(token: str) -> None:
    with pytest.raises(IdentityStateError, match="invalid session"):
        IdentityRepository().authenticate_session(UnusedConnection(), token)


def test_last_active_owner_cannot_be_disabled() -> None:
    cursor = OwnerSurvivalCursor([(True,), None])

    with pytest.raises(IdentityStateError, match="last active owner"):
        _require_owner_survives_disable(cursor, uuid4())


def test_non_owner_can_be_disabled_without_owner_count_query() -> None:
    _require_owner_survives_disable(OwnerSurvivalCursor([(False,)]), uuid4())
