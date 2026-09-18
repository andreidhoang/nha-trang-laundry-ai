"""Retention control for the section 15 schedule: versioned, held, audited, and fail-closed.

`SHADOW-001` is where real customer names, phone numbers and delivery addresses first enter this
database, so the disposal rule has to exist before the data does. A class with no approved schedule
refuses to run rather than defaulting to delete or to keep-forever.

`DEC-008` was **resolved on 2026-08-18** and now carries a signed per-class schedule. That changed
nothing here, deliberately, and the reason is worth stating because it looks like an omission: the
resolution says in terms that *no class is enabled by the decision itself* — enabling stays a
`publish_configuration` act carrying `DEC-008` as its `decision_ref`. A resolved decision is
therefore permission to publish a schedule, never a published schedule, and this module still ships
with every class refusing. The same resolution routes the ledger-backed classes to
`SEPARATE_DISPOSABLE_PAYLOAD`, which is `RETENTION-STORE-001`, not this item.

Two things this module deliberately does not do.

It does not implement a hidden soft-delete. Section 15 requires purge to be reversible only through
backup policy, so a class whose disposition is PURGE must really remove the data or refuse.

It does not purge an append-only ledger. `RETENTION-STORE-001` resolved that conflict by moving the
disposable payload out from under the trigger rather than by weakening it: the ledger row stays
immutable and keeps identifiers, hashes, decisions and timestamps, and the payload lives in a side
table keyed 1:1 by that row, which carries no append-only trigger because it is the part section 15
schedules for disposal. A class with no such store still ends `REFUSED_UNSUPPORTED_STORE`, and names
which of the four reasons applies to it.

Three owner decisions of 2026-09-17 bind what follows, and each is load-bearing rather than
background:

`DEC-018` -- **evidence wins.** When a payload is also evidence, the run holds those rows back,
records how many and why, and reports `COMPLETED_WITH_EXEMPTIONS`. A partial purge is not a purge
and does not get to be called one. The asymmetry of harm is the reason: deleting the ciphertext of a
DỪNG destroys the only proof the shop was told to stop, and its absence is indistinguishable from
never having been told.

`DEC-019` -- the class of a text follows **what the customer received**, not which process produced
the bytes. That governs `AGENT_RUN_PAYLOAD` and `CONVERSATION_BODY`; neither is implemented here
and both name it in their refusal.

`DEC-020` -- a dedicated `retention_purge` database role executes purges, holding DELETE on exactly
the disposable side tables and nothing else. The grant ships in migration `0038` beside the table it
governs, and every run records the role that ran it -- read from `current_user`, which the database
attests rather than the application claiming.

One residual the owner accepted rather than one nobody noticed. The audit chain that survives a
purge rests on `webhook_events.payload_hash`, and that hash is an **unsalted SHA-256 over the
plaintext**. For a short Vietnamese message -- "DỪNG" above all -- it is dictionary-reversible, so
disposing of the ciphertext leaves a permanent commitment from which the message can be recovered.
`DEC-018` records this knowingly, together with `command_idempotency_records.request_hash`, and
schedules both to move to a keyed HMAC under a per-deployment secret, which preserves equality
comparison and therefore replay defence. That is `HASH-KEYING-001` and it is explicitly not
blocking. It is written here rather than only in the registry because this module's disposal claim
is exactly as strong as that hash, and a reader of this file must not have to discover that
elsewhere.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
    #: DEC-018. Rows were disposed of and rows were held back because they are also retained
    #: evidence. A partial purge is not a purge, so it reports as itself rather than as COMPLETED.
    COMPLETED_WITH_EXEMPTIONS = "COMPLETED_WITH_EXEMPTIONS"
    SKIPPED_DISABLED = "SKIPPED_DISABLED"
    REFUSED_NO_APPROVED_SCHEDULE = "REFUSED_NO_APPROVED_SCHEDULE"
    REFUSED_LEGAL_HOLD = "REFUSED_LEGAL_HOLD"
    REFUSED_UNSUPPORTED_STORE = "REFUSED_UNSUPPORTED_STORE"
    #: A class published as REDACT reaching the purge path. `run_purge` used to ignore
    #: `disposition` entirely, which was harmless only while no class was supported; against a
    #: supported class it would have destroyed what DEC-008 scheduled for redaction.
    REFUSED_DISPOSITION_NOT_EXECUTABLE = "REFUSED_DISPOSITION_NOT_EXECUTABLE"


class UnsupportedStoreReason(StrEnum):
    """Why a class has no executable purge. Every class outside the registry names exactly one.

    The four are genuinely different situations and were previously one sentence about ledger
    triggers. "The obligation is already met because nothing is stored" and "a payload is sitting in
    an append-only table and nobody has moved it" are opposite states of the world, and a run record
    that cannot tell them apart cannot be audited.
    """

    #: DEC-008 keeps this class indefinitely under access restriction. Nothing to schedule.
    RETAINED_INDEFINITELY_BY_DECISION = "RETAINED_INDEFINITELY_BY_DECISION"
    #: No table in the schema holds this class at all.
    NO_BACKING_STORE_EXISTS = "NO_BACKING_STORE_EXISTS"
    #: Tables exist and deliberately keep only hashes or bounded summaries. The retention obligation
    #: is discharged by never having stored the thing, which is the strongest form of compliance.
    NO_DISPOSABLE_PAYLOAD_IS_STORED = "NO_DISPOSABLE_PAYLOAD_IS_STORED"
    #: A real payload sits under an append-only trigger and this item did not separate it.
    PAYLOAD_NOT_YET_SEPARATED = "PAYLOAD_NOT_YET_SEPARATED"
    #: DEC-018. The payload is separable, but a byte-identical copy survives somewhere that forbids
    #: deletion, so purging the original would be a false statement made by software.
    BLOCKED_BY_UNDELETABLE_DUPLICATE = "BLOCKED_BY_UNDELETABLE_DUPLICATE"


#: Advisory-lock namespace for retention, alongside `consent_egress`'s "SUPR".
_LOCK_NAMESPACE = 0x52544E31  # "RTN1"


def retention_class_lock(cursor: Any, *, class_name: RetentionClass) -> None:
    """Serialize a purge of one class against a legal hold being placed on it.

    Both `place_legal_hold` and `run_purge` take this, so a hold committed while a run is in flight
    either precedes the DELETE or waits for it. A row lock would not close the window, for the same
    reason `suppression_lock` documents for a contact's first STOP: on a class's first hold there is
    no row to lock. Under `RETENTION-001` the window was harmless because nothing was deleted; now
    that the purge is real, losing this race destroys data under a legal hold and records it as a
    clean COMPLETED, which is the exact outcome `retention_legal_holds` exists to prevent.

    Transaction-scoped, so it cannot be held across a purge by accident.
    """

    cursor.execute(
        "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
        (_LOCK_NAMESPACE, class_name.value),
    )


@dataclass(frozen=True, slots=True)
class PurgeCounts:
    """What one execution actually did: what it removed, and what DEC-018 made it keep."""

    deleted: int
    exempt: int


@dataclass(frozen=True, slots=True)
class PurgeExecution:
    """What a store needs to dispose of rows and to write the evidence that it did."""

    run_id: UUID
    class_name: RetentionClass
    cutoff_at: datetime
    disposed_at: datetime


#: Dispose of the payload rows past the cutoff that nothing longer-lived points at, and record each
#: disposal as a fact in the same statement that performs it.
#:
#: DEC-018: `consent_events.evidence_webhook_id` is NOT NULL and references `webhook_events(id)`, so
#: for a DỪNG message the ciphertext scheduled for deletion at 30 days *is* the evidence of a
#: withdrawal retained forever. `inbox_replay_conflicts.webhook_event_id` and
#: `agent_runs.source_webhook_event_id` have the same shape. A pointer from a longer-lived record
#: pins its target; the three NOT EXISTS clauses are that rule, written once.
#:
#: The cutoff is tested against `webhook_events.received_at`, never against a copy inside the row
#: being destroyed, and `subject_occurred_at` is copied from that same immutable column. The fact
#: that justifies a disposal has to survive the disposal or the run is unauditable afterwards.
_PURGE_WEBHOOK_PAYLOADS = """
WITH disposed AS (
    DELETE FROM webhook_event_payloads p
     USING webhook_events w
     WHERE w.id = p.webhook_event_id
       AND w.received_at < %(cutoff)s
       AND NOT EXISTS (
           SELECT 1 FROM consent_events c WHERE c.evidence_webhook_id = p.webhook_event_id
       )
       AND NOT EXISTS (
           SELECT 1 FROM inbox_replay_conflicts r WHERE r.webhook_event_id = p.webhook_event_id
       )
       AND NOT EXISTS (
           SELECT 1 FROM agent_runs a WHERE a.source_webhook_event_id = p.webhook_event_id
       )
    RETURNING p.webhook_event_id AS subject_key, w.received_at AS subject_occurred_at
)
INSERT INTO retention_disposal_records (
    run_id, class_name, subject_table, subject_key, subject_occurred_at, disposed_at
)
SELECT %(run_id)s, %(class_name)s, 'webhook_events', subject_key, subject_occurred_at,
       %(disposed_at)s
  FROM disposed
"""

#: Counted after the disposal rather than before it, and as a plain "what is still here past the
#: cutoff" rather than as the complement of the delete predicate. Both choices are deliberate: the
#: post-delete state is exact, so no row can slip between a count and a delete and be held back
#: without being reported -- which would be a COMPLETED run over a silent exemption, the one thing
#: DEC-018 forbids by name.
_COUNT_WEBHOOK_EXEMPTIONS = """
SELECT count(*)
  FROM webhook_event_payloads p
  JOIN webhook_events w ON w.id = p.webhook_event_id
 WHERE w.received_at < %(cutoff)s
"""


def _purge_webhook_payloads(cursor: Any, execution: PurgeExecution) -> PurgeCounts:
    """Dispose, record each disposal, then count whatever evidence pinned in place."""

    cursor.execute(
        _PURGE_WEBHOOK_PAYLOADS,
        {
            "cutoff": execution.cutoff_at,
            "run_id": execution.run_id,
            "class_name": execution.class_name.value,
            "disposed_at": execution.disposed_at,
        },
    )
    deleted = int(cursor.rowcount)
    cursor.execute(_COUNT_WEBHOOK_EXEMPTIONS, {"cutoff": execution.cutoff_at})
    row = cursor.fetchone()
    return PurgeCounts(deleted=deleted, exempt=int(row[0]) if row is not None else 0)


#: Dispose of the summaries of complaints that are finished and past the cutoff.
#:
#: DEC-018 generalises here rather than being confined to the case that motivated it. Its rule is
#: that a pointer from a longer-lived record pins its target; the same asymmetry of harm applies to
#: an unfinished matter. Deleting the description of a complaint whose fault or remedy nobody has
#: decided destroys the only account of what was actually wrong, and `DEC-004` governs remedies that
#: can be owed years later. Keeping it past its schedule harms nobody anyone can point at.
#:
#: So an incident holds its own evidence until it is CLOSED. That is not an open-ended exemption --
#: incidents close, and the run reports every held-back row rather than passing over it in silence.
_PURGE_INCIDENT_EVIDENCE = """
WITH disposed AS (
    DELETE FROM customer_incident_evidence e
     USING customer_incidents i
     WHERE i.id = e.incident_id
       AND i.opened_at < %(cutoff)s
       AND i.status = 'CLOSED'
    RETURNING e.incident_id AS subject_key, i.opened_at AS subject_occurred_at
)
INSERT INTO retention_disposal_records (
    run_id, class_name, subject_table, subject_key, subject_occurred_at, disposed_at
)
SELECT %(run_id)s, %(class_name)s, 'customer_incidents', subject_key, subject_occurred_at,
       %(disposed_at)s
  FROM disposed
"""

_COUNT_INCIDENT_EXEMPTIONS = """
SELECT count(*)
  FROM customer_incident_evidence e
  JOIN customer_incidents i ON i.id = e.incident_id
 WHERE i.opened_at < %(cutoff)s
"""


def _purge_incident_evidence(cursor: Any, execution: PurgeExecution) -> PurgeCounts:
    """Dispose, record each disposal, then count what an unfinished matter held in place."""

    cursor.execute(
        _PURGE_INCIDENT_EVIDENCE,
        {
            "cutoff": execution.cutoff_at,
            "run_id": execution.run_id,
            "class_name": execution.class_name.value,
            "disposed_at": execution.disposed_at,
        },
    )
    deleted = int(cursor.rowcount)
    cursor.execute(_COUNT_INCIDENT_EXEMPTIONS, {"cutoff": execution.cutoff_at})
    row = cursor.fetchone()
    return PurgeCounts(deleted=deleted, exempt=int(row[0]) if row is not None else 0)


#: Dispose of the transcripts past the cutoff whose answer does not survive somewhere undeletable.
#:
#: DEC-018 held this class NOT_SUPPORTED because every answer was stored twice, the second copy in
#: `command_idempotency_records.response` under a trigger that refuses DELETE outright and refuses
#: UPDATE once the response is non-NULL. `ASSISTANT-RETENTION-001` stopped that copy being written:
#: `_turn_mapping` now returns the turn id alone and a replay rehydrates from `assistant_turns`.
#:
#: Records written before that change still carry an answer and still cannot be deleted, so those
#: turns are pinned and the run says so. This is DEC-018's own rule -- a longer-lived record pins
#: its target -- applied to the record that motivated it. It self-heals: nothing written from now on
#: has an `answer` key, so no new turn can be pinned this way, and the exemption count falls to zero
#: as the old records age past their own retention.
_PURGE_ASSISTANT_PAYLOADS = """
WITH disposed AS (
    DELETE FROM assistant_turn_payloads p
     USING assistant_turns t
     WHERE t.turn_id = p.turn_id
       AND t.created_at < %(cutoff)s
       AND NOT EXISTS (
           SELECT 1 FROM command_idempotency_records c
            WHERE c.response ? 'answer'
              AND c.response->>'turn_id' = t.turn_id::text
       )
    RETURNING p.turn_id AS subject_key, t.created_at AS subject_occurred_at
)
INSERT INTO retention_disposal_records (
    run_id, class_name, subject_table, subject_key, subject_occurred_at, disposed_at
)
SELECT %(run_id)s, %(class_name)s, 'assistant_turns', subject_key, subject_occurred_at,
       %(disposed_at)s
  FROM disposed
"""

_COUNT_ASSISTANT_EXEMPTIONS = """
SELECT count(*)
  FROM assistant_turn_payloads p
  JOIN assistant_turns t ON t.turn_id = p.turn_id
 WHERE t.created_at < %(cutoff)s
"""


def _purge_assistant_payloads(cursor: Any, execution: PurgeExecution) -> PurgeCounts:
    """Dispose, record each disposal, then count what an undeletable copy held in place."""

    cursor.execute(
        _PURGE_ASSISTANT_PAYLOADS,
        {
            "cutoff": execution.cutoff_at,
            "run_id": execution.run_id,
            "class_name": execution.class_name.value,
            "disposed_at": execution.disposed_at,
        },
    )
    deleted = int(cursor.rowcount)
    cursor.execute(_COUNT_ASSISTANT_EXEMPTIONS, {"cutoff": execution.cutoff_at})
    row = cursor.fetchone()
    return PurgeCounts(deleted=deleted, exempt=int(row[0]) if row is not None else 0)


@dataclass(frozen=True, slots=True)
class DisposablePayloadStore:
    """A side table holding exactly what section 15 schedules, keyed to an immutable ledger row."""

    class_name: RetentionClass
    payload_table: str
    ledger_table: str
    #: What the ledger row still carries after its payload is gone. This is the audit chain, and a
    #: test asserts each column is really still there -- a comment cannot.
    ledger_retains: tuple[str, ...]
    #: The DEC-018 rule for this class, in one sentence, recorded verbatim in the run detail so an
    #: auditor reads why rows were held back without opening the source.
    exemption_rule: str
    purge: Callable[[Any, PurgeExecution], PurgeCounts]


#: Every class with an implemented, tested purge target. `SUPPORTED_PURGE_CLASSES` is derived from
#: this mapping rather than maintained beside it, so the set cannot claim a class the code cannot
#: dispose of -- which was the specific failure mode `RETENTION-001` warned about.
DISPOSABLE_PAYLOAD_STORES: Mapping[RetentionClass, DisposablePayloadStore] = {
    RetentionClass.RAW_WEBHOOK_PAYLOAD: DisposablePayloadStore(
        class_name=RetentionClass.RAW_WEBHOOK_PAYLOAD,
        payload_table="webhook_event_payloads",
        ledger_table="webhook_events",
        ledger_retains=(
            "id",
            "provider",
            "channel_account_id",
            "provider_event_id",
            "payload_hash",
            "event_type",
            "contact_binding_id",
            "channel",
            "opt_out_disposition",
            "opt_out_registry_version",
            "processing_status",
            "received_at",
        ),
        exemption_rule=(
            "held back payloads pinned by retained evidence: consent_events.evidence_webhook_id, "
            "inbox_replay_conflicts.webhook_event_id, agent_runs.source_webhook_event_id (DEC-018)"
        ),
        purge=_purge_webhook_payloads,
    ),
    RetentionClass.INCIDENT_EVIDENCE: DisposablePayloadStore(
        class_name=RetentionClass.INCIDENT_EVIDENCE,
        payload_table="customer_incident_evidence",
        ledger_table="customer_incidents",
        ledger_retains=(
            "id",
            "store_id",
            "order_id",
            "affected_message_id",
            "contact_scope_hash",
            "category",
            "status",
            "fault_decided",
            "remedy_decided",
            "evidence_summary_hash",
            "opened_at",
        ),
        exemption_rule=(
            "held back summaries of incidents that are not CLOSED: an unfinished matter pins its "
            "own evidence, because DEC-004 governs remedies that can be owed later and the "
            "description is the only account of what was wrong (DEC-018, DEC-028)"
        ),
        purge=_purge_incident_evidence,
    ),
    RetentionClass.ASSISTANT_TRANSCRIPT: DisposablePayloadStore(
        class_name=RetentionClass.ASSISTANT_TRANSCRIPT,
        payload_table="assistant_turn_payloads",
        ledger_table="assistant_turns",
        ledger_retains=(
            "turn_id",
            "store_id",
            "staff_user_id",
            "intent",
            "links",
            "reason_codes",
            "correlation_id",
            "created_at",
        ),
        exemption_rule=(
            "held back transcripts whose answer survives in a command_idempotency_records row "
            "written before ASSISTANT-RETENTION-001, which protect_idempotency_record forbids "
            "anyone to delete; purging the original would report a disposal that did not happen "
            "(DEC-018)"
        ),
        purge=_purge_assistant_payloads,
    ),
}

#: Classes for which a purge target is actually implemented. Derived, never hand-written.
SUPPORTED_PURGE_CLASSES: frozenset[RetentionClass] = frozenset(DISPOSABLE_PAYLOAD_STORES)

#: Why each remaining class refuses. Keyed exhaustively: a test asserts that this mapping and the
#: registry together partition `RetentionClass` exactly, so a class added to the enum with no
#: decision behind it fails the suite instead of refusing anonymously.
UNSUPPORTED_STORE_REASONS: Mapping[RetentionClass, tuple[UnsupportedStoreReason, str]] = {
    RetentionClass.CONVERSATION_BODY: (
        UnsupportedStoreReason.PAYLOAD_NOT_YET_SEPARATED,
        "agent_drafts.draft_text and agent_draft_reviews.edited_text when the text was sent "
        "(DEC-019); both sit under reject_ledger_mutation and DEC-008 schedules REDACT, which this "
        "purge path does not perform",
    ),
    RetentionClass.AGENT_RUN_PAYLOAD: (
        UnsupportedStoreReason.PAYLOAD_NOT_YET_SEPARATED,
        "agent_tool_calls keeps only a sha256 request_fingerprint and a bounded safe_summary, but "
        "DEC-019 assigns never-sent agent_drafts.draft_text to this class and that text is not yet "
        "separated",
    ),
    RetentionClass.EXACT_DELIVERY_LOCATION: (
        UnsupportedStoreReason.NO_BACKING_STORE_EXISTS,
        "no address, coordinate or ward column exists in any of the 42 tables; delivery_legs "
        "records an outcome and deliberately no free text (DEC-019, DEC-023)",
    ),
    RetentionClass.CONSENT_EVIDENCE: (
        UnsupportedStoreReason.RETAINED_INDEFINITELY_BY_DECISION,
        "DEC-008 retains consent evidence indefinitely under access restriction; it is the proof a "
        "customer asked to be left alone",
    ),
    RetentionClass.ORDER_FINANCIAL_RECORD: (
        UnsupportedStoreReason.PAYLOAD_NOT_YET_SEPARATED,
        "DEC-008 schedules 3650 days under the Vietnamese accounting retention period; the record "
        "is the orders ledger itself rather than a payload beside it, so disposal is a different "
        "design and no row is near the horizon",
    ),
    RetentionClass.DEBUG_LOG: (
        UnsupportedStoreReason.NO_BACKING_STORE_EXISTS,
        "debug logs are process output and are not persisted to this database",
    ),
    RetentionClass.SECURITY_AUDIT_EVENT: (
        UnsupportedStoreReason.RETAINED_INDEFINITELY_BY_DECISION,
        "DEC-008 retains security audit events indefinitely under access restriction; audit_events "
        "is the chain every other record is reconstructed from",
    ),
}


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
    #: DEC-020 requires every run to record its per-class counts and its held-back exemptions.
    exempt_row_count: int = 0
    #: The database role that executed the run, read from `current_user`. `None` only for runs
    #: recorded before migration 0038 added the column.
    executed_by_role: str | None = None


@dataclass(frozen=True, slots=True)
class DisposalRecord:
    """The answer to "what happened to this record's payload", for one ledger row."""

    run_id: UUID
    class_name: RetentionClass
    subject_table: str
    subject_key: UUID
    subject_occurred_at: datetime
    disposed_at: datetime
    configuration_version: int | None
    decision_ref: str | None
    cutoff_at: datetime | None
    executed_by_role: str | None


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
            # Taken before the insert, and taken by `run_purge` too: a hold placed while a purge is
            # in flight must either land before that purge reads holds, or wait for it to finish.
            retention_class_lock(cursor, class_name=class_name)
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

    @staticmethod
    def disposal_record(
        connection: Any, *, subject_table: str, subject_key: UUID
    ) -> DisposalRecord | None:
        """What happened to one ledger row's payload: a recorded fact, or `None`.

        This is the per-record auditor read the packet asks for -- "an auditor reading a purged
        record sees that it existed, what was decided, and that its payload was disposed of under a
        named schedule -- not a gap". It answers from `retention_disposal_records` joined to the run
        that wrote it, so the schedule version, the decision that approved it, the cutoff and the
        executing role all come back with the disposal.

        `None` means no disposal is recorded, which after `0038` is a stronger statement than it
        sounds: a ledger row whose payload row is also absent and which has no disposal record has
        lost its payload to something other than a retention run, and that is now detectable rather
        than indistinguishable from a lawful purge.

        Not folded into `ShadowConsoleRepository.audit_timeline`: that read is store-scoped and
        resolves an aggregate's owning store from a fixed list of store-bearing tables.
        `webhook_events` has no store column and is not in that list, so a webhook id returns an
        empty timeline there before and after any purge. Surfacing a webhook disposal through it
        would mean inventing a store for a storeless table, which is a worse answer than a read that
        says plainly what it is scoped to.
        """

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.run_id, d.class_name, d.subject_table, d.subject_key,
                       d.subject_occurred_at, d.disposed_at,
                       r.configuration_version, r.cutoff_at, r.executed_by_role,
                       c.decision_ref
                  FROM retention_disposal_records d
                  JOIN retention_purge_runs r ON r.run_id = d.run_id
                  LEFT JOIN retention_class_configurations c
                    ON c.class_name = r.class_name AND c.version = r.configuration_version
                 WHERE d.subject_table = %s AND d.subject_key = %s
                """,
                (subject_table, subject_key),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return DisposalRecord(
            run_id=row[0] if isinstance(row[0], UUID) else UUID(str(row[0])),
            class_name=RetentionClass(str(row[1])),
            subject_table=str(row[2]),
            subject_key=row[3] if isinstance(row[3], UUID) else UUID(str(row[3])),
            subject_occurred_at=row[4],
            disposed_at=row[5],
            configuration_version=int(row[6]) if row[6] is not None else None,
            cutoff_at=row[7],
            executed_by_role=str(row[8]) if row[8] is not None else None,
            decision_ref=str(row[9]) if row[9] is not None else None,
        )

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
        # Everything from here on happens inside one transaction holding the class lock: the
        # binding legal-hold check, the disposal, the disposal evidence and the run record. Under
        # `RETENTION-001` the hold was read on its own connection outside any transaction, which was
        # harmless while no class could delete anything. It is not harmless now.
        with connection.transaction():
            with connection.cursor() as cursor:
                retention_class_lock(cursor, class_name=class_name)

            if self.active_hold_exists(connection, class_name=class_name):
                return self._record_run(
                    connection,
                    run_id=uuid4(),
                    class_name=class_name,
                    configuration=configuration,
                    outcome=PurgeOutcome.REFUSED_LEGAL_HOLD,
                    cutoff_at=None,
                    detail="an active legal hold covers this class",
                    correlation_id=correlation_id,
                    timestamp=timestamp,
                )
            if configuration.disposition is not RetentionDisposition.PURGE:
                # Checked before the store is consulted, and deliberately so: this refusal does not
                # depend on what is implemented. A class DEC-008 scheduled for redaction must not be
                # purged by the best store in the world.
                return self._record_run(
                    connection,
                    run_id=uuid4(),
                    class_name=class_name,
                    configuration=configuration,
                    outcome=PurgeOutcome.REFUSED_DISPOSITION_NOT_EXECUTABLE,
                    cutoff_at=None,
                    detail=(
                        f"published disposition is {configuration.disposition.value}; this job "
                        "executes PURGE only and will not redact by deleting"
                    ),
                    correlation_id=correlation_id,
                    timestamp=timestamp,
                )
            store = DISPOSABLE_PAYLOAD_STORES.get(class_name)
            if store is None:
                reason, explanation = UNSUPPORTED_STORE_REASONS[class_name]
                return self._record_run(
                    connection,
                    run_id=uuid4(),
                    class_name=class_name,
                    configuration=configuration,
                    outcome=PurgeOutcome.REFUSED_UNSUPPORTED_STORE,
                    cutoff_at=None,
                    detail=f"{reason.value}: {explanation}"[:500],
                    correlation_id=correlation_id,
                    timestamp=timestamp,
                )

            run_id = uuid4()
            cutoff_at = timestamp - timedelta(days=configuration.retention_days)
            with connection.cursor() as cursor:
                counts = store.purge(
                    cursor,
                    PurgeExecution(
                        run_id=run_id,
                        class_name=class_name,
                        cutoff_at=cutoff_at,
                        disposed_at=timestamp,
                    ),
                )
            if counts.exempt:
                outcome = PurgeOutcome.COMPLETED_WITH_EXEMPTIONS
                detail: str | None = f"{counts.exempt} {store.exemption_rule}"[:500]
            else:
                outcome = PurgeOutcome.COMPLETED
                detail = None
            # `commit_material_change` opens its own `connection.transaction()` here, which psycopg
            # makes a SAVEPOINT rather than a second transaction -- so the disposal, its evidence
            # and the run record commit together or not at all. A purge that committed without its
            # record is the unprovable disposal `0023` refuses to allow: it destroys the data and
            # the account of destroying it.
            return self._record_run(
                connection,
                run_id=run_id,
                class_name=class_name,
                configuration=configuration,
                outcome=outcome,
                cutoff_at=cutoff_at,
                detail=detail,
                correlation_id=correlation_id,
                timestamp=timestamp,
                affected_row_count=counts.deleted,
                exempt_row_count=counts.exempt,
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
        run_id: UUID | None = None,
        affected_row_count: int = 0,
        exempt_row_count: int = 0,
    ) -> PurgeRun:
        # Supplied by the purge path, which needs the identifier before the run record exists so
        # each disposal can name the run that performed it. Generated here for the refusal paths,
        # which dispose of nothing.
        run_id = run_id or uuid4()
        executed_by: list[str | None] = [None]

        def mutation(cursor: Any) -> None:
            # `current_user` rather than a value passed in from Python. DEC-020 makes the executing
            # identity part of the record, and an identity the application names is an identity the
            # application could misname; this one is the database's own answer.
            cursor.execute(
                """
                INSERT INTO retention_purge_runs (
                    run_id, class_name, configuration_version, outcome, cutoff_at,
                    affected_row_count, exempt_row_count, detail, executed_by_role, executed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, current_user, %s)
                RETURNING executed_by_role
                """,
                (
                    run_id,
                    class_name.value,
                    configuration.version if configuration else None,
                    outcome.value,
                    cutoff_at,
                    affected_row_count,
                    exempt_row_count,
                    detail,
                    timestamp,
                ),
            )
            row = cursor.fetchone()
            executed_by[0] = str(row[0]) if row is not None else None

        record = {
            "run_id": str(run_id),
            "class_name": class_name.value,
            "outcome": outcome.value,
            "affected_row_count": affected_row_count,
            "exempt_row_count": exempt_row_count,
            "cutoff_at": cutoff_at.isoformat() if cutoff_at else None,
            "configuration_version": configuration.version if configuration else None,
            "decision_ref": configuration.decision_ref if configuration else None,
            "detail": detail,
        }

        commit_material_change(
            connection,
            MaterialChange(
                aggregate_type="RETENTION_PURGE_RUN",
                aggregate_id=run_id,
                aggregate_version=1,
                event_type="RETENTION_PURGE_RUN_RECORDED",
                event_payload=record,
                audit_action="RETENTION_PURGE_RUN",
                actor_type="WORKER",
                actor_id=None,
                correlation_id=correlation_id,
                # Section 16 asks an audited event for the resource it concerns and the facts that
                # justify it, and `{"event_type": ...}` alone met none of that. The cutoff, the
                # schedule version and the decision that approved it are the whole justification for
                # destroying data, and until now they reached only `retention_purge_runs` -- so the
                # immutable event ledger recorded that a purge happened and not why it was allowed.
                audit_details=record,
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
            exempt_row_count=exempt_row_count,
            executed_by_role=executed_by[0],
        )


def _class_uuid(class_name: RetentionClass) -> UUID:
    """A stable aggregate identifier per class, so versions form one auditable stream."""

    from hashlib import sha256

    return UUID(bytes=sha256(class_name.value.encode("utf-8")).digest()[:16])


__all__ = [
    "DISPOSABLE_PAYLOAD_STORES",
    "SUPPORTED_PURGE_CLASSES",
    "UNSUPPORTED_STORE_REASONS",
    "DisposablePayloadStore",
    "DisposalRecord",
    "PurgeCounts",
    "PurgeExecution",
    "PurgeOutcome",
    "PurgeRun",
    "RetentionAuthorizationError",
    "RetentionClass",
    "RetentionConfiguration",
    "RetentionDisposition",
    "RetentionRepository",
    "RetentionStateError",
    "UnsupportedStoreReason",
    "retention_class_lock",
]
