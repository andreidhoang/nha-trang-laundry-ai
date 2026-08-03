# Task packet: SUPPLYCHAIN-CI-PORTABILITY-001

```text
Task ID: SUPPLYCHAIN-CI-PORTABILITY-001
Goal: Make the release supply-chain image scan run without paid scanner entitlement while preserving strict evidence gates.
Domain(s): platform, runtime_architecture, evaluation_release
Stage: PRODUCTION_HARDENING
Risk: HIGH
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially Sections 7, 9, 10, 11, and 13
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `specs/TEAM_REVIEW_REPORT_V1.md`
- `specs/contracts/container-scan-evidence-v1.schema.json`
- `specs/contracts/supply-chain-evidence-v1.schema.json`
- `context/tasks/TASK-supplychain-001.md`
- the failed GitHub supply-chain run for PR #2

## Files in scope

- `.github/workflows/release-supply-chain.yml`
- `scripts/normalize_container_scan.py`
- `packages/evals/tests/test_supply_chain_workflow.py`
- delivery/context state and evidence for this corrective item

## Required behavior

1. Use a maintained open-source scanner pinned by immutable container digest; require no paid service,
   external account, repository write permission, or credential.
2. Scan exact locally built image archives without exposing the Docker socket to the scanner.
3. Generate CycloneDX JSON and SARIF for each exact image, bind them to its immutable digest, and fail
   on every critical/high finding or malformed/unknown result.
4. Preserve sanitized artifact upload, least-privilege workflow permissions, and independent evidence
   verification.
5. Do not suppress, waive, or reclassify findings and do not change any capability authorization.

## Done when

- workflow contract tests prove the pinned scanner, no-entitlement path, archive isolation, and strict
  severity/evidence behavior;
- a hosted GitHub supply-chain run builds and scans all exact images successfully;
- cumulative repository gates and the guarded PostgreSQL suite pass without mandatory skips;
- rollback restores the preceding workflow without changing images, deployments, data, or authority.
