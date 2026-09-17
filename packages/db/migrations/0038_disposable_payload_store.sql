-- RETENTION-STORE-001: separate the disposable payload from the append-only ledger.
--
-- `RETENTION-001` built the retention control plane and proved it cannot execute. Five of the nine
-- section 15 classes are backed by tables that reject every DELETE and UPDATE, so
-- `SUPPORTED_PURGE_CLASSES` shipped empty and no class could dispose of anything. Both requirements
-- are correct and neither yields: an audit ledger that can be rewritten is not an audit ledger, and
-- a retention schedule that cannot run accumulates customer data the business promised to dispose
-- of. `SHADOW-001` is where real names and phone numbers first arrive, so this has to exist before
-- the data does.
--
-- The resolution is to stop asking one row to be both things:
--
--   * the **ledger row** stays immutable and keeps what an auditor reads -- identifiers, the
--     content hash, the disposition, the decision, the timestamps. It never expires.
--   * the **payload** moves to a side table keyed 1:1 by the ledger row. That table carries no
--     append-only trigger, on purpose: it is the part section 15 schedules for disposal.
--
-- Deleting a payload row leaves the ledger row and its meaning intact. An auditor reading a purged
-- record still sees that the event arrived, which exact bytes arrived (`payload_hash`), what was
-- decided about it, and -- from `retention_purge_runs` -- under which schedule version and cutoff
-- its payload was disposed of, by which database role, and how many rows were held back. That is an
-- account, not a gap.
--
-- **No trigger is dropped, relaxed or bypassed.** `protect_webhook_event` still rejects every
-- DELETE and every column change on `webhook_events`. It is replaced below only because one of the
-- columns it names stops existing; every other column it guarded, it still guards.
--
-- **On the backfill and the append-only trigger.** `0034` recorded the trap: ADD COLUMN is DDL and
-- fires no row trigger, but the UPDATE that follows it is DML and does -- which applied cleanly to
-- the empty database every test uses and failed on every database holding a single row. This
-- migration has no such step. Moving the payload is an INSERT ... SELECT into a brand-new table
-- (no trigger) followed by DROP COLUMN (DDL, no trigger). Nothing here issues DML against a
-- protected table, so nothing here needs a trigger disabled. `test_migration_populated.py` applies
-- 0001-0037, seeds real rows, then applies this file and compares row count and payload digest on
-- both sides -- because the suite's own fixtures always migrate a fresh database and structurally
-- cannot see this class of defect.

-- ------------------------------------------------------------------------------------------------
-- The separable store
-- ------------------------------------------------------------------------------------------------

CREATE TABLE webhook_event_payloads (
    -- ON DELETE RESTRICT in the one direction that can exist. Deleting a payload row cannot touch
    -- the ledger, and deleting a ledger row is now blocked twice: by `protect_webhook_event` and by
    -- this reference. CASCADE either way would be a defect -- ledger to side would be a hidden
    -- purge path that no run record ever accounts for.
    webhook_event_id UUID PRIMARY KEY REFERENCES webhook_events(id) ON DELETE RESTRICT,
    encrypted_payload BYTEA NOT NULL CHECK (octet_length(encrypted_payload) > 0)
);

-- Disposability is added alongside immutability, not traded for it.
--
-- `protect_webhook_event` named `encrypted_payload` in its immutable-column list, so before this
-- migration the ciphertext could not be rewritten by anyone. Moving it to a table with no guard at
-- all would quietly hand that power to every identity holding UPDATE -- a weakening, in the one
-- item whose first constraint is that it weakens nothing. So the payload stays immutable for
-- exactly as long as it exists, and DELETE -- and only DELETE -- is permitted, because DELETE is
-- the purge.
CREATE FUNCTION reject_payload_rewrite() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'disposable payloads are immutable; they may be disposed of, never rewritten';
END;
$$;

CREATE TRIGGER webhook_event_payloads_immutable
    BEFORE UPDATE ON webhook_event_payloads
    FOR EACH ROW EXECUTE FUNCTION reject_payload_rewrite();

-- The cutoff is read from the ledger row rather than copied here. `webhook_events.received_at` is
-- immutable, so a copy could not drift -- but one source of truth for "when did this arrive" is
-- still the thing an auditor checks, so the purge joins rather than trusting a duplicate.
CREATE INDEX webhook_events_received_at_idx ON webhook_events (received_at);

INSERT INTO webhook_event_payloads (webhook_event_id, encrypted_payload)
SELECT id, encrypted_payload FROM webhook_events;

-- Same guarantee, one fewer column to name: the payload is no longer in this table to protect.
CREATE OR REPLACE FUNCTION protect_webhook_event() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'webhook events cannot be hard deleted';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.provider IS DISTINCT FROM OLD.provider
       OR NEW.channel_account_id IS DISTINCT FROM OLD.channel_account_id
       OR NEW.provider_event_id IS DISTINCT FROM OLD.provider_event_id
       OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
       OR NEW.event_type IS DISTINCT FROM OLD.event_type
       OR NEW.contact_binding_id IS DISTINCT FROM OLD.contact_binding_id
       OR NEW.channel IS DISTINCT FROM OLD.channel
       OR NEW.opt_out_disposition IS DISTINCT FROM OLD.opt_out_disposition
       OR NEW.opt_out_registry_version IS DISTINCT FROM OLD.opt_out_registry_version
       OR NEW.received_at IS DISTINCT FROM OLD.received_at
       OR OLD.processing_status <> 'DISPATCH_PENDING'
       OR NEW.processing_status <> 'PROCESSED' THEN
        RAISE EXCEPTION 'webhook event payload and lifecycle are immutable';
    END IF;
    RETURN NEW;
END;
$$;

ALTER TABLE webhook_events DROP COLUMN encrypted_payload;

-- ------------------------------------------------------------------------------------------------
-- Positive per-record disposal evidence
-- ------------------------------------------------------------------------------------------------
--
-- Without this table the only account of a disposal is class-level: a `retention_purge_runs` row
-- naming a cutoff and a count, and the absence of the payload row. An adversarial review of this
-- item's design refuted that inference in both directions, and both refutations survive into the
-- implementation:
--
--   * absence does not imply lawful purge -- any identity holding DELETE produces a state
--     observationally identical to a scheduled one. `DEC-020`'s role separation narrows who that
--     can be; it does not turn absence into a record.
--   * past-the-cutoff does not imply absence -- `DEC-018` holds evidence-pinned rows back
--     indefinitely, so an in-scope row being present proves nothing either.
--
-- An inference unsound in both directions carries no evidentiary weight, and the packet asks for
-- the opposite outcome in terms: an auditor reading a purged record must see "that its payload was
-- disposed of under a named schedule -- not a gap".
--
-- So the disposal is written as a fact, in the same statement that performs it. This is not the
-- tombstone the packet prohibits and it retains no payload: every column is a key or a timestamp
-- that already lives forever in the immutable ledger row beside it. It also restores, as evidence,
-- what `encrypted_payload BYTEA NOT NULL` used to guarantee structurally -- a ledger row with no
-- payload row and no disposal record is now a detectable anomaly rather than something
-- indistinguishable from a lawful purge.
CREATE TABLE retention_disposal_records (
    record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- DEFERRABLE because the disposal and its run record are written in one transaction and the
    -- run's counts are not known until the disposal has happened. The constraint still holds at
    -- commit, so no disposal record can exist without the run that produced it.
    run_id UUID NOT NULL REFERENCES retention_purge_runs(run_id) DEFERRABLE INITIALLY DEFERRED,
    class_name TEXT NOT NULL CHECK (length(class_name) BETWEEN 1 AND 100),
    subject_table TEXT NOT NULL CHECK (length(subject_table) BETWEEN 1 AND 100),
    subject_key UUID NOT NULL,
    -- The timestamp the disposal was justified by, copied from the immutable ledger row rather than
    -- from the row being destroyed. After the purge this is the only surviving proof that the
    -- disposed payload really was past the run's cutoff.
    subject_occurred_at TIMESTAMPTZ NOT NULL,
    disposed_at TIMESTAMPTZ NOT NULL,

    -- A payload is disposed of exactly once, so a second record for the same subject is impossible
    -- rather than merely unexpected.
    UNIQUE (subject_table, subject_key)
);

CREATE INDEX retention_disposal_records_run_idx ON retention_disposal_records (run_id);

CREATE TRIGGER retention_disposal_records_append_only
    BEFORE UPDATE OR DELETE ON retention_disposal_records
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- ------------------------------------------------------------------------------------------------
-- DEC-020: who may execute a purge, and what the database grants them
-- ------------------------------------------------------------------------------------------------
--
-- Before this migration no GRANT, REVOKE or CREATE ROLE statement existed in 0001-0037, and
-- `apply_demo_grants.py` REVOKEs DELETE from `laundry_api` and `laundry_worker` with a test pinning
-- that. A purge is a DELETE, so no identity could execute one:
-- `SECURITY_RELIABILITY_SPEC_V1` section 9.4 requires deletion of raw inbox data to be audited AND
-- restricted, and only the audited half existed.
--
-- The owner's decision: a dedicated `retention_purge` role holds DELETE on exactly the disposable
-- side tables and nothing else. Not the API role and not the worker role -- the identity that
-- serves customers must not be the identity that can erase their records, because a compromise of
-- the API must not be able to delete the evidence of itself. And the GRANT ships in the migration
-- that creates the purgeable table, because a table the schedule promises to purge that no identity
-- may delete from reports a schedule it cannot honour, and that drift is silent.
--
-- `NOLOGIN` and no password: this is a group role carrying one privilege, and an operator grants
-- membership to a real login identity at deployment time. A migration is a repository file, so it
-- may not contain a credential.
--
-- Created here rather than in provisioning so the table and its permission cannot exist apart. The
-- migration identity therefore needs CREATEROLE; `docs/runbooks/` records that as a provisioning
-- requirement. The guard is `pg_roles`, so re-applying against a cluster that already has the role
-- is a no-op rather than an error.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'retention_purge') THEN
        CREATE ROLE retention_purge NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO retention_purge;
GRANT SELECT, DELETE ON webhook_event_payloads TO retention_purge;
-- The cutoff and the DEC-018 exemption tests are joins, so the purge must read them. It may read
-- them and nothing else, and it may delete from nowhere else.
GRANT SELECT ON webhook_events TO retention_purge;
GRANT SELECT ON consent_events TO retention_purge;
GRANT SELECT ON inbox_replay_conflicts TO retention_purge;
GRANT SELECT ON agent_runs TO retention_purge;
-- A run that cannot record itself must not run: `retention_purge_runs` plus the atomic envelope
-- (`domain_events`, `audit_events`, `outbox_events`) are INSERT-only for this role. It can write
-- the account of what it deleted and can never rewrite one.
GRANT SELECT ON retention_class_configurations TO retention_purge;
GRANT SELECT ON retention_legal_holds TO retention_purge;
GRANT SELECT, INSERT ON retention_purge_runs TO retention_purge;
GRANT SELECT, INSERT ON retention_disposal_records TO retention_purge;
GRANT INSERT ON domain_events TO retention_purge;
GRANT INSERT ON audit_events TO retention_purge;
GRANT INSERT ON outbox_events TO retention_purge;

-- ------------------------------------------------------------------------------------------------
-- DEC-018: what a purge means when the same bytes are also retained evidence
-- ------------------------------------------------------------------------------------------------
--
-- `consent_events.evidence_webhook_id` is NOT NULL and references `webhook_events(id)`, so for a
-- DỪNG message the ciphertext scheduled for deletion at 30 days IS the evidence of a withdrawal
-- retained forever. `inbox_replay_conflicts.webhook_event_id` and `agent_runs.source_webhook_event_id`
-- have the same shape. The owner decided evidence wins: a pointer from a longer-lived record pins
-- its target, the run holds those rows back, and it records how many and why.
--
-- The asymmetry of harm is the reason. Deleting the ciphertext of a DỪNG destroys the only proof the
-- shop was told to stop, and its absence is indistinguishable from never having been told. Keeping a
-- payload past its schedule harms nobody anyone can point at.
--
-- A partial purge is not a purge, so it does not get to be called one. `COMPLETED_WITH_EXEMPTIONS`
-- is a distinct outcome and the CHECK below makes `COMPLETED` mean exactly zero held-back rows --
-- in the schema rather than in a code convention, so a run cannot be recorded as clean over a
-- silent exemption even by a caller that wanted to.
ALTER TABLE retention_purge_runs
    DROP CONSTRAINT retention_purge_runs_outcome_check;

ALTER TABLE retention_purge_runs
    ADD CONSTRAINT retention_purge_runs_outcome_check CHECK (outcome IN (
        'COMPLETED', 'COMPLETED_WITH_EXEMPTIONS', 'SKIPPED_DISABLED',
        'REFUSED_NO_APPROVED_SCHEDULE', 'REFUSED_LEGAL_HOLD', 'REFUSED_UNSUPPORTED_STORE',
        'REFUSED_DISPOSITION_NOT_EXECUTABLE'
    ));

ALTER TABLE retention_purge_runs
    ADD COLUMN exempt_row_count INTEGER NOT NULL DEFAULT 0 CHECK (exempt_row_count >= 0);

-- Nullable on purpose: rows written before this column existed have no honest value to carry, and
-- defaulting them to the migration identity would attribute historical runs to whoever deployed.
-- New rows take `current_user`, which the database attests rather than the application claiming.
ALTER TABLE retention_purge_runs
    ADD COLUMN executed_by_role TEXT NULL CHECK (
        executed_by_role IS NULL OR length(executed_by_role) BETWEEN 1 AND 100
    );

ALTER TABLE retention_purge_runs
    DROP CONSTRAINT retention_purge_run_affects_only_when_completed;

ALTER TABLE retention_purge_runs
    ADD CONSTRAINT retention_purge_run_affects_only_when_completed CHECK (
        outcome IN ('COMPLETED', 'COMPLETED_WITH_EXEMPTIONS') OR affected_row_count = 0
    );

-- DEC-018 as a constraint: a run that held rows back may not call itself COMPLETED, and a run that
-- refused may not claim to have held anything back.
ALTER TABLE retention_purge_runs
    ADD CONSTRAINT retention_purge_run_exemptions_are_declared CHECK (
        (outcome = 'COMPLETED_WITH_EXEMPTIONS' AND exempt_row_count > 0)
        OR (outcome <> 'COMPLETED_WITH_EXEMPTIONS' AND exempt_row_count = 0)
    );

-- Section 15 disposal is INSERT + DELETE on the side table for the purge role, and INSERT + SELECT
-- for the application. `ALTER DEFAULT PRIVILEGES` in `apply_demo_grants.py` already grants
-- SELECT/INSERT/UPDATE on new tables to the application roles and grants them no DELETE, so
-- `webhook_event_payloads` is writable by the inbox and deletable only by `retention_purge`.
