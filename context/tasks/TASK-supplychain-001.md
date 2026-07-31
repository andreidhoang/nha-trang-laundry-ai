# Task packet: SUPPLYCHAIN-001

```text
Task ID: SUPPLYCHAIN-001
Goal: Add fail-closed software supply-chain evidence gates for release candidates.
Domain(s): platform, runtime_architecture, evaluation_release
Stage: PRODUCTION_HARDENING
Risk: HIGH
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially Sections 7, 9, 10, 11, and 13
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `specs/TEAM_REVIEW_REPORT_V1.md`
- `specs/contracts/container-scan-evidence-v1.schema.json`
- `specs/contracts/release-gate-manifest-v1.schema.json`
- `scripts/verify_release_candidate.py`
- checked-in CI workflow and production Dockerfiles

## Files in scope

- `.github/workflows/quality.yml` or one narrowly scoped release workflow
- `packages/evals/tests/test_supply_chain_workflow.py` (create)
- the smallest scripts/adapters required to normalize and validate scan/SBOM evidence
- release-candidate verification and its tests
- release verification runbook

## Required behavior

1. Scan committed content for secrets without uploading repository contents to an unapproved service.
2. Audit Python and Node dependencies and licenses from their lockfiles.
3. Generate a standard SBOM for each exact built image.
4. Scan each exact image and produce evidence conforming to the existing container-scan schema.
5. Bind scan and SBOM hashes to the exact image digest and release candidate.
6. Fail on missing, expired, mismatched, malformed, or high/critical unwaived findings.
7. Keep waivers explicit, time-bounded, reviewable, and outside model authority.
8. Upload only sanitized engineering artifacts with least-privilege CI permissions.

Prefer maintained open-source scanners and native platform artifact storage. Do not introduce a paid
service or new external account without explicit user approval.

## Tests first

Create workflow/verification tests proving rejection of:

- missing scan or SBOM;
- digest/hash mismatch;
- stale evidence;
- failing severity result;
- unsigned or schema-invalid evidence;
- evidence for a different image/release commit;
- workflow permissions broader than required.

## Done when

- every declared acceptance command passes;
- a synthetic local candidate produces schema-valid, hash-bound evidence;
- tampered/missing evidence is rejected deterministically;
- no finding is suppressed or reclassified by an LLM;
- capability authorization remains `NOT_AUTHORIZED`;
- rollback removes workflow/adapters without changing deployed artifacts.
