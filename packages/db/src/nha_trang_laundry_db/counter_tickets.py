"""Issue the number a walk-in customer is known by, and nothing else about them.

`DEC-013`, resolved 2026-08-26. The shop needed a way to take in a person who never messaged it,
and the cheapest answer was also the most private one: hand them a number. Nothing in this module
records a name, a phone number or an address, because the owner decided none may be stored.

The module exists at all because `orders.bound_contact_id` is required and its only other source is
a channel binding, which is created by an inbound message. A person standing at the counter has not
sent one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: Who may hand a customer a ticket. The same set that may take a payment or accept a quote: this is
#: counter work, and a rule that stopped the person at the counter doing it would be worked around.
TICKET_ROLES = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR})


class CounterTicketError(ValueError):
    """A ticket could not be issued."""


@dataclass(frozen=True)
class IssuedTicket:
    """What the counter hands over: an opaque reference and the number said out loud."""

    ticket_id: UUID
    ticket_number: int
    issued_on: date


class CounterTicketRepository:
    """Issue tickets. There is no read-by-person, because there is no person to read by."""

    @staticmethod
    def ticket_exists(cursor: Any, *, ticket_id: UUID, store_id: UUID) -> bool:
        """Whether this store itself issued this ticket.

        Scoped to the store, never global. A ticket is a customer reference, so answering across
        stores would let one counter name another counter's customer -- the cross-store shape
        `STORE-SCOPING-002` closed for writes, arriving through an identifier instead of a route.

        Existence is all this answers. A ticket grants nothing: it is a number on a slip of paper
        that says a stranger is standing at this counter today.
        """
        cursor.execute(
            """
            SELECT 1 FROM counter_tickets WHERE id = %s AND store_id = %s
            """,
            (ticket_id, store_id),
        )
        return cursor.fetchone() is not None

    def issue(
        self,
        connection: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        correlation_id: UUID,
        issued_at: datetime | None = None,
    ) -> IssuedTicket:
        """Hand out the next number for this store today.

        The number is computed and inserted in one statement, so two counters serving two customers
        at the same moment cannot both read the same maximum. If they collide anyway the UNIQUE
        rejects the second, which is the correct outcome -- two people told "số 7" is worse than one
        of them waiting a moment for "số 8".
        """

        if not principal.roles & TICKET_ROLES or not principal.mfa_verified:
            raise StoreAccessError("issuing a ticket requires an operations role with MFA")
        moment = issued_at or datetime.now(UTC)
        issued_on = moment.astimezone(UTC).date()
        ticket_id = uuid4()
        assigned: list[int] = []

        def mutation(cursor: Any) -> None:
            require_store_membership(
                cursor,
                staff_user_id=principal.staff_user_id,
                store_id=store_id,
                error=StoreAccessError,
            )
            # Serialise the allocation per store per day. `coalesce(max(...))+1` reads under READ
            # COMMITTED with no lock, so two counters serving two walk-ins in the same moment both
            # computed the same number and the loser's UNIQUE violation escaped as a bare psycopg
            # error -- HTTP 500 on the first action of a customer's visit. An advisory lock is the
            # right shape here rather than a sequence: numbering restarts each day per store
            # (`test_numbers_restart_each_day_and_never_repeat_within_one`), which a sequence
            # cannot express, and the lock is released when this transaction ends either way.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"counter-ticket:{store_id}:{issued_on.isoformat()}",),
            )
            cursor.execute(
                """
                INSERT INTO counter_tickets (
                    id, store_id, issued_on, ticket_number, issued_by, issued_at, correlation_id
                )
                SELECT %s, %s, %s, coalesce(max(ticket_number), 0) + 1, %s, %s, %s
                FROM counter_tickets
                WHERE store_id = %s AND issued_on = %s
                RETURNING ticket_number
                """,
                (
                    ticket_id,
                    store_id,
                    issued_on,
                    principal.staff_user_id,
                    moment,
                    correlation_id,
                    store_id,
                    issued_on,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise CounterTicketError("the counter could not issue a ticket")
            assigned.append(int(row[0]))

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="COUNTER_TICKET",
                aggregate_id=ticket_id,
                aggregate_version=1,
                event_type="COUNTER_TICKET_ISSUED",
                # Deliberately carries no customer facts, because none were collected.
                event_payload={"ticket_id": str(ticket_id), "issued_on": issued_on.isoformat()},
                audit_action="COUNTER_TICKET_ISSUE",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "counter.ticket_issued.v1",
                        {"ticket_id": str(ticket_id), "store_id": str(store_id)},
                        f"store:{store_id}:ticket:{ticket_id}",
                    ),
                ),
                occurred_at=moment,
            ),
            mutation,
        )
        return IssuedTicket(ticket_id=ticket_id, ticket_number=assigned[0], issued_on=issued_on)


__all__ = [
    "TICKET_ROLES",
    "CounterTicketError",
    "CounterTicketRepository",
    "IssuedTicket",
]
