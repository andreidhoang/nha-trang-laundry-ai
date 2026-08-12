# TASK-shadow-console-001 — the console a Shadow pilot actually needs

**Goal:** give staff a surface to review every agent draft, resolve send exceptions, and read the
audit trail. Without it `SHADOW-001` has nowhere to happen.

**Domains:** `orders_audit`, `channel_operations`, `privacy_consent`

**Stable work item:** `SHADOW-CONSOLE-001`

**Stage:** M5
**Risk:** MEDIUM — new read surfaces over existing authoritative data; no new business authority.

## Why this exists

The 2026-08-12 readiness assessment measured the staff web slice at 617 lines total (301 of
JavaScript). It has real, working screens for quotes, orders, approvals, incidents, queue recovery
and manual send — and **no surface for the Shadow workflow at all**.

`G1_INTERNAL_SHADOW_READY` requires a human to approve every outbound action across 14 days and 30
real orders. There is currently no screen on which to read a conversation, see the draft the agent
produced, and approve, edit or reject it. `STAFF-OPS-001` is legitimately complete for the internal
order flow; this item covers what Shadow adds.

## Required surfaces

1. **Conversation and draft review** — inbound message, the deterministic facts the run used, the
   proposed draft, the policy result, and the terminal code when the run handed off. Approve, edit
   or reject, each attributed to a named staff member.
2. **Exception queue** — outbound sends in `UNKNOWN` reconciliation, showing the exact message text
   so a human can check the conversation and record `CONFIRMED_SENT` or `CONFIRMED_NOT_SENT`. Per
   `CHANNEL_ADAPTER_SPEC_V1` §4.2 this is the only way out of `UNKNOWN`.
3. **SLA risk board** — orders approaching a promised time, computed by domain code.
4. **Audit timeline** — per order and per conversation, showing every decision and its actor.

## Constraints

- Every number, denominator, freshness timestamp, SLA flag and priority comes from **SQL and domain
  code**. AI may explain an already-computed fact through a narrow read-only tool; it may never
  originate, mutate or rank one (ADR-0003 §4).
- Editing a draft creates a new attributed version; it never mutates the agent's original record.
- Rejecting is a first-class outcome with a reason code, because rejection data is what improves the
  eval corpus.
- Server-enforced RBAC on every projection. No route trusts a client-supplied identifier.
- No customer PII in client-side logs, error strings or the service worker cache.
- The console never sends. It authorizes; the outbox worker sends.

## Required tests

- draft approve, edit and reject each produce an attributed, auditable record;
- an edited draft preserves the original agent output immutably;
- `UNKNOWN` reconciliation can be resolved by a human and cannot be resolved automatically;
- RBAC negative tests: a role without approval rights cannot approve, by API not just by UI;
- IDOR negative tests: a staff member cannot read another store's conversations by changing an id;
- an expired or revoked session cannot act;
- the service worker caches no customer data.

## Done when

- a full Shadow loop is exercisable by a human: message arrives, draft appears, staff approves,
  outbox records, receipt returns, audit shows the chain;
- an `UNKNOWN` outcome is resolvable only through this surface;
- the full gate battery passes with no required skips;
- rollback is disabling the new routes; the existing operational console keeps working.
