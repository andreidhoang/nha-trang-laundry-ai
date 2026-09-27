-- Round 7 wave 2 integration: an account order that waited past the free days (PAYMENT-002 x
-- UNCLAIMED-001, DEC-035 x DEC-036).
--
-- The two slices were built in parallel and each is right on its own:
--
--   * `0059` binds an account charge to "the order's single quoted total" (`enforce_account_charge`)
--     and gives `ON_ACCOUNT` its meaning in `0056`'s ledger rule;
--   * `0060` makes the storage fee a second charge on the order, fixed only by the payment that
--     settles it (`order_storage_fees.settlement_id NOT NULL`), and widens `0056`'s refund rule so a
--     disposed-of order closes without a refund.
--
-- The functions they replaced are different ones (`enforce_order_payment_ledger` in `0059`,
-- `enforce_order_refund_consistency` in `0060`), so both rules are in force after `0060` and neither
-- is restated here. What neither could see is the order that is both: a business customer's laundry
-- left on the shelf past day 20, then taken away on the account. Its fee is owed (DEC-036 charges
-- laundry left waiting, whoever owns it), the account's limit must be measured against what the
-- order really owes, and the moment the goods leave is the moment the fee stops -- so that is where
-- it is fixed. Under `0059` and `0060` alone that charge could not be written: the charge had to
-- equal the quoted total, and the fee could only be fixed beside a settlement the account order
-- does not have until the account pays it off.
--
-- This migration admits exactly that case and nothing wider:
--
-- 1. A storage fee is fixed by a settlement (as before) or by the account charge the goods left
--    on -- exactly one of the two.
-- 2. An account charge owes the quoted total plus the fee fixed with it (0 when none), checked at
--    commit because the fee row references the charge and is written after it.
-- 3. A fee fixed by either path cannot be waived afterwards (`0060` refused a waiver only after a
--    settlement).
--
-- Additive and forward-only: no existing row changes (every existing fee row has a settlement), and
-- `0059`'s and `0060`'s functions are replaced rather than edited -- both are applied and
-- checksummed.

-- 1. The fee's binding: a settlement, or an account charge.
ALTER TABLE order_storage_fees
    ALTER COLUMN settlement_id DROP NOT NULL,
    ADD COLUMN account_charge_id UUID NULL UNIQUE REFERENCES customer_account_charges (id),
    ADD CONSTRAINT order_storage_fees_fixed_by_one
        CHECK ((settlement_id IS NULL) <> (account_charge_id IS NULL));

COMMENT ON COLUMN order_storage_fees.account_charge_id IS
    'The account charge the fee was fixed by, when the goods left on the customer''s account '
    '(PAYMENT-002). NULL when a settling payment fixed it (settlement_id).';

-- `0060`'s check, with the account binding beside the settlement one: the charge belongs to the
-- same order and owes exactly its quoted total plus this fee.
CREATE OR REPLACE FUNCTION enforce_storage_fee_settlement() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.settlement_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM order_settlements s
            JOIN quote_revisions r
              ON r.quote_id = s.settled_quote_id AND r.revision = s.settled_quote_revision
            WHERE s.id = NEW.settlement_id
              AND s.order_id = NEW.order_id
              AND r.display_total_min_vnd = r.display_total_max_vnd
              AND s.expected_total_vnd = r.display_total_min_vnd + NEW.amount_vnd
        ) THEN
            RAISE EXCEPTION 'a storage fee is paid inside a settlement of the quoted total plus the fee';
        END IF;
    ELSE
        IF NOT EXISTS (
            SELECT 1
            FROM customer_account_charges c
            JOIN orders o ON o.id = c.order_id
            JOIN quote_revisions r
              ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
            WHERE c.id = NEW.account_charge_id
              AND c.order_id = NEW.order_id
              AND r.display_total_min_vnd = r.display_total_max_vnd
              AND c.owed_vnd = r.display_total_min_vnd + NEW.amount_vnd
        ) THEN
            RAISE EXCEPTION 'a storage fee fixed by an account charge is inside that charge';
        END IF;
    END IF;
    RETURN NULL;
END;
$$;

-- 2. `0059`'s charge rule, with the equality moved to commit. Every clause but the owed amount is
-- `0059`'s, unchanged; the owed amount is at least the quoted total here, and exactly the quoted
-- total plus the fee fixed with the charge at commit (below).
CREATE OR REPLACE FUNCTION enforce_account_charge() RETURNS trigger
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
    IF total_min IS NULL OR total_min IS DISTINCT FROM total_max OR NEW.owed_vnd < total_min THEN
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

CREATE FUNCTION enforce_account_charge_owes_total_and_fee() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM orders o
        JOIN quote_revisions r
          ON r.quote_id = o.current_quote_id AND r.revision = o.current_quote_revision
        WHERE o.id = NEW.order_id
          AND NEW.owed_vnd = r.display_total_min_vnd + coalesce(
              (SELECT f.amount_vnd FROM order_storage_fees f WHERE f.account_charge_id = NEW.id), 0
          )
    ) THEN
        RAISE EXCEPTION 'an account charge owes the quoted total plus the storage fee fixed with it';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER customer_account_charges_owe_total_and_fee
    AFTER INSERT ON customer_account_charges
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_account_charge_owes_total_and_fee();

-- 3. `0060`'s waiver rule: a fee fixed by a settlement or by an account charge is not waived.
CREATE OR REPLACE FUNCTION enforce_waiver_before_settlement() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM order_settlements s WHERE s.order_id = NEW.order_id)
       OR EXISTS (SELECT 1 FROM order_storage_fees f WHERE f.order_id = NEW.order_id) THEN
        RAISE EXCEPTION 'a storage fee is waived only before the order is paid in full';
    END IF;
    RETURN NEW;
END;
$$;
