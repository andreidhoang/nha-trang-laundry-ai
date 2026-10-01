-- INVOICE-TRUTH-009 (review M6): an issued invoice request keeps the figure it was issued for.
--
-- `0063` stored no amount on a request: an order's charges and an account month's charges were
-- read at the moment the request was read. That is right while the request waits for the
-- bookkeeper, and wrong once the bookkeeper has issued the invoice in the provider's portal: the
-- invoice is a document the customer holds, and a storage fee that accrues, a refund or a
-- cancellation afterwards silently changed the figure the shop showed beside it.
--
-- So the moment a request is recorded as ISSUED, what it was for is fixed here, in the same
-- transaction, and never moves again:
--
-- * `invoice_request_snapshots` -- one row per issued request: the total, the storage fee part of
--   an order's total, the order's quote revision (its lines are that immutable revision's), and how
--   many orders it covers.
-- * `invoice_request_snapshot_orders` -- one row per covered order: its amount on the invoice (an
--   account month's line is the order's whole cost, `owed_vnd`, review M5), the account charge it
--   came from, and whether the order was already cancelled or refunded when the invoice was issued
--   -- which is what lets a later read say "đã hoàn tiền sau khi xuất hóa đơn" by comparison
--   rather than by guessing from times.
--
-- Both are append-only. At commit, every ISSUED request has its snapshot, every snapshot belongs to
-- an ISSUED request of its own store and kind, and a snapshot's orders are exactly as many as it
-- says and add up to its total (an account month; an order's single line is its total).
--
-- **Backfill.** Requests already ISSUED before this migration get their snapshot here, from the
-- value the pre-`0068` read computed at this moment, marked `origin = 'MIGRATION_0068'`:
--
-- * an order: its quote's single total (null when the quote has none, as the read said) plus the
--   storage fee the ledger has fixed for it (`order_storage_fees`), else 0. The one case the read
--   computed differently is a fee still accruing, unfixed, at this moment (a published policy, the
--   laundry waiting on the shelf past its free days): that fee is policy arithmetic in Python and is
--   not repeated in SQL here. The snapshot then holds the quote's total and the next read flags
--   `AMOUNT_CHANGED_AFTER_ISSUE` with the figure now, so the difference is shown, never absorbed.
-- * an account month: one line per order charged to the month with what went on the account
--   (`amount_vnd`), which is what the pre-`0068` read and download showed. Where an order had a
--   deposit before it went on the account, the next read flags `AMOUNT_CHANGED_AFTER_ISSUE`: the
--   invoice was issued without the deposit (review M5), and the bookkeeper is told.
--
-- Forward-only. Rolling the code back leaves two tables the older code never reads, and the
-- deferred check below would refuse the older code's issue (it writes no snapshot): a rollback of
-- the code must be to a build that writes one, or this constraint trigger is dropped by a later
-- migration with that decision recorded.

-- 1. The snapshot of an issued request.
CREATE TABLE invoice_request_snapshots (
    request_id UUID PRIMARY KEY REFERENCES invoice_requests (id),
    store_id UUID NOT NULL REFERENCES stores (id),
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('ORDER', 'ACCOUNT_MONTH')),
    origin TEXT NOT NULL CHECK (origin IN ('AT_ISSUE', 'MIGRATION_0068')),
    -- What the invoice was for. Null only for an order whose quote presents no single total.
    total_vnd BIGINT NULL CHECK (total_vnd IS NULL OR total_vnd >= 0),
    -- The storage fee among an order's total, when there was one.
    storage_fee_vnd BIGINT NULL CHECK (storage_fee_vnd IS NULL OR storage_fee_vnd > 0),
    -- An order's lines are its quote revision's, which is immutable.
    quote_id UUID NULL,
    quote_revision INTEGER NULL,
    order_count INTEGER NOT NULL CHECK (order_count >= 1),
    taken_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (quote_id, quote_revision) REFERENCES quote_revisions (quote_id, revision),
    CONSTRAINT invoice_request_snapshots_shape CHECK (
        (subject_kind = 'ORDER' AND quote_id IS NOT NULL AND quote_revision IS NOT NULL
            AND order_count = 1
            AND (storage_fee_vnd IS NULL OR total_vnd IS NOT NULL))
        OR (subject_kind = 'ACCOUNT_MONTH' AND quote_id IS NULL AND quote_revision IS NULL
            AND storage_fee_vnd IS NULL AND total_vnd IS NOT NULL)
    )
);

COMMENT ON TABLE invoice_request_snapshots IS
    'What question does this answer: "what figure was this invoice request issued for?". One '
    'append-only row per ISSUED request, written in the issuing transaction (INVOICE-TRUTH-009, '
    'review M6); later events on its orders are flagged at read, never folded into it.';

-- 2. The orders an issued request covers, as they stood when it was issued.
CREATE TABLE invoice_request_snapshot_orders (
    request_id UUID NOT NULL REFERENCES invoice_request_snapshots (request_id),
    position INTEGER NOT NULL CHECK (position >= 1),
    order_id UUID NOT NULL REFERENCES orders (id),
    -- An account month's line: the charge the order went on the account with.
    account_charge_id UUID NULL REFERENCES customer_account_charges (id),
    -- The order's amount on the invoice. Null only for an order with no single total.
    amount_vnd BIGINT NULL CHECK (amount_vnd IS NULL OR amount_vnd >= 0),
    cancelled_at_issue BOOLEAN NOT NULL,
    refunded_at_issue BOOLEAN NOT NULL,
    PRIMARY KEY (request_id, order_id),
    UNIQUE (request_id, position)
);

COMMENT ON TABLE invoice_request_snapshot_orders IS
    'What question does this answer: "which orders did this issued invoice cover, for how much each, '
    'and were they already cancelled or refunded when it was issued?". Append-only (INVOICE-TRUTH-009).';

CREATE INDEX invoice_request_snapshot_orders_order_idx
    ON invoice_request_snapshot_orders (order_id);

CREATE TRIGGER invoice_request_snapshots_append_only
    BEFORE UPDATE OR DELETE ON invoice_request_snapshots
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TRIGGER invoice_request_snapshot_orders_append_only
    BEFORE UPDATE OR DELETE ON invoice_request_snapshot_orders
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- 3. Each covered order belongs to the request's subject.
CREATE FUNCTION enforce_invoice_snapshot_order() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    request RECORD;
    charge RECORD;
BEGIN
    SELECT r.subject_kind, r.order_id, r.account_id, r.period_month
      INTO request
      FROM invoice_requests r WHERE r.id = NEW.request_id;
    IF request.subject_kind = 'ORDER' THEN
        IF NEW.order_id IS DISTINCT FROM request.order_id OR NEW.account_charge_id IS NOT NULL THEN
            RAISE EXCEPTION 'an order request''s snapshot covers exactly its own order';
        END IF;
        RETURN NEW;
    END IF;
    SELECT c.order_id, c.account_id, c.charged_at INTO charge
      FROM customer_account_charges c WHERE c.id = NEW.account_charge_id;
    IF NOT FOUND
       OR charge.order_id IS DISTINCT FROM NEW.order_id
       OR charge.account_id IS DISTINCT FROM request.account_id
       OR date_trunc('month', charge.charged_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
          IS DISTINCT FROM request.period_month THEN
        RAISE EXCEPTION 'an account month''s snapshot covers charges of that account and month';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER invoice_request_snapshot_orders_subject
    BEFORE INSERT ON invoice_request_snapshot_orders
    FOR EACH ROW EXECUTE FUNCTION enforce_invoice_snapshot_order();

-- 4. At commit: an issued request has its snapshot, and a snapshot is whole and agrees with itself.
CREATE FUNCTION check_invoice_snapshot(target UUID) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    request RECORD;
    snapshot RECORD;
    lines INTEGER;
    line_total NUMERIC;
    null_lines INTEGER;
    has_snapshot BOOLEAN;
BEGIN
    SELECT r.status, r.store_id, r.subject_kind INTO request
      FROM invoice_requests r WHERE r.id = target;
    SELECT s.store_id, s.subject_kind, s.total_vnd, s.order_count INTO snapshot
      FROM invoice_request_snapshots s WHERE s.request_id = target;
    has_snapshot := FOUND;
    IF request.status = 'ISSUED' AND NOT has_snapshot THEN
        RAISE EXCEPTION 'INVOICE_SNAPSHOT_MISSING: an issued invoice request keeps what it was issued for';
    END IF;
    IF NOT has_snapshot THEN
        RETURN;
    END IF;
    IF request.status IS DISTINCT FROM 'ISSUED'
       OR request.store_id IS DISTINCT FROM snapshot.store_id
       OR request.subject_kind IS DISTINCT FROM snapshot.subject_kind THEN
        RAISE EXCEPTION 'an invoice snapshot belongs to an issued request of its own store and kind';
    END IF;
    SELECT count(*), sum(o.amount_vnd), count(*) FILTER (WHERE o.amount_vnd IS NULL)
      INTO lines, line_total, null_lines
      FROM invoice_request_snapshot_orders o WHERE o.request_id = target;
    IF lines <> snapshot.order_count THEN
        RAISE EXCEPTION 'an invoice snapshot covers exactly the orders it counts';
    END IF;
    IF snapshot.total_vnd IS NULL THEN
        IF null_lines <> lines THEN
            RAISE EXCEPTION 'an invoice snapshot without a total has no line amount';
        END IF;
    ELSIF null_lines > 0 OR line_total IS DISTINCT FROM snapshot.total_vnd::NUMERIC THEN
        RAISE EXCEPTION 'an invoice snapshot''s lines add up to its total';
    END IF;
END;
$$;

CREATE FUNCTION check_invoice_snapshot_row() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'invoice_requests' THEN
        PERFORM check_invoice_snapshot(NEW.id);
    ELSE
        PERFORM check_invoice_snapshot(NEW.request_id);
    END IF;
    RETURN NULL;
END;
$$;

-- 5. Backfill the requests issued before this migration (see the header), then arm the checks.
INSERT INTO invoice_request_snapshots (
    request_id, store_id, subject_kind, origin, total_vnd, storage_fee_vnd, quote_id,
    quote_revision, order_count, taken_at
)
SELECT r.id, r.store_id, 'ORDER', 'MIGRATION_0068',
       CASE WHEN q.display_total_min_vnd = q.display_total_max_vnd
            THEN q.display_total_min_vnd + coalesce(f.amount_vnd, 0) END,
       CASE WHEN q.display_total_min_vnd = q.display_total_max_vnd AND f.amount_vnd > 0
            THEN f.amount_vnd END,
       o.current_quote_id, o.current_quote_revision, 1, transaction_timestamp()
FROM invoice_requests r
JOIN orders o ON o.id = r.order_id
JOIN quote_revisions q ON q.quote_id = o.current_quote_id AND q.revision = o.current_quote_revision
LEFT JOIN order_storage_fees f ON f.order_id = o.id
WHERE r.status = 'ISSUED' AND r.subject_kind = 'ORDER';

INSERT INTO invoice_request_snapshot_orders (
    request_id, position, order_id, account_charge_id, amount_vnd, cancelled_at_issue,
    refunded_at_issue
)
SELECT s.request_id, 1, r.order_id, NULL, s.total_vnd, o.commercial_status = 'CANCELLED',
       EXISTS (SELECT 1 FROM order_refunds x WHERE x.order_id = o.id)
FROM invoice_request_snapshots s
JOIN invoice_requests r ON r.id = s.request_id
JOIN orders o ON o.id = r.order_id
WHERE s.subject_kind = 'ORDER';

INSERT INTO invoice_request_snapshots (
    request_id, store_id, subject_kind, origin, total_vnd, storage_fee_vnd, quote_id,
    quote_revision, order_count, taken_at
)
SELECT r.id, r.store_id, 'ACCOUNT_MONTH', 'MIGRATION_0068', sum(c.amount_vnd), NULL, NULL, NULL,
       count(c.id), transaction_timestamp()
FROM invoice_requests r
JOIN customer_account_charges c
  ON c.account_id = r.account_id
 AND c.charged_at >= r.period_month::timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh'
 AND c.charged_at < (r.period_month + INTERVAL '1 month')::date::timestamp
     AT TIME ZONE 'Asia/Ho_Chi_Minh'
WHERE r.status = 'ISSUED' AND r.subject_kind = 'ACCOUNT_MONTH'
GROUP BY r.id, r.store_id;

INSERT INTO invoice_request_snapshot_orders (
    request_id, position, order_id, account_charge_id, amount_vnd, cancelled_at_issue,
    refunded_at_issue
)
SELECT r.id,
       row_number() OVER (PARTITION BY r.id ORDER BY c.charged_at, c.id),
       c.order_id, c.id, c.amount_vnd, o.commercial_status = 'CANCELLED',
       EXISTS (SELECT 1 FROM order_refunds x WHERE x.order_id = o.id)
FROM invoice_requests r
JOIN invoice_request_snapshots s ON s.request_id = r.id
JOIN customer_account_charges c
  ON c.account_id = r.account_id
 AND c.charged_at >= r.period_month::timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh'
 AND c.charged_at < (r.period_month + INTERVAL '1 month')::date::timestamp
     AT TIME ZONE 'Asia/Ho_Chi_Minh'
JOIN orders o ON o.id = c.order_id
WHERE r.subject_kind = 'ACCOUNT_MONTH';

-- Every issued request now has its snapshot; one that does not (an issued month whose charges are
-- gone, which the append-only charges make impossible) stops the migration rather than pass.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM invoice_requests r
        WHERE r.status = 'ISSUED'
          AND NOT EXISTS (SELECT 1 FROM invoice_request_snapshots s WHERE s.request_id = r.id)
    ) THEN
        RAISE EXCEPTION '0068: an issued invoice request could not be given its snapshot';
    END IF;
END;
$$;

CREATE CONSTRAINT TRIGGER invoice_requests_issued_has_snapshot
    AFTER UPDATE ON invoice_requests
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (NEW.status = 'ISSUED' AND OLD.status IS DISTINCT FROM 'ISSUED')
    EXECUTE FUNCTION check_invoice_snapshot_row();

CREATE CONSTRAINT TRIGGER invoice_request_snapshots_whole
    AFTER INSERT ON invoice_request_snapshots
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_invoice_snapshot_row();

CREATE CONSTRAINT TRIGGER invoice_request_snapshot_orders_whole
    AFTER INSERT ON invoice_request_snapshot_orders
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_invoice_snapshot_row();
