"""Read back what `REMEDY-001` wrote: an incident's proposals, and the credits an order issued.

`READ-PATHS-001`. `remedies.py` built the write path first and the read path never followed, which
left two facts the counter needs only in the database:

* **The proposals recorded on an incident.** The remedies screen could show only the proposal its
  own session had just sent, so a proposal made yesterday, by a colleague, or before `DEC-031` (a
  `POLICY_UNRESOLVED` loss with no figure) was invisible to the person answering the customer.
* **The credits an order issued.** A remedy credit is a bearer instrument (`DEC-015` declines the
  customer record that would hold a balance), and since `DEC-030` (2026-09-25) a credit can wait
  unspent while a promotion applies -- so a customer who lost the code lost the credit. The customer
  still holds the paper ticket of the order the credit was issued on, and the counter can already
  find that order by its ticket number. This read goes from that order to its credits.

Neither read creates a customer record: both are keyed by artefacts the shop already holds (an
incident, an order), and neither links two visits.

**Separate from `remedies.py` on purpose.** That module owns the money decisions and changes with
every ruling; a read that cannot decide anything has no business sharing a file with code that can.

**Authorization.** The counter roles that may already propose a remedy and spend a credit
(`REMEDY_ROLES`, with MFA) may read them, and only in a store they are an assigned member of. The
membership check runs against the store named in the request, and every row is then selected with
that store in its predicate, so another store's order or incident is indistinguishable from one that
does not exist. A wrong role and a non-member are the same `StoreAccessError`.

Nothing here reads a clock: the one comparison against time -- whether an owner envelope still
waiting has run out -- takes `now` from the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from nha_trang_laundry_db.identity import StaffPrincipal
from nha_trang_laundry_db.remedies import REMEDY_ROLES
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership

#: `remedy_credits` has no expiry column and no state beyond "spent or not": a credit is unspent
#: until `redeemed_at` is written by the order that spends it, and nothing else moves it.
CREDIT_UNUSED: Final = "UNUSED"
CREDIT_REDEEMED: Final = "REDEEMED"

#: A safety bound. An incident gathers a few proposals and an order a credit or two; a result this
#: long is reported as cut rather than presented as complete.
REMEDY_READ_LIMIT: Final = 200


class RemedyReadNotFoundError(LookupError):
    """The order or incident is not in the named store, or does not exist. One answer for both."""


@dataclass(frozen=True, slots=True)
class OrderRemedyCredit:
    """One credit issued against an order, through the remedy that paid it."""

    credit_id: UUID
    remedy_proposal_id: UUID
    incident_id: UUID
    kind: str
    amount_vnd: int
    #: `UNUSED` or `REDEEMED`. There is no expired state because the schema records no expiry.
    status: str
    issued_at: datetime
    redeemed_at: datetime | None
    redeemed_quote_id: UUID | None
    redeemed_quote_revision: int | None


@dataclass(frozen=True, slots=True)
class OrderRemedyCredits:
    order_id: UUID
    credits: tuple[OrderRemedyCredit, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class IncidentRemedyProposal:
    """One proposal as recorded, with its owner envelope's state when it has one."""

    proposal_id: UUID
    kind: str
    status: str
    amount_vnd: int | None
    ceiling_vnd: int | None
    order_line_id: str | None
    attested_late_by_minutes: int | None
    window_closes_at: datetime | None
    approval_id: UUID | None
    #: The envelope's stored state (`approval_request_states.status`), `None` without an envelope.
    approval_status: str | None
    approval_expires_at: datetime | None
    #: True only for an envelope still `REQUESTED` whose expiry has passed at `now`: the owner can
    #: no longer decide it, whatever the stored status still says. `None` without an envelope.
    approval_lapsed: bool | None
    proposed_by: UUID
    proposed_by_name: str
    proposed_at: datetime
    executed_at: datetime | None
    #: The credit an executed money remedy issued, if any.
    credit_id: UUID | None


@dataclass(frozen=True, slots=True)
class IncidentRemedyProposals:
    incident_id: UUID
    order_id: UUID | None
    proposals: tuple[IncidentRemedyProposal, ...]
    truncated: bool


class RemedyReadRepository:
    """Store-scoped reads over `remedy_proposals` and `remedy_credits`. Writes nothing."""

    @staticmethod
    def list_order_credits(
        cursor: Any, *, store_id: UUID, order_id: UUID, principal: StaffPrincipal
    ) -> OrderRemedyCredits:
        _require_remedy_reader(principal)
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        cursor.execute("SELECT 1 FROM orders WHERE id = %s AND store_id = %s", (order_id, store_id))
        if cursor.fetchone() is None:
            raise RemedyReadNotFoundError("order is not in this store")
        cursor.execute(
            """
            SELECT c.id, c.remedy_proposal_id, p.incident_id, p.kind, c.amount_vnd, c.issued_at,
                   c.redeemed_at, c.redeemed_quote_id, c.redeemed_quote_revision
            FROM remedy_credits c
            JOIN remedy_proposals p ON p.id = c.remedy_proposal_id
            WHERE c.store_id = %s AND c.issued_from_order_id = %s
            ORDER BY c.issued_at, c.id
            LIMIT %s
            """,
            (store_id, order_id, REMEDY_READ_LIMIT + 1),
        )
        rows = cursor.fetchall()
        return OrderRemedyCredits(
            order_id=order_id,
            credits=tuple(
                OrderRemedyCredit(
                    credit_id=_uuid(row[0]),
                    remedy_proposal_id=_uuid(row[1]),
                    incident_id=_uuid(row[2]),
                    kind=str(row[3]),
                    amount_vnd=int(row[4]),
                    status=CREDIT_UNUSED if row[6] is None else CREDIT_REDEEMED,
                    issued_at=row[5],
                    redeemed_at=row[6],
                    redeemed_quote_id=None if row[7] is None else _uuid(row[7]),
                    redeemed_quote_revision=None if row[8] is None else int(row[8]),
                )
                for row in rows[:REMEDY_READ_LIMIT]
            ),
            truncated=len(rows) > REMEDY_READ_LIMIT,
        )

    @staticmethod
    def list_incident_proposals(
        cursor: Any,
        *,
        store_id: UUID,
        incident_id: UUID,
        principal: StaffPrincipal,
        now: datetime,
    ) -> IncidentRemedyProposals:
        _require_remedy_reader(principal)
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        cursor.execute(
            "SELECT order_id FROM customer_incidents WHERE id = %s AND store_id = %s",
            (incident_id, store_id),
        )
        incident = cursor.fetchone()
        if incident is None:
            raise RemedyReadNotFoundError("incident is not in this store")
        # LEFT JOINs: a proposal without an envelope (staff-authorised, a rewash, a pre-DEC-031
        # loss) and one that issued no credit must both be listed. Oldest first, the order the
        # proposals were made in, so an owner reading the list reads the history as it happened.
        cursor.execute(
            """
            SELECT p.id, p.kind, p.status, p.amount_vnd, p.ceiling_vnd, p.order_line_id,
                   p.attested_late_by_minutes, p.window_closes_at, p.approval_id, st.status,
                   ar.expires_at, p.proposed_by, s.display_name, p.proposed_at, p.executed_at,
                   c.id
            FROM remedy_proposals p
            JOIN staff_users s ON s.id = p.proposed_by
            LEFT JOIN approval_requests ar ON ar.id = p.approval_id
            LEFT JOIN approval_request_states st ON st.approval_request_id = p.approval_id
            LEFT JOIN remedy_credits c ON c.remedy_proposal_id = p.id
            WHERE p.store_id = %s AND p.incident_id = %s
            ORDER BY p.proposed_at, p.id
            LIMIT %s
            """,
            (store_id, incident_id, REMEDY_READ_LIMIT + 1),
        )
        rows = cursor.fetchall()
        return IncidentRemedyProposals(
            incident_id=incident_id,
            order_id=None if incident[0] is None else _uuid(incident[0]),
            proposals=tuple(_proposal(row, now) for row in rows[:REMEDY_READ_LIMIT]),
            truncated=len(rows) > REMEDY_READ_LIMIT,
        )


def _proposal(row: tuple[Any, ...], now: datetime) -> IncidentRemedyProposal:
    approval_status = None if row[9] is None else str(row[9])
    expires_at = row[10]
    return IncidentRemedyProposal(
        proposal_id=_uuid(row[0]),
        kind=str(row[1]),
        status=str(row[2]),
        amount_vnd=None if row[3] is None else int(row[3]),
        ceiling_vnd=None if row[4] is None else int(row[4]),
        order_line_id=None if row[5] is None else str(row[5]),
        attested_late_by_minutes=None if row[6] is None else int(row[6]),
        window_closes_at=row[7],
        approval_id=None if row[8] is None else _uuid(row[8]),
        approval_status=approval_status,
        approval_expires_at=expires_at,
        approval_lapsed=(
            None
            if approval_status is None or expires_at is None
            else approval_status == "REQUESTED" and expires_at <= now
        ),
        proposed_by=_uuid(row[11]),
        proposed_by_name=str(row[12]),
        proposed_at=row[13],
        executed_at=row[14],
        credit_id=None if row[15] is None else _uuid(row[15]),
    )


def _require_remedy_reader(principal: StaffPrincipal) -> None:
    """The counter roles that may propose a remedy and spend a credit, with MFA -- no wider.

    `StoreAccessError`, not a remedy error, so the application's single handler answers a wrong role
    with the same opaque 403 as a store the caller is not in.
    """

    if not principal.roles & REMEDY_ROLES or not principal.mfa_verified:
        raise StoreAccessError("reading remedies requires an operations role with MFA")


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "CREDIT_REDEEMED",
    "CREDIT_UNUSED",
    "REMEDY_READ_LIMIT",
    "IncidentRemedyProposal",
    "IncidentRemedyProposals",
    "OrderRemedyCredit",
    "OrderRemedyCredits",
    "RemedyReadNotFoundError",
    "RemedyReadRepository",
]
