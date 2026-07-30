-- Server-owned, fail-closed gates for any future automated execution.
-- This migration deliberately adds no provider client or public capability.

CREATE TABLE automation_execution_gates (
    capability TEXT PRIMARY KEY,
    global_automation_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    agent_processing_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    agent_outbound_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    channel_ingress_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    capability_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    stage_policy_allows BOOLEAN NOT NULL DEFAULT FALSE,
    pdp_allows BOOLEAN NOT NULL DEFAULT FALSE,
    version INTEGER NOT NULL CHECK (version >= 1),
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE automated_execution_envelopes (
    id UUID PRIMARY KEY,
    capability TEXT NOT NULL,
    outbox_event_id UUID NOT NULL UNIQUE REFERENCES outbox_events(id),
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'HELD', 'CANCELLED')),
    hold_policy TEXT NOT NULL CHECK (hold_policy IN ('HOLD', 'CANCEL')),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    held_at TIMESTAMPTZ NULL,
    hold_reason TEXT NULL
);

CREATE TRIGGER automated_execution_envelopes_no_hard_delete
    BEFORE DELETE ON automated_execution_envelopes
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();
