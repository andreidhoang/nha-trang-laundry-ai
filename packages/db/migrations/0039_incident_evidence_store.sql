-- INCIDENT-INTAKE-001 / DEC-028: give a staff-opened incident somewhere to put the complaint.
--
-- `POST /internal/v1/stores/{store_id}/incidents` has always required `contact_scope_hash` and
-- `evidence_summary_hash`, and nothing in this repository has ever produced either. Measured on
-- 2026-09-10 by driving the screen and then searching for a producer. The consequence at the counter
-- is that a customer says their shirt came back stained and the staff member cannot finish the form,
-- so the complaint is written on paper and the shop's most commercially important signal -- what is
-- going wrong -- lives outside the system.
--
-- DEC-028 answers both halves. The contact scope is derived by the server from the order the
-- incident names, which needs no schema. The evidence summary is stored, which needs this file.
--
-- **Why storing it is safe now and was not in September.** The objection to storing the summary was
-- that free text a staff member types about a real person is a new personal-data class, and one with
-- no disposal route is a liability that accumulates. DEC-008 already schedules INCIDENT_EVIDENCE at
-- 365 days PURGE; what did not exist was a mechanism that could execute such a schedule against a
-- protected table. `RETENTION-STORE-001` built it on 2026-09-17, and this file is its second
-- application: the ledger row keeps the hash, the text lives beside it in a table the retention job
-- can actually empty.
--
-- The alternative answers were worse. Hashing the summary and discarding the text proves only that a
-- string nobody can read did not change, and leaves the owner making a DEC-004 remedy decision with
-- nothing to read. Recording no summary at all makes an incident a tally mark.

CREATE TABLE customer_incident_evidence (
    incident_id UUID PRIMARY KEY REFERENCES customer_incidents(id) ON DELETE RESTRICT,
    -- What the staff member typed, verbatim. `customer_incidents.evidence_summary_hash` is computed
    -- by the server over exactly these bytes, so the hash commits to what was kept rather than to
    -- something that was thrown away.
    summary TEXT NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 2000 AND btrim(summary) <> '')
);

-- Immutable while it exists, disposable when the schedule says so -- the same shape `0038` gave
-- `webhook_event_payloads`, and for the same reason: a complaint that can be edited after the fact
-- is not evidence of anything, and DELETE is the purge.
CREATE TRIGGER customer_incident_evidence_immutable
    BEFORE UPDATE ON customer_incident_evidence
    FOR EACH ROW EXECUTE FUNCTION reject_payload_rewrite();

-- The purge joins to `customer_incidents.opened_at` rather than copying it here, so the fact that
-- justifies a disposal survives the disposal.
CREATE INDEX customer_incidents_opened_at_idx ON customer_incidents (opened_at);

-- The disposal rests on `opened_at` being the moment the incident was opened, and on the hash still
-- describing the text that was disposed of. Neither was guaranteed: `customer_incidents` carries
-- only `reject_operational_hard_delete`, which refuses DELETE and leaves UPDATE entirely unguarded.
-- Nothing in the repository has ever updated this table -- verified by search -- so this guard adds
-- protection and removes none, and it is added now because `0039` is the change that starts relying
-- on those columns holding still.
--
-- The lifecycle columns stay writable, because an incident is meant to move: OPEN -> UNDER_REVIEW ->
-- CLOSED, and the two decision flags DEC-004 governs.
CREATE FUNCTION protect_customer_incident() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.order_id IS DISTINCT FROM OLD.order_id
       OR NEW.affected_message_id IS DISTINCT FROM OLD.affected_message_id
       OR NEW.contact_scope_hash IS DISTINCT FROM OLD.contact_scope_hash
       OR NEW.category IS DISTINCT FROM OLD.category
       OR NEW.evidence_summary_hash IS DISTINCT FROM OLD.evidence_summary_hash
       OR NEW.opened_at IS DISTINCT FROM OLD.opened_at THEN
        RAISE EXCEPTION 'an incident''s binding, evidence and opening time are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER customer_incidents_binding_immutable
    BEFORE UPDATE ON customer_incidents
    FOR EACH ROW EXECUTE FUNCTION protect_customer_incident();

-- DEC-020, same rule as 0038: the table and the permission to purge it ship together, and the purge
-- identity is not an identity that serves customers.
GRANT SELECT, DELETE ON customer_incident_evidence TO retention_purge;
GRANT SELECT ON customer_incidents TO retention_purge;
