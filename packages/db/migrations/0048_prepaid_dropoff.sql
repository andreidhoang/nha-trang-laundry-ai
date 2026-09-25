-- PREPAID-DROPOFF-001: a walk-in customer pays the exact total at drop-off; pickup is its own record.
--
-- `DEC-032` (2026-09-25, delegated): a self-collect customer may pay the exact quoted total when
-- they drop the laundry off, as `DEC-023` already lets a delivery customer do. Collecting the goods
-- is recorded separately, at pickup, by the named staff member who hands them over, and an order
-- completes only when it is paid, released and collected.
--
-- Until now the only way to take that money was `collected_by_customer = true` -- ticking "the
-- customer took their goods" while the shirts were still in the machine. The settlement row said
-- `collected_by = 'CUSTOMER'` about a handover that had not happened, and `self_collection_recorded`
-- went TRUE at drop-off, so nothing on the record could say when the customer really left with it.
--
-- `DEC-010` is not reopened. This is the exact total in one payment, the shape it kept, at a
-- different moment; `paid_amount_vnd = expected_total_vnd` still holds for every row below.

-- 1. A third settlement shape, admitted by the columns that enumerate them.
--
-- `SettlementShape`'s own comment asks that adding a shape be a visible decision, and `0033` widened
-- these same three constraints for `DEC-023`. `PENDING_COLLECTION` is what `collected_by` truthfully
-- is at the moment the money is attested: paid, and nobody has taken anything yet.
ALTER TABLE order_settlements
    DROP CONSTRAINT order_settlements_settlement_shape_check,
    ADD CONSTRAINT order_settlements_settlement_shape_check CHECK (
        settlement_shape IN (
            'EXACT_PAYMENT_SELF_COLLECTION',
            'EXACT_PAYMENT_PREPAID_DELIVERY',
            'EXACT_PAYMENT_PREPAID_SELF_COLLECTION'
        )
    ),
    DROP CONSTRAINT order_settlements_collected_by_check,
    ADD CONSTRAINT order_settlements_collected_by_check CHECK (
        collected_by IN ('CUSTOMER', 'PENDING_DELIVERY', 'PENDING_COLLECTION')
    ),
    DROP CONSTRAINT order_settlements_shape_matches_collection,
    ADD CONSTRAINT order_settlements_shape_matches_collection CHECK (
        (settlement_shape = 'EXACT_PAYMENT_SELF_COLLECTION' AND collected_by = 'CUSTOMER')
        OR (settlement_shape = 'EXACT_PAYMENT_PREPAID_DELIVERY'
            AND collected_by = 'PENDING_DELIVERY')
        OR (settlement_shape = 'EXACT_PAYMENT_PREPAID_SELF_COLLECTION'
            AND collected_by = 'PENDING_COLLECTION')
    ),
    -- The key the collection below points at. `id` is already unique, so this admits nothing new;
    -- it exists so a foreign key can carry the shape and the store along with the id.
    ADD CONSTRAINT order_settlements_collection_key UNIQUE (id, order_id, store_id, settlement_shape);

-- 2. The pickup, as its own attestation.
--
-- One row per order, written by the staff member who handed the goods over, carrying no money.
-- The composite foreign key is the schema saying what the repository already checks: a collection
-- belongs to a prepaid self-collection settlement of the same order in the same store. A delivery
-- has its legs (`0033`); a customer who pays at pickup is collected by that same settlement. Neither
-- can acquire a row here, by this code or any later code.
CREATE TABLE order_collections (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL UNIQUE REFERENCES orders(id),
    store_id UUID NOT NULL,
    settlement_id UUID NOT NULL,
    settlement_shape TEXT NOT NULL CHECK (
        settlement_shape = 'EXACT_PAYMENT_PREPAID_SELF_COLLECTION'
    ),
    -- `DEC-032`'s "the named staff member who hands them over". From the session, never a request.
    collected_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    collected_at TIMESTAMPTZ NOT NULL,
    correlation_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (settlement_id, order_id, store_id, settlement_shape)
        REFERENCES order_settlements (id, order_id, store_id, settlement_shape)
);

COMMENT ON TABLE order_collections IS
    'What question does this answer: "a customer paid at drop-off -- when did they take the goods, '
    'and who handed them over?". One append-only row per prepaid self-collection order (DEC-032), '
    'written at pickup by the named staff member. Carries no money: the money is the settlement.';

CREATE INDEX order_collections_store_idx ON order_collections (store_id, collected_at);

-- Append-only like every attestation in this schema. A pickup recorded in error is an incident.
CREATE TRIGGER order_collections_append_only
    BEFORE UPDATE OR DELETE ON order_collections
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- 3. The collection and the order's flag agree, enforced from both sides -- the shape `0046` gave
-- refunds, and for the same reason: `self_collection_recorded` is what `transition_commercial`
-- reads to allow COMPLETED, so a flag with no record behind it is a handover nobody attested.
--
-- From the order: a prepaid self-collection order cannot read as collected without its collection
-- row. The repository writes the row first and moves the flag second in one transaction, so the row
-- is already visible here. A pay-at-pickup settlement is untouched: its shape is not this one.
CREATE FUNCTION enforce_prepaid_collection_is_recorded() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.self_collection_recorded AND NOT OLD.self_collection_recorded
       AND EXISTS (
           SELECT 1 FROM order_settlements s
           WHERE s.order_id = NEW.id
             AND s.settlement_shape = 'EXACT_PAYMENT_PREPAID_SELF_COLLECTION'
       )
       AND NOT EXISTS (SELECT 1 FROM order_collections c WHERE c.order_id = NEW.id) THEN
        RAISE EXCEPTION 'a prepaid order cannot read as collected without a collection record';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER order_prepaid_collection_consistency
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION enforce_prepaid_collection_is_recorded();

-- From the collection: checked at commit, because the row is written before the order moves.
CREATE FUNCTION enforce_collection_marks_order() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM orders o
        WHERE o.id = NEW.order_id AND o.self_collection_recorded AND o.balance_status = 'PAID'
    ) THEN
        RAISE EXCEPTION 'a collection must commit with its paid order marked collected';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER order_collections_mark_their_order
    AFTER INSERT ON order_collections
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_collection_marks_order();
