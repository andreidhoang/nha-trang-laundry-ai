# ADR-0006: Two-party release authorization with compensating controls

**Status:** accepted

**Date:** 2026-08-12
**Amends:** the separation-of-duty invariant in
`specs/contracts/release-gate-manifest-v1.schema.json` (`signoffs`, three distinct actor IDs).
All other release-gate invariants — JCS payload hashing, detached signature verification, deployed
commit/stage/capability binding, artifact hash verification, chronology and expiry — remain binding
and unchanged.

## Context

`release-gate-manifest-v1.schema.json` requires exactly three `signoffs` with functions `OWNER`,
`SECURITY` and `OPERATIONS`, and the runtime additionally rejects *"reused signer identities across
functions"*. Three distinct human actors must therefore exist to authorize any gate.

The organization has two: one engineer and the shop owner. There is no third party.

A control that cannot be satisfied is not a control. Left as-is it produces one of two outcomes, both
worse than amending it: the gate is never passed and the system never ships, or someone creates a
third identity for one human and the separation-of-duty invariant becomes a lie that the schema now
certifies. The second outcome is the dangerous one, because every downstream artifact would carry a
cryptographically valid signature attesting to a review that did not happen.

Separation of duty exists to stop one person from both building a thing and unilaterally declaring it
safe for customers. That objective is achievable with two people. What is not achievable with two
people is independent security review — and that is the part worth buying rather than pretending.

## Decision

### 1. Keep three signed functions; relax actor distinctness asymmetrically

`release-gate-manifest-v2.schema.json` keeps the three `signoffs` entries and their functions, so the
audit record still shows what each function attested to. It changes only the distinctness rule:

- `OWNER` **must** be an actor distinct from every other signoff actor. The business owner signs for
  the business. The engineer may never sign `OWNER`.
- `SECURITY` and `OPERATIONS` **may** share an `actor_id`, and must then use distinct `key_id`s so
  each attestation remains individually revocable.

The load-bearing separation is preserved: the person who builds the system cannot alone authorize its
effect on customers.

### 2. Compensating controls are schema-required, not prose

v2 adds a required `compensating_controls` object. A manifest without it is invalid, so these cannot
decay into intentions:

| Field | Requirement |
|---|---|
| `dual_role_declared` | `true` when one actor holds SECURITY and OPERATIONS. Makes the reduced separation explicit in the artifact rather than inferable from actor IDs. |
| `cooling_off_hours` | Minimum elapsed time between the last evidence artifact timestamp and the earliest signoff. `>= 24` for G1, `>= 72` for G2 and above. Blocks same-session self-approval. |
| `external_review` | Required for `G2_PUBLIC_ASSISTED_ENTRY` and above: reviewer identity, scope, completion date and a SHA-256 hash of the review report, verified like any other referenced artifact. Optional for G1. |

The verifier enforces `cooling_off_hours` arithmetically against evidence and signoff timestamps and
rejects a missing or unfetchable `external_review` artifact at G2+. It is not a self-reported number.

### 3. External security review is purchased once, before public ingress

G1 covers internal Shadow with a human approving every outbound action; the blast radius is the two
people already in the loop. G2 is the first gate that exposes the system to the public, so that is
where independent review is mandatory.

Scope for the G2 review, at minimum: public agent cell isolation, webhook authentication and replay
handling, credential placement, the deterministic policy decision point, consent and suppression
paths, and outbound send authorization.

### 4. Key ceremony and registry

`SIGNER-REGISTRY-001` provisions the keys. Requirements:

- keys generated on the signer's own device; private keys never enter this repository, CI, any
  container image, or any backup captured by `BACKUP-RESTORE-001`;
- `trusted-release-signers-v1` registry contains public keys only, pinned by an out-of-band SHA-256;
- the owner's key is generated in the owner's presence with a written record of what signing means —
  a signature the owner does not understand is not authorization;
- key rotation and revocation are exercised once before G1, not first attempted during an incident.

### 5. v1 manifests remain valid

No existing manifest is invalidated or migrated. There are none — no signed manifest has ever been
produced. The verifier accepts both schema versions and applies each version's own rules, so the
amendment is additive.

## Consequences

- G1 becomes reachable by the organization that actually exists.
- The reduced separation of duty is visible in every manifest that relies on it, rather than hidden
  behind three plausible actor IDs.
- One real external review is purchased at the point where it protects real customers.
- Cooling-off is enforceable arithmetic, which is the only kind of control that survives being
  inconvenient at 11pm.
- If a third independent reviewer later exists, manifests may simply stop setting
  `dual_role_declared`. No further schema change is needed.

## Required verification before this ADR takes effect

- `release-gate-manifest-v2.schema.json` validates under `scripts/verify_contracts.py`;
- the verifier rejects: an `OWNER` actor reused in any other function; a missing
  `compensating_controls` block; cooling-off shorter than the gate minimum; a missing or
  hash-mismatched `external_review` at G2+;
- negative tests cover each rejection path;
- `scripts/report_delivery_status.py` continues to report every capability `NOT_AUTHORIZED`, because
  no signed manifest exists.
