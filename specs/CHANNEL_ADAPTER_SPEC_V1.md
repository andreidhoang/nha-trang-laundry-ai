# Channel Adapter Specification v1

**Ngày phát hành:** 2026-08-12
**Trạng thái:** `SPEC_APPROVED_NO_CHANNEL_AUTHORIZED`
**Nguồn quyết định:** ADR-0003 §3, ADR-0005, ADR-0007
**Contracts:** `contracts/channel-inbound-envelope-v1.schema.json`,
`contracts/channel-outbound-receipt-v1.schema.json`

This specification defines how any messaging provider connects to the system. It authorizes no
channel, no credential, no public ingress and no send. `G2_PUBLIC_ASSISTED_ENTRY` remains the only
thing that can do that.

Prose here explains intent. Where prose and the structured contracts disagree, the contracts win and
CI must report drift (`specs/README.md`).

## 1. Nguyên tắc

A channel adapter is a **translator with no opinions**. It converts provider-specific bytes into one
canonical envelope and provider-specific delivery results into one canonical receipt. It contains no
business logic, no policy evaluation, no pricing, no model call and no send decision.

Everything an adapter is forbidden to do:

- decide whether to reply, what to reply, or whether an action is permitted;
- resolve a customer, contact, order or address identity from model output or message content alone;
- call the agent runtime, synchronously or otherwise;
- send anything. Only the provider-specific sender worker, driven by the transactional outbox, sends.

An adapter that stays this small can be reviewed in an afternoon and replaced without touching the
domain. That is the entire point.

## 2. Ingress

```text
provider webhook
  -> authenticate (§2.1)
  -> reject oversized/malformed (§2.2)
  -> normalize to canonical envelope (§3)
  -> resolve contact binding server-side (§2.3)
  -> deterministic STOP/suppression check (§5)
  -> durable inbox insert, deduplicated (§2.4)
  -> acknowledge provider
  -> asynchronous agent or human processing
```

The acknowledgement happens **after** the durable insert and **never** waits on a model response.
Providers retry on non-2xx; a model timeout must never become a duplicate customer message.

### 2.1 Authentication and replay

Every inbound request is authenticated before any parsing beyond what authentication itself requires.

- The provider's published signature or secret-header scheme is verified with a constant-time
  comparison against a credential held only in Zone C (ADR-0007 §1).
- Requests carrying a timestamp are rejected outside a bounded skew window.
- The raw body used for signature verification is the exact bytes received. Re-serializing before
  verification is a defect.
- Verification failure returns a generic rejection. It never reveals which check failed.
- An unauthenticated request is never persisted, never logged with its body, and never counted.

### 2.2 Resource limits

Body size cap, header count cap, and a per-provider rate limit applied before normalization. An
oversized or malformed payload is rejected without partial processing. Attachments are recorded as
references and typed as `UNSUPPORTED` unless the capability explicitly handles them.

### 2.3 Contact binding is server-side

The provider's user identifier maps to an internal contact through a server-owned binding table. The
model never supplies, influences or overrides this binding — this is the existing bound-request IDOR
invariant applied at ingress.

An unrecognized provider identity creates a new unverified contact. Unverified contacts may receive
only capabilities whose disclosure policy permits an unauthenticated audience. They never receive
order status, quotes or any customer-specific fact.

### 2.4 Deduplication

Every provider update carries a provider-assigned identifier. `(provider, provider_update_id)` is
unique in the inbox. A replayed update is acknowledged and discarded.

Where a provider does not guarantee a stable update identifier, the adapter derives one
deterministically from stable fields and records `dedupe_key_source: DERIVED`. A derived key is a
declared weakness, not a silent fallback.

## 3. Canonical inbound envelope

One shape for every provider, defined by `channel-inbound-envelope-v1.schema.json`. Provider payloads
are normalized into it and the raw body is **not** retained beyond what audit requires.

Required semantics:

| Field | Meaning |
|---|---|
| `provider` | `ZALO_OA` \| `TELEGRAM_SANDBOX`. Reserved: `FACEBOOK_MESSENGER`. |
| `provider_update_id` | Provider-assigned; the deduplication key. |
| `received_at` | Server receipt time, not provider-claimed time. |
| `contact_binding` | Server-resolved internal contact reference and verification state. |
| `content` | Typed content: `TEXT`, `IMAGE`, `FILE`, `STICKER`, `LOCATION`, `UNSUPPORTED`. |
| `text` | Present only for `TEXT`. Length-capped. Never interpreted by the adapter. |
| `attachments` | References only. No bytes in the envelope. |
| `provider_metadata` | Bounded allowlist of transport fields. Never a raw payload dump. |

Channel transport vocabulary lives in these contracts, deliberately **not** in
`canonical-enums-v1.json`. Canonical enums are business vocabulary consumed by the domain and the
agent tool contract; transport identifiers are neither, and mixing them would couple provider
churn to the domain's canonical snapshot.

## 4. Egress

```text
approved or capability-authorized content
  -> transactional outbox row (idempotency key, message kind, contact)
  -> provider-specific sender worker
  -> send attempt
  -> canonical receipt (§4.1)
  -> reconciliation (§4.2)
```

The sender worker is the only component holding a channel credential. It never evaluates policy — it
sends what the outbox already authorized, or it fails.

Outbound status uses the existing canonical `MessageDeliveryStatus`. The adapter adds no parallel
status vocabulary.

### 4.1 Receipt

Every send attempt produces exactly one receipt row conforming to
`channel-outbound-receipt-v1.schema.json`, including attempts that fail or time out.

### 4.2 Unknown outcomes

A send whose outcome is unknown — timeout, connection reset, ambiguous provider response — is the
only genuinely hard case, because both "assume sent" and "assume not sent" are wrong.

Required handling:

1. record `reconciliation_state: UNKNOWN`;
2. **never** retry automatically on an unknown outcome;
3. attempt provider-side confirmation where the provider exposes it;
4. where it does not, escalate to `UNKNOWN_REQUIRES_HUMAN` and surface it in the staff exception
   queue with the exact message content, so a human can check the conversation and decide;
5. only a human or a provider confirmation may move the state out of `UNKNOWN`.

An automatic retry on an unknown outcome is how customers get the same message twice. It is
prohibited, not discouraged.

### 4.3 Messaging windows

Providers restrict when a business may message a user outside an active conversation window, and
out-of-window messages typically require pre-approved templates.

The adapter does not model these rules from memory. It reads a per-provider window policy from
published configuration, evaluated deterministically before the outbox row is created. An expired or
unknown window fails closed to `REQUIRE_HUMAN` — the send is held for staff, not attempted and not
silently dropped.

## 5. STOP, consent and suppression

Deterministic on **both** ingress and egress, per `context/CONTEXT_MAP.yaml` `privacy_consent`.

- **Ingress:** a recognized opt-out phrase records suppression atomically with the inbound envelope
  and blocks downstream processing of that message beyond acknowledgement.
- **Egress:** the sender worker re-checks suppression inside the send transaction, immediately before
  the provider call. A check performed only when the outbox row was created is insufficient.
- **The race:** an opt-out arriving while a send is in flight must not deliver that send. The
  suppression write and the outbox claim contend on the same row; the loser is the send.
- **Ambiguity:** a message that may or may not be an opt-out is `REQUIRE_HUMAN`. It is never resolved
  by the model.
- Suppression is never overridden by any capability, including transactional messages.

## 6. Provider profiles

Profiles record what is provider-specific. Both share every rule above.

### 6.1 Zalo OA — production candidate

Authentication scheme, token lifecycle, messaging-window rules, template requirements and rate
limits **must be verified against current official Zalo documentation during `CHANNEL-ZALO-001`** and
recorded in its evidence. ADR-0005 §5 deliberately does not fix them here, because they change
independently of this repository and a stale constant in a spec is worse than no constant.

Fixed regardless of provider detail:

- credentials live in Zone C only; token refresh is automatic, logged, and never writes the token to
  a trace, metric or archive;
- a refresh failure disables sending and raises an alert — it never falls back to an expired token;
- unverified contacts get the unauthenticated-audience capability set only.

### 6.2 Telegram — engineering sandbox only

Enabled in development and private staging only, with synthetic contacts. Never production, never
real PII, never evidence for any gate (ADR-0005 §3).

Its purpose is to prove §2–§5 end to end while Zalo OA verification is pending. If the canonical
envelope needs a Zalo-shaped change that Telegram cannot express, the envelope is wrong.

## 7. Required tests

Each is a named acceptance check on `CHANNEL-ENVELOPE-001`, `CHANNEL-TELEGRAM-001`,
`CHANNEL-ZALO-001` or `CONSENT-STOP-001`.

**Ingress:** valid signed update accepted · invalid signature rejected without persistence · replayed
signature outside skew rejected · duplicate `provider_update_id` acknowledged exactly once ·
oversized body rejected · malformed JSON rejected · unsupported content type normalized, not dropped
· unknown provider identity creates an unverified contact with no customer-specific access · model
output cannot alter contact binding.

**Egress:** one outbox row produces exactly one send · provider rejection recorded, not retried
blindly · unknown outcome yields `UNKNOWN`, no automatic retry, and a staff exception · duplicate
outbox claim by two workers is prevented by the existing lease · expired messaging window holds the
send for a human · token refresh mid-send does not duplicate or drop.

**Consent:** ingress opt-out suppresses atomically · egress re-check blocks a send whose suppression
landed after outbox creation · concurrent opt-out and send claim resolves against the send ·
ambiguous opt-out is `REQUIRE_HUMAN` · forged consent is rejected.

**Isolation:** the public agent cell cannot reach the channel API — proven by connection attempt
(ADR-0007) · no channel credential appears in any image layer, log line, OTel attribute or backup
archive.

## 8. Cấm

- Zalo Personal automation, in any form, for any reason.
- Channel credentials in the public agent runtime cell.
- A model call in the webhook request path.
- Automatic retry on an unknown send outcome.
- Raw provider payload bodies in traces, evidence or logs.
- A second status vocabulary for outbound messages.
- Any adapter reading or writing business tables directly instead of through the domain.
