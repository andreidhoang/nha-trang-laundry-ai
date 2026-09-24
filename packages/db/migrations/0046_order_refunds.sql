-- DEC-024: a paid order cancelled under a resolution that charges the customer nothing records the
-- money going back, and the order stops reading as paid.
--
-- Found independently by two reviewers. `OrderRepository.transition` wrote the custody resolution
-- only into the event payload. `orders.balance_status` stayed 'PAID', the `order_settlements` row
-- stayed (correctly -- it is append-only and the payment did happen), and `collected_today` summed
-- every settlement. So a prepaid delivery order (DEC-023) paid at the counter, never washed and
-- cancelled as RETURNED_UNWASHED_REFUNDED with the cash handed back left the day's "tiền đã thu"
-- (DEC-014) above the drawer by exactly the refund, and nothing anywhere said money had moved.
--
-- The repair is a second append-only ledger beside the first, not an edit of it. A settlement is a
-- fact about money coming in; a refund is a fact about money going out. Both happened, on their own
-- business days, and a reader of either table must be able to see both.
--
-- Money stays a non-negative integer (invariant 2). The amount is stored positive and `direction`
-- says which way it moved -- a negative number would be one sign error away from doubling a
-- day's takings instead of cancelling them.
--
-- Nothing here adds a resolution, a partial refund or a refund of anything but the whole settled
-- amount. DEC-010 keeps partial payment NOT_SUPPORTED, and a partial refund is the same question in
-- the other direction.

-- 1. The balance an order reads once its money has gone back.
ALTER TABLE orders DROP CONSTRAINT orders_balance_status_check;
ALTER TABLE orders
    ADD CONSTRAINT orders_balance_status_check CHECK (balance_status IN (
        'UNPAID', 'PARTIALLY_PAID', 'PAID', 'OVERPAID', 'ON_ACCOUNT', 'REFUNDED'
    )),
    -- A refund is only ever the consequence of a cancellation. An open or completed order that
    -- read REFUNDED would be an order the shop is still working on, or has closed, with no money.
    ADD CONSTRAINT orders_refunded_only_when_cancelled CHECK (
        balance_status <> 'REFUNDED' OR commercial_status = 'CANCELLED'
    );

-- 2. The refund ledger.
--
-- The composite key below is what makes "full, deterministic, no staff-typed amount" a property of
-- the schema rather than of the code that happens to write the row today: a refund can only name a
-- (settlement, order, store, amount) tuple that exists in `order_settlements`, so its amount *is*
-- the settled amount and its order *is* the settled order. `order_settlements` is append-only, so
-- adding a UNIQUE over columns that already contain its primary key changes nothing about it.
ALTER TABLE order_settlements
    ADD CONSTRAINT order_settlements_refund_binding
    UNIQUE (id, order_id, store_id, paid_amount_vnd);

CREATE TABLE order_refunds (
    id UUID PRIMARY KEY,
    -- One refund per order, because there is one settlement per order and it is refunded whole.
    order_id UUID NOT NULL UNIQUE REFERENCES orders(id),
    store_id UUID NOT NULL REFERENCES stores(id),
    settlement_id UUID NOT NULL UNIQUE,
    refunded_amount_vnd BIGINT NOT NULL CHECK (refunded_amount_vnd >= 0),
    -- Single-valued on purpose, like `order_settlements.settlement_shape` was: the column exists so
    -- the meaning of the positive amount is written down next to it, and a second value would be a
    -- second kind of money movement that needs its own decision.
    direction TEXT NOT NULL CHECK (direction = 'TO_CUSTOMER'),
    -- The DEC-024 resolutions under which the customer is not charged. NOT_RECEIVED is absent: it
    -- means nothing was taken in, and a paid order always has custody recorded.
    custody_resolution TEXT NOT NULL CHECK (
        custody_resolution IN ('RETURNED_UNWASHED_REFUNDED', 'SHOP_FAULT_NO_CHARGE')
    ),
    -- The staff member who handed the money back, as DEC-024 requires ("recorded by name").
    attested_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    -- When the money went back across the counter. This, not the settlement's time, decides which
    -- business day the refund reduces.
    refunded_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (settlement_id, order_id, store_id, refunded_amount_vnd)
        REFERENCES order_settlements (id, order_id, store_id, paid_amount_vnd)
);

CREATE INDEX order_refunds_store_idx ON order_refunds (store_id, refunded_at);

CREATE TRIGGER order_refunds_append_only
    BEFORE UPDATE OR DELETE ON order_refunds
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- 3. The two tables agree, enforced from both sides.
--
-- From the order: a paid order cannot be cancelled with its balance left PAID -- which is exactly the
-- state the defect produced -- and cannot claim REFUNDED without a refund row behind it. This is a
-- separate trigger rather than an edit of `enforce_order_projection_update`, so the projection guard
-- keeps the one job its name describes.
CREATE FUNCTION enforce_order_refund_consistency() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.balance_status = 'PAID'
       AND NEW.commercial_status = 'CANCELLED'
       AND NEW.balance_status <> 'REFUNDED' THEN
        RAISE EXCEPTION 'a paid order cannot be cancelled without refunding the settled amount';
    END IF;
    IF NEW.balance_status = 'REFUNDED' AND OLD.balance_status IS DISTINCT FROM 'REFUNDED' THEN
        IF OLD.balance_status <> 'PAID'
           OR OLD.commercial_status <> 'CANCELLATION_REVIEW'
           OR NEW.commercial_status <> 'CANCELLED' THEN
            RAISE EXCEPTION 'only a paid order leaving cancellation review can become refunded';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM order_refunds r WHERE r.order_id = NEW.id) THEN
            RAISE EXCEPTION 'an order cannot read as refunded without a refund record';
        END IF;
    END IF;
    IF OLD.balance_status = 'REFUNDED' AND NEW.balance_status <> 'REFUNDED' THEN
        RAISE EXCEPTION 'a refunded order cannot be re-paid by an update';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER order_refund_consistency
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION enforce_order_refund_consistency();

-- From the refund: checked at commit, because the refund row is written first and the order moves
-- to CANCELLED/REFUNDED after it in the same transaction. A refund that commits beside an order
-- still open, or still reading PAID, is money recorded as gone for goods the shop still owes.
CREATE FUNCTION enforce_refund_cancels_order() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM orders o
        WHERE o.id = NEW.order_id
          AND o.commercial_status = 'CANCELLED'
          AND o.balance_status = 'REFUNDED'
    ) THEN
        RAISE EXCEPTION 'a refund must commit with the cancellation of its order';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER order_refunds_cancel_their_order
    AFTER INSERT ON order_refunds
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_refund_cancels_order();
