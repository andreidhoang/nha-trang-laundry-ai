"""Explicit database coordination for the synthetic browser verifier."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg


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
