-- An approval belongs to a store, and now says so.
--
-- Found 2026-08-29 by adversarial verification, reproduced twice: a staff member of store B, with
-- no assignment to store A, permanently approved store A's approval. `decide()` checked
-- `required_role`, MFA and self-approval, and nothing else -- `_lock_approval` did not even select
-- a store, because there was no store to select. The same hole let store B's operator consume
-- store A's one-time SEND_MESSAGE approval and attest a send to a recipient of their own choosing,
-- burning store A's authorisation and recording store B's staff as having sent it.
--
-- **Why a column and not a join.** `list_pending` resolved the store by joining
-- `orders o ON o.id = r.resource_id`, which works only when the resource happens to be an order.
-- `APPROVAL_RESOURCE_TYPES` names thirteen types and exactly two have a backing table
-- (`QUOTE_REVISION`, `ORDER`). The rest -- `MESSAGE_DRAFT` above all, which is the manual-send path
-- and a capability that is actually built -- are content that lives in the envelope itself. An
-- inferred store cannot be checked for those, so the store is named and checked instead of guessed.
--
-- Append-only is unaffected: `ALTER TABLE ... ADD COLUMN` is DDL and does not fire the row-level
-- `reject_ledger_mutation` trigger. Nobody edits an approval after this either.

ALTER TABLE approval_requests ADD COLUMN store_id UUID;

-- Backfill the two resource types whose owner is derivable, then refuse to continue if anything is
-- left. `0029` set this precedent deliberately: a migration that cannot establish the truth for an
-- existing row fails loudly rather than marking the constraint NOT VALID and leaving a hole behind
-- a green schema. On a fresh database there are no rows and both statements are no-ops.
UPDATE approval_requests r
   SET store_id = o.store_id
  FROM orders o
 WHERE o.id = r.resource_id
   AND r.resource_type = 'ORDER'
   AND r.store_id IS NULL;

UPDATE approval_requests r
   SET store_id = q.store_id
  FROM quote_revisions v
  JOIN quotes q ON q.id = v.quote_id
 WHERE v.quote_id = r.resource_id
   AND r.resource_type = 'QUOTE_REVISION'
   AND r.store_id IS NULL;

DO $$
DECLARE
    orphaned INT;
BEGIN
    SELECT count(*) INTO orphaned FROM approval_requests WHERE store_id IS NULL;
    IF orphaned > 0 THEN
        RAISE EXCEPTION
            'cannot bind % approval request(s) to a store: their resource does not resolve. '
            'Each one is an envelope asserting an approval nobody can attribute to a shop; '
            'resolve or delete them deliberately rather than letting this migration guess.',
            orphaned;
    END IF;
END
$$;

ALTER TABLE approval_requests ALTER COLUMN store_id SET NOT NULL;

-- The queue reads by store and status; the decision path reads one row by id and already has its
-- primary key.
CREATE INDEX approval_requests_store_idx ON approval_requests (store_id, requested_at DESC, id);
