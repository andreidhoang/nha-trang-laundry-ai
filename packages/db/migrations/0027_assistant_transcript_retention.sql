-- ASSISTANT-001 retention: enroll the assistant transcript in the section 15 schedule.
--
-- `assistant_turns` is staff-internal Tier-2-style memory, so it needs a retention class like
-- every other table holding free text. The window is published configuration, never a constant:
-- this migration only makes the class name legal to publish. The table carries
-- `reject_ledger_mutation`, so an enabled purge against this class refuses the same way every
-- other append-only ledger refuses, until a schema decision says otherwise.

ALTER TABLE retention_class_configurations
    DROP CONSTRAINT retention_class_configurations_class_name_check;

ALTER TABLE retention_class_configurations
    ADD CONSTRAINT retention_class_configurations_class_name_check CHECK (class_name IN (
        'RAW_WEBHOOK_PAYLOAD', 'CONVERSATION_BODY', 'AGENT_RUN_PAYLOAD',
        'EXACT_DELIVERY_LOCATION', 'CONSENT_EVIDENCE', 'ORDER_FINANCIAL_RECORD',
        'INCIDENT_EVIDENCE', 'DEBUG_LOG', 'SECURITY_AUDIT_EVENT',
        'ASSISTANT_TRANSCRIPT'
    ));
