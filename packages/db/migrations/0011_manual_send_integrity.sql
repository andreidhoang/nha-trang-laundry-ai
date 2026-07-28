-- Controlled pre-channel Shadow manual-send ledger and mutual exclusion with worker execution.

CREATE TABLE manual_send_envelopes (
    id UUID PRIMARY KEY,
    approval_request_id UUID NOT NULL UNIQUE REFERENCES approval_requests(id),
    resource_id UUID NOT NULL,
    resource_version BIGINT NOT NULL CHECK (resource_version > 0),
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    rendered_hash TEXT NOT NULL CHECK (rendered_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    recipient_binding_id UUID NOT NULL,
    channel TEXT NOT NULL CHECK (length(channel) BETWEEN 1 AND 50),
    purpose TEXT NOT NULL CHECK (purpose IN ('TRANSACTIONAL', 'MARKETING')),
    status TEXT NOT NULL CHECK (status IN ('APPROVED_FOR_MANUAL_SEND', 'MANUAL_SEND_RECORDED')),
    row_version BIGINT NOT NULL CHECK (row_version > 0),
    prepared_by UUID NOT NULL,
    prepared_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TRIGGER manual_send_envelopes_no_hard_delete
    BEFORE DELETE ON manual_send_envelopes
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

CREATE FUNCTION enforce_manual_send_envelope_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.approval_request_id IS DISTINCT FROM OLD.approval_request_id
       OR NEW.resource_id IS DISTINCT FROM OLD.resource_id
       OR NEW.resource_version IS DISTINCT FROM OLD.resource_version
       OR NEW.snapshot_hash IS DISTINCT FROM OLD.snapshot_hash
       OR NEW.rendered_hash IS DISTINCT FROM OLD.rendered_hash
       OR NEW.recipient_binding_id IS DISTINCT FROM OLD.recipient_binding_id
       OR NEW.channel IS DISTINCT FROM OLD.channel
       OR NEW.purpose IS DISTINCT FROM OLD.purpose
       OR NEW.prepared_by IS DISTINCT FROM OLD.prepared_by
       OR NEW.prepared_at IS DISTINCT FROM OLD.prepared_at
       OR NEW.status <> 'MANUAL_SEND_RECORDED'
       OR OLD.status <> 'APPROVED_FOR_MANUAL_SEND'
       OR NEW.row_version <> OLD.row_version + 1
       OR NEW.updated_at < OLD.updated_at THEN
        RAISE EXCEPTION 'invalid manual-send envelope transition';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER manual_send_envelopes_transition
    BEFORE UPDATE ON manual_send_envelopes
    FOR EACH ROW EXECUTE FUNCTION enforce_manual_send_envelope_transition();

CREATE TABLE manual_send_attestations (
    id UUID PRIMARY KEY,
    manual_send_envelope_id UUID NOT NULL UNIQUE REFERENCES manual_send_envelopes(id),
    approval_request_id UUID NOT NULL UNIQUE REFERENCES approval_requests(id),
    exact_rendered_hash TEXT NOT NULL CHECK (exact_rendered_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    resource_version BIGINT NOT NULL CHECK (resource_version > 0),
    actor_id UUID NOT NULL,
    channel TEXT NOT NULL CHECK (length(channel) BETWEEN 1 AND 50),
    recipient_binding_id UUID NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL
);

CREATE TRIGGER manual_send_attestations_append_only
    BEFORE UPDATE OR DELETE ON manual_send_attestations
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
