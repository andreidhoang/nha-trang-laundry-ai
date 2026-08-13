-- SHADOW-CONSOLE-001: the surfaces a Shadow pilot needs, over authoritative data only.
--
-- Three additions. Staff-to-store membership, because the existing console authorizes by role alone
-- and a role check cannot stop a staff member reading another store by changing an id. The agent's
-- proposed draft, which was previously never persisted -- the run summary keeps only a character
-- count -- so there was nothing for a human to review. And the attributed review decision itself.

CREATE TABLE staff_store_assignments (
    staff_user_id UUID NOT NULL REFERENCES staff_users(id),
    store_id UUID NOT NULL,
    assigned_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    assigned_at TIMESTAMPTZ NOT NULL,
    row_version BIGINT NOT NULL CHECK (row_version > 0),
    PRIMARY KEY (staff_user_id, store_id)
);

CREATE INDEX staff_store_assignments_store_idx ON staff_store_assignments (store_id);

CREATE TRIGGER staff_store_assignments_no_hard_delete
    BEFORE DELETE ON staff_store_assignments
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

-- The agent's own output, immutable. Editing produces a new review row; it never rewrites this.
CREATE TABLE agent_drafts (
    agent_run_id UUID PRIMARY KEY REFERENCES agent_runs(id),
    store_id UUID NOT NULL,
    conversation_binding_id UUID NOT NULL,
    contact_binding_id UUID NOT NULL,
    draft_text TEXT NOT NULL CHECK (length(draft_text) BETWEEN 1 AND 4000),
    terminal_outcome TEXT NOT NULL CHECK (terminal_outcome IN ('DRAFT', 'REQUIRE_HUMAN')),
    terminal_code TEXT NOT NULL CHECK (terminal_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    tool_call_count INTEGER NOT NULL CHECK (tool_call_count >= 0),
    produced_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX agent_drafts_store_idx ON agent_drafts (store_id, produced_at DESC);

CREATE TRIGGER agent_drafts_append_only
    BEFORE UPDATE OR DELETE ON agent_drafts
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- One attributed decision per review. An edit carries the replacement text; the agent's original
-- stays in agent_drafts untouched, which is what makes rejection and edit data usable as eval input.
CREATE TABLE agent_draft_reviews (
    review_id UUID PRIMARY KEY,
    agent_run_id UUID NOT NULL REFERENCES agent_drafts(agent_run_id),
    store_id UUID NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('APPROVE', 'EDIT', 'REJECT')),
    reason_code TEXT NULL CHECK (reason_code IS NULL OR reason_code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    edited_text TEXT NULL CHECK (edited_text IS NULL OR length(edited_text) BETWEEN 1 AND 4000),
    decided_by_staff_id UUID NOT NULL REFERENCES staff_users(id),
    decided_at TIMESTAMPTZ NOT NULL,

    -- An edit must supply its replacement; an approval and a rejection must not.
    CONSTRAINT agent_draft_reviews_edit_carries_text CHECK (
        (decision = 'EDIT' AND edited_text IS NOT NULL)
        OR (decision <> 'EDIT' AND edited_text IS NULL)
    ),
    -- Rejection is a first-class outcome and must say why; that reason is eval corpus input.
    CONSTRAINT agent_draft_reviews_rejection_has_reason CHECK (
        decision <> 'REJECT' OR reason_code IS NOT NULL
    ),
    -- One terminal decision per run. A second review loses at the constraint, not in the UI.
    UNIQUE (agent_run_id)
);

CREATE INDEX agent_draft_reviews_store_idx ON agent_draft_reviews (store_id, decided_at DESC);

CREATE TRIGGER agent_draft_reviews_append_only
    BEFORE UPDATE OR DELETE ON agent_draft_reviews
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
