-- UNCLAIMED-001 (DEC-036): laundry waiting for pickup -- contact attempts, the storage fee, disposal.
--
-- The shop's own terms: "Lấy đồ trong 20 ngày; sau đó tính phí lưu kho; sau 60 ngày hiện có điều
-- khoản thanh lý." The figures are the owner's, published as the STORAGE_POLICY configuration by
-- `scripts/publish_storage_policy.py`; nothing here charges or disposes of anything by itself.
--
-- Additive only: four new append-only tables, and one trigger function replaced so a disposed-of
-- order may close without a refund. No existing row is touched, and no existing column changes.
--
-- * `order_contact_attempts` -- every call, Zalo, SMS or visit, with its outcome. Works before the
--   policy is published: the waiting list and the attempts are how the shop reaches customers.
-- * `order_storage_fees` -- the fee FIXED when the settling payment is taken, so what the customer
--   paid is what was owed then. One per order, bound to the settlement it was paid in, and checked
--   at commit to make that settlement exactly the quoted total plus the fee.
-- * `storage_fee_waivers` -- an approver's or the owner's waiver, with its reason; one per order.
-- * `order_disposals` -- the owner's thanh lý: days waited, attempts counted, and the money:
--   kept (paid) + written off = owed. Commits only beside an order that is CANCELLED.
--
-- Forward-only. Rolling the code back leaves four tables the older code never reads and a refund
-- rule that is wider by exactly one case the older code cannot produce (it has no disposal route).
-- An older reader sees a disposed order as a cancelled order whose balance is what was paid.

-- One store check for all four tables: the row's store is its order's store.
CREATE FUNCTION enforce_unclaimed_row_store() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM orders o WHERE o.id = NEW.order_id AND o.store_id = NEW.store_id) THEN
        RAISE EXCEPTION 'an unclaimed-laundry record belongs to its order''s store';
    END IF;
    RETURN NEW;
END;
$$;

-- 1. Contact attempts. The note is a few words about what happened ("hẹn chiều mai qua"); it is
-- never a phone number -- the same pattern the domain's `PHONE_LIKE` refuses -- and never leaves
-- this table (no event, audit or outbox payload carries it).
CREATE TABLE order_contact_attempts (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(id),
    store_id UUID NOT NULL REFERENCES stores(id),
    channel TEXT NOT NULL CHECK (channel IN ('CALL', 'ZALO', 'SMS', 'VISIT')),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('REACHED', 'NO_ANSWER', 'WRONG_NUMBER', 'PROMISED_TO_COME')
    ),
    note TEXT NULL CHECK (
        note IS NULL
        OR (length(btrim(note)) BETWEEN 1 AND 120 AND note !~ '\+?[0-9]([ .-]?[0-9]){6,}')
    ),
    attempted_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    attempted_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

COMMENT ON TABLE order_contact_attempts IS
    'What question does this answer: "did the shop try to reach this customer about laundry waiting '
    'for pickup, how, when, and what happened?". Append-only (DEC-036). Disposal needs at least the '
    'published number of attempts on at least the published number of different shop days.';

CREATE INDEX order_contact_attempts_order_idx ON order_contact_attempts (order_id, attempted_at, id);

CREATE TRIGGER order_contact_attempts_store
    BEFORE INSERT ON order_contact_attempts
    FOR EACH ROW EXECUTE FUNCTION enforce_unclaimed_row_store();

CREATE TRIGGER order_contact_attempts_append_only
    BEFORE UPDATE OR DELETE ON order_contact_attempts
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- 2. The storage fee a settling payment fixed. `days_waiting` / `chargeable_days` are the domain's
-- trace; the configuration version is the published policy it was computed under.
CREATE TABLE order_storage_fees (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL UNIQUE REFERENCES orders(id),
    store_id UUID NOT NULL REFERENCES stores(id),
    settlement_id UUID NOT NULL UNIQUE REFERENCES order_settlements(id),
    amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
    days_waiting INTEGER NOT NULL CHECK (days_waiting > 0),
    chargeable_days INTEGER NOT NULL CHECK (chargeable_days > 0 AND chargeable_days <= days_waiting),
    policy_version_id UUID NOT NULL REFERENCES configuration_versions(id),
    fixed_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    fixed_at TIMESTAMPTZ NOT NULL
);

COMMENT ON TABLE order_storage_fees IS
    'What question does this answer: "what storage fee did the customer pay on this order?". Written '
    'with the payment that settled the order in full (DEC-036), never changed after; the settlement '
    'it names is exactly the quoted total plus this amount.';

CREATE TRIGGER order_storage_fees_store
    BEFORE INSERT ON order_storage_fees
    FOR EACH ROW EXECUTE FUNCTION enforce_unclaimed_row_store();

CREATE TRIGGER order_storage_fees_append_only
    BEFORE UPDATE OR DELETE ON order_storage_fees
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- The fee and the settlement agree: the settlement it was paid in belongs to the same order and
-- settled exactly the settled quote revision's total plus the fee. Deferred, because the payment
-- writes the settlement and this row in one transaction.
CREATE FUNCTION enforce_storage_fee_settlement() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
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
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER order_storage_fees_agree_with_settlement
    AFTER INSERT ON order_storage_fees
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_storage_fee_settlement();

-- 3. Waivers. One per order: once waived, nothing accrues on the order any more.
CREATE TABLE storage_fee_waivers (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL UNIQUE REFERENCES orders(id),
    store_id UUID NOT NULL REFERENCES stores(id),
    waived_amount_vnd BIGINT NOT NULL CHECK (waived_amount_vnd > 0),
    days_waiting INTEGER NOT NULL CHECK (days_waiting > 0),
    reason TEXT NOT NULL CHECK (
        length(btrim(reason)) BETWEEN 1 AND 120 AND reason !~ '\+?[0-9]([ .-]?[0-9]){6,}'
    ),
    policy_version_id UUID NOT NULL REFERENCES configuration_versions(id),
    waived_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    waived_at TIMESTAMPTZ NOT NULL
);

COMMENT ON TABLE storage_fee_waivers IS
    'What question does this answer: "who waived the storage fee on this order, why, and how much '
    'was owed then?". An OPS_APPROVER or the owner, with a reason (DEC-036). Append-only.';

CREATE TRIGGER storage_fee_waivers_store
    BEFORE INSERT ON storage_fee_waivers
    FOR EACH ROW EXECUTE FUNCTION enforce_unclaimed_row_store();

CREATE TRIGGER storage_fee_waivers_append_only
    BEFORE UPDATE OR DELETE ON storage_fee_waivers
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- A paid order's fee is fixed, so a waiver after the settlement would waive nothing.
CREATE FUNCTION enforce_waiver_before_settlement() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM order_settlements s WHERE s.order_id = NEW.order_id) THEN
        RAISE EXCEPTION 'a storage fee is waived only before the order is paid in full';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER storage_fee_waivers_before_settlement
    BEFORE INSERT ON storage_fee_waivers
    FOR EACH ROW EXECUTE FUNCTION enforce_waiver_before_settlement();

-- 4. Disposals (thanh lý). The money columns state DEC-036's rule as arithmetic the database checks:
-- what was paid is kept, the rest of what was owed is written off.
CREATE TABLE order_disposals (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL UNIQUE REFERENCES orders(id),
    store_id UUID NOT NULL REFERENCES stores(id),
    days_waiting INTEGER NOT NULL CHECK (days_waiting > 0),
    attempts_counted INTEGER NOT NULL CHECK (attempts_counted > 0),
    attempt_days INTEGER NOT NULL CHECK (attempt_days > 0 AND attempt_days <= attempts_counted),
    owed_vnd BIGINT NOT NULL CHECK (owed_vnd >= 0),
    storage_fee_vnd BIGINT NOT NULL CHECK (storage_fee_vnd >= 0 AND storage_fee_vnd <= owed_vnd),
    kept_vnd BIGINT NOT NULL CHECK (kept_vnd >= 0),
    written_off_vnd BIGINT NOT NULL CHECK (written_off_vnd >= 0),
    policy_version_id UUID NOT NULL REFERENCES configuration_versions(id),
    disposed_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    disposed_at TIMESTAMPTZ NOT NULL,
    CHECK (kept_vnd + written_off_vnd = owed_vnd)
);

COMMENT ON TABLE order_disposals IS
    'What question does this answer: "which laundry did the shop dispose of as unclaimed, on whose '
    'approval, after how long and how many attempts, and what happened to the money?". The owner '
    'only (DEC-036). The order closes CANCELLED with custody resolution UNCLAIMED_DISPOSED.';

CREATE TRIGGER order_disposals_store
    BEFORE INSERT ON order_disposals
    FOR EACH ROW EXECUTE FUNCTION enforce_unclaimed_row_store();

CREATE TRIGGER order_disposals_append_only
    BEFORE UPDATE OR DELETE ON order_disposals
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- The disposal and the order agree at commit: a disposal row beside an order that did not close,
-- or whose ledger says something other than what the row kept, cannot commit.
CREATE FUNCTION enforce_disposal_closes_order() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    commercial TEXT;
    paid BIGINT;
BEGIN
    SELECT o.commercial_status INTO commercial FROM orders o WHERE o.id = NEW.order_id;
    SELECT coalesce(sum(p.amount_vnd), 0) INTO paid FROM order_payments p WHERE p.order_id = NEW.order_id;
    IF commercial IS DISTINCT FROM 'CANCELLED' THEN
        RAISE EXCEPTION 'a disposal closes its order';
    END IF;
    IF paid <> NEW.kept_vnd THEN
        RAISE EXCEPTION 'a disposal keeps exactly what the order''s payments sum to';
    END IF;
    IF EXISTS (SELECT 1 FROM order_refunds r WHERE r.order_id = NEW.order_id) THEN
        RAISE EXCEPTION 'a disposed order refunds nothing';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER order_disposals_close_their_order
    AFTER INSERT ON order_disposals
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_disposal_closes_order();

-- 5. The refund rule, widened by exactly the disposal. `0056`'s body, with one exception to its
-- first clause: a paid or partly paid order may close CANCELLED without a refund when its disposal
-- is recorded -- DEC-036, "money already paid is kept". The disposal row is written before the
-- order moves, so it is visible here. Replaced rather than edited: `0056` is applied and
-- checksummed.
CREATE OR REPLACE FUNCTION enforce_order_refund_consistency() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.balance_status IN ('PAID', 'PARTIALLY_PAID')
       AND NEW.commercial_status = 'CANCELLED'
       AND NEW.balance_status <> 'REFUNDED'
       AND NOT EXISTS (SELECT 1 FROM order_disposals d WHERE d.order_id = NEW.id) THEN
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
