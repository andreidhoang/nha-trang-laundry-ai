-- Give the review log a keyset index, so the decisions a human made about agent drafts can be
-- read back a page at a time.
--
-- `agent_draft_reviews_store_idx` was `(store_id, decided_at DESC)`. That serves "the most recent
-- decisions" and stops there: it carries no tiebreaker, so a query ordering `decided_at DESC,
-- review_id` -- which is what a stable cursor requires -- has to sort whatever shares a timestamp.
-- Two reviews sharing one is vanishingly rare in practice and catastrophic when it happens, because
-- an unstable order across two pages either repeats a decision or drops one, and this is the log
-- somebody grades the agent from.
--
-- Replaced rather than added, because the new index has the old one's columns as its leading pair:
-- every read the old index served, the new one serves identically, so leaving both would be two
-- indexes maintained for one access pattern.
--
-- Shipping it now rather than when the table has volume is the lesson
-- `STAFF_CONSOLE_COMPLETION_PROGRAM_V1.md` records as P5. The order board is the counter-example
-- there: its index is `(store_id, commercial_status, created_at)` while the query orders by
-- `created_at DESC, id`, so the filter column sits between the two columns the query needs and
-- Postgres must sort. That index is now expensive to correct. This one is not, yet.
--
-- `apply_migrations.py` runs as the migration identity, which owns the schema it created -- `app`
-- locally, the migration role in the container demo -- so the drop is within its rights in both.

DROP INDEX IF EXISTS agent_draft_reviews_store_idx;

CREATE INDEX agent_draft_reviews_store_idx
    ON agent_draft_reviews (store_id, decided_at DESC, review_id);
