-- Record that the shop's own courier took laundry out, and whether it arrived.
--
-- `DEC-023`, resolved 2026-08-26. Until now `orders.required_delivery_legs_succeeded` was read in
-- three places, defaulted FALSE and was written nowhere, so a delivery order could be taken,
-- priced, agreed and washed and then never closed. `transition_commercial` requires
-- `required_delivery_legs_succeeded OR self_collection_recorded` to complete an order, and for a
-- delivery order neither could ever become true.
--
-- **No money here, deliberately.** The owner decided the customer pays the exact total at the
-- counter before the laundry leaves (`SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY`). A leg
-- therefore attests one thing -- the laundry reached the customer, or did not -- and carries no
-- amount, no payment method and no reconciliation. Had the answer been "the driver collects cash",
-- this table would need all three, which is why it was not designed before the question was asked.
--
-- **Which leg completes an order.** `RETURN` is the one that matters: it is the leg on which the
-- customer receives their laundry. `PICKUP` records the shop collecting it and completes nothing.
-- So `PICKUP_AND_RETURN` and `RETURN_ONLY` complete on a succeeded `RETURN`; `PICKUP_ONLY` has no
-- return leg at all and is completed by self-collection at the counter, exactly as a walk-in is.
--
-- **A failed leg is not terminal.** Nobody was home, nobody answered. The attempt is recorded,
-- nothing is charged, the order stays ACTIVE and the laundry comes back to the shop. A retry is a
-- new row, which is why the unique constraint is on succeeded legs only -- an order may accumulate
-- any number of failed attempts and exactly one success per leg kind. Charging for a second attempt
-- would be this system deciding money; staff negotiate it as they do any fee over 6km.

CREATE TABLE delivery_legs (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL,
    order_id UUID NOT NULL REFERENCES orders (id),
    leg_kind TEXT NOT NULL CHECK (leg_kind IN ('PICKUP', 'RETURN')),
    outcome TEXT NOT NULL CHECK (outcome IN ('SUCCEEDED', 'FAILED')),
    -- Free text is deliberately absent. "Khách không có nhà" is an operational note about a real
    -- person, and no retention class covers it (DEC-019). The outcome is the record.
    recorded_by UUID NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    correlation_id UUID NOT NULL
);

CREATE UNIQUE INDEX delivery_legs_one_success_per_kind
    ON delivery_legs (order_id, leg_kind)
    WHERE outcome = 'SUCCEEDED';

CREATE INDEX delivery_legs_order_idx ON delivery_legs (order_id, recorded_at DESC, id);

CREATE INDEX delivery_legs_store_idx ON delivery_legs (store_id, recorded_at DESC, id);

CREATE TRIGGER delivery_legs_append_only
    BEFORE UPDATE OR DELETE ON delivery_legs
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- `DEC-023` also adds a second settlement shape, so the two columns that enumerated one must admit
-- it. `0025` wrote both as an equality on purpose -- "the one supported shape" -- and widening them
-- is the visible act `SettlementShape`'s own comment asks for.
--
-- `collected_by` records who took the laundry away at the moment the money was attested. For a
-- prepaid delivery that is nobody: the customer has paid and the laundry is still at the shop, to
-- be delivered later. `PENDING_DELIVERY` says exactly that, and the delivery leg above is what
-- eventually attests arrival. Writing `CUSTOMER` there would record a handover that did not happen.

ALTER TABLE order_settlements
    DROP CONSTRAINT order_settlements_settlement_shape_check,
    ADD CONSTRAINT order_settlements_settlement_shape_check CHECK (
        settlement_shape IN ('EXACT_PAYMENT_SELF_COLLECTION', 'EXACT_PAYMENT_PREPAID_DELIVERY')
    ),
    DROP CONSTRAINT order_settlements_collected_by_check,
    ADD CONSTRAINT order_settlements_collected_by_check CHECK (
        collected_by IN ('CUSTOMER', 'PENDING_DELIVERY')
    ),
    -- The pairing is the invariant worth keeping: a self-collection settlement means the customer
    -- took it, and a prepaid delivery means nobody has yet. Neither may be recorded as the other.
    ADD CONSTRAINT order_settlements_shape_matches_collection CHECK (
        (settlement_shape = 'EXACT_PAYMENT_SELF_COLLECTION' AND collected_by = 'CUSTOMER')
        OR (settlement_shape = 'EXACT_PAYMENT_PREPAID_DELIVERY'
            AND collected_by = 'PENDING_DELIVERY')
    );
