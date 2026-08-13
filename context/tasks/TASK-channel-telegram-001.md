# TASK-channel-telegram-001 — Telegram sandbox adapter

**Goal:** prove the channel contract end to end against a real provider, while the Zalo OA
application is still in external review.

**Domains:** `channel_operations`

**Stable work item:** `CHANNEL-TELEGRAM-001`

**Stage:** M4B
**Risk:** MEDIUM — the risk is that a sandbox result gets quoted as gate evidence.

## Why this exists

`CHANNEL-ZALO-APPLY-001` takes 2–8 weeks of external verification that no engineering shortens.
Meanwhile the canonical envelope, the receipt path, deduplication and reconciliation have never met a
real webhook. Telegram is a supported, documented Bot API that can exercise all of it now.

`docs/adr/0005-official-channel-selection-zalo-oa.md` confines Telegram to an engineering sandbox,
and the `channel_operations` domain states the prohibition plainly: **Telegram is a sandbox only and
never counts toward any gate evidence minimum.** This item exists to de-risk `CHANNEL-ZALO-001`, not
to substitute for it.

## Required design

Build against the canonical contracts from `CHANNEL-ENVELOPE-001`, not against Telegram's shapes.
If the adapter needs a concept the envelope cannot express, that is a finding about the envelope.

- authenticate the webhook and reject an unauthenticated or replayed update;
- persist and deduplicate **before** any model call — a `channel_operations` prohibition;
- acknowledge a duplicate update exactly once;
- reject oversized and malformed payloads at the boundary;
- emit a receipt per send attempt;
- reconcile an unknown outcome without ever retrying it automatically.

## Constraints

- Sandbox only. No real customer, no real PII, no production credential, no gate evidence.
- The bot token is a channel credential: it never reaches the agent cell and never enters the
  repository.
- Do not let Telegram's semantics leak into the canonical envelope. The envelope is provider-neutral
  and `CHANNEL-ZALO-001` must be able to satisfy it too.
- No capability is authorized and no automatic send is enabled.

## Required tests

- an unauthenticated webhook call is rejected;
- a replayed update is rejected;
- a duplicate update is acknowledged exactly once and creates one inbox row;
- oversized and malformed payloads are rejected at the boundary with no partial persistence;
- every send attempt produces a schema-valid receipt;
- an unknown outcome is surfaced for reconciliation and is never automatically retried;
- no fixture contains real personal data.

## Done when

- the full inbound and outbound path runs against the real Bot API in a sandbox;
- every finding about the canonical envelope is recorded for `CHANNEL-ZALO-001`;
- the evidence record states explicitly that it is sandbox-only and counts toward no gate minimum;
- rollback is removing the adapter and its route, which touches no production path.
