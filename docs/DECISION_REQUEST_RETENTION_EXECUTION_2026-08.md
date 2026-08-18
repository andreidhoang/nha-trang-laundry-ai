# Decision request — what "purge" means when the same bytes are also evidence

**Opened:** 2026-08-18 · **Owner:** `BUSINESS_OWNER` · **Decisions:** `DEC-018`, `DEC-019`, `DEC-020`
**Status:** unsigned. All three fail closed today. `RETENTION-STORE-001` is blocked on `DEC-018` and
`DEC-020`; `DEC-019` blocks nothing yet but will block the first Shadow run that produces a draft.

These came out of designing `RETENTION-STORE-001` and then trying to refute the design. `DEC-008`
resolved the retention *schedule* — how long each class lives. It did not resolve what disposal
*means* when the same bytes are simultaneously scheduled for deletion and required as evidence, and
that turns out to be the binding constraint on executing any purge at all.

Read `context/tasks/TASK-retention-store-001.md` first for what the item was trying to build, and
`docs/RETENTION_STORE_001_DESIGN_REVIEW_2026-08.md` for the design that was produced, the three
adversarial reviews that refuted it, and the 23 must-fix items an implementation would start from.

---

## DEC-018 — When the same bytes are both disposable payload and retained evidence, which rule wins?

### What happens today

Nothing is purged, so nothing is wrong yet. `SUPPORTED_PURGE_CLASSES` is empty and every class
refuses. The question becomes live the moment the first class is enabled.

`DEC-008` signs **raw webhook payload → 30 days, PURGE** and, in the same sentence, **consent evidence
→ retained indefinitely under access restriction**. Measured against the schema, those two instructions
land on the same row.

- `consent_events.evidence_webhook_id` is `NOT NULL` and references `webhook_events(id)` —
  `packages/db/migrations/0007_operations_control.sql:87`.
- So when a customer sends `DỪNG` (STOP), the ciphertext of that message in
  `webhook_events.encrypted_payload` **is** the evidence for the consent withdrawal.
- Purging it at 30 days destroys the evidence for a record the same decision retains forever.
- Keeping it forever means a class signed as 30-day PURGE never purges the rows that matter most.

The same shape appears twice more and is currently unaddressed:
`inbox_replay_conflicts.webhook_event_id` (retained indefinitely, pointing at a 30-day payload) and
`agent_runs.source_webhook_event_id` (a 90-day run pointing at a 30-day input).

### A second, sharper instance — the assistant transcript is stored twice

`DEC-008` signs **assistant transcript → 180 days, PURGE**. It cannot be honoured today, and this is
not a schema-design problem that engineering can route around:

1. `AssistantService.post_turn` wraps the write in `IdempotencyRepository.execute`, whose result
   document is `_turn_mapping(turn)` — and that mapping includes `"answer": turn.answer` and
   `"links"` (`apps/api/src/nha_trang_laundry_api/assistant.py`, `_turn_mapping`).
2. `IdempotencyRepository` persists that document verbatim into
   `command_idempotency_records.response` as JSONB (`packages/db/.../idempotency.py:88-96`).
3. `protect_idempotency_record` — read live from `pg_proc`, not from the migration text — raises on
   `TG_OP = 'DELETE'` and on any UPDATE where `OLD.response IS NOT NULL`. A completed idempotency
   record is **permanently undeletable and unmodifiable**.

So deleting the transcript at 180 days leaves a byte-identical copy of the answer in a row nothing
may ever remove. **A purge that leaves the data behind is not a purge**, and shipping it as one
would be exactly the silent no-op the retention module was built to prevent.

There is a related residual the owner should know about even if it changes nothing:
`command_idempotency_records.request_hash` is an unsalted commitment to the exact question text and
also outlives the purge, as does `webhook_events.payload_hash` for the message body. For short
Vietnamese messages — `DỪNG`, `ok`, a phone number — an unsalted SHA-256 is dictionary-reversible.
Neither hash can be removed: production reads `payload_hash` on every duplicate arrival as replay
defence (`packages/db/.../inbox.py:91`).

### What the owner is being asked

1. **When a payload is also evidence, which instruction wins** — the shorter purge or the longer
   retention? The recommendation on record: **evidence wins, and the exception is recorded**. A
   purge run should hold back the rows it may not delete, report the count and the reason in its own
   run record, and never report `COMPLETED` over a silent exemption.
2. **Is a partial purge acceptable**, and if so must it be disclosed? Concretely: may the assistant
   transcript be deleted from `assistant_turns` while a copy remains in an immutable idempotency
   record, or is that class `NOT_SUPPORTED` until the duplicate stops being written?
3. **If the duplicate must stop**, that is a change to `ASSISTANT-001`, which is `COMPLETE`.
   `context/CONTINUATION_PROTOCOL.md` says a completed item is immutable planning history unless a
   new corrective work item is created. Do you want that corrective item opened?

### Until it is signed

`SUPPORTED_PURGE_CLASSES` stays empty. `RAW_WEBHOOK_PAYLOAD` and `ASSISTANT_TRANSCRIPT` — the only
two classes with a backing store that could be separated today — stay refused.

---

## DEC-019 — Which retention class covers text the AI wrote, and text a human edited?

### What happens today

`agent_drafts.draft_text` holds up to 4,000 characters of model-composed, customer-facing text. Its
sibling `agent_draft_reviews.edited_text` holds the human-edited version. Both carry
`agent_drafts_append_only`. **Neither is mentioned anywhere in
`packages/db/src/nha_trang_laundry_db/retention.py`, and `DEC-008` names no class that obviously
covers them.**

A sweep of every `text`, `jsonb` and `bytea` column across all 41 tables found the same condition in
several more places: `approval_decisions.note`, `channel_send_receipts.resolution_note`,
`staff_users.display_name` and `staff_users.email` (staff personal data, in scope of
`Luật 91/2025/QH15`), `contact_channel_bindings.provider_user_ref`, and the `jsonb` payload columns
on `approval_requests`, `outbox_events`, `domain_events` and `command_idempotency_records`.

`SHADOW-001` is the point at which these tables first fill with real customer content. The
obligation attaches before the data exists, not after.

### What the owner is being asked

1. **Which class does an AI draft belong to** — `AGENT_RUN_PAYLOAD` (90 days), `CONVERSATION_BODY`
   (180 days, REDACT), or a new class? A draft that was *sent* and a draft that was *rejected* may
   deserve different answers.
2. **Does a human's edit inherit the same schedule**, or is an edited draft a staff work product
   with its own rule?
3. **Do the other uncovered columns need classes**, or are they deliberately out of scope? Naming
   them out of scope is a legitimate answer; leaving them unnamed is not, because the retention
   module then reports a complete schedule that silently covers less than the database holds.

### Until it is signed

No class covers these columns, so nothing purges them and nothing claims to. The gap is now
recorded rather than latent.

---

## DEC-020 — Which identity may execute a purge, and what does the database grant it?

### What happens today

**No database role restriction exists anywhere.** There is not one `GRANT`, `REVOKE`, `CREATE ROLE`
or `ROW LEVEL SECURITY` statement across migrations `0001`–`0027`. Purge discipline would be
code-only.

Worse for the item: `scripts/apply_demo_grants.py:33-35` applies

```sql
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role};
REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM {role};
```

to the API and worker roles, and a test pins that behaviour. **So the identities that run this
system cannot `DELETE` anything.** A purge is a `DELETE`. Whichever process executes it needs a
grant that no role currently has, on exactly the tables it may purge and no others.

`specs/SECURITY_RELIABILITY_SPEC_V1.md` §9.4 requires access, export and deletion of raw inbox data
to be **audited and restricted**. The audited half is built — every run commits atomically with its
domain event, audit row and outbox row. The restricted half does not exist.

### What the owner is being asked

1. **Which identity executes a retention purge** — the worker, a separate purge role, or a human
   running a script under an owner credential?
2. **Is a purge allowed to run unattended at all**, or must each run be initiated by a named person?
   The answer changes the design: an unattended job needs a granted role; an attended one can borrow
   the operator's identity and record it.
3. **Should the two disposable side tables be the only tables in the schema with `DELETE` granted to
   anyone?** The recommendation on record: yes, and the grant is part of the same migration that
   creates them, so a table that can be purged and the permission to purge it never drift apart.

### Until it is signed

No purge can execute regardless of the schedule, because no identity holds `DELETE`. This is
fail-closed and correct — but it means `RETENTION-STORE-001` cannot be completed by engineering
alone, which is why it is recorded as a blocker rather than worked around.

---

## What was verified, and how

Every claim above was checked against the running database or the source, not against documentation:

- Triggers and function bodies read from `pg_proc` and `pg_trigger` on the live schema.
- `ALTER TABLE ... DROP COLUMN` was probed inside a rolled-back transaction and **succeeds** under an
  append-only row trigger, with the trigger still armed afterwards — so the ledger guarantee is not
  what blocks this item. Policy is.
- The absent tables (`conversations`, `messages`, `addresses`, `evidence_assets`, `parties`,
  `payments`) were confirmed absent by querying `information_schema`.

Of `DEC-008`'s ten classes, exactly **two** have a backing store that could be separated today.
Four have no backing store at all, two are retained indefinitely, one is not due for ten years, and
one — `DEBUG_LOG` — is not in the database, so this control plane can never dispose of it and that
obligation belongs to whoever owns the log collector.
