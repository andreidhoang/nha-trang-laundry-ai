"""Keep the amounts a `SET_RANGE_PRICE` envelope is about, so its approver can read them.

`RANGE-APPROVAL-VISIBILITY-001`. `RANGE-PRICE-001` shipped with the proposed amounts persisted
nowhere: the envelope held a `rendered_hash` and the owner, following the queue's link to the
quote, saw the published band rather than the number. The owner's approval is the item's only
second-party control over that number, and it was being given blind.

**This module stores content for a human to read. It does not verify anything.** The two are
deliberately separate, and conflating them would undo the thing `RANGE-PRICE-001` got right:
`apply_range_prices` re-derives the rendered digest from the amounts the caller is holding and
refuses unless it equals the one the owner approved. That check does not consult this table and
must not, because a digest compared against a stored copy of its own input proves nothing.

**What the read here does check, and why it is not that.** Before handing amounts to a screen, it
re-derives `range_price_rendered_document` from the stored rows and compares the result with the
`rendered_hash` on `approval_requests` -- the envelope's own, immutable copy. That is not
authorisation; nothing is permitted by it. It answers a display question: *are these the amounts
the envelope is about?* A row that fails it is withheld entirely rather than shown with a caveat,
because a number on an approval screen is read as the number being approved and there is no caveat
that makes a wrong one safe. Storing a copy for display is only worth doing if the copy cannot
silently disagree with the original.

**Ordering, and what a failure between the two writes leaves behind.** The envelope is created
first and these rows second, exactly as `remedies.propose` does and for the same reason:
`range_price_proposals.approval_id` is a foreign key. The residue of a crash between them is an
envelope with no readable amounts -- which the approvals surface renders as *undecidable*, with no
approve control at all, and which expires on its own ten-minute TTL. The failure mode of this
module is therefore an approval nobody can give, never an approval given blind.
"""

from __future__ import annotations

import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from nha_trang_laundry_domain.range_prices import (
    RangePriceAttestation,
    RangePriceChoice,
    range_price_rendered_document,
)

from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Emitted when the stored amounts do not re-derive to the digest the envelope binds. Invariant 8
#: owns it: the approval binds an exact rendered-content hash, so content that is not that hash is
#: not the content of this approval and may not be presented as though it were.
RANGE_PRICE_PROPOSAL_CONTENT_MISMATCH: Final = "RANGE_PRICE_PROPOSAL_CONTENT_MISMATCH"

#: Not a member of `outbox.INTERNAL_EVENT_TYPES`, on purpose and like
#: `remedy.proposal_recorded.v1`: nothing downstream acts on a proposal being recorded, so the row
#: is written for the audit trail and never claimed. `commit_material_change` requires at least one
#: outbox event, and an event that is honest about having no consumer is better than a material
#: change that pretends not to be one.
RANGE_PRICE_PROPOSAL_RECORDED: Final = "range_price.proposal_recorded.v1"


class RangePriceProposalIntegrityError(ValueError):
    """The stored amounts are not the content the envelope binds. Fail closed; show nothing."""


@dataclass(frozen=True, slots=True)
class ProposedRangePriceLine:
    """One line of a proposal: the published bound, and the exact amount chosen inside it."""

    service_code: str
    band_minimum_vnd: int
    band_maximum_vnd: int
    proposed_amount_vnd: int


@dataclass(frozen=True, slots=True)
class RangePriceProposalRecord:
    """Everything an approver needs in order to be approving a number rather than a digest."""

    approval_id: UUID
    store_id: UUID
    quote_id: UUID
    revision: int
    pricebook_version_id: UUID
    pricebook_version: int
    rendered_hash: str
    proposed_by: UUID
    proposed_at: datetime
    lines: tuple[ProposedRangePriceLine, ...]


@dataclass(frozen=True)
class RecordRangePriceProposalCommand:
    """The amounts to keep, and the envelope they were raised for.

    Every field is server-derived at the call site: the bands come from the stored revision through
    `stored_price_bands`, the pricebook version from the revision's own configuration snapshot, and
    the digest from the domain. A caller supplies amounts and nothing else, which is the same
    division `propose_range_prices` already keeps.
    """

    approval_id: UUID
    store_id: UUID
    quote_id: UUID
    revision: int
    pricebook_version_id: UUID
    pricebook_version: int
    rendered_hash: str
    proposed_by: UUID
    correlation_id: UUID
    lines: Sequence[ProposedRangePriceLine]
    proposed_at: datetime | None = None


class RangePriceProposalRepository:
    """Write the proposed amounts once, and read them back only when they still check out."""

    @staticmethod
    def record(connection: Any, command: RecordRangePriceProposalCommand) -> None:
        """Persist one proposal and its lines, atomically with its event, audit and outbox rows.

        Not idempotent by an idempotency key of its own, and it does not need to be: the primary
        key is the approval id, `ApprovalRepository.request` is already idempotent, and the caller
        skips this write when the envelope came back replayed. A race that gets past that check
        meets the primary key, which is the guard that does not depend on anybody remembering it.
        """

        if not command.lines:
            # A `SET_RANGE_PRICE` envelope with no amounts is the blind approval this item exists
            # to end. Refusing here means such an envelope cannot acquire a display record that
            # would make it look reviewable.
            raise ValueError("a range price proposal must carry at least one amount")
        proposed_at = command.proposed_at or datetime.now(UTC)
        lines = tuple(command.lines)

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO range_price_proposals (
                    approval_id, store_id, quote_id, revision, pricebook_version_id,
                    pricebook_version, rendered_hash, proposed_by, proposed_at, correlation_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    command.approval_id,
                    command.store_id,
                    command.quote_id,
                    command.revision,
                    command.pricebook_version_id,
                    command.pricebook_version,
                    command.rendered_hash,
                    command.proposed_by,
                    proposed_at,
                    command.correlation_id,
                ),
            )
            for line in lines:
                cursor.execute(
                    """
                    INSERT INTO range_price_proposal_amounts (
                        approval_id, service_code, band_minimum_vnd, band_maximum_vnd,
                        proposed_amount_vnd
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        command.approval_id,
                        line.service_code,
                        line.band_minimum_vnd,
                        line.band_maximum_vnd,
                        line.proposed_amount_vnd,
                    ),
                )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="RANGE_PRICE_PROPOSAL",
                aggregate_id=command.approval_id,
                aggregate_version=1,
                event_type="RANGE_PRICE_PROPOSAL_RECORDED",
                event_payload={
                    "quote_id": str(command.quote_id),
                    "revision": command.revision,
                    "rendered_hash": command.rendered_hash,
                    # The amounts are in the domain event because the event log is the other place
                    # this content is readable, and a log that recorded only a digest would leave
                    # an auditor asking the same question the owner was asking.
                    "amounts": [
                        {
                            "service_code": line.service_code,
                            "proposed_amount_vnd": line.proposed_amount_vnd,
                        }
                        for line in sorted(lines, key=lambda item: item.service_code)
                    ],
                },
                audit_action="RANGE_PRICE_PROPOSE",
                actor_type="STAFF",
                actor_id=command.proposed_by,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        RANGE_PRICE_PROPOSAL_RECORDED,
                        {
                            "approval_request_id": str(command.approval_id),
                            "quote_id": str(command.quote_id),
                            "revision": command.revision,
                        },
                        f"range-price:{command.approval_id}:proposed",
                    ),
                ),
                occurred_at=proposed_at,
            ),
            mutation,
        )

    @staticmethod
    def read(
        cursor: Any, *, approval_id: UUID, principal: StaffPrincipal
    ) -> RangePriceProposalRecord | None:
        """The amounts one envelope is about, for a caller assigned to the shop that raised it.

        `None` when no such proposal exists, and `None` for another store's proposal too, after
        `require_store_membership` has refused: a caller learns nothing from the difference between
        a proposal that is not theirs and one that is not there.

        The store comes from the row, never from the request. A caller naming both an approval and
        a store could otherwise be told whether the two agree, which is the cross-store disclosure
        `require_store_membership` exists to prevent.
        """

        cursor.execute(
            """
            SELECT p.store_id, p.quote_id, p.revision, p.pricebook_version_id,
                   p.pricebook_version, p.rendered_hash, p.proposed_by, p.proposed_at,
                   r.rendered_hash
            FROM range_price_proposals p
            JOIN approval_requests r ON r.id = p.approval_id
            WHERE p.approval_id = %s
            """,
            (approval_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        store_id = _uuid(row[0])
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        cursor.execute(
            """
            SELECT service_code, band_minimum_vnd, band_maximum_vnd, proposed_amount_vnd
            FROM range_price_proposal_amounts
            WHERE approval_id = %s
            ORDER BY service_code
            """,
            (approval_id,),
        )
        lines = tuple(
            ProposedRangePriceLine(
                service_code=str(line[0]),
                band_minimum_vnd=int(line[1]),
                band_maximum_vnd=int(line[2]),
                proposed_amount_vnd=int(line[3]),
            )
            for line in cursor.fetchall()
        )
        record = RangePriceProposalRecord(
            approval_id=approval_id,
            store_id=store_id,
            quote_id=_uuid(row[1]),
            revision=int(row[2]),
            pricebook_version_id=_uuid(row[3]),
            pricebook_version=int(row[4]),
            rendered_hash=str(row[5]),
            proposed_by=_uuid(row[6]),
            proposed_at=row[7],
            lines=lines,
        )
        _require_stored_content_matches_envelope(record, envelope_rendered_hash=str(row[8]))
        return record


def _require_stored_content_matches_envelope(
    record: RangePriceProposalRecord, *, envelope_rendered_hash: str
) -> None:
    """Re-derive the digest from the stored amounts and demand the envelope's own copy.

    This is a display guard, not an authorisation. Nothing is permitted by passing it; what fails
    it is withheld. The point is that the copy kept for a human to read cannot drift away from the
    content the envelope binds without the drift being visible -- and if it ever does drift, the
    approval surface must show nothing rather than a number that is not the one being approved.

    `hmac.compare_digest` for the same reason every other hash comparison in this repository uses
    it: these are attacker-influenced values and a timing-variable comparison of them is a habit
    worth not having, even where the margin is small.
    """

    derived = range_price_rendered_document(
        RangePriceAttestation(
            quote_id=record.quote_id,
            revision=record.revision,
            pricebook_version_id=record.pricebook_version_id,
            pricebook_version=record.pricebook_version,
            choices=tuple(
                RangePriceChoice(line.service_code, line.proposed_amount_vnd)
                for line in record.lines
            ),
        )
    ).snapshot_hash
    if not hmac.compare_digest(derived, envelope_rendered_hash) or not hmac.compare_digest(
        record.rendered_hash, envelope_rendered_hash
    ):
        raise RangePriceProposalIntegrityError(RANGE_PRICE_PROPOSAL_CONTENT_MISMATCH)


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "RANGE_PRICE_PROPOSAL_CONTENT_MISMATCH",
    "RANGE_PRICE_PROPOSAL_RECORDED",
    "ProposedRangePriceLine",
    "RangePriceProposalIntegrityError",
    "RangePriceProposalRecord",
    "RangePriceProposalRepository",
    "RecordRangePriceProposalCommand",
]
