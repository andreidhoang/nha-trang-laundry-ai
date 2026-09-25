-- PROMISE-001 (DEC-037): every order gets a promised-ready time ("hẹn trả").
--
-- Additive only. Every existing order keeps NULL in all five columns: it was taken before the
-- owner published a turnaround policy, it carries no promise, and nothing below touches its row (a
-- nullable column with no default rewrites nothing and fires no row trigger).
--
-- * `promised_ready_at` is the FIRST promise, set once at Nhận đồ (RECEIVE) and immutable after:
--   the on-time figure counts against it, so re-promising can never improve the shop's score. The
--   trigger below refuses any change to it once set, whatever path the UPDATE comes from.
-- * `current_promise_at` is what the customer was last told. It equals the first promise until a
--   Hẹn lại, and may only move to a time an `order_promise_changes` row records, in the same
--   transaction -- so the order row and the change ledger cannot disagree.
-- * `promise_basis` / `promise_rule_id` say which rule produced the first promise (RULE, H24, H48,
--   EXPRESS_2H, CUSTOM; SLA_STANDARD_CLOTHES, SLA_BLANKETS_SHEETS, STAFF_SET, ...), and
--   `promise_policy_version_id` the published turnaround policy it was computed under; the full
--   calculation trace travels on the ORDER_PROMISE_SET domain event.
--
-- Forward-only. Rolling the code back leaves columns the older code never reads; orders keep their
-- promise data, and the older code simply stops showing it.
ALTER TABLE orders
    ADD COLUMN promised_ready_at TIMESTAMPTZ,
    ADD COLUMN current_promise_at TIMESTAMPTZ,
    ADD COLUMN promise_basis TEXT,
    ADD COLUMN promise_rule_id TEXT,
    ADD COLUMN promise_policy_version_id UUID REFERENCES configuration_versions(id);

ALTER TABLE orders
    ADD CONSTRAINT orders_promise_shape CHECK (
        (promised_ready_at IS NULL) = (current_promise_at IS NULL)
        AND (promised_ready_at IS NULL) = (promise_basis IS NULL)
        AND (promised_ready_at IS NULL) = (promise_rule_id IS NULL)
        AND (promised_ready_at IS NULL) = (promise_policy_version_id IS NULL)
    ),
    ADD CONSTRAINT orders_promise_basis_check CHECK (
        promise_basis IS NULL OR promise_basis IN ('RULE', 'H24', 'H48', 'EXPRESS_2H', 'CUSTOM')
    ),
    ADD CONSTRAINT orders_promise_rule_check CHECK (
        promise_rule_id IS NULL OR promise_rule_id ~ '^[A-Z][A-Z0-9_]{1,62}$'
    );

-- Hẹn lại: every change of what the customer was told, append-only. The reason is a code the
-- counter picks; `note` is an optional few words (required for OTHER) and stays in this table only --
-- it is never copied into an event, audit or outbox payload.
CREATE TABLE order_promise_changes (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(id),
    store_id UUID NOT NULL REFERENCES stores(id),
    previous_promise_at TIMESTAMPTZ NOT NULL,
    new_promise_at TIMESTAMPTZ NOT NULL CHECK (new_promise_at <> previous_promise_at),
    reason_code TEXT NOT NULL CHECK (reason_code IN (
        'CUSTOMER_REQUEST', 'EXTRA_TREATMENT', 'MACHINE_ISSUE', 'WORKLOAD', 'WEATHER_DRYING', 'OTHER'
    )),
    note TEXT CHECK (note IS NULL OR (length(btrim(note)) BETWEEN 1 AND 120)),
    changed_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    changed_at TIMESTAMPTZ NOT NULL,
    CHECK (reason_code <> 'OTHER' OR note IS NOT NULL)
);

CREATE INDEX order_promise_changes_order_idx ON order_promise_changes (order_id, changed_at, id);

CREATE TRIGGER order_promise_changes_append_only
    BEFORE UPDATE OR DELETE ON order_promise_changes
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE FUNCTION enforce_order_promise_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- The first promise is set once and never again.
    IF OLD.promised_ready_at IS NOT NULL AND (
        NEW.promised_ready_at IS DISTINCT FROM OLD.promised_ready_at
        OR NEW.promise_basis IS DISTINCT FROM OLD.promise_basis
        OR NEW.promise_rule_id IS DISTINCT FROM OLD.promise_rule_id
        OR NEW.promise_policy_version_id IS DISTINCT FROM OLD.promise_policy_version_id
    ) THEN
        RAISE EXCEPTION 'the first promised-ready time of an order is immutable';
    END IF;
    -- Setting the first promise sets the current one to the same instant.
    IF OLD.promised_ready_at IS NULL AND NEW.promised_ready_at IS NOT NULL
       AND NEW.current_promise_at IS DISTINCT FROM NEW.promised_ready_at THEN
        RAISE EXCEPTION 'a new promise starts with the current promise equal to it';
    END IF;
    -- A later move of the current promise is a Hẹn lại, and the ledger must record it.
    IF OLD.current_promise_at IS NOT NULL
       AND NEW.current_promise_at IS DISTINCT FROM OLD.current_promise_at
       AND NOT EXISTS (
           SELECT 1 FROM order_promise_changes c
           WHERE c.order_id = NEW.id
             AND c.previous_promise_at = OLD.current_promise_at
             AND c.new_promise_at = NEW.current_promise_at
       ) THEN
        RAISE EXCEPTION 'a changed promise must be recorded in order_promise_changes';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER orders_promise_guard
    BEFORE UPDATE OF promised_ready_at, current_promise_at, promise_basis, promise_rule_id,
        promise_policy_version_id
    ON orders
    FOR EACH ROW EXECUTE FUNCTION enforce_order_promise_update();

COMMENT ON COLUMN orders.promised_ready_at IS
    'PROMISE-001 / DEC-037: the first promised-ready time, set at RECEIVE, immutable. NULL for an '
    'order taken while no turnaround policy was published. The on-time figure counts against it.';
-- The SLA board ranks by when each order is due (`sla-risk-board-v2`): promised orders by their
-- current promise, through this index; the rest by acceptance, through `0044`'s
-- `orders_sla_board_idx`. The predicate is `0044`'s population plus "has a promise", so each arm
-- of the board's statement is one ordered index scan cut at the page size.
CREATE INDEX orders_sla_promise_board_idx
    ON orders (store_id, current_promise_at, id)
    WHERE production_accepted_at IS NOT NULL
      AND production_status <> 'RELEASED'
      AND commercial_status <> 'CANCELLED'
      AND current_promise_at IS NOT NULL;

COMMENT ON COLUMN orders.current_promise_at IS
    'PROMISE-001: what the customer was last told; moves only with an order_promise_changes row.';
