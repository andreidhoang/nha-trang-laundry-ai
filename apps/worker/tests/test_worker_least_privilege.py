"""`PLATFORM-SECURITY-009` P1/P5: every worker path, executed as `laundry_worker`, and nothing else.

The worker used to hold the API's grant -- SELECT, INSERT and UPDATE on every table -- and
the deployment hash key. It is the process that will host the model runtime, so it is the
compromise the design plans for, and with that grant it could read every customer's phone
ciphertext and every staff session. These tests pin the other side of the fix as well: a grant
set that is too narrow fails here, on a real migrated database, instead of as `permission denied`
in the shop.

How the role is assumed. With `LAUNDRY_WORKER_DATABASE_URL` set (CI does, after creating the role
with a password) the worker side logs in as the role, exactly as the container does. Otherwise it
connects with `DATABASE_URL` and `SET ROLE laundry_worker`, which makes every privilege check the
role's own. Either way the test asserts `current_user` before trusting a single result.

Seeding (stores, enqueued runs, pending events) is done as the schema owner, because in production
those rows are written by the API; only what the worker process itself executes runs as the worker.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_contracts import AgentToolOperation
from nha_trang_laundry_db import keyed_digest
from nha_trang_laundry_db.agent_runs import (
    AgentRunRepository,
    AgentToolCallLedgerEntry,
    read_agent_run_policy_facts,
    request_fingerprint,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.outbox import OutboxRepository
from nha_trang_laundry_domain.catalog import ActorRole
from nha_trang_laundry_worker import InternalOutboxWorker
from nha_trang_laundry_worker.host import WorkerSettings, WorkerSupervisor
from psycopg import sql
from test_agent_pipeline import enqueue, pipeline

ROOT = Path(__file__).resolve().parents[3]
WORKER = "laundry_worker"
API = "laundry_api"


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / f"{name}.py"
    specification = importlib.util.spec_from_file_location(f"_ps009_{name}", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    return value


def _table_owner(connection: Any) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT tableowner FROM pg_tables WHERE tablename = 'schema_migrations'")
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def ensure_application_roles(connection: Any) -> None:
    """Roles are cluster-wide; a fresh cluster (CI before its role step) may not have them."""

    with connection.cursor() as cursor:
        for role in (API, WORKER):
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))


def apply_grants_as_operator(connection: Any) -> None:
    """Exactly what the runbooks run: `scripts/apply_demo_grants.py`, owner = the schema owner."""

    _load_script("apply_demo_grants").apply_grants(connection, owner=_table_owner(connection))


@contextmanager
def connect_as(role: str) -> Iterator[psycopg.Connection[Any]]:
    login = os.environ.get(f"{role.upper()}_DATABASE_URL")
    if login:
        connection = psycopg.connect(login, autocommit=True)
    else:
        connection = psycopg.connect(_database_url(), autocommit=True)
        connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user"
            )
            assert cursor.fetchone() == (role, False), "the smoke must run as the role itself"
        yield connection
    finally:
        connection.close()


@pytest.fixture
def owner() -> Generator[psycopg.Connection[Any], None, None]:
    with psycopg.connect(_database_url(), autocommit=True) as connection:
        apply_migrations(connection)
        ensure_application_roles(connection)
        apply_grants_as_operator(connection)
        yield connection


@pytest.fixture
def no_hash_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The worker container no longer mounts the key; prove no worker path reaches for it."""

    monkeypatch.delenv(keyed_digest.KEY_VARIABLE, raising=False)
    monkeypatch.delenv(keyed_digest.KEY_FILE_VARIABLE, raising=False)
    monkeypatch.setattr(keyed_digest, "KEY_SECRET_PATH", Path("/nonexistent/ps009/hash_key"))
    keyed_digest.reset()
    try:
        with pytest.raises(keyed_digest.HashKeyUnavailable):
            keyed_digest.load_hash_key()
        yield
    finally:
        keyed_digest.reset()


def _internal_event(connection: Any, *, available_at: datetime) -> UUID:
    event_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO outbox_events (
                id, aggregate_type, aggregate_id, event_type, payload, idempotency_key,
                correlation_id, occurred_at, available_at
            ) VALUES (%s, 'TEST', %s, 'order.state_transitioned.v1', '{}'::jsonb, %s, %s, %s, %s)
            """,
            (event_id, uuid4(), f"ps009:{event_id}", uuid4(), available_at, available_at),
        )
    return event_id


def _one(connection: Any, query: str, *params: object) -> tuple[Any, ...]:
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
    assert row is not None
    return tuple(row)


# --- the worker's own paths, as the worker ------------------------------------------------------


def test_every_internal_outbox_path_runs_as_laundry_worker_without_the_hash_key(
    owner: psycopg.Connection[Any], no_hash_key: None
) -> None:
    """Claim → complete, claim → retry, claim → dead-letter, expired claim → DLQ + event + audit,
    and the supervisor's own probe and recovery cycle, all as `laundry_worker`."""

    base = datetime(2001, 1, 1, tzinfo=UTC)
    completed = _internal_event(owner, available_at=base)
    retried = _internal_event(owner, available_at=base + timedelta(seconds=1))
    dead = _internal_event(owner, available_at=base + timedelta(seconds=2))
    expired = _internal_event(owner, available_at=base + timedelta(seconds=3))

    def fails(_: Any) -> None:
        raise RuntimeError("transient")

    with connect_as(WORKER) as worker:
        # The supervisor's cycle: readiness probe, due recovery of both queues, one claim.
        settings = WorkerSettings(
            database_url="postgresql://unused-by-the-injected-factory",
            worker_internal_outbox_enabled=True,
        )
        supervisor = WorkerSupervisor(
            settings,
            internal_worker=InternalOutboxWorker({"order.state_transitioned.v1": lambda _: None}),
            connection_factory=lambda _url: _Borrowed(worker),
        )
        supervisor.run_cycle()
        snapshot = supervisor.snapshot()
        assert snapshot.last_error_code is None, snapshot
        assert snapshot.internal_outbox_status == "COMPLETED"

        assert (
            InternalOutboxWorker({"order.state_transitioned.v1": fails}).run_once(worker).status
            == "RETRY_SCHEDULED"
        )
        assert InternalOutboxWorker({}).run_once(worker).status == "DEAD"

        repository = OutboxRepository()
        claimed = repository.claim_next_internal(worker, worker_role=ActorRole.OUTBOX_WORKER)
        assert claimed is not None and claimed.event_id == expired
        recovered = repository.recover_expired_internal(
            worker,
            worker_role=ActorRole.OUTBOX_WORKER,
            now=datetime.now(UTC) + timedelta(minutes=5),
        )
        assert recovered == expired

    assert _one(owner, "SELECT status FROM outbox_events WHERE id = %s", completed) == ("SENT",)
    assert _one(owner, "SELECT status, held_reason FROM outbox_events WHERE id = %s", retried) == (
        "PENDING",
        "INTERNAL_HANDLER_TRANSIENT_FAILURE",
    )
    for event_id, error_class in (
        (dead, "HANDLER_NOT_CONFIGURED"),
        (expired, "UNKNOWN_INTERNAL_HANDLER_OUTCOME"),
    ):
        assert _one(
            owner,
            """
            SELECT event.status, dead.last_error_class FROM outbox_events AS event
            JOIN dead_letter_events AS dead ON dead.outbox_event_id = event.id
            WHERE event.id = %s
            """,
            event_id,
        ) == ("DEAD", error_class)
    assert _one(
        owner,
        "SELECT count(*) FROM audit_events WHERE aggregate_id = %s "
        "AND action = 'OUTBOX_EXPIRED_TO_DLQ'",
        expired,
    ) == (1,)


def test_the_agent_pipeline_runs_to_a_reviewable_draft_as_laundry_worker(
    owner: psycopg.Connection[Any], no_hash_key: None
) -> None:
    """Claim, policy facts, runtime, completion and the draft filed for review -- as the worker.

    The policy gate turns any exception in its facts read into `POLICY_STORE_UNAVAILABLE` and a
    failed run, so a missing grant there would not raise; it would quietly fail every run. The
    assertion is therefore on the outcome, not on the absence of an exception.
    """

    queued = enqueue(owner)
    with connect_as(WORKER) as worker:
        result = pipeline().run_cycle(worker, lambda: True)

    assert result.status == "DRAFT_REQUIRES_HUMAN", result
    status, summary = _one(
        owner,
        "SELECT status, result_safe_summary FROM agent_runs WHERE id = %s",
        queued.agent_run_id,
    )
    assert status == "DRAFT_REQUIRES_HUMAN"
    assert "POLICY_STORE_UNAVAILABLE" not in str(summary)
    assert _one(
        owner, "SELECT count(*) FROM agent_drafts WHERE agent_run_id = %s", queued.agent_run_id
    ) == (1,)


def test_the_agent_run_ledger_paths_run_as_laundry_worker(
    owner: psycopg.Connection[Any], no_hash_key: None
) -> None:
    """The repository calls the pipeline makes around a run: policy facts, a tool-call ledger row,
    a failure, and the recovery of a run whose lease expired."""

    failing = enqueue(owner)
    repository = AgentRunRepository()
    with connect_as(WORKER) as worker:
        claimed = repository.claim_next(worker, worker_role=ActorRole.AGENT_RUNNER)
        assert claimed is not None and claimed.agent_run_id == failing.agent_run_id
        facts = read_agent_run_policy_facts(worker, claimed)
        assert facts.has_source_event is False
        now = datetime.now(UTC)
        repository.record_tool_call(
            worker,
            AgentToolCallLedgerEntry(
                agent_run_id=claimed.agent_run_id,
                claim_token=claimed.claim_token,
                sequence_number=1,
                operation=AgentToolOperation.CATALOG_RESOLVE,
                request_fingerprint=request_fingerprint({"locale": "vi-VN"}),
                result_status_code=200,
                result_code="REQUIRE_HUMAN",
                trace_id="tr_ps009",
                safe_summary={"candidate_count": 0},
                started_at=now,
                completed_at=now,
                correlation_id=failing.correlation_id,
            ),
        )
        repository.fail(
            worker,
            agent_run_id=claimed.agent_run_id,
            claim_token=claimed.claim_token,
            failure_code="PS009_SMOKE",
            correlation_id=failing.correlation_id,
        )

    expiring = enqueue(owner)
    with connect_as(WORKER) as worker:
        claimed = repository.claim_next(worker, worker_role=ActorRole.AGENT_RUNNER)
        assert claimed is not None and claimed.agent_run_id == expiring.agent_run_id
        recovered = repository.recover_expired(
            worker,
            worker_role=ActorRole.AGENT_RUNNER,
            now=datetime.now(UTC) + timedelta(minutes=5),
        )
        assert recovered == expiring.agent_run_id

    assert _one(
        owner, "SELECT status, failure_code FROM agent_runs WHERE id = %s", failing.agent_run_id
    ) == ("FAILED", "PS009_SMOKE")
    assert _one(
        owner, "SELECT count(*) FROM agent_tool_calls WHERE agent_run_id = %s", failing.agent_run_id
    ) == (1,)
    assert _one(
        owner, "SELECT status, failure_code FROM agent_runs WHERE id = %s", expiring.agent_run_id
    ) == ("FAILED", "LEASE_EXPIRED")


class _Borrowed:
    """Hand the supervisor an open connection without letting its `with` close it."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def __enter__(self) -> psycopg.Connection[Any]:
        return self._connection

    def __exit__(self, *_: object) -> None:
        return None


# --- and nothing else ---------------------------------------------------------------------------

#: What a compromised worker would reach for first, plus the write and delete shapes. Each must be
#: refused by the database, not merely unused by the code.
REFUSED = (
    "SELECT phone_ciphertext FROM customers",
    "SELECT phone_digest FROM customers",
    "SELECT id FROM customers",
    "SELECT secret_hash FROM staff_sessions",
    "SELECT id FROM staff_users",
    "SELECT id FROM orders",
    "SELECT amount_vnd FROM order_payments",
    "SELECT id FROM order_refunds",
    "SELECT request_hash FROM command_idempotency_records",
    "SELECT payload_hash, provider_event_id FROM webhook_events",
    "SELECT encrypted_payload FROM webhook_event_payloads",
    "SELECT payload FROM domain_events",
    "SELECT details FROM audit_events",
    "SELECT draft_text FROM agent_drafts",
    "SELECT id FROM export_requests",
    "SELECT id FROM stores",
    "INSERT INTO stores (id) VALUES (gen_random_uuid())",
    "UPDATE orders SET id = id",
    "UPDATE customers SET id = id",
    "UPDATE agent_runs SET store_id = store_id",
    "UPDATE outbox_events SET payload = payload",
    "DELETE FROM outbox_events",
    "DELETE FROM agent_runs",
    "TRUNCATE dead_letter_events",
)


@pytest.mark.parametrize("statement", REFUSED)
def test_laundry_worker_is_refused_everything_outside_its_grant(
    owner: psycopg.Connection[Any], statement: str
) -> None:
    with connect_as(WORKER) as worker, pytest.raises(psycopg.errors.InsufficientPrivilege):
        worker.execute(statement)


def test_the_api_role_keeps_its_grant_on_what_the_worker_lost(
    owner: psycopg.Connection[Any],
) -> None:
    """The narrowing is the worker's alone: the API still reads the tables the worker cannot."""

    with connect_as(API) as api:
        for table in ("customers", "staff_sessions", "orders", "order_payments", "stores"):
            api.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))


# --- the operator's scripts agree with the tests ------------------------------------------------


def _verify(database_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/verify_database_grants.py", "--database-url", database_url],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("tamper", "named"),
    (
        ("GRANT SELECT (phone_ciphertext) ON customers TO laundry_worker", "customers"),
        ("GRANT SELECT ON staff_sessions TO laundry_worker", "staff_sessions.SELECT"),
        ("GRANT SELECT (payload_hash) ON webhook_events TO laundry_worker", "webhook_events"),
        ("GRANT UPDATE (store_id) ON agent_runs TO laundry_worker", "agent_runs(store_id).UPDATE"),
        ("REVOKE INSERT ON dead_letter_events FROM laundry_worker", "dead_letter_events.INSERT"),
        (
            # The grant every deployment provisioned before this change carries.
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE ON TABLES TO laundry_worker",
            "default privilege",
        ),
    ),
)
def test_verify_database_grants_names_any_drift_and_a_reapply_repairs_it(
    owner: psycopg.Connection[Any], tamper: str, named: str
) -> None:
    database_url = _database_url()
    clean = _verify(database_url)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "OK   laundry_worker" in clean.stdout

    owner.execute(tamper)
    try:
        drifted = _verify(database_url)
        assert drifted.returncode == 1, drifted.stdout
        assert named in drifted.stdout, drifted.stdout
    finally:
        apply_grants_as_operator(owner)
        # The default-privilege tamper was declared for the session user; clear it the same way.
        owner.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM laundry_worker"
        )

    repaired = _verify(database_url)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr


def test_reapplying_narrows_a_database_provisioned_with_the_old_worker_grant(
    owner: psycopg.Connection[Any],
) -> None:
    """An existing shop's database holds the API-shaped worker grant; the next run must take it
    away, not add to it."""

    table_owner = _table_owner(owner)
    owner.execute("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO laundry_worker")
    owner.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE ON TABLES TO laundry_worker"
        ).format(sql.Identifier(table_owner))
    )
    apply_grants_as_operator(owner)

    with connect_as(WORKER) as worker, pytest.raises(psycopg.errors.InsufficientPrivilege):
        worker.execute("SELECT phone_ciphertext FROM customers")
    verified = _verify(_database_url())
    assert verified.returncode == 0, verified.stdout + verified.stderr
