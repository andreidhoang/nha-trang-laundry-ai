-- LATE-CREDIT-002 (DEC-042): what a person decided about a delivery the server measured as late.
--
-- The lateness itself is not stored as a fact of the order: it is measured, every time it is read,
-- by `nha_trang_laundry_domain.late_delivery.late_delivery_clock` from the first promise (`0057`),
-- the customer-requested *Hẹn lại* rows (`order_promise_changes`) and the delivery legs (`0033`).
-- What is stored here is the one thing a person adds: whose fault it was.
--
-- * `STORE_FAULT_CREDITED` is written in the same transaction as the late-delivery incident and
--   its `LATE_DELIVERY_CREDIT` remedy proposal, so it names both. The proposal's attested minutes
--   are the server's measurement, and the trigger below refuses a decision whose minutes disagree
--   with its proposal's -- the two rows cannot tell two different stories about one delivery.
-- * `NOT_STORE_FAULT` carries a reason code; `OTHER` needs a few words. The note stays in this
--   table only: no event, audit or outbox payload carries it (the `0057` promise-change rule).
--
-- One decision per order (`order_id` UNIQUE), append-only. `late_by_minutes`, `deadline_at` and
-- `delivered_at` are the measurement as it stood when the decision was made, kept so the decision
-- can be read back years later without re-deriving it.
--
-- Additive and forward-only: a new table nothing older reads. Rolling the code back leaves the
-- rows in place, unread; the manual remedy path is unchanged either way (DEC-042's reversal).
CREATE TABLE late_delivery_decisions (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL UNIQUE REFERENCES orders(id),
    store_id UUID NOT NULL REFERENCES stores(id),
    decision TEXT NOT NULL CHECK (decision IN ('STORE_FAULT_CREDITED', 'NOT_STORE_FAULT')),
    reason_code TEXT CHECK (reason_code IS NULL OR reason_code IN (
        'CUSTOMER_ABSENT', 'CUSTOMER_WRONG_ADDRESS', 'CUSTOMER_ASKED_LATER', 'OTHER'
    )),
    note TEXT CHECK (note IS NULL OR (length(btrim(note)) BETWEEN 1 AND 120)),
    late_by_minutes INTEGER NOT NULL CHECK (late_by_minutes >= 0),
    deadline_at TIMESTAMPTZ NOT NULL,
    deadline_basis TEXT NOT NULL CHECK (deadline_basis IN ('FIRST_PROMISE', 'CUSTOMER_REQUEST')),
    delivered_at TIMESTAMPTZ NOT NULL,
    incident_id UUID REFERENCES customer_incidents(id),
    remedy_proposal_id UUID UNIQUE REFERENCES remedy_proposals(id),
    decided_by UUID NOT NULL REFERENCES staff_users(id),
    decided_at TIMESTAMPTZ NOT NULL,
    -- The shop's fault names its incident and its credit proposal; the customer's side names a
    -- reason and neither of those.
    CHECK (
        (decision = 'STORE_FAULT_CREDITED'
            AND remedy_proposal_id IS NOT NULL AND incident_id IS NOT NULL
            AND reason_code IS NULL AND note IS NULL)
        OR (decision = 'NOT_STORE_FAULT'
            AND remedy_proposal_id IS NULL AND incident_id IS NULL
            AND reason_code IS NOT NULL)
    ),
    CHECK (reason_code IS DISTINCT FROM 'OTHER' OR note IS NOT NULL)
);

CREATE INDEX late_delivery_decisions_store_idx
    ON late_delivery_decisions (store_id, decided_at, id);

CREATE TRIGGER late_delivery_decisions_append_only
    BEFORE UPDATE OR DELETE ON late_delivery_decisions
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE FUNCTION enforce_late_delivery_decision() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    proposal RECORD;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM orders o WHERE o.id = NEW.order_id AND o.store_id = NEW.store_id
    ) THEN
        RAISE EXCEPTION 'a late-delivery decision belongs to its order''s store';
    END IF;
    IF NEW.remedy_proposal_id IS NOT NULL THEN
        SELECT p.order_id, p.store_id, p.incident_id, p.kind, p.attested_late_by_minutes
        INTO proposal
        FROM remedy_proposals p WHERE p.id = NEW.remedy_proposal_id;
        IF proposal.order_id IS DISTINCT FROM NEW.order_id
           OR proposal.store_id IS DISTINCT FROM NEW.store_id
           OR proposal.incident_id IS DISTINCT FROM NEW.incident_id
           OR proposal.kind IS DISTINCT FROM 'LATE_DELIVERY_CREDIT'
           OR proposal.attested_late_by_minutes IS DISTINCT FROM NEW.late_by_minutes THEN
            RAISE EXCEPTION 'a credited late delivery must name its own order''s late-delivery '
                'credit, attested at the measured minutes';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER late_delivery_decisions_binding
    BEFORE INSERT ON late_delivery_decisions
    FOR EACH ROW EXECUTE FUNCTION enforce_late_delivery_decision();

COMMENT ON TABLE late_delivery_decisions IS
    'LATE-CREDIT-002 / DEC-042: one append-only decision per measured late delivery -- the shop''s '
    'fault (with its incident and LATE_DELIVERY_CREDIT proposal) or not (with a reason).';
