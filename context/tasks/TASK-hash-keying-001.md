# TASK-hash-keying-001 — make the hashes that outlive a purge stop being reversible

**Goal:** move `webhook_events.payload_hash` and `command_idempotency_records.request_hash` to a
keyed HMAC under a per-deployment secret, so that disposing of a payload disposes of the ability to
recover it.

**Domains:** `privacy_consent`, `runtime_architecture`

**Stable work item:** `HASH-KEYING-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM — both values are equality-compared for deduplication and replay defence. A change
that alters comparison semantics breaks inbound deduplication, which invariant 6 depends on.

## Why this exists

`DEC-018` recorded this as an accepted residual with a scheduled fix, and named it. Both values are
**unsalted SHA-256 commitments over plaintext**:

- `payload_hash` commits to the raw inbound message. `RETENTION-STORE-001` disposes of the
  ciphertext at 30 days and keeps this hash forever, because it is what lets an auditor prove which
  bytes arrived. For a short Vietnamese message — `DỪNG` above all, which is exactly the message
  whose evidence matters most — the preimage space is small enough to enumerate. The purge therefore
  removes the payload and leaves a recoverable commitment to it.
- `request_hash` commits to `{"store_id": ..., "question": ...}` and lands in a row
  `protect_idempotency_record` will not let anyone delete, outliving the 180-day transcript purge.

Neither is a defect in the code that wrote it; both are the correct primitive used before anyone
asked what it would mean after a purge. This item asks.

## Required design

HMAC-SHA256 under a secret held per deployment, not per row. Equality comparison is preserved
exactly — `HMAC(k, a) == HMAC(k, b)` iff `a == b` for a fixed `k` — so deduplication and replay
defence behave identically while the value stops being enumerable by anyone without the key.

- The key lives in the environment or `/run/secrets`, never in a repository file, per
  `docs/runbooks/provider-credentials.md`. A missing key fails closed: the write refuses rather than
  falling back to an unkeyed digest.
- The prefix changes so the two generations are distinguishable at a glance and by constraint:
  `RAW-SHA256-V1:` becomes `RAW-HMAC-V2:`, and the CHECK admits both until the migration completes.
- Existing rows **cannot be re-keyed**. The plaintext they commit to is gone or is exactly what must
  not be read to compute a new value. They keep their V1 prefix and their residual, and the residual
  stops growing. A migration that claimed to re-key them would be a migration that read customer
  payloads to do it.
- Deduplication compares values of the same generation. Two rows of different generations for the
  same provider event is an operational fact of the cutover window, and the replay-conflict path
  already records a hash mismatch honestly rather than guessing.

## Constraints

- No plaintext is read, logged or re-derived to perform this change.
- A deployment with no key configured refuses inbound writes rather than silently writing an
  unkeyed hash. Unknown means stop.
- Key rotation is a stated operational procedure, not a code path that tries to re-key history.
- The disposal evidence in `retention_disposal_records` is unaffected; it names keys and timestamps,
  never hashes.

## Required tests

- the same input under the same key produces the same value, and under a different key does not;
- deduplication of a repeated provider event still returns `DUPLICATE` and writes no second row;
- a replay with a different payload for the same provider event still records a replay conflict;
- a missing key refuses the write with a named error rather than degrading;
- no test, fixture or log contains the key;
- V1 rows are readable and are never rewritten.

## Done when

- every new `payload_hash` and `request_hash` is keyed;
- the residual `DEC-018` accepted is closed for all new data and honestly recorded as permanent for
  existing rows;
- the full gate battery passes with no required skips;
- rollback is reverting the code, which resumes writing V1 values; both generations remain readable,
  so rollback destroys nothing.
