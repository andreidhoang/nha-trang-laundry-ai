# TASK-channel-zalo-apply-001 — official Zalo OA registration and business verification

**Goal:** start the external clock on the only approved production channel.

**Domains:** `channel_operations`, `business_truth`

**Stable work item:** `CHANNEL-ZALO-APPLY-001`

**Stage:** M5
**Risk:** MEDIUM — the risk is entirely schedule, and it is not compressible by engineering.

## Why this exists

`docs/adr/0005-official-channel-selection-zalo-oa.md` selects official Zalo OA as the production
channel. `CHANNEL-ZALO-001` builds the adapter, `CHANNEL-001` is the public entry gate, and both wait
on an approval issued by Zalo, not by this project.

**Verification takes 2 to 8 weeks and none of it is engineering work.** No amount of code shortens
it. It has no dependencies in the queue and can start today.

Zalo Personal automation is prohibited — a `channel_operations` prohibition, not a preference.
Telegram is an engineering sandbox only and never counts toward any gate evidence minimum, which is
why `CHANNEL-TELEGRAM-001` exists in parallel and cannot substitute for this item.

## What must be obtained

- **Business verification** for the Official Account, in the business's own name.
- **An API tier** that permits the messaging pattern the system needs: inbound webhook delivery,
  outbound send with a receipt, and whatever messaging-window rules apply to the tier. Record the
  window rules precisely — `CHANNEL-ZALO-001` must fail closed against them.
- **Separation from any personal account.** The OA must not be operated through, or recoverable via,
  an individual's personal Zalo identity.
- **The `DEC-005` decision record**, moving from `OPEN` to `RESOLVED` with the selected channel and
  the tier.

## Constraints

- Do not build the adapter under this item. Provider behaviour is verified at build time against
  official documentation in `CHANNEL-ZALO-001`; asserting it here would be assertion, not evidence.
- Do not connect a webhook, publish an endpoint, or send a message. This item obtains authorization,
  it does not exercise it.
- Credentials issued during registration go to the owner and to secret storage, never to the
  repository and never to the agent cell — `CHANNEL-ZALO-001` proves the agent cell cannot reach
  them.
- No capability is authorized. `LIST_PRICE_INFO` still requires G1 and G2 after this.

## Done when

- the Official Account is business-verified in the business's name;
- the provisioned API tier is recorded, including its messaging-window rules;
- separation from any personal account is confirmed;
- `DEC-005` reads `RESOLVED` with the selected channel;
- `CHANNEL-ZALO-001` can start against a real, documented provider rather than an assumed one.
