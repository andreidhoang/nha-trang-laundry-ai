# TASK-channel-envelope-001 — canonical channel envelope and receipt

**Goal:** create the provider-neutral ingress and egress contract that every channel adapter maps
into, wired to the existing inbox, outbox and audit semantics.

**Domains:** `channel_operations`, `orders_audit`

**Stable work item:** `CHANNEL-ENVELOPE-001`

**Stage:** M5
**Risk:** HIGH — this is the first inbound path from an untrusted source.

## Required design

Per `specs/CHANNEL_ADAPTER_SPEC_V1.md` §2–§4:

```text
authenticate -> limit -> normalize -> bind contact server-side
  -> suppression check -> durable inbox insert (deduplicated) -> acknowledge
```

- `(provider, provider_update_id)` is a uniqueness constraint in the database, not an application
  check. Two workers racing the same replayed update must lose at the constraint.
- Contact binding is resolved through a server-owned table. An unknown provider identity creates an
  `UNVERIFIED` contact with no access to order status, quotes or customer-specific facts.
- Acknowledgement happens after the durable insert and never waits on a model call.
- One receipt row per send attempt, including failures and timeouts.

## Constraints

- The adapter contains no business logic, no policy evaluation, no pricing and no model call.
- It never sends. Only the provider-specific sender worker sends, driven by the outbox.
- Raw provider payload bodies are not persisted, logged, traced or placed in evidence.
- Attachment bytes never enter the envelope — references only.
- Outbound status uses the canonical `MessageDeliveryStatus`. No parallel status vocabulary.
- No capability is enabled. `FEATURE_PUBLIC_CHANNELS_ENABLED` stays `false`.

## The unknown-outcome rule

A send whose outcome is unknown sets `reconciliation_state: UNKNOWN` and is **never retried
automatically**. It attempts provider-side confirmation where available, otherwise escalates to
`UNKNOWN_REQUIRES_HUMAN` in the staff exception queue. Only a provider confirmation or an explicit
human decision may leave `UNKNOWN`.

An automatic retry here is how a customer receives the same message twice. Treat any code path that
could produce one as a defect, not a tuning question.

## Required tests

Ingress: valid update accepted · invalid authentication rejected without persistence · replay outside
skew rejected · duplicate `provider_update_id` acknowledged exactly once and inserted once ·
oversized body rejected · malformed payload rejected · unsupported content type normalized rather
than dropped · unknown identity creates `UNVERIFIED` contact with no customer-specific access ·
model output cannot alter contact binding.

Egress: one outbox row produces exactly one send · receipt written for accepted, rejected, timeout
and transport-error outcomes · unknown outcome never auto-retried · concurrent outbox claim prevented
by the existing worker lease · suppression re-checked inside the send transaction.

Atomicity: envelope insert, domain event, audit row and any suppression write commit together or not
at all.

## Done when

- schema conformance is enforced at the boundary against both channel contracts;
- the dedupe constraint is proven by a concurrent replay test, not asserted;
- Ruff, format, mypy, contracts, context drift and the PostgreSQL suite pass with no required skips;
- rollback is disabling the adapter route; the inbox, outbox and staff console keep working.
