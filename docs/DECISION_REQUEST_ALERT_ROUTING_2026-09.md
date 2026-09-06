# Decision request — who is told when the shop's software is in trouble

**Date:** 2026-09-03
**Status:** RESOLVED 2026-09-03 as `DEC-025` — a Telegram bot to the owner's phone, with host cron
mail retained as the floor. This document is the packet that was put to the owner; the register in
`context/DECISION_REGISTRY.yaml` carries the decision and is authoritative. It said OPEN for three
days after the decision was taken, which is the same defect the console's gap register had.
**Trigger:** `SHOP-OBSERVABILITY-001` built four checks that can genuinely fail on the R1
deployment. Nothing carries their result to a person.

---

## 0. Why this is a decision and not an implementation detail

Every way of delivering an alert adds a counterparty and a credential: an SMTP account, a
messaging bot token, a paging provider. R1 deliberately has none — no channel is connected, no
provider is authorized, and `DEC-006` is open. Adding one to carry alerts would be the first
outbound integration this deployment has, and choosing it is the owner's, not an engineer's.

An alert nobody receives is not an alert. That sentence is the whole reason this request exists.

## 1. What can fail, and what it costs to miss it

| Check | What it means when it fails | Cost of finding out late |
|---|---|---|
| **WAL archive gap** | The recovery guarantee is silently gone. Backups look configured and are not current. | Discovered on the morning of a restore, which is the worst possible morning. |
| **Database volume filling** | A failing `archive_command` pins WAL segments forever; the disk fills; PostgreSQL stops accepting writes. | **The counter cannot take an order.** The shop is down and the cause is invisible from the console. |
| **Console unreachable** | In R1 the console *is* the business — no channel, no agent, no fallback. | Staff fall back to paper immediately, so this one announces itself. The alert matters for how fast somebody starts fixing it. |
| **A capability flag enabled without a signed manifest** | An authorization bypass. `PRODUCTION_OPERATIONS_SPEC_V1` §4.1 calls this a security incident rather than operational noise. | An AI capability or an automated send running with nobody's signature behind it. |

The first two are the ones a person will not notice on their own.

## 2. Options

| Answer | Consequence |
|---|---|
| **A. Local mail to the host operator** (recommended) | A host cron that mails root, read over SSH. No new counterparty, no credential, no data leaving the host. Costs: somebody has to actually read it, and it is worthless if the host itself is down — which is exactly the case where the console-unreachable check cannot run either. Honest for the volume and archive checks, weak for host death. |
| **B. A messaging bot the owner already uses** (Telegram, Zalo) | Reaches a phone, which is what an alert is for. Introduces the first outbound credential in the deployment and a channel `DEC-005`/`DEC-016` have opinions about. Would need to be scoped so an alerting bot cannot become a customer-messaging path by accident. |
| **C. A paging provider** | Built for this and reliable. A third counterparty, a subscription, and a data-processing question for a two-person shop. Disproportionate. |
| **D. Nothing yet** | The current state. Checks run, exit non-zero, and the console shows the last failure. The shop finds out about a full disk when it cannot take an order. |

**Recommendation: A now, and B once a channel exists for its own reasons.** Local mail costs
nothing and carries the two checks that matter most; revisiting when `CHANNEL-TELEGRAM-001` lands
avoids creating a credential for alerting alone.

## 3. What only the owner can decide

- Which of the four checks warrant waking somebody, and at what hour.
- Whether an alerting channel may be the same account customers will eventually message.
- Who is on the other end, by name. `DEC-016` records that the equivalent question for inbound
  customer messages — "Người trực inbound" — is still blank.

## 4. Signature block

```yaml
decision_dec_025_alert_routing:
  answer:              # A | B | C | D
  channel:             # if B: which account, and whose
  recipient:           # by name
  hours:               # always, or opening hours only
  owner: BUSINESS_OWNER
  decided_at:
  owner_written_approval:
```
