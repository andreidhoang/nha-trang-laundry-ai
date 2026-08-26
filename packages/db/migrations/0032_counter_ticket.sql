-- Let a person who walks in off the street be taken in, without storing anything about them.
--
-- `DEC-013`, resolved 2026-08-26: a walk-in is identified by a number the counter issues. No name,
-- no phone number, no address. `DEC-015`, resolved the same day: no customer-record layer is built.
--
-- **There are no personal-data columns here and that is the entire design.** Nothing in this table
-- identifies a person, so there is no consent event to record, no retention schedule to write, and
-- nothing Decree 13/2023 obliges the shop over. The owner's reasoning is recorded in the registry
-- and is worth repeating where the schema is: adding a name later is additive and needs consent;
-- un-collecting a name already taken is not possible. The cheap option is also the reversible one.
--
-- `id` is what fills `orders.bound_contact_id`. It is an opaque identifier for an order's customer
-- reference, not for a customer -- the shop can tell two orders apart without knowing who either
-- belongs to, which is the whole point.
--
-- `ticket_number` is the human-facing part: what staff writes on the bag and says out loud. It
-- restarts each day per store, which is how a counter actually works, and is unique within that
-- day so two customers are never told the same number.

CREATE TABLE counter_tickets (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL,
    issued_on DATE NOT NULL,
    ticket_number INT NOT NULL CHECK (ticket_number >= 1),
    issued_by UUID NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    correlation_id UUID NOT NULL,
    UNIQUE (store_id, issued_on, ticket_number)
);

CREATE INDEX counter_tickets_store_day_idx ON counter_tickets (store_id, issued_on, ticket_number);

-- Append-only, by the shared ledger trigger. A ticket is a fact about what the counter handed
-- somebody; re-issuing or editing one would make two customers' orders indistinguishable.
CREATE TRIGGER counter_tickets_append_only
    BEFORE UPDATE OR DELETE ON counter_tickets
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
