-- Forward-bind durable agent runs to only server-selected order/public references.

ALTER TABLE agent_runs
    ADD COLUMN order_request_id UUID NULL,
    ADD COLUMN public_code TEXT NULL CHECK (
        public_code IS NULL OR public_code ~ '^[A-Za-z0-9_-]{16,64}$'
    ),
    ADD COLUMN bound_row_version BIGINT NOT NULL DEFAULT 0 CHECK (bound_row_version >= 0);

CREATE OR REPLACE FUNCTION enforce_agent_run_transition() RETURNS trigger
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
       OR NEW.order_request_id IS DISTINCT FROM OLD.order_request_id
       OR NEW.public_code IS DISTINCT FROM OLD.public_code
       OR NEW.bound_row_version IS DISTINCT FROM OLD.bound_row_version
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
