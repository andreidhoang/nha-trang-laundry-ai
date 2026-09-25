"""`OPS-HARDENING-002` item 1: `/healthz` said the console was fine while its database was gone.

`/healthz` answers from the process alone, and it has to: the container healthcheck calls it and
`tls` waits on that, so tying it to the database would take the console's front door down on a
database blip -- and Docker has no restart for "unhealthy", so it would not even come back by
itself. `/readyz` is the other question, "can this process serve a request now", and it is what the
console check asks on the shop's behalf.
"""

from __future__ import annotations

import os
import socket
import time
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from nha_trang_laundry_api.main import app

POSTGRES_SKIP_REASON = "DATABASE_URL is required for PostgreSQL integration tests"


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_readiness_is_ready_when_the_database_answers() -> None:
    if os.environ.get("DATABASE_URL") is None:
        pytest.skip(POSTGRES_SKIP_REASON)

    response = TestClient(app).get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers["Cache-Control"] == "no-store"


def test_readiness_is_503_when_the_database_does_not_and_liveness_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = f"postgresql://laundry_api:s3cret@127.0.0.1:{_closed_port()}/nha_trang_laundry"
    monkeypatch.setenv("DATABASE_URL", dsn)
    client = TestClient(app)

    started = time.monotonic()
    ready = client.get("/readyz")
    assert time.monotonic() - started < 5

    assert ready.status_code == 503
    assert ready.json() == {"status": "unavailable", "dependency": "database"}
    # Nothing about where the database is or who connects to it leaves the process.
    assert "s3cret" not in ready.text and "127.0.0.1" not in ready.text
    assert ready.headers["Cache-Control"] == "no-store"

    # And the process itself is fine, which is what the container healthcheck must keep saying.
    live = client.get("/healthz")
    assert live.status_code == 200 and live.json() == {"status": "ok"}


def test_readiness_without_a_configured_database_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("nha_trang_laundry_api.readiness._configured_database_url", lambda: None)

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "dependency": "database"}


def test_the_probe_is_bounded_well_inside_a_checker_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console check gives up after five seconds; the probe must answer before that."""

    captured: list[dict[str, Any]] = []

    def _connect(_conninfo: str, **kwargs: Any) -> Any:
        captured.append(kwargs)
        raise psycopg.OperationalError("refused")

    monkeypatch.setattr(psycopg, "connect", _connect)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@db/x")

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert captured and captured[0]["connect_timeout"] <= 2
    assert "-c statement_timeout=2000" in captured[0]["options"]
