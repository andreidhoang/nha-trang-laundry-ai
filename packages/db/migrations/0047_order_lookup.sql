-- ORDER-LOOKUP-001: let the counter find an order that is not among the newest hundred.
--
-- The order board read `WHERE store_id = $1 ORDER BY created_at DESC, id LIMIT $2`, and no index
-- had that shape: `orders_store_commercial_idx (store_id, commercial_status, created_at)` leads with
-- the status, so the planner read every order the store had ever taken and sorted them. The board
-- now also answers two narrower questions, and each gets the index its statement actually uses.
-- Indexes only: no column, no data and no constraint changes, so rolling forward is additive and
-- rolling back is three DROP INDEX statements with no data consequence.

-- 1. The board: newest first. `created_at DESC, id` is exactly the ORDER BY, so one index serves the
--    filter, the order and the limit.
CREATE INDEX orders_store_created_idx
    ON orders (store_id, created_at DESC, id);

-- 2. Open orders: everything not yet COMPLETED or CANCELLED, whatever its age. An order that is still
--    being washed must never fall off the board because thirty newer ones arrived, and the open set
--    is a small, bounded fraction of a store's history, so the index is partial. The predicate is
--    spelled word for word as `orders.OPEN_ORDERS_SQL_PREDICATE`; if they drift, the planner stops
--    using this index silently, which `test_order_lookup.py` reads out of EXPLAIN.
CREATE INDEX orders_store_open_idx
    ON orders (store_id, created_at DESC, id)
    WHERE commercial_status NOT IN ('CANCELLED', 'COMPLETED');

-- 3. Finding an order by the walk-in ticket it is tracked by (DEC-013). The ticket side is already
--    served by `counter_tickets (store_id, issued_on, ticket_number)`; this is the other half of
--    the join, from a ticket id to the orders that name it.
CREATE INDEX orders_store_bound_contact_idx
    ON orders (store_id, bound_contact_id);
