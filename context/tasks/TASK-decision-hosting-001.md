# TASK-decision-hosting-001 — hosting decision packet and admissibility verdict

**Goal:** give the owner everything needed to choose a hosting provider in one sitting, and record
the choice in a form the rest of the queue can depend on.

**Domains:** `platform`

**Stable work item:** `DECISION-HOSTING-001`

**Stage:** M4B
**Risk:** MEDIUM — the risk is choosing on price and discovering later that restore or residency
does not hold.

## Why this exists

`DEPLOY-TARGET-001`, `MONITORING-001`, `BACKUP-RESTORE-001`, `SLO-VERIFY-001` and `OPS-RUNBOOK-001`
all wait on a host. `BACKUP-RESTORE-001` is already `BLOCKED` for exactly this reason. Six items and
both remaining G1 drills sit behind one decision that takes an afternoon once the packet exists.

`docs/adr/0007-production-deployment-topology.md` fixes the topology and deliberately leaves the
provider open. This item closes it.

## What the engineer produces

An admissibility verdict per candidate — not a recommendation dressed as analysis. A candidate is
**admissible** only if it satisfies every hard constraint:

| Constraint | Source | How it is verified |
|---|---|---|
| Two separate hosts, three network zones | ADR-0007 | provider supports private networking between instances with no public route to Zone B or C |
| Zone A reaches only the model endpoint and the Tool Facade | ADR-0007, `platform` prohibitions | egress control is expressible, not merely "possible with discipline" |
| PostgreSQL point-in-time recovery to an RPO ≤ 15 minutes | `PRODUCTION_OPERATIONS_SPEC_V1.md` §3.1 | managed PITR, or self-managed WAL archiving to a separate failure domain |
| Encrypted off-host recovery copy in a **separate failure domain** | §3.1, `BACKUP-RESTORE-001` | object storage that is not the same physical or account failure domain as the database |
| Restoration within 4 hours, demonstrable | §3.4 | provider does not prevent a timed restore drill |
| Data residency compatible with the owner's obligation | `DEC-008`, Vietnamese personal-data rules | written residency terms, not a marketing claim |
| Cost the owner accepts at pilot and at 12-month scale | owner | two figures, both verified against the published price list on the day |

Present the verdict as a table with `ADMISSIBLE` / `INADMISSIBLE` / `UNVERIFIED` per constraint per
candidate. `UNVERIFIED` is a legitimate and useful answer; guessing is not.

## What only the owner can do

- Select one admissible candidate.
- Accept its commercial terms.
- Record `owner_written_approval` — a dated, named, written approval, not a verbal preference
  relayed by an engineer.

An agent may assemble the packet. An agent may not select a vendor, accept terms, assert residency,
or sign the approval.

## Constraints

- Do not provision anything. This item produces a decision and its record, no infrastructure.
- Do not enter a credit card, create an account, or accept terms of service.
- If a candidate's restore feasibility cannot be established without an account, say so and mark it
  `UNVERIFIED`; do not create the account to find out.
- No capability moves. Every flag stays closed.

## Done when

- every candidate has a per-constraint admissibility verdict with its evidence source and date;
- cost and residency are verified figures with the date they were read;
- restore feasibility is assessed against §3.4, including whether a timed drill is possible at all;
- the owner's written approval names one candidate;
- `DEPLOY-TARGET-001` can start without asking a further question.
