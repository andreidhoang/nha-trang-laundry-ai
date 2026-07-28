-- Forward-fix lifecycle guards after 0007 created operational projections.

DROP TRIGGER webhook_events_append_only ON webhook_events;

CREATE FUNCTION protect_webhook_event() RETURNS trigger
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
       OR NEW.encrypted_payload IS DISTINCT FROM OLD.encrypted_payload
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

CREATE TRIGGER webhook_events_protected
    BEFORE UPDATE OR DELETE ON webhook_events
    FOR EACH ROW EXECUTE FUNCTION protect_webhook_event();

CREATE FUNCTION enforce_approval_state_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.approval_request_id IS DISTINCT FROM OLD.approval_request_id
       OR NEW.row_version <> OLD.row_version + 1
       OR NEW.updated_at < OLD.updated_at
       OR NOT (
           (OLD.status = 'REQUESTED' AND NEW.status IN ('APPROVED', 'REJECTED', 'EXPIRED', 'CANCELLED'))
           OR (OLD.status = 'APPROVED' AND NEW.status IN ('EXECUTING', 'EXPIRED', 'CANCELLED'))
           OR (OLD.status = 'EXECUTING' AND NEW.status IN ('EXECUTED', 'FAILED'))
           OR (OLD.status = 'FAILED' AND NEW.status IN ('MANUAL_REVIEW', 'DLQ'))
       ) THEN
        RAISE EXCEPTION 'invalid approval state transition';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER approval_state_transition_guard
    BEFORE UPDATE ON approval_request_states
    FOR EACH ROW EXECUTE FUNCTION enforce_approval_state_transition();

CREATE FUNCTION enforce_suppression_projection_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.contact_binding_id IS DISTINCT FROM OLD.contact_binding_id
       OR NEW.purpose IS DISTINCT FROM OLD.purpose
       OR NEW.channel IS DISTINCT FROM OLD.channel
       OR NEW.row_version <> OLD.row_version + 1
       OR NEW.updated_at < OLD.updated_at
       OR (OLD.state = 'SUPPRESSED' AND NEW.state <> 'SUPPRESSED') THEN
        RAISE EXCEPTION 'invalid suppression projection update';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER suppression_projection_guard
    BEFORE UPDATE ON suppression_entries
    FOR EACH ROW EXECUTE FUNCTION enforce_suppression_projection_update();

CREATE FUNCTION enforce_order_projection_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.bound_contact_id IS DISTINCT FROM OLD.bound_contact_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.row_version <> OLD.row_version + 1
       OR OLD.commercial_status IN ('CANCELLED', 'COMPLETED')
       OR (OLD.commercial_status = 'ACTIVE' AND NEW.commercial_status = 'CANCELLED') THEN
        RAISE EXCEPTION 'invalid order projection update';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER order_projection_guard
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION enforce_order_projection_update();

CREATE FUNCTION enforce_outbox_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.aggregate_type IS DISTINCT FROM OLD.aggregate_type
       OR NEW.aggregate_id IS DISTINCT FROM OLD.aggregate_id
       OR NEW.event_type IS DISTINCT FROM OLD.event_type
       OR NEW.payload IS DISTINCT FROM OLD.payload
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.correlation_id IS DISTINCT FROM OLD.correlation_id
       OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
       OR NEW.approved_action_id IS DISTINCT FROM OLD.approved_action_id
       OR NEW.recipient_binding_id IS DISTINCT FROM OLD.recipient_binding_id
       OR NEW.purpose IS DISTINCT FROM OLD.purpose
       OR NOT (
           (OLD.status = 'PENDING' AND NEW.status IN ('PROCESSING', 'HELD', 'CANCELLED'))
           OR (OLD.status = 'PROCESSING' AND NEW.status IN ('PENDING', 'HELD', 'SENT', 'DEAD'))
           OR (OLD.status = 'HELD' AND NEW.status IN ('PENDING', 'CANCELLED'))
       ) THEN
        RAISE EXCEPTION 'invalid outbox state transition';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER outbox_status_transition_guard
    BEFORE UPDATE OR DELETE ON outbox_events
    FOR EACH ROW EXECUTE FUNCTION enforce_outbox_status_transition();
