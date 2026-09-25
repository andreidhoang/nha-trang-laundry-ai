-- REMEDY-GARMENT-001 (the DEC-031 addendum, 2026-09-25): a claim names which garment it is.
--
-- `DEC-004` lets staff approve up to 100.000 d *per item* and caps each item at 5x *its* cleaning
-- fee. Until this migration a proposal named only a line, so on a line of three shirts the staff
-- limit was the line's: a second damaged shirt went to the owner although nothing had been paid on
-- it. The founder's ruling: a claim on a line priced per piece names its garment by its 1-based
-- position within the line's quantity, and the staff limit and the 5x ceiling are cumulative per
-- (line, garment). `domain.remedies` decides that; this column records which garment was named.
--
-- **Existing rows keep NULL, and NULL means "line-level".** They were proposed before a garment
-- could be named, so nothing says which shirt they were about. The reading the code applies to them
-- is the conservative one: a NULL row counts against **every** garment on its line, and against the
-- line's total, so no garment is ever given headroom a line-level proposal might already have used.
-- Nothing is backfilled -- choosing garment 1 for an old row would be inventing a fact, and would
-- hand garments 2..N headroom the old row may have spent.
--
-- The upper bound (at most the line's quantity) lives in the order's immutable quote snapshot, not
-- in a column this table can see, so the schema checks the shape and the domain checks the range.
--
-- Forward-only and additive: every row `0051` admitted is still admitted. Rolling back means
-- dropping the column, which the application would then refuse to run against, and which loses
-- which garment each post-addendum claim named -- the backup is the rollback.

ALTER TABLE remedy_proposals ADD COLUMN garment_index INTEGER NULL;

ALTER TABLE remedy_proposals
    ADD CONSTRAINT remedy_proposals_garment_is_a_position CHECK (
        garment_index IS NULL OR garment_index >= 1
    );

-- Only a claim about one item may name a garment. A rewash, a late-delivery credit and a loss
-- recorded before DEC-031 (no line, no figure) are about the order or about nothing priced.
ALTER TABLE remedy_proposals
    ADD CONSTRAINT remedy_proposals_garment_needs_an_item CHECK (
        garment_index IS NULL
        OR (kind IN ('DAMAGE_COMPENSATION', 'LOST_ITEM') AND order_line_id IS NOT NULL
            AND amount_vnd IS NOT NULL)
    );

-- The committed-total reads group by (order, line, garment) under the order-row lock.
CREATE INDEX remedy_proposals_garment_idx
    ON remedy_proposals (order_id, order_line_id, garment_index);

-- `0042`'s trigger lists every column a proposal may never change after it is written. The garment
-- a claim named is part of what was decided and what the owner's envelope binds, so it joins them;
-- everything else in the function is `0042`'s text unchanged.
CREATE OR REPLACE FUNCTION protect_remedy_proposal() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.incident_id IS DISTINCT FROM OLD.incident_id
       OR NEW.order_id IS DISTINCT FROM OLD.order_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.amount_vnd IS DISTINCT FROM OLD.amount_vnd
       OR NEW.direction IS DISTINCT FROM OLD.direction
       OR NEW.ceiling_vnd IS DISTINCT FROM OLD.ceiling_vnd
       OR NEW.order_line_id IS DISTINCT FROM OLD.order_line_id
       OR NEW.garment_index IS DISTINCT FROM OLD.garment_index
       OR NEW.policy_version_id IS DISTINCT FROM OLD.policy_version_id
       OR NEW.policy_version IS DISTINCT FROM OLD.policy_version
       OR NEW.store_fault_attested IS DISTINCT FROM OLD.store_fault_attested
       OR NEW.attested_late_by_minutes IS DISTINCT FROM OLD.attested_late_by_minutes
       OR NEW.window_opened_at IS DISTINCT FROM OLD.window_opened_at
       OR NEW.window_closes_at IS DISTINCT FROM OLD.window_closes_at
       OR NEW.proposal_hash IS DISTINCT FROM OLD.proposal_hash
       OR NEW.approval_id IS DISTINCT FROM OLD.approval_id
       OR NEW.proposed_by IS DISTINCT FROM OLD.proposed_by
       OR NEW.proposed_at IS DISTINCT FROM OLD.proposed_at
       OR NEW.row_version <> OLD.row_version + 1 THEN
        RAISE EXCEPTION 'a remedy proposal''s binding, amount and approval are immutable';
    END IF;
    -- One direction of travel, and nothing leaves EXECUTED or POLICY_UNRESOLVED. A remedy that was
    -- paid cannot be un-paid by an UPDATE; that is an incident of its own, exactly as a settlement
    -- recorded in error is.
    IF NOT (
        (OLD.status IN ('STAFF_AUTHORIZED', 'OWNER_APPROVAL_REQUIRED') AND NEW.status = 'EXECUTED')
    ) THEN
        RAISE EXCEPTION 'invalid remedy proposal state transition';
    END IF;
    RETURN NEW;
END;
$$;
