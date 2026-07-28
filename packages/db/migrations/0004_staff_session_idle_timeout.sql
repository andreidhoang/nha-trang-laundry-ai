-- Preserve the configured idle duration so successful activity can extend idle expiry safely.

ALTER TABLE staff_sessions
    ADD COLUMN idle_timeout_seconds INTEGER;

UPDATE staff_sessions
SET idle_timeout_seconds = GREATEST(
    1,
    EXTRACT(EPOCH FROM (idle_expires_at - issued_at))::INTEGER
);

ALTER TABLE staff_sessions
    ALTER COLUMN idle_timeout_seconds SET NOT NULL,
    ADD CONSTRAINT staff_sessions_idle_timeout_positive CHECK (idle_timeout_seconds > 0);
