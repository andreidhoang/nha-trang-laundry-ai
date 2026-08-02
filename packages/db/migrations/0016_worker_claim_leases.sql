-- Fence internal worker claims and make crash recovery explicit and fail-closed.

ALTER TABLE outbox_events
    ADD COLUMN claim_token UUID NULL,
    ADD COLUMN claimed_at TIMESTAMPTZ NULL,
    ADD COLUMN lease_expires_at TIMESTAMPTZ NULL,
    ADD CONSTRAINT outbox_claim_lease_shape CHECK (
        (claim_token IS NULL AND claimed_at IS NULL AND lease_expires_at IS NULL)
        OR
        (claim_token IS NOT NULL AND claimed_at IS NOT NULL
         AND lease_expires_at IS NOT NULL AND lease_expires_at > claimed_at)
    );

CREATE INDEX outbox_processing_lease_idx
    ON outbox_events (lease_expires_at, id)
    WHERE status = 'PROCESSING';

DROP TRIGGER outbox_status_transition_guard ON outbox_events;

CREATE OR REPLACE FUNCTION enforce_outbox_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE'
       OR NEW.id IS DISTINCT FROM OLD.id
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
           (OLD.status = 'PENDING' AND NEW.status = 'PROCESSING'
            AND NEW.attempt_count = OLD.attempt_count + 1
            AND NEW.claim_token IS NOT NULL
            AND NEW.claimed_at IS NOT NULL
            AND NEW.lease_expires_at > NEW.claimed_at)
           OR
           (OLD.status = 'PENDING' AND NEW.status IN ('HELD', 'CANCELLED')
            AND NEW.attempt_count = OLD.attempt_count
            AND NEW.claim_token IS NULL)
           OR
           (OLD.status = 'PROCESSING' AND NEW.status IN ('PENDING', 'HELD', 'SENT', 'DEAD')
            AND NEW.attempt_count = OLD.attempt_count
            AND NEW.claim_token IS NULL
            AND NEW.claimed_at IS NULL
            AND NEW.lease_expires_at IS NULL)
           OR
           (OLD.status = 'HELD' AND NEW.status IN ('PENDING', 'CANCELLED')
            AND NEW.attempt_count = OLD.attempt_count
            AND NEW.claim_token IS NULL)
       ) THEN
        RAISE EXCEPTION 'invalid outbox state transition';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER outbox_status_transition_guard
    BEFORE UPDATE OR DELETE ON outbox_events
    FOR EACH ROW EXECUTE FUNCTION enforce_outbox_status_transition();
