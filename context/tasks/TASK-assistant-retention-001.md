# TASK-assistant-retention-001 — stop storing the assistant's answer twice

**Goal:** remove the undeletable duplicate of every assistant answer, so the 180-day
`ASSISTANT_TRANSCRIPT` purge `DEC-008` signed can become a true statement.

**Domains:** `privacy_consent`, `orders_audit`

**Stable work item:** `ASSISTANT-RETENTION-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM — it changes what an idempotent replay returns. A replay that stops returning the
original answer would break the guarantee the idempotency layer exists to provide.

## Why this exists

`DEC-018` opened this item by name. `AssistantService.post_turn` wraps the write in
`IdempotencyRepository.execute`, whose executor returns `_turn_mapping(turn)` carrying `answer` and
`links`. `idempotency.py` persists that document verbatim into `command_idempotency_records.response`,
and `protect_idempotency_record` rejects DELETE outright and rejects any UPDATE once the response is
non-NULL.

So a byte-identical copy of every assistant answer sits in a row nothing may ever remove. Purging
`assistant_turns` at 180 days while that copy survives would be, in `DEC-018`'s words, "a false
statement made by software" — which is why the same decision holds `ASSISTANT_TRANSCRIPT`
`NOT_SUPPORTED` until this item lands. `RETENTION-STORE-001` records that refusal with the reason
`BLOCKED_BY_UNDELETABLE_DUPLICATE`, and clearing it is the acceptance test for this work.

The question half is already clean: the idempotency *request* carries the un-redacted question but
only its JCS-SHA256 `request_hash` is stored. That hash is its own residual and belongs to
`HASH-KEYING-001`, not here.

## Required design

`DEC-018` states a preferred but explicitly non-binding direction, and it is the right one: an
idempotency record answers a retry. Its useful life is minutes to hours, not the 180 days of the
thing it is shadowing. Give those records their own short, purgeable lifetime and the duplicate
disappears by expiry rather than by surgery on a protected table.

Two parts, and the first is not optional:

1. **Stop writing payload into the record.** `_turn_mapping` must not carry `answer` or `links` into
   the stored response. A replay rehydrates from `assistant_turns` by `turn_id`, which is the
   authoritative row and the one the retention schedule governs. The replay contract is unchanged
   from the caller's side: the same document comes back, assembled rather than remembered.
2. **Give idempotency records a lifetime.** A new retention class or a bounded sweep, with the same
   discipline as every other: published configuration, never a constant; a run record; fail closed.
   Records whose response is already payload-free are cheap to keep, so the lifetime exists to bound
   the table rather than to fix the leak — part 1 fixes the leak.

Then `ASSISTANT_TRANSCRIPT` gets its side table (`assistant_turn_payloads` holding `question`,
`answer`, `links`) and enters `DISPOSABLE_PAYLOAD_STORES`, with the `retention_purge` DELETE grant in
the same migration per `DEC-020`.

## Constraints

- A replay must return exactly what the first call returned, for the caller. Prove it, do not assert
  it: the existing idempotency tests must pass unchanged, plus one that replays across a process
  boundary.
- `protect_idempotency_record` is not dropped, relaxed or bypassed. Existing rows keep their stored
  responses; this item stops *new* ones carrying payload and lets time remove the rest.
- `assistant_turns` keeps `reject_ledger_mutation`. The payload moves out from under it, exactly as
  `webhook_events` did; the trigger is untouched.
- The assistant read paths (`list_recent`, `get_scoped`) are store-scoped and keyset-paginated, and
  their visibility rule is a security property. A join that changes which rows a non-owner sees is a
  defect, not a refactor — test the visibility rule before and after.
- A purged turn renders as its intent and reason codes with the text absent. It must not render as an
  error, an empty string, or a gap.

## Required tests

- no new `command_idempotency_records.response` contains `answer` or `links`;
- a replay returns the identical document to the first call, including after the payload has moved;
- purging `ASSISTANT_TRANSCRIPT` removes question and answer and leaves the turn, its intent, its
  reason codes and its actor;
- an auditor can still reconstruct who asked what kind of question and when, after the text is gone;
- the visibility rule is unchanged: an owner sees every turn in the store, anyone else only their
  own, before and after the split, including across the keyset page boundary;
- `UNSUPPORTED_STORE_REASONS` no longer contains `ASSISTANT_TRANSCRIPT`, and the partition test
  still passes.

## Done when

- `ASSISTANT_TRANSCRIPT` is in `SUPPORTED_PURGE_CLASSES` with a tested purge;
- no code path writes an assistant answer to a row that cannot be deleted;
- the full gate battery passes with no required skips;
- rollback is reverting to the refusing class, which disposes of nothing and destroys nothing.
