"""The filmed busy scenario must own the row lock until its assertion is complete."""

import os
import subprocess
import time
import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier

from scripts.workflow_database import hold_order_lock, read_query


def test_container_reader_remains_usable_when_a_url_is_also_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert options == {"capture_output": True, "text": True, "check": True}
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="42\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    assert (
        read_query(
            "SELECT 42",
            database_url="postgresql://not-used",
            psql_container="test-postgres",
            database="synthetic_workflow",
        )
        == "42"
    )
    assert calls == [
        [
            "docker",
            "exec",
            "test-postgres",
            "psql",
            "-U",
            "app",
            "-d",
            "synthetic_workflow",
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            "SELECT 42",
        ]
    ]


@pytest.mark.parametrize("missing_client", [False, True])
def test_sql_failure_is_not_data_and_does_not_expose_the_private_url(
    monkeypatch: pytest.MonkeyPatch, missing_client: bool
) -> None:
    private_url = "postgresql://fixture:private-test-password@localhost/synthetic"

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        del options
        if missing_client:
            raise FileNotFoundError(command[0])
        raise subprocess.CalledProcessError(1, command, stderr="query failed")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="verification database read failed") as raised:
        read_query("SELECT 42", database_url=private_url)
    assert private_url not in str(raised.value)
    assert "private-test-password" not in str(raised.value)


def test_no_database_reader_is_honestly_unavailable() -> None:
    assert read_query("SELECT 42") == ""


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
