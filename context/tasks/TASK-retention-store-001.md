# TASK-retention-store-001 — make the retention schedule executable

**Goal:** resolve the conflict between the append-only ledger design and the section 15 retention
schedule, so that a purge job can actually dispose of something.

**Domains:** `privacy_consent`, `orders_audit`

**Stable work item:** `RETENTION-STORE-001`

**Stage:** M4B
**Risk:** HIGH — this touches the ledger guarantee, which is the reason this system can be trusted
about money and consent. It must weaken exactly nothing about that.

## Why this exists

`RETENTION-001` built the retention control plane and, in doing so, proved that it cannot execute.
Five of the nine section 15 data classes are backed by tables carrying `reject_ledger_mutation`,
which rejects every `DELETE` and `UPDATE`:

| Class | Backing table | Trigger |
|---|---|---|
| `RAW_WEBHOOK_PAYLOAD` | `webhook_events` | `webhook_events_append_only` |
| `CONSENT_EVIDENCE` | `consent_events` | `consent_events_append_only` |
| `AGENT_RUN_PAYLOAD` | `agent_tool_calls` | `agent_tool_calls_append_only` |
| `SECURITY_AUDIT_EVENT` | `audit_events` | `audit_events_append_only` |
| `INCIDENT_EVIDENCE` | ledger-backed | append-only |

The remaining four have no backing store at all. A test in `test_retention.py` proves the trigger
really refuses, so this is measured rather than assumed.

Both requirements are correct and neither yields. The audit ledger must be immutable or it is not an
audit ledger. The retention schedule must execute or the system accumulates customer data it has
promised to dispose of. **`SHADOW-001` is where real names, phone numbers and addresses first
arrive, so this cannot be deferred past it.**

## Required design

Separate what must be kept forever from what must be disposed of, rather than trying to make one
table do both:

- the **sanitized ledger row** stays append-only and immutable: identifiers, hashes, decisions,
  outcomes, timestamps, actors. This is what an auditor reads and it never expires;
- the **disposable payload** moves to a side table keyed by the ledger row, holding exactly the
  fields section 15 schedules — raw payload bytes, conversation body, exact delivery location, tool
  arguments. This table is mutable and purgeable;
- deleting a side-table row leaves the ledger row intact and its audit meaning unchanged. An
  auditor reading a purged record sees that it existed, what was decided, and that its payload was
  disposed of under a named schedule — not a gap.

Register each supported class in `SUPPORTED_PURGE_CLASSES` only once its side table exists and its
purge is tested. The set is empty today and must never be widened ahead of the implementation.

## Constraints

- **No existing ledger trigger is dropped, relaxed or bypassed.** If a class cannot be made
  disposable without touching one, that class stays unsupported and the reason is recorded.
- Forward-only migrations. Moving existing payload data is a migration, not a manual step.
- A purge must remain irreversible except through backup policy: no soft-delete, no tombstone that
  quietly retains the data.
- `RETENTION-001`'s fail-closed behaviour is preserved exactly: a class with no approved schedule
  still refuses, a legal hold still suspends, and every run still leaves a record.
- `DEC-008` remains the authority on *what* is disposed of and when. This item changes only whether
  disposal is mechanically possible.

## Required tests

- purging a side-table row leaves its ledger row present and its audit query unchanged;
- an auditor can still reconstruct the decision chain for a record whose payload was purged;
- the ledger triggers still reject a direct `DELETE` or `UPDATE` after the change;
- a class is refused until its side table exists, and accepted immediately after;
- purge remains idempotent and remains blocked by a legal hold;
- migrating existing payload data loses nothing, proven by count and hash before and after.

## Done when

- at least the classes that hold customer PII are disposable, and the rest are refused with a stated
  reason;
- `SUPPORTED_PURGE_CLASSES` matches exactly the set with a tested purge;
- the full gate battery passes with no required skips;
- rollback is reverting to refusal-only, which disposes of nothing and destroys nothing.
