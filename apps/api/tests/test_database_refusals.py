"""`API-INTEGRITY-003`: a database that is busy or unreachable answers 503, and nothing else does.

`OPS-HARDENING-002` bounded every application connection -- `statement_timeout`, `lock_timeout`,
`connect_timeout` -- and the staging review recorded what happened when one fired: the exception
escaped the route and the API answered a generic 500. The console renders a 500 as "Máy chủ gặp
lỗi. Đừng thử lại", which is the right advice when nobody knows whether a write landed and the
wrong advice here, where the server does know.

These tests need no database: the database-side refusals are raised by a stub service, and the
unreachable database is a real `psycopg.connect` to a port nothing listens on. The half that proves
the rolled-back transaction and the safe same-key retry runs against PostgreSQL in
`test_api_integrity_003_postgres.py` (timeouts) and `test_api_integrity_004_postgres.py` (a real
deadlock, which `API-INTEGRITY-004` moved from the 500 side to the 503 side).

The negative half matters as much as the positive one. `IntegrityError` and `ProgrammingError` are a
refused write and a defect; an `OperationalError` from a connection lost mid-transaction may sit on
either side of a `COMMIT`. None of those is "busy", and a 503 would tell a client to retry into it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.auth import AuthSettings
from nha_trang_laundry_api.main import (
    DATABASE_BUSY_RETRY_AFTER_SECONDS,
    DATABASE_UNAVAILABLE_RETRY_AFTER_SECONDS,
    app,
    current_principal,
    get_operations_service,
)
from nha_trang_laundry_api.operations import OperationsService
from nha_trang_laundry_db.connection import DatabaseUnavailableError, application_connect
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole

APPROVER = StaffPrincipal(
    uuid4(), "refusal-approver", frozenset({StaffRole.OPS_APPROVER}), True, uuid4()
)


class _RaisingService:
    """An operations service whose queue read fails with exactly the error it was given."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def list_pending_approvals(self, *, principal: StaffPrincipal, limit: int) -> Any:
        del principal, limit
        raise self._error


@contextmanager
def _client(service: object) -> Iterator[TestClient]:
    app.dependency_overrides[current_principal] = lambda: APPROVER
    app.dependency_overrides[get_operations_service] = lambda: service
    try:
        # Without `raise_server_exceptions=False` TestClient re-raises the unhandled ones, and a
        # 500 could not be told apart from a test error -- the difference this module is about.
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "error",
    [
        psycopg.errors.QueryCanceled("canceling statement due to statement timeout"),
        psycopg.errors.LockNotAvailable("canceling statement due to lock timeout"),
        # `API-INTEGRITY-004`: PostgreSQL aborted this transaction whole to let another finish.
        psycopg.errors.DeadlockDetected("deadlock detected"),
        psycopg.errors.SerializationFailure("could not serialize access"),
    ],
    ids=["statement_timeout", "lock_timeout", "deadlock", "serialization_failure"],
)
def test_a_timed_out_statement_answers_503_with_retry_after_and_a_reason_code(
    error: BaseException,
) -> None:
    with _client(_RaisingService(error)) as client:
        response = client.get("/internal/v1/approvals")

    assert response.status_code == 503, response.text
    assert response.headers["Retry-After"] == str(DATABASE_BUSY_RETRY_AFTER_SECONDS)
    assert response.json() == {"detail": {"reason_code": "DATABASE_BUSY"}}
    # The driver's sentence names tables and settings; none of it reaches the client.
    assert "timeout" not in response.text
    assert "deadlock" not in response.text
    assert "serialize" not in response.text


def test_a_database_that_cannot_be_reached_answers_503_database_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real connection attempt against a port nothing listens on, through the real factory."""

    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "2")
    service = OperationsService(
        AuthSettings(database_url="postgresql://nobody:nothing@127.0.0.1:1/none")
    )
    with _client(service) as client:
        response = client.get("/internal/v1/approvals")

    assert response.status_code == 503, response.text
    assert response.headers["Retry-After"] == str(DATABASE_UNAVAILABLE_RETRY_AFTER_SECONDS)
    assert response.json() == {"detail": {"reason_code": "DATABASE_UNAVAILABLE"}}
    # Neither the host nor the role in the DSN is echoed back.
    assert "127.0.0.1" not in response.text
    assert "nobody" not in response.text


def test_the_connection_factory_says_which_failure_it_was() -> None:
    """A refused connection is `DatabaseUnavailableError`, and still an `OperationalError`.

    The subclass is what lets the API tell "never connected" from "lost mid-transaction" -- psycopg
    raises the same class for both -- and the parent is what keeps the migration job's startup
    wait and every other existing `except psycopg.OperationalError` working unchanged.
    """

    with pytest.raises(DatabaseUnavailableError) as raised:
        application_connect("postgresql://nobody:nothing@127.0.0.1:1/none")
    assert isinstance(raised.value, psycopg.OperationalError)
    assert isinstance(raised.value.__cause__, psycopg.OperationalError)
    assert "127.0.0.1" not in str(raised.value)


@pytest.mark.parametrize(
    "error",
    [
        # A refused write: the constraint said no, and it will say no again.
        psycopg.errors.UniqueViolation("duplicate key value violates unique constraint"),
        psycopg.errors.CheckViolation("new row violates check constraint"),
        # A defect: retrying cannot fix a query that names a table that is not there.
        psycopg.errors.UndefinedTable('relation "nothing" does not exist'),
        # A connection lost mid-transaction. The COMMIT may have landed; "try again" is a guess.
        psycopg.OperationalError("server closed the connection unexpectedly"),
        # 2026-09-25, `API-INTEGRITY-004`: the `DeadlockDetected` case that stood here asserting 500
        # was reversed deliberately and moved to the 503 test above. A deadlock is PostgreSQL
        # aborting the transaction whole, so the outcome is known and the same-key retry is safe --
        # proven against a real deadlock in `test_api_integrity_004_postgres.py`. Its siblings in
        # SQLSTATE class 40 are not, and they take its place here so the class is never mapped
        # wholesale: 40003 is literally "statement completion unknown", and 40002 is a constraint
        # refusing the write at commit.
        psycopg.errors.StatementCompletionUnknown("statement completion unknown"),
        psycopg.errors.TransactionIntegrityConstraintViolation("integrity constraint violation"),
    ],
    ids=[
        "unique",
        "check",
        "undefined_table",
        "connection_lost",
        "completion_unknown",
        "transaction_integrity",
    ],
)
def test_integrity_and_programming_errors_are_never_answered_as_busy(
    error: BaseException,
) -> None:
    with _client(_RaisingService(error)) as client:
        response = client.get("/internal/v1/approvals")

    assert response.status_code == 500, response.text
    assert "Retry-After" not in response.headers
    assert "DATABASE_" not in response.text
