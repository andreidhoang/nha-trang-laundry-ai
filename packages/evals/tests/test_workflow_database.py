"""The filmed busy scenario must own the row lock until its assertion is complete."""

import os
import time
import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier

from scripts.workflow_database import hold_order_lock


@pytest.mark.parametrize("raise_in_browser", [False, True])
def test_busy_lock_is_ready_survives_navigation_and_releases_on_exit(
    raise_in_browser: bool,
) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    schema = "verifier_lock_" + uuid.uuid4().hex
    order_id = str(uuid.uuid4())
    with psycopg.connect(database_url, autocommit=True) as setup:
        setup.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
        try:
            setup.execute(
                SQL("CREATE TABLE {}.orders (id uuid PRIMARY KEY)").format(Identifier(schema))
            )
            setup.execute(
                SQL("INSERT INTO {}.orders (id) VALUES (%s)").format(Identifier(schema)),
                (order_id,),
            )
            isolated_url = make_conninfo(database_url, options=f"-c search_path={schema}")
            with psycopg.connect(isolated_url, autocommit=True) as competitor:
                try:
                    with hold_order_lock(isolated_url, order_id):
                        # Readiness is actual lock ownership, not a sleep before assuming it.
                        with pytest.raises(psycopg.errors.LockNotAvailable):
                            competitor.execute("SELECT id FROM orders FOR UPDATE NOWAIT")
                        # Exceed the old 12-second lock lifetime, as filmed navigation can do.
                        if not raise_in_browser:
                            time.sleep(12.1)
                        with pytest.raises(psycopg.errors.LockNotAvailable):
                            competitor.execute("SELECT id FROM orders FOR UPDATE NOWAIT")
                        if raise_in_browser:
                            raise RuntimeError("synthetic browser timeout")
                except RuntimeError as error:
                    assert raise_in_browser and str(error) == "synthetic browser timeout"
                assert competitor.execute("SELECT id FROM orders FOR UPDATE NOWAIT").fetchone()
        finally:
            setup.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))
