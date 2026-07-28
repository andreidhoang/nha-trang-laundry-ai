-- Immutable quote revision snapshots and mutable container pointer.

CREATE TABLE quotes (
    id UUID PRIMARY KEY,
    store_id UUID NOT NULL,
    bound_order_request_id UUID NOT NULL,
    lifecycle TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (lifecycle IN ('OPEN', 'CONVERTED', 'CLOSED')),
    current_revision INTEGER NOT NULL CHECK (current_revision > 0),
    row_version BIGINT NOT NULL CHECK (row_version > 0),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (store_id, bound_order_request_id)
);

CREATE TABLE quote_revisions (
    quote_id UUID NOT NULL REFERENCES quotes(id),
    revision INTEGER NOT NULL CHECK (revision > 0),
    finality TEXT NOT NULL CHECK (finality IN ('ESTIMATE', 'RANGE', 'APPROVED_EXACT')),
    status TEXT NOT NULL CHECK (status IN (
        'DRAFT', 'PROVISIONAL', 'REVIEW_REQUIRED', 'APPROVED', 'PRESENTED',
        'ACKNOWLEDGED_ESTIMATE', 'ACCEPTED_FINAL', 'SUPERSEDED', 'EXPIRED', 'REJECTED'
    )),
    snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    snapshot_hash TEXT NOT NULL
        CHECK (snapshot_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    calculation_engine_version TEXT NOT NULL CHECK (length(calculation_engine_version) > 0),
    calculation_engine_hash TEXT NOT NULL
        CHECK (calculation_engine_hash ~ '^JCS-SHA256-V1:[0-9a-f]{64}$'),
    list_service_subtotal_min_vnd BIGINT NOT NULL CHECK (list_service_subtotal_min_vnd >= 0),
    list_service_subtotal_max_vnd BIGINT NOT NULL CHECK (
        list_service_subtotal_max_vnd >= list_service_subtotal_min_vnd
    ),
    discount_amount_min_vnd BIGINT NOT NULL CHECK (discount_amount_min_vnd >= 0),
    discount_amount_max_vnd BIGINT NOT NULL CHECK (
        discount_amount_max_vnd >= discount_amount_min_vnd
        AND discount_amount_max_vnd <= list_service_subtotal_max_vnd
    ),
    net_service_subtotal_min_vnd BIGINT NOT NULL CHECK (
        net_service_subtotal_min_vnd = list_service_subtotal_min_vnd - discount_amount_min_vnd
    ),
    net_service_subtotal_max_vnd BIGINT NOT NULL CHECK (
        net_service_subtotal_max_vnd = list_service_subtotal_max_vnd - discount_amount_max_vnd
    ),
    delivery_fee_vnd BIGINT NULL CHECK (delivery_fee_vnd >= 0),
    approved_surcharge_vnd BIGINT NOT NULL CHECK (approved_surcharge_vnd >= 0),
    display_total_min_vnd BIGINT NULL CHECK (display_total_min_vnd >= 0),
    display_total_max_vnd BIGINT NULL CHECK (display_total_max_vnd >= display_total_min_vnd),
    approval_id UUID NULL,
    priced_at TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NULL CHECK (valid_until > priced_at),
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (quote_id, revision),
    CHECK ((delivery_fee_vnd IS NULL) = (display_total_min_vnd IS NULL)),
    CHECK ((display_total_min_vnd IS NULL) = (display_total_max_vnd IS NULL)),
    CHECK (
        delivery_fee_vnd IS NULL
        OR display_total_min_vnd = net_service_subtotal_min_vnd
            + delivery_fee_vnd + approved_surcharge_vnd
    ),
    CHECK (
        delivery_fee_vnd IS NULL
        OR display_total_max_vnd = net_service_subtotal_max_vnd
            + delivery_fee_vnd + approved_surcharge_vnd
    ),
    CHECK (finality <> 'RANGE' OR status <> 'ACCEPTED_FINAL'),
    CHECK (finality <> 'ESTIMATE' OR (status <> 'ACCEPTED_FINAL' AND approval_id IS NULL)),
    CHECK (finality <> 'APPROVED_EXACT' OR approval_id IS NOT NULL)
);

CREATE INDEX quote_revisions_snapshot_hash_idx ON quote_revisions (snapshot_hash);

CREATE FUNCTION reject_quote_revision_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'quote revisions are immutable; create a new revision';
END;
$$;

CREATE TRIGGER quote_revisions_immutable
    BEFORE UPDATE OR DELETE ON quote_revisions
    FOR EACH ROW EXECUTE FUNCTION reject_quote_revision_mutation();

CREATE FUNCTION reject_quote_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'quotes cannot be hard deleted';
END;
$$;

CREATE TRIGGER quotes_no_hard_delete
    BEFORE DELETE ON quotes
    FOR EACH ROW EXECUTE FUNCTION reject_quote_delete();
