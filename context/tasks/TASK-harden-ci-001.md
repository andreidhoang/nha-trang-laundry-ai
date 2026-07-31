# Task packet: HARDEN-CI-001

```text
Task ID: HARDEN-CI-001
Goal: Make PostgreSQL integration coverage and the OpenClaw plugin build/test mandatory in CI.
Domain(s): platform, runtime_architecture, evaluation_release
Stage: PRODUCTION_HARDENING
Risk: MEDIUM
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially Sections 10–13
- `specs/IMPLEMENTATION_ROADMAP_V1.md`, especially the CI and test-pyramid requirements
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `.github/workflows/quality.yml`
- `compose.yaml`
- `.env.example`
- `docs/runbooks/local-development.md`
- `runtime/openclaw/public-cell/plugin/package.json`
- existing tests that skip when `DATABASE_URL` is absent

## Files in scope

- `.github/workflows/quality.yml`
- `packages/evals/tests/test_ci_quality_workflow.py` (create)
- `docs/runbooks/local-development.md` when commands or expectations change
- the smallest supporting script/config file needed to make required-test skipping fail visibly

Do not change application behavior, domain rules, release authorization, credentials, or public
capability flags in this task.

## Required behavior

1. Add a PostgreSQL 16 service to the Python CI job with an explicit health check.
2. Use synthetic CI credentials only and set `DATABASE_URL` for migrations and tests.
3. Apply all migrations before the test suite.
4. Make CI fail when database integration tests expected by this repository are skipped because the
   database is missing or unreachable.
5. Install the plugin dependencies from `package-lock.json`, build TypeScript, and run the plugin
   tests.
6. Preserve every current Ruff, format, mypy, contract, context, and status gate.
7. Keep GitHub Actions permissions least-privilege and pin maintained actions to an approved major or
   immutable commit according to existing repository policy.

## Tests first

Create a deterministic workflow-contract test that parses the checked-in workflow and proves:

- PostgreSQL is configured and health-checked;
- migrations run before database-enabled pytest;
- the database-enabled run rejects unexpected skips;
- `npm ci` and `npm test` run in the OpenClaw plugin directory;
- current quality and contract gates remain present.

Do not write a brittle test that merely searches for one arbitrary string when structured YAML
inspection is practical.

## Done when

- all acceptance commands declared for `HARDEN-CI-001` pass;
- local evidence records whether PostgreSQL-backed tests actually executed;
- no required test is silently converted into a skip or xfail;
- the diff contains no credential, production hostname, public-channel enablement, or weakened gate;
- rollback is a workflow-only revert with no database or production-state mutation.
