"""`CONSENT-TRANSACTIONAL-001` (`DEC-033`): a STOP stops everything the shop initiates there.

Before this item a manual send -- hardcoded TRANSACTIONAL -- ran no consent or suppression check at
all, `check_egress_allowed` answered REQUIRE_HUMAN for every purpose but MARKETING, and the ingress
STOP wrote a MARKETING row only. A customer who wrote STOP could still be sent a service message.

Every claim here is about rows, locks and transactions, so every test runs against PostgreSQL.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from message_draft_test_data import (
    current_binding,
    publish_test_messaging_policy,
    record_customer_message,
    seed_message_draft,
)
from nha_trang_laundry_contracts import AgentDeploymentStage
from nha_trang_laundry_db.approvals import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
)
from nha_trang_laundry_db.consent_egress import (
    EgressCheck,
    EgressDecision,
    EgressRefusedError,
    check_egress_allowed,
    suppression_lock,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.manual_sends import (
    ManualSendAttestationCommand,
    ManualSendPrepareCommand,
    ManualSendRepository,
)
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.service_messaging import (
    MessagingPolicyAuthorizationError,
    publish_messaging_policy,
)
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_db.transactional_consent import (
    TransactionalConsentAuthorizationError,
    TransactionalConsentNotFoundError,
    TransactionalConsentStateError,
    TransactionalReleaseCommand,
    read_service_messaging_state,
    release_transactional_suppression,
)
from nha_trang_laundry_domain.catalog import ApprovalAction
from nha_trang_laundry_domain.consent import OptOutDisposition
from quote_test_data import accepted_quote

CHANNEL = "INTERNAL_TEST"
AT = datetime(2026, 9, 25, 3, tzinfo=UTC)
WINDOW = timedelta(hours=48)
GRACE = timedelta(hours=72)
TICK = timedelta(microseconds=1)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


# --- fixtures ----------------------------------------------------------------------------------


def _principal(role: StaffRole, *, mfa: bool = True) -> StaffPrincipal:
    return StaffPrincipal(uuid4(), f"consent-{uuid4().hex}", frozenset({role}), mfa, uuid4())


def _store(connection: Any, *members: StaffPrincipal, store_id: UUID | None = None) -> UUID:
    store_id = store_id or uuid4()
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM stores WHERE id = %s", (store_id,))
        exists = cursor.fetchone() is not None
    if not exists:
        StoreRepository.create(
            connection, store_id=store_id, name="Cửa hàng", created_by=None, correlation_id=uuid4()
        )
    moment = datetime.now(UTC)
    with connection.transaction(), connection.cursor() as cursor:
        for member in members:
            cursor.execute(
                """
                INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
                VALUES (%s, %s, 'Nhân viên', 'ACTIVE', %s) ON CONFLICT (id) DO NOTHING
                """,
                (member.staff_user_id, member.oidc_subject, moment),
            )
            cursor.execute(
                """
                INSERT INTO staff_store_assignments (
                    staff_user_id, store_id, assigned_by_staff_id, assigned_at, row_version
                ) VALUES (%s, %s, %s, %s, 1) ON CONFLICT DO NOTHING
                """,
                (member.staff_user_id, store_id, member.staff_user_id, moment),
            )
    return store_id


def _stop(
    connection: Any,
    contact: UUID,
    *,
    at: datetime = AT,
    disposition: OptOutDisposition = OptOutDisposition.WITHDRAW,
    channel: str = CHANNEL,
) -> UUID:
    return record_customer_message(
        connection, contact, received_at=at, channel=channel, disposition=disposition
    )


def _check(connection: Any, contact: UUID, *, at: datetime = AT) -> EgressCheck:
    with connection.transaction(), connection.cursor() as cursor:
        return check_egress_allowed(
            cursor, contact_binding_id=contact, channel=CHANNEL, purpose="TRANSACTIONAL", at=at
        )


def _rows(connection: Any, contact: UUID) -> dict[str, str]:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT purpose, state FROM suppression_entries WHERE contact_binding_id = %s",
            (contact,),
        )
        return {str(row[0]): str(row[1]) for row in cursor.fetchall()}


def _count(connection: Any, statement: str, *values: Any) -> int:
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(statement, values)
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


class _Shop:
    """A store with a draft to a contact, a requester, an approver, a sender and an owner."""

    def __init__(self, connection: Any) -> None:
        self.requester = _principal(StaffRole.OPERATOR)
        self.approver = _principal(StaffRole.OPS_APPROVER)
        self.sender = _principal(StaffRole.OPERATOR)
        self.owner = _principal(StaffRole.OWNER_ADMIN)
        self.store_id = _store(connection, self.requester, self.approver, self.sender, self.owner)
        self.draft = seed_message_draft(connection, self.store_id)
        self.contact = self.draft.contact_binding_id

    def approved(self, connection: Any) -> UUID:
        binding = current_binding(connection, self.draft.agent_run_id)
        repository = ApprovalRepository()
        created = repository.request(
            connection,
            ApprovalRequestCommand(
                ApprovalAction.SEND_MESSAGE,
                "MESSAGE_DRAFT",
                self.draft.agent_run_id,
                binding.resource_version,
                binding.snapshot_hash,
                binding.rendered_hash,
                "manual-send-policy-v1",
                self.requester.staff_user_id,
                f"approval-{uuid4().hex}",
                uuid4(),
                store_id=self.store_id,
            ),
        )
        repository.decide(
            connection,
            ApprovalDecisionCommand(
                created.approval_request_id,
                ApprovalDecision.APPROVED,
                binding.resource_version,
                binding.snapshot_hash,
                binding.rendered_hash,
                "HUMAN_REVIEW_COMPLETE",
                self.approver,
                uuid4(),
            ),
        )
        return created.approval_request_id

    def prepare_command(self, connection: Any, approval_id: UUID) -> ManualSendPrepareCommand:
        binding = current_binding(connection, self.draft.agent_run_id)
        return ManualSendPrepareCommand(
            approval_request_id=approval_id,
            observed_resource_version=binding.resource_version,
            observed_snapshot_hash=binding.snapshot_hash,
            observed_rendered_hash=binding.rendered_hash,
            channel=CHANNEL,
            purpose="TRANSACTIONAL",
            deployment_stage=AgentDeploymentStage.SHADOW,
            principal=self.sender,
            correlation_id=uuid4(),
        )

    def attest_command(self, connection: Any, envelope_id: UUID) -> ManualSendAttestationCommand:
        binding = current_binding(connection, self.draft.agent_run_id)
        return ManualSendAttestationCommand(
            manual_send_envelope_id=envelope_id,
            observed_resource_version=binding.resource_version,
            exact_rendered_hash=binding.rendered_hash,
            principal=self.sender,
            correlation_id=uuid4(),
        )

    def release(
        self,
        connection: Any,
        evidence: UUID,
        *,
        principal: StaffPrincipal | None = None,
        store_id: UUID | None = None,
        contact: UUID | None = None,
        at: datetime | None = None,
    ) -> Any:
        return release_transactional_suppression(
            connection,
            TransactionalReleaseCommand(
                store_id=store_id or self.store_id,
                contact_binding_id=contact or self.contact,
                channel=CHANNEL,
                evidence_webhook_event_id=evidence,
                principal=principal or self.owner,
                correlation_id=uuid4(),
                released_at=at,
            ),
        )


def _order_row(
    connection: Any,
    shop: _Shop,
    *,
    created_at: datetime,
    status: str,
    closed_at: datetime | None = None,
) -> UUID:
    """An order row bound to the shop's contact, in the given commercial state.

    The `bulk_newer_orders` technique: a real accepted quote chain, and a row that satisfies every
    constraint while skipping the transition history no read here looks at.
    """
    quote_id, revision, quote, _ = accepted_quote(
        connection, store_id=shop.store_id, principal=shop.requester
    )
    order_id = uuid4()
    completed = status == "COMPLETED"
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO orders (
                id, store_id, bound_contact_id, current_quote_id, current_quote_revision,
                current_quote_snapshot_hash, commercial_status, intake_status, production_status,
                fulfillment_mode, balance_status, self_collection_recorded,
                customer_final_quote_accepted_at, production_accepted_at, closed_at,
                row_version, created_at, acquisition_source
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'SELF_DROP_SELF_COLLECT', %s, %s,
                      %s, %s, %s, 1, %s, 'WALK_IN')
            """,
            (
                order_id,
                shop.store_id,
                shop.contact,
                quote_id,
                revision,
                quote.document.snapshot_hash,
                status,
                "ACCEPTED" if completed else "AWAITING_HANDOFF",
                "RELEASED" if completed else "NOT_STARTED",
                "PAID" if completed else "UNPAID",
                completed,
                created_at,
                created_at if completed else None,
                closed_at,
                created_at,
            ),
        )
    return order_id


# --- ruling 1: a STOP writes both purposes -----------------------------------------------------


def test_an_exact_stop_suppresses_marketing_and_transactional_in_one_transaction(
    connection: psycopg.Connection[Any],
) -> None:
    contact = uuid4()
    webhook = _stop(connection, contact)

    assert _rows(connection, contact) == {"MARKETING": "SUPPRESSED", "TRANSACTIONAL": "SUPPRESSED"}
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT purpose, event_type, evidence_webhook_id FROM consent_events
            WHERE contact_binding_id = %s ORDER BY purpose
            """,
            (contact,),
        )
        events = [(str(row[0]), str(row[1]), row[2]) for row in cursor.fetchall()]
    assert events == [
        ("MARKETING", "WITHDRAW", webhook),
        ("TRANSACTIONAL", "WITHDRAW", webhook),
    ]


def test_an_ambiguous_opt_out_blocks_both_purposes_pending_review(
    connection: psycopg.Connection[Any],
) -> None:
    contact = uuid4()
    _stop(connection, contact, disposition=OptOutDisposition.PENDING_REVIEW_BLOCKED)

    assert _rows(connection, contact) == {
        "MARKETING": "PENDING_REVIEW_BLOCKED",
        "TRANSACTIONAL": "PENDING_REVIEW_BLOCKED",
    }
    publish_test_messaging_policy(connection)
    refused = _check(connection, contact)
    assert refused.decision is EgressDecision.REQUIRE_HUMAN
    assert refused.reason_code == "PENDING_REVIEW"


def test_a_stop_on_one_channel_leaves_the_other_channel_alone(
    connection: psycopg.Connection[Any],
) -> None:
    contact = uuid4()
    _stop(connection, contact, channel="OTHER_CHANNEL")
    publish_test_messaging_policy(connection)
    record_customer_message(connection, contact, received_at=AT)

    assert _check(connection, contact).decision is EgressDecision.ALLOW


# --- ruling 3: what a service send needs -------------------------------------------------------


def test_a_suppressed_contact_is_suppressed_whatever_the_basis(
    connection: psycopg.Connection[Any],
) -> None:
    contact = uuid4()
    publish_test_messaging_policy(connection)
    _stop(connection, contact, at=AT - timedelta(hours=1))
    record_customer_message(connection, contact, received_at=AT)  # a basis, after the STOP

    result = _check(connection, contact)
    assert result.decision is EgressDecision.SUPPRESSED
    assert result.reason_code == "SUPPRESSED"
    assert result.suppression_state == "SUPPRESSED"


def test_no_published_policy_refuses_every_service_send(
    connection: psycopg.Connection[Any],
) -> None:
    contact = uuid4()
    record_customer_message(connection, contact, received_at=AT)

    result = _check(connection, contact)
    assert result.decision is EgressDecision.REQUIRE_HUMAN
    assert result.reason_code == "MESSAGING_POLICY_UNPUBLISHED"
    assert result.policy_version is None


def test_a_published_policy_without_a_basis_refuses(
    connection: psycopg.Connection[Any],
) -> None:
    publish_test_messaging_policy(connection)

    result = _check(connection, uuid4())
    assert result.decision is EgressDecision.REQUIRE_HUMAN
    assert result.reason_code == "NO_SERVICE_BASIS"
    assert result.suppression_state == "NONE"


def test_the_customer_initiated_window_is_inclusive_at_exactly_its_hours(
    connection: psycopg.Connection[Any],
) -> None:
    publish_test_messaging_policy(connection)
    inside, outside, future = uuid4(), uuid4(), uuid4()
    record_customer_message(connection, inside, received_at=AT - WINDOW)
    record_customer_message(connection, outside, received_at=AT - WINDOW - TICK)
    record_customer_message(connection, future, received_at=AT + TICK)

    allowed = _check(connection, inside)
    assert allowed.decision is EgressDecision.ALLOW
    assert allowed.basis == "CUSTOMER_INITIATED"
    assert allowed.policy_version == 1
    assert _check(connection, outside).reason_code == "NO_SERVICE_BASIS"
    # A message dated after the instant judged is not a basis for it.
    assert _check(connection, future).reason_code == "NO_SERVICE_BASIS"


def test_a_stop_message_is_not_a_customer_initiated_basis(
    connection: psycopg.Connection[Any],
) -> None:
    """The withdrawal is the customer writing, but writing to stop; it grounds nothing."""
    publish_test_messaging_policy(connection)
    shop_contact = uuid4()
    _stop(connection, shop_contact, channel="OTHER_CHANNEL")
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM webhook_events WHERE contact_binding_id = %s", (shop_contact,)
        )
        assert cursor.fetchone() == (1,)
    assert _check(connection, shop_contact).reason_code == "NO_SERVICE_BASIS"


def test_an_open_order_is_a_basis_and_a_cancelled_one_is_not(
    connection: psycopg.Connection[Any],
) -> None:
    publish_test_messaging_policy(connection)
    shop = _Shop(connection)
    _order_row(connection, shop, created_at=AT - timedelta(days=30), status="CANCELLED")
    assert _check(connection, shop.contact).reason_code == "NO_SERVICE_BASIS"

    _order_row(connection, shop, created_at=AT - timedelta(days=30), status="CONFIRMED")
    allowed = _check(connection, shop.contact)
    assert allowed.decision is EgressDecision.ALLOW
    assert allowed.basis == "OPEN_ORDER"
    # Judged before the order existed, it was no basis.
    assert _check(connection, shop.contact, at=AT - timedelta(days=31)).decision is (
        EgressDecision.REQUIRE_HUMAN
    )


def test_a_closed_order_is_a_basis_until_exactly_its_grace_hours(
    connection: psycopg.Connection[Any],
) -> None:
    publish_test_messaging_policy(connection)
    shop = _Shop(connection)
    closed = AT - GRACE
    _order_row(
        connection, shop, created_at=AT - timedelta(days=10), status="COMPLETED", closed_at=closed
    )

    at_edge = _check(connection, shop.contact, at=closed + GRACE)
    assert at_edge.decision is EgressDecision.ALLOW
    assert at_edge.basis == "OPEN_ORDER"
    past = _check(connection, shop.contact, at=closed + GRACE + TICK)
    assert past.reason_code == "NO_SERVICE_BASIS"
    # Before it closed it was open, which is a basis too.
    assert _check(connection, shop.contact, at=closed - TICK).basis == "OPEN_ORDER"


def test_a_policy_publishing_only_one_basis_ignores_the_other(
    connection: psycopg.Connection[Any],
) -> None:
    import json

    from message_draft_test_data import MESSAGING_POLICY_TEMPLATE

    document = json.loads(MESSAGING_POLICY_TEMPLATE.read_text(encoding="utf-8"))
    publish_test_messaging_policy(connection, payload={**document, "bases": ["OPEN_ORDER"]})
    contact = uuid4()
    record_customer_message(connection, contact, received_at=AT)

    assert _check(connection, contact).reason_code == "NO_SERVICE_BASIS"


def test_only_an_active_owner_may_publish_the_policy(
    connection: psycopg.Connection[Any],
) -> None:
    import json

    from message_draft_test_data import MESSAGING_POLICY_TEMPLATE

    document = json.loads(MESSAGING_POLICY_TEMPLATE.read_text(encoding="utf-8"))
    approver = _principal(StaffRole.OPS_APPROVER)
    _store(connection, approver)
    for actor in (approver.staff_user_id, uuid4()):
        with pytest.raises(MessagingPolicyAuthorizationError):
            publish_messaging_policy(connection, actor_id=actor, payload=document)
    assert (
        _count(
            connection,
            "SELECT count(*) FROM configuration_versions WHERE config_type = %s",
            "TRANSACTIONAL_MESSAGING_POLICY",
        )
        == 0
    )


def test_a_transactional_check_without_an_instant_is_refused(
    connection: psycopg.Connection[Any],
) -> None:
    from nha_trang_laundry_db.consent_egress import EgressSuppressionError

    with (
        pytest.raises(EgressSuppressionError),
        connection.transaction(),
        connection.cursor() as cursor,
    ):
        check_egress_allowed(
            cursor, contact_binding_id=uuid4(), channel=CHANNEL, purpose="TRANSACTIONAL"
        )


# --- ruling 4(i): the manual send prepare -----------------------------------------------------


def test_a_refused_prepare_writes_nothing_and_carries_no_content(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    approval_id = shop.approved(connection)
    _stop(connection, shop.contact, at=datetime.now(UTC) - timedelta(minutes=1))
    events_before = _count(connection, "SELECT count(*) FROM domain_events")
    outbox_before = _count(connection, "SELECT count(*) FROM outbox_events")

    with pytest.raises(EgressRefusedError) as refused:
        ManualSendRepository().prepare(connection, shop.prepare_command(connection, approval_id))

    assert refused.value.check.decision is EgressDecision.SUPPRESSED
    assert refused.value.contact_binding_id == shop.contact
    assert refused.value.store_id == shop.store_id
    assert shop.draft.text not in str(refused.value)
    assert _count(connection, "SELECT count(*) FROM manual_send_envelopes") == 0
    assert _count(connection, "SELECT count(*) FROM domain_events") == events_before
    assert _count(connection, "SELECT count(*) FROM outbox_events") == outbox_before


def test_prepare_without_a_published_policy_is_refused_unpublished(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    approval_id = shop.approved(connection)
    record_customer_message(connection, shop.contact, received_at=datetime.now(UTC))

    with pytest.raises(EgressRefusedError) as refused:
        ManualSendRepository().prepare(connection, shop.prepare_command(connection, approval_id))
    assert refused.value.check.reason_code == "MESSAGING_POLICY_UNPUBLISHED"
    assert _count(connection, "SELECT count(*) FROM manual_send_envelopes") == 0


def test_an_allowed_send_records_why_on_both_events(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    record_customer_message(connection, shop.contact, received_at=datetime.now(UTC))
    approval_id = shop.approved(connection)
    manual = ManualSendRepository()
    prepared = manual.prepare(connection, shop.prepare_command(connection, approval_id))
    manual.attest(connection, shop.attest_command(connection, prepared.manual_send_envelope_id))

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT event_type, payload->'egress' FROM domain_events
            WHERE aggregate_type = 'MANUAL_SEND' AND aggregate_id = %s ORDER BY aggregate_version
            """,
            (prepared.manual_send_envelope_id,),
        )
        rows = cursor.fetchall()
    assert [row[0] for row in rows] == ["MANUAL_SEND_APPROVED", "MANUAL_SEND_RECORDED"]
    for _, egress in rows:
        assert egress["purpose"] == "TRANSACTIONAL"
        assert egress["decision"] == "ALLOW"
        assert egress["basis"] == "CUSTOMER_INITIATED"
        assert egress["suppression_state"] == "NONE"
        assert egress["policy_version"] == 1
        assert len(egress["policy_snapshot_hash"]) == 64


# --- ruling 4(ii): the claim-to-send window ---------------------------------------------------


def test_a_stop_after_prepare_refuses_the_attestation(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    record_customer_message(connection, shop.contact, received_at=datetime.now(UTC))
    approval_id = shop.approved(connection)
    manual = ManualSendRepository()
    prepared = manual.prepare(connection, shop.prepare_command(connection, approval_id))

    _stop(connection, shop.contact, at=datetime.now(UTC))

    with pytest.raises(EgressRefusedError) as refused:
        manual.attest(connection, shop.attest_command(connection, prepared.manual_send_envelope_id))
    assert refused.value.check.decision is EgressDecision.SUPPRESSED
    assert _count(connection, "SELECT count(*) FROM manual_send_attestations") == 0
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, row_version FROM manual_send_envelopes WHERE id = %s",
            (prepared.manual_send_envelope_id,),
        )
        assert cursor.fetchone() == ("APPROVED_FOR_MANUAL_SEND", 1)


def test_a_stop_committing_during_a_prepare_waits_for_it_then_blocks_the_attestation(
    connection: psycopg.Connection[Any],
) -> None:
    """Two connections, one advisory key. The prepare holds its transaction open; the STOP waits.

    The prepare saw no STOP and was entitled to lock the envelope. The STOP must not interleave
    with it -- it commits only after the prepare's transaction ends -- and the attestation, which
    re-checks under the same lock, then sees it.
    """
    database_url = os.environ["DATABASE_URL"]
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    record_customer_message(connection, shop.contact, received_at=datetime.now(UTC))
    approval_id = shop.approved(connection)
    command = shop.prepare_command(connection, approval_id)
    # The approval request leaves an implicit transaction open on this connection; the threads
    # below use their own connections and must see what it wrote.
    connection.commit()
    prepared_inside = threading.Event()
    stop_done = threading.Event()
    failures: list[BaseException] = []
    envelope: list[UUID] = []

    def prepare() -> None:
        try:
            with psycopg.connect(database_url) as own, own.transaction():
                stored = ManualSendRepository().prepare(own, command)
                envelope.append(stored.manual_send_envelope_id)
                prepared_inside.set()
                # Held open: the STOP must still be waiting when this sleep ends.
                time.sleep(0.8)
                assert not stop_done.is_set(), "the STOP committed inside the prepare"
        except BaseException as error:
            failures.append(error)
            prepared_inside.set()

    def stop() -> None:
        try:
            prepared_inside.wait(timeout=10)
            with psycopg.connect(database_url) as own:
                _stop(own, shop.contact, at=datetime.now(UTC))
        except BaseException as error:
            failures.append(error)
        finally:
            stop_done.set()

    workers = [threading.Thread(target=prepare), threading.Thread(target=stop)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert not failures, failures
    assert envelope
    assert _rows(connection, shop.contact)["TRANSACTIONAL"] == "SUPPRESSED"
    with pytest.raises(EgressRefusedError):
        ManualSendRepository().attest(connection, shop.attest_command(connection, envelope[0]))


def test_a_prepare_arriving_while_a_stop_is_being_written_waits_and_sees_it(
    connection: psycopg.Connection[Any],
) -> None:
    database_url = os.environ["DATABASE_URL"]
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    record_customer_message(connection, shop.contact, received_at=datetime.now(UTC))
    approval_id = shop.approved(connection)
    command = shop.prepare_command(connection, approval_id)
    # The approval request leaves an implicit transaction open on this connection; the threads
    # below use their own connections and must see what it wrote.
    connection.commit()
    stop_written = threading.Event()
    failures: list[BaseException] = []
    outcome: list[object] = []
    waited: list[float] = []

    def stop() -> None:
        try:
            with psycopg.connect(database_url) as own, own.transaction():
                _stop(own, shop.contact, at=datetime.now(UTC))  # savepoint inside the open one
                stop_written.set()
                time.sleep(0.8)
        except BaseException as error:
            failures.append(error)
            stop_written.set()

    def prepare() -> None:
        try:
            stop_written.wait(timeout=10)
            started = time.monotonic()
            with psycopg.connect(database_url) as own:
                try:
                    ManualSendRepository().prepare(own, command)
                    outcome.append("PREPARED")
                except EgressRefusedError as refused:
                    outcome.append(refused.check.decision)
            waited.append(time.monotonic() - started)
        except BaseException as error:
            failures.append(error)

    workers = [threading.Thread(target=stop), threading.Thread(target=prepare)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert not failures, failures
    assert outcome == [EgressDecision.SUPPRESSED]
    assert waited and waited[0] >= 0.5, waited
    assert _count(connection, "SELECT count(*) FROM manual_send_envelopes") == 0


def test_the_guard_serializes_against_the_stop_writer_on_one_advisory_key(
    connection: psycopg.Connection[Any],
) -> None:
    """The transactional guard takes exactly the key the ingress writer takes."""
    database_url = os.environ["DATABASE_URL"]
    contact = uuid4()
    holding = threading.Event()
    released = threading.Event()

    def hold() -> None:
        with psycopg.connect(database_url) as own, own.transaction(), own.cursor() as cursor:
            suppression_lock(cursor, contact_binding_id=contact, channel=CHANNEL)
            holding.set()
            time.sleep(0.6)
            released.set()

    worker = threading.Thread(target=hold)
    worker.start()
    holding.wait(timeout=10)
    _check(connection, contact)
    assert released.is_set(), "the guard read without waiting for the lock holder"
    worker.join(timeout=10)


# --- ruling 4(iii): the approval request -------------------------------------------------------


def test_raising_a_send_envelope_for_a_suppressed_contact_is_refused_early(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    _stop(connection, shop.contact)

    with pytest.raises(EgressRefusedError) as refused:
        shop.approved(connection)
    assert refused.value.check.decision is EgressDecision.SUPPRESSED
    assert _count(connection, "SELECT count(*) FROM approval_requests") == 0


def test_raising_a_send_envelope_is_not_refused_for_a_basis_that_may_yet_appear(
    connection: psycopg.Connection[Any],
) -> None:
    """Advisory-early: no policy and no basis are the send's refusals, not the envelope's."""
    shop = _Shop(connection)
    assert shop.approved(connection)


# --- ruling 2: release -------------------------------------------------------------------------


def test_a_release_on_a_later_message_from_the_customer_clears_transactional_only(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    now = datetime.now(UTC)
    _stop(connection, shop.contact, at=now - timedelta(hours=2))
    evidence = record_customer_message(
        connection, shop.contact, received_at=now - timedelta(hours=1)
    )
    assert _check(connection, shop.contact, at=now).decision is EgressDecision.SUPPRESSED

    released = shop.release(connection, evidence, at=now)

    assert released.previous_state == "SUPPRESSED"
    assert released.state == "CLEAR"
    assert _rows(connection, shop.contact) == {"MARKETING": "SUPPRESSED", "TRANSACTIONAL": "CLEAR"}
    after = _check(connection, shop.contact, at=now)
    assert after.decision is EgressDecision.ALLOW
    assert after.suppression_state == "CLEAR"
    # Marketing is untouched by it: still suppressed for the marketing guard.
    with connection.transaction(), connection.cursor() as cursor:
        marketing = check_egress_allowed(cursor, contact_binding_id=shop.contact, channel=CHANNEL)
    assert marketing.decision is EgressDecision.SUPPRESSED
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT event_type, purpose, evidence_webhook_id, released_by_staff_id,
                   released_for_store_id
            FROM consent_events WHERE id = %s
            """,
            (released.release_consent_event_id,),
        )
        assert cursor.fetchone() == (
            "RELEASE",
            "TRANSACTIONAL",
            evidence,
            shop.owner.staff_user_id,
            shop.store_id,
        )
        cursor.execute(
            """
            SELECT
              (SELECT count(*) FROM domain_events WHERE aggregate_id = %(id)s
                 AND event_type = 'TRANSACTIONAL_SUPPRESSION_RELEASED'),
              (SELECT count(*) FROM audit_events WHERE aggregate_id = %(id)s
                 AND action = 'CONSENT_TRANSACTIONAL_RELEASE' AND actor_id = %(actor)s),
              (SELECT count(*) FROM outbox_events WHERE aggregate_id = %(id)s
                 AND event_type = 'consent.changed.v1')
            """,
            {"id": released.release_consent_event_id, "actor": shop.owner.staff_user_id},
        )
        assert cursor.fetchone() == (1, 1, 1)


def test_an_approver_may_release_a_pending_review_block(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    now = datetime.now(UTC)
    _stop(
        connection,
        shop.contact,
        at=now - timedelta(hours=2),
        disposition=OptOutDisposition.PENDING_REVIEW_BLOCKED,
    )
    evidence = record_customer_message(
        connection, shop.contact, received_at=now - timedelta(hours=1)
    )

    released = shop.release(connection, evidence, principal=shop.approver, at=now)

    assert released.previous_state == "PENDING_REVIEW_BLOCKED"
    assert _rows(connection, shop.contact) == {
        "MARKETING": "PENDING_REVIEW_BLOCKED",
        "TRANSACTIONAL": "CLEAR",
    }


@pytest.mark.parametrize(
    "who",
    ["operator", "approver_without_mfa", "owner_of_another_store", "stranger"],
)
def test_a_release_needs_an_owner_or_approver_with_mfa_in_this_store(
    connection: psycopg.Connection[Any], who: str
) -> None:
    shop = _Shop(connection)
    now = datetime.now(UTC)
    _stop(connection, shop.contact, at=now - timedelta(hours=2))
    evidence = record_customer_message(
        connection, shop.contact, received_at=now - timedelta(hours=1)
    )
    principal = {
        "operator": shop.sender,
        "approver_without_mfa": StaffPrincipal(
            shop.approver.staff_user_id,
            shop.approver.oidc_subject,
            shop.approver.roles,
            False,
        ),
        "owner_of_another_store": _principal(StaffRole.OWNER_ADMIN),
        "stranger": _principal(StaffRole.OPS_APPROVER),
    }[who]
    if who == "owner_of_another_store":
        _store(connection, principal)

    with pytest.raises(TransactionalConsentAuthorizationError):
        shop.release(connection, evidence, principal=principal, at=now)
    assert _rows(connection, shop.contact)["TRANSACTIONAL"] == "SUPPRESSED"


def test_a_release_naming_an_unknown_store_is_refused_as_non_membership(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    now = datetime.now(UTC)
    _stop(connection, shop.contact, at=now - timedelta(hours=2))
    evidence = record_customer_message(
        connection, shop.contact, received_at=now - timedelta(hours=1)
    )

    with pytest.raises(TransactionalConsentAuthorizationError):
        shop.release(connection, evidence, store_id=uuid4(), at=now)


def test_a_contact_the_store_never_dealt_with_is_not_found(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    stranger = uuid4()
    now = datetime.now(UTC)
    _stop(connection, stranger, at=now - timedelta(hours=2))
    evidence = record_customer_message(connection, stranger, received_at=now - timedelta(hours=1))

    with pytest.raises(TransactionalConsentNotFoundError):
        shop.release(connection, evidence, contact=stranger, at=now)
    assert _rows(connection, stranger)["TRANSACTIONAL"] == "SUPPRESSED"


@pytest.mark.parametrize(
    "evidence_kind", ["before_the_stop", "another_contact", "another_channel", "the_stop_itself"]
)
def test_a_release_is_refused_on_evidence_the_server_cannot_verify(
    connection: psycopg.Connection[Any], evidence_kind: str
) -> None:
    shop = _Shop(connection)
    now = datetime.now(UTC)
    before = record_customer_message(connection, shop.contact, received_at=now - timedelta(hours=3))
    stop = _stop(connection, shop.contact, at=now - timedelta(hours=2))
    evidence = {
        "before_the_stop": before,
        "another_contact": record_customer_message(
            connection, uuid4(), received_at=now - timedelta(hours=1)
        ),
        "another_channel": record_customer_message(
            connection, shop.contact, received_at=now - timedelta(hours=1), channel="OTHER"
        ),
        "the_stop_itself": stop,
    }[evidence_kind]

    with pytest.raises(TransactionalConsentStateError) as refused:
        shop.release(connection, evidence, at=now)
    assert refused.value.reason_code == "RELEASE_EVIDENCE_INVALID"
    assert _rows(connection, shop.contact)["TRANSACTIONAL"] == "SUPPRESSED"
    assert (
        _count(connection, "SELECT count(*) FROM consent_events WHERE event_type = 'RELEASE'") == 0
    )


def test_there_is_nothing_to_release_where_nobody_wrote_stop(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    evidence = record_customer_message(connection, shop.contact, received_at=datetime.now(UTC))

    with pytest.raises(TransactionalConsentStateError) as refused:
        shop.release(connection, evidence)
    assert refused.value.reason_code == "NOTHING_TO_RELEASE"


def test_a_new_stop_after_a_release_suppresses_again(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    now = datetime.now(UTC)
    _stop(connection, shop.contact, at=now - timedelta(hours=3))
    evidence = record_customer_message(
        connection, shop.contact, received_at=now - timedelta(hours=2)
    )
    shop.release(connection, evidence, at=now - timedelta(hours=1))
    # Delivered late: received before the release was recorded. It must still suppress.
    _stop(connection, shop.contact, at=now - timedelta(hours=1, minutes=30))

    assert _check(connection, shop.contact, at=now).decision is EgressDecision.SUPPRESSED


def test_the_read_offers_only_messages_a_release_may_cite(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    publish_test_messaging_policy(connection)
    now = datetime.now(UTC)
    record_customer_message(connection, shop.contact, received_at=now - timedelta(hours=3))
    _stop(connection, shop.contact, at=now - timedelta(hours=2))
    later = record_customer_message(connection, shop.contact, received_at=now - timedelta(hours=1))
    record_customer_message(
        connection, shop.contact, received_at=now - timedelta(minutes=30), channel="OTHER"
    )

    state = read_service_messaging_state(
        connection,
        store_id=shop.store_id,
        contact_binding_id=shop.contact,
        channel=CHANNEL,
        principal=shop.sender,
        at=now,
    )
    assert state.transactional_state == "SUPPRESSED"
    assert state.marketing_state == "SUPPRESSED"
    assert state.releasable is True
    assert [item.webhook_event_id for item in state.evidence] == [later]
    assert state.egress.decision is EgressDecision.SUPPRESSED
    with pytest.raises(TransactionalConsentAuthorizationError):
        read_service_messaging_state(
            connection,
            store_id=shop.store_id,
            contact_binding_id=shop.contact,
            channel=CHANNEL,
            principal=_principal(StaffRole.OPERATOR),
            at=now,
        )


# --- 0053's guard: the database refuses what the repository would ------------------------------


def test_the_database_refuses_a_transactional_clear_without_a_verified_release(
    connection: psycopg.Connection[Any],
) -> None:
    contact = uuid4()
    _stop(connection, contact)
    with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
        connection.execute(
            """
            UPDATE suppression_entries SET state = 'CLEAR', row_version = row_version + 1
            WHERE contact_binding_id = %s AND purpose = 'TRANSACTIONAL'
            """,
            (contact,),
        )


def test_the_database_still_refuses_to_clear_a_marketing_withdrawal(
    connection: psycopg.Connection[Any],
) -> None:
    shop = _Shop(connection)
    now = datetime.now(UTC)
    _stop(connection, shop.contact, at=now - timedelta(hours=2))
    evidence = record_customer_message(
        connection, shop.contact, received_at=now - timedelta(hours=1)
    )
    released = shop.release(connection, evidence, at=now)
    with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
        connection.execute(
            """
            UPDATE suppression_entries
            SET state = 'CLEAR', row_version = row_version + 1, source_consent_event_id = %s
            WHERE contact_binding_id = %s AND purpose = 'MARKETING'
            """,
            (released.release_consent_event_id, shop.contact),
        )


# --- the publish script ------------------------------------------------------------------------


def test_the_publish_script_refuses_anyone_but_an_owner_and_publishes_for_the_owner(
    connection: psycopg.Connection[Any],
) -> None:
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "publish_messaging_policy.py"
    approver = _principal(StaffRole.OPS_APPROVER)
    owner = _principal(StaffRole.OWNER_ADMIN)
    _store(connection, approver, owner)
    with connection.transaction(), connection.cursor() as cursor:
        for member, role in ((approver, "OPS_APPROVER"), (owner, "OWNER_ADMIN")):
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), member.staff_user_id, role, datetime.now(UTC)),
            )

    # The script opens its own connection, so what this one wrote must be committed first.
    connection.commit()

    def run(actor: UUID) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), "--actor-id", str(actor)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ},
            timeout=120,
        )

    refused = run(approver.staff_user_id)
    assert refused.returncode == 3, refused.stderr
    assert "OWNER_ADMIN" in refused.stderr
    published = run(owner.staff_user_id)
    assert published.returncode == 0, published.stderr
    assert "transactional messaging policy published" in published.stdout
    again = run(owner.staff_user_id)
    assert "already in force" in again.stdout
    contact = uuid4()
    record_customer_message(connection, contact, received_at=AT)
    assert _check(connection, contact).decision is EgressDecision.ALLOW
