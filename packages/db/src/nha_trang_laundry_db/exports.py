"""The owner's sanitized export of one store's own day, as an approved and audited act.

`OPS-BOARD-001`. `ApprovalAction.EXPORT_SANITIZED_DATA` and the `EXPORT_REQUEST` resource type have
existed since the first approval migration and had nothing behind them, so the one capability the
vocabulary named — the owner taking the shop's own records out of the system — did not exist. This
module is that capability, and the shape it takes is not incidental.

**An export is an act, not a button.** `APPROVAL_POLICIES` maps this action to `_OWNER_FINANCIAL`:
owner role, MFA, separation of duty, ten-minute window. That mapping is owner policy and is used
unchanged here — nothing in this module edits it, weakens it, or routes around it.

**Separation of duty binds to the person who defined the export, and it did not at first.** This
docstring used to claim that "the person who asks for an export can never be the person who
approves it", and the code did not have that property. `_authorize_decision` refuses a decision
from the staff member who raised the *approval envelope* — `approval_requests.requested_by` — and
the envelope is a second, later act that any account may perform against a stored request. So the
staff member who chose which day of which shop leaves the building could approve it leaving, as
long as somebody else had pressed "xin duyệt" in between. The requirement `_OWNER_FINANCIAL` names
was being checked against the wrong person.

What matters here is who defined the content, because that is the choice an export is. The rule is
therefore bound to `export_requests.requested_by_staff_id`, in two places and deliberately not one:
`approvals._RESOURCE_DEFINERS` refuses the decision at the moment it is made, so the definer meets
the refusal on the approvals screen rather than after a file was expected; and `execute` below
refuses to release bytes whose `APPROVED` decision was recorded by the definer, so the property
holds over what actually leaves even if a decision were ever written by some path that is not
`ApprovalRepository.decide`. In a shop with exactly one `OWNER_ADMIN`, that owner may raise the
envelope for somebody else's request but cannot approve their own. That is the separation of duty
working, not a defect.

**"Sanitized" is a requirement, not a label.** The export carries order, money and status facts. It
carries no incident free text and no evidence summary, and that exclusion is the point of the word:
those live in `customer_incident_evidence` and `assistant_turn_payloads`, disposable side tables
under `RETENTION-STORE-001`, precisely because they are the sensitive part. A copy in a spreadsheet
on somebody's laptop escapes every guarantee the purge machinery provides — the 365-day incident
disposal cannot reach it, and nobody would know it was there. `EXPORT_EXCLUSIONS` names what is
withheld, the rendered digest the owner approves covers that list, and
`packages/db/tests/test_sanitized_export.py` proves it by searching the produced bytes for a
complaint it wrote first. That digest is re-derived at release from the column list and the
exclusions then in force, never read back off the stored request: a hash compared against a second
value frozen at the same moment proves only that a row was not edited, and what invariant 8 is for
is proving that what leaves is what was signed for.

**No new personal-data column, and none carried.** `DEC-027` rests on there being none, so the
column list below is orders, timestamps, statuses and integer VND. `bound_contact_id` is absent even
though it is only an identifier: an export is the one artefact that leaves the system's own access
controls behind, and a customer key in it is a customer key in a file nobody governs.

The bytes are produced here and never stored. What is stored is `data_exports`: the approval that
authorised the release, the versioned query that produced its columns, the row count and a digest of
the bytes — written with its domain event, audit event and outbox record in one transaction under
invariant 5, so an export that happened without an audit trail is not a state this code can reach.
"""

from __future__ import annotations

import csv
import hmac
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.approvals import APPROVAL_RESOURCE_TYPES
from nha_trang_laundry_domain.canonical import canonical_document
from nha_trang_laundry_domain.catalog import ApprovalAction
from psycopg.errors import UniqueViolation

from .approvals import ApprovalBinding, read_approval_binding
from .idempotency import IdempotencyRepository, IdempotentCommand
from .identity import StaffPrincipal, StaffRole
from .query_version import query_version
from .store_access import require_store_membership
from .transactions import MaterialChange, OutboxEvent, commit_material_change

#: The shop's day, matching `settlement.BUSINESS_TIMEZONE` and `assistant.BUSINESS_TIMEZONE`. An
#: export of "16 September" means the day the staff worked, not a UTC window that cuts it in half.
BUSINESS_TIMEZONE = "Asia/Ho_Chi_Minh"

#: Who may raise or carry out an export. Deliberately narrower than the operations gate that reads
#: the board: the board is a list of work, and this releases the shop's money records. `OPERATOR` is
#: absent, and `AUDITOR` — which reads every Shadow surface — is absent too, because reading inside
#: the system and taking a copy out of it are different acts.
EXPORT_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER})

#: The policy version recorded on the envelope. Not a published business policy: it names the shape
#: of this export surface, so an envelope raised against an older shape is visibly older.
EXPORT_POLICY_VERSION = "sanitized-export-v1"


class ExportDataset(StrEnum):
    """What may be exported. One member, and adding a second is a decision plus a migration."""

    STORE_DAY_ORDERS_V1 = "STORE_DAY_ORDERS_V1"


#: The CSV header, in order. This list *is* the export's contract: it is hashed into the rendered
#: document the owner approves, so widening it invalidates every approval raised against the
#: narrower one instead of quietly shipping more than was authorised (invariant 8).
#:
#: `incident_open` was here and is deliberately gone. `orders.incident_open` exists in
#: `0007_operations_control.sql` with `DEFAULT FALSE` and **nothing in this system ever sets it** --
#: `INCIDENT-INTAKE-001` recorded that in its own evidence, and `enforce_order_projection_update`
#: raises on any UPDATE to a COMPLETED or CANCELLED order, which is exactly when a complaint
#: arrives. So the column was a constant `false` published as a fact, inside a document an owner
#: signs and a digest makes authoritative. A reader of the file concludes that no order in the shop
#: ever had an incident, which is untrue, and has no way to tell that from a column that is simply
#: empty. An always-false field is worse than an absent one, and removing it is the honest repair:
#: it moves `EXPORT_QUERY` and the rendered digest, so every approval signed over the old column
#: list stops matching. That is invariant 8 doing its job, not collateral damage.
#:
#: `customer_incidents.order_id` is where "did this order have an incident" is really answered. It
#: is not exported: an incident row is the sensitive half, and `EXPORT_EXCLUSIONS` says so.
EXPORT_COLUMNS: tuple[str, ...] = (
    "order_id",
    "created_at",
    "commercial_status",
    "intake_status",
    "production_status",
    "production_accepted_at",
    "production_ready_at",
    "production_released_at",
    "closed_at",
    "expected_total_vnd",
    "paid_amount_vnd",
    "settlement_attested_at",
    # `DEC-024`. Until these three existed a paid order cancelled with the cash handed back read,
    # in this file, exactly like a paid order: `paid_amount_vnd` filled in and nothing beside it.
    # Anyone summing that column reported the day larger than the drawer by every refund. The
    # settlement stays -- the customer did pay -- and the refund sits on the same row, positive,
    # with the moment it went back, so both movements are visible and neither is netted away.
    "balance_status",
    "refunded_amount_vnd",
    "refunded_at",
)

#: What this export withholds, named rather than implied, and hashed into the same document.
#:
#: A reader who is told only what a file contains cannot tell the difference between "the complaint
#: text is not here" and "no complaint was recorded". These are the four things a person would
#: reasonably expect and will not find, each with the reason it is absent.
EXPORT_EXCLUSIONS: tuple[str, ...] = (
    "customer_incident_evidence.summary",
    "assistant_turn_payloads.question",
    "assistant_turn_payloads.answer",
    "orders.bound_contact_id",
)

#: Which event cuts the shop's day for this file, named rather than left to be inferred from the
#: SQL below -- and named because there are two answers in this system and they are not the same.
#:
#: `settlement.COLLECTED_TODAY_QUERY` buckets by `order_settlements.attested_at`: money that came
#: across the counter today, which is what `#/today` labels *tiền đã thu*. This file buckets by
#: `orders.created_at`: the orders opened on a named day, with whatever has since been paid against
#: them. An order opened yesterday and paid this morning is in today's takings and in *yesterday's*
#: export, so the two figures answer different questions and will differ most on exactly the busy
#: days somebody would reconcile them on.
#:
#: Neither is wrong and they are not aligned here: a dataset called `STORE_DAY_ORDERS_V1` is the
#: day's orders by definition, and re-cutting it on payment would silently make it something else.
#: What is refused is shipping both under one label. The boundary is hashed into `EXPORT_QUERY`,
#: stated in the Vietnamese document the owner approves, and rendered on both console surfaces --
#: `#/exports` and the approval card -- beside the money columns it governs.
EXPORT_DAY_BOUNDARY = "orders.created_at"

#: The rows, as one statement so that one edit moves one rule.
#:
#: A LEFT JOIN, so an order with no settlement appears with empty money cells rather than
#: disappearing from its own day. That matters for what this file is for: an unpaid order is part of
#: the day's record, and a total that silently dropped it would read as smaller takings rather than
#: as a missing row.
_EXPORT_SQL = """
    SELECT o.id, o.created_at, o.commercial_status, o.intake_status, o.production_status,
           o.production_accepted_at, o.production_ready_at, o.production_released_at,
           o.closed_at,
           s.expected_total_vnd, s.paid_amount_vnd, s.attested_at,
           o.balance_status, rf.refunded_amount_vnd, rf.refunded_at
    FROM orders o
    LEFT JOIN order_settlements s ON s.order_id = o.id
    LEFT JOIN order_refunds rf ON rf.order_id = o.id
    WHERE o.store_id = %(store)s
      AND (o.created_at AT TIME ZONE %(zone)s)::date = %(business_date)s
    ORDER BY o.created_at, o.id
"""

#: The published version of the rule above, travelling with the file and onto its `data_exports`
#: row. The column list, the timezone and the boundary event are hashed with the SQL: any of them
#: could change what a figure in the file means while leaving the statement itself untouched.
EXPORT_QUERY = query_version(
    "store-day-orders-export-v2",
    _EXPORT_SQL,
    BUSINESS_TIMEZONE,
    EXPORT_DAY_BOUNDARY,
    ",".join(EXPORT_COLUMNS),
    ",".join(EXPORT_EXCLUSIONS),
)


class ExportAuthorizationError(PermissionError):
    """Raised when a principal may not request or carry out an export for this store."""


class ExportStateError(ValueError):
    """Raised when an export is unapproved, mis-bound, expired, or already produced."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ExportRequestFacts:
    """What the approval's `snapshot_hash` covers: which records, from which shop, for which day."""

    dataset: str
    store_id: UUID
    business_date: str


@dataclass(frozen=True, slots=True)
class ExportStatement:
    """What the approval's `rendered_hash` covers: the sentence the owner is agreeing to.

    Everything that decides what leaves the building is in here — the facts above, the exact column
    list, the exclusions, the versioned rule and the Vietnamese statement a person reads. Invariant
    8 then does the rest: change any of it and the digest moves, so an approval granted against the
    old wording cannot authorise the new one.
    """

    facts: ExportRequestFacts
    business_timezone: str
    #: Which event puts an order on the named day. In the hashed document because two figures in
    #: this system are called *tiền đã thu* and are cut differently; see `EXPORT_DAY_BOUNDARY`.
    day_boundary: str
    columns: tuple[str, ...]
    excludes: tuple[str, ...]
    query_version: str
    statement_vi: str


@dataclass(frozen=True, slots=True)
class ExportRequestCommand:
    store_id: UUID
    dataset: ExportDataset
    #: Named by the requester. There is deliberately no default: a day nobody chose is a day nobody
    #: is accountable for having exported.
    business_date: date
    principal: StaffPrincipal
    correlation_id: UUID
    idempotency_key: str
    requested_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredExportRequest:
    """Everything the caller needs to raise the approval envelope for this request."""

    export_request_id: UUID
    store_id: UUID
    dataset: str
    business_date: date
    resource_type: str
    resource_version: int
    snapshot_hash: str
    rendered_hash: str
    policy_version: str
    requested_at: datetime
    columns: tuple[str, ...]
    excludes: tuple[str, ...]
    query_version: str
    #: The event the named day is cut on, and the Vietnamese sentence hashed into `rendered_hash`.
    #: Returned rather than left to the console to compose: what an owner approves is this exact
    #: wording, so a screen writing its own would be describing a document nobody signed.
    day_boundary: str
    statement_vi: str
    #: True when this answer was replayed from a stored idempotency record rather than written now.
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ExportExecutionCommand:
    export_request_id: UUID
    approval_request_id: UUID
    principal: StaffPrincipal
    correlation_id: UUID
    #: The store the caller believes this request belongs to, when they have one to assert. It is
    #: never used to authorise anything -- membership is checked against the store read off the
    #: locked row -- but a caller whose URL named a different shop is mistaken about what they are
    #: releasing, and finding that out after the file exists is too late.
    expected_store_id: UUID | None = None
    executed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExportApprovalDisclosure:
    """What an `EXPORT_SANITIZED_DATA` envelope authorises, for the owner being asked to sign it.

    Every field is either read off the request row or re-derived from the column list in force
    right now -- nothing is read back off `export_requests.rendered_hash`. That is the same choice
    `execute` makes and for the same reason: the digest here is the digest of the content being
    displayed, so a console that compares it to the envelope's own copy is comparing what the owner
    is reading against what the owner would be signing. Widen `EXPORT_COLUMNS` after the envelope
    was raised and the two stop matching, which is exactly when the approve control must shut --
    and the release would refuse with `EXPORT_APPROVAL_NOT_BOUND` anyway.
    """

    approval_request_id: UUID
    export_request_id: UUID
    store_id: UUID
    dataset: str
    business_date: date
    business_timezone: str
    day_boundary: str
    columns: tuple[str, ...]
    excludes: tuple[str, ...]
    query_version: str
    statement_vi: str
    rendered_hash: str
    requested_at: datetime
    #: True when the caller is the staff member who defined this export, and therefore the one
    #: person separation of duty forbids from approving it.
    requested_by_you: bool


@dataclass(frozen=True, slots=True)
class ProducedExport:
    export_id: UUID
    export_request_id: UUID
    store_id: UUID
    approval_request_id: UUID
    business_date: date
    row_count: int
    content_hash: str
    query_version: str
    content_csv: str
    produced_at: datetime


class SanitizedExportRepository:
    """Raise an export request, then release it once against an owner's approval."""

    def __init__(self, idempotency: IdempotencyRepository | None = None) -> None:
        self._idempotency = idempotency or IdempotencyRepository()

    def request(self, connection: Any, command: ExportRequestCommand) -> StoredExportRequest:
        """Record what somebody wants exported, and hand back the binding an envelope needs.

        Nothing is exported here and no approval is raised here. The caller takes the digests this
        returns to `POST /internal/v1/approvals`, which is the one place envelopes are created; the
        split exists so that this module never has to decide who may approve what.

        Idempotent, like every other command that writes a row from a console tap. The stored
        response carries the column list and the query version as they were *at the time of the
        request*, not as they are now: a replay must return the document whose digest the owner is
        being asked to approve, and recomputing those from today's constants would hand back a
        description that no longer matches the stored `rendered_hash`.
        """
        if not command.principal.roles & EXPORT_ROLES or not command.principal.mfa_verified:
            raise ExportAuthorizationError(
                "requesting an export requires an owner or approver role with MFA"
            )
        requested_at = command.requested_at or datetime.now(UTC)
        business_date = command.business_date.isoformat()

        def create() -> dict[str, object]:
            export_request_id = uuid4()
            facts = ExportRequestFacts(
                dataset=command.dataset.value,
                store_id=command.store_id,
                business_date=business_date,
            )
            snapshot = canonical_document(facts)
            statement = _statement(facts)
            rendered = canonical_document(statement)

            def mutation(cursor: Any) -> None:
                require_store_membership(
                    cursor,
                    staff_user_id=command.principal.staff_user_id,
                    store_id=command.store_id,
                    error=ExportAuthorizationError,
                )
                cursor.execute(
                    """
                    INSERT INTO export_requests (
                        id, store_id, dataset, business_date, row_version,
                        snapshot_hash, rendered_hash, requested_by_staff_id, requested_at
                    ) VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s)
                    """,
                    (
                        export_request_id,
                        command.store_id,
                        command.dataset.value,
                        command.business_date,
                        snapshot.snapshot_hash,
                        rendered.snapshot_hash,
                        command.principal.staff_user_id,
                        requested_at,
                    ),
                )

            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="EXPORT_REQUEST",
                    aggregate_id=export_request_id,
                    aggregate_version=1,
                    event_type="EXPORT_REQUESTED",
                    event_payload={
                        "store_id": str(command.store_id),
                        "dataset": command.dataset.value,
                        "business_date": business_date,
                        "rendered_hash": rendered.snapshot_hash,
                    },
                    audit_action="EXPORT_REQUEST",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "export.requested.v1",
                            {
                                "export_request_id": str(export_request_id),
                                "store_id": str(command.store_id),
                            },
                            f"export-request:{export_request_id}",
                        ),
                    ),
                    occurred_at=requested_at,
                    audit_details={
                        "dataset": command.dataset.value,
                        "business_date": business_date,
                    },
                ),
                mutation,
            )
            return {
                "export_request_id": str(export_request_id),
                "snapshot_hash": snapshot.snapshot_hash,
                "rendered_hash": rendered.snapshot_hash,
                "requested_at": requested_at,
                "columns": list(EXPORT_COLUMNS),
                "excludes": list(EXPORT_EXCLUSIONS),
                "query_version": EXPORT_QUERY.label,
                "day_boundary": statement.day_boundary,
                "statement_vi": statement.statement_vi,
            }

        result = self._idempotency.execute(
            connection,
            IdempotentCommand(
                scope=f"export-request:{command.store_id}",
                key=command.idempotency_key,
                payload={
                    "store_id": str(command.store_id),
                    "dataset": command.dataset.value,
                    "business_date": business_date,
                },
                occurred_at=requested_at,
            ),
            create,
        )
        response = result.response
        return StoredExportRequest(
            export_request_id=UUID(str(response["export_request_id"])),
            store_id=command.store_id,
            dataset=command.dataset.value,
            business_date=command.business_date,
            resource_type=APPROVAL_RESOURCE_TYPES[ApprovalAction.EXPORT_SANITIZED_DATA],
            resource_version=1,
            snapshot_hash=str(response["snapshot_hash"]),
            rendered_hash=str(response["rendered_hash"]),
            policy_version=EXPORT_POLICY_VERSION,
            requested_at=_datetime(response["requested_at"]),
            columns=tuple(str(item) for item in _sequence(response["columns"])),
            excludes=tuple(str(item) for item in _sequence(response["excludes"])),
            query_version=str(response["query_version"]),
            day_boundary=_text(response, "day_boundary"),
            statement_vi=_text(response, "statement_vi"),
            replayed=result.replayed,
        )

    @staticmethod
    def read_for_approval(
        cursor: Any, *, approval_id: UUID, principal: StaffPrincipal
    ) -> ExportApprovalDisclosure | None:
        """The export one envelope is about, for a caller assigned to the shop that raised it.

        `None` when this approval is not an export envelope, and `None` for an approval that does
        not exist -- a caller learns nothing from the difference. Another store's export is refused
        by `require_store_membership` with the same opaque error every store-scoped read uses.

        The store comes from the `export_requests` row, never from the caller: this read is keyed
        by the approval, so a caller naming both an approval and a store could otherwise be told
        whether the two agree.

        No decision is taken here and nothing is authorised by it. This is the read that lets
        `#/approvals` put the business date, the column list and the stated exclusions in front of
        an owner before the approve control is reachable, which `RANGE-APPROVAL-VISIBILITY-001` set
        as the standard for this console and which an export needs more than a price does: a file
        that has left the building cannot be recalled.
        """
        cursor.execute(
            """
            SELECT e.id, e.store_id, e.dataset, e.business_date, e.requested_by_staff_id,
                   e.requested_at
            FROM approval_requests r
            JOIN export_requests e ON e.id = r.resource_id
            WHERE r.id = %s AND r.action = %s AND r.resource_type = %s
            """,
            (
                approval_id,
                ApprovalAction.EXPORT_SANITIZED_DATA.value,
                APPROVAL_RESOURCE_TYPES[ApprovalAction.EXPORT_SANITIZED_DATA],
            ),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        store_id = _uuid(row[1])
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=ExportAuthorizationError,
        )
        business_date = row[3]
        statement = _statement(
            ExportRequestFacts(
                dataset=str(row[2]),
                store_id=store_id,
                business_date=business_date.isoformat(),
            )
        )
        return ExportApprovalDisclosure(
            approval_request_id=approval_id,
            export_request_id=_uuid(row[0]),
            store_id=store_id,
            dataset=statement.facts.dataset,
            business_date=business_date,
            business_timezone=statement.business_timezone,
            day_boundary=statement.day_boundary,
            columns=statement.columns,
            excludes=statement.excludes,
            query_version=statement.query_version,
            statement_vi=statement.statement_vi,
            rendered_hash=canonical_document(statement).snapshot_hash,
            requested_at=row[5],
            requested_by_you=principal.staff_user_id == _uuid(row[4]),
        )

    def execute(self, connection: Any, command: ExportExecutionCommand) -> ProducedExport:
        """Produce the file once, against an approval that names this exact request."""
        if not command.principal.roles & EXPORT_ROLES or not command.principal.mfa_verified:
            raise ExportAuthorizationError(
                "carrying out an export requires an owner or approver role with MFA"
            )
        executed_at = command.executed_at or datetime.now(UTC)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT store_id, dataset, business_date, requested_by_staff_id
                FROM export_requests
                WHERE id = %s
                FOR UPDATE
                """,
                (command.export_request_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ExportStateError(
                    "export request is missing", reason_code="EXPORT_REQUEST_NOT_FOUND"
                )
            store_id = _uuid(row[0])
            defined_by = _uuid(row[3])
            # Membership on the same cursor that holds the row locked, exactly as the remedy and
            # settlement paths do. The store comes from the request row, never from the caller: this
            # route is keyed by the request identifier, so a URL-shape reading of it would miss the
            # scoping entirely.
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=store_id,
                error=ExportAuthorizationError,
            )
            if command.expected_store_id is not None and command.expected_store_id != store_id:
                # Checked here rather than after the bytes exist. The caller is a member of the
                # shop that owns this request, so this is a mislabelled URL and not an access
                # attempt -- but producing the file and then refusing to hand it back would leave a
                # `data_exports` row saying a release happened that nobody received, and the UNIQUE
                # constraint would then refuse the corrected attempt.
                raise ExportStateError(
                    "the export request belongs to a different store",
                    reason_code="EXPORT_REQUEST_STORE_MISMATCH",
                )
            binding = read_approval_binding(cursor, command.approval_request_id)
            business_date = row[2]
            # The digests the approval is checked against are re-derived here, from the request's
            # own facts and from `EXPORT_COLUMNS`/`EXPORT_EXCLUSIONS` as they are *at release*.
            #
            # `export_requests` stores both, and comparing the envelope's copy to the stored copy
            # would have looked like invariant 8 while proving nothing: two values frozen at request
            # time, equal to each other by construction, neither of them derived from the content
            # about to leave the building. Widen `EXPORT_COLUMNS` between the request and the
            # release and that comparison still passes, while the file carries a column the owner
            # never saw. Recomputing is what makes the approval bind the content: the digest is
            # taken over what is really being authorised, and anything that moved since the owner
            # signed now fails `EXPORT_APPROVAL_NOT_BOUND` instead of shipping. The stored pair
            # stays where it is, for display and for replaying the request -- storing rendered
            # content is fine, deciding from it is not.
            released = _statement(
                ExportRequestFacts(
                    dataset=str(row[1]),
                    store_id=store_id,
                    business_date=business_date.isoformat(),
                )
            )
            _require_export_approval(
                binding,
                store_id=store_id,
                export_request_id=command.export_request_id,
                snapshot_hash=canonical_document(released.facts).snapshot_hash,
                rendered_hash=canonical_document(released).snapshot_hash,
                at=executed_at,
            )
            # Separation of duty, checked against the person who DEFINED this export rather than
            # against whoever raised the envelope -- read on the same cursor that holds the request
            # row locked. `ApprovalRepository.decide` already refuses that decision (see
            # `approvals._RESOURCE_DEFINERS`), so reaching this refusal means a decision row exists
            # that `decide` would not have written. The file is the thing that cannot be recalled,
            # so the property is enforced again over the bytes rather than trusted from upstream.
            _require_separate_approver(
                cursor,
                approval_request_id=command.approval_request_id,
                defined_by=defined_by,
            )
            cursor.execute(
                _EXPORT_SQL,
                {
                    "store": store_id,
                    "zone": BUSINESS_TIMEZONE,
                    "business_date": business_date,
                },
            )
            rows = cursor.fetchall()

        content = _csv_bytes(rows)
        content_hash = f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"
        export_id = uuid4()

        def mutation(change_cursor: Any) -> None:
            change_cursor.execute(
                """
                INSERT INTO data_exports (
                    id, export_request_id, store_id, approval_request_id, query_version,
                    row_count, content_hash, produced_by_staff_id, produced_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    export_id,
                    command.export_request_id,
                    store_id,
                    command.approval_request_id,
                    EXPORT_QUERY.label,
                    len(rows),
                    content_hash,
                    command.principal.staff_user_id,
                    executed_at,
                ),
            )

        try:
            commit_material_change(
                connection,
                MaterialChange(
                    aggregate_type="DATA_EXPORT",
                    aggregate_id=export_id,
                    aggregate_version=1,
                    event_type="EXPORT_PRODUCED",
                    event_payload={
                        "export_request_id": str(command.export_request_id),
                        "store_id": str(store_id),
                        "approval_request_id": str(command.approval_request_id),
                        "row_count": len(rows),
                        "content_hash": content_hash,
                        "query_version": EXPORT_QUERY.label,
                    },
                    audit_action="EXPORT_PRODUCE",
                    actor_type="STAFF",
                    actor_id=command.principal.staff_user_id,
                    correlation_id=command.correlation_id,
                    outbox_events=(
                        OutboxEvent(
                            "export.produced.v1",
                            {
                                "export_id": str(export_id),
                                "store_id": str(store_id),
                                "row_count": len(rows),
                            },
                            f"export-produced:{command.export_request_id}",
                        ),
                    ),
                    occurred_at=executed_at,
                    # The audit event names the approval that authorised the release. An export with
                    # an audit row that does not say who allowed it is an export nobody can answer
                    # for, which is the whole reason this path is not a GET.
                    audit_details={
                        "approval_request_id": str(command.approval_request_id),
                        "export_request_id": str(command.export_request_id),
                        "row_count": len(rows),
                        "content_hash": content_hash,
                        "query_version": EXPORT_QUERY.label,
                    },
                ),
                mutation,
            )
        except UniqueViolation as error:
            # `data_exports.export_request_id` is UNIQUE, so one approved request releases one file.
            # The race and the retry land in the same place, which is the point of using the
            # constraint rather than a prior SELECT.
            raise ExportStateError(
                "this export has already been produced",
                reason_code="EXPORT_ALREADY_PRODUCED",
            ) from error

        return ProducedExport(
            export_id=export_id,
            export_request_id=command.export_request_id,
            store_id=store_id,
            approval_request_id=command.approval_request_id,
            business_date=business_date,
            row_count=len(rows),
            content_hash=content_hash,
            query_version=EXPORT_QUERY.label,
            content_csv=content,
            produced_at=executed_at,
        )


def _statement(facts: ExportRequestFacts) -> ExportStatement:
    """The document an owner approves, in the words they will read it in.

    The second sentence is the day boundary, and it is in the signed document rather than only in
    a comment because it is the difference between two numbers this console calls *tiền đã thu*.
    An owner who signs this is signing a file cut on `orders.created_at`; the counter's takings
    figure is cut on `order_settlements.attested_at`, and the two disagree by every order that was
    opened on one day and paid on another. Saying which one this is costs a sentence. Discovering
    it by subtracting two spreadsheets costs an afternoon and an argument about who is right.
    """
    return ExportStatement(
        facts=facts,
        business_timezone=BUSINESS_TIMEZONE,
        day_boundary=EXPORT_DAY_BOUNDARY,
        columns=EXPORT_COLUMNS,
        excludes=EXPORT_EXCLUSIONS,
        query_version=EXPORT_QUERY.label,
        statement_vi=(
            "Xuất bản sao hồ sơ của chính cửa hàng cho ngày "
            f"{facts.business_date} (theo giờ Việt Nam): mã đơn, trạng thái, mốc thời gian, "
            "số tiền đã thu và số tiền đã hoàn lại cho khách của những đơn MỞ trong ngày đó. "
            "Đơn đã thu tiền rồi bị huỷ và hoàn tiền vẫn hiện số tiền đã thu, kèm số tiền đã "
            "hoàn và lúc hoàn trên cùng dòng — cả hai việc đều đã xảy ra. "
            "Ngày được cắt theo lúc mở đơn, không phải theo lúc thu tiền: đơn mở hôm trước mà "
            "thu tiền hôm sau vẫn nằm ở ngày mở. Vì vậy tổng tiền trong tệp này không bằng ô "
            "“tiền đã thu hôm nay” trên màn hình Hôm nay — ô đó cộng theo lúc thu. Hai con số "
            "trả lời hai câu hỏi khác nhau, không phải một con số sai. "
            "Bản xuất không kèm lời khách phàn nàn, không kèm mô tả bằng chứng "
            "và không kèm mã liên hệ của khách — những phần đó nằm trong lịch xoá dữ liệu, và một "
            "bản sao mang ra ngoài sẽ không còn được lịch đó bảo vệ."
        ),
    )


def _require_export_approval(
    binding: ApprovalBinding | None,
    *,
    store_id: UUID,
    export_request_id: UUID,
    snapshot_hash: str,
    rendered_hash: str,
    at: datetime,
) -> None:
    """Prove the envelope in hand is an approved, unexpired export of *this* request.

    Six facts, each refused by name, following `_require_remedy_approval`: a single "not approved"
    would hide which one failed from the person waiting for the file. The two digest comparisons are
    invariant 8 — the owner approved a specific day of a specific shop rendered with a specific
    column list, and the only way to act on that approval is to still hold exactly that document.

    Both digests arrive re-derived from the request's facts and the column list in force at release,
    never read back off `export_requests`. The caller does that rather than this function so that
    the comparison here is unmistakably between what the owner signed and what is about to leave;
    a stored digest passed in would make this read the same and prove nothing.
    """
    if binding is None:
        raise ExportStateError(
            "this export has no approval envelope", reason_code="EXPORT_APPROVAL_REQUIRED"
        )
    if (
        binding.store_id != store_id
        or binding.action is not ApprovalAction.EXPORT_SANITIZED_DATA
        or binding.resource_type != APPROVAL_RESOURCE_TYPES[ApprovalAction.EXPORT_SANITIZED_DATA]
    ):
        raise ExportStateError(
            "the approval does not authorise an export in this store",
            reason_code="EXPORT_APPROVAL_NOT_BOUND",
        )
    if binding.resource_id != export_request_id or binding.resource_version != 1:
        raise ExportStateError(
            "the approval names a different export request",
            reason_code="EXPORT_APPROVAL_NOT_BOUND",
        )
    if not hmac.compare_digest(binding.snapshot_hash, snapshot_hash) or not hmac.compare_digest(
        binding.rendered_hash, rendered_hash
    ):
        raise ExportStateError(
            "the approval binds different content than this export request",
            reason_code="EXPORT_APPROVAL_NOT_BOUND",
        )
    if binding.status != "APPROVED":
        raise ExportStateError(
            "the owner has not approved this export", reason_code="EXPORT_APPROVAL_REQUIRED"
        )
    if at >= binding.expires_at:
        raise ExportStateError(
            "the owner's approval of this export has expired",
            reason_code="EXPORT_APPROVAL_EXPIRED",
        )


def _require_separate_approver(cursor: Any, *, approval_request_id: UUID, defined_by: UUID) -> None:
    """Refuse a release whose approval was decided by the person who defined the export.

    `_OWNER_FINANCIAL` carries `SEPARATION_OF_DUTY`, and until this check the only place that
    requirement was enforced was `_authorize_decision`, which compares the decider against
    `approval_requests.requested_by` -- the person who raised the ENVELOPE. For an export those are
    two different people by design: the request names the day, the shop and the column list, and
    the envelope is a later act anyone may perform against it. So the account that chose what
    leaves the building could approve it leaving, which is the property the module docstring
    claimed and the code did not have.

    The `APPROVED` decision is what is checked, not merely the latest one. An envelope reaches here
    only with `binding.status == "APPROVED"`, and an approval is decided once, so in practice there
    is exactly one row; naming the decision explicitly keeps that true if a rejected-then-reopened
    shape is ever added rather than letting this silently read the wrong row.

    A decision that cannot be found is refused, not waved through. Reaching this point means the
    envelope is approved, so a missing decision row is a fault in the system rather than in what
    the operator typed -- and the safe direction for a fault on the path that releases data is to
    release nothing.
    """
    cursor.execute(
        """
        SELECT decided_by
        FROM approval_decisions
        WHERE approval_request_id = %s AND decision = 'APPROVED'
        ORDER BY decided_at DESC
        LIMIT 1
        """,
        (approval_request_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ExportStateError(
            "the approval for this export has no recorded decision",
            reason_code="EXPORT_APPROVAL_REQUIRED",
        )
    if _uuid(row[0]) == defined_by:
        raise ExportStateError(
            "the export was approved by the same staff member who defined it",
            reason_code="EXPORT_APPROVAL_SELF_DECIDED",
        )


def _text(response: dict[str, object], key: str) -> str:
    """One string off a stored idempotency response, refusing a record that does not carry it.

    A replay has to return the document whose digest the owner is being asked to approve. A record
    written by an earlier shape of this module carries no `statement_vi`, and composing one now
    from today's constants would hand back a description that no longer matches the stored
    `rendered_hash` -- the exact substitution invariant 8 exists to prevent. So a missing or
    non-string value is `EXPORT_REQUEST_CORRUPT`, and the operator raises a fresh request under a
    new idempotency key rather than reading a sentence nobody signed.
    """
    value = response.get(key)
    if not isinstance(value, str):
        raise ExportStateError(
            "the stored export request is not readable", reason_code="EXPORT_REQUEST_CORRUPT"
        )
    return value


def _csv_bytes(rows: list[tuple[Any, ...]]) -> str:
    """Render the fetched rows as CSV, converting nothing and computing nothing.

    Two rendering rules, both house style rather than taste. A null money cell is written empty and
    never as `0`: an order with no settlement has not been paid nothing, it has not been settled,
    and a spreadsheet that reads the two the same way will report a day's takings as larger than
    they were. A timestamp is written in ISO-8601 with its offset, so a reader in another timezone
    cannot silently reinterpret the shop's day.

    `\\r\\n` is the CSV dialect's own line terminator and is left alone; the digest is taken over
    exactly these bytes.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return buffer.getvalue()


#: The four characters that make a spreadsheet cell executable, plus the two whitespace prefixes
#: Excel strips before deciding. A cell beginning with any of them is a formula when the file is
#: opened, and `#/gaps` recorded that as a requirement of this capability before it was built.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: object) -> str:
    """One CSV cell, refusing anything a spreadsheet would execute.

    The usual mitigation is to prefix a dangerous cell with an apostrophe, which changes the shop's
    own data to work around a spreadsheet's behaviour. This column set has no legitimate value that
    could begin with one of those characters -- a UUID, an uppercase enum token, an ISO timestamp
    and a non-negative integer of VND -- so the honest response to one is that something is wrong
    with the data, not with the formatting.

    The boolean branch below has no column behind it since `incident_open` left `EXPORT_COLUMNS`,
    and it stays for the same reason the formula guard does: this list is expected to grow, and a
    renderer that met an unrendered type would otherwise reach `str(True)` and write `True` into a
    file that says `true` everywhere else.

    So it fails closed: no file is produced, and the refusal names the cell's own text. That stays
    correct if somebody later widens `EXPORT_COLUMNS` to a field that can hold arbitrary text --
    they will meet this rather than ship an executable file -- which is the direction a guard on a
    list that is expected to grow has to fail in.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = value.isoformat() if isinstance(value, datetime) else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        raise ExportStateError(
            "a value in this export would be executed as a formula by a spreadsheet",
            reason_code="EXPORT_CELL_NOT_SAFE",
        )
    return text


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _datetime(value: object) -> datetime:
    """A timestamp from a stored idempotency response; JSON round-trips it as an ISO string."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ExportStateError(
            "the stored export request is not readable", reason_code="EXPORT_REQUEST_CORRUPT"
        )
    return value


__all__ = [
    "BUSINESS_TIMEZONE",
    "EXPORT_COLUMNS",
    "EXPORT_DAY_BOUNDARY",
    "EXPORT_EXCLUSIONS",
    "EXPORT_POLICY_VERSION",
    "EXPORT_QUERY",
    "EXPORT_ROLES",
    "ExportApprovalDisclosure",
    "ExportAuthorizationError",
    "ExportDataset",
    "ExportExecutionCommand",
    "ExportRequestCommand",
    "ExportStateError",
    "ProducedExport",
    "SanitizedExportRepository",
    "StoredExportRequest",
]
