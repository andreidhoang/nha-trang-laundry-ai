"""Atomic persistence for immutable quote revision snapshots."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_domain.canonical import CanonicalDocument, canonical_document
from nha_trang_laundry_domain.quotes import ImmutableQuoteSnapshot, verify_quote_snapshot

from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change


class QuoteStateError(ValueError):
    """Raised for stale quote state or a non-sequential revision."""


class QuoteIntegrityError(ValueError):
    """Raised when stored quote JSON no longer matches its bound snapshot hash."""


@dataclass(frozen=True)
class QuoteRevisionCommand:
    store_id: UUID
    bound_order_request_id: UUID
    snapshot: ImmutableQuoteSnapshot
    expected_current_revision: int
    expected_row_version: int
    created_by: UUID
    correlation_id: UUID
    occurred_at: datetime | None = None
    # The staff command path audits as STAFF; the agent tool path must not impersonate it.
    actor_type: str = "STAFF"


@dataclass(frozen=True, slots=True)
class QuoteContainerBinding:
    """The mutable container pointer for one bound order request's quote."""

    quote_id: UUID
    current_revision: int
    row_version: int


@dataclass(frozen=True)
class StoredQuoteRevision:
    quote_id: UUID
    revision: int
    finality: str
    status: str
    document: CanonicalDocument


@dataclass(frozen=True, slots=True)
class QuoteSummary:
    """A listed revision. The optional fields are optional in the schema, and were not here.

    `QUOTE-COMMAND-001` found this by creating the first quote any application had ever created.
    Migration 0005 declares `display_total_min_vnd`, `display_total_max_vnd` and `valid_until`
    NULL-able, and ties the display totals to `delivery_fee_vnd` with a CHECK — a quote whose
    delivery fee is unresolved is *required* to have no display total. This dataclass and its reader
    assumed otherwise, so the first such quote made `GET /internal/v1/stores/{id}/quotes` raise
    `TypeError: int() argument must be ... not 'NoneType'` and return 500. Nothing caught it earlier
    because no code path could produce a quote to list.
    """

    quote_id: UUID
    revision: int
    row_version: int
    finality: str
    status: str
    snapshot_hash: str
    display_total_min_vnd: int | None
    display_total_max_vnd: int | None
    valid_until: datetime | None


class QuoteRepository:
    """Create-only quote revisions with optimistic container concurrency."""

    def create_revision(self, connection: Any, command: QuoteRevisionCommand) -> None:
        snapshot = command.snapshot
        data = snapshot.data
        if not verify_quote_snapshot(snapshot):
            raise QuoteIntegrityError("quote snapshot failed canonical verification")
        if (
            command.expected_current_revision < 0
            or command.expected_row_version < 0
            or data.revision != command.expected_current_revision + 1
            or (data.revision == 1 and command.expected_row_version != 0)
        ):
            raise QuoteStateError("quote revision must be sequential")
        occurred_at = command.occurred_at or datetime.now(UTC)

        def mutation(cursor: Any) -> None:
            if data.revision == 1:
                cursor.execute(
                    """
                    INSERT INTO quotes (
                        id, store_id, bound_order_request_id, lifecycle, current_revision,
                        row_version, created_at
                    ) VALUES (%s, %s, %s, 'OPEN', 1, 1, %s)
                    """,
                    (data.quote_id, command.store_id, command.bound_order_request_id, occurred_at),
                )
            else:
                cursor.execute(
                    """
                    UPDATE quotes
                    SET current_revision = %s, row_version = row_version + 1
                    WHERE id = %s AND lifecycle = 'OPEN' AND current_revision = %s
                        AND row_version = %s
                    RETURNING id
                    """,
                    (
                        data.revision,
                        data.quote_id,
                        command.expected_current_revision,
                        command.expected_row_version,
                    ),
                )
                if cursor.fetchone() is None:
                    raise QuoteStateError("quote is missing, closed, or stale")
            totals = data.totals
            cursor.execute(
                """
                INSERT INTO quote_revisions (
                    quote_id, revision, finality, status, snapshot, snapshot_hash,
                    calculation_engine_version, calculation_engine_hash,
                    list_service_subtotal_min_vnd, list_service_subtotal_max_vnd,
                    discount_amount_min_vnd, discount_amount_max_vnd,
                    net_service_subtotal_min_vnd, net_service_subtotal_max_vnd,
                    delivery_fee_vnd, approved_surcharge_vnd,
                    display_total_min_vnd, display_total_max_vnd, approval_id,
                    priced_at, valid_until, customer_estimate_acknowledged_at,
                    created_by, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s::jsonb, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    data.quote_id,
                    data.revision,
                    data.finality.value,
                    data.status.value,
                    snapshot.document.canonical_json.decode("utf-8"),
                    snapshot.document.snapshot_hash,
                    data.calculation_engine_version,
                    data.calculation_engine_hash,
                    totals.list_service_subtotal_min_vnd,
                    totals.list_service_subtotal_max_vnd,
                    totals.discount_amount_min_vnd,
                    totals.discount_amount_max_vnd,
                    totals.net_service_subtotal_min_vnd,
                    totals.net_service_subtotal_max_vnd,
                    totals.delivery_fee_vnd,
                    totals.approved_surcharge_vnd,
                    totals.display_total_min_vnd,
                    totals.display_total_max_vnd,
                    data.approval_id,
                    data.priced_at,
                    data.valid_until,
                    data.customer_estimate_acknowledged_at,
                    command.created_by,
                    occurred_at,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="QUOTE",
                aggregate_id=data.quote_id,
                aggregate_version=data.revision,
                event_type="QUOTE_REVISION_CREATED",
                event_payload={
                    "revision": data.revision,
                    "snapshot_hash": snapshot.document.snapshot_hash,
                    "finality": data.finality.value,
                },
                audit_action="QUOTE_CALCULATE",
                actor_type=command.actor_type,
                actor_id=command.created_by,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "quote.revision_created.v1",
                        {
                            "quote_id": str(data.quote_id),
                            "revision": data.revision,
                            "snapshot_hash": snapshot.document.snapshot_hash,
                        },
                        f"quote:{data.quote_id}:revision:{data.revision}",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            mutation,
        )

    @staticmethod
    def find_container(
        cursor: Any, *, store_id: UUID, bound_order_request_id: UUID
    ) -> QuoteContainerBinding | None:
        """Locate the one quote container a bound order request may have, if it exists."""
        cursor.execute(
            """
            SELECT id, current_revision, row_version FROM quotes
            WHERE store_id = %s AND bound_order_request_id = %s
            """,
            (store_id, bound_order_request_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        identifier = row[0] if isinstance(row[0], UUID) else UUID(str(row[0]))
        return QuoteContainerBinding(identifier, int(row[1]), int(row[2]))

    @staticmethod
    def get_revision(cursor: Any, quote_id: UUID, revision: int) -> StoredQuoteRevision | None:
        cursor.execute(
            """
            SELECT finality, status, snapshot, snapshot_hash
            FROM quote_revisions
            WHERE quote_id = %s AND revision = %s
            """,
            (quote_id, revision),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        payload = row[2]
        if not isinstance(payload, dict):
            raise QuoteIntegrityError("stored quote snapshot is not an object")
        rebuilt = canonical_document(payload, exclude_volatile=False)
        stored_hash = str(row[3])
        if not hmac.compare_digest(rebuilt.snapshot_hash, stored_hash):
            raise QuoteIntegrityError("stored quote snapshot hash mismatch")
        # Round-trip JSON once to ensure no driver-specific non-JSON values survived.
        json.loads(rebuilt.canonical_json)
        return StoredQuoteRevision(quote_id, revision, str(row[0]), str(row[1]), rebuilt)

    @staticmethod
    def list_for_store(
        cursor: Any, *, store_id: UUID, principal: StaffPrincipal, limit: int
    ) -> tuple[QuoteSummary, ...]:
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        if not 1 <= limit <= 200:
            raise ValueError("quote list limit must be between 1 and 200")
        cursor.execute(
            """
            SELECT quote.id, quote.current_revision, quote.row_version,
                   revision.finality, revision.status, revision.snapshot_hash,
                   revision.display_total_min_vnd, revision.display_total_max_vnd,
                   revision.valid_until
            FROM quotes AS quote
            JOIN quote_revisions AS revision
              ON revision.quote_id = quote.id
             AND revision.revision = quote.current_revision
            WHERE quote.store_id = %s
            ORDER BY quote.created_at DESC, quote.id DESC
            LIMIT %s
            """,
            (store_id, limit),
        )
        return tuple(
            QuoteSummary(
                UUID(str(row[0])),
                int(row[1]),
                int(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                _optional_amount(row[6]),
                _optional_amount(row[7]),
                _optional_timestamp(row[8]),
            )
            for row in cursor.fetchall()
        )


def _optional_amount(value: object) -> int | None:
    """An absent display total is a fact about the quote, not a corrupt row."""
    return None if value is None else int(value)  # type: ignore[call-overload]


def _optional_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise QuoteIntegrityError("stored quote timestamp is invalid")
    return value


@dataclass(frozen=True)
class QuoteAcceptanceCommand:
    """One staff member's record that a customer accepted this exact revision.

    `DEC-021`, resolved 2026-08-25. The shape follows `order_settlements`: a fact a named person
    witnessed at the counter, written once, never edited. It names the revision *and* its digest, so
    the attestation cannot later be read as being about different content.
    """

    store_id: UUID
    quote_id: UUID
    accepted_revision: int
    accepted_snapshot_hash: str
    final_revision: int
    display_total_vnd: int
    accepted_by: UUID
    correlation_id: UUID
    policy_version: str
    accepted_at: datetime | None = None


class QuoteAcceptanceRepository:
    """Write and read the attestation that stands behind an exact price."""

    def record(self, connection: Any, command: QuoteAcceptanceCommand) -> UUID:
        """Write the attestation, or refuse because this revision was already accepted.

        The UNIQUE on `(quote_id, accepted_revision)` makes acceptance single-shot. A second attempt
        is a conflict rather than a second attestation, which matters because two rows would leave
        no answer to "who chốt this" -- the question the owner's decision exists to make answerable.
        """

        acceptance_id = uuid4()
        occurred_at = command.accepted_at or datetime.now(UTC)

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO quote_acceptances (
                    id, store_id, quote_id, accepted_revision, accepted_snapshot_hash,
                    final_revision, display_total_vnd, accepted_by, accepted_at, correlation_id,
                    policy_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (quote_id, accepted_revision) DO NOTHING
                RETURNING id
                """,
                (
                    acceptance_id,
                    command.store_id,
                    command.quote_id,
                    command.accepted_revision,
                    command.accepted_snapshot_hash,
                    command.final_revision,
                    command.display_total_vnd,
                    command.accepted_by,
                    occurred_at,
                    command.correlation_id,
                    command.policy_version,
                ),
            )
            if cursor.fetchone() is None:
                raise QuoteStateError("this quote revision has already been accepted")

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="QUOTE_ACCEPTANCE",
                aggregate_id=command.quote_id,
                aggregate_version=command.accepted_revision,
                event_type="QUOTE_ACCEPTED_BY_CUSTOMER",
                event_payload={
                    "acceptance_id": str(acceptance_id),
                    "accepted_revision": command.accepted_revision,
                    "accepted_snapshot_hash": command.accepted_snapshot_hash,
                    "display_total_vnd": command.display_total_vnd,
                    "policy_version": command.policy_version,
                },
                audit_action="QUOTE_ACCEPT",
                actor_type="STAFF",
                actor_id=command.accepted_by,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "quote.accepted.v1",
                        {
                            "quote_id": str(command.quote_id),
                            "acceptance_id": str(acceptance_id),
                        },
                        # Revision-scoped. Keyed on the quote alone until 2026-08-29, which made
                        # the *second* acceptance of any quote a permanent 500: outbox_events'
                        # idempotency_key is UNIQUE, so "thêm cái áo này nữa" -- reprice, customer
                        # agrees again -- collided with the first acceptance forever. The
                        # acceptance row itself was always unique per (quote, revision); only this
                        # key disagreed.
                        f"quote:{command.quote_id}:acceptance:{command.accepted_revision}",
                    ),
                ),
                occurred_at=occurred_at,
            ),
            mutation,
        )
        return acceptance_id
