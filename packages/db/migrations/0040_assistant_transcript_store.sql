-- ASSISTANT-RETENTION-001 / DEC-018: give the assistant transcript a disposable home.
--
-- `DEC-008` signed `ASSISTANT_TRANSCRIPT` at 180 days PURGE. `RETENTION-STORE-001` could not honour
-- it and said so with the reason `BLOCKED_BY_UNDELETABLE_DUPLICATE`, because every answer was stored
-- twice: once in `assistant_turns`, and again in `command_idempotency_records.response`, where
-- `protect_idempotency_record` rejects DELETE outright and rejects any UPDATE once the response is
-- non-NULL. Purging the first while the second survived would have been, in `DEC-018`'s words, "a
-- false statement made by software".
--
-- The application half of this item stops the duplicate being written: `_turn_mapping` no longer
-- carries `answer` or `links` into the stored response, and a replay rehydrates from
-- `assistant_turns` instead of remembering. This file is the other half.
--
-- **Question and answer move; intent, reason codes and links stay.** The free text a person typed
-- and the free text the system typed back are the payload section 15 schedules. `intent` and
-- `reason_codes` are enums the deterministic brain emitted, and `links` are navigation built from
-- that intent whose hrefs name identifiers the ledger keeps forever. Leaving them behind is what
-- makes a purged turn still readable as "this person asked a TODAY_OVERVIEW question on this date
-- and was answered" rather than as a blank row.
--
-- **What this migration cannot fix, and does not pretend to.** Idempotency records written before
-- today still carry an answer, and nothing may delete them. Those turns are not purgeable, and the
-- purge exempts them by name rather than reporting them as disposed of -- `retention.py`'s
-- `_PURGE_ASSISTANT_PAYLOADS` tests `command_idempotency_records` for a surviving copy. The
-- exemption self-heals: no record written from now on has an `answer` key, so no new turn is pinned.

CREATE TABLE assistant_turn_payloads (
    turn_id UUID PRIMARY KEY REFERENCES assistant_turns(turn_id) ON DELETE RESTRICT,
    question TEXT NOT NULL CHECK (char_length(question) <= 4000 AND btrim(question) <> ''),
    answer TEXT NOT NULL
);

-- Immutable while it exists, deletable when the schedule says so. `assistant_turns` carries
-- `reject_ledger_mutation`, so the question and answer could not be rewritten before this migration;
-- moving them to a table with no guard would have handed that power to every identity holding
-- UPDATE. Same shape as `0038` and `0039`, and for the same reason.
CREATE TRIGGER assistant_turn_payloads_immutable
    BEFORE UPDATE ON assistant_turn_payloads
    FOR EACH ROW EXECUTE FUNCTION reject_payload_rewrite();

-- The purge joins to `assistant_turns.created_at`, which `reject_ledger_mutation` makes immutable,
-- rather than copying it here: the fact that justifies a disposal has to survive the disposal.
-- `assistant_turns_store_idx` leads with `store_id`, so it cannot serve a store-wide cutoff scan.
CREATE INDEX assistant_turns_created_at_idx ON assistant_turns (created_at);

INSERT INTO assistant_turn_payloads (turn_id, question, answer)
SELECT turn_id, question, answer FROM assistant_turns;

-- DDL, so no row trigger fires and `assistant_turns_append_only` is neither dropped nor disabled.
-- Nothing else in the schema references these two columns: no view, no index and no foreign key.
ALTER TABLE assistant_turns DROP COLUMN question;
ALTER TABLE assistant_turns DROP COLUMN answer;

-- DEC-020: the table and the permission to purge it ship together, and the purge identity is not
-- one that serves customers. `command_idempotency_records` is readable because the DEC-018
-- exemption above is a join against it.
GRANT SELECT, DELETE ON assistant_turn_payloads TO retention_purge;
GRANT SELECT ON assistant_turns TO retention_purge;
GRANT SELECT ON command_idempotency_records TO retention_purge;
