-- SETTLEMENT-001: the evidence the COMPLETED guard has always asked for and never received.
--
-- `orders.balance_status` was hardcoded to 'UNPAID' at insert and never updated; the two fulfilment
-- flags took their DEFAULT FALSE and no statement touched them. So the guard on COMPLETED — released
-- production, one fulfilment fact, a settled balance — could not be satisfied by any code path, and
-- no order this system creates could ever finish.
--
-- This table is that evidence, and it is deliberately narrow. One row per order, recording that a
-- named staff member witnessed the customer pay the quoted total in full and take their goods away.
-- The shape and collector columns are single-valued CHECKs rather than free enums: adding a second
-- settlement shape should require a migration and a decision, not a new string.
--
-- What is *not* here is as deliberate. No partial payment, no deposit, no instalment, no change
-- given, no payment method, no B2B account. Every one of those is DEC-010, open and owned by the
-- business owner. A column would invite a value, and a value would be a policy nobody chose.
--
-- Append-only, like every other attestation in this schema. A settlement recorded in error is an
-- incident, not an edit.

CREATE TABLE order_settlements (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL UNIQUE REFERENCES orders(id),
    store_id UUID NOT NULL,

    -- The quote the amount was checked against, pinned by hash. Re-pricing later must not be able
    -- to change what the customer was asked for at the counter.
    settled_quote_id UUID NOT NULL,
    settled_quote_revision INTEGER NOT NULL CHECK (settled_quote_revision > 0),
    settled_quote_snapshot_hash TEXT NOT NULL CHECK (
        settled_quote_snapshot_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'
    ),

    expected_total_vnd BIGINT NOT NULL CHECK (expected_total_vnd >= 0),
    paid_amount_vnd BIGINT NOT NULL CHECK (paid_amount_vnd >= 0),

    settlement_shape TEXT NOT NULL CHECK (
        settlement_shape = 'EXACT_PAYMENT_SELF_COLLECTION'
    ),
    collected_by TEXT NOT NULL CHECK (collected_by = 'CUSTOMER'),

    attested_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    attested_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,

    -- The database's own statement of the only shape it will hold. The domain decides this first;
    -- this is the second line of defence, for the day somebody writes a second code path.
    CHECK (paid_amount_vnd = expected_total_vnd)
);

CREATE INDEX order_settlements_store_idx ON order_settlements (store_id, attested_at);

CREATE TRIGGER order_settlements_append_only
    BEFORE UPDATE OR DELETE ON order_settlements
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
