-- Server-bound intake drafts are not commercial orders.

CREATE TABLE order_requests (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL,
    contact_binding_id UUID NOT NULL,
    conversation_binding_id UUID NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'SUBMITTED', 'CANCELLED')),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK (row_version >= 1),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX order_requests_contact_idx
    ON order_requests (store_id, contact_binding_id, created_at);

CREATE TRIGGER order_requests_no_hard_delete
    BEFORE DELETE ON order_requests
    FOR EACH ROW EXECUTE FUNCTION reject_operational_hard_delete();
