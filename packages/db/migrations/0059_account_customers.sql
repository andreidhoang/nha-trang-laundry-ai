-- PAYMENT-002: account customers (công nợ) -- the B2B half of `DEC-035` (2026-09-25).
--
-- `BUSINESS_TRUTH_INTAKE.md` says B2B credit is "có hỗ trợ sau phê duyệt", with the term, the
-- limit, the approver and the overdue handling `CẦN CHỐT`. `DEC-035` settled them: only a BUSINESS
-- customer the owner marks as an account, with a limit in VND the owner types (none enforced until
-- typed); the calendar month as the statement period, due by the 15th of the next month; an order's
-- goods may leave unpaid while outstanding plus the order stays within the limit and no statement is
-- overdue; the owner may lift an overdue block, and every lift is recorded.
--
-- The shape chosen reuses the money path PAYMENT-001 built rather than adding a parallel one:
--
--   * an order that leaves on the account moves its balance to `ON_ACCOUNT` (a member of the
--     balance CHECK since `0007`) and writes one charge row -- no payment row, because no money
--     moved, so the day's takings do not count it;
--   * a payment against the account is one row here, split oldest-first into ordinary
--     `order_payments` rows (one per order it reaches), each tied to it by an allocation row. The
--     takings and the report read `order_payments` exactly as before, so an account payment is
--     money in on the day it was taken, by its method, once;
--   * the allocation that covers an order's charge writes its settlement (a fourth shape,
--     `EXACT_PAYMENT_ON_ACCOUNT`) and moves its balance to `PAID` -- including on a completed order,
--     the one update of a closed order this schema now admits.
--
-- Everything money here is append-only; the account row is the one versioned projection.

-- 1. The account. One per BUSINESS customer, in the customer's store.
CREATE TABLE customer_accounts (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL UNIQUE,
    store_id UUID NOT NULL,
    -- The owner's limit in whole đồng. NULL until the owner types one: `ACCOUNT_LIMIT_UNSET`, and no
    -- default is ever assumed (`DEC-035`'s 3.000.000 ₫ is a recommendation shown as a hint).
    credit_limit_vnd BIGINT NULL
        CHECK (credit_limit_vnd IS NULL OR credit_limit_vnd BETWEEN 1 AND 9007199254740991),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'SUSPENDED')),
    -- The owner's current lift of the overdue block, exclusive end; the lifts ledger below keeps
    -- every one with its reason.
    overdue_block_lifted_until TIMESTAMPTZ NULL,
    -- The published account terms (`scripts/publish_account_terms.py`) in force when the owner
    -- opened it.
    terms_version_id UUID NOT NULL REFERENCES configuration_versions (id),
    opened_by_staff_id UUID NOT NULL REFERENCES staff_users (id),
    opened_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL CHECK (row_version > 0),
    updated_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (customer_id, store_id) REFERENCES customers (id, store_id),
    -- The key the ledgers below point at, carrying the customer and the store along with the id.
    UNIQUE (id, customer_id, store_id)
);

COMMENT ON TABLE customer_accounts IS
    'What question does this answer: "may this business customer take goods unpaid, up to how much, '
    'and is it blocked?". PAYMENT-002 / DEC-035. Opened, limited, suspended and lifted by the owner '
    'only. Outstanding, statements and overdue are summed from the ledgers below, never stored here.';

CREATE FUNCTION protect_customer_account() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'an account is suspended, never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (
            SELECT 1 FROM customers c
            WHERE c.id = NEW.customer_id AND c.store_id = NEW.store_id
              AND c.kind = 'BUSINESS' AND c.erased_at IS NULL
        ) THEN
            RAISE EXCEPTION 'only a business customer on record may have an account';
        END IF;
        IF NEW.row_version <> 1 THEN
            RAISE EXCEPTION 'an account starts at version 1';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.customer_id IS DISTINCT FROM OLD.customer_id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.terms_version_id IS DISTINCT FROM OLD.terms_version_id
       OR NEW.opened_by_staff_id IS DISTINCT FROM OLD.opened_by_staff_id
       OR NEW.opened_at IS DISTINCT FROM OLD.opened_at
       OR NEW.updated_at < OLD.updated_at THEN
        RAISE EXCEPTION 'an account''s identity and opening are immutable';
    END IF;
    IF NEW.row_version <> OLD.row_version + 1 THEN
        RAISE EXCEPTION 'STALE_VERSION: account row_version must advance by one';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER customer_accounts_guard
    BEFORE INSERT OR UPDATE OR DELETE ON customer_accounts
    FOR EACH ROW EXECUTE FUNCTION protect_customer_account();

-- A customer with an account stays a business customer: `DEC-035` admits no other kind.
CREATE FUNCTION protect_account_customer_kind() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind IS DISTINCT FROM 'BUSINESS'
       AND EXISTS (SELECT 1 FROM customer_accounts a WHERE a.customer_id = NEW.id) THEN
        RAISE EXCEPTION 'a customer with an account stays a business customer';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER customers_account_kind
    BEFORE UPDATE OF kind ON customers
    FOR EACH ROW EXECUTE FUNCTION protect_account_customer_kind();

-- 2. Every lift of the overdue block, with the owner's reason and its end. Append-only.
CREATE TABLE customer_account_block_lifts (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES customer_accounts (id),
    store_id UUID NOT NULL,
    reason TEXT NOT NULL CHECK (char_length(reason) BETWEEN 3 AND 200),
    lifted_until TIMESTAMPTZ NOT NULL,
    lifted_by_staff_id UUID NOT NULL REFERENCES staff_users (id),
    lifted_at TIMESTAMPTZ NOT NULL,
    CHECK (lifted_until > lifted_at)
);

CREATE INDEX customer_account_block_lifts_account_idx
    ON customer_account_block_lifts (account_id, lifted_at);

CREATE TRIGGER customer_account_block_lifts_append_only
    BEFORE UPDATE OR DELETE ON customer_account_block_lifts
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- 3. An order that left on the account: what it still owed, charged to the account. One per order.
CREATE TABLE customer_account_charges (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL,
    customer_id UUID NOT NULL,
    store_id UUID NOT NULL,
    order_id UUID NOT NULL UNIQUE REFERENCES orders (id),
    -- What the order owed in all, what its ledger held before the charge (a deposit), and the rest,
    -- which is the charge.
    owed_vnd BIGINT NOT NULL CHECK (owed_vnd > 0),
    paid_before_vnd BIGINT NOT NULL CHECK (paid_before_vnd >= 0),
    amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
    -- The customer took the bag at the counter in the same press; false for a delivery order,
    -- which reaches its customer by a leg.
    collected_by_customer BOOLEAN NOT NULL,
    charged_by_staff_id UUID NOT NULL REFERENCES staff_users (id),
    charged_at TIMESTAMPTZ NOT NULL,
    CHECK (owed_vnd = paid_before_vnd + amount_vnd),
    FOREIGN KEY (account_id, customer_id, store_id)
        REFERENCES customer_accounts (id, customer_id, store_id),
    UNIQUE (id, account_id, order_id)
);

COMMENT ON TABLE customer_account_charges IS
    'What question does this answer: "which orders left on the account, when, for how much?". One '
    'append-only row per order (PAYMENT-002). Money owed, not money collected: takings never read it.';

CREATE INDEX customer_account_charges_account_idx
    ON customer_account_charges (account_id, charged_at, id);

CREATE TRIGGER customer_account_charges_append_only
    BEFORE UPDATE OR DELETE ON customer_account_charges
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- The charge names an order of the account's own customer and store, and exactly what it still
-- owed: its quote's single total, less what its payment ledger held when the charge was written.
CREATE FUNCTION enforce_account_charge() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    order_customer UUID;
    order_store UUID;
    total_min BIGINT;
    total_max BIGINT;
    paid BIGINT;
    account_status TEXT;
    account_limit BIGINT;
BEGIN
    SELECT o.customer_id, o.store_id, r.display_total_min_vnd, r.display_total_max_vnd
      INTO order_customer, order_store, total_min, total_max
      FROM orders o
      JOIN quote_revisions r
        ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
     WHERE o.id = NEW.order_id;
    IF order_customer IS DISTINCT FROM NEW.customer_id OR order_store IS DISTINCT FROM NEW.store_id THEN
        RAISE EXCEPTION 'an account charge names an order of the account''s own customer';
    END IF;
    IF total_min IS NULL OR total_min IS DISTINCT FROM total_max OR total_min <> NEW.owed_vnd THEN
        RAISE EXCEPTION 'an account charge owes the order''s single quoted total';
    END IF;
    SELECT coalesce(sum(p.amount_vnd), 0) INTO paid FROM order_payments p WHERE p.order_id = NEW.order_id;
    IF paid <> NEW.paid_before_vnd THEN
        RAISE EXCEPTION 'an account charge is what the order still owed';
    END IF;
    SELECT a.status, a.credit_limit_vnd INTO account_status, account_limit
      FROM customer_accounts a WHERE a.id = NEW.account_id;
    IF account_status <> 'ACTIVE' OR account_limit IS NULL THEN
        RAISE EXCEPTION 'only an active account with a limit takes a charge';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER customer_account_charges_consistent
    BEFORE INSERT ON customer_account_charges
    FOR EACH ROW EXECUTE FUNCTION enforce_account_charge();

-- At commit: the charged order reads ON_ACCOUNT (or PAID, when a payment in the same transaction
-- already covered it).
CREATE FUNCTION enforce_account_charge_moves_order() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM orders o
        WHERE o.id = NEW.order_id AND o.balance_status IN ('ON_ACCOUNT', 'PAID')
    ) THEN
        RAISE EXCEPTION 'a charged order reads as on account';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER customer_account_charges_move_order
    AFTER INSERT ON customer_account_charges
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_account_charge_moves_order();

-- 4. A payment against the account: one amount, one method, as the counter took it.
CREATE TABLE customer_account_payments (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL,
    customer_id UUID NOT NULL,
    store_id UUID NOT NULL,
    amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
    method TEXT NOT NULL CHECK (method IN ('TIEN_MAT', 'CHUYEN_KHOAN')),
    bank_ref_last TEXT NULL CHECK (bank_ref_last IS NULL OR bank_ref_last ~ '^[A-Z0-9]{2,12}$'),
    recorded_by_staff_id UUID NOT NULL REFERENCES staff_users (id),
    recorded_at TIMESTAMPTZ NOT NULL,
    CHECK (bank_ref_last IS NULL OR method = 'CHUYEN_KHOAN'),
    FOREIGN KEY (account_id, customer_id, store_id)
        REFERENCES customer_accounts (id, customer_id, store_id)
);

COMMENT ON TABLE customer_account_payments IS
    'What question does this answer: "how much did the account customer pay, how, when?". One '
    'append-only row per payment (PAYMENT-002), split oldest-first into order_payments rows by '
    'customer_account_allocations; takings read those, never this, so nothing counts twice.';

CREATE INDEX customer_account_payments_account_idx
    ON customer_account_payments (account_id, recorded_at, id);

CREATE TRIGGER customer_account_payments_append_only
    BEFORE UPDATE OR DELETE ON customer_account_payments
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- 5. The allocation ledger: which part of which account payment went to which order's charge, and
-- the `order_payments` row that carries it. Append-only.
CREATE TABLE customer_account_allocations (
    id UUID PRIMARY KEY,
    account_payment_id UUID NOT NULL REFERENCES customer_account_payments (id),
    account_id UUID NOT NULL,
    charge_id UUID NOT NULL,
    order_id UUID NOT NULL,
    order_payment_id UUID NOT NULL UNIQUE REFERENCES order_payments (id),
    amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
    -- The order the payment reached it in: 1 is the oldest unpaid charge.
    position INTEGER NOT NULL CHECK (position >= 1),
    FOREIGN KEY (charge_id, account_id, order_id)
        REFERENCES customer_account_charges (id, account_id, order_id),
    UNIQUE (account_payment_id, position),
    UNIQUE (account_payment_id, charge_id)
);

CREATE INDEX customer_account_allocations_charge_idx
    ON customer_account_allocations (charge_id);

CREATE TRIGGER customer_account_allocations_append_only
    BEFORE UPDATE OR DELETE ON customer_account_allocations
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- At commit, from the allocation: it is its order payment, to the đồng and in every other fact,
-- and it never takes a charge past what the charge owed.
CREATE FUNCTION enforce_account_allocation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    payment_account UUID;
    payment_amount BIGINT;
    payment_method TEXT;
    payment_ref TEXT;
    payment_at TIMESTAMPTZ;
    payment_store UUID;
    charged BIGINT;
    allocated BIGINT;
BEGIN
    SELECT p.account_id, p.amount_vnd, p.method, p.bank_ref_last, p.recorded_at, p.store_id
      INTO payment_account, payment_amount, payment_method, payment_ref, payment_at, payment_store
      FROM customer_account_payments p WHERE p.id = NEW.account_payment_id;
    IF payment_account IS DISTINCT FROM NEW.account_id THEN
        RAISE EXCEPTION 'an allocation belongs to its payment''s account';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM order_payments op
        WHERE op.id = NEW.order_payment_id AND op.order_id = NEW.order_id
          AND op.amount_vnd = NEW.amount_vnd AND op.method = payment_method
          AND op.bank_ref_last IS NOT DISTINCT FROM payment_ref
          AND op.recorded_at = payment_at AND op.store_id = payment_store AND NOT op.legacy
    ) THEN
        RAISE EXCEPTION 'an allocation is exactly its order payment';
    END IF;
    SELECT c.amount_vnd INTO charged FROM customer_account_charges c WHERE c.id = NEW.charge_id;
    SELECT coalesce(sum(a.amount_vnd), 0) INTO allocated
      FROM customer_account_allocations a WHERE a.charge_id = NEW.charge_id;
    IF allocated > charged THEN
        RAISE EXCEPTION 'an account payment never pays an order past what it owed';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER customer_account_allocations_consistent
    AFTER INSERT ON customer_account_allocations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_account_allocation();

-- At commit, from the payment: its allocations sum to it exactly -- no money unallocated, none
-- invented.
CREATE FUNCTION enforce_account_payment_allocated() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.amount_vnd IS DISTINCT FROM (
        SELECT coalesce(sum(a.amount_vnd), 0) FROM customer_account_allocations a
        WHERE a.account_payment_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'an account payment is allocated in full';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER customer_account_payments_allocated
    AFTER INSERT ON customer_account_payments
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_account_payment_allocated();

-- At commit, from the order payment: once an order is on the account, money reaches it only
-- through the account. A payment row after its charge with no allocation behind it is a second
-- path to the same money.
CREATE FUNCTION enforce_charged_order_paid_through_account() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM customer_account_charges c
        WHERE c.order_id = NEW.order_id AND NEW.recorded_at >= c.charged_at
    ) AND NOT EXISTS (
        SELECT 1 FROM customer_account_allocations a WHERE a.order_payment_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'an order on account is paid through its account';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER order_payments_on_account_through_account
    AFTER INSERT ON order_payments
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_charged_order_paid_through_account();

-- 6. A statement frozen at month close by the owner's script (`scripts/close_account_statements.py`).
-- The figures are the statement SQL's (`nha_trang_laundry_db.accounts`) at the moment of freezing,
-- with the query version that produced them. Append-only; one per account and month.
CREATE TABLE customer_account_statements (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES customer_accounts (id),
    store_id UUID NOT NULL,
    period_month DATE NOT NULL CHECK (extract(day FROM period_month) = 1),
    opening_vnd BIGINT NOT NULL CHECK (opening_vnd >= 0),
    charges_vnd BIGINT NOT NULL CHECK (charges_vnd >= 0),
    payments_vnd BIGINT NOT NULL CHECK (payments_vnd >= 0),
    closing_vnd BIGINT NOT NULL CHECK (closing_vnd >= 0),
    charge_count INTEGER NOT NULL CHECK (charge_count >= 0),
    payment_count INTEGER NOT NULL CHECK (payment_count >= 0),
    -- `DEC-035`: the 15th of the following month.
    due_on DATE NOT NULL,
    query_version TEXT NOT NULL,
    frozen_by_staff_id UUID NOT NULL REFERENCES staff_users (id),
    frozen_at TIMESTAMPTZ NOT NULL,
    CHECK (closing_vnd = opening_vnd + charges_vnd - payments_vnd),
    CHECK (due_on = (period_month + INTERVAL '1 month')::date + 14),
    UNIQUE (account_id, period_month)
);

CREATE TRIGGER customer_account_statements_append_only
    BEFORE UPDATE OR DELETE ON customer_account_statements
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- 7. The fourth settlement shape, admitted by the columns that enumerate them (`0033`, `0048`):
-- the exact total, paid through the account after the goods left on it. `ACCOUNT_HANDOVER` is what
-- `collected_by` truthfully is at that moment: the leaving is recorded by the account charge.
ALTER TABLE order_settlements
    DROP CONSTRAINT order_settlements_settlement_shape_check,
    ADD CONSTRAINT order_settlements_settlement_shape_check CHECK (
        settlement_shape IN (
            'EXACT_PAYMENT_SELF_COLLECTION',
            'EXACT_PAYMENT_PREPAID_DELIVERY',
            'EXACT_PAYMENT_PREPAID_SELF_COLLECTION',
            'EXACT_PAYMENT_ON_ACCOUNT'
        )
    ),
    DROP CONSTRAINT order_settlements_collected_by_check,
    ADD CONSTRAINT order_settlements_collected_by_check CHECK (
        collected_by IN ('CUSTOMER', 'PENDING_DELIVERY', 'PENDING_COLLECTION', 'ACCOUNT_HANDOVER')
    ),
    DROP CONSTRAINT order_settlements_shape_matches_collection,
    ADD CONSTRAINT order_settlements_shape_matches_collection CHECK (
        (settlement_shape = 'EXACT_PAYMENT_SELF_COLLECTION' AND collected_by = 'CUSTOMER')
        OR (settlement_shape = 'EXACT_PAYMENT_PREPAID_DELIVERY'
            AND collected_by = 'PENDING_DELIVERY')
        OR (settlement_shape = 'EXACT_PAYMENT_PREPAID_SELF_COLLECTION'
            AND collected_by = 'PENDING_COLLECTION')
        OR (settlement_shape = 'EXACT_PAYMENT_ON_ACCOUNT' AND collected_by = 'ACCOUNT_HANDOVER')
    );

-- 8. `0056`'s balance-and-ledger rule, with `ON_ACCOUNT` given its meaning: the order has its
-- charge, no settlement yet, and money still owed on it. Replaced here, not edited there: `0056` is
-- applied and checksummed. Every other clause is `0056`'s, unchanged.
CREATE OR REPLACE FUNCTION enforce_order_payment_ledger() RETURNS trigger
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
    charge_owed BIGINT;
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
    ELSIF balance = 'ON_ACCOUNT' THEN
        SELECT c.owed_vnd INTO charge_owed FROM customer_account_charges c WHERE c.order_id = subject;
        IF charge_owed IS NULL OR settlement IS NOT NULL OR paid >= charge_owed THEN
            RAISE EXCEPTION 'an order on account has its charge, no settlement, and money still owed';
        END IF;
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

-- 9. The two balance moves an account makes, and only those: into ON_ACCOUNT from a balance still
-- owing money, with the charge row already written; and out of it only to PAID, through a
-- settlement of the account shape. A separate trigger, so `0046`'s refund rule and `0056`'s ledger
-- rule keep the one job each.
CREATE FUNCTION enforce_order_account_balance() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.balance_status = 'ON_ACCOUNT' AND OLD.balance_status IS DISTINCT FROM 'ON_ACCOUNT' THEN
        IF OLD.balance_status NOT IN ('UNPAID', 'PARTIALLY_PAID')
           OR NOT EXISTS (SELECT 1 FROM customer_account_charges c WHERE c.order_id = NEW.id) THEN
            RAISE EXCEPTION 'an order goes on account from money owed, with its charge recorded';
        END IF;
    END IF;
    IF OLD.balance_status = 'ON_ACCOUNT' AND NEW.balance_status IS DISTINCT FROM 'ON_ACCOUNT' THEN
        IF NEW.balance_status <> 'PAID' OR NOT EXISTS (
            SELECT 1 FROM order_settlements s
            WHERE s.order_id = NEW.id AND s.settlement_shape = 'EXACT_PAYMENT_ON_ACCOUNT'
        ) THEN
            RAISE EXCEPTION 'an order on account leaves it only when the account pays it in full';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER order_account_balance
    BEFORE UPDATE OF balance_status ON orders
    FOR EACH ROW EXECUTE FUNCTION enforce_order_account_balance();

-- 10. The projection guard (`0008`, `0036`) admits one update of a closed row: a completed order on
-- account becoming PAID when the account pays it off -- its balance and its row version, and not
-- one other column. Every other clause is `0036`'s, unchanged.
CREATE OR REPLACE FUNCTION enforce_order_projection_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.bound_contact_id IS DISTINCT FROM OLD.bound_contact_id
       OR NEW.acquisition_source IS DISTINCT FROM OLD.acquisition_source
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.row_version <> OLD.row_version + 1
       OR (
           OLD.commercial_status IN ('CANCELLED', 'COMPLETED')
           AND NOT (
               OLD.commercial_status = 'COMPLETED'
               AND OLD.balance_status = 'ON_ACCOUNT'
               AND NEW.balance_status = 'PAID'
               AND (to_jsonb(NEW) - 'balance_status' - 'row_version')
                   = (to_jsonb(OLD) - 'balance_status' - 'row_version')
           )
       )
       OR (OLD.commercial_status = 'ACTIVE' AND NEW.commercial_status = 'CANCELLED') THEN
        RAISE EXCEPTION 'invalid order projection update';
    END IF;
    RETURN NEW;
END;
$$;
