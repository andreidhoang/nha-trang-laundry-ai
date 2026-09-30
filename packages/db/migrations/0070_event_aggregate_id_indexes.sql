-- OPS-OBSERVABILITY-009 (review finding P7): index the order history read on `aggregate_id`.
--
-- The order history (`ShadowConsoleRepository.audit_timeline`, `AUDIT_TIMELINE_SQL`) filters
-- `audit_events` on `aggregate_id` alone, and for each transition row reads its domain event by
-- `aggregate_id`, `correlation_id` and `occurred_at`. The only indexes on either table (`0001`) lead
-- with `aggregate_type`, which neither read names, so PostgreSQL cannot use them and scans the whole
-- table: both are append-only ledgers that grow with every write the shop makes, so every opening
-- of an order's page got slower for ever. `test_event_aggregate_indexes.py` EXPLAINs the exact
-- statement over thousands of rows and requires these two indexes in the plan.
--
-- 1. `audit_events (aggregate_id, occurred_at, id)`: the filter, then the timeline's own
--    `ORDER BY a.occurred_at, a.id` -- so the LIMIT stops reading instead of sorting.
-- 2. `domain_events (aggregate_id, correlation_id, occurred_at)`: all three equalities of the
--    lateral join, so each transition's event is one index probe.
--
-- **Not CONCURRENTLY, and that is deliberate.** `apply_migrations` runs every file inside its own
-- transaction (with `lock_timeout` bounded), and `CREATE INDEX CONCURRENTLY` cannot run in one. A
-- plain `CREATE INDEX` holds a SHARE lock on the table -- reads continue, writes to these two tables
-- wait -- for as long as the build takes. At pilot size (one shop, tens of thousands of rows) that
-- is well under a second, measured on the gate database in the OPS-OBSERVABILITY-009 report. A
-- deployment large enough for that to matter should build the index CONCURRENTLY by hand first;
-- `IF NOT EXISTS` then makes this migration a no-op there rather than an error.
--
-- Additive only: no row, column or constraint changes. Forward-only; rolling the code back leaves two
-- indexes the older code's planner is free to use and the writers maintain.

CREATE INDEX IF NOT EXISTS audit_events_aggregate_id_idx
    ON audit_events (aggregate_id, occurred_at, id);

CREATE INDEX IF NOT EXISTS domain_events_aggregate_id_idx
    ON domain_events (aggregate_id, correlation_id, occurred_at);
