-- EXPORT-RANGE-001 (FR-RPT-003): an export names a window of the shop's days, not only one day.
--
-- Unavoidable, and additive only. The window has to be stored on the request row itself: the
-- request is immutable (`export_requests_append_only`), the approval binds its facts, and the
-- release re-derives both digests from those facts. A window that lived anywhere else -- in the
-- idempotency record, in the envelope, in the console -- would be a window nobody's approval
-- actually covered.
--
-- `business_date` keeps its meaning and becomes the window's FIRST day. `business_date_to` is the
-- LAST day, inclusive, and is NULL for a one-day export. That NULL is deliberate rather than lazy:
--
--   * Every row written before this migration is a one-day export and has no second date. Leaving
--     it NULL means no existing row is touched (the append-only trigger would refuse an UPDATE
--     anyway, and a backfill here would need to disable it), and its digests re-derive exactly as
--     they did, so an envelope already raised for one keeps releasing.
--   * A one-day export written after this migration takes the same shape, so there is one way to
--     say "one day" and the one-day binding is byte-for-byte what it was. `business_date_to` equal
--     to `business_date` is refused below rather than admitted as a second spelling of it.
--
-- The 92-day bound is the spec's (COUNTER_COMPLETENESS_SPEC_V1 §3.6: "a window [from, to] of at
-- most 92 days"), checked in `exports.export_window` before anything is written and repeated here
-- so no path that bypasses the repository can store a longer one. `to - from <= 91` is 92 days
-- counted inclusively.
--
-- Adding a nullable column with no default rewrites nothing and fires no row trigger, so the
-- append-only guarantee on existing rows is untouched. Forward-only: there is no down migration;
-- rolling the code back leaves a column the older code never reads, and any window request
-- written meanwhile simply cannot be released by that older code (its re-derived one-day digest
-- would not match the window digest the owner approved), which is the fail-closed direction.
ALTER TABLE export_requests
    ADD COLUMN business_date_to DATE;

ALTER TABLE export_requests
    ADD CONSTRAINT export_requests_window_shape CHECK (
        business_date_to IS NULL
        OR (business_date_to > business_date AND business_date_to - business_date <= 91)
    );

COMMENT ON COLUMN export_requests.business_date_to IS
    'Last shop-local day of the exported window, inclusive (EXPORT-RANGE-001). NULL for a one-day '
    'export, which is every row written before 0054: business_date is then both first and last day.';
