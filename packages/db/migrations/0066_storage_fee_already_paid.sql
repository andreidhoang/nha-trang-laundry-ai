-- MONEY-LIFECYCLE-009 (M1, DEC-035 x DEC-036): a storage fee the customer already paid part of
-- stays owed-for when the fee later falls.
--
-- `0060` fixes the storage fee only when the payment that settles the order is taken, and records
-- the accrual that produced it (`days_waiting`, `chargeable_days`, the policy version). Until then
-- the fee is recomputed at every read. A part payment can cover part of the fee (100.000 d quoted,
-- 5.000 d accrued, 103.000 d paid) and a later event can lower the recomputed fee -- a waiver, a
-- hold, a rewash that restarts the free days, the owner withdrawing the policy, a cancellation.
-- The rule the code now applies (`unclaimed.fee_already_paid`): money that moved is never un-owed
-- by a later event. The part of the fee the ledger already holds stays owed-for; returning it is a
-- refund, a separate and explicit act. DEC-036's own reversal clause says the same thing for a
-- withdrawn policy: "recorded fees stay on their orders".
--
-- When such an order is settled -- by the waiver that leaves nothing owed, or by a 0 d settlement
-- of a ledger that already covers what is owed -- its fee is fixed at that kept part. There is no
-- accrual to record for it: the policy may be withdrawn, the free days may have restarted, the
-- order may be on hold. So this migration adds exactly that second kind of fixed fee:
--
--   ACCRUED       the fee the published policy computed when it was fixed; the trace columns are
--                 filled, as for every row written before this migration (the default).
--   ALREADY_PAID  the part of the fee the order's payments had already covered when the fee fell;
--                 the trace columns are NULL, because no accrual produced this figure.
--
-- Additive and forward-only. Every existing row reads ACCRUED with its trace intact, and `0060`'s
-- and `0061`'s commit-time checks -- the settlement or the account charge owes the quoted total
-- plus this amount -- apply to both kinds unchanged. Rolling the code back leaves a column with a
-- default the older code never names: its inserts still write ACCRUED rows with their trace.

ALTER TABLE order_storage_fees
    ADD COLUMN basis TEXT NOT NULL DEFAULT 'ACCRUED'
        CONSTRAINT order_storage_fees_basis_check CHECK (basis IN ('ACCRUED', 'ALREADY_PAID')),
    ALTER COLUMN days_waiting DROP NOT NULL,
    ALTER COLUMN chargeable_days DROP NOT NULL,
    ALTER COLUMN policy_version_id DROP NOT NULL,
    ADD CONSTRAINT order_storage_fees_trace_matches_basis CHECK (
        (
            basis = 'ACCRUED'
            AND days_waiting IS NOT NULL
            AND chargeable_days IS NOT NULL
            AND policy_version_id IS NOT NULL
        )
        OR (
            basis = 'ALREADY_PAID'
            AND days_waiting IS NULL
            AND chargeable_days IS NULL
            AND policy_version_id IS NULL
        )
    );

COMMENT ON COLUMN order_storage_fees.basis IS
    'ACCRUED: the fee the published storage policy computed when it was fixed (days_waiting, '
    'chargeable_days and policy_version_id are its trace). ALREADY_PAID: the part of the fee the '
    'order''s payments had already covered when the fee later fell (waiver, hold, rewash, policy '
    'withdrawn); kept, never un-owed (MONEY-LIFECYCLE-009, DEC-036 reversal clause). No trace.';
