# Task packet: OPENCLAW-REPACK-001

```text
Task ID: OPENCLAW-REPACK-001
Goal: Build and verify an immutable EVAL_ONLY OpenClaw 2026.7.1-2 artifact whose vulnerable shrinkwrapped transitive dependencies are replaced by compatible published fixes.
Domain(s): platform, runtime_architecture, evaluation_release
Stage: PRODUCTION_HARDENING
Risk: HIGH
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially Sections 4, 5, 7, 9, 10, and 13
- `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md`
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `specs/TEAM_REVIEW_REPORT_V1.md`
- `docs/adr/0002-production-agent-runtime-and-trust-boundaries.md`
- `specs/contracts/container-scan-evidence-v1.schema.json`
- `specs/contracts/supply-chain-evidence-v1.schema.json`
- `specs/contracts/openclaw-repackage-manifest-v1.schema.json`
- `specs/contracts/openclaw-repackage-manifest-v2.schema.json`
- `specs/contracts/openclaw-cross-platform-result-v1.schema.json`
- `context/tasks/TASK-runtime-security-001.md`
- the exact upstream npm package and shrinkwrap for OpenClaw 2026.7.1-2

## Files in scope

- `runtime/openclaw/repack/`
- `runtime/openclaw/public-cell/`
- `runtime/model-registry-v1.yaml`
- the plugin package manifest and lockfile
- runtime build, verification, workflow, contract, test, evidence, and delivery-state files

## Required behavior

1. Verify the exact upstream OpenClaw 2026.7.1-2 tarball against its registry integrity before
   extraction; record its reviewed source digest and never substitute a moving release alias.
2. Preserve disabled `r1` as the exact rollback base and create a distinct immutable derived `r2`.
   Replace only `brace-expansion` `5.0.8 -> 5.0.9`, `fast-uri` `3.1.4 -> 3.1.5`, `ip-address`
   `10.2.0 -> 10.3.1`, and `undici` `8.5.0 -> 8.9.0`. The first three must satisfy the declared
   upstream ranges; `undici` permits only the reviewed exact dependency mutation in
   `package/package.json` and corresponding allowlisted shrinkwrap records.
3. Bind every material to exact name, path, version, registry URL, integrity, SHA-256, byte size,
   required-by path/range, and expected source metadata. Reject every unlisted value or file change.
4. Canonically encode the archive across platforms by normalizing ordering, UTF-8 POSIX paths,
   timestamps, uid/gid, permissions, tar headers, and gzip metadata. Two independent local builds
   must be byte-identical, and an independent verifier must validate the complete extracted tree.
5. Audit the complete repacked dependency tree with no exclusion, waiver, suppression, or severity
   reclassification. Both npm and OpenClaw security audits must fail closed.
6. Build an immutable Linux runtime image, generate CycloneDX or SPDX SBOM, SLSA-compatible build
   provenance, and scan the exact image with zero critical/high findings.
7. Run plugin, runtime, prompt/tool-boundary, focused undici compatibility, and full PostgreSQL
   regressions without mandatory skips.
8. Keep the artifact visibly `DERIVED` and `EVAL_ONLY`; keep real-customer data, provider calls, public ingress, automatic
   sends, and direct sends disabled, and keep all capabilities `NOT_AUTHORIZED`.
9. Rollback restores the preceding disabled r1/upstream artifact; no database, deployment, customer,
   credential, DNS, provider, or channel mutation is permitted.
10. Hosted release preparation must build r2 independently on clean Windows and Linux runners at the
    same exact commit, schema-validate both results, compare bytes and manifest-bound metadata, and
    allow the Linux OCI/SBOM/provenance/scan path to consume only the successfully compared bytes.

## Done when

- upstream source, replacement packages, repacked output, image, SBOM, provenance, and scan are
  digest-bound and independently verified;
- complete production dependency audit, OpenClaw security audit, and declared regressions pass;
- a hosted GitHub run reproduces and scans the exact runtime image with zero critical/high findings;
- the preceding disabled EVAL_ONLY artifact remains an explicit rollback target;
- evidence remains non-release and grants no customer-facing authority.

## Acceptance checks

- `uv run python scripts/build_openclaw_repackage.py --verify-reproducible`
- `uv run python scripts/verify_openclaw_repackage.py`
- `npm --prefix runtime/openclaw/public-cell/plugin audit --audit-level=high`
- `uv run python scripts/verify_agent_runtime.py`
- `uv run pytest packages/evals/tests/test_runtime_repackage.py packages/evals/tests/test_runtime_verifier.py packages/evals/tests/test_local_agent_evidence.py packages/evals/tests/test_synthetic_tool_escape.py`
- `uv run pytest --require-postgres-integration`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy apps packages`
- `uv run python scripts/verify_contracts.py`
- `uv run python scripts/check_context_drift.py`
- `npm --prefix runtime/openclaw/public-cell/plugin test`
- `uv run pytest packages/evals/tests/test_openclaw_cross_platform_workflow.py packages/evals/tests/test_supply_chain_workflow.py`

## Required evidence

- upstream_source_integrity
- compatible_transitive_replacements
- reproducible_repacked_artifact
- complete_dependency_and_security_audits
- immutable_runtime_image_scan_sbom_provenance
- regression_suite
- disabled_authority_and_rollback

## Codex execution rule

Implement only this reviewed EVAL_ONLY correction. Do not mark it complete if any required integration
check skips, any high/critical finding remains, provenance or digest binding is incomplete, or any
customer/provider/public/send/capability authority changes.

## Phase A local expansion — 2026-08-06

Authority is limited to local EVAL_ONLY engineering. It permits the reviewed four-replacement r2
manifest, deterministic builder/verifier, local plugin/runtime bindings, synthetic tests, and
non-release evidence. It does not grant Security/Privacy or Supply-chain approval and does not permit
a push, pull request, hosted workflow, merge, release, deployment, provider call, customer data, or
customer-facing capability.

The local target is `openclaw-2026.7.1-2-nha-trang-r2.tgz`. The prior r1 identity is retained and
hash-bound as the disabled rollback base because it represents a materially different two-replacement
artifact. Completion still requires separately authorized hosted Windows/Linux reproducibility and
exact-image SBOM, provenance, and zero-HIGH/CRITICAL evidence plus independent Security/Supply-chain
review.

Local Phase A result: two canonical builds were byte-identical at SHA-256
`0c4d5d0dcdccde0290932c9baf17c1e371a12d46660ebba32dfa3b878124edab`, npm integrity
`sha512-yFjlTDU5sv+4lPR7kIM+iIsMAqkkfY2Jd07+f0wubCPK6nFl3vYFHcELtln+q3/9x1flCLtxp3G7ukAFougSgA==`,
size `19,535,080`. Independent artifact/runtime verification, complete-tree audit, `30` focused tests,
`9` plugin tests, `535` guarded PostgreSQL tests, migrations, Ruff, formatting, mypy, contract checks,
and context drift all passed with no required skips. These results are non-release evidence only.

## Phase B1 local hosted-workflow preparation — 2026-08-09

Authority is limited to local workflow engineering and static/typed verification. The reviewed graph
must contain independent `windows-latest` and `ubuntu-latest` r2 builds, a comparison job that depends
on both, and a Linux supply-chain job that depends on comparison. Both platform results bind the exact
Git commit, runner identity, manifest digest, upstream source digest, artifact SHA-256, npm integrity,
and byte size. The comparison result must be schema-valid and prove exact byte identity before its r2
artifact is copied into the OCI build context.

The final supply-chain evidence must hash-bind the comparison record, sanitized OpenClaw/runtime audit,
OCI provenance, exact image scan, and CycloneDX SBOM. Permissions remain `contents: read`; write,
OIDC, attestation, signing, deployment, credential, provider, customer-data, ingress, and send authority
are prohibited. Phase B1 cannot prove hosted behavior and cannot complete this item. Phase B2 still
requires independent Security/Supply-chain review, exact-commit branch/push authority, and an authorized
hosted run whose Windows/Linux bytes, OCI provenance/SBOM, and zero-HIGH/CRITICAL scan all pass.

Local Phase B1 result: the four-job dependency graph and typed comparison tooling passed `23` focused
workflow/contract tests; runtime/evidence checks passed `30`, plugin/undici checks passed `9`, and the
guarded PostgreSQL suite passed `543` with no final failures or skips. Canonical r2 reproducibility,
independent verification, complete-tree npm/runtime audits, Ruff, formatting, mypy, `15` JSON and `2`
YAML contract checks, context drift, delivery projection, and diff review passed. The npm audit still
reports seven moderate findings and zero HIGH/CRITICAL; all findings remain unwaived and pending
independent review. No hosted execution occurred, so these results remain local non-release evidence.

## Implementation journal — 2026-08-03

Status: **BLOCKED after all local gates passed; dirty working tree; no completion evidence recorded.** Branch:
`fix/openclaw-immutable-repack`; starting commit: `36909e7`.

### Evidence already observed

- `uv run python scripts/check_context_drift.py` passes, and delivery used a fresh generation to record
  the hosted-run blocker. Obtain another fresh generation before any later unblock or completion.
- `uv run python scripts/build_openclaw_repackage.py --verify-reproducible` passed at the final local
  tree. Two independent outputs were byte-identical:
  SHA-256 `8478f9110425449a7162a8fefd0ca866594e91a584dc681f9a382b8cd0454dcc`, npm integrity
  `sha512-8Mx+tv9tYy53lIhvZM9aMGF8OATg/kovktAJkkWlYFnZAJ5DClmXsflBl3moPZjMMiNAfbXdnQColWuasg+Rlw==`,
  size `19,728,669`.
- After a clean `npm ci --ignore-scripts`, the final full development/runtime tree audit returned
  `critical=0`, `high=0`, `moderate=7`, `total=7`. This proves the two target high findings were absent
  from that installed tree.
- The regenerated tracked plugin candidate hashes to
  `sha256:617dcbdede123cb76cb845fb1cdb823fdf9375f6e629320347461d76c0306eb1`.

### Files currently added or changed for this item

- Repackage source/artifact: `runtime/openclaw/repack/manifest-v1.json`, `Dockerfile`, `README.md`, and
  `dist/openclaw-2026.7.1-2-nha-trang-r1.tgz`.
- Build/verification: `scripts/build_openclaw_repackage.py`,
  `scripts/verify_openclaw_repackage.py`, and `scripts/verify_openclaw_oci_attestations.py`.
- Typed boundary/tests: `specs/contracts/openclaw-repackage-manifest-v1.schema.json`,
  `packages/evals/tests/test_runtime_repackage.py`, runtime-registry models/tests, and runtime-verifier
  evidence validation.
- Runtime binding: plugin `package.json`/`package-lock.json`, plugin inventory, model registry, offline
  evidence capture inputs, and the regenerated plugin candidate tarball.
- Hosted evidence path: `.github/workflows/release-supply-chain.yml` now contains reproducible build,
  independent verification, complete npm/OpenClaw audits, Linux OCI build, BuildKit SLSA provenance,
  CycloneDX generation, Trivy critical/high scanning, normalization, and artifact upload.
- Delivery/context: queue claim, loop state, context harness count, context map, and project continuation
  brief. No migration, production configuration, deployed resource, credential, customer data, provider
  call, public ingress, or outbound send was changed.

### Final local verification and blocker

1. All queue-declared acceptance commands pass at the current tree: reproducible packaging,
   independent verification, complete-tree npm audit, runtime verification, 16 focused tests,
   521 PostgreSQL tests, Ruff, format, mypy, contracts, context drift, migrations, and plugin tests.
2. A real local BuildKit OCI archive verified SLSA v1 provenance and correct nested-index digest
   binding. Pinned Trivy `0.72.0` generated a CycloneDX SBOM and returned zero critical/high findings.
3. The hosted workflow path now extracts the OCI archive before Trivy scanning; real local execution
   proved that direct tar input is invalid and that the extracted layout succeeds.
4. The hosted exact-commit workflow has not run. Delivery records this missing external evidence as
   the blocker; local results do not satisfy the hosted evidence requirement.
5. All capabilities remain `NOT_AUTHORIZED`; the runtime image pin remains `verified: false`, and
   provider/public/send flags remain false.

### Next commands and decision points

1. Obtain authorization for the exact branch/commit, remote push or pull request, and reviewed GitHub
   supply-chain workflow.
2. Retain and verify its exact OCI provenance, SBOM, normalized zero-high scan, audits, and evidence
   bundle; do not substitute local output.
3. If hosted evidence passes, create the schema-valid delivery evidence, obtain a fresh generation,
   unblock, and complete the item through the recorder.
