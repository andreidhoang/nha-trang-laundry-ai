-- Operational command, order, approval, inbox, suppression, and delivery-ledger slice.

CREATE TABLE command_idempotency_records (
    scope TEXT NOT NULL CHECK (length(scope) BETWEEN 1 AND 300),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 300),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    response JSONB NULL CHECK (response IS NULL OR jsonb_typeof(response) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NULL,
    PRIMARY KEY (scope, idempotency_key),
    CHECK ((response IS NULL) = (completed_at IS NULL))
);

CREATE FUNCTION protect_idempotency_record() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' OR OLD.response IS NOT NULL
       OR NEW.scope IS DISTINCT FROM OLD.scope
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.response IS NULL OR NEW.completed_at IS NULL THEN
        RAISE EXCEPTION 'completed idempotency records are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER command_idempotency_protected
    BEFORE UPDATE OR DELETE ON command_idempotency_records
    FOR EACH ROW EXECUTE FUNCTION protect_idempotency_record();

CREATE TABLE webhook_events (
    id UUID PRIMARY KEY,
    provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 100),
    channel_account_id TEXT NOT NULL CHECK (length(channel_account_id) BETWEEN 1 AND 255),
    provider_event_id TEXT NOT NULL CHECK (length(provider_event_id) BETWEEN 1 AND 255),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^RAW-SHA256-V1:[0-9a-f]{64}$'),
    encrypted_payload BYTEA NOT NULL CHECK (octet_length(encrypted_payload) > 0),
    event_type TEXT NOT NULL CHECK (length(event_type) BETWEEN 1 AND 100),
    contact_binding_id UUID NULL,
    channel TEXT NOT NULL CHECK (length(channel) BETWEEN 1 AND 50),
    opt_out_disposition TEXT NOT NULL CHECK (opt_out_disposition IN (
        'NONE', 'WITHDRAW', 'PENDING_REVIEW_BLOCKED', 'POLICY_UNAVAILABLE_BLOCKED'
    )),
    opt_out_registry_version TEXT NULL,
    processing_status TEXT NOT NULL CHECK (processing_status IN (
        'DISPATCH_PENDING', 'SAFETY_BLOCKED', 'PROCESSED'
    )),
    received_at TIMESTAMPTZ NOT NULL,
    UNIQUE (provider, channel_account_id, provider_event_id)
);

CREATE INDEX webhook_events_dispatch_idx
    ON webhook_events (processing_status, received_at)
    WHERE processing_status = 'DISPATCH_PENDING';

CREATE TRIGGER webhook_events_append_only
    BEFORE UPDATE OR DELETE ON webhook_events
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TABLE inbox_replay_conflicts (
    id UUID PRIMARY KEY,
    webhook_event_id UUID NOT NULL REFERENCES webhook_events(id),
    expected_payload_hash TEXT NOT NULL CHECK (
        expected_payload_hash ~ '^RAW-SHA256-V1:[0-9a-f]{64}$'
    ),
    observed_payload_hash TEXT NOT NULL CHECK (
        observed_payload_hash ~ '^RAW-SHA256-V1:[0-9a-f]{64}$'
    ),
    detected_at TIMESTAMPTZ NOT NULL
);

CREATE TRIGGER inbox_replay_conflicts_append_only
    BEFORE UPDATE OR DELETE ON inbox_replay_conflicts
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TABLE consent_events (
    id UUID PRIMARY KEY,
    contact_binding_id UUID NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('MARKETING')),
    channel TEXT NOT NULL CHECK (length(channel) BETWEEN 1 AND 50),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'WITHDRAW', 'PENDING_REVIEW_BLOCK', 'POLICY_UNAVAILABLE_BLOCK'
    )),
    registry_version TEXT NULL,
    evidence_webhook_id UUID NOT NULL REFERENCES webhook_events(id),
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE TRIGGER consent_events_append_only
    BEFORE UPDATE OR DELETE ON consent_events
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TABLE suppression_entries (
    contact_binding_id UUID NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('MARKETING')),
    channel TEXT NOT NULL CHECK (length(channel) BETWEEN 1 AND 50),
    state TEXT NOT NULL CHECK (state IN (
        'SUPPRESSED', 'PENDING_REVIEW_BLOCKED', 'UNKNOWN_BLOCKED'
    )),
    source_consent_event_id UUID NOT NULL REFERENCES consent_events(id),
    row_version BIGINT NOT NULL CHECK (row_version > 0),
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (contact_binding_id, purpose, channel)
);

CREATE FUNCTION reject_operational_hard_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'operational records cannot be hard deleted';
END;
$$;

CREATE TRIGGER suppression_entries_no_hard_delete
    BEFORE DELETE ON suppression_entries
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

CREATE TABLE approval_requests (
    id UUID PRIMARY KEY,
    action TEXT NOT NULL CHECK (action IN (
        'PRESENT_QUOTE', 'CONFIRM_SLOT', 'SET_RANGE_PRICE', 'SET_DELIVERY_FEE',
        'APPLY_PROMOTION', 'SEND_MESSAGE', 'ACCEPT_ORDER', 'CANCEL_ACTIVE_ORDER',
        'APPROVE_REMEDY', 'APPROVE_B2B_TERMS', 'PUBLISH_POLICY', 'EXPORT_SANITIZED_DATA'
    )),
    resource_type TEXT NOT NULL CHECK (length(resource_type) BETWEEN 2 AND 128),
    resource_id UUID NOT NULL,
    resource_version BIGINT NOT NULL CHECK (resource_version > 0),
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    rendered_hash TEXT NOT NULL CHECK (rendered_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    policy_version TEXT NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 200),
    required_role TEXT NOT NULL CHECK (required_role IN (
        'OWNER_ADMIN', 'OPS_APPROVER', 'OPERATOR', 'ACCOUNTANT'
    )),
    reason_codes JSONB NOT NULL CHECK (jsonb_typeof(reason_codes) = 'array'),
    obligations JSONB NOT NULL CHECK (jsonb_typeof(obligations) = 'array'),
    execution_capability TEXT NOT NULL CHECK (length(execution_capability) BETWEEN 1 AND 128),
    requested_by UUID NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (expires_at > requested_at),
    envelope JSONB NOT NULL CHECK (jsonb_typeof(envelope) = 'object'),
    envelope_hash TEXT NOT NULL UNIQUE CHECK (
        envelope_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'
    )
);

CREATE TRIGGER approval_requests_append_only
    BEFORE UPDATE OR DELETE ON approval_requests
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TABLE approval_request_states (
    approval_request_id UUID PRIMARY KEY REFERENCES approval_requests(id),
    status TEXT NOT NULL CHECK (status IN (
        'REQUESTED', 'APPROVED', 'EXECUTING', 'EXECUTED', 'FAILED', 'MANUAL_REVIEW',
        'DLQ', 'REJECTED', 'EXPIRED', 'CANCELLED'
    )),
    row_version BIGINT NOT NULL CHECK (row_version > 0),
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TRIGGER approval_request_states_no_hard_delete
    BEFORE DELETE ON approval_request_states
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

CREATE TABLE approval_decisions (
    id UUID PRIMARY KEY,
    approval_request_id UUID NOT NULL UNIQUE REFERENCES approval_requests(id),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED')),
    decided_by UUID NOT NULL,
    observed_resource_version BIGINT NOT NULL CHECK (observed_resource_version > 0),
    observed_snapshot_hash TEXT NOT NULL CHECK (
        observed_snapshot_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'
    ),
    observed_rendered_hash TEXT NOT NULL CHECK (
        observed_rendered_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'
    ),
    decided_at TIMESTAMPTZ NOT NULL
);

CREATE TRIGGER approval_decisions_append_only
    BEFORE UPDATE OR DELETE ON approval_decisions
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TABLE approval_executions (
    id UUID PRIMARY KEY,
    approval_request_id UUID NOT NULL UNIQUE REFERENCES approval_requests(id),
    claimed_by TEXT NOT NULL CHECK (claimed_by = 'OUTBOX_WORKER'),
    observed_resource_version BIGINT NOT NULL CHECK (observed_resource_version > 0),
    observed_snapshot_hash TEXT NOT NULL CHECK (
        observed_snapshot_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'
    ),
    observed_rendered_hash TEXT NOT NULL CHECK (
        observed_rendered_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'
    ),
    claimed_at TIMESTAMPTZ NOT NULL
);

CREATE TRIGGER approval_executions_append_only
    BEFORE UPDATE OR DELETE ON approval_executions
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TABLE orders (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL,
    bound_contact_id UUID NOT NULL,
    current_quote_id UUID NOT NULL,
    current_quote_revision INTEGER NOT NULL CHECK (current_quote_revision > 0),
    current_quote_snapshot_hash TEXT NOT NULL CHECK (
        current_quote_snapshot_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'
    ),
    commercial_status TEXT NOT NULL CHECK (commercial_status IN (
        'DRAFT', 'REQUESTED', 'STORE_CONFIRMATION_PENDING', 'CONFIRMED', 'ACTIVE',
        'CANCELLATION_REVIEW', 'CANCELLED', 'COMPLETED'
    )),
    intake_status TEXT NOT NULL CHECK (intake_status IN (
        'AWAITING_HANDOFF', 'RECEIVED_PENDING_INSPECTION', 'WAITING_PRICE_APPROVAL',
        'WAITING_CUSTOMER_RECONFIRMATION', 'WAITING_SLOT_APPROVAL', 'ACCEPTED', 'REJECTED'
    )),
    production_status TEXT NOT NULL CHECK (production_status IN (
        'NOT_STARTED', 'QUEUED', 'IN_PROCESS', 'QUALITY_CHECK', 'READY_AT_STORE',
        'RELEASED', 'ON_HOLD', 'EXCEPTION'
    )),
    production_resume_status TEXT NULL CHECK (production_resume_status IS NULL OR production_resume_status IN (
        'QUEUED', 'IN_PROCESS', 'QUALITY_CHECK', 'READY_AT_STORE'
    )),
    fulfillment_mode TEXT NOT NULL CHECK (fulfillment_mode IN (
        'SELF_DROP_SELF_COLLECT', 'PICKUP_AND_RETURN', 'PICKUP_ONLY', 'RETURN_ONLY'
    )),
    balance_status TEXT NOT NULL CHECK (balance_status IN (
        'UNPAID', 'PARTIALLY_PAID', 'PAID', 'OVERPAID', 'ON_ACCOUNT'
    )),
    required_delivery_legs_succeeded BOOLEAN NOT NULL DEFAULT FALSE,
    self_collection_recorded BOOLEAN NOT NULL DEFAULT FALSE,
    incident_open BOOLEAN NOT NULL DEFAULT FALSE,
    customer_final_quote_accepted_at TIMESTAMPTZ NOT NULL,
    production_accepted_at TIMESTAMPTZ NULL,
    closed_at TIMESTAMPTZ NULL,
    row_version BIGINT NOT NULL CHECK (row_version > 0),
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (current_quote_id, current_quote_revision)
        REFERENCES quote_revisions(quote_id, revision),
    CHECK ((intake_status = 'ACCEPTED') = (production_accepted_at IS NOT NULL)),
    CHECK (production_status = 'NOT_STARTED' OR intake_status = 'ACCEPTED'),
    CHECK (commercial_status NOT IN ('ACTIVE', 'COMPLETED') OR intake_status = 'ACCEPTED'),
    CHECK (commercial_status <> 'COMPLETED' OR (
        production_status = 'RELEASED'
        AND (required_delivery_legs_succeeded OR self_collection_recorded)
        AND balance_status IN ('PAID', 'ON_ACCOUNT')
        AND closed_at IS NOT NULL
    ))
);

CREATE INDEX orders_store_commercial_idx ON orders (store_id, commercial_status, created_at);

CREATE TRIGGER orders_no_hard_delete
    BEFORE DELETE ON orders
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

ALTER TABLE outbox_events DROP CONSTRAINT outbox_events_status_check;
ALTER TABLE outbox_events
    ADD CONSTRAINT outbox_events_status_check CHECK (
        status IN ('PENDING', 'PROCESSING', 'HELD', 'SENT', 'DEAD', 'CANCELLED')
    ),
    ADD COLUMN approved_action_id UUID NULL REFERENCES approval_requests(id),
    ADD COLUMN recipient_binding_id UUID NULL,
    ADD COLUMN purpose TEXT NULL CHECK (purpose IS NULL OR purpose IN ('TRANSACTIONAL', 'MARKETING')),
    ADD COLUMN held_reason TEXT NULL;

CREATE TABLE delivery_attempts (
    id UUID PRIMARY KEY,
    outbox_event_id UUID NOT NULL REFERENCES outbox_events(id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    result_class TEXT NOT NULL CHECK (result_class IN (
        'PROVIDER_ACCEPTED', 'TRANSIENT_FAILURE', 'PERMANENT_FAILURE', 'POLICY_REJECTED'
    )),
    provider_message_id TEXT NULL,
    attempted_at TIMESTAMPTZ NOT NULL,
    UNIQUE (outbox_event_id, attempt_number)
);

CREATE TRIGGER delivery_attempts_append_only
    BEFORE UPDATE OR DELETE ON delivery_attempts
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TABLE dead_letter_events (
    id UUID PRIMARY KEY,
    outbox_event_id UUID NOT NULL UNIQUE REFERENCES outbox_events(id),
    normalized_payload_hash TEXT NOT NULL CHECK (
        normalized_payload_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'
    ),
    last_error_class TEXT NOT NULL CHECK (length(last_error_class) BETWEEN 1 AND 100),
    attempts INTEGER NOT NULL CHECK (attempts > 0),
    first_attempted_at TIMESTAMPTZ NOT NULL,
    last_attempted_at TIMESTAMPTZ NOT NULL,
    replay_eligible BOOLEAN NOT NULL,
    operator_decision TEXT NULL,
    CHECK (last_attempted_at >= first_attempted_at)
);

CREATE TRIGGER dead_letter_events_append_only
    BEFORE UPDATE OR DELETE ON dead_letter_events
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
