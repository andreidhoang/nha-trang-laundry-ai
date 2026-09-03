"""The shop itself, as a row.

Until `STORE-REGISTRY-001` there was no `stores` table. Fourteen tables carried a `store_id` and
none of them referenced anything, so the identifier that every authorization decision in this system
turns on was a value nobody had ever validated: `staff_store_assignments` granted membership of it,
`require_store_membership` checked it, `0034` bound approvals to it, and each of them compared one
unchecked value against another.

The table is deliberately small. A store here is an identity and a name, not a profile: opening
hours, addresses, capacity and pricing all live elsewhere or do not exist yet, and inventing columns
for them would be guessing at a business nobody has described.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from .transactions import MaterialChange, OutboxEvent, commit_material_change


class StoreStateError(ValueError):
    """A store cannot be created as asked."""


@dataclass(frozen=True)
class StoredStore:
    store_id: UUID
    name: str | None
    created: bool


class StoreRepository:
    """Create the store a deployment works in, attributed and audited like any other change."""

    @staticmethod
    def create(
        connection: Any,
        *,
        store_id: UUID,
        name: str,
        created_by: UUID | None,
        correlation_id: UUID,
        occurred_at: datetime | None = None,
    ) -> StoredStore:
        """Create one store, or return the existing one unchanged if it is already there.

        Idempotent by identity rather than by key, because this runs once per deployment from a
        runbook step and a second run should be a no-op rather than a conflict an operator has to
        interpret at the counter on the first morning.

        `created_by` may be `None` for the genesis store of a deployment, where the shop exists
        before anyone has been given a login for it. The audit record says `SYSTEM` in that case
        rather than naming somebody who did not act.
        """

        display_name = name.strip()
        if not 1 <= len(display_name) <= 200:
            raise StoreStateError("a store name is required")
        timestamp = occurred_at or datetime.now(UTC)

        # `connection.transaction()` and not a bare cursor: a plain SELECT opens an implicit
        # transaction that nothing here would close, and every later `commit_material_change` on
        # this connection would then nest inside it as a savepoint rather than committing. The
        # symptom is remote -- work that looks written is invisible to any other session, which
        # surfaced as two agent workers both failing to claim a run that had been enqueued.
        with connection.transaction(), connection.cursor() as cursor:
            cursor.execute("SELECT name FROM stores WHERE id = %s", (store_id,))
            existing = cursor.fetchone()
        if existing is not None:
            return StoredStore(store_id, existing[0], created=False)

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO stores (id, name, created_at, created_by_staff_id, row_version)
                VALUES (%s, %s, %s, %s, 1)
                ON CONFLICT (id) DO NOTHING
                """,
                (store_id, display_name, timestamp, created_by),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="STORE",
                aggregate_id=store_id,
                aggregate_version=1,
                event_type="STORE_CREATED",
                event_payload={"name": display_name},
                audit_action="STORE_CREATE",
                actor_type="STAFF" if created_by is not None else "SYSTEM",
                actor_id=created_by,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "store.created.v1",
                        {"store_id": str(store_id), "name": display_name},
                        f"store:{store_id}:created",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )
        return StoredStore(store_id, display_name, created=True)

    @staticmethod
    def exists(cursor: Any, store_id: UUID) -> bool:
        """Whether this identifier names a store, for callers that must refuse rather than raise."""

        cursor.execute("SELECT 1 FROM stores WHERE id = %s", (store_id,))
        return cursor.fetchone() is not None
