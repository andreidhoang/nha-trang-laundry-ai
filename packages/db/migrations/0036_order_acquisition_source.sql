-- ACQUISITION-ATTRIBUTION-001: an order records where the customer came from, once, forever.
--
-- `ACQUISITION-001` section 3.3 specified this field on 2026-08-18 and correctly deferred it: the
-- `orders` table could not receive a row at all, because no customer reference could be created.
-- `DEC-013` and `DEC-015` resolved on 2026-08-26 and `0032_counter_ticket.sql` shipped the counter
-- identity, so orders exist and attribution over them means something. This is that slice and
-- nothing else from that packet.
--
-- **Attested, never inferred.** The value is what a staff member says the customer said. No model
-- writes it, no heuristic derives it, and nothing reachable by the tool facade can read it. In
-- particular `RETURNING` is a claim rather than a fact: `DEC-015` resolved that no customer-record
-- layer exists, so two orders placed by one person share nothing this database can compare.
--
-- `UNKNOWN` is a first-class value, not a placeholder for a missing one. A counter that did not ask
-- must be able to record that it did not ask; the alternative is a field filled with whatever gets
-- staff past the form, and a channel report built on that is worse than none because it will be
-- believed and spent against.

ALTER TABLE orders ADD COLUMN acquisition_source TEXT NOT NULL DEFAULT 'UNKNOWN'
    CHECK (acquisition_source IN (
        'WALK_IN', 'GOOGLE_MAPS', 'ZALO', 'FACEBOOK', 'PARTNER_FRONT_DESK',
        'REFERRAL_CUSTOMER', 'LEAFLET_QR', 'RETURNING', 'UNKNOWN'
    ));

-- The default existed only to give already-written rows a truthful value -- an order taken before
-- this column existed genuinely has no recorded source, and `UNKNOWN` is exactly what that is.
-- It is dropped immediately so that it can never stand in for an answer nobody gave: from here an
-- INSERT that omits the column fails loudly rather than quietly recording ignorance as if it had
-- been asked for.
ALTER TABLE orders ALTER COLUMN acquisition_source DROP DEFAULT;

-- Attribution is one of the few facts here that cannot be corrected later, because the only source
-- of truth for it walked out of the shop. Nobody can be asked in November how they found the place
-- in September, and section 4 of the packet forbids a model guessing. So it is immutable in the
-- same way and in the same place as `store_id`, `bound_contact_id` and `created_at`: in the trigger
-- that already guards them, rather than in a Python convention a future caller can forget.
CREATE OR REPLACE FUNCTION enforce_order_projection_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.bound_contact_id IS DISTINCT FROM OLD.bound_contact_id
       OR NEW.acquisition_source IS DISTINCT FROM OLD.acquisition_source
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.row_version <> OLD.row_version + 1
       OR OLD.commercial_status IN ('CANCELLED', 'COMPLETED')
       OR (OLD.commercial_status = 'ACTIVE' AND NEW.commercial_status = 'CANCELLED') THEN
        RAISE EXCEPTION 'invalid order projection update';
    END IF;
    RETURN NEW;
END;
$$;

-- The channel report reads every order of a store in a date range and groups by source. Without
-- this it is a sequential scan of the whole order history each time the owner asks a question they
-- should be asking weekly.
CREATE INDEX orders_store_source_created_idx
    ON orders (store_id, acquisition_source, created_at);
