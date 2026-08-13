# TASK-ops-runbook-001 — the five G1 runbooks

**Goal:** produce the five runbooks `specs/PRODUCTION_OPERATIONS_SPEC_V1.md` §7 requires before G1,
and execute each one once with the person who will execute it under pressure.

**Domains:** `platform`, `channel_operations`

**Stable work item:** `OPS-RUNBOOK-001`

**Stage:** M4B
**Risk:** MEDIUM — the risk is producing five documents and calling that operational readiness.

## Why this exists

§7 lists exactly five runbooks as required before G1:

- `production-deployment.md` — deploy, verify digests, verify flags closed, roll back
- `restore-drill.md` — the §3.4 procedure, timed, with the exact commands
- `incident-response.md` — severity table, kill switch, communication, evidence preservation
- `credential-rotation.md` — database and provider key rotation with the system running
- `channel-reconciliation.md` — resolving `UNKNOWN` send outcomes without creating duplicates

`docs/runbooks/` currently holds `container-deployment.md`, `local-development.md`,
`private-staging.md`, `release-candidate-verification.md` and `release-supply-chain.md`. None of the
five exists. No work item named them: `SECURITY-001` carries the incident, kill-switch and restore
*drills*, and `BACKUP-RESTORE-001` carries the recovery configuration, but the operating procedure
those drills are supposed to follow was unowned.

§7 closes with the standard this item is held to: "A runbook that has never been executed by the
person who will execute it under pressure is a draft."

## Required design

- Each runbook is written against the real ADR-0007 topology and the real host selected by
  `DECISION-HOSTING-001`, with exact commands, not with placeholders.
- Each states its preconditions, its abort condition, and how to tell that it worked.
- `channel-reconciliation.md` must encode the rule that an `UNKNOWN` send outcome is never
  automatically retried; only provider confirmation or a human resolves it.
- Each runbook records the date it was executed, by whom, and what surprised them. The surprises are
  the deliverable — a runbook that produced none was probably not really executed.

## Constraints

- This item does not authorize a deployment, a rotation against production credentials, or a
  capability. Execution happens against the staging topology unless a separate authorization exists.
- Do not duplicate the drills owned by `SECURITY-001` and `BACKUP-RESTORE-001`; this item supplies
  the procedure those drills follow and records the first execution of each.
- Do not write a runbook for infrastructure that does not exist yet. This item depends on
  `DEPLOY-TARGET-001` for that reason.
- No secret, credential, key material or customer identifier appears in any runbook.

## Required tests

Documentation items are verified by execution rather than by unit test:

- each of the five runbooks exists at its declared path and names its operator and execution date;
- the deployment runbook's rollback section was followed end to end at least once;
- the reconciliation runbook was exercised against a simulated `UNKNOWN` outcome and produced no
  duplicate send.

## Done when

- all five runbooks exist, each executed once, each carrying its execution record;
- `scripts/verify_contracts.py`, `scripts/check_context_drift.py` and
  `scripts/report_delivery_status.py` pass and every capability still reports `NOT_AUTHORIZED`;
- rollback is deleting the runbooks, which removes documentation only and no evidence.
