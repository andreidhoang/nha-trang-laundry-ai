# TASK-consent-stop-001 — consent capture, STOP suppression, and the opt-out race

**Goal:** make STOP mean stopped, including for the message that was already being sent when it
arrived.

**Domains:** `privacy_consent`, `channel_operations`, `orders_audit`

**Stable work item:** `CONSENT-STOP-001`

**Stage:** M4B
**Risk:** HIGH — a suppression miss is a zero-tolerance G2 defect.

## Why this exists

`G2_PUBLIC_ASSISTED_ENTRY` requires zero suppression misses. `CHANNEL-001` depends on this item.
Local synthetic preflights already exist for the STOP/outbox race, ambiguous opt-out and forged
consent, and every one of them records `SKIP` on the degraded path — the assertions pass, but no
real ingress or egress path exercises them because neither exists yet.

The `privacy_consent` domain fixes the shape: STOP and suppression are **deterministic ingress and
egress checks**. Not a model judgement, not a best effort.

## Required design

Two checks, not one, because either alone leaves a hole:

- **Ingress suppression, atomic.** A STOP arriving is recorded in the same transaction that
  suppresses the contact. A STOP that is acknowledged but not yet effective is a suppression miss
  waiting to happen.
- **Egress recheck inside the send transaction.** The claim-to-send window is where the race lives:
  a message claimed for sending before the STOP arrived must still be stopped, because the recheck
  happens inside the transaction that dispatches it, not before it.

Ambiguity fails closed. "dừng lại nhé" is a stop; "dừng giao hàng hôm nay thôi" may not be. When the
system cannot tell, it is `REQUIRE_HUMAN` — never a guess in either direction.

## Constraints

- Consent state is server-side. A consent claim arriving in a channel payload is an input to be
  verified, never an authority; forged consent is denied.
- The model never decides whether a message is a STOP. Deterministic code does.
- Preserve atomic mutation + domain event + audit + outbox semantics.
- An unknown send outcome is never automatically retried — a `channel_operations` prohibition that
  interacts directly with suppression, because a retry after a STOP is a suppression miss.
- No real customer data in fixtures.

## Required tests

- ingress suppression and its audit record commit atomically; a failed audit write rolls back the
  suppression;
- a STOP arriving after a send is claimed but before it is dispatched stops the send;
- the opt-out versus send-claim race is exercised concurrently, not simulated sequentially;
- an ambiguous opt-out returns `REQUIRE_HUMAN` and suppresses nothing silently;
- forged consent in a channel payload is denied and audited;
- suppression survives a worker restart mid-transaction.

## Done when

- both checks exist and the race test passes repeatedly under concurrency;
- every path is audited;
- the full gate battery passes with no required skips;
- rollback is reverting the commit; no suppression record is destroyed by the rollback.
