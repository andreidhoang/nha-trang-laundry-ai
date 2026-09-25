"""The channel customers a store has served: bind a returning one with a tap, not a pasted code.

`CONTACT-PICK-001`. A customer who wrote to the shop through a channel is identified by a
server-owned contact binding (`contact_channel_bindings`, migration `0020`), and until this read the
counter could start an order for one only by typing that binding's UUID under "Nhập mã thủ công".
There is no name, phone number or address to search by, and there will not be one: `DEC-015`
declines the customer record, so this is **not** a search. It is the list of bindings that already
have an order or an order request *in this store*, newest activity first -- the people this shop has
already dealt with.

**Unknown means stop.** A binding is a channel identity, not a store's (`create_order_request`
checks it without a store predicate because "a channel binding is not a store's to own"). The only
server-side fact that ties one to a store is a row this store wrote for it: an `order_requests` row
or an `orders` row naming it. A binding with neither here -- say one only another store has served,
or one no order was ever started for -- is not listed, rather than guessed into this store's list.

What a row carries, and what it deliberately does not:

* the binding id (the value `POST …/order-requests` takes), the channel(s) it was recorded on, and
  when this store last started something for it;
* this store's most recent order for it: its id, its four stored statuses and its payable total --
  the same figure the order board shows, read from the same quote-revision columns; nothing summed;
* how many of this store's orders for it are still open, and the newest intake of this store for it
  that has not become an order yet, so the counter resumes that intake instead of opening another;
* **no** message text, **no** provider handle (`provider_user_ref`), **no** verification evidence,
  and no walk-in ticket: a counter ticket is not a channel binding, so walk-ins never appear here.

Nothing here reads a clock and nothing writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.orders import OPEN_ORDERS_SQL_PREDICATE
from nha_trang_laundry_db.store_access import StoreAccessError, require_store_membership

#: The roles that may start an order request for a channel customer (`require_operations_staff`),
#: and so the only ones this list is for. `AUDITOR` is not here: the list exists to bind a customer
#: to a new intake, which an auditor may not do, and a list of who wrote to the shop is not an
#: audit fact the auditor lacks -- every order it could lead to is on the order board.
RECENT_CONTACT_READ_ROLES: Final = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
)
RECENT_CONTACT_DEFAULT_LIMIT: Final = 20
RECENT_CONTACT_MAX_LIMIT: Final = 100


@dataclass(frozen=True, slots=True)
class RecentContactOrder:
    """This store's most recent order for the binding, as stored."""

    order_id: UUID
    created_at: datetime
    commercial: str
    intake: str
    production: str
    balance: str
    #: The bound revision's single total, or `None` when it presents none (as on `OrderView`).
    payable_total_vnd: int | None


@dataclass(frozen=True, slots=True)
class RecentContact:
    contact_binding_id: UUID
    #: `contact_channel_bindings.provider` values for this binding, sorted. Normally one.
    channels: tuple[str, ...]
    #: The newest `created_at` of this store's order requests and orders naming the binding.
    last_activity_at: datetime
    latest_order: RecentContactOrder | None
    open_order_count: int
    #: The newest intake of this store for the binding that is not cancelled and has not become an
    #: order: the counter resumes it rather than opening a second one for the same customer.
    waiting_order_request_id: UUID | None


@dataclass(frozen=True, slots=True)
class RecentContacts:
    store_id: UUID
    limit: int
    contacts: tuple[RecentContact, ...]
    truncated: bool


#: One statement. `activity` is every order request and order of the store, keyed by the customer
#: reference it names; only references that are channel bindings survive the `EXISTS`, so walk-in
#: tickets drop out there. Grouped to one row per binding, newest first, stopped at the limit; the
#: lateral reads then run once per listed binding, each on an index keyed by (store, binding):
#: `order_requests_contact_idx` and `orders_store_bound_contact_idx`.
_RECENT_CONTACTS_SQL: Final = f"""
    WITH activity AS (
        SELECT req.contact_binding_id AS binding, req.created_at
        FROM order_requests req
        WHERE req.store_id = %(store)s
        UNION ALL
        SELECT o.bound_contact_id, o.created_at
        FROM orders o
        WHERE o.store_id = %(store)s
    ), recent AS (
        SELECT a.binding, max(a.created_at) AS last_activity_at
        FROM activity a
        WHERE EXISTS (
            SELECT 1 FROM contact_channel_bindings b WHERE b.contact_binding_id = a.binding
        )
        GROUP BY a.binding
        ORDER BY max(a.created_at) DESC, a.binding DESC
        LIMIT %(limit)s
    )
    SELECT r.binding, r.last_activity_at,
           (
               SELECT array_agg(DISTINCT b.provider ORDER BY b.provider)
               FROM contact_channel_bindings b WHERE b.contact_binding_id = r.binding
           ) AS channels,
           latest.id, latest.created_at, latest.commercial_status, latest.intake_status,
           latest.production_status, latest.balance_status, latest.payable_total_vnd,
           (
               SELECT count(*) FROM orders o
               WHERE o.store_id = %(store)s AND o.bound_contact_id = r.binding
                 AND o.{OPEN_ORDERS_SQL_PREDICATE}
           ) AS open_order_count,
           (
               SELECT req.id FROM order_requests req
               WHERE req.store_id = %(store)s AND req.contact_binding_id = r.binding
                 AND req.status <> 'CANCELLED'
                 AND NOT EXISTS (
                     SELECT 1 FROM orders o
                     JOIN quotes q ON q.id = o.current_quote_id
                     WHERE q.bound_order_request_id = req.id AND o.store_id = req.store_id
                 )
               ORDER BY req.created_at DESC, req.id DESC
               LIMIT 1
           ) AS waiting_order_request_id
    FROM recent r
    LEFT JOIN LATERAL (
        SELECT o.id, o.created_at, o.commercial_status, o.intake_status, o.production_status,
               o.balance_status,
               CASE WHEN rev.display_total_min_vnd = rev.display_total_max_vnd
                    THEN rev.display_total_min_vnd END AS payable_total_vnd
        FROM orders o
        JOIN quote_revisions rev
          ON rev.quote_id = o.current_quote_id AND rev.revision = o.current_quote_revision
        WHERE o.store_id = %(store)s AND o.bound_contact_id = r.binding
        ORDER BY o.created_at DESC, o.id DESC
        LIMIT 1
    ) latest ON TRUE
    ORDER BY r.last_activity_at DESC, r.binding DESC
"""


class RecentContactRepository:
    """Store-scoped read over the channel bindings this store has served. Writes nothing."""

    @staticmethod
    def list_for_store(
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        limit: int = RECENT_CONTACT_DEFAULT_LIMIT,
    ) -> RecentContacts:
        """Role and MFA, then membership of the named store -- one `StoreAccessError` for all three.

        Every row is then selected with that store in its predicate: a binding served only by
        another store is indistinguishable from one never served at all.
        """

        if not principal.roles & RECENT_CONTACT_READ_ROLES or not principal.mfa_verified:
            raise StoreAccessError("reading recent contacts requires an operations role with MFA")
        if not 1 <= limit <= RECENT_CONTACT_MAX_LIMIT:
            raise ValueError(
                f"recent contact limit must be between 1 and {RECENT_CONTACT_MAX_LIMIT}"
            )
        require_store_membership(
            cursor,
            staff_user_id=principal.staff_user_id,
            store_id=store_id,
            error=StoreAccessError,
        )
        cursor.execute(_RECENT_CONTACTS_SQL, {"store": store_id, "limit": limit + 1})
        rows = cursor.fetchall()
        return RecentContacts(
            store_id=store_id,
            limit=limit,
            contacts=tuple(_contact(row) for row in rows[:limit]),
            truncated=len(rows) > limit,
        )


def _contact(row: tuple[Any, ...]) -> RecentContact:
    latest = (
        None
        if row[3] is None
        else RecentContactOrder(
            order_id=_uuid(row[3]),
            created_at=row[4],
            commercial=str(row[5]),
            intake=str(row[6]),
            production=str(row[7]),
            balance=str(row[8]),
            payable_total_vnd=None if row[9] is None else int(row[9]),
        )
    )
    return RecentContact(
        contact_binding_id=_uuid(row[0]),
        channels=tuple(str(item) for item in (row[2] or ())),
        last_activity_at=row[1],
        latest_order=latest,
        open_order_count=int(row[10]),
        waiting_order_request_id=None if row[11] is None else _uuid(row[11]),
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "RECENT_CONTACT_DEFAULT_LIMIT",
    "RECENT_CONTACT_MAX_LIMIT",
    "RECENT_CONTACT_READ_ROLES",
    "RecentContact",
    "RecentContactOrder",
    "RecentContactRepository",
    "RecentContacts",
]
