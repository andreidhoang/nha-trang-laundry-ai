# TASK-monitoring-001 — collector, retention, and paging alert contracts

**Goal:** make the telemetry the system already emits reach somewhere a human will actually be
woken by.

**Domains:** `platform`, `runtime_architecture`

**Stable work item:** `MONITORING-001`

**Stage:** M4B
**Risk:** MEDIUM — the risk is building a dashboard nobody watches and calling it monitoring.

## Why this exists

`packages/observability` is implemented — 640 lines across seven modules covering redaction,
correlation, telemetry and events — and `TELEMETRY-001` is complete. The spans and events exist and
go nowhere. `SECURITY-001` depends on this item, and `SLO-VERIFY-001` binds its measured targets to
the alerts this item creates.

## Required design

`specs/PRODUCTION_OPERATIONS_SPEC_V1.md` §4 is the contract. Two halves matter equally:

**§4.1 — alerts that page a human.** A page means someone is woken. Reserve it for conditions where
that is proportionate, and make each one testable by inducing the condition rather than by asserting
the rule exists.

**§4.2 — deliberately not paging.** This list is as important as the first. An alert that fires
routinely trains the operator to ignore the channel, and then the real page is missed too.

Also required: an **archive gap alert**. Backup and WAL archiving that silently stops is the failure
mode that turns a recoverable incident into an unrecoverable one, and it is invisible until the
restore is attempted.

## Constraints

- Redaction must hold **in transit**, not only at the emitter. Verify what the collector actually
  received, not what the application intended to send.
- Never emit a secret, raw PII, a raw provider payload or chain-of-thought into telemetry. The
  existing redaction is the mechanism; this item proves it survives the network hop.
- Telemetry retention here is telemetry retention. Customer-data retention is `RETENTION-001` and
  the two must not be conflated or share a schedule.
- No capability moves.

## Required tests

- the collector receives the existing OTel contracts without the application changing its emitters;
- a span deliberately seeded with a secret-shaped value arrives redacted;
- every paging alert is exercised by inducing its condition, not by unit-testing its expression;
- stopping the archive raises the archive gap alert within its declared window;
- a condition on the §4.2 list does not page.

## Done when

- telemetry reaches a collector with retention configured;
- redaction is verified in transit with evidence of what was received;
- every paging alert has been fired once, deliberately, and reached a human;
- the archive gap alert works;
- `SLO-VERIFY-001` can bind its measured targets to real alert rules;
- rollback is pointing the exporter back at nothing, which loses observability and no data.
