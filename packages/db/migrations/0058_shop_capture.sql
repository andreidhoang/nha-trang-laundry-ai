-- SHOP-CAPTURE-001 (DEC-038): measure the shop inside taps staff already make.
--
-- `SHOP-INSTRUMENT-001` is blocked on weeks of real cycle, delivery-cost and spending data, and
-- nothing recorded any of it. Four tables collect it where the event already happens:
--
--   machines            the shop's machine list, seeded from `templates/machine-master.csv` by
--                       `scripts/seed_machines.py` (owner-confirmed, 2026-07-27); the owner adds,
--                       renames or retires one. A retired machine keeps its row, because a cycle
--                       measured on it last month is still a fact about last month.
--   wash_cycles         one per wash of one order (*giặt riêng từng khách*): opened by the
--                       production move into IN_PROCESS -- the `START_WASH` step, and again by a
--                       `REWASH` -- and closed by the move into QUALITY_CHECK, inside the order
--                       transition's own transaction. `machine_id` NULL is "not captured": staff
--                       pressed "Bỏ qua", or the move came through the per-axis route. That is a
--                       counted state, not an error (DEC-038: capture is visible in the data rather
--                       than forced at the counter).
--   delivery_leg_costs  what a trip cost: vehicle, kilometres, money and a short note, recorded on
--                       the same press as the leg (`templates/delivery-cost-log.csv`'s columns).
--                       One row per leg at most, every field optional, and append-only like the leg.
--   expenses            Sổ thu chi: the shop's spending by day and category. Append-only except
--                       for one void, so a typo is corrected by voiding the line and writing the
--                       right one -- the wrong figure stays readable, as a ledger's does.
--
-- **Money here is integer VND in BIGINT** and is summed only by PostgreSQL. Kilometres are
-- NUMERIC(6,1): a distance, never money, and never a float.
--
-- Forward-only: nothing existing is altered. Rolling the code back leaves four tables the older
-- code never reads; a step or leg recorded meanwhile keeps its order-side rows exactly as the older
-- code would have written them.

CREATE TABLE machines (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES stores (id),
    -- The stable identifier painted on the machine (`WASH-01`), from the machine master or typed
    -- by the owner. Unique per store; it is what the seed script matches on, so re-running it never
    -- duplicates a machine and never overwrites the owner's rename.
    code TEXT NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9-]{0,31}$'),
    display_name TEXT NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 60),
    category TEXT NOT NULL CHECK (category IN (
        'washer', 'dryer', 'dry_cleaner', 'shoe_washer_dryer', 'vacuum_ironing_table',
        'boiler_iron_set', 'other'
    )),
    source TEXT NOT NULL CHECK (source IN ('MACHINE_MASTER', 'OWNER')),
    retired_at TIMESTAMPTZ NULL,
    created_by UUID NULL REFERENCES staff_users (id),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    UNIQUE (store_id, code)
);

CREATE FUNCTION protect_machine() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.code IS DISTINCT FROM OLD.code
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.row_version <> OLD.row_version + 1 THEN
        RAISE EXCEPTION 'a machine''s identity is immutable; rename or retire it';
    END IF;
    -- Retiring is one-way: a machine sold or scrapped does not come back under the same row. A new
    -- machine is a new row with its own code.
    IF OLD.retired_at IS NOT NULL AND NEW.retired_at IS DISTINCT FROM OLD.retired_at THEN
        RAISE EXCEPTION 'a retired machine stays retired';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER machines_protected
    BEFORE UPDATE ON machines
    FOR EACH ROW EXECUTE FUNCTION protect_machine();

CREATE TRIGGER machines_no_hard_delete
    BEFORE DELETE ON machines
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

CREATE TABLE wash_cycles (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES stores (id),
    order_id UUID NOT NULL REFERENCES orders (id),
    machine_id UUID NULL REFERENCES machines (id),
    -- WASH for the first wash of the order, REWASH for any later one.
    kind TEXT NOT NULL CHECK (kind IN ('WASH', 'REWASH')),
    started_at TIMESTAMPTZ NOT NULL,
    started_by UUID NOT NULL REFERENCES staff_users (id),
    ended_at TIMESTAMPTZ NULL,
    ended_by UUID NULL REFERENCES staff_users (id),
    CHECK ((ended_at IS NULL) = (ended_by IS NULL)),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

-- An order is washed one load at a time: at most one open cycle.
CREATE UNIQUE INDEX wash_cycles_one_open_per_order ON wash_cycles (order_id) WHERE ended_at IS NULL;
CREATE INDEX wash_cycles_store_started_idx ON wash_cycles (store_id, started_at, id);
CREATE INDEX wash_cycles_machine_started_idx ON wash_cycles (machine_id, started_at DESC);

CREATE FUNCTION protect_wash_cycle() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- The one update a cycle admits is its close, once.
    IF OLD.ended_at IS NOT NULL
       OR NEW.ended_at IS NULL
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.order_id IS DISTINCT FROM OLD.order_id
       OR NEW.machine_id IS DISTINCT FROM OLD.machine_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.started_by IS DISTINCT FROM OLD.started_by THEN
        RAISE EXCEPTION 'a wash cycle is closed once and otherwise immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER wash_cycles_protected
    BEFORE UPDATE ON wash_cycles
    FOR EACH ROW EXECUTE FUNCTION protect_wash_cycle();

CREATE TRIGGER wash_cycles_no_hard_delete
    BEFORE DELETE ON wash_cycles
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

CREATE TABLE delivery_leg_costs (
    leg_id UUID PRIMARY KEY REFERENCES delivery_legs (id),
    store_id UUID NOT NULL REFERENCES stores (id),
    order_id UUID NOT NULL REFERENCES orders (id),
    vehicle TEXT NULL CHECK (vehicle IN ('XE_MAY', 'O_TO', 'THUE_NGOAI')),
    km NUMERIC(6, 1) NULL CHECK (km >= 0 AND km <= 999.9),
    cost_vnd BIGINT NULL CHECK (cost_vnd >= 0 AND cost_vnd <= 10000000),
    -- About the trip's cost ("gửi xe", "Grab 2 chiều"), never about the customer; `0033` keeps
    -- free text off the leg itself for that reason, and the console says so beside the field.
    note TEXT NULL CHECK (note IS NULL OR length(btrim(note)) BETWEEN 1 AND 120),
    recorded_by UUID NOT NULL REFERENCES staff_users (id),
    recorded_at TIMESTAMPTZ NOT NULL,
    CHECK (vehicle IS NOT NULL OR km IS NOT NULL OR cost_vnd IS NOT NULL OR note IS NOT NULL)
);

CREATE INDEX delivery_leg_costs_store_idx ON delivery_leg_costs (store_id, recorded_at, leg_id);

CREATE TRIGGER delivery_leg_costs_append_only
    BEFORE UPDATE OR DELETE ON delivery_leg_costs
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TABLE expenses (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES stores (id),
    spent_on DATE NOT NULL,
    category TEXT NOT NULL CHECK (category IN (
        'DIEN', 'NUOC', 'HOA_CHAT', 'TUI_NHAN', 'LUONG', 'MAT_BANG', 'SUA_CHUA', 'XANG_XE', 'KHAC'
    )),
    amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0 AND amount_vnd <= 1000000000),
    note TEXT NULL CHECK (note IS NULL OR length(btrim(note)) BETWEEN 1 AND 120),
    recorded_by UUID NOT NULL REFERENCES staff_users (id),
    recorded_at TIMESTAMPTZ NOT NULL,
    voided_at TIMESTAMPTZ NULL,
    voided_by UUID NULL REFERENCES staff_users (id),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version IN (1, 2)),
    CHECK ((voided_at IS NULL) = (voided_by IS NULL)),
    CHECK ((row_version = 2) = (voided_at IS NOT NULL))
);

CREATE INDEX expenses_store_day_idx ON expenses (store_id, spent_on, recorded_at, id);

CREATE FUNCTION protect_expense() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- The one update an expense admits is its void, once. The amount, day and category are never
    -- rewritten: the correction is a void and a new line.
    IF OLD.voided_at IS NOT NULL
       OR NEW.voided_at IS NULL
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.spent_on IS DISTINCT FROM OLD.spent_on
       OR NEW.category IS DISTINCT FROM OLD.category
       OR NEW.amount_vnd IS DISTINCT FROM OLD.amount_vnd
       OR NEW.note IS DISTINCT FROM OLD.note
       OR NEW.recorded_by IS DISTINCT FROM OLD.recorded_by
       OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at THEN
        RAISE EXCEPTION 'an expense is voided once and otherwise immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER expenses_protected
    BEFORE UPDATE ON expenses
    FOR EACH ROW EXECUTE FUNCTION protect_expense();

CREATE TRIGGER expenses_no_hard_delete
    BEFORE DELETE ON expenses
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();
