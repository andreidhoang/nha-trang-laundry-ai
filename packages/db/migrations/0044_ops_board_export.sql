-- OPS-BOARD-001: make the day's board pageable at speed, and give an export somewhere to be
-- recorded.
--
-- Three objects, and none of them computes anything. The board's numbers already come from
-- `ShadowConsoleRepository.sla_risk_board` and the domain SLA engine; what was missing was a way
-- for a person to work from them and a way for the owner to take the shop's own records out under
-- an approval that leaves a trail.

-- 1. The index the board's page actually needs.
--
-- `orders_store_commercial_idx (store_id, commercial_status, created_at)` is the wrong shape for
-- this read: the board filters on production state and orders by `production_accepted_at`, so the
-- planner had to read every order this store has ever taken and sort them. That is invisible at
-- thirty orders and is the whole latency at a year of them, which is the bound
-- `packages/db/tests/test_ops_board.py` measures rather than asserts.
--
-- Partial, with the predicate written to match the query word for word, because the board's
-- population is a small and shrinking fraction of `orders`: an order is on it from acceptance until
-- release, and every finished order in the shop's history is excluded from the index rather than
-- merely skipped by it. `(store_id, production_accepted_at, id)` is exactly the ORDER BY, so one
-- index satisfies the keyset comparison, the ordering and the page.
CREATE INDEX orders_sla_board_idx
    ON orders (store_id, production_accepted_at, id)
    WHERE production_accepted_at IS NOT NULL
      AND production_status <> 'RELEASED'
      AND commercial_status <> 'CANCELLED';

COMMENT ON INDEX orders_sla_board_idx IS
    'Covers ShadowConsoleRepository.sla_risk_board: its predicate and its keyset order. The partial '
    'predicate must stay identical to that query or the planner silently stops using it.';

-- 2. What somebody asked to export.
--
-- An export is not a button. `ApprovalAction.EXPORT_SANITIZED_DATA` already maps to
-- `_OWNER_FINANCIAL` with the resource type `EXPORT_REQUEST`, and that mapping is owner policy this
-- item does not touch. What did not exist was the resource: an approval envelope names a
-- `resource_id`, a `resource_version` and a `snapshot_hash`, and until this table there was nothing
-- for those to point at, so an `EXPORT_REQUEST` envelope could be raised against any UUID at all
-- with invented digests. This row is what `_require_resolvable_resource` now resolves.
--
-- `business_date` is named by the requester and has no default. It is the one field somebody could
-- be tempted to fill in for them -- "today", "the last 30 days" -- and a date nobody chose is a
-- date nobody can be accountable for having exported.
--
-- Immutable: the request is what the owner approved, so editing it would silently move what the
-- approval authorised. Invariant 8 is enforced above this table by the digests, and enforced here
-- by the append-only trigger.
CREATE TABLE export_requests (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES stores(id),

    -- Which records, and for which of the shop's own days. `dataset` is a single-valued CHECK
    -- rather than a free enum for the same reason `order_settlements.settlement_shape` is: a second
    -- dataset should cost a migration and a decision about what it may carry, not a new string.
    dataset TEXT NOT NULL CHECK (dataset = 'STORE_DAY_ORDERS_V1'),
    business_date DATE NOT NULL,

    -- Fixed at 1 and never incremented: the envelope binds `resource_version`, and a row that
    -- cannot change has exactly one version. The column exists because the approval machinery reads
    -- one, not because there is a sequence here.
    row_version INTEGER NOT NULL CHECK (row_version = 1),

    -- What the approval binds. `snapshot_hash` covers the request's own facts -- store, day,
    -- dataset -- and is what `_require_resolvable_resource` compares an incoming envelope against.
    -- `rendered_hash` covers the statement an owner reads before approving, including the exact
    -- column list the export will carry and the exclusions it promises. Invariant 8: widen the
    -- export's columns and the rendered digest moves, so every approval already granted against the
    -- narrower document stops matching instead of quietly authorising the wider one.
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    rendered_hash TEXT NOT NULL CHECK (rendered_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),

    requested_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    requested_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX export_requests_store_idx ON export_requests (store_id, requested_at);

CREATE TRIGGER export_requests_append_only
    BEFORE UPDATE OR DELETE ON export_requests
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- 3. That an export was produced, by whom, under which approval.
--
-- The bytes are not stored. They are the shop's own records leaving the system, and keeping a
-- second copy inside it would be a new place for data to age past every retention schedule -- the
-- failure mode the export's own sanitisation rule exists to prevent. What is kept is the
-- accountability: the approval that authorised it, the versioned query that produced it, how many
-- rows it had and a digest of the bytes, so a file somebody still holds can be checked against the
-- record of what was released.
--
-- `export_request_id UNIQUE` is the one-time property. An approved request produces one export; a
-- second attempt hits the constraint rather than handing out a second copy under one approval.
CREATE TABLE data_exports (
    id UUID PRIMARY KEY,
    export_request_id UUID NOT NULL UNIQUE REFERENCES export_requests(id),
    store_id UUID NOT NULL REFERENCES stores(id),
    approval_request_id UUID NOT NULL REFERENCES approval_requests(id),

    -- Invariant 18 travels with the artefact, not only with the screen: a file on somebody's laptop
    -- can be traced back to the rule that produced its columns.
    query_version TEXT NOT NULL,

    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^sha256:[0-9a-f]{64}$'),

    produced_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    produced_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX data_exports_store_idx ON data_exports (store_id, produced_at);

CREATE TRIGGER data_exports_append_only
    BEFORE UPDATE OR DELETE ON data_exports
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

COMMENT ON TABLE data_exports IS
    'One row per sanitized export released to a person. The bytes are deliberately not stored; the '
    'approval, the query version, the row count and the content digest are.';
