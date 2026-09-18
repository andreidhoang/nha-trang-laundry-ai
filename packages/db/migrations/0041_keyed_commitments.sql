-- HASH-KEYING-001 / DEC-018: admit the keyed generation of the two commitments that outlive a purge.
--
-- `webhook_events.payload_hash` and `command_idempotency_records.request_hash` are unsalted SHA-256
-- over customer content, and both are designed to survive the disposal of that content.
-- `RETENTION-STORE-001` disposes of a webhook ciphertext at 30 days and keeps its hash forever;
-- `protect_idempotency_record` lets nobody delete a request hash at all. A short Vietnamese message
-- -- `DỪNG` above all -- has a small enough preimage space to enumerate, so the purge removed the
-- payload and left a commitment from which it can be recovered. `DEC-018` recorded that knowingly
-- and scheduled this fix.
--
-- HMAC-SHA256 under a per-deployment key preserves equality comparison exactly, so deduplication of
-- provider events and idempotent replay detection are unchanged, while the value stops being
-- enumerable by anyone without the key.
--
-- **Both generations stay legal, permanently.** Rows written before the key existed keep their `V1`
-- prefix. They are never re-keyed: doing so would mean reading the plaintext they commit to, which
-- is either already disposed of or is exactly the material this change protects. So this migration
-- widens a constraint and rewrites nothing. The prefix distinguishes the generations in the data
-- rather than by deployment date, and `HashKeyUnavailable` makes a third, unkeyed generation
-- impossible to write.

ALTER TABLE webhook_events
    DROP CONSTRAINT webhook_events_payload_hash_check;

ALTER TABLE webhook_events
    ADD CONSTRAINT webhook_events_payload_hash_check CHECK (
        payload_hash ~ '^(RAW-SHA256-V1|RAW-HMAC-V2):[0-9a-f]{64}$'
    );

-- The replay-conflict record compares a stored hash against an observed one, so it carries the same
-- two shapes. A conflict spanning the cutover reads as a mismatch, which is the honest answer: the
-- two values were produced by different generations and the record says so rather than guessing.
ALTER TABLE inbox_replay_conflicts
    DROP CONSTRAINT inbox_replay_conflicts_expected_payload_hash_check;

ALTER TABLE inbox_replay_conflicts
    ADD CONSTRAINT inbox_replay_conflicts_expected_payload_hash_check CHECK (
        expected_payload_hash ~ '^(RAW-SHA256-V1|RAW-HMAC-V2):[0-9a-f]{64}$'
    );

ALTER TABLE inbox_replay_conflicts
    DROP CONSTRAINT inbox_replay_conflicts_observed_payload_hash_check;

ALTER TABLE inbox_replay_conflicts
    ADD CONSTRAINT inbox_replay_conflicts_observed_payload_hash_check CHECK (
        observed_payload_hash ~ '^(RAW-SHA256-V1|RAW-HMAC-V2):[0-9a-f]{64}$'
    );

ALTER TABLE command_idempotency_records
    DROP CONSTRAINT command_idempotency_records_request_hash_check;

ALTER TABLE command_idempotency_records
    ADD CONSTRAINT command_idempotency_records_request_hash_check CHECK (
        request_hash ~ '^(JCS-SHA256-V1|JCS-HMAC-V2):[0-9a-f]{64}$'
    );
