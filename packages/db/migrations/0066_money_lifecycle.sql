-- MONEY-LIFECYCLE-009 (round 9): money that moved is never un-owed, never paid twice, never lost.
--
--   1. M1 (DEC-035 x DEC-036): a storage fee the customer already paid part of stays owed-for when
--      the fee later falls (`order_storage_fees.basis`).
--   2. DEC-047: a hold pauses the storage fee; it never erases it (`order_storage_holds`).
--   3. DEC-045 / DEC-046 (M3, M7): a cancellation without charge voids an unspent credit issued
--      from the order, nets a spent one from the refund, and reissues a credit the bill spent
--      (`remedy_credits.voided_*`, `reissue_of`; `order_refunds.netted_remedy_vnd`).
--
-- Part 1: a storage fee the customer already paid part of stays owed-for when the fee later falls.
--
-- `0060` fixes the storage fee only when the payment that settles the order is taken, and records
-- the accrual that produced it (`days_waiting`, `chargeable_days`, the policy version). Until then
-- the fee is recomputed at every read. A part payment can cover part of the fee (100.000 d quoted,
-- 5.000 d accrued, 103.000 d paid) and a later event can lower the recomputed fee -- a waiver, a
-- hold, a rewash that restarts the free days, the owner withdrawing the policy, a cancellation.
-- The rule the code now applies (`unclaimed.fee_already_paid`): money that moved is never un-owed
-- by a later event. The part of the fee the ledger already holds stays owed-for; returning it is a
-- refund, a separate and explicit act. DEC-036's own reversal clause says the same thing for a
-- withdrawn policy: "recorded fees stay on their orders".
--
-- When such an order is settled -- by the waiver that leaves nothing owed, or by a 0 d settlement
-- of a ledger that already covers what is owed -- its fee is fixed at that kept part. There is no
-- accrual to record for it: the policy may be withdrawn, the free days may have restarted, the
-- order may be on hold. So this migration adds exactly that second kind of fixed fee:
--
--   ACCRUED       the fee the published policy computed when it was fixed; the trace columns are
--                 filled, as for every row written before this migration (the default).
--   ALREADY_PAID  the part of the fee the order's payments had already covered when the fee fell;
--                 the trace columns are NULL, because no accrual produced this figure.
--
-- Additive and forward-only. Every existing row reads ACCRUED with its trace intact, and `0060`'s
-- and `0061`'s commit-time checks -- the settlement or the account charge owes the quoted total
-- plus this amount -- apply to both kinds unchanged. Rolling the code back leaves a column with a
-- default the older code never names: its inserts still write ACCRUED rows with their trace.

ALTER TABLE order_storage_fees
    ADD COLUMN basis TEXT NOT NULL DEFAULT 'ACCRUED'
        CONSTRAINT order_storage_fees_basis_check CHECK (basis IN ('ACCRUED', 'ALREADY_PAID')),
    ALTER COLUMN days_waiting DROP NOT NULL,
    ALTER COLUMN chargeable_days DROP NOT NULL,
    ALTER COLUMN policy_version_id DROP NOT NULL,
    ADD CONSTRAINT order_storage_fees_trace_matches_basis CHECK (
        (
            basis = 'ACCRUED'
            AND days_waiting IS NOT NULL
            AND chargeable_days IS NOT NULL
            AND policy_version_id IS NOT NULL
        )
        OR (
            basis = 'ALREADY_PAID'
            AND days_waiting IS NULL
            AND chargeable_days IS NULL
            AND policy_version_id IS NULL
        )
    );

COMMENT ON COLUMN order_storage_fees.basis IS
    'ACCRUED: the fee the published storage policy computed when it was fixed (days_waiting, '
    'chargeable_days and policy_version_id are its trace). ALREADY_PAID: the part of the fee the '
    'order''s payments had already covered when the fee later fell (waiver, hold, rewash, policy '
    'withdrawn); kept, never un-owed (MONEY-LIFECYCLE-009, DEC-036 reversal clause). No trace.';

-- ================================================================================================
-- DEC-047 (2026-09-30, delegated): a hold pauses the storage fee; it never erases it.
--
-- Until now the fee was computed only while the order waited for pickup, so putting finished
-- laundry on hold (production ON_HOLD, resuming to READY_AT_STORE) made the accrued fee vanish: an
-- operator could hold the order, take only the quoted total and settle it -- getting round the
-- approver-only waiver (DEC-036). The fee accrued up to a hold now stays owed, the days on hold do
-- not count, and RESUME continues the count where it stopped (`unclaimed.counted_days`).
--
-- One row per hold of finished laundry: begun by the transition that holds it (`hold_move` START)
-- and ended, once, by the one that lifts it (END), in the same transaction as the move. Nothing
-- else writes it. A rewash needs no write: holds that began before the laundry was last ready do
-- not count. Ordinary production moves never touch this table.
CREATE TABLE order_storage_holds (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(id),
    store_id UUID NOT NULL REFERENCES stores(id),
    held_at TIMESTAMPTZ NOT NULL,
    resumed_at TIMESTAMPTZ NULL,
    CONSTRAINT order_storage_holds_resumed_after_held CHECK (
        resumed_at IS NULL OR resumed_at >= held_at
    )
);

CREATE UNIQUE INDEX order_storage_holds_one_open
    ON order_storage_holds (order_id) WHERE resumed_at IS NULL;
CREATE INDEX order_storage_holds_order_idx ON order_storage_holds (order_id, held_at);

-- The one update a hold admits is its end, once; it is never deleted.
CREATE FUNCTION protect_order_storage_hold() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'a storage hold is a record; it is never deleted';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.order_id IS DISTINCT FROM OLD.order_id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.held_at IS DISTINCT FROM OLD.held_at
       OR OLD.resumed_at IS NOT NULL
       OR NEW.resumed_at IS NULL THEN
        RAISE EXCEPTION 'the only update to a storage hold is its end, once';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER order_storage_holds_protected
    BEFORE UPDATE OR DELETE ON order_storage_holds
    FOR EACH ROW EXECUTE FUNCTION protect_order_storage_hold();

-- An order already on hold from the shelf when this migration runs: its hold began at the event
-- that put it there (every transition writes one). Closed orders are left alone (nothing accrues
-- on them). An order with no such event gets no row, which reads as before this migration -- the
-- customer's side. No order row is touched.
INSERT INTO order_storage_holds (id, order_id, store_id, held_at)
SELECT gen_random_uuid(), o.id, o.store_id, held.occurred_at
FROM orders o
JOIN (
    SELECT e.aggregate_id AS order_id, max(e.occurred_at) AS occurred_at
    FROM domain_events e
    WHERE e.aggregate_type = 'ORDER'
      AND e.event_type = 'ORDER_STATE_TRANSITIONED'
      AND e.payload ->> 'dimension' = 'production'
      AND e.payload ->> 'target' = 'ON_HOLD'
    GROUP BY e.aggregate_id
) held ON held.order_id = o.id
WHERE o.production_status = 'ON_HOLD'
  AND o.production_resume_status = 'READY_AT_STORE'
  AND o.production_ready_at IS NOT NULL
  AND held.occurred_at >= o.production_ready_at
  AND o.commercial_status NOT IN ('CANCELLED', 'COMPLETED');

COMMENT ON TABLE order_storage_holds IS
    'DEC-047: each hold of finished laundry; the storage fee is frozen while one is open and the '
    'days of the lifted ones do not count. Holds before the last ready time (a rewash) do not count.';

-- ================================================================================================
-- DEC-045 (M3) and DEC-046 (M7), 2026-09-30, delegated: a cancellation without charge never
-- compensates twice and never loses a credit.
--
-- `0042` allowed exactly one update to a remedy credit, its redemption. Two more facts now exist:
--
--   voided_*        DEC-045. An unspent credit issued from an order that is then cancelled without
--                   charge is voided in the same transaction, attributed to the staff member and
--                   naming the cancelled order. A voided credit can never be spent.
--   reissue_of      DEC-046. A credit spent on an order that is then cancelled without charge comes
--                   back as a NEW credit of the same face value under the same proposal, linked to
--                   the credit it replaces and to the order whose cancellation brought it back.
--                   `0042`'s one-credit-per-proposal becomes one ORIGINAL credit per proposal, plus
--                   at most one reissue of any credit.
--
-- A credit from the order that was already spent elsewhere is netted from the refund instead:
-- `order_refunds.netted_remedy_vnd`, with what went back plus what was netted equal to what was
-- paid -- `0046`'s settlement binding and `0059`'s ledger rule restated over that sum.
ALTER TABLE remedy_credits
    ADD COLUMN voided_at TIMESTAMPTZ NULL,
    ADD COLUMN voided_by_staff_id UUID NULL REFERENCES staff_users(id),
    ADD COLUMN voided_with_order_id UUID NULL REFERENCES orders(id),
    ADD COLUMN reissue_of UUID NULL REFERENCES remedy_credits(id),
    ADD COLUMN reissued_for_order_id UUID NULL REFERENCES orders(id),
    ADD CONSTRAINT remedy_credits_void_is_complete CHECK (
        (voided_at IS NULL AND voided_by_staff_id IS NULL AND voided_with_order_id IS NULL)
        OR (voided_at IS NOT NULL AND voided_by_staff_id IS NOT NULL
            AND voided_with_order_id IS NOT NULL)
    ),
    -- Voided with the cancellation of the order it was issued from, and never after being spent.
    ADD CONSTRAINT remedy_credits_void_names_its_order CHECK (
        voided_with_order_id IS NULL OR voided_with_order_id = issued_from_order_id
    ),
    ADD CONSTRAINT remedy_credits_spent_or_voided CHECK (voided_at IS NULL OR redeemed_at IS NULL),
    ADD CONSTRAINT remedy_credits_reissue_is_complete CHECK (
        (reissue_of IS NULL) = (reissued_for_order_id IS NULL)
    ),
    ADD CONSTRAINT remedy_credits_reissued_once UNIQUE (reissue_of);

ALTER TABLE remedy_credits DROP CONSTRAINT remedy_credits_remedy_proposal_id_key;
CREATE UNIQUE INDEX remedy_credits_one_original_per_proposal
    ON remedy_credits (remedy_proposal_id) WHERE reissue_of IS NULL;

-- The open-credit index, narrowed to what "open" now means: neither spent nor voided.
DROP INDEX remedy_credits_open_idx;
CREATE INDEX remedy_credits_open_idx ON remedy_credits (store_id, issued_at DESC)
    WHERE redeemed_at IS NULL AND voided_at IS NULL;

-- `0042`'s guard, restated with the void as the second legal update. Every clause of `0042` stands.
CREATE OR REPLACE FUNCTION protect_remedy_credit() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.remedy_proposal_id IS DISTINCT FROM OLD.remedy_proposal_id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.bearer_contact_id IS DISTINCT FROM OLD.bearer_contact_id
       OR NEW.issued_from_order_id IS DISTINCT FROM OLD.issued_from_order_id
       OR NEW.amount_vnd IS DISTINCT FROM OLD.amount_vnd
       OR NEW.direction IS DISTINCT FROM OLD.direction
       OR NEW.policy_version_id IS DISTINCT FROM OLD.policy_version_id
       OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
       OR NEW.reissue_of IS DISTINCT FROM OLD.reissue_of
       OR NEW.reissued_for_order_id IS DISTINCT FROM OLD.reissued_for_order_id
       OR NEW.row_version <> OLD.row_version + 1 THEN
        RAISE EXCEPTION 'an issued remedy credit is immutable';
    END IF;
    IF OLD.voided_at IS NOT NULL THEN
        RAISE EXCEPTION 'a voided remedy credit is final';
    END IF;
    IF OLD.redeemed_at IS NOT NULL THEN
        RAISE EXCEPTION 'a remedy credit is redeemable exactly once';
    END IF;
    IF NEW.voided_at IS NOT NULL THEN
        IF NEW.redeemed_at IS NOT NULL THEN
            RAISE EXCEPTION 'a voided remedy credit cannot be spent';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.redeemed_at IS NULL THEN
        RAISE EXCEPTION 'the only updates to a remedy credit are its redemption and its void';
    END IF;
    RETURN NEW;
END;
$$;

-- A reissue is the credit it replaces, again: same proposal, store, face value and policy version,
-- and that credit was spent on the bill of the order whose cancellation brings it back.
CREATE FUNCTION check_remedy_credit_reissue() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    original remedy_credits%ROWTYPE;
BEGIN
    IF NEW.reissue_of IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO original FROM remedy_credits c WHERE c.id = NEW.reissue_of;
    IF original.id IS NULL
       OR original.redeemed_at IS NULL
       OR original.remedy_proposal_id <> NEW.remedy_proposal_id
       OR original.store_id <> NEW.store_id
       OR original.amount_vnd <> NEW.amount_vnd
       OR original.policy_version_id <> NEW.policy_version_id
       OR original.issued_from_order_id <> NEW.issued_from_order_id
       OR NOT EXISTS (
           SELECT 1 FROM orders o
           WHERE o.id = NEW.reissued_for_order_id
             AND o.current_quote_id = original.redeemed_quote_id
             AND o.current_quote_revision = original.redeemed_quote_revision
       ) THEN
        RAISE EXCEPTION 'a reissued credit replaces a credit spent on the order it is reissued for';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER remedy_credits_reissue_checked
    BEFORE INSERT ON remedy_credits
    FOR EACH ROW EXECUTE FUNCTION check_remedy_credit_reissue();

-- Both happen only with the cancellation of the order they name, checked at commit.
CREATE FUNCTION require_remedy_credit_cancellation() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    subject UUID;
BEGIN
    subject := coalesce(NEW.reissued_for_order_id, NEW.voided_with_order_id);
    IF subject IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM orders o WHERE o.id = subject AND o.commercial_status = 'CANCELLED'
    ) THEN
        RAISE EXCEPTION 'a credit is voided or reissued only with the cancellation of its order';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER remedy_credits_move_with_a_cancellation
    AFTER INSERT OR UPDATE ON remedy_credits
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION require_remedy_credit_cancellation();

-- The refund's netting. `refunded_amount_vnd` stays what went back to the customer; the netted
-- part is beside it, and the settlement binding `0046` put on the amount now binds the two summed.
ALTER TABLE order_refunds
    ADD COLUMN netted_remedy_vnd BIGINT NOT NULL DEFAULT 0
        CONSTRAINT order_refunds_netted_remedy_vnd_check CHECK (netted_remedy_vnd >= 0),
    ADD COLUMN returned_and_netted_vnd BIGINT
        GENERATED ALWAYS AS (refunded_amount_vnd + netted_remedy_vnd) STORED;

ALTER TABLE order_refunds
    DROP CONSTRAINT order_refunds_settlement_id_order_id_store_id_refunded_amo_fkey,
    ADD CONSTRAINT order_refunds_settlement_binding
        FOREIGN KEY (settlement_id, order_id, store_id, returned_and_netted_vnd)
        REFERENCES order_settlements (id, order_id, store_id, paid_amount_vnd);

COMMENT ON COLUMN order_refunds.netted_remedy_vnd IS
    'DEC-045: the face value of credits issued from this order and already spent, taken off the '
    'refund (never more than was paid). refunded_amount_vnd is what went back to the customer.';

-- A netted refund names credits that exist: no more is netted than the spent credits issued from
-- the order are worth.
CREATE FUNCTION check_refund_netting() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.netted_remedy_vnd > 0 AND NEW.netted_remedy_vnd > (
        SELECT coalesce(sum(c.amount_vnd), 0) FROM remedy_credits c
        WHERE c.issued_from_order_id = NEW.order_id AND c.redeemed_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'a refund nets only credits issued from its order and spent';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER order_refunds_netting_checked
    BEFORE INSERT ON order_refunds
    FOR EACH ROW EXECUTE FUNCTION check_refund_netting();

-- `0059`'s balance-and-ledger rule with the refund clause over what went back plus what was netted.
-- Replaced here, not edited there: `0059` is applied and checksummed. Every other clause is its.
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
        SELECT TRUE, r.settlement_id, r.refunded_amount_vnd + r.netted_remedy_vnd
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
