# TASK-channel-zalo-001 — official Zalo OA adapter

**Goal:** implement the production channel against verified provider behaviour rather than assumed
behaviour.

**Domains:** `channel_operations`

**Stable work item:** `CHANNEL-ZALO-001`

**Stage:** M5
**Risk:** HIGH — this is the code path a real customer touches.

## Why this exists

`docs/adr/0005-official-channel-selection-zalo-oa.md` selects official Zalo OA. `CHANNEL-001` — the
G2 public entry gate — depends on this item, which in turn depends on the canonical envelope
(`CHANNEL-ENVELOPE-001`), the OA authorization (`CHANNEL-ZALO-APPLY-001`) and suppression
(`CONSENT-STOP-001`).

`CHANNEL-TELEGRAM-001` will already have exercised the canonical contract end to end against a real
provider. Its findings are inputs here. Its results are not evidence here — Telegram counts toward
no gate minimum.

## Required design

Every provider behaviour is **verified against official documentation at build time and asserted in
a test**, never assumed from another provider's semantics:

- webhook authentication, replay rejection and payload size limits;
- token refresh with no loss of inbound or outbound messages across the refresh;
- the messaging window: outside it, the system **fails closed** rather than attempting a send that
  the provider will reject or, worse, silently drop;
- unknown outcome reconciliation — never an automatic retry, only provider confirmation or a human;
- persistence and deduplication **before** any model call.

## Constraints

- No channel credential is reachable from the agent cell. Prove it by attempt, as
  `DEPLOY-TARGET-001` does for the zone boundary.
- Zalo Personal automation is prohibited. Only the official OA path may be implemented.
- An unknown send outcome is never automatically retried — the prohibition that most directly
  causes duplicate sends, which are a zero-tolerance G2 defect.
- Do not enable automatic send. G2 permits it only for `LIST_PRICE_INFO`, and only under a signed
  gate manifest.
- No real customer data in fixtures.

## Required tests

- an unauthenticated or replayed webhook is rejected;
- an oversized payload is rejected at the boundary with no partial persistence;
- a duplicate inbound message creates exactly one inbox row;
- token refresh loses no message in either direction;
- a send attempted outside the messaging window fails closed with a typed outcome;
- an unknown outcome is surfaced for reconciliation and never retried automatically;
- the agent cell cannot read the channel credential, proven by a failed attempt.

## Done when

- every provider behaviour above is verified against official documentation and covered by a test
  that names the documentation it encodes;
- the credential is unreachable from the agent cell;
- the full gate battery passes with no required skips;
- every capability still reports `NOT_AUTHORIZED`;
- rollback is disabling the adapter route, which returns the system to internal-only operation.
