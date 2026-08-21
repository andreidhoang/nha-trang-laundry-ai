"""The internal owner-assistant's turn ledger and its governed reads.

ASSISTANT-001. The assistant answers questions about the business from data the server already
governs; it never calls a model and never computes money, policy or state. What it *said* is
evidence, so every turn lands in `assistant_turns` through the same atomic envelope as every other
material change: insert, domain event, audit event and outbox commit together or not at all.

Authorization lives here and not in the route, because a route is a place a check can be forgotten:

* **Membership.** Recording or reading a turn requires an explicit `staff_store_assignments` row,
  exactly as on the Shadow surfaces.
* **Visibility.** A staff member reads back only their own turns. `OWNER_ADMIN` additionally sees
  the store's turns, because the owner is accountable for what the assistant told their staff.

The reads the assistant's answers are built from (`today_status_counts`, `find_order_status`) are
COUNT/single-row projections over tables the rest of the system owns. They compute nothing the
owning aggregate does not already compute.

This table is staff-internal Tier-2-style memory: what the assistant was asked and what it
answered, redacted before persistence. It is deliberately separate from the future customer-facing
conversation-turn contract (`specs/CUSTOMER_MEMORY_SPEC_V1.md`, status
SPEC_DRAFT_AWAITING_OWNER_APPROVAL), whose `conversation_*` names are reserved for MEM-SPEC-001.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from nha_trang_laundry_db.identity import StaffPrincipal, StaffRole
from nha_trang_laundry_db.store_access import require_store_membership
from nha_trang_laundry_db.transactions import MaterialChange, OutboxEvent, commit_material_change

#: The business day an answer about "hôm nay" refers to, pinned by canonical-enums-v1.json.
BUSINESS_TIMEZONE = "Asia/Ho_Chi_Minh"

#: Hard ceiling on any turn history read, whatever the caller asked for.
LIST_LIMIT_MAX = 100


class AssistantAuthorizationError(PermissionError):
    """Raised when a principal may not record or read assistant turns for a store."""


class AssistantStateError(ValueError):
    """Raised when an assistant turn command or a stored row is invalid."""


@dataclass(frozen=True, slots=True)
class AssistantLink:
    """A deep link into the staff console, attached to an answer."""

    label: str
    href: str


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    turn_id: UUID
    store_id: UUID
    staff_user_id: UUID
    question: str
    intent: str
    answer: str
    links: tuple[AssistantLink, ...]
    reason_codes: tuple[str, ...]
    correlation_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RecordTurnCommand:
    """One answered question. The answer arrives fully formed; nothing is computed here."""

    turn_id: UUID
    store_id: UUID
    principal: StaffPrincipal
    question: str
    intent: str
    answer: str
    links: tuple[AssistantLink, ...]
    reason_codes: tuple[str, ...]
    correlation_id: UUID
    idempotency_key: str
    created_at: datetime | None = None


class AssistantTurnRepository:
    """Every write is store-scoped, membership-checked and event/audit/outbox backed."""

    def record_turn(self, connection: Any, command: RecordTurnCommand) -> AssistantTurn:
        _validate_command(command)
        occurred_at = command.created_at or datetime.now(UTC)

        def mutation(cursor: Any) -> None:
            require_store_membership(
                cursor,
                staff_user_id=command.principal.staff_user_id,
                store_id=command.store_id,
                error=AssistantAuthorizationError,
            )
            cursor.execute(
                """
                INSERT INTO assistant_turns (
                    turn_id, store_id, staff_user_id, question, intent, answer, links,
                    reason_codes, correlation_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                """,
                (
                    command.turn_id,
                    command.store_id,
                    command.principal.staff_user_id,
                    command.question,
                    command.intent,
                    command.answer,
                    json.dumps([_link_mapping(link) for link in command.links]),
                    json.dumps(list(command.reason_codes)),
                    str(command.correlation_id),
                    occurred_at,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="ASSISTANT_TURN",
                aggregate_id=command.turn_id,
                aggregate_version=1,
                event_type="ASSISTANT_TURN_RECORDED",
                event_payload={
                    "store_id": str(command.store_id),
                    "intent": command.intent,
                    "reason_codes": list(command.reason_codes),
                },
                audit_action="ASSISTANT_TURN_RECORD",
                actor_type="STAFF",
                actor_id=command.principal.staff_user_id,
                correlation_id=command.correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "assistant.turn_recorded.v1",
                        {"turn_id": str(command.turn_id), "intent": command.intent},
                        # Caller-owned and stable: a retry of the same logical turn carries the
                        # same key, and the outbox worker can deduplicate on it.
                        command.idempotency_key,
                    ),
                ),
                occurred_at=occurred_at,
            ),
            mutation,
        )
        return AssistantTurn(
            turn_id=command.turn_id,
            store_id=command.store_id,
            staff_user_id=command.principal.staff_user_id,
            question=command.question,
            intent=command.intent,
            answer=command.answer,
            links=command.links,
            reason_codes=command.reason_codes,
            correlation_id=str(command.correlation_id),
            created_at=occurred_at,
        )

    @staticmethod
    def list_recent(
        connection: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        limit: int = 50,
        before: UUID | None = None,
    ) -> tuple[AssistantTurn, ...]:
        """Newest first. An owner sees the store's turns; anyone else sees only their own.

        `before` is the oldest turn the caller already holds, and the page returned is the one that
        continues after it. A turn id is the cursor rather than an encoded position because the
        caller already has one, it is opaque without being invented, and resolving it goes through
        the same visibility rule as everything else here — so an id naming a turn this caller may
        not see is refused rather than quietly restarting them at the newest page. A silent restart
        would look like the end of the history to a reader paging backwards.

        The anchor is read in a second statement rather than folded into a subselect, because that
        read is where the visibility check on the anchor happens and it is a security property, not
        a round trip worth saving. Both statements run on the same connection; the table is
        append-only (`assistant_turns_append_only`), so no row the anchor depends on can change
        between them.
        """
        if not 1 <= limit <= LIST_LIMIT_MAX:
            raise ValueError(f"assistant history limit must be between 1 and {LIST_LIMIT_MAX}")
        with connection.cursor() as cursor:
            require_store_membership(
                cursor,
                staff_user_id=principal.staff_user_id,
                store_id=store_id,
                error=AssistantAuthorizationError,
            )
            owner = StaffRole.OWNER_ADMIN in principal.roles
            anchor: tuple[Any, Any] | None = None
            if before is not None:
                cursor.execute(
                    """
                    SELECT created_at, turn_id
                    FROM assistant_turns
                    WHERE store_id = %s AND turn_id = %s AND (%s OR staff_user_id = %s)
                    """,
                    (store_id, before, owner, principal.staff_user_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise AssistantAuthorizationError("assistant history cursor is not visible")
                anchor = (row[0], row[1])
            # The sort is `created_at DESC, turn_id` ASC, so the row after `(c, t)` is one with an
            # older `created_at`, or the same `created_at` and a larger `turn_id`. The directions
            # differ, which is why this is written out rather than as a row comparison: a tuple
            # compare would order the tie the wrong way and silently drop or repeat turns that
            # share a timestamp. `assistant_turns_store_idx` is `(store_id, created_at DESC,
            # turn_id)`, so this reads as a range on that index rather than a sort.
            cursor.execute(
                """
                SELECT turn_id, store_id, staff_user_id, question, intent, answer, links,
                       reason_codes, correlation_id, created_at
                FROM assistant_turns
                WHERE store_id = %s AND (%s OR staff_user_id = %s)
                  AND (
                    %s::timestamptz IS NULL
                    OR created_at < %s
                    OR (created_at = %s AND turn_id > %s)
                  )
                ORDER BY created_at DESC, turn_id
                LIMIT %s
                """,
                (
                    store_id,
                    owner,
                    principal.staff_user_id,
                    anchor[0] if anchor else None,
                    anchor[0] if anchor else None,
                    anchor[0] if anchor else None,
                    anchor[1] if anchor else None,
                    limit,
                ),
            )
            return tuple(_turn(row) for row in cursor.fetchall())

    @staticmethod
    def get_scoped(
        connection: Any,
        *,
        store_id: UUID,
        principal: StaffPrincipal,
        turn_id: UUID,
    ) -> AssistantTurn | None:
        """One turn under exactly the visibility rule of `list_recent`.

        An owner may read any turn in the store; anyone else may read only their own. A turn the
        caller may not see returns `None`, identical to a turn that does not exist — the two are
        deliberately indistinguishable so an id cannot be probed across staff accounts.
        """
        with connection.cursor() as cursor:
            require_store_membership(
                cursor,
                staff_user_id=principal.staff_user_id,
                store_id=store_id,
                error=AssistantAuthorizationError,
            )
            owner = StaffRole.OWNER_ADMIN in principal.roles
            cursor.execute(
                """
                SELECT turn_id, store_id, staff_user_id, question, intent, answer, links,
                       reason_codes, correlation_id, created_at
                FROM assistant_turns
                WHERE store_id = %s AND turn_id = %s AND (%s OR staff_user_id = %s)
                """,
                (store_id, turn_id, owner, principal.staff_user_id),
            )
            row = cursor.fetchone()
            return None if row is None else _turn(row)


def today_status_counts(
    cursor: Any,
    *,
    store_id: UUID,
    principal: StaffPrincipal,
) -> tuple[tuple[str, int], ...]:
    """Order counts for the store's current business day, grouped by commercial status.

    A COUNT over `orders`, nothing more. The day boundary is the declared business timezone,
    because "hôm nay" means the shop's day, not the server's.
    """
    _require_assistant_access(principal)
    require_store_membership(
        cursor,
        staff_user_id=principal.staff_user_id,
        store_id=store_id,
        error=AssistantAuthorizationError,
    )
    cursor.execute(
        """
        SELECT commercial_status, count(*)
        FROM orders
        WHERE store_id = %s
          AND (created_at AT TIME ZONE %s)::date = (now() AT TIME ZONE %s)::date
        GROUP BY commercial_status
        ORDER BY commercial_status
        """,
        (store_id, BUSINESS_TIMEZONE, BUSINESS_TIMEZONE),
    )
    return tuple((str(row[0]), int(str(row[1]))) for row in cursor.fetchall())


def find_order_status(
    cursor: Any,
    *,
    store_id: UUID,
    principal: StaffPrincipal,
    order_id: UUID,
) -> tuple[UUID, str] | None:
    """Resolve one order's commercial status inside one store, or None when it is not there.

    Orders carry no short public code — the only reference a person can quote is the identifier the
    console shows. A miss returns None rather than raising: "not found in this store" is a normal
    answer, and it is deliberately indistinguishable from "that store is not yours to see".
    """
    _require_assistant_access(principal)
    require_store_membership(
        cursor,
        staff_user_id=principal.staff_user_id,
        store_id=store_id,
        error=AssistantAuthorizationError,
    )
    cursor.execute(
        """
        SELECT id, commercial_status
        FROM orders
        WHERE id = %s AND store_id = %s
        """,
        (order_id, store_id),
    )
    row = cursor.fetchone()
    return None if row is None else (_uuid(row[0]), str(row[1]))


def _require_assistant_access(principal: StaffPrincipal) -> None:
    allowed = {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER, StaffRole.OPERATOR}
    if not principal.roles & allowed:
        raise AssistantAuthorizationError("assistant access is not authorized")


def _validate_command(command: RecordTurnCommand) -> None:
    _require_assistant_access(command.principal)
    question = command.question.strip()
    if not question or len(command.question) > 4000:
        raise AssistantStateError("assistant question must contain 1 to 4000 characters")
    if not command.intent or not command.intent.replace("_", "").isalnum():
        raise AssistantStateError("assistant intent is invalid")
    if not command.answer:
        raise AssistantStateError("assistant answer must not be empty")
    if not command.idempotency_key.strip():
        raise AssistantStateError("assistant turn idempotency key is required")


def _link_mapping(link: AssistantLink) -> dict[str, str]:
    return {"label": link.label, "href": link.href}


def _turn(row: tuple[object, ...]) -> AssistantTurn:
    created_at = row[9]
    if not isinstance(created_at, datetime) or created_at.tzinfo is None:
        raise AssistantStateError("stored assistant turn timestamp is invalid")
    links = row[6]
    reason_codes = row[7]
    if not isinstance(links, list) or not isinstance(reason_codes, list):
        raise AssistantStateError("stored assistant turn payload is invalid")
    return AssistantTurn(
        turn_id=_uuid(row[0]),
        store_id=_uuid(row[1]),
        staff_user_id=_uuid(row[2]),
        question=str(row[3]),
        intent=str(row[4]),
        answer=str(row[5]),
        links=tuple(
            AssistantLink(label=str(item["label"]), href=str(item["href"]))
            for item in links
            if isinstance(item, dict) and "label" in item and "href" in item
        ),
        reason_codes=tuple(str(code) for code in reason_codes),
        correlation_id=str(row[8]),
        created_at=created_at,
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


__all__ = [
    "BUSINESS_TIMEZONE",
    "LIST_LIMIT_MAX",
    "AssistantAuthorizationError",
    "AssistantLink",
    "AssistantStateError",
    "AssistantTurn",
    "AssistantTurnRepository",
    "RecordTurnCommand",
    "find_order_status",
    "today_status_counts",
]
