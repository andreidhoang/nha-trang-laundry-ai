-- Bind queue processing state to a valid lease and fail closed on unreconciled legacy claims.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM outbox_events
        WHERE status = 'PROCESSING'
          AND (claim_token IS NULL OR claimed_at IS NULL OR lease_expires_at IS NULL)
    ) THEN
        RAISE EXCEPTION
            'legacy outbox PROCESSING rows require explicit unknown-outcome reconciliation';
    END IF;
END;
$$;

ALTER TABLE outbox_events
    DROP CONSTRAINT outbox_claim_lease_shape,
    ADD CONSTRAINT outbox_claim_lease_shape CHECK (
        (
            status = 'PROCESSING'
            AND claim_token IS NOT NULL
            AND claimed_at IS NOT NULL
            AND lease_expires_at IS NOT NULL
            AND lease_expires_at > claimed_at
        )
        OR
        (
            status <> 'PROCESSING'
            AND claim_token IS NULL
            AND claimed_at IS NULL
            AND lease_expires_at IS NULL
        )
    );

ALTER TABLE agent_runs
    ADD CONSTRAINT agent_run_claim_lease_order CHECK (
        status <> 'PROCESSING' OR lease_expires_at > claimed_at
    );
