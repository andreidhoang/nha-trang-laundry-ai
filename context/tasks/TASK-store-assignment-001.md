# TASK-store-assignment-001 — a staff member the API can create can do nothing

**Goal:** give store membership a governed write path, so provisioning a working staff member does
not require writing to the database by hand.

**Domains:** `orders_audit`, `privacy_consent`

**Stable work item:** `STORE-ASSIGNMENT-001`

**Stage:** M4B
**Risk:** MEDIUM. This creates the first route that **grants** an authorization, so its failure mode
is the opposite of the usual one: not a refused operator, but a granted one who should not have been.

## Why this exists

`staff_store_assignments` gates almost every read and every write in the system. `IDENTITY-001` can
create a staff user and assign a role. `STORE-SCOPING-001` made membership load-bearing.

**Nothing can create a membership row.** `ShadowConsoleRepository.assign_store` exists, is tested,
and has no HTTP route and no calling script. Grep returns tests and eval harnesses only.

So the default state of every staff user this API can create is: correct role, no store, refused
everywhere. An owner provisioning a new operator today must connect to PostgreSQL and insert the row
themselves — an unaudited write to an authorization table, performed by hand, on production.

Two consequences worth stating plainly:

1. The audit trail has a hole exactly where it matters most. Every other authorization change —
   creating a user, assigning a role, disabling an account — is a recorded command with an actor.
   Granting someone access to a store's customers is the one that is not.
2. It is the first thing an owner does and the thing least likely to be done twice the same way.

The staff console surfaces this as an explicit unsupported capability rather than hiding it, which
is how it was found.

## Required design

- One route, owner-only, that assigns a staff member to a store. `require_owner`, re-checked in the
  repository as `IdentityRepository` already does for role assignment — the route gate is a
  convenience, the repository check is the authority.
- Reuse `ShadowConsoleRepository.assign_store`. Introduce no second membership concept and no second
  table.
- Atomic mutation **plus audit event plus outbox event**, in one transaction, like every other
  material command in this system. An assignment that is not audited is the defect this item exists
  to close, so an audit-write failure must roll the assignment back.
- `Idempotency-Key` required, as every other mutating route requires it. Assigning twice must be a
  replay, not a second row and not an error.
- A **revoke** path as well as a grant. A grant with no revoke means the only way to remove access
  is to disable the whole account, which is a blunter instrument than the situation usually needs
  and loses the person's history. Revoke is append-only in the audit sense: the assignment row goes,
  the events stay.
- `OWNER_ADMIN` is **not** implicitly a member of every store — `store_access.py` says so
  deliberately. This route does not change that. An owner who wants to see a store assigns
  themselves, and that assignment is audited like any other.

## Open question the item must answer, not assume

**May an owner assign a staff member to a store the owner is not a member of?** Both answers are
defensible — an owner administers the business rather than a shop, versus an owner cannot grant
access to data they cannot see. Pick one, write it down, and test it. Do not leave it to fall out of
the implementation.

## Constraints

- No client-supplied identifier is authority for anything; the actor comes from the session.
- Do not weaken `require_store_membership` anywhere to make provisioning easier.
- Do not add a bulk or CSV import. One assignment, one command, one audit record.
- The last-active-owner protection that `disable_staff` already has has an analogue here: consider
  whether revoking the last assignment on a store should be refused, and record the decision.

## Required tests

- an owner assigns a staff member and that member immediately passes the store-scoped routes that
  refused them before;
- a non-owner is refused, with the same body as any other role refusal;
- assigning twice with the same idempotency key replays and creates exactly one row;
- a forced audit-write failure rolls the assignment back, leaving no membership and no event;
- revoke removes access on the next request and leaves both events in the audit trail;
- the answer chosen for the open question above is asserted, so a later change to it is a test
  failure rather than a silent drift.

## Done when

- grant and revoke both exist, owner-gated at the route and in the repository;
- both are atomic with their audit and outbox events;
- the open question is answered in this packet's terms and covered by a test;
- the staff console's unsupported-capability entry for store assignment is replaced by the real
  control, and the `#/gaps` catalogue entry is removed rather than left claiming the gap persists;
- the full gate battery passes with no required skips;
- rollback removes a governed grant path and returns provisioning to hand-written SQL — a loss of
  audit coverage, which is what the rollback assessment must say.
