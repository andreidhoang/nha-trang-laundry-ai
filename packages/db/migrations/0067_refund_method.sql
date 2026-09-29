-- GOODS-AND-DRAWER-009 (review M4): how a refund's money went back, so "tiền trong két" is the drawer.
--
-- The review: `collected-today-v3` netted every refund against money in by EVERY method, and the
-- Today screen called the result "Tiền trong két". 500.000 d cash + 2.000.000 d transfer - 100.000 d
-- refund read "tăng 2.400.000 d" while the drawer had moved about 400.000 d. The drawer is cash in
-- minus cash handed back, and `order_refunds` recorded that money went back but never how, so the
-- cash half of a refund could not be told apart from a bank transfer back.
--
-- 1. `order_refunds.refund_method`: 'TIEN_MAT' (handed back from the drawer) or 'CHUYEN_KHOAN'
--    (sent back by bank transfer) -- the vocabulary of `order_payments.method` -- chosen by the
--    staff member who records the refunding cancellation. Nullable, because every refund written
--    before this migration did not record it, and guessing would put a figure in the wrong
--    column: those rows stay NULL, read as "không rõ cách hoàn", and the drawer figure states that
--    it excludes them (their count and their total) instead of netting them.
-- 2. Every refund written from now on must carry it: a BEFORE INSERT trigger, rather than a NOT
--    NULL, so the rows already written stay exactly as they are (`order_refunds` is append-only;
--    nothing here or anywhere updates them).
--
-- Additive only; no existing row changes. Forward-only: rolling the code back leaves a nullable
-- column the older code never reads -- but the older code's refund INSERT names no method, so the
-- trigger refuses its refunding cancellations (fail closed, with the trigger's message) until the
-- code is rolled forward again or the trigger is dropped by a later migration.

ALTER TABLE order_refunds
    ADD COLUMN refund_method TEXT NULL CHECK (
        refund_method IS NULL OR refund_method IN ('TIEN_MAT', 'CHUYEN_KHOAN')
    );

COMMENT ON COLUMN order_refunds.refund_method IS
    'What question does this answer: "how did this money go back to the customer?" TIEN_MAT from '
    'the drawer, CHUYEN_KHOAN by bank transfer, as the staff member recording the refund said. NULL '
    'only on refunds written before 0067, which never recorded it: unknown, never guessed.';

CREATE FUNCTION require_refund_method() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.refund_method IS NULL THEN
        RAISE EXCEPTION 'a refund records how the money went back (refund_method)';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER order_refunds_method_required
    BEFORE INSERT ON order_refunds
    FOR EACH ROW EXECUTE FUNCTION require_refund_method();
