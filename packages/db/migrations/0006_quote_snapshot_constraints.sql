-- Forward-fix hardening after 0005: defend quote money/finality outside the repository path.

ALTER TABLE quote_revisions
    ADD CONSTRAINT quote_revision_discount_min_within_subtotal
        CHECK (discount_amount_min_vnd <= list_service_subtotal_min_vnd),
    ADD CONSTRAINT quote_revision_net_min_nonnegative
        CHECK (net_service_subtotal_min_vnd >= 0),
    ADD CONSTRAINT quote_revision_net_max_nonnegative
        CHECK (net_service_subtotal_max_vnd >= 0),
    ADD CONSTRAINT quote_revision_approved_exact_amounts
        CHECK (
            finality <> 'APPROVED_EXACT'
            OR (
                list_service_subtotal_min_vnd = list_service_subtotal_max_vnd
                AND discount_amount_min_vnd = discount_amount_max_vnd
                AND net_service_subtotal_min_vnd = net_service_subtotal_max_vnd
                AND delivery_fee_vnd IS NOT NULL
                AND display_total_min_vnd = display_total_max_vnd
                AND status NOT IN (
                    'DRAFT', 'PROVISIONAL', 'REVIEW_REQUIRED', 'ACKNOWLEDGED_ESTIMATE'
                )
            )
        );
