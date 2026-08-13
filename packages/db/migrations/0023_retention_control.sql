-- RETENTION-001: the control plane for the section 15 retention schedule.
--
-- Three tables and no purge of anything yet. Retention classes are versioned published
-- configuration rather than constants in code, because a schedule that changes by deployment is a
-- schedule nobody reviewed. No class ships enabled: DEC-008 is open, and a class with no approved
-- schedule must refuse to run rather than default to deleting or to keeping forever.
--
-- Legal holds are first-class and suspend purge for their scope, because "we deleted it during an
-- investigation" is the outcome this table exists to prevent.

CREATE TABLE retention_class_configurations (
    class_name TEXT NOT NULL CHECK (class_name IN (
        'RAW_WEBHOOK_PAYLOAD', 'CONVERSATION_BODY', 'AGENT_RUN_PAYLOAD',
        'EXACT_DELIVERY_LOCATION', 'CONSENT_EVIDENCE', 'ORDER_FINANCIAL_RECORD',
        'INCIDENT_EVIDENCE', 'DEBUG_LOG', 'SECURITY_AUDIT_EVENT'
    )),
    version INTEGER NOT NULL CHECK (version > 0),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    disposition TEXT NOT NULL CHECK (disposition IN ('PURGE', 'REDACT')),
    retention_days INTEGER NULL CHECK (retention_days IS NULL OR retention_days > 0),
    decision_ref TEXT NULL CHECK (decision_ref IS NULL OR decision_ref ~ '^DEC-[0-9]{3}$'),
    published_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    published_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (class_name, version),

    -- An enabled class must carry both a period and the decision that approved it. This is the
    -- constraint that keeps DEC-008 from being satisfied by a deployment instead of by a decision.
    CONSTRAINT retention_configuration_enabled_is_approved CHECK (
        NOT enabled OR (retention_days IS NOT NULL AND decision_ref IS NOT NULL)
    )
);

CREATE TRIGGER retention_class_configurations_append_only
    BEFORE UPDATE OR DELETE ON retention_class_configurations
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TABLE retention_legal_holds (
    hold_id UUID PRIMARY KEY,
    class_name TEXT NOT NULL,
    subject_contact_binding_id UUID NULL,
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
    placed_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    placed_at TIMESTAMPTZ NOT NULL,
    released_by_staff_id UUID NULL REFERENCES staff_users(id),
    released_at TIMESTAMPTZ NULL,

    CONSTRAINT retention_legal_hold_release_evidence CHECK (
        (released_at IS NULL AND released_by_staff_id IS NULL)
        OR (released_at IS NOT NULL AND released_by_staff_id IS NOT NULL)
    )
);

CREATE INDEX retention_legal_holds_active_idx
    ON retention_legal_holds (class_name, subject_contact_binding_id)
    WHERE released_at IS NULL;

CREATE TRIGGER retention_legal_holds_no_hard_delete
    BEFORE DELETE ON retention_legal_holds
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

-- Every run, including one that purged nothing and one that refused to run. A purge that left no
-- run record did not happen as far as this system can prove, and an unprovable purge is worse than
-- no purge: it destroys data and the account of destroying it.
CREATE TABLE retention_purge_runs (
    run_id UUID PRIMARY KEY,
    class_name TEXT NOT NULL,
    configuration_version INTEGER NULL,
    outcome TEXT NOT NULL CHECK (outcome IN (
        'COMPLETED', 'SKIPPED_DISABLED', 'REFUSED_NO_APPROVED_SCHEDULE',
        'REFUSED_LEGAL_HOLD', 'REFUSED_UNSUPPORTED_STORE'
    )),
    cutoff_at TIMESTAMPTZ NULL,
    affected_row_count INTEGER NOT NULL DEFAULT 0 CHECK (affected_row_count >= 0),
    detail TEXT NULL CHECK (detail IS NULL OR length(detail) <= 500),
    executed_at TIMESTAMPTZ NOT NULL,

    -- Only a completed run may claim to have touched anything.
    CONSTRAINT retention_purge_run_affects_only_when_completed CHECK (
        outcome = 'COMPLETED' OR affected_row_count = 0
    )
);

CREATE INDEX retention_purge_runs_class_idx ON retention_purge_runs (class_name, executed_at DESC);

CREATE TRIGGER retention_purge_runs_append_only
    BEFORE UPDATE OR DELETE ON retention_purge_runs
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
