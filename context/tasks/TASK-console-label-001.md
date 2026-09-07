# TASK-console-label-001 — tapping a label focuses the field it names

**Goal:** drive the console in a real browser and fix what that reveals.

**Domains:** `platform`

**Stable work item:** `CONSOLE-LABEL-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** LOW. One helper in `apps/web/src/ui/`.

## Why this exists

`scripts/verify_console_interaction.py` needs `--with playwright` and
had never been run. Everything before it inspected the console; this one uses it.

## Acceptance

The six commands, plus the browser check passing.
