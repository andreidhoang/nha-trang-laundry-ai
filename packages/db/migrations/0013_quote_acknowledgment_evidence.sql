-- Immutable evidence that an estimate, not a final quote, was acknowledged by the customer.

ALTER TABLE quote_revisions
    ADD COLUMN customer_estimate_acknowledged_at TIMESTAMPTZ NULL,
    ADD CONSTRAINT quote_estimate_acknowledgment_evidence
        CHECK (
            (status = 'ACKNOWLEDGED_ESTIMATE')
            = (customer_estimate_acknowledged_at IS NOT NULL)
        ),
    ADD CONSTRAINT quote_estimate_acknowledgment_time
        CHECK (
            customer_estimate_acknowledged_at IS NULL
            OR customer_estimate_acknowledged_at >= priced_at
        );
