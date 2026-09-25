"""The shop's customer records: create, search, read, correct, link, erase, and the retention job.

`CUSTOMER-001`, `DEC-034`. Every rule is the domain's (`nha_trang_laundry_domain.customers`); this
module holds the rows. What it guarantees, and where:

* **Fail closed on the owner's notice.** `create` refuses `PRIVACY_NOTICE_UNPUBLISHED` inside the
  write's own transaction while no notice is published, and records which notice version the staff
  member read from when one is.
* **No phone value leaves the row.** The number is sealed (`personal_data.seal_phone`), digested
  and cut to its last four digits before anything is written. Every domain event, audit row and
  outbox row this module writes carries the customer id and the *names* of the fields that changed
  -- never a value. Names, addresses and notes are not written to those ledgers either: they are
  personal data an erasure must be able to remove, and the ledgers are append-only.
* **Store-scoped, fail-closed authorisation.** Role and MFA first, then membership of the named
  store, then every row selected with that store in its predicate: another store's customer is
  indistinguishable from one that does not exist.
* **Masking by role** (`DEC-034`): `OPERATOR`, `OPS_APPROVER` and `OWNER_ADMIN` -- the roles that
  call customers -- read the number and the address; an `AUDITOR` reads neither (last four only).
* **Erasure keeps orders.** Personal columns are nulled and the row stays, so `orders.customer_id`
  keeps its key; the migration's CHECK makes a half-erased row unstorable and its trigger makes an
  erased row final.

Nothing here reads a clock: the instant is always passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final
from uuid import UUID, uuid4

import psycopg
from nha_trang_laundry_domain.customers import (
    CUSTOMER_DECISION,
    SEARCH_LIMIT,
    CustomerKind,
    CustomerQuery,
    CustomerRefusal,
    CustomerRuleError,
    ErasureReason,
    LinkKind,
    MarketingChange,
    QueryMode,
    classify_query,
    clean_address,
    clean_display_name,
    clean_note,
    fold_text,
    marketing_change,
    national_from_e164,
    normalize_phone,
    retention_cutoff,
)

from .channel import ContactChannelBindingRepository
from .counter_tickets import CounterTicketRepository
from .identity import StaffPrincipal, StaffRole
from .orders import OPEN_ORDERS_SQL_PREDICATE
from .personal_data import open_phone, phone_digest, seal_phone
from .privacy_notice import read_published_privacy_notice
from .remedy_reads import STORE_CREDIT_READ_ROLES, StoreRemedyCredit
from .store_access import StoreAccessError, require_store_membership
from .transactions import MaterialChange, OutboxEvent, commit_material_change

#: `DEC-034`: operations roles search and read; an auditor reads masked.
CUSTOMER_READ_ROLES: Final = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR, StaffRole.AUDITOR}
)
#: The roles that call customers, and so see the full number and the address.
PHONE_VISIBLE_ROLES: Final = frozenset(
    {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
)
CUSTOMER_WRITE_ROLES: Final = PHONE_VISIBLE_ROLES
#: `SHOP_OPERATIONS_SPEC_V1.md` §1: "owner or approver, MFA".
CUSTOMER_ERASE_ROLES: Final = frozenset({StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER})

OPEN_ORDERS_LIMIT: Final = 20
RECENT_ORDERS_LIMIT: Final = 10
CUSTOMER_CREDITS_LIMIT: Final = 20
LINKS_LIMIT: Final = 50
LIST_MAX_LIMIT: Final = 100
#: The retention job erases at most this many records per run, and says when it stopped early.
RETENTION_BATCH_MAX: Final = 500

#: The personal columns an erasure nulls, named in its event and audit rows (names, not values).
ERASED_FIELDS: Final = (
    "phone",
    "display_name",
    "delivery_address",
    "note",
)


class CustomerNotFoundError(LookupError):
    """No such customer in this store -- or another store's, which is the same answer."""


class CustomerStateError(ValueError):
    """The row moved since the caller read it (`STALE_VERSION: …`)."""


class CustomerPhoneExistsError(CustomerRuleError):
    """A record with this number already exists in the store; the refusal names it."""

    def __init__(self, customer_id: UUID) -> None:
        super().__init__(CustomerRefusal.CUSTOMER_PHONE_EXISTS)
        self.customer_id = customer_id


# --- read models ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CustomerProfile:
    customer_id: UUID
    store_id: UUID
    display_name: str | None
    #: The national form (`0905123456`) for a role that calls customers; `None` otherwise or erased.
    phone: str | None
    phone_last4: str | None
    phone_visible: bool
    kind: CustomerKind
    delivery_address: str | None
    note: str | None
    marketing_consent: bool
    marketing_consent_at: datetime | None
    marketing_withdrawn_at: datetime | None
    service_consent_at: datetime
    service_consent_notice_version: int
    last_activity_at: datetime
    created_at: datetime
    erased_at: datetime | None
    erasure_reason: str | None
    row_version: int


@dataclass(frozen=True, slots=True)
class CustomerSummary:
    customer_id: UUID
    display_name: str | None
    phone: str | None
    phone_last4: str | None
    phone_visible: bool
    kind: CustomerKind
    marketing_consent: bool
    last_activity_at: datetime
    open_order_count: int


@dataclass(frozen=True, slots=True)
class CustomerSearch:
    store_id: UUID
    mode: QueryMode
    limit: int
    truncated: bool
    customers: tuple[CustomerSummary, ...]


@dataclass(frozen=True, slots=True)
class CustomerOrder:
    order_id: UUID
    created_at: datetime
    ticket_number: int | None
    ticket_issued_on: date | None
    commercial: str
    intake: str
    production: str
    balance: str
    fulfillment_mode: str
    self_collection_recorded: bool
    required_delivery_legs_succeeded: bool
    payable_total_vnd: int | None


@dataclass(frozen=True, slots=True)
class CustomerLinkView:
    link_id: UUID
    link_kind: LinkKind
    ref_id: UUID
    linked_at: datetime
    ticket_number: int | None
    ticket_issued_on: date | None
    channels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CustomerDetail:
    profile: CustomerProfile
    open_orders: tuple[CustomerOrder, ...]
    open_orders_truncated: bool
    recent_orders: tuple[CustomerOrder, ...]
    #: Unspent remedy credits issued from this customer's orders (`CREDIT-PICK-001`'s read shape);
    #: empty for a role the credit read does not admit.
    credits: tuple[StoreRemedyCredit, ...]
    credits_truncated: bool
    links: tuple[CustomerLinkView, ...]


@dataclass(frozen=True, slots=True)
class CustomerChanges:
    """A correction. Only the names in `provided` are applied; a provided `None` clears a field."""

    provided: frozenset[str]
    phone: str | None = None
    display_name: str | None = None
    delivery_address: str | None = None
    note: str | None = None
    kind: CustomerKind | None = None
    marketing_consent: bool | None = None


@dataclass(frozen=True, slots=True)
class RetentionOutcome:
    as_of: datetime
    cutoff: datetime
    erased: tuple[UUID, ...]
    #: More records were due than one run erases; run again.
    more_due: bool


_EDITABLE: Final = frozenset(
    {"phone", "display_name", "delivery_address", "note", "kind", "marketing_consent"}
)

_PROFILE_COLUMNS: Final = """
    c.id, c.store_id, c.display_name, c.phone_ciphertext, c.phone_last4, c.kind,
    c.delivery_address, c.note, c.marketing_consent_at, c.marketing_withdrawn_at,
    c.service_consent_at, c.service_consent_notice_version, c.last_activity_at, c.created_at,
    c.erased_at, c.erasure_reason, c.row_version
"""

#: The customer's orders: taken for the record, or under a ticket or binding linked to it. Always
#: this store's, through both predicates.
_CUSTOMER_ORDERS: Final = """
    o.store_id = %(store)s AND (
        o.customer_id = %(customer)s
        OR o.bound_contact_id IN (
            SELECT l.ref_id FROM customer_links l
            WHERE l.customer_id = %(customer)s AND l.store_id = %(store)s
        )
    )
"""

_ORDER_COLUMNS: Final = """
    SELECT o.id, o.created_at, t.ticket_number, t.issued_on, o.commercial_status,
           o.intake_status, o.production_status, o.balance_status, o.fulfillment_mode,
           o.self_collection_recorded, o.required_delivery_legs_succeeded,
           CASE WHEN rev.display_total_min_vnd = rev.display_total_max_vnd
                THEN rev.display_total_min_vnd END AS payable_total_vnd
    FROM orders o
    JOIN quote_revisions rev
      ON rev.quote_id = o.current_quote_id AND rev.revision = o.current_quote_revision
    LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
"""

#: `CREDIT-PICK-001`'s statement (`remedy_reads.STORE_CREDITS_SQL`) narrowed to this customer's
#: orders. Kept beside it rather than folded into it, so the counter's store-wide credit read keeps
#: the plan its test pins.
CUSTOMER_CREDITS_SQL: Final = (
    """
    SELECT c.id, p.kind, c.amount_vnd, c.issued_at, c.issued_from_order_id,
           t.ticket_number, t.issued_on, c.policy_version_id
    FROM remedy_credits c
    JOIN remedy_proposals p ON p.id = c.remedy_proposal_id
    JOIN orders o ON o.id = c.issued_from_order_id AND o.store_id = c.store_id
    LEFT JOIN counter_tickets t ON t.id = o.bound_contact_id AND t.store_id = o.store_id
    WHERE c.store_id = %(store)s AND c.redeemed_at IS NULL AND"""
    + _CUSTOMER_ORDERS
    + """
    ORDER BY c.issued_at DESC, c.id DESC
    LIMIT %(limit)s
"""
)

_OPEN_COUNT: Final = f"""
    (
        SELECT count(*) FROM orders o
        WHERE o.{OPEN_ORDERS_SQL_PREDICATE} AND o.store_id = c.store_id AND (
            o.customer_id = c.id
            OR o.bound_contact_id IN (
                SELECT l.ref_id FROM customer_links l
                WHERE l.customer_id = c.id AND l.store_id = c.store_id
            )
        )
    )
"""


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class CustomerRepository:
    """The only path to `customers` and `customer_links`."""

    # --- authorisation -------------------------------------------------------------------------

    @staticmethod
    def _require(
        cursor: Any, principal: StaffPrincipal, store_id: UUID, roles: frozenset[StaffRole]
    ) -> None:
        if not principal.roles & roles or not principal.mfa_verified:
            raise StoreAccessError("this customer action is not authorized for the role")
        require_store_membership(
            cursor, staff_user_id=principal.staff_user_id, store_id=store_id, error=StoreAccessError
        )

    @staticmethod
    def authorize_read(cursor: Any, *, store_id: UUID, principal: StaffPrincipal) -> None:
        CustomerRepository._require(cursor, principal, store_id, CUSTOMER_READ_ROLES)

    @staticmethod
    def authorize_write(cursor: Any, *, store_id: UUID, principal: StaffPrincipal) -> None:
        CustomerRepository._require(cursor, principal, store_id, CUSTOMER_WRITE_ROLES)

    @staticmethod
    def authorize_erase(cursor: Any, *, store_id: UUID, principal: StaffPrincipal) -> None:
        CustomerRepository._require(cursor, principal, store_id, CUSTOMER_ERASE_ROLES)

    # --- create --------------------------------------------------------------------------------

    def create(
        self,
        connection: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        phone: str,
        display_name: str | None,
        delivery_address: str | None,
        note: str | None,
        kind: CustomerKind,
        service_consent: bool,
        marketing_consent: bool,
        at: datetime,
        correlation_id: UUID,
    ) -> UUID:
        """Record a customer, or refuse by name. The notice is checked first, inside the write."""

        if at.tzinfo is None:
            raise ValueError("customer creation time must be timezone-aware")
        customer_id = uuid4()

        def mutation(cursor: Any) -> None:
            self.authorize_write(cursor, store_id=store_id, principal=principal)
            published = read_published_privacy_notice(cursor)
            if published is None:
                raise CustomerRuleError(CustomerRefusal.PRIVACY_NOTICE_UNPUBLISHED)
            if service_consent is not True:
                raise CustomerRuleError(CustomerRefusal.SERVICE_CONSENT_REQUIRED)
            number = normalize_phone(phone)
            name = clean_display_name(display_name)
            digest = phone_digest(number.e164)
            existing = _customer_by_digest(cursor, store_id=store_id, digest=digest)
            if existing is not None:
                raise CustomerPhoneExistsError(existing)
            try:
                cursor.execute(
                    """
                    INSERT INTO customers (
                        id, store_id, phone_ciphertext, phone_digest, phone_last4, display_name,
                        name_folded, delivery_address, note, kind, service_consent_notice_id,
                        service_consent_notice_version, service_consent_by, service_consent_at,
                        marketing_consent_at, marketing_consent_by, last_activity_at, row_version,
                        created_by, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1,
                        %s, %s, %s
                    )
                    """,
                    (
                        customer_id,
                        store_id,
                        seal_phone(number.e164, customer_id=customer_id, store_id=store_id),
                        digest,
                        number.last4,
                        name,
                        None if name is None else fold_text(name),
                        clean_address(delivery_address),
                        clean_note(note),
                        CustomerKind(kind).value,
                        published.version_id,
                        published.version,
                        principal.staff_user_id,
                        at,
                        at if marketing_consent else None,
                        principal.staff_user_id if marketing_consent else None,
                        at,
                        principal.staff_user_id,
                        at,
                        at,
                    ),
                )
            except psycopg.errors.UniqueViolation as error:
                # Two counters recorded the same number in the same moment: the loser is told
                # which record won, exactly as the pre-check would have told it.
                raise CustomerRuleError(CustomerRefusal.CUSTOMER_PHONE_EXISTS) from error

        provided = sorted(
            field
            for field, value in (
                ("display_name", display_name),
                ("delivery_address", delivery_address),
                ("note", note),
            )
            if value is not None and value.strip()
        )
        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CUSTOMER",
                aggregate_id=customer_id,
                aggregate_version=1,
                event_type="CUSTOMER_RECORDED",
                # Names of what was recorded, never values. The notice version is the consent's
                # evidence: which text the customer was read.
                event_payload={
                    "store_id": str(store_id),
                    "kind": CustomerKind(kind).value,
                    "fields_recorded": ["phone", *provided],
                    "service_consent": True,
                    "marketing_consent": bool(marketing_consent),
                    "decision_ref": CUSTOMER_DECISION,
                },
                audit_action="CUSTOMER_CREATE",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "customer.recorded.v1",
                        {"customer_id": str(customer_id), "store_id": str(store_id)},
                        f"customer:{customer_id}:recorded",
                    ),
                ),
                occurred_at=at,
                audit_details={"store_id": str(store_id), "fields_recorded": ["phone", *provided]},
            ),
            mutation,
        )
        return customer_id

    # --- reads ---------------------------------------------------------------------------------

    def search(
        self,
        cursor: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        query: str,
        limit: int = SEARCH_LIMIT,
    ) -> CustomerSearch:
        """Full phone by digest, four digits by last four, anything else by folded name.

        Bounded, newest activity first, erased records never listed. A query that asks for no
        search (digits still being typed, one letter) answers an empty page with its mode, so the
        console can say why rather than show "không có khách".
        """

        self.authorize_read(cursor, store_id=store_id, principal=principal)
        if not 1 <= limit <= LIST_MAX_LIMIT:
            raise ValueError(f"customer list limit must be between 1 and {LIST_MAX_LIMIT}")
        classified: CustomerQuery = classify_query(query)
        if not classified.searches:
            return CustomerSearch(store_id, classified.mode, limit, False, ())
        clauses = ["c.store_id = %(store)s", "c.erased_at IS NULL"]
        parameters: dict[str, object] = {"store": store_id, "limit": limit + 1}
        if classified.mode is QueryMode.PHONE:
            clauses.append("c.phone_digest = %(digest)s")
            parameters["digest"] = phone_digest(classified.value)
        elif classified.mode is QueryMode.LAST4:
            clauses.append("c.phone_last4 = %(last4)s")
            parameters["last4"] = classified.value
        elif classified.mode is QueryMode.NAME:
            clauses.append("c.name_folded LIKE %(name)s ESCAPE '\\'")
            parameters["name"] = f"%{_escape_like(classified.value)}%"
        cursor.execute(
            f"""
            SELECT c.id, c.display_name, c.phone_ciphertext, c.phone_last4, c.kind,
                   c.marketing_consent_at, c.marketing_withdrawn_at, c.last_activity_at,
                   {_OPEN_COUNT} AS open_order_count
            FROM customers c
            WHERE {" AND ".join(clauses)}
            ORDER BY c.last_activity_at DESC, c.id DESC
            LIMIT %(limit)s
            """,
            parameters,
        )
        rows = cursor.fetchall()
        visible = bool(principal.roles & PHONE_VISIBLE_ROLES)
        return CustomerSearch(
            store_id=store_id,
            mode=classified.mode,
            limit=limit,
            truncated=len(rows) > limit,
            customers=tuple(
                CustomerSummary(
                    customer_id=_uuid(row[0]),
                    display_name=row[1],
                    phone=(
                        national_from_e164(
                            open_phone(row[2], customer_id=_uuid(row[0]), store_id=store_id)
                        )
                        if visible
                        else None
                    ),
                    phone_last4=row[3],
                    phone_visible=visible,
                    kind=CustomerKind(str(row[4])),
                    marketing_consent=row[5] is not None and row[6] is None,
                    last_activity_at=row[7],
                    open_order_count=int(row[8]),
                )
                for row in rows[:limit]
            ),
        )

    def profile(
        self, cursor: Any, *, store_id: UUID, customer_id: UUID, principal: StaffPrincipal
    ) -> CustomerProfile:
        self.authorize_read(cursor, store_id=store_id, principal=principal)
        return self._profile(
            cursor,
            store_id=store_id,
            customer_id=customer_id,
            visible=bool(principal.roles & PHONE_VISIBLE_ROLES),
        )

    @staticmethod
    def _profile(
        cursor: Any, *, store_id: UUID, customer_id: UUID, visible: bool, lock: bool = False
    ) -> CustomerProfile:
        cursor.execute(
            f"SELECT {_PROFILE_COLUMNS} FROM customers c WHERE c.id = %s AND c.store_id = %s"
            + (" FOR UPDATE" if lock else ""),
            (customer_id, store_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise CustomerNotFoundError("customer is not in this store")
        erased = row[14] is not None
        return CustomerProfile(
            customer_id=_uuid(row[0]),
            store_id=_uuid(row[1]),
            display_name=row[2],
            phone=(
                national_from_e164(open_phone(row[3], customer_id=customer_id, store_id=store_id))
                if visible and not erased
                else None
            ),
            phone_last4=row[4],
            phone_visible=visible and not erased,
            kind=CustomerKind(str(row[5])),
            delivery_address=row[6] if visible else None,
            note=row[7],
            marketing_consent=row[8] is not None and row[9] is None,
            marketing_consent_at=row[8],
            marketing_withdrawn_at=row[9],
            service_consent_at=row[10],
            service_consent_notice_version=int(row[11]),
            last_activity_at=row[12],
            created_at=row[13],
            erased_at=row[14],
            erasure_reason=None if row[15] is None else str(row[15]),
            row_version=int(row[16]),
        )

    def detail(
        self, cursor: Any, *, store_id: UUID, customer_id: UUID, principal: StaffPrincipal
    ) -> CustomerDetail:
        """The customer's page: profile, open orders, the last ten orders, credits, links."""

        profile = self.profile(
            cursor, store_id=store_id, customer_id=customer_id, principal=principal
        )
        parameters = {"store": store_id, "customer": customer_id}
        cursor.execute(
            _ORDER_COLUMNS
            + " WHERE "
            + _CUSTOMER_ORDERS
            + f" AND o.{OPEN_ORDERS_SQL_PREDICATE} ORDER BY o.created_at DESC, o.id DESC"
            + " LIMIT %(limit)s",
            {**parameters, "limit": OPEN_ORDERS_LIMIT + 1},
        )
        open_rows = cursor.fetchall()
        cursor.execute(
            _ORDER_COLUMNS
            + " WHERE "
            + _CUSTOMER_ORDERS
            + " ORDER BY o.created_at DESC, o.id DESC LIMIT %(limit)s",
            {**parameters, "limit": RECENT_ORDERS_LIMIT},
        )
        recent_rows = cursor.fetchall()
        credits: tuple[StoreRemedyCredit, ...] = ()
        credits_truncated = False
        if principal.roles & STORE_CREDIT_READ_ROLES:
            cursor.execute(
                CUSTOMER_CREDITS_SQL, {**parameters, "limit": CUSTOMER_CREDITS_LIMIT + 1}
            )
            credit_rows = cursor.fetchall()
            credits_truncated = len(credit_rows) > CUSTOMER_CREDITS_LIMIT
            credits = tuple(
                StoreRemedyCredit(
                    credit_id=_uuid(row[0]),
                    kind=str(row[1]),
                    amount_vnd=int(row[2]),
                    issued_at=row[3],
                    issued_from_order_id=_uuid(row[4]),
                    ticket_number=None if row[5] is None else int(row[5]),
                    ticket_issued_on=row[6],
                    policy_version_id=_uuid(row[7]),
                )
                for row in credit_rows[:CUSTOMER_CREDITS_LIMIT]
            )
        cursor.execute(
            """
            SELECT l.id, l.link_kind, l.ref_id, l.linked_at, t.ticket_number, t.issued_on,
                   (
                       SELECT array_agg(DISTINCT b.provider ORDER BY b.provider)
                       FROM contact_channel_bindings b WHERE b.contact_binding_id = l.ref_id
                   )
            FROM customer_links l
            LEFT JOIN counter_tickets t ON t.id = l.ref_id AND t.store_id = l.store_id
            WHERE l.customer_id = %(customer)s AND l.store_id = %(store)s
            ORDER BY l.linked_at, l.id
            LIMIT %(limit)s
            """,
            {**parameters, "limit": LINKS_LIMIT},
        )
        links = tuple(
            CustomerLinkView(
                link_id=_uuid(row[0]),
                link_kind=LinkKind(str(row[1])),
                ref_id=_uuid(row[2]),
                linked_at=row[3],
                ticket_number=None if row[4] is None else int(row[4]),
                ticket_issued_on=row[5],
                channels=tuple(str(item) for item in (row[6] or ())),
            )
            for row in cursor.fetchall()
        )
        return CustomerDetail(
            profile=profile,
            open_orders=tuple(_order(row) for row in open_rows[:OPEN_ORDERS_LIMIT]),
            open_orders_truncated=len(open_rows) > OPEN_ORDERS_LIMIT,
            recent_orders=tuple(_order(row) for row in recent_rows),
            credits=credits,
            credits_truncated=credits_truncated,
            links=links,
        )

    # --- correct -------------------------------------------------------------------------------

    def update(
        self,
        connection: Any,
        *,
        store_id: UUID,
        customer_id: UUID,
        principal: StaffPrincipal,
        expected_row_version: int,
        changes: CustomerChanges,
        at: datetime,
        correlation_id: UUID,
    ) -> int:
        """Apply a correction under `If-Match`. A marketing-consent change is a consent event."""

        if at.tzinfo is None:
            raise ValueError("customer correction time must be timezone-aware")
        if not changes.provided or not changes.provided <= _EDITABLE:
            raise CustomerRuleError(CustomerRefusal.NOTHING_TO_CHANGE)
        outcome: dict[str, Any] = {}

        def mutation(cursor: Any) -> None:
            self.authorize_write(cursor, store_id=store_id, principal=principal)
            current = self._profile(
                cursor, store_id=store_id, customer_id=customer_id, visible=True, lock=True
            )
            if current.erased_at is not None:
                raise CustomerRuleError(CustomerRefusal.CUSTOMER_ERASED)
            if current.row_version != expected_row_version:
                raise CustomerStateError("STALE_VERSION: customer changed since it was read")
            assignments: dict[str, object] = {}
            changed: list[str] = []
            if "phone" in changes.provided:
                number = normalize_phone(changes.phone or "")
                if number.national != current.phone:
                    digest = phone_digest(number.e164)
                    existing = _customer_by_digest(cursor, store_id=store_id, digest=digest)
                    if existing is not None and existing != customer_id:
                        raise CustomerPhoneExistsError(existing)
                    assignments["phone_ciphertext"] = seal_phone(
                        number.e164, customer_id=customer_id, store_id=store_id
                    )
                    assignments["phone_digest"] = digest
                    assignments["phone_last4"] = number.last4
                    changed.append("phone")
            if "display_name" in changes.provided:
                name = clean_display_name(changes.display_name)
                if name != current.display_name:
                    assignments["display_name"] = name
                    assignments["name_folded"] = None if name is None else fold_text(name)
                    changed.append("display_name")
            if "delivery_address" in changes.provided:
                address = clean_address(changes.delivery_address)
                if address != current.delivery_address:
                    assignments["delivery_address"] = address
                    changed.append("delivery_address")
            if "note" in changes.provided:
                note = clean_note(changes.note)
                if note != current.note:
                    assignments["note"] = note
                    changed.append("note")
            if (
                "kind" in changes.provided
                and changes.kind is not None
                and CustomerKind(changes.kind) is not current.kind
            ):
                assignments["kind"] = CustomerKind(changes.kind).value
                changed.append("kind")
            consent = (
                marketing_change(
                    active=current.marketing_consent, requested=changes.marketing_consent
                )
                if "marketing_consent" in changes.provided
                else None
            )
            if consent is MarketingChange.GIVEN:
                assignments["marketing_consent_at"] = at
                assignments["marketing_consent_by"] = principal.staff_user_id
                assignments["marketing_withdrawn_at"] = None
                changed.append("marketing_consent")
            elif consent is MarketingChange.WITHDRAWN:
                assignments["marketing_withdrawn_at"] = at
                changed.append("marketing_consent")
            if not changed:
                raise CustomerRuleError(CustomerRefusal.NOTHING_TO_CHANGE)
            columns = ", ".join(f"{name} = %({name})s" for name in assignments)
            cursor.execute(
                f"""
                UPDATE customers
                SET {columns}, row_version = row_version + 1, updated_at = %(at)s
                WHERE id = %(id)s AND store_id = %(store)s AND row_version = %(version)s
                RETURNING row_version
                """,
                {
                    **assignments,
                    "at": at,
                    "id": customer_id,
                    "store": store_id,
                    "version": expected_row_version,
                },
            )
            row = cursor.fetchone()
            if row is None:
                raise CustomerStateError("STALE_VERSION: customer changed during the correction")
            outcome["row_version"] = int(row[0])
            outcome["changed"] = sorted(changed)
            outcome["consent"] = None if consent is None else consent.value

        # The event type is decided by what changed, which is only known inside the transaction;
        # so the envelope is written by a second, inner material change that the mutation fills.
        self._commit_correction(
            connection,
            customer_id=customer_id,
            store_id=store_id,
            expected_row_version=expected_row_version,
            principal=principal,
            at=at,
            correlation_id=correlation_id,
            mutation=mutation,
            outcome=outcome,
        )
        return int(outcome["row_version"])

    @staticmethod
    def _commit_correction(
        connection: Any,
        *,
        customer_id: UUID,
        store_id: UUID,
        expected_row_version: int,
        principal: StaffPrincipal,
        at: datetime,
        correlation_id: UUID,
        mutation: Any,
        outcome: dict[str, Any],
    ) -> None:
        # One outer transaction around both halves: the mutation decides what changed (and so the
        # event type), then the ledger rows are written in a nested savepoint of the same
        # transaction. A failure in either rolls back both -- there is no committed correction
        # without its event, audit and outbox rows.
        with connection.transaction():
            with connection.cursor() as cursor:
                mutation(cursor)
            CustomerRepository._write_correction_ledger(
                connection,
                customer_id=customer_id,
                store_id=store_id,
                expected_row_version=expected_row_version,
                principal=principal,
                at=at,
                correlation_id=correlation_id,
                outcome=outcome,
            )

    @staticmethod
    def _write_correction_ledger(
        connection: Any,
        *,
        customer_id: UUID,
        store_id: UUID,
        expected_row_version: int,
        principal: StaffPrincipal,
        at: datetime,
        correlation_id: UUID,
        outcome: dict[str, Any],
    ) -> None:
        consent = outcome["consent"]
        event_type = "CUSTOMER_CONSENT_CHANGED" if consent else "CUSTOMER_DETAILS_CORRECTED"
        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CUSTOMER",
                aggregate_id=customer_id,
                aggregate_version=expected_row_version + 1,
                event_type=event_type,
                event_payload={
                    "store_id": str(store_id),
                    "changed_fields": outcome["changed"],
                    "marketing_consent": consent,
                },
                audit_action="CUSTOMER_UPDATE",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "customer.updated.v1",
                        {
                            "customer_id": str(customer_id),
                            "row_version": expected_row_version + 1,
                            "changed_fields": outcome["changed"],
                        },
                        f"customer:{customer_id}:v{expected_row_version + 1}",
                    ),
                ),
                occurred_at=at,
                audit_details={"store_id": str(store_id), "changed_fields": outcome["changed"]},
            ),
            lambda _cursor: None,
        )

    # --- erase ---------------------------------------------------------------------------------

    def erase(
        self,
        connection: Any,
        *,
        store_id: UUID,
        customer_id: UUID,
        principal: StaffPrincipal,
        expected_row_version: int,
        reason: ErasureReason,
        at: datetime,
        correlation_id: UUID,
    ) -> int:
        """A customer's request (or an approver acting on retention): null every personal field."""

        if at.tzinfo is None:
            raise ValueError("erasure time must be timezone-aware")

        def authorize(cursor: Any) -> None:
            self.authorize_erase(cursor, store_id=store_id, principal=principal)

        return _erase(
            connection,
            store_id=store_id,
            customer_id=customer_id,
            actor_id=principal.staff_user_id,
            expected_row_version=expected_row_version,
            reason=ErasureReason(reason),
            at=at,
            correlation_id=correlation_id,
            authorize=authorize,
            trigger="STAFF_REQUEST",
        )

    # --- link ----------------------------------------------------------------------------------

    def link(
        self,
        connection: Any,
        *,
        store_id: UUID,
        customer_id: UUID,
        principal: StaffPrincipal,
        link_kind: LinkKind,
        ref_id: UUID,
        at: datetime,
        correlation_id: UUID,
    ) -> UUID:
        """Attach a ticket or a channel binding this store has served. An explicit staff act."""

        if at.tzinfo is None:
            raise ValueError("link time must be timezone-aware")
        link_id = uuid4()
        kind = LinkKind(link_kind)

        def mutation(cursor: Any) -> None:
            self.authorize_write(cursor, store_id=store_id, principal=principal)
            current = self._profile(
                cursor, store_id=store_id, customer_id=customer_id, visible=False, lock=True
            )
            if current.erased_at is not None:
                raise CustomerRuleError(CustomerRefusal.CUSTOMER_ERASED)
            if kind is LinkKind.COUNTER_TICKET:
                known = CounterTicketRepository.ticket_exists(
                    cursor, ticket_id=ref_id, store_id=store_id
                )
            else:
                # A binding is nobody's store's to own; it is linkable here only when this store
                # has itself started an intake or an order for it (the `CONTACT-PICK-001` rule).
                known = ContactChannelBindingRepository.binding_exists(
                    cursor, contact_binding_id=ref_id
                ) and _store_served_binding(cursor, store_id=store_id, binding_id=ref_id)
            if not known:
                raise CustomerRuleError(CustomerRefusal.LINK_REFERENCE_UNKNOWN)
            cursor.execute(
                "SELECT 1 FROM customer_links WHERE link_kind = %s AND ref_id = %s",
                (kind.value, ref_id),
            )
            if cursor.fetchone() is not None:
                raise CustomerRuleError(CustomerRefusal.LINK_EXISTS)
            try:
                cursor.execute(
                    """
                    INSERT INTO customer_links (
                        id, customer_id, store_id, link_kind, ref_id, linked_by, linked_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        link_id,
                        customer_id,
                        store_id,
                        kind.value,
                        ref_id,
                        principal.staff_user_id,
                        at,
                    ),
                )
            except psycopg.errors.UniqueViolation as error:
                raise CustomerRuleError(CustomerRefusal.LINK_EXISTS) from error

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="CUSTOMER_LINK",
                aggregate_id=link_id,
                aggregate_version=1,
                event_type="CUSTOMER_LINKED",
                event_payload={
                    "customer_id": str(customer_id),
                    "store_id": str(store_id),
                    "link_kind": kind.value,
                    "ref_id": str(ref_id),
                },
                audit_action="CUSTOMER_LINK",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "customer.linked.v1",
                        {"customer_id": str(customer_id), "link_id": str(link_id)},
                        f"customer-link:{link_id}",
                    ),
                ),
                occurred_at=at,
            ),
            mutation,
        )
        return link_id

    # --- the intake side -----------------------------------------------------------------------

    @staticmethod
    def touch_for_intake(cursor: Any, *, store_id: UUID, customer_id: UUID, at: datetime) -> None:
        """An intake is opened for the customer: their activity moves; nothing else does.

        Called inside the intake's own material change, so it commits with it. An erased record,
        or another store's, is refused as not found -- an intake is never opened against one.
        """

        cursor.execute(
            """
            UPDATE customers SET last_activity_at = greatest(last_activity_at, %s)
            WHERE id = %s AND store_id = %s AND erased_at IS NULL
            RETURNING id
            """,
            (at, customer_id, store_id),
        )
        if cursor.fetchone() is None:
            raise CustomerNotFoundError("customer is not in this store")

    @staticmethod
    def exists_active(cursor: Any, *, store_id: UUID, customer_id: UUID) -> bool:
        cursor.execute(
            "SELECT 1 FROM customers WHERE id = %s AND store_id = %s AND erased_at IS NULL",
            (customer_id, store_id),
        )
        return cursor.fetchone() is not None


# --- retention ------------------------------------------------------------------------------------


#: A record is due when neither it, nor any order or intake taken for it or under a linked ticket
#: or binding, moved since the cutoff -- and it has no open order at all, whatever its age.
RETENTION_DUE_SQL: Final = f"""
    SELECT c.id, c.store_id, c.row_version
    FROM customers c
    WHERE c.erased_at IS NULL
      AND c.last_activity_at <= %(cutoff)s
      AND NOT EXISTS (
          SELECT 1 FROM orders o
          WHERE o.store_id = c.store_id
            AND (o.created_at > %(cutoff)s OR o.{OPEN_ORDERS_SQL_PREDICATE})
            AND (
                o.customer_id = c.id
                OR o.bound_contact_id IN (
                    SELECT l.ref_id FROM customer_links l
                    WHERE l.customer_id = c.id AND l.store_id = c.store_id
                )
            )
      )
      AND NOT EXISTS (
          SELECT 1 FROM order_requests r
          WHERE r.customer_id = c.id AND r.created_at > %(cutoff)s
      )
    ORDER BY c.last_activity_at, c.id
    LIMIT %(limit)s
"""


def run_customer_retention(
    connection: Any,
    *,
    actor_id: UUID,
    as_of: datetime,
    batch: int = RETENTION_BATCH_MAX,
    dry_run: bool = False,
) -> RetentionOutcome:
    """Erase every record with no order for 24 months (`DEC-034`), each in its own transaction.

    Run by the owner (`scripts/run_customer_retention.py --actor-id`), who must be an active
    `OWNER_ADMIN`; each erasure is audited with reason `RETENTION` and the owner as actor, exactly
    as a customer's own request is, and a record that became active between the selection and its
    erasure is skipped rather than erased.
    """

    if as_of.tzinfo is None:
        raise ValueError("retention runs at a timezone-aware instant")
    if not 1 <= batch <= RETENTION_BATCH_MAX:
        raise ValueError(f"retention batch must be between 1 and {RETENTION_BATCH_MAX}")
    cutoff = retention_cutoff(as_of)
    with connection.transaction(), connection.cursor() as cursor:
        _require_active_owner(cursor, actor_id)
        cursor.execute(RETENTION_DUE_SQL, {"cutoff": cutoff, "limit": batch + 1})
        due = [(_uuid(row[0]), _uuid(row[1]), int(row[2])) for row in cursor.fetchall()]
    erased: list[UUID] = []
    if not dry_run:
        for customer_id, store_id, row_version in due[:batch]:

            def still_due(cursor: Any, customer_id: UUID = customer_id) -> None:
                # Lock first, then decide: an intake opened for the customer from now on waits
                # for this transaction and then finds the record erased, rather than racing it.
                cursor.execute("SELECT 1 FROM customers WHERE id = %s FOR UPDATE", (customer_id,))
                cursor.execute(
                    RETENTION_DUE_SQL.replace(
                        "WHERE c.erased_at IS NULL", "WHERE c.erased_at IS NULL AND c.id = %(id)s"
                    ),
                    {"cutoff": cutoff, "limit": 1, "id": customer_id},
                )
                if cursor.fetchone() is None:
                    raise _NoLongerDue()

            try:
                _erase(
                    connection,
                    store_id=store_id,
                    customer_id=customer_id,
                    actor_id=actor_id,
                    expected_row_version=row_version,
                    reason=ErasureReason.RETENTION,
                    at=as_of,
                    correlation_id=uuid4(),
                    authorize=still_due,
                    trigger="RETENTION_JOB",
                )
            except (_NoLongerDue, CustomerStateError):
                continue
            erased.append(customer_id)
    else:
        erased = [item[0] for item in due[:batch]]
    return RetentionOutcome(
        as_of=as_of, cutoff=cutoff, erased=tuple(erased), more_due=len(due) > batch
    )


class _NoLongerDue(Exception):
    """A record touched between the selection and its erasure. Skipped, never erased."""


def _erase(
    connection: Any,
    *,
    store_id: UUID,
    customer_id: UUID,
    actor_id: UUID,
    expected_row_version: int,
    reason: ErasureReason,
    at: datetime,
    correlation_id: UUID,
    authorize: Any,
    trigger: str,
) -> int:
    def mutation(cursor: Any) -> None:
        authorize(cursor)
        current = CustomerRepository._profile(
            cursor, store_id=store_id, customer_id=customer_id, visible=False, lock=True
        )
        if current.erased_at is not None:
            raise CustomerRuleError(CustomerRefusal.CUSTOMER_ERASED)
        if current.row_version != expected_row_version:
            raise CustomerStateError("STALE_VERSION: customer changed since it was read")
        cursor.execute(
            """
            UPDATE customers
            SET phone_ciphertext = NULL, phone_digest = NULL, phone_last4 = NULL,
                display_name = NULL, name_folded = NULL, delivery_address = NULL, note = NULL,
                erased_at = %s, erased_by = %s, erasure_reason = %s,
                row_version = row_version + 1, updated_at = %s
            WHERE id = %s AND store_id = %s AND row_version = %s AND erased_at IS NULL
            RETURNING row_version
            """,
            (at, actor_id, reason.value, at, customer_id, store_id, expected_row_version),
        )
        if cursor.fetchone() is None:
            raise CustomerStateError("STALE_VERSION: customer changed during the erasure")

    commit_material_change(
        connection,
        MaterialChange(
            aggregate_type="CUSTOMER",
            aggregate_id=customer_id,
            aggregate_version=expected_row_version + 1,
            event_type="CUSTOMER_ERASED",
            event_payload={
                "store_id": str(store_id),
                "reason": reason.value,
                "erased_fields": list(ERASED_FIELDS),
                "orders_kept": True,
            },
            audit_action="CUSTOMER_ERASE",
            actor_type="STAFF",
            actor_id=actor_id,
            correlation_id=correlation_id,
            outbox_events=(
                OutboxEvent(
                    "customer.erased.v1",
                    {"customer_id": str(customer_id), "store_id": str(store_id)},
                    f"customer:{customer_id}:erased",
                ),
            ),
            occurred_at=at,
            audit_details={
                "store_id": str(store_id),
                "reason": reason.value,
                "trigger": trigger,
                "erased_fields": list(ERASED_FIELDS),
            },
        ),
        mutation,
    )
    return expected_row_version + 1


def _require_active_owner(cursor: Any, actor_id: UUID) -> None:
    cursor.execute(
        """
        SELECT 1
        FROM staff_users u
        JOIN staff_role_assignments r ON r.staff_user_id = u.id
        WHERE u.id = %s AND u.status = 'ACTIVE' AND r.role = %s AND r.revoked_at IS NULL
        """,
        (actor_id, StaffRole.OWNER_ADMIN.value),
    )
    if cursor.fetchone() is None:
        raise StoreAccessError("only an active OWNER_ADMIN may run customer retention")


def _customer_by_digest(cursor: Any, *, store_id: UUID, digest: str) -> UUID | None:
    cursor.execute(
        "SELECT id FROM customers WHERE store_id = %s AND phone_digest = %s",
        (store_id, digest),
    )
    row = cursor.fetchone()
    return None if row is None else _uuid(row[0])


def _store_served_binding(cursor: Any, *, store_id: UUID, binding_id: UUID) -> bool:
    cursor.execute(
        """
        SELECT 1 WHERE EXISTS (
            SELECT 1 FROM order_requests WHERE store_id = %(store)s AND contact_binding_id = %(b)s
        ) OR EXISTS (
            SELECT 1 FROM orders WHERE store_id = %(store)s AND bound_contact_id = %(b)s
        )
        """,
        {"store": store_id, "b": binding_id},
    )
    return cursor.fetchone() is not None


def _order(row: tuple[Any, ...]) -> CustomerOrder:
    return CustomerOrder(
        order_id=_uuid(row[0]),
        created_at=row[1],
        ticket_number=None if row[2] is None else int(row[2]),
        ticket_issued_on=row[3],
        commercial=str(row[4]),
        intake=str(row[5]),
        production=str(row[6]),
        balance=str(row[7]),
        fulfillment_mode=str(row[8]),
        self_collection_recorded=bool(row[9]),
        required_delivery_legs_succeeded=bool(row[10]),
        payable_total_vnd=None if row[11] is None else int(row[11]),
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "CUSTOMER_CREDITS_SQL",
    "CUSTOMER_ERASE_ROLES",
    "CUSTOMER_READ_ROLES",
    "CUSTOMER_WRITE_ROLES",
    "ERASED_FIELDS",
    "PHONE_VISIBLE_ROLES",
    "RETENTION_BATCH_MAX",
    "RETENTION_DUE_SQL",
    "CustomerChanges",
    "CustomerDetail",
    "CustomerLinkView",
    "CustomerNotFoundError",
    "CustomerOrder",
    "CustomerPhoneExistsError",
    "CustomerProfile",
    "CustomerRepository",
    "CustomerSearch",
    "CustomerStateError",
    "CustomerSummary",
    "RetentionOutcome",
    "run_customer_retention",
]
