"""The one place an application connection is opened, and the bounds every one of them carries.

`OPS-HARDENING-002`. There was no `connect_timeout`, `statement_timeout` or `lock_timeout` anywhere,
and every request opened its own `psycopg.connect`. So one request holding a row lock -- or one
migration queued for an `ACCESS EXCLUSIVE` lock behind it -- made every later request wait without
bound, each holding a connection, until PostgreSQL ran out of them and the counter could not take an
order. Nothing timed out, so nothing said what had happened.

Every bound here is a *session* setting passed at connect time, so it covers every statement the
repositories run without any of them having to remember, and it cannot be lost by a code path that
forgets a `SET`. A DSN's own `options` are appended after these, so an operator's explicit override
wins -- but only an explicit one: absent configuration is bounded, and "unbounded" is not a value
this module will produce for the application.

What a timeout costs is a failed request, and the transaction it was in rolls back whole: the
repositories commit a mutation with its event, audit and outbox rows in one transaction, so a
cancelled statement leaves nothing half-written (invariant 5). A failed request is visible and
retryable; a hung one is neither. `API-INTEGRITY-003` makes the API say so: a cancelled statement,
a lock not granted in time and a connection that could not be opened answer 503 with `Retry-After`
and a reason code, instead of the 500 that tells a counter not to retry.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict

#: Bounds on what an operator may configure. A value outside them is a typo or a bad idea, and
#: either way it refuses rather than falling back to a default nobody chose.
MAX_CONNECT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class ConnectionTimeouts:
    """Session bounds for one kind of connection. Zero means "no limit" and is explicit."""

    connect_timeout_seconds: int
    statement_timeout_ms: int
    lock_timeout_ms: int
    idle_in_transaction_session_timeout_ms: int

    def session_options(self) -> str:
        return (
            f"-c statement_timeout={self.statement_timeout_ms} "
            f"-c lock_timeout={self.lock_timeout_ms} "
            f"-c idle_in_transaction_session_timeout={self.idle_in_transaction_session_timeout_ms}"
        )


#: A staff console request. Fifteen seconds is two orders of magnitude above the slowest query a
#: one-shop database runs, and below the point at which staff give up and press again. Five seconds
#: for a lock rides out a concurrent write to the same order and is short enough that a
#: stuck one fails the request instead of the shop. A transaction left open and idle for a minute is
#: a leak, and it would otherwise hold its locks for ever.
APPLICATION_TIMEOUTS_DEFAULT = ConnectionTimeouts(
    connect_timeout_seconds=5,
    statement_timeout_ms=15_000,
    lock_timeout_ms=5_000,
    idle_in_transaction_session_timeout_ms=60_000,
)

#: Migrations. A data migration may legitimately run for minutes, so no statement bound. What it may
#: not do is *wait* for a lock: an `ALTER TABLE` queued behind a running request holds its place in
#: the lock queue, and every request that arrives after it queues behind the `ALTER` -- so one slow
#: request plus one migration stops the counter. Five seconds, then that migration's transaction
#: rolls back whole, `migrate` exits non-zero, and `up` stops before the new API starts. Re-running
#: it is safe: applied migrations are recorded and skipped.
MIGRATION_LOCK_TIMEOUT_MS_DEFAULT = 5_000

_APPLICATION_VARIABLES = (
    "DATABASE_CONNECT_TIMEOUT_SECONDS",
    "DATABASE_STATEMENT_TIMEOUT_MS",
    "DATABASE_LOCK_TIMEOUT_MS",
    "DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_MS",
)


def _integer(
    environment: Mapping[str, str], name: str, default: int, *, maximum: int, allow_zero: bool
) -> int:
    raw = environment.get(name)
    if raw is None or raw.strip() == "":
        return default
    text = raw.strip()
    if not text.isdigit():
        raise ValueError(f"{name} must be a whole number, not {raw!r}")
    value = int(text)
    if value == 0 and not allow_zero:
        raise ValueError(f"{name} must be positive: 0 would mean no limit at all")
    if value > maximum:
        raise ValueError(f"{name}={value} is above the maximum of {maximum}")
    return value


def application_timeouts(environment: Mapping[str, str] | None = None) -> ConnectionTimeouts:
    """The API, worker and tool-facade bounds: defaults, overridden by `DATABASE_*` variables.

    Every one of them must be positive. Zero is PostgreSQL's "no limit", which is the defect.
    """

    source = os.environ if environment is None else environment
    connect, statement, lock, idle = _APPLICATION_VARIABLES
    default = APPLICATION_TIMEOUTS_DEFAULT
    return ConnectionTimeouts(
        connect_timeout_seconds=_integer(
            source,
            connect,
            default.connect_timeout_seconds,
            maximum=MAX_CONNECT_TIMEOUT_SECONDS,
            allow_zero=False,
        ),
        statement_timeout_ms=_integer(
            source,
            statement,
            default.statement_timeout_ms,
            maximum=MAX_TIMEOUT_MS,
            allow_zero=False,
        ),
        lock_timeout_ms=_integer(
            source, lock, default.lock_timeout_ms, maximum=MAX_TIMEOUT_MS, allow_zero=False
        ),
        idle_in_transaction_session_timeout_ms=_integer(
            source,
            idle,
            default.idle_in_transaction_session_timeout_ms,
            maximum=MAX_TIMEOUT_MS,
            allow_zero=False,
        ),
    )


def migration_lock_timeout_ms(environment: Mapping[str, str] | None = None) -> int:
    """How long one migration may wait for a lock: `MIGRATION_LOCK_TIMEOUT_MS`, default 5000."""

    return _integer(
        os.environ if environment is None else environment,
        "MIGRATION_LOCK_TIMEOUT_MS",
        MIGRATION_LOCK_TIMEOUT_MS_DEFAULT,
        maximum=MAX_TIMEOUT_MS,
        allow_zero=False,
    )


class DatabaseUnavailableError(psycopg.OperationalError):
    """No connection could be opened: nothing reached the database, so nothing was written.

    `API-INTEGRITY-003`. psycopg raises one `OperationalError` both for a connection that could not
    be opened and for one lost in the middle of a transaction, and those are opposite answers to
    the only question a caller has -- *did my write land?* A refused connection certainly did not;
    a connection dropped around a `COMMIT` may have. The API tells a counter "try again" for the
    first and "check the board first" for the second, so the difference is made here, where it is
    still known, rather than guessed at from an exception message later.

    A subclass, so every existing `except psycopg.OperationalError` -- the migration job's startup
    wait, the readiness probe -- behaves exactly as before. The server's own message is kept only as
    the cause: it names the host and the role, and this exception's text may reach a log line.
    """


def connect_with_timeouts(
    conninfo: str, timeouts: ConnectionTimeouts, **kwargs: Any
) -> psycopg.Connection[Any]:
    """`psycopg.connect` with these bounds, keeping anything the DSN itself says after them.

    A connection that cannot be opened -- refused, unreachable within `connect_timeout`, out of
    slots, still starting up -- raises `DatabaseUnavailableError`.
    """

    given = conninfo_to_dict(conninfo)
    options = timeouts.session_options()
    if given.get("options"):
        options = f"{options} {given['options']}"
    connect_timeout = int(given.get("connect_timeout") or timeouts.connect_timeout_seconds)
    try:
        return psycopg.connect(conninfo, connect_timeout=connect_timeout, options=options, **kwargs)
    except psycopg.OperationalError as error:
        raise DatabaseUnavailableError("the database connection could not be opened") from error


def application_connect(conninfo: str, **kwargs: Any) -> psycopg.Connection[Any]:
    """The default `connection_factory` for every application service.

    Reads the environment on each call, which costs nothing next to opening a connection and means
    a changed setting needs a restart of nothing but the container that reads it.
    """

    return connect_with_timeouts(conninfo, application_timeouts(), **kwargs)
