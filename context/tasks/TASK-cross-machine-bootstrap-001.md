# TASK-cross-machine-bootstrap-001 — prove a fresh GitHub clone works

**Goal:** on a machine with no repository-generated state, clone the project, reproduce the synthetic
staff application, exercise it through a real browser and backend, and return evidence another
engineer can compare.

**Domains:** `platform`

**Stage:** `PRODUCTION_HARDENING`

**Risk:** MEDIUM — the task creates synthetic orders and local containers, but may not touch a shop
database, production credential or capability authorization.

**Queue status:** owner-directed portability task, deliberately not listed in
`delivery/WORK_QUEUE.yaml`. It validates distribution and must not rewrite product-delivery history.

## Authoritative context

Assemble before acting:

```bash
uv run python scripts/assemble_context.py \
  --task-id CROSS-MACHINE-BOOTSTRAP-001 --domain platform
```

Then read `AGENTS.md`, `BUILD_ENGINEERING_SPEC.md`, `context/INVARIANTS.md`,
`docs/CODEX_CROSS_MACHINE_HANDOFF.md`, `docs/runbooks/demo-stack.md` and
`docs/CORE_BUSINESS_WORKFLOWS_V1.md`.

## Constraints

- Use a fresh clone in a non-synced local directory.
- Use synthetic data only. Never copy `.shop/`, `.demo/`, `.env`, a Docker volume or a credential
  from another machine.
- Do not enable a feature flag, public channel, provider model call or automated send.
- Do not run the record-creating browser scripts against a real shop.
- Do not weaken, skip or relabel a failing required check.
- PostgreSQL-backed acceptance must use an isolated disposable database, never the shop ledger.
- Unknown policy remains `REQUIRE_HUMAN` or `NOT_SUPPORTED`.

## Work

1. Record OS, architecture, Docker, Python, Node.js, commit SHA and clean-worktree state.
2. Bootstrap the workspace and run context, contract and capability reports.
3. Generate fresh demo material locally and start the private synthetic stack.
4. Verify container health, TLS/HTTP behavior, authentication, authorization and refusal cases.
5. In a real browser, sign in as owner, operator, approver and auditor. Confirm the auditor cannot
   mutate state.
6. Run the detailed interaction verifier, daily-operation verifier and workflow-conformance verifier.
7. Run the full guarded repository gate and isolated OpenClaw plugin checks with a disposable
   PostgreSQL database, following `docs/CODEX_CROSS_MACHINE_HANDOFF.md` §5 exactly.
8. Run the high-severity npm advisory check separately. Record its expected `RUNTIME-SECURITY-001`
   failure without changing the pinned dependency or evidence bundle.
9. Scan browser/server logs for 5xx responses, invalid `/stores/null` requests, tracebacks and
   unhandled errors.
10. Tear down only the synthetic resources created by this task, unless the owner asks to keep the
   demo running.
11. Return the handoff report specified in `docs/CODEX_CROSS_MACHINE_HANDOFF.md` §8.

## Done when

- the demo opens at `http://localhost:8081/demo-idp/`;
- every expected container is healthy or its one-shot job exited zero;
- all four roles behave according to RBAC;
- both normal shop-day paths reach `COMPLETED` using synthetic records;
- every declared workflow and interactive control passes;
- the guarded PostgreSQL suite contains no missing-database skips;
- contracts, context drift, formatting, linting, typing and service-worker checks pass;
- all thirteen public/AI capabilities still report `NOT_AUTHORIZED`;
- the report contains no secret or customer data.

## Rollback

This task changes no repository source and no delivery state. Stop the demo with the exact Compose
files used to start it. Removing the demo volume is permitted only when the operator confirms its
synthetic data is disposable. Never remove the real shop volume.
