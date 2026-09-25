-- CUSTOMER-001 (DEC-034, reopens DEC-015): the shop's customer list, with consent.
--
-- Additive only. Two new tables and one nullable column on each of `orders` and `order_requests`;
-- no existing row is rewritten, no existing trigger is replaced, and every order and intake written
-- before this migration keeps a NULL customer -- a walk-in stays a ticket (DEC-013).
--
-- The phone number is stored three ways and never in clear:
--   * `phone_ciphertext` -- AES-256-GCM under a key derived from the deployment hash key
--     (`nha_trang_laundry_db.personal_data`), bound to the row by its associated data;
--   * `phone_digest`     -- keyed HMAC of the normalised +84 number, for exact search and for
--     uniqueness per store (`PHONE-HMAC-V1:` + 64 hex);
--   * `phone_last4`      -- the last four digits, for the counter's last-4 search.
--
-- Erasure (a customer's request, or 24 months with no order) nulls every personal column, sets
-- `erased_at`, and keeps the row, so orders keep their foreign key and money keeps its history.
-- `customers_personal_shape` makes a half-erased row unstorable; `customers_erasure_is_final` makes
-- an erased row unwritable. Neither the application roles nor anyone else may DELETE a row.
--
-- Consent evidence (who ticked, when, under which published notice version) is not personal data
-- about the customer's life; it is the shop's proof of the basis it kept the data on, and it
-- survives erasure.
--
-- Forward-only. Rolling the code back leaves two tables and two NULL-able columns the older code
-- never reads; orders written meanwhile keep working because nothing older joins through them.

CREATE TABLE customers (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES stores (id),
    phone_ciphertext BYTEA,
    phone_digest TEXT,
    phone_last4 TEXT,
    display_name TEXT,
    -- Case- and diacritic-folded name, for search only; personal, so erased with the rest.
    name_folded TEXT,
    delivery_address TEXT,
    note TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('RETAIL', 'BUSINESS')),
    service_consent_notice_id UUID NOT NULL REFERENCES configuration_versions (id),
    service_consent_notice_version INTEGER NOT NULL CHECK (service_consent_notice_version > 0),
    service_consent_by UUID NOT NULL REFERENCES staff_users (id),
    service_consent_at TIMESTAMPTZ NOT NULL,
    marketing_consent_at TIMESTAMPTZ,
    marketing_consent_by UUID REFERENCES staff_users (id),
    marketing_withdrawn_at TIMESTAMPTZ,
    last_activity_at TIMESTAMPTZ NOT NULL,
    erased_at TIMESTAMPTZ,
    erased_by UUID REFERENCES staff_users (id),
    erasure_reason TEXT CHECK (erasure_reason IN ('CUSTOMER_REQUEST', 'RETENTION')),
    row_version INTEGER NOT NULL CHECK (row_version >= 1),
    created_by UUID NOT NULL REFERENCES staff_users (id),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    -- The composite key the orders, intakes and links reference, so a customer of one store can
    -- never be attached to another store's order by a foreign key that is merely valid.
    UNIQUE (id, store_id),
    CONSTRAINT customers_personal_shape CHECK (
        (
            erased_at IS NULL
            AND erased_by IS NULL
            AND erasure_reason IS NULL
            AND phone_ciphertext IS NOT NULL
            AND octet_length(phone_ciphertext) > 28
            AND phone_digest IS NOT NULL
            AND phone_last4 IS NOT NULL
        )
        OR (
            erased_at IS NOT NULL
            AND erasure_reason IS NOT NULL
            AND phone_ciphertext IS NULL
            AND phone_digest IS NULL
            AND phone_last4 IS NULL
            AND display_name IS NULL
            AND name_folded IS NULL
            AND delivery_address IS NULL
            AND note IS NULL
        )
    ),
    CONSTRAINT customers_phone_digest_shape CHECK (
        phone_digest IS NULL OR phone_digest ~ '^PHONE-HMAC-V1:[0-9a-f]{64}$'
    ),
    CONSTRAINT customers_phone_last4_shape CHECK (
        phone_last4 IS NULL OR phone_last4 ~ '^[0-9]{4}$'
    ),
    CONSTRAINT customers_name_shape CHECK (
        (display_name IS NULL) = (name_folded IS NULL)
        AND (display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 80)
    ),
    CONSTRAINT customers_note_shape CHECK (note IS NULL OR char_length(note) BETWEEN 1 AND 200),
    CONSTRAINT customers_address_shape CHECK (
        delivery_address IS NULL OR char_length(delivery_address) BETWEEN 1 AND 300
    ),
    CONSTRAINT customers_marketing_shape CHECK (
        (marketing_consent_at IS NULL) = (marketing_consent_by IS NULL)
        AND (marketing_withdrawn_at IS NULL OR marketing_consent_at IS NOT NULL)
    )
);

-- One record per number per store. Erased rows carry no digest and drop out of the index, so the
-- same person may be recorded again after asking to be forgotten.
CREATE UNIQUE INDEX customers_store_phone_digest_key
    ON customers (store_id, phone_digest)
    WHERE phone_digest IS NOT NULL;

CREATE INDEX customers_store_last4_idx
    ON customers (store_id, phone_last4, last_activity_at DESC, id DESC)
    WHERE erased_at IS NULL;

CREATE INDEX customers_store_activity_idx
    ON customers (store_id, last_activity_at DESC, id DESC)
    WHERE erased_at IS NULL;

CREATE TRIGGER customers_no_hard_delete
    BEFORE DELETE ON customers
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

CREATE FUNCTION protect_customer_record() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.erased_at IS NOT NULL THEN
        RAISE EXCEPTION 'CUSTOMER_ERASED: an erased customer record is final';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.service_consent_notice_id IS DISTINCT FROM OLD.service_consent_notice_id
       OR NEW.service_consent_notice_version IS DISTINCT FROM OLD.service_consent_notice_version
       OR NEW.service_consent_by IS DISTINCT FROM OLD.service_consent_by
       OR NEW.service_consent_at IS DISTINCT FROM OLD.service_consent_at THEN
        RAISE EXCEPTION 'customer identity and service consent evidence are immutable';
    END IF;
    IF NEW.last_activity_at < OLD.last_activity_at THEN
        RAISE EXCEPTION 'a customer''s last activity never moves backwards';
    END IF;
    IF NEW.row_version = OLD.row_version THEN
        -- A touch: an intake opened for the customer moves `last_activity_at` and nothing else,
        -- so a staff member editing the record at the same moment is not told it went stale.
        IF (
            NEW.phone_ciphertext, NEW.phone_digest, NEW.phone_last4, NEW.display_name,
            NEW.name_folded, NEW.delivery_address, NEW.note, NEW.kind, NEW.marketing_consent_at,
            NEW.marketing_consent_by, NEW.marketing_withdrawn_at, NEW.erased_at, NEW.erased_by,
            NEW.erasure_reason
        ) IS DISTINCT FROM (
            OLD.phone_ciphertext, OLD.phone_digest, OLD.phone_last4, OLD.display_name,
            OLD.name_folded, OLD.delivery_address, OLD.note, OLD.kind, OLD.marketing_consent_at,
            OLD.marketing_consent_by, OLD.marketing_withdrawn_at, OLD.erased_at, OLD.erased_by,
            OLD.erasure_reason
        ) THEN
            RAISE EXCEPTION 'STALE_VERSION: a change to a customer record advances row_version';
        END IF;
    ELSIF NEW.row_version <> OLD.row_version + 1 THEN
        RAISE EXCEPTION 'STALE_VERSION: customer row_version must advance by one';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER customers_erasure_is_final
    BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION protect_customer_record();

-- A counter ticket or a channel binding a staff member attached to a customer, so the customer's
-- page shows the orders made under it before the record existed. One customer per reference.
CREATE TABLE customer_links (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL,
    store_id UUID NOT NULL,
    link_kind TEXT NOT NULL CHECK (link_kind IN ('COUNTER_TICKET', 'CHANNEL_BINDING')),
    ref_id UUID NOT NULL,
    linked_by UUID NOT NULL REFERENCES staff_users (id),
    linked_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (customer_id, store_id) REFERENCES customers (id, store_id),
    UNIQUE (link_kind, ref_id)
);

CREATE INDEX customer_links_customer_idx ON customer_links (customer_id, linked_at);

CREATE FUNCTION reject_customer_link_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'customer links are append-only';
END;
$$;

CREATE TRIGGER customer_links_append_only
    BEFORE UPDATE OR DELETE ON customer_links
    FOR EACH ROW EXECUTE FUNCTION reject_customer_link_mutation();

-- The customer an intake was opened for, and the customer the order inherited from it. Both NULL
-- for a walk-in ticket or a channel binding with no record. Same-store by the composite key.
ALTER TABLE order_requests ADD COLUMN customer_id UUID;
ALTER TABLE order_requests
    ADD CONSTRAINT order_requests_customer_fk
    FOREIGN KEY (customer_id, store_id) REFERENCES customers (id, store_id);

ALTER TABLE orders ADD COLUMN customer_id UUID;
ALTER TABLE orders
    ADD CONSTRAINT orders_customer_fk
    FOREIGN KEY (customer_id, store_id) REFERENCES customers (id, store_id);

CREATE INDEX order_requests_customer_idx
    ON order_requests (customer_id, created_at DESC)
    WHERE customer_id IS NOT NULL;

CREATE INDEX orders_customer_idx
    ON orders (customer_id, created_at DESC, id)
    WHERE customer_id IS NOT NULL;

-- The order inherits the customer its intake was opened for, in the database, whichever path
-- writes the order: through the quote's own request, and only within the order's store. Derived
-- here rather than passed by the repository, so no code path can create an order for an intake's
-- customer and forget to say so -- and no caller can name a different one.
CREATE FUNCTION inherit_order_customer() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    SELECT req.customer_id INTO NEW.customer_id
    FROM quotes q
    JOIN order_requests req ON req.id = q.bound_order_request_id
    WHERE q.id = NEW.current_quote_id AND req.store_id = NEW.store_id;
    RETURN NEW;
END;
$$;

CREATE TRIGGER orders_inherit_customer
    BEFORE INSERT ON orders
    FOR EACH ROW EXECUTE FUNCTION inherit_order_customer();

-- An order's customer is set when the order is written and never moved: history is attached to a
-- later record through `customer_links`, not by rewriting whose order it was. A separate trigger,
-- so the existing `orders` guards are not replaced.
CREATE FUNCTION protect_order_customer() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.customer_id IS DISTINCT FROM OLD.customer_id THEN
        RAISE EXCEPTION 'an order''s customer is fixed when the order is created';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER orders_customer_immutable
    BEFORE UPDATE OF customer_id ON orders
    FOR EACH ROW EXECUTE FUNCTION protect_order_customer();

CREATE TRIGGER order_requests_customer_immutable
    BEFORE UPDATE OF customer_id ON order_requests
    FOR EACH ROW EXECUTE FUNCTION protect_order_customer();

COMMENT ON TABLE customers IS
    'DEC-034 customer records. Phone stored encrypted + keyed digest + last4; erasure nulls every '
    'personal column and keeps the row so orders keep their key.';
COMMENT ON COLUMN orders.customer_id IS
    'CUSTOMER-001: the customer record the order was taken for (inherited from its intake). NULL '
    'for a walk-in ticket or channel binding with no record. Immutable.';
