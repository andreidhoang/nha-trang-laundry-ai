"""Explicit database coordination for the synthetic browser verifier."""

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg


def read_query(
    query: str, *, database_url: str = "", psql_container: str = "", database: str = ""
) -> str:
    """Read verifier evidence, preferring the supplied container's existing client.

    A direct URL also supports the separate lock/fixture helpers; supplying it must not
    accidentally require a host psql when the caller selected a container for SQL reads.
    A failed read is a failed verification, never an error string mistaken for stored data.
    """
    if psql_container:
        command = [
            "docker",
            "exec",
            psql_container,
            "psql",
            "-U",
            "app",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            query,
        ]
    elif database_url:
        command = ["psql", database_url, "-v", "ON_ERROR_STOP=1", "-tAc", query]
    else:
        return ""
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        # CalledProcessError's command may contain the private DSN. Keep it out of logs.
        raise RuntimeError(
            "verification database read failed; check the selected SQL client"
        ) from None
    return result.stdout.strip()


@contextmanager
def hold_order_lock(database_url: str, order_id: str) -> Iterator[psycopg.Connection[Any]]:
    """Yield only after locking the row; retain it until the caller finishes its assertion.

    A wall-clock sleep can expire while a filmed browser is still opening the payment sheet.
    Bound acquisition and abandoned transactions, but make successful release explicit.
    """
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL idle_in_transaction_session_timeout = '120s'")
        row = connection.execute(
            "SELECT id FROM orders WHERE id = %s FOR UPDATE", (order_id,)
        ).fetchone()
        if row is None:
            raise AssertionError("the busy verifier must lock an existing synthetic order")
        yield connection
        # If the bounded safety timeout fired, fail rather than mistake its release for success.
        connection.execute("SELECT 1")
