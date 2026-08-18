# Decision request — official production customer channel (DEC-005)

**Date:** 2026-08-18
**Status:** ratified 2026-08-18 by the business owner against the recommendation in §2 — see
`context/DECISION_REGISTRY.yaml` DEC-005. The decision is resolved; `CHANNEL-TELEGRAM-001` and
`CHANNEL-ZALO-APPLY-001` still each wait on their own artifact (a bot token, a completed business
verification) — neither exists yet.
**Trigger:** `CHANNEL-ZALO-APPLY-001` and `CHANNEL-TELEGRAM-001` both sit ready to start, and both are
blocked on the same unanswered question: which is the *official* channel, and in what order.
**Assessments this rests on:** `specs/TEAM_REVIEW_REPORT_V1.md` §5 ("NO-GO — public Assisted mode:
official provider channel is selected and approved; Zalo Personal remains prohibited"),
`BUSINESS_TRUTH_INTAKE.md` §5.

---

## 0. What this request does not do

- It does not create a Zalo OA application, a Telegram bot, or any channel credential.
- It does not authorize `PUBLIC_FAQ`, `LIST_PRICE_INFO`, `INTAKE_QUESTION`, or `ORDER_STATUS` — those
  still require G1/G2 evidence independent of which channel is chosen.
- Zalo Personal is not an option under review: the spec prohibits it outright, because a personal
  account cannot carry the channel-credential isolation ADR-0007 requires.

## 1. The actual choice is sequencing, not either/or

Two channels are relevant, and they are not substitutes for each other:

| Channel | What it takes to start | What it's for |
|---|---|---|
| **Telegram** | A bot token from BotFather — no business verification, an owner or staff member with a Telegram account can create one in minutes. `CHANNEL-TELEGRAM-001`'s adapter code is already built and tested; it activates the moment a token exists. | Low-friction channel to run G1 Shadow-mode validation (internal, human-approves-every-send) without waiting on external verification. Real Vietnamese retail customers overwhelmingly do not use Telegram, so it is not a credible path to G2 real customers on its own. |
| **Zalo OA** (Official Account, not Personal) | Business verification in the company's own legal name (**CÔNG TY TNHH A & T CARE**, MST 4202059758) through Zalo's own process — external, 2–8 weeks, requires the owner's business documents. Not started by this document. | The channel Vietnamese customers actually use for businesses. This is what G2 (real public customers) needs to mean anything. |

## 2. Recommendation

**Run both, in parallel, not as alternatives:**

1. Start the Zalo OA business-verification application now — it is the long external pole (2–8 weeks)
   and nothing about it can be sped up from this side. **This step needs the owner**, not an agent: it
   requires the business's own Zalo Business account and real registration documents.
2. Obtain a Telegram bot token in the same session — this can be done immediately, by the owner or by
   handing the bot-creation steps to an engineer, since it needs no business verification. This
   unblocks G1 Shadow-mode validation while the Zalo verification clock runs, so Shadow days and Zalo
   approval land close together instead of sequentially.
3. Register DEC-005 now as: **Telegram for Shadow-mode validation; Zalo OA is the official production
   customer channel once verified.** This is a real, signable answer — it does not defer the question,
   it sequences it.

## 3. What each answer unblocks

```
DEC-005 signed "Telegram-Shadow / Zalo-OA-production" ─┬─ CHANNEL-TELEGRAM-001 unblocks immediately
                                                        └─ CHANNEL-ZALO-APPLY-001 starts (external clock)
                                                            once verification completes → G2 becomes
                                                            reachable on the real customer channel
DEC-005 unsigned → both items stay BLOCKED; no Shadow-mode channel exists at all
```

## 4. Signature block

Fill and commit. Unsigned keeps the current behaviour: no public channel is admissible.

```yaml
decision_dec_005_official_channel:
  answer: TELEGRAM_SHADOW_THEN_ZALO_OA_PRODUCTION
  owner: BUSINESS_OWNER
  decided_at: '2026-08-18'
  zalo_oa_application_started_by:   # STILL OPEN — not started; needs the owner's Zalo Business account
  telegram_bot_owner:               # STILL OPEN — not created; needs a BotFather session
```
