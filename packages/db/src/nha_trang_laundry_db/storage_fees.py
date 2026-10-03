"""The owner's storage policy, and the storage fee one order owes at one instant (`UNCLAIMED-001`).

`DEC-036`. The policy is a configuration version like the messaging and turnaround policies: an
immutable, hashed document in `configuration_versions`, published by
`scripts/publish_storage_policy.py` -- a script the owner runs, never a seed a process applies on
boot. Only an active `OWNER_ADMIN` may publish it, because it is money the shop charges its
customers. Until it is published no fee is charged and no disposal is offered; the waiting list and
the contact attempts work regardless. Publishing `withdrawn: true` returns a shop to that state;
fees already paid stay on the orders they were paid on (`order_storage_fees`).

This module is deliberately small and imports nothing from `orders.py`, so the order read, the
payment and the exact-total settlement can all ask it the same question without an import cycle:
*what storage fee does this order owe now?* The answer is always `unclaimed.order_storage_fee`'s.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid4

from nha_trang_laundry_domain.catalog import (
    CommercialOrderStatus,
    FulfillmentMode,
    ProductionStatus,
)
from nha_trang_laundry_domain.promise import SHOP_TIMEZONE_NAME
from nha_trang_laundry_domain.unclaimed import (
    STORAGE_POLICY_CONFIG_TYPE,
    OrderStorageFee,
    StorageClock,
    StorageHold,
    StoragePolicy,
    StoragePolicyError,
    WaitingClock,
    order_storage_fee,
    parse_storage_policy,
    storage_clock,
    validate_storage_document,
    waiting_clock,
)

from nha_trang_laundry_db.configurations import (
    ConfigurationDraft,
    ConfigurationRepository,
    JsonObject,
    snapshot_hash,
)
from nha_trang_laundry_db.identity import StaffRole

#: The refusal a fee- or disposal-dependent request gets while no storage policy is in force.
STORAGE_POLICY_UNPUBLISHED: Final = "STORAGE_POLICY_UNPUBLISHED"


class StoragePolicyAuthorizationError(PermissionError):
    """Only an active owner may publish the fee the shop charges for laundry left waiting."""


@dataclass(frozen=True, slots=True)
class PublishedStoragePolicy:
    policy: StoragePolicy
    version_id: UUID
    version: int
    snapshot_hash: str


def publish_storage_policy(
    connection: Any, *, actor_id: UUID, payload: JsonObject
) -> tuple[str, bool]:
    """Publish one storage document; return its digest and whether this call created it.

    Idempotent on the version in force, as the messaging and turnaround policies are: the document
    already in force changes nothing; a different one (or an earlier one again) is a new version.
    """

    validate_storage_document(payload)
    digest = snapshot_hash(payload)
    repository = ConfigurationRepository({STORAGE_POLICY_CONFIG_TYPE: validate_storage_document})
    with connection.transaction(), connection.cursor() as cursor:
        _require_active_owner(cursor, actor_id)
        in_force = ConfigurationRepository.latest_published(cursor, STORAGE_POLICY_CONFIG_TYPE)
        if in_force is not None and in_force.snapshot_hash == digest:
            return digest, False
        cursor.execute(
            "SELECT coalesce(max(version), 0) FROM configuration_versions WHERE config_type = %s",
            (STORAGE_POLICY_CONFIG_TYPE,),
        )
        row = cursor.fetchone()
        next_version = int(row[0]) + 1 if row else 1
    config_id = repository.create_draft(
        connection,
        ConfigurationDraft(
            config_type=STORAGE_POLICY_CONFIG_TYPE,
            version=next_version,
            payload=payload,
            created_by=actor_id,
        ),
        correlation_id=uuid4(),
    )
    repository.publish(
        connection,
        config_id=config_id,
        version=next_version,
        snapshot_hash_value=digest,
        published_by=actor_id,
        correlation_id=uuid4(),
    )
    return digest, True


def published_from_document(
    *, version_id: object, version: object, digest: object, payload: object
) -> PublishedStoragePolicy | None:
    """The policy in force, from its stored row, or `None` -- which means no fee and no disposal.

    Re-hashed against the digest recorded at publication and re-parsed before use, as the other
    published policies are: a payload that no longer matches or no longer parses is not a published
    policy whatever the lifecycle column says. A withdrawal in force is `None`.
    """

    if not isinstance(payload, Mapping) or payload.get("withdrawn") is True:
        return None
    if not hmac.compare_digest(snapshot_hash(payload), str(digest)):
        return None
    try:
        policy = parse_storage_policy(payload)
    except StoragePolicyError:
        return None
    return PublishedStoragePolicy(
        policy=policy,
        version_id=version_id if isinstance(version_id, UUID) else UUID(str(version_id)),
        version=int(str(version)),
        snapshot_hash=str(digest),
    )


#: `DEC-047`: the order's holds of finished laundry since it was last ready, oldest first, as one
#: JSON array of `[held_at, resumed_at]` -- what `unclaimed.counted_days` counts the fee's days
#: from. Served by `order_storage_holds_order_idx`; `[]` for nearly every order.
STORAGE_HOLDS_SQL: Final = """
    (
        SELECT coalesce(
            jsonb_agg(jsonb_build_array(h.held_at, h.resumed_at) ORDER BY h.held_at), '[]'::jsonb
        )
        FROM order_storage_holds h
        WHERE h.order_id = o.id AND h.held_at >= o.production_ready_at
    )
"""

#: `DEC-050`: the instant an order's laundry would have been ready had the shop never held it --
#: the ready time moved on by every lifted hold since. The waiting reads break a tie of counted
#: days (`WAITING_DAYS_SQL`) with it, the longest wall-time wait first. Used only to order, never
#: to count.
WAITING_SINCE_SQL: Final = """
    (
        o.production_ready_at + coalesce(
            (
                SELECT sum(h.resumed_at - h.held_at)
                FROM order_storage_holds h
                WHERE h.order_id = o.id AND h.held_at >= o.production_ready_at
                  AND h.resumed_at IS NOT NULL
            ),
            interval '0'
        )
    )
"""


def _shop_day_sql(moment: str) -> str:
    """`unclaimed.shop_date` in SQL: the shop-local calendar day of a `timestamptz`."""

    return f"(({moment}) AT TIME ZONE '{SHOP_TIMEZONE_NAME}')::date"


#: The instant `WAITING_DAYS_SQL` counts to, bound once per statement as its first placeholder:
#: `... FROM orders o ... {WAITING_AS_OF_SQL} WHERE ...` with `as_of` first among the parameters.
WAITING_AS_OF_SQL: Final = "CROSS JOIN (SELECT %s::timestamptz AS at) AS waiting_as_of"

#: `DEC-050`'s day count in SQL, so a bounded waiting read picks its page by the very days the
#: counter reads (verification round 3, P2): a page chosen by wall time left off orders that had
#: waited more counted days than ones it showed, because a one-hour hold across the shop's
#: midnight takes a whole day out of the count. It is `unclaimed.waiting_clock(ready_at, as_of,
#: holds=...).days` for an order whose clock runs (every order `AWAITING_PICKUP_SQL` admits):
#: shop-local days from the ready day to `as_of`, less, for each hold lifted since the laundry was
#: ready and begun before `as_of`, the shop-local days from its day to the day it was lifted (or
#: `as_of`'s), never negative. NULL when no ready time is recorded (legacy, `0037`). Each read that
#: orders by it checks every row against `waiting_clock` and refuses to answer on a difference.
WAITING_DAYS_SQL: Final = f"""
    (
        CASE WHEN o.production_ready_at IS NULL THEN NULL ELSE greatest(
            0,
            greatest(
                0,
                {_shop_day_sql("waiting_as_of.at")} - {_shop_day_sql("o.production_ready_at")}
            ) - coalesce(
                (
                    SELECT sum(greatest(
                        0,
                        {_shop_day_sql("least(h.resumed_at, waiting_as_of.at)")}
                            - {_shop_day_sql("h.held_at")}
                    ))
                    FROM order_storage_holds h
                    WHERE h.order_id = o.id AND h.held_at >= o.production_ready_at
                      AND h.resumed_at IS NOT NULL AND h.held_at < waiting_as_of.at
                ),
                0
            )
        ) END
    )
"""

#: The waiting reads' one order: the most counted days first (an unknown wait before all, as it
#: may be the longest), then the longest wall-time wait, then the order id.
WAITING_ORDER_SQL: Final = (
    f"{WAITING_DAYS_SQL} DESC NULLS FIRST, {WAITING_SINCE_SQL} ASC NULLS FIRST, o.id"
)


class WaitingCountDisagrees(RuntimeError):
    """`WAITING_DAYS_SQL` and `unclaimed.waiting_clock` counted one order differently: a waiting
    read would page by one count and print the other, so it answers nothing instead."""


def require_same_days(order_id: object, sql_days: object, clock: WaitingClock | None) -> None:
    """Refuse a waiting row whose SQL day count is not the clock's (`WaitingCountDisagrees`)."""

    counted = None if clock is None else clock.days
    if (None if sql_days is None else int(str(sql_days))) != counted:
        raise WaitingCountDisagrees(
            f"order {order_id}: the page counted {sql_days} days, the clock {counted}"
        )


#: The latest published storage policy as one JSON value, for reads that want it beside each row.
#: Uncorrelated, so PostgreSQL evaluates it once per statement (an InitPlan), not once per order.
_POLICY_DOCUMENT_SQL: Final = f"""
    (
        SELECT jsonb_build_object(
            'id', c.id, 'version', c.version, 'hash', c.snapshot_hash, 'payload', c.payload
        )
        FROM configuration_versions c
        WHERE c.config_type = '{STORAGE_POLICY_CONFIG_TYPE}' AND c.lifecycle = 'PUBLISHED'
        ORDER BY c.version DESC
        LIMIT 1
    )
"""

#: `UNCLAIMED-001`'s three columns on the order read, appended after the payment columns: the fee a
#: settling payment fixed (null when none), whether the fee was waived, and the policy in force --
#: then `DEC-047`'s holds of finished laundry (`STORAGE_HOLDS_SQL`).
STORAGE_VIEW_COLUMNS: Final = f"""
    , (SELECT f.amount_vnd FROM order_storage_fees f WHERE f.order_id = o.id)
        AS storage_fee_fixed_vnd
    , EXISTS (SELECT 1 FROM storage_fee_waivers w WHERE w.order_id = o.id) AS storage_fee_waived
    , {_POLICY_DOCUMENT_SQL} AS storage_policy
    , {STORAGE_HOLDS_SQL} AS storage_holds
"""


def policy_from_column(value: object) -> PublishedStoragePolicy | None:
    """`STORAGE_VIEW_COLUMNS`' `storage_policy` value as the published policy, or `None`."""

    if not isinstance(value, Mapping):
        return None
    return published_from_document(
        version_id=value.get("id"),
        version=value.get("version"),
        digest=value.get("hash"),
        payload=value.get("payload"),
    )


def read_published_storage_policy(cursor: Any) -> PublishedStoragePolicy | None:
    """The policy in force, or `None` -- which means no fee is charged and no disposal offered."""

    cursor.execute("SELECT " + _POLICY_DOCUMENT_SQL)
    row = cursor.fetchone()
    return None if row is None else policy_from_column(row[0])


def holds_from_column(value: object) -> tuple[StorageHold, ...]:
    """`STORAGE_HOLDS_SQL`'s JSON array as the domain's holds (`DEC-047`)."""

    if not isinstance(value, list):
        return ()
    holds: list[StorageHold] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2 or item[0] is None:
            raise ValueError("a stored storage hold is malformed")
        holds.append(
            StorageHold(
                held_at=datetime.fromisoformat(str(item[0])),
                resumed_at=None if item[1] is None else datetime.fromisoformat(str(item[1])),
            )
        )
    return tuple(holds)


@dataclass(frozen=True, slots=True)
class LockedStorageFee:
    """What one order owes for storage at one instant, and the policy it was computed under."""

    fee: OrderStorageFee
    published: PublishedStoragePolicy | None
    awaiting: bool
    ready_at: datetime | None
    #: `MONEY-LIFECYCLE-009`: the facts the fee was measured against, for the waiver's effect.
    quoted_total_vnd: int | None = None
    paid_vnd: int = 0
    waived: bool = False
    #: `DEC-050`: the order's holds of finished laundry since it was ready, and whether one is open
    #: on finished laundry the customer still has to collect (the storage clock is `PAUSED`) --
    #: what `unclaimed.waiting_clock` counts the days waiting, disposal and reminders from.
    holds: tuple[StorageHold, ...] = ()
    paused: bool = False

    def waiting(self, as_of: datetime) -> WaitingClock | None:
        """`DEC-050`'s clock for this order at `as_of`; `None` when no ready time is recorded."""

        if self.ready_at is None:
            return None
        return waiting_clock(self.ready_at, as_of, holds=self.holds, paused=self.paused)


def storage_fee_for_order(cursor: Any, *, order_id: UUID, moment: datetime) -> LockedStorageFee:
    """The storage fee the order owes at `moment`, read from its stored facts.

    Called by the payment and the exact-total settlement while they hold the order's row lock, so
    the fee they measure the money against cannot move under them.
    """

    cursor.execute(
        """
        SELECT o.commercial_status, o.production_status, o.fulfillment_mode,
               o.self_collection_recorded, o.production_ready_at,
               CASE WHEN r.display_total_min_vnd = r.display_total_max_vnd
                    THEN r.display_total_min_vnd END,
               EXISTS (SELECT 1 FROM order_settlements s WHERE s.order_id = o.id),
               (SELECT f.amount_vnd FROM order_storage_fees f WHERE f.order_id = o.id),
               EXISTS (SELECT 1 FROM storage_fee_waivers w WHERE w.order_id = o.id),
        """
        + _POLICY_DOCUMENT_SQL
        + """,
               (SELECT coalesce(sum(p.amount_vnd), 0) FROM order_payments p
                 WHERE p.order_id = o.id),
               o.production_resume_status,
        """
        + STORAGE_HOLDS_SQL
        + """
        FROM orders o
        JOIN quote_revisions r
          ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
        WHERE o.id = %s
        """,
        (order_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise LookupError("order is missing")
    published = policy_from_column(row[9])
    clock = storage_clock(
        commercial=CommercialOrderStatus(str(row[0])),
        production=ProductionStatus(str(row[1])),
        resume_to=None if row[11] is None else ProductionStatus(str(row[11])),
        fulfillment_mode=FulfillmentMode(str(row[2])),
        self_collection_recorded=bool(row[3]),
    )
    awaiting = clock is StorageClock.RUNNING
    ready_at = row[4] if isinstance(row[4], datetime) else None
    quoted = None if row[5] is None else int(str(row[5]))
    paid = int(str(row[10]))
    holds = holds_from_column(row[12])
    fee = order_storage_fee(
        None if published is None else published.policy,
        clock=clock,
        ready_at=ready_at,
        as_of=moment,
        quoted_total_vnd=quoted,
        waived=bool(row[8]),
        settled=bool(row[6]),
        fixed_vnd=None if row[7] is None else int(str(row[7])),
        paid_vnd=paid,
        holds=holds,
    )
    return LockedStorageFee(
        fee=fee,
        published=published,
        awaiting=awaiting,
        ready_at=ready_at,
        quoted_total_vnd=quoted,
        paid_vnd=paid,
        waived=bool(row[8]),
        holds=holds,
        paused=clock is StorageClock.PAUSED,
    )


def insert_fixed_storage_fee(
    cursor: Any,
    *,
    order_id: UUID,
    store_id: UUID,
    settlement_id: UUID,
    storage: LockedStorageFee,
    amount_vnd: int,
    fixed_by_staff_id: UUID,
    fixed_at: datetime,
) -> str:
    """Fix the order's storage fee beside the settlement that pays it; return the basis (`0066`).

    `ACCRUED` when the amount is the fee the published policy computed now -- the trace (days,
    chargeable days, policy version) is recorded as `0060` always has. `ALREADY_PAID` when it is the
    part of the fee the ledger had already covered before the fee fell (`MONEY-LIFECYCLE-009`):
    there is no accrual behind that figure, so no trace is written for it.
    """

    trace = storage.fee.fee
    accrued = (
        trace is not None
        and storage.published is not None
        and trace.chargeable_days > 0
        and trace.amount_vnd == amount_vnd
    )
    basis = "ACCRUED" if accrued else "ALREADY_PAID"
    cursor.execute(
        """
        INSERT INTO order_storage_fees (
            id, order_id, store_id, settlement_id, amount_vnd, days_waiting,
            chargeable_days, policy_version_id, fixed_by_staff_id, fixed_at, basis
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            uuid4(),
            order_id,
            store_id,
            settlement_id,
            amount_vnd,
            trace.days_waiting if accrued and trace is not None else None,
            trace.chargeable_days if accrued and trace is not None else None,
            storage.published.version_id if accrued and storage.published is not None else None,
            fixed_by_staff_id,
            fixed_at,
            basis,
        ),
    )
    return basis


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
        raise StoragePolicyAuthorizationError(
            "only an active OWNER_ADMIN may publish the storage policy"
        )


__all__ = [
    "STORAGE_HOLDS_SQL",
    "STORAGE_POLICY_UNPUBLISHED",
    "STORAGE_VIEW_COLUMNS",
    "WAITING_AS_OF_SQL",
    "WAITING_DAYS_SQL",
    "WAITING_ORDER_SQL",
    "WAITING_SINCE_SQL",
    "LockedStorageFee",
    "PublishedStoragePolicy",
    "StoragePolicyAuthorizationError",
    "WaitingCountDisagrees",
    "holds_from_column",
    "insert_fixed_storage_fee",
    "policy_from_column",
    "publish_storage_policy",
    "published_from_document",
    "read_published_storage_policy",
    "require_same_days",
    "storage_fee_for_order",
]
