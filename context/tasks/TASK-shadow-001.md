# TASK-shadow-001 — internal real-order Shadow pilot and G1 evidence

**Goal:** run the business on the system for fourteen days with a human approving every outbound
message, and find out what is actually wrong.

**Domains:** `orders_audit`, `business_truth`, `evaluation_release`, `privacy_consent`

**Stable work item:** `SHADOW-001`

**Stage:** M4C
**Risk:** HIGH — the first real customer data, the first real money, and the first opportunity for
the system to be wrong in public.

## Why this exists

This is the G1 evidence carrier at the business level. `CHANNEL-001` and everything at G2 depend on
it. It is the first item where the system meets reality rather than a fixture.

## Required conditions before the first order

- **A human approves every outbound message, for fourteen days.** `SHADOW-CONSOLE-001` exists so
  that there is somewhere to do this; without it the pilot is not physically possible.
- **A measured baseline** from `SHOP-INSTRUMENT-001`. Without it, 30 orders produce numbers with
  nothing to compare them to.
- **Retention obligations in force** — `RETENTION-001`. Real customer names, phone numbers and
  addresses enter the database here.
- **Runbooks and SLOs** — `OPS-RUNBOOK-001` and `SLO-VERIFY-001`. When something goes wrong during
  the pilot, the procedure must already exist.
- **A signer registry** — `SIGNER-REGISTRY-001`, because the pilot ends in a signed gate manifest.

## Required evidence

From `specs/evals/eval-manifest-v1.yaml` `shadow_exit`:

| Requirement | Minimum |
|---|---:|
| Real orders | 30 |
| Batch/cycle logs | 10 |
| Delivery logs | 20 |
| Representative interactions | 100 |
| Clean days | 14 |
| Wrong confirmed or sent monetary values | **0** |

Zero-tolerance across all of: wrong money, unauthorized action, cross-customer disclosure,
suppression miss, duplicate send.

## Constraints

- A zero-tolerance defect resets the clean-day count. It is not waived, explained away, or
  reclassified as a near-miss.
- The model drafts; a named human approves and sends. No automatic send exists at this stage and
  none may be enabled to save time during the pilot.
- Every material mutation keeps atomic mutation + domain event + audit + outbox semantics under real
  load, not only under test.
- Real customer data is in scope for the first time: no raw PII in logs, telemetry, evidence or the
  eval corpus.
- The pilot's outcome may be that the system is not ready. Record that outcome; do not extend the
  window until the numbers look better.

## Done when

- 30 real orders, 10 cycle logs, 20 delivery logs and 100 representative interactions are recorded;
- 14 clean days elapsed with zero zero-tolerance defects;
- a signed gate manifest exists, satisfying `docs/adr/0006-two-party-release-authorization.md`
  including its compensating controls and computed cooling-off;
- `G1_INTERNAL_SHADOW_READY` is genuinely satisfiable, item by item;
- rollback is returning to manual operation, which the shop is already capable of.
