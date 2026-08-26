-- Record that a named staff member witnessed a customer accept an exact price.
--
-- `DEC-021`, resolved 2026-08-25: a quote is chốt when a named staff member confirms the customer
-- agreed to the price read to them; the staff member on duty at the counter may do it; and the
-- control is attribution, immutability and owner review rather than a second signature.
--
-- **Why this is a table and not an approval envelope.** The first attempt routed the attestation
-- through `approval_requests`, and `approvals.py:542` refused it: `requester cannot approve their
-- own action`. That rule is right and applies to every approval in the system -- an approval is two
-- parties, one asking and one authorising. What the owner ratified is one party recording a fact
-- they witnessed, which is a different thing, and the resolution says so itself by naming
-- `SETTLEMENT-001` as its precedent. `order_settlements` records money received the same way, in
-- its own table, for the same reason. Weakening separation of duty to fit an attestation into an
-- approval would have relaxed a control protecting sends, cancellations and every financial action.
--
-- `accepted_revision` is the revision the customer agreed to, and `accepted_snapshot_hash` is that
-- revision's digest, so the attestation names exact content rather than a revision number that
-- something else could later reinterpret. UNIQUE on `(quote_id, accepted_revision)` makes acceptance
-- single-shot: a quote cannot be accepted twice, and a replay is a conflict rather than a second
-- attestation by the same or a different person.
--
-- Append-only by the shared ledger trigger. Nobody edits who chốt or when.

CREATE TABLE quote_acceptances (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL,
    quote_id UUID NOT NULL,
    accepted_revision INT NOT NULL CHECK (accepted_revision >= 1),
    accepted_snapshot_hash TEXT NOT NULL CHECK (
        accepted_snapshot_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'
    ),
    -- The revision this attestation produced. Two numbers are needed and they are different things:
    -- `accepted_revision` is the priced revision whose price the customer heard, and
    -- `final_revision` is the APPROVED_EXACT revision derived from it, which is what an order is
    -- created against. A revision cannot be promoted in place -- `quote_revisions` is immutable --
    -- so acceptance always produces a new one, and recording only the first would leave the order
    -- guard inferring the link by arithmetic instead of reading it.
    final_revision INT NOT NULL CHECK (final_revision = accepted_revision + 1),
    display_total_vnd BIGINT NOT NULL CHECK (display_total_vnd >= 0),
    accepted_by UUID NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL,
    correlation_id UUID NOT NULL,
    policy_version TEXT NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 200),
    UNIQUE (quote_id, accepted_revision),
    UNIQUE (quote_id, final_revision),
    FOREIGN KEY (quote_id, accepted_revision) REFERENCES quote_revisions (quote_id, revision)
);

CREATE INDEX quote_acceptances_store_idx ON quote_acceptances (store_id, accepted_at DESC, id);

CREATE TRIGGER quote_acceptances_append_only
    BEFORE UPDATE OR DELETE ON quote_acceptances
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- An exact price no longer has to cite an approval envelope, because under DEC-021 its authority is
-- an acceptance attestation instead. The column and its foreign key from migration 0029 stay: an
-- envelope remains the right record for a two-party approval, and nothing in this change lets an
-- unreal envelope be cited.
-- Located by its definition rather than by name. `0005` declared this CHECK inline, so its name
-- is one PostgreSQL generated positionally (`quote_revisions_check12` here), and a migration that
-- hardcodes a generated name breaks on any database where the numbering came out differently.
DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'quote_revisions'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) = 'CHECK (((finality <> ''APPROVED_EXACT''::text) '
                                      'OR (approval_id IS NOT NULL)))';
    IF constraint_name IS NULL THEN
        RAISE EXCEPTION 'the APPROVED_EXACT approval_id check is not present to drop';
    END IF;
    EXECUTE format('ALTER TABLE quote_revisions DROP CONSTRAINT %I', constraint_name);
END
$$;
