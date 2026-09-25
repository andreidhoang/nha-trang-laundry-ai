-- DEC-031 (2026-09-25): a loss is proposed with a figure and always goes to the owner.
--
-- `0042` wrote the pre-DEC-031 rule into the schema: a LOST_ITEM row carried no amount, no ceiling,
-- no line and no window, and its status was POLICY_UNRESOLVED, which nothing leaves. That was
-- right while DEC-004 carried loss as undecided. DEC-031 read "loss/damage compensation is capped
-- at 5x the item's cleaning fee" where its words stop: the ceiling applies to loss, and what
-- "case-by-case" adds is that **every loss claim needs the owner**, whatever the amount.
--
-- So a loss row may now have the damage shape -- a line, an amount, a CREDIT direction, a ceiling
-- it is within -- and when it does, the schema itself says it can never be staff-authorised: it
-- carries an owner envelope, from the moment it is written, and it never enters STAFF_AUTHORIZED.
-- The code enforces the same rule in `domain.remedies` and in `RemedyProposalRepository.execute`;
-- this is the line no future writer can skip.
--
-- Rows written under `0042` keep their shape and their meaning. A POLICY_UNRESOLVED loss is still
-- legal, still has no figure, and `execute` still refuses it: a deployed database with such rows
-- migrates, and none of them becomes payable by this migration.
--
-- Nothing else changes. Additive in effect -- every row `0042` admitted is still admitted -- and
-- forward-only: rolling back means reinstating `0042`'s two constraints, which the database will
-- refuse for as long as a DEC-031 loss row exists. That refusal is correct; such a row is a record
-- of money the owner approved.

ALTER TABLE remedy_proposals DROP CONSTRAINT remedy_proposals_shape_matches_kind;
ALTER TABLE remedy_proposals
    ADD CONSTRAINT remedy_proposals_shape_matches_kind CHECK (
        (kind = 'FREE_REWASH' AND amount_vnd IS NULL AND direction IS NULL
            AND ceiling_vnd IS NULL AND order_line_id IS NULL
            AND attested_late_by_minutes IS NULL)
        -- Damage and, since DEC-031, loss: one item, one amount, within that item's ceiling.
        OR (kind IN ('DAMAGE_COMPENSATION', 'LOST_ITEM') AND amount_vnd IS NOT NULL
            AND direction = 'CREDIT' AND ceiling_vnd IS NOT NULL AND order_line_id IS NOT NULL
            AND attested_late_by_minutes IS NULL AND amount_vnd <= ceiling_vnd)
        OR (kind = 'LATE_DELIVERY_CREDIT' AND amount_vnd IS NOT NULL AND direction = 'CREDIT'
            AND ceiling_vnd IS NOT NULL AND order_line_id IS NULL
            AND attested_late_by_minutes IS NOT NULL AND amount_vnd <= ceiling_vnd)
        -- A loss recorded before DEC-031: no figure of any kind.
        OR (kind = 'LOST_ITEM' AND amount_vnd IS NULL AND direction IS NULL
            AND ceiling_vnd IS NULL AND order_line_id IS NULL
            AND attested_late_by_minutes IS NULL)
    );

-- `0042` said POLICY_UNRESOLVED if and only if LOST_ITEM. The "if" still holds; the "only if" is
-- what DEC-031 changed. POLICY_UNRESOLVED is now exactly the figureless loss record, so a loss
-- that carries an amount can never be parked in the state nothing leaves, and a figureless one can
-- never be anything else.
ALTER TABLE remedy_proposals DROP CONSTRAINT remedy_proposals_loss_is_unresolved;
ALTER TABLE remedy_proposals
    ADD CONSTRAINT remedy_proposals_unresolved_is_a_figureless_loss CHECK (
        (status = 'POLICY_UNRESOLVED') = (kind = 'LOST_ITEM' AND amount_vnd IS NULL)
    );

-- DEC-031 rule 2 in the schema. A loss that carries a figure carries the owner's envelope, and is
-- never in the one state a staff member may execute on their own authority. `approval_id` is
-- immutable under `protect_remedy_proposal`, so this holds for the row's whole life.
ALTER TABLE remedy_proposals
    ADD CONSTRAINT remedy_proposals_loss_needs_the_owner CHECK (
        kind <> 'LOST_ITEM'
        OR amount_vnd IS NULL
        OR (approval_id IS NOT NULL AND status <> 'STAFF_AUTHORIZED')
    );
