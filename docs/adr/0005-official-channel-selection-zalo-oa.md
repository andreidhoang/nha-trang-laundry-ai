# ADR-0005: Official channel selection — Zalo OA production, Telegram sandbox

**Status:** accepted

**Date:** 2026-08-12
**Proposes resolution of:** `DEC-005` (Official production customer channel and supported provider
path). `DEC-005` is owned by `BUSINESS_OWNER` and stays `OPEN` in
`context/DECISION_REGISTRY.yaml` until the shop owner decides. This ADR records the engineering
direction and the reasoning behind it; it does not decide on the owner's behalf.
**Builds on:** ADR-0003 §3 (channel adapters are independent of AI). Nothing here grants public
ingress, send authority or capability authorization; those remain behind `G2_PUBLIC_ASSISTED_ENTRY`.

## Context

`DEC-005` has been open since the specification pack was issued and blocks `LIST_PRICE_INFO`,
`PUBLIC_FAQ`, `INTAKE_QUESTION` and `ORDER_STATUS`. ADR-0003 named Telegram Bot API as the preferred
engineering sandbox and official Zalo OA as a later production candidate without choosing.

The business facts constrain the choice more than the engineering facts do:

- `BUSINESS_TRUTH_INTAKE.md` §5 records a Zalo account on the hotline `0382 318 492`, a Facebook Page
  and a Google Business Profile. It explicitly notes the Zalo account is **not confirmed to be an
  OA**.
- The operating legal entity and tax code are confirmed (`CÔNG TY TNHH A & T CARE`, MST `4202059758`),
  which is what an OA business verification requires.
- Customers of a neighbourhood laundry in Nha Trang contact shops on Zalo. A channel with no real
  customers cannot produce the *"14 Shadow days, 100 representative interactions, 30 real orders"*
  evidence that `G2_PUBLIC_ASSISTED_ENTRY` requires.

That last point decides it. Telegram would ship fastest and produce evidence that represents nobody.

## Decision

### 1. Official Zalo OA is the production channel

The first and only production customer channel for `G2_PUBLIC_ASSISTED_ENTRY` is an **official Zalo
Official Account**, verified against `CÔNG TY TNHH A & T CARE` / MST `4202059758`.

### 2. Zalo Personal automation remains prohibited

Unchanged and non-negotiable. The existing account on `0382 318 492` may continue to be operated
manually by staff. It is never automated, never given a credential, and never wired to the outbox.
If that number is converted to an OA, the manual and official paths must be separated before any
automation.

### 3. Telegram Bot API is an engineering sandbox, never a production channel

Telegram exists in this project for exactly one purpose: to prove the channel adapter contract end to
end without waiting on an external approval. It is enabled only in development and private staging,
with synthetic contacts. It never receives a production credential, never carries real customer PII,
and no Telegram interaction counts toward any gate's evidence minimum.

### 4. Facebook Messenger is deferred, not rejected

The shop has a Page. Messenger remains an admissible second adapter after G2, and the canonical
envelope must not encode Zalo-specific assumptions that would block it. It is not built now — a
second channel before the first one has produced evidence multiplies the review surface without
adding information.

### 5. Provider specifics are verified at build time, not asserted here

Zalo OA authentication, webhook signature scheme, token refresh, messaging-window rules, template
requirements for out-of-window messages, and rate limits change independently of this repository.
This ADR deliberately does **not** record them.

`CHANNEL-ZALO-001` must verify each against current official Zalo documentation at implementation
time and record the observed behavior in its evidence. Any provider behavior that cannot be verified
fails closed to `NOT_SUPPORTED`.

What this ADR does fix are the invariants the adapter must satisfy regardless of provider details:

- inbound webhooks are authenticated and replay-resistant before any processing;
- an update is persisted to the durable inbox and deduplicated **before** any model call;
- the adapter acknowledges only after durable acceptance and never waits on a model response;
- provider identity maps to an internal contact **server-side**; the model never supplies that binding;
- outbound delivery goes only through the transactional outbox and a provider-specific sender worker;
- every send has an idempotency key, a recorded attempt, and a reconciliation path for unknown
  outcomes;
- the public agent runtime holds no channel credential and has no send client;
- STOP and suppression are deterministic checks on both ingress and egress.

### 6. Registration starts before the adapter is built

OA registration and business verification have an external lead time measured in weeks and are
entirely outside engineering control. `CHANNEL-ZALO-APPLY-001` starts immediately and runs in
parallel with `CHANNEL-ENVELOPE-001` and `CHANNEL-TELEGRAM-001`.

## Consequences

- Engineering proceeds on Telegram while OA approval is pending; only the public-entry step waits.
- `DEC-005` moves to `RESOLVED` in `context/DECISION_REGISTRY.yaml` with this ADR as its source. The
  affected capabilities stay `NOT_AUTHORIZED` — resolving the channel decision removes one blocker,
  not the gate.
- The canonical inbound envelope is provider-neutral by construction, because two adapters must map
  into it before G2.
- If OA verification is refused, this ADR is superseded rather than worked around. Automating the
  personal Zalo account is not an available response.

## Required verification before `CHANNEL-ZALO-001` completes

- webhook authentication, replay, duplicate-update, payload-limit and attachment tests;
- STOP-race test: opt-out arriving while a send is in flight holds the send;
- sender unknown-outcome and duplicate-send reconciliation tests;
- token refresh and credential rotation without dropped or duplicated updates;
- proof that no channel credential is reachable from the public agent runtime cell.
