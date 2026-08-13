# TASK-security-001 — identity, privacy, PITR, incident, kill-switch, release readiness

**Goal:** run the drills. Everything else in this item already exists as code.

**Domains:** `runtime_architecture`, `privacy_consent`, `orders_audit`, `evaluation_release`

**Stable work item:** `SECURITY-001`

**Stage:** M4B
**Risk:** HIGH — this is the last item before real customer orders reach the system.

## Why this exists

`SECURITY-001` has ten dependencies, the most of any item in the queue, because it is the
convergence point: agent evidence, observability, policy, supply chain, HTTP security, telemetry,
staging, backup, the deployment target and monitoring all land here before `SHADOW-001` may start.

`AGENTS.md` is explicit that this item **is not authorized by passing unit tests**. The deliverable
is a set of executed drills, each with a timeline and a surprise list.

## Required drills

Each is executed against the real deployed topology, timed, and recorded with what went wrong:

- **Restore drill** — `PRODUCTION_OPERATIONS_SPEC_V1.md` §3.4, achieving the declared RPO and RTO.
  Shared with `BACKUP-RESTORE-001`; run once, record in both.
- **Incident drill** — severity assessment, kill switch, communication, evidence preservation. The
  test is whether the operator can find the runbook and follow it, not whether the runbook exists.
- **Kill-switch drill** — an **in-flight** send is held. Not a flag flipped while the system is
  idle; the value of a kill switch is entirely in what it does mid-flight.
- **Credential rotation** — database and provider keys rotated with the system running, no dropped
  or duplicated effect.
- **OIDC integration** — real provider, real MFA boundary, real session recovery, CSRF.

## Constraints

- A drill that has not been executed by the person who will execute it under pressure is a draft —
  `PRODUCTION_OPERATIONS_SPEC_V1.md` §7, and it applies to drills as much as to runbooks.
- Never mark a drill passed because its mechanism is unit-tested. The mechanism is already tested;
  the drill tests the operator, the runbook and the topology together.
- `DEC-006` must be `RESOLVED` before the provider data review can be completed.
- Do not authorize a capability. This item satisfies gate inputs; a signed manifest authorizes.
- Preserve every existing negative and authorization test; add to them for anything a drill exposes.

## Required evidence

- security tests, including the negative and authorization cases for every sensitive path;
- OIDC provider integration, exercised against the real provider;
- CSRF and session recovery;
- a timed restore drill meeting RPO and RTO;
- an incident drill with its timeline and communication record;
- a kill-switch drill holding an in-flight send;
- the provider data review, referencing the resolved `DEC-006`.

## Done when

- every drill above has been executed once, timed, with its surprises recorded;
- each drill's runbook was followed as written, and discrepancies were fixed in the runbook;
- the full gate battery passes with no required skips;
- every capability still reports `NOT_AUTHORIZED`;
- `SHADOW-001` can start.
