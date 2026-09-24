"""`OPS-BOARD-001`: the shop's own records leave only with an approval and a trail.

`ApprovalAction.EXPORT_SANITIZED_DATA` and the `EXPORT_REQUEST` resource type were vocabulary with
nothing behind them. This module pins the four properties that make the thing behind them safe:

* an export with no approval, a stale one, or one bound to a different document is refused by name;
* an approved export carries order, money and status facts and **no** incident free text — asserted
  by searching the produced bytes for a complaint this module wrote first, not by reading the SQL;
* the audit event names the approval that authorised the release, atomically with the record;
* one approval releases one file.

The sanitisation assertion is the load-bearing one. `customer_incident_evidence` exists so the
retention schedule can dispose of a customer's words at 365 days; an exported copy escapes that
schedule entirely, which is why the word in the action's name is a requirement rather than a label.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from nha_trang_laundry_db import exports
from nha_trang_laundry_db.approvals import (
    ApprovalAuthorizationError,
    ApprovalDecision,
    ApprovalDecisionCommand,
    ApprovalRepository,
    ApprovalRequestCommand,
    ApprovalStateError,
)
from nha_trang_laundry_db.exports import (
    EXPORT_COLUMNS,
    EXPORT_EXCLUSIONS,
    EXPORT_QUERY,
    ExportAuthorizationError,
    ExportDataset,
    ExportExecutionCommand,
    ExportRequestCommand,
    ExportStateError,
    SanitizedExportRepository,
)
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.incidents import IncidentRepository, StaffIncidentOpenCommand
from nha_trang_laundry_db.migrations import apply_migrations
from nha_trang_laundry_db.orders import (
    CreateOrderCommand,
    OrderRepository,
    OrderTransitionCommand,
)
from nha_trang_laundry_db.query_version import query_version
from nha_trang_laundry_db.shadow_console import ShadowConsoleRepository
from nha_trang_laundry_db.stores import StoreRepository
from nha_trang_laundry_domain.approvals import APPROVAL_RESOURCE_TYPES
from nha_trang_laundry_domain.catalog import (
    AcquisitionSource,
    ApprovalAction,
    FulfillmentMode,
    IntakeStatus,
)
from nha_trang_laundry_domain.orders import IntakeReadiness
from quote_test_data import accepted_quote

#: The words a customer used, which must never reach a file. Distinctive on purpose: a substring
#: search for it over the produced bytes is only meaningful if it could not appear by accident.
COMPLAINT = "áo sơ mi trắng bị ố vàng ở cổ, khách báo sáng nay — CHUOI-RIENG-9137"

READY = IntakeReadiness(True, True, True, True, True, True)


@pytest.fixture
def connection() -> Generator[psycopg.Connection[Any], None, None]:
    database_url = os.environ.get("DATABASE_URL")
    if database_url is None:
        pytest.skip("DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(database_url) as established:
        apply_migrations(established)
        yield established


def _staff(connection: Any, *, roles: frozenset[StaffRole], now: datetime) -> StaffPrincipal:
    staff_user_id = uuid4()
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO staff_users (id, oidc_subject, display_name, status, created_at)
            VALUES (%s, %s, 'Nhân viên thử nghiệm', 'ACTIVE', %s)
            """,
            (staff_user_id, f"oidc-{staff_user_id}", now),
        )
        for role in roles:
            cursor.execute(
                """
                INSERT INTO staff_role_assignments (id, staff_user_id, role, assigned_at)
                VALUES (%s, %s, %s, %s)
                """,
                (uuid4(), staff_user_id, role.value, now),
            )
    return StaffPrincipal(
        staff_user_id=staff_user_id,
        oidc_subject=f"oidc-{staff_user_id}",
        roles=roles,
        mfa_verified=True,
        session_id=uuid4(),
    )


class _Shop:
    """One store, two owners, a requester — the cast an export needs.

    Two owners rather than one, because `_OWNER_FINANCIAL` carries SEPARATION_OF_DUTY and the
    decision path refuses a decision from the staff member who defined the action. A fixture with a
    single owner could not approve anything, which is the shape a real single-owner shop is in and
    is recorded in the module docstring rather than worked around here.

    `second_owner` exists for the separation-of-duty pair below and has to be an `OWNER_ADMIN`
    rather than a second `OPS_APPROVER`: `_OWNER_FINANCIAL` names `OWNER_ADMIN` as the required
    role, so an approver-roled account is turned away for a reason that has nothing to do with the
    rule under test and would make the refusal prove nothing.
    """

    def __init__(self, connection: Any, now: datetime) -> None:
        self.now = now
        self.store_id = uuid4()
        StoreRepository.create(
            connection,
            store_id=self.store_id,
            name="Cửa hàng thử nghiệm",
            created_by=None,
            correlation_id=uuid4(),
        )
        self.owner = _staff(connection, roles=frozenset({StaffRole.OWNER_ADMIN}), now=now)
        self._assign(connection, self.owner.staff_user_id)
        self.second_owner = _staff(connection, roles=frozenset({StaffRole.OWNER_ADMIN}), now=now)
        self._assign(connection, self.second_owner.staff_user_id)
        self.requester = _staff(connection, roles=frozenset({StaffRole.OPS_APPROVER}), now=now)
        self._assign(connection, self.requester.staff_user_id)

    def _assign(self, connection: Any, staff_user_id: UUID) -> None:
        granter = self.owner if hasattr(self, "owner") else None
        if granter is None:
            # The first grant is made by the owner themselves, which
            # `test_store_assignment.py` records as the deliberate answer to "may an owner assign
            # into a store they are not yet a member of".
            granter = _staff(connection, roles=frozenset({StaffRole.OWNER_ADMIN}), now=self.now)
        ShadowConsoleRepository.assign_store(
            connection,
            staff_user_id=staff_user_id,
            store_id=self.store_id,
            principal=granter,
            correlation_id=uuid4(),
        )


def _order_with_complaint(connection: Any, shop: _Shop) -> UUID:
    """An order of the shop's day, with a customer complaint recorded against it."""
    quote_id, revision, quote, contact_id = accepted_quote(
        connection, store_id=shop.store_id, principal=shop.owner
    )
    stored = OrderRepository().create(
        connection,
        CreateOrderCommand(
            shop.store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            FulfillmentMode.SELF_DROP_SELF_COLLECT,
            shop.owner,
            f"order-{uuid4().hex}",
            uuid4(),
            shop.now,
            AcquisitionSource.WALK_IN,
        ),
    )
    version = stored.row_version
    for step in (
        {"intake_target": IntakeStatus.RECEIVED_PENDING_INSPECTION},
        {
            "intake_target": IntakeStatus.ACCEPTED,
            "production_accepted_at": shop.now,
            "intake_readiness": READY,
        },
    ):
        moved = OrderRepository().transition(
            connection,
            OrderTransitionCommand(
                stored.order_id,
                version,
                shop.owner,
                f"step-{uuid4().hex}",
                uuid4(),
                occurred_at=shop.now,
                **step,
            ),
        )
        version = moved.row_version

    IncidentRepository().open_from_counter(
        connection,
        StaffIncidentOpenCommand(
            store_id=shop.store_id,
            order_id=stored.order_id,
            evidence_summary=COMPLAINT,
            actor_id=shop.owner.staff_user_id,
            correlation_id=uuid4(),
            opened_at=shop.now,
        ),
        principal=shop.owner,
    )
    return stored.order_id


def _request_export(connection: Any, shop: _Shop, business_date: Any) -> Any:
    return SanitizedExportRepository().request(
        connection,
        ExportRequestCommand(
            store_id=shop.store_id,
            dataset=ExportDataset.STORE_DAY_ORDERS_V1,
            business_date=business_date,
            principal=shop.requester,
            correlation_id=uuid4(),
            idempotency_key=f"export-{uuid4().hex}",
            requested_at=shop.now,
        ),
    )


def _raise_envelope(
    connection: Any, shop: _Shop, created: Any, *, raised_by: StaffPrincipal | None = None
) -> UUID:
    """Raise the `EXPORT_SANITIZED_DATA` envelope for a stored export request.

    The resource type comes from `APPROVAL_RESOURCE_TYPES` rather than a literal, because
    `build_approval_envelope` refuses any other value for this action and a literal here would be a
    second place to get it wrong.

    `raised_by` is a parameter because raising the envelope and defining the export are two
    different acts by two possibly different accounts -- which is the whole of the defect the
    separation-of-duty pair below pins.
    """
    raiser = raised_by or shop.requester
    stored = ApprovalRepository().request(
        connection,
        ApprovalRequestCommand(
            ApprovalAction.EXPORT_SANITIZED_DATA,
            APPROVAL_RESOURCE_TYPES[ApprovalAction.EXPORT_SANITIZED_DATA],
            created.export_request_id,
            created.resource_version,
            created.snapshot_hash,
            created.rendered_hash,
            created.policy_version,
            raiser.staff_user_id,
            f"approval-{uuid4().hex}",
            uuid4(),
            shop.now,
            store_id=shop.store_id,
        ),
    )
    return stored.approval_request_id


def _decide(
    connection: Any,
    shop: _Shop,
    created: Any,
    approval_id: UUID,
    *,
    decided_by: StaffPrincipal,
) -> None:
    ApprovalRepository().decide(
        connection,
        ApprovalDecisionCommand(
            approval_request_id=approval_id,
            decision=ApprovalDecision.APPROVED,
            observed_resource_version=created.resource_version,
            observed_snapshot_hash=created.snapshot_hash,
            observed_rendered_hash=created.rendered_hash,
            reason_code="OWNER_APPROVED_EXPORT",
            principal=decided_by,
            correlation_id=uuid4(),
            decided_at=shop.now,
        ),
    )


def _approve(
    connection: Any,
    shop: _Shop,
    created: Any,
    *,
    raised_by: StaffPrincipal | None = None,
    decided_by: StaffPrincipal | None = None,
) -> UUID:
    """Raise the envelope and have an owner who is not the definer decide it."""
    approval_id = _raise_envelope(connection, shop, created, raised_by=raised_by)
    _decide(connection, shop, created, approval_id, decided_by=decided_by or shop.owner)
    return approval_id


def _business_date(moment: datetime) -> Any:
    """The shop's own day for an instant, taken the way the export's SQL takes it."""
    return moment.astimezone(tz=None).date() if moment.tzinfo is None else _local_date(moment)


def _local_date(moment: datetime) -> Any:
    from zoneinfo import ZoneInfo

    from nha_trang_laundry_db.exports import BUSINESS_TIMEZONE

    return moment.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()


def test_the_export_query_version_is_pinned_to_the_rule_it_names() -> None:
    """Invariant 18 follows the file out of the building.

    The version is written onto the `data_exports` row and returned with the bytes, so a CSV on
    somebody\'s laptop can still be traced to the rule that chose its columns. Both halves are
    pinned for the reason `test_ops_board.py` states: the identifier alone would let the rule change
    under a name that no longer describes it.

    The column list and the exclusion list are hashed alongside the SQL, so widening the export --
    the change that most needs a fresh reading -- moves this digest even though the statement it
    edits is one line.

    The digest moved from `5d29b9defdf2f3d7` to `7489179454314e46`, and the arithmetic behind the
    move is two edits to the hashed inputs rather than a number bumped until the test went green:

    1. `incident_open` left `EXPORT_COLUMNS` and left the SELECT. Nothing in this system ever sets
       `orders.incident_open` -- `INCIDENT-INTAKE-001` recorded that, and
       `enforce_order_projection_update` refuses the UPDATE that would, on exactly the COMPLETED
       and CANCELLED orders a complaint arrives against -- so the file published a constant `false`
       as a fact about every order, inside a document the owner's approval and this digest make
       authoritative. Thirteen columns became twelve.
    2. `EXPORT_DAY_BOUNDARY` joined the hashed inputs. The boundary was always `orders.created_at`
       and is unchanged; what changed is that it is now named in the version, because this system
       cuts the shop's day two ways -- this file by when an order was opened and
       `COLLECTED_TODAY_QUERY` by when money was taken -- and both are labelled *tiền đã thu* on
       the console.

    Both are intended to invalidate approvals signed over the old document, which is what the
    digest is for: an envelope raised before this change no longer matches at release and refuses
    with `EXPORT_APPROVAL_NOT_BOUND` rather than shipping a different file than was signed for.
    """
    # v2: `balance_status`, `refunded_amount_vnd` and `refunded_at` joined the columns and
    # `order_refunds` joined the SELECT (DEC-024). v1 (`7489179454314e46`) showed a paid order
    # cancelled with the cash handed back exactly like a paid order, so a sum of `paid_amount_vnd`
    # overstated the day by every refund. Approvals signed over v1's twelve columns stop matching,
    # which is the point of hashing the column list.
    assert EXPORT_QUERY.identifier == "store-day-orders-export-v2"
    assert EXPORT_QUERY.label == "store-day-orders-export-v2:3f884e227d6a2d05"


# --- refusals -----------------------------------------------------------------------------------


def test_an_export_without_an_approval_is_refused(connection: psycopg.Connection[Any]) -> None:
    """The headline refusal. No envelope, no file — and the reason says which is missing."""
    shop = _Shop(connection, datetime.now(UTC))
    _order_with_complaint(connection, shop)
    created = _request_export(connection, shop, _local_date(shop.now))

    with pytest.raises(ExportStateError) as raised:
        SanitizedExportRepository().execute(
            connection,
            ExportExecutionCommand(
                export_request_id=created.export_request_id,
                # A UUID that names no approval at all, which is what a console would send if it
                # skipped the approval step entirely.
                approval_request_id=uuid4(),
                principal=shop.requester,
                correlation_id=uuid4(),
            ),
        )

    assert raised.value.reason_code == "EXPORT_APPROVAL_REQUIRED"
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM data_exports")
        assert cursor.fetchone() == (0,)


def test_an_approval_for_a_different_export_request_does_not_authorise_this_one(
    connection: psycopg.Connection[Any],
) -> None:
    """Invariant 8 across two documents: an approval binds one, and only one.

    Both requests are real and both are for the same shop; the owner approved the first day's. A
    binding check that only looked at action and store would release the second day's records under
    it, which is how an approval becomes a standing permission.
    """
    shop = _Shop(connection, datetime.now(UTC))
    _order_with_complaint(connection, shop)
    today = _local_date(shop.now)
    approved = _request_export(connection, shop, today)
    other = _request_export(connection, shop, today - timedelta(days=1))
    approval_id = _approve(connection, shop, approved)

    with pytest.raises(ExportStateError) as raised:
        SanitizedExportRepository().execute(
            connection,
            ExportExecutionCommand(
                export_request_id=other.export_request_id,
                approval_request_id=approval_id,
                principal=shop.requester,
                correlation_id=uuid4(),
            ),
        )

    assert raised.value.reason_code == "EXPORT_APPROVAL_NOT_BOUND"


def test_an_envelope_naming_an_export_request_that_does_not_exist_is_refused(
    connection: psycopg.Connection[Any],
) -> None:
    """`_RESOLVABLE_RESOURCES` gained `EXPORT_REQUEST`, and this is what that buys.

    Before this item the type had no table, so an envelope could name any UUID with invented digests
    and be approved and frozen into the ledger. The approval is now refused at request time, which
    is the fail-closed direction: the bad envelope never reaches an owner to be signed.
    """
    shop = _Shop(connection, datetime.now(UTC))

    with pytest.raises(ApprovalStateError):
        ApprovalRepository().request(
            connection,
            ApprovalRequestCommand(
                ApprovalAction.EXPORT_SANITIZED_DATA,
                APPROVAL_RESOURCE_TYPES[ApprovalAction.EXPORT_SANITIZED_DATA],
                uuid4(),
                1,
                "JCS-SHA256-V1:" + "a" * 64,
                "JCS-SHA256-V1:" + "b" * 64,
                "sanitized-export-v1",
                shop.requester.staff_user_id,
                f"approval-{uuid4().hex}",
                uuid4(),
                shop.now,
                store_id=shop.store_id,
            ),
        )


def test_a_request_named_under_the_wrong_store_is_refused_before_a_file_exists(
    connection: psycopg.Connection[Any],
) -> None:
    """A mislabelled URL must not burn the request's one release.

    The caller is a member of the shop that owns the request, so this is a console pointing at the
    wrong store rather than an access attempt. Producing the file and then declining to hand it back
    would leave a `data_exports` row saying a release happened that nobody received -- and the
    UNIQUE constraint would then refuse the corrected attempt, turning a typo into a lost export.
    """
    shop = _Shop(connection, datetime.now(UTC))
    _order_with_complaint(connection, shop)
    created = _request_export(connection, shop, _local_date(shop.now))
    approval_id = _approve(connection, shop, created)

    with pytest.raises(ExportStateError) as raised:
        SanitizedExportRepository().execute(
            connection,
            ExportExecutionCommand(
                export_request_id=created.export_request_id,
                approval_request_id=approval_id,
                principal=shop.requester,
                correlation_id=uuid4(),
                expected_store_id=uuid4(),
            ),
        )

    assert raised.value.reason_code == "EXPORT_REQUEST_STORE_MISMATCH"
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM data_exports")
        assert cursor.fetchone() == (0,)

    # And the corrected call still works: nothing was consumed by the refusal.
    produced = SanitizedExportRepository().execute(
        connection,
        ExportExecutionCommand(
            export_request_id=created.export_request_id,
            approval_request_id=approval_id,
            principal=shop.requester,
            correlation_id=uuid4(),
            expected_store_id=shop.store_id,
        ),
    )
    assert produced.row_count == 1


def test_a_column_added_after_the_owner_approved_refuses_rather_than_ships(
    connection: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check that makes the approval bind the content, rather than bind another stored string.

    `export_requests` stores the digests as they were at request time, and until
    `OPS-BOARD-FIX-001` `execute` compared the envelope's `rendered_hash` to that stored copy. Both
    were frozen at the same moment and equal to each other by construction, so the comparison
    proved only that the row had not been edited -- which the append-only trigger already proves.
    Nothing was ever re-derived from the content about to leave the building.

    Here `EXPORT_COLUMNS` gains `bound_contact_id` between the owner's approval and the release: a
    customer key, the one column `DEC-027` rests on the export not carrying, added by exactly the
    kind of well-meant edit that lands between a request and a release. The old check passed it.
    The digest is now recomputed from the column list and the exclusions in force at release, so
    the approval no longer matches what is being authorised and the file is never produced.

    `EXPORT_APPROVAL_NOT_BOUND` rather than a new code, because that is what happened: the envelope
    in hand binds a different document than the one this release would hand over. The remedy is the
    same as for any other mismatch -- raise the request again and let the owner read the wider list
    and decide it.
    """
    shop = _Shop(connection, datetime.now(UTC))
    _order_with_complaint(connection, shop)
    created = _request_export(connection, shop, _local_date(shop.now))
    approval_id = _approve(connection, shop, created)

    monkeypatch.setattr(exports, "EXPORT_COLUMNS", (*EXPORT_COLUMNS, "bound_contact_id"))

    with pytest.raises(ExportStateError) as raised:
        SanitizedExportRepository().execute(
            connection,
            ExportExecutionCommand(
                export_request_id=created.export_request_id,
                approval_request_id=approval_id,
                principal=shop.requester,
                correlation_id=uuid4(),
                expected_store_id=shop.store_id,
            ),
        )

    assert raised.value.reason_code == "EXPORT_APPROVAL_NOT_BOUND"
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM data_exports")
        assert cursor.fetchone() == (0,)


def test_withdrawing_a_stated_exclusion_after_approval_refuses_too(
    connection: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same property from the other side, because the exclusions are half of the promise.

    What the file withholds is not commentary: the owner's statement names
    `customer_incident_evidence.summary` and the other three by name, and a reader told only what a
    file contains cannot tell "the complaint text is not here" from "no complaint was recorded".
    Dropping one from the list changes what the owner agreed to without touching a single column,
    so it has to move the released digest as surely as adding a column does.
    """
    shop = _Shop(connection, datetime.now(UTC))
    _order_with_complaint(connection, shop)
    created = _request_export(connection, shop, _local_date(shop.now))
    approval_id = _approve(connection, shop, created)

    monkeypatch.setattr(exports, "EXPORT_EXCLUSIONS", EXPORT_EXCLUSIONS[:-1])

    with pytest.raises(ExportStateError) as raised:
        SanitizedExportRepository().execute(
            connection,
            ExportExecutionCommand(
                export_request_id=created.export_request_id,
                approval_request_id=approval_id,
                principal=shop.requester,
                correlation_id=uuid4(),
                expected_store_id=shop.store_id,
            ),
        )

    assert raised.value.reason_code == "EXPORT_APPROVAL_NOT_BOUND"


def test_a_role_outside_the_export_set_cannot_request_or_release_one(
    connection: psycopg.Connection[Any],
) -> None:
    """An operator runs the counter and reads the board; taking a copy out is a different act."""
    shop = _Shop(connection, datetime.now(UTC))
    operator = _staff(connection, roles=frozenset({StaffRole.OPERATOR}), now=shop.now)
    ShadowConsoleRepository.assign_store(
        connection,
        staff_user_id=operator.staff_user_id,
        store_id=shop.store_id,
        principal=shop.owner,
        correlation_id=uuid4(),
    )

    with pytest.raises(ExportAuthorizationError):
        SanitizedExportRepository().request(
            connection,
            ExportRequestCommand(
                store_id=shop.store_id,
                dataset=ExportDataset.STORE_DAY_ORDERS_V1,
                business_date=_local_date(shop.now),
                principal=operator,
                correlation_id=uuid4(),
                idempotency_key=f"export-{uuid4().hex}",
                requested_at=shop.now,
            ),
        )


# --- the approved export ------------------------------------------------------------------------


def test_an_approved_export_carries_the_order_facts_and_no_incident_free_text(
    connection: psycopg.Connection[Any],
) -> None:
    """The word "sanitized" as a test rather than a label.

    The complaint was written into `customer_incident_payloads` by the same fixture that made the
    order, so it is really there to be leaked. The file is then searched for it — the produced
    bytes, not the query — because the property that matters is what a person ends up holding.
    """
    shop = _Shop(connection, datetime.now(UTC))
    order_id = _order_with_complaint(connection, shop)
    created = _request_export(connection, shop, _local_date(shop.now))
    approval_id = _approve(connection, shop, created)

    produced = SanitizedExportRepository().execute(
        connection,
        ExportExecutionCommand(
            export_request_id=created.export_request_id,
            approval_request_id=approval_id,
            principal=shop.requester,
            correlation_id=uuid4(),
        ),
    )

    content = produced.content_csv
    assert produced.row_count == 1
    assert content.splitlines()[0] == ",".join(EXPORT_COLUMNS)
    assert str(order_id) in content
    assert produced.query_version == EXPORT_QUERY.label

    # The assertion this test exists for, stated three ways because a partial leak is still a leak.
    assert COMPLAINT not in content
    assert "CHUOI-RIENG-9137" not in content
    assert "ố vàng" not in content
    # And no column that could carry one, or carry a customer key, is in the header at all.
    assert "evidence_summary" not in content
    assert "bound_contact_id" not in content

    # The complaint really was recorded; otherwise the three assertions above would pass over an
    # incident that never existed, which is the way this test could rot into a tautology.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM customer_incident_evidence WHERE summary = %s",
            (COMPLAINT,),
        )
        assert cursor.fetchone() == (1,)


def test_the_export_audit_event_names_the_approval_that_authorised_it(
    connection: psycopg.Connection[Any],
) -> None:
    """Invariant 5, and the accountability the whole surface exists for.

    Record, domain event, audit event and outbox record are written in one transaction, and the
    audit row carries the approval identifier. An export whose trail does not say who allowed it is
    an export nobody can answer for.
    """
    shop = _Shop(connection, datetime.now(UTC))
    _order_with_complaint(connection, shop)
    created = _request_export(connection, shop, _local_date(shop.now))
    approval_id = _approve(connection, shop, created)

    produced = SanitizedExportRepository().execute(
        connection,
        ExportExecutionCommand(
            export_request_id=created.export_request_id,
            approval_request_id=approval_id,
            principal=shop.requester,
            correlation_id=uuid4(),
        ),
    )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT action, actor_type, actor_id, details
            FROM audit_events
            WHERE aggregate_type = 'DATA_EXPORT' AND aggregate_id = %s
            """,
            (produced.export_id,),
        )
        audit = cursor.fetchone()
        cursor.execute(
            "SELECT event_type FROM domain_events WHERE aggregate_id = %s", (produced.export_id,)
        )
        events = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT event_type FROM outbox_events WHERE aggregate_id = %s", (produced.export_id,)
        )
        outbox = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            "SELECT approval_request_id, row_count, query_version FROM data_exports WHERE id = %s",
            (produced.export_id,),
        )
        record = cursor.fetchone()

    assert audit is not None
    assert audit[0] == "EXPORT_PRODUCE"
    assert audit[1] == "STAFF"
    assert UUID(str(audit[2])) == shop.requester.staff_user_id
    assert audit[3]["approval_request_id"] == str(approval_id)
    assert events == ["EXPORT_PRODUCED"]
    assert outbox == ["export.produced.v1"]
    assert record is not None
    assert UUID(str(record[0])) == approval_id
    assert int(record[1]) == produced.row_count
    assert str(record[2]) == EXPORT_QUERY.label


def test_one_approval_releases_one_file(connection: psycopg.Connection[Any]) -> None:
    """A second attempt under the same approval is refused, not served.

    The one-time property is `data_exports.export_request_id UNIQUE` rather than a prior SELECT, so
    a retry and a race land in the same place.
    """
    shop = _Shop(connection, datetime.now(UTC))
    _order_with_complaint(connection, shop)
    created = _request_export(connection, shop, _local_date(shop.now))
    approval_id = _approve(connection, shop, created)
    command = ExportExecutionCommand(
        export_request_id=created.export_request_id,
        approval_request_id=approval_id,
        principal=shop.requester,
        correlation_id=uuid4(),
    )
    SanitizedExportRepository().execute(connection, command)

    with pytest.raises(ExportStateError) as raised:
        SanitizedExportRepository().execute(connection, command)

    assert raised.value.reason_code == "EXPORT_ALREADY_PRODUCED"
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM data_exports")
        assert cursor.fetchone() == (1,)


def test_a_cell_a_spreadsheet_would_execute_stops_the_export(
    connection: psycopg.Connection[Any],
) -> None:
    """The requirement `#/gaps` recorded for this capability before it was built, enforced.

    A CSV cell beginning with `=`, `+`, `-` or `@` is a formula when the file is opened. No value
    this column set can hold legitimately starts with one, so such a cell means the data is wrong
    rather than the formatting -- and the export fails closed instead of prefixing an apostrophe
    onto the shop's own record to work around a spreadsheet.

    Exercised at `_cell`, which is where the rule lives. Reaching it through a real export would
    mean writing a `commercial_status` the table's own CHECK forbids, so the test that could be
    written end to end is a test of the CHECK.
    """
    from nha_trang_laundry_db.exports import _cell

    assert _cell("ACTIVE") == "ACTIVE"
    assert _cell(None) == ""
    assert _cell(110_000) == "110000"
    for dangerous in ("=1+1", "+44", "-3", "@SUM(A1)", "\tcmd", "\rcmd"):
        with pytest.raises(ExportStateError) as raised:
            _cell(dangerous)
        assert raised.value.reason_code == "EXPORT_CELL_NOT_SAFE"


def test_a_repeated_export_request_replays_rather_than_raising_a_second_one(
    connection: psycopg.Connection[Any],
) -> None:
    """The console taps twice on a slow connection; the shop does not get two pending requests."""
    shop = _Shop(connection, datetime.now(UTC))
    key = f"export-{uuid4().hex}"

    def once() -> Any:
        return SanitizedExportRepository().request(
            connection,
            ExportRequestCommand(
                store_id=shop.store_id,
                dataset=ExportDataset.STORE_DAY_ORDERS_V1,
                business_date=_local_date(shop.now),
                principal=shop.requester,
                correlation_id=uuid4(),
                idempotency_key=key,
                requested_at=shop.now,
            ),
        )

    first = once()
    second = once()

    assert first.replayed is False
    assert second.replayed is True
    assert second.export_request_id == first.export_request_id
    assert second.rendered_hash == first.rendered_hash


# --- separation of duty -------------------------------------------------------------------------


def test_the_staff_member_who_defined_the_export_cannot_approve_it(
    connection: psycopg.Connection[Any],
) -> None:
    """The maker-checker rule, bound to the person who chose what leaves the building.

    `_OWNER_FINANCIAL` carries `SEPARATION_OF_DUTY`, and `_authorize_decision` implements it by
    refusing a decision from `approval_requests.requested_by` -- whoever raised the ENVELOPE. For
    an export those are two different acts by two possibly different accounts: the request names
    the shop, the day and the column list, and raising an envelope against that stored row is a
    later act any member of the store may perform.

    So this exact sequence used to succeed, with the rule reading green throughout: the owner
    defines the export, a colleague presses "xin chủ tiệm duyệt", and the owner approves their own
    release. The envelope's requester and its decider really are two people, which is all the
    original check could see.

    `approvals._RESOURCE_DEFINERS` now measures the rule against `export_requests.requested_by_
    staff_id`, and the refusal is the same opaque `ApprovalAuthorizationError` the rule it extends
    uses: an approver is told separation of duty refused them, not which of two accounts it
    compared.
    """
    shop = _Shop(connection, datetime.now(UTC))
    # The owner defines the export -- they choose the day, the shop and the column list.
    created = SanitizedExportRepository().request(
        connection,
        ExportRequestCommand(
            store_id=shop.store_id,
            dataset=ExportDataset.STORE_DAY_ORDERS_V1,
            business_date=_local_date(shop.now),
            principal=shop.owner,
            correlation_id=uuid4(),
            idempotency_key=f"export-{uuid4().hex}",
            requested_at=shop.now,
        ),
    )
    # Somebody else raises the envelope, which is what made the old check pass.
    approval_id = _raise_envelope(connection, shop, created, raised_by=shop.requester)

    with pytest.raises(ApprovalAuthorizationError):
        _decide(connection, shop, created, approval_id, decided_by=shop.owner)

    # And nothing was recorded: the envelope is still waiting, not silently approved.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM approval_request_states WHERE approval_request_id = %s",
            (approval_id,),
        )
        assert cursor.fetchone() == ("REQUESTED",)
        cursor.execute(
            "SELECT count(*) FROM approval_decisions WHERE approval_request_id = %s",
            (approval_id,),
        )
        assert cursor.fetchone() == (0,)


def test_an_owner_who_did_not_define_the_export_approves_it_and_the_file_is_released(
    connection: psycopg.Connection[Any],
) -> None:
    """The other direction, which is the half that proves the rule is not simply "no exports".

    Same shape as the refusal above -- the owner defines the export, a colleague raises the
    envelope -- and the only thing that changes is which owner decides. A second `OWNER_ADMIN`
    approves, the release goes through, and the produced file is the one that was signed for.
    """
    shop = _Shop(connection, datetime.now(UTC))
    order_id = _order_with_complaint(connection, shop)
    created = SanitizedExportRepository().request(
        connection,
        ExportRequestCommand(
            store_id=shop.store_id,
            dataset=ExportDataset.STORE_DAY_ORDERS_V1,
            business_date=_local_date(shop.now),
            principal=shop.owner,
            correlation_id=uuid4(),
            idempotency_key=f"export-{uuid4().hex}",
            requested_at=shop.now,
        ),
    )
    approval_id = _raise_envelope(connection, shop, created, raised_by=shop.requester)
    _decide(connection, shop, created, approval_id, decided_by=shop.second_owner)

    produced = SanitizedExportRepository().execute(
        connection,
        ExportExecutionCommand(
            export_request_id=created.export_request_id,
            approval_request_id=approval_id,
            principal=shop.owner,
            correlation_id=uuid4(),
        ),
    )

    assert produced.row_count == 1
    assert str(order_id) in produced.content_csv


def test_a_release_whose_approval_was_decided_by_the_definer_is_refused(
    connection: psycopg.Connection[Any],
) -> None:
    """The same property, enforced a second time over the bytes rather than trusted from upstream.

    The decision path refuses this case, so reaching the refusal below means a decision row exists
    that `ApprovalRepository.decide` would not have written. The check is still here because the
    file is the thing that cannot be recalled: an approval can be re-examined, and a CSV on
    somebody's laptop cannot.

    The approval is therefore written directly -- the decision row and the state transition, the
    two writes `decide` makes after its checks pass. What that simulates is a future code path (a
    migration, an admin tool, a second decision surface) recording an approval without going
    through `_authorize_decision`. The export refuses it by name and no `data_exports` row is
    written.
    """
    shop = _Shop(connection, datetime.now(UTC))
    _order_with_complaint(connection, shop)
    created = SanitizedExportRepository().request(
        connection,
        ExportRequestCommand(
            store_id=shop.store_id,
            dataset=ExportDataset.STORE_DAY_ORDERS_V1,
            business_date=_local_date(shop.now),
            principal=shop.owner,
            correlation_id=uuid4(),
            idempotency_key=f"export-{uuid4().hex}",
            requested_at=shop.now,
        ),
    )
    approval_id = _raise_envelope(connection, shop, created, raised_by=shop.requester)

    # The two writes `decide` makes once its checks have passed, made here without them, with the
    # definer recorded as the decider. `approval_decisions` is append-only, so this is an INSERT
    # rather than an edit of a legitimately decided row -- which is also the more faithful
    # simulation: a path that never called `_authorize_decision` would write exactly this.
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO approval_decisions (
                id, approval_request_id, decision_type, decision, decided_by, reason_code, note,
                observed_resource_version, observed_snapshot_hash, observed_rendered_hash,
                decided_at
            )
            SELECT %s, r.id, r.action, 'APPROVED', %s, 'OWNER_APPROVED_EXPORT', NULL,
                   r.resource_version, r.snapshot_hash, r.rendered_hash, %s
            FROM approval_requests r WHERE r.id = %s
            """,
            (uuid4(), shop.owner.staff_user_id, shop.now, approval_id),
        )
        cursor.execute(
            """
            UPDATE approval_request_states
            SET status = 'APPROVED', row_version = row_version + 1, updated_at = %s
            WHERE approval_request_id = %s
            """,
            (shop.now, approval_id),
        )

    with pytest.raises(ExportStateError) as raised:
        SanitizedExportRepository().execute(
            connection,
            ExportExecutionCommand(
                export_request_id=created.export_request_id,
                approval_request_id=approval_id,
                principal=shop.second_owner,
                correlation_id=uuid4(),
            ),
        )

    assert raised.value.reason_code == "EXPORT_APPROVAL_SELF_DECIDED"
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM data_exports")
        assert cursor.fetchone() == (0,)


# --- what the file says, and what it must not say -----------------------------------------------


def test_the_export_does_not_publish_a_field_nothing_in_this_system_sets(
    connection: psycopg.Connection[Any],
) -> None:
    """`orders.incident_open` is out of the column list, and this is why it stays out.

    The column exists in `0007_operations_control.sql` with `DEFAULT FALSE` and nothing writes it.
    `INCIDENT-INTAKE-001` recorded that after searching for a writer and finding none, and
    `enforce_order_projection_update` refuses the UPDATE that would set it on a COMPLETED or
    CANCELLED order -- which is exactly when a complaint arrives.

    So the export used to publish a constant `false` as a fact about every order, inside a document
    an owner signs and a digest makes authoritative. A reader concludes no order in the shop ever
    had an incident. That is untrue, and an always-false field is worse than an absent one because
    the reader cannot tell it from a real answer.

    The order below has a real incident recorded against it, which is what makes the assertion mean
    something: the column would have said `false` about this very order.
    """
    shop = _Shop(connection, datetime.now(UTC))
    order_id = _order_with_complaint(connection, shop)
    created = _request_export(connection, shop, _local_date(shop.now))
    approval_id = _approve(connection, shop, created)

    produced = SanitizedExportRepository().execute(
        connection,
        ExportExecutionCommand(
            export_request_id=created.export_request_id,
            approval_request_id=approval_id,
            principal=shop.requester,
            correlation_id=uuid4(),
        ),
    )

    assert "incident_open" not in EXPORT_COLUMNS
    header = produced.content_csv.splitlines()[0]
    assert "incident_open" not in header
    assert header == ",".join(EXPORT_COLUMNS)
    # No bare `false` anywhere in the body either, which is the shape the old column took.
    body = produced.content_csv.splitlines()[1:]
    assert body and all("false" not in line for line in body)

    # The incident really exists, so the column would have lied about this order specifically.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM customer_incidents WHERE order_id = %s",
            (order_id,),
        )
        assert cursor.fetchone() == (1,)
        cursor.execute("SELECT incident_open FROM orders WHERE id = %s", (order_id,))
        assert cursor.fetchone() == (False,)


def test_the_signed_statement_names_which_event_cuts_the_shop_s_day(
    connection: psycopg.Connection[Any],
) -> None:
    """Two figures in this system are called *tiền đã thu*; the document says which one this is.

    `COLLECTED_TODAY_QUERY` sums `order_settlements.attested_at` -- money across the counter today,
    which is the box on `#/today`. This file is cut on `orders.created_at` -- the orders opened on
    a named day, with whatever has since been paid against them. An order opened yesterday and paid
    this morning is in today's takings and in yesterday's export.

    Neither is wrong and they are deliberately not aligned: a dataset called `STORE_DAY_ORDERS_V1`
    is the day's orders by definition, and re-cutting it on payment would make it a different
    dataset under the same name. What is refused is shipping both under one label in silence, so
    the boundary is in the hashed statement the owner reads and in the query version.
    """
    shop = _Shop(connection, datetime.now(UTC))
    created = _request_export(connection, shop, _local_date(shop.now))

    assert created.day_boundary == exports.EXPORT_DAY_BOUNDARY == "orders.created_at"
    assert "mở đơn" in created.statement_vi
    assert "không phải theo lúc thu tiền" in created.statement_vi

    # The boundary is a hashed input of the query version, so a future edit that cuts the day on
    # payment instead cannot keep the version it was published under. Asserted by rebuilding the
    # version without that part and demanding a different digest -- reading the inputs back off
    # `EXPORT_QUERY` would only prove the constant equals itself.
    without_boundary = query_version(
        EXPORT_QUERY.identifier,
        exports._EXPORT_SQL,
        exports.BUSINESS_TIMEZONE,
        ",".join(EXPORT_COLUMNS),
        ",".join(EXPORT_EXCLUSIONS),
    )
    assert without_boundary.digest != EXPORT_QUERY.digest


def test_the_approval_disclosure_shows_what_is_being_released_and_who_may_not_release_it(
    connection: psycopg.Connection[Any],
) -> None:
    """The read `#/approvals` needs before it can offer an approve control for an export.

    `EXPORT_REQUEST` was absent from the console's viewable-resource table, so the envelope
    `#/exports` raises could never be decided from `#/approvals` -- a staff member could request an
    export that no owner was able to release. Adding the type alone would have let an owner approve
    a digest and a UUID, so this read is what makes the approval not blind.

    `rendered_hash` here is re-derived from the column list in force right now, not read back off
    `export_requests`. That is what lets the console compare it against the queue row and refuse to
    show today's columns as though they were the ones the envelope binds.
    """
    shop = _Shop(connection, datetime.now(UTC))
    created = SanitizedExportRepository().request(
        connection,
        ExportRequestCommand(
            store_id=shop.store_id,
            dataset=ExportDataset.STORE_DAY_ORDERS_V1,
            business_date=_local_date(shop.now),
            principal=shop.owner,
            correlation_id=uuid4(),
            idempotency_key=f"export-{uuid4().hex}",
            requested_at=shop.now,
        ),
    )
    approval_id = _raise_envelope(connection, shop, created, raised_by=shop.requester)

    with connection.cursor() as cursor:
        for_definer = SanitizedExportRepository.read_for_approval(
            cursor, approval_id=approval_id, principal=shop.owner
        )
        for_other = SanitizedExportRepository.read_for_approval(
            cursor, approval_id=approval_id, principal=shop.second_owner
        )
        missing = SanitizedExportRepository.read_for_approval(
            cursor, approval_id=uuid4(), principal=shop.second_owner
        )

    assert for_definer is not None and for_other is not None
    assert missing is None
    # What is being released, in the words the owner signs.
    assert for_other.columns == EXPORT_COLUMNS
    assert for_other.excludes == EXPORT_EXCLUSIONS
    assert for_other.business_date == _local_date(shop.now)
    assert for_other.day_boundary == "orders.created_at"
    assert for_other.query_version == EXPORT_QUERY.label
    assert for_other.statement_vi == created.statement_vi
    # The digest the console compares against its queue row is the one the envelope binds.
    assert for_other.rendered_hash == created.rendered_hash
    # And the one field that is about the reader: the definer is told before the press, not after.
    assert for_definer.requested_by_you is True
    assert for_other.requested_by_you is False


def test_a_paid_order_cancelled_with_a_refund_shows_the_refund_in_the_export(
    connection: psycopg.Connection[Any],
) -> None:
    """The export used to LEFT JOIN the settlement and stop, so a refunded order read as paid.

    A paid order cancelled under `DEC-024`'s `RETURNED_UNWASHED_REFUNDED` hands the money back at
    the counter. The file must say so on the order's own row -- the settlement it reverses stays
    visible beside it, because both things happened -- or anyone summing `paid_amount_vnd` reports
    a day's takings larger than the drawer by exactly the refund.
    """
    from nha_trang_laundry_db.settlement import SettlementCommand, SettlementRepository
    from nha_trang_laundry_domain.catalog import CommercialOrderStatus, CustodyResolution

    shop = _Shop(connection, datetime.now(UTC))
    quote_id, revision, quote, contact_id = accepted_quote(
        connection,
        store_id=shop.store_id,
        principal=shop.owner,
        fulfillment_mode=FulfillmentMode.PICKUP_AND_RETURN,
    )
    stored = OrderRepository().create(
        connection,
        CreateOrderCommand(
            shop.store_id,
            contact_id,
            quote_id,
            revision,
            quote.document.snapshot_hash,
            FulfillmentMode.PICKUP_AND_RETURN,
            shop.owner,
            f"order-{uuid4().hex}",
            uuid4(),
            shop.now,
            AcquisitionSource.WALK_IN,
        ),
    )
    order_id, version = stored.order_id, stored.row_version

    def move(**step: Any) -> None:
        nonlocal version
        version = (
            OrderRepository()
            .transition(
                connection,
                OrderTransitionCommand(
                    order_id,
                    version,
                    shop.owner,
                    f"step-{uuid4().hex}",
                    uuid4(),
                    occurred_at=shop.now,
                    **step,
                ),
            )
            .row_version
        )

    move(intake_target=IntakeStatus.RECEIVED_PENDING_INSPECTION)
    move(
        intake_target=IntakeStatus.ACCEPTED, production_accepted_at=shop.now, intake_readiness=READY
    )
    move(commercial_target=CommercialOrderStatus.STORE_CONFIRMATION_PENDING)
    move(commercial_target=CommercialOrderStatus.CONFIRMED)
    move(commercial_target=CommercialOrderStatus.ACTIVE)
    settled = SettlementRepository().record(
        connection,
        SettlementCommand(
            order_id=order_id,
            paid_amount_vnd=110_000,
            collected_by_customer=False,
            principal=shop.owner,
            correlation_id=uuid4(),
            attested_at=shop.now,
        ),
    )
    version = settled.row_version
    move(commercial_target=CommercialOrderStatus.CANCELLATION_REVIEW)
    move(
        commercial_target=CommercialOrderStatus.CANCELLED,
        custody_resolution=CustodyResolution.RETURNED_UNWASHED_REFUNDED,
    )

    created = _request_export(connection, shop, _local_date(shop.now))
    approval_id = _approve(connection, shop, created)
    produced = SanitizedExportRepository().execute(
        connection,
        ExportExecutionCommand(
            export_request_id=created.export_request_id,
            approval_request_id=approval_id,
            principal=shop.requester,
            correlation_id=uuid4(),
        ),
    )

    header, *body = produced.content_csv.splitlines()
    assert header == ",".join(EXPORT_COLUMNS)
    assert len(body) == 1
    row = dict(zip(EXPORT_COLUMNS, body[0].split(","), strict=True))
    assert row["order_id"] == str(order_id)
    assert row["commercial_status"] == "CANCELLED"
    assert row["balance_status"] == "REFUNDED"
    assert row["paid_amount_vnd"] == "110000"
    assert row["refunded_amount_vnd"] == "110000"
    assert datetime.fromisoformat(row["refunded_at"]) == shop.now
