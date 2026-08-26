-- Admit FINALIZE_QUOTE as an approval action, so a staff attestation that a customer accepted an
-- exact price can be recorded as an envelope.
--
-- `DEC-021` resolved on 2026-08-25: a quote is chốt when a named staff member confirms the customer
-- agreed to the price that was read to them, and the staff member on duty at the counter may do it.
-- The attestation has to live somewhere that order creation can trust, and that place already
-- exists -- `quote_revisions.approval_id` references `approval_requests(id)` since migration 0029.
-- What was missing was an action naming this fact.
--
-- Deliberately not PRESENT_QUOTE. That action authorises *showing* a price to a customer, which is a
-- different fact from the customer having agreed to it, and it is the action a future agent-drafted
-- quote would use when a human approves presenting it. Reusing it would put two meanings in one
-- column of the approval ledger, and the ledger is the record order creation rests on.
--
-- Two CHECK constraints enumerate the actions -- `approval_requests.action` from 0007 and
-- `approval_decisions.decision_type` from 0018 -- so both are replaced. Dropping and recreating is
-- the only way to widen a CHECK; no row is read or rewritten, and the new list is the old list plus
-- one member, so nothing already stored can fail it.

ALTER TABLE approval_requests
    DROP CONSTRAINT approval_requests_action_check,
    ADD CONSTRAINT approval_requests_action_check CHECK (
        action IN (
            'PRESENT_QUOTE', 'FINALIZE_QUOTE', 'CONFIRM_SLOT', 'SET_RANGE_PRICE',
            'SET_DELIVERY_FEE', 'APPLY_PROMOTION', 'SEND_MESSAGE', 'ACCEPT_ORDER',
            'CANCEL_ACTIVE_ORDER', 'APPROVE_REMEDY', 'APPROVE_B2B_TERMS', 'PUBLISH_POLICY',
            'EXPORT_SANITIZED_DATA'
        )
    );

ALTER TABLE approval_decisions
    DROP CONSTRAINT approval_decisions_decision_type_check,
    ADD CONSTRAINT approval_decisions_decision_type_check CHECK (
        decision_type IN (
            'PRESENT_QUOTE', 'FINALIZE_QUOTE', 'CONFIRM_SLOT', 'SET_RANGE_PRICE',
            'SET_DELIVERY_FEE', 'APPLY_PROMOTION', 'SEND_MESSAGE', 'ACCEPT_ORDER',
            'CANCEL_ACTIVE_ORDER', 'APPROVE_REMEDY', 'APPROVE_B2B_TERMS', 'PUBLISH_POLICY',
            'EXPORT_SANITIZED_DATA'
        )
    );
