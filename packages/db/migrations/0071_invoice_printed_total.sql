-- MONEY-RESIDUAL-009B (round 9b, J6a): an invoice already issued at the provider is recorded at the
-- figure it prints, never refused into a dead end.
--
-- `0068` fixes an issued request at what its orders cost when *Ghi số hóa đơn* is pressed, and the
-- round-9 rule (`orders_on_invoice`) refused the press when the total typed off the invoice was not
-- that figure. A typing slip is the common cause, so the press is still refused first. But the
-- invoice is a document the provider has already issued: when a storage fee accrued (or was
-- settled) between the bookkeeper making it and the owner recording it, the figures can never agree
-- again, and the request could only be cancelled -- the issued invoice then lived nowhere in the
-- app. When the owner confirms the typed total is what the invoice prints, the request is now
-- recorded with that printed figure kept beside the shop's own:
--
-- * `total_vnd` and the snapshot's lines stay what the orders cost at the press -- `0068`'s
--   commit-time check (the lines add up to the total) is unchanged;
-- * `printed_total_vnd` is the invoice's printed total, set only when it differs from `total_vnd`
--   (NULL for every row written before this migration and for every invoice that agrees).
--
-- The read shows the printed figure as the invoice's and flags it ("Số trên hóa đơn khác số hiện
-- tại — báo kế toán"); the bookkeeper's download lists it with that flag.
--
-- Additive and forward-only: the snapshot tables stay append-only (`0068`'s triggers), the column is
-- written only at INSERT. Rolling the code back leaves a column the older code never names; its
-- inserts write NULL, which is what an agreeing invoice holds.

ALTER TABLE invoice_request_snapshots
    ADD COLUMN printed_total_vnd BIGINT NULL,
    ADD CONSTRAINT invoice_request_snapshots_printed_total CHECK (
        printed_total_vnd IS NULL
        OR (
            printed_total_vnd >= 0
            AND total_vnd IS NOT NULL
            AND printed_total_vnd <> total_vnd
        )
    );

COMMENT ON COLUMN invoice_request_snapshots.printed_total_vnd IS
    'Round 9b (J6a): the total printed on an invoice already issued at the provider, recorded when '
    'the owner confirmed it differs from what the orders cost at the press (total_vnd, the sum of '
    'the lines). NULL when the invoice agrees. Flagged PRINTED_TOTAL_DIFFERS on every read.';
