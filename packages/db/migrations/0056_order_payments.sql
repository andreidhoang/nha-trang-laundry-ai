-- PAYMENT-001: deposits and part payments with a method (`DEC-035`, 2026-09-25, the counter half).
--
-- `0025` deliberately had no payment method, no deposit and no part payment, because `DEC-010` held
-- all of them open. `DEC-035` supersedes that deferral for the counter: the shop takes cash
-- (`TIEN_MAT`) or a bank transfer to its account (`CHUYEN_KHOAN`), any amount from 1 đồng up to what
-- is still owed, as many times as needed. Overpayment is refused; the counter gives change.
--
-- The shape chosen: `order_payments` is the money ledger, one append-only row per amount taken, and
-- `order_settlements` keeps its meaning -- "this order is paid in full" -- written by whichever
-- payment completes the order, with `paid_amount_vnd = expected_total_vnd` still true for every row.
-- That keeps every reader of the settlement (the pickup record `0048`, the refund binding `0046`,
-- remedies, exports) exactly as it was, and puts the new facts -- how much, how, when, by whom --
-- in the new table.
--
-- `kind PAYMENT|REFUND` (spec §2) is not a column here. Money going back already has its own
-- append-only ledger, `order_refunds` (`0046`), and `DEC-035` routes a deposit refund through that
-- existing path. A REFUND row here as well would be two records of one movement, which is how two
-- ledgers start to disagree. Every row in this table is money in.

-- 1. The ledger.
CREATE TABLE order_payments (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(id),
    -- The order's store, checked against the order by `enforce_order_payment_ledger` below.
    store_id UUID NOT NULL,
    amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
    method TEXT NOT NULL CHECK (method IN ('TIEN_MAT', 'CHUYEN_KHOAN')),
    -- The last characters of the bank reference, as the staff member read them off the shop's bank
    -- app. Optional, unverified (no bank feed exists), and only ever on a transfer.
    bank_ref_last TEXT NULL CHECK (bank_ref_last IS NULL OR bank_ref_last ~ '^[A-Z0-9]{2,12}$'),
    -- Recorded by a path that did not ask how the customer paid: every settlement written before
    -- this migration, and the exact-total settlement route an older client still calls. Counted as
    -- `TIEN_MAT`, as the spec directs, and marked so a reader can tell "cash" from "cash, assumed".
    legacy BOOLEAN NOT NULL,
    -- The settlement this payment completed, when it was the one that brought the order to paid in
    -- full. One completing payment per settlement.
    settlement_id UUID NULL UNIQUE REFERENCES order_settlements(id),
    recorded_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    recorded_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CHECK (bank_ref_last IS NULL OR method = 'CHUYEN_KHOAN'),
    -- A legacy row is always a whole settlement taken in one amount, with no method recorded.
    CHECK (NOT legacy OR (method = 'TIEN_MAT' AND bank_ref_last IS NULL AND settlement_id IS NOT NULL))
);

COMMENT ON TABLE order_payments IS
    'What question does this answer: "how much has the customer paid on this order, how, when, and '
    'to whom?". One append-only row per amount taken at the counter (DEC-035). The order''s balance '
    'and its settlement agree with the sum of these rows at every commit.';

CREATE INDEX order_payments_order_idx ON order_payments (order_id, recorded_at, id);
CREATE INDEX order_payments_store_idx ON order_payments (store_id, recorded_at);

-- Append-only like every other money record in this schema. A payment recorded in error is an
-- incident, not an edit.
CREATE TRIGGER order_payments_append_only
    BEFORE UPDATE OR DELETE ON order_payments
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- 2. Every settlement already written becomes the one payment it was, preserving each order's sum.
--
-- The spec: "existing PAID rows migrate as one payment of their settled amount with method TIEN_MAT
-- marked legacy". Every settlement, not only the orders still reading PAID: a refunded order was
-- paid too, and the day it was paid its money came in -- `collected-today-v2` counted it then, and
-- the takings read that moves onto this ledger must keep counting it. The payment is dated by the
-- settlement's attestation and attributed to its staff member. The identifier is derived from the
-- settlement's, so the backfill is the same on every database it runs on.
--
-- A settlement of a zero total (a bill a credit covered in full) carried no money and gets no row;
-- its order's payments sum to 0, which is what it settled.
INSERT INTO order_payments (
    id, order_id, store_id, amount_vnd, method, bank_ref_last, legacy, settlement_id,
    recorded_by_staff_id, recorded_at, created_at
)
SELECT md5('order-payment-legacy:' || s.id::text)::uuid, s.order_id, s.store_id,
       s.paid_amount_vnd, 'TIEN_MAT', NULL, TRUE, s.id,
       s.attested_by_staff_id, s.attested_at, s.attested_at
FROM order_settlements s
WHERE s.paid_amount_vnd > 0;

-- 3. The balance and the ledger agree, checked from both sides (the shape `0046` gave refunds and
-- `0048` gave pickups): from the payment at commit, because it is written before the order moves,
-- and from the order as its balance moves, when every ledger row is already written.
--
--   UNPAID          no payments.
--   PARTIALLY_PAID  at least one payment, and no settlement: money is still owed.
--   PAID            a settlement, and the payments sum to exactly its amount.
--   REFUNDED        a refund of exactly what the payments sum to, naming the settlement if any.
--
-- Overpayment is refused by the domain, and the PAID rule is its second line: a ledger summing to
-- more than the settled total cannot commit beside a paid order.
CREATE FUNCTION enforce_order_payment_ledger() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    subject UUID;
    order_store UUID;
    balance TEXT;
    paid BIGINT;
    entries BIGINT;
    foreign_store BOOLEAN;
    settlement UUID;
    settled BIGINT;
    refund_found BOOLEAN;
    refund_settlement UUID;
    refunded BIGINT;
BEGIN
    IF TG_TABLE_NAME = 'orders' THEN
        subject := NEW.id;
    ELSE
        subject := NEW.order_id;
    END IF;
    SELECT o.store_id, o.balance_status INTO order_store, balance FROM orders o WHERE o.id = subject;
    SELECT coalesce(sum(p.amount_vnd), 0), count(*), coalesce(bool_or(p.store_id <> order_store), FALSE)
      INTO paid, entries, foreign_store
      FROM order_payments p WHERE p.order_id = subject;
    IF foreign_store THEN
        RAISE EXCEPTION 'a payment must be recorded in its order''s store';
    END IF;
    SELECT s.id, s.paid_amount_vnd INTO settlement, settled
      FROM order_settlements s WHERE s.order_id = subject;
    IF balance = 'UNPAID' AND entries > 0 THEN
        RAISE EXCEPTION 'an order with payments cannot read as unpaid';
    ELSIF balance = 'PARTIALLY_PAID' AND (entries = 0 OR settlement IS NOT NULL) THEN
        RAISE EXCEPTION 'a partly paid order has payments and no settlement';
    ELSIF balance = 'PAID' AND (settlement IS NULL OR paid <> settled) THEN
        RAISE EXCEPTION 'a paid order''s payments must sum to its settled amount';
    ELSIF balance = 'REFUNDED' THEN
        SELECT TRUE, r.settlement_id, r.refunded_amount_vnd
          INTO refund_found, refund_settlement, refunded
          FROM order_refunds r WHERE r.order_id = subject;
        IF refund_found IS NULL OR refunded <> paid
           OR refund_settlement IS DISTINCT FROM settlement THEN
            RAISE EXCEPTION 'a refund must return exactly what the order''s payments sum to';
        END IF;
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER order_payments_agree_with_their_order
    AFTER INSERT ON order_payments
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_order_payment_ledger();

-- From the order: immediate, not deferred. Every writer (the payment, the exact-total settlement,
-- the refunding cancellation) writes its ledger rows first and moves the balance last, so they are
-- all visible when this runs. Deferring it would also leave a pending event on `orders` that makes
-- any later `ALTER TABLE orders` in the same transaction fail.
CREATE TRIGGER order_balance_agrees_with_its_payments
    AFTER UPDATE OF balance_status ON orders
    FOR EACH ROW
    WHEN (OLD.balance_status IS DISTINCT FROM NEW.balance_status)
    EXECUTE FUNCTION enforce_order_payment_ledger();

-- 4. A deposit goes back through the existing refund path (`DEC-035`: "refunds of money taken (a
-- cancellation after a deposit) go through the existing refund path, up to what was paid").
--
-- A partly paid order has no settlement, so its refund names none. The composite foreign key of
-- `0046` still binds every refund that does name one to the settled amount (MATCH SIMPLE skips a
-- row whose settlement is NULL), and the ledger check above binds every refund, with or without a
-- settlement, to the sum of the order's payments.
ALTER TABLE order_refunds ALTER COLUMN settlement_id DROP NOT NULL;

-- `0046`'s consistency rule, widened from PAID to "PAID or PARTIALLY_PAID" in both of its money
-- clauses. Replaced here rather than edited there: `0046` is applied and checksummed.
CREATE OR REPLACE FUNCTION enforce_order_refund_consistency() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.balance_status IN ('PAID', 'PARTIALLY_PAID')
       AND NEW.commercial_status = 'CANCELLED'
       AND NEW.balance_status <> 'REFUNDED' THEN
        RAISE EXCEPTION 'a paid order cannot be cancelled without refunding what was paid';
    END IF;
    IF NEW.balance_status = 'REFUNDED' AND OLD.balance_status IS DISTINCT FROM 'REFUNDED' THEN
        IF OLD.balance_status NOT IN ('PAID', 'PARTIALLY_PAID')
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
