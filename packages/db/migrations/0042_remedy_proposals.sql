-- REMEDY-001 / DEC-004: give an incident somewhere to end.
--
-- `INCIDENT-INTAKE-001` let a staff member record the complaint and the thread then stopped: an
-- incident could not lead to any money or order outcome. The vocabulary for one existed and nothing
-- was behind it -- `ApprovalAction.APPROVE_REMEDY`, the `REMEDY_PROPOSAL` resource type,
-- `AdjustmentDirection.CREDIT` -- so the shop resolved complaints verbally, and the 5x ceiling and
-- the 100.000d escalation the owner ratified on 2026-08-18 were enforced by nothing.
--
-- What this adds is the **accountability layer**, not a state machine. The rewash mechanic already
-- exists (`0037`, and `EXCEPTION` -> earlier production state since `DEC-024`); the incident status
-- ladder already exists (`0014`); the approval envelope already exists (`0007`, `0034`). What did
-- not exist is a record saying who decided store fault, on what evidence, inside which window,
-- against which published policy version, and with which approval.
--
-- Three objects, and each is answering a question `DEC-004` asks.

-- 1. When the customer got their laundry back.
--
-- `DEC-004`'s two windows are measured from one moment: `CUSTOMER_SERVICE_POLICY_DRAFT.md` s5 says
-- "khach ... bao som neu co sai sot ... trong 24 gio sau khi nhan do" -- the *customer* receiving,
-- not the shop. The rewash window runs from pickup, which is that same handover seen from the shop's
-- side. Neither timestamp existed. `0037` added `production_ready_at` (when production reported the
-- work finished) and `closed_at` is set when the commercial order completes, which is a different
-- event again and can be days later.
--
-- Nullable, and NULL for every order that already exists: no timestamp for them was ever taken, and
-- inventing one -- the row's creation, the settlement, `now` -- would be a measurement nobody made.
-- A remedy against such an order is refused with `REMEDY_WINDOW_EVIDENCE_MISSING`, which is what
-- "we do not know when this customer got their laundry" honestly is.
--
-- Write-once by the COALESCE in `OrderRepository.transition`: `RELEASED` is terminal on the
-- production dimension, so there is no second release to record, and the column would be wrong
-- rather than merely stale if a later command moved it.
ALTER TABLE orders ADD COLUMN production_released_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN orders.production_released_at IS
    'When production first recorded RELEASED: the moment the customer collected at the counter. '
    'Starts the DEC-004 remedy windows for a self-collected order; a delivered order uses the '
    'succeeded RETURN leg instead.';

-- 2. The proposal itself.
--
-- One row per remedy a named staff member proposed against one incident. Nothing here is typed by
-- staff except the kind, the fault attestation, the damaged line and -- for damage alone -- the
-- amount: `ceiling_vnd` is computed by the server from the order's own priced line or settled
-- total, and `window_opened_at`/`window_closes_at` are computed from the recorded handover above.
-- Storing the computed values rather than recomputing them later is the point of an accountability
-- record: it says which bound this proposal was actually checked against, even after the owner
-- republishes the policy.
CREATE TABLE remedy_proposals (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL REFERENCES stores(id),
    incident_id UUID NOT NULL REFERENCES customer_incidents(id),
    order_id UUID NOT NULL REFERENCES orders(id),

    kind TEXT NOT NULL CHECK (kind IN (
        'FREE_REWASH', 'DAMAGE_COMPENSATION', 'LATE_DELIVERY_CREDIT', 'LOST_ITEM'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'STAFF_AUTHORIZED', 'OWNER_APPROVAL_REQUIRED', 'EXECUTED', 'POLICY_UNRESOLVED'
    )),

    -- Invariant 2: money is a non-negative integer and the sign lives in the direction, never in
    -- the amount. A remedy is owed *to* the customer, so the only direction this table admits is
    -- CREDIT -- there is no shape here in which a remedy takes money from anyone.
    amount_vnd BIGINT NULL CHECK (amount_vnd IS NULL OR amount_vnd >= 0),
    direction TEXT NULL CHECK (direction IS NULL OR direction = 'CREDIT'),
    ceiling_vnd BIGINT NULL CHECK (ceiling_vnd IS NULL OR ceiling_vnd >= 0),
    -- Which priced line of the order's own revision the damage was to. The 5x cap is a multiple of
    -- what the shop charged for *that item*, so the line is part of the record, not a detail.
    order_line_id TEXT NULL,

    -- Which published `REMEDY_POLICY` version supplied the figures. Invariant 4: the document is
    -- immutable and versioned, so a proposal checked against version 2 stays checked against
    -- version 2 after version 3 is published.
    policy_version_id UUID NOT NULL REFERENCES configuration_versions(id),
    policy_version INTEGER NOT NULL CHECK (policy_version > 0),

    -- The staff finding `DEC-004` rests every remedy on, and the lateness the shop cannot derive.
    -- There is no recorded promised-arrival time anywhere in this schema, so ">2 hours late" is an
    -- attested fact; what *is* checkable -- that a return leg succeeded at all -- is checked
    -- against `delivery_legs` before a proposal is written.
    store_fault_attested BOOLEAN NOT NULL,
    attested_late_by_minutes INTEGER NULL CHECK (
        attested_late_by_minutes IS NULL OR attested_late_by_minutes >= 0
    ),

    window_opened_at TIMESTAMPTZ NULL,
    window_closes_at TIMESTAMPTZ NULL,

    -- Invariant 8: an approval binds an exact rendered-content hash. This is that content's digest,
    -- computed by `remedies.remedy_proposal_document` over the proposal's identity, amount, ceiling
    -- and policy version, so an envelope approving 150.000d of damage on one incident cannot be
    -- replayed against another incident, another amount, or a policy version the owner never saw.
    proposal_hash TEXT NOT NULL CHECK (proposal_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    approval_id UUID NULL REFERENCES approval_requests(id),

    proposed_by UUID NOT NULL REFERENCES staff_users(id),
    proposed_at TIMESTAMPTZ NOT NULL,
    executed_at TIMESTAMPTZ NULL,
    correlation_id UUID NOT NULL,
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version > 0),

    -- Each kind owns exactly one shape, stated by the database as well as by the domain. A rewash
    -- moves no money; a damage claim names a line and an amount; a late-delivery credit names a
    -- lateness and an amount the server computed; loss names nothing at all, because `DEC-004`
    -- carries it forward as unresolved and any figure on such a row would be one nobody ratified.
    CONSTRAINT remedy_proposals_shape_matches_kind CHECK (
        (kind = 'FREE_REWASH' AND amount_vnd IS NULL AND direction IS NULL
            AND ceiling_vnd IS NULL AND order_line_id IS NULL
            AND attested_late_by_minutes IS NULL)
        OR (kind = 'DAMAGE_COMPENSATION' AND amount_vnd IS NOT NULL AND direction = 'CREDIT'
            AND ceiling_vnd IS NOT NULL AND order_line_id IS NOT NULL
            AND attested_late_by_minutes IS NULL AND amount_vnd <= ceiling_vnd)
        OR (kind = 'LATE_DELIVERY_CREDIT' AND amount_vnd IS NOT NULL AND direction = 'CREDIT'
            AND ceiling_vnd IS NOT NULL AND order_line_id IS NULL
            AND attested_late_by_minutes IS NOT NULL AND amount_vnd <= ceiling_vnd)
        OR (kind = 'LOST_ITEM' AND amount_vnd IS NULL AND direction IS NULL
            AND ceiling_vnd IS NULL AND order_line_id IS NULL
            AND attested_late_by_minutes IS NULL)
    ),
    -- Loss is the only kind that reaches POLICY_UNRESOLVED, and it reaches nothing else. This is
    -- the schema's own statement of the refusal: there is no sequence of updates that carries a
    -- loss record to EXECUTED, so no later code path can accidentally pay one out.
    CONSTRAINT remedy_proposals_loss_is_unresolved CHECK (
        (status = 'POLICY_UNRESOLVED') = (kind = 'LOST_ITEM')
    ),
    CONSTRAINT remedy_proposals_owner_approval_has_envelope CHECK (
        status <> 'OWNER_APPROVAL_REQUIRED' OR approval_id IS NOT NULL
    ),
    CONSTRAINT remedy_proposals_executed_is_timestamped CHECK (
        (status = 'EXECUTED') = (executed_at IS NOT NULL)
    ),
    CONSTRAINT remedy_proposals_window_is_a_window CHECK (
        (window_opened_at IS NULL) = (window_closes_at IS NULL)
        AND (window_closes_at IS NULL OR window_closes_at > window_opened_at)
    )
);

CREATE INDEX remedy_proposals_incident_idx ON remedy_proposals (incident_id, proposed_at DESC);
CREATE INDEX remedy_proposals_store_idx ON remedy_proposals (store_id, proposed_at DESC, id);

-- A proposal is an attestation about money, so almost all of it is immutable. What moves is the
-- execution: the status, when it happened, and the row version that guards it. The binding, the
-- amount, the ceiling, the window and the digest an owner approved may never change -- an editable
-- proposal would make invariant 8 a comment, because the approval binds the digest of content that
-- could then be rewritten underneath it.
CREATE FUNCTION protect_remedy_proposal() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.incident_id IS DISTINCT FROM OLD.incident_id
       OR NEW.order_id IS DISTINCT FROM OLD.order_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.amount_vnd IS DISTINCT FROM OLD.amount_vnd
       OR NEW.direction IS DISTINCT FROM OLD.direction
       OR NEW.ceiling_vnd IS DISTINCT FROM OLD.ceiling_vnd
       OR NEW.order_line_id IS DISTINCT FROM OLD.order_line_id
       OR NEW.policy_version_id IS DISTINCT FROM OLD.policy_version_id
       OR NEW.policy_version IS DISTINCT FROM OLD.policy_version
       OR NEW.store_fault_attested IS DISTINCT FROM OLD.store_fault_attested
       OR NEW.attested_late_by_minutes IS DISTINCT FROM OLD.attested_late_by_minutes
       OR NEW.window_opened_at IS DISTINCT FROM OLD.window_opened_at
       OR NEW.window_closes_at IS DISTINCT FROM OLD.window_closes_at
       OR NEW.proposal_hash IS DISTINCT FROM OLD.proposal_hash
       OR NEW.approval_id IS DISTINCT FROM OLD.approval_id
       OR NEW.proposed_by IS DISTINCT FROM OLD.proposed_by
       OR NEW.proposed_at IS DISTINCT FROM OLD.proposed_at
       OR NEW.row_version <> OLD.row_version + 1 THEN
        RAISE EXCEPTION 'a remedy proposal''s binding, amount and approval are immutable';
    END IF;
    -- One direction of travel, and nothing leaves EXECUTED or POLICY_UNRESOLVED. A remedy that was
    -- paid cannot be un-paid by an UPDATE; that is an incident of its own, exactly as a settlement
    -- recorded in error is.
    IF NOT (
        (OLD.status IN ('STAFF_AUTHORIZED', 'OWNER_APPROVAL_REQUIRED') AND NEW.status = 'EXECUTED')
    ) THEN
        RAISE EXCEPTION 'invalid remedy proposal state transition';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER remedy_proposals_protected
    BEFORE UPDATE ON remedy_proposals
    FOR EACH ROW EXECUTE FUNCTION protect_remedy_proposal();

CREATE TRIGGER remedy_proposals_no_hard_delete
    BEFORE DELETE ON remedy_proposals
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();

-- 3. The credit an executed money remedy creates.
--
-- `DEC-004` says "10% credit on the next bill", and there is no next bill yet. The settlement ledger
-- is not touched to produce one: `order_settlements` is append-only, `reject_ledger_mutation()`
-- would refuse a rewrite, and `DEC-010` keeps the settlement path accepting only the exact quoted
-- total in full. An approved remedy creates a separate forward obligation instead, and this is it.
--
-- **It is a bearer instrument and that is deliberate.** `DEC-015` refuses to build a customer
-- record, so there is nothing to attach a balance to. The credit is issued against the counter
-- ticket or channel binding the order already carries -- the same two sources `OrderRepository`
-- checks -- and redeemed by presenting that ticket. Whoever holds the number can redeem it; at 10%
-- of a laundry bill the exposure is proportionate, and the alternative is the customer ledger this
-- system has decided not to build.
--
-- **Exactly once**, enforced here rather than trusted: the redemption columns are write-once by the
-- trigger below and the repository's UPDATE is a compare-and-swap on `redeemed_at IS NULL`, so two
-- concurrent redemptions cannot both win.
CREATE TABLE remedy_credits (
    id UUID PRIMARY KEY,
    -- One credit per executed proposal. A second execution of the same proposal would be a second
    -- credit for one incident, which is what this UNIQUE makes impossible.
    remedy_proposal_id UUID NOT NULL UNIQUE REFERENCES remedy_proposals(id),
    store_id UUID NOT NULL REFERENCES stores(id),
    -- What the credit was issued against: `orders.bound_contact_id`, which is a counter ticket or a
    -- channel binding. Not a foreign key for the same reason that column is not one -- `DEC-015`
    -- declines to unify the two sources behind a party layer, because unifying them *is* the
    -- customer-record layer the decision says not to build.
    bearer_contact_id UUID NOT NULL,
    issued_from_order_id UUID NOT NULL REFERENCES orders(id),

    amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
    direction TEXT NOT NULL CHECK (direction = 'CREDIT'),
    policy_version_id UUID NOT NULL REFERENCES configuration_versions(id),

    issued_at TIMESTAMPTZ NOT NULL,
    redeemed_at TIMESTAMPTZ NULL,
    redeemed_quote_id UUID NULL,
    redeemed_quote_revision INTEGER NULL CHECK (
        redeemed_quote_revision IS NULL OR redeemed_quote_revision > 0
    ),
    row_version BIGINT NOT NULL DEFAULT 1 CHECK (row_version > 0),

    -- A redemption is one fact in three columns; a row carrying some of them describes a redemption
    -- that half happened, which is not a state the counter can act on.
    CONSTRAINT remedy_credits_redemption_is_complete CHECK (
        (redeemed_at IS NULL AND redeemed_quote_id IS NULL AND redeemed_quote_revision IS NULL)
        OR (redeemed_at IS NOT NULL AND redeemed_quote_id IS NOT NULL
            AND redeemed_quote_revision IS NOT NULL)
    ),
    FOREIGN KEY (redeemed_quote_id, redeemed_quote_revision)
        REFERENCES quote_revisions(quote_id, revision)
);

CREATE INDEX remedy_credits_bearer_idx ON remedy_credits (bearer_contact_id, issued_at DESC);
CREATE INDEX remedy_credits_open_idx ON remedy_credits (store_id, issued_at DESC)
    WHERE redeemed_at IS NULL;

CREATE FUNCTION protect_remedy_credit() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.remedy_proposal_id IS DISTINCT FROM OLD.remedy_proposal_id
       OR NEW.store_id IS DISTINCT FROM OLD.store_id
       OR NEW.bearer_contact_id IS DISTINCT FROM OLD.bearer_contact_id
       OR NEW.issued_from_order_id IS DISTINCT FROM OLD.issued_from_order_id
       OR NEW.amount_vnd IS DISTINCT FROM OLD.amount_vnd
       OR NEW.direction IS DISTINCT FROM OLD.direction
       OR NEW.policy_version_id IS DISTINCT FROM OLD.policy_version_id
       OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
       OR NEW.row_version <> OLD.row_version + 1 THEN
        RAISE EXCEPTION 'an issued remedy credit is immutable';
    END IF;
    -- The single-use rule, in the database. The repository's compare-and-swap is the first line and
    -- this is the second: once a credit names the revision it was spent on, no statement may point
    -- it at another one or hand it back unspent.
    IF OLD.redeemed_at IS NOT NULL THEN
        RAISE EXCEPTION 'a remedy credit is redeemable exactly once';
    END IF;
    IF NEW.redeemed_at IS NULL THEN
        RAISE EXCEPTION 'the only update to a remedy credit is its redemption';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER remedy_credits_protected
    BEFORE UPDATE ON remedy_credits
    FOR EACH ROW EXECUTE FUNCTION protect_remedy_credit();

CREATE TRIGGER remedy_credits_no_hard_delete
    BEFORE DELETE ON remedy_credits
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();
