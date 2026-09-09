-- The production clock stops when the laundry is finished, not when someone looks at the board.
--
-- `ENGINEERING_SPEC_V1.md:256` says `ready_at_store` stops the production clock, and
-- `evaluate_production_sla` takes it: `comparison_at = ready_at_store or evaluated_at`, and
-- `SlaOutcome.MET` is reachable only when it is supplied. No column held it, so no caller could
-- supply one, so the SLA risk board compared every order against `now` -- and its query keeps
-- orders in the population until they are physically RELEASED.
--
-- For this shop that is not a rounding error, it is the whole reading. A customer who collects the
-- next morning leaves a washed, finished, waiting order accruing elapsed time overnight, so it
-- reports SLA_BREACHED, and MET can never be reported by that surface at all. The assistant tells
-- the owner "N đơn đang sản xuất, trong đó M đơn đã quá mốc rủi ro" every morning with M climbing,
-- which is how a real alert gets trained out of somebody.
--
-- Nullable and write-once by convention: it is the first moment production reported the work
-- finished. A later ON_HOLD and resume does not reset it, because the question it answers is when
-- the laundry was done, not how many times the board was touched.
ALTER TABLE orders ADD COLUMN production_ready_at TIMESTAMPTZ NULL;

-- Already-finished orders keep a NULL: no timestamp for them exists and inventing one -- the row's
-- creation, the settlement, `now` -- would be a measurement nobody took. They read as PENDING,
-- which is what "we do not know when this was finished" honestly is.
COMMENT ON COLUMN orders.production_ready_at IS
    'When production first reported READY_AT_STORE. Stops the production SLA clock (ADR/spec 256).';
