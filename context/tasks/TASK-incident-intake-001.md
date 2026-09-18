# TASK-incident-intake-001 — let a staff member open an incident at the counter

**Goal:** make `#/incidents` completable by the person standing in front of the customer, by
deriving the contact scope on the server and giving the evidence summary a purgeable home.

**Domains:** `orders_audit`, `privacy_consent`

**Stable work item:** `INCIDENT-INTAKE-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM — it adds a personal-data column and a server-derived identity. Neither may be
computed by a client, and the column must be disposable from the day it exists.

## Why this exists

`POST /internal/v1/stores/{store_id}/incidents` requires `contact_scope_hash` and
`evidence_summary_hash`, both `^sha256:[0-9a-f]{64}$`, and **nothing in this repository produces
either value**. `WORKFLOW-CONFORMANCE-001` measured that on 2026-09-10 by driving the screen against
a real API and then searching the whole repository for a producer. A customer says their shirt came
back stained; the staff member opens the form, fills in the order id, and cannot finish. The shop
records the complaint on paper.

Both fields are agent-pipeline concepts that the staff console inherited by calling the same command
(`incidents.py:30` defaults `actor_type` to `AGENT_RUNNER`). The counter has no equivalent of either.

`DEC-028` decides both questions. This item implements that decision.

## Required design

**Contact scope — server-derived, never supplied.** Remove `contact_scope_hash` from the staff
request model. The server derives it from `orders.bound_contact_id` for the order the incident names,
which the route already requires. For a walk-in that binding is the counter ticket, because `DEC-013`
decided the ticket *is* the customer and the system stores no name, phone or address for a walk-in.
A staff member must not be able to name a scope for a customer of their choosing: invariant 9 puts
server-derived contact binding outside client control, and this is that rule applied.

**Evidence summary — stored, and disposable from day one.** The summary is the whole value of the
record: an incident with no description is a tally mark, and `DEC-004`'s remedy decision cannot be
made from one. A hash of text nobody can read back proves only that a discarded string did not
change.

So the text is stored in a **disposable payload side table** keyed to the incident, exactly as
`RETENTION-STORE-001` built for `webhook_events`:

- `customer_incidents` keeps the hash and stays append-only. The hash is computed by the server over
  the stored text, so it commits to what was actually kept rather than to something discarded.
- `customer_incident_evidence` holds the free text, carries a BEFORE UPDATE guard, and is deletable.
- `INCIDENT_EVIDENCE` enters `DISPOSABLE_PAYLOAD_STORES` with its `DEC-008` schedule of 365 days
  PURGE, and its `UNSUPPORTED_STORE_REASONS` entry is removed.
- `retention_purge` is granted DELETE on the new table in the same migration, per `DEC-020`.

This is what makes storing the text safe, and it is the reason the answer is different today from
what it was when the request was written: on 2026-09-10 storing a summary meant "brings a new
personal-data class into scope, so it needs a retention answer alongside DEC-008". That answer now
exists and executes.

**`orders.incident_open`.** The column exists and no code sets it. Opening an incident sets it in the
same atomic envelope. `DEC-024`'s `SHOP_FAULT_NO_CHARGE` exit is the case the column was added for.

## Constraints

- No client computes either hash. Both are server-derived; the request carries the summary text and
  the order id.
- The summary is free text a staff member types about a real person, so it is treated as personal
  data from the first commit: purgeable table, retention class, no copy in an event payload, an
  audit row or an outbox row.
- `customer_incidents` keeps its existing guards. Nothing is dropped or relaxed.
- The agent path's request model is not changed. It has its own producers and its own contract
  (`specs/contracts/agent-tools-v1.openapi.yaml`), and it is `NOT_AUTHORIZED` regardless.
- Vietnamese copy on `#/incidents` stops saying the form cannot be completed, because it can.
  `#/gaps` loses the entry naming this blocker. The test asserting "no producer exists" is replaced
  by one asserting the producer does exist and is server-side.

## Required tests

- a staff member with an order can open an incident end to end, and the stored `contact_scope_hash`
  equals the server's digest over that order's bound contact;
- a request that tries to supply either hash is refused rather than trusted;
- two incidents on the same order carry the same contact scope; incidents on two different orders of
  the same walk-in ticket carry the same scope, and of different tickets do not;
- the evidence text is readable back through the incident read model, and disappears when
  `INCIDENT_EVIDENCE` is purged while the incident row and its hash survive;
- `orders.incident_open` is set atomically with the incident, its event, its audit row and its
  outbox row;
- the console form submits successfully in a browser against a real API.

## Done when

- a staff member can record a complaint at the counter without leaving the console;
- `INCIDENT_EVIDENCE` is in `SUPPORTED_PURGE_CLASSES` with a tested purge, so the schedule covers it;
- the full gate battery passes with no required skips;
- rollback is reverting to the refusing form, which loses the ability to record new incidents and
  destroys none already recorded.
