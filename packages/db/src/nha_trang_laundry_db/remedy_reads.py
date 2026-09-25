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

**The owner's read (`REMEDY-OWNER-DECIDE-001`).** Since `DEC-031` every loss claim, every
compensation on a refunded order and anything above the staff limit waits for the owner on an
`APPROVE_REMEDY` envelope, and the approvals card had nothing to show the owner, so none could be
decided from the console. `read_approval_binding` is that card's read. It is narrower than the two
above on purpose -- only the roles that may *decide* an `APPROVE_REMEDY` envelope may read it,
because it exists to put one decision in front of the person who makes it -- and it returns the
envelope binding exactly as `approvals.DECISION_TIME_RESOLVERS` resolves it, by calling that
resolver, so the digests the owner is shown and the digests the decision is checked against cannot
disagree.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal
from uuid import UUID

from nha_trang_laundry_domain.approvals import APPROVAL_POLICIES, APPROVAL_RESOURCE_TYPES
from nha_trang_laundry_domain.catalog import ApprovalAction

from nha_trang_laundry_db.approvals import DECISION_TIME_RESOLVERS
from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.remedies import (
    REMEDY_PROPOSAL_RECORDED,
    REMEDY_ROLES,
    RemedyStateError,
    _policy_by_version,
    _read_order_record,
    _service_names,
)
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership

#: `remedy_credits` has no expiry column and no state beyond "spent or not": a credit is unspent
#: until `redeemed_at` is written by the order that spends it, and nothing else moves it.
CREDIT_UNUSED: Final = "UNUSED"
CREDIT_REDEEMED: Final = "REDEEMED"

#: What the counter can do next with a recorded proposal, decided here from stored state so the
#: console renders it rather than working it out (`REMEDY-OWNER-DECIDE-001`).
#:
#: `EXECUTE` -- staff-authorised, or the owner approved it and the envelope has not run out: the
#: execute route may be called. The route still re-checks everything under its row lock (a refund
#: since, a ceiling lowered since), so this is "may be attempted", never "will be paid".
NEXT_STEP_EXECUTE: Final = "EXECUTE"
#: The owner has not decided yet and still can.
NEXT_STEP_AWAIT_OWNER: Final = "AWAIT_OWNER"
#: The envelope can never pay any more -- rejected, cancelled, or past its expiry whether or not it
#: was decided (`_require_remedy_approval` refuses an approved envelope past `expires_at` too). The
#: only way forward is a new proposal.
NEXT_STEP_PROPOSE_AGAIN: Final = "PROPOSE_AGAIN"
#: Executed, or a pre-`DEC-031` loss nothing may pay.
NEXT_STEP_NONE: Final = "NONE"
NextStep = Literal["EXECUTE", "AWAIT_OWNER", "PROPOSE_AGAIN", "NONE"]

#: The roles that may decide an `APPROVE_REMEDY` envelope, and so read what one binds.
#: `_authorize_decision` admits `OWNER_ADMIN` always and otherwise the policy's required role, which
#: for this action is `OWNER_ADMIN` too. Derived from the policy rather than spelled out, so a
#: change of who decides remedies moves who reads them in the same edit.
REMEDY_DECIDER_ROLES: Final = frozenset(
    {
        StaffRole.OWNER_ADMIN,
        StaffRole(APPROVAL_POLICIES[ApprovalAction.APPROVE_REMEDY].required_role.value),
    }
)

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
    #: One of the `NEXT_STEP_*` values above, at `now`.
    next_step: NextStep


@dataclass(frozen=True, slots=True)
class IncidentRemedyProposals:
    incident_id: UUID
    order_id: UUID | None
    proposals: tuple[IncidentRemedyProposal, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class RemedyApprovalBinding:
    """What the owner must see to decide one `APPROVE_REMEDY` envelope, and what it binds.

    Every figure is stored: the amount and ceiling on the immutable proposal row, the staff limit
    in the policy version the proposal was checked against, and the reasons the owner is needed in
    the `REMEDY_PROPOSAL_RECORDED` event the proposal was committed with -- the same list that is
    inside the hashed proposal document the envelope's `rendered_hash` names. Nothing is recomputed.
    """

    store_id: UUID
    proposal_id: UUID
    incident_id: UUID
    order_id: UUID
    #: The paper ticket the customer holds; `None` for an order bound to a channel instead.
    ticket_number: int | None
    ticket_issued_on: date | None
    kind: str
    status: str
    amount_vnd: int | None
    ceiling_vnd: int | None
    #: The staff limit of the policy version the proposal was checked against; `None` when that
    #: version can no longer be read and verified.
    staff_approval_ceiling_vnd: int | None
    #: `OwnerReason` values as recorded with the proposal; `None` when no recorded event carries
    #: them, which the console must say rather than guess.
    owner_reasons: tuple[str, ...] | None
    order_line_id: str | None
    service_code: str | None
    service_name: str | None
    #: `None` is a line-level claim (a bag by weight, or a proposal written before migration 0052).
    garment_index: int | None
    #: The complaint in the staff member's words; `None` once disposed of under
    #: `INCIDENT_EVIDENCE`. Untrusted text: the console prints it through text nodes only.
    incident_summary: str | None
    proposed_by: UUID
    proposed_by_name: str
    proposed_at: datetime
    executed_at: datetime | None
    credit_id: UUID | None
    approval_id: UUID
    approval_status: str
    approval_expires_at: datetime
    approval_lapsed: bool
    next_step: NextStep
    #: The binding as `DECISION_TIME_RESOLVERS["REMEDY_PROPOSAL"]` resolves it now -- the values the
    #: decision is checked against, not an echo of the envelope.
    resource_version: int
    snapshot_hash: str
    rendered_hash: str
    #: True when those three equal the ones the stored envelope binds. False means approving is
    #: refused (`ApprovalResourceChangedError`); the console withholds the content and offers only
    #: a refusal.
    envelope_matches: bool


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

    @staticmethod
    def read_approval_binding(
        cursor: Any,
        *,
        store_id: UUID,
        proposal_id: UUID,
        principal: StaffPrincipal,
        now: datetime,
    ) -> RemedyApprovalBinding:
        """One proposal's owner envelope, with everything the owner reads before deciding it.

        Role and MFA first, then membership of the named store, both one `StoreAccessError`. The
        proposal is then selected with that store in its predicate and only when it has an
        envelope, so another store's proposal, one that does not exist and one that never needed
        the owner are the same `RemedyReadNotFoundError`.
        """

        if not principal.roles & REMEDY_DECIDER_ROLES or not principal.mfa_verified:
            raise StoreAccessError("reading a remedy approval requires the deciding role with MFA")
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        cursor.execute(
            """
            SELECT p.id, p.incident_id, p.order_id, p.kind, p.status, p.amount_vnd,
                   p.ceiling_vnd, p.order_line_id, p.garment_index, p.policy_version_id,
                   p.proposed_by, s.display_name, p.proposed_at, p.executed_at, c.id,
                   p.approval_id, st.status, ar.expires_at, ar.store_id, ar.action,
                   ar.resource_type, ar.resource_id, ar.resource_version, ar.snapshot_hash,
                   ar.rendered_hash, t.ticket_number, t.issued_on, e.summary
            FROM remedy_proposals p
            JOIN staff_users s ON s.id = p.proposed_by
            JOIN approval_requests ar ON ar.id = p.approval_id
            JOIN approval_request_states st ON st.approval_request_id = p.approval_id
            JOIN orders o ON o.id = p.order_id
            LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
            LEFT JOIN customer_incident_evidence e ON e.incident_id = p.incident_id
            LEFT JOIN remedy_credits c ON c.remedy_proposal_id = p.id
            WHERE p.id = %s AND p.store_id = %s
            """,
            (proposal_id, store_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise RemedyReadNotFoundError("no owner remedy envelope for this proposal here")
        # An envelope that is not this action over this proposal in this store is not a binding
        # this read may present as one. Nothing in this repository writes such a row; if one
        # exists it is answered as missing rather than shown to an owner as decidable.
        resource_type = str(row[20])
        if (
            _uuid(row[18]) != store_id
            or str(row[19]) != ApprovalAction.APPROVE_REMEDY.value
            or resource_type != APPROVAL_RESOURCE_TYPES[ApprovalAction.APPROVE_REMEDY]
            or _uuid(row[21]) != proposal_id
        ):
            raise RemedyReadNotFoundError("no owner remedy envelope for this proposal here")

        current = DECISION_TIME_RESOLVERS[resource_type](cursor, proposal_id)
        if current is None or current.rendered_hash is None:
            raise RemedyReadNotFoundError("no owner remedy envelope for this proposal here")
        envelope_matches = (
            current.store_id == store_id
            and current.resource_version == int(row[22])
            and hmac.compare_digest(current.snapshot_hash, str(row[23]))
            and hmac.compare_digest(current.rendered_hash, str(row[24]))
        )

        order_id = _uuid(row[2])
        order_line_id = None if row[7] is None else str(row[7])
        service_code, service_name = _line_service(cursor, order_id, order_line_id)
        policy = _policy_by_version(cursor, _uuid(row[9]))
        status = str(row[4])
        approval_status = str(row[16])
        expires_at = row[17]
        return RemedyApprovalBinding(
            store_id=store_id,
            proposal_id=proposal_id,
            incident_id=_uuid(row[1]),
            order_id=order_id,
            ticket_number=None if row[25] is None else int(row[25]),
            ticket_issued_on=row[26],
            kind=str(row[3]),
            status=status,
            amount_vnd=None if row[5] is None else int(row[5]),
            ceiling_vnd=None if row[6] is None else int(row[6]),
            staff_approval_ceiling_vnd=(
                None if policy is None else int(policy.staff_approval_ceiling_vnd)
            ),
            owner_reasons=_recorded_owner_reasons(cursor, proposal_id),
            order_line_id=order_line_id,
            service_code=service_code,
            service_name=service_name,
            garment_index=None if row[8] is None else int(row[8]),
            incident_summary=None if row[27] is None else str(row[27]),
            proposed_by=_uuid(row[10]),
            proposed_by_name=str(row[11]),
            proposed_at=row[12],
            executed_at=row[13],
            credit_id=None if row[14] is None else _uuid(row[14]),
            approval_id=_uuid(row[15]),
            approval_status=approval_status,
            approval_expires_at=expires_at,
            approval_lapsed=approval_status == "REQUESTED" and expires_at <= now,
            next_step=_next_step(status, approval_status, expires_at, now),
            resource_version=current.resource_version,
            snapshot_hash=current.snapshot_hash,
            rendered_hash=current.rendered_hash,
            envelope_matches=envelope_matches,
        )


def _line_service(
    cursor: Any, order_id: UUID, order_line_id: str | None
) -> tuple[str | None, str | None]:
    """The service code and display name of the line a claim names, from the order's revision.

    Display only, through the same reads `RemedyProposalRepository.options` uses. A revision this
    system cannot parse gives no code rather than a guessed one.
    """

    if order_line_id is None:
        return None, None
    try:
        record = _read_order_record(cursor, order_id=order_id)
    except RemedyStateError:
        return None, None
    line = record.facts.lines.get(order_line_id)
    if line is None:
        return None, None
    return line.service_code, _service_names(cursor, record.pricebook).get(line.service_code)


def _recorded_owner_reasons(cursor: Any, proposal_id: UUID) -> tuple[str, ...] | None:
    """Why the proposal needs the owner, as the domain decided it when the proposal was recorded.

    Read from the `REMEDY_PROPOSAL_RECORDED` event committed atomically with the proposal row --
    append-only, and the same list the hashed proposal document carries. Not re-derived from the
    order's facts now: an order refunded after the proposal did not change why it went to the
    owner, and a staff limit read from today's policy is not the one it was checked against.
    """

    cursor.execute(
        """
        SELECT payload FROM domain_events
        WHERE aggregate_type = 'REMEDY_PROPOSAL' AND aggregate_id = %s AND event_type = %s
        ORDER BY aggregate_version
        LIMIT 1
        """,
        (proposal_id, REMEDY_PROPOSAL_RECORDED),
    )
    row = cursor.fetchone()
    if row is None or not isinstance(row[0], Mapping):
        return None
    reasons = row[0].get("owner_reasons")
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        return None
    return tuple(reasons)


def _next_step(
    status: str, approval_status: str | None, expires_at: Any, now: datetime
) -> NextStep:
    """The `NEXT_STEP_*` for one proposal at `now`. See those constants for what each means."""

    if status == "STAFF_AUTHORIZED":
        return NEXT_STEP_EXECUTE
    if status != "OWNER_APPROVAL_REQUIRED" or approval_status is None or expires_at is None:
        return NEXT_STEP_NONE
    if expires_at <= now:
        return NEXT_STEP_PROPOSE_AGAIN
    if approval_status == "APPROVED":
        return NEXT_STEP_EXECUTE
    if approval_status == "REQUESTED":
        return NEXT_STEP_AWAIT_OWNER
    return NEXT_STEP_PROPOSE_AGAIN


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
        next_step=_next_step(str(row[2]), approval_status, expires_at, now),
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
    "NEXT_STEP_AWAIT_OWNER",
    "NEXT_STEP_EXECUTE",
    "NEXT_STEP_NONE",
    "NEXT_STEP_PROPOSE_AGAIN",
    "REMEDY_DECIDER_ROLES",
    "REMEDY_READ_LIMIT",
    "IncidentRemedyProposal",
    "IncidentRemedyProposals",
    "OrderRemedyCredit",
    "OrderRemedyCredits",
    "RemedyApprovalBinding",
    "RemedyReadNotFoundError",
    "RemedyReadRepository",
]
