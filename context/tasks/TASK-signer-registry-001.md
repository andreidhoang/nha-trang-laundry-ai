# TASK-signer-registry-001 — two-party release authorization

**Goal:** make the release gate satisfiable by the two people who exist, without turning
separation of duty into a fiction the schema certifies.

**Domains:** `evaluation_release`

**Stable work item:** `SIGNER-REGISTRY-001`

**Stage:** M5C
**Risk:** HIGH — this task weakens a control. It must weaken exactly the intended part.

## Required change

Per ADR-0006. Note this is **code plus a ceremony**, not only a schema file.

1. `release-gate-manifest-v2.schema.json` is already derived from v1 and validated. It keeps three
   signed functions and adds a required `compensating_controls` block.
2. Extend the release verifier to apply v2 rules when `schema_version` is 2, while still applying v1
   rules to v1 manifests. Both versions remain valid.
3. Enforce, in code:
   - the `OWNER` `actor_id` differs from every other signoff `actor_id`;
   - if `SECURITY` and `OPERATIONS` share an `actor_id`, their `key_id`s differ and
     `dual_role_declared` is `true`;
   - `cooling_off_hours` is **computed** from the latest gate-evidence timestamp to the earliest
     signoff timestamp, and compared against the stage minimum (24h SHADOW, 72h ASSISTED/BOUNDED).
     The declared value is cross-checked, never trusted;
   - `external_review` is present, fetchable and hash-verified for ASSISTED and BOUNDED.
4. Run the key ceremony and publish the public-key-only registry.

## Constraints

- Private keys are generated on the signer's own device and never enter this repository, CI, an
  image, or any archive produced by `BACKUP-RESTORE-001`.
- The engineer may never sign `OWNER`. This is the separation that survives, and it is the one that
  matters — the person who builds the system cannot alone authorize its effect on customers.
- The owner's key is generated in the owner's presence with a written record of what a signature
  means. A signature over a hash whose contents were not read is not authorization.
- No manifest is produced by this task. Every capability stays `NOT_AUTHORIZED`.
- Do not relax any other v1 invariant: JCS payload hashing, detached signature verification,
  commit/stage/capability binding, artifact hash verification, chronology and expiry all remain.

## Required tests

Each is a rejection path, and each must fail closed:

- `OWNER` `actor_id` reused in `SECURITY` or `OPERATIONS`;
- shared `SECURITY`/`OPERATIONS` actor with an identical `key_id`;
- shared actor without `dual_role_declared: true`;
- computed cooling-off below the stage minimum, including a manifest whose declared
  `cooling_off_hours` is inflated above the computed value;
- ASSISTED manifest with no `external_review`;
- `external_review` report whose hash does not match the fetched artifact;
- v1 manifest still validated under v1 rules and not required to carry `compensating_controls`;
- expired manifest, wrong commit and unknown signer still rejected as before.

## Done when

- the verifier enforces every rule above with a negative test per rule;
- the signer registry contains public keys only, pinned by an out-of-band SHA-256;
- rotation and revocation are exercised once, before G1, not first attempted during an incident;
- `scripts/report_delivery_status.py` still reports every capability `NOT_AUTHORIZED`;
- rollback is reverting to v1-only verification, which blocks releases rather than permitting them.
