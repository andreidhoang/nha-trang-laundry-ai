-- Durable, draft-only Agent Runner ledger. No hidden reasoning or direct-send state is stored.

CREATE TABLE agent_runs (
    id UUID PRIMARY KEY,
    source_webhook_event_id UUID NULL REFERENCES webhook_events(id),
    organization_id UUID NOT NULL,
    store_id UUID NOT NULL,
    channel TEXT NOT NULL CHECK (length(channel) BETWEEN 1 AND 50),
    conversation_binding_id UUID NOT NULL,
    contact_binding_id UUID NOT NULL,
    capability TEXT NOT NULL CHECK (capability IN (
        'INTERNAL_SHADOW', 'PUBLIC_FAQ', 'LIST_PRICE_INFO', 'INTAKE_QUESTION',
        'INTAKE_FACT_CAPTURE', 'INTAKE_RECEIPT', 'INCIDENT_RECEIPT', 'ORDER_STATUS',
        'SLA_GUIDANCE', 'QUOTE_ESTIMATE', 'BOOKING', 'DELIVERY_ADVISORY',
        'MARKETING_FOLLOWUP'
    )),
    deployment_stage TEXT NOT NULL CHECK (deployment_stage IN (
        'MANUAL_TRUTH', 'SHADOW', 'ASSISTED', 'BOUNDED'
    )),
    data_classification TEXT NOT NULL CHECK (data_classification IN ('SYNTHETIC', 'REAL_CUSTOMER')),
    runtime_registry_version TEXT NOT NULL CHECK (length(runtime_registry_version) BETWEEN 1 AND 120),
    runtime_registry_hash TEXT NOT NULL CHECK (runtime_registry_hash ~ '^sha256:[0-9a-f]{64}$'),
    prompt_bundle_version TEXT NOT NULL CHECK (length(prompt_bundle_version) BETWEEN 1 AND 120),
    prompt_bundle_hash TEXT NOT NULL CHECK (prompt_bundle_hash ~ '^sha256:[0-9a-f]{64}$'),
    tool_contract_hash TEXT NOT NULL CHECK (tool_contract_hash ~ '^sha256:[0-9a-f]{64}$'),
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'PROCESSING', 'DRAFT_REQUIRES_HUMAN', 'FAILED'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    claim_token UUID NULL,
    claimed_at TIMESTAMPTZ NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    result_safe_summary JSONB NULL CHECK (
        result_safe_summary IS NULL OR jsonb_typeof(result_safe_summary) = 'object'
    ),
    automatic_send_authorized BOOLEAN NOT NULL DEFAULT FALSE CHECK (automatic_send_authorized = FALSE),
    failure_code TEXT NULL CHECK (failure_code IS NULL OR length(failure_code) BETWEEN 1 AND 100),
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NULL,
    UNIQUE (source_webhook_event_id),
    CHECK (
        (status = 'PROCESSING') = (
            claim_token IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL
            AND completed_at IS NULL AND failure_code IS NULL
        )
    ),
    CHECK (
        status <> 'DRAFT_REQUIRES_HUMAN' OR (
            result_safe_summary IS NOT NULL AND completed_at IS NOT NULL AND failure_code IS NULL
        )
    ),
    CHECK (
        status <> 'FAILED' OR (failure_code IS NOT NULL AND completed_at IS NOT NULL)
    )
);

CREATE INDEX agent_runs_claim_idx
    ON agent_runs (created_at, id)
    WHERE status = 'PENDING';

CREATE TABLE agent_tool_calls (
    id UUID PRIMARY KEY,
    agent_run_id UUID NOT NULL REFERENCES agent_runs(id),
    sequence_number INTEGER NOT NULL CHECK (sequence_number BETWEEN 1 AND 6),
    operation_id TEXT NOT NULL CHECK (operation_id IN (
        'catalogResolve', 'orderRequestCreate', 'orderRequestRecordCustomerFacts',
        'quoteEstimate', 'deliveryEvaluate', 'capacityCheck', 'messageDraftCreate',
        'publicOrderStatusGet', 'incidentOpen', 'approvalRequestCreate'
    )),
    request_fingerprint TEXT NOT NULL CHECK (request_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    result_status_code INTEGER NOT NULL CHECK (result_status_code BETWEEN 100 AND 599),
    result_code TEXT NOT NULL CHECK (length(result_code) BETWEEN 1 AND 100),
    trace_id TEXT NULL CHECK (trace_id IS NULL OR length(trace_id) BETWEEN 8 AND 80),
    safe_summary JSONB NOT NULL CHECK (jsonb_typeof(safe_summary) = 'object'),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL CHECK (completed_at >= started_at),
    UNIQUE (agent_run_id, sequence_number)
);

CREATE TRIGGER agent_tool_calls_append_only
    BEFORE UPDATE OR DELETE ON agent_tool_calls
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE FUNCTION enforce_agent_run_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'agent runs cannot be hard deleted';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.source_webhook_event_id IS DISTINCT FROM OLD.source_webhook_event_id
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.channel IS DISTINCT FROM OLD.channel
       OR NEW.conversation_binding_id IS DISTINCT FROM OLD.conversation_binding_id
       OR NEW.contact_binding_id IS DISTINCT FROM OLD.contact_binding_id
       OR NEW.capability IS DISTINCT FROM OLD.capability
       OR NEW.deployment_stage IS DISTINCT FROM OLD.deployment_stage
       OR NEW.data_classification IS DISTINCT FROM OLD.data_classification
       OR NEW.runtime_registry_version IS DISTINCT FROM OLD.runtime_registry_version
       OR NEW.runtime_registry_hash IS DISTINCT FROM OLD.runtime_registry_hash
       OR NEW.prompt_bundle_version IS DISTINCT FROM OLD.prompt_bundle_version
       OR NEW.prompt_bundle_hash IS DISTINCT FROM OLD.prompt_bundle_hash
       OR NEW.tool_contract_hash IS DISTINCT FROM OLD.tool_contract_hash
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.automatic_send_authorized IS DISTINCT FROM FALSE
       OR NOT (
           (OLD.status = 'PENDING' AND NEW.status = 'PROCESSING' AND NEW.attempt_count = OLD.attempt_count + 1)
           OR (OLD.status = 'PROCESSING' AND NEW.status IN ('DRAFT_REQUIRES_HUMAN', 'FAILED')
               AND NEW.attempt_count = OLD.attempt_count)
       ) THEN
        RAISE EXCEPTION 'invalid agent run transition';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER agent_run_transition_guard
    BEFORE UPDATE OR DELETE ON agent_runs
    FOR EACH ROW EXECUTE FUNCTION enforce_agent_run_transition();
