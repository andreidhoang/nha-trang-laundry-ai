# TASK-retention-001 — retention, redaction and deletion jobs

**Goal:** make the retention schedule in `specs/SECURITY_RELIABILITY_SPEC_V1.md` §15 an executable,
audited, testable job rather than a table in a document.

**Domains:** `privacy_consent`, `orders_audit`

**Stable work item:** `RETENTION-001`

**Stage:** M4B
**Risk:** HIGH — a purge job is the one piece of this system that destroys data on purpose.

## Why this exists

§15 of the security specification sets an initial retention schedule per data class and states that
"retention jobs must be testable, auditable and reversible only through backup policy — not hidden
soft-delete forever". §13 sets 30 days as the initial maximum for rejected/raw webhook data unless a
legal hold applies.

No work item implemented any of it. `MONITORING-001` covers *telemetry* retention, which is a
different data class and a different system. `SHADOW-001` is the point at which real customer
conversations, names, phone numbers and delivery addresses first enter the database — so the
obligation attaches before that item, not after it.

The schedule itself is "pending legal/accounting review". That review is registered as `DEC-008` and
is the owner's; this item builds the mechanism and fails closed until the schedule exists.

## Required design

- Retention classes as versioned configuration, not constants in code, covering at minimum: raw
  webhook payload, conversation body, agent run and tool payload, exact delivery location,
  consent/suppression evidence, orders and invoices, incident evidence, debug logs, and
  security/audit events.
- A purge/redact job per class that runs in one transaction with its audit record, so a purge that
  is not audited did not happen.
- Legal hold suspends purge for the held scope and says so in the audit trail.
- Conversation-body handling is redact-or-summarize, not delete, matching §15.
- With no approved schedule for a class, the job must refuse to run for that class and report
  `REQUIRE_HUMAN`. A missing schedule must never default to "delete" or to "keep forever silently".

## Constraints

- Purge is irreversible except through backup policy. Do not implement a hidden soft-delete that
  quietly retains the data the schedule says to remove.
- Never purge an audit or consent record to satisfy a conversation-body rule.
- `DEC-008` is open. Ship the mechanism with no class enabled; enabling a class is a configuration
  publication, not a code change.
- Preserve atomic mutation + domain event + audit + outbox semantics for every mutation this job
  makes.
- Forward-only migrations; do not rewrite a deployed migration.

## Required tests

- a purge whose audit write fails rolls back the purge;
- a legal hold prevents purge for its scope and leaves an audit record saying so;
- a class with no approved schedule fails closed and purges nothing;
- conversation-body handling redacts and leaves the sanitized audit intact;
- a purge job cannot remove a consent, suppression or audit record;
- purge is idempotent: a second run over the same window is a no-op.

## Done when

- every §15 data class has a class definition, a job and a test;
- no class is enabled by default and the disabled state is proven by test;
- the full gate battery passes with no required skips;
- rollback is disabling the jobs and reverting the migration forward, with no data already destroyed
  by a job that ran only in test.
