-- PICKUP-REMIND-001 (DEC-043): the pickup reminder a contact attempt was made for, and "message sent".
--
-- The schedule (day 0, 3, 7, 14, and the last day before the storage fee) is the domain's
-- (`nha_trang_laundry_domain.pickup_reminders`). A reminder is due until an attempt is recorded
-- for it, in the same append-only log `0060` built and the disposal rule already counts -- so a
-- reminder attempt is a contact attempt, and it counts toward thanh lý like any other.
--
-- Additive only. No existing row is touched and no existing value changes meaning:
--
-- 1. `order_contact_attempts.reminder_step`, nullable: NULL for every attempt written before this
--    migration and for every attempt that answers no reminder (a call from Đồ chờ lấy).
-- 2. The outcome CHECK admits `MESSAGE_SENT` beside its four values.
-- 3. `MESSAGE_SENT` is a message: only on Zalo or SMS, and only for a named reminder step. Whether
--    the egress guard allowed it is the repository's check, inside the same transaction.
--
-- Forward-only. Rolling the code back leaves a nullable column the older code never reads; an older
-- reader of an attempt row sees an outcome it does not list only for a `MESSAGE_SENT` row, which
-- the older code cannot write.

ALTER TABLE order_contact_attempts
    ADD COLUMN reminder_step TEXT NULL CHECK (
        reminder_step IS NULL
        OR reminder_step IN ('READY', 'DAY_3', 'DAY_7', 'DAY_14', 'BEFORE_FEE')
    );

ALTER TABLE order_contact_attempts DROP CONSTRAINT order_contact_attempts_outcome_check;
ALTER TABLE order_contact_attempts ADD CONSTRAINT order_contact_attempts_outcome_check CHECK (
    outcome IN ('REACHED', 'NO_ANSWER', 'WRONG_NUMBER', 'PROMISED_TO_COME', 'MESSAGE_SENT')
);

ALTER TABLE order_contact_attempts ADD CONSTRAINT order_contact_attempts_message_sent_shape CHECK (
    outcome <> 'MESSAGE_SENT' OR (channel IN ('ZALO', 'SMS') AND reminder_step IS NOT NULL)
);

COMMENT ON COLUMN order_contact_attempts.reminder_step IS
    'What question does this answer: "which pickup reminder (DEC-043) was this attempt made for?". '
    'NULL when it answers none. A step is due until an attempt names it.';

-- The due list reads, per waiting order, the steps already done since the laundry was ready.
CREATE INDEX order_contact_attempts_reminder_idx
    ON order_contact_attempts (order_id, attempted_at)
    WHERE reminder_step IS NOT NULL;
