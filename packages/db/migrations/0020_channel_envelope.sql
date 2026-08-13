-- CHANNEL-ENVELOPE-001: server-owned contact binding and one receipt per send attempt.
--
-- Contact binding is resolved here and never accepted from a provider payload or from model output.
-- An unknown provider identity becomes UNVERIFIED, which carries no access to order status, quotes
-- or any customer-specific fact.

CREATE TABLE contact_channel_bindings (
    provider TEXT NOT NULL CHECK (provider IN (
        'ZALO_OA', 'TELEGRAM_SANDBOX', 'FACEBOOK_MESSENGER'
    )),
    provider_user_ref TEXT NOT NULL CHECK (length(provider_user_ref) BETWEEN 1 AND 200),
    contact_binding_id UUID NOT NULL,
    verification_state TEXT NOT NULL CHECK (verification_state IN ('UNVERIFIED', 'VERIFIED')),
    verified_by_staff_id UUID NULL,
    verified_at TIMESTAMPTZ NULL,
    row_version BIGINT NOT NULL CHECK (row_version > 0),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider, provider_user_ref),
    CONSTRAINT contact_channel_bindings_verification_evidence CHECK (
        (verification_state = 'UNVERIFIED'
         AND verified_by_staff_id IS NULL AND verified_at IS NULL)
        OR (verification_state = 'VERIFIED'
            AND verified_by_staff_id IS NOT NULL AND verified_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX contact_channel_bindings_identity_idx
    ON contact_channel_bindings (provider, contact_binding_id);

CREATE TRIGGER contact_channel_bindings_no_hard_delete
    BEFORE DELETE ON contact_channel_bindings
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

-- One row per send attempt, including failures and timeouts. An attempt that produced no receipt
-- did not happen as far as reconciliation is concerned.
CREATE TABLE channel_send_receipts (
    receipt_id UUID PRIMARY KEY,
    outbox_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 200),
    provider TEXT NOT NULL CHECK (provider IN (
        'ZALO_OA', 'TELEGRAM_SANDBOX', 'FACEBOOK_MESSENGER'
    )),
    message_kind TEXT NOT NULL CHECK (message_kind IN (
        'LIST_PRICE_INFO', 'INTAKE_FACT_REQUEST', 'INTAKE_RECEIPT', 'INCIDENT_RECEIPT',
        'ORDER_STATUS', 'APPROVED_QUOTE_PRESENTATION', 'APPROVED_SLOT_PRESENTATION',
        'FREE_FORM_TRANSACTIONAL', 'MARKETING'
    )),
    authorization_source TEXT NOT NULL CHECK (authorization_source IN (
        'HUMAN_APPROVAL', 'CAPABILITY_AUTHORIZED'
    )),
    approval_ref UUID NULL,
    capability TEXT NULL,
    egress_suppression_check TEXT NOT NULL
        CHECK (egress_suppression_check = 'PASSED_IN_SEND_TRANSACTION'),
    messaging_window TEXT NULL CHECK (messaging_window IN (
        'IN_WINDOW', 'TEMPLATE_APPROVED', 'HELD_REQUIRE_HUMAN'
    )),
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    attempt_started_at TIMESTAMPTZ NOT NULL,
    attempt_completed_at TIMESTAMPTZ NULL,
    attempt_outcome TEXT NOT NULL CHECK (attempt_outcome IN (
        'ACCEPTED', 'REJECTED', 'TIMEOUT', 'TRANSPORT_ERROR'
    )),
    provider_message_ref TEXT NULL CHECK (provider_message_ref IS NULL
        OR length(provider_message_ref) BETWEEN 1 AND 200),
    provider_error_code TEXT NULL CHECK (provider_error_code IS NULL
        OR length(provider_error_code) BETWEEN 1 AND 120),
    rate_limited BOOLEAN NOT NULL DEFAULT FALSE,
    delivery_status TEXT NOT NULL CHECK (delivery_status IN (
        'OUTBOX_PENDING', 'PROVIDER_ACCEPTED', 'DELIVERED', 'FAILED',
        'MANUAL_SEND_RECORDED', 'CANCELLED'
    )),
    reconciliation_state TEXT NOT NULL CHECK (reconciliation_state IN (
        'NOT_REQUIRED', 'UNKNOWN', 'UNKNOWN_REQUIRES_HUMAN',
        'CONFIRMED_SENT', 'CONFIRMED_NOT_SENT'
    )),
    resolved_by TEXT NULL CHECK (resolved_by IN ('PROVIDER_CONFIRMATION', 'HUMAN_DECISION')),
    resolved_at TIMESTAMPTZ NULL,
    resolution_actor_id TEXT NULL CHECK (resolution_actor_id IS NULL
        OR length(resolution_actor_id) BETWEEN 1 AND 200),
    resolution_note TEXT NULL CHECK (resolution_note IS NULL OR length(resolution_note) <= 500),
    recorded_at TIMESTAMPTZ NOT NULL,
    UNIQUE (outbox_id, attempt_number),

    -- HUMAN_APPROVAL requires its approval; CAPABILITY_AUTHORIZED requires its capability.
    CONSTRAINT channel_send_receipts_authorization_evidence CHECK (
        (authorization_source = 'HUMAN_APPROVAL' AND approval_ref IS NOT NULL)
        OR (authorization_source = 'CAPABILITY_AUTHORIZED' AND capability IS NOT NULL)
    ),

    -- An ambiguous provider outcome is never recorded as settled.
    CONSTRAINT channel_send_receipts_ambiguous_outcome CHECK (
        attempt_outcome NOT IN ('TIMEOUT', 'TRANSPORT_ERROR')
        OR reconciliation_state IN ('UNKNOWN', 'UNKNOWN_REQUIRES_HUMAN',
                                    'CONFIRMED_SENT', 'CONFIRMED_NOT_SENT')
    ),

    -- Only a provider confirmation or a human decision may leave UNKNOWN, and leaving it requires
    -- recording who did so and when. An automatic retry can never satisfy this.
    CONSTRAINT channel_send_receipts_resolution_evidence CHECK (
        (reconciliation_state IN ('CONFIRMED_SENT', 'CONFIRMED_NOT_SENT')
         AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)
        OR (reconciliation_state NOT IN ('CONFIRMED_SENT', 'CONFIRMED_NOT_SENT')
            AND resolved_by IS NULL AND resolved_at IS NULL
            AND resolution_actor_id IS NULL AND resolution_note IS NULL)
    ),

    CONSTRAINT channel_send_receipts_human_resolution_actor CHECK (
        resolved_by IS DISTINCT FROM 'HUMAN_DECISION' OR resolution_actor_id IS NOT NULL
    )
);

CREATE INDEX channel_send_receipts_outbox_idx
    ON channel_send_receipts (outbox_id, attempt_number);

CREATE INDEX channel_send_receipts_reconciliation_idx
    ON channel_send_receipts (reconciliation_state, recorded_at)
    WHERE reconciliation_state IN ('UNKNOWN', 'UNKNOWN_REQUIRES_HUMAN');

CREATE TRIGGER channel_send_receipts_no_hard_delete
    BEFORE DELETE ON channel_send_receipts
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();
