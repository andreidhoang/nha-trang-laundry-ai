-- RANGE-APPROVAL-VISIBILITY-001: let the owner see the amount they are being asked to authorise.
--
-- `RANGE-PRICE-001` shipped with the approval envelope holding only a `rendered_hash`, and the
-- proposed amounts persisted nowhere at all. The owner opened the approvals queue, followed the
-- link to the quote, and saw the BAND -- "Áo dài truyền thống · 80.000 ₫ – 240.000 ₫" -- never the
-- number. So a staff member could agree 150.000 ₫ with the customer, propose 240.000 ₫, and the
-- owner's approval, which is the item's only second-party control over that number, rubber-stamped
-- a figure nobody on the approving side had read.
--
-- **What this table is, and what it is emphatically not.** It is a DISPLAY record: the content of
-- a proposal, kept so a human can read it before deciding. It is NOT the thing `rendered_hash` is
-- checked against. `apply_range_prices` still re-derives the digest from the amounts the caller
-- holds and refuses unless it equals the one the owner approved -- `operations.py` calls comparing
-- an unstored rendering "theatre" and is right. Reading these rows to satisfy that check would
-- make the digest a digest of itself. They are two separate jobs: this one answers "what am I
-- approving?", and the re-derivation answers "is this the same content?".
--
-- The rows are therefore written with the same immutability as any other attestation about money.
-- A mutable copy would be worse than no copy: it would show the owner one number while a different
-- one travelled to `apply_range_prices`, and the owner would have read something that was true
-- when it was written and false when it was used.
--
-- Two objects, because a proposal has one identity and many lines.

-- 1. The proposal, keyed by the envelope it was raised for.
--
-- `approval_id` is the primary key rather than a surrogate with a UNIQUE beside it: one
-- `SET_RANGE_PRICE` envelope is exactly one set of proposed amounts, and there is no version of
-- this record that exists without an envelope to be read from. `ApprovalRepository.request` is
-- idempotent, so a replayed proposal command returns the same envelope id and this primary key is
-- what makes the second write a conflict instead of a second set of amounts under one approval.
--
-- Everything here except the amounts on the child table is server-derived, and is stored rather
-- than recomputed for the reason `remedy_proposals` gives: an accountability record says which
-- bound the proposal was actually checked against, and stays true after the owner republishes the
-- pricebook.
CREATE TABLE range_price_proposals (
    approval_id UUID PRIMARY KEY REFERENCES approval_requests(id),
    store_id UUID NOT NULL REFERENCES stores(id),

    -- The revision the amounts were chosen against. A composite foreign key rather than a bare
    -- quote id, because a band closed on revision 1 has nothing to do with revision 2 of the same
    -- quote -- that is the edit that invalidates the approval, and the row must not be readable as
    -- though it described the new revision.
    quote_id UUID NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    FOREIGN KEY (quote_id, revision) REFERENCES quote_revisions(quote_id, revision),

    -- Which published pricebook version drew the bands below. Invariant 4: the band that
    -- authorises an amount is the one the revision was priced against, never whichever is newest.
    pricebook_version_id UUID NOT NULL REFERENCES configuration_versions(id),
    pricebook_version INTEGER NOT NULL CHECK (pricebook_version > 0),

    -- The digest of `range_price_rendered_document(attestation)` -- the same value the envelope
    -- carries. Stored so a reader can say *which* rendering these amounts are the content of, and
    -- so a row whose envelope has been superseded is recognisably about the older content. It is
    -- never the input to the application-time comparison; see the header.
    rendered_hash TEXT NOT NULL CHECK (rendered_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),

    proposed_by UUID NOT NULL REFERENCES staff_users(id),
    proposed_at TIMESTAMPTZ NOT NULL,
    correlation_id UUID NOT NULL
);

COMMENT ON TABLE range_price_proposals IS
    'What question does this answer: "the owner is being asked to approve a price inside a '
    'published band -- which price?". One row per SET_RANGE_PRICE envelope, holding the amounts a '
    'named staff member proposed and the bands they were checked against, so the approver reads '
    'the number instead of a digest of it. Display evidence only: application still re-derives '
    'the rendered hash from the amounts in hand and refuses unless it equals the approved one.';

CREATE INDEX range_price_proposals_store_idx
    ON range_price_proposals (store_id, proposed_at DESC, approval_id);
CREATE INDEX range_price_proposals_quote_idx ON range_price_proposals (quote_id, revision);

-- 2. One line of it: the band the owner published, and the amount inside it a person chose.
--
-- Both ends of the band are stored beside the amount rather than left to be re-read from the quote
-- revision, because what the approver has to be shown is the bound *this amount was checked
-- against*. Re-reading it elsewhere at display time is how a screen comes to show an amount beside
-- a band that did not authorise it.
CREATE TABLE range_price_proposal_amounts (
    approval_id UUID NOT NULL REFERENCES range_price_proposals(approval_id),
    service_code TEXT NOT NULL CHECK (length(service_code) > 0),

    -- Invariant 2: money is a non-negative integer number of dong. There is no direction column
    -- here because there is no direction to record -- a price is what the customer pays, and the
    -- one shape this table admits is a price.
    band_minimum_vnd BIGINT NOT NULL CHECK (band_minimum_vnd >= 0),
    band_maximum_vnd BIGINT NOT NULL CHECK (band_maximum_vnd >= 0),
    proposed_amount_vnd BIGINT NOT NULL CHECK (proposed_amount_vnd >= 0),

    -- One amount per line, stated by the primary key. Two amounts for one service would give the
    -- approver two numbers and no way to know which one the envelope authorises, which is the same
    -- ambiguity `resolve_range_prices` refuses with RANGE_PRICE_NOT_APPLICABLE.
    PRIMARY KEY (approval_id, service_code),

    CONSTRAINT range_price_proposal_amounts_band_is_a_band CHECK (
        band_maximum_vnd >= band_minimum_vnd
    ),
    -- The server already refused an out-of-band amount before any envelope existed. This says the
    -- same thing in the schema, so a row that would display an unauthorised number to an approver
    -- cannot be written at all -- by this code or by any later one.
    CONSTRAINT range_price_proposal_amounts_amount_is_in_band CHECK (
        proposed_amount_vnd BETWEEN band_minimum_vnd AND band_maximum_vnd
    )
);

COMMENT ON TABLE range_price_proposal_amounts IS
    'One proposed line of a SET_RANGE_PRICE proposal: the service, the published band it was '
    'checked against, and the exact amount inside that band a named staff member chose.';

-- Immutable in full, both tables. Nothing about a proposal ever changes: an edit is a new
-- revision, a new envelope and a new row, which is precisely how invariant 8 invalidates an
-- approval. An UPDATE here would let the number the owner read drift away from the number the
-- owner authorised, which is the defect this migration exists to close, reintroduced from inside.
CREATE FUNCTION protect_range_price_proposal() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'a proposed range price is immutable; propose again on a new revision';
END;
$$;

CREATE TRIGGER range_price_proposals_protected
    BEFORE UPDATE ON range_price_proposals
    FOR EACH ROW EXECUTE FUNCTION protect_range_price_proposal();

CREATE TRIGGER range_price_proposal_amounts_protected
    BEFORE UPDATE ON range_price_proposal_amounts
    FOR EACH ROW EXECUTE FUNCTION protect_range_price_proposal();

CREATE TRIGGER range_price_proposals_no_hard_delete
    BEFORE DELETE ON range_price_proposals
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

CREATE TRIGGER range_price_proposal_amounts_no_hard_delete
    BEFORE DELETE ON range_price_proposal_amounts
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();
