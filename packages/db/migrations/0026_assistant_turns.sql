-- ASSISTANT-001: the internal owner-assistant's turn ledger.
--
-- Every question the owner asks the deterministic assistant and every answer it gives is one
-- immutable row. The answer is evidence of what the system told the person running the shop, so it
-- is kept with the same append-only discipline as the other ledgers: a later, better answer is a
-- new turn, never a rewrite of an old one.
--
-- The question is stored verbatim. Matching normalizes a copy (lowercase, diacritics stripped) and
-- never rewrites what the person actually asked.

CREATE TABLE assistant_turns (
    turn_id UUID PRIMARY KEY,
    store_id UUID NOT NULL,
    staff_user_id UUID NOT NULL,
    question TEXT NOT NULL CHECK (char_length(question) <= 4000 AND btrim(question) <> ''),
    intent TEXT NOT NULL CHECK (intent ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    answer TEXT NOT NULL,
    links JSONB NOT NULL DEFAULT '[]',
    reason_codes JSONB NOT NULL DEFAULT '[]',
    correlation_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX assistant_turns_store_idx ON assistant_turns (store_id, created_at DESC, turn_id);

CREATE INDEX assistant_turns_staff_idx
    ON assistant_turns (store_id, staff_user_id, created_at DESC, turn_id);

CREATE TRIGGER assistant_turns_append_only
    BEFORE UPDATE OR DELETE ON assistant_turns
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
