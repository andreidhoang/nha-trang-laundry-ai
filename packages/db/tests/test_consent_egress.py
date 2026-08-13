"""CONSENT-STOP-001: STOP means stopped, including for the send already in flight."""

from __future__ import annotations

import os
import threading
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db.consent_egress import (
    EgressDecision,
    check_egress_allowed,
    record_clear_consent,
)
from nha_trang_laundry_db.inbox import (
    EncryptedInboundPayload,
    InboundWebhook,
    InboxRepository,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_domain.consent import OptOutDisposition

NOW = datetime.now(UTC)
CHANNEL = "TEST_CHANNEL"


@pytest.fixture
def postgres_connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as connection:
        apply_migrations(connection)
        yield connection


def _stop(
    connection: psycopg.Connection[Any],
    contact_binding_id: UUID,
    *,
    disposition: OptOutDisposition = OptOutDisposition.WITHDRAW,
) -> None:
    InboxRepository().record(
        connection,
        InboundWebhook(
            provider="TEST_PROVIDER",
            channel_account_id=f"account-{uuid4().hex}",
            provider_event_id=f"event-{uuid4().hex}",
            event_type="MESSAGE",
            channel=CHANNEL,
            payload=EncryptedInboundPayload.from_ciphertext_and_plaintext(
                ciphertext=b"sealed", authenticated_plaintext=b'{"text":"dung"}'
            ),
            opt_out_disposition=disposition,
            contact_binding_id=contact_binding_id,
            opt_out_registry_version="opt-out-v1",
            correlation_id=uuid4(),
            received_at=NOW,
        ),
    )


def _clear(connection: psycopg.Connection[Any], contact_binding_id: UUID) -> None:
    consent_event_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO webhook_events (
                id, provider, channel_account_id, provider_event_id, payload_hash,
                encrypted_payload, event_type, contact_binding_id, channel,
                opt_out_disposition, processing_status, received_at
            ) VALUES (%s, 'TEST_PROVIDER', %s, %s, %s, %s, 'MESSAGE', %s, %s,
                      'NONE', 'DISPATCH_PENDING', %s)
            """,
            (
                (webhook_id := uuid4()),
                f"account-{uuid4().hex}",
                f"event-{uuid4().hex}",
                f"RAW-SHA256-V1:{'a' * 64}",
                b"sealed",
                contact_binding_id,
                CHANNEL,
                NOW,
            ),
        )
        cursor.execute(
            """
            INSERT INTO consent_events (
                id, contact_binding_id, purpose, channel, event_type, evidence_webhook_id,
                occurred_at
            ) VALUES (%s, %s, 'MARKETING', %s, 'WITHDRAW', %s, %s)
            """,
            (consent_event_id, contact_binding_id, CHANNEL, webhook_id, NOW),
        )
        record_clear_consent(
            cursor,
            contact_binding_id=contact_binding_id,
            channel=CHANNEL,
            source_consent_event_id=consent_event_id,
            now=NOW,
        )


def _check(connection: psycopg.Connection[Any], contact_binding_id: UUID) -> EgressDecision:
    with connection.transaction(), connection.cursor() as cursor:
        return check_egress_allowed(
            cursor, contact_binding_id=contact_binding_id, channel=CHANNEL
        ).decision


# --- fail-closed defaults ---------------------------------------------------------------------


def test_an_unknown_contact_is_never_sendable(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Absence of a consent decision is unknown, not permission."""
    assert _check(postgres_connection, uuid4()) is EgressDecision.REQUIRE_HUMAN


def test_an_affirmative_clear_state_permits_a_send(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    contact_binding_id = uuid4()
    _clear(postgres_connection, contact_binding_id)

    assert _check(postgres_connection, contact_binding_id) is EgressDecision.ALLOW


def test_a_withdrawal_suppresses(postgres_connection: psycopg.Connection[Any]) -> None:
    contact_binding_id = uuid4()
    _clear(postgres_connection, contact_binding_id)
    _stop(postgres_connection, contact_binding_id)

    assert _check(postgres_connection, contact_binding_id) is EgressDecision.SUPPRESSED


def test_an_ambiguous_opt_out_requires_a_human_and_suppresses_nothing_silently(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """An ambiguous phrase is not a stop command.

    "dừng giao hàng hôm nay thôi" is not "dừng"; the system must not guess in either direction.
    """
    contact_binding_id = uuid4()
    _clear(postgres_connection, contact_binding_id)
    _stop(
        postgres_connection,
        contact_binding_id,
        disposition=OptOutDisposition.PENDING_REVIEW_BLOCKED,
    )

    assert _check(postgres_connection, contact_binding_id) is EgressDecision.REQUIRE_HUMAN
    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT state FROM suppression_entries
            WHERE contact_binding_id = %s AND purpose = 'MARKETING' AND channel = %s
            """,
            (contact_binding_id, CHANNEL),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == "PENDING_REVIEW_BLOCKED"


def test_a_withdrawal_is_never_undone_by_a_later_clear_write(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    contact_binding_id = uuid4()
    _stop(postgres_connection, contact_binding_id)

    _clear(postgres_connection, contact_binding_id)

    assert _check(postgres_connection, contact_binding_id) is EgressDecision.SUPPRESSED


def test_an_unmodelled_purpose_fails_closed(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    contact_binding_id = uuid4()
    _clear(postgres_connection, contact_binding_id)

    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        result = check_egress_allowed(
            cursor,
            contact_binding_id=contact_binding_id,
            channel=CHANNEL,
            purpose="TRANSACTIONAL_EXPERIMENT",
        )

    assert result.decision is EgressDecision.REQUIRE_HUMAN
    assert result.send_allowed is False


def test_withdrawn_consent_blocks_even_when_the_caller_claims_it_is_active(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Consent state is server-side. A caller asserting consent_active cannot override a record."""
    contact_binding_id = uuid4()
    _stop(postgres_connection, contact_binding_id)

    with postgres_connection.transaction(), postgres_connection.cursor() as cursor:
        result = check_egress_allowed(
            cursor,
            contact_binding_id=contact_binding_id,
            channel=CHANNEL,
            consent_active=True,
        )

    assert result.decision is EgressDecision.SUPPRESSED


# --- the claim-to-send race ---------------------------------------------------------------------


def test_a_stop_arriving_during_an_in_flight_send_is_seen_by_the_send(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """The failure this closes: a STOP committed after the outbox check but before dispatch.

    The sender holds its transaction open across the guard and the dispatch, exactly as a real
    sender must. The STOP is issued concurrently. Whichever order the database picks, the send never
    dispatches after a committed withdrawal: either the guard sees SUPPRESSED, or the STOP waits for
    the send transaction and the send was already permitted at the moment it was decided.
    """
    database_url = os.environ["DATABASE_URL"]
    contact_binding_id = uuid4()
    _clear(postgres_connection, contact_binding_id)

    guard_ready = threading.Event()
    stop_committed = threading.Event()
    decisions: list[EgressDecision] = []
    failures: list[BaseException] = []

    def send() -> None:
        try:
            with (
                psycopg.connect(database_url) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                guard_ready.set()
                # The STOP is issued now; the guard must serialize against it.
                stop_committed.wait(timeout=5)
                decisions.append(
                    check_egress_allowed(
                        cursor, contact_binding_id=contact_binding_id, channel=CHANNEL
                    ).decision
                )
        except BaseException as error:  # surfaced by the assertion below
            failures.append(error)

    def stop() -> None:
        try:
            guard_ready.wait(timeout=5)
            with psycopg.connect(database_url) as connection:
                _stop(connection, contact_binding_id)
        except BaseException as error:
            failures.append(error)
        finally:
            stop_committed.set()

    workers = [threading.Thread(target=send), threading.Thread(target=stop)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert not failures, failures
    assert decisions == [EgressDecision.SUPPRESSED]
    assert _check(postgres_connection, contact_binding_id) is EgressDecision.SUPPRESSED


def test_the_guard_and_a_concurrent_stop_never_both_proceed_unserialized(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    """Both sides take the same advisory key, so one always waits for the other."""
    database_url = os.environ["DATABASE_URL"]
    contact_binding_id = uuid4()
    _clear(postgres_connection, contact_binding_id)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def racer(role: str) -> None:
        with psycopg.connect(database_url) as connection:
            barrier.wait(timeout=10)
            if role == "send":
                with connection.transaction(), connection.cursor() as cursor:
                    decision = check_egress_allowed(
                        cursor, contact_binding_id=contact_binding_id, channel=CHANNEL
                    ).decision
                with lock:
                    outcomes.append(f"send:{decision.value}")
            else:
                _stop(connection, contact_binding_id)
                with lock:
                    outcomes.append("stop:committed")

    workers = [threading.Thread(target=racer, args=(role,)) for role in ("send", "stop")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert len(outcomes) == 2
    assert "stop:committed" in outcomes
    # The send either ran before the STOP committed (ALLOW) or after it (SUPPRESSED). It is never
    # a torn read, and the terminal state is always suppressed.
    assert {"send:ALLOW", "send:SUPPRESSED"} & set(outcomes)
    assert _check(postgres_connection, contact_binding_id) is EgressDecision.SUPPRESSED


def test_ingress_suppression_and_its_audit_commit_together(
    postgres_connection: psycopg.Connection[Any],
) -> None:
    contact_binding_id = uuid4()

    _stop(postgres_connection, contact_binding_id)

    with postgres_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              (SELECT count(*) FROM suppression_entries WHERE contact_binding_id = %(contact)s),
              (SELECT count(*) FROM consent_events WHERE contact_binding_id = %(contact)s)
            """,
            {"contact": contact_binding_id},
        )
        row = cursor.fetchone()
    assert row is not None
    assert cast(tuple[int, int], row) == (1, 1)
