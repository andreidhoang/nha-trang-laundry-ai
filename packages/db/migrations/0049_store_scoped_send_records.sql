-- API-INTEGRITY-002: a send record names the shop it belongs to and the recipient that was approved.
--
-- Two findings from the 2026-09-24 API review, both about records that could say something nobody
-- authorised.
--
-- 1. `manual_send_envelopes.recipient_binding_id` (0011) was copied from the request body and had no
--    foreign key, so an attestation could record that approver B's message went to a recipient X
--    that no approval ever named. The repository now derives the recipient from the approved draft;
--    the composite keys below make the schema refuse any envelope whose recipient is not the one on
--    the draft it names, and any attestation whose recipient is not its envelope's.
--
-- 2. `channel_send_receipts` (0020) had no store, so the unknown-send queue listed and resolved
--    every shop's receipts for anyone holding a role. A receipt now carries `store_id`.
--
-- **Backfill, and what happens where it cannot be done.** A receipt authorised by a human approval
-- takes that approval's store: `approval_requests.store_id` is NOT NULL since 0034, so every receipt
-- whose `approval_ref` names a real approval is derivable. A receipt whose approval_ref names
-- nothing, or that was capability-authorised, has no row to derive a store from.
--
-- Those rows are NOT given a store by guessing, and NOT made visible to everyone. They keep
-- `store_id` NULL, which every store-scoped read excludes (`store_id = $1` is never true of NULL)
-- and every resolution refuses, so they fail closed. 0034 stopped the migration outright in the
-- equivalent case; that is not repeated here because no production code path has ever written a
-- receipt -- the only rows that can exist are test residue, which carries exactly these unresolvable
-- references, and stopping would break every developer database for rows nobody can act on.
--
-- An operator who knows which shop such rows belong to can say so before migrating, exactly as 0034
-- allowed for approvals:
--
--     SET ntl.legacy_receipt_store = '<store uuid>';
--
-- It is deliberately not defaulted. Naming the one shop a single-store deployment has is a
-- decision a person makes and is accountable for; inferring it would forge an attribution.
--
-- **New rows only.** The CHECK and the foreign keys are added NOT VALID: PostgreSQL enforces them on
-- every INSERT, and on every UPDATE that touches the constrained columns, but does not re-examine
-- rows written before this migration. So no legacy envelope or receipt is rewritten or refused
-- retroactively, and none of them can be *changed* into a state the new rules forbid.
--
-- Forward-only. Rolling back past this is a restore (`docs/runbooks/restore-drill.md`); the
-- structural parts could be dropped by hand, but a receipt's recorded store is data, not schema.

-- --- 1. recipients -------------------------------------------------------------------------------

-- Trivially unique -- `agent_run_id` is the primary key -- and required as the target of the
-- composite key below, which is what binds the pair rather than each half separately.
ALTER TABLE agent_drafts
    ADD CONSTRAINT agent_drafts_run_contact_key UNIQUE (agent_run_id, contact_binding_id);

ALTER TABLE manual_send_envelopes
    ADD CONSTRAINT manual_send_envelopes_recipient_is_the_drafts
    FOREIGN KEY (resource_id, recipient_binding_id)
    REFERENCES agent_drafts (agent_run_id, contact_binding_id)
    NOT VALID;

ALTER TABLE manual_send_envelopes
    ADD CONSTRAINT manual_send_envelopes_id_recipient_key UNIQUE (id, recipient_binding_id);

ALTER TABLE manual_send_attestations
    ADD CONSTRAINT manual_send_attestations_recipient_is_the_envelopes
    FOREIGN KEY (manual_send_envelope_id, recipient_binding_id)
    REFERENCES manual_send_envelopes (id, recipient_binding_id)
    NOT VALID;

-- --- 2. receipt store ----------------------------------------------------------------------------

ALTER TABLE channel_send_receipts ADD COLUMN store_id UUID NULL REFERENCES stores (id);

UPDATE channel_send_receipts c
   SET store_id = r.store_id
  FROM approval_requests r
 WHERE r.id = c.approval_ref
   AND c.store_id IS NULL;

DO $$
DECLARE
    fallback TEXT := current_setting('ntl.legacy_receipt_store', true);
    orphaned INT;
BEGIN
    SELECT count(*) INTO orphaned FROM channel_send_receipts WHERE store_id IS NULL;
    IF orphaned = 0 THEN
        RETURN;
    END IF;
    IF fallback IS NULL OR fallback = '' THEN
        RAISE WARNING
            '% channel send receipt(s) have no derivable store and stay unattributed: invisible '
            'to every store-scoped queue and unresolvable. Set ntl.legacy_receipt_store before '
            'migrating to attribute them.', orphaned;
        RETURN;
    END IF;
    UPDATE channel_send_receipts SET store_id = fallback::uuid WHERE store_id IS NULL;
END
$$;

-- Every receipt written from here on names its store.
ALTER TABLE channel_send_receipts
    ADD CONSTRAINT channel_send_receipts_store_required CHECK (store_id IS NOT NULL) NOT VALID;

-- And a human-approved receipt names its approval's store, not merely some store: the pair is
-- bound, so a worker cannot file store A's approved send under store B. Capability-authorised
-- receipts have no approval_ref, and MATCH SIMPLE leaves them to the CHECK above.
ALTER TABLE approval_requests
    ADD CONSTRAINT approval_requests_id_store_key UNIQUE (id, store_id);

ALTER TABLE channel_send_receipts
    ADD CONSTRAINT channel_send_receipts_approval_store
    FOREIGN KEY (approval_ref, store_id) REFERENCES approval_requests (id, store_id)
    NOT VALID;

-- The queue reads one store's unresolved receipts, oldest first. 0020's global partial index stays:
-- the worker-side `unresolved_unknown_outcomes` read still uses it.
CREATE INDEX channel_send_receipts_store_reconciliation_idx
    ON channel_send_receipts (store_id, recorded_at, receipt_id)
    WHERE reconciliation_state IN ('UNKNOWN', 'UNKNOWN_REQUIRES_HUMAN');
