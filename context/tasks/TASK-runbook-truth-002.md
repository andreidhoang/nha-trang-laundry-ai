# TASK-runbook-truth-002 — every command the deploy runbooks give can reach what it needs

**Goal:** make the remaining runbook commands executable on the topology they describe.

**Domains:** `platform`

**Stable work item:** `RUNBOOK-TRUTH-002`

**Stage:** PRODUCTION_HARDENING
**Risk:** LOW. Tooling and documentation; no application
code and no schema change.

## Why this exists

`RUNBOOK-TRUTH-001` fixed the flags and the missing steps. It did not notice that a whole
class of commands names a database the host cannot reach — because `postgres` publishes no
port and sits only on `internal: true` networks, which is what ADR-0007 §1 asks for. Eleven
setup lines and the entire monitoring cron were in that class.

## Acceptance

The six commands, plus a test that fails if any runbook line naming a
database-dependent script is in a form that cannot connect.
