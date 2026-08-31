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
-- **The append-only trigger and this migration.** The first version of this file said "ADD COLUMN
-- is DDL and does not fire `reject_ledger_mutation`" -- true -- and then issued two `UPDATE`
-- statements, which are DML and do. It applied cleanly to an empty database, which is every test,
-- and failed on **any** database holding a single approval request, which is every deployment. A
-- re-verification on 2026-08-31 caught it; the test suite never could, because migrations always
-- run against a fresh database there.
--
-- The trigger is therefore disabled for the backfill and restored immediately. That is not a
-- loophole in the append-only rule: the rule exists so application code cannot rewrite history, and
-- this is a schema migration completing a record rather than altering what it says. No existing
-- column is touched -- every row keeps the action, resource, digests and decision it already had.

ALTER TABLE approval_requests ADD COLUMN store_id UUID;

ALTER TABLE approval_requests DISABLE TRIGGER approval_requests_append_only;

-- The two resource types whose owner is derivable from the resource itself.
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

-- Everything else -- `MESSAGE_DRAFT` and the ten unbuilt types -- has no derivable owner by
-- construction, so the operator names one or the migration stops.
--
-- The first version told the operator to "resolve or delete them deliberately". Deleting was
-- impossible: the same trigger blocks DELETE, so the remedy the error named could not be carried
-- out. This one names a setting that works:
--
--     psql "$DATABASE_URL" -c "ALTER DATABASE ... SET ntl.legacy_approval_store = '<uuid>'"
--
-- or, per-session, `SET ntl.legacy_approval_store = '<uuid>';` before running the migration. It is
-- deliberately not defaulted. An approval nobody can attribute to a shop is exactly the thing this
-- migration exists to make impossible, and guessing a store for one would forge the attribution it
-- is meant to establish.
DO $$
DECLARE
    fallback TEXT := current_setting('ntl.legacy_approval_store', true);
    orphaned INT;
BEGIN
    SELECT count(*) INTO orphaned FROM approval_requests WHERE store_id IS NULL;
    IF orphaned = 0 THEN
        RETURN;
    END IF;
    IF fallback IS NULL OR fallback = '' THEN
        RAISE EXCEPTION
            'cannot bind % approval request(s) to a store: their resource type has no derivable '
            'owner. Set ntl.legacy_approval_store to the store id these belong to and re-run; it '
            'is not defaulted because guessing would forge the attribution this migration exists '
            'to establish.', orphaned;
    END IF;
    UPDATE approval_requests SET store_id = fallback::uuid WHERE store_id IS NULL;
END
$$;

ALTER TABLE approval_requests ENABLE TRIGGER approval_requests_append_only;

ALTER TABLE approval_requests ALTER COLUMN store_id SET NOT NULL;

-- The queue reads by store and status; the decision path reads one row by id and already has its
-- primary key.
CREATE INDEX approval_requests_store_idx ON approval_requests (store_id, requested_at DESC, id);
