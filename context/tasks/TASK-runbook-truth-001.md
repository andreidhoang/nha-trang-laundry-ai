# TASK-runbook-truth-001 — the deploy runbooks can be executed

**Goal:** the deploy runbooks can be executed.

**Domains:** `platform`

**Stable work item:** `RUNBOOK-TRUTH-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. A corrective item: it changes artifacts belonging to COMPLETE items, whose planning history is immutable (ADR-0004).

## Why this exists

`deploy-today.md` claimed every command had been run except those needing a host. Three of its
failures were argument parsing and needed no host: a verification script invoked bare and with a
flag that does not exist, a validator flag that does not exist, and a drill that halts on its own
first command because two required variables are never exported.

The two runbooks disagreed on the secret list and the reader was told the wrong one wins.

## What must be true when this is done

1. Every flag a runbook passes to a script exists, checked by a test.
2. Everything `restore.sh` requires is exported by the drill, checked by a test.
3. Following either runbook end to end produces a stack that starts and an admin who can sign in.

## Acceptance

The six commands in `delivery/WORK_QUEUE.yaml`, and a reproduction for each defect that
is verified to fail without its fix.
