# TASK-channel-001 — official channel and isolated public-cell Assisted entry

**Goal:** open the public path for exactly one capability, behind a signed gate.

**Domains:** `channel_operations`, `runtime_architecture`, `evaluation_release`

**Stable work item:** `CHANNEL-001`

**Stage:** M5
**Risk:** HIGH — the first message the system sends without a human reading it first.

## Why this exists

This is the `G2_PUBLIC_ASSISTED_ENTRY` carrier. Only `LIST_PRICE_INFO` is initially auto-send
eligible; every other capability stays behind G3. `AUTONOMY-001` depends on this item.

## Required conditions

Each is a separate item and all must be complete: `CHANNEL-ZALO-001` (the adapter),
`CONSENT-STOP-001` (suppression), `PUBLIC-POLICY-001` (the published bundle),
`EVAL-PUBLIC-CORPUS-001` (graded against that bundle) and `SHADOW-001` (14 clean days).
`DEC-005` and `DEC-006` must both be `RESOLVED`.

## Required evidence

- **Official channel review** — the adapter's provider behaviour, re-verified against current
  official documentation at the time of entry rather than at the time it was built.
- **Public-cell scan** — the Zone A image, with zero critical or high findings, digest-bound.
  No channel credential, generic tool, owner mount or direct-send capability in the cell.
- **Webhook replay tests** against the deployed endpoint, not against a local harness.
- **A signed G2 manifest** satisfying `docs/adr/0006-two-party-release-authorization.md`: two
  parties with `OWNER` distinct, schema-required compensating controls, computed cooling-off, and
  external review at the assisted stage.
- **A rollback drill** — the public path closed and reopened, timed, with no message lost or
  duplicated.

## Constraints

- `LIST_PRICE_INFO` only. Enabling a second capability is `G3_ASSISTED_EVIDENCE_COMPLETE`.
- The first 200 automated sends are human-reviewed — a G3 requirement that begins the moment the
  first automatic send happens, so the review process must exist before entry, not after.
- The public cell remains a reasoning client. It never becomes the business or security authority,
  and it never calculates money.
- An unknown send outcome is never automatically retried.
- Zero tolerance: wrong money, unauthorized action, cross-customer disclosure, suppression miss,
  duplicate send. Any occurrence closes the path.

## Done when

- the official channel review, public-cell scan, webhook replay tests and rollback drill are all
  recorded;
- a valid, signed, unexpired G2 manifest exists and `scripts/verify_release_candidate.py` accepts
  it;
- `LIST_PRICE_INFO` reads `AUTHORIZED` in `delivery/CAPABILITY_STATUS.yaml`, revalidated against the
  signed manifest, the hash-pinned signer registry, the deployed commit and the activation window;
- every other capability still reports `NOT_AUTHORIZED`;
- rollback is closing the public path, rehearsed and timed before entry rather than after.
