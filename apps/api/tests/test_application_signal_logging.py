"""`OPS-OBSERVABILITY-009` (review P2): every answer the API gives leaves a line the ops check
reads.

The operations check counts 5xx answers, database refusals and browser-boundary rejections from the
API's structured log. That is only as good as the API's own lines, and one path wrote none: an
unhandled exception escaped `correlation_middleware` through `call_next`, so the request that most
needed recording -- a 500, whose outcome the counter cannot know -- left metrics and nothing in the
log. These tests capture what the real app writes and hand it to the real parser, so the writer and
the reader are held to one format.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api import main as api_main
from nha_trang_laundry_api import security as api_security
from nha_trang_laundry_api.main import app, current_principal, get_operations_service
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_observability import SafeStructuredLogger

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
CHECKS = importlib.import_module("check_shop_operations")

OWNER_ID = UUID("00000000-0000-0000-0000-000000000101")


@pytest.fixture
def written(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every structured line the API and its browser boundary write during the test."""

    lines: list[str] = []
    monkeypatch.setattr(api_main, "_LOGGER", SafeStructuredLogger(sink=lines.append))
    monkeypatch.setattr(api_security, "_LOGGER", SafeStructuredLogger(sink=lines.append))
    app.dependency_overrides[current_principal] = lambda: StaffPrincipal(
        OWNER_ID, "owner-test-subject", frozenset({StaffRole.OWNER_ADMIN}), True
    )
    try:
        yield lines
    finally:
        app.dependency_overrides.clear()


class _Exploding:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def queue_recovery_summary(self, *, principal: StaffPrincipal) -> object:
        raise self._error


def _completed(lines: list[str]) -> list[dict[str, object]]:
    import json

    return [
        parsed["fields"]
        for parsed in (json.loads(line) for line in lines)
        if parsed["event"] == "http.request.completed"
    ]


def test_an_unhandled_exception_still_writes_its_500_line(written: list[str]) -> None:
    app.dependency_overrides[get_operations_service] = lambda: _Exploding(RuntimeError("boom"))
    response = TestClient(app, raise_server_exceptions=False).get("/internal/v1/queue-recovery")

    assert response.status_code == 500
    assert _completed(written) == [
        {"method": "GET", "route": "/internal/v1/queue-recovery", "status_code": 500}
    ]
    counts = CHECKS.count_application_signals(written, now=datetime.now(UTC))
    assert counts.server_errors == 1
    # And the line carries no exception text: a driver message can name tables and hosts.
    assert "boom" not in "".join(written)


def test_the_500_line_is_written_when_the_test_client_reraises_too(written: list[str]) -> None:
    app.dependency_overrides[get_operations_service] = lambda: _Exploding(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        TestClient(app).get("/internal/v1/queue-recovery")
    assert [fields["status_code"] for fields in _completed(written)] == [500]


def test_a_database_refusal_is_one_refusal_and_not_a_server_error(written: list[str]) -> None:
    app.dependency_overrides[get_operations_service] = lambda: _Exploding(
        psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    )
    response = TestClient(app).get("/internal/v1/queue-recovery")

    assert response.status_code == 503
    counts = CHECKS.count_application_signals(written, now=datetime.now(UTC))
    assert counts.database_refusals == 1
    assert counts.server_errors == 0


def test_a_browser_boundary_rejection_is_counted(written: list[str]) -> None:
    response = TestClient(app).post(
        "/internal/v1/auth/session",
        headers={"Origin": "https://not-the-console.example"},
        json={},
    )

    assert response.status_code == 403
    counts = CHECKS.count_application_signals(written, now=datetime.now(UTC))
    assert counts.browser_boundary_rejections == 1


def test_an_ordinary_answer_is_liveness_and_nothing_else(written: list[str]) -> None:
    assert TestClient(app).get("/healthz").status_code == 200
    counts = CHECKS.count_application_signals(written, now=datetime.now(UTC))
    assert counts.api_lines_in_liveness_window == 1
    assert (counts.server_errors, counts.database_refusals, counts.browser_boundary_rejections) == (
        0,
        0,
        0,
    )
