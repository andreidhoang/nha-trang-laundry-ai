-- A store is a row, not a UUID somebody typed.
--
-- Sixteen migrations before this one reference `store_id`, across fourteen tables, and none of them
-- creates a `stores` table or a foreign key to one. `scripts/seed_demo_data.py` says so in its own
-- header: "The store is a bare UUID because there is no `stores` table."
--
-- Everything this system does to keep one shop's work away from another rests on that identifier.
-- `staff_store_assignments` grants membership of it, `require_store_membership` checks it, `0034`
-- binds approvals to it, the shadow audit timeline resolves ownership through it, and
-- `AGREEMENT-INTEGRITY-001` scoped the reprice UPDATE by it. Every one of those compares an
-- unvalidated value against another unvalidated value.
--
-- Two consequences, and the second is the one that blocks a deployment:
--
--   1. A typo creates a store. Assigning a staff member to a mistyped identifier succeeds, and that
--      member is then a member of a store that does not exist -- refused everywhere, shown nothing,
--      with no error that says why.
--   2. There is no production path to create a store at all. The only code that mints one is
--      `seed_demo_data.py`, whose `require_local_database()` refuses any DSN whose host is not
--      local. A production deployment could bring its first store into existence only by inventing
--      a UUID and pasting it into the console.
--
-- **`name` is nullable on purpose.** A backfilled store has an identifier and no name, because
-- nobody has told this system what the shop is called. Defaulting one would write a fact the
-- business never stated, which is the same objection `0034` records against defaulting a store for
-- an orphaned approval. `scripts/bootstrap_store.py` names a store when a person names it.

CREATE TABLE stores (
    id UUID PRIMARY KEY,
    name TEXT NULL CHECK (name IS NULL OR length(btrim(name)) BETWEEN 1 AND 200),
    created_at TIMESTAMPTZ NOT NULL,
    created_by_staff_id UUID NULL REFERENCES staff_users (id),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1)
);

CREATE TRIGGER stores_no_hard_delete
    BEFORE DELETE ON stores
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

-- Backfill from every table that already claims to belong to a store. `created_at` is the earliest
-- moment that store is known to have existed, which is a fact these rows carry; it is not invented.
INSERT INTO stores (id, name, created_at, created_by_staff_id, row_version)
SELECT store_id, NULL, min(first_seen), NULL, 1
FROM (
    SELECT store_id, min(created_at) AS first_seen FROM orders GROUP BY store_id
    UNION ALL SELECT store_id, min(created_at) FROM quotes GROUP BY store_id
    UNION ALL SELECT store_id, min(created_at) FROM order_requests GROUP BY store_id
    UNION ALL SELECT store_id, min(opened_at) FROM customer_incidents GROUP BY store_id
    UNION ALL SELECT store_id, min(issued_at) FROM counter_tickets GROUP BY store_id
    UNION ALL SELECT store_id, min(created_at) FROM order_settlements GROUP BY store_id
    UNION ALL SELECT store_id, min(recorded_at) FROM delivery_legs GROUP BY store_id
    UNION ALL SELECT store_id, min(accepted_at) FROM quote_acceptances GROUP BY store_id
    UNION ALL SELECT store_id, min(created_at) FROM agent_runs GROUP BY store_id
    UNION ALL SELECT store_id, min(produced_at) FROM agent_drafts GROUP BY store_id
    UNION ALL SELECT store_id, min(decided_at) FROM agent_draft_reviews GROUP BY store_id
    UNION ALL SELECT store_id, min(created_at) FROM assistant_turns GROUP BY store_id
    UNION ALL SELECT store_id, min(requested_at) FROM approval_requests GROUP BY store_id
    UNION ALL SELECT store_id, min(assigned_at) FROM staff_store_assignments GROUP BY store_id
) AS claimed
GROUP BY store_id
ON CONFLICT (id) DO NOTHING;

-- Now the identifier means something. Every table that says "this belongs to a store" points at a
-- store that exists, and PostgreSQL refuses the typo the application never could.
ALTER TABLE staff_store_assignments ADD CONSTRAINT staff_store_assignments_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE orders ADD CONSTRAINT orders_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE quotes ADD CONSTRAINT quotes_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE order_requests ADD CONSTRAINT order_requests_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE customer_incidents ADD CONSTRAINT customer_incidents_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE counter_tickets ADD CONSTRAINT counter_tickets_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE order_settlements ADD CONSTRAINT order_settlements_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE delivery_legs ADD CONSTRAINT delivery_legs_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE quote_acceptances ADD CONSTRAINT quote_acceptances_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE agent_drafts ADD CONSTRAINT agent_drafts_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE agent_draft_reviews ADD CONSTRAINT agent_draft_reviews_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE assistant_turns ADD CONSTRAINT assistant_turns_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
ALTER TABLE approval_requests ADD CONSTRAINT approval_requests_store_fk
    FOREIGN KEY (store_id) REFERENCES stores (id);
