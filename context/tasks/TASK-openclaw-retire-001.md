# TASK-openclaw-retire-001 — reversible public-path retirement

**Goal:** remove OpenClaw from the public production dependency and deployment path only after the
runtime-selection, provider-data, security and rollback gates prove the custom adapter.

**Domains:** `runtime_architecture`, `evaluation_release`, `platform`  
**Stable work item:** `OPENCLAW-RETIRE-001`  
**Stage:** M4C  
**Risk:** HIGH

## Preconditions

- `RUNTIME-PARITY-001` is complete with accepted hash-bound evidence;
- custom runtime has no P0/critical regression and satisfies registered latency/cost/recovery budgets;
- effective provider request and DEC-006 data controls are approved;
- signed release and rollback artifacts identify the exact runtime/model/prompt/tool/context/config set;
- rollback rehearsal restores the last verified comparator without weakening any gate.

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

