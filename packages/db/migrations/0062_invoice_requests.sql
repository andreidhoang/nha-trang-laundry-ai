-- EINVOICE-REQUEST-001 (DEC-040): record every invoice request; issuing stays with the owner's
-- e-invoice provider.
--
-- A Vietnamese e-invoice is issued only through a licensed provider, under the shop's own tax code
-- and signature. Which invoice type, which rate and which date are the owner's and the
-- accountant's to settle. So nothing here issues, numbers, prices or taxes anything: a request is
-- the buyer's details at the moment the customer asked, a state, and -- once the bookkeeper has
-- issued the invoice in the provider's portal -- the symbol, number and date they read back.
--
-- Additive only: four new tables and two triggers on `customers` that fire only on erasure. No
-- existing row is touched, no existing trigger is replaced, and no existing column changes.
--
-- * `invoice_requests` -- one row per request. `REQUESTED` -> `ISSUED` | `CANCELLED`, by trigger;
--   both are terminal and an issued row is immutable. One live request (REQUESTED or ISSUED) per
--   subject: an order, or an account customer's calendar month.
-- * `customer_invoice_profiles` -- an account customer's buyer details, saved only when staff tick
--   "lưu cho lần sau", pre-filled into their next request.
-- * `invoice_request_exports` -- every download of the open list for the bookkeeper: who, when,
--   how many rows, the file's digest and the query version that produced it. Append-only.
--
-- **Amounts are never stored.** What a request is for is read at the moment it is read: the order's
-- charges (its quoted total, and the storage fee when there is one), or the account month's charges
-- through the statement read `PAYMENT-002` uses. A stored copy would be a second figure that could
-- disagree with the ledger it came from.
--
-- **Personal data.** A buyer's name or email can identify a person, so a request is written only
-- under the published privacy notice (`DEC-034`), whose version it records. Erasing a customer
-- blanks the buyer details of that customer's requests that were never issued, and their profile;
-- an issued request keeps them, under `DEC-008`'s financial schedule. No buyer field is ever a phone
-- number (the pattern the domain's `PHONE_LIKE` refuses), so no export can carry one.
--
-- Forward-only. Rolling the code back leaves four tables the older code never reads; the erasure
-- triggers keep blanking what they blank, which is the privacy-safe direction.

-- 1. The requests.
CREATE TABLE invoice_requests (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES stores (id),
    -- The code the counter and the bookkeeper quote ("YC-0007"): per store, from 1, never reused.
    request_number INTEGER NOT NULL CHECK (request_number >= 1),
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('ORDER', 'ACCOUNT_MONTH')),
    order_id UUID NULL REFERENCES orders (id),
    account_id UUID NULL REFERENCES customer_accounts (id),
    period_month DATE NULL CHECK (period_month IS NULL OR extract(day FROM period_month) = 1),
    -- The customer the subject belongs to, derived by the insert trigger below (never passed):
    -- the order's customer (NULL for a walk-in ticket) or the account's. Erasure keys on it.
    customer_id UUID NULL,
    -- The buyer, as told at the counter. NULL only for the optional fields, or after erasure.
    buyer_unit_name TEXT NULL,
    buyer_tax_code TEXT NULL
        CHECK (buyer_tax_code IS NULL OR buyer_tax_code ~ '^([0-9]{10}(-[0-9]{3})?|[0-9]{12})$'),
    buyer_address TEXT NULL,
    buyer_email TEXT NULL,
    buyer_name TEXT NULL,
    buyer_erased_at TIMESTAMPTZ NULL,
    -- The privacy notice in force when the buyer's details were written (`DEC-034`).
    privacy_notice_version_id UUID NOT NULL REFERENCES configuration_versions (id),
    status TEXT NOT NULL CHECK (status IN ('REQUESTED', 'ISSUED', 'CANCELLED')),
    invoice_symbol TEXT NULL CHECK (invoice_symbol IS NULL OR invoice_symbol ~ '^[0-9A-Z]{1,12}$'),
    invoice_number TEXT NULL CHECK (invoice_number IS NULL OR invoice_number ~ '^[0-9]{1,8}$'),
    invoice_date DATE NULL,
    cancel_reason TEXT NULL CHECK (
        cancel_reason IS NULL
        OR cancel_reason IN ('CUSTOMER_WITHDREW', 'DUPLICATE', 'WRONG_DETAILS', 'OTHER')
    ),
    cancel_note TEXT NULL CHECK (
        cancel_note IS NULL
        OR (
            char_length(cancel_note) BETWEEN 1 AND 200
            AND cancel_note !~ '[[:cntrl:]]'
            AND cancel_note !~ '\+?[0-9]([ .-]?[0-9]){6,}'
        )
    ),
    requested_by UUID NOT NULL REFERENCES staff_users (id),
    requested_at TIMESTAMPTZ NOT NULL,
    closed_by UUID NULL REFERENCES staff_users (id),
    closed_at TIMESTAMPTZ NULL,
    row_version BIGINT NOT NULL CHECK (row_version >= 1),
    UNIQUE (store_id, request_number),
    FOREIGN KEY (customer_id, store_id) REFERENCES customers (id, store_id),
    CONSTRAINT invoice_requests_subject_shape CHECK (
        (subject_kind = 'ORDER' AND order_id IS NOT NULL AND account_id IS NULL
            AND period_month IS NULL)
        OR (subject_kind = 'ACCOUNT_MONTH' AND order_id IS NULL AND account_id IS NOT NULL
            AND period_month IS NOT NULL)
    ),
    -- Every buyer text: its length, no control character, nothing a spreadsheet would execute
    -- (a leading = + - @), and nothing that looks like a phone number.
    CONSTRAINT invoice_requests_buyer_text CHECK (
        (buyer_unit_name IS NULL OR (
            char_length(buyer_unit_name) BETWEEN 1 AND 200
            AND buyer_unit_name !~ '[[:cntrl:]]' AND buyer_unit_name !~ '^[=+@-]'
            AND buyer_unit_name !~ '\+?[0-9]([ .-]?[0-9]){6,}'))
        AND (buyer_address IS NULL OR (
            char_length(buyer_address) BETWEEN 1 AND 300
            AND buyer_address !~ '[[:cntrl:]]' AND buyer_address !~ '^[=+@-]'
            AND buyer_address !~ '\+?[0-9]([ .-]?[0-9]){6,}'))
        AND (buyer_name IS NULL OR (
            char_length(buyer_name) BETWEEN 1 AND 120
            AND buyer_name !~ '[[:cntrl:]]' AND buyer_name !~ '^[=+@-]'
            AND buyer_name !~ '\+?[0-9]([ .-]?[0-9]){6,}'))
        AND (buyer_email IS NULL OR (
            char_length(buyer_email) BETWEEN 3 AND 254
            AND buyer_email ~ '^[^@[:space:][:cntrl:]=+-][^@[:space:][:cntrl:]]*@[^@[:space:][:cntrl:]]+\.[^@[:space:][:cntrl:]]+$'
            AND buyer_email !~ '\+?[0-9]([ .-]?[0-9]){6,}'))
    ),
    -- The buyer is there (a unit name, and an address whenever a tax code is given), or erased.
    CONSTRAINT invoice_requests_buyer_shape CHECK (
        (buyer_erased_at IS NULL AND buyer_unit_name IS NOT NULL
            AND (buyer_tax_code IS NULL OR buyer_address IS NOT NULL))
        OR (buyer_erased_at IS NOT NULL AND buyer_unit_name IS NULL AND buyer_tax_code IS NULL
            AND buyer_address IS NULL AND buyer_email IS NULL AND buyer_name IS NULL)
    ),
    -- Each state carries exactly its own facts.
    CONSTRAINT invoice_requests_state_shape CHECK (
        (status = 'REQUESTED'
            AND invoice_symbol IS NULL AND invoice_number IS NULL AND invoice_date IS NULL
            AND cancel_reason IS NULL AND cancel_note IS NULL
            AND closed_by IS NULL AND closed_at IS NULL)
        OR (status = 'ISSUED'
            AND invoice_symbol IS NOT NULL AND invoice_number IS NOT NULL
            AND invoice_date IS NOT NULL
            AND cancel_reason IS NULL AND cancel_note IS NULL
            AND closed_by IS NOT NULL AND closed_at IS NOT NULL)
        OR (status = 'CANCELLED'
            AND invoice_symbol IS NULL AND invoice_number IS NULL AND invoice_date IS NULL
            AND cancel_reason IS NOT NULL
            AND (cancel_reason <> 'OTHER' OR cancel_note IS NOT NULL OR buyer_erased_at IS NOT NULL)
            AND closed_by IS NOT NULL AND closed_at IS NOT NULL)
    ),
    CONSTRAINT invoice_requests_closed_after_requested CHECK (
        closed_at IS NULL OR closed_at >= requested_at
    )
);

COMMENT ON TABLE invoice_requests IS
    'What question does this answer: "who asked for a hóa đơn, for which order or account month, '
    'with which buyer details, and has the bookkeeper issued it (symbol, number, date)?". '
    'EINVOICE-REQUEST-001 / DEC-040. The software never issues an invoice; amounts are read from the '
    'ledgers at read time, never stored here.';

-- One live request per subject. A cancelled request frees its subject for a new one.
CREATE UNIQUE INDEX invoice_requests_live_order_key
    ON invoice_requests (order_id)
    WHERE subject_kind = 'ORDER' AND status IN ('REQUESTED', 'ISSUED');

CREATE UNIQUE INDEX invoice_requests_live_account_month_key
    ON invoice_requests (account_id, period_month)
    WHERE subject_kind = 'ACCOUNT_MONTH' AND status IN ('REQUESTED', 'ISSUED');

-- One issued request per invoice: the same symbol and number recorded twice is a typing slip.
CREATE UNIQUE INDEX invoice_requests_issued_number_key
    ON invoice_requests (store_id, invoice_symbol, invoice_number)
    WHERE status = 'ISSUED';

CREATE INDEX invoice_requests_store_status_idx
    ON invoice_requests (store_id, status, requested_at, id);

CREATE INDEX invoice_requests_customer_idx
    ON invoice_requests (customer_id)
    WHERE customer_id IS NOT NULL;

CREATE FUNCTION protect_invoice_request() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    subject_store UUID;
    subject_customer UUID;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'an invoice request is cancelled, never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'REQUESTED' OR NEW.row_version <> 1 OR NEW.buyer_erased_at IS NOT NULL THEN
            RAISE EXCEPTION 'an invoice request starts REQUESTED at version 1, with its buyer';
        END IF;
        -- The subject is in the request's store, and the customer is the subject's own.
        IF NEW.subject_kind = 'ORDER' THEN
            SELECT o.store_id, o.customer_id INTO subject_store, subject_customer
              FROM orders o WHERE o.id = NEW.order_id;
        ELSE
            SELECT a.store_id, a.customer_id INTO subject_store, subject_customer
              FROM customer_accounts a WHERE a.id = NEW.account_id;
        END IF;
        IF subject_store IS DISTINCT FROM NEW.store_id THEN
            RAISE EXCEPTION 'an invoice request names a subject of its own store';
        END IF;
        NEW.customer_id := subject_customer;
        RETURN NEW;
    END IF;
    -- UPDATE.
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.request_number IS DISTINCT FROM OLD.request_number
       OR NEW.subject_kind IS DISTINCT FROM OLD.subject_kind
       OR NEW.order_id IS DISTINCT FROM OLD.order_id
       OR NEW.account_id IS DISTINCT FROM OLD.account_id
       OR NEW.period_month IS DISTINCT FROM OLD.period_month
       OR NEW.customer_id IS DISTINCT FROM OLD.customer_id
       OR NEW.privacy_notice_version_id IS DISTINCT FROM OLD.privacy_notice_version_id
       OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
       OR NEW.requested_at IS DISTINCT FROM OLD.requested_at THEN
        RAISE EXCEPTION 'an invoice request''s subject and origin are immutable';
    END IF;
    IF OLD.status = 'ISSUED' THEN
        RAISE EXCEPTION 'INVOICE_REQUEST_CLOSED: an issued invoice request is immutable';
    END IF;
    IF NEW.row_version <> OLD.row_version + 1 THEN
        RAISE EXCEPTION 'STALE_VERSION: invoice request row_version must advance by one';
    END IF;
    IF (NEW.buyer_unit_name, NEW.buyer_tax_code, NEW.buyer_address, NEW.buyer_email, NEW.buyer_name,
        NEW.buyer_erased_at)
       IS DISTINCT FROM
       (OLD.buyer_unit_name, OLD.buyer_tax_code, OLD.buyer_address, OLD.buyer_email, OLD.buyer_name,
        OLD.buyer_erased_at) THEN
        -- The one change to a buyer this table admits: erasure, which blanks all of it at once
        -- (the shape CHECK makes a partial blanking unstorable) and leaves the state alone.
        IF OLD.buyer_erased_at IS NOT NULL OR NEW.buyer_erased_at IS NULL
           OR NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'a buyer''s details are only ever erased, and never with a state change';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'CANCELLED' THEN
        RAISE EXCEPTION 'INVOICE_REQUEST_CLOSED: a cancelled invoice request is closed';
    END IF;
    IF NEW.status NOT IN ('ISSUED', 'CANCELLED') THEN
        RAISE EXCEPTION 'an open invoice request moves only to ISSUED or CANCELLED';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER invoice_requests_guard
    BEFORE INSERT OR UPDATE OR DELETE ON invoice_requests
    FOR EACH ROW EXECUTE FUNCTION protect_invoice_request();

-- 2. An account customer's buyer details, for next time. Written only by an explicit tick.
CREATE TABLE customer_invoice_profiles (
    customer_id UUID PRIMARY KEY,
    store_id UUID NOT NULL,
    buyer_unit_name TEXT NULL,
    buyer_tax_code TEXT NULL
        CHECK (buyer_tax_code IS NULL OR buyer_tax_code ~ '^([0-9]{10}(-[0-9]{3})?|[0-9]{12})$'),
    buyer_address TEXT NULL,
    buyer_email TEXT NULL,
    buyer_name TEXT NULL,
    erased_at TIMESTAMPTZ NULL,
    updated_by UUID NOT NULL REFERENCES staff_users (id),
    updated_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL CHECK (row_version >= 1),
    FOREIGN KEY (customer_id, store_id) REFERENCES customers (id, store_id),
    CONSTRAINT customer_invoice_profiles_text CHECK (
        (buyer_unit_name IS NULL OR (
            char_length(buyer_unit_name) BETWEEN 1 AND 200
            AND buyer_unit_name !~ '[[:cntrl:]]' AND buyer_unit_name !~ '^[=+@-]'
            AND buyer_unit_name !~ '\+?[0-9]([ .-]?[0-9]){6,}'))
        AND (buyer_address IS NULL OR (
            char_length(buyer_address) BETWEEN 1 AND 300
            AND buyer_address !~ '[[:cntrl:]]' AND buyer_address !~ '^[=+@-]'
            AND buyer_address !~ '\+?[0-9]([ .-]?[0-9]){6,}'))
        AND (buyer_name IS NULL OR (
            char_length(buyer_name) BETWEEN 1 AND 120
            AND buyer_name !~ '[[:cntrl:]]' AND buyer_name !~ '^[=+@-]'
            AND buyer_name !~ '\+?[0-9]([ .-]?[0-9]){6,}'))
        AND (buyer_email IS NULL OR (
            char_length(buyer_email) BETWEEN 3 AND 254
            AND buyer_email ~ '^[^@[:space:][:cntrl:]=+-][^@[:space:][:cntrl:]]*@[^@[:space:][:cntrl:]]+\.[^@[:space:][:cntrl:]]+$'
            AND buyer_email !~ '\+?[0-9]([ .-]?[0-9]){6,}'))
    ),
    CONSTRAINT customer_invoice_profiles_erased CHECK (
        erased_at IS NULL
        OR (buyer_unit_name IS NULL AND buyer_tax_code IS NULL AND buyer_address IS NULL
            AND buyer_email IS NULL AND buyer_name IS NULL)
    )
);

COMMENT ON TABLE customer_invoice_profiles IS
    'What question does this answer: "which buyer details does this account customer want on their '
    'hóa đơn?". Saved only by an explicit "lưu cho lần sau" on a request (DEC-040); blanked when the '
    'customer is erased.';

CREATE FUNCTION protect_customer_invoice_profile() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'an invoice profile is blanked, never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (
            SELECT 1 FROM customer_accounts a
            JOIN customers c ON c.id = a.customer_id AND c.store_id = a.store_id
            WHERE a.customer_id = NEW.customer_id AND a.store_id = NEW.store_id
              AND c.erased_at IS NULL
        ) THEN
            RAISE EXCEPTION 'only an account customer on record keeps an invoice profile';
        END IF;
        IF NEW.row_version <> 1 OR NEW.erased_at IS NOT NULL THEN
            RAISE EXCEPTION 'an invoice profile starts at version 1';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.customer_id IS DISTINCT FROM OLD.customer_id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id THEN
        RAISE EXCEPTION 'an invoice profile belongs to its customer';
    END IF;
    IF OLD.erased_at IS NOT NULL THEN
        RAISE EXCEPTION 'an erased invoice profile is final';
    END IF;
    IF NEW.row_version <> OLD.row_version + 1 THEN
        RAISE EXCEPTION 'STALE_VERSION: invoice profile row_version must advance by one';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER customer_invoice_profiles_guard
    BEFORE INSERT OR UPDATE OR DELETE ON customer_invoice_profiles
    FOR EACH ROW EXECUTE FUNCTION protect_customer_invoice_profile();

-- 3. Erasure, whichever path erases (a staff request or retention): the moment a customer's
-- `erased_at` is set, the buyer details of their requests that were never issued are blanked, with
-- any cancellation note, and their profile is blanked. Issued requests keep theirs (`DEC-008`).
-- In the erasure's own transaction, so a customer is never erased while a copy of them survives.
CREATE FUNCTION erase_customer_invoice_details() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.erased_at IS NULL AND NEW.erased_at IS NOT NULL THEN
        UPDATE invoice_requests
           SET buyer_unit_name = NULL, buyer_tax_code = NULL, buyer_address = NULL,
               buyer_email = NULL, buyer_name = NULL, cancel_note = NULL,
               buyer_erased_at = NEW.erased_at, row_version = row_version + 1
         WHERE customer_id = NEW.id AND store_id = NEW.store_id
           AND status IN ('REQUESTED', 'CANCELLED') AND buyer_erased_at IS NULL;
        UPDATE customer_invoice_profiles
           SET buyer_unit_name = NULL, buyer_tax_code = NULL, buyer_address = NULL,
               buyer_email = NULL, buyer_name = NULL, erased_at = NEW.erased_at,
               updated_at = greatest(updated_at, NEW.erased_at), row_version = row_version + 1
         WHERE customer_id = NEW.id AND erased_at IS NULL;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER customers_erase_invoice_details
    AFTER UPDATE OF erased_at ON customers
    FOR EACH ROW EXECUTE FUNCTION erase_customer_invoice_details();

-- 4. Every download of the open list: who took it, when, how much, the digest of the exact bytes
-- and the query version. The file itself is never stored.
CREATE TABLE invoice_request_exports (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES stores (id),
    query_version TEXT NOT NULL CHECK (char_length(query_version) BETWEEN 1 AND 200),
    request_count INTEGER NOT NULL CHECK (request_count >= 0),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    truncated BOOLEAN NOT NULL,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^sha256:[0-9a-f]{64}$'),
    produced_by UUID NOT NULL REFERENCES staff_users (id),
    produced_at TIMESTAMPTZ NOT NULL
);

COMMENT ON TABLE invoice_request_exports IS
    'What question does this answer: "who downloaded the invoice requests for the bookkeeper, when, '
    'and exactly which file?". Append-only (DEC-040). The bytes are not kept.';

CREATE INDEX invoice_request_exports_store_idx ON invoice_request_exports (store_id, produced_at);

CREATE TRIGGER invoice_request_exports_append_only
    BEFORE UPDATE OR DELETE ON invoice_request_exports
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
