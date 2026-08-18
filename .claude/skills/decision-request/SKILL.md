---
name: decision-request
description: Convert an unknown business policy into an owner-answerable decision request and a fail-closed registry entry. Use whenever work stalls on something the business has not decided — a price, a policy, a threshold, a name, who is responsible.
---

# Open a decision request

The model never decides business policy. But failing closed and stopping is only half the job — the
other half is handing back something the owner can answer in a minute. That artifact is a decision
request.

Every one of `DEC-013` through `DEC-017` came out of this move, and each replaced a place where an
agent would otherwise have guessed.

## When this applies

- A number the business has not confirmed (anything marked `CẦN ĐO` / `CẦN CHỐT` in
  `BUSINESS_TRUTH_INTAKE.md`).
- A policy with two defensible readings and no recorded answer.
- A responsibility with no name attached.
- A boundary question — what counts as a customer, when does a record get created, whose account is
  this.

If a plausible default would work and be invisible if wrong, that is exactly the case this exists
for. Invisible-if-wrong is the dangerous kind.

## Write `docs/DECISION_REQUEST_<TOPIC>_<YYYY-MM>.md`

House style — match the existing files in `docs/`:

1. **What happens today**, with `file:line` citations proving it. Measured, not remembered. Open the
   file and quote it.
2. **What specifically stops.** Which motion, which screen, which order shape, which customer. A
   decision request that cannot name what it unblocks is a question, not a request.
3. **What the owner is being asked** — two to four numbered questions a non-engineer can answer
   without reading code. Vietnamese business context, not engineering register.
4. **An options table** with a recommendation and the cost of each option.
5. **"Until it is signed"** — the fail-closed behaviour holding in the meantime, stated precisely
   enough that someone can verify it is actually what the code does.

Never assert a fact you have not verified. Where something is unknown, mark it `CẦN XÁC MINH` and say
who could confirm it. A decision request that contains a guess is worse than no decision request,
because it launders the guess into the record.

## Register it

Append to `context/DECISION_REGISTRY.yaml` with `status: OPEN`, an `id`, a `fail_closed_behavior`,
and a `source`. Leave every existing entry byte-identical.

If you are taking a number an older document recommended for a different question, say so in a
`supersedes_recommendation` field and correct any live pointer — including in the console. A stale
`DEC-0NN` reference rendered to operators is a real defect, not a documentation nit.

```bash
uv run python scripts/check_context_drift.py
```

## What you do not do

- **Do not enqueue anything.** Adding a row to `delivery/WORK_QUEUE.yaml` is a scheduling act and it
  is the owner's.
- **Do not resolve it yourself**, and do not change any existing decision's status.
- **Do not code around it.** A workaround that avoids the decision usually encodes an answer to it.
- Some questions cannot be registry entries at all — `DEC-HOSTING` is the standing example. You may
  assemble the admissibility packet; you may not select a vendor or accept terms.
