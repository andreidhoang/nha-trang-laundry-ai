# TASK-deploy-target-001 — production topology, isolated agent cell, closed flags

**Goal:** stand up the ADR-0007 topology on the selected host and prove its isolation by attempting
to break it.

**Domains:** `platform`

**Stable work item:** `DEPLOY-TARGET-001`

**Stage:** M4B
**Risk:** HIGH — this is the first infrastructure that will ever hold real customer data.

## Why this exists

Nothing is provisioned. `BACKUP-RESTORE-001` is `BLOCKED` on the absence of a host,
`MONITORING-001`, `SLO-VERIFY-001` and `OPS-RUNBOOK-001` all depend on this item, and `SECURITY-001`
depends on all of them. It is the single largest unblocking action on the infrastructure side, and
it cannot start until `DECISION-HOSTING-001` names a provider.

## Required design

`docs/adr/0007-production-deployment-topology.md` fixes three zones across two hosts. The zone
boundary is the security property, so it is verified by attempt rather than by configuration review:

- **Zone A** — the public agent runtime cell. Reaches the approved model endpoint and the Tool
  Facade. Nothing else. No channel credential, no database, no owner mount, no shell, no direct
  send.
- **Zone B** — the control plane: internal API, worker, staff console.
- **Zone C** — PostgreSQL and object storage.

`specs/PRODUCTION_OPERATIONS_SPEC_V1.md` §8 lists the verification this item owes, and §1.4 fixes the
rule that matters most operationally: **production feature flags ship closed, and deploying code
never enables a capability.**

## Constraints

- Every capability flag reads `false` on a fresh instance. Prove it on an instance built from
  scratch, not on one that has been adjusted by hand.
- No secret in any image layer, log, span attribute or archive. Prove absence by scanning the built
  image, not by inspecting the Dockerfile.
- Zone A's isolation is proven by attempting to connect from Zone A to PostgreSQL, to the channel
  API and to the staff console, and recording that each attempt failed.
- Forward-only migrations. Do not rewrite a deployed migration file.
- This item provisions infrastructure. It authorizes no capability, opens no public ingress, and
  sends nothing.

## Required tests

- external port scan reaches only the webhook path and nothing else;
- Zone A cannot reach PostgreSQL, the channel API or the staff console — each proven by a failed
  attempt, logged;
- no secret is present in any image layer or archive;
- all capability flags read `false` on a fresh instance;
- credential rotation completes with the system running, with no dropped or duplicated effect.

## Done when

- the three-zone, two-host topology is running on the approved host;
- all five verifications above are recorded with their output;
- `BACKUP-RESTORE-001`, `MONITORING-001`, `SLO-VERIFY-001` and `OPS-RUNBOOK-001` can start;
- rollback is destroying the instances; no customer data exists yet to lose, and this item must be
  completed before any does.
