-- Customer incidents are intake records, separate from fault/remedy authority.

CREATE TABLE customer_incidents (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL,
    order_id UUID NULL REFERENCES orders(id),
    affected_message_id UUID NULL,
    affected_policy_version TEXT NULL,
    contact_scope_hash TEXT NOT NULL CHECK (contact_scope_hash ~ '^sha256:[0-9a-f]{64}$'),
    category TEXT NOT NULL CHECK (category IN ('SERVICE_QUALITY', 'AUTOMATED_MESSAGE_ERROR')),
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'UNDER_REVIEW', 'CLOSED')),
    fault_decided BOOLEAN NOT NULL DEFAULT FALSE,
    remedy_decided BOOLEAN NOT NULL DEFAULT FALSE,
    evidence_summary_hash TEXT NOT NULL CHECK (evidence_summary_hash ~ '^sha256:[0-9a-f]{64}$'),
    opened_at TIMESTAMPTZ NOT NULL,
    CHECK (order_id IS NOT NULL OR affected_message_id IS NOT NULL)
);

CREATE TABLE customer_correction_drafts (
    id UUID PRIMARY KEY,
    incident_id UUID NOT NULL UNIQUE REFERENCES customer_incidents(id),
    affected_message_id UUID NOT NULL,
    affected_policy_version TEXT NOT NULL,
    rendered_hash TEXT NOT NULL CHECK (rendered_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    approval_required BOOLEAN NOT NULL DEFAULT TRUE CHECK (approval_required),
    status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL' CHECK (status IN ('PENDING_APPROVAL', 'APPROVED', 'VOID')),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TRIGGER customer_incidents_no_hard_delete
    BEFORE DELETE ON customer_incidents
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

CREATE TRIGGER customer_correction_drafts_no_hard_delete
    BEFORE DELETE ON customer_correction_drafts
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();
