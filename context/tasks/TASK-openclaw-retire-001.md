# TASK-openclaw-retire-001 — reversible public-path retirement

**Goal:** remove OpenClaw from the public production dependency and deployment path, keeping its
history complete and checkable.

**Domains:** `runtime_architecture`, `evaluation_release`, `platform`

**Stable work item:** `OPENCLAW-RETIRE-001`

**Stage:** M4C
**Risk:** HIGH

**Decision:** `docs/adr/0009-retire-public-openclaw-runtime.md` (owner, 2026-09-28).

## Preconditions (amended by ADR-0009)

The original preconditions — `RUNTIME-PARITY-001` complete, DEC-006 approved, a rehearsed restore of
"the last verified comparator" — assumed OpenClaw was the rollback runtime. ADR-0004 ended that role
and ADR-0009 removes the ordering. What remains required:

- production does not select, construct or deploy OpenClaw (measured, not assumed);
- a stale or implicit OpenClaw route fails closed at startup;
- no release blocker is weakened: each removed blocker names what still covers its risk;
- every retired byte stays verifiable from history, and the retained evidence still validates;
- the rollback (a revert) is rehearsed and shown to restore the retired state.

## Ordered cleanup

1. remove OpenClaw from public runtime selection and deployment routing;
2. verify startup fails closed for stale/implicit routes and public control endpoints remain unreachable;
3. exercise rollback before deleting mutable build inputs;
4. remove public-cell OpenClaw packages, plugin build paths and image jobs no longer required;
5. preserve immutable manifests, source/artifact hashes, SBOM/provenance, evals, delivery evidence,
   security findings and rollback documentation;
6. update architecture, runbooks, CI and inventories without touching Private Owner OpenClaw.

## Constraints and rollback

- This task does not delete or rewrite historical evidence and does not remove the separately isolated
  Private Owner OpenClaw environment.
- Do not combine retirement with a public launch, credential migration or capability authorization.
- If rollback or startup fail-closed checks fail, stop and restore the last verified routing/deployment
  state; OpenClaw remains a comparator until a new reviewed attempt.

## Done when

- production manifests and deployment graphs contain no public OpenClaw runtime dependency;
- historical evidence remains verifiable and rollback drill evidence is attached;
- all runtime, security, supply-chain, contract, context and repository quality gates pass;
- documentation clearly distinguishes retired public runtime code from retained owner-only OpenClaw.
