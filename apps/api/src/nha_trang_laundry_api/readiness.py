"""`/readyz`: can this process serve a request now? `/healthz` only says the process is alive.

`OPS-HARDENING-002`. `/healthz` never touched the database, so the console check read "healthy"
while every request the counter made was failing -- the one check the runbooks call "the console
is the business" was blind to the most likely way the business stops.

**Where each one is used, and why they must not be swapped.**

- `/healthz` stays the container healthcheck. `tls` waits on `api` being healthy, so a readiness
  healthcheck would put the database in front of the TLS listener: a database blip would take the
  console's front door down, and since Docker never restarts a container for being unhealthy it
  would not come back by itself either. Liveness is the right question for "should this container
  be running".
- `/readyz` is what `scripts/check_shop_operations.py --check console` asks, through the TLS proxy
  by the console's own name, because that check stands in for a person at the counter. A 503 there
  is an alert (07:00-21:00, `DEC-025`), not a restart.

The probe is bounded well inside that check's five-second timeout, returns no detail about where the
database is or who connects to it, and is never cached.
"""

from __future__ import annotations

from fastapi import Response, status
from nha_trang_laundry_db.connection import ConnectionTimeouts, connect_with_timeouts

from nha_trang_laundry_api.auth import AuthSettings

#: Two seconds to connect, two to answer `SELECT 1`. The console check gives up at five, so a slow
#: database reads as "not ready" rather than as "the console did not answer".
READINESS_TIMEOUTS = ConnectionTimeouts(
    connect_timeout_seconds=2,
    statement_timeout_ms=2_000,
    lock_timeout_ms=1_000,
    idle_in_transaction_session_timeout_ms=5_000,
)


def _configured_database_url() -> str | None:
    return AuthSettings().database_url


def database_answers(database_url: str | None) -> bool:
    if not database_url:
        return False
    try:
        with connect_with_timeouts(database_url, READINESS_TIMEOUTS, autocommit=True) as connection:
            row = connection.execute("SELECT 1").fetchone()
    except Exception:
        return False
    return row == (1,)


def readyz(response: Response) -> dict[str, str]:
    """Report whether this process can reach its database within two seconds."""

    response.headers["Cache-Control"] = "no-store"
    try:
        database_url = _configured_database_url()
    except Exception:
        database_url = None
    if database_answers(database_url):
        return {"status": "ready"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "unavailable", "dependency": "database"}
