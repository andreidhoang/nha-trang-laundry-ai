# TASK-verify-gate-001 — deploy day's verification gate can run, and passes

**Goal:** make the step that proves a deployment before staff are let in actually able to
run, and run it.

**Domains:** `platform`

**Stable work item:** `VERIFY-GATE-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** LOW. A verification script and a monitoring check;
no application code, no schema change.

## Why this exists

A gate that cannot fail honestly is worse than no gate. `staging_smoke.py` refused every host
but one and looked for a string the console has not contained since it was localised, so it
failed against every deployment and nobody knew. `check_console_reachable` could not verify
the shop's own certificate.

## Acceptance

The six commands, plus the gate passing
against a running stack.
