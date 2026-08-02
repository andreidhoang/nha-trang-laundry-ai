-- Persist the explicit human authority evidence required by ENGINEERING_SPEC P-02.

ALTER TABLE approval_decisions
    ADD COLUMN decision_type TEXT,
    ADD COLUMN reason_code TEXT,
    ADD COLUMN note TEXT;

DROP TRIGGER approval_decisions_append_only ON approval_decisions;

UPDATE approval_decisions d
SET decision_type = r.action,
    reason_code = 'LEGACY_UNSPECIFIED'
FROM approval_requests r
WHERE r.id = d.approval_request_id;

CREATE TRIGGER approval_decisions_append_only
    BEFORE UPDATE OR DELETE ON approval_decisions
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

ALTER TABLE approval_decisions
    ALTER COLUMN decision_type SET NOT NULL,
    ALTER COLUMN reason_code SET NOT NULL,
    ADD CONSTRAINT approval_decisions_decision_type_check CHECK (
        decision_type IN (
            'PRESENT_QUOTE', 'CONFIRM_SLOT', 'SET_RANGE_PRICE', 'SET_DELIVERY_FEE',
            'APPLY_PROMOTION', 'SEND_MESSAGE', 'ACCEPT_ORDER', 'CANCEL_ACTIVE_ORDER',
            'APPROVE_REMEDY', 'APPROVE_B2B_TERMS', 'PUBLISH_POLICY', 'EXPORT_SANITIZED_DATA'
        )
    ),
    ADD CONSTRAINT approval_decisions_reason_code_check CHECK (
        reason_code ~ '^[A-Z][A-Z0-9_]{1,99}$'
    ),
    ADD CONSTRAINT approval_decisions_note_check CHECK (
        note IS NULL OR length(note) BETWEEN 1 AND 500
    );
