-- CASH-COUNT-009 (DEC-049): the end-of-day cash count, "đếm két".
--
-- The review found no open/close count anywhere: "Tiền mặt trong két" (GOODS-AND-DRAWER-009) says
-- how the drawer MOVED, and nobody counted it against that. DEC-049: staff record the opening float
-- (cash counted at the start of the business day) and the closing count; the app shows
-- expected = float + cash taken - cash refunded - Sổ thu chi lines marked "trả từ két", the counted
-- amount and the difference. The difference is recorded and shown to the owner. Nothing happens
-- automatically.
--
-- 1. `expenses.paid_from_drawer`: the yes/no "Trả từ két" on a Sổ thu chi line, default no, and no
--    for every line already written (the column default fills them; nobody paid from the drawer
--    "by default"). It is part of the line, so `protect_expense` is replaced to keep it immutable
--    with the rest: a line is still voided once and otherwise never rewritten.
-- 2. `cash_counts`: one append-only row per entry. An entry is an OPENING_FLOAT or a CLOSING_COUNT
--    for one store and one shop-local business day. Each is recorded once (`cash_counts_original`);
--    a correction is a NEW row that names the row it supersedes (`supersedes_id`, each row
--    superseded at most once, so the chain never forks) and says why (`correction_reason`), for
--    the same store, day and kind (`cash_count_supersedes_same`). Nothing is ever edited or
--    deleted: the wrong figure stays readable, as a ledger's does.
--    A closing count stores what the books said at the moment it was recorded: the expected
--    figure's status, the figure itself when there is one, the difference as a size and a word
--    (never a signed number), the float entry it started from, the calculation trace and its
--    RFC 8785 hash, and the rule version -- so the recorded difference is reproducible from the row
--    alone, whatever is recorded in the books later.
--
-- Money is integer VND in BIGINT, summed only by PostgreSQL; the one subtraction is the domain's
-- (`nha_trang_laundry_domain.cash_count`), on integers.
--
-- Forward-only and additive. Rolling the code back leaves a table the older code never reads and a
-- column with a default the older code's INSERT does not name (so it writes "no"); the replaced
-- `protect_expense` refuses only what the older one refused, plus a rewrite of the new column the
-- older code never makes.

-- 1. "Trả từ két" on a Sổ thu chi line.
ALTER TABLE expenses
    ADD COLUMN paid_from_drawer BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN expenses.paid_from_drawer IS
    'What question does this answer: "was this money handed out of the counter''s drawer?" TRUE '
    'when the person recording the line said so ("Trả từ két"); the day''s cash count expects that '
    'much less cash. FALSE by default and for every line written before 0072.';

CREATE OR REPLACE FUNCTION protect_expense() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- The one update an expense admits is its void, once. The amount, day, category and whether it
    -- came out of the drawer are never rewritten: the correction is a void and a new line.
    IF OLD.voided_at IS NOT NULL
       OR NEW.voided_at IS NULL
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.spent_on IS DISTINCT FROM OLD.spent_on
       OR NEW.category IS DISTINCT FROM OLD.category
       OR NEW.amount_vnd IS DISTINCT FROM OLD.amount_vnd
       OR NEW.note IS DISTINCT FROM OLD.note
       OR NEW.paid_from_drawer IS DISTINCT FROM OLD.paid_from_drawer
       OR NEW.recorded_by IS DISTINCT FROM OLD.recorded_by
       OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at THEN
        RAISE EXCEPTION 'an expense is voided once and otherwise immutable';
    END IF;
    RETURN NEW;
END;
$$;

-- The day's drawer expenses are read by store and day; the existing (store_id, spent_on, ...)
-- index already serves that predicate, so no new index is needed here.

-- 2. The cash count's entries.
CREATE TABLE cash_counts (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES stores (id),
    -- The shop-local business day (Asia/Ho_Chi_Minh) the drawer was counted on.
    business_day DATE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('OPENING_FLOAT', 'CLOSING_COUNT')),
    counted_vnd BIGINT NOT NULL CHECK (counted_vnd >= 0 AND counted_vnd <= 1000000000),
    -- A correction names the entry it replaces and says why; an original does neither.
    supersedes_id UUID NULL UNIQUE REFERENCES cash_counts (id),
    correction_reason TEXT NULL CHECK (
        correction_reason IS NULL OR length(btrim(correction_reason)) BETWEEN 1 AND 120
    ),
    -- A closing count's record of what the books said when it was counted (see the header).
    expected_status TEXT NULL CHECK (
        expected_status IN ('COMPLETE', 'INCOMPLETE', 'FLOAT_MISSING', 'BOOKS_BELOW_ZERO')
    ),
    expected_vnd BIGINT NULL CHECK (expected_vnd IS NULL OR expected_vnd >= 0),
    difference_vnd BIGINT NULL CHECK (difference_vnd IS NULL OR difference_vnd >= 0),
    difference_direction TEXT NULL CHECK (difference_direction IN ('EVEN', 'OVER', 'SHORT')),
    float_entry_id UUID NULL REFERENCES cash_counts (id),
    trace JSONB NULL,
    trace_hash TEXT NULL CHECK (trace_hash IS NULL OR trace_hash LIKE 'JCS-SHA256-V1:%'),
    rule_version TEXT NULL,
    recorded_by UUID NOT NULL REFERENCES staff_users (id),
    recorded_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT cash_counts_correction_has_reason
        CHECK ((supersedes_id IS NULL) = (correction_reason IS NULL)),
    CONSTRAINT cash_counts_never_self CHECK (supersedes_id IS DISTINCT FROM id),
    -- An opening float records a count and nothing else.
    CONSTRAINT cash_counts_float_shape CHECK (
        kind <> 'OPENING_FLOAT' OR (
            expected_status IS NULL AND expected_vnd IS NULL AND difference_vnd IS NULL
            AND difference_direction IS NULL AND float_entry_id IS NULL AND trace IS NULL
            AND trace_hash IS NULL AND rule_version IS NULL
        )
    ),
    -- A closing count always records what the books said, and has a figure and a difference
    -- exactly when the books produced one; it started from a float exactly when there was one.
    CONSTRAINT cash_counts_closing_shape CHECK (
        kind <> 'CLOSING_COUNT' OR (
            expected_status IS NOT NULL AND trace IS NOT NULL AND trace_hash IS NOT NULL
            AND rule_version IS NOT NULL
            AND (expected_vnd IS NOT NULL) = (expected_status IN ('COMPLETE', 'INCOMPLETE'))
            AND (difference_vnd IS NOT NULL) = (expected_vnd IS NOT NULL)
            AND (difference_direction IS NOT NULL) = (expected_vnd IS NOT NULL)
            AND (difference_direction IS DISTINCT FROM 'EVEN' OR difference_vnd = 0)
            AND (difference_direction NOT IN ('OVER', 'SHORT') OR difference_vnd > 0)
            AND (float_entry_id IS NULL) = (expected_status = 'FLOAT_MISSING')
        )
    )
);

COMMENT ON TABLE cash_counts IS
    'What question does this answer: "how much cash did staff count in the drawer at the start '
    'and the end of a business day, and how far was the closing count from what the books say?" '
    'Append-only (CASH-COUNT-009, DEC-049). One original per store, day and kind; a correction is '
    'a new row superseding one, with a reason. The current entry is the one nothing supersedes.';

-- Each kind is recorded once per store and day; everything after that is a correction.
CREATE UNIQUE INDEX cash_counts_original
    ON cash_counts (store_id, business_day, kind) WHERE supersedes_id IS NULL;

CREATE INDEX cash_counts_store_day_idx ON cash_counts (store_id, business_day, recorded_at, id);

CREATE TRIGGER cash_counts_append_only
    BEFORE UPDATE OR DELETE ON cash_counts
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- A correction is of the same store, day and kind as the entry it supersedes, and a float a
-- closing count started from is that store's float for that day.
CREATE FUNCTION enforce_cash_count_links() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    linked RECORD;
BEGIN
    IF NEW.supersedes_id IS NOT NULL THEN
        SELECT store_id, business_day, kind INTO linked FROM cash_counts WHERE id = NEW.supersedes_id;
        IF NOT FOUND
           OR linked.store_id IS DISTINCT FROM NEW.store_id
           OR linked.business_day IS DISTINCT FROM NEW.business_day
           OR linked.kind IS DISTINCT FROM NEW.kind THEN
            RAISE EXCEPTION 'a cash count correction supersedes an entry of the same store, day and kind';
        END IF;
    END IF;
    IF NEW.float_entry_id IS NOT NULL THEN
        SELECT store_id, business_day, kind INTO linked FROM cash_counts WHERE id = NEW.float_entry_id;
        IF NOT FOUND
           OR linked.store_id IS DISTINCT FROM NEW.store_id
           OR linked.business_day IS DISTINCT FROM NEW.business_day
           OR linked.kind IS DISTINCT FROM 'OPENING_FLOAT' THEN
            RAISE EXCEPTION 'a closing count starts from that store''s opening float for that day';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER cash_counts_supersedes_same
    BEFORE INSERT ON cash_counts
    FOR EACH ROW EXECUTE FUNCTION enforce_cash_count_links();
