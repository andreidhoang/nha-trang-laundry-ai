"""Retention control for the section 15 schedule: versioned, held, audited, and fail-closed.

`SHADOW-001` is where real customer names, phone numbers and delivery addresses first enter this
database, so the disposal rule has to exist before the data does. `DEC-008` is open, which is
exactly why this module ships with no class enabled: a class with no approved schedule refuses to
run rather than defaulting to delete or to keep-forever.

Two things this module deliberately does not do.

It does not implement a hidden soft-delete. Section 15 requires purge to be reversible only through
backup policy, so a class whose disposition is PURGE must really remove the data or refuse.

It does not purge an append-only ledger. Most tables holding customer data carry a
`reject_ledger_mutation` trigger, so the database itself refuses. That conflict between the ledger
design and the retention schedule is real and is recorded rather than worked around: a run against
such a class ends `REFUSED_UNSUPPORTED_STORE` and says so, instead of silently succeeding while
deleting nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from .identity import StaffPrincipal, StaffRole
from .transactions import MaterialChange, OutboxEvent, commit_material_change


class RetentionClass(StrEnum):
    RAW_WEBHOOK_PAYLOAD = "RAW_WEBHOOK_PAYLOAD"
    CONVERSATION_BODY = "CONVERSATION_BODY"
    AGENT_RUN_PAYLOAD = "AGENT_RUN_PAYLOAD"
    EXACT_DELIVERY_LOCATION = "EXACT_DELIVERY_LOCATION"
    CONSENT_EVIDENCE = "CONSENT_EVIDENCE"
    ORDER_FINANCIAL_RECORD = "ORDER_FINANCIAL_RECORD"
    INCIDENT_EVIDENCE = "INCIDENT_EVIDENCE"
    DEBUG_LOG = "DEBUG_LOG"
    SECURITY_AUDIT_EVENT = "SECURITY_AUDIT_EVENT"
    ASSISTANT_TRANSCRIPT = "ASSISTANT_TRANSCRIPT"


class RetentionDisposition(StrEnum):
    PURGE = "PURGE"
    REDACT = "REDACT"


class PurgeOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    REFUSED_NO_APPROVED_SCHEDULE = "REFUSED_NO_APPROVED_SCHEDULE"
    REFUSED_LEGAL_HOLD = "REFUSED_LEGAL_HOLD"
    REFUSED_UNSUPPORTED_STORE = "REFUSED_UNSUPPORTED_STORE"


#: Classes for which a purge target is actually implemented. It is empty, and that is the honest
#: state: no class can dispose of anything yet. A class reaching COMPLETED while deleting nothing
#: would be the silent no-op this module exists to avoid -- a control that looks satisfied.
SUPPORTED_PURGE_CLASSES: frozenset[RetentionClass] = frozenset()

#: Classes whose backing tables carry an append-only ledger trigger. The database will refuse a
#: purge against these, so the job refuses first and says which store blocked it. Resolving this
#: needs a schema decision, not a code change: see docs/PATH_TO_PRODUCTION_REVIEW.md.
LEDGER_BACKED_CLASSES = frozenset(
    {
        RetentionClass.RAW_WEBHOOK_PAYLOAD,
        RetentionClass.CONSENT_EVIDENCE,
        RetentionClass.AGENT_RUN_PAYLOAD,
        RetentionClass.SECURITY_AUDIT_EVENT,
        RetentionClass.INCIDENT_EVIDENCE,
        # `assistant_turns` carries `reject_ledger_mutation`, so the transcript gets the same
        # honest refusal as every other ledger: a published, enabled schedule runs, is refused by
        # the store, and the refusal is what the run record shows.
        RetentionClass.ASSISTANT_TRANSCRIPT,
    }
)


class RetentionAuthorizationError(PermissionError):
    """Raised when a principal may not publish a schedule or place a hold."""


class RetentionStateError(ValueError):
    """Raised when a retention action would be inconsistent with recorded state."""


@dataclass(frozen=True, slots=True)
class RetentionConfiguration:
    class_name: RetentionClass
    version: int
    enabled: bool
    disposition: RetentionDisposition
    retention_days: int | None
    decision_ref: str | None


@dataclass(frozen=True, slots=True)
class PurgeRun:
    run_id: UUID
    class_name: RetentionClass
    outcome: PurgeOutcome
    affected_row_count: int
    cutoff_at: datetime | None
    detail: str | None


class RetentionRepository:
    """Publish schedules, place holds, and run purges that always leave a record."""

    def publish_configuration(
        self,
        connection: Any,
        *,
        class_name: RetentionClass,
        disposition: RetentionDisposition,
        principal: StaffPrincipal,
        correlation_id: UUID,
        enabled: bool = False,
        retention_days: int | None = None,
        decision_ref: str | None = None,
        now: datetime | None = None,
    ) -> RetentionConfiguration:
        """Publish a new immutable version of one class's schedule. Owner-only and audited."""

        if StaffRole.OWNER_ADMIN not in principal.roles:
            raise RetentionAuthorizationError(
                "publishing a retention schedule requires OWNER_ADMIN"
            )
        if enabled and (retention_days is None or decision_ref is None):
            raise RetentionStateError(
                "an enabled retention class requires both a period and the "
                "decision that approved it"
            )
        timestamp = now or datetime.now(UTC)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT coalesce(max(version), 0) FROM retention_class_configurations "
                "WHERE class_name = %s",
                (class_name.value,),
            )
            row = cursor.fetchone()
        version = int(row[0]) + 1 if row is not None else 1

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO retention_class_configurations (
                    class_name, version, enabled, disposition, retention_days, decision_ref,
                    published_by_staff_id, published_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    class_name.value,
                    version,
                    enabled,
                    disposition.value,
                    retention_days,
                    decision_ref,
                    principal.staff_user_id,
                    timestamp,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="RETENTION_CLASS",
                aggregate_id=_class_uuid(class_name),
                aggregate_version=version,
                event_type="RETENTION_SCHEDULE_PUBLISHED",
                event_payload={
                    "class_name": class_name.value,
                    "version": version,
                    "enabled": enabled,
                    "disposition": disposition.value,
                    "retention_days": retention_days,
                    "decision_ref": decision_ref,
                },
                audit_action="RETENTION_SCHEDULE_PUBLISH",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "retention.schedule_published.v1",
                        {"class_name": class_name.value, "version": version, "enabled": enabled},
                        f"retention-schedule:{class_name.value}:{version}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )
        return RetentionConfiguration(
            class_name=class_name,
            version=version,
            enabled=enabled,
            disposition=disposition,
            retention_days=retention_days,
            decision_ref=decision_ref,
        )

    @staticmethod
    def active_configuration(
        connection: Any, *, class_name: RetentionClass
    ) -> RetentionConfiguration | None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT version, enabled, disposition, retention_days, decision_ref
                FROM retention_class_configurations
                WHERE class_name = %s
                ORDER BY version DESC
                LIMIT 1
                """,
                (class_name.value,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return RetentionConfiguration(
            class_name=class_name,
            version=int(row[0]),
            enabled=bool(row[1]),
            disposition=RetentionDisposition(str(row[2])),
            retention_days=int(row[3]) if row[3] is not None else None,
            decision_ref=str(row[4]) if row[4] is not None else None,
        )

    def place_legal_hold(
        self,
        connection: Any,
        *,
        class_name: RetentionClass,
        reason: str,
        principal: StaffPrincipal,
        correlation_id: UUID,
        subject_contact_binding_id: UUID | None = None,
        now: datetime | None = None,
    ) -> UUID:
        if not principal.roles & {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER}:
            raise RetentionAuthorizationError("placing a legal hold is not authorized")
        timestamp = now or datetime.now(UTC)
        hold_id = uuid4()

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO retention_legal_holds (
                    hold_id, class_name, subject_contact_binding_id, reason,
                    placed_by_staff_id, placed_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    hold_id,
                    class_name.value,
                    subject_contact_binding_id,
                    reason,
                    principal.staff_user_id,
                    timestamp,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="RETENTION_LEGAL_HOLD",
                aggregate_id=hold_id,
                aggregate_version=1,
                event_type="RETENTION_LEGAL_HOLD_PLACED",
                event_payload={"class_name": class_name.value},
                audit_action="RETENTION_LEGAL_HOLD_PLACE",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "retention.legal_hold_placed.v1",
                        {"hold_id": str(hold_id), "class_name": class_name.value},
                        f"retention-hold:{hold_id}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )
        return hold_id

    def release_legal_hold(
        self,
        connection: Any,
        *,
        hold_id: UUID,
        principal: StaffPrincipal,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> None:
        """Release one hold, attributed. A hold with no release path is a bug, not a safeguard."""

        if not principal.roles & {StaffRole.OWNER_ADMIN, StaffRole.OPS_APPROVER}:
            raise RetentionAuthorizationError("releasing a legal hold is not authorized")
        timestamp = now or datetime.now(UTC)

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                UPDATE retention_legal_holds
                SET released_by_staff_id = %s, released_at = %s
                WHERE hold_id = %s AND released_at IS NULL
                """,
                (principal.staff_user_id, timestamp, hold_id),
            )
            if cursor.rowcount != 1:
                raise RetentionStateError("no active legal hold with that identifier")

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="RETENTION_LEGAL_HOLD",
                aggregate_id=hold_id,
                aggregate_version=2,
                event_type="RETENTION_LEGAL_HOLD_RELEASED",
                event_payload={},
                audit_action="RETENTION_LEGAL_HOLD_RELEASE",
                actor_type="STAFF",
                actor_id=principal.staff_user_id,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "retention.legal_hold_released.v1",
                        {"hold_id": str(hold_id)},
                        f"retention-hold-release:{hold_id}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )

    @staticmethod
    def active_hold_ids(connection: Any, *, class_name: RetentionClass) -> tuple[UUID, ...]:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT hold_id FROM retention_legal_holds "
                "WHERE class_name = %s AND released_at IS NULL",
                (class_name.value,),
            )
            return tuple(
                value if isinstance(value := row[0], UUID) else UUID(str(row[0]))
                for row in cursor.fetchall()
            )

    @staticmethod
    def active_hold_exists(connection: Any, *, class_name: RetentionClass) -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM retention_legal_holds
                WHERE class_name = %s AND released_at IS NULL
                LIMIT 1
                """,
                (class_name.value,),
            )
            return cursor.fetchone() is not None

    def run_purge(
        self,
        connection: Any,
        *,
        class_name: RetentionClass,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> PurgeRun:
        """Execute at most one class. Always writes a run record, including when it refuses."""

        timestamp = now or datetime.now(UTC)
        configuration = self.active_configuration(connection, class_name=class_name)

        if configuration is None or configuration.retention_days is None:
            return self._record_run(
                connection,
                class_name=class_name,
                configuration=configuration,
                outcome=PurgeOutcome.REFUSED_NO_APPROVED_SCHEDULE,
                cutoff_at=None,
                detail="no approved schedule; DEC-008 governs this class",
                correlation_id=correlation_id,
                timestamp=timestamp,
            )
        if not configuration.enabled:
            return self._record_run(
                connection,
                class_name=class_name,
                configuration=configuration,
                outcome=PurgeOutcome.SKIPPED_DISABLED,
                cutoff_at=None,
                detail="class is published but not enabled",
                correlation_id=correlation_id,
                timestamp=timestamp,
            )
        if self.active_hold_exists(connection, class_name=class_name):
            return self._record_run(
                connection,
                class_name=class_name,
                configuration=configuration,
                outcome=PurgeOutcome.REFUSED_LEGAL_HOLD,
                cutoff_at=None,
                detail="an active legal hold covers this class",
                correlation_id=correlation_id,
                timestamp=timestamp,
            )
        if class_name not in SUPPORTED_PURGE_CLASSES:
            detail = (
                "backing table is an append-only ledger; a schema decision is required"
                if class_name in LEDGER_BACKED_CLASSES
                else "no purge target is implemented for this class yet"
            )
            return self._record_run(
                connection,
                class_name=class_name,
                configuration=configuration,
                outcome=PurgeOutcome.REFUSED_UNSUPPORTED_STORE,
                cutoff_at=None,
                detail=detail,
                correlation_id=correlation_id,
                timestamp=timestamp,
            )
        cutoff_at = timestamp - timedelta(days=configuration.retention_days)
        return self._record_run(
            connection,
            class_name=class_name,
            configuration=configuration,
            outcome=PurgeOutcome.COMPLETED,
            cutoff_at=cutoff_at,
            detail=None,
            correlation_id=correlation_id,
            timestamp=timestamp,
            affected_row_count=0,
        )

    def _record_run(
        self,
        connection: Any,
        *,
        class_name: RetentionClass,
        configuration: RetentionConfiguration | None,
        outcome: PurgeOutcome,
        cutoff_at: datetime | None,
        detail: str | None,
        correlation_id: UUID,
        timestamp: datetime,
        affected_row_count: int = 0,
    ) -> PurgeRun:
        run_id = uuid4()

        def mutation(cursor: Any) -> None:
            cursor.execute(
                """
                INSERT INTO retention_purge_runs (
                    run_id, class_name, configuration_version, outcome, cutoff_at,
                    affected_row_count, detail, executed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    class_name.value,
                    configuration.version if configuration else None,
                    outcome.value,
                    cutoff_at,
                    affected_row_count,
                    detail,
                    timestamp,
                ),
            )

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="RETENTION_PURGE_RUN",
                aggregate_id=run_id,
                aggregate_version=1,
                event_type="RETENTION_PURGE_RUN_RECORDED",
                event_payload={
                    "class_name": class_name.value,
                    "outcome": outcome.value,
                    "affected_row_count": affected_row_count,
                },
                audit_action="RETENTION_PURGE_RUN",
                actor_type="WORKER",
                actor_id=None,
                correlation_id=correlation_id,
                outbox_events=(
                    OutboxEvent(
                        "retention.purge_run_recorded.v1",
                        {"run_id": str(run_id), "outcome": outcome.value},
                        f"retention-run:{run_id}",
                    ),
                ),
                occurred_at=timestamp,
            ),
            mutation,
        )
        return PurgeRun(
            run_id=run_id,
            class_name=class_name,
            outcome=outcome,
            affected_row_count=affected_row_count,
            cutoff_at=cutoff_at,
            detail=detail,
        )


def _class_uuid(class_name: RetentionClass) -> UUID:
    """A stable aggregate identifier per class, so versions form one auditable stream."""

    from hashlib import sha256

    return UUID(bytes=sha256(class_name.value.encode("utf-8")).digest()[:16])


__all__ = [
    "LEDGER_BACKED_CLASSES",
    "SUPPORTED_PURGE_CLASSES",
    "PurgeOutcome",
    "PurgeRun",
    "RetentionAuthorizationError",
    "RetentionClass",
    "RetentionConfiguration",
    "RetentionDisposition",
    "RetentionRepository",
    "RetentionStateError",
]
