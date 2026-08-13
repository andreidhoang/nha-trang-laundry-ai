-- CONSENT-STOP-001: an affirmative CLEAR state, so a permitted send rests on a recorded decision.
--
-- Before this, suppression_entries could only express SUPPRESSED, PENDING_REVIEW_BLOCKED or
-- UNKNOWN_BLOCKED. Absence of a row means unknown, and unknown blocks -- correct, but it left no way
-- to record that consent is affirmatively in place. CLEAR must be written by a deliberate, audited
-- act; it is never the default and never inferred from silence.

ALTER TABLE suppression_entries DROP CONSTRAINT suppression_entries_state_check;

ALTER TABLE suppression_entries ADD CONSTRAINT suppression_entries_state_check
    CHECK (state IN ('CLEAR', 'SUPPRESSED', 'PENDING_REVIEW_BLOCKED', 'UNKNOWN_BLOCKED'));
